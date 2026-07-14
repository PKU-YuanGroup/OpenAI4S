---
name: retrosynthesis_planning
description: industrial retrosynthesis planning pipeline for target SMILES; run AiZynthFinder, normalize route JSON, query route molecules, rank routes, render figure-style route dashboards, and write analyst reports with retrosynthetic rationale.
origin: openai4s
category: chemistry
---
# Skill: retrosynthesis planning

Use this skill when a task asks for retrosynthetic analysis, synthesis route
planning, purchasable precursor search, route-tree visualization, molecule
lookup, or a medicinal chemistry synthesis feasibility summary.

The recommended backend is AiZynthFinder running in a separate environment. This
skill keeps OpenAI4S core dependency-free: the helper module is pure stdlib and
uses RDKit from the optional science environment for transparent-background 2D
molecule depictions. Install the science extra before generating visual reports:

```bash
uv sync --extra science
```

## Capability summary

This skill implements a complete retrosynthesis review pipeline:

1. build a reproducible `aizynthcli` command for a target SMILES
2. load AiZynthFinder JSON exports
3. normalize backend routes into a stable route schema
4. rank routes by solved status, score, step count, and precursor count
5. collect molecule briefs for target, intermediates, stock precursors, and
   unresolved terminal precursors
6. call the configured conversation LLM (`host.llm`) for route, molecule, and
   reaction annotations
7. render a self-contained HTML dashboard with route ranking, molecule
   structures, an interactive retrosynthesis knowledge graph, route cards, and
   a Markdown analyst report

The dashboard is intended for chemist review and route triage. It does not
claim experimental validation. Conditions, yield ranges, route verdicts, and
safety notes produced by the LLM must be treated as hypotheses until checked
against literature, internal ELN data, vendor availability, and expert review.

## Inputs

- **`target_smiles`** (required) — target molecule as SMILES.
- **`config_path`** (required for live search) — AiZynthFinder `config.yml`.
- **`workdir`** (optional) — where to write route JSON, HTML dashboard, and
  Markdown report. Defaults to the current workspace.
- **`max_routes`** (optional) — number of ranked routes to visualize; default 10.

## Tools this skill expects

| Purpose | Tool |
|---|---|
| Route search | `host.bash` running `conda run -n retro aizynthcli ...` |
| Molecule lookup | `host.web_fetch` or `host.web_search` using PubChem, vendor pages, or literature |
| Visualization | `render_route_tree_html(...)` from this skill |
| Reporting | `build_markdown_report(...)` from this skill |

For publication-facing visuals, load `figure-style` before finalizing the HTML
or figures. This skill's dashboard follows the same principles: data-grounded
labels, limited semantic colors, explicit uncertainty, CVD-safe distinctions,
and a render-then-verify QA pass.

## Backend setup

Create the backend once outside OpenAI4S:

```bash
conda create -n retro python=3.11 -y
conda activate retro
python -m pip install "aizynthfinder[all]"
mkdir -p ~/Documents/Openai4S/retro_data
download_public_data ~/Documents/Openai4S/retro_data
```

The public-data command writes a `config.yml` file. Keep model files and stock
files out of git.

## Import

```python
from retrosynthesis_planning.kernel import (
    annotate_routes_with_llm,
    build_aizynth_command,
    build_llm_annotation_prompt,
    build_markdown_report,
    build_molecule_structure_src,
    build_pubchem_query_url,
    canonicalize_smiles,
    collect_molecule_briefs,
    collect_reaction_evidence,
    collect_reaction_briefs,
    command_to_shell,
    load_aizynth_routes,
    normalize_routes,
    OpenAI4SLLMReactionEvidenceProvider,
    rank_routes,
    render_route_tree_html,
)
```

## Workflow

### Phase 1 — Normalize and search

Normalize the target and run AiZynthFinder. Set `MPLCONFIGDIR` to a writable
temporary directory when running through the web app so Matplotlib does not
pause on cache creation.

