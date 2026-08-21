# GPTomics bioSkills bundle

[中文说明](README_zh.md)

This directory vendors all 561 operational bioinformatics recipes from the
archived [GPTomics/bioSkills](https://github.com/GPTomics/bioSkills) repository
at commit
[`d91ed3d563019e649dc854c56ccd62551359488a`](https://github.com/GPTomics/bioSkills/tree/d91ed3d563019e649dc854c56ccd62551359488a).
The upstream work is MIT-licensed; its exact license text is preserved in
`LICENSE`. OpenAI4S treats the collection as a pinned, read-only third-party
resource, not as 561 independently maintained OpenAI4S implementations.

## What is included

The collection covers 63 categories, including sequence and alignment I/O,
variant calling, expression and epigenomics, single-cell and spatial analysis,
structural biology, proteomics, metabolomics, microbiome analysis, population
genetics, clinical biostatistics, visualization, reporting, and workflow
management. `COLLECTION.json` is what makes this directory one catalog entry rather than
561 peers: it carries the collection id and the single line the system prompt
shows for it. `MANIFEST.json` is the authoritative inventory: it records every
public Skill name, upstream path, converted directory, source commit, and
SHA-256/size for every imported payload file.

The upstream `clawhub-installer` meta-Skill is deliberately excluded. It is an
installer for another agent platform, not a scientific recipe.

## OpenAI4S conversion

| Upstream layout | Bundled layout |
| --- | --- |
| `<category>/<skill>/SKILL.md` | `bio-<category>-<skill>/SKILL.md` |
| `examples/` | `scripts/` |
| `usage-guide.md` | `references/usage-guide.md` |
| top-level `tool_type` and `primary_tool` | nested under `metadata` |
| no distribution provenance | `origin: openai4s`, category, repository, commit, and license metadata |

`origin: openai4s` identifies the read-only distribution boundary; authorship
and licensing remain GPTomics/MIT as recorded beside it. Command snippets are
also normalized to this repository's relay-safety convention: `python -m` /
`python -c` snippets use `python3`, silent `curl` flag spellings gain fail-fast
flags, and the two download-to-shell Nextflow examples use the documented
bioconda package route. The rules applied are recorded in `MANIFEST.json`.
This is spelling normalization, not an audit: bare `python script.py`, `wget`,
`pip install git+`, `install_github(...)`, `docker run` and `sudo apt install`
instructions survive untouched. If executed, they remain subject to shell
approval, the OS sandbox, and the configured raw-network posture; raw network
calls do not acquire Host egress or SSRF enforcement merely by appearing in a
Skill.

## Discovery and context cost

Every recipe remains individually available through `list_skills`,
`search_skills`, and `load_skill`. The always-on system prompt advertises the
collection as one summary line instead of injecting 561 descriptions (about
109,000 tokens upstream's installer estimated). Search still indexes each full
recipe. The aggregate prompt explicitly tells the agent to search before any
bioinformatics pipeline, translating the method, tool, data type, and workflow
to English keywords when the user's query is in another language. An explicitly
scoped specialist receives the same collapsed line, counting only the
recipes its allowlist permits.

The per-Skill directories are generated third-party assets. They are excluded
from the repository's per-directory bilingual-README rule; this README pair,
`LICENSE`, and `MANIFEST.json` document their owning boundary. The manifest is
verified rather than merely asserted: `python3 scripts/import_bioskills.py
--check` re-derives every recorded hash against the tree, and the offline test
suite runs it.

## Dependencies and execution safety

These files are recipes, not preinstalled software. Across the full collection
they mention hundreds of optional Python/R packages, command-line programs,
databases, containers, references, and external services. Importing the
catalog therefore does not add dependencies to OpenAI4S's zero-dependency core
or claim that every recipe is ready in one environment. Each recipe requires a
local version/PATH check before execution; GPU, network, credentials, licensed
software, or clinical-data access still needs the normal OpenAI4S approvals and
environment setup.

The imported scripts are examples and are never auto-executed by discovery or
search. If an agent chooses to run one, the normal kernel/shell approval and OS
sandbox posture still apply to that execution. Many vendored examples make
raw network calls with `requests`, `urllib`, `curl`, or `wget`; none of those
calls pass through the Host egress allowlist or SSRF checks. The conservative
cross-language scan in `tests/test_egress_surface.py` inventories explicit
Python/R/shell/recipe network patterns and fails when that lower bound moves;
it is detection, not runtime enforcement, and third-party libraries or
scientific CLIs may still hide additional egress. Review or adapt a recipe
before execution and prefer `host.web_fetch` / `host.web_download` for network
access. Scientific claims in a recipe are guidance, not a validated result for
a new dataset.

## Reproducing the import

Maintainers use `scripts/import_bioskills.py` with a local checkout at the exact
commit above and an empty destination. The importer is offline and fails closed
on a different commit, a count other than 561, duplicate declared names, or a
non-empty target. A future upstream refresh must be reviewed as a new vendor
update: change the pin intentionally, review the diff and licenses, regenerate
the manifest, and rerun the Skill, packaging, secret-scan, and documentation
gates.
