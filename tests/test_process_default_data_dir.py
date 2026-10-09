"""A run configured on its own data dir never touches the process default.

`openai4s benchmark` builds `Config(data_dir=<tmp>)` for every case, and on
2026-10-08 one of its delegation cases still opened a developer's real
`~/.openai4s/openai4s.db` and migrated it to an unreleased schema. Both
judgment shadows -- the task-mode shadow behind `resolve_task_mode` and the
safety shadow behind the three screeners -- resolved `get_config()`, the
process default, instead of the Config of the run that triggered them.

The suite could not see it: conftest points the process default at each
test's own temp dir, so the stray open landed somewhere harmless. These tests
point the default at a separate directory A (`HOME` and `OPENAI4S_DATA_DIR`
both), configure the run on B, and require A to stay empty.

The BYOC remote-GPU host registry also leaked the other way: a run on B read
A's hosts, creating A's directories to do it. Now that a registry read
creates nothing, "A stays empty" cannot see a wrong read, so those tests put
a different host in A, require B's to be the one used, and require A to be
unchanged.
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.judgment import shadow, task_mode_shadow
from openai4s.judgment.port import BackendError

_REFACTOR = "Refactor the repository so the loaders live in their own module"


class _OfflineBackend:
    """Answers nothing; a job that reaches it is recorded as unavailable."""

    def evaluate(self, **_kwargs):
        raise BackendError("unconfigured", "offline test backend")


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        pass


@pytest.fixture(autouse=True)
def _fresh_shadows():
    shadow.reset_for_tests()
    task_mode_shadow.reset_for_tests()
    yield
    shadow.reset_for_tests()
    task_mode_shadow.reset_for_tests()


@pytest.fixture
def process_default(tmp_path, monkeypatch) -> Path:
    """Directory A, where every process-default lookup now resolves."""

    import openai4s.config as config_mod

    home = tmp_path / "A"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(home / ".openai4s"))
    monkeypatch.setattr(config_mod, "_CONFIG", None)
    return home


def _written(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _run_config(root: Path) -> Config:
    return Config(
        data_dir=root,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=2,
    )


def _delegation_workflow():
    from openai4s.benchmark.model import load_workflows

    return next(item for item in load_workflows() if item.id == "delegation")


def _enable_in(store, *capabilities: str) -> None:
    from openai4s.judgment.disclosure import DISCLOSURE_VERSION
    from openai4s.judgment.flags import (
        SETTING_BY_CAPABILITY,
        SETTING_DISCLOSURE_ACK,
        SETTING_MASTER,
    )

    store.set_setting(
        SETTING_DISCLOSURE_ACK,
        json.dumps({"version": DISCLOSURE_VERSION, "capabilities": capabilities}),
    )
    store.set_setting(SETTING_MASTER, "true")
    for name in capabilities:
        store.set_setting(SETTING_BY_CAPABILITY[name], "true")


def test_a_delegated_benchmark_child_leaves_the_process_default_alone(
    process_default, tmp_path
):
    """The incident's path: `run_delegation` -> the real DelegationRunner -> a
    child Agent's `_run_task` -> `resolve_task_mode` -> the task-mode shadow,
    and the child's Cell -> the screeners -> the safety shadow. Driven below
    `run_case` on purpose, because the runner now pins the process default for
    each case; this asserts the shadows themselves."""

    from openai4s.benchmark.steps import make_context, open_session, run_delegation

    case = _delegation_workflow().cases[0]
    context = make_context(tmp_path / "B")
    open_session(context, {})
    observed = run_delegation(context, case.inputs["run_delegation"])

    assert observed["task_status"] == case.expect["task_status"]
    assert _written(process_default) == []


def test_the_delegation_case_passes_and_writes_nothing_to_the_default(
    process_default, tmp_path
):
    """The reproduction as reported, through the public entry point."""

    from openai4s.benchmark.runner import run_case

    workflow = _delegation_workflow()
    result = run_case(workflow, workflow.cases[0], root=tmp_path / "B")

    assert result.passed, result.detail
    assert _written(process_default) == []


def test_a_case_that_resolves_the_process_default_fails_without_touching_it(
    process_default, monkeypatch
):
    """The runner answers `get_config()` from a scratch dir for each case and
    fails the case when anything lands there. The next lookup of this kind
    turns the benchmark red instead of migrating someone's database. The
    environment is left alone: the kernel sandbox builds its secret-read
    denials from OPENAI4S_DATA_DIR, so swapping that would narrow them."""

    from openai4s.benchmark import runner
    from openai4s.benchmark.model import Case, Workflow
    from openai4s.config import get_config
    from openai4s.store import get_store

    seen_env: list[str | None] = []

    def resolves_the_default(context, inputs):
        seen_env.append(os.environ.get("OPENAI4S_DATA_DIR"))
        get_store(get_config().db_path)
        return {"done": True}

    monkeypatch.setitem(runner.STEPS, "_resolves_the_default", resolves_the_default)
    case = Case(
        id="probe/default",
        workflow="probe",
        title="probe",
        outcome="success",
        steps=("_resolves_the_default",),
        expect={"done": True},
    )
    workflow = Workflow(
        id="probe",
        version="1",
        title="probe",
        summary="probe",
        steps=case.steps,
        permissions=(),
        artifacts=(),
        failure_conditions=("x",),
        cases=(case,),
    )

    result = runner.run_case(workflow, case)

    assert result.passed is False
    assert "process-global data dir" in result.detail
    assert "openai4s.db" in result.detail
    assert _written(process_default) == []
    assert seen_env == [str(process_default / ".openai4s")]
    # And the caller's own default is back once the case is over.
    assert get_config(initialize_dirs=False).data_dir == process_default / ".openai4s"


def _drive_web_turn(cfg: Config, monkeypatch, text: str) -> list[dict]:
    """One real `run_message` on ``cfg`` with the provider and runtime faked.

    Returns the messages the model was sent. Nothing on the turn is stubbed
    beyond the provider and the runtime: the remote-GPU note, which reads the
    BYOC host registry, now reads the runner's.
    """

    from openai4s.server import gateway as gateway_mod
    from openai4s.store import get_store

    sent: list[dict] = []

    def fake_chat(messages, cfg, on_delta=None, **kw):
        sent.extend(dict(message) for message in messages)
        return {"content": "no action here.", "usage": {}}

    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        frame_id = get_store(cfg.db_path).new_frame(
            kind="turn", project_id="default", status="ready"
        )

        def fake_ensure(st):
            st.dispatcher = SimpleNamespace(
                last_output=None, set_task_mode=lambda mode: None
            )
            st.messages = [{"role": "system", "content": "sys"}]
            st.booted = True

        monkeypatch.setattr(gateway_mod, "chat", fake_chat)
        monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
        monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
        runner.run_message(frame_id, "default", text)
    finally:
        runner.close()
    return sent


def _run_cli_agent(cfg: Config, monkeypatch, text: str) -> None:
    """One root `Agent.run` on ``cfg`` whose model answers without a Cell."""

    import openai4s.agent.loop as loop_mod

    monkeypatch.setattr(
        loop_mod, "chat", lambda messages, *a, **k: {"content": "done.", "usage": {}}
    )
    loop_mod.Agent(
        cfg=cfg,
        max_turns=1,
        use_skills=False,
        allow_delegate=False,
        workspace=str(cfg.data_dir),
    ).run(text)


def test_a_web_turn_configured_elsewhere_leaves_the_process_default_alone(
    process_default, tmp_path, monkeypatch
):
    """`run_message` resolves the turn's task mode with the runner's Config
    and reads the runner's GPU host registry for its remote-GPU note, so a
    SessionRunner on its own data dir (a contract drive, a test, any
    embedder) never reaches the process default through either."""

    _drive_web_turn(_run_config(tmp_path / "B"), monkeypatch, _REFACTOR)

    assert _written(process_default) == []


@pytest.mark.parametrize("surface", ["web", "cli"])
def test_each_owning_loop_hands_its_own_config_to_the_task_mode_shadow(
    process_default, tmp_path, monkeypatch, surface
):
    """The other half of the fail-safe: a loop that forgot to pass its Config
    would leak nothing, because the shadow is off without one, and would
    silently stop recording. With the capability on in B's own settings, a
    turn on B is recorded, through each loop's own seam."""

    from openai4s.host.judgment import JudgmentService
    from openai4s.store import get_store

    cfg = _run_config(tmp_path / "B")
    _enable_in(get_store(cfg.db_path), "task_mode_shadow")
    task_mode_shadow.bind(
        service=JudgmentService(cfg, None, backend_factory=_OfflineBackend)
    )
    drive = _drive_web_turn if surface == "web" else _run_cli_agent
    drive(cfg, monkeypatch, _REFACTOR)

    assert task_mode_shadow.stats()["submitted"] == 1
    assert task_mode_shadow.wait_idle(timeout=5.0)
    assert _written(process_default) == []


