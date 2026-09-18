"""The matplotlib font list a sandboxed kernel starts with, and who built it.

An enforced sandbox gives every kernel an empty private MPLCONFIGDIR, so the
first plot in each new kernel rebuilt matplotlib's font list from scratch --
8-48 seconds on macOS, where the scan shells out to `system_profiler`. The cure
must not become a channel between kernels: the list a kernel is seeded with is
built by the host (the kernel's interpreter, confined, running a fixed program)
and copied *into* each new kernel's private temp, never read back out of one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.kernel.manager as manager_module
from openai4s.kernel import Kernel, font_cache
from openai4s.security.sandbox import KernelSandbox, SandboxStatus

INTERPRETER = "/opt/example-env/bin/python"
#: The stand-in matplotlib `font_manager.py` the fake builder reports.
_STANDIN_SOURCE: list[Path] = []


@pytest.fixture(autouse=True)
def _isolated_font_cache_state(tmp_path, monkeypatch):
    """Font roots under tmp, and no build enabled or remembered across tests."""

    roots = [tmp_path / "fonts-system", tmp_path / "fonts-user"]
    for root in roots:
        root.mkdir()
    monkeypatch.setattr(font_cache, "_font_roots", lambda platform, home: roots)
    source = tmp_path / "mpl-install" / "matplotlib" / "font_manager.py"
    source.parent.mkdir(parents=True)
    source.write_text("__version__ = '3.11.0'\n", encoding="utf-8")
    _STANDIN_SOURCE[:] = [source]
    font_cache.disable_background_builds()
    font_cache._inflight.clear()
    font_cache._failed_at.clear()
    yield roots
    font_cache.disable_background_builds()
    font_cache._inflight.clear()
    font_cache._failed_at.clear()


def _fontlist(version: object = "3.11.0", **extra) -> str:
    return json.dumps(
        {
            "__class__": "FontManager",
            "_version": version,
            "_FontManager__default_weight": "normal",
            "default_size": None,
            "defaultFamily": {"ttf": "DejaVu Sans", "afm": "Helvetica"},
            "afmlist": [],
            "ttflist": [],
            **extra,
        }
    )


def _source_fields(source: Path) -> dict:
    """What the builder reports about the font manager it imported."""

    info = source.stat()
    return {"source": str(source), "source_stat": [info.st_mtime_ns, info.st_size]}


def _raw_runner(payload, *, calls: list | None = None):
    def run(command, **kwargs):
        if calls is not None:
            calls.append((list(command), kwargs))
        body = payload() if callable(payload) else payload
        return SimpleNamespace(
            returncode=0,
            stdout=("noise\n" + font_cache._MARKER + json.dumps(body) + "\n").encode(),
            stderr=b"",
        )

    return run


def _builder_runner(payload: dict | None = None, *, calls: list | None = None):
    """A builder that reports the stand-in install as it is when it runs."""

    def body() -> dict:
        reported = dict(
            payload or {"name": "fontlist-v3.11.0.json", "content": _fontlist()}
        )
        if "content" in reported and "source" not in reported:
            reported.update(_source_fields(_STANDIN_SOURCE[0]))
        return reported

    return _raw_runner(body, calls=calls)


def _enforced_sandbox(tmp_path: Path, name: str = "kernel") -> KernelSandbox:
    workspace = tmp_path / f"{name}-workspace"
    workspace.mkdir()
    temp = tmp_path / f"{name}-temp"
    temp.mkdir(mode=0o700)
    status = SandboxStatus(
        mode="enforce",
        state="enabled",
        backend="seatbelt",
        enforced=True,
        self_test_passed=True,
        network_policy="blocked",
        workspace=str(workspace),
        temp_dir=str(temp),
        detail="offline test sandbox",
    )
    return KernelSandbox(
        status=status,
        executable="/usr/bin/sandbox-exec",
        temp_dir=str(temp),
        owns_temp_dir=True,
    )


def _kernel_mplconfigdir(sandbox: KernelSandbox) -> Path:
    return Path(sandbox.apply_environment({})["MPLCONFIGDIR"])


def test_an_enforced_kernel_is_seeded_with_the_host_built_font_list(tmp_path):
    data_dir = tmp_path / "data"
    calls: list = []
    built = font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner(calls=calls)
    )
    assert built is not None and built.name == "fontlist-v3.11.0.json"
    # The host ran the kernel's own interpreter on a fixed program.
    assert calls[0][0] == [INTERPRETER, "-c", font_cache._BUILDER]

    sandbox = _enforced_sandbox(tmp_path)
    try:
        assert font_cache.seed_kernel_font_cache(
            sandbox, interpreter=INTERPRETER, data_dir=data_dir
        )
        seeded = _kernel_mplconfigdir(sandbox) / "fontlist-v3.11.0.json"
        assert seeded.read_text(encoding="utf-8") == built.read_text(encoding="utf-8")
        assert not seeded.is_symlink()
    finally:
        sandbox.close()


def test_a_font_list_a_kernel_wrote_never_reaches_another_kernel(tmp_path):
    data_dir = tmp_path / "data"
    built = font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner()
    )
    assert built is not None
    trusted = built.read_bytes()

    first = _enforced_sandbox(tmp_path, "first")
    assert font_cache.seed_kernel_font_cache(
        first, interpreter=INTERPRETER, data_dir=data_dir
    )
    # A Cell in the first kernel rewrites its own copy -- a poisoned list, and
    # a fresh one for a matplotlib version the host never built.
    own = _kernel_mplconfigdir(first)
    (own / "fontlist-v3.11.0.json").write_text(
        _fontlist(ttflist=[{"fname": "/etc/passwd"}]), encoding="utf-8"
    )
    (own / "fontlist-v9.9.9.json").write_text(_fontlist("9.9.9"), encoding="utf-8")
    first.close()

    second = _enforced_sandbox(tmp_path, "second")
    try:
        assert font_cache.seed_kernel_font_cache(
            second, interpreter=INTERPRETER, data_dir=data_dir
        )
        theirs = _kernel_mplconfigdir(second)
        assert sorted(path.name for path in theirs.iterdir()) == [
            "fontlist-v3.11.0.json"
        ]
        assert (theirs / "fontlist-v3.11.0.json").read_bytes() == trusted
    finally:
        second.close()
    assert built.read_bytes() == trusted
    assert sorted(path.name for path in built.parent.iterdir()) == [
        "fontlist-v3.11.0.json",
        "manifest.json",
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "../fontlist-v3.11.0.json", "content": _fontlist()},
        {"name": "fontlist-v3.11.0.json", "content": _fontlist("3.10.0")},
        {
            "name": "fontlist-v3.11.0.json",
            "content": json.dumps({"__class__": "FontEntry", "_version": "3.11.0"}),
        },
        {"name": "fontlist-v3.11.0.json", "content": "{not json"},
        {"name": "fontlist-v3.11.0.json", "content": _fontlist(pad="x" * 4096)},
        {"error": "ModuleNotFoundError"},
        {"name": "fontlist-v390.json", "content": _fontlist(391)},
        # Neither spelling matplotlib has used: its version is a str (3.11+)
        # or an int (up to 3.10), never a float that happens to format alike.
        {"name": "fontlist-v390.0.json", "content": _fontlist(390.0)},
    ],
    ids=[
        "path",
        "version-mismatch",
        "not-a-fontmanager",
        "not-json",
        "oversize",
        "error",
        "integer-version-mismatch",
        "float-version",
    ],
)
def test_builder_output_that_is_not_a_font_list_is_not_stored(
    tmp_path, monkeypatch, payload
):
    monkeypatch.setattr(font_cache, "MAX_FONTLIST_BYTES", 1024)
    data_dir = tmp_path / "data"
    assert (
        font_cache.build_font_cache(
            INTERPRETER, data_dir=data_dir, runner=_builder_runner(payload)
        )
        is None
    )
    assert not (data_dir / "cache").exists()


def test_a_matplotlib_before_3_11_list_is_stored_as_written_and_seeded(tmp_path):
    """matplotlib up to 3.10 versions its font list with an int.

    ``FontManager.__version__ = 390`` there, so the list is
    ``fontlist-v390.json`` carrying ``"_version": 390``. Refusing it left the
    py3.10 floor (and any environment still on 3.10) scanning in every
    enforced kernel, with a doomed rebuild every ``RETRY_FAILED_BUILD_AFTER_S``.
    It is stored byte for byte: matplotlib 3.10 loads a list only when
    ``_version == 390``, so a copy re-serialised with a string version would be
    ignored inside the kernel.
    """

    data_dir = tmp_path / "data"
    content = _fontlist(390)
    built = font_cache.build_font_cache(
        INTERPRETER,
        data_dir=data_dir,
        runner=_builder_runner({"name": "fontlist-v390.json", "content": content}),
    )
    assert built is not None and built.name == "fontlist-v390.json"
    assert built.read_text(encoding="utf-8") == content
    manifest = json.loads((built.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fontlist"] == "fontlist-v390.json"

    sandbox = _enforced_sandbox(tmp_path)
    try:
        assert font_cache.seed_kernel_font_cache(
            sandbox, interpreter=INTERPRETER, data_dir=data_dir
        )
        seeded = _kernel_mplconfigdir(sandbox) / "fontlist-v390.json"
        assert seeded.read_text(encoding="utf-8") == content
        assert json.loads(content)["_version"] == 390
    finally:
        sandbox.close()


@pytest.mark.parametrize(
    "report",
    ["missing", "relative", "vanished", "changed-during-build"],
)
def test_a_list_whose_matplotlib_install_cannot_be_pinned_is_not_stored(
    tmp_path, monkeypatch, report
):
    source = _STANDIN_SOURCE[0]
    fields = _source_fields(source)
    if report == "missing":
        fields = {}
    elif report == "relative":
        # It even resolves from the host's cwd -- but the builder ran in
        # another directory, so what it named is not knowable here.
        monkeypatch.chdir(source.parent.parent)
        fields["source"] = "matplotlib/font_manager.py"
    elif report == "vanished":
        fields["source"] = str(tmp_path / "gone" / "font_manager.py")
    else:
        # The builder imported one install and the host now sees another.
        fields["source_stat"] = [fields["source_stat"][0], fields["source_stat"][1] + 1]
    payload = {"name": "fontlist-v3.11.0.json", "content": _fontlist(), **fields}
    data_dir = tmp_path / "data"
    assert (
        font_cache.build_font_cache(
            INTERPRETER, data_dir=data_dir, runner=_raw_runner(payload)
        )
        is None
    )
    assert not (data_dir / "cache").exists()


def test_upgrading_matplotlib_rebuilds_instead_of_seeding_the_old_list(tmp_path):
    """A new matplotlib names its font list after its own version.

    A list built for the old one is then ignored inside the kernel, which
    scans again -- in every new kernel, for up to ``MAX_CACHE_AGE_S`` -- unless
    the host notices the install it was built from has changed.
    """

    data_dir = tmp_path / "data"
    assert font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner()
    )
    first = _enforced_sandbox(tmp_path, "first")
    try:
        assert font_cache.seed_kernel_font_cache(
            first, interpreter=INTERPRETER, data_dir=data_dir
        )
    finally:
        first.close()

    # `pip install -U matplotlib` in that environment, then a new kernel.
    source = _STANDIN_SOURCE[0]
    source.write_text("__version__ = '3.12.0'  # upgraded\n", encoding="utf-8")
    os.utime(source, ns=(2_000_000_000, 2_000_000_000))
    font_cache.enable_background_builds(data_dir)
    calls: list = []
    second = _enforced_sandbox(tmp_path, "second")
    try:
        assert not font_cache.seed_kernel_font_cache(
            second,
            interpreter=INTERPRETER,
            data_dir=data_dir,
            runner=_builder_runner(calls=calls),
        )
        assert not _kernel_mplconfigdir(second).exists()
    finally:
        second.close()

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (not calls or font_cache._inflight):
        time.sleep(0.01)
    assert len(calls) == 1, "the upgrade did not start a rebuild"
    third = _enforced_sandbox(tmp_path, "third")
    try:
        assert font_cache.seed_kernel_font_cache(
            third, interpreter=INTERPRETER, data_dir=data_dir
        )
    finally:
        third.close()


def test_a_newly_installed_font_stops_the_stale_list_being_seeded(
    tmp_path, _isolated_font_cache_state
):
    data_dir = tmp_path / "data"
    assert font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner()
    )
    user_fonts = _isolated_font_cache_state[1]
    (user_fonts / "NotoSansCJK.otf").write_bytes(b"font")
    # Pin a different mtime explicitly: a coarse filesystem clock must not be
    # what decides whether this test can see the install.
    os.utime(user_fonts, ns=(1_000_000_000, 1_000_000_000))

    sandbox = _enforced_sandbox(tmp_path)
    try:
        assert not font_cache.seed_kernel_font_cache(
            sandbox, interpreter=INTERPRETER, data_dir=data_dir
        )
        assert not _kernel_mplconfigdir(sandbox).exists()
    finally:
        sandbox.close()


def test_a_cache_inside_the_kernels_own_workspace_is_not_trusted(tmp_path):
    sandbox = _enforced_sandbox(tmp_path)
    data_dir = Path(sandbox.status.workspace) / "data"
    assert font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner()
    )
    try:
        assert not font_cache.seed_kernel_font_cache(
            sandbox, interpreter=INTERPRETER, data_dir=data_dir
        )
        assert not _kernel_mplconfigdir(sandbox).exists()
    finally:
        sandbox.close()


def test_a_symlinked_cache_file_is_not_followed(tmp_path):
    data_dir = tmp_path / "data"
    built = font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner()
    )
    assert built is not None
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_bytes(built.read_bytes())
    built.unlink()
    built.symlink_to(elsewhere)

    sandbox = _enforced_sandbox(tmp_path)
    try:
        assert not font_cache.seed_kernel_font_cache(
            sandbox, interpreter=INTERPRETER, data_dir=data_dir
        )
    finally:
        sandbox.close()


def test_an_unconfined_kernel_keeps_its_shared_runtime_cache(tmp_path):
    data_dir = tmp_path / "data"
    assert font_cache.build_font_cache(
        INTERPRETER, data_dir=data_dir, runner=_builder_runner()
    )
    status = SandboxStatus(
        mode="off",
        state="disabled",
        backend=None,
        enforced=False,
        self_test_passed=None,
        network_policy="not_enforced",
        workspace=str(tmp_path),
        temp_dir=None,
        detail="off",
    )
    assert not font_cache.seed_kernel_font_cache(
        KernelSandbox(status=status), interpreter=INTERPRETER, data_dir=data_dir
    )


def test_builds_only_start_where_enabled_and_run_once_at_a_time(tmp_path):
    data_dir = tmp_path / "data"
    sandbox = _enforced_sandbox(tmp_path)
    calls: list = []
    release = threading.Event()
    build = _builder_runner()

    def slow_builder(command, **kwargs):
        calls.append(list(command))
        release.wait(10)
        return build(command, **kwargs)

    try:
        # Not enabled (a CLI one-shot, a test): a cache miss starts nothing.
        assert not font_cache.seed_kernel_font_cache(
            sandbox, interpreter=INTERPRETER, data_dir=data_dir, runner=slow_builder
        )
        assert font_cache.request_build(INTERPRETER, runner=slow_builder) is None
        assert calls == []

        font_cache.enable_background_builds(data_dir)
        thread = font_cache.request_build(INTERPRETER, runner=slow_builder)
        assert thread is not None
        # A second spawn while the first build is still scanning joins nothing.
        assert font_cache.request_build(INTERPRETER, runner=slow_builder) is None
        release.set()
        thread.join(10)
        assert not thread.is_alive()
        assert len(calls) == 1
        assert font_cache.seed_kernel_font_cache(sandbox, interpreter=INTERPRETER)
    finally:
        release.set()
        sandbox.close()


def test_a_failed_build_is_not_retried_on_every_spawn(tmp_path):
    font_cache.enable_background_builds(tmp_path / "data")
    calls: list = []
    runner = _builder_runner({"error": "ModuleNotFoundError"}, calls=calls)
    thread = font_cache.request_build(INTERPRETER, runner=runner)
    assert thread is not None
    thread.join(10)
    assert font_cache.request_build(INTERPRETER, runner=runner) is None
    assert len(calls) == 1


#: A child that leaves its parent's process group, as macOS `system_profiler`'s
#: scan helper does, then writes its pid to the file named by its argument.
_ESCAPED_CHILD = (
    "import os, sys, time; os.setpgid(0, 0); "
    "handle = open(sys.argv[1] + '.tmp', 'w'); handle.write(str(os.getpid())); "
    "handle.close(); os.replace(sys.argv[1] + '.tmp', sys.argv[1]); time.sleep(120)"
)


def _slow_builder_interpreter(root: Path, report: Path) -> Path:
    """An interpreter whose font scan outlasts any daemon that starts it.

    It runs the real builder program on this Python, against a stand-in
    matplotlib whose font manager starts a scan child the way macOS
    `system_profiler` does -- in a process group of its own, holding the
    builder's stdout and stderr -- reports both pids and the builder's private
    cache directory, and then sleeps where the real one would still be walking
    fonts.
    """

    site = root / "site"
    (site / "matplotlib").mkdir(parents=True)
    (site / "matplotlib" / "__init__.py").write_text(
        "import os\n" "def get_cachedir():\n" "    return os.environ['MPLCONFIGDIR']\n",
        encoding="utf-8",
    )
    (site / "matplotlib" / "font_manager.py").write_text(
        "import json, os, subprocess, sys, time\n"
        f"report = {str(report)!r}\n"
        f"scan = subprocess.Popen([sys.executable, '-c', {_ESCAPED_CHILD!r},"
        " report + '.scan'])\n"
        "while not os.path.exists(report + '.scan'):\n"
        "    time.sleep(0.01)\n"
        "state = {'pid': os.getpid(), 'child': scan.pid,"
        " 'cache': os.environ['MPLCONFIGDIR']}\n"
        "with open(report + '.tmp', 'w', encoding='utf-8') as handle:\n"
        "    json.dump(state, handle)\n"
        "os.replace(report + '.tmp', report)\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )
    interpreter = root / "env" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text(
        "#!/bin/sh\n"
        f'PYTHONPATH={shlex.quote(str(site))} exec {shlex.quote(sys.executable)} "$@"\n',
        encoding="utf-8",
    )
    interpreter.chmod(0o755)
    return interpreter


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.fixture
def _probe_temp(tmp_path, monkeypatch):
    """Unconfined probes whose temp directories land where the test can look.

    The sandbox is off so the lifecycle is the same on a Linux runner without
    bubblewrap as on a Mac; an enforced sandbox only changes where the builder's
    own temp lives, not who stops it.
    """

    if not hasattr(os, "killpg"):
        pytest.skip("needs POSIX process groups")
    temp = tmp_path / "tmp"
    temp.mkdir()
    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    monkeypatch.setenv("TMPDIR", str(temp))
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    return temp


class _StoppingDaemon:
    """``run_server``'s httpd: a kernel spawn starts a build, then a stop arrives."""

    def __init__(self, interpreter: Path, data_dir: Path, ready) -> None:
        self.interpreter = interpreter
        self.data_dir = data_dir
        self.ready = ready
        self.builds: list = []
        self.stop_requested_at: float | None = None

    def serve_forever(self) -> None:
        # A sandboxed kernel spawn finds no font list (seed_kernel_font_cache)...
        self.builds.append(
            font_cache.request_build(str(self.interpreter), data_dir=self.data_dir)
        )
        assert self.ready(), "the build never reached the point under test"
        # ...and `openai4s stop`'s SIGTERM arrives as KeyboardInterrupt.
        self.stop_requested_at = time.monotonic()
        raise KeyboardInterrupt

    def shutdown(self) -> None:
        pass

    def server_close(self) -> None:
        pass