```python
target = canonicalize_smiles("CC(=O)Oc1ccccc1C(=O)O")  # aspirin
cmd = build_aizynth_command(
    target,
    config_path="~/Documents/Openai4S/retro_data/config.yml",
    output_path="aspirin_routes.json",
    conda_env="retro",
)
host.bash("MPLCONFIGDIR=/private/tmp/aizynth_mpl " + command_to_shell(cmd))
```

### Phase 2 — Normalize and rank routes

```python
routes = load_aizynth_routes("aspirin_routes.json")
ranked = rank_routes(normalize_routes(routes))
for route in ranked[:5]:
    print(route["rank"], route["solved"], route["score"], route["steps"])
```

### Phase 3 — Molecule lookup and interpretation

Every report must explain each target/intermediate/terminal molecule that
appears in the displayed routes. Use `collect_molecule_briefs(...)` first, then
query the key molecules. At minimum, check the target and all top-route terminal
precursors in PubChem; for industrial deployment, also check vendors and
literature precedent.

```python
briefs = collect_molecule_briefs(ranked[:10], target_smiles=target)
for brief in briefs:
    print(brief["role"], brief["smiles"], brief["stock_status"])
    print("query:", brief["pubchem_url"])
    print("structure:", build_molecule_structure_src(brief["smiles"]))
```

Use `host.web_fetch(brief["pubchem_url"])` or `host.web_search(...)` for the
important molecules, then summarize:

- what the molecule is in this route (target, intermediate, stock precursor, or
  unresolved precursor)
- whether it is in the selected stock
- what external lookup confirms or fails to confirm
- why it matters to the proposed disconnection

For a concise chemistry narrative, always ask the configured conversation LLM to
annotate the displayed molecules and reactions before rendering the dashboard:

```python
annotations = annotate_routes_with_llm(ranked[:8], llm=host.llm, target_smiles=target)
```

`annotate_routes_with_llm(...)` tolerates the usual conversation-model reply
shapes: bare JSON, a fenced block, or JSON wrapped in prose. If no JSON can be
recovered it warns and returns `{}`, and the dashboard falls back to its own
"Route Planning Readout" instead of failing the render. Check the warning if a
Route X card shows the readout rather than "LLM Route Analysis".

The LLM must return a human-readable `reaction_type`, detailed reaction
description, mechanistic rationale, bond changes, plausible conditions,
expected yield range, yield rationale, selectivity risks, safety notes, and a
validation plan for each reaction key. It must also return route-level
annotations for each displayed route: `route_strategy`, `key_disconnections`,
`reaction_sequence`, `conditions_strategy`, `yield_outlook`, `route_risks`,
`recommended_next_steps`, and `chemist_verdict`. `render_route_tree_html(...,
llm=host.llm)` calls the configured conversation LLM before rendering and embeds
these annotations directly into each Route X card and the interactive graph
detail panel. Treat conditions and yields as hypotheses unless the route export
or literature lookup provides experimental evidence. If AiZynthFinder reports a
backend class such as `0.0 Unrecognized`, do not repeat it as the final reaction
type. Use the SMARTS/mapped reaction, policy probability, and LLM/literature
annotation to explain the disconnection.

For an industrial decision workflow, attach source-backed evidence separately
from LLM annotations. Retrieve the stable keys first, then pass only evidence
records that identify their source and verification state:

```python
reaction_briefs = collect_reaction_briefs(ranked[:10])
for reaction in reaction_briefs:
    print(reaction["reaction_key"], reaction["template"])

reaction_evidence = {
    "reactions": {
        reaction_briefs[0]["reaction_key"]: [
            {
                "source_type": "literature",  # literature, patent, internal_eln, reaction_database, vendor
                "title": "Verified source title",
                "identifier": "DOI, patent, ELN run, or database record",
                "url": "https://example.org/record",
                "match_level": "exact_substrate",  # exact_substrate, close_analog, reaction_class
                "verified": True,
                "conditions": {"solvent": "...", "temperature": "..."},
                "yield_range": "82-88%",
                "risk_flags": ["exothermic quench"],
                "notes": "Reviewer-confirmed record.",
                "retrieved_at": "2026-07-14",
            }
        ]
    }
}
```

