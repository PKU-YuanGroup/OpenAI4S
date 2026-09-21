"""Which install channel is this, and may it update itself.

Detection is **pure**: it reads environment variables and the filesystem, and
that is all. No network, no subprocess, no Store, no `ensure_dirs()`. That is
not a stylistic preference — `doctor`, the CLI's first lines and a read-only
HTTP projection all call it, and each of the three has a documented reason it
cannot spawn anything. `tests/test_update_channel.py` asserts the property by
forbidding both primitives for the duration of a detection.

The order below is a decision, not an accident. Each probe answers a narrower
question than the one after it, so the first match wins and an install that
looks like two things is called the one whose *refusal* is correct:

    container     the code is an image layer; an install is discarded on restart
    macos_app     the code is inside a deep-codesigned bundle; a write breaks it
    managed WSL   <data_dir>/app/<Name>-<sha256>, owned by bootstrap.sh
    linux bundle  <root>/src/openai4s, run by <root>/runtime/bin/python3
    source        a git worktree that may hold work an updater must not discard
    venv          a site directory this interpreter's own installer writes to
    unknown       a refusal, never a guess

**What counts as evidence.** A probe that grants ``self_update`` may rest only
on facts about *this process*: the resolved path of the loaded package, the
resolved running interpreter, the platform, and the interpreter's own site
configuration. Files found near the code -- landmarks, markers, distribution
metadata, a ``.env`` -- are corroboration at most, because anything that can
write a checkout, a home directory or a workspace (a cell, a Skill, an
extracted archive) can plant them. Plantable files may push a detection toward
*refusal*, never toward a write. ``OPENAI4S_CHANNEL`` follows the same rule: it
can pin a refusing channel outright, but for a channel that updates itself it
can only confirm what the probes already found. ``config._load_dotenv`` reads
``.env`` from every ancestor of the package, so an unconfirmed pin is exactly a
planted file.

An ambiguous install is `unknown`. Guessing here means installing over
something we did not identify, which is the one failure this package exists to
avoid.

``Channel.evidence`` records which probe fired and the absolute path that
decided it. It is **admin-only**: a path names a home directory, a username, and
often a research project. `Channel.as_dict()` leaves it out unless the caller
asks, so a projection that forgets is a projection that is safe.
"""

from __future__ import annotations

import os
import re
import sys
import sysconfig
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from openai4s.update import UpdateError

#: Every channel id this package knows. `unknown` is one of them: an install we
#: could not identify is a state with a name, not an error to be raised later.
CHANNEL_IDS: tuple[str, ...] = (
    "venv",
    "linux-bundle",
    "wsl-bundle",
    "source",
    "macos-app",
    "container",
    "unknown",
)

#: Channels on which this package installs anything at all. `wsl-bundle` is in
#: here because the install genuinely happens; what it cannot do is restart the
#: daemon, which is `restart_supported`, a separate field, so the two are never
#: conflated into one boolean that has to mean both.
SELF_UPDATE_CHANNELS: frozenset[str] = frozenset({"venv", "linux-bundle", "wsl-bundle"})

#: The content-addressed directory name bootstrap.sh writes:
#: `$DATA_DIR/app/<Name>-<64 hex>` with a `.installed` marker written last.
#: Matched rather than constructed, because bootstrap.sh is the only writer and
#: shipped copies of it are on users' machines.
_BUNDLE_DIR_RE = re.compile(r"^[A-Za-z0-9._-]+-[0-9a-f]{64}$")

_TRUE = frozenset({"1", "true", "yes", "on"})

