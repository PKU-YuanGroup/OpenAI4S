"""Schema-normalized access to public scientific databases.

The connectors are deliberately thin: they construct fixed, allowlisted API
URLs, use the existing :mod:`openai4s.webtools` fetch path, and normalize a
small useful subset of each upstream schema.  They do not replace code cells;
their stable records are designed to be looped over and joined inside the
persistent Python kernel.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Callable, Mapping


class ScienceConnectorError(RuntimeError):
    """A public API failed or returned a schema the connector cannot trust."""


@dataclass(frozen=True, slots=True)
class ScienceDatabase:
    id: str
    label: str
    description: str
    domains: tuple[str, ...]
    record_type: str
    query_hint: str
    filters: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "domains": list(self.domains),
            "record_type": self.record_type,
            "query_hint": self.query_hint,
            "filters": list(self.filters),
        }


DATABASES: tuple[ScienceDatabase, ...] = (
    ScienceDatabase(
        "uniprot",
        "UniProtKB",
        "Curated and unreviewed protein sequence and function records.",
        ("biology",),
        "protein",
        "UniProt query syntax, accession, gene, protein, or free text.",
        ("organism_id",),
    ),
    ScienceDatabase(
        "pdb",
        "RCSB Protein Data Bank",
        "Experimentally determined biomolecular structure entries.",
        ("biology", "chemistry"),
        "structure",
        "Full-text structure, macromolecule, ligand, or method query.",
    ),
    ScienceDatabase(
        "ensembl",
        "Ensembl",
        "Genome annotations and stable gene/transcript/protein identifiers.",
        ("biology",),
        "genomic_feature",
        "Exact gene symbol; species defaults to homo_sapiens.",
        ("species",),
    ),
    ScienceDatabase(
        "chembl",
        "ChEMBL",
        "Bioactive molecules, drug-like properties, and development phase.",
        ("chemistry", "biology"),
        "molecule",
        "Compound name, ChEMBL id, synonym, or chemical search text.",
    ),
    ScienceDatabase(
        "pubchem",
        "PubChem",
        "Compound identities and computed physicochemical properties.",
        ("chemistry",),
        "compound",
        "Compound name or synonym; exact PUG REST name lookup.",
    ),
    ScienceDatabase(
        "arxiv",
        "arXiv",
        "Open preprints across ML, physics, mathematics, and related fields.",
        ("literature", "ml", "physics"),
        "preprint",
        "arXiv API expression or free text searched across all fields.",
    ),
    ScienceDatabase(
        "openalex",
        "OpenAlex",
        "Scholarly works, authors, concepts, venues, citations, and open access.",
        ("literature", "ml", "physics", "biology", "chemistry"),
        "work",
        "Title, abstract, author, concept, DOI, or general scholarly text.",
        ("year_from", "year_to", "work_type"),
    ),
)

_DATABASE_BY_ID = {database.id: database for database in DATABASES}
_DOMAINS = frozenset({"all", "biology", "chemistry", "literature", "ml", "physics"})
_FILTERS = frozenset({"organism_id", "species", "year_from", "year_to", "work_type"})
_SPECIES = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_MAX_RESPONSE_CHARS = 5_000_000


class ScienceConnectorService:
    """Query fixed public APIs and return one stable cross-database envelope."""

    def __init__(
        self,
        fetch: Callable[[str, str, float, int], str] | None = None,
    ) -> None:
        self._fetch = fetch or self._default_fetch

    def list_databases(self, domain: str = "all") -> dict[str, Any]:
        selected = str(domain or "all").strip().lower()
        if selected not in _DOMAINS:
            raise ScienceConnectorError(
                f"unknown science domain {selected!r}; choose one of: "
                + ", ".join(sorted(_DOMAINS))
            )
        databases = [
            database.public()
            for database in DATABASES
            if selected == "all" or selected in database.domains
        ]
        return {
            "domain": selected,
            "count": len(databases),
            "databases": databases,
            "result_schema": {
                "id": "stable source identifier",
                "title": "human-readable record title",
                "url": "canonical public record URL",
                "type": "normalized record kind",
                "attributes": "source-specific typed fields",
            },
        }

    def search(
        self,
        database: str,
        query: str,
        *,
        limit: int = 10,
        cursor: str | None = None,
        filters: Mapping[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        database_id = str(database or "").strip().lower()
        metadata = _DATABASE_BY_ID.get(database_id)
        if metadata is None:
            raise ScienceConnectorError(
                f"unknown scientific database {database_id!r}; choose one of: "
                + ", ".join(_DATABASE_BY_ID)
            )
        normalized_query = " ".join(str(query or "").split())
        if not normalized_query:
            raise ScienceConnectorError("science query must not be empty")
        if len(normalized_query) > 500:
            raise ScienceConnectorError("science query is limited to 500 characters")
        requested_limit = int(limit)
        if not 1 <= requested_limit <= 50:
            raise ScienceConnectorError("science result limit must be between 1 and 50")
        requested_timeout = float(timeout)
        if not 1 <= requested_timeout <= 120:
            raise ScienceConnectorError(
                "science timeout must be between 1 and 120 seconds"
            )
        clean_filters = self._filters(filters)

        adapter = getattr(self, f"_search_{database_id}")
        try:
            results, next_cursor, request_url = adapter(
                normalized_query,
                requested_limit,
                str(cursor or ""),
                clean_filters,
                requested_timeout,
            )
        except ScienceConnectorError:
            raise
        except Exception as exc:  # noqa: BLE001 - normalize public API failures
            raise ScienceConnectorError(
                f"{metadata.label} request failed: {type(exc).__name__}: {exc}"
            ) from exc
        return {
            "database": database_id,
            "source": metadata.label,
            "query": normalized_query,
            "count": len(results),
            "results": results[:requested_limit],
            "next_cursor": next_cursor or None,
            "request_url": request_url,
        }

    @staticmethod
    def _filters(filters: Mapping[str, Any] | None) -> dict[str, Any]:
        values = dict(filters or {})
        unknown = sorted(set(values) - _FILTERS)
        if unknown:
            raise ScienceConnectorError(
                "unknown science filters: " + ", ".join(unknown)
            )
        for key in ("year_from", "year_to"):
            if key in values and values[key] not in (None, ""):
                try:
                    values[key] = int(values[key])
                except (TypeError, ValueError) as exc:
                    raise ScienceConnectorError(f"{key} must be an integer") from exc
                if not 1000 <= values[key] <= 3000:
                    raise ScienceConnectorError(f"{key} must be between 1000 and 3000")
        if values.get("year_from") and values.get("year_to"):
            if values["year_from"] > values["year_to"]:
                raise ScienceConnectorError("year_from must not exceed year_to")
        return values

    @staticmethod
    def _default_fetch(url: str, fmt: str, timeout: float, max_chars: int) -> str:
        from openai4s import webtools

        response = webtools.web_fetch(
            url,
            fmt=fmt,
            timeout=timeout,
            max_chars=max_chars,
        )
        if response.get("truncated"):
            raise ScienceConnectorError("scientific database response exceeded 5 MB")
        return str(response.get("content") or "")

    def _json(self, url: str, timeout: float, *, allow_empty: bool = False) -> Any:
        raw = self._fetch(url, "json", timeout, _MAX_RESPONSE_CHARS)
        if allow_empty and not raw.strip():
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ScienceConnectorError(
                "scientific database returned invalid JSON"
            ) from exc

    def _text(self, url: str, timeout: float) -> str:
        return self._fetch(url, "text", timeout, _MAX_RESPONSE_CHARS)

    def _search_uniprot(self, query, limit, cursor, filters, timeout):
        del cursor
        expression = query
        organism_id = str(filters.get("organism_id") or "").strip()
        if organism_id:
            if not organism_id.isdigit():
                raise ScienceConnectorError("organism_id must contain digits only")
            expression = f"({query}) AND (organism_id:{organism_id})"
        params = urllib.parse.urlencode(
            {
                "query": expression,
                "format": "json",
                "size": limit,
                "fields": (
                    "accession,id,protein_name,gene_names,organism_name,"
                    "organism_id,length,reviewed"
                ),
            }
        )
        url = f"https://rest.uniprot.org/uniprotkb/search?{params}"
        payload = self._json(url, timeout)
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ScienceConnectorError("UniProt returned an unexpected result schema")
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            accession = _string(row.get("primaryAccession"))
            if not accession:
                continue
            protein = row.get("proteinDescription") or {}
            recommended = protein.get("recommendedName") or {}
            full_name = recommended.get("fullName") or {}
            title = _string(full_name.get("value")) or _string(row.get("uniProtkbId"))
            genes = []
            for gene in row.get("genes") or []:
                value = _nested(gene, "geneName", "value")
                if value:
                    genes.append(_string(value))
            organism = row.get("organism") or {}
            results.append(
                _record(
                    accession,
                    title or accession,
                    f"https://www.uniprot.org/uniprotkb/{urllib.parse.quote(accession)}",
                    "protein",
                    {
                        "entry_name": _string(row.get("uniProtkbId")),
                        "gene_names": genes[:10],
                        "organism": _string(organism.get("scientificName")),
                        "taxon_id": organism.get("taxonId"),
                        "length": _nested(row, "sequence", "length"),
                        "entry_type": _string(row.get("entryType")),
                    },
                )
            )
        return results, "", url

    def _search_pdb(self, query, limit, cursor, filters, timeout):
        del cursor, filters
        request = {
            "query": {
                "type": "terminal",
                "service": "full_text",
                "parameters": {"value": query},
            },
            "return_type": "entry",
            "request_options": {"paginate": {"start": 0, "rows": limit}},
        }
        encoded = urllib.parse.quote(json.dumps(request, separators=(",", ":")))
        url = f"https://search.rcsb.org/rcsbsearch/v2/query?json={encoded}"
        payload = self._json(url, timeout, allow_empty=True)
        if payload is None:
            return [], "", url
        rows = payload.get("result_set") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ScienceConnectorError("RCSB PDB returned an unexpected result schema")
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            identifier = _string(row.get("identifier")).upper()
            if not identifier:
                continue
            results.append(
                _record(
                    identifier,
                    f"PDB {identifier}",
                    f"https://www.rcsb.org/structure/{urllib.parse.quote(identifier)}",
                    "structure",
                    {"score": row.get("score")},
                )
            )
        return results, "", url

    def _search_ensembl(self, query, limit, cursor, filters, timeout):
        del cursor
        species = str(filters.get("species") or "homo_sapiens").strip().lower()
        if not _SPECIES.fullmatch(species):
            raise ScienceConnectorError("species must be an Ensembl species slug")
        url = (
            "https://rest.ensembl.org/xrefs/symbol/"
            f"{urllib.parse.quote(species)}/{urllib.parse.quote(query, safe='')}"
            "?content-type=application/json"
        )
        rows = self._json(url, timeout)
        if not isinstance(rows, list):
            raise ScienceConnectorError("Ensembl returned an unexpected result schema")
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            identifier = _string(row.get("id"))
            if not identifier:
                continue
            results.append(
                _record(
                    identifier,
                    query,
                    f"https://www.ensembl.org/id/{urllib.parse.quote(identifier)}",
                    "genomic_feature",
                    {
                        "species": species,
                        "feature_type": _string(row.get("type")),
                    },
                )
            )
        return results, "", url

    def _search_chembl(self, query, limit, cursor, filters, timeout):
        del cursor, filters
        params = urllib.parse.urlencode({"q": query, "limit": limit})
        url = f"https://www.ebi.ac.uk/chembl/api/data/molecule/search.json?{params}"
        payload = self._json(url, timeout)
        rows = payload.get("molecules") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ScienceConnectorError("ChEMBL returned an unexpected result schema")
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            identifier = _string(row.get("molecule_chembl_id"))
            if not identifier:
                continue
            properties = row.get("molecule_properties") or {}
            structures = row.get("molecule_structures") or {}
            results.append(
                _record(
                    identifier,
                    _string(row.get("pref_name")) or identifier,
                    f"https://www.ebi.ac.uk/chembl/explore/compound/{urllib.parse.quote(identifier)}",
                    "molecule",
                    {
                        "molecule_type": _string(row.get("molecule_type")),
                        "max_phase": row.get("max_phase"),
                        "molecular_formula": _string(properties.get("full_molformula")),
                        "molecular_weight": _number(properties.get("full_mwt")),
                        "alogp": _number(properties.get("alogp")),
                        "canonical_smiles": _string(
                            structures.get("canonical_smiles"), 2000
                        ),
                    },
                )
            )
        return results, "", url

    def _search_pubchem(self, query, limit, cursor, filters, timeout):
        del cursor, filters
        properties = (
            "Title,MolecularFormula,MolecularWeight,ConnectivitySMILES,"
            "SMILES,InChIKey,XLogP,HBondDonorCount,HBondAcceptorCount"
        )
        url = (
            "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
            f"{urllib.parse.quote(query, safe='')}/property/{properties}/JSON"
        )
        payload = self._json(url, timeout)
        table = payload.get("PropertyTable") if isinstance(payload, dict) else None
        rows = table.get("Properties") if isinstance(table, dict) else None
        if not isinstance(rows, list):
            raise ScienceConnectorError("PubChem returned an unexpected result schema")
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            identifier = str(row.get("CID") or "").strip()
            if not identifier:
                continue
            results.append(
                _record(
                    identifier,
                    _string(row.get("Title")) or f"PubChem CID {identifier}",
                    f"https://pubchem.ncbi.nlm.nih.gov/compound/{identifier}",
                    "compound",
                    {
                        "molecular_formula": _string(row.get("MolecularFormula")),
                        "molecular_weight": _number(row.get("MolecularWeight")),
                        "canonical_smiles": _string(row.get("ConnectivitySMILES")),
                        "isomeric_smiles": _string(row.get("SMILES")),
                        "inchikey": _string(row.get("InChIKey")),
                        "xlogp": _number(row.get("XLogP")),
                        "h_bond_donors": row.get("HBondDonorCount"),
                        "h_bond_acceptors": row.get("HBondAcceptorCount"),
                    },
                )
            )
        return results, "", url

    def _search_arxiv(self, query, limit, cursor, filters, timeout):
        del filters
        start = 0
        if cursor:
            try:
                start = max(0, int(cursor))
            except ValueError as exc:
                raise ScienceConnectorError(
                    "arXiv cursor must be a non-negative integer"
                ) from exc
        expression = (
            query
            if re.search(r"\b(?:all|ti|au|abs|cat|id):", query)
            else f"all:{query}"
        )
        params = urllib.parse.urlencode(
            {
                "search_query": expression,
                "start": start,
                "max_results": limit,
                "sortBy": "relevance",
            }
        )
        url = f"https://export.arxiv.org/api/query?{params}"
        raw = self._text(url, timeout)
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise ScienceConnectorError("arXiv returned invalid Atom XML") from exc
        atom = "{http://www.w3.org/2005/Atom}"
        arxiv = "{http://arxiv.org/schemas/atom}"
        results = []
        for entry in root.findall(f"{atom}entry")[:limit]:
            record_url = _element_text(entry, f"{atom}id")
            identifier = record_url.rsplit("/", 1)[-1]
            if not identifier:
                continue
            authors = [
                _element_text(author, f"{atom}name")
                for author in entry.findall(f"{atom}author")
            ]
            categories = [
                _string(category.attrib.get("term"))
                for category in entry.findall(f"{atom}category")
                if category.attrib.get("term")
            ]
            results.append(
                _record(
                    identifier,
                    _element_text(entry, f"{atom}title") or identifier,
                    record_url or f"https://arxiv.org/abs/{identifier}",
                    "preprint",
                    {
                        "authors": authors[:50],
                        "published": _element_text(entry, f"{atom}published"),
                        "updated": _element_text(entry, f"{atom}updated"),
                        "categories": categories,
                        "doi": _element_text(entry, f"{arxiv}doi"),
                        "abstract": _element_text(entry, f"{atom}summary", 4000),
                    },
                )
            )
        next_cursor = str(start + len(results)) if len(results) == limit else ""
        return results, next_cursor, url

    def _search_openalex(self, query, limit, cursor, filters, timeout):
        params: dict[str, Any] = {
            "search": query,
            "per-page": limit,
            "cursor": cursor or "*",
        }
        clauses = []
        if filters.get("year_from"):
            clauses.append(f"from_publication_date:{filters['year_from']}-01-01")
        if filters.get("year_to"):
            clauses.append(f"to_publication_date:{filters['year_to']}-12-31")
        work_type = str(filters.get("work_type") or "").strip()
        if work_type:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,50}", work_type):
                raise ScienceConnectorError("work_type contains unsupported characters")
            clauses.append(f"type:{work_type}")
        if clauses:
            params["filter"] = ",".join(clauses)
        url = "https://api.openalex.org/works?" + urllib.parse.urlencode(params)
        payload = self._json(url, timeout)
        rows = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ScienceConnectorError("OpenAlex returned an unexpected result schema")
        results = []
        for row in rows[:limit]:
            if not isinstance(row, dict):
                continue
            identifier_url = _string(row.get("id"))
            identifier = identifier_url.rsplit("/", 1)[-1]
            if not identifier:
                continue
            authors = []
            for authorship in row.get("authorships") or []:
                name = _nested(authorship, "author", "display_name")
                if name:
                    authors.append(_string(name))
            concepts = [
                _string(concept.get("display_name"))
                for concept in (row.get("concepts") or [])[:10]
                if isinstance(concept, dict) and concept.get("display_name")
            ]
            doi = _string(row.get("doi"))
            results.append(
                _record(
                    identifier,
                    _string(row.get("display_name")) or identifier,
                    doi or identifier_url,
                    "work",
                    {
                        "doi": doi,
                        "publication_year": row.get("publication_year"),
                        "work_type": _string(row.get("type")),
                        "authors": authors[:50],
                        "concepts": concepts,
                        "cited_by_count": row.get("cited_by_count"),
                        "is_open_access": _nested(row, "open_access", "is_oa"),
                        "language": _string(row.get("language")),
                    },
                )
            )
        meta = payload.get("meta") or {}
        next_cursor = _string(meta.get("next_cursor"), 2000)
        return results, next_cursor, url


def _record(
    identifier: str,
    title: str,
    url: str,
    record_type: str,
    attributes: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "id": _string(identifier, 500),
        "title": _string(title, 1000),
        "url": _string(url, 4000),
        "type": record_type,
        "attributes": {
            key: value
            for key, value in attributes.items()
            if value not in (None, "", [], {})
        },
    }


def _string(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").split())[:limit]


def _number(value: Any) -> float | int | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _element_text(parent: ET.Element, path: str, limit: int = 1000) -> str:
    element = parent.find(path)
    return _string(element.text if element is not None else "", limit)


__all__ = [
    "DATABASES",
    "ScienceConnectorError",
    "ScienceConnectorService",
    "ScienceDatabase",
]
