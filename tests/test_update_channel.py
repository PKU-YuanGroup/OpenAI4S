"""What kind of install is this, and is the updater allowed to write to it.

Detection decides whether code is installed over something we did not
identify, so the negative cases carry the weight here. A probe that fires on a
tree that merely *looks* like its channel is worse than one that never fires:
the refusal is the product, and a wrong refusal is a wrong install.

Two properties are asserted as mechanisms rather than read off the source.
Detection opens no socket and spawns no process — both primitives are replaced
with ones that fail the test for the duration — because `doctor`, the CLI's
first lines and a read-only HTTP projection all call `detect()` and each has
its own documented reason it cannot spawn anything.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from openai4s.update import UpdateError, channel


@pytest.fixture(autouse=True)
def _no_channel_override(monkeypatch):
    """Nothing in this file may inherit a developer's channel pin."""
    for name in ("OPENAI4S_CHANNEL", "OPENAI4S_BUNDLE_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_container(monkeypatch):
    """The machine running the suite must not decide what `detect()` answers.

    The container probe is first in the order, so on a containerized developer
    machine — this repository ships a `Dockerfile` and a `scripts/container_
    smoke.sh`, so that is a normal way to run it — every test below that
    expects a bundle, a `.app` or a venv would instead get `container`, and the
    file would be green on a laptop and red in a dev container for a reason
    that has nothing to do with the code. Each of the four ambient signals is
    forced off here; the container tests set the one they are about themselves,
    and a later `monkeypatch.setattr` wins over this one.
    """
    real_exists = channel._exists
    markers = {"/.dockerenv", "/run/.containerenv", "/run/systemd/container"}
    monkeypatch.setattr(
        channel, "_exists", lambda path: (path not in markers) and real_exists(path)
    )
    monkeypatch.setattr(channel, "_pid1_cgroup", lambda: "")
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)


@pytest.fixture(autouse=True)
def _no_ambient_install_posture(monkeypatch):
    """The same rule for the interpreter running the suite: run it through
    `uvx`, or on a distribution's Python marked EXTERNALLY-MANAGED, and every
    venv expectation below would flip for a reason unrelated to the code. The
    tests about those two guards call the real functions captured below."""
    monkeypatch.setattr(channel, "_in_uv_cache", lambda: False)
    monkeypatch.setattr(channel, "_externally_managed", lambda: False)


#: Captured at import, before `_no_ambient_container` replaces the module
#: attribute. A test *about* the reader itself has to call the reader, and
#: `channel._pid1_cgroup` is a stub for the whole of this file.
_REAL_PID1_CGROUP = channel._pid1_cgroup
_REAL_IN_UV_CACHE = channel._in_uv_cache
_REAL_EXTERNALLY_MANAGED = channel._externally_managed


class _Cfg:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir


def _plant_package(root: Path) -> Path:
    """A fake `<something>/openai4s/update/` for `_PACKAGE_ROOT` to point at."""
    package = root / "openai4s"
    (package / "update").mkdir(parents=True, exist_ok=True)
    return package


def _pin_root(monkeypatch, package_root: Path) -> None:
    monkeypatch.setattr(channel, "_PACKAGE_ROOT", package_root.resolve())


def _pin_interpreter(monkeypatch, interpreter: Path | None) -> None:
    """Say which interpreter is running, without touching ``sys.executable``.

    Also says the host is Linux: both bundles are Linux artifacts, so every
    bundle scenario is a Linux scenario, and the suite runs on macOS too.
    `test_a_bundle_is_never_detected_off_linux` pins the gate itself.
    """
    resolved = None if interpreter is None else interpreter.resolve()
    monkeypatch.setattr(channel, "_running_interpreter", lambda: resolved)
    monkeypatch.setattr(channel, "_on_linux", lambda: True)


def _plant_distribution(site: Path, *, kind: str = "dist-info") -> Path:
    """What an installer (or a build, or an attacker) leaves beside a package.

    ``dist-info`` is what pip and uv write. The others are the shapes the
    probe must not accept: setuptools' ``egg-info`` (even with a RECORD), a
    ``dist-info`` with no RECORD, one whose RECORD does not list the package,
    and one whose directory name is not the distribution it claims to be.
    """
    header = "Metadata-Version: 2.1\nName: openai4s\nVersion: 0.3.0\n"
    record = "openai4s/__init__.py,,\n"
    if kind == "egg-info":
        meta = site / "openai4s.egg-info"
        meta.mkdir(parents=True)
        (meta / "PKG-INFO").write_text(header, encoding="utf-8")
        (meta / "RECORD").write_text(record, encoding="utf-8")
        return meta
    name = "zzz-1.0.dist-info" if kind == "misnamed" else "openai4s-0.3.0.dist-info"
    meta = site / name
    meta.mkdir(parents=True)
    (meta / "METADATA").write_text(header, encoding="utf-8")
    if kind in ("dist-info", "misnamed"):
        (meta / "RECORD").write_text(record, encoding="utf-8")
    elif kind == "record-without-package":
        (meta / "RECORD").write_text("somethingelse/__init__.py,,\n", encoding="utf-8")
    return meta


def _pin_site_dirs(monkeypatch, *prefix: Path, user: tuple[Path, ...] = ()) -> None:
    """Say which directories this interpreter's installer writes into."""
    dirs = {d.resolve(): "prefix" for d in prefix}
    dirs.update({d.resolve(): "user" for d in user})
    monkeypatch.setattr(channel, "_site_dirs", lambda: dict(dirs))


# --------------------------------------------------------------------------- #
#  purity
# --------------------------------------------------------------------------- #


def test_detection_opens_no_socket_and_spawns_no_process(monkeypatch, tmp_path):
    """The property three callers depend on, enforced rather than described."""

    def no_connect(*_args, **_kwargs):
        raise AssertionError("channel detection opened a socket")

    def no_popen(*_args, **_kwargs):
        raise AssertionError("channel detection spawned a process")

    monkeypatch.setattr(socket.socket, "connect", no_connect)
    monkeypatch.setattr(subprocess, "Popen", no_popen)
    monkeypatch.setattr(subprocess, "run", no_popen)

    found = channel.detect(_Cfg(tmp_path))
    assert found.id in channel.CHANNEL_IDS


def test_detect_works_with_no_config_at_all():
    """Every install in the field today has no OPENAI4S_CHANNEL and the CLI
    calls this before a Config exists."""
    assert channel.detect().id in channel.CHANNEL_IDS
    assert channel.detect(None).id in channel.CHANNEL_IDS


# --------------------------------------------------------------------------- #
#  the explicit override
# --------------------------------------------------------------------------- #


_REFUSING = sorted(
    c for c in channel.CHANNEL_IDS if c not in channel.SELF_UPDATE_CHANNELS
)


@pytest.mark.parametrize("channel_id", _REFUSING)
def test_a_refusing_channel_can_be_pinned_by_the_environment(monkeypatch, channel_id):
    """Narrowing is always safe, so a refusing pin is honoured as stated --
    which is what lets the Dockerfile say `container` outright."""
    monkeypatch.setenv("OPENAI4S_CHANNEL", channel_id)
    found = channel.detect()
    assert found.id == channel_id
    assert found.self_update is False
    assert any(item.startswith("env:OPENAI4S_CHANNEL=") for item in found.evidence)


@pytest.mark.parametrize("channel_id", sorted(channel.SELF_UPDATE_CHANNELS))
def test_an_unconfirmed_self_update_pin_is_a_refusal(monkeypatch, tmp_path, channel_id):
    """A pin that would license a write may only confirm. `config._load_dotenv`
    reads `.env` from every ancestor of the package, so a pin nothing on disk
    agrees with is indistinguishable from a file somebody planted in a
    checkout, a home directory or a workspace -- the same trust in files near
    the code that the bundle probes no longer extend."""
    _pin_root(monkeypatch, _plant_package(tmp_path))
    monkeypatch.setenv("OPENAI4S_CHANNEL", channel_id)
    found = channel.detect()
    assert found.id == "unknown"
    assert found.self_update is False
    assert "uncorroborated" in found.evidence
    with pytest.raises(UpdateError):
        channel.require_self_update(found)


def test_a_self_update_pin_cannot_skip_a_refusal_that_fires_first(
    monkeypatch, tmp_path
):
    """Inside a container, a writable site-packages is still an image layer.
    Before, a pin ran only its own probe, so `venv` skipped the container's."""
    site = tmp_path / "site-packages"
    _pin_root(monkeypatch, _plant_package(site))
    _plant_distribution(site)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setattr(channel, "_exists", lambda path: path == "/.dockerenv")
    assert channel._probe_venv(None) is not None  # the pin's own probe agrees
    monkeypatch.setenv("OPENAI4S_CHANNEL", "venv")
    found = channel.detect()
    assert found.self_update is False
    assert "detected=container" in found.evidence


