# Literature Review Skill

This progressive-disclosure Skill gives an evidence-first literature synthesis workflow. Its sidecar can query public scholarly metadata and verify identifiers, but network responses, index coverage, retraction state, and article access remain external and time-dependent.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Defines request framing, retrieve-before-write grounding, DOI/source verification, retraction/null-result handling, comparative synthesis, evidence calibration, citation placement, and final prose checks. |
| [`kernel.py`](kernel.py) | Optional sidecar: obtains a rebind-safe Host SDK/contact; performs bounded Crossref/OpenAlex/DOI HTTP lookups; extracts/quotes/verifies DOIs; expands citation graphs; decodes minimal HTML; and runs a deterministic `style_pass` prose lint. |

## Direct subdirectories

None.

Lookup success is not full-text verification, and lookup failure is not evidence that a paper does not exist. Final claims must remain grounded in retrieved primary sources.
