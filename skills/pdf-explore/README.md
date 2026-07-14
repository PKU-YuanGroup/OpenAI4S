# PDF Explore Skill

This progressive-disclosure Skill supports multi-section navigation and extraction from PDFs too large to keep in conversation context. Its sidecar parses/caches pages and can fan out bounded Host LLM calls; results remain dependent on parser/OCR quality and require visual verification for figures or layouts.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Chooses among page parsing, outline, relevance scan, map, structured extraction, detailed figure crops, OCR mode, caching, and cost-aware fan-out; also states when a direct page read is simpler. |
| [`kernel.py`](kernel.py) | Optional sidecar: resolves local path/artifact IDs; parses and caches per-page text/images with `pdf_pages`; constructs nonce-guarded prompts; maps/scans/extracts pages through parallel `host.llm`; builds an outline; truncates safely; and aggregates usage with `pdf_scan_cost`. |

## Direct subdirectories

None.

Optional PDF libraries/OCR tools must exist in the active kernel. Extracted text can omit visual structure; critical labels and values should be checked against rendered pages/crops.