@pytest.mark.parametrize("enabled_by_env", [False, True], ids=["store", "env"])
def test_a_shadow_handed_no_config_stays_off_and_opens_nothing(
    process_default, monkeypatch, enabled_by_env
):
    """No Config means no data dir anybody chose, so the shadow is off. Even
    the headless env switch cannot turn it on: its job would need a Store."""

    from openai4s import store as store_mod
    from openai4s.agent.task_modes import resolve_task_mode
    from openai4s.security.classifier import classify_code

    if enabled_by_env:
        for name in (
            "OPENAI4S_EXPERIMENTAL_JUDGMENT",
            "OPENAI4S_JUDGMENT_TASK_MODE_SHADOW",
            "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
        ):
            monkeypatch.setenv(name, "1")
    opened: list[tuple] = []
    real_get_store = store_mod.get_store

    def recording_get_store(*args, **kwargs):
        opened.append(args)
        return real_get_store(*args, **kwargs)

    monkeypatch.setattr(store_mod, "get_store", recording_get_store)
    shadow.set_allow_workers(False)
    shadow.set_backend_factory(_OfflineBackend)

    resolve_task_mode(_REFACTOR)
    classify_code("print(1)")
    shadow.submit("code", state={"code": "print(1)"}, existing_verdict="SAFE")

    assert opened == []
    assert task_mode_shadow.stats()["submitted"] == 0
    assert shadow.stats()["submitted"] == 0
    assert _written(process_default) == []


