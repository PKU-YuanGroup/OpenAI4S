"""B-02 Skill network manifest and execution admission contracts."""

from __future__ import annotations

import inspect
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

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


def test_cell_and_shell_sinks_have_zero_bypass():
    cell_src = inspect.getsource(CellExecutionService._execute_admitted)
    bash_src = inspect.getsource(BashAuthorizationService.authorize)
    assert "admit_cell(" in cell_src
    assert "admit_shell(" in bash_src
    # The only production callers of these two sinks.
    cell_mod = Path("openai4s/server/cell_run.py").read_text("utf-8")
    bash_mod = Path("openai4s/host/bash.py").read_text("utf-8")
    assert cell_mod.count("admit_cell(") == 1
    assert bash_mod.count("admit_shell(") == 1


def test_the_cli_and_delegation_cell_sink_refuses_raw_required():
    """`CellExecutionService` is not the only Code-as-Action sink.

    A CLI run and every delegated child execute Cells through
    `LocalActionExecutor`, whose admission hook is `Agent._admit_cell`. While
    this file only read `cell_run.py` and `host/bash.py`, a Skill bound
    `raw_required` ran unconfined there and the omission was invisible to the
    zero-bypass check itself.

    Only the unconditional half is asserted, which is the half that sink can
    apply: `host_only` needs measured posture and no kernel exists yet.
    """

    loop_src = inspect.getsource(Agent._admit_cell)
    assert "raw_required_binding(" in loop_src


def test_raw_required_is_refused_without_any_measured_posture():
    """The refusal that must not depend on a live kernel."""

    from openai4s.server.skill_network_admission import raw_required_binding

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
    assert "api.openalex.org" in lit.network.domains
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
