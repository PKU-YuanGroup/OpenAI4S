"""B-02 Skill network manifest and execution admission contracts."""

from __future__ import annotations

import inspect
import os
import socket
import subprocess
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.agent.loop import Agent
from openai4s.bash_capability import command_digest
from openai4s.config import Config
from openai4s.egress import EgressBlocked, check_url, grant_domain, reset_grants
from openai4s.host.bash import BashAuthorizationService
from openai4s.server.cell_run import CellExecutionService
from openai4s.server.skill_network_admission import (
    admit_cell,
    admit_shell,
    bind_skill_load,
    bindings_for,
    constrain_check_url,
    frame_scope,
    host_only_boundary_holds,
    raw_required_binding,
    reset_bindings,
)
from openai4s.skills_loader import SkillLoader
from openai4s.skills_loader.capabilities import (
    NETWORK_MODES,
    canonical_network_digest,
    compose_readiness,
    declared_capability,
    parse_network_frontmatter,
    resolve_network_capability,
)
from openai4s.skills_loader.loader import NEEDS_SETUP, READY


def _enforced_sandbox() -> dict:
    return {
        "enforced": True,
        "self_test_passed": True,
        "network_policy": "blocked",
        "backend": "seatbelt",
        "state": "enforced",
        "mode": "auto",
    }


@pytest.fixture(autouse=True)
def _clean_bindings_and_grants():
    reset_bindings()
    reset_grants()
    yield
    reset_bindings()
    reset_grants()


def test_canonical_digest_is_stable_under_domain_reordering():
    a = canonical_network_digest("host_only", ["api.openalex.org", "doi.org"])
    b = canonical_network_digest("host_only", ["doi.org", "api.openalex.org"])
    assert a == b
    assert len(a) == 64
    assert a != canonical_network_digest("none", [])


def test_frontmatter_parser_reads_closed_network_schema():
    raw = (
        "---\n"
        "name: lit\n"
        "capabilities:\n"
        "  network:\n"
        "    mode: host_only\n"
        "    domains:\n"
        "      - api.openalex.org\n"
        "      - doi.org\n"
        "---\n# body\n"
    )
    cap = parse_network_frontmatter(raw)
    assert cap is not None
    assert cap.mode == "host_only"
    assert cap.domains == ("api.openalex.org", "doi.org")
    assert cap.declaration == "declared"
    assert cap.explicit is True
    assert cap.digest == canonical_network_digest("host_only", cap.domains)


def test_missing_and_invalid_network_fields_do_not_grant():
    assert parse_network_frontmatter("---\nname: x\n---\n") is None
    bad = parse_network_frontmatter(
        "---\nname: x\ncapabilities:\n  network:\n    mode: warp\n---\n"
    )
    assert bad is not None
    assert bad.mode == "unknown"
    assert bad.declaration == "unknown"


def test_every_bundled_skill_has_explicit_network_mode():
    loader = SkillLoader()
    missing = []
    invalid = []
    for skill in loader.discover().values():
        if skill.source != "bundled":
            continue
        cap = skill.network
        if not cap.explicit:
            missing.append(skill.name)
        if skill.collection:
            if cap.declaration != "legacy" or cap.mode != "unknown":
                invalid.append((skill.name, cap.declaration, cap.mode))
        elif cap.mode not in NETWORK_MODES:
            invalid.append((skill.name, cap.declaration, cap.mode))
    assert missing == []
    assert invalid == []


def test_catalog_full_traversal_uses_zero_sockets_and_zero_subprocesses(monkeypatch):
    def _spawned(*_a, **_k):
        raise AssertionError("catalog traversal started a subprocess")

    def _dialled(*_a, **_k):
        raise AssertionError("catalog traversal opened a socket")

    monkeypatch.setattr(subprocess, "Popen", _spawned)
    monkeypatch.setattr(subprocess, "run", _spawned)
    monkeypatch.setattr(os, "system", _spawned)
    monkeypatch.setattr(os, "posix_spawn", _spawned, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _dialled)
    monkeypatch.setattr(socket, "socket", _dialled)
    monkeypatch.setattr(socket, "create_connection", _dialled)

    grants_before = 0

    def _grant(domain: str) -> str:
        nonlocal grants_before
        grants_before += 1
        raise AssertionError(f"catalog granted egress for {domain!r}")

    monkeypatch.setattr("openai4s.egress.grant_domain", _grant)

    loader = SkillLoader()
    rows = loader.catalog(include_disabled=True)
    assert rows, "no skills discovered"
    assert grants_before == 0
    for row in rows:
        readiness = row["readiness"]
        assert readiness["checked_locally"] is True
        assert readiness["probed"] is False
        assert "blocked_on" in readiness
        assert "state" in readiness
        assert isinstance(row["ready"], bool)
        caps = row["capabilities"]["network"]
        assert caps["mode"] in NETWORK_MODES | {"unknown"}
        assert readiness["checked_locally"] is True


def test_manifest_load_does_not_change_egress_grants(monkeypatch, tmp_path):
    from openai4s.host.skills import SkillService

    grants = {"count": 0}

    def _grant(domain: str) -> str:
        grants["count"] += 1
        return domain

    monkeypatch.setattr("openai4s.egress.grant_domain", _grant)
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "lit").mkdir()
    (skills / "lit" / "SKILL.md").write_text(
        "---\nname: lit\ndescription: d\norigin: openai4s\n"
        "capabilities:\n  network:\n    mode: host_only\n"
        "    domains:\n      - api.openalex.org\n---\n# x\n",
        "utf-8",
    )
    service = SkillService(Config(data_dir=tmp_path / "data", skills_dir=skills))
    loaded = service.load("lit")
    assert loaded["name"] == "lit"
    assert grants["count"] == 0
    assert (
        loaded["network_manifest_digest"] == loaded["capabilities"]["network"]["digest"]
    )


