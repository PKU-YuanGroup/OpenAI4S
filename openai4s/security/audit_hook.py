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

import hashlib
import hmac
import json
import os
import sys
from collections.abc import Callable


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


def install(
    *,
    enabled: bool = True,
    skill_event_sink: Callable[[dict], None] | None = None,
    skill_event_origin: Callable[[], str] | None = None,
    skill_event_key: bytes | None = None,
) -> bool:
    """Install the dlopen and optional Skill-diagnostic audit hook.

    Idempotent-ish: a second call installs a second (equivalent) hook, harmless
    but wasteful, so callers guard with `sys._openai4s_audit_armed`.

    The diagnostic MAC is not a trust boundary: arbitrary Python running in
    this interpreter can recover the key or invoke the signer. The Host must
    never use these events as durable recovery evidence.
    """
    if not enabled and skill_event_sink is None:
        return False
    if skill_event_sink is not None and (
        not isinstance(skill_event_key, bytes) or len(skill_event_key) < 32
    ):
        raise ValueError("Skill event attestation requires a 32-byte manager key")
    if getattr(sys, "_openai4s_audit_armed", False):
        return True

    import posix as _posix  # C-level getcwd, immune to os.getcwd monkeypatch
    import posixpath as _posixpath

    blocked = tuple(_writable_roots())
    allowed = tuple(_allowed_prefixes())
    registered_skill_loaders: set[object] = set()
    pending_skill_loads: dict[int, tuple[object, str, bool, str]] = {}
    code_type = type(install.__code__)

    def _signed_skill_event(
        event: dict,
        *,
        _dumps=json.dumps,
        _hmac_new=hmac.new,
        _sha256=hashlib.sha256,
        _key=skill_event_key,
    ) -> dict:
        payload = dict(event)
        encoded = _dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        payload["attestation_mac"] = _hmac_new(_key, encoded, _sha256).hexdigest()
        return payload

    def _under(path: str, roots: tuple) -> bool:
        for r in roots:
            if path == r or path.startswith(r + "/"):
                return True
        return False

    def _trusted_recovery_path(
        value: object,
        *,
        _realpath=os.path.realpath,
        _allowed=allowed,
    ) -> bool:
        if not isinstance(value, (str, bytes)):
            return True
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8", "surrogateescape")
            except Exception:  # noqa: BLE001
                return False
        if value.startswith("<") and value.endswith(">"):
            return True
        try:
            resolved = _realpath(value)
        except Exception:  # noqa: BLE001
            return False
        return _under(resolved, _allowed)

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
        _dlopen_enabled=bool(enabled),
        _skill_sink=skill_event_sink,
        _skill_origin=skill_event_origin,
        _registered_skill_loaders=registered_skill_loaders,
        _pending_skill_loads=pending_skill_loads,
        _code_type=code_type,
        _getframe=sys._getframe,
        _sha256=hashlib.sha256,
        _urandom=os.urandom,
        _signed_skill_event=_signed_skill_event,
        _trusted_recovery_path=_trusted_recovery_path,
    ):  # noqa: ANN001
        if event == "openai4s.skill_loader_register":
            if _skill_sink is None or _skill_origin is None or len(args) != 1:
                return
            try:
                trusted_origin = _skill_origin() in {"system", "recovery"}
            except Exception:  # noqa: BLE001 - an attestation error fails closed
                trusted_origin = False
            code = args[0]
            if trusted_origin and isinstance(code, _code_type):
                _registered_skill_loaders.add(code)
            return
        if event == "compile" and _skill_sink is not None and args:
            try:
                caller = _getframe(1)
            except (AttributeError, ValueError):
                caller = None
            if (
                caller is not None
                and caller.f_code in _registered_skill_loaders
                and isinstance(args[0], (str, bytes))
            ):
                source = args[0]
                if isinstance(source, str):
                    source = source.encode("utf-8")
                attestation_id = _urandom(16).hex()
                source_sha256 = _sha256(source).hexdigest()
                _pending_skill_loads[id(caller)] = (
                    caller,
                    source_sha256,
                    False,
                    attestation_id,
                )
                _skill_sink(
                    _signed_skill_event(
                        {
                            "event": "sidecar_capture_started",
                            "attestation_id": attestation_id,
                            "sha256": source_sha256,
                        }
                    )
                )
                return
        if event == "exec" and args:
            try:
                caller = _getframe(1)
            except (AttributeError, ValueError):
                caller = None
            pending = (
                _pending_skill_loads.get(id(caller)) if caller is not None else None
            )
            if pending is not None and pending[0] is caller:
                _pending_skill_loads[id(caller)] = (
                    pending[0],
                    pending[1],
                    True,
                    pending[3],
                )
                return
        if event == "openai4s.skill_sidecar_loaded":
            if _skill_sink is None or len(args) != 1 or type(args[0]) is not dict:
                return
            try:
                caller = _getframe(1)
            except (AttributeError, ValueError):
                return
            caller_code = caller.f_code
            if caller_code not in _registered_skill_loaders:
                return
            pending = _pending_skill_loads.pop(id(caller), None)
            event_record = dict(args[0])
            if (
                pending is not None
                and pending[0] is caller
                and pending[2]
                and event_record.get("sha256") == pending[1]
            ):
                event_record["attestation_id"] = pending[3]
                _skill_sink(_signed_skill_event(event_record))
            else:
                _skill_sink(
                    _signed_skill_event(
                        {
                            "event": "invalid_sidecar_event",
                            "attestation_id": pending[3] if pending is not None else "",
                        }
                    )
                )
            return
        if event == "openai4s.skill_cell_complete":
            _pending_skill_loads.clear()
            return
        try:
            frozen_recovery = (
                _skill_origin is not None and _skill_origin() == "sidecar_recovery"
            )
        except Exception:  # noqa: BLE001 - an origin error fails closed below
            frozen_recovery = True
        if frozen_recovery:
            source_path = None
            if event == "open" and args:
                source_path = args[0]
            elif event == "compile" and len(args) >= 2:
                source_path = args[1]
            elif event == "exec" and args:
                source_path = getattr(args[0], "co_filename", None)
            if source_path is not None and not _trusted_recovery_path(source_path):
                raise _perm(
                    "Refusing mutable file/code access during frozen "
                    f"sidecar recovery: {source_path}"
                )
        if not _dlopen_enabled:
            return
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
    del _dlopen_guard, _posix, _posixpath, _signed_skill_event
    return True
