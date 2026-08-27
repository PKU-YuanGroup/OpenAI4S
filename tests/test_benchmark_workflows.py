"""The benchmark, run — not merely present.

The proposal is explicit about what would make thirteen workflows and
forty-six cases worthless: a directory of fixtures nobody executes, or cases
that pass because the thing they exercise is a mock. So this file runs every
case against the real subsystems and asserts the outcome each case declared.

The declared outcome is the point. A case that says `failure` and completes
cleanly has failed exactly as much as one that says `success` and raises — a
benchmark scoring "no exception" measures only the half of the system nobody
doubted. The suite deliberately includes cases that watch something refuse.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace

import pytest

from openai4s.benchmark import load_workflows, run_case
from openai4s.benchmark.model import OUTCOMES
from openai4s.benchmark.steps import STEPS

WORKFLOWS = load_workflows()
CASES = [(w, c) for w in WORKFLOWS for c in w.cases]
CASE_PARAMS = [
    (
        pytest.param(
            workflow,
            case,
            id=case.id,
            marks=pytest.mark.stubbed_backend,
        )
        if workflow.id == "tool-bringup"
        else pytest.param(workflow, case, id=case.id)
    )
    for workflow, case in CASES
]


# --------------------------------------------------------------------------
# the frozen shape of the suite
# --------------------------------------------------------------------------


def test_thirteen_workflows_are_frozen():
    """The number is the commitment. Dropping one to make a run green is the
    failure mode this asserts against."""
    assert len(WORKFLOWS) == 13, [w.id for w in WORKFLOWS]


def test_every_workflow_carries_at_least_two_cases():
    thin = [w.id for w in WORKFLOWS if len(w.cases) < 2]
    assert not thin, f"a single case cannot represent a workflow: {thin}"


def test_forty_six_versioned_cases_are_frozen():
    assert len(CASES) == 46


def test_the_engineering_deliverable_workflows_are_present_and_refuse():
    """`codebase-mode` and `delegation` are the two workflows whose subject is
    a claim rather than a computation, and both would be vacuous as
    happy-path-only suites: the point is what the Host refuses to believe."""
    by_id = {w.id: w for w in WORKFLOWS}
    codebase = by_id["codebase-mode"]
    assert len(codebase.cases) == 9
    refusing = [c.id for c in codebase.cases if c.outcome == "failure"]
    assert len(refusing) == 6, refusing
    delegation = by_id["delegation"]
    assert len(delegation.cases) == 3
    # Terminal states only. A delegation case that waits on timing measures the
    # runner, not the contract.
    assert {c.outcome for c in delegation.cases} == {"provenance"}


def test_tool_bringup_carries_fourteen_cases():
    tool_bringup = next(w for w in WORKFLOWS if w.id == "tool-bringup")
    assert len(tool_bringup.cases) == 14


def test_every_case_id_is_unique():
    ids = [case.id for _workflow, case in CASES]
    assert len(ids) == len(set(ids))


def test_every_workflow_declares_what_would_make_it_fail():
    """A workflow with no stated failure condition is a demo, not a case."""
    for workflow in WORKFLOWS:
        assert workflow.failure_conditions, workflow.id
        assert workflow.version
        assert workflow.summary


def test_every_step_a_manifest_names_is_implemented():
    """A manifest may not describe work nothing performs."""
    for workflow in WORKFLOWS:
        names = set(workflow.steps) | {
            name for case in workflow.cases for name in case.steps
        }
        missing = sorted(names - set(STEPS))
        assert not missing, f"{workflow.id} names unimplemented step(s) {missing}"


def test_the_suite_measures_more_than_the_happy_path():
    """Success-only coverage is the shape a benchmark drifts into."""
    outcomes = {case.outcome for _workflow, case in CASES}
    assert outcomes <= OUTCOMES
    assert outcomes - {"success"}, "every case expects success"
    refusing = [
        c.id for _w, c in CASES if c.outcome in ("failure", "permission_denied")
    ]
    assert len(refusing) >= 3, f"only {len(refusing)} case(s) watch something refuse"


# --------------------------------------------------------------------------
# and then it runs
# --------------------------------------------------------------------------


@pytest.mark.parametrize("workflow,case", CASE_PARAMS)
def test_case(workflow, case):
    result = run_case(workflow, case)
    if result.skipped:
        pytest.skip(result.detail)
    assert result.passed, f"{case.id} ({case.outcome}): {result.detail}"


def _tiny_workflow(steps, cases):
    from openai4s.benchmark.model import Case, Workflow

    return Workflow(
        id="probe",
        version="1",
        title="probe",
        summary="probe",
        steps=tuple(steps),
        permissions=(),
        artifacts=(),
        failure_conditions=("x",),
        cases=tuple(cases),
    )


def test_an_unimplemented_step_is_a_hard_error_not_a_scored_refusal():
    """A manifest naming a step nothing implements is a manifest bug. Raising it
    inside the outcome-evaluation try let an error-expecting case catch the
    KeyError and score it green — a workflow describing work nothing does passed.
    It must fail hard instead, regardless of the declared outcome."""
    from openai4s.benchmark.model import Case

    case = Case(
        id="c",
        workflow="probe",
        title="c",
        outcome="failure",
        steps=("does_not_exist",),
        expect={},
    )
    with pytest.raises(KeyError, match="not.*implemented|nothing does"):
        run_case(_tiny_workflow(("does_not_exist",), [case]), case)


def test_an_error_expecting_case_must_assert_something_about_the_error(monkeypatch):
    """An empty `expect` on a failure/permission_denied case would pass on any
    incidental exception — fabricated coverage in a suite that gates releases.
    An unexpected infrastructure error must not be scored as the declared
    refusal when the case asserts nothing about it."""
    from openai4s.benchmark import runner as runner_mod
    from openai4s.benchmark.model import Case

    def boom(context, inputs):
        raise RuntimeError("an incidental infrastructure failure, not a refusal")

    monkeypatch.setitem(runner_mod.STEPS, "_boom", boom)
    case = Case(
        id="c",
        workflow="probe",
        title="c",
        outcome="failure",
        steps=("_boom",),
        expect={},  # asserts nothing about the error
    )
    result = run_case(_tiny_workflow(("_boom",), [case]), case)
    assert result.passed is False, "an incidental error was scored as the refusal"
    assert not result.skipped
    assert "asserts nothing" in result.detail or "empty expect" in result.detail


# --------------------------------------------------------------------------
# the manifests must ship, and the root parameter must be honoured
# --------------------------------------------------------------------------


def test_the_workflows_are_shipped_as_package_data():
    """An installed wheel that did not ship the manifests would make
    `openai4s benchmark` report a green run over zero workflows."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text("utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        # `tomllib` is stdlib from 3.11, and this project supports 3.10 — where
        # an unconditional import made the whole test error out rather than
        # check anything. The core is zero-dependency, so there is no parser to
        # fall back to; the same claim is asserted as text, which is what the
        # two checks below already do.
        assert '"workflows*"' in text, (
            "the benchmark manifests are not packaged; an installed benchmark "
            "would find nothing and pass silently"
        )
    else:
        include = tomllib.loads(text)["tool"]["setuptools"]["packages"]["find"][
            "include"
        ]
        assert "workflows*" in include, (
            "the benchmark manifests are not packaged; an installed benchmark "
            "would find nothing and pass silently"
        )
    manifest = (root / "MANIFEST.in").read_text("utf-8")
    assert "recursive-include workflows" in manifest
    build = (root / "scripts" / "build_macos_dmg.sh").read_text("utf-8")
    assert "/workflows" in build, "the DMG does not copy the workflow manifests"


