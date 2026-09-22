"""Offline chemistry, evidence, and executable recipe contracts."""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader


@pytest.fixture(scope="module", autouse=True)
def _skills_on_path():
    path = str(get_config().skills_dir)
    sys.path.insert(0, path)
    yield
    if path in sys.path:
        sys.path.remove(path)
    sys.modules.pop("target_druggability_screening.kernel", None)
    sys.modules.pop("target_druggability_screening", None)


def _kernel():
    return importlib.import_module("target_druggability_screening.kernel")


def _descriptors(**changes):
    return dict(molecular_weight=180.16, logp=1.31, hbd=1, hba=3, **changes)


def _ligand(identifier="123", **attrs):
    return {
        "id": identifier,
        "url": f"https://www.bindingdb.org/bind/chemsearch/marvin/MolStructure.jsp?monomerid={identifier}",
        "attributes": dict(
            {
                "monomer_id": identifier,
                "smiles": "CCO",
                "affinity_type": "Ki",
                "affinity_value": 5.0,
                "affinity_raw": "5.0",
            },
            **attrs,
        ),
    }


def test_target_druggability_skill_is_discovered():
    skill = SkillLoader().discover()["target_druggability_screening"]
    assert skill.name == "target_druggability_screening"
    assert skill.has_kernel is True
    assert "target_druggability_screening.kernel" in (skill.import_hint or "")
    assert skill.origin == "openai4s"
    assert skill.read_only is True


@pytest.mark.parametrize(
    "smiles,mw,hbd,hba",
    [
        ("CCO", 46.069, 1, 1),
        ("CC(=O)Oc1ccccc1C(=O)O", 180.159, 1, 3),
        ("CC(=O)N", 59.068, 1, 1),
    ],
)
def test_descriptors_use_molecular_graph(smiles, mw, hbd, hba):
    pytest.importorskip("rdkit")
    desc = _kernel().calculate_smiles_descriptors(smiles)
    assert desc["status"] == "ok"
    assert desc["molecular_weight"] == pytest.approx(mw, abs=0.001)
    assert desc["hbd"] == hbd
    assert desc["hba"] == hba
    assert desc["method"] == "RDKit"
    assert desc["method_version"]
    assert desc["logp"] is not None


def test_long_alkane_fails_mass_and_logp():
    pytest.importorskip("rdkit")
    result = _kernel().evaluate_lipinski("C" * 36)
    assert result["molecular_weight"] > 500
    assert result["logp"] > 5
    assert result["violations_count"] == 2
    assert result["pass_rule_of_5"] is False


@pytest.mark.parametrize("smiles", ["", " ", "not-a-smiles", "C1CC", "[H][H]"])
def test_absent_or_invalid_structures_never_pass(smiles):
    result = _kernel().evaluate_lipinski(smiles)
    assert result["pass_rule_of_5"] is None
    assert result["violations_count"] is None
    assert result["status"] == "unknown"


def test_optional_rdkit_absence_does_not_invent_descriptors(monkeypatch):
    monkeypatch.setitem(sys.modules, "rdkit", None)
    desc = _kernel().calculate_smiles_descriptors("CCO")
    assert desc["status"] == "unavailable"
    assert desc["molecular_weight"] is None
    result = _kernel().evaluate_lipinski(desc)
    assert result["pass_rule_of_5"] is None
    assert _kernel().rank_candidates([_ligand()])[0]["score"] is None