def test_raw_required_is_fail_closed_for_every_sandbox_posture(monkeypatch):
    cap = declared_capability("raw_required", [], source="frontmatter")
    bind_skill_load(
        frame_id="frame-raw",
        action_group_id="ag-1",
        skill_id="alphafold2",
        version="1",
        document_digest="d" * 64,
        capability=cap,
        source="load_skill",
    )
    postures = [
        None,
        {
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "not_enforced",
            "backend": None,
        },
        {
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "unproven",
            "backend": "remote",
        },
        {
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "raw_allowed",
            "backend": "bwrap",
        },
        _enforced_sandbox(),
    ]
    monkeypatch.setenv("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK", "1")
    for status in postures:
        cell = admit_cell(frame_id="frame-raw", sandbox_status=status)
        shell = admit_shell(frame_id="frame-raw", sandbox_status=status)
        assert cell.allowed is False
        assert shell.allowed is False
        assert "raw_network" in cell.blocked_on


def test_host_only_undeclared_and_unapproved_domains_are_refused(monkeypatch):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    cap = declared_capability("host_only", ["api.openalex.org"], source="frontmatter")
    bind_skill_load(
        frame_id="frame-host",
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="a" * 64,
        capability=cap,
        source="load_skill",
    )
    status = _enforced_sandbox()
    undeclared = admit_shell(
        frame_id="frame-host",
        sandbox_status=status,
        command_domains=["evil.example.com"],
    )
    assert undeclared.allowed is False
    assert "undeclared_domain" in undeclared.blocked_on

    unapproved = admit_shell(
        frame_id="frame-host",
        sandbox_status=status,
        command_domains=["api.openalex.org"],
    )
    # openalex is on the science allowlist, so declared+approved should pass
    assert unapproved.allowed is True

    blocked_builtin = admit_shell(
        frame_id="frame-host",
        sandbox_status=status,
        command_domains=["example.com"],
    )
    assert blocked_builtin.allowed is False

    reset_bindings()
    bind_skill_load(
        frame_id="frame-host",
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="a" * 64,
        capability=declared_capability(
            "host_only", ["evil.example.com"], source="frontmatter"
        ),
        source="load_skill",
    )
    unapproved_declared = admit_shell(
        frame_id="frame-host",
        sandbox_status=status,
        command_domains=["evil.example.com"],
    )
    assert unapproved_declared.allowed is False
    assert "unapproved_domain" in unapproved_declared.blocked_on


def test_host_only_noncompliant_redirect_is_refused():
    cap = declared_capability("host_only", ["api.openalex.org"], source="frontmatter")
    bind_skill_load(
        frame_id="frame-redir",
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="b" * 64,
        capability=cap,
        source="load_skill",
    )
    with frame_scope("frame-redir"):
        assert constrain_check_url("https://api.openalex.org/works") is None
        with pytest.raises(EgressBlocked, match="does not declare"):
            check_url("https://evil.example.com/next")


def test_legacy_collection_skill_does_not_gain_authorization_from_new_fields():
    raw = (
        "---\nname: bio-example\ncapabilities:\n  network:\n"
        "    mode: host_only\n    domains:\n      - evil.example.com\n---\n"
    )
    cap = resolve_network_capability(
        raw_text=raw,
        name="bio-example",
        directory="bio-example",
        collection="bioskills",
        source="bundled",
    )
    assert cap.declaration == "legacy"
    assert cap.mode == "unknown"
    assert cap.domains == ()
    bind_skill_load(
        frame_id="frame-legacy",
        action_group_id="ag-1",
        skill_id="bio-example",
        version="1",
        document_digest="c" * 64,
        capability=cap,
        source="load_skill",
    )
    # Legacy bindings must not impose host_only domain grants or extra rights.
    decision = admit_shell(
        frame_id="frame-legacy",
        sandbox_status=_enforced_sandbox(),
        command_domains=["evil.example.com"],
    )
    assert decision.allowed is True
    with frame_scope("frame-legacy"):
        assert constrain_check_url("https://evil.example.com/") is None


def test_user_skill_without_field_is_unknown_and_does_not_grant():
    cap = resolve_network_capability(
        raw_text="---\nname: mine\n---\n# body\n",
        name="mine",
        directory="mine",
        collection=None,
        source="user",
    )
    assert cap.declaration == "unknown"
    assert cap.mode == "unknown"
    assert cap.explicit is False


def test_host_only_is_blocked_when_sandbox_is_degraded_or_raw_allowed():
    cap = declared_capability("host_only", ["doi.org"], source="frontmatter")
    bind_skill_load(
        frame_id="frame-deg",
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="e" * 64,
        capability=cap,
        source="load_skill",
    )
    degraded = {
        "enforced": False,
        "self_test_passed": False,
        "network_policy": "not_enforced",
        "backend": None,
    }
    raw = {
        "enforced": True,
        "self_test_passed": True,
        "network_policy": "raw_allowed",
        "backend": "bwrap",
    }
    remote = {
        "enforced": False,
        "self_test_passed": False,
        "network_policy": "unproven",
        "backend": "remote",
    }
    for status in (degraded, raw, remote, None):
        cell = admit_cell(frame_id="frame-deg", sandbox_status=status)
        assert cell.allowed is False
        shell = admit_shell(frame_id="frame-deg", sandbox_status=status)
        assert shell.allowed is False


def test_host_only_boundary_helper_matches_measured_fields():
    assert host_only_boundary_holds(_enforced_sandbox()) is True
    assert host_only_boundary_holds({"backend": "remote", "enforced": True}) is False
    assert host_only_boundary_holds(None) is False


#: Where each declared sink actually applies admission. Keyed by the name in
#: `ADMISSION_SINKS` so the two cannot drift: a sink added to the tuple with
#: nothing wired fails here, and a sink wired without being declared fails
#: too. This replaced a hand-written pair of greps that named `cell_run.py`
#: and `host/bash.py` and called them "the only production callers" -- while
#: `agent/loop.py`, `host_dispatch.py`'s background launcher, and its
#: `load_skill` handler all reached executable code with no check. Each of
#: those was found by review, not by this test, because a grep only covers
#: the files someone thought to name.
_SINK_EVIDENCE = {
    "cell": ("openai4s/server/cell_run.py", ("admit_cell(", "admit_cell_preflight(")),
    "shell": ("openai4s/host/bash.py", ("admit_shell(",)),
    "cli_cell": ("openai4s/agent/loop.py", ("admit_cell(", "raw_required_binding(")),
    "exec_background": ("openai4s/host_dispatch.py", ("raw_required_binding(",)),
    "load_skill": ("openai4s/host_dispatch.py", ("_bind_loaded_skill(",)),
}