def test_the_run_store_not_the_process_default_decides_whether_a_shadow_runs(
    process_default, tmp_path
):
    """Both shadows enabled in A's settings and not in B's: a run on B records
    nothing. Enabled in B's: it records. Customize writes the settings a run's
    own data dir holds, and those are the ones that count."""

    from openai4s.agent.task_modes import resolve_task_mode
    from openai4s.host.judgment import JudgmentService
    from openai4s.security.classifier import classify_code
    from openai4s.store import get_store

    default_cfg = Config(data_dir=process_default / ".openai4s")
    _enable_in(get_store(default_cfg.db_path), "task_mode_shadow", "safety_shadow")
    run_cfg = _run_config(tmp_path / "B")
    shadow.set_allow_workers(False)
    shadow.set_backend_factory(_OfflineBackend)
    task_mode_shadow.bind(
        service=JudgmentService(run_cfg, None, backend_factory=_OfflineBackend)
    )

    resolve_task_mode(_REFACTOR, cfg=run_cfg)
    classify_code("print(1)", run_cfg)
    assert task_mode_shadow.stats()["submitted"] == 0
    assert shadow.stats()["submitted"] == 0

    _enable_in(get_store(run_cfg.db_path), "task_mode_shadow", "safety_shadow")
    resolve_task_mode(_REFACTOR, cfg=run_cfg)
    classify_code("print(1)", run_cfg)
    assert task_mode_shadow.stats()["submitted"] == 1
    assert shadow.stats()["submitted"] == 1
    assert task_mode_shadow.wait_idle(timeout=5.0)


