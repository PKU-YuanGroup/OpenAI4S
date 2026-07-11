# The web app

`openai4s serve` starts a pure-stdlib scientific workbench at
`http://127.0.0.1:8760/`: `http.server`, a hand-rolled WebSocket, and static
HTML/CSS/JavaScript served directly from the working tree.

## Available now

- **Projects and sessions** — folder/date grouping, deep links, session search,
  command palette, rename/delete, and background turns that survive a closed
  tab.
- **Live turns** — prose, semantic steps, permission pauses, plans, Cell output,
  and artifacts stream over WebSocket. Reopening an in-flight session replays
  the bounded current-turn buffer; completed history reloads over REST.
- **Versioned artifacts** — file writes become immutable versions with
  provenance, environment snapshots, lineage, annotations, priority, edit,
  rename, restore, and artifact/project ZIP download. The current viewers cover
  images, CSV/TSV tables, Markdown/text, HTML/PDF previews, and 3D molecular
  structures through vendored 3Dmol.
- **Read-only Notebook by default** — stable Cell IDs project Python/R source,
  stdout/stderr, errors, figures, files, and retry revisions. Failed older
  revisions remain collapsed and read-only; a running Cell is updated in place.
- **Explicit developer REPL** — `OPENAI4S_NOTEBOOK_REPL=1` enables multiline
  Python/R input. Shift+Enter appends a new Cell; it never edits an executed
  Cell. User Cells and Agent turns share the same per-session FIFO execution
  coordinator.
- **Exact ownership and cancellation** — runtime events expose the active
  execution owner and queue positions. Cancel/interrupt requests send the exact
  `execution_id` plus `owner.kind` and `owner.id`. The Notebook Stop control
  selects only an active or queued `user_repl` ticket and fails closed when none
  exists, so it does not fall back to interrupting the Agent. Composer/session
  cancel targets the currently displayed exact owner.
- **Action Timeline workbench surface** — the right dock has safe cards for
  native tools (including delegation calls), Python/R Cells, domain mutations,
  and finalization, plus a separate recovery-status card and
  Branch/Context/Sandbox containers. Permission waits still use their existing
  interactive prompt rather than a persisted Timeline card. The frontend
  allowlists fields and never renders raw arguments, provider wire state, or
  tokens.
- **Customize and research UX** — model profiles, Skills/Specialists,
  connectors, compute, network, memory, permission rules, plan/explore modes,
  voice dictation, uploads/paste/drag-drop, annotations, and bilingual 中文/EN.

## Notebook lifecycle and truthfulness

Python and R are lazy, independent persistent slots. A metadata-only or
tool-only turn does not start either kernel. The Notebook shows generation,
branch/revision placeholders, owner/queue, and states such as
`Live / Busy / Ended · view only / Restoring / Partial / Failed` when the
corresponding projections are available.

Stopping/restarting a kernel preserves messages, Cell history, workspace files,
and Artifact versions, but not arbitrary in-memory objects. A daemon restart
must not be described as namespace recovery unless the verified recovery
pipeline actually rebuilds and validates that state.

## Session-domain feature status

The Gateway now exposes the session-domain read/write adapters, while several
product affordances remain intentionally partial:

| Feature | Status |
|---|---|
| Action Ledger and safe Timeline projection | Backend, redacted max-500 latest/older/newer REST windows, and UI cards are implemented. Completed recent history reloads from REST; there is no separate durable per-action WS backlog or visible older-history paging control. |
| Checkpoint / branch / revert preview | Content-addressed snapshots and public checkpoint/fork/preview/apply/undo routes are implemented. The UI exposes checkpoint creation and revert preview/apply. Fork accepts checkpoints only; fork-from-cell and visible fork/undo/branch-navigation controls are still absent. |
| Recovery Journal and verified recovery pipeline | Status/actions are public with honest active/partial/failed states. No Gateway action currently runs the full recovery pipeline, so reopen cannot promise complete namespace recovery. |
| Python/R `.ipynb` export | Deterministic language export/ZIP route and a stable bundle ZIP download in the Notebook header and provenance execution view are implemented. Separate Python/R single-notebook selectors remain API-only. |
| Scientific renderer registry | Safe catalog and version-bound descriptor routes are implemented; dedicated 2D chemistry, genome, sequence/MSA, and LaTeX UI components are not yet wired. |
| Context composition / security panels | Safe session-specific REST projections and frontend containers are implemented. Sandbox status aggregates the Python/R workers that actually started and takes the weaker claim when they differ; it remains `not_started` only until neither language has run a self-test. |

The frontend deliberately treats absent routes as unavailable: controls are
disabled or show an empty state rather than mutating history or pretending a
backend action succeeded.

## Demo session (seeded on first boot)

On first boot the app seeds a NIF3/DUF34 protein-family analysis that calls the
real UniProt and RCSB PDB APIs plus a bundled MCP connector, running six
deterministic Notebook Cells without an LLM key:

1. UniProt sequence retrieval.
2. A bundled MCP connector call.
3. A Kyte-Doolittle hydropathy plot.
4. `family_biochemistry.csv`.
5. An RCSB search and `nif3_structure.pdb` download.
6. `nif3_report.md`.

Network-unavailable steps are skipped and reported; demo data is never
fabricated.
