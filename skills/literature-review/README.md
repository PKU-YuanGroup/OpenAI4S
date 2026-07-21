# Literature Review Skill

Literature work, evidence first: retrieve, then write. A DOI you emit either resolves to a real paper that says what you claim or it is a fabrication, and the difference takes seconds to check — so the recipe puts verification in the tool trace rather than in a sentence of the reply, even for the papers you know cold. The sidecar queries the public scholarly APIs and checks identifiers, but what an index covers, whether a paper is flagged retracted, and whether you can reach the article at all are external conditions that change over time.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Reading the request for what it actually asks, grounding every claim in a retrieved source, verifying DOIs instead of recalling them, handling retractions and the honest "no such paper" answer, synthesizing by comparison rather than summary, calibrating confidence to the evidence, placing citations inline, and the prose checks that come before saving. |
| [`kernel.py`](kernel.py) | Optional sidecar. `lr_sdk` returns a `host` handle that survives a rebind of the name in the kernel, and `litrev_contact` fetches the user's email for the polite-pool User-Agent when there is one. `litrev_get` and `litrev_head` are the bounded HTTP underneath everything else (one retry on a 429, `None` on any error), with `quote_doi_path` encoding a DOI into a request path and `crossref_year` reading the year back out. Above them: `verify_dois`, `crossref_lookup` and `search_openalex` for resolution and search, `expand_citations` for one step backward and forward on the citation graph, and `extract_dois` plus `html_decode` for pulling DOIs back out of prose. `style_pass` is a regex lint over the finished draft, deliberately with no LLM call in it: the draft quotes retrieved third-party text, and a free-text fix hint the agent is told to apply would be an injection channel. |

Lookup success is not full-text verification, and lookup failure is not evidence that a paper does not exist. Final claims must remain grounded in retrieved primary sources.
