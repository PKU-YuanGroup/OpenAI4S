"""Direct contracts for real remote folding and mutation scoring."""

from __future__ import annotations

import base64
import json
import subprocess
from types import SimpleNamespace

import pytest

from openai4s.host.remote_science import RemoteScienceService


class FakeRegistry:
    def __init__(self, capabilities=None, hosts=None) -> None:
        self.capabilities = capabilities or {}
        self.hosts = hosts or {}
        self.calls: list[str] = []

    def get_host(self, host):
        return self.hosts.get(host)

    def capability_host(self, capability: str):
        self.calls.append(capability)
        return self.capabilities.get(capability, (None, None))


class FakeRunner:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def process(stdout: str, stderr: str = "", returncode: int = 0):
    return SimpleNamespace(
        stdout=stdout.encode(),
        stderr=stderr.encode(),
        returncode=returncode,
    )


def encoded(value: str | dict) -> str:
    raw = json.dumps(value) if isinstance(value, dict) else value
    return base64.b64encode(raw.encode()).decode()


def pdb_text(sequence="ACDE"):
    names = dict(zip("ACDE", ["ALA", "CYS", "ASP", "GLU"]))
    return (
        "".join(
            f"ATOM  {index:5d}  CA  {names[residue]} A{index:4d}    "
            f"{1.0:8.3f}{2.0:8.3f}{3.0:8.3f}  1.00 90.00           C  \n"
            for index, residue in enumerate(sequence, 1)
        )
        + "END\n"
    )


def plddt_text():
    return "chain,resid,resname,plddt\nA,1,ALA,90\nA,2,CYS,90\nA,3,ASP,90\nA,4,GLU,90\n"


def fold_output(
    *,
    provenance: str | None = None,
    manifest: str | None = None,
    pdb_b64=None,
    plddt_b64=None,
    confidence_b64=None,
):
    manifest = manifest or json.dumps(
        {
            "engine": "protenix-test",
            "mean_plddt": 88.5,
            "ptm": 0.71,
            "length": 4,
            "residues_modeled": 4,
            "msa": True,
        }
    )
    provenance_block = (
        "===PROVENANCE_JSON===\n" f"{provenance}\n" "===END_PROVENANCE_JSON===\n"
        if provenance is not None
        else ""
    )
    # Precompute encodings: py3.10 forbids a backslash inside an f-string
    # expression, and these payloads contain newlines.
    pdb_b64 = encoded(pdb_text()) if pdb_b64 is None else pdb_b64
    plddt_b64 = encoded(plddt_text()) if plddt_b64 is None else plddt_b64
    confidence_b64 = (
        encoded({"overall": 0.9}) if confidence_b64 is None else confidence_b64
    )
    return (
        "===FOLD_RESULT_JSON===\n"
        f"{manifest}\n"
        "===END_FOLD_RESULT_JSON===\n"
        "===FOLD_PDB_B64===\n"
        f"{pdb_b64}\n"
        "===FOLD_PLDDT_CSV_B64===\n"
        f"{plddt_b64}\n"
        "===FOLD_CONFIDENCE_JSON_B64===\n"
        f"{confidence_b64}\n"
        f"{provenance_block}"
        "===FOLD_DONE===\n"
    )


def mutation_output(
    *, provenance: str | None = None, summary: str | None = None, csv_b64=None
):
    summary = summary or json.dumps(
        {
            "mean_score": -0.2,
            "top5": [{"mutation": "A1C", "score": 1.2}],
            "length": 3,
        }
    )
    provenance_block = (
        "===PROVENANCE_JSON===\n" f"{provenance}\n" "===END_PROVENANCE_JSON===\n"
        if provenance is not None
        else ""
    )
    csv_b64 = (
        encoded("mutation,score\nA1C,1.2\nC2A,-0.4\nD3A,-1.4\n")
        if csv_b64 is None
        else csv_b64
    )
    return (
        "===MUT_RESULT_JSON===\n"
        f"{summary}\n"
        "===END_MUT_RESULT_JSON===\n"
        "===MUT_CSV_B64===\n"
        f"{csv_b64}\n"
        f"{provenance_block}"
        "===MUT_DONE===\n"
    )


