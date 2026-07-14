---
title: Skills
description: Discovery, execution, ownership, and versioning of OpenAI4S Skills.
outline: deep
status: current
audience: [contributors, operators, users]
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Skills — capabilities as code, not schemas

> Verified against repository revision `a92e736` on 2026-07-14. Inventory counts
> are generated from the bundled directories under `skills/`; they are not a
> compatibility promise.

A Skill is a directory under
[`skills/`](https://github.com/PKU-YuanGroup/OpenAI4S/tree/main/skills):

```
skills/example_stats/
    SKILL.md      recipe-centric doc (code examples, not a JSON schema)
    kernel.py     importable sidecar module (helper functions)
```

Skills are consumed by **writing code**. The loader surfaces each `SKILL.md` to the model via *progressive disclosure* (only a one-line summary up front; the full doc is fetched on demand with `host.search_skills(query)`), the kernel adds `skills/` to `sys.path`, and the agent runs e.g. `from example_stats.kernel import summary`. A Skill's capability lands as **callable Python inside the kernel** — the same principle as the core paradigm, not another tool schema.

## Bundled Skills (32)

| category | Skills |
|---|---|
| **Structure prediction** (GPU) | `alphafold2` · `openfold3` · `boltz` · `chai1` · `esmfold2` |
| **Sequence / omics / docking** (GPU) | `fair-esm2` · `evo2` · `borzoi` · `scgpt` · `scvi-tools` · `diffdock` |
| **Protein design** (GPU) | `proteinmpnn` · `ligandmpnn` · `solublempnn` |
| **Research workflow** | `literature-review` · `pdf-explore` · `paper-narrative` · `figure-composer` · `figure-style` · `indication-dossier` · `retrosynthesis_planning` · `mineral_spectra_analysis` · `admet_genetic` · `protein-mutation-enhancement` · `catalyst_sar_screening` |
| **Data and model workflow** | `audit-dataset` · `evaluate-model` · `plan-ml-experiment` |
| **Platform** | `remote-compute-nvidia` · `remote-compute-ssh` · `using-model-endpoint` |

`example_stats` is the reference example Skill (pure-stdlib descriptive-statistics helpers).

## Writing a Skill

1. Create `skills/<name>/SKILL.md` with a short YAML frontmatter (`name`, `description`, optional `origin`, `category`, `requirements: [gpu]`) followed by a body of **runnable code examples**.
2. Optionally add a `kernel.py` with importable helper functions.
3. That's it — the loader discovers it on the next run and surfaces its one-line summary to the agent. Bundled skills (`origin: openai4s`) are read-only; skills you author or import are editable from the UI (**Customize → Skills**).

GPU/model Skills (`requirements: [gpu]`) describe how to request a compatible
remote provider through [`host.compute`](compute.md). Whether a recipe can run
is still conditional on the selected provider, its implementation status, and
the environment it exposes. Skills without that requirement normally run in
the selected persistent kernel environment.

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
