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


#: Captured at import, before `_no_ambient_container` replaces the module
#: attribute. A test *about* the reader itself has to call the reader, and
#: `channel._pid1_cgroup` is a stub for the whole of this file.
_REAL_PID1_CGROUP = channel._pid1_cgroup


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


@pytest.mark.parametrize("channel_id", sorted(channel.CHANNEL_IDS))
def test_every_channel_id_can_be_pinned_by_the_environment(monkeypatch, channel_id):
    monkeypatch.setenv("OPENAI4S_CHANNEL", channel_id)
    found = channel.detect()
    assert found.id == channel_id
    assert any(item.startswith("env:OPENAI4S_CHANNEL=") for item in found.evidence)


def test_an_override_the_probe_cannot_corroborate_says_so(monkeypatch, tmp_path):
    """An uncorroborated pin is still honoured — the operator asserted it — but
    the evidence records that nothing on disk agreed."""
    _pin_root(monkeypatch, _plant_package(tmp_path))
    monkeypatch.setenv("OPENAI4S_CHANNEL", "linux-bundle")
    found = channel.detect()
    assert found.id == "linux-bundle"
    assert "uncorroborated" in found.evidence


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


def _plant_bundle(tmp_path: Path) -> Path:
    tree = tmp_path / "app" / ("OpenAI4S-" + "a" * 64)
    package = _plant_package(tree / "src")
    (tree / ".installed").write_text("", encoding="utf-8")
    return package


def test_a_managed_bundle_needs_a_second_fact_beyond_its_shape(monkeypatch, tmp_path):
    """The content-addressed tree alone is also what a Linux bundle looks like
    after bootstrap.sh installed it, so the shape is not sufficient."""
    package = _plant_bundle(tmp_path)
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_is_wsl", lambda: False)
    assert channel._probe_managed_bundle(None) is None

    monkeypatch.setenv("OPENAI4S_BUNDLE_ID", "OpenAI4S-" + "a" * 64)
    found = channel.detect()
    assert found.id == "wsl-bundle"
    assert "env:OPENAI4S_BUNDLE_ID=set" in found.evidence


def test_wsl_corroborates_a_managed_bundle_without_the_variable(monkeypatch, tmp_path):
    _pin_root(monkeypatch, _plant_bundle(tmp_path))
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    found = channel.detect()
    assert found.id == "wsl-bundle"
    assert "wsl=true" in found.evidence


def test_the_wsl_bundle_installs_but_cannot_restart(monkeypatch, tmp_path):
    """One boolean meaning both would make the refusal unreadable."""
    _pin_root(monkeypatch, _plant_bundle(tmp_path))
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    found = channel.detect()
    assert found.self_update is True
    assert found.restart_supported is False
    assert found.refusal_command == ("OpenAI4S.cmd stop",)
    channel.require_self_update(found)  # installs: must not raise


def test_a_missing_installed_marker_is_not_a_bundle(monkeypatch, tmp_path):
    package = _plant_bundle(tmp_path)
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
    monkeypatch.setattr(channel, "_is_wsl", lambda: True)
    assert channel._probe_managed_bundle(None) is None


def test_the_relocatable_linux_bundle_needs_all_three_landmarks(monkeypatch, tmp_path):
    appdir = tmp_path / "OpenAI4S-0.3.0-linux-x86_64"
    package = _plant_package(appdir / "src")
    _pin_root(monkeypatch, package)
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
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
    found = channel.detect()
    assert found.id == "venv"
    assert found.self_update is True
    assert found.restart_supported is True
    assert found.install_root == site.resolve()
    channel.require_self_update(found)  # must not raise


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0,
    reason="root can write an unwritable directory, so the negative is not real",
)
def test_a_site_packages_this_euid_cannot_write_is_not_a_venv(monkeypatch, tmp_path):
    site = tmp_path / "site-packages"
    package = _plant_package(site)
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
    import importlib.metadata as _metadata

    package = _plant_package(tmp_path / "unpacked")
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "elsewhere")
    monkeypatch.setattr(channel, "_writable", lambda _path: True)
    monkeypatch.setattr(channel, "_exists", lambda _path: False)

    def _absent(_name):
        raise _metadata.PackageNotFoundError("openai4s")

    monkeypatch.setattr(_metadata, "distribution", _absent)
    assert channel._probe_venv(None) is None
    assert channel.detect().id == "unknown"


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
    _pin_root(monkeypatch, package)
    monkeypatch.setattr(channel, "_purelib", lambda: site.resolve())
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
        built = channel._build(channel_id, None, [])
        assert built.self_update == (channel_id in channel.SELF_UPDATE_CHANNELS)
        assert built.supported == built.self_update


def test_the_projection_leaves_evidence_out_unless_it_is_asked(tmp_path):
    """Evidence carries absolute paths: a home directory, a username, often a
    project name. Forgetting to think about it has to be the safe outcome."""
    built = channel._build("venv", tmp_path, [f"site_packages={tmp_path}"])
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


@pytest.mark.parametrize("same_tree", [False, True])
def test_distribution_metadata_must_describe_the_loaded_package(
    monkeypatch, tmp_path, same_tree
):
    import importlib.metadata as metadata

    loaded = _plant_package(tmp_path / "loaded")
    installed = loaded if same_tree else _plant_package(tmp_path / "installed")
    _pin_root(monkeypatch, loaded)
    monkeypatch.setattr(channel, "_purelib", lambda: tmp_path / "elsewhere")

    class Distribution:
        def locate_file(self, name):
            return installed.parent / name

    monkeypatch.setattr(metadata, "distribution", lambda _name: Distribution())
    found = channel.detect()
    assert found.id == ("venv" if same_tree else "unknown")
    assert found.self_update is same_tree