def test_lipinski_uses_all_four_criteria():
    result = _kernel().evaluate_lipinski(_descriptors())
    assert result["pass_rule_of_5"] is True
    assert result["violations_count"] == 0
    result = _kernel().evaluate_lipinski(
        {"molecular_weight": 850, "logp": 8, "hbd": 8, "hba": 15}
    )
    assert result["violations_count"] == 4
    assert result["pass_rule_of_5"] is False
    result = _kernel().evaluate_lipinski(
        {**_descriptors(), "logp": 6, "rotatable_bonds": 40}
    )
    assert result["violations_count"] == 1
    assert result["pass_rule_of_5"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("logp", None),
        ("logp", float("nan")),
        ("hbd", -1),
        ("hba", True),
        ("hbd", 1.5),
        ("molecular_weight", 0),
        ("molecular_weight", float("inf")),
    ],
)
def test_incomplete_or_invalid_descriptors_are_unknown(field, value):
    result = _kernel().evaluate_lipinski({**_descriptors(), field: value})
    assert result["pass_rule_of_5"] is None
    assert field in result["missing_descriptors"]


def test_score_lead_potency_and_rules():
    assert _kernel().score_lead(5.0, "Ki", True, 0) == 100.0
    assert _kernel().score_lead(5000.0, "IC50", False, 2) == 20.0
    assert _kernel().score_lead(5.0, "Ki", None, None) is None
    assert _kernel().score_lead(5.0, None, True, 0) is None


@pytest.mark.parametrize("value", [None, 0, -1, True, float("nan"), float("inf"), "5"])
def test_unusable_affinity_cannot_receive_a_score(value):
    assert _kernel().score_lead(value, "Ki", True, 0) is None


@pytest.fixture
def known_descriptors(monkeypatch):
    monkeypatch.setattr(
        _kernel(), "calculate_smiles_descriptors", lambda _: _descriptors()
    )


def test_rank_candidates_preserves_assay_evidence(known_descriptors):
    ligands = [
        _ligand("weak", affinity_value=5000.0, affinity_raw="5000"),
        _ligand(
            "potent",
            affinity_value=2.5,
            affinity_raw="2.5",
            pmid="12345678",
            doi="10.1234/example",
        ),
        _ligand("potent", affinity_value=25.0, affinity_raw="25", pmid="87654321"),
    ]
    ranked = _kernel().rank_candidates(ligands, top_k=5)
    assert [row["affinity_value"] for row in ranked] == [2.5, 25.0, 5000.0]
    assert ranked[0]["pmid"] == "12345678"
    assert ranked[0]["doi"] == "10.1234/example"
    assert len(ranked) == 3  # Keep the second assay and its citation.
    assert _kernel().rank_candidates(ligands, top_k=0) == []
    with pytest.raises(ValueError, match="top_k"):
        _kernel().rank_candidates(ligands, top_k=-1)


@pytest.mark.parametrize(
    "raw,value",
    [
        (">2", 2.0),
        ("<2", 2.0),
        ("~2", 2.0),
        ("2", 3.0),
        (None, None),
        ("NaN", float("nan")),
    ],
)
def test_qualified_or_missing_affinity_stays_unscored(known_descriptors, raw, value):
    ranked = _kernel().rank_candidates(
        [
            _ligand("unknown", affinity_raw=raw, affinity_value=value),
            _ligand("measured", affinity_raw="20000", affinity_value=20000),
        ]
    )
    assert ranked[0]["id"] == "measured"
    assert ranked[1]["score"] is None
    assert ranked[1]["affinity_raw"] == raw
    json.dumps(ranked, allow_nan=False)


def test_mixed_endpoints_require_explicit_selection(known_descriptors):
    ligands = [_ligand(), _ligand("456", affinity_type="IC50")]
    with pytest.raises(ValueError, match="Mixed affinity"):
        _kernel().rank_candidates(ligands)
    assert len(_kernel().rank_candidates(ligands, affinity_type="ki")) == 1


def test_sparse_records_do_not_crash_or_default_to_ki():
    records = [None, {}, {"id": "no-attrs"}, {"id": "null-attrs", "attributes": None}]
    ranked = _kernel().rank_candidates(records)
    assert len(ranked) == 2
    assert all(row["score"] is None and row["affinity_type"] == "" for row in ranked)