def test_every_declared_sink_is_wired_to_admission():
    from openai4s.server.skill_network_admission import ADMISSION_SINKS

    assert set(_SINK_EVIDENCE) == set(ADMISSION_SINKS), (
        "ADMISSION_SINKS and the evidence table disagree: a sink was declared "
        "without being wired, or wired without being declared"
    )
    for sink, (path, calls) in sorted(_SINK_EVIDENCE.items()):
        src = Path(path).read_text("utf-8")
        assert any(call in src for call in calls), f"{sink} sink has no admission"


def test_cell_and_shell_sinks_have_zero_bypass():
    cell_src = inspect.getsource(CellExecutionService._execute_admitted)
    bash_src = inspect.getsource(BashAuthorizationService.authorize)
    assert "admit_cell(" in cell_src
    assert "admit_shell(" in bash_src


def test_load_skill_refuses_a_blocked_skill_instead_of_only_the_next_cell(tmp_path):
    """Binding the requirement is not the same as refusing it.

    Cell admission runs once, when the Cell starts. A Cell that calls
    `load_skill` mid-flight is already inside its fence, so binding the
    manifest and handing back the recipe leaves exactly one Cell able to run
    a blocked Skill: the one that asked for it. That is the ordinary
    load-then-use shape, so the freeze has to apply to the load itself.
    """

    from openai4s.host_dispatch import build_dispatcher

    skills = tmp_path / "skills"
    (skills / "raw").mkdir(parents=True)
    (skills / "raw" / "SKILL.md").write_text(
        "---\nname: raw\ndescription: d\norigin: openai4s\n"
        "capabilities:\n  network:\n    mode: raw_required\n    domains: []\n---\n"
        "# recipe\nimport socket\n",
        "utf-8",
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    disp = build_dispatcher(cfg)
    disp.frame_id = "frame-raw-load"

    refused = disp._m_load_skill("raw")
    assert set(refused) == {"error"}, refused
    assert "raw kernel network" in refused["error"]
    assert "import socket" not in str(refused)


def test_background_execution_is_a_sink_too(tmp_path):
    """`exec_background` spawns its own worker; the freeze has to reach it.

    The requirement is bound directly. Loading a `raw_required` Skill is
    refused and a refused load binds nothing, so the rows this sink still
    answers to are the ones an older build persisted.
    """

    from openai4s.host_dispatch import build_dispatcher

    skills = tmp_path / "skills"
    skills.mkdir()
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    disp = build_dispatcher(cfg)
    disp.frame_id = "frame-raw-bg"
    bind_skill_load(
        frame_id="frame-raw-bg",
        action_group_id="ag-1",
        skill_id="raw",
        version="1",
        document_digest="b" * 64,
        capability=declared_capability("raw_required", [], source="frontmatter"),
        source="load_skill",
    )

    out = disp._m_exec_background({"code": "print(1)"})
    assert set(out) == {"error"}, out
    assert "raw kernel network" in out["error"]


def test_the_cli_and_delegation_cell_sink_has_full_network_admission():
    """`CellExecutionService` is not the only Code-as-Action sink.

    A CLI run and every delegated child execute Cells through
    `LocalActionExecutor`, whose admission hook is `Agent._admit_cell`. While
    this file only read `cell_run.py` and `host/bash.py`, a Skill bound
    `raw_required` ran unconfined there and the omission was invisible to the
    zero-bypass check itself.

    The unconditional `raw_required` refusal runs before lazy startup. For a
    `host_only` binding, the first requested Cell creates the worker, checks its
    actual posture before Skill bootstrap/user code, and refuses when the
    boundary is not enforced. Tool-only/finalization turns still spawn nothing.
    """

    loop_src = inspect.getsource(Agent._admit_cell)
    assert "raw_required_binding(" in loop_src
    # `run` owns the frame's terminal status and delegates the turn itself to
    # `_run_task`, which is where the lazy Python worker is spawned and admitted.
    run_src = inspect.getsource(Agent.run)
    assert "self._run_task(task)" in run_src
    run_src += inspect.getsource(Agent._run_task)
    r_src = inspect.getsource(Agent._execute_r)
    assert "self._admit_spawned_cell_kernel(kernel)" in run_src
    assert "self._admit_spawned_cell_kernel(k)" in r_src


def test_cli_host_only_uses_spawned_workers_measured_posture():
    cap = declared_capability("host_only", ["doi.org"], source="frontmatter")
    bind_skill_load(
        frame_id="frame-cli-host",
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="f" * 64,
        capability=cap,
        source="load_skill",
    )
    agent = object.__new__(Agent)
    agent.frame_id = "frame-cli-host"
    degraded = SimpleNamespace(
        sandbox_status={
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "not_enforced",
            "backend": None,
        }
    )
    with pytest.raises(PermissionError, match="Host-only network"):
        agent._admit_spawned_cell_kernel(degraded)

    enforced = SimpleNamespace(sandbox_status=_enforced_sandbox())
    agent._admit_spawned_cell_kernel(enforced)


@pytest.mark.parametrize(("language", "fence"), [("python", "python"), ("r", "r")])
def test_cli_host_only_refuses_before_first_python_or_r_user_code(
    monkeypatch, language, fence
):
    def one_cell(_messages, _cfg, **_kwargs):
        return {
            "content": f"```{fence}\nprint(42)\n```",
            "reasoning": None,
            "usage": {},
            "finish_reason": "stop",
            "raw": {},
        }

    created = []

    class DegradedKernel:
        generation = 1
        sandbox_status = {
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "not_enforced",
            "backend": None,
        }

        def __init__(self, *_args, **_kwargs):
            self.closed = False
            created.append(self)

        def is_alive(self):
            return not self.closed

        def execute(self, *_args, **_kwargs):
            raise AssertionError("user or Skill bootstrap code reached the worker")

        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(loop_mod, "chat", one_cell)
    if language == "python":
        monkeypatch.setattr(loop_mod, "Kernel", DegradedKernel)
    else:
        import openai4s.kernel.r_kernel as r_kernel_mod

        monkeypatch.setattr(r_kernel_mod, "spawn_r_kernel", DegradedKernel)

    agent = Agent(use_skills=False, allow_delegate=False, max_turns=1)
    bind_skill_load(
        frame_id=agent.frame_id,
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="9" * 64,
        capability=declared_capability("host_only", ["doi.org"], source="frontmatter"),
        source="load_skill",
    )

    with pytest.raises(PermissionError, match="Host-only network"):
        agent.run("run one Cell")

    assert len(created) == 1
    assert created[0].closed is True


def test_raw_required_is_refused_without_any_measured_posture():
    """The refusal that must not depend on a live kernel."""

    assert raw_required_binding("frame-with-no-bindings") is None


def test_load_event_records_skill_version_document_and_manifest_digest(tmp_path):
    from openai4s.host_dispatch import build_dispatcher

    skills = tmp_path / "skills"
    (skills / "demo").mkdir(parents=True)
    (skills / "demo" / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\norigin: openai4s\n"
        "capabilities:\n  network:\n    mode: none\n    domains: []\n---\n# x\n",
        "utf-8",
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    disp = build_dispatcher(cfg)
    disp.frame_id = "frame-load"
    loaded = disp._m_load_skill("demo")
    assert loaded["name"] == "demo"
    assert loaded["skill_id"] == "demo"
    assert loaded["document_digest"]
    assert loaded["manifest_digest"]
    bound = bindings_for("frame-load")
    assert len(bound) == 1
    assert bound[0].manifest_digest == loaded["manifest_digest"]
    assert bound[0].document_digest == loaded["document_digest"]
    assert bound[0].skill_id == "demo"


def test_restart_restores_skill_network_binding_to_exact_loading_frame(tmp_path):
    from openai4s.host_dispatch import build_dispatcher
    from openai4s.store import get_store

    # An admitted load: a refused one binds nothing, so has nothing to restore.
    skills = tmp_path / "skills"
    (skills / "host-demo").mkdir(parents=True)
    (skills / "host-demo" / "SKILL.md").write_text(
        "---\nname: host-demo\ndescription: d\norigin: openai4s\n"
        "capabilities:\n  network:\n    mode: host_only\n"
        "    domains:\n      - doi.org\n---\n# x\n",
        "utf-8",
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="science")
    child = store.new_frame(parent_id=root, kind="delegate", project_id="science")
    sibling = store.new_frame(parent_id=root, kind="delegate", project_id="science")
    child_dispatcher = build_dispatcher(cfg, frame_id=child)
    with child_dispatcher.bind_sandbox_status(_enforced_sandbox()):
        loaded = child_dispatcher._m_load_skill("host-demo")
    assert loaded.get("name") == "host-demo", loaded
    assert [item.skill_id for item in bindings_for(child)] == ["host-demo"]

    reset_bindings()
    store.close()
    parent_dispatcher = build_dispatcher(cfg, frame_id=root)
    assert bindings_for(root) == ()
    sibling_dispatcher = build_dispatcher(cfg, frame_id=sibling)
    assert bindings_for(sibling) == ()
    restored_child = build_dispatcher(cfg, frame_id=child)
    assert [item.skill_id for item in bindings_for(child)] == ["host-demo"]
    for dispatcher in (
        child_dispatcher,
        parent_dispatcher,
        sibling_dispatcher,
        restored_child,
    ):
        dispatcher.store.close()


def test_cell_execute_records_admission_and_refuses_raw_required(tmp_path):
    from dataclasses import replace
    from types import SimpleNamespace

    from openai4s.execution import CaptureResult, CellRequest
    from openai4s.kernel import KernelSupervisor
    from openai4s.server.cell_run import CellExecutionPorts, CellExecutionService

    cap = declared_capability("raw_required", [], source="frontmatter")
    bind_skill_load(
        frame_id="frame-1",
        action_group_id="ag-cell",
        skill_id="alphafold2",
        version="1",
        document_digest="f" * 64,
        capability=cap,
        source="load_skill",
    )

    class Harness:
        def __init__(self) -> None:
            self.ran = False
            self.records: list[dict] = []

        def ports(self) -> CellExecutionPorts:
            return CellExecutionPorts(
                prepare_language=lambda *_a: None,
                kernel_id=lambda *_a: "python",
                snapshot=lambda *_a: {},
                protect_versions=lambda *_a: None,
                safety_refusal=lambda *_a: None,
                run=self.run,
                capture=lambda *a: CaptureResult(),
                emit_artifact_step=lambda *a: None,
                record_cell=self.record_cell,
            )

        def run(self, *args):
            self.ran = True
            return {"stdout": "should not run", "stderr": "", "error": None}

        def record_cell(self, **record):
            self.records.append(record)

    harness = Harness()
    service = CellExecutionService(harness.ports())
    session = SimpleNamespace(
        root_frame_id="frame-1",
        project_id="project-1",
        workspace=tmp_path,
        cell_index=0,
        kernels=KernelSupervisor(),
    )
    events: list[dict] = []
    outcome = service.execute(
        session,
        CellRequest("print(1)", "agent", action_group_id="ag-cell"),
        events.append,
    )
    assert harness.ran is False
    assert outcome.result.get("error")
    assert outcome.result["skill_network"]["allowed"] is False
    assert outcome.executed is False or outcome.result.get("error")


def test_bash_authorize_refuses_raw_required_skill(tmp_path):
    cap = declared_capability("raw_required", [], source="frontmatter")
    bind_skill_load(
        frame_id="frame-bash",
        action_group_id="ag-1",
        skill_id="alphafold2",
        version="1",
        document_digest="g" * 64,
        capability=cap,
        source="load_skill",
    )
    service = BashAuthorizationService(
        workspace=lambda: tmp_path,
        frame_id=lambda: "frame-bash",
        generation=lambda: "python:g-1",
        sandbox_status=lambda: _enforced_sandbox(),
        token_factory=lambda: "test-token-0123456789abcdef0123456789",
    )
    command = "echo allowed"
    result = service.authorize(
        {
            "command": command,
            "command_sha256": command_digest(command),
            "cwd": str(tmp_path.resolve()),
            "workspace": str(tmp_path.resolve()),
            "generation": "python:g-1",
            "challenge": "challenge-0123456789abcdef",
            "timeout": 30,
        }
    )
    assert "error" in result
    assert "raw" in result["error"].lower()


def test_compose_readiness_blocks_raw_required_locally(monkeypatch):
    monkeypatch.delenv("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK", raising=False)
    cap = declared_capability("raw_required", [], source="frontmatter")
    readiness = compose_readiness((), cap)
    assert readiness["checked_locally"] is True
    assert readiness["probed"] is False
    assert readiness["ready"] is False
    assert readiness["state"] == NEEDS_SETUP
    assert "raw_network" in readiness["blocked_on"]


def test_compose_readiness_none_mode_stays_ready_without_probing():
    cap = declared_capability("none", [], source="frontmatter")
    readiness = compose_readiness((), cap)
    assert readiness["state"] == READY
    assert readiness["ready"] is True
    assert readiness["blocked_on"] == []
    assert readiness["probed"] is False


def test_literature_review_is_host_only_and_example_stats_needs_no_network():
    loader = SkillLoader()
    lit = loader.get("literature-review")
    stats = loader.get("example_stats")
    assert lit is not None and stats is not None
    assert lit.network.mode == "host_only"
    # Exact membership, spelled so it cannot be mistaken for a URL substring
    # test: `domains` is a tuple of hosts, never a URL to sanitise.
    assert any(domain == "api.openalex.org" for domain in lit.network.domains)
    assert stats.network.mode == "none"
    af = loader.get("alphafold2")
    assert af is not None
    assert af.network.mode == "raw_required"
    bio = loader.get("bio-structural-biology-structure-validation")
    assert bio is not None
    assert bio.network.declaration == "legacy"
    assert bio.network.mode == "unknown"


def test_remote_sandbox_status_is_unproven():
    from openai4s.kernel.manager import Kernel

    kernel = Kernel.__new__(Kernel)
    kernel.transport_factory = object()
    kernel._sandbox = type(
        "S",
        (),
        {
            "status": type(
                "St",
                (),
                {
                    "to_dict": staticmethod(
                        lambda: {
                            "enforced": True,
                            "self_test_passed": True,
                            "network_policy": "blocked",
                            "backend": "seatbelt",
                        }
                    )
                },
            )()
        },
    )()
    status = Kernel.sandbox_status.__get__(kernel, Kernel)
    assert status["enforced"] is False
    assert status["self_test_passed"] is False
    assert status["backend"] == "remote"
    assert status["network_policy"] == "unproven"
    assert host_only_boundary_holds(status) is False


def test_cell_refuses_a_blocked_skill_before_running_its_bootstrap(tmp_path):
    """The refusal must precede `prepare_language`, not merely happen.

    `prepare_language` is where Skill bootstrap runs -- sidecar imports on a
    worker, `origin="system"`. Full admission cannot run before it, because
    the posture it intersects is measured from that worker; so the sink runs
    the posture-independent half first. A test that only asserts "the Cell
    was refused" stays green when that half is deleted, because the later
    full admission still refuses -- after the blocked Skill's code has run.
    """

    from types import SimpleNamespace

    from openai4s.execution import CaptureResult, CellRequest
    from openai4s.kernel import KernelSupervisor
    from openai4s.server.cell_run import CellExecutionPorts, CellExecutionService

    bind_skill_load(
        frame_id="frame-bootstrap",
        action_group_id="ag-cell",
        skill_id="alphafold2",
        version="1",
        document_digest="f" * 64,
        capability=declared_capability("raw_required", [], source="frontmatter"),
        source="load_skill",
    )

    class Harness:
        def __init__(self) -> None:
            self.prepared = False
            self.ran = False

        def prepare_language(self, *_args):
            self.prepared = True
            return None

        def ports(self) -> CellExecutionPorts:
            return CellExecutionPorts(
                prepare_language=self.prepare_language,
                kernel_id=lambda *_a: "python",
                snapshot=lambda *_a: {},
                protect_versions=lambda *_a: None,
                safety_refusal=lambda *_a: None,
                run=self.run,
                capture=lambda *a: CaptureResult(),
                emit_artifact_step=lambda *a: None,
                record_cell=lambda **record: None,
            )

        def run(self, *args):
            self.ran = True
            return {"stdout": "should not run", "stderr": "", "error": None}

    harness = Harness()
    outcome = CellExecutionService(harness.ports()).execute(
        SimpleNamespace(
            root_frame_id="frame-bootstrap",
            project_id="project-1",
            workspace=tmp_path,
            cell_index=0,
            kernels=KernelSupervisor(),
        ),
        CellRequest("print(1)", "agent", action_group_id="ag-cell"),
        [].append,
    )

    assert harness.prepared is False, "Skill bootstrap ran before the refusal"
    assert harness.ran is False
    assert outcome.result["skill_network"]["allowed"] is False
    assert "raw_network" in outcome.result["skill_network"]["blocked_on"]


def _host_only_dispatcher(tmp_path):
    from openai4s.host_dispatch import build_dispatcher

    skills = tmp_path / "skills"
    (skills / "lit").mkdir(parents=True)
    (skills / "lit" / "SKILL.md").write_text(
        "---\nname: lit\ndescription: d\norigin: openai4s\n"
        "capabilities:\n  network:\n    mode: host_only\n"
        "    domains:\n      - api.openalex.org\n---\n"
        "# recipe\nimport requests\n",
        "utf-8",
    )
    return build_dispatcher(Config(data_dir=tmp_path / "data", skills_dir=skills))


_DEGRADED = {
    "enforced": False,
    "self_test_passed": False,
    "network_policy": "raw_allowed",
    "backend": "none",
}
_ENFORCED = {
    "enforced": True,
    "self_test_passed": True,
    "network_policy": "blocked",
    "backend": "seatbelt",
}


def test_load_refuses_host_only_when_the_measured_sandbox_is_degraded(tmp_path):
    """`raw_required` needs no posture; `host_only` does -- and load has it.

    Every `host_call` runs inside `bind_sandbox_status(self.sandbox_status)`,
    which the manager reads from the worker executing this very Cell. Only
    the version-wide `raw_required` freeze was applied at load, so a
    `host_only` Skill on a degraded sandbox was refused for every subsequent
    Cell and handed to the one that loaded it -- the load-then-use shape,
    which is the one that matters.
    """

    disp = _host_only_dispatcher(tmp_path)
    disp.frame_id = "frame-host-only-degraded"
    with disp.bind_sandbox_status(_DEGRADED):
        refused = disp._m_load_skill("lit")
    assert set(refused) == {"error"}, refused
    assert "does not confine the kernel" in refused["error"]
    assert "import requests" not in str(refused)

    disp.frame_id = "frame-host-only-enforced"
    with disp.bind_sandbox_status(_ENFORCED):
        loaded = disp._m_load_skill("lit")
    assert loaded.get("name") == "lit", loaded
    disp.store.close()


def test_reading_a_skill_file_is_gated_like_loading_it(tmp_path):
    """`skills_read` with the default path returns SKILL.md -- the recipe.

    Gating only `load_skill` left the freeze one synonym wide: an
    already-admitted Cell that reads instead of loads got the same
    executable guidance with no bind and no refusal.
    """

    disp = _host_only_dispatcher(tmp_path)
    disp.frame_id = "frame-read-degraded"
    with disp.bind_sandbox_status(_DEGRADED):
        refused = disp._m_skills_read({"name": "lit"})
    assert isinstance(refused, dict) and set(refused) == {"error"}, refused
    assert "import requests" not in str(refused)

    disp.frame_id = "frame-read-enforced"
    with disp.bind_sandbox_status(_ENFORCED):
        allowed = disp._m_skills_read({"name": "lit"})
    assert isinstance(allowed, str) and "import requests" in allowed
    disp.store.close()


@pytest.mark.parametrize("mode", ["host_only", "raw_required"])
def test_a_refused_load_leaves_no_requirement_behind(tmp_path, mode):
    """A refusal withholds the recipe, so it has to withhold the requirement.

    The manifest was bound, and its `skill_loaded` row committed, before
    admission ran. A refused `host_only` load therefore kept narrowing every
    later Host request in the frame to its declared domains -- the search
    engines came back empty and blamed rate limiting -- and a refused
    `raw_required` load kept refusing every later Cell. Neither recipe had
    been handed over, and a restart restored the same requirement.
    """

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.store import get_store

    domains = (
        "    domains:\n      - api.openalex.org\n"
        if mode == "host_only"
        else "    domains: []\n"
    )
    skills = tmp_path / "skills"
    (skills / "blocked").mkdir(parents=True)
    (skills / "blocked" / "SKILL.md").write_text(
        "---\nname: blocked\ndescription: d\norigin: openai4s\n"
        f"capabilities:\n  network:\n    mode: {mode}\n{domains}---\n# recipe\n",
        "utf-8",
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    store = get_store(cfg.db_path)
    frame = store.new_frame(kind="turn", project_id="science")
    disp = build_dispatcher(cfg, frame_id=frame)
    with disp.bind_sandbox_status(_DEGRADED):
        refused = disp._m_load_skill("blocked")
    assert set(refused) == {"error"}, refused

    assert bindings_for(frame) == ()
    with frame_scope(frame):
        assert constrain_check_url("https://html.duckduckgo.com/html/") is None
    assert admit_cell(frame_id=frame, sandbox_status=_enforced_sandbox()).allowed

    reset_bindings()
    store.close()
    restarted = build_dispatcher(cfg, frame_id=frame)
    assert bindings_for(frame) == ()
    for dispatcher in (disp, restarted):
        dispatcher.store.close()


_REMOTE = {
    "enforced": False,
    "self_test_passed": False,
    "network_policy": "unproven",
    "backend": "remote",
}


def _never_probed():
    raise AssertionError("the daemon-side self-test was consulted")


def test_a_native_load_is_decided_by_the_daemons_own_self_test(tmp_path, monkeypatch):
    """A native `load_skill` has no worker, so it had no posture to read.

    Every such load of a `host_only` Skill was refused as "the measured
    sandbox does not confine the kernel", though nothing had been measured:
    73255df3 assumed a load always arrives from a Cell. The daemon can build
    and self-test the same sandbox a new Kernel gets, so that decides whether
    to hand the recipe over. It decides nothing else: a Cell is still
    admitted against the worker it actually runs on.
    """

    from openai4s.server import skill_network_admission as admission

    disp = _host_only_dispatcher(tmp_path)

    monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda: dict(_ENFORCED))
    disp.frame_id = "frame-native-enforced"
    loaded = disp._m_load_skill("lit")
    assert loaded.get("name") == "lit", loaded
    assert [item.skill_id for item in bindings_for("frame-native-enforced")] == ["lit"]
    assert not admit_cell(
        frame_id="frame-native-enforced", sandbox_status=_DEGRADED
    ).allowed
    assert admit_cell(
        frame_id="frame-native-enforced", sandbox_status=_enforced_sandbox()
    ).allowed

    for name, posture in (("degraded", dict(_DEGRADED)), ("unbuildable", None)):
        monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda p=posture: p)
        disp.frame_id = f"frame-native-{name}"
        refused = disp._m_load_skill("lit")
        assert set(refused) == {"error"}, (name, refused)
        assert "does not confine the kernel" in refused["error"]
        assert "import requests" not in str(refused)
        assert bindings_for(f"frame-native-{name}") == ()

    # Inside a worker's host_call the worker's own posture is the only
    # evidence, including when it is unknown.
    monkeypatch.setattr(admission, "unstarted_kernel_posture", _never_probed)
    disp.frame_id = "frame-in-cell-unknown"
    with disp.bind_sandbox_status(None):
        refused = disp._m_load_skill("lit")
    assert set(refused) == {"error"}, refused
    disp.frame_id = "frame-in-cell-degraded"
    with disp.bind_sandbox_status(_DEGRADED):
        refused = disp._m_load_skill("lit")
    assert set(refused) == {"error"}, refused
    disp.store.close()


def test_the_self_test_runs_only_for_a_load_that_needs_a_posture(tmp_path, monkeypatch):
    """`none` needs no network and `raw_required` is refused on any posture."""

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.server import skill_network_admission as admission

    skills = tmp_path / "skills"
    for name, mode in (("plain", "none"), ("raw", "raw_required")):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\norigin: openai4s\n"
            f"capabilities:\n  network:\n    mode: {mode}\n    domains: []\n---\n# x\n",
            "utf-8",
        )
    disp = build_dispatcher(Config(data_dir=tmp_path / "data", skills_dir=skills))
    disp.frame_id = "frame-no-posture-needed"
    monkeypatch.setattr(admission, "unstarted_kernel_posture", _never_probed)

    assert disp._m_load_skill("plain").get("name") == "plain"
    refused = disp._m_load_skill("raw")
    assert set(refused) == {"error"} and "raw kernel network" in refused["error"]
    disp.store.close()