def _kill_leftover_builder(report: Path) -> None:
    """Teardown after a failure: the builder the test started is still alive."""

    if not report.exists():
        return
    state = json.loads(report.read_text(encoding="utf-8"))
    for pid in (state["pid"], state["child"]):
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return predicate()
        time.sleep(0.02)
    return True


def test_stopping_the_daemon_stops_its_font_list_build(tmp_path, _probe_temp):
    """`openai4s stop` must not leave the daemon's font scan running.

    A build is a confined child in its own session, waited on from a daemon
    thread. The daemon exiting froze that thread mid-wait: the builder ran on,
    re-parented to PID 1, for the rest of its scan (up to ``BUILD_TIMEOUT_S``),
    its result discarded and its probe workspace never removed, after `stop`
    had reported the daemon stopped. Driven through the real ``run_server``
    teardown and the real confined probe.
    """

    from openai4s.server import gateway

    report = tmp_path / "builder.json"
    interpreter = _slow_builder_interpreter(tmp_path / "slow", report)
    data_dir = tmp_path / "data"
    daemon = _StoppingDaemon(
        interpreter, data_dir, lambda: _wait_for(report.exists, 60)
    )
    passed = False
    try:
        gateway.run_server(daemon)
        # Nothing may be left for after `run_server`: the daemon process exits
        # there, and a thread still waiting on its builder dies with it.
        returned_at = time.monotonic()
        build = daemon.builds[0]
        assert build is not None
        builder = json.loads(report.read_text(encoding="utf-8"))
        assert not build.is_alive(), "the font-list builder outlived the daemon"
        # Stopped, not waited out: the stand-in scan had two minutes to go.
        assert returned_at - daemon.stop_requested_at < 10
        # Everything it started, not only the interpreter: a scan child that
        # left its process group and held the builder's pipes too.
        assert _wait_for(lambda: not _pid_alive(builder["child"]), 5)
        assert not _pid_alive(builder["pid"])
        assert not Path(builder["cache"]).exists()
        assert sorted(path.name for path in _probe_temp.iterdir()) == []
        assert not (data_dir / "cache").exists()
        passed = True
    finally:
        if not passed:
            _kill_leftover_builder(report)
        for thread in daemon.builds:
            if thread is not None:
                thread.join(10)