def test_run_all_honours_the_root_it_is_given(tmp_path):
    """The parameter was accepted and ignored, so a caller pointing at another
    suite silently got the repository default."""
    from openai4s.benchmark.runner import run_all

    empty = tmp_path / "empty-suite"
    empty.mkdir()
    report = run_all(empty)
    assert (
        report["workflows"] == 0
    ), "run_all ran the default suite instead of the empty root it was given"


def test_the_cli_treats_zero_workflows_as_a_failure(monkeypatch, capsys, tmp_path):
    """Zero workflows is not a pass. A packaging regression must not exit 0."""
    import importlib
    import types

    from openai4s.benchmark import model as bmodel

    cli = importlib.import_module("openai4s.cli.main")
    monkeypatch.setattr(bmodel, "WORKFLOW_ROOT", tmp_path / "no-workflows")
    rc = cli.cmd_benchmark(types.SimpleNamespace(list=False, json=False))
    assert rc == 1
    assert "no benchmark workflows" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Stage 0: the versioned next-round acceptance pack
# --------------------------------------------------------------------------


def test_next_round_acceptance_manifest_has_exact_field_and_safety_coverage():
    from openai4s.benchmark.acceptance import (
        DEFAULT_ACCEPTANCE_MANIFEST,
        FIELD_PATH_IDS,
        METRIC_IDS,
        SAFETY_ACTION_IDS,
        load_acceptance_pack,
    )

    pack = load_acceptance_pack()
    assert pack.pack_version == "2026-08-16.2"
    assert tuple(item.id for item in pack.field_paths) == FIELD_PATH_IDS
    assert tuple(item.id for item in pack.safety_actions) == SAFETY_ACTION_IDS
    assert tuple(item.id for item in pack.metrics) == METRIC_IDS
    assert len(pack.field_paths) == 6
    assert len(pack.safety_actions) == 7
    raw = json.loads(DEFAULT_ACCEPTANCE_MANIFEST.read_text("utf-8"))
    canonical = json.dumps(
        raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    assert pack.manifest_digest == "sha256:" + hashlib.sha256(canonical).hexdigest()

    claims = {item.id: item.claim for item in pack.field_paths}
    assert claims["ketcher"] == "baseline_observation"
    assert claims["clinvar"] == "baseline_observation"
    reviewer = next(
        item for item in pack.field_paths if item.id == "reviewer_correction"
    )
    assert reviewer.expected.public()["value"]["workspace_unchanged"] is True
    for action in pack.safety_actions:
        if action.id in {"safe_read", "restricted_write"}:
            assert action.execution == "confined_local"
        else:
            assert action.execution == "preflight_only"


def test_next_round_acceptance_manifest_rejects_unknown_nested_fields(tmp_path):
    from openai4s.benchmark.acceptance import (
        DEFAULT_ACCEPTANCE_MANIFEST,
        AcceptanceManifestError,
        load_acceptance_pack,
    )

    record = json.loads(DEFAULT_ACCEPTANCE_MANIFEST.read_text("utf-8"))
    record["field_paths"][0]["expected"]["value"]["sloep"] = 2.0
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AcceptanceManifestError, match="unknown observation fields"):
        load_acceptance_pack(path)