def test_provenance_buffer_parses_best_effort_and_drains_by_identity():
    service = RemoteScienceService()
    del service._remote_provenance
    assert service.pop_remote_provenance() == []
    service.record_remote_provenance(
        "fold", "gpu-a", "protenix", "/jobs/a", ' {"cuda":"12"} '
    )
    service.record_remote_provenance(
        "score_mutations", "gpu-b", None, "/jobs/b", "not-json"
    )
    service.record_remote_provenance("fold", "gpu-c", None, "/jobs/c", None)

    buffered = service.pop_remote_provenance()
    assert buffered == [
        {
            "service": "fold",
            "host": "gpu-a",
            "engine": "protenix",
            "remote_dir": "/jobs/a",
            "env": {"cuda": "12"},
        },
        {
            "service": "score_mutations",
            "host": "gpu-b",
            "engine": None,
            "remote_dir": "/jobs/b",
            "env": None,
        },
        {
            "service": "fold",
            "host": "gpu-c",
            "engine": None,
            "remote_dir": "/jobs/c",
            "env": None,
        },
    ]
    buffered.append({"external": True})
    assert service.pop_remote_provenance() == []

    delegated = []
    service = RemoteScienceService(
        provenance_recorder=lambda *args: delegated.append(args)
    )
    service._record_provenance("fold", "gpu", "engine", "/job", "{}")
    assert delegated == [("fold", "gpu", "engine", "/job", "{}")]
    assert service.pop_remote_provenance() == []


def test_fold_validation_and_no_fabrication_errors_are_exact():
    registry = FakeRegistry()
    service = RemoteScienceService(registry_factory=lambda: registry)

    assert service.fold({}) == {
        "error": "fold: invalid_input: a non-empty protein sequence string is required"
    }
    assert service.fold({"sequence": "123 BZX"}) == {
        "error": "fold: invalid_input: unsupported residue at normalized position 1 (1-based)"
    }
    assert service.fold({"sequence": "A" * 1201}) == {
        "error": "fold: invalid_input: sequence too long (1201 aa); cap is 1200"
    }
    assert registry.calls == []
    assert service.fold({"sequence": "ACDE"}) == {
        "error": "fold: no remote GPU host with a folding service is configured "
        "(Settings → Remote GPU). Refusing to fabricate a structure — configure "
        "a host first."
    }
    assert registry.calls == ["fold"]