def test_a_build_that_had_not_spawned_when_the_daemon_stopped_never_scans(
    tmp_path, _probe_temp, monkeypatch
):
    """A build between its request and its spawn when shutdown begins.

    The window is real: the directory fingerprint and the sandbox self-test run
    first. Stopping only the builders already running would let this one start
    its scan after the daemon's teardown had finished.
    """

    from openai4s.server import gateway

    monkeypatch.setattr(font_cache, "SHUTDOWN_TIMEOUT_S", 0.2, raising=False)
    entered = threading.Event()
    release = threading.Event()
    fingerprint = font_cache.font_directory_fingerprint

    def held_fingerprint(*args, **kwargs):
        entered.set()
        release.wait(60)
        return fingerprint(*args, **kwargs)

    monkeypatch.setattr(font_cache, "font_directory_fingerprint", held_fingerprint)
    report = tmp_path / "builder.json"
    interpreter = _slow_builder_interpreter(tmp_path / "slow", report)
    daemon = _StoppingDaemon(interpreter, tmp_path / "data", lambda: entered.wait(60))
    passed = False
    try:
        gateway.run_server(daemon)
        build = daemon.builds[0]
        assert build is not None
        # The build reaches its spawn only after the daemon's teardown returned.
        release.set()
        build.join(10)
        assert not build.is_alive(), "a build spawned after shutdown ran its scan"
        if report.exists():
            state = json.loads(report.read_text(encoding="utf-8"))
            assert _wait_for(lambda: not _pid_alive(state["child"]), 5)
            assert not _pid_alive(state["pid"])
        assert sorted(path.name for path in _probe_temp.iterdir()) == []
        passed = True
    finally:
        release.set()
        if not passed:
            _kill_leftover_builder(report)
        for thread in daemon.builds:
            if thread is not None:
                thread.join(10)