def _recorded_worker_fixture(tmp_path):
    from openai4s.store import get_store

    skills = tmp_path / "skills"
    (skills / "lit").mkdir(parents=True)
    (skills / "lit" / "SKILL.md").write_text(
        "---\nname: lit\ndescription: d\norigin: openai4s\n"
        "capabilities:\n  network:\n    mode: host_only\n"
        "    domains:\n      - api.openalex.org\n---\n# recipe\n",
        "utf-8",
    )
    cfg = Config(data_dir=tmp_path / "data", skills_dir=skills)
    return cfg, get_store(cfg.db_path)


def _session_with_workers(store, *workers, ended=False):
    frame = store.new_frame(kind="turn", project_id="science")
    for language, posture in workers:
        generation = store.create_kernel_generation(
            root_frame_id=frame, language=language, environment={"sandbox": posture}
        )
        if ended:
            store.finish_kernel_generation(
                generation["generation_id"], state="released", reason="idle"
            )
    return frame


def test_a_live_worker_of_the_session_outranks_the_self_test(tmp_path, monkeypatch):
    """A worker the session is running is better evidence than a prediction.

    The weaker of a Python and an R worker decides, and the self-test is not
    consulted at all while a worker is alive.
    """

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.server import skill_network_admission as admission

    cfg, store = _recorded_worker_fixture(tmp_path)
    degraded = _session_with_workers(store, ("python", _DEGRADED))
    mixed = _session_with_workers(store, ("python", _ENFORCED), ("r", _DEGRADED))
    confined = _session_with_workers(store, ("python", _ENFORCED))

    monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda: dict(_ENFORCED))
    for frame in (degraded, mixed):
        refused = build_dispatcher(cfg, frame_id=frame)._m_load_skill("lit")
        assert set(refused) == {"error"}, refused
        assert bindings_for(frame) == ()

    monkeypatch.setattr(admission, "unstarted_kernel_posture", _never_probed)
    loaded = build_dispatcher(cfg, frame_id=confined)._m_load_skill("lit")
    assert loaded.get("name") == "lit", loaded
    store.close()