def test_a_self_update_pin_that_the_probes_confirm_is_honoured(monkeypatch, tmp_path):
    """What a future launcher exporting OPENAI4S_CHANNEL actually gets."""
    site = tmp_path / "lib" / "python3.13" / "site-packages"
    _pin_root(monkeypatch, _plant_package(site))
    _plant_distribution(site)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setenv("OPENAI4S_CHANNEL", "venv")
    found = channel.detect()
    assert found.id == "venv"
    assert found.self_update is True
    assert "env:OPENAI4S_CHANNEL=venv" in found.evidence
    assert found.install_root == site.resolve()


def test_the_unknown_refusal_does_not_advise_a_pin_that_cannot_work():
    """The old copy told the operator to pin any channel id; for the three that
    update themselves that advice now yields the same refusal again."""
    for channel_id in channel.SELF_UPDATE_CHANNELS:
        assert f"pin one of {channel_id}" not in channel._UNKNOWN_REFUSAL
    assert "only confirms" in channel._UNKNOWN_REFUSAL


def test_an_unrecognised_override_is_unknown_not_a_guess(monkeypatch):
    monkeypatch.setenv("OPENAI4S_CHANNEL", "homebrew")
    found = channel.detect()
    assert found.id == "unknown"
    assert found.self_update is False
    assert "unrecognised-override" in found.evidence


def test_the_override_is_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setenv("OPENAI4S_CHANNEL", "  Container  ")
    assert channel.detect().id == "container"


# --------------------------------------------------------------------------- #
#  container
# --------------------------------------------------------------------------- #


def test_container_is_detected_from_either_runtime_marker(monkeypatch, tmp_path):
    _pin_root(monkeypatch, _plant_package(tmp_path))
    for marker in ("/.dockerenv", "/run/.containerenv"):
        monkeypatch.setattr(channel, "_exists", lambda path, m=marker: path == m)
        found = channel.detect()
        assert found.id == "container"
        assert f"marker={marker}" in found.evidence
        assert found.self_update is False
        assert found.refusal_command == ("docker compose pull", "docker compose up -d")


def test_container_falls_back_to_a_read_only_install_under_slash_data(
    monkeypatch, tmp_path
):
    """The image's own shape: `/data` mounted, site-packages owned by root."""
    _pin_root(monkeypatch, _plant_package(tmp_path))
    monkeypatch.setattr(channel, "_exists", lambda _path: False)
    monkeypatch.setattr(channel, "_writable", lambda _path: False)
    found = channel.detect(_Cfg(Path("/data")))
    assert found.id == "container"
    assert "data_dir=/data" in found.evidence


def test_slash_data_alone_is_not_a_container(monkeypatch, tmp_path):
    """A writable install that merely keeps its data in /data is not an image
    layer, and calling it one would refuse an update it can perform."""
    _pin_root(monkeypatch, _plant_package(tmp_path))
    monkeypatch.setattr(channel, "_exists", lambda _path: False)
    monkeypatch.setattr(channel, "_writable", lambda _path: True)
    assert channel._probe_container(Path("/data")) is None


def test_a_root_pod_with_no_runtime_marker_is_still_a_container(monkeypatch, tmp_path):
    """The misdetection this probe exists to stop.

    containerd and CRI-O — what Kubernetes actually runs — write neither
    `/.dockerenv` nor `/run/.containerenv`. A pod that also runs as root has a
    writable site-packages, so with only those two markers the venv probe fires
    and the updater installs into a layer the next restart discards: an
    "upgraded" daemon that silently reverts. Both remaining signals are
    asserted separately, because either one alone has to be sufficient.
    """
    package = _plant_package(tmp_path)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_exists", lambda _path: False)
    monkeypatch.setattr(channel, "_writable", lambda _path: True)

    # Without either, this tree is a writable venv — the wrong answer.
    monkeypatch.setattr(channel, "_pid1_cgroup", lambda: "0::/init.scope\n")
    assert channel._probe_container(None) is None

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    found = channel.detect()
    assert found.id == "container"
    assert found.self_update is False
    assert "env:KUBERNETES_SERVICE_HOST=set" in found.evidence
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST")

    monkeypatch.setattr(
        channel,
        "_pid1_cgroup",
        lambda: "0::/kubepods.slice/kubepods-burstable.slice/cri-containerd-ab.scope\n",
    )
    found = channel.detect()
    assert found.id == "container"
    assert any(item.startswith("pid1_cgroup~") for item in found.evidence)


@pytest.mark.parametrize(
    "cgroup",
    [
        "",
        "0::/\n",
        "0::/init.scope\n",
        "12:pids:/init.scope\n11:memory:/init.scope\n",
        "0::/user.slice/user-1000.slice/session-3.scope\n",
    ],
)
def test_a_host_cgroup_is_never_read_as_a_container(monkeypatch, tmp_path, cgroup):
    """The other direction, which is the one that costs a user their update.

    A false container is a refusal of an install that would have worked, so
    every shape PID 1 has on a machine that is not a container is pinned here.
    """
    _pin_root(monkeypatch, _plant_package(tmp_path))
    monkeypatch.setattr(channel, "_exists", lambda _path: False)
    monkeypatch.setattr(channel, "_pid1_cgroup", lambda: cgroup)
    assert channel._probe_container(None) is None


def test_the_cgroup_read_survives_a_platform_without_procfs(monkeypatch):
    """macOS has no `/proc`, and a detector that raises there is a `doctor`
    that raises there.

    Deliberately through `_REAL_PID1_CGROUP`: `_no_ambient_container` replaces
    `channel._pid1_cgroup` with a stub for every test in this file, so calling
    it by name here would assert something about the stub. The first assertion
    guards exactly that mistake.
    """
    assert _REAL_PID1_CGROUP is not channel._pid1_cgroup
    assert isinstance(_REAL_PID1_CGROUP(), str)

    def _boom(*_args, **_kwargs):
        raise OSError(13, "permission denied")

    monkeypatch.setattr("builtins.open", _boom)
    assert _REAL_PID1_CGROUP() == ""


def test_the_cgroup_read_is_bounded(monkeypatch):
    """A ceiling that only the source claims is a ceiling nobody can delete
    noisily. `/proc/1/cgroup` is small in practice, but this is the one path
    the detector opens that the operator did not choose."""
    asked: list[object] = []

    class _Handle:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, size=-1):
            asked.append(size)
            return b"0::/init.scope\n"

    monkeypatch.setattr("builtins.open", lambda *_a, **_k: _Handle())
    assert _REAL_PID1_CGROUP() == "0::/init.scope\n"
    assert asked and isinstance(asked[0], int) and 0 < asked[0] <= 1 << 20


# --------------------------------------------------------------------------- #
#  macOS .app
# --------------------------------------------------------------------------- #


def test_macos_app_needs_the_whole_bundle_shape(monkeypatch, tmp_path):
    app = tmp_path / "OpenAI4S.app"
    package = _plant_package(app / "Contents" / "Resources" / "src")
    _pin_root(monkeypatch, package)
    found = channel.detect()
    assert found.id == "macos-app"
    assert found.self_update is False
    assert found.install_root == app
    assert "codesigned" in found.refusal or "deep-codesigned" in found.refusal


@pytest.mark.parametrize(
    "layout",
    [
        # Each row breaks exactly one of the four names the probe requires, so
        # each of the four is individually load-bearing. Asserting only the
        # first of them — which is what "a directory merely named .app" alone
        # does — leaves the other three deletable with the file still green.
        ("Weird.app", "src"),
        ("OpenAI4S.app", "Contents", "Wrong", "src"),
        ("OpenAI4S.app", "Wrong", "Resources", "src"),
        ("OpenAI4S.app", "Contents", "Resources", "lib"),
        ("OpenAI4S", "Contents", "Resources", "src"),
        ("OpenAI4S.app.bak", "Contents", "Resources", "src"),
    ],
    ids=[
        "no-Contents-or-Resources",
        "Resources-misnamed",
        "Contents-misnamed",
        "src-misnamed",
        "no-.app-suffix",
        "suffix-not-at-the-end",
    ],
)
def test_the_macos_bundle_needs_every_one_of_its_four_names(
    monkeypatch, tmp_path, layout
):
    """A tree that merely resembles the bundle is not the bundle.

    The refusal is the product here: calling something a signed `.app` refuses
    an update that would have worked, and failing to call the real one a `.app`
    writes inside a deep-codesigned bundle and breaks Gatekeeper.
    """
    root = tmp_path
    for part in layout:
        root = root / part
    _pin_root(monkeypatch, _plant_package(root))
    assert channel._probe_macos_app(None) is None