def test_fold_preserves_ssh_argv_markers_result_and_environment_provenance():
    registry = FakeRegistry({"fold": ("gpu-a", {"engine": "registered-engine"})})
    runner = FakeRunner(
        process(
            fold_output(provenance='{"cuda":"12.4","weights":"sha256:abc"}'),
            returncode=0,
        )
    )
    environment = {
        "OPENAI4S_FOLD_SCRIPT": "/opt/fold script.sh",
        "OPENAI4S_FOLD_JOBS_DIR": "/jobs base",
    }
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=runner,
        environment=lambda: environment,
        job_suffix=lambda: "abcd1234",
    )

    result = service.fold(
        {
            "sequence": " ac dE ",
            "name": "My Protein!*",
            "gpu": "2",
            "cycle": "3",
            "step": "4",
        }
    )

    assert runner.calls == [
        (
            [
                "ssh",
                "-o",
                "ConnectTimeout=15",
                "-o",
                "BatchMode=yes",
                "gpu-a",
                "mkdir -p '/jobs base/MyProtein_abcd1234' && "
                "'/opt/fold script.sh' --seq ACDE --name MyProtein --out "
                "'/jobs base/MyProtein_abcd1234' --gpu 2 --cycle 3 --step 4",
            ],
            {"capture_output": True, "timeout": 900},
        )
    ]
    assert result == {
        "ok": True,
        "pdb": pdb_text(),
        "plddt_csv": plddt_text(),
        "confidence": {"overall": 0.9},
        "mean_plddt": 88.5,
        "ptm": 0.71,
        "length": 4,
        "residues_modeled": 4,
        "engine": "protenix-test",
        "msa": True,
        "host": "gpu-a",
        "remote_dir": "/jobs base/MyProtein_abcd1234",
    }
    assert service.pop_remote_provenance() == [
        {
            "service": "fold",
            "host": "gpu-a",
            "engine": "protenix-test",
            "remote_dir": "/jobs base/MyProtein_abcd1234",
            "env": {"cuda": "12.4", "weights": "sha256:abc"},
        }
    ]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            subprocess.TimeoutExpired("ssh", 900),
            "fold: timed out after 900s on gpu-a",
        ),
        (OSError("offline"), "fold: transport_error: ssh to gpu-a failed"),
    ],
)
def test_fold_transport_failures_are_soft_errors(failure, expected):
    registry = FakeRegistry({"fold": ("gpu-a", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(failure),
        job_suffix=lambda: "job",
    )
    assert service.fold({"sequence": "ACDE"}) == {"error": expected}
    assert service.pop_remote_provenance() == []


def test_fold_incomplete_and_parse_failures_keep_exact_diagnostics():
    registry = FakeRegistry({"fold": ("gpu-a", {"script": "/fold"})})
    runner = FakeRunner(process("stdout tail", "  remote failed\n", returncode=9))
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=runner,
        job_suffix=lambda: "job",
    )
    assert service.fold({"sequence": "ACDE"}) == {
        "error": "fold: remote_exit: prediction failed on gpu-a (rc=9)"
    }

    runner.result = process(fold_output(manifest="not-json"))
    result = service.fold({"sequence": "ACDE"})
    assert result["error"].startswith("fold: invalid_output: ")
    assert service.pop_remote_provenance() == []


def test_mutation_validation_and_no_fabrication_errors_are_exact():
    registry = FakeRegistry()
    service = RemoteScienceService(registry_factory=lambda: registry)
    assert service.score_mutations({}) == {
        "error": "score_mutations: invalid_input: a non-empty protein sequence string is required"
    }
    assert service.score_mutations({"sequence": "A" * 1025}) == {
        "error": "score_mutations: invalid_input: sequence too long (1025 aa); cap is 1024"
    }
    assert registry.calls == []
    assert service.score_mutations({"sequence": "ACD"}) == {
        "error": "score_mutations: no remote GPU host has a mutation-scoring "
        "service configured, so there is no real predictor available. Do NOT "
        "fabricate scores (no np.random, no BLOSUM-as-ESM, no fake heatmap) — "
        "report that this step cannot be done for real. Provision a service via "
        "Settings → Remote GPU."
    }
    registry.capabilities["score_mutations"] = ("gpu-b", {"engine": "esm"})
    assert service.score_mutations({"sequence": "ACD"}) == {
        "error": "score_mutations: host gpu-b has no script recorded"
    }


def test_mutation_scoring_preserves_ssh_result_and_provenance_contract():
    registry = FakeRegistry(
        {
            "score_mutations": (
                "gpu-b",
                {"script": "/opt/esm score.sh", "engine": "ESM-2"},
            )
        }
    )
    runner = FakeRunner(
        process(
            mutation_output(
                provenance='{"torch":"2.5"}',
                csv_b64=encoded("mutation,score\nA1C,1.2\nC2A,-0.4\n"),
            ),
            returncode=0,
        )
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=runner,
        environment=lambda: {"OPENAI4S_ESM_JOBS_DIR": "/esm jobs"},
        job_suffix=lambda: "ef567890",
    )

    result = service.score_mutations(
        {
            "sequence": " ac d ",
            "name": "Variant Set!",
            "gpu": "3",
            "positions": [1, "2"],
        }
    )

    assert runner.calls == [
        (
            [
                "ssh",
                "-o",
                "ConnectTimeout=15",
                "-o",
                "BatchMode=yes",
                "gpu-b",
                "mkdir -p '/esm jobs/VariantSet_ef567890' && "
                "'/opt/esm score.sh' --seq ACD --name VariantSet --out "
                "'/esm jobs/VariantSet_ef567890' --gpu 3 --positions 1,2",
            ],
            {"capture_output": True, "timeout": 1200},
        )
    ]
    assert result == {
        "ok": True,
        "scores_csv": "mutation,score\nA1C,1.2\nC2A,-0.4\n",
        "summary": {
            "mean_score": None,
            "top5": [{"mutation": "A1C", "score": 1.2}],
            "length": 3,
        },
        "mean_score": None,
        "top5": [{"mutation": "A1C", "score": 1.2}],
        "length": 3,
        "model": "ESM-2",
        "host": "gpu-b",
        "remote_dir": "/esm jobs/VariantSet_ef567890",
    }
    assert service.pop_remote_provenance() == [
        {
            "service": "score_mutations",
            "host": "gpu-b",
            "engine": "ESM-2",
            "remote_dir": "/esm jobs/VariantSet_ef567890",
            "env": {"torch": "2.5"},
        }
    ]


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (
            subprocess.TimeoutExpired("ssh", 1200),
            "score_mutations: timed out after 1200s on gpu-b",
        ),
        (OSError("offline"), "score_mutations: transport_error: ssh to gpu-b failed"),
    ],
)
def test_mutation_transport_failures_are_soft_errors(failure, expected):
    registry = FakeRegistry(
        {"score_mutations": ("gpu-b", {"script": "/score", "engine": "ESM"})}
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(failure),
        job_suffix=lambda: "job",
    )
    assert service.score_mutations({"sequence": "ACD"}) == {"error": expected}
    assert service.pop_remote_provenance() == []