def test_a_worker_that_has_ended_decides_nothing(tmp_path, monkeypatch):
    """`latest_kernel_generation` is the newest row, alive or not.

    Read without that distinction, a released worker's record kept deciding:
    a dead "confined" row admitted a load on a host that had since degraded,
    and a dead "not confined" row kept refusing after the sandbox was fixed
    and the daemon restarted. Once the worker is gone the next one is a new
    Kernel, which is exactly what the self-test measures.
    """

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.server import skill_network_admission as admission

    cfg, store = _recorded_worker_fixture(tmp_path)
    was_confined = _session_with_workers(store, ("python", _ENFORCED), ended=True)
    was_degraded = _session_with_workers(store, ("python", _DEGRADED), ended=True)

    monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda: dict(_DEGRADED))
    refused = build_dispatcher(cfg, frame_id=was_confined)._m_load_skill("lit")
    assert set(refused) == {"error"}, refused

    monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda: dict(_ENFORCED))
    loaded = build_dispatcher(cfg, frame_id=was_degraded)._m_load_skill("lit")
    assert loaded.get("name") == "lit", loaded
    store.close()


def test_a_cluster_bound_session_is_not_judged_by_the_daemons_sandbox(
    tmp_path, monkeypatch
):
    """The daemon's sandbox says nothing about a worker on another machine.

    A session bound to a workload runs its kernel on a cluster node, or
    refuses to run at all. The binding is what the gateway reads to choose
    that kernel, so it is read here too: before the first worker, after one
    was swept, and while one is alive, the answer is the same.
    """

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.server import skill_network_admission as admission

    cfg, store = _recorded_worker_fixture(tmp_path)
    never_started = _session_with_workers(store)
    swept = _session_with_workers(store, ("python", _REMOTE), ended=True)
    running = _session_with_workers(store, ("python", _REMOTE))
    for index, frame in enumerate((never_started, swept, running)):
        store.leases.bind_session(frame, f"workload-{index}")

    monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda: dict(_ENFORCED))
    for frame in (never_started, swept, running):
        refused = build_dispatcher(cfg, frame_id=frame)._m_load_skill("lit")
        assert set(refused) == {"error"}, refused
        assert bindings_for(frame) == ()
    store.close()