Pass `reaction_evidence=reaction_evidence` to `render_route_tree_html(...)`.
Each route then includes a Step Evidence card, and the same evidence appears in
the selected reaction node of the knowledge graph. The displayed coverage score
is a retrieval-completeness heuristic, not a probability of experimental
success. LLM-generated conditions and yields never become evidence records
automatically.

### Source retrieval with OpenAI4S skills

Use `OpenAI4SLLMReactionEvidenceProvider` when a live evidence sweep is
appropriate. It explicitly composes the configured conversation model with the
existing `host.web_search` and optional `host.web_fetch` skills: the LLM drafts
reaction-aware search queries, OpenAI4S retrieves the pages, and a second LLM
pass can select **only** returned source IDs. This is intentionally not an
implicit tool-call: `host.llm` is a text-completion API, so the provider keeps
all network activity observable and auditable.

If the `literature-review` skill is active in the same kernel, pass its
`verify_dois` helper as shown below. It verifies whether a DOI resolves, but it
does not prove that the paper supports the proposed substrate scope. The output
is therefore marked as a *retrieved source candidate*, receives capped coverage,
and never becomes verified exact-substrate precedent without reviewer or ELN
confirmation.

```python
# Load/use the literature-review skill first when DOI verification is needed;
# its kernel makes verify_dois available in the active science environment.
provider = OpenAI4SLLMReactionEvidenceProvider(
    llm=host.llm,
    search=host.web_search,
    fetch=host.web_fetch,
    doi_verifier=verify_dois,  # optional; omit when literature-review is not active
    max_reactions=10,
    max_queries_per_reaction=2,
    results_per_query=5,
)
reaction_evidence = collect_reaction_evidence(ranked[:10], [provider])
```

The provider never accepts an LLM-supplied URL or title: every card is built
from a retrieved `host.web_search` result. It also never auto-populates
conditions, yields, or `verified=True`. A chemist should promote a reviewed
candidate by adding a source record with explicit scope, conditions, yield, and
verification status before using it in execution scoring.

### Phase 4 — Visualize and report

The HTML artifact is a self-contained dashboard: KPI summary, ranked route
table, an interactive retrosynthesis knowledge graph, molecule briefs,
color-coded SVG route trees with molecule structure thumbnails, stock precursor
chips, and a text outline for audit/debugging. The knowledge graph merges
identical molecule nodes across displayed routes, preserves AND-OR route
semantics, and supports pan, zoom, collapse/expand, node selection, neighbor
highlighting, molecule structure display, LLM reaction-type display, backend
class audit, policy probability, template details, and rich reaction
interpretation with proposed conditions, yield caveats, risk notes, and
validation steps.

AiZynthFinder's normal JSON export contains solved/top route trees, not
necessarily every internal MCTS visit. The dashboard therefore visualizes the
exported route hypotheses as a merged knowledge graph. If the user asks for the
complete internal search tree, export an AiZynthFinder checkpoint/search graph
from the backend and state which graph source was used.

Molecule structures are rendered with RDKit SVG when RDKit is installed in the
kernel; otherwise the dashboard uses transparent local SVG placeholders. Do not
use PubChem PNGs as in-dashboard molecule images; PubChem remains a query link
for lookup only. (`build_pubchem_structure_image_url(...)` is still exported for
external reports, but must not be embedded in the dashboard.) Reaction conditions are not predicted by AiZynthFinder route
planning; label them as not predicted unless an external condition-prediction,
literature lookup, or LLM hypothesis with explicit uncertainty provides
evidence.

