# Skills — capabilities as code, not schemas

A Skill is a directory under [`skills/`](../skills):

```
skills/example_stats/
    SKILL.md      recipe-centric doc (code examples, not a JSON schema)
    kernel.py     importable sidecar module (helper functions)
```

Skills are consumed by **writing code**. The loader surfaces each `SKILL.md` to the model via *progressive disclosure* (only a one-line summary up front; the full doc is fetched on demand with `host.search_skills(query)`), the kernel bootstrap finder binds each permitted Skill package to its exact discovered directory, and the agent runs e.g. `from example_stats.kernel import summary`. A Skill's capability lands as **callable Python inside the kernel** — the same principle as the core paradigm, not another tool schema.

## Bundled Skills (603)

The catalog has two maintenance tiers: 42 curated OpenAI4S Skills and a pinned,
read-only import of all 561 MIT-licensed
[GPTomics/bioSkills](../skills/bioskills/) recipes. Every imported recipe is
individually searchable and loadable, but the system prompt represents the
collection with one aggregate line rather than 561 descriptions.

A **collection** is any directory under `skills/` that holds a
`COLLECTION.json` (`id`, plus the `prompt_line` it wants in the system prompt,
where `{count}` is the number of members the caller can see) and keeps its
member Skills one level lower. The loader discovers collections from that
marker alone — no directory name, id, or retrieval policy lives in
`skills_loader/loader.py` — and every surface reads the same object:
`system_context` prints one line per collection, `list_skills` returns curated
names plus one entry per collection (pass `collection=<id>` with `offset=0`,
then follow every returned `next_offset`, to enumerate one), and the Web catalog
renders it as a single collapsed row. The bundle's
source commit, conversion rules, license, complete inventory, and per-file
hashes live at its linked boundary; importing it installs no scientific
packages and does not imply that every optional tool is ready locally.

### Curated OpenAI4S Skills (42)

| category | Skills |
|---|---|
| **Structure prediction** (GPU) | `alphafold2` · `openfold3` · `boltz` · `chai1` · `esmfold2` |
| **Sequence / omics / docking** (GPU) | `fair-esm2` · `evo2` · `borzoi` · `scgpt` · `scvi-tools` · `diffdock` |
| **Protein design** (GPU) | `rfdiffusion` · `proteinmpnn` · `ligandmpnn` · `solublempnn` · `protein-design-mcp` |
| **Chemistry / materials** (GPU) | `catalyst_sar_screening` |
| **Reaction chemistry** | `reaction-atom-mapping` · `reaction-condition-recommendation` · `reaction-forward-prediction` · `reaction-yield-estimation` · `single-step-retrosynthesis` |
| **Research workflow** | `literature-review` · `pdf-explore` · `paper-narrative` · `figure-composer` · `figure-style` · `indication-dossier` · `evidence-walkthrough` · `retrosynthesis_planning` · `mineral_spectra_analysis` · `admet_genetic` · `protein-mutation-enhancement` |
| **ML methodology / benchmarks** | `plan-ml-experiment` · `audit-dataset` · `evaluate-model` · `bioprobench` |
| **Platform** | `remote-compute-nvidia` · `remote-compute-ssh` · `using-model-endpoint` · `volcengine-datapro` |

`example_stats` is the reference example Skill (pure-stdlib descriptive-statistics helpers).

`volcengine-datapro` is intentionally limited to discovering and calling the
configured `dataPro_search` MCP tool. Discovery is not an authentication check;
only an integer `raw.structuredContent.code` of zero from a real search call is
treated as usable.

## Writing a Skill

1. Create `skills/<name>/SKILL.md` with a short YAML frontmatter (`name`, `description`, optional `origin`, `category`, `requirements: [gpu]`) followed by a body of **runnable code examples**.
2. Optionally add a `kernel.py` with importable helper functions.
   Skill directories are loaded as pinned namespace packages: executable
   `__init__.py` files are intentionally not run. Put all executable sidecar
   initialization in `kernel.py`, whose exact bytes are hashed and captured.
3. That's it — the loader discovers it on the next run and surfaces its one-line summary to the agent. Bundled skills (`origin: openai4s`) are read-only; skills you author or import are editable from the UI (**Customize → Skills**).

GPU/model Skills (`requirements: [gpu]`) run their heavy step on a remote GPU through [`host.compute`](compute.md); everything else runs directly in the kernel.

## Writable Skill versions and rollback

Bundled `openai4s` Skills remain authoritative and read-only. Writable Skills
have two explicit distribution scopes:

- `personal` lives under `<data_dir>/user-skills` and is available to every
  project unless capability policy disables it;
- `project` lives in a project-identity-isolated overlay and is discovered only
  by a `SkillLoader` scoped to that project. A project Skill overrides a
  same-named personal Skill, but neither can shadow a bundled Skill.

`SkillVersionService` is the narrow stdlib API for installing, upgrading,
publishing, listing history, and rolling back these packages. Every operation
captures `SKILL.md`, the exact `kernel.py` bytes, and bounded resource files.
SQLite stores immutable SHA-256-addressed blobs, an immutable canonical
manifest, and append-only installation events. The active version is changed
with compare-and-swap semantics; the runtime directory is staged and verified
before replacement, and a failed pointer update restores the prior directory.
Newer versions are retained after rollback or deletion.

```python
from openai4s.skills_loader import SkillVersionService

versions = SkillVersionService()
installed = versions.install(
    "assay-qc",
    {
        "SKILL.md": "---\nname: assay-qc\norigin: personal\n---\nQC recipe\n",
        "kernel.py": "def accepted(x): return x >= 0.9\n",
    },
)
history = versions.history("assay-qc")
versions.rollback("assay-qc", installed["version_id"])
```

For project-local content, pass `scope="project", project_id="..."` to the
same methods and construct the runtime loader with the matching `project_id`.
Package ingestion rejects traversal paths, symlinks, oversized files/packages,
invalid UTF-8 documents, trusted-origin claims, and (for install/publish) a
`kernel.py` that fails the compile gate. Draft editors may retain a broken
sidecar as a versioned draft, but publishing still fails closed until it
compiles.

The same lifecycle is available through three named JSON control-tool classes:
`skill_status`, `skill_history`, and `rollback_skill_version`. Status/history
are read-only; rollback declares a runtime mutation, requires approval, is
audited by `HostDispatcher`, and can address only `personal` or the dispatcher's
current `project` scope. Python cells expose the matching
`host.skills.status(...)`, `host.skills.history(...)`, and
`host.skills.rollback(...)` methods.

Customize uses narrow HTTP routes. Personal history/rollback lives at
`/api/skills/<name>/versions` and `/api/skills/<name>/rollback`; project-local
state uses `/api/projects/<project_id>/skills/<name>/versions` and
`.../rollback`. Project IDs are path-scoped and checked against the Store;
bundled Skills never expose a rollback action.