def test_the_background_judgment_reads_the_run_data_dir(process_default, tmp_path):
    """The worker's JudgmentService is built from the job's Config: its store
    and config are that run's, it is reused for the same Config object, and a
    run on another data dir gets its own."""

    from openai4s.store import get_store

    first = _run_config(tmp_path / "B")
    second = _run_config(tmp_path / "C")
    for service_for in (task_mode_shadow._service, shadow._get_service):
        service = service_for(first)
        assert service_for(first) is service
        assert service._store() is get_store(first.db_path)
        assert Path(service._config().data_dir) == first.data_dir
        other = service_for(second)
        assert other is not service
        assert other._store() is get_store(second.db_path)
    assert _written(process_default) == []


# The BYOC remote-GPU host registry is one file per data dir, and every
# reader below used to name none -- which means the process default.


def _register_gpu(data_dir: Path, alias: str) -> None:
    """A host with a fold service, recorded in ``data_dir``'s registry."""

    from openai4s.compute import registry

    registry.add_host(alias, gpus="1x test GPU", gpu_count=1, data_dir=data_dir)
    registry.set_capability(
        alias,
        "fold",
        {"script": f"/opt/{alias}/fold.sh", "engine": f"{alias}-engine"},
        data_dir,
    )


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root`` with its bytes, to compare a seeded tree."""

    return {
        str(path.relative_to(root)): path.read_bytes() if path.is_file() else None
        for path in sorted(root.rglob("*"))
    }


def _offer_ssh_alias(home: Path, alias: str, monkeypatch) -> list[str]:
    """Put ``alias`` in ``~/.ssh/config`` and answer its probe offline."""

    from openai4s.server import gateway as gateway_mod

    (home / ".ssh").mkdir()
    (home / ".ssh" / "config").write_text(
        f"Host {alias}\n  HostName 192.0.2.1\n", "utf-8"
    )
    probed: list[str] = []

    def unreachable(target: str) -> dict:
        probed.append(target)
        return {"reachable": False, "gpu_count": 0, "gpus": None}

    monkeypatch.setattr(gateway_mod, "_probe_remote_gpu", unreachable)
    monkeypatch.setattr(gateway_mod, "_REMOTE_COMPUTE_CACHE", {})
    return probed


@contextlib.contextmanager
def _settings_routes(cfg: Config):
    """Call the API of a handler serving ``cfg``, one request per handler."""

    from openai4s.server import gateway as gateway_mod

    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)

    def call(method: str, path: str, body: dict | None = None) -> tuple[dict, int]:
        handler = object.__new__(handler_cls)
        handler._query = lambda: {}
        handler._body = lambda: dict(body or {})
        answers: list[tuple[dict, int]] = []
        handler._json = lambda obj, code=200: answers.append((obj, code))
        handler._api(method, path)
        return answers[-1]

    try:
        yield call
    finally:
        handler_cls.jobs_manager.close()
        runner.close()


def test_the_remote_gpu_note_lists_the_runner_hosts_not_the_default(
    process_default, tmp_path, monkeypatch
):
    """Both places the note is written -- the seeded system prompt and each
    turn -- list the hosts in the runner's registry. They read the process
    default, which handed an embedder the developer's real GPU hosts and told
    the model about hosts its own `host.compute` refuses."""

    from openai4s.server import gateway as gateway_mod

    _register_gpu(process_default / ".openai4s", "gpu-in-a")
    before = _snapshot(process_default)
    cfg = _run_config(tmp_path / "B")
    _register_gpu(cfg.data_dir, "gpu-in-b")

    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        project = runner.store.create_project(name="gpu")["project_id"]
        state = runner._state(runner.create_session(project), project)
        runner._seed_messages(state)
    finally:
        runner.close()
    system = next(m for m in state.messages if m.get("role") == "system")
    sent = _drive_web_turn(cfg, monkeypatch, "Fold this protein sequence: ACDE")
    turn = [m for m in sent if "dynamic remote GPU" in str(m.get("content"))]

    assert turn, "the turn carried no remote-GPU note"
    for text in [str(system["content"]), *(str(m["content"]) for m in turn)]:
        assert "- gpu-in-b [default]: 1x test GPU; fold (gpu-in-b-engine)" in text
        assert "gpu-in-a" not in text
    assert _snapshot(process_default) == before


@pytest.mark.stubbed_backend
def test_settings_keep_remote_gpu_hosts_in_the_runner_data_dir(
    process_default, tmp_path, monkeypatch
):
    """Settings → Remote GPU adds, lists and removes hosts in the registry of
    the data dir its handler serves, which is the one that runner's sessions
    read. `stubbed_backend`: the reachability probe, `ssh <alias>
    nvidia-smi`, answers offline here."""

    from openai4s.compute import registry

    probed = _offer_ssh_alias(process_default, "gpu-in-b", monkeypatch)
    cfg = _run_config(tmp_path / "B")
    with _settings_routes(cfg) as call:
        added, status = call("POST", "/compute/remote", {"alias": "gpu-in-b"})
        assert (status, added["ok"]) == (200, True)
        assert [h["alias"] for h in added["info"]["hosts"]] == ["gpu-in-b"]
        assert list(registry.list_hosts(cfg.data_dir)) == ["gpu-in-b"]

        listed, _ = call("GET", "/compute/remote")
        assert [h["alias"] for h in listed["hosts"]] == ["gpu-in-b"]
        assert listed["default_host"] == "gpu-in-b"

        assert call("DELETE", "/compute/remote/gpu-in-b") == ({"ok": True}, 200)
        assert registry.list_hosts(cfg.data_dir) == {}

    assert probed == ["gpu-in-b"]
    # The ssh config is the user's own; nothing else lands in A.
    assert _written(process_default) == [".ssh", ".ssh/config"]


@pytest.mark.stubbed_backend
def test_in_the_daemon_settings_and_sessions_share_the_process_default(
    process_default, monkeypatch
):
    """The daemon's Config *is* the process default, so its registry did not
    move: a host added in Settings is the one a bare `registry.list_hosts()`,
    a session's `host.remote_gpu_status` and the prompt note all see.
    `stubbed_backend` for the same probe as above."""

    from openai4s.compute import registry
    from openai4s.config import get_config
    from openai4s.host_dispatch import HostDispatcher
    from openai4s.server import gateway as gateway_mod

    _offer_ssh_alias(process_default, "gpu-daemon", monkeypatch)
    cfg = get_config()
    with _settings_routes(cfg) as call:
        added, status = call("POST", "/compute/remote", {"alias": "gpu-daemon"})
        assert (status, added["ok"]) == (200, True)

    assert (process_default / ".openai4s" / "remote_compute.json").is_file()
    assert list(registry.list_hosts()) == ["gpu-daemon"]
    reported = HostDispatcher(cfg)._m_remote_gpu_status()
    assert [h["alias"] for h in reported["hosts"]] == ["gpu-daemon"]
    note = gateway_mod._remote_gpu_runtime_context(data_dir=cfg.data_dir)
    assert "- gpu-daemon [default]:" in note


def test_a_dispatcher_runs_its_remote_gpu_services_on_its_own_registry(
    process_default, tmp_path, monkeypatch
):
    """`host.remote_gpu_status`, `host.register_remote_capability`,
    `host.fold` and `host.score_mutations` use the registry of the
    dispatcher's Config, the one its `host.compute` already checks aliases
    against. On the process default, a dispatcher on B reported A's hosts,
    ran `host.fold` on A's GPU and refused to register a service on B's
    own host."""

    from openai4s.compute import registry
    from openai4s.host_dispatch import HostDispatcher

    _register_gpu(process_default / ".openai4s", "gpu-in-a")
    before = _snapshot(process_default)
    cfg = _run_config(tmp_path / "B")
    _register_gpu(cfg.data_dir, "gpu-in-b")
    dispatcher = HostDispatcher(cfg)
    real_run = subprocess.run
    ssh: list[list[str]] = []

    def remote(argv, *args, **kwargs):
        if not argv or argv[0] != "ssh":
            return real_run(argv, *args, **kwargs)
        ssh.append(list(argv))
        # A registration probe passes; a prediction job fails on the host.
        code = 0 if argv[-1].startswith("test -e ") else 1
        return subprocess.CompletedProcess(argv, code, b"", b"")

    monkeypatch.setattr(subprocess, "run", remote)

    status = dispatcher._m_remote_gpu_status()
    assert [h["alias"] for h in status["hosts"]] == ["gpu-in-b"]
    assert status["default_host"] == "gpu-in-b"

    esm = {"capability": "score_mutations", "script": "/opt/esm/score.sh"}
    refused = dispatcher._m_register_remote_capability({"alias": "gpu-in-a", **esm})
    assert "unknown remote GPU host 'gpu-in-a'" in refused["error"]
    registered = dispatcher._m_register_remote_capability({"alias": "gpu-in-b", **esm})
    assert registered["ok"] is True
    assert (
        "score_mutations" in registry.get_host("gpu-in-b", cfg.data_dir)["capabilities"]
    )

    folded = dispatcher._m_fold({"sequence": "ACDE"})
    assert "prediction failed on gpu-in-b" in folded["error"]
    scored = dispatcher._m_score_mutations({"sequence": "ACDE"})
    assert "scoring failed on gpu-in-b" in scored["error"]

    assert [argv[argv.index("BatchMode=yes") + 1] for argv in ssh] == ["gpu-in-b"] * 3
    assert "/opt/gpu-in-b/fold.sh" in ssh[1][-1]
    assert _snapshot(process_default) == before


def test_a_registry_call_naming_no_data_dir_creates_nothing_to_read(process_default):
    """Without a data dir the registry means the process default, the
    daemon's own. Reading it went through `get_config()`, which created and
    chmod-ed that directory; now a read creates nothing, and the first write
    creates it owner-only, as `Config.ensure_dirs` would."""

    from openai4s.compute import registry

    assert registry.list_hosts() == {}
    assert registry.capability_host("fold") == (None, None)
    assert _written(process_default) == []

    registry.add_host("gpu-default")
    default_dir = process_default / ".openai4s"
    assert _written(process_default) == [".openai4s", ".openai4s/remote_compute.json"]
    assert list(registry.list_hosts(default_dir)) == ["gpu-default"]
    if os.name == "posix":
        assert stat.S_IMODE(default_dir.stat().st_mode) == 0o700


def test_a_permission_check_handed_no_config_leaves_the_process_default_alone(
    process_default, tmp_path
):
    """`harness characterize` asks the broker for headless decisions on a
    Store of its own and hands it no Config, so the Guardian fell back to
    `get_config()`, which created and chmod-ed the process default. Its flags
    and budgets come from the environment either way: the answers match an
    explicit Config's, and nothing is created."""

    from openai4s.permissions import PermissionBroker
    from openai4s.store import Store

    store = Store(tmp_path / "B" / "permissions.db")
    try:
        store.seed_default_permission_rules()
        broker = PermissionBroker()
        explicit = _run_config(tmp_path / "B")

        asked = broker.gate(
            store=store, frame_id=None, method="bash", target="echo characterize"
        )
        reachable = broker.approval_reachable(store=store, frame_id=None, method="bash")
        adjudicated = broker.guardian_adjudicates(store=store, frame_id=None)

        assert asked["allow"] is False
        assert reachable is broker.approval_reachable(
            store=store, frame_id=None, method="bash", guardian_config=explicit
        )
        assert adjudicated is broker.guardian_adjudicates(
            store=store, frame_id=None, guardian_config=explicit
        )
    finally:
        store.close()
    assert _written(process_default) == []
