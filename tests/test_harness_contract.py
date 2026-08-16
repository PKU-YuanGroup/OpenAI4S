"""Contract tests for the stdlib-only deterministic scenario harness."""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest

from harness.cli import main
from harness.evals.retrosynthesis_backends import evaluate_backend_replays
from harness.faults import FakeClock, FakeUUIDFactory, FaultSchedule
from harness.normalize import normalized_trace_bytes
from harness.providers.scripted_llm import ScriptedLLM, ScriptedProviderError
from harness.runner import run_scenario
from harness.schema import (
    FaultSpec,
    ProviderStep,
    Scenario,
    ScenarioValidationError,
    load_scenario,
)
from openai4s.config import get_config

_SCENARIOS = Path(__file__).resolve().parents[1] / "harness" / "scenarios"


def _scenario_paths() -> list[Path]:
    return sorted(_SCENARIOS.rglob("*.json"))


def _skill_module(name: str):
    skills_dir = str(get_config().skills_dir)
    if skills_dir not in sys.path:
        sys.path.insert(0, skills_dir)
    return importlib.import_module(name)


def test_pr_offline_baseline_has_at_least_three_versioned_scenarios():
    scenarios = [load_scenario(path) for path in _scenario_paths()]
    selected = [
        scenario
        for scenario in scenarios
        if scenario.in_tier("pr") and scenario.is_offline
    ]
    assert len(selected) >= 3
    assert all(scenario.schema_version == 1 for scenario in selected)


def test_schema_rejects_unknown_version_and_unknown_fields():
    raw = {
        "schema_version": 2,
        "id": "bad_version",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [{"response": {"content": "x"}}],
        "faults": [],
        "expect": {"terminal_reason": "script_exhausted", "model_attempts": 1},
    }
    with pytest.raises(ScenarioValidationError, match="schema_version"):
        Scenario.from_dict(raw)
    raw["schema_version"] = 1
    raw["surprise"] = True
    with pytest.raises(ScenarioValidationError, match="unsupported field"):
        Scenario.from_dict(raw)


def test_schema_defaults_rules_only_and_rejects_hyphenated_alias():
    raw = {
        "schema_version": 1,
        "id": "permission_mode",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "fixtures": {"workspace": "minimal"},
        "provider_script": [{"response": {"content": "x"}}],
        "faults": [],
        "expect": {"terminal_reason": "script_exhausted", "model_attempts": 1},
    }
    scenario = Scenario.from_dict(raw)
    assert scenario.fixtures == {"workspace": "minimal"}
    assert scenario.permissions.noninteractive == "rules_only"
    raw["permissions"] = {"noninteractive": "rules-only"}
    with pytest.raises(ScenarioValidationError, match="rules_only"):
        Scenario.from_dict(raw)


def test_fake_clock_and_uuid_are_deterministic_and_never_sleep():
    clock = FakeClock(start_ms=10)
    clock.sleep(0.25)
    assert clock.monotonic_ms() == 260
    ids = FakeUUIDFactory()
    assert ids() == "00000000-0000-4000-8000-000000000001"
    assert ids() == "00000000-0000-4000-8000-000000000002"


def test_fault_schedule_fires_only_at_exact_occurrence():
    schedule = FaultSchedule(
        [FaultSpec("before_model", 2, "timeout", "boom", retryable=True)]
    )
    assert schedule.check("before_model") is None
    fault = schedule.check("before_model")
    assert fault is not None
    assert (fault.kind, fault.retryable, str(fault)) == ("timeout", True, "boom")
    assert schedule.check("before_model") is None
    assert schedule.unfired == ()


def test_scripted_provider_records_calls_and_exposes_typed_error():
    provider = ScriptedLLM(
        [
            ProviderStep(response={"content": "ok"}),
            ProviderStep(
                error={"kind": "rate_limit", "message": "later", "status": 429}
            ),
        ]
    )
    messages = [{"role": "user", "content": "hello"}]
    assert provider(messages)["content"] == "ok"
    messages[0]["content"] = "mutated after call"
    assert provider.calls[0][0]["content"] == "hello"
    with pytest.raises(ScriptedProviderError) as caught:
        provider(messages)
    assert caught.value.kind == "rate_limit"
    assert caught.value.status == 429


@pytest.mark.parametrize(
    "error,match",
    [
        ({"kind": "x", "message": "x", "status": True}, "status"),
        ({"kind": "x", "message": "x", "headers": {"x": 1}}, "headers"),
        ({"kind": "x", "message": "x", "retryable": "yes"}, "retryable"),
    ],
)
def test_schema_rejects_ill_typed_provider_errors(error, match):
    raw = {
        "schema_version": 1,
        "id": "typed_error",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [{"error": error}],
        "expect": {"terminal_reason": "model_error", "model_attempts": 1},
    }
    with pytest.raises(ScenarioValidationError, match=match):
        Scenario.from_dict(raw)


