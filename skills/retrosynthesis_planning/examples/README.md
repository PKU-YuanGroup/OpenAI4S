# Retrosynthesis Aspirin Example

This directory demonstrates deterministic rendering from committed AiZynthFinder-shaped route trees and illustrative annotations. It does not claim that a live search, literature verification, vendor check, synthesis, or assay occurred.

## Direct files

| File | Responsibility |
| --- | --- |
| [`build_example.py`](build_example.py) | Reads the committed route and annotation fixtures, normalizes/ranks routes through the parent sidecar, regenerates HTML/Markdown, and reports whether RDKit or placeholder SVG depictions were used. |
| [`aspirin_routes.json`](aspirin_routes.json) | Five AiZynthFinder-shaped aspirin route trees with molecule/reaction nodes, stock flags, scores, and metadata for deterministic normalization/ranking. |
| [`aspirin_annotations.json`](aspirin_annotations.json) | Deterministic demonstration route/molecule/reaction prose, risks, conditions strategies, and next steps; explicitly illustrative rather than experimental evidence. |
| [`aspirin_retrosynthesis.html`](aspirin_retrosynthesis.html) | Generated self-contained interactive dashboard with ranked routes, structures/placeholders, route cards, and knowledge/tree views. |
| [`aspirin_retrosynthesis_report.md`](aspirin_retrosynthesis_report.md) | Generated analyst report listing ranked routes, molecule briefs, and review notes for the fixture. |

## Direct subdirectories

None.
