"""Evidence-preserving helpers for target and ligand screening.

Importing this sidecar needs only stdlib. Molecular descriptors use optional
RDKit; absent chemistry support or invalid structures remain explicitly unknown.
"""

from __future__ import annotations

import html
import math
import re
from typing import Any, Mapping, Sequence
from urllib.parse import quote, urlsplit

_AFFINITY_TYPES = {"KI": "Ki", "KD": "Kd", "IC50": "IC50", "EC50": "EC50"}
_DESCRIPTOR_FIELDS = (
    "molecular_weight",
    "logp",
    "hbd",
    "hba",
    "heavy_atom_count",
    "rotatable_bonds",
)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def calculate_smiles_descriptors(smiles: str) -> dict[str, Any]:
    """Calculate descriptors from a parsed molecule, or return an unknown state."""
    clean_smiles = smiles.strip() if isinstance(smiles, str) else ""
    result = dict.fromkeys(_DESCRIPTOR_FIELDS)
    result.update(smiles=clean_smiles, status="missing", reason="SMILES is missing")
    if not clean_smiles:
        return result
    try:
        from rdkit import Chem, rdBase
        from rdkit.Chem import Crippen, Descriptors, Lipinski
    except ImportError:
        result.update(status="unavailable", reason="RDKit is not installed")
        return result

    mol = Chem.MolFromSmiles(clean_smiles)
    if mol is None or mol.GetNumHeavyAtoms() == 0:
        result.update(
            status="invalid", reason="SMILES does not describe a valid molecule"
        )
        return result
    result.update(
        status="ok",
        reason="",
        method="RDKit",
        method_version=rdBase.rdkitVersion,
        molecular_weight=Descriptors.MolWt(mol),
        logp=Crippen.MolLogP(mol),
        hbd=Lipinski.NumHDonors(mol),
        hba=Lipinski.NumHAcceptors(mol),
        heavy_atom_count=mol.GetNumHeavyAtoms(),
        rotatable_bonds=Lipinski.NumRotatableBonds(mol),
    )
    return result


def evaluate_lipinski(
    descriptors_or_smiles: dict[str, Any] | str,
    mw_override: float | None = None,
) -> dict[str, Any]:
    """Screen MW <= 500, logP <= 5, HBD <= 5, HBA <= 10; allow one violation.

    All four descriptors must be known. Rotatable bonds are reported separately
    and are not a Rule of Five criterion. This heuristic is not clinical evidence.
    """
    desc = (
        calculate_smiles_descriptors(descriptors_or_smiles)
        if isinstance(descriptors_or_smiles, str)
        else dict(descriptors_or_smiles)
    )
    if mw_override is not None:
        desc["molecular_weight"] = mw_override
    limits = {"molecular_weight": 500, "logp": 5, "hbd": 5, "hba": 10}
    labels = {"molecular_weight": "MW", "logp": "LogP", "hbd": "HBD", "hba": "HBA"}
    values = {name: _finite_number(desc.get(name)) for name in limits}
    missing = []
    violations = []
    for name, limit in limits.items():
        value = values[name]
        invalid = value is None
        if value is not None:
            invalid |= name == "molecular_weight" and value <= 0
            invalid |= name in {"hbd", "hba"} and (value < 0 or not value.is_integer())
        if invalid:
            values[name] = None
            missing.append(name)
        elif value > limit:
            violations.append(f"{labels[name]} > {limit} ({value:g})")
    complete = not missing and desc.get("status", "ok") == "ok"
    return {
        **values,
        "status": "ok" if complete else "unknown",
        "reason": (
            "" if complete else desc.get("reason") or "Missing or invalid descriptors"
        ),
        "missing_descriptors": missing,
        "pass_rule_of_5": len(violations) <= 1 if complete else None,
        "violations_count": len(violations) if complete else None,
        "violations": violations,
        "rotatable_bonds": desc.get("rotatable_bonds"),
    }


def score_lead(
    affinity_val: float | None,
    affinity_type: str | None,
    lipinski_pass: bool | None,
    violations_count: int | None = 0,
) -> float | None:
    """Return an unvalidated within-endpoint heuristic, or None for unknown data.

    Exact positive nM measurements score 60/50/35/20/5 at <10/<100/<1000/
    <10000/>=10000. A complete Rule of Five screen adds 40 (pass) or 15
    (fail), minus 10 per violation, floored at zero. Different assay endpoints
    must not be ranked together; assay conditions still require manual review.
    """
    affinity = _finite_number(affinity_val)
    if (
        affinity is None
        or affinity <= 0
        or str(affinity_type).upper() not in _AFFINITY_TYPES
        or not isinstance(lipinski_pass, bool)
        or type(violations_count) is not int
        or not 0 <= violations_count <= 4
    ):
        return None
    affinity_score = next(
        (
            points
            for upper, points in ((10, 60), (100, 50), (1000, 35), (10000, 20))
            if affinity < upper
        ),
        5,
    )
    drug_score = max(0, (40 if lipinski_pass else 15) - violations_count * 10)
    return float(affinity_score + drug_score)


