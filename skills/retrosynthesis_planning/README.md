# Retrosynthesis Planning Skill

Taking a target SMILES through a route search and then through a chemist's review of what comes back. AiZynthFinder does the searching, in an environment of its own. The sidecar here is stdlib-first: it normalizes and ranks the exported routes and renders the review artifacts. RDKit, when it is installed, adds real structure depictions; Host LLM calls, when they are used, add the chemistry annotations.

The workflow supports planning and chemist triage, not experimental validation of a route. Conditions, yields, availability, safety notes and everything the LLM writes remain hypotheses until they are checked against the literature, ELN and vendor data, and expert review.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The pipeline end to end: the inputs to supply (target SMILES, an AiZynthFinder `config.yml`, a workdir), how the search is invoked and its JSON export loaded, and the ranking that follows — solved status first, then score, step count and precursor count. Then the molecule briefs for target, intermediates, stock precursors and the terminal precursors nothing resolved; the `host.llm` annotations; the self-contained HTML dashboard and the Markdown analyst report; and the line the reviewer may not cross, since conditions, yields and verdicts written by a model are hypotheses. |
| [`kernel.py`](kernel.py) | The optional sidecar. It canonicalizes SMILES when RDKit is present, builds a safe AiZynth command, loads route exports and normalizes and ranks them, and collects each molecule's role, query URL and structure source. It builds the Host LLM annotation prompt and parses the reply back, then renders the self-contained route tables, AND-OR trees, knowledge graph and dashboard, and writes the Markdown analyst report. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`examples/`](examples/) | The deterministic aspirin-shaped route and annotation fixtures, the HTML and report generated from them, and the script that rebuilds both. |