def test_next_round_acceptance_manifest_rejects_missing_or_extra_actions(tmp_path):
    from openai4s.benchmark.acceptance import (
        DEFAULT_ACCEPTANCE_MANIFEST,
        AcceptanceManifestError,
        load_acceptance_pack,
    )

    record = json.loads(DEFAULT_ACCEPTANCE_MANIFEST.read_text("utf-8"))
    record["safety_actions"].pop()
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AcceptanceManifestError, match="expected exactly"):
        load_acceptance_pack(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda record: record["field_paths"][0].__setitem__(
                "claim", "baseline_observation"
            ),
            "frozen contract requires 'capability'",
        ),
        (
            lambda record: record["field_paths"][0]["expected"]["value"].pop("slope"),
            "frozen assertion fields",
        ),
        (
            lambda record: record["field_paths"][1]["expected"]["value"].pop(
                "workspace_unchanged"
            ),
            "frozen assertion fields",
        ),
    ],
)
def test_next_round_acceptance_manifest_refuses_contract_weakening(
    tmp_path, mutate, message
):
    from openai4s.benchmark.acceptance import (
        DEFAULT_ACCEPTANCE_MANIFEST,
        AcceptanceManifestError,
        load_acceptance_pack,
    )

    record = json.loads(DEFAULT_ACCEPTANCE_MANIFEST.read_text("utf-8"))
    mutate(record)
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AcceptanceManifestError, match=message):
        load_acceptance_pack(path)


