# Scientific database connectors

OpenAI4S exposes a thin, schema-normalized layer over common public scientific
APIs. It keeps the tool surface flat while letting Python cells combine real
records without scraping provider-specific pages.

## Supported databases

| id | source | disciplines | normalized record |
|---|---|---|---|
| `uniprot` | UniProtKB REST | biology | protein accession, name, genes, organism, length |
| `pdb` | RCSB PDB Search | biology, chemistry | structure id and relevance score |
| `ensembl` | Ensembl REST | biology | stable genomic feature from an exact symbol |
| `chembl` | ChEMBL REST | chemistry, biology | molecule identity, properties, SMILES, max phase |
| `pubchem` | PubChem PUG REST | chemistry | CID and computed compound properties |
| `arxiv` | arXiv Atom API | ML, physics, literature | preprint metadata, authors, categories, abstract |
| `openalex` | OpenAlex Works API | multidisciplinary literature | work, DOI, authors, concepts, citations, OA state |

The public model-facing surface remains two tools:

- `science_list_dbs(domain?)` lists sources, query hints, filters, and the
  normalized result contract.
- `science_search(database, query, limit?, cursor?, filters?, timeout?)`
  searches one source and returns typed records.

Every result uses the same envelope:

```json
{
  "database": "uniprot",
  "source": "UniProtKB",
  "query": "insulin",
  "count": 1,
  "results": [
    {
      "id": "P01308",
      "title": "Insulin",
      "url": "https://www.uniprot.org/uniprotkb/P01308",
      "type": "protein",
      "attributes": {"gene_names": ["INS"], "taxon_id": 9606}
    }
  ],
  "next_cursor": null,
  "request_url": "https://rest.uniprot.org/..."
}
```

## Code-cell composition

The same two operations are available through the injected singleton. This is
the intended path when several pages must be joined, filtered, or analyzed in
one persistent cell:

```python
sources = host.science.list_databases("chemistry")
aspirin = host.science.search("pubchem", "aspirin", limit=5)
papers = host.science.search(
    "openalex",
    "aspirin pharmacogenomics",
    limit=25,
    filters={"year_from": 2022, "work_type": "article"},
)
rows = [
    {"cid": aspirin["results"][0]["id"], **paper}
    for paper in papers["results"]
]
```

Source-specific filters are intentionally bounded:

- `organism_id` for UniProt;
- `species` for an exact Ensembl gene-symbol lookup (default `homo_sapiens`);
- `year_from`, `year_to`, and `work_type` for OpenAlex.

arXiv and OpenAlex return cursors. Other first-batch connectors are bounded
single-page searches. PubChem uses its exact name/synonym endpoint rather than
claiming fuzzy text-search semantics.

## Safety and failure behavior

Connectors construct URLs from fixed HTTPS endpoints; callers cannot supply a
host or arbitrary URL. Requests use the existing Web fetch path, so the global
network switch, per-redirect SSRF checks, egress allowlist, response-size cap,
timeouts, permission audit, and prompt-injection annotation remain active.
Network/API/schema failures return the normal single-key `{ "error": "..." }`
soft-fail shape at the tool boundary. No connector adds a runtime dependency to
the stdlib-only core, and all default tests use captured synthetic API payloads
without network access.

## Doubao Search Custom

Doubao Search Custom is the primary managed web-search option. OpenAI4S calls
Volcengine's fixed search API through a stdlib-only client and normalizes web
hits to the exact `{title,url,snippet}` envelope. It does not
install the Ark CLI Skills catalog or a local third-party MCP package.

The client resolves the same Agent Plan Key used by Ark and DataPro from
SecretBroker immediately before each outbound request. The credential is sent
only as the upstream Authorization value and is never returned by the config
route, search response, DOM, or diagnostic text. Saving it from Customize →
Network therefore authorizes both managed products once; an active Ark API key
is reused only when the selected provider is Ark.

The dedicated `POST /doubao-search/search` product check has no fallback. It
does not call Tavily, DuckDuckGo, Bing, Mojeek, or an identifier resolver after
an upstream failure or empty result. The server and UI report “豆包搜索可用” only
when a real response is identified as `source: "doubao"` and contains at least
one normalized result with a non-empty URL. Saving a key, opening the endpoint,
or receiving an empty success response is not an authentication or readiness
verdict. Tavily remains visible in Customize → Network as a backup option for
the separate generic search path.

## Volcengine DataPro professional datasets