def test_the_real_shipped_bundle_layout_is_the_one_that_matches(monkeypatch, tmp_path):
    """Pinned against `scripts/build_macos_dmg.sh`, which writes the package to
    `$APP/Contents/Resources/src` and runs it with that directory on
    `PYTHONPATH`. If that script moves the tree, this probe stops firing and
    the `.app` starts reporting itself as a writable venv."""
    app = tmp_path / "OpenAI4S.app"
    package = _plant_package(app / "Contents" / "Resources" / "src")
    _pin_root(monkeypatch, package)
    found = channel._probe_macos_app(None)
    assert found is not None and found[0] == app


# --------------------------------------------------------------------------- #
#  the two bundles
# --------------------------------------------------------------------------- #


def _plant_bundle(tmp_path: Path, monkeypatch) -> Path:
    """A bootstrap.sh install, run the way its launcher runs it.

    The interpreter is pinned to the tree's own ``runtime/`` so that every
    negative test below is refused for the reason it names, not by the
    interpreter binding happening to fail first.
    """
    tree = tmp_path / "app" / ("OpenAI4S-" + "a" * 64)
    package = _plant_package(tree / "src")
    (tree / ".installed").write_text("", encoding="utf-8")
    python = tree / "runtime" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    _pin_interpreter(monkeypatch, python)
    return package


def test_a_managed_bundle_needs_a_second_fact_beyond_its_shape(monkeypatch, tmp_path):
    """The content-addressed tree alone is also what a Linux bundle looks like
    after bootstrap.sh installed it, so the shape is not sufficient."""
    package = _plant_bundle(tmp_path, monkeypatch)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_is_wsl", lambda: False)
    assert channel._probe_managed_bundle(None) is None

    monkeypatch.setenv("OPENAI4S_BUNDLE_ID", "OpenAI4S-" + "a" * 64)
    found = channel.detect()
    assert found.id == "wsl-bundle"
    assert "env:OPENAI4S_BUNDLE_ID=set" in found.evidence


def test_wsl_corroborates_a_managed_bundle_without_the_variable(monkeypatch, tmp_path):
    _pin_root(monkeypatch, _plant_bundle(tmp_path, monkeypatch))
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    found = channel.detect()
    assert found.id == "wsl-bundle"
    assert "wsl=true" in found.evidence


def test_the_wsl_bundle_installs_but_cannot_restart(monkeypatch, tmp_path):
    """One boolean meaning both would make the refusal unreadable."""
    _pin_root(monkeypatch, _plant_bundle(tmp_path, monkeypatch))
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    found = channel.detect()
    assert found.self_update is True
    assert found.restart_supported is False
    assert found.refusal_command == ("OpenAI4S.cmd stop",)
    channel.require_self_update(found)  # installs: must not raise


def test_a_missing_installed_marker_is_not_a_bundle(monkeypatch, tmp_path):
    package = _plant_bundle(tmp_path, monkeypatch)
    (package.parents[1] / ".installed").unlink()
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    assert channel._probe_managed_bundle(None) is None


def test_a_bundle_directory_that_is_not_content_addressed_is_refused(
    monkeypatch, tmp_path
):
    tree = tmp_path / "app" / "OpenAI4S-latest"
    package = _plant_package(tree / "src")
    (tree / ".installed").write_text("", encoding="utf-8")
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, tree / "runtime" / "bin" / "python3")
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    assert channel._probe_managed_bundle(None) is None


def test_the_relocatable_linux_bundle_needs_its_landmarks(monkeypatch, tmp_path):
    """Run the way the shipped launcher runs it, and it still needs both files."""
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "src")
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, appdir / "runtime" / "bin" / "python3")
    assert channel._probe_linux_bundle(None) is None

    (appdir / "VERSION").write_text("0.3.0\n", encoding="utf-8")
    assert channel._probe_linux_bundle(None) is None

    (appdir / "runtime" / "bin").mkdir(parents=True)
    (appdir / "runtime" / "bin" / "python3").write_text("", encoding="utf-8")
    found = channel.detect()
    assert found.id == "linux-bundle"
    assert found.self_update is True
    assert found.restart_supported is True
    assert found.refusal == ""


def _plant_landmarks(directory: Path) -> None:
    """Everything the old ancestor walk accepted, written by someone else."""
    (directory / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    python = directory / "runtime" / "bin" / "python3"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    (directory / "src" / "openai4s").mkdir(parents=True, exist_ok=True)


def _plant_checkout(root: Path) -> Path:
    package = _plant_package(root)
    (root / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "openai4s"\n', encoding="utf-8"
    )
    return package


@pytest.mark.parametrize("where", ["checkout", "above-checkout"])
def test_landmarks_planted_around_a_checkout_do_not_make_it_a_bundle(
    monkeypatch, tmp_path, where
):
    """The security review's case. A checkout is refused; a cell that can write
    the workspace plants the three landmarks in the repository root (or any
    directory above it), and the old walk accepted the first ancestor holding
    them -- `linux-bundle`, `self_update=True`, and an install root that was
    the user's working tree. The package is not `src/openai4s` under either
    directory, so neither can be its bundle root."""
    repo = tmp_path / "home" / "OpenAI4S"
    package = _plant_checkout(repo)
    _pin_root(monkeypatch, package)
    _plant_landmarks(repo if where == "checkout" else repo.parent)
    _pin_interpreter(monkeypatch, repo.parent / "runtime" / "bin" / "python3")

    assert channel._probe_linux_bundle(None) is None
    found = channel.detect()
    assert found.id == "source"
    assert found.self_update is False


def test_a_planted_symlink_to_the_package_does_not_bind_the_bundle(
    monkeypatch, tmp_path
):
    """Pointing `src/openai4s` at the real package makes the landmark *exist*
    and even resolve to the package -- which is why the binding compares the
    resolved package path's own shape, not what a planted link resolves to."""
    repo = tmp_path / "OpenAI4S"
    package = _plant_checkout(repo)
    _pin_root(monkeypatch, package)
    (repo / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    python = repo / "runtime" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "openai4s").symlink_to(package, target_is_directory=True)
    _pin_interpreter(monkeypatch, python)

    assert (repo / "src" / "openai4s").resolve() == channel._PACKAGE_ROOT
    assert channel._probe_linux_bundle(None) is None
    assert channel.detect().id == "source"


def test_a_checkout_cloned_into_src_needs_the_running_interpreter(
    monkeypatch, tmp_path
):
    """The one layout the path shape cannot tell apart: a repository cloned
    into `~/src`, so the package really is `src/openai4s` under `~`. Planting
    `VERSION` and `runtime/bin/python3` in `~` must not make `~` a bundle root.
    Two facts refuse it independently: the running interpreter is not that
    runtime, and `src/.git` exists, which the builder's `rsync --exclude .git`
    guarantees no bundle has."""
    home = tmp_path / "home"
    package = _plant_checkout(home / "src")
    _pin_root(monkeypatch, package)
    (home / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    planted = home / "runtime" / "bin" / "python3"
    planted.parent.mkdir(parents=True)
    planted.write_text("", encoding="utf-8")

    _pin_interpreter(monkeypatch, home / "src" / ".venv" / "bin" / "python3")
    assert channel._probe_linux_bundle(None) is None
    assert channel.detect().id == "source"

    # Even genuinely run by that runtime, a checkout stays a checkout.
    _pin_interpreter(monkeypatch, planted)
    assert channel._probe_linux_bundle(None) is None
    assert channel.detect().id == "source"

    # Without the checkout, the same tree run by its own runtime is what a
    # bundle is -- so each refusal above came from the fact it names.
    (home / "src" / ".git").unlink()
    assert channel._probe_linux_bundle(None) is not None
    _pin_interpreter(monkeypatch, home / "src" / ".venv" / "bin" / "python3")
    assert channel._probe_linux_bundle(None) is None


def test_an_unknown_interpreter_is_never_a_bundle(monkeypatch, tmp_path):
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "src")
    _pin_root(monkeypatch, package)
    _plant_landmarks(appdir)
    _pin_interpreter(monkeypatch, None)
    assert channel._probe_linux_bundle(None) is None


def test_the_real_interpreter_seam_reads_sys_executable(monkeypatch, tmp_path):
    python = tmp_path / "bin" / "python3"
    python.parent.mkdir()
    python.write_text("", encoding="utf-8")
    monkeypatch.setattr(channel.sys, "executable", str(python))
    assert channel._running_interpreter() == python.resolve()
    monkeypatch.setattr(channel.sys, "executable", "")
    assert channel._running_interpreter() is None


def test_the_interpreter_seam_resolves_a_symlinked_executable(monkeypatch, tmp_path):
    real = tmp_path / "elsewhere" / "bin" / "python3"
    real.parent.mkdir(parents=True)
    real.write_text("", encoding="utf-8")
    link = tmp_path / "link" / "python3"
    link.parent.mkdir()
    link.symlink_to(real)
    monkeypatch.setattr(channel.sys, "executable", str(link))
    assert channel._running_interpreter() == real.resolve()


def test_a_runtime_link_to_a_foreign_interpreter_does_not_bind_the_bundle(
    monkeypatch, tmp_path
):
    """`<root>/runtime/bin/python3` is a link to an interpreter outside the
    tree -- a `python -m venv runtime`, or a planted link something later
    execs. Its *path* is under runtime/; the interpreter actually running is
    not. Drives the real seam, not `_pin_interpreter`."""
    home = tmp_path / "home"
    package = _plant_package(home / "src")
    (home / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    real = tmp_path / "usr" / "bin" / "python3.13"
    real.parent.mkdir(parents=True)
    real.write_text("", encoding="utf-8")
    link = home / "runtime" / "bin" / "python3"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_on_linux", lambda: True)
    monkeypatch.setattr(channel.sys, "executable", str(link))
    assert channel._probe_linux_bundle(None) is None


def test_the_relocatable_bundle_without_version_is_not_one(monkeypatch, tmp_path):
    """VERSION is the only thing missing, so it is the only possible reason."""
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "src")
    python = appdir / "runtime" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, python)
    assert channel._probe_linux_bundle(None) is None
    (appdir / "VERSION").write_text("0.3.0\n", encoding="utf-8")
    assert channel._probe_linux_bundle(None) is not None


