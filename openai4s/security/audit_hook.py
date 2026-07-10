"""In-kernel CPython audit hook: the dlopen guard.

The third defense layer: a `sys.addaudithook` that intercepts `ctypes.dlopen`
and refuses to load a shared library from an agent-writable path. This closes
the "write a malicious `.so` into the workspace, then `dlopen` it to run native
code and escape the OS sandbox" vector — a load that the OS-level and
code-classifier layers can miss.

This runs INSIDE the kernel worker process (it must — an audit hook only sees
events raised in its own interpreter). Three properties make the guard hard to
defeat from inside a cell:

  * literal-path AND realpath are both checked. `posixpath.realpath` looks up
    `os.lstat`/`readlink`/`getcwd` at CALL time, so a cell that monkeypatches
    `os.readlink` could make realpath lie; we therefore also normalize the
    *literal* argument with the C-level `posix.getcwd` + pure-string `normpath`,
    both captured at install time.
  * dependencies are captured as keyword-default args at def time (not looked up
    from the module namespace at call time, which user code could rebind).
  * after `sys.addaudithook`, the function object and the captured modules are
    `del`'d so there is no Python-level handle to unload or tamper with the hook.

A legitimate library load from the interpreter / conda prefix / site-packages is
always allowed, so importing numpy, scipy, torch, etc. is unaffected — only
loads out of a writable workspace/scratch/artifacts path are refused.
"""
from __future__ import annotations

import os
import sys


def _writable_roots() -> list[str]:
    """Agent-writable roots a dlopen is refused from (unless under a prefix)."""
    roots: list[str] = []

    def add(p: str | None) -> None:
        if not p:
            return
        try:
            rp = os.path.realpath(p)
        except OSError:
            rp = p
        if rp and rp not in roots:
            roots.append(rp)

    # explicit override wins (colon-separated), else sensible defaults.
    override = os.environ.get("OPENAI4S_DLOPEN_BLOCK_ROOTS")
    if override:
        for part in override.split(os.pathsep):
            add(part.strip())
        return roots

    add(os.getcwd())  # the workspace the agent writes into
    add(os.environ.get("OPENAI4S_WORKSPACE"))
    data = os.environ.get("OPENAI4S_DATA_DIR") or os.path.expanduser("~/.openai4s")
    add(os.path.join(data, "artifacts"))
    for scratch in ("/tmp", "/private/tmp", os.environ.get("TMPDIR")):
        add(scratch)
    return roots


def _allowed_prefixes() -> list[str]:
    """Trusted read-mostly prefixes where legit native libs live (never block)."""
    prefixes: list[str] = []

    def add(p: str | None) -> None:
        if not p:
            return
        try:
            rp = os.path.realpath(p)
        except OSError:
            rp = p
        if rp and rp not in prefixes:
            prefixes.append(rp)

    for p in (
        sys.prefix,
        sys.base_prefix,
        getattr(sys, "exec_prefix", None),
        os.environ.get("CONDA_PREFIX"),
    ):
        add(p)
    try:
        import site

        for sp in site.getsitepackages():
            add(sp)
        add(site.getusersitepackages())
    except Exception:  # noqa: BLE001 - site may be restricted
        pass
    # common system library homes
    for p in (
        "/usr/lib",
        "/usr/local/lib",
        "/lib",
        "/lib64",
        "/System/Library",
        "/usr/local/Cellar",
        "/opt/homebrew",
    ):
        add(p)
    return prefixes


def install(*, enabled: bool = True) -> bool:
    """Install the dlopen audit hook. Returns True if armed.

    Idempotent-ish: a second call installs a second (equivalent) hook, harmless
    but wasteful, so callers guard with `sys._openai4s_audit_armed`.
    """
    if not enabled:
        return False
    if getattr(sys, "_openai4s_audit_armed", False):
        return True

    import posix as _posix  # C-level getcwd, immune to os.getcwd monkeypatch
    import posixpath as _posixpath

    blocked = tuple(_writable_roots())
    allowed = tuple(_allowed_prefixes())

    def _under(path: str, roots: tuple) -> bool:
        for r in roots:
            if path == r or path.startswith(r + "/"):
                return True
        return False

    # Dependencies captured as keyword defaults AT DEF TIME — call-time lookups
    # in the (user-controllable) namespace cannot redirect them.
    def _dlopen_guard(
        event,
        args,
        *,
        _realpath=os.path.realpath,
        _normpath=_posixpath.normpath,
        _getcwd=_posix.getcwd,
        _lexists=os.path.lexists,
        _blocked=blocked,
        _allowed=allowed,
        _perm=PermissionError,
    ):  # noqa: ANN001
        if event != "ctypes.dlopen":
            return
        if not args:
            return
        name = args[0]
        if not isinstance(name, (str, bytes)):
            return
        if isinstance(name, bytes):
            try:
                name = name.decode("utf-8", "surrogateescape")
            except Exception:  # noqa: BLE001
                return
        if not name:
            return  # dlopen(None) -> the main program handle; allow.

        # A BARE library name (no separator, e.g. "libSystem.B.dylib",
        # "libc.so.6") is resolved by the trusted dynamic-loader search path, NOT
        # relative to the workspace — do not block it, UNLESS a file by that name
        # actually exists under a writable root (a loader that searches cwd could
        # pick it up). Only an explicit PATH argument (contains a separator or a
        # leading '.') is the "write a .so then dlopen it" escape we guard.
        pathlike = ("/" in name) or ("\\" in name) or name.startswith(".")

        # Literal check: normalize WITHOUT touching the (monkeypatchable) fs.
        try:
            literal = _normpath(
                name if name.startswith("/") else _getcwd() + "/" + name
            )
        except Exception:  # noqa: BLE001
            literal = name

        if not pathlike:
            # bare name: suspicious only if such a file is really present in a
            # writable root (else it's a normal system/conda library load).
            try:
                present = _lexists(literal)
            except Exception:  # noqa: BLE001
                present = False
            if not present:
                return
            candidates = (literal,)
        else:
            # realpath resolves symlinks — a second, independent view.
            try:
                real = _realpath(name)
            except Exception:  # noqa: BLE001
                real = literal
            candidates = (literal, real)

        for candidate in candidates:
            if _under(candidate, _allowed):
                continue  # a trusted prefix wins even if nested under a root
            if _under(candidate, _blocked):
                raise _perm(
                    "Refusing to dlopen shared library from a writable path: "
                    f"{candidate}. Load native libraries from the conda "
                    "environment / site-packages, not the workspace."
                )

    sys.addaudithook(_dlopen_guard)
    sys._openai4s_audit_armed = True  # type: ignore[attr-defined]
    # Drop every Python-level handle so nothing can unload / rebind the hook.
    del _dlopen_guard, _posix, _posixpath
    return True
