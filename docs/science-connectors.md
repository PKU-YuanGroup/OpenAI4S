# Scientific database connectors

OpenAI4S exposes a thin, schema-normalized layer over common public scientific
APIs. It keeps the tool surface flat while letting Python cells combine real
records without scraping provider-specific pages.

## Supported databases

| id | source | disciplines | normalized record |
|---|---|---|---|
| `zenodo` | Zenodo Records API | multidisciplinary | dataset record/concept DOI, declared license, version and file inventory |
| `uniprot` | UniProtKB REST | biology | protein accession, name, genes, organism, length |
| `pdb` | RCSB PDB Search | biology, chemistry | structure id and relevance score |
| `ensembl` | Ensembl REST | biology | stable genomic feature from an exact symbol |
| `chembl` | ChEMBL REST | chemistry, biology | molecule identity, properties, SMILES, max phase |
| `pubchem` | PubChem PUG REST | chemistry | CID and computed compound properties |
| `arxiv` | arXiv Atom API | ML, physics, literature | preprint metadata, authors, categories, abstract |
| `openalex` | OpenAlex Works API | multidisciplinary literature | work, DOI, authors, concepts, citations, OA state |
| `string` | STRING REST API | biology | protein-protein interactions, partner, confidence scores |
| `bindingdb` | BindingDB REST | biology, chemistry | experimental binding affinity (Ki, Kd, IC50, EC50), SMILES, ligand structure |
| `clinvar` | ClinVar E-utilities (Stage 10 flag) | biology | variant accession, interpretation, gene |
| `pubmed` | PubMed E-utilities (Stage 10 flag) | literature | PMID, title, journal, date |
| `clinicaltrials` | ClinicalTrials.gov API v2 (Stage 10 flag) | biology, literature | NCT id, title, status |

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
- `species` (positive NCBI taxon ID or supported slug, default `9606`),
  `required_score` (integer, 0–1000), and `network_type` (`functional` by
  default, or `physical`) for STRING;
- `cutoff` (finite positive float or integer in nM, default `100`) and
  `affinity_type` (`Ki`, `IC50`, `Kd`, `EC50`) for BindingDB;
- `year_from`, `year_to`, and `work_type` for OpenAlex.

arXiv, OpenAlex and Zenodo return cursors. Other first-batch connectors are bounded
single-page searches. PubChem uses its exact name/synonym endpoint rather than
claiming fuzzy text-search semantics.

### STRING interaction partners

