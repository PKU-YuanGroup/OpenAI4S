---
name: is-qualitative-analysis
description: "Information Systems (IS) interview and case-study analysis: codebooks, reflexive thematic analysis, evidence locators, negative cases and nominal intercoder agreement (Krippendorff alpha/Cohen kappa). 信息系统访谈、案例、质性编码与编码者一致性。"
license: MIT
origin: openai4s
category: empirical-analysis
capabilities:
  network:
    mode: none
    domains: []
---

# IS qualitative analysis

Adapted from AlterLab's `alterlab-qualitative-analysis`, pinned and attributed
in `UPSTREAM.json` and `NOTICE.md`. Use for interviews, observations, documents,
and cases about technology use, implementation, work practices, and organizing.
This adds human qualitative analysis support; use existing `text-features` for
computational text features and `literature-review` for finding publications.

## Choose the analytic approach before coding

1. Record the question, cases/participants, recruitment, temporal boundary,
   unit of analysis, epistemological stance, and researcher role. Distinguish
   participant accounts of an effect from a demonstrated causal effect.
2. For coding-reliability/content analysis, define the unit boundaries and
   codebook before independently coding the assessment subset. Keep original
   codes separate from adjudicated ones. Agreement on reconciled labels does
   not measure independent coding. A codebook alone does not make an agreement
   coefficient appropriate: justify it for the particular study.
3. For reflexive thematic analysis, develop interpretations through engagement
   with the material, memoing, and reflexivity. Collaborative discussion can
   expose assumptions; neither consensus nor an agreement score is a required
   validation target. Do not present model-generated themes as discovered facts.

Read the relevant self-contained guide through the real skill resource API:

```python
print(host.skills.read("is-qualitative-analysis", "analysis-guide.md"))
# Read this second reference only for a coding-agreement calculation:
print(host.skills.read("is-qualitative-analysis", "agreement-guide.md"))
```

## Preserve the evidence chain

For codebook/content analysis, maintain definitions, inclusion/exclusion rules,
versions, and verified exemplar locators. For reflexive thematic analysis, keep
evolving analytic memos and interpretive decisions instead of imposing a fixed
codebook. For each coded excerpt, retain the source Artifact
version, pseudonymous case/participant ID, exact line/page/timestamp or segment
ID, the source quote, coder identity/type, coding pass, and analytic memo. Keep
translation separate from the original; identify omissions and paraphrases.
Do not invent participant quotations or infer a locator from a summary.

Build a case-by-theme matrix with supporting and disconfirming evidence. Follow
negative cases and alternative interpretations; describe how they change scope
or interpretation. A count of excerpts is not a population prevalence estimate,
and a small case collection does not establish saturation automatically.

LLM suggestions are provisional analytic aids reviewed by the researcher. They
are not independent human coding or an automatic human gold standard. Record
model, prompt/version, material supplied, and human changes if used. Do not
invoke another model or upload interview material merely to run this skill;
its helper works locally and makes no network requests.

## Nominal agreement recipe

Use only for fixed shared units with one nominal label per coder per unit.
The stdlib helper provides point estimates and a missing-data audit; it does
not fit ordinal/multilabel/unitizing agreement or compute confidence intervals.
Do not silently encode a multi-label set as one category.

After selecting the real input Artifact, bind its ID to `coding_version_id`.
The CSV must contain unique nonblank `unit_id` values and one column for each
selected coder. Replace the coder names below with the actual selected columns.
Blank cells mean missing; use an explicit code for a substantive “not present.”

```python
import csv
import importlib
import json
from pathlib import Path

nominal_agreement = importlib.import_module(
    "is-qualitative-analysis.kernel"
).nominal_agreement
coder_names = ["human_A", "human_B"]
with open(host.artifact_path(coding_version_id), newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    required = {"unit_id", *coder_names}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError("CSV must contain unit_id and the selected coder columns")
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("CSV header names must be unique")
    records = list(reader)
if any(None in row or any(value is None for value in row.values()) for row in records):
    raise ValueError("CSV rows must match the header width")
unit_ids = [row["unit_id"].strip() for row in records]
if any(not value for value in unit_ids) or len(set(unit_ids)) != len(unit_ids):
    raise ValueError("unit_id must be nonblank and unique")
report = nominal_agreement(
    [[row[coder].strip() for coder in coder_names] for row in records],
    coder_names=coder_names,
)
report["input_version_id"] = coding_version_id
report["unit_ids_in_order"] = unit_ids
path = Path("qualitative-agreement.json")
path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
saved = host.save_artifact(
    str(path), content_type="application/json", input_version_ids=[coding_version_id]
)
print({"artifact": saved, "agreement": report})
```

The double-coded fraction applies to the supplied rows only; name the full
corpus denominator separately. Report coder training, selection of the assessed
subset, pre-adjudication labels, missingness, coefficient, and uncertainty.
If all observed codes have one category, chance-corrected agreement is undefined,
even when observed agreement is 100%. Missing uncertainty stays explicitly
`not_estimated`; do not manufacture a CI, a “reliable” verdict, or equivalence
between a model and human from the point estimate.

## Deliver the analysis

Save the codebook/memos and theme-evidence matrix as workspace files and register
them with `host.save_artifact(..., input_version_ids=[...])`. Use genuine source
versions; omit source links that do not exist. Include the chosen analytic
approach, human review status, source locators, negative cases, reflexive
decisions, and limits of transferability in the narrative. Complete via the
normal `finalize_response` or `host.submit_output` contract after the work is
done. A numeric agreement report alone is not a completed qualitative analysis.