def test_mutation_incomplete_and_parse_failures_keep_exact_diagnostics():
    registry = FakeRegistry(
        {"score_mutations": ("gpu-b", {"script": "/score", "engine": "ESM"})}
    )
    runner = FakeRunner(process("stdout tail", " remote failed ", returncode=11))
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=runner,
        job_suffix=lambda: "job",
    )
    assert service.score_mutations({"sequence": "ACD"}) == {
        "error": "score_mutations: remote_exit: scoring failed on gpu-b (rc=11)"
    }

    runner.result = process(mutation_output(summary="not-json"))
    result = service.score_mutations({"sequence": "ACD"})
    assert result["error"].startswith("score_mutations: invalid_output: ")
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "method,output", [("fold", fold_output), ("score_mutations", mutation_output)]
)
@pytest.mark.parametrize(
    "sequence", ["ACDX", ">header\nACD", "ACU", "ACO", "AßD", "AıD", 123]
)
def test_invalid_sequence_rejected_before_ssh(method, output, sequence):
    registry = FakeRegistry({method: ("gpu-a", {"script": "/predict"})})
    runner = FakeRunner(process(output()))
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    result = getattr(service, method)({"sequence": sequence})
    assert set(result) == {"error"}
    assert runner.calls == []
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "method,output,sequence",
    [("fold", fold_output, "ACDE"), ("score_mutations", mutation_output, "ACD")],
)
def test_nonzero_exit_rejects_complete_markers(method, output, sequence):
    registry = FakeRegistry({method: ("gpu-a", {"script": "/predict"})})
    runner = FakeRunner(
        process(output(), "secret stderr must not be copied", returncode=7)
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    result = getattr(service, method)({"sequence": sequence})
    assert set(result) == {"error"}
    assert "rc=7" in result["error"]
    assert "secret stderr" not in result["error"]
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize("method", ["fold", "score_mutations"])
@pytest.mark.parametrize(
    "value", [True, False, -1, 1.5, float("nan"), float("inf"), "1.2", "NaN", "", None]
)
def test_invalid_gpu_never_launches_ssh(method, value):
    runner = FakeRunner()
    service = RemoteScienceService(run_command=runner)
    result = getattr(service, method)({"sequence": "ACDE", "gpu": value})
    assert set(result) == {"error"}
    assert "invalid_input" in result["error"]
    assert runner.calls == []


@pytest.mark.parametrize("field", ["cycle", "step"])
@pytest.mark.parametrize(
    "value", [True, 0, -1, 2.5, float("nan"), float("inf"), "1.5", "", None]
)
def test_invalid_fold_parameter_never_launches_ssh(field, value):
    runner = FakeRunner()
    service = RemoteScienceService(run_command=runner)
    result = service.fold({"sequence": "ACDE", field: value})
    assert set(result) == {"error"}
    assert runner.calls == []


@pytest.mark.parametrize(
    "positions",
    [
        True,
        2,
        1.5,
        [],
        (),
        "",
        "1,",
        ",1",
        "1,,2",
        [False],
        [1.5],
        [float("nan")],
        [float("inf")],
        [0],
        [-1],
        [5],
        "1;2",
        {1: 2},
    ],
)
def test_invalid_positions_never_launch_ssh(positions):
    runner = FakeRunner()
    service = RemoteScienceService(run_command=runner)
    result = service.score_mutations({"sequence": "ACDE", "positions": positions})
    assert set(result) == {"error"}
    assert runner.calls == []


@pytest.mark.parametrize("positions", [[1, "2"], (1, "2"), " 1 , +2 "])
def test_positions_supported_inputs_preserve_one_based_values(positions):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    runner = FakeRunner(
        process(mutation_output(csv_b64=encoded("mutation,score\nA1C,1.2\nC2A,-0.4\n")))
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    assert service.score_mutations({"sequence": "ACD", "positions": positions})["ok"]
    assert "--positions 1,2" in runner.calls[0][0][-1]


@pytest.mark.parametrize(
    "method,sequence,output",
    [("fold", "ACDE", fold_output), ("score_mutations", "ACD", mutation_output)],
)
@pytest.mark.parametrize(
    "count,gpu,success", [(2, 2, False), (2, 1, True), (0, 9, True), (None, 9, True)]
)
def test_only_known_gpu_count_limits_index(
    method, sequence, output, count, gpu, success
):
    registry = FakeRegistry(
        {method: ("gpu", {"script": "/predict"})}, hosts={"gpu": {"gpu_count": count}}
    )
    runner = FakeRunner(process(output()))
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    result = getattr(service, method)({"sequence": sequence, "gpu": gpu})
    assert bool(result.get("ok")) is success
    assert len(runner.calls) == int(success)


@pytest.mark.parametrize(
    "manifest",
    [
        "[]",
        "null",
        "{}",
        '{"length":4,"residues_modeled":3}',
        '{"length":3,"residues_modeled":4}',
        '{"length":4,"residues_modeled":4,"mean_plddt":NaN}',
        '{"length":4,"residues_modeled":4,"ptm":1e999}',
        '{"length":4,"length":4,"residues_modeled":4}',
        '{"length":4,"residues_modeled":4,"msa":"false"}',
    ],
)
def test_fold_rejects_invalid_manifest_without_provenance(manifest):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(manifest=manifest))),
    )
    result = service.fold({"sequence": "ACDE"})
    assert set(result) == {"error"}
    assert "invalid_output" in result["error"]
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize("field", ["pdb_b64", "plddt_b64", "confidence_b64"])
@pytest.mark.parametrize("payload", ["", "!!!", "YQ", "YQ==!", "/w=="])
def test_fold_rejects_empty_invalid_truncated_base64_or_utf8(field, payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(**{field: payload}))),
    )
    assert set(service.fold({"sequence": "ACDE"})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "ATOM\n",
        "REMARK ATOM is not coordinates\n",
        pdb_text("ACD"),
        pdb_text().replace("ALA", "CYS"),
        pdb_text().replace("   1.000", "     NaN"),
        pdb_text().replace("   2.000", "     inf"),
        pdb_text().replace("   3.000", "        "),
        pdb_text().replace("  CA ", "  CB "),
        pdb_text().replace(" A   4", " B   4"),
        "MODEL        1\n" + pdb_text() + "MODEL        2\n" + pdb_text(),
    ],
)
def test_fold_checks_real_pdb_columns_sequence_and_complete_model(payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(pdb_b64=encoded(payload)))),
    )
    assert set(service.fold({"sequence": "ACDE"})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "payload", ["[]", "{}", '{"ptm":NaN}', '{"nested":{"number":1e999}}']
)
def test_confidence_must_be_nonempty_finite_json_object(payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(confidence_b64=encoded(payload)))),
    )
    assert set(service.fold({"sequence": "ACDE"})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "payload",
    [
        "chain,resid,resname,plddt\n",
        plddt_text().replace("A,4,GLU,90\n", ""),
        plddt_text().replace("A,4,GLU,90", "A,3,GLU,90"),
        plddt_text().replace("90", "NaN"),
    ],
)
def test_plddt_rows_must_match_modeled_residues(payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(plddt_b64=encoded(payload)))),
    )
    assert set(service.fold({"sequence": "ACDE"})) == {"error"}


