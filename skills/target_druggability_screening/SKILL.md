---
name: target_druggability_screening
description: >
  End-to-end biological target druggability assessment and lead candidate screening.
  Retrieves protein-protein interaction networks via STRING, queries measured binding
  affinities (Ki, Kd, IC50) via BindingDB, calculates Lipinski Rule of 5 drug-likeness
  descriptors, and compiles structured evidence dossiers into session artifacts.
origin: openai4s
category: biology
requirements: []
capabilities:
  network:
    mode: none
    domains: []

---

# Target Druggability Screening

Produces an evidence-backed target druggability and lead candidate prioritization
dossier for any biological protein target (e.g. `CDK4`, `EGFR`, `TP53`, `BRAF`).
The workflow integrates functional PPI networks with quantitative pharmacology and
rule-based cheminformatics into a reproducible Markdown artifact.

## Workflow

1. **Target PPI Context**: Query STRING (`host.science.search("string", target)`)
   to assess biological interactors, hub protein centrality, and functional pathways.
2. **Quantitative Pharmacology**: Query BindingDB (`host.science.search("bindingdb", target, filters={"cutoff": 100})`)
   to retrieve experimentally validated small-molecule chemical binders ($K_i, IC_{50}, K_d$).
3. **Cheminformatics & Drug-Likeness**: Import the `target_druggability_screening.kernel`
   sidecar to evaluate Lipinski's Rule of 5 (molecular weight, H-bond donors/acceptors, rotatable bonds)
   and calculate composite prioritization scores.
4. **Artifact Compilation**: Generate an evidence-attributed dossier using `format_dossier`
   and save to the session's versioned artifacts (`host.save_artifact`).

## Interactive / Cell Recipe

Run inside a persistent Python kernel cell:

```python
from target_druggability_screening.kernel import rank_candidates, format_dossier

target = "CDK4"  # or UniProt accession "P11802"

# 1. Biological Interaction Network
ppi_res = host.science.search("string", target, limit=10)
ppi_records = ppi_res.get("results", [])

# 2. Measured Binding Affinities
binding_res = host.science.search(
    "bindingdb",
    "P11802",
    limit=20,
    filters={"cutoff": 100, "affinity_type": "Ki"},
)
ligands = binding_res.get("results", [])

# 3. Cheminformatics & Lead Prioritization
ranked_leads = rank_candidates(ligands, top_k=10)

# 4. Generate Structured Dossier Artifact
dossier_content = format_dossier(
    target=target,
    ppi_records=ppi_records,
    ranked_leads=ranked_leads,
)

artifact_id = host.save_artifact(
    f"target-druggability-{target.lower()}.md",
    dossier_content,
    mime_type="text/markdown",
    description=f"Target druggability assessment and lead candidate screening for {target}.",
)
print(f"Dossier saved as artifact: {artifact_id}")
```

## Sidecar Functions

The sidecar `target_druggability_screening.kernel` provides:

- `calculate_smiles_descriptors(smiles: str)`: Estimates molecular weight, HBD, HBA, and rotatable bonds from a SMILES representation.
- `evaluate_lipinski(descriptors: dict | str)`: Evaluates compliance with Lipinski's Rule of 5 and counts rule violations.
- `score_lead(affinity_val, affinity_type, lipinski_pass)`: Computes a composite prioritization score in $[0, 100]$.
- `rank_candidates(ligands, top_k)`: Ranks and filters active chemical ligands.
- `format_dossier(target, ppi_records, ranked_leads)`: Generates Markdown dossier tables with complete provenance.