DataPro is intentionally separate from the normalized public-database tools
above. It is one fixed, managed MCP Streamable HTTP connector named
`volcengine-datapro`, and exposes only `dataPro_search(query:string)` to its
bundled Skill. The endpoint is not user-selectable. The Agent Plan Key is stored
through SecretBroker and resolved only as each outbound POST is assembled; an
active Ark model key is reused only when the active provider is Ark.

The Customize → Connectors card performs the actual search call and persists
its result as a JSON Artifact. Every successful response is indexed without a
field allowlist: all keys, scalar values, nested objects, nested arrays, MCP
content blocks, text, future result-envelope fields, and duplicate logical
occurrences in the redacted result returned by that call are covered by a
leaf-count and digest completeness receipt. This is a guarantee about all
content returned by that query, not a claim that the single-argument MCP tool
can enumerate DataPro's entire remote corpus.

Neither MCP initialization nor tool discovery is an authentication verdict.
The UI reports “专业数据集可用” only after the real tool result contains an
integer `structuredContent.code` equal to `0` and the local index transaction
returns a complete receipt with matching source/index leaf counts; code `4011`
reports “Key 无效、额度不足，或者专业数据集 Harness 未开启。”. The dedicated Web
route, managed connector call, and bundled Skill's `host.mcp.call` path share
this ingestion boundary. Indexed entries are discoverable from the global
command palette and link back to their saved Artifact when one exists.
The default permission seed allows only the exact
`volcengine-datapro/dataPro_search` target. Supplying or activating the shared
brokered Agent Plan credential is the user's authorization; the bundled
connector and Skill are enabled by default, and the UI enable action is
idempotent. All other MCP calls keep the existing ask policy, and any operator
deny remains an absolute veto.
Redirects are refused so authenticated headers cannot cross origins, and the
fixed endpoint still passes the global network switch, exact-host egress
allowlist, SSRF guard, timeout, and response-size ceiling.

## CUA cloud computer

CUA is a hosted cloud Windows desktop operated by its own remote agent.
OpenAI4S delegates a whole user objective to it and follows the outcome; it
does not script individual mouse or keyboard actions. Like DataPro it is one
fixed, managed MCP Streamable HTTP connector — `connector_id` `cua`, endpoint
`https://sd8j64df316pc5mfa3qpg.apigateway-cn-beijing.volceapi.com/skill/mcp`,
authenticated on every request with `Authorization: Bearer <CUA API Key>`. The
endpoint is not user-selectable.

The credential is a dedicated **CUA API Key** (`cua_api_key`), stored through
SecretBroker and pasted into the Customize → Connectors CUA card. It is issued
by CUA's official authorization flow (browser SSO sign-in, then key creation).
It is **not** the `ark-` Agent Plan Key that Doubao Search and DataPro share —
the CUA service rejects `ark-` keys.

Six tools, as returned by the live server's `tools/list`:

| tool | kind | purpose |
|---|---|---|
| `cua_ping` | read-only | connectivity, auth, and desktop-binding check; creates no task |
| `cua_delegate` | mutating | start a delegation carrying the user's verbatim objective |
| `cua_watch` | read-only | wait for or poll an invocation's next semantic state |
| `cua_answer` | mutating | forward the user's answer when an invocation is `needs_input` |
| `cua_cancel` | mutating | cancel an invocation on the user's explicit request; no rollback of performed actions |
| `cua_observe` | read-only | desktop visibility: environment state, a short-lived `access_url`, optional screenshot |

Default permissions follow that split: `cua_ping`, `cua_watch`, and
`cua_observe` are seeded `allow`; `cua_delegate`, `cua_answer`, and
`cua_cancel` operate a real cloud desktop and keep the `ask` policy, so each
call prompts for approval unless the user pre-approves those targets in the
permission panel.

Agent code reaches the connector from a Python cell as `host.mcp.tools("cua")`
and `host.mcp.call("cua", "<tool>", {...})`. The bundled
[`cua` Skill](../skills/cua/SKILL.md) is the recipe, including the outcome
state machine (`completed` / `in_progress` / `needs_input` / `failed` /
`cancelled`) and the rule that a `completed` invocation's `result.text` is the
sole authoritative final answer.

Verification is read-only: the Customize → Connectors CUA card's verify action
runs `cua_ping` plus `cua_observe` and reports the auth status together with a
temporary desktop `access_url`. The link is short-lived — re-verify (or call
`cua_observe` again) for a fresh one. Neither MCP initialization nor tool
discovery is an authentication verdict.

With `OPENAI4S_EGRESS=allowlist`, the CUA endpoint's host must be allowed: add
`*.volceapi.com` (or the exact
`sd8j64df316pc5mfa3qpg.apigateway-cn-beijing.volceapi.com`) to the allowlist,
or the connector's requests are refused.