@pytest.mark.parametrize(
    "payload",
    [
        "mutation,score\nA1C,1.2\n",
        "position,wt,mut,mutation,esm_score\r\n1,A,C,A1C,1.2\r\n",
    ],
)
def test_both_mutation_csv_contracts_return_original_bytes(payload):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(mutation_output(csv_b64=encoded(payload)))),
    )
    result = service.score_mutations({"sequence": "ACD", "positions": [1]})
    assert result["ok"] is True
    assert result["scores_csv"] == payload
    assert result["model"] is None
    assert result["host"] == "gpu"
    assert service.pop_remote_provenance()[0]["engine"] is None


@pytest.mark.parametrize(
    "payload",
    [
        "mutation,score\n",
        "mutation,score\nA1C,NaN\n",
        "mutation,score\nA1C,inf\n",
        "mutation,score\nA1C,1e999\n",
        "mutation,score\nC1A,1\n",
        "mutation,score\nA4C,1\n",
        "mutation,score\nA0C,1\n",
        "mutation,score\nA1X,1\n",
        "mutation,score\nA1C\n",
        "mutation,score\nA1C,1,extra\n",
        "mutation,score\nA1C,1\nA1C,2\n",
        "mutation,score,score\nA1C,1,2\n",
        "position,wt,mut,mutation,esm_score\n2,A,C,A1C,1\n",
        "position,wt,mut,mutation,esm_score\n1,C,C,A1C,1\n",
        "position,wt,mut,mutation,esm_score\n1,A,D,A1C,1\n",
        "position,wt,mut,mutation,esm_score,score\n1,A,C,A1C,1,2\n",
        "mutation,esm_score\nA1C,1\n",
        'mutation,score\nA1C,"1\n',
    ],
)
def test_mutation_rejects_nonfinite_conflicting_or_malformed_csv(payload):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(mutation_output(csv_b64=encoded(payload)))),
    )
    assert set(service.score_mutations({"sequence": "ACD"})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "summary",
    [
        "[]",
        "null",
        "{}",
        '{"length":4}',
        '{"length":3,"mean_score":NaN}',
        '{"length":3,"top5":[{"score":1e999}]}',
    ],
)
def test_mutation_summary_requires_object_matching_sequence_and_finite_values(summary):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(mutation_output(summary=summary))),
    )
    assert set(service.score_mutations({"sequence": "ACD"})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize("payload", ["", "!", "YQ", "/w=="])
def test_mutation_requires_nonempty_strict_base64_and_utf8(payload):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(mutation_output(csv_b64=payload))),
    )
    assert set(service.score_mutations({"sequence": "ACD"})) == {"error"}
    assert service.pop_remote_provenance() == []


