---
name: target_druggability_screening
description: >
  Evidence-attributed target and ligand screening. Retrieves STRING interaction
  records and BindingDB assay measurements, optionally calculates molecular
  descriptors with RDKit, and saves a dossier with source snapshots and limitations.
origin: openai4s
category: biology
requirements: []
capabilities:
  network:
    mode: none
    domains: []
---

# Target Druggability Screening

Compile a preliminary evidence dossier for a protein target. STRING associations
and BindingDB measurements alone do not establish druggability or therapeutic
relevance. This recipe retrieves a limited sample, not an exhaustive search.

## Workflow

1. Confirm the target's **UniProt accession and species**. Resolve a gene name
   such as CDK4 through `host.science.search("uniprot", ...)` first; review ambiguous
   matches. BindingDB accepts UniProt accessions or PDB IDs, not arbitrary gene names.
   Use the same confirmed accession for both searches below.
2. Retrieve STRING interaction partners with an explicit species. The returned
   functional associations do not measure physical binding, whole-network hub
   centrality, or pathway enrichment.
3. Query BindingDB for one endpoint (`Ki`, `Kd`, `IC50`, or `EC50`) at a time.
   Even within an endpoint, review assay conditions before comparing values.
4. Use optional RDKit for molecular weight, calculated logP, HBD, HBA, and
   rotatable bonds. The Rule of Five uses the first four descriptors, allowing
   at most one violation. Install the existing `chemistry` extra in the active
   Python environment (`uv sync --locked --extra chemistry`) to enable this step.
   No RDKit, missing SMILES, and invalid structures produce **Unknown**, never
   invented descriptors or a passing verdict. Core imports remain stdlib-only.
5. Preserve qualified affinities (`<`, `>`, `~`), missing values, citations, and
   repeated assay records. These are evidence, not distinct confirmed leads.
   Scores require an exact positive nM measurement and a complete descriptor
   screen; otherwise they remain **Unscored**. The score is an unvalidated
   within-endpoint heuristic, not a probability or a biological conclusion.
6. Save each complete query envelope with its provenance, then save the dossier
   with lineage to those exact artifact versions. Propagate connector failures;
   do not replace an error with an empty successful search.

## Interactive / Cell Recipe

Run inside a persistent Python kernel cell. Host connectors mediate all network
requests; the sidecar performs no direct networking.

```python
import json
from pathlib import Path

from target_druggability_screening.kernel import format_dossier, rank_candidates

# Confirm both values before running: this example is human CDK4.
target = "P11802"
species = "9606"
affinity_type = "Ki"

ppi_res = host.science.search(
    "string", target, limit=10, filters={"species": species}
)
binding_res = host.science.search(
    "bindingdb", target, limit=20,
    filters={"cutoff": 100, "affinity_type": affinity_type},
)
ranked_leads = rank_candidates(
    binding_res["results"], top_k=10, affinity_type=affinity_type
)

source_versions = []
for result in (ppi_res, binding_res):
    source_path = Path(f"{target.lower()}-{result['database']}-source.json")
    source_path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    saved = host.save_artifact(
        str(source_path), content_type="application/json", source=result["provenance"]
    )
    source_versions.append(saved["version_id"])

dossier_content = format_dossier(
    target=target,
    ppi_records=ppi_res["results"],
    ranked_leads=ranked_leads,
    provenance=[ppi_res["provenance"], binding_res["provenance"]],
)
dossier_path = Path(f"target-druggability-{target.lower()}.md")
dossier_path.write_text(dossier_content, encoding="utf-8")
artifact = host.save_artifact(
    str(dossier_path), content_type="text/markdown", input_version_ids=source_versions
)
print(f"Dossier saved as artifact version: {artifact['version_id']}")
```

## Sidecar Functions

- `calculate_smiles_descriptors(smiles)`: Parsed RDKit descriptors and engine
  version, or a reason for missing/invalid/unavailable chemistry.
- `evaluate_lipinski(descriptors_or_smiles)`: Four-criterion screen; an incomplete
  input has `pass_rule_of_5=None`.
- `score_lead(affinity_val, affinity_type, lipinski_pass, violations_count)`:
  Exact nM potency contributes 60/50/35/20/5 for <10/<100/<1000/<10000/>=10000.
  A complete Rule of Five screen contributes 40 (pass) or 15 (fail), minus
  10 per violation, floored at zero. Unknown inputs return `None`.
- `rank_candidates(ligands, top_k, affinity_type=...)`: Select one endpoint,
  rank scored assay records, and retain unscored evidence afterwards. Mixed
  endpoints without explicit selection raise an error. Repeated assays are
  retained, so row count is not a distinct-compound count.
- `format_dossier(target, ppi_records, ranked_leads, provenance=...)`: Report
  evidence, unknowns, source links, retrieval hashes, and interpretation limits.

Descriptor definitions: [RDKit Lipinski API](https://www.rdkit.org/docs/source/rdkit.Chem.Lipinski.html)
and [RDKit molecular descriptors](https://www.rdkit.org/docs/GettingStartedInPython.html#list-of-available-descriptors).
