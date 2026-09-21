import sys
from pathlib import Path

import pytest

from openai4s.config import get_config
from openai4s.skills_loader import SkillLoader

skills_dir = str(get_config().skills_dir)
if skills_dir not in sys.path:
    sys.path.insert(0, skills_dir)

from target_druggability_screening.kernel import (
    calculate_smiles_descriptors,
    evaluate_lipinski,
    format_dossier,
    rank_candidates,
    score_lead,
)


def test_target_druggability_skill_is_discovered():
    loader = SkillLoader()
    skills = loader.discover()

    assert "target_druggability_screening" in skills
    skill = skills["target_druggability_screening"]
    assert skill.name == "target_druggability_screening"
    assert skill.has_kernel is True
    assert "target_druggability_screening.kernel" in (skill.import_hint or "")
    assert skill.origin == "openai4s"
    assert skill.read_only is True
    assert "druggability" in skill.description.lower()


def test_calculate_smiles_descriptors():
    # Aspirin: C9H8O4, MW ~ 180.16
    aspirin = "CC(=O)Oc1ccccc1C(=O)O"
    desc = calculate_smiles_descriptors(aspirin)

    assert 170.0 < desc["molecular_weight"] < 195.0
    assert desc["heavy_atom_count"] == 13
    assert desc["hba"] >= 3
    assert desc["hbd"] >= 1

    # Empty SMILES defense
    empty_desc = calculate_smiles_descriptors("")
    assert empty_desc["molecular_weight"] == 0.0
    assert empty_desc["heavy_atom_count"] == 0


def test_evaluate_lipinski_rule_of_5():
    # Drug-like molecule (Aspirin)
    aspirin = "CC(=O)Oc1ccccc1C(=O)O"
    res = evaluate_lipinski(aspirin)
    assert res["pass_rule_of_5"] is True
    assert res["violations_count"] == 0

    # Non-drug-like (large peptide/polymer with artificial MW and excessive donors/acceptors)
    violator_desc = {
        "molecular_weight": 850.0,
        "hbd": 8,
        "hba": 15,
        "rotatable_bonds": 12,
    }
    viol_res = evaluate_lipinski(violator_desc)
    assert viol_res["pass_rule_of_5"] is False
    assert viol_res["violations_count"] == 3
    assert any("MW > 500" in v for v in viol_res["violations"])
    assert any("HBD > 5" in v for v in viol_res["violations"])
    assert any("HBA > 10" in v for v in viol_res["violations"])


def test_score_lead_potency_and_rules():
    # Sub-10 nM with clean Lipinski
    high_score = score_lead(5.0, "Ki", lipinski_pass=True, violations_count=0)
    assert high_score == 100.0  # 60 + 40

    # Micromolar binder with Lipinski violation
    low_score = score_lead(5000.0, "IC50", lipinski_pass=False, violations_count=2)
    assert low_score < 30.0


def test_rank_candidates_prioritization():
    mock_ligands = [
        {
            "id": "ligand_weak",
            "url": "https://example.com/weak",
            "attributes": {
                "monomer_id": "weak_1",
                "target_name": "Kinase",
                "smiles": "CC(=O)Oc1ccccc1C(=O)O",
                "affinity_type": "IC50",
                "affinity_raw": "5000",
                "affinity_value": 5000.0,
            },
        },
        {
            "id": "ligand_potent",
            "url": "https://example.com/potent",
            "attributes": {
                "monomer_id": "potent_1",
                "target_name": "Kinase",
                "smiles": "CNc1nc(C)c(s1)-c1ccnc(Nc2cccc(c2)S(N)(=O)=O)n1",
                "affinity_type": "Ki",
                "affinity_raw": "2.5",
                "affinity_value": 2.5,
                "pmid": "12345678",
            },
        },
    ]

    ranked = rank_candidates(mock_ligands, top_k=5)
    assert len(ranked) == 2
    # The potent ligand should rank first
    assert ranked[0]["id"] == "potent_1"
    assert ranked[0]["affinity_value"] == 2.5
    assert ranked[0]["score"] > ranked[1]["score"]
    assert ranked[0]["pmid"] == "12345678"


def test_format_dossier_markdown_assembly():
    ppi_mock = [
        {
            "id": "TP53--MDM2",
            "attributes": {
                "partner_protein": "MDM2",
                "partner_string_id": "9606.ENSP00000266970",
                "score": 0.994,
                "experimental_score": 0.991,
                "textmining_score": 0.985,
            },
        }
    ]

    leads_mock = [
        {
            "id": "BDBM81430",
            "url": "https://www.bindingdb.org/entry/81430",
            "affinity_type": "Ki",
            "affinity_raw": "9.1",
            "lipinski": {"pass_rule_of_5": True, "violations_count": 0, "molecular_weight": 380.5},
            "score": 95.0,
            "pmid": "21035734",
        }
    ]

    dossier = format_dossier("CDK4", ppi_mock, leads_mock)

    assert "# Target Druggability & Lead Candidate Screening: CDK4" in dossier
    assert "MDM2" in dossier
    assert "0.994" in dossier
    assert "BDBM81430" in dossier
    assert "Ki: 9.1 nM" in dossier
    assert "PMID 21035734" in dossier