def test_next_round_acceptance_manifest_binds_content_to_version(tmp_path):
    from openai4s.benchmark.acceptance import (
        DEFAULT_ACCEPTANCE_MANIFEST,
        AcceptanceManifestError,
        load_acceptance_pack,
    )

    record = json.loads(DEFAULT_ACCEPTANCE_MANIFEST.read_text("utf-8"))
    record["title"] += " weakened without a version bump"
    path = tmp_path / "acceptance.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(
        AcceptanceManifestError, match="manifest content does not match"
    ):
        load_acceptance_pack(path)

    record = json.loads(DEFAULT_ACCEPTANCE_MANIFEST.read_text("utf-8"))
    record["pack_version"] = "unreviewed-version"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(AcceptanceManifestError, match="no frozen manifest digest"):
        load_acceptance_pack(path)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda pack: replace(pack, field_paths=()),
        lambda pack: replace(pack, safety_actions=pack.safety_actions[:-1]),
        lambda pack: replace(pack, manifest_digest="sha256:" + "0" * 64),
        lambda pack: replace(
            pack,
            field_paths=(
                replace(
                    pack.field_paths[0],
                    expected=replace(pack.field_paths[0].expected, value={}),
                ),
                *pack.field_paths[1:],
            ),
        ),
    ],
)
def test_acceptance_runner_refuses_caller_weakened_pack(tmp_path, mutate):
    from openai4s.benchmark.acceptance import (
        AcceptanceManifestError,
        load_acceptance_pack,
        run_acceptance_pack,
    )

    weakened = mutate(load_acceptance_pack())
    with pytest.raises(AcceptanceManifestError, match="exact frozen canonical pack"):
        run_acceptance_pack(weakened, root=tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_acceptance_ketcher_loopback_probe_repeats_without_stream_leak(
    tmp_path, capsys
):
    from types import SimpleNamespace

    from openai4s.benchmark import acceptance

    for iteration in range(5):
        run_root = tmp_path / f"ketcher-{iteration}"
        run_root.mkdir()
        observed, evidence = acceptance._probe_ketcher(
            SimpleNamespace(run_root=run_root)
        )
        assert observed["http_status"] == 200
        assert observed["route"] == "/ketcher"
        assert "credential banner was captured and verified" in evidence[0].detail
        assert "read-only env-injection backend" in evidence[0].detail
        assert not (run_root / "ketcher-route-data" / "access-token").exists()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "?token=" not in captured.err
    assert not re.search(
        r"token(?:=|:\s*)[A-Za-z0-9_-]{20,}", captured.err, re.IGNORECASE
    )


@pytest.mark.stubbed_backend
def test_next_round_acceptance_pack_replays_all_paths_and_reports_evidence(
    tmp_path, monkeypatch
):
    from openai4s import review as review_mod
    from openai4s.benchmark import run_acceptance_pack

    # The acceptance fake must be supplied to exactly its own Reviewer call;
    # replacing or reaching the process-global provider would race a live run.
    monkeypatch.setattr(
        review_mod,
        "chat",
        lambda *_args, **_kwargs: pytest.fail(
            "acceptance must not replace or call the global Reviewer provider"
        ),
    )

    report = run_acceptance_pack(root=tmp_path)
    assert report["pass"] is True, [
        (item["id"], item["observed"])
        for item in [*report["field_paths"], *report["safety_actions"]]
        if not item["pass"]
    ]
    assert len(report["field_paths"]) == 6
    assert len(report["safety_actions"]) == 7
    assert report["recorded_at_ms"] > 0
    assert report["manifest_digest"].startswith("sha256:")
    for result in [*report["field_paths"], *report["safety_actions"]]:
        assert set(("expected", "observed", "pass", "evidence", "duration_ms")) <= set(
            result
        )
        assert result["evidence"]
        assert result["duration_ms"] >= 0

    fields = {item["id"]: item for item in report["field_paths"]}
    assert fields["ketcher"]["observed"]["status"] == "placeholder"
    assert fields["ketcher"]["observed"]["http_status"] == 200
    assert fields["ketcher"]["observed"]["route"] == "/ketcher"
    assert "GET /ketcher" in fields["ketcher"]["evidence"][0]["source"]
    assert fields["ketcher"]["capability_pass"] is False
    assert fields["clinvar"]["observed"]["status"] == "not_implemented"
    assert fields["clinvar"]["capability_pass"] is False
    assert fields["reviewer_correction"]["observed"]["repair_triggered"] is False
    assert fields["reviewer_correction"]["observed"]["workspace_unchanged"] is True
    assert (
        fields["reviewer_correction"]["expected"]["value"]["workspace_unchanged"]
        is True
    )
    assert report["summary"]["capability_passes"] == 1

    latency = report["metrics"]["latency_ms"]
    assert latency["samples"] == 6
    assert latency["p50"] is not None and latency["p95"] is not None
    tokens = report["metrics"]["tokens"]
    assert tokens["offline_contract"]["samples"] == 1
    assert tokens["offline_contract"]["total_tokens"] == 56
    assert tokens["live_observed"]["samples"] == 0
    assert tokens["live_observed"]["total_tokens"] is None
    review_hits = report["metrics"]["review_hit_rate"]
    assert review_hits["offline_contract"]["denominator"] == 1
    assert review_hits["offline_contract"]["value"] == 1.0
    assert review_hits["live_observed"]["denominator"] == 0
    assert review_hits["live_observed"]["value"] is None
    environment = report["environment"]
    assert environment["isolation"]["reviewer_inference"] == (
        "offline_call_scoped_injection"
    )
    assert environment["configured_security"]["unattended_approval"] == "deny"
    assert environment["kernel_sandboxes"]
    for metric_id in ("cell_failure_rate", "duplicate_version_rate"):
        metric = report["metrics"][metric_id]
        assert "denominator" in metric
        assert metric["denominator_definition"]
        assert metric["zero_sample_behavior"]
    assert review_hits["denominator_definition"]
    assert review_hits["zero_sample_behavior"]


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "terminal",
    [
        {"error": "forced failure"},
        {"interrupted": True},
    ],
)
def test_acceptance_cell_attempt_counts_error_and_interrupted_terminals(terminal):
    from types import SimpleNamespace

    from openai4s.benchmark import acceptance

    class Kernel:
        def execute(self, _code):
            return terminal

    metrics = acceptance.AcceptanceMetrics()
    runtime = SimpleNamespace(metrics=metrics)
    assert acceptance._execute_cell(runtime, Kernel(), "pass") == terminal
    assert metrics.cell_attempts == 1
    assert metrics.cell_failures == 1


