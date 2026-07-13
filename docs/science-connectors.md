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
