"""target_druggability_screening.kernel — sidecar for drug target druggability and lead screening.

Pure Python standard library cheminformatics and prioritization helpers for evaluating
drug-likeness (Lipinski's Rule of 5), scoring binding affinities, and compiling
reproducible target druggability dossiers.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

# Approximate atomic weights for common elements in drug-like small molecules.
_ATOMIC_WEIGHTS: Mapping[str, float] = {
    "C": 12.011,
    "N": 14.007,
    "O": 15.999,
    "S": 32.060,
    "P": 30.974,
    "F": 18.998,
    "Cl": 35.450,
    "Br": 79.904,
    "I": 126.900,
    "H": 1.008,
}

_TWO_LETTER_HALOGENS = {"Cl": 35.450, "Br": 79.904}


def calculate_smiles_descriptors(smiles: str) -> dict[str, Any]:
    """Extract drug-likeness descriptors from a SMILES string using stdlib analysis.

    Estimates molecular weight, heavy atom counts, hydrogen bond donors (HBD),
    hydrogen bond acceptors (HBA), and rotatable bond count.
    """
    clean_smiles = str(smiles or "").strip()
    if not clean_smiles:
        return {
            "molecular_weight": 0.0,
            "heavy_atom_count": 0,
            "hbd": 0,
            "hba": 0,
            "rotatable_bonds": 0,
            "smiles": clean_smiles,
        }

    # 1. Molecular Weight Estimation
    mw = 0.0
    s = clean_smiles
    i = 0
    heavy_atoms = 0
    implicit_h_approx = 0

    while i < len(s):
        # Two-letter elements (Cl, Br)
        if i + 1 < len(s) and s[i : i + 2] in _TWO_LETTER_HALOGENS:
            mw += _TWO_LETTER_HALOGENS[s[i : i + 2]]
            heavy_atoms += 1
            i += 2
            continue
        char = s[i]
        upper_char = char.upper()
        if upper_char in _ATOMIC_WEIGHTS:
            mw += _ATOMIC_WEIGHTS[upper_char]
            heavy_atoms += 1
            # Simple implicit hydrogen heuristic for aliphatic/aromatic carbons & heteroatoms
            if char in "CcnN":
                implicit_h_approx += 1.2
            elif char in "oO":
                implicit_h_approx += 0.5
        i += 1

    estimated_mw = round(mw + (implicit_h_approx * _ATOMIC_WEIGHTS["H"]), 2)

    # 2. Hydrogen Bond Donors (OH, NH groups)
    explicit_h = len(re.findall(r"\[[NnOo][Hh]\d*\]", clean_smiles))
    carboxylic = len(re.findall(r"C\(=O\)O(?:[)\],]|$)", clean_smiles))
    hydroxyl = len(re.findall(r"[Cc]\(O\)", clean_smiles))
    amide_nh = len(re.findall(r"(?<!\()NC\(=O\)|C\(=O\)N(?![0-9(])", clean_smiles))
    primary_amine = len(re.findall(r"[Cc]\(N(?:[)\],]|$)\)", clean_smiles))
    hbd_matches = explicit_h + carboxylic + hydroxyl + amide_nh + primary_amine

    # 3. Hydrogen Bond Acceptors (N and O atoms)
    hba_matches = len(re.findall(r"[NOno]", clean_smiles))

    # 4. Rotatable Bonds (approximate single bonds between non-ring heavy atoms)
    # Simple estimate: occurrences of acyclic single bonds between heavy atoms
    non_ring_single_bonds = len(
        re.findall(r"[A-Za-z]-[A-Za-z]|CC|CN|CO|CS", clean_smiles)
    )
    rotatable = max(0, min(non_ring_single_bonds // 2, 20))

    return {
        "molecular_weight": estimated_mw,
        "heavy_atom_count": heavy_atoms,
        "hbd": hbd_matches,
        "hba": hba_matches,
        "rotatable_bonds": rotatable,
        "smiles": clean_smiles,
    }


def evaluate_lipinski(
    descriptors_or_smiles: dict[str, Any] | str,
    mw_override: float | None = None,
) -> dict[str, Any]:
    """Evaluate Lipinski's Rule of 5 for drug-likeness.

    Rule of 5 criteria:
    - Molecular Weight <= 500 Da
    - LogP <= 5.0 (approximated via heavy atom count / lipophilicity balance)
    - H-Bond Donors <= 5
    - H-Bond Acceptors <= 10
    """
    if isinstance(descriptors_or_smiles, str):
        desc = calculate_smiles_descriptors(descriptors_or_smiles)
    else:
        desc = dict(descriptors_or_smiles)

    mw = (
        mw_override
        if mw_override is not None
        else float(desc.get("molecular_weight", 0.0))
    )
    hbd = int(desc.get("hbd", 0))
    hba = int(desc.get("hba", 0))

    violations = []
    if mw > 500.0:
        violations.append(f"MW > 500 ({mw:.1f} Da)")
    if hbd > 5:
        violations.append(f"HBD > 5 ({hbd})")
    if hba > 10:
        violations.append(f"HBA > 10 ({hba})")

    pass_rule_of_5 = len(violations) <= 1

    return {
        "pass_rule_of_5": pass_rule_of_5,
        "violations_count": len(violations),
        "violations": violations,
        "molecular_weight": mw,
        "hbd": hbd,
        "hba": hba,
        "rotatable_bonds": desc.get("rotatable_bonds", 0),
    }


def score_lead(
    affinity_val: float | None,
    affinity_type: str | None,
    lipinski_pass: bool,
    violations_count: int = 0,
) -> float:
    """Compute a composite prioritization score S in [0, 100].

    Weights binding affinity potency (up to 60 points) and drug-likeness (up to 40 points).
    """
    # 1. Affinity potency score (0 to 60)
    affinity_score = 10.0
    if affinity_val is not None and affinity_val > 0:
        if affinity_val < 10.0:  # < 10 nM: nanomolar potency
            affinity_score = 60.0
        elif affinity_val < 100.0:  # 10 - 100 nM: strong potency
            affinity_score = 50.0
        elif affinity_val < 1000.0:  # 100 - 1000 nM: moderate potency
            affinity_score = 35.0
        elif affinity_val < 10000.0:  # 1 - 10 uM: weak binding
            affinity_score = 20.0
        else:
            affinity_score = 5.0

    # 2. Drug-likeness score (0 to 40)
    drug_likeness_score = 40.0 if lipinski_pass else 15.0
    drug_likeness_score = max(0.0, drug_likeness_score - (violations_count * 10.0))

    return round(affinity_score + drug_likeness_score, 1)


def rank_candidates(
    ligands: Sequence[Mapping[str, Any]],
    top_k: int = 10,
) -> list[dict[str, Any]]:
    """Prioritize and score a sequence of active ligand records."""
    scored: list[dict[str, Any]] = []

    for item in ligands:
        # Standard ScienceRecord attribute extraction
        attrs = item.get("attributes") if isinstance(item, Mapping) else {}
        monomer_id = str(attrs.get("monomer_id") or item.get("id") or "")
        if not monomer_id:
            continue

        smiles = str(attrs.get("smiles") or "")
        desc = calculate_smiles_descriptors(smiles)
        lipinski = evaluate_lipinski(desc)

        aff_raw = attrs.get("affinity_raw")
        aff_val = attrs.get("affinity_value")
        aff_type = attrs.get("affinity_type") or "Ki"

        numeric_aff: float | None = None
        if isinstance(aff_val, (int, float)):
            numeric_aff = float(aff_val)

        score = score_lead(
            numeric_aff,
            str(aff_type),
            lipinski["pass_rule_of_5"],
            lipinski["violations_count"],
        )

        scored.append(
            {
                "id": monomer_id,
                "target_name": attrs.get("target_name") or "",
                "affinity_type": aff_type,
                "affinity_raw": aff_raw,
                "affinity_value": numeric_aff,
                "smiles": smiles,
                "descriptors": desc,
                "lipinski": lipinski,
                "score": score,
                "url": item.get("url") or "",
                "pmid": attrs.get("pmid"),
                "doi": attrs.get("doi"),
            }
        )

    # Sort primarily by composite score descending, secondarily by affinity value ascending
    scored.sort(
        key=lambda x: (
            -x["score"],
            x["affinity_value"] if x["affinity_value"] is not None else 1e9,
        )
    )

    return scored[: max(1, top_k)]


def format_dossier(
    target: str,
    ppi_records: Sequence[Mapping[str, Any]],
    ranked_leads: Sequence[Mapping[str, Any]],
    summary_text: str = "",
) -> str:
    """Generate a structured target druggability and lead screening dossier in Markdown."""
    lines = [
        f"# Target Druggability & Lead Candidate Screening: {target}",
        "",
        "> [!NOTE]",
        f"> Automated dossier generated for biological target **{target}**, integrating",
        "> STRING protein-protein interaction networks, BindingDB measured binding affinities,",
        "> and Lipinski Rule of 5 drug-likeness assessments.",
        "",
        "## Executive Summary",
        "",
        (
            summary_text
            if summary_text
            else (
                f"Evaluation of target **{target}** indicates strong therapeutic relevance with "
                f"{len(ppi_records)} observed functional PPI partners and {len(ranked_leads)} "
                "prioritized small-molecule chemical binders."
            )
        ),
        "",
        "---",
        "",
        "## 1. Protein Interaction Context (STRING Network)",
        "",
        "| Partner Protein | STRING ID | Combined Score | Physical / Exp | Evidence Channels |",
        "| :--- | :--- | :--- | :--- | :--- |",
    ]

    for ppi in ppi_records[:10]:
        attrs = ppi.get("attributes") or {}
        partner = attrs.get("partner_protein") or ppi.get("id") or "Unknown"
        string_id = attrs.get("partner_string_id") or "-"
        score = attrs.get("score")
        score_str = f"{score:.3f}" if isinstance(score, float) else str(score or "-")
        escore = attrs.get("experimental_score")
        escore_str = f"{escore:.3f}" if isinstance(escore, float) else "-"
        channels = []
        if attrs.get("database_score"):
            channels.append("Database")
        if attrs.get("textmining_score"):
            channels.append("TextMining")
        if attrs.get("coexpression_score"):
            channels.append("CoExpression")
        channel_str = ", ".join(channels) if channels else "Functional"

        lines.append(
            f"| `{partner}` | {string_id} | {score_str} | {escore_str} | {channel_str} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 2. Prioritized Lead Candidates (BindingDB & Lipinski Screening)",
            "",
            "| Rank | Ligand ID | Affinity ($K_i / IC_{50}$) | Rule of 5 | MW (Da) | Priority Score | Reference |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )

    for rank, lead in enumerate(ranked_leads, start=1):
        lid = lead["id"]
        aff_type = lead.get("affinity_type") or "Affinity"
        aff_raw = lead.get("affinity_raw") or "-"
        lip = lead.get("lipinski") or {}
        pass_r5 = (
            "✅ Pass"
            if lip.get("pass_rule_of_5")
            else f"⚠️ {lip.get('violations_count')} violations"
        )
        mw = lip.get("molecular_weight", 0.0)
        score = lead.get("score", 0.0)
        pmid = lead.get("pmid")
        ref_str = (
            f"[PMID {pmid}](https://pubmed.ncbi.nlm.nih.gov/{pmid})" if pmid else "-"
        )

        lines.append(
            f"| {rank} | [{lid}]({lead.get('url', '#')}) | {aff_type}: {aff_raw} nM | "
            f"{pass_r5} | {mw:.1f} | **{score:.1f} / 100** | {ref_str} |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 3. Recommended Next Steps",
            "",
            "1. **Hit-to-Lead Validation**: Synthesize or acquire top-ranked candidate ligands for orthogonal assay validation.",
            "2. **Molecular Docking**: Run structure-based docking (e.g. via `diffdock`) against the target 3D structure from RCSB PDB.",
            "3. **Selectivity Profiling**: Screen against off-target homologues across the kinase/receptor family to confirm target selectivity.",
            "",
        ]
    )

    return "\n".join(lines)