@pytest.mark.stubbed_backend
def test_acceptance_cell_attempt_counts_a_raised_execute():
    from types import SimpleNamespace

    from openai4s.benchmark import acceptance

    class Kernel:
        def execute(self, _code):
            raise RuntimeError("forced execute failure")

    metrics = acceptance.AcceptanceMetrics()
    runtime = SimpleNamespace(metrics=metrics)
    with pytest.raises(RuntimeError, match="forced execute failure"):
        acceptance._execute_cell(runtime, Kernel(), "pass")
    assert metrics.cell_attempts == 1
    assert metrics.cell_failures == 1


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("terminal", ["raise", "error", "interrupted"])
def test_acceptance_reviewer_failure_stays_in_offline_denominator(
    tmp_path, monkeypatch, terminal
):
    from openai4s import review as review_mod
    from openai4s.benchmark import acceptance
    from openai4s.config import Config, LLMConfig

    workspace = tmp_path / terminal
    workspace.mkdir()
    runtime = acceptance._Runtime(
        run_root=tmp_path,
        workspace=workspace,
        config=Config(
            data_dir=tmp_path / f"data-{terminal}",
            llm=LLMConfig(provider="deepseek", api_key="offline"),
        ),
    )

    if terminal == "raise":

        def fail_review(*_args, **_kwargs):
            raise RuntimeError("forced Reviewer failure")

        monkeypatch.setattr(review_mod, "review_evidence", fail_review)
        expected = "forced Reviewer failure"
    else:
        monkeypatch.setattr(
            review_mod,
            "review_evidence",
            lambda *_args, **_kwargs: {terminal: True},
        )
        expected = "error terminal result"

    with pytest.raises(RuntimeError, match=expected):
        acceptance._probe_reviewer_correction(runtime)
    assert runtime.metrics.offline_review_cases == 1
    assert runtime.metrics.offline_review_failures == 1
    assert runtime.metrics.offline_review_hits == 0