def _exact_affinity(attrs: Mapping[str, Any]) -> float | None:
    value = _finite_number(attrs.get("affinity_value"))
    if value is None or value <= 0:
        return None
    raw = attrs.get("affinity_raw")
    if raw not in (None, ""):
        match = re.fullmatch(
            r"=?\s*([+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)",
            str(raw).strip(),
        )
        if match is None or float(match[1]) != value:
            return None
    return value


def rank_candidates(
    ligands: Sequence[Mapping[str, Any]],
    top_k: int = 10,
    *,
    affinity_type: str | None = None,
) -> list[dict[str, Any]]:
    """Rank assay records for one endpoint; retain unscored evidence after scores.

    Records are not distinct compounds: repeated measurements are kept with their
    citations. Select one endpoint explicitly when the input contains mixed types.
    """
    if type(top_k) is not int or top_k < 0:
        raise ValueError("top_k must be a nonnegative integer")
    selected = str(affinity_type or "").upper()
    if selected and selected not in _AFFINITY_TYPES:
        raise ValueError("affinity_type must be Ki, Kd, IC50, or EC50")
    scored: list[dict[str, Any]] = []
    for item in ligands:
        if not isinstance(item, Mapping):
            continue
        attrs = item.get("attributes") or {}
        if not isinstance(attrs, Mapping):
            continue
        monomer_id = str(attrs.get("monomer_id") or item.get("id") or "")
        if not monomer_id:
            continue
        endpoint = str(attrs.get("affinity_type") or "").upper()
        if selected and endpoint != selected:
            continue
        smiles = attrs.get("smiles") or ""
        desc = calculate_smiles_descriptors(smiles)
        lipinski = evaluate_lipinski(desc)
        numeric_aff = _exact_affinity(attrs)
        score = score_lead(
            numeric_aff,
            endpoint,
            lipinski["pass_rule_of_5"],
            lipinski["violations_count"],
        )
        scored.append(
            {
                "id": monomer_id,
                "source_record_id": item.get("id"),
                "target_name": attrs.get("target_name") or "",
                "affinity_type": _AFFINITY_TYPES.get(endpoint, endpoint),
                "affinity_raw": attrs.get("affinity_raw"),
                "affinity_value": numeric_aff,
                "smiles": smiles,
                "descriptors": desc,
                "lipinski": lipinski,
                "score": score,
                "score_reason": (
                    ""
                    if score is not None
                    else "Exact positive affinity and complete descriptors required"
                ),
                "url": item.get("url") or "",
                "pmid": attrs.get("pmid"),
                "doi": attrs.get("doi"),
            }
        )
    endpoints = {row["affinity_type"] for row in scored if row["affinity_type"]}
    if len(endpoints) > 1:
        raise ValueError(
            "Mixed affinity endpoints; select affinity_type before ranking"
        )
    scored.sort(
        key=lambda row: (
            row["score"] is None,
            -row["score"] if row["score"] is not None else 0,
            row["affinity_value"] if row["score"] is not None else 0,
        )
    )
    return scored[:top_k]