def _fake_matplotlib(root: Path) -> Path:
    package = root / "matplotlib"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "import os\n" "def get_cachedir():\n" "    return os.environ['MPLCONFIGDIR']\n",
        encoding="utf-8",
    )
    (package / "font_manager.py").write_text(
        "import json, os\n"
        "import matplotlib\n"
        "class FontManager:\n"
        "    __version__ = '3.11.0'\n"
        "path = os.path.join(matplotlib.get_cachedir(), 'fontlist-v3.11.0.json')\n"
        "with open(path, 'w', encoding='utf-8') as handle:\n"
        f"    handle.write({_fontlist()!r})\n",
        encoding="utf-8",
    )
    return root


def test_the_builder_program_returns_the_list_matplotlib_wrote(tmp_path):
    """The fixed program the host runs, against a stand-in matplotlib.

    It must hand back what the font manager wrote into the fresh directory it
    created -- not a pre-existing cache the environment pointed it at.
    """

    fake = _fake_matplotlib(tmp_path / "site")
    stale = tmp_path / "stale-shared-cache"
    stale.mkdir()
    (stale / "fontlist-v3.11.0.json").write_text("poison", encoding="utf-8")

    def runner(command, *, timeout):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(fake),
            "MPLCONFIGDIR": str(stale),
            "TMPDIR": str(tmp_path),
        }
        return subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            env=env,
            check=False,
        )

    built = font_cache.build_font_cache(
        sys.executable, data_dir=tmp_path / "data", runner=runner
    )
    assert built is not None
    assert json.loads(built.read_text(encoding="utf-8"))["__class__"] == "FontManager"
    assert "poison" not in built.read_text(encoding="utf-8")
    # ...and the manifest pins the font manager install it came from.
    manifest = json.loads((built.parent / "manifest.json").read_text(encoding="utf-8"))
    assert (
        Path(manifest["matplotlib_source"]).resolve()
        == (fake / "matplotlib" / "font_manager.py").resolve()
    )