#: This file is `<package root>/update/channel.py`.
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Channel:
    """What kind of install this is, and what we are allowed to do to it.

    ``refusal`` is the whole user-facing sentence, already written, including
    the reason. ``refusal_command`` is what the operator should run instead —
    separate from the prose so a UI can offer a copy button without scraping a
    paragraph. ``refusal_text_key`` is the i18n key for the same sentence in the
    workbench; the CLI stays English-only, consistent with every other
    subcommand.

    ``install_scheme`` says which installer writes ``install_root``: ``prefix``
    (this interpreter's own purelib/platlib, what a plain ``pip install``
    writes), ``user`` (its user site, which needs ``--user``), ``bundle``
    (replaced as a whole tree), or ``""`` when nothing may be written. An
    applier derives its command from this field, never from ``evidence``, and
    refuses when the place that command would write differs from
    ``install_root``.

    ``evidence`` carries absolute paths and is **admin-only**. See the module
    docstring.
    """

    id: str
    self_update: bool
    restart_supported: bool
    install_root: Path | None = None
    install_scheme: str = ""
    interpreter: Path = field(default_factory=lambda: Path(sys.executable))
    refusal: str = ""
    refusal_command: tuple[str, ...] = ()
    refusal_text_key: str = ""
    evidence: tuple[str, ...] = ()

    @property
    def supported(self) -> bool:
        """Whether `openai4s update apply` performs an install on this channel."""
        return self.self_update

    def as_dict(self, *, include_evidence: bool = False) -> dict[str, object]:
        """The projection a CLI ``--json`` or an HTTP route may serialise.

        Evidence is omitted by default, so forgetting to think about it is the
        safe outcome rather than the leaky one.
        """
        payload: dict[str, object] = {
            "id": self.id,
            "self_update": self.self_update,
            "restart_supported": self.restart_supported,
            "supported": self.supported,
            "install_scheme": self.install_scheme,
            "refusal": self.refusal,
            "refusal_command": list(self.refusal_command),
            "refusal_text_key": self.refusal_text_key,
        }
        if include_evidence:
            payload["evidence"] = list(self.evidence)
            payload["install_root"] = (
                str(self.install_root) if self.install_root else ""
            )
            payload["interpreter"] = str(self.interpreter)
        return payload


# --------------------------------------------------------------------------- #
#  refusal copy
# --------------------------------------------------------------------------- #
#
# Written once, here, in the voice the operator meets it in. A refusal that does
# not say what to do instead is an error message wearing a policy's clothes, so
# every one of these names a command.

_CONTAINER_REFUSAL = (
    "this install is a container image layer: site-packages belongs to root "
    "while the process runs unprivileged, and anything installed into the "
    "layer is discarded the next time the container starts — an 'upgraded' "
    "daemon that silently reverts. Pull the new image instead; the /data "
    "volume is not touched."
)
_MACOS_REFUSAL = (
    "this install is a signed macOS application bundle. It is deep-codesigned, "
    "so any write inside OpenAI4S.app/Contents/ breaks the seal and Gatekeeper "
    "then refuses to launch it — a write that succeeds and breaks the product "
    "later. The disk image is downloaded and verified for you; drag it to "
    "Applications and relaunch."
)
_SOURCE_REFUSAL = (
    "this install runs from a git worktree. The code is yours and may hold "
    "uncommitted work an updater has no right to discard, and swapping files "
    "underneath a checkout makes `git status` and the running code disagree."
)
_UNKNOWN_REFUSAL = (
    "this install could not be identified, so nothing will be written to it. "
    "Installs made with `pip install --target`, a PYTHONPATH entry, `uvx` or "
    "`uv run --with`, an interpreter marked EXTERNALLY-MANAGED, or an editable "
    "install of a tree without .git are refused on purpose -- update those by "
    "re-running the command that installed them. "
    "Otherwise reinstall through a channel the updater recognises (pip or uv "
    "into a virtual environment, the Linux bundle, or the Windows/WSL package) "
    "and report the case. OPENAI4S_CHANNEL cannot vouch for it: the variable "
    "can pin one of "
    + ", ".join(
        sorted(
            c for c in CHANNEL_IDS if c not in SELF_UPDATE_CHANNELS and c != "unknown"
        )
    )
    + " outright, but for "
    + ", ".join(sorted(SELF_UPDATE_CHANNELS))
    + " it only confirms what the probes found -- an updater that took a pin "
    "on trust would install over something it did not recognise."
)


def _pin_mismatch_refusal(pinned: str, detected: str) -> str:
    return (
        f"OPENAI4S_CHANNEL={pinned} is set, but this install was detected as "
        f"{detected}, so nothing will be written to it. Unset OPENAI4S_CHANNEL; "
        "it may come from a .env file in or above the install directory."
    )


_WSL_RESTART_REFUSAL = (
    "the update is installed, but this daemon runs inside a launcher-owned WSL "
    "session that `openai4s serve --detached` cannot replace. Stop it from "
    "Windows (OpenAI4S.cmd stop) and start it again."
)