def _text(value: Any) -> str:
    text = html.escape(str(value if value is not None else "-"), quote=False)
    return (
        " ".join(text.split())
        .replace("|", "&#124;")
        .replace("`", "&#96;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
    )


def _link(label: Any, url: Any) -> str:
    text = _text(label)
    try:
        valid = (
            urlsplit(str(url)).scheme in {"http", "https"} and urlsplit(str(url)).netloc
        )
    except ValueError:
        valid = False
    return f"[{text}]({quote(str(url), safe=':/?&=%#@')})" if valid else text


def _number_text(value: Any, digits: int = 1) -> str:
    number = _finite_number(value)
    return f"{number:.{digits}f}" if number is not None else "-"


def format_dossier(
    target: str,
    ppi_records: Sequence[Mapping[str, Any]],
    ranked_leads: Sequence[Mapping[str, Any]],
    summary_text: str = "",
    *,
    provenance: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Summarize returned evidence without asserting therapeutic relevance."""
    target_text = _text(target)
    lines = [
        f"# Target Druggability & Lead Candidate Screening: {target_text}",
        "",
        "## Evidence Summary",
        "",
        f"Retrieved {len(ppi_records)} STRING interaction records; showing {min(10, len(ppi_records))}. "
        f"Selected {len(ranked_leads)} BindingDB assay records (not necessarily distinct compounds).",
        "These limited query results do not establish target druggability, therapeutic relevance, "
        "clinical efficacy, selectivity, or network centrality. No matches do not establish absence of binders.",
        "",
    ]
    if summary_text:
        lines.extend(["Caller-supplied interpretation: " + _text(summary_text), ""])
    lines.extend(
        [
            "## 1. Protein Interaction Context (STRING)",
            "",
            "Functional association is not necessarily physical binding. Scores are evidence confidence.",
            "",
            "| Partner Protein | STRING ID | Combined Score | Experimental Score | Network Type | Source |",
            "| :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for ppi in ppi_records[:10]:
        attrs = ppi.get("attributes") or {}
        lines.append(
            f"| {_text(attrs.get('partner_protein') or ppi.get('id'))} | {_text(attrs.get('partner_string_id'))} | "
            f"{_number_text(attrs.get('score'), 3)} | {_number_text(attrs.get('experimental_score'), 3)} | "
            f"{_text(attrs.get('network_type'))} | {_link('STRING', ppi.get('url'))} |"
        )
    if not ppi_records:
        lines.extend(["", "No interaction records returned for this query."])
    lines.extend(
        [
            "",
            "## 2. Ligand Assay Records (BindingDB)",
            "",
            "Scores are unvalidated screening heuristics within one endpoint; Ki, Kd, IC50 and EC50 "
            "are not interchangeable. Assay conditions may differ even within an endpoint. "
            "Qualified or missing affinities and incomplete descriptors remain unscored.",
            "",
            "Rule of Five permits at most one violation of MW <= 500, logP <= 5, HBD <= 5, HBA <= 10. "
            "Rotatable bonds are not a Rule of Five criterion. Descriptors use RDKit when available.",
            "",
            "| Row | Ligand ID | Endpoint / Affinity (nM) | Rule of Five | MW (Da) | logP | HBD / HBA | Score / 100 | Reference |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
        ]
    )
    for rank, lead in enumerate(ranked_leads, start=1):
        lip = lead.get("lipinski") or {}
        passed = lip.get("pass_rule_of_5")
        verdict = (
            f"{'Pass' if passed else 'Fail'} ({lip.get('violations_count')} violations)"
            if isinstance(passed, bool)
            else "Unknown: " + _text(lip.get("reason") or "incomplete descriptors")
        )
        raw = lead.get("affinity_raw")
        if raw in (None, ""):
            raw = lead.get("affinity_value")
        refs = []
        if lead.get("pmid"):
            refs.append(
                _link(
                    f"PMID {lead['pmid']}",
                    f"https://pubmed.ncbi.nlm.nih.gov/{quote(str(lead['pmid']), safe='')}/",
                )
            )
        if lead.get("doi"):
            refs.append(
                _link(
                    lead["doi"], f"https://doi.org/{quote(str(lead['doi']), safe='/')}"
                )
            )
        score = (
            _number_text(lead.get("score"))
            if lead.get("score") is not None
            else "Unscored"
        )
        lines.append(
            f"| {rank} | {_link(lead['id'], lead.get('url'))} | {_text(lead.get('affinity_type') or 'Unknown')}: {_text(raw)} nM | "
            f"{verdict} | {_number_text(lip.get('molecular_weight'))} | {_number_text(lip.get('logp'), 2)} | "
            f"{_number_text(lip.get('hbd'), 0)} / {_number_text(lip.get('hba'), 0)} | {score} | {'; '.join(refs) or '-'} |"
        )
    if not ranked_leads:
        lines.extend(["", "No ligand assay records selected for this query."])
    versions = sorted(
        {
            str(lead["descriptors"]["method_version"])
            for lead in ranked_leads
            if lead.get("descriptors", {}).get("method_version")
        }
    )
    if versions:
        lines.extend(
            ["", "Descriptor engine: RDKit " + _text(", ".join(versions)) + "."]
        )
    lines.extend(["", "## 3. Retrieval Provenance", ""])
    if not provenance:
        lines.append(
            "Retrieval provenance was not supplied; source completeness cannot be verified."
        )
    for source in provenance:
        lines.append(
            f"- {_text(source.get('database'))}: {_link('request', source.get('request_url'))}; "
            f"retrieved_at (Unix ms): {_text(source.get('retrieved_at'))}; "
            f"response SHA-256: {_text(source.get('response_sha256'))}."
        )
    lines.extend(
        [
            "",
            "## 4. Evidence Gaps and Follow-up",
            "",
            "Confirm target identity and species, inspect the saved source records and assay conditions, "
            "and obtain independent binding and selectivity evidence before choosing experimental candidates.",
            "",
        ]
    )
    return "\n".join(lines)