def test_a_content_addressed_tree_outside_app_is_not_a_managed_bundle(
    monkeypatch, tmp_path
):
    """bootstrap.sh installs only under `<data_dir>/app/`; the same tree
    extracted to ~/Downloads is not one it manages."""
    tree = tmp_path / "Downloads" / ("OpenAI4S-" + "a" * 64)
    package = _plant_package(tree / "src")
    (tree / ".installed").write_text("", encoding="utf-8")
    python = tree / "runtime" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, python)
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    assert channel._probe_managed_bundle(None) is None
    control = tmp_path / "app" / tree.name
    control.parent.mkdir()
    tree.rename(control)
    _pin_root(monkeypatch, control / "src" / "openai4s")
    _pin_interpreter(monkeypatch, control / "runtime" / "bin" / "python3")
    assert channel._probe_managed_bundle(None) is not None


def test_a_bundle_is_never_detected_off_linux(monkeypatch, tmp_path):
    """Both bundles are Linux artifacts. The gate is also what makes macOS's
    PYTHONEXECUTABLE, which rewrites sys.executable on framework builds,
    irrelevant to bundle detection."""
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "src")
    _plant_landmarks(appdir)
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, appdir / "runtime" / "bin" / "python3")
    assert channel._probe_linux_bundle(None) is not None
    monkeypatch.setattr(channel, "_on_linux", lambda: False)
    assert channel._bundle_root() is None
    assert channel._probe_linux_bundle(None) is None


def test_a_signed_app_with_a_planted_version_is_still_the_app(monkeypatch, tmp_path):
    """The .app's layout satisfies the bundle-root shape (`Resources/src/
    openai4s`, run by `Resources/runtime`), so two things keep it out of the
    self-updating channel: the probe order, and -- on its real host -- the
    Linux gate. Pin both."""
    resources = tmp_path / "OpenAI4S.app" / "Contents" / "Resources"
    package = _plant_package(resources / "src")
    _plant_landmarks(resources)
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, resources / "runtime" / "bin" / "python3")
    found = channel.detect()
    assert found.id == "macos-app"
    assert found.self_update is False


def test_landmarks_above_a_venv_do_not_make_it_a_bundle(monkeypatch, tmp_path):
    """The review's second case: a venv under `$HOME` with the landmarks
    planted in `$HOME`. A venv's package sits under `site-packages`, never
    `src`, so there is no bundle root to find."""
    home = tmp_path / "home"
    site = home / ".venv" / "lib" / "python3.13" / "site-packages"
    package = _plant_package(site)
    _pin_root(monkeypatch, package)
    _plant_landmarks(home)
    _pin_interpreter(monkeypatch, home / "runtime" / "bin" / "python3")
    assert channel._probe_linux_bundle(None) is None
    assert channel._probe_managed_bundle(None) is None


def test_a_managed_bundle_run_by_another_interpreter_is_not_one(monkeypatch, tmp_path):
    package = _plant_bundle(tmp_path, monkeypatch)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    assert channel._probe_managed_bundle(None) is not None

    _pin_interpreter(monkeypatch, tmp_path / "elsewhere" / "bin" / "python3")
    assert channel._probe_managed_bundle(None) is None


# --------------------------------------------------------------------------- #
#  source
# --------------------------------------------------------------------------- #


def test_a_checkout_is_refused_and_names_the_command(monkeypatch, tmp_path):
    repo = tmp_path / "OpenAI4S"
    package = _plant_package(repo)
    (repo / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "openai4s"\nversion = "0.3.0"\n', encoding="utf-8"
    )
    _pin_root(monkeypatch, package)
    found = channel.detect()
    assert found.id == "source"
    assert found.self_update is False
    assert found.refusal_command[0] == "git pull"
    assert found.refusal_command[1].startswith("uv sync --locked")
    with pytest.raises(UpdateError):
        channel.require_self_update(found)


def test_a_worktrees_dot_git_file_still_counts_as_a_checkout(monkeypatch, tmp_path):
    """In a git worktree `.git` is a regular file, which is exactly the shape a
    contributor on a feature branch has."""
    repo = tmp_path / "wt"
    package = _plant_package(repo)
    (repo / ".git").write_text("gitdir: /repo/.git/worktrees/wt\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text(
        "[project]\nname = 'openai4s'\n", encoding="utf-8"
    )
    _pin_root(monkeypatch, package)
    assert channel.detect().id == "source"


def test_someone_elses_checkout_is_not_this_project(monkeypatch, tmp_path):
    repo = tmp_path / "other"
    package = _plant_package(repo)
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "not-openai4s"\n', encoding="utf-8"
    )
    _pin_root(monkeypatch, package)
    assert channel._probe_source(None) is None


def test_a_name_outside_the_project_table_does_not_count(monkeypatch, tmp_path):
    repo = tmp_path / "other"
    package = _plant_package(repo)
    (repo / ".git").mkdir()
    (repo / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "openai4s"\n', encoding="utf-8"
    )
    _pin_root(monkeypatch, package)
    assert channel._probe_source(None) is None


# --------------------------------------------------------------------------- #
#  venv
# --------------------------------------------------------------------------- #


def test_a_writable_site_packages_is_the_supported_channel(monkeypatch, tmp_path):
    site = tmp_path / "lib" / "python3.11" / "site-packages"
    package = _plant_package(site)
    _plant_distribution(site)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    found = channel.detect()
    assert found.id == "venv"
    assert found.self_update is True
    assert found.restart_supported is True
    assert found.install_root == site.resolve()
    assert found.install_scheme == "prefix"
    channel.require_self_update(found)  # must not raise


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="root can write an unwritable directory, so the negative is not real",
)
def test_a_site_packages_this_euid_cannot_write_is_not_a_venv(monkeypatch, tmp_path):
    site = tmp_path / "site-packages"
    package = _plant_package(site)
    _plant_distribution(site)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setattr(channel, "_writable", lambda _path: True)
    assert channel._probe_venv(None) is not None  # only writability differs below
    monkeypatch.undo()
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    site.chmod(0o500)
    try:
        assert channel._probe_venv(None) is None
    finally:
        site.chmod(0o700)


def test_a_writable_tree_that_is_not_an_installed_distribution_is_not_a_venv(
    monkeypatch, tmp_path
):
    """Writable is not the same fact as installed, and only one of them licenses
    a write.

    When the package root is not under `purelib` — an unpacked tarball, a
    `PYTHONPATH` entry, a copy somebody made — the probe's second question is
    whether `openai4s` is an installed distribution at all. Without it the
    probe reduces to "is the parent directory writable", which is true of any
    directory the user owns, and `self_update` then says yes about a tree no
    installer manages.
    """
    package = _plant_package(tmp_path / "unpacked")
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "elsewhere")
    monkeypatch.setattr(channel, "_writable", lambda _path: True)
    monkeypatch.setattr(channel, "_exists", lambda _path: False)
    # The directory *is* one of the interpreter's site dirs, so the question
    # that refuses below is the distribution one and nothing else.
    _pin_site_dirs(monkeypatch, tmp_path / "unpacked")
    assert channel._probe_venv(None) is None
    assert channel.detect().id == "unknown"
    _plant_distribution(tmp_path / "unpacked")
    assert channel._probe_venv(None) is not None