def _run_for_surface(scenario):
    """The same dispatch the CLI does, for the same reason: a scenario's own
    `surface` decides which runner executes it, so a scenario cannot be
    silently run by the wrong one because of where its file sits."""
    if scenario.surface == "orchestration":
        from harness.orchestration import run_orchestration_scenario

        return run_orchestration_scenario(scenario, offline=True)
    return run_scenario(scenario, offline=True)


@pytest.mark.parametrize("path", _scenario_paths(), ids=lambda path: path.stem)
def test_each_baseline_scenario_passes_and_is_byte_identical(path):
    scenario = load_scenario(path)
    first = _run_for_surface(scenario)
    second = _run_for_surface(scenario)
    assert first.passed, first.errors
    assert second.passed, second.errors
    assert first.normalized == second.normalized
    assert first.trace_sha256 == second.trace_sha256


def test_normalizer_preserves_event_order_instead_of_sorting():
    scenario = load_scenario(_SCENARIOS / "baseline" / "two_response_sequence.json")
    result = run_scenario(scenario)
    forward = normalized_trace_bytes(result.events)
    reversed_bytes = normalized_trace_bytes(reversed(result.events))
    assert forward != reversed_bytes
    normalized = json.loads(forward)
    assert [event["seq"] for event in normalized] == [1, 2, 3, 4, 5, 6]
    parent_positions = {
        event["event_id"]: index for index, event in enumerate(normalized)
    }
    for index, event in enumerate(normalized):
        parent = event["parent_event_id"]
        if parent is not None:
            assert parent_positions[parent] < index


def test_normalizer_uses_explicit_path_and_localhost_port_replacements():
    scenario = load_scenario(_SCENARIOS / "baseline" / "single_response_submitted.json")
    result = run_scenario(scenario)
    events = [event.to_dict() for event in result.events]
    events[0]["payload"]["workspace_file"] = "/tmp/run-a/workspace/data.csv"
    events[0]["payload"]["db"] = "/tmp/run-a/data-dir/openai4s.db"
    events[0]["payload"]["endpoint"] = "http://127.0.0.1:54321/api/ws"
    replacements = {
        "/tmp/run-a/workspace": "<workspace>",
        "/tmp/run-a/data-dir": "<data-dir>",
        "127.0.0.1:54321": "127.0.0.1:<port>",
    }
    first = json.loads(normalized_trace_bytes(events, replacements=replacements))
    payload = first[0]["payload"]
    assert payload["workspace_file"] == "<workspace>/data.csv"
    assert payload["db"] == "<data-dir>/openai4s.db"
    assert payload["endpoint"] == "http://127.0.0.1:<port>/api/ws"
    assert [event["seq"] for event in first] == [1, 2, 3, 4]


