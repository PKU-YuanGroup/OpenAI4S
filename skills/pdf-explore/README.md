# PDF Explore Skill

Working through a PDF too big to keep in conversation context. Pages attached with `read_file` are dropped again after one turn, so a multi-section answer turns into re-reading the same ranges over and over; this Skill parses the document once in the Python kernel instead, and the text stays put. Find the sections you need, pull what you need out of them, leave the rest on disk. The sidecar caches the parsed pages and fans bounded per-page `host.llm` calls out over them. What the model sees is what the text layer or the OCR pass could read, and no better.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Picks the helper for the job: outline first, then a relevance scan, a page map, structured extraction, or a high-dpi crop when you need to read values off a figure. Also covers the scanned-PDF mode, the page cache, what a fan-out costs, and when reading the page directly is simpler than any of this. |
| [`kernel.py`](kernel.py) | Optional sidecar, every name `pdf_`-prefixed since it shares the kernel's `__main__`. `pdf_resolve` turns a path or an Artifact id into a local file and `pdf_pages` parses and caches per-page text and page renders. On top of those, `pdf_outline` builds a table of contents (from the PDF's own outline when it has one, from the model when it does not), `pdf_scan` ranks pages against a query, `pdf_map` summarizes every page, and `pdf_extract` pulls a JSON-Schema record out of each page, all through parallel `host.llm` calls under a batch cap. Page text is untrusted, so every prompt is built with per-call nonce delimiters, tag-shaped page text is neutralized before it is interpolated, and long pages are truncated with an explicit marker. `pdf_scan_cost` adds up the token usage afterwards. |

The optional PDF and OCR libraries must be present in the active kernel. Extracted text drops the visual structure of the page, so any label or value you are going to rely on should be checked against the rendered page or a crop of it.