def test_an_install_nothing_recognised_is_unknown_and_refuses(monkeypatch, tmp_path):
    """No marker, no bundle, no checkout, and a tree this euid cannot write."""
    package = _plant_package(tmp_path / "somewhere")
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_exists", lambda _path: False)
    monkeypatch.setattr(channel, "_purelib", lambda: None)
    monkeypatch.setattr(channel, "_writable", lambda _path: False)
    found = channel.detect()
    assert found.id == "unknown"
    assert found.self_update is False
    assert "OPENAI4S_CHANNEL" in found.refusal
    with pytest.raises(UpdateError):
        channel.require_self_update(found)


# --------------------------------------------------------------------------- #
#  ordering, refusal copy and the projection
# --------------------------------------------------------------------------- #


def test_the_container_wins_over_every_later_probe(monkeypatch, tmp_path):
    """An install that looks like two things is called the one whose refusal is
    correct: inside a container, a writable site-packages is still discarded on
    the next `docker run`."""
    site = tmp_path / "site-packages"
    package = _plant_package(site)
    _plant_distribution(site)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    assert channel.detect().id == "venv"  # what it would be outside a container
    monkeypatch.setattr(channel, "_exists", lambda path: path == "/.dockerenv")
    assert channel.detect().id == "container"


def test_every_refused_channel_names_a_command_the_operator_can_run():
    """A refusal that does not say what to do instead is an error message
    wearing a policy's clothes."""
    for channel_id in ("container", "macos-app", "source"):
        built = channel._build(channel_id, None, [])
        assert built.self_update is False
        assert built.refusal
        assert built.refusal_command
        assert built.refusal_text_key.startswith("update.refusal.")


def test_every_supported_channel_is_in_the_declared_set():
    for channel_id in channel.CHANNEL_IDS:
        built = channel._build_channel(channel_id, None, [])
        assert built.self_update == (channel_id in channel.SELF_UPDATE_CHANNELS)
        assert built.supported == built.self_update


@pytest.mark.parametrize("channel_id", sorted(channel.SELF_UPDATE_CHANNELS))
def test_a_self_update_channel_without_an_installer_scheme_is_refused(channel_id):
    """`self_update=True` with `install_scheme=""` would be a channel we may
    write to with no command that writes its root. `_build` refuses to make
    one, whatever a probe or a later caller hands it."""
    if channel_id == "venv":
        built = channel._build(channel_id, None, [])
        assert built.id == "unknown"
        assert built.self_update is False
        assert "no-install-scheme" in built.evidence
    else:
        built = channel._build(channel_id, None, [])
        assert built.install_scheme == "bundle"


def test_the_projection_leaves_evidence_out_unless_it_is_asked(monkeypatch, tmp_path):
    """Evidence carries absolute paths: a home directory, a username, often a
    project name. Forgetting to think about it has to be the safe outcome."""
    _pin_site_dirs(monkeypatch, tmp_path)
    built = channel._build("venv", tmp_path, [f"site_packages={tmp_path}"])
    assert built.id == "venv"
    public = built.as_dict()
    assert "evidence" not in public
    assert "install_root" not in public
    assert "interpreter" not in public
    assert str(tmp_path) not in repr(public)

    admin = built.as_dict(include_evidence=True)
    assert admin["evidence"] == [f"site_packages={tmp_path}"]
    assert admin["install_root"] == str(tmp_path)


def test_the_source_sync_command_keeps_the_science_extra(monkeypatch):
    """Telling a contributor with the science extra to run the bare sync would
    uninstall it, which is worse than the update they asked for."""
    import importlib.util as importlib_util

    monkeypatch.setattr(importlib_util, "find_spec", lambda _name: object())
    assert channel._source_sync_command()[1] == "uv sync --locked --extra science"

    monkeypatch.setattr(importlib_util, "find_spec", lambda _name: None)
    assert channel._source_sync_command()[1] == "uv sync --locked"


def test_this_worktree_detects_as_a_source_checkout():
    """One assertion against the real tree rather than a planted one.

    Skipped inside a container, where the earlier probe legitimately wins.
    """
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        pytest.skip("a container image layer is correctly detected first")
    found = channel.detect()
    assert found.id == "source"
    assert found.self_update is False


@pytest.mark.parametrize("kind", ["dist-info", "egg-info", "no-record"])
def test_metadata_beside_a_package_outside_the_site_dirs_is_not_an_install(
    monkeypatch, tmp_path, kind
):
    """The security review's class, on the venv probe. Outside purelib the old
    check accepted any `openai4s` metadata whose `locate_file` resolved to the
    loaded package -- which only proves the metadata sits beside it. A
    `python -m` run from an extracted source archive has the archive root on
    `sys.path` and setuptools' `openai4s.egg-info` in it, with no attacker at
    all; a planted `*.dist-info` does the same. pip would update a different
    directory from the one the code is loaded from."""
    tree = tmp_path / "OpenAI4S-main"
    package = _plant_package(tree)
    _plant_distribution(tree, kind=kind)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "elsewhere")
    _pin_site_dirs(monkeypatch, tmp_path / "elsewhere")
    assert channel._probe_venv(None) is None
    found = channel.detect()
    assert found.id == "unknown"
    assert found.self_update is False


@pytest.mark.parametrize(
    "kind, expected",
    [("dist-info", "venv"), ("egg-info", "unknown"), ("no-record", "unknown")],
)
def test_a_user_site_install_needs_pips_own_record(
    monkeypatch, tmp_path, kind, expected
):
    """`pip install --user` lands outside purelib, in a site dir the
    interpreter owns. That is a real install -- when pip's RECORD is there."""
    user_site = tmp_path / ".local" / "lib" / "python3.13" / "site-packages"
    package = _plant_package(user_site)
    _plant_distribution(user_site, kind=kind)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "elsewhere")
    _pin_site_dirs(monkeypatch, tmp_path / "elsewhere", user=(user_site,))
    found = channel.detect()
    assert found.id == expected
    assert found.self_update is (expected == "venv")
    if expected == "venv":
        assert found.install_root == user_site.resolve()
        assert found.install_scheme == "user"


def test_pips_record_for_a_different_copy_does_not_count(monkeypatch, tmp_path):
    """A wheel installed in one site dir, the package loaded from another."""
    installed = tmp_path / "a" / "site-packages"
    loaded = tmp_path / "b" / "site-packages"
    _plant_package(installed)
    _plant_distribution(installed)
    package = _plant_package(loaded)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "elsewhere")
    _pin_site_dirs(monkeypatch, installed, loaded)
    assert channel._probe_venv(None) is None


def test_the_site_dirs_come_from_this_interpreter(monkeypatch):
    """No monkeypatching of the seam: the real answer contains purelib."""
    import sysconfig

    purelib = Path(sysconfig.get_paths()["purelib"]).resolve()
    assert purelib in channel._site_dirs()


# --------------------------------------------------------------------------- #
#  the venv probe: exact site dir, pip's own record, never blocking
# --------------------------------------------------------------------------- #


def test_a_vendored_copy_below_purelib_is_not_the_install(monkeypatch, tmp_path):
    """`purelib/somepkg/_vendor/openai4s` is under purelib, but pip upgrades
    `purelib/openai4s` and the vendored copy keeps loading -- an upgrade that
    silently reverts. The site dir must be purelib itself."""
    purelib = tmp_path / "site-packages"
    vendored = purelib / "somepkg" / "_vendor"
    package = _plant_package(vendored)
    _plant_distribution(vendored)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: purelib.resolve())
    assert channel._probe_venv(None) is None
    assert channel.detect().self_update is False


def test_the_base_interpreters_site_packages_is_not_this_interpreters(
    monkeypatch, tmp_path
):
    """Under `--system-site-packages`, `site.getsitepackages()` names the base
    interpreter's directory. This interpreter's pip does not write there."""
    import site

    base = tmp_path / "base" / "site-packages"
    package = _plant_package(base)
    _plant_distribution(base)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "venv-site")
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(base)], raising=False)
    assert base.resolve() not in channel._site_dirs()
    assert channel._probe_venv(None) is None