def _shadowing_directory(root: Path) -> Path:
    """A directory whose modules would answer for the stdlib and matplotlib.

    Each one prints a forged, perfectly valid font list and exits cleanly, so
    the only thing that keeps it out of the host cache is never importing it.
    A daemon launched from a directory a Cell can write (a CLI kernel's
    workspace is its launch directory) is exactly such a place.
    """

    root.mkdir(parents=True)
    forged = font_cache._MARKER + json.dumps(
        {
            "name": "fontlist-v3.11.0.json",
            "content": _fontlist(defaultFamily={"ttf": "POISONED", "afm": "POISONED"}),
        }
    )
    hijack = f"print({forged!r}, flush=True)\nimport os\nos._exit(0)\n"
    for name in ("json", "shutil", "signal", "tempfile"):
        (root / f"{name}.py").write_text(hijack, encoding="utf-8")
    (root / "matplotlib").mkdir()
    (root / "matplotlib" / "__init__.py").write_text(hijack, encoding="utf-8")
    return root


def test_the_builder_program_never_imports_from_its_working_directory(tmp_path):
    """`python -c` puts the working directory first on `sys.path`.

    The builder program itself must drop it before any import that is not
    already loaded, whatever directory a runner leaves it in.
    """

    fake = _fake_matplotlib(tmp_path / "site")
    poisoned = _shadowing_directory(tmp_path / "poisoned-cwd")

    def runner(command, *, timeout):
        env = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(fake),
            "TMPDIR": str(tmp_path),
        }
        return subprocess.run(
            command,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=poisoned,
            check=False,
        )

    built = font_cache.build_font_cache(
        sys.executable, data_dir=tmp_path / "data", runner=runner
    )
    assert built is not None, "the builder failed against the stand-in matplotlib"
    assert "POISONED" not in built.read_text(encoding="utf-8")