@pytest.mark.stubbed_backend
def test_acceptance_reviewer_workspace_write_is_observed_and_fails_contract(
    tmp_path,
):
    from openai4s.benchmark import acceptance
    from openai4s.config import Config, LLMConfig

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = acceptance._Runtime(
        run_root=tmp_path,
        workspace=workspace,
        config=Config(
            data_dir=tmp_path / "data",
            llm=LLMConfig(provider="deepseek", api_key="offline"),
        ),
    )
    written = workspace / "reviewer-illegal-write.txt"

    def writing_reviewer(_messages, _cfg, **_kwargs):
        # Stage 0 observes and fails this boundary violation.  It does not
        # pretend the future read-only Reviewer sandbox already prevented it.
        written.write_text("Reviewer changed the formal workspace\n", encoding="utf-8")
        return {
            "content": json.dumps(
                {
                    "verdict": "issues",
                    "summary": "The planted coefficient is inconsistent.",
                    "issues": [
                        {
                            "severity": "high",
                            "title": "Regression coefficient mismatch",
                            "detail": "The answer reports 3 while the evidence reports 2.",
                            "evidence": "regression.json: slope=2",
                            "artifact_id": "acceptance-regression",
                        }
                    ],
                }
            ),
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    reviewer_spec = next(
        item
        for item in acceptance.load_acceptance_pack().field_paths
        if item.id == "reviewer_correction"
    )
    try:
        result = acceptance._run_probe(
            runtime,
            probe_id=reviewer_spec.id,
            expected=reviewer_spec.expected,
            probe=lambda active: acceptance._probe_reviewer_correction(
                active, chat_call=writing_reviewer
            ),
            extra={"claim": reviewer_spec.claim},
        )
    finally:
        runtime.close()

    assert written.read_text("utf-8") == "Reviewer changed the formal workspace\n"
    assert result["observed"]["verdict"] == "issues"
    assert result["observed"]["workspace_unchanged"] is False
    assert result["expected"]["value"]["workspace_unchanged"] is True
    assert result["pass"] is False


@pytest.mark.stubbed_backend
def test_acceptance_duplicate_capture_failure_counts_started_opportunity(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from openai4s.benchmark import acceptance
    from openai4s.kernel import manager as manager_mod

    class Kernel:
        def __init__(self, *, cwd):
            self.cwd = Path(cwd)
            self.calls = 0

        @property
        def sandbox_status(self):
            return {
                "mode": "auto",
                "state": "test",
                "backend": "stub",
                "enforced": False,
                "self_test_passed": None,
                "network_policy": "test",
                "warning": None,
            }

        def execute(self, _code):
            self.calls += 1
            if self.calls == 1:
                (self.cwd / "regression.json").write_text(
                    '{"intercept":1.0,"slope":2.0}', encoding="utf-8"
                )
            return {"stdout": "ok"}

        def shutdown(self):
            return None

    class Runtime:
        def __init__(self):
            self.workspace = tmp_path
            self.metrics = acceptance.AcceptanceMetrics()
            self.capture_calls = 0

        def record_kernel_posture(self, _language, _status):
            return None

        def log_cell(self, _code, _result, language):
            return f"cell-{language}-{self.capture_calls}"

        def capture(self, path, _cell):
            self.capture_calls += 1
            if self.capture_calls == 2:
                raise RuntimeError("forced duplicate capture failure")
            return {
                "version_id": "v-first",
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

    monkeypatch.setattr(manager_mod, "Kernel", Kernel)
    runtime = Runtime()
    with pytest.raises(RuntimeError, match="forced duplicate capture failure"):
        acceptance._probe_deterministic_regression(runtime)
    assert runtime.metrics.cell_attempts == 2
    assert runtime.metrics.cell_failures == 0
    assert runtime.metrics.duplicate_opportunities == 1
    assert runtime.metrics.duplicate_failures == 1


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(("fail_on", "attempts"), [(1, 1), (2, 2)])
def test_acceptance_failed_cell_never_fabricates_duplicate_opportunity(
    tmp_path, monkeypatch, fail_on, attempts
):
    from pathlib import Path

    from openai4s.benchmark import acceptance
    from openai4s.kernel import manager as manager_mod

    class Kernel:
        def __init__(self, *, cwd):
            self.cwd = Path(cwd)
            self.calls = 0

        @property
        def sandbox_status(self):
            return {}

        def execute(self, _code):
            self.calls += 1
            if self.calls == fail_on:
                return {"error": "forced cell failure"}
            (self.cwd / "regression.json").write_text(
                '{"intercept":1.0,"slope":2.0}', encoding="utf-8"
            )
            return {"stdout": "ok"}

        def shutdown(self):
            return None

    class Runtime:
        def __init__(self):
            self.workspace = tmp_path
            self.metrics = acceptance.AcceptanceMetrics()

        def record_kernel_posture(self, _language, _status):
            return None

        def log_cell(self, _code, _result, language):
            return f"cell-{language}"

        def capture(self, path, _cell):
            return {
                "version_id": "v-first",
                "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
            }

    monkeypatch.setattr(manager_mod, "Kernel", Kernel)
    runtime = Runtime()
    with pytest.raises(RuntimeError, match="forced cell failure"):
        acceptance._probe_deterministic_regression(runtime)
    assert runtime.metrics.cell_attempts == attempts
    assert runtime.metrics.cell_failures == 1
    assert runtime.metrics.duplicate_opportunities == 0
    assert runtime.metrics.duplicate_failures == 0


@pytest.mark.stubbed_backend
def test_next_round_safety_probes_never_execute_external_actions(tmp_path):
    from openai4s.benchmark import run_acceptance_pack

    report = run_acceptance_pack(root=tmp_path)
    safety = {item["id"]: item for item in report["safety_actions"]}
    for action_id in (
        "external_write",
        "network_get",
        "sensitive_payload_egress",
        "narrow_delete",
        "broad_delete",
    ):
        observed = safety[action_id]["observed"]
        assert observed["executed"] is False
        assert observed["outside_effect"] is False
    assert safety["network_get"]["observed"]["transport"] == "not_invoked"
    assert safety["broad_delete"]["observed"]["effective_decision"] == "deny"


@pytest.mark.stubbed_backend
def test_acceptance_global_network_deny_overrides_allow_and_fails_baseline(
    tmp_path, monkeypatch
):
    from openai4s.benchmark import run_acceptance_pack

    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "0")
    report = run_acceptance_pack(root=tmp_path)
    network = next(
        item for item in report["safety_actions"] if item["id"] == "network_get"
    )

    assert report["pass"] is False
    assert report["environment"]["configured_security"]["network_allowed"] is False
    assert network["expected"]["value"]["effective_decision"] == "allow"
    assert network["observed"]["policy_decision"] == "allow"
    assert network["observed"]["global_network_allowed"] is False
    assert network["observed"]["effective_decision"] == "deny"
    assert network["observed"]["executed"] is False
    assert network["observed"]["transport"] == "not_invoked"
    assert network["pass"] is False
    assert report["summary"]["safety_action_failures"] == 1


def test_acceptance_metric_zero_samples_are_explicit():
    from openai4s.benchmark import acceptance

    metrics = acceptance._aggregate_metrics(
        acceptance.load_acceptance_pack().metrics,
        acceptance.AcceptanceMetrics(),
        [],
    )
    assert metrics["latency_ms"]["p50"] is None
    assert metrics["latency_ms"]["p95"] is None
    assert metrics["cell_failure_rate"]["value"] is None
    assert metrics["duplicate_version_rate"]["value"] is None
    assert metrics["tokens"]["live_observed"]["total_tokens"] is None
    assert metrics["review_hit_rate"]["live_observed"]["value"] is None
    for metric in metrics.values():
        assert metric["zero_sample_behavior"]


@pytest.mark.stubbed_backend
def test_acceptance_pack_has_one_machine_readable_cli_entrypoint(capsys):
    import importlib

    cli = importlib.import_module("openai4s.cli.main")
    rc = cli.main(["benchmark", "--acceptance", "--json"])
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    assert rc == 0
    assert "?token=" not in captured.err
    assert "X-OpenAI4S-Token" not in captured.err
    assert not re.search(
        r"token(?:=|:\s*)[A-Za-z0-9_-]{20,}", captured.err, re.IGNORECASE
    )
    assert report["schema_version"] == 1
    assert report["manifest_digest"] == (
        "sha256:d6b1b5c991e4092475b24c5e89ec8fa220ca166fa4b6f6582abb07e34279f533"
    )
    assert report["pass"] is True
    assert len(report["field_paths"]) == 6
    assert len(report["safety_actions"]) == 7
    assert report["summary"]["capability_passes"] == 1