def test_a_record_that_cannot_be_read_is_not_an_absent_worker(tmp_path, monkeypatch):
    """A failed lookup fell through to the self-test, which can admit.

    "No worker on record" and "the record could not be read" are different
    answers. The second may be hiding a worker that is not confined, so it is
    refused instead of handed to a prediction about a different worker.
    """

    from openai4s.host_dispatch import build_dispatcher
    from openai4s.server import skill_network_admission as admission

    cfg, store = _recorded_worker_fixture(tmp_path)
    frame = _session_with_workers(store, ("python", _REMOTE))
    disp = build_dispatcher(cfg, frame_id=frame)
    monkeypatch.setattr(admission, "unstarted_kernel_posture", lambda: dict(_ENFORCED))

    def unreadable(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    for name in ("latest_kernel_generation", "active_session_branch"):
        with monkeypatch.context() as patch:
            patch.setattr(disp.store, name, unreadable)
            refused = disp._m_load_skill("lit")
        assert set(refused) == {"error"}, (name, refused)
        assert bindings_for(frame) == ()
    with monkeypatch.context() as patch:
        patch.setattr(disp.store.leases, "workload_for_session", unreadable)
        refused = disp._m_load_skill("lit")
    assert set(refused) == {"error"}, refused
    store.close()


def test_the_self_test_reports_what_a_new_kernel_would_get(monkeypatch):
    """Real construction, no stub: `off` is the one posture every host agrees on."""

    from openai4s.server.skill_network_admission import unstarted_kernel_posture

    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "off")
    disabled = unstarted_kernel_posture()
    assert disabled is not None and disabled["enforced"] is False
    assert host_only_boundary_holds(disabled) is False

    monkeypatch.setenv("OPENAI4S_KERNEL_SANDBOX", "not-a-mode")
    assert unstarted_kernel_posture() is None

    monkeypatch.delenv("OPENAI4S_KERNEL_SANDBOX")
    measured = unstarted_kernel_posture()
    assert measured is None or {"enforced", "network_policy"} <= set(measured)