STRING queries use the fixed `https://string-db.org/api/json/interaction_partners`
endpoint. Supply one protein symbol or STRING identifier per line, for example
`host.science.search("string", "TP53\nCDK2", filters={"required_score": 700})`.
As described in the [STRING API documentation](https://en.string-db.org/help/api/),
this retrieves partners of the submitted proteins; it does not restrict the
result to edges within the submitted set. The upstream limit applies per
protein; OpenAI4S also caps the total returned records at `limit` in upstream
order, with no pagination. Larger sets should be queried one protein at a time
if every input needs coverage.

Supported species aliases are `homo_sapiens`/`human`, `mus_musculus`/`mouse`,
`rattus_norvegicus`/`rat`, `danio_rerio`/`zebrafish`,
`drosophila_melanogaster`/`fruitfly`, `caenorhabditis_elegans`/`worm`, and
`saccharomyces_cerevisiae`/`yeast`; other species require their numeric taxon ID.
Each interaction `id` joins the two sorted STRING IDs with `--`. Attributes
retain both endpoints, the selected network type, the combined confidence and
all seven evidence-channel scores, including `phylogenetic_score` (`pscore`).
Response confidence scores use the upstream 0–1 scale, while the request's
`required_score` uses 0–1000. Functional associations do not by themselves
establish physical binding; select `network_type="physical"` explicitly when
that network is required. An empty JSON array is a valid empty result; blank,
null, and malformed responses are errors. Every provided confidence score must
be finite and within 0–1; booleans, nonnumeric values, and out-of-range scores
are rejected as schema errors. Missing optional scores remain omitted rather
than being replaced with zero.

### BindingDB drug-target binding affinities

BindingDB queries retrieve measured binding affinities ($K_i, K_d, IC_{50}, EC_{50}$)
and chemical structures of active small molecules. Supply a UniProt accession
(e.g. `P11802`) or a 4-character PDB identifier (e.g. `1T46`). Queries automatically
route to either `https://www.bindingdb.org/rest/getLigandsByUniprots` or
`https://www.bindingdb.org/rest/getLigandsByPDBs`. Use the `cutoff` filter (in nM,
default 100) to limit results to high-affinity binders, and optional `affinity_type`
to filter for specific assay measurements (`Ki`, `IC50`, `Kd`, `EC50`).
The PDB route requires 100% sequence identity; this does not assert that every
returned ligand is co-crystallized in the queried structure. Results are capped
at `limit` after affinity-type filtering, in upstream order, without pagination.

Attributes preserve the target name, monomer ID, complete SMILES structure,
numerical and raw affinity values, and upstream citations (PMID, DOI). Each `id`
is a ligand monomer ID and can repeat for different measurements or sources.
Exact numeric affinities must be finite and nonnegative. Qualified values such
as `<1` retain their raw notation and omit `affinity_value` so a bound is never
presented as an exact measurement. Missing optional measurements remain omitted.

Empty affinity arrays and the blank no-match body documented by the
[BindingDB API](https://www.bindingdb.org/rwd/bind/BindingDBRESTfulAPI.jsp)
represent valid empty results with provenance. Null payloads, missing or
invalid affinity containers, and non-object rows raise connector errors;
records without monomer IDs are skipped. BindingDB is discoverable by name and
its domain is included in the built-in scientific egress allowlist.

### Published Zenodo datasets

`host.science.search("zenodo", "retrosynthesis", limit=10)` searches the fixed
public [Records API](https://developers.zenodo.org/#records) with
`type=dataset`. Anonymous pages support 1–25 records. Use the returned cursor
with the same query and limit; page URLs are rebuilt locally and an upstream
pagination URL is never followed. Zenodo serves only the first 10,000 hits of a
search, so the cursor ends there even when the upstream still offers a next
page.

The connector keeps a concrete record DOI separate from the concept DOI that
groups versions. Each file entry preserves its exact remote key, declared byte
count, source checksum, and a fixed `download_url`. Unknown license, size,
checksum, and file inventory values remain unknown; they are never converted to
zero, an empty list, or an open-license claim. Zenodo lists no files for
restricted or embargoed records, so an empty file list is an empty inventory
only on an `open` record; otherwise the inventory stays unknown. Discovery
reads metadata only and marks `file_verification=not_downloaded`.

The native `science_search` observation, for every database, starts with the
cursor and response receipt. When a page's file inventories would exceed an
ordinary tool's share of the observation, the largest are replaced by a
`files_omitted_from_view` count rather than cut or refused. A Python cell and
the Stage 10 Artifact always receive every entry.

The canonical offline fixture is PaRoutes Zenodo record `6275421`, version
`1.0.0`, licensed `cc-by-4.0`. Its `n1-targets.txt` file is 465,689 bytes with
source checksum `md5:5adae99357cdad829073b197c7813152`; the same record's
`uspto_raw_template_library.csv` (about 645 MB) is retained for future
oversized-import tests. The fixture is captured under `tests/fixtures/zenodo/`
and default tests never contact Zenodo.

This connector adds `zenodo.org` to the built-in Data repositories egress
group, which is enabled by default and matches subdomains. Under
`OPENAI4S_EGRESS=allowlist` that authorizes every outbound consumer, not only
this connector, including `web_download` of a Zenodo file URL; disabling the
group blocks the connector as well. SSRF, permission, timeout, response-size
and prompt-injection checks remain active.

### Import one selected Zenodo file

Use the native `science_import_dataset` tool in a Web session, then analyze
its workspace file in a subsequent Python/R Cell. The tool builds the file
URL itself; it does not accept a caller-supplied URL or resolve a concept DOI
to a moving latest version. For the PaRoutes example:

```json
{
  "record_id": "6275421",
  "file_key": "n1-targets.txt",
  "expected_size": 465689,
  "expected_checksum": "md5:5adae99357cdad829073b197c7813152",
  "path": "datasets/n1-targets.txt",
  "max_bytes": 1048576
}
```

Select the exact record ID, file key and declared size/checksum from discovery.
All six fields above are required; `timeout` optionally limits each network
request to 1–120 seconds (default 60). `max_bytes=0` admits only an empty file.
A missing declaration or a declaration above the explicit budget is refused
before I/O. File keys keep their exact spelling and are never local paths.
Destinations excluded from Artifact capture (hidden/dependency/repository
paths) are refused.

The import has its own `science_import_dataset` permission targeting
`zenodo.org`. A standing `web_download` deny for that host also refuses the
import; an existing download allow does not grant import permission. After
approval, the fixed record is refreshed and the selected file must still be
open with matching size/checksum. The shared egress/SSRF checks still apply
on metadata reads and each transfer redirect. Source MD5/SHA-256 is verified
while streaming; a separate SHA-256 describes the actual downloaded bytes.
The workspace copy is bounded and checked again before publication. A failed
transfer or cancellation before publication preserves any previous target.
Downloads never extract archives or execute code. Cancellation is checked
between reads; an in-flight read may take until its network timeout to return.

Success includes an `artifact` with an immutable `artifact_id`, `version_id`
and `filename`, plus source metadata binding the exact record/concept DOI,
version, source license/access declarations, file key, size/checksum, metadata
response receipt and local file SHA-256. Unknown license remains unknown.
Source declarations and matching checksums are not proof of scientific validity
or permission to reuse data. Repeated imports may create separate versions;
earlier source records are immutable, including after Store reopen.

Standalone CLI, Python Cell and background calls cannot provide the immediate
native capture transaction and refuse before metadata or file I/O. If capture
fails after file publication, the action fails with `output_committed` to veto
automatic replay; the workspace file may remain. Inspect the file and version
history before explicitly retrying. The generic `web_download` and its SDK
signature are unchanged. Dataset-specific workbench projection is tracked in
#185's Stage 3.

The opt-in acceptance test imports the real PaRoutes file through this native
transaction and checks Store reopen:

```bash
uv run pytest tests/test_dataset_import_live.py -m network -s
```

The 2026-09-23 acceptance read measured file SHA-256
`c0d1b48379e1ceb1129fba4bf3773f73f27bdb22bb4d468417e6e404d3210c15`.
The test pins this value independently of the record's MD5 declaration.
Default tests remain offline and no dataset file is bundled.

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