def test_mutation_output_must_be_within_requested_positions():
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(mutation_output())),
    )
    assert set(service.score_mutations({"sequence": "ACD", "positions": [2]})) == {
        "error"
    }
    assert service.pop_remote_provenance() == []


def test_fold_optional_metadata_is_unknown_and_base64_whitespace_is_supported():
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    folded = encoded(pdb_text())
    folded = "\n \t".join(
        folded[index : index + 17] for index in range(0, len(folded), 17)
    )
    output = fold_output(manifest='{"length":4,"residues_modeled":4}', pdb_b64=folded)
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )
    result = service.fold({"sequence": "ACDE"})
    assert result["ok"] is True
    assert result["pdb"] == pdb_text()
    assert result["engine"] is None
    assert result["msa"] is None
    assert result["host"] == "gpu"


@pytest.mark.parametrize(
    "method,output,sequence",
    [("fold", fold_output, "ACDE"), ("score_mutations", mutation_output, "ACD")],
)
def test_missing_protocol_or_invalid_utf8_never_copies_output(method, output, sequence):
    registry = FakeRegistry({method: ("gpu", {"script": "/predict"})})
    runner = FakeRunner(process("private payload and sequence must not be copied"))
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    result = getattr(service, method)({"sequence": sequence})
    assert set(result) == {"error"}
    assert "private payload" not in result["error"]
    runner.result = process(output())
    runner.result.stdout += b"\xff"
    assert set(getattr(service, method)({"sequence": sequence})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.stubbed_backend
def test_real_dispatcher_and_kernel_keep_single_key_error_to_runtime_error(monkeypatch):
    from openai4s.config import get_config
    from openai4s.host_dispatch import HostDispatcher
    from openai4s.kernel import Kernel

    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "allow")
    dispatcher = HostDispatcher(get_config())
    runner = FakeRunner()
    dispatcher._remote_science_service = RemoteScienceService(run_command=runner)
    for method in ("fold", "score_mutations"):
        result = dispatcher(method, [{"sequence": "ACDX"}])
        assert set(result) == {"error"}
        assert "invalid_input" in result["error"]
    with Kernel(dispatcher=dispatcher) as kernel:
        result = kernel.execute(
            "for method in (host.fold, host.score_mutations):\n"
            "    try:\n"
            "        method('ACDX')\n"
            "    except RuntimeError as error:\n"
            "        print('caught:', 'invalid_input' in str(error))\n"
        )
        assert result["error"] is None
        assert result["stdout"].strip() == "caught: True\ncaught: True"
        assert kernel.is_alive()
    assert runner.calls == []
    assert dispatcher.pop_remote_provenance() == []
    assert dispatcher.last_output is None


@pytest.mark.parametrize(
    "payload", ["residue,plddt\n1,90\n2,90\n3,90\n4,90\n", plddt_text()]
)
def test_fold_both_plddt_formats_keep_original_text(payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(plddt_b64=encoded(payload)))),
    )
    result = service.fold({"sequence": "ACDE"})
    assert result["ok"] is True
    assert result["plddt_csv"] == payload


@pytest.mark.parametrize(
    "payload",
    [
        "residue,plddt\n1,90\n",
        "residue,plddt\n0,90\n",
        "residue,plddt\n5,90\n",
        "residue,plddt\n1,90\n1,90\n",
        "residue,plddt\n1,NaN\n",
        "chain,resid,resname,plddt,residue\nA,1,ALA,90,1\n",
    ],
)
def test_fold_legacy_plddt_rejects_incomplete_or_ambiguous_rows(payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(plddt_b64=encoded(payload)))),
    )
    assert set(service.fold({"sequence": "ACDE"})) == {"error"}
    assert service.pop_remote_provenance() == []


def test_sequence_error_uses_normalized_one_based_position_without_echo():
    service = RemoteScienceService()
    result = service.fold({"sequence": " a c  x d "})
    assert "position 3 (1-based)" in result["error"]
    assert "ACXD" not in result["error"]