def test_a_daemon_launched_from_a_poisoned_directory_stores_no_font_list(
    tmp_path, monkeypatch
):
    """End to end through the real confined probe, from a shadowing cwd.

    The interpreter is a fresh virtualenv with no matplotlib, so the honest
    outcome is a failed build and an empty cache -- in milliseconds, not a
    real font scan. A forged list in the host cache would instead be seeded
    into every later enforced kernel on that interpreter.
    """

    venv = tmp_path / "venv"
    created = subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv)],
        capture_output=True,
        timeout=120,
        check=False,
    )
    interpreter = venv / "bin" / "python"
    if created.returncode != 0 or not interpreter.exists():
        pytest.skip(f"cannot create a virtualenv here: {created.stderr!r}")
    data_dir = tmp_path / "data"
    monkeypatch.chdir(_shadowing_directory(tmp_path / "daemon-cwd"))

    assert (
        font_cache.build_font_cache(str(interpreter), data_dir=data_dir, timeout=120)
        is None
    )
    assert not (data_dir / "cache").exists()


def test_a_python_kernel_asks_for_its_font_list_with_its_own_sandbox(
    tmp_path, monkeypatch
):
    seeded: list = []
    monkeypatch.setattr(
        manager_module,
        "seed_kernel_font_cache",
        lambda sandbox, *, interpreter: seeded.append((sandbox, interpreter)),
    )
    with Kernel(cwd=str(tmp_path)) as kernel:
        assert kernel.execute("x = 1")["error"] is None
        assert seeded == [(kernel._sandbox, sys.executable)]