def test_dossier_reports_empty_unknown_and_qualified_evidence():
    empty = _kernel().format_dossier("CDK4", [], [])
    assert "strong therapeutic relevance" not in empty
    assert "No interaction records" in empty
    assert "No ligand assay records" in empty
    assert "do not establish" in empty
    assert "provenance was not supplied" in empty
    leads = _kernel().rank_candidates(
        [
            _ligand(
                affinity_raw=">2", affinity_value=None, smiles="", doi="10.1234/example"
            )
        ]
    )
    dossier = _kernel().format_dossier("CDK4", [], leads)
    assert "Unknown:" in dossier
    assert "Unscored" in dossier
    assert "&gt;2 nM" in dossier
    assert "https://doi.org/10.1234/example" in dossier
    assert "not necessarily distinct compounds" in dossier


def test_dossier_preserves_zero_confidence_and_escapes_source_text():
    ppi = {
        "id": "edge",
        "url": "javascript:alert(1)",
        "attributes": {
            "partner_protein": "A|B\n<script>",
            "score": 0,
            "experimental_score": 0,
            "network_type": "functional",
        },
    }
    dossier = _kernel().format_dossier("CDK4", [ppi], [])
    assert "A&#124;B &lt;script&gt;" in dossier
    assert "| 0.000 | 0.000 |" in dossier
    assert "javascript:" not in dossier


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("empty", [False, True])
def test_documented_recipe_runs_through_real_host_and_artifact_store(
    tmp_path, monkeypatch, empty
):
    from test_science_connectors import RESPONSES

    from openai4s.host.data import HostDataService
    from openai4s.host.science import ScienceConnectorService
    from openai4s.sdk.host import _Host, decode_args
    from openai4s.store import get_store

    cfg = get_config()
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="screening", status="running")
    monkeypatch.chdir(tmp_path)

    def resolve(path, *, must_exist=False):
        resolved = (tmp_path / path).resolve()
        resolved.relative_to(tmp_path)
        if must_exist and not resolved.is_file():
            raise FileNotFoundError(resolved)
        return resolved

    data = HostDataService(
        store=store, config=cfg, frame_id=frame_id, resolve_path=resolve
    )
    requests = []

    def fetch(url, *_args):
        requests.append(url)
        if "string-db.org" in url:
            return json.dumps([] if empty else RESPONSES["string-db.org"])
        if empty:
            return ""  # The real BindingDB empty-result transport contract.
        return json.dumps(RESPONSES["www.bindingdb.org"])

    science = ScienceConnectorService(fetch)
    saved = []

    def host_call(method, args):
        spec = decode_args(args)[0]
        if method == "science_search":
            return science.search(**spec)
        assert method == "save_artifact"
        result = data.save_artifact(spec)
        saved.append(result)
        return result

    doc = SkillLoader().discover()["target_druggability_screening"].root / "SKILL.md"
    code = re.search(r"```python\n(.*?)```", doc.read_text(), re.S)[1]
    namespace = {"host": _Host(host_call)}
    exec(compile(code, str(doc), "exec"), namespace)
    assert len(saved) == 3
    assert "identifiers=P11802" in requests[0] and "species=9606" in requests[0]
    assert "uniprot=P11802" in requests[1] and "affinity_type" not in requests[1]
    for artifact in saved[:2]:
        envelope = json.loads(Path(artifact["path"]).read_text())
        assert envelope["provenance"]["response_sha256"]
        meta = store.version_meta(artifact["version_id"])
        assert (
            json.loads(meta["source"])["response_sha256"]
            == envelope["provenance"]["response_sha256"]
        )
    dossier = Path(saved[-1]["path"]).read_text()
    assert "strong therapeutic relevance" not in dossier
    assert "response SHA-256:" in dossier
    assert namespace["artifact"]["version_id"] == saved[-1]["version_id"]
    assert namespace["source_versions"] == [item["version_id"] for item in saved[:2]]
    lineage = store.lineage_inputs(saved[-1]["version_id"])
    assert {item["version_id"] for item in lineage} == set(namespace["source_versions"])