@pytest.mark.parametrize(
    "in_venv, enabled, counted",
    [(False, True, True), (True, True, False), (False, False, False)],
)
def test_the_user_site_counts_only_outside_a_venv_and_when_enabled(
    monkeypatch, tmp_path, in_venv, enabled, counted
):
    import site

    user = tmp_path / "user-site"
    user.mkdir()
    monkeypatch.setattr(site, "getusersitepackages", lambda: str(user))
    monkeypatch.setattr(site, "ENABLE_USER_SITE", enabled)
    monkeypatch.setattr(channel, "_in_virtualenv", lambda: in_venv)
    dirs = channel._site_dirs()
    assert (dirs.get(user.resolve()) == "user") is counted


def test_a_broken_sysconfig_refuses_instead_of_raising(monkeypatch, tmp_path):
    """A planted `.env` can set `_PYTHON_SYSCONFIGDATA_NAME` before sysconfig
    first initialises; the detector must answer, not raise."""

    def _broken(*_args, **_kwargs):
        raise ModuleNotFoundError("_sysconfigdata_planted")

    _pin_root(monkeypatch, _plant_package(tmp_path / "site-packages"))
    monkeypatch.setattr(channel.sysconfig, "get_paths", _broken)
    assert channel._purelib() is None
    assert all(scheme != "prefix" for scheme in channel._site_dirs().values())
    assert channel.detect().id in channel.CHANNEL_IDS


@pytest.mark.parametrize(
    "kind", ["egg-info", "no-record", "record-without-package", "misnamed"]
)
def test_only_a_wheel_record_for_openai4s_counts_as_installed(
    monkeypatch, tmp_path, kind
):
    site = tmp_path / "site-packages"
    _plant_package(site)
    _plant_distribution(site, kind=kind)
    assert channel._installed_here(site) is False


def test_another_distributions_record_does_not_vouch_for_openai4s(tmp_path):
    site = tmp_path / "site-packages"
    _plant_package(site)
    meta = site / "requests-2.32.0.dist-info"
    meta.mkdir(parents=True)
    (meta / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: requests\nVersion: 2.32.0\n", encoding="utf-8"
    )
    (meta / "RECORD").write_text("openai4s/__init__.py,,\n", encoding="utf-8")
    assert channel._installed_here(site) is False
    _plant_distribution(site)
    assert channel._installed_here(site) is True


def test_an_oversized_record_is_not_read(monkeypatch, tmp_path):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    monkeypatch.setattr(channel, "_MAX_RECORD_BYTES", 8)
    assert channel._installed_here(site) is False
    monkeypatch.setattr(channel, "_MAX_RECORD_BYTES", 1024)
    assert channel._installed_here(site) is True
    assert meta.exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
@pytest.mark.parametrize("which", ["METADATA", "RECORD"])
def test_a_fifo_in_the_record_cannot_hang_detection(tmp_path, which):
    """A detector that can hang is a doctor and a CLI that can hang."""
    import threading

    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    (meta / which).unlink()
    os.mkfifo(meta / which)
    result: list[bool] = []
    worker = threading.Thread(
        target=lambda: result.append(channel._installed_here(site)), daemon=True
    )
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "reading a FIFO blocked the detector"
    assert result == [False]


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks")
def test_a_symlinked_record_is_not_followed(tmp_path):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    real = tmp_path / "elsewhere-RECORD"
    real.write_text("openai4s/__init__.py,,\n", encoding="utf-8")
    (meta / "RECORD").unlink()
    (meta / "RECORD").symlink_to(real)
    assert channel._installed_here(site) is False


# --------------------------------------------------------------------------- #
#  the bundle root: the remaining shape checks
# --------------------------------------------------------------------------- #


def test_a_package_that_is_not_under_src_is_never_a_bundle(monkeypatch, tmp_path):
    """`_bundle_root()` is exactly `<root>/src/openai4s`."""
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "lib")
    _plant_landmarks(appdir)
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, appdir / "runtime" / "bin" / "python3")
    assert channel._bundle_root() is None
    assert channel._probe_linux_bundle(None) is None
    assert channel.detect().self_update is False


def test_a_checkout_that_keeps_its_package_under_src_is_not_a_bundle(
    monkeypatch, tmp_path
):
    """`.git` at the root, the package at `src/openai4s`, a VERSION and a
    copied-binary venv named `runtime/`: still a working tree."""
    root = tmp_path / "project"
    package = _plant_package(root / "src")
    _plant_landmarks(root)
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, root / "runtime" / "bin" / "python3")
    assert channel._probe_linux_bundle(None) is not None
    (root / ".git").mkdir()
    assert channel._bundle_root() is None
    assert channel._probe_linux_bundle(None) is None


# --------------------------------------------------------------------------- #
#  refusal copy for pins, and the install scheme
# --------------------------------------------------------------------------- #


def test_a_mismatched_self_update_pin_names_itself_in_the_refusal(
    monkeypatch, tmp_path
):
    """Before, a stale pin on a genuine install produced "could not be
    identified ... report the case", and only admin-only evidence said why."""
    site = tmp_path / "lib" / "python3.13" / "site-packages"
    _pin_root(monkeypatch, _plant_package(site))
    _plant_distribution(site)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setenv("OPENAI4S_CHANNEL", "linux-bundle")
    found = channel.detect()
    assert found.self_update is False
    assert "OPENAI4S_CHANNEL=linux-bundle" in found.refusal
    assert "detected as venv" in found.refusal
    assert found.refusal_command == ("unset OPENAI4S_CHANNEL",)
    assert found.refusal_text_key == "update.refusal.pin_mismatch"


def test_an_uncorroborated_refusing_pin_names_what_was_detected(monkeypatch, tmp_path):
    site = tmp_path / "lib" / "python3.13" / "site-packages"
    _pin_root(monkeypatch, _plant_package(site))
    _plant_distribution(site)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setenv("OPENAI4S_CHANNEL", "container")
    found = channel.detect()
    assert found.id == "container"
    assert found.self_update is False
    assert found.refusal.startswith("OPENAI4S_CHANNEL=container is set;")
    assert "detected as venv" in found.refusal


def test_the_unknown_refusal_no_longer_carries_the_old_advice():
    assert "Set OPENAI4S_CHANNEL to one of" not in channel._UNKNOWN_REFUSAL
    assert "--target" in channel._UNKNOWN_REFUSAL


def test_every_channel_carries_the_scheme_its_installer_writes(monkeypatch, tmp_path):
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "src")
    _plant_landmarks(appdir)
    _pin_root(monkeypatch, package)
    _pin_interpreter(monkeypatch, appdir / "runtime" / "bin" / "python3")
    bundle = channel.detect()
    assert (bundle.id, bundle.install_scheme) == ("linux-bundle", "bundle")
    assert bundle.as_dict()["install_scheme"] == "bundle"

    monkeypatch.setenv("OPENAI4S_CHANNEL", "container")
    refused = channel.detect()
    assert refused.install_scheme == ""
    assert refused.as_dict()["install_scheme"] == ""


# --------------------------------------------------------------------------- #
#  guards found by the third mutation round
# --------------------------------------------------------------------------- #


def test_platlib_is_a_prefix_site_dir_of_its_own(monkeypatch, tmp_path):
    purelib = tmp_path / "lib" / "python3.13" / "site-packages"
    platlib = tmp_path / "lib64" / "python3.13" / "site-packages"
    purelib.mkdir(parents=True)
    package = _plant_package(platlib)
    _plant_distribution(platlib)
    paths = {"purelib": str(purelib), "platlib": str(platlib)}
    monkeypatch.setattr(channel.sysconfig, "get_paths", lambda *a, **k: dict(paths))
    monkeypatch.setattr(channel, "_prefixes", lambda: (tmp_path.resolve(),))
    monkeypatch.setattr(channel, "_in_virtualenv", lambda: True)
    dirs = channel._site_dirs()
    assert dirs.get(purelib.resolve()) == "prefix"
    assert dirs.get(platlib.resolve()) == "prefix"
    _pin_root(monkeypatch, package)
    found = channel.detect()
    assert (found.id, found.install_scheme) == ("venv", "prefix")
    assert found.install_root == platlib.resolve()


def test_a_record_elsewhere_on_sys_path_does_not_vouch_for_this_site_dir(
    monkeypatch, tmp_path
):
    installed = tmp_path / "a" / "site-packages"
    loaded = tmp_path / "b" / "site-packages"
    _plant_package(installed)
    _plant_distribution(installed)
    _plant_package(loaded)
    monkeypatch.syspath_prepend(str(installed))
    assert channel._installed_here(installed) is True
    assert channel._installed_here(loaded) is False