def test_search_blocked_by_a_bound_manifest_does_not_blame_rate_limits(monkeypatch):
    """A bound `host_only` Skill narrows Host requests to its declared domains.

    The search engines are outside that list, so every one of them is refused
    before a connection. The empty result said "they may be rate-limiting;
    retry shortly", and a model did exactly that, twelve times. A later
    "0 results" with the reason in a note still read as "nothing on the web"
    on the card, so the search is now refused outright (SKL-3).
    """

    from openai4s import webtools

    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")
    monkeypatch.setattr(webtools.time, "sleep", lambda *_a, **_k: None)

    def refused_by_egress(url, **_kwargs):
        check_url(url)
        return None

    monkeypatch.setattr(webtools, "_http_get", refused_by_egress)

    unbound = webtools.web_search("KRAS G12C resistance", engines=("builtin",))
    assert unbound["count"] == 0 and "rate-limiting" in unbound["note"]

    bind_skill_load(
        frame_id="frame-narrowed-search",
        action_group_id="ag-1",
        skill_id="literature-review",
        version="1",
        document_digest="a" * 64,
        capability=declared_capability(
            "host_only", ["api.openalex.org", "doi.org"], source="frontmatter"
        ),
        source="load_skill",
    )
    with frame_scope("frame-narrowed-search"):
        with pytest.raises(EgressBlocked) as refused:
            webtools.web_search("KRAS G12C resistance", engines=("builtin",))
    reason = str(refused.value)
    assert "rate-limiting" not in reason
    assert "retrying will not help" in reason
    # The domains the refusal names, compared whole rather than searched for.
    named = reason.split("network to 2 domains (", 1)[1].split(")", 1)[0]
    assert named.split(", ") == ["api.openalex.org", "doi.org"]