def _source_sync_command() -> tuple[str, ...]:
    """`git pull && uv sync --locked`, with `--extra science` when it applies.

    Probing for the extra by asking the import system whether `numpy` is
    findable costs a `sys.path` scan and imports nothing. Telling a contributor
    with the science extra to run the bare `uv sync --locked` would uninstall
    it, which is a worse outcome than the update they asked for.
    """
    from importlib.util import find_spec

    sync = "uv sync --locked"
    try:
        if find_spec("numpy") is not None:
            sync += " --extra science"
    except (ImportError, ValueError):  # pragma: no cover - broken meta path
        pass
    return ("git pull", sync)


# --------------------------------------------------------------------------- #
#  probes
# --------------------------------------------------------------------------- #

#: Every probe has one shape: given the data dir, either the install root it
#: found with the evidence that decided it, or None for "not this channel".
_Probe = Callable[[Optional[Path]], Optional[Tuple[Optional[Path], List[str]]]]


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE


def _exists(path: str) -> bool:
    try:
        return os.path.exists(path)
    except OSError:  # pragma: no cover - unreadable mount
        return False


def _writable(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return os.access(path, os.W_OK)
    except OSError:  # pragma: no cover - unreadable mount
        return False


def _is_wsl() -> bool:
    """Delegated rather than re-implemented.

    `openai4s.security.wsl` already owns the answer and already documents why a
    container on a Windows host inherits the kernel release string without
    being WSL. A second spelling of this test is a second thing to keep true.
    """
    try:
        from openai4s.security import wsl
    except Exception:  # noqa: BLE001 - detection must never fail the caller
        return False
    try:
        return bool(wsl.is_wsl())
    except Exception:  # noqa: BLE001
        return False


def _prefixes() -> tuple[Path, ...]:
    """``sys.prefix`` and ``sys.exec_prefix``, resolved.

    Fixed when the interpreter starts -- from its own location, a
    ``pyvenv.cfg`` beside it, or ``PYTHONHOME`` in the environment it was
    *launched* with -- so nothing written into ``os.environ`` afterwards can
    move them. A seam for the suite.
    """
    found: list[Path] = []
    for raw in (sys.prefix, sys.exec_prefix):
        try:
            found.append(Path(raw).resolve())
        except (OSError, RuntimeError, TypeError):
            continue
    return tuple(found)


def _within_prefix(path: Path) -> bool:
    return any(_under(path, prefix) for prefix in _prefixes())


def _sysconfig_dir(key: str) -> Path | None:
    """One ``sysconfig`` install path, resolved, or None.

    Two ways ``sysconfig`` can be turned against this probe, both through its
    lazy initialisation: it reads ``_PYTHON_SYSCONFIGDATA_NAME`` from
    ``os.environ`` on first use, which is *after* ``config._load_dotenv`` has
    copied an ancestor ``.env`` into it. Naming a module that is not there
    makes it raise -- caught, because a detector that raises is a ``doctor``, a
    CLI and an HTTP projection that fail. Naming one that *is* there (planted
    on ``sys.path``) makes it return whatever directory that module says. So
    the answer is checked against a fact the environment cannot move: every
    real install scheme puts purelib and platlib under ``sys.prefix`` or
    ``sys.exec_prefix``. A path outside both is not this interpreter's.
    """
    try:
        found = Path(sysconfig.get_paths()[key]).resolve()
    except Exception:  # noqa: BLE001 - see the docstring
        return None
    if not _within_prefix(found):
        return None
    return found


def _purelib() -> Path | None:
    """This interpreter's purelib, or None. See `_sysconfig_dir`."""
    return _sysconfig_dir("purelib")


def _under(child: Path, parent: Path | None) -> bool:
    if parent is None:
        return False
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _declares_openai4s(pyproject: Path) -> bool:
    """Whether `pyproject.toml` is *this* project's.

    Read with a line scan rather than `tomllib`, which is 3.11+ while this
    package's floor is 3.10. The question is narrow enough that a parser buys
    nothing: does the `[project]` table name openai4s.
    """
    try:
        text = pyproject.read_text("utf-8")
    except OSError:
        return False
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section != "[project]":
            continue
        if re.match(r'^name\s*=\s*["\']openai4s["\']\s*$', stripped):
            return True
    return False


#: Substrings that appear in PID 1's cgroup path inside a container and in no
#: host's. A host reads `0::/init.scope` (cgroup v2) or `…:/init.scope` /
#: `…:/` (v1); none of these is a substring of either, so a match is a
#: container and a non-match is not evidence of anything.
_CONTAINER_CGROUPS: tuple[str, ...] = (
    "/docker/",
    "/docker-",
    "/kubepods",
    "containerd",
    "/lxc/",
    "libpod",
    "/crio-",
    "/garden/",
)


def _pid1_cgroup() -> str:
    """PID 1's cgroup line, bounded, or "" on any platform without procfs.

    A plain read with a ceiling rather than `read_text()`: this is the one
    probe that opens a path the operator did not choose, and a detector that
    can hang is a `doctor` and a CLI that can hang.
    """
    try:
        with open("/proc/1/cgroup", "rb") as handle:
            return handle.read(8192).decode("utf-8", "replace")
    except OSError:
        return ""


def _probe_container(
    data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    """Is this an image layer — where an install is discarded on the next start.

    Four signals, every one of them a positive fact about a container rather
    than an inference from what is missing. The two runtime marker files answer
    for Docker and Podman. They do **not** answer for containerd/CRI-O, which
    is what Kubernetes runs: a pod there has neither file, and if it also runs
    as root then site-packages is writable, `_probe_venv` fires, and this
    package installs into a layer that the next restart throws away — an
    "upgraded" daemon that silently reverts, which is the exact failure the
    refusal below is written about. So PID 1's cgroup and the Kubernetes
    service variable are read as well, and the `/data` fallback stays for the
    unprivileged case this project's own image produces.

    Every branch widens the refusal and none narrows it, which is the direction
    a misdetection here has to fail in.
    """
    for marker in ("/.dockerenv", "/run/.containerenv", "/run/systemd/container"):
        if _exists(marker):
            return None, [f"marker={marker}"]
    if os.environ.get("KUBERNETES_SERVICE_HOST", "").strip():
        return None, ["env:KUBERNETES_SERVICE_HOST=set"]
    cgroup = _pid1_cgroup()
    for needle in _CONTAINER_CGROUPS:
        if needle in cgroup:
            return None, [f"pid1_cgroup~{needle}"]
    if data_dir is not None and data_dir == Path("/data"):
        if not _writable(_PACKAGE_ROOT.parent):
            return None, [
                "data_dir=/data",
                f"not-writable={_PACKAGE_ROOT.parent}",
            ]
    return None


def _probe_macos_app(
    _data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    parents = _PACKAGE_ROOT.parents
    if len(parents) < 4:
        return None
    src, resources, contents, app = parents[0], parents[1], parents[2], parents[3]
    if (
        src.name == "src"
        and resources.name == "Resources"
        and contents.name == "Contents"
        and app.name.endswith(".app")
    ):
        return app, [f"app_bundle={app}"]
    return None


def _running_interpreter() -> Path | None:
    """The interpreter this process is running, resolved; None if unknown.

    A seam, so a test can say which interpreter is running without replacing
    ``sys.executable`` for the whole process. ``sys.executable`` is empty or
    unresolvable in embedded interpreters, and "unknown" must refuse.
    """
    raw = sys.executable
    if not raw:
        return None
    try:
        return Path(raw).resolve()
    except (OSError, RuntimeError):
        return None


def _on_linux() -> bool:
    """Both bundles are Linux artifacts: the relocatable tarball, and the same
    tarball installed into WSL. A seam, so the suite can run bundle cases on
    any host."""
    return sys.platform.startswith("linux")


def _bundle_root() -> Path | None:
    """The one directory that can be a bundle root for *this* package, or None.

    Both bundle layouts put the package at ``<root>/src/openai4s`` and run
    ``<root>/runtime/bin/python3`` against it: that is what the launchers in
    ``scripts/build_linux_bundle.sh`` exec, and the WSL payload bootstrap.sh
    installs is the same tarball. So a bundle root is not "an ancestor that
    holds the landmarks". It is exactly ``_PACKAGE_ROOT.parent.parent``, and
    only when the loaded package really is ``src/openai4s`` beneath it and the
    running interpreter really lives under its ``runtime/``.

    Both facts are properties of this process rather than of the filesystem
    around it: ``_PACKAGE_ROOT`` is resolved from ``__file__``, and the
    interpreter from ``sys.executable``. Planting files cannot change either.
    The landmarks alone could be planted by anything that can write a
    checkout, a home directory or a workspace -- including a cell -- and they
    are what used to flip a refused source checkout, or a writable venv, into
    a channel that self-updates into whatever directory held them. A
    ``src/openai4s`` symlink planted beside them does not help either, because
    the comparison is against the resolved package path, not through the link.
    """
    if not _on_linux():
        # Also what makes PYTHONEXECUTABLE irrelevant: it rewrites
        # sys.executable only on macOS framework builds.
        return None
    if _PACKAGE_ROOT.name != "openai4s" or _PACKAGE_ROOT.parent.name != "src":
        return None
    if _exists(str(_PACKAGE_ROOT.parent / ".git")):
        # A checkout cloned into a directory named `src`. The builder rsyncs
        # the tree with `--exclude .git`, so no bundle ever has one here, and
        # a plantable file may only ever push toward refusal.
        return None
    root = _PACKAGE_ROOT.parent.parent
    if _exists(str(root / ".git")):
        # The same rule one level up: a checkout that keeps its package under
        # src/. Not walked any further, or a bundle unpacked somewhere inside
        # a git-tracked home directory would be refused.
        return None
    interpreter = _running_interpreter()
    if interpreter is None or not _under(interpreter, root / "runtime"):
        return None
    return root


def _probe_managed_bundle(
    _data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    """`<data_dir>/app/<Name>-<sha256>/` with `.installed`, owned by bootstrap.sh.

    Cross-checked, because the *shape* alone is also what a Linux bundle looks
    like once bootstrap.sh has installed it: either `OPENAI4S_BUNDLE_ID` is
    exported (bootstrap.sh is its only writer) or this is a WSL distribution.
    The candidate is `_bundle_root()`, never an arbitrary ancestor.
    """
    candidate = _bundle_root()
    if candidate is None:
        return None
    if candidate.parent.name != "app":
        return None
    if not _BUNDLE_DIR_RE.match(candidate.name):
        return None
    if not _exists(str(candidate / ".installed")):
        return None
    evidence = [
        f"bundle_tree={candidate}",
        f"marker={candidate / '.installed'}",
        f"interpreter={_running_interpreter()}",
    ]
    bundle_id = os.environ.get("OPENAI4S_BUNDLE_ID", "").strip()
    if bundle_id:
        evidence.append("env:OPENAI4S_BUNDLE_ID=set")
        return candidate, evidence
    if _is_wsl():
        evidence.append("wsl=true")
        return candidate, evidence
    return None


def _probe_linux_bundle(
    _data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    """The relocatable bundle: this package at `src/openai4s`, run by its own
    `runtime/`, with `VERSION` and `runtime/bin/python3` beside it.

    The candidate is `_bundle_root()`, never an arbitrary ancestor. The two
    landmarks are still required -- they are what distinguishes a bundle from
    any other tree that happens to put the package under `src/` and run an
    interpreter from a sibling `runtime/` -- but they are corroboration, not
    the evidence.
    """
    candidate = _bundle_root()
    if candidate is None:
        return None
    if not _exists(str(candidate / "VERSION")):
        return None
    if not _exists(str(candidate / "runtime" / "bin" / "python3")):
        return None
    return candidate, [
        f"appdir={candidate}",
        f"version_file={candidate / 'VERSION'}",
        f"interpreter={_running_interpreter()}",
    ]


def _probe_source(
    _data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    """A checkout beside the package root.

    `.git` is tested with `exists()`, not `is_dir()`: in a **git worktree** it
    is a regular file holding a `gitdir:` pointer, and a worktree is exactly the
    shape a contributor running this from a feature branch has.
    """
    repo = _PACKAGE_ROOT.parent
    git = repo / ".git"
    pyproject = repo / "pyproject.toml"
    if not _exists(str(git)):
        return None
    if not _declares_openai4s(pyproject):
        return None
    return repo, [f"git={git}", f"pyproject={pyproject}"]


def _in_virtualenv() -> bool:
    """A seam over ``sys.prefix != sys.base_prefix``, so a test can ask the
    question without rebinding the interpreter's prefix, which ``sysconfig``
    has already cached."""
    return sys.prefix != sys.base_prefix


def _site_dirs() -> dict[Path, str]:
    """The directories this interpreter's installer writes into, by scheme.

    ``prefix``: this interpreter's own purelib and platlib -- where a plain
    ``pip install`` or ``uv pip install --python <this>`` writes. ``user``: the
    user site, and only for an interpreter that is not a virtual environment
    and has it enabled; inside a virtual environment pip either refuses
    ``--user`` or, with ``--system-site-packages``, writes a plain install to
    the environment instead, so the user site is never where an update lands.

    Deliberately *not* ``site.getsitepackages()``: under
    ``--system-site-packages`` it names the base interpreter's site-packages,
    which this interpreter's pip does not write to (it reports "not
    uninstalling ... outside environment" and installs into the venv, leaving
    the old copy loaded). Every entry is a fact about this interpreter's own
    configuration, never about files near the package.
    """
    dirs: dict[Path, str] = {}
    purelib = _purelib()
    if purelib is not None:
        dirs[purelib] = "prefix"
    platlib = _sysconfig_dir("platlib")
    if platlib is not None:
        dirs.setdefault(platlib, "prefix")
    try:
        import site

        if not _in_virtualenv() and getattr(site, "ENABLE_USER_SITE", False):
            user = site.getusersitepackages()
            if isinstance(user, str) and user:
                dirs.setdefault(Path(user).resolve(), "user")
    except Exception:  # noqa: BLE001 - a broken `site` means no user site
        pass
    return dirs


#: Byte caps for the two metadata files read below. Real ones are a few KiB.
_MAX_METADATA_BYTES = 1024 * 1024
_MAX_RECORD_BYTES = 4 * 1024 * 1024
_DIST_NAME_RE = re.compile(r"[-_.]+")


def _read_regular(path: Path, cap: int) -> str | None:
    """Read a small regular file without following a link or blocking.

    ``O_NOFOLLOW`` refuses a symlink (to ``/dev/zero``, say) and ``O_NONBLOCK``
    lets a FIFO open without a writer; ``fstat`` then refuses anything that is
    not a regular file before a byte is read. A detector that can hang is a
    ``doctor`` and a CLI that can hang.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return None
    try:
        import stat as _stat

        if not _stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, cap + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(data) > cap:
        return None
    return data.decode("utf-8", errors="replace")


def _installed_here(site_dir: Path) -> bool:
    """Does pip's own record of an ``openai4s`` wheel install live in
    ``site_dir``?

    Reads the directory listing and at most two small files; never imports a
    metadata finder, so nothing on ``sys.meta_path`` can answer for another
    directory. Requires a ``*.dist-info`` whose directory name *and* ``Name:``
    header are ``openai4s``, and a ``RECORD`` that lists
    ``openai4s/__init__.py``. ``RECORD`` is what a wheel installer writes and
    setuptools' ``*.egg-info`` -- left in any development tree, and so in any
    extracted archive of one -- does not.
    """
    try:
        candidates = sorted(site_dir.glob("*.dist-info"))
    except OSError:
        return False
    for meta in candidates:
        stem = meta.name[: -len(".dist-info")]
        if _DIST_NAME_RE.sub("_", stem.split("-", 1)[0]).lower() != "openai4s":
            continue
        if meta.is_symlink():
            continue
        metadata = _read_regular(meta / "METADATA", _MAX_METADATA_BYTES)
        if metadata is None:
            continue
        declared = ""
        for line in metadata.splitlines():
            if not line.strip():
                break  # end of the header block
            key, sep, value = line.partition(":")
            if sep and key.strip().lower() == "name":
                declared = _DIST_NAME_RE.sub("-", value.strip()).lower()
                break
        if declared != "openai4s":
            continue
        record = _read_regular(meta / "RECORD", _MAX_RECORD_BYTES)
        if record is None:
            continue
        if any(
            line.split(",", 1)[0] == "openai4s/__init__.py"
            for line in record.splitlines()
        ):
            return True
    return False


#: uv's content-addressed cache buckets. An interpreter whose prefix lives in
#: one is an ephemeral environment (`uvx`, `uv tool run`, `uv run --with`):
#: the cache owns it, `uv cache prune` deletes it, and the next run may rebuild
#: or reuse it. Nothing written there is an update.
_UV_CACHE_BUCKET_RE = re.compile(r"^(archive|builds|environments)-v\d+$")


def _in_uv_cache() -> bool:
    return any(
        _UV_CACHE_BUCKET_RE.match(part)
        for prefix in _prefixes()
        for part in prefix.parts
    )


def _externally_managed() -> bool:
    """PEP 668: a distributor has declared this interpreter off-limits to
    installers. pip refuses both a prefix and a ``--user`` install into it, so
    detection refusing now is cheaper than a transaction failing later.
    Consulted only outside a virtual environment, where the marker applies.

    Fails *closed*, unlike `_sysconfig_dir`: the same planted
    ``_sysconfigdata`` that could move purelib can move ``installed_base``
    alone, pointing ``stdlib`` at a directory without the marker while purelib
    stays genuine. So a ``stdlib`` outside the prefix, or a ``sysconfig`` that
    raises, counts as managed. Outside a virtual environment every real layout
    keeps ``stdlib`` under ``sys.prefix``, and ``sysconfig`` only fails here
    when the environment has been tampered with -- a refusal is the right
    answer to both.
    """
    try:
        stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    except Exception:  # noqa: BLE001 - see the docstring
        return True
    if not _within_prefix(stdlib):
        return True
    return _exists(str(stdlib / "EXTERNALLY-MANAGED"))


def _probe_venv(_data_dir: Path | None = None) -> tuple[Path | None, list[str]] | None:
    """A site directory this interpreter's own installer writes to.

    Two facts, both required, and neither about files that merely sit near the
    package: the package's directory is *exactly* one of `_site_dirs()` -- not
    somewhere beneath one, where a vendored copy lives that pip would never
    touch -- and pip's record of the install lives in that same directory. A
    ``python -m`` run from an extracted source archive, a ``PYTHONPATH`` entry
    or a ``pip install --target`` directory is none of these, and pip would
    update a different directory from the one the code is loaded from.
    """
    if _in_uv_cache():
        return None
    # An editable checkout may have matching distribution metadata. It is not
    # a self-updatable install even when the project-name probe cannot read it.
    # (A plantable file, used only in the refusal direction.)
    if _exists(str(_PACKAGE_ROOT.parent / ".git")):
        return None
    site_dir = _PACKAGE_ROOT.parent
    scheme = _site_dirs().get(site_dir)
    if scheme is None:
        return None
    if not _in_virtualenv() and _externally_managed():
        return None
    if not _installed_here(site_dir):
        return None
    evidence = [f"scheme={scheme}", "distribution=openai4s"]
    if not _writable(site_dir):
        evidence.append(f"not-writable={site_dir}")
        return None
    evidence.append(f"site_packages={site_dir}")
    return site_dir, evidence


#: The detection order, as one list. Every probe takes the (optional) data dir
#: so the dispatch below has no special case; only the container's `/data`
#: fallback actually reads it.
_PROBES: tuple[tuple[str, "_Probe"], ...] = (
    ("container", _probe_container),
    ("macos-app", _probe_macos_app),
    ("wsl-bundle", _probe_managed_bundle),
    ("linux-bundle", _probe_linux_bundle),
    ("source", _probe_source),
    ("venv", _probe_venv),
)


def _run_probe(
    channel_id: str, data_dir: Path | None
) -> tuple[Path | None, list[str]] | None:
    for name, probe in _PROBES:
        if name == channel_id:
            return probe(data_dir)
    return None


def _scheme_for(channel_id: str, install_root: Path | None) -> str:
    if channel_id in ("linux-bundle", "wsl-bundle"):
        return "bundle"
    if channel_id == "venv" and install_root is not None:
        return _site_dirs().get(install_root, "")
    return ""


def _build(
    channel_id: str,
    install_root: Path | None,
    evidence: list[str],
) -> Channel:
    channel = _build_channel(channel_id, install_root, evidence)
    if channel.self_update:
        scheme = _scheme_for(channel.id, install_root)
        if not scheme:
            # A self-updating channel with no installer that writes its root
            # is not one we can update. Unreachable by construction today.
            return _build_channel("unknown", None, evidence + ["no-install-scheme"])
        return replace(channel, install_scheme=scheme)
    return channel


def _build_channel(
    channel_id: str,
    install_root: Path | None,
    evidence: list[str],
) -> Channel:
    if channel_id == "container":
        return Channel(
            id=channel_id,
            self_update=False,
            restart_supported=False,
            install_root=install_root,
            refusal=_CONTAINER_REFUSAL,
            refusal_command=("docker compose pull", "docker compose up -d"),
            refusal_text_key="update.refusal.container",
            evidence=tuple(evidence),
        )
    if channel_id == "macos-app":
        return Channel(
            id=channel_id,
            self_update=False,
            restart_supported=False,
            install_root=install_root,
            refusal=_MACOS_REFUSAL,
            refusal_command=("open <the downloaded .dmg>",),
            refusal_text_key="update.refusal.macos_app",
            evidence=tuple(evidence),
        )
    if channel_id == "source":
        return Channel(
            id=channel_id,
            self_update=False,
            restart_supported=False,
            install_root=install_root,
            refusal=_SOURCE_REFUSAL,
            refusal_command=_source_sync_command(),
            refusal_text_key="update.refusal.source",
            evidence=tuple(evidence),
        )
    if channel_id == "wsl-bundle":
        return Channel(
            id=channel_id,
            self_update=True,
            restart_supported=False,
            install_root=install_root,
            refusal=_WSL_RESTART_REFUSAL,
            refusal_command=("OpenAI4S.cmd stop",),
            refusal_text_key="update.refusal.wsl_restart",
            evidence=tuple(evidence),
        )
    if channel_id in ("venv", "linux-bundle"):
        return Channel(
            id=channel_id,
            self_update=True,
            restart_supported=True,
            install_root=install_root,
            evidence=tuple(evidence),
        )
    return Channel(
        id="unknown",
        self_update=False,
        restart_supported=False,
        install_root=install_root,
        refusal=_UNKNOWN_REFUSAL,
        refusal_command=(),
        refusal_text_key="update.refusal.unknown",
        evidence=tuple(evidence),
    )


def detect(cfg: object | None = None) -> Channel:
    """Identify this install. Pure: no network, no subprocess.

    ``cfg`` is any object carrying a ``data_dir`` — `openai4s.config.Config` in
    production. It is optional because `doctor` and the CLI both call this
    before a Config exists, and only one probe (the container's `/data`
    fallback) consults it at all.
    """
    data_dir: Path | None = None
    raw_dir = getattr(cfg, "data_dir", None)
    if raw_dir is not None:
        try:
            data_dir = Path(raw_dir)
        except TypeError:  # pragma: no cover - a cfg with a nonsense data_dir
            data_dir = None

    override = os.environ.get("OPENAI4S_CHANNEL", "").strip().lower()
    if override:
        evidence = [f"env:OPENAI4S_CHANNEL={override}"]
        if override not in CHANNEL_IDS:
            evidence.append("unrecognised-override")
            return _build("unknown", None, evidence)
        if override in SELF_UPDATE_CHANNELS:
            # A pin that would license a write may only confirm. The ordinary
            # chain runs in full, so a refusing probe that fires first (the
            # container, a signed .app, a checkout) still wins.
            channel_id, install_root, probe_evidence = _natural(data_dir)
            if channel_id == override:
                return _build(override, install_root, evidence + probe_evidence)
            evidence += ["uncorroborated", f"detected={channel_id}"]
            refused = _build("unknown", None, evidence)
            return replace(
                refused,
                refusal=_pin_mismatch_refusal(override, channel_id),
                refusal_command=("unset OPENAI4S_CHANNEL",),
                refusal_text_key="update.refusal.pin_mismatch",
            )
        # Pinning a refusing channel only narrows, so it is honoured as stated.
        found = _run_probe(override, data_dir)
        if found is not None:
            install_root, probe_evidence = found
            return _build(override, install_root, evidence + probe_evidence)
        detected = _natural(data_dir)[0]
        evidence += ["uncorroborated", f"detected={detected}"]
        refused = _build(override, None, evidence)
        return replace(
            refused,
            refusal=(
                f"OPENAI4S_CHANNEL={override} is set; this install was detected as "
                f"{detected}. " + refused.refusal
            ),
        )

    channel_id, install_root, probe_evidence = _natural(data_dir)
    return _build(channel_id, install_root, probe_evidence)


def _natural(data_dir: Path | None) -> tuple[str, Path | None, list[str]]:
    """The first probe that fires, in order; `unknown` when none does."""
    for channel_id, _probe in _PROBES:
        found = _run_probe(channel_id, data_dir)
        if found is None:
            continue
        install_root, probe_evidence = found
        return channel_id, install_root, probe_evidence
    return "unknown", None, [f"package_root={_PACKAGE_ROOT}"]


def require_self_update(channel: Channel) -> None:
    """Raise the channel's own refusal when it does not install anything.

    One place, so the CLI, the HTTP route and the applier cannot each decide
    the question slightly differently — the shape of defect this repository
    keeps finding is a guard wired to one call site of several.
    """
    if channel.self_update:
        return
    raise UpdateError(channel.refusal)
