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

    explicit OPENAI4S_CHANNEL
    container     the code is an image layer; an install is discarded on restart
    macos_app     the code is inside a deep-codesigned bundle; a write breaks it
    managed WSL   bootstrap.sh owns the install; we hand it the payload
    linux bundle  an immutable content-addressed tree with an atomic pointer
    source        a git worktree that may hold work an updater must not discard
    venv          site-packages, writable by this euid
    unknown       a refusal, never a guess

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
from dataclasses import dataclass, field
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

    ``evidence`` carries absolute paths and is **admin-only**. See the module
    docstring.
    """

    id: str
    self_update: bool
    restart_supported: bool
    install_root: Path | None = None
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
    "Set OPENAI4S_CHANNEL to one of "
    + ", ".join(c for c in CHANNEL_IDS if c != "unknown")
    + " if you know which it is, and report the case — an updater that guesses "
    "installs over something it did not recognise."
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


def _purelib() -> Path | None:
    try:
        return Path(sysconfig.get_paths()["purelib"]).resolve()
    except (KeyError, OSError):  # pragma: no cover - exotic sysconfig
        return None


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


def _probe_managed_bundle(
    _data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    """`<data_dir>/app/<Name>-<sha256>/` with `.installed`, owned by bootstrap.sh.

    Cross-checked, because the *shape* alone is also what a Linux bundle looks
    like once bootstrap.sh has installed it: either `OPENAI4S_BUNDLE_ID` is
    exported (bootstrap.sh is its only writer) or this is a WSL distribution.
    """
    for candidate in _PACKAGE_ROOT.parents:
        if candidate.parent.name != "app":
            continue
        if not _BUNDLE_DIR_RE.match(candidate.name):
            continue
        if not _exists(str(candidate / ".installed")):
            continue
        evidence = [f"bundle_tree={candidate}", f"marker={candidate / '.installed'}"]
        bundle_id = os.environ.get("OPENAI4S_BUNDLE_ID", "").strip()
        if bundle_id:
            evidence.append("env:OPENAI4S_BUNDLE_ID=set")
            return candidate, evidence
        if _is_wsl():
            evidence.append("wsl=true")
            return candidate, evidence
        return None
    return None


def _probe_linux_bundle(
    _data_dir: Path | None = None,
) -> tuple[Path | None, list[str]] | None:
    """The relocatable bundle: `VERSION`, `runtime/bin/python3`, `src/openai4s`."""
    for candidate in _PACKAGE_ROOT.parents:
        if not _exists(str(candidate / "VERSION")):
            continue
        if not _exists(str(candidate / "runtime" / "bin" / "python3")):
            continue
        if not _exists(str(candidate / "src" / "openai4s")):
            continue
        return candidate, [f"appdir={candidate}", f"version_file={candidate/'VERSION'}"]
    return None


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


def _probe_venv(_data_dir: Path | None = None) -> tuple[Path | None, list[str]] | None:
    purelib = _purelib()
    site_dir = _PACKAGE_ROOT.parent
    evidence: list[str] = []
    if _under(_PACKAGE_ROOT, purelib):
        evidence.append(f"purelib={purelib}")
    else:
        from importlib.metadata import distribution

        try:
            installed = distribution("openai4s")
            # Metadata elsewhere on sys.path is not evidence about this copy.
            # In particular, an editable install or PYTHONPATH can shadow an
            # installed wheel while pip would update a different directory.
            if Path(installed.locate_file("openai4s")).resolve() != _PACKAGE_ROOT:
                return None
        except Exception:  # noqa: BLE001 - PackageNotFoundError and friends
            return None
        evidence.append("distribution=openai4s")
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


def _build(
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
        found = _run_probe(override, data_dir)
        if found is not None:
            install_root, probe_evidence = found
            return _build(override, install_root, evidence + probe_evidence)
        evidence.append("uncorroborated")
        return _build(override, None, evidence)

    for channel_id, _probe in _PROBES:
        found = _run_probe(channel_id, data_dir)
        if found is None:
            continue
        install_root, probe_evidence = found
        return _build(channel_id, install_root, probe_evidence)
    return _build("unknown", None, [f"package_root={_PACKAGE_ROOT}"])


def require_self_update(channel: Channel) -> None:
    """Raise the channel's own refusal when it does not install anything.

    One place, so the CLI, the HTTP route and the applier cannot each decide
    the question slightly differently — the shape of defect this repository
    keeps finding is a guard wired to one call site of several.
    """
    if channel.self_update:
        return
    raise UpdateError(channel.refusal)