def _one_python_cell(_messages, _cfg, **_kwargs):
    return {
        "content": "```python\nprint(42)\n```",
        "reasoning": None,
        "usage": {},
        "finish_reason": "stop",
        "raw": {},
    }


def test_cli_allowlist_refuses_a_degraded_kernel_without_a_traceback(
    monkeypatch, capsys
):
    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    created = []

    class DegradedKernel:
        generation = 1
        sandbox_status = {
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "not_enforced",
            "backend": None,
        }

        def __init__(self, *_args, **_kwargs):
            self.closed = False
            self.executed = 0
            created.append(self)

        def is_alive(self):
            return not self.closed

        def execute(self, *_args, **_kwargs):
            self.executed += 1
            raise AssertionError("user or Skill bootstrap code reached the worker")

        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(loop_mod, "chat", _one_python_cell)
    monkeypatch.setattr(loop_mod, "Kernel", DegradedKernel)
    agent = Agent(use_skills=False, allow_delegate=False, max_turns=1)
    result = agent.run("run one Cell")
    assert result["stop_reason"] == "egress_boundary_unavailable"
    assert any(
        "egress_boundary_unavailable" in str(turn.get("content"))
        for turn in result["transcript"]
    )
    assert "error: egress_boundary_unavailable:" in capsys.readouterr().err
    assert created and created[0].closed is True
    assert created[0].executed == 0


def test_cli_projects_a_boundary_refusal_raised_during_execute(monkeypatch, capsys):
    from openai4s.egress import EgressBoundaryUnavailable

    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")

    class ProvenKernel:
        generation = 1
        sandbox_status = {
            "mode": "enforce",
            "state": "enabled",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
        }

        def __init__(self, *_args, **_kwargs):
            self.closed = False

        def is_alive(self):
            return not self.closed

        def execute(self, *_args, **_kwargs):
            raise EgressBoundaryUnavailable(
                {
                    "code": "egress_boundary_unavailable",
                    "reason": "mode flipped before the frame was sent",
                }
            )

        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(loop_mod, "chat", _one_python_cell)
    monkeypatch.setattr(loop_mod, "Kernel", ProvenKernel)
    agent = Agent(use_skills=False, allow_delegate=False, max_turns=1)
    result = agent.run("run one Cell")
    assert result["stop_reason"] == "egress_boundary_unavailable"
    captured = capsys.readouterr().err
    assert "error: egress_boundary_unavailable:" in captured
    assert "mode flipped before the frame was sent" in captured


def test_r_egress_refusal_keeps_the_kernel(monkeypatch):
    import threading

    from openai4s.egress import EgressBoundaryUnavailable

    monkeypatch.setenv("OPENAI4S_EGRESS", "allowlist")
    agent = object.__new__(Agent)
    agent.frame_id = "frame-r-egress"
    agent.dispatcher = SimpleNamespace(active_r_env=None)
    agent._foreground_lock = threading.Lock()
    agent._generation_recorder = None
    agent.cancellation = None
    agent.workspace = None
    agent.read_isolation = None
    shut: list[str] = []

    class KernelDouble:
        def __init__(self, sandbox_status, execute):
            self.sandbox_status = sandbox_status
            self._execute = execute

        def is_alive(self):
            return True

        def shutdown(self):
            shut.append("shutdown")

        def execute(self, *_args, **_kwargs):
            return self._execute()

    agent._r_kernel = KernelDouble(
        {
            "enforced": False,
            "self_test_passed": False,
            "network_policy": "not_enforced",
            "backend": None,
        },
        lambda: (_ for _ in ()).throw(AssertionError("r cell ran")),
    )
    agent._r_kernel_env = None
    with pytest.raises(PermissionError, match="egress_boundary_unavailable"):
        agent._execute_r("print(1)")
    assert shut == []
    assert agent._r_kernel is not None

    agent._r_kernel = KernelDouble(
        {
            "mode": "enforce",
            "state": "enabled",
            "backend": "seatbelt",
            "enforced": True,
            "self_test_passed": True,
            "network_policy": "blocked",
        },
        lambda: (_ for _ in ()).throw(
            EgressBoundaryUnavailable(
                {
                    "code": "egress_boundary_unavailable",
                    "reason": "mode flipped on the r kernel",
                }
            )
        ),
    )
    with pytest.raises(EgressBoundaryUnavailable, match="mode flipped on the r kernel"):
        agent._execute_r("print(1)")
    assert shut == []
    assert agent._r_kernel is not None
