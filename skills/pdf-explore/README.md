# PDF Explore Skill

Working through a PDF too big to keep in conversation context. Pages attached with `read_file` are dropped again after one turn, so a multi-section answer turns into re-reading the same ranges over and over; this Skill parses the document once in the Python kernel instead, and the text stays put. Find the sections you need, pull what you need out of them, leave the rest on disk. The sidecar caches the parsed pages and fans bounded per-page `host.llm` calls out over them. What the model sees is what the text layer or the OCR pass could read, and no better.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install pdf-explore --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install pdf-explore`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/pdf-explore
python3 -m zipfile -c pdf-explore.zip pdf-explore
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/pdf-explore/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Picks the helper for the job: outline first, then a relevance scan, a page map, structured extraction, or a high-dpi crop when you need to read values off a figure. Also covers the scanned-PDF mode, the page cache, what a fan-out costs, and when reading the page directly is simpler than any of this. |
| [`kernel.py`](kernel.py) | Optional sidecar, every name `pdf_`-prefixed since it shares the kernel's `__main__`. `pdf_resolve` turns a path or an Artifact id into a local file and `pdf_pages` parses and caches per-page text and page renders. On top of those, `pdf_outline` builds a table of contents (from the PDF's own outline when it has one, from the model when it does not), `pdf_scan` ranks pages against a query, `pdf_map` summarizes every page, and `pdf_extract` pulls a JSON-Schema record out of each page, all through parallel `host.llm` calls under a batch cap. Page text is untrusted, so every prompt is built with per-call nonce delimiters, tag-shaped page text is neutralized before it is interpolated, and long pages are truncated with an explicit marker. `pdf_scan_cost` adds up the token usage afterwards. |

The optional PDF and OCR libraries must be present in the active kernel. Extracted text drops the visual structure of the page, so any label or value you are going to rely on should be checked against the rendered page or a crop of it.
