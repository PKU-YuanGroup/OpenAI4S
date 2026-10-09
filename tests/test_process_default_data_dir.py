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
"""

from __future__ import annotations

import json
import os
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


def _drive_web_turn(cfg: Config, monkeypatch, text: str) -> None:
    """One real `run_message` on ``cfg`` with the provider and runtime faked.

    The turn's remote-GPU prompt note is stubbed out. It reads the BYOC host
    registry, which is process-scoped everywhere it is used (Settings routes,
    `host.compute`), and reaching it creates the default's directories, though
    no database. Stubbing that one call keeps the rest of the turn under the
    strict "A stays empty" rule.
    """

    from openai4s.server import gateway as gateway_mod
    from openai4s.store import get_store

    monkeypatch.setattr(
        gateway_mod, "_remote_gpu_runtime_context", lambda user_text=None: ""
    )
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

        monkeypatch.setattr(
            gateway_mod,
            "chat",
            lambda messages, cfg, on_delta=None, **kw: {
                "content": "no action here.",
                "usage": {},
            },
        )
        monkeypatch.setattr(runner, "_ensure_runtime", fake_ensure)
        monkeypatch.setattr(runner, "_spawn_title_summary", lambda *a, **k: None)
        runner.run_message(frame_id, "default", text)
    finally:
        runner.close()


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
    """`run_message` resolves the turn's task mode with the runner's Config,
    so a SessionRunner on its own data dir (a contract drive, a test, any
    embedder) never reaches the process default through the shadow."""

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