def test_cli_runs_pr_offline_tier(capsys):
    assert main(["run", "--tier", "pr", "--offline"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert sum(line.startswith("PASS ") for line in lines) >= 3
    summary = json.loads(
        next(line[8:] for line in lines if line.startswith("SUMMARY "))
    )
    assert summary == {
        "failed": 0,
        "load_errors": 0,
        "offline": True,
        "passed": summary["selected"],
        "schema_version": 1,
        "selected": summary["selected"],
        "tier": "pr",
    }
    assert summary["selected"] >= 3


def test_declared_fault_that_never_fires_fails_the_scenario():
    raw = {
        "schema_version": 1,
        "id": "unfired_fault",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [
            {"response": {"content": "x"}, "terminal_reason": "submitted"}
        ],
        "faults": [
            {
                "point": "before_modle",
                "occurrence": 1,
                "kind": "timeout",
                "message": "typo'd point must not pass vacuously",
            }
        ],
        "expect": {"terminal_reason": "submitted", "model_attempts": 1},
    }
    result = run_scenario(Scenario.from_dict(raw), offline=True)
    assert not result.passed
    assert any("never fired" in error for error in result.errors)


def test_explicit_empty_invariants_is_an_opt_out():
    raw = {
        "schema_version": 1,
        "id": "invariant_opt_out",
        "tags": ["offline", "tier:pr"],
        "surface": "harness",
        "task": "x",
        "provider_script": [{"response": {"content": "x"}}],
        "expect": {
            "terminal_reason": "script_exhausted",
            "model_attempts": 1,
            "invariants": [],
        },
    }
    assert Scenario.from_dict(raw).expect.invariants == ()
    del raw["expect"]["invariants"]
    assert Scenario.from_dict(raw).expect.invariants == (
        "ordered_events",
        "one_run_terminal",
    )


def test_offline_runner_rejects_external_scenario():
    raw = {
        "schema_version": 1,
        "id": "external_case",
        "tags": ["tier:pr", "external"],
        "surface": "harness",
        "task": "must not run offline",
        "provider_script": [
            {
                "response": {"content": "x"},
                "terminal_reason": "submitted",
            }
        ],
        "faults": [],
        "expect": {"terminal_reason": "submitted", "model_attempts": 1},
    }
    with pytest.raises(ValueError, match="not eligible"):
        run_scenario(Scenario.from_dict(raw), offline=True)


def test_retrosynthesis_manifest_and_capabilities_are_offline(tmp_path):
    backend_module = _skill_module("retrosynthesis_planning.external_backends")
    manifest = backend_module.ModelManifest(
        provider="Microsoft Research",
        model="RetroChimera",
        model_version="1.2.0",
        checkpoint_id="synthetic-checkpoint",
        checkpoint_sha256="a" * 64,
        training_dataset="synthetic fixture",
        code_license="MIT",
        checkpoint_license="MIT",
        source_url="https://github.com/microsoft/retrochimera",
    )
    assert manifest.provenance_status == "complete"
    assert len(manifest.fingerprint) == 64
    with pytest.raises(backend_module.BackendProtocolError, match="SHA-256"):
        backend_module.ModelManifest(
            provider="Microsoft Research",
            model="RetroChimera",
            model_version="1.2.0",
            checkpoint_id="bad",
            checkpoint_sha256="not-a-digest",
            training_dataset="synthetic fixture",
            code_license="MIT",
            checkpoint_license="MIT",
        )

    backend = backend_module.SyntheseusBackend(
        model="RetroChimera",
        model_dir=tmp_path / "checkpoint",
        manifest=manifest,
    )
    capabilities = backend.capabilities(request_id="capabilities-test")
    assert capabilities["ok"] is True
    assert capabilities["operation"] == "capabilities"
    assert "RetroChimera" in capabilities["capabilities"]["models"]

    no_checkpoint = backend_module.SyntheseusBackend(model="RetroChimera")
    with pytest.raises(ValueError, match="automatic checkpoint downloads"):
        no_checkpoint.single_step("CCO")


def test_retrosynthesis_worker_runs_behind_fake_optional_modules(monkeypatch):
    backend_module = _skill_module("retrosynthesis_planning.external_backends")
    worker = _skill_module("retrosynthesis_planning.syntheseus_worker")

    class FakeMolecule:
        def __init__(self, smiles):
            self.smiles = smiles

    class FakePrediction:
        reactants_str = "CCO.N"
        reaction_smiles = "CCO.N>>CCON"
        metadata = {
            "probability": 0.7,
            "checkpoint_path": "/private/path/must-not-escape",
        }

    class FakeRetroChimeraModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __call__(self, molecules, num_results):
            assert molecules[0].smiles == "CCON"
            assert num_results == 3
            return [[FakePrediction()]]

    syntheseus = types.ModuleType("syntheseus")
    syntheseus.Molecule = FakeMolecule
    retrochimera = types.ModuleType("retrochimera")
    retrochimera.RetroChimeraModel = FakeRetroChimeraModel
    monkeypatch.setitem(sys.modules, "syntheseus", syntheseus)
    monkeypatch.setitem(sys.modules, "retrochimera", retrochimera)

    manifest = backend_module.ModelManifest(
        provider="Microsoft Research",
        model="RetroChimera",
        model_version="1.2.0",
        checkpoint_id="synthetic-checkpoint",
        checkpoint_sha256="b" * 64,
        training_dataset="synthetic fixture",
        code_license="MIT",
        checkpoint_license="MIT",
    )
    response = worker.handle_request(
        {
            "schema_version": 1,
            "request_id": "fake-worker-test",
            "operation": "single_step",
            "target_smiles": "CCON",
            "model": "RetroChimera",
            "model_dir": "/synthetic/checkpoint",
            "num_results": 3,
            "allow_model_download": False,
            "model_manifest": manifest.to_dict(),
        }
    )
    normalized = backend_module.normalize_external_backend_response(
        response, expected_request_id="fake-worker-test"
    )
    assert normalized["ok"] is True
    assert normalized["predictions"][0]["reactants_smiles"] == "CCO.N"
    assert normalized["predictions"][0]["score"] == 0.7
    assert "checkpoint_path" not in normalized["predictions"][0]["metadata"]
    assert normalized["provenance_status"] == "complete"
    assert "experimental success probabilities" in normalized["scientific_disclaimer"]


def test_retrosynthesis_backend_replays_are_deterministic():
    first = evaluate_backend_replays()
    second = evaluate_backend_replays()
    assert first["accuracy"] == 1.0
    assert first["complete_provenance_rate"] == 1.0
    assert first["prediction_count"] == 2
    assert first["scored_prediction_rate"] == 1.0
    assert [case["normalized_sha256"] for case in first["cases"]] == [
        case["normalized_sha256"] for case in second["cases"]
    ]