def _font_manager_source() -> Path:
    spec = importlib.util.find_spec("matplotlib")
    assert spec is not None and spec.submodule_search_locations
    return Path(list(spec.submodule_search_locations)[0]) / "font_manager.py"


def _parse_font_manager_version(source: str) -> int | str | None:
    """``FontManager.__version__`` as matplotlib's own source spells it.

    An int up to 3.10 (``__version__ = 390``), a string from 3.11 on. Typed as
    written: the list's ``_version`` must compare equal to it inside the kernel.
    """

    match = re.search(
        r"^\s+__version__\s*=\s*(\d+|'[^']+'|\"[^\"]+\")\s*(?:#.*)?$",
        source,
        re.MULTILINE,
    )
    if match is None:
        return None
    literal = match.group(1)
    return int(literal) if literal.isdigit() else literal[1:-1]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("    __version__ = 390\n", 390),
        ("    __version__ = '3.11.0'\n", "3.11.0"),
        ('    __version__ = "3.12.0"  # a comment\n', "3.12.0"),
        ("    __version__ = _version.version\n", None),
    ],
    ids=["int-before-3.11", "str-3.11", "double-quoted", "not-a-literal"],
)
def test_the_end_to_end_test_reads_every_font_manager_version_spelling(line, expected):
    """A version it cannot read makes the end-to-end test skip, not fail.

    It only understood the quoted 3.11 form, so on matplotlib 3.10 -- the
    py3.10 floor -- it skipped as "matplotlib is not installed" and the CI leg
    where seeding did nothing stayed green.
    """

    source = f"class FontManager:\n{line}    def __init__(self):\n        pass\n"
    parsed = _parse_font_manager_version(source)
    assert parsed == expected and type(parsed) is type(expected)


