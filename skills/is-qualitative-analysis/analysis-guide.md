# Method, evidence, and interpretation

Adapted from AlterLab's `references/reflexive_vs_codebook.md`; see `UPSTREAM.json`.

## Fit the approach to the claim

Coding-reliability/content analysis treats specified coding decisions as
replicable classification. Fix unit boundaries, codebook version and coding
instructions for the reliability subset; sample that subset deliberately and
report whether it covers cases, languages, dates and rare categories. Human
coders work independently before reconciliation. Keep calibration/training
examples separate from the assessed subset.

Codebook and framework approaches can use structured matrices without adopting
coding reliability as their quality criterion. Explain the study's assumptions
before adding a coefficient. For mixed approaches, identify precisely which
structured component the number concerns.

Reflexive thematic analysis treats interpretation as situated researcher work.
Document theoretical stance, positionality, changes in interpretation and
relations with participants. Collaborating researchers can challenge each
other's assumptions without converging on one supposedly correct coding. Do not
impose a fixed codebook, consensus requirement, or inter-rater reliability
threshold on this approach. This corrects the upstream “consensus + reflexivity”
shorthand. See the authors' [quality checklist](https://www.thematicanalysis.net/editor-checklist/)
and [approach distinction](https://www.thematicanalysis.net/understanding-ta/).

## A useful IS evidence table

Use one row per claim/excerpt relation, with these explicit columns:

| Column | Content |
| --- | --- |
| `claim_id`, `theme` | The analytical proposition and organizing concept. |
| `case_id`, `participant_id` | Pseudonymous identifiers; distinguish organization, person, and event. |
| `source_version_id`, `locator` | Actual Artifact version plus line/page/timestamp or fixed segment ID. |
| `source_quote`, `translation` | Exact excerpt and separately identified translation, if needed. |
| `code`, `codebook_version` | Applied code and rules used; leave inapplicable fields explicit. |
| `coder`, `coder_type`, `coding_pass` | Human identity or model identifier, human/model type, and pass. |
| `evidence_role` | Support, contradiction, alternative explanation, or contextual evidence. |
| `interpretation`, `review_status` | Analytic memo and actual human-review state. |

The quote establishes what was said or recorded, not automatically the truth of
the participant's causal explanation. An interview about an AI rollout can
support an account of perceived deskilling; establishing a productivity effect
needs a corresponding design and measurements. Check whether managerial and
front-line accounts disagree, and preserve that disagreement in the analysis.

Compare within-case histories before making cross-case claims. Look for
contradictions, absent expected patterns, and rival explanations. Explain the
case-selection logic and boundary conditions. Do not convert a recurring code
into a population frequency, claim saturation from a preset interview count,
or imply that an LLM checked the interpretation independently.

Use a redacted excerpt set where the task permits; keep identifying material
out of the public analysis outputs. A source locator should be sufficient for
authorized reviewers to verify the evidence without exposing participants in
the final text. Never restore a redacted identity by inference.