```python
html = render_route_tree_html(
    ranked,
    target_smiles=target,
    max_routes=10,
    llm=host.llm,
    reaction_evidence=reaction_evidence,
)
report = build_markdown_report(ranked, target_smiles=target)

host.write_file("aspirin_retrosynthesis.html", html)
host.write_file("aspirin_retrosynthesis_report.md", report)
```

## Example dashboard

This skill includes an example dashboard generated from an aspirin route export:

```text
skills/retrosynthesis_planning/examples/aspirin_retrosynthesis.html
```

It is regenerated from committed source data rather than hand-edited:

```bash
uv run python skills/retrosynthesis_planning/examples/build_example.py
```

`aspirin_routes.json` holds the route trees and `aspirin_annotations.json` the
deterministic demonstration annotations. The committed dashboard is rendered
with RDKit depictions. To regenerate it with the same molecule rendering, first
install the science extra and then run the build command above.

Open it directly in a browser, or serve the skill directory locally:

```bash
python3 -m http.server 9876 --bind 127.0.0.1 -d skills/retrosynthesis_planning
```

Then visit:

```text
http://127.0.0.1:9876/examples/aspirin_retrosynthesis.html
```

The aspirin example demonstrates:

- Route X cards with embedded route-level LLM analysis
- an interactive retrosynthesis knowledge graph with merged molecule and
  reaction nodes
- reaction detail panels with LLM reaction type, proposed conditions, yield
  caveats, selectivity risks, safety notes, and validation steps
- Molecule Briefs using RDKit/local SVG visualization rather than PubChem PNGs
- explicit uncertainty around backend route predictions and LLM-generated
  chemistry interpretations

The example annotations are deterministic demonstration text, not experimental
evidence for aspirin manufacturing conditions.

## Recipe: analyze an existing JSON export

```python
routes = load_aizynth_routes("routes.json")
ranked = rank_routes(normalize_routes(routes))
target = (
    ranked[0]["tree"].get("smiles")
    if ranked and isinstance(ranked[0].get("tree"), dict)
    else None
)
briefs = collect_molecule_briefs(ranked[:10], target_smiles=target)

for route in ranked[:5]:
    print(route["rank"], route["solved"], route["score"], route["steps"])
    print("starting materials:", ", ".join(route["starting_materials"]))

for brief in briefs:
    print(brief["role"], brief["smiles"], brief["pubchem_url"])

host.write_file(
    "routes.html",
    render_route_tree_html(ranked, max_routes=10, target_smiles=target, llm=host.llm),
)
host.write_file("routes_report.md", build_markdown_report(ranked))
```

## Output layout

```
<workdir>/
├── <target>_routes.json                 # raw AiZynthFinder output
├── <target>_retrosynthesis.html         # visual dashboard
└── <target>_retrosynthesis_report.md    # route rationale + molecule briefs
```

## Analyst checklist

For each recommended route, explicitly discuss:

- whether the route reaches purchasable or stock precursors
- route length and branch complexity
- the role of every target/intermediate/terminal molecule shown
- PubChem/vendor/literature lookup status for the target and terminal precursors
- high-risk disconnections, functional-group compatibility, and stereochemistry
- missing reaction conditions, yields, purification, or safety information
- what a synthetic chemist should verify before experimental execution

Do not claim that a predicted route is experimentally validated unless the data
source explicitly contains experimental evidence.

## Visual QA

Before submitting the final answer:

- open the HTML dashboard and check that the route tree is non-empty
- verify that the route-ranking table and molecule briefs agree with the JSON
- interact with the knowledge-graph panel: expand/collapse, click molecule
  nodes, click reaction nodes, and confirm the detail panel explains reaction class,
  template, policy probability, and condition caveat
- verify that molecular structures, not only SMILES strings, appear in route
  nodes and molecule-brief cards
- confirm that stock precursors and unresolved precursors use distinct colors
- ensure labels fit within SVG nodes and do not obscure neighboring nodes
- state any unresolved molecule lookup gaps in the Markdown report