@pytest.mark.parametrize(
    "method,output,sequence,marker",
    [
        ("fold", fold_output, "ACDE", "===FOLD_DONE==="),
        ("score_mutations", mutation_output, "ACD", "===MUT_DONE==="),
    ],
)
def test_truncated_or_duplicate_completion_marker_rejects_even_with_provenance(
    method, output, sequence, marker
):
    registry = FakeRegistry({method: ("gpu", {"script": "/predict"})})
    runner = FakeRunner(
        process(output(provenance='{"torch":"2.5"}').replace(marker, ""))
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    assert set(getattr(service, method)({"sequence": sequence})) == {"error"}
    runner.result = process(output(provenance='{"torch":"2.5"}') + marker)
    assert set(getattr(service, method)({"sequence": sequence})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "method,output,sequence,field,value",
    [
        (
            "fold",
            fold_output,
            "ACDE",
            "manifest",
            '{"length":4,"residues_modeled":4,"engine":false}',
        ),
        (
            "score_mutations",
            mutation_output,
            "ACD",
            "summary",
            '{"length":3,"model":0}',
        ),
    ],
)
def test_malformed_metadata_does_not_fall_back_to_registered_engine(
    method, output, sequence, field, value
):
    registry = FakeRegistry(
        {method: ("gpu", {"script": "/predict", "engine": "registered"})}
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(output(**{field: value}))),
    )
    assert set(getattr(service, method)({"sequence": sequence})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "top5",
    [
        False,
        "A1C",
        {"mutation": "A1C", "score": 1.2},
        [None],
        [{"mutation": "W99Y", "score": 1.2}],
        [{"mutation": "A1C", "score": "NaN"}],
        [{"mutation": "A1C", "score": 500}],
        [{"mutation": "A1C", "score": 1.2, "esm_score": 2}],
        [{"mutation": "A1C", "score": 1.2, "position": 2, "wt": "A", "mut": "C"}],
        [{"mutation": "A1C", "score": 1.2}, {"mutation": "A1C", "score": 1.2}],
    ],
)
def test_mutation_summary_entries_must_match_validated_csv(top5):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    output = mutation_output(
        summary=json.dumps({"length": 3, "top5": top5}),
        csv_b64=encoded("mutation,score\nA1C,1.2\n"),
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )
    result = service.score_mutations({"sequence": "ACD", "positions": [1]})
    assert set(result) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize("positions", [[1, 2], None])
def test_mutation_rejects_missing_requested_position_without_guessing_candidate_count(
    positions,
):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    output = mutation_output(csv_b64=encoded("mutation,score\nA1C,1.2\n"))
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )
    result = service.score_mutations({"sequence": "ACD", "positions": positions})
    assert set(result) == {"error"}
    assert service.pop_remote_provenance() == []


def test_mutation_does_not_publish_mean_without_a_declared_statistic_contract():
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    output = mutation_output(
        summary=json.dumps({"length": 3, "mean_score": 500}),
        csv_b64=encoded("mutation,score\nA1C,1.2\n"),
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )
    result = service.score_mutations({"sequence": "ACD", "positions": [1]})
    assert result["ok"] is True
    assert result["mean_score"] is None
    assert result["summary"]["mean_score"] is None


@pytest.mark.parametrize(
    "payload",
    [
        "END\n" + pdb_text(),
        "ENDMDL\n" + pdb_text(),
        "ENDXXX\n" + pdb_text(),
        pdb_text().replace("END\n", "") + "MODEL        1\nENDMDL\nEND\n",
        "MODEL        1\n" + pdb_text(),
        "MODEL        1\nENDMDL\n" + pdb_text(),
    ],
)
def test_fold_rejects_atoms_outside_complete_pdb_model_boundaries(payload):
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(pdb_b64=encoded(payload)))),
    )
    assert set(service.fold({"sequence": "ACDE"})) == {"error"}
    assert service.pop_remote_provenance() == []


@pytest.mark.parametrize(
    "row",
    [
        {"mutation": "A1C", "score": 1.2},
        {"mutation": "A1C", "esm_score": 1.2, "position": 1, "wt": "A", "mut": "C"},
    ],
)
def test_valid_mutation_summary_keeps_verified_entries_without_fixed_candidates_per_position(
    row,
):
    registry = FakeRegistry({"score_mutations": ("gpu", {"script": "/score"})})
    output = mutation_output(summary=json.dumps({"length": 3, "top5": [row]}))
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )
    result = service.score_mutations({"sequence": "ACD"})
    assert result["ok"] is True
    assert result["top5"] == [row]
    assert result["summary"]["top5"] == [row]
    assert len(service.pop_remote_provenance()) == 1


def test_fold_accepts_a_complete_single_model_with_explicit_boundaries():
    payload = "MODEL        1\n" + pdb_text().replace("END\n", "ENDMDL\nEND\n")
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    service = RemoteScienceService(
        registry_factory=lambda: registry,
        run_command=FakeRunner(process(fold_output(pdb_b64=encoded(payload)))),
    )
    result = service.fold({"sequence": "ACDE"})
    assert result["ok"] is True
    assert result["pdb"] == payload
    assert len(service.pop_remote_provenance()) == 1


def _fold_service(output: str) -> RemoteScienceService:
    registry = FakeRegistry({"fold": ("gpu", {"script": "/fold"})})
    return RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )


def _score_service(output: str) -> RemoteScienceService:
    registry = FakeRegistry(
        {"score_mutations": ("gpu-b", {"script": "/score", "engine": "ESM"})}
    )
    return RemoteScienceService(
        registry_factory=lambda: registry, run_command=FakeRunner(process(output))
    )


_MUTATION_CSV = "mutation,score\nA1C,1.2\nC2A,-0.4\nD3A,-1.4\n"


@pytest.mark.parametrize(
    "decorate",
    [
        pytest.param(lambda text: text + "\n", id="trailing-newline"),
        pytest.param(lambda text: text + "\r\n", id="trailing-crlf"),
        pytest.param(lambda text: "﻿" + text, id="utf8-bom"),
    ],
)
def test_csv_trailing_blank_line_or_bom_is_not_a_malformed_result(decorate):
    """`print(df.to_csv())` ends with a blank line; a BOM is a signature, not
    content. Neither is a short row or a missing column."""
    fold = _fold_service(fold_output(plddt_b64=encoded(decorate(plddt_text()))))
    assert fold.fold({"sequence": "ACDE"})["ok"] is True
    score = _score_service(mutation_output(csv_b64=encoded(decorate(_MUTATION_CSV))))
    assert score.score_mutations({"sequence": "ACD"})["ok"] is True


def test_blank_line_between_csv_rows_is_still_malformed():
    torn = plddt_text().replace("A,2,CYS,90\n", "A,2,CYS,90\n\n")
    result = _fold_service(fold_output(plddt_b64=encoded(torn))).fold(
        {"sequence": "ACDE"}
    )
    assert result["error"] == "fold: invalid_output: malformed pLDDT CSV row"


def test_top5_score_tolerates_two_serializations_of_one_number():
    """A float32 tensor printed by pandas and json.dumps(float(x)) differ in
    the 8th digit; a 4-dp summary differs in the 5th. Neither is fabrication."""
    csv_b64 = encoded("mutation,score\nA1C,-1.2345678\nC2A,-0.4\nD3A,-1.4\n")
    for score in (-1.2345678, -1.2345677614212036, -1.2346):
        summary = json.dumps(
            {"top5": [{"mutation": "A1C", "score": score}], "length": 3}
        )
        result = _score_service(
            mutation_output(summary=summary, csv_b64=csv_b64)
        ).score_mutations({"sequence": "ACD"})
        assert result["ok"] is True, score
    wrong = json.dumps({"top5": [{"mutation": "A1C", "score": -1.2}], "length": 3})
    rejected = _score_service(
        mutation_output(summary=wrong, csv_b64=csv_b64)
    ).score_mutations({"sequence": "ACD"})
    assert rejected["error"] == (
        "score_mutations: invalid_output: top5 score conflicts with validated CSV"
    )


@pytest.mark.parametrize("value", ["1_0.5", "１.５", "٣", "0x10", "nan", "inf", ""])
def test_numeric_text_fields_must_be_ascii_decimals(value):
    """float() alone accepts separators and Unicode digits that no PDB reader
    or the workbench viewer parse the same way."""
    from openai4s.host.remote_science import _finite

    with pytest.raises(ValueError):
        _finite(value, "PDB coordinate")


def test_numeric_text_fields_accept_ascii_decimals():
    from openai4s.host.remote_science import _finite

    assert _finite(" 1.000", "x") == 1.0
    assert _finite("-2.5e1", "x") == -25.0
    assert _finite(".5", "x") == 0.5
    assert _finite(3, "x") == 3.0


def test_pdb_coordinate_with_a_digit_separator_is_rejected_not_passed_through():
    payload = pdb_text().replace(f"{1.0:8.3f}", " 1_0.500", 1)
    result = _fold_service(fold_output(pdb_b64=encoded(payload))).fold(
        {"sequence": "ACDE"}
    )
    assert result["error"] == "fold: invalid_output: invalid numeric PDB coordinate"


def test_non_finite_confidence_is_diagnosed_as_non_finite_not_invalid_json():
    result = _fold_service(
        fold_output(confidence_b64=encoded('{"plddt": 80.1, "iptm": NaN}'))
    ).fold({"sequence": "ACDE"})
    assert result["error"] == "fold: invalid_output: non-finite number in confidence"


def test_every_empty_positions_spelling_shares_one_message():
    runner = FakeRunner(process(mutation_output()))
    registry = FakeRegistry(
        {"score_mutations": ("gpu-b", {"script": "/score", "engine": "ESM"})}
    )
    service = RemoteScienceService(
        registry_factory=lambda: registry, run_command=runner
    )
    messages = {
        service.score_mutations({"sequence": "ACD", "positions": empty})["error"]
        for empty in ([], (), "", "   ")
    }
    assert len(messages) == 1
    assert "pass None to score every position" in messages.pop()
    assert runner.calls == [], "rejected before any SSH launch"