def test_a_record_over_the_cap_is_refused_even_when_its_head_lists_the_package(
    monkeypatch, tmp_path
):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    (meta / "RECORD").write_text(
        "openai4s/__init__.py,,\n" + "x" * 64 + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(channel, "_MAX_RECORD_BYTES", 32)
    assert channel._installed_here(site) is False
    monkeypatch.setattr(channel, "_MAX_RECORD_BYTES", 1024)
    assert channel._installed_here(site) is True


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs POSIX FIFOs")
def test_a_fifo_with_a_writer_cannot_supply_the_record(tmp_path):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    (meta / "RECORD").unlink()
    os.mkfifo(meta / "RECORD")
    fd = os.open(meta / "RECORD", os.O_RDWR | os.O_NONBLOCK)
    try:
        os.write(fd, b"openai4s/__init__.py,,\n")
        assert channel._installed_here(site) is False
    finally:
        os.close(fd)


def test_a_dist_info_named_openai4s_that_declares_another_name_does_not_count(
    tmp_path,
):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    assert channel._installed_here(site) is True
    (meta / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: requests\nVersion: 2.32.0\n", encoding="utf-8"
    )
    assert channel._installed_here(site) is False


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks")
def test_a_symlinked_dist_info_directory_is_not_followed(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    real = _plant_distribution(elsewhere)
    assert channel._installed_here(elsewhere) is True
    site = tmp_path / "site-packages"
    site.mkdir()
    (site / real.name).symlink_to(real, target_is_directory=True)
    assert channel._installed_here(site) is False


def test_only_the_top_level_package_path_in_record_counts(tmp_path):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    (meta / "RECORD").write_text("vendor/openai4s/__init__.py,,\n", encoding="utf-8")
    assert channel._installed_here(site) is False


def test_a_name_in_the_metadata_body_is_not_the_declared_name(tmp_path):
    site = tmp_path / "site-packages"
    meta = _plant_distribution(site)
    (meta / "METADATA").write_text(
        "Metadata-Version: 2.1\nVersion: 0.3.0\n\nName: openai4s\n", encoding="utf-8"
    )
    assert channel._installed_here(site) is False


def test_a_package_directory_not_named_openai4s_is_never_a_bundle(
    monkeypatch, tmp_path
):
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    appdir.mkdir()
    _plant_landmarks(appdir)
    copy = appdir / "src" / "vendored_copy"
    (copy / "update").mkdir(parents=True)
    _pin_interpreter(monkeypatch, appdir / "runtime" / "bin" / "python3")
    _pin_root(monkeypatch, copy)
    assert channel._bundle_root() is None
    assert channel._probe_linux_bundle(None) is None
    _pin_root(monkeypatch, appdir / "src" / "openai4s")
    assert channel._bundle_root() == appdir.resolve()


# --------------------------------------------------------------------------- #
#  sysconfig's answer, uv's cache, PEP 668
# --------------------------------------------------------------------------- #


def test_a_sysconfig_answer_outside_the_prefix_is_not_this_interpreters(
    monkeypatch, tmp_path
):
    """A planted `.env` naming a planted `_sysconfigdata` module steers what
    `sysconfig` returns -- it initialises lazily, after `config._load_dotenv`
    has run. The answer is checked against `sys.prefix`/`sys.exec_prefix`,
    which were fixed when the interpreter started."""
    fake = tmp_path / "evilprefix" / "lib" / "python3.13" / "site-packages"
    package = _plant_package(fake)
    _plant_distribution(fake)
    _pin_root(monkeypatch, package)
    paths = {"purelib": str(fake), "platlib": str(fake)}
    monkeypatch.setattr(channel.sysconfig, "get_paths", lambda *a, **k: dict(paths))
    monkeypatch.setattr(channel, "_prefixes", lambda: (tmp_path / "real-prefix",))
    assert channel._purelib() is None
    assert fake.resolve() not in channel._site_dirs()
    found = channel.detect()
    assert found.self_update is False

    # Control: the same answer *inside* the prefix is this interpreter's.
    monkeypatch.setattr(channel, "_prefixes", lambda: (tmp_path / "evilprefix",))
    assert channel.detect().id == "venv"


def test_the_real_prefixes_contain_the_real_purelib():
    purelib = channel._purelib()
    assert purelib is not None
    assert channel._within_prefix(purelib)


@pytest.mark.parametrize(
    "prefix, cached",
    [
        ("uv/archive-v0/SS1-D07FRthreFZq", True),
        ("uv/builds-v0/.tmpAbC123", True),
        ("uv/environments-v2/openai4s-9f8e", True),
        ("projects/archive-2024/.venv", False),
        ("home/.venv", False),
    ],
)
def test_an_interpreter_in_uvs_cache_is_not_an_install(
    monkeypatch, tmp_path, prefix, cached
):
    """`uvx openai4s` runs from `<cache>/archive-v0/<hash>`: every other fact
    agrees it is a venv, and a write there lands in a shared cache entry that
    `uv cache prune` deletes and the next run may rebuild."""
    root = tmp_path / prefix
    site = root / "lib" / "python3.13" / "site-packages"
    package = _plant_package(site)
    _plant_distribution(site)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setattr(channel, "_prefixes", lambda: (root.resolve(),))
    monkeypatch.setattr(channel, "_in_uv_cache", _REAL_IN_UV_CACHE)
    assert _REAL_IN_UV_CACHE() is cached
    assert (channel._probe_venv(None) is None) is cached


@pytest.mark.parametrize("in_venv, refused", [(False, True), (True, False)])
def test_an_externally_managed_interpreter_is_not_updated_outside_a_venv(
    monkeypatch, tmp_path, in_venv, refused
):
    """PEP 668: pip refuses a prefix and a `--user` install into it, so a
    transaction that got as far as the commit would fail there instead."""
    stdlib = tmp_path / "stdlib"
    stdlib.mkdir()
    (stdlib / "EXTERNALLY-MANAGED").write_text(
        "[externally-managed]\n", encoding="utf-8"
    )
    monkeypatch.setattr(channel.sysconfig, "get_path", lambda *_a, **_k: str(stdlib))
    monkeypatch.setattr(channel, "_prefixes", lambda: (tmp_path.resolve(),))
    monkeypatch.setattr(channel, "_externally_managed", _REAL_EXTERNALLY_MANAGED)
    assert _REAL_EXTERNALLY_MANAGED() is True

    site = tmp_path / "site-packages"
    package = _plant_package(site)
    _plant_distribution(site)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    monkeypatch.setattr(channel, "_in_virtualenv", lambda: in_venv)
    assert (channel._probe_venv(None) is None) is refused

    (stdlib / "EXTERNALLY-MANAGED").unlink()
    assert _REAL_EXTERNALLY_MANAGED() is False


def test_the_unknown_refusal_names_the_ephemeral_uv_environments():
    assert "`uvx`" in channel._UNKNOWN_REFUSAL
    assert "EXTERNALLY-MANAGED" in channel._UNKNOWN_REFUSAL


# --------------------------------------------------------------------------- #
#  guards found by the fourth round
# --------------------------------------------------------------------------- #


def test_purelib_under_prefix_and_platlib_under_exec_prefix_both_count(
    monkeypatch, tmp_path
):
    """A split-prefix build keeps platlib under exec_prefix; refusing it would
    refuse a genuine install."""
    pure_prefix = tmp_path / "share"
    plat_prefix = tmp_path / "arch"
    purelib = pure_prefix / "lib" / "python3.13" / "site-packages"
    platlib = plat_prefix / "lib" / "python3.13" / "site-packages"
    purelib.mkdir(parents=True)
    platlib.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(pure_prefix))
    monkeypatch.setattr(sys, "exec_prefix", str(plat_prefix))
    paths = {"purelib": str(purelib), "platlib": str(platlib)}
    monkeypatch.setattr(channel.sysconfig, "get_paths", lambda *a, **k: dict(paths))
    monkeypatch.setattr(channel, "_in_virtualenv", lambda: True)
    dirs = channel._site_dirs()
    assert dirs.get(purelib.resolve()) == "prefix"
    assert dirs.get(platlib.resolve()) == "prefix"


@pytest.mark.skipif(os.name != "posix", reason="needs symlinks")
def test_a_symlinked_prefix_still_contains_its_own_site_dir(monkeypatch, tmp_path):
    """A venv reached through a symlinked home, or macOS's /tmp -> /private/tmp:
    the site dir is resolved, so the prefix must be too."""
    real = tmp_path / "real-venv"
    purelib = real / "lib" / "python3.13" / "site-packages"
    purelib.mkdir(parents=True)
    link = tmp_path / "linked-venv"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(sys, "prefix", str(link))
    monkeypatch.setattr(sys, "exec_prefix", str(link))
    linked = link / "lib" / "python3.13" / "site-packages"
    monkeypatch.setattr(
        channel.sysconfig,
        "get_paths",
        lambda *a, **k: {"purelib": str(linked), "platlib": str(linked)},
    )
    assert channel._purelib() == purelib.resolve()


def test_a_sibling_whose_name_extends_the_prefix_is_not_inside_it(
    monkeypatch, tmp_path
):
    """Containment is by path component: `<prefix>-evil` is not `<prefix>`."""
    real = tmp_path / "venv"
    real.mkdir()
    evil = tmp_path / "venv-evil" / "lib" / "python3.13" / "site-packages"
    package = _plant_package(evil)
    _plant_distribution(evil)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(
        channel.sysconfig,
        "get_paths",
        lambda *a, **k: {"purelib": str(evil), "platlib": str(evil)},
    )
    monkeypatch.setattr(channel, "_prefixes", lambda: (real.resolve(),))
    assert channel._purelib() is None
    assert channel.detect().self_update is False


@pytest.mark.parametrize(
    "prefix, cached",
    [
        ("data/my-archive-v2/.venv", False),
        ("ci/builds-v2-staging/.venv", False),
        ("uv/archive-v12/abc", True),
    ],
)
def test_only_a_whole_uv_bucket_name_marks_the_cache(
    monkeypatch, tmp_path, prefix, cached
):
    """A genuine venv under a directory that merely contains a bucket-like
    name must not be refused."""
    root = tmp_path / prefix
    root.mkdir(parents=True)
    monkeypatch.setattr(channel, "_prefixes", lambda: (root.resolve(),))
    assert _REAL_IN_UV_CACHE() is cached


def test_either_prefix_in_uvs_cache_is_enough(monkeypatch, tmp_path):
    monkeypatch.setattr(
        channel,
        "_prefixes",
        lambda: (
            tmp_path / "home" / ".venv",
            tmp_path / "uv" / "environments-v2" / "x",
        ),
    )
    assert _REAL_IN_UV_CACHE() is True


def test_a_broken_sysconfig_makes_the_pep668_check_refuse_not_raise(monkeypatch):
    """Fails closed, deliberately: sysconfig only raises here when the
    environment has been tampered with, and a refusal is the right answer."""

    def _broken(*_a, **_k):
        raise ModuleNotFoundError("_sysconfigdata_planted")

    monkeypatch.setattr(channel.sysconfig, "get_path", _broken)
    assert _REAL_EXTERNALLY_MANAGED() is True


def test_a_stdlib_answer_outside_the_prefix_cannot_hide_the_pep668_marker(
    monkeypatch, tmp_path
):
    """The planted `_sysconfigdata` that moves only `installed_base` points
    stdlib at a directory with no marker while purelib stays genuine."""
    real = tmp_path / "prefix"
    stdlib = real / "lib" / "python3.13"
    stdlib.mkdir(parents=True)
    (stdlib / "EXTERNALLY-MANAGED").write_text(
        "[externally-managed]\n", encoding="utf-8"
    )
    decoy = tmp_path / "decoy" / "lib" / "python3.13"
    decoy.mkdir(parents=True)
    monkeypatch.setattr(channel, "_prefixes", lambda: (real.resolve(),))
    monkeypatch.setattr(channel.sysconfig, "get_path", lambda *_a, **_k: str(decoy))
    assert _REAL_EXTERNALLY_MANAGED() is True
    monkeypatch.setattr(channel.sysconfig, "get_path", lambda *_a, **_k: str(stdlib))
    assert _REAL_EXTERNALLY_MANAGED() is True
    (stdlib / "EXTERNALLY-MANAGED").unlink()
    assert _REAL_EXTERNALLY_MANAGED() is False


def test_the_pep668_marker_is_read_from_the_stdlib_directory(monkeypatch, tmp_path):
    stdlib = tmp_path / "stdlib"
    other = tmp_path / "other"
    stdlib.mkdir()
    other.mkdir()
    (stdlib / "EXTERNALLY-MANAGED").write_text(
        "[externally-managed]\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        channel.sysconfig,
        "get_path",
        lambda name, *_a, **_k: str(stdlib if name == "stdlib" else other),
    )
    monkeypatch.setattr(channel, "_prefixes", lambda: (tmp_path.resolve(),))
    assert _REAL_EXTERNALLY_MANAGED() is True


# --------------------------------------------------------------------------- #
#  from 7ad14c1f (the parallel fix for the same review), adapted to the
#  stricter contract: a genuine venv carries pip's RECORD, and `_plant_bundle`
#  now pins the interpreter the shipped launcher runs
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("install", ["source", "venv"])
@pytest.mark.parametrize(
    "alias", [False, True], ids=["unrelated-package", "symlink-to-loaded-package"]
)
def test_ancestor_landmarks_cannot_reclassify_the_loaded_install(
    tmp_path, monkeypatch, install, alias
):
    ancestor = tmp_path / "home"
    site = ancestor / ("checkout" if install == "source" else "venv/lib/site-packages")
    package = _plant_package(site)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(
        channel, "_purelib", lambda: site if install == "venv" else None
    )
    if install == "source":
        (site / ".git").write_text("gitdir: elsewhere\n")
        (site / "pyproject.toml").write_text('[project]\nname = "openai4s"\n')
    else:
        _plant_distribution(site)
    (ancestor / "VERSION").write_text("0.3.0\n")
    (ancestor / "runtime/bin").mkdir(parents=True)
    (ancestor / "runtime/bin/python3").touch()
    if alias:
        (ancestor / "src").mkdir()
        (ancestor / "src/openai4s").symlink_to(package, target_is_directory=True)
    else:
        _plant_package(ancestor / "src")
    found = channel.detect()
    assert found.id == install
    assert found.install_root == site
    assert found.self_update is (install == "venv")


@pytest.mark.parametrize("install", ["source", "unmanaged"])
@pytest.mark.parametrize("signal", ["wsl", "bundle-id"])
def test_managed_bundle_requires_its_own_loaded_src_package(
    tmp_path, monkeypatch, signal, install
):
    bundled_package = _plant_bundle(tmp_path, monkeypatch)
    bundle = bundled_package.parents[1]
    site = bundle / "workspace"
    _pin_root(monkeypatch, _plant_package(site))
    monkeypatch.setattr(channel, "_purelib", lambda: None)
    if install == "source":
        (site / ".git").write_text("gitdir: elsewhere\n")
        (site / "pyproject.toml").write_text('[project]\nname = "openai4s"\n')
    (bundle / "VERSION").write_text("0.3.0\n")
    (bundle / "runtime/bin").mkdir(parents=True, exist_ok=True)
    (bundle / "runtime/bin/python3").touch()
    monkeypatch.setattr(channel, "_is_wsl", lambda: signal == "wsl")
    if signal == "bundle-id":
        monkeypatch.setenv("OPENAI4S_BUNDLE_ID", bundle.name)
    found = channel.detect()
    assert found.id == ("source" if install == "source" else "unknown")
    assert found.install_root == (site if install == "source" else None)
    assert found.self_update is False


def test_a_nested_tree_is_not_the_interpreters_site_package(tmp_path, monkeypatch):
    site = tmp_path / "site-packages"
    package = _plant_package(site / "unmanaged-copy")
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site)
    found = channel.detect()
    assert found.id == "unknown"
    assert found.self_update is False


