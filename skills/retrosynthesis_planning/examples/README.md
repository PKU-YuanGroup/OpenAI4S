# Retrosynthesis Aspirin Example

What deterministic rendering looks like when the input is a committed AiZynthFinder-shaped route tree and a set of illustrative annotations. No live search, literature verification, vendor check, synthesis or assay stands behind any of it.

## Files

| File | Responsibility |
| --- | --- |
| [`build_example.py`](build_example.py) | Reads the two fixtures, runs them through the parent sidecar to normalize and rank the routes, and regenerates the HTML and the Markdown report. It prints whether the depictions came from RDKit or from the placeholder SVG. |
| [`aspirin_routes.json`](aspirin_routes.json) | Five aspirin route trees in AiZynthFinder's export shape, with molecule and reaction nodes, stock flags, scores and metadata. Enough for normalization and ranking to come out the same way every time. |
| [`aspirin_annotations.json`](aspirin_annotations.json) | The demonstration annotations the dashboard displays: route, molecule and reaction prose, plus risks, conditions strategies and next steps. This is illustrative text written to fill the panels, not experimental evidence. |
| [`aspirin_retrosynthesis.html`](aspirin_retrosynthesis.html) | The generated dashboard, self-contained and interactive: ranked routes, molecule structures or placeholders, route cards, and the knowledge-graph and tree views. |
| [`aspirin_retrosynthesis_report.md`](aspirin_retrosynthesis_report.md) | The generated analyst report for the same fixture: ranked routes, molecule briefs, review notes. |