def test_a_sandboxed_kernel_uses_the_seeded_list_instead_of_scanning(
    tmp_path, monkeypatch
):
    """End to end on a host where the sandbox is really enforced.

    The host list is deliberately empty. A kernel that loads it reports zero
    fonts at once; a kernel that ignored it would scan the system (seconds,
    and a non-empty list). Skipped where no sandbox is enforced, since an
    unconfined kernel keeps the shared runtime cache instead.
    """

    spec = importlib.util.find_spec("matplotlib")
    if spec is None or not spec.submodule_search_locations:
        pytest.skip("matplotlib is not installed")
    source = _font_manager_source()
    version = _parse_font_manager_version(source.read_text(encoding="utf-8"))
    if version is None:
        pytest.skip(f"cannot read FontManager.__version__ from {source}")
    data_dir = Path(os.environ["OPENAI4S_DATA_DIR"])
    payload = {
        "name": f"fontlist-v{version}.json",
        "content": _fontlist(version),
        **_source_fields(_font_manager_source()),
    }
    assert font_cache.build_font_cache(
        sys.executable, data_dir=data_dir, runner=_builder_runner(payload)
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with Kernel(cwd=str(workspace)) as kernel:
        if not kernel.sandbox_status.get("enforced"):
            pytest.skip("no enforced kernel sandbox on this host")
        result = kernel.execute(
            "from matplotlib import font_manager\n"
            "print(len(font_manager.fontManager.ttflist))"
        )

    assert result["error"] is None
    assert result["stdout"].strip() == "0"