@pytest.mark.parametrize("kind", ["linux", "wsl"])
def test_a_checkout_named_src_is_not_a_bundle(tmp_path, monkeypatch, kind):
    package = _plant_bundle(tmp_path, monkeypatch)
    site = package.parent
    bundle = site.parent
    _pin_root(monkeypatch, package)
    (site / ".git").write_text("gitdir: elsewhere\n")
    (site / "pyproject.toml").write_text('[project]\nname = "openai4s"\n')
    (bundle / "VERSION").write_text("0.3.0\n")
    (bundle / "runtime/bin").mkdir(parents=True, exist_ok=True)
    (bundle / "runtime/bin/python3").touch()
    monkeypatch.setattr(channel, "_is_wsl", lambda: kind == "wsl")
    found = channel.detect()
    assert found.id == "source"
    assert found.install_root == site
    assert found.self_update is False


def test_editable_checkout_metadata_cannot_override_git_ownership(
    tmp_path, monkeypatch
):
    """Holds even when the checkout's directory *is* a site dir holding pip's
    record -- which is what the venv probe's own `.git` guard is for."""
    site = tmp_path / "editable"
    package = _plant_package(site)
    _pin_root(monkeypatch, package)
    (site / ".git").write_text("gitdir: elsewhere\n")
    (site / "pyproject.toml").write_text(
        '[project]\nname = "openai4s" # valid TOML comment\n'
    )
    _plant_distribution(site)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    assert channel._probe_venv(None) is None
    assert channel.detect().self_update is False
