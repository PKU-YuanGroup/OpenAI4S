"""What each science connector depends on from its upstream API.

A connector adapter reaches into a public API's JSON along specific paths --
UniProt's `results[].primaryAccession`, OpenAlex's `results[].id`. When the API
renames or restructures a field, the adapter does not crash; it silently drops
records or returns them with empty titles, and nobody notices until a result
set looks thin. The manifest is the declaration of those paths, so that the
break has somewhere to be caught.

Two levels, because they fail differently:

* **required** -- the connector returns nothing useful without it. Every adapter
  skips a record that has no identifier, so the array container and the id are
  required. Drift here is an outage and a canary alarms on it.
* **expected** -- the parser reads it and degrades gracefully when it is
  missing (a title, an author list, a score). Drift here is quality loss, worth
  noticing but not worth waking anyone.

The manifest is not a second copy of the parse logic, and the tests keep it from
becoming one: an offline test proves every required path is actually present in
the connector's own fixture, and a mutation test proves each required path is
load-bearing by deleting it and watching the adapter stop returning the record.
A path that can be removed without breaking anything is not required, and the
manifest may not claim it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: A path is a tuple of steps. A string is an object key; the sentinel EACH
#: means "descend into every element of this array". So
#: ("results", EACH, "primaryAccession") is "for each result, its accession".
EACH = object()

Path = tuple


def resolve(document: Any, path: Path) -> list[Any]:
    """Every value reached by ``path``, flattening EACH steps.

    Returns a list because an EACH step fans out. An empty list means the path
    was not present -- which, for a required path, is exactly the drift a canary
    is looking for.
    """
    frontier: list[Any] = [document]
    for step in path:
        nxt: list[Any] = []
        for node in frontier:
            if step is EACH:
                if isinstance(node, list):
                    nxt.extend(node)
            elif isinstance(node, dict):
                if step in node:
                    nxt.append(node[step])
        frontier = nxt
    return frontier


def _label(path: Path) -> str:
    return ".".join("[]" if step is EACH else str(step) for step in path)


@dataclass(frozen=True)
class ConnectorManifest:
    """What one connector needs from its API, and how to check it is still there."""

    id: str
    #: A query that should always return at least one record. Stable and boring
    #: on purpose: "insulin" will be in UniProt for as long as UniProt exists.
    probe_query: str
    #: Paths without which the connector returns nothing useful.
    required: tuple[Path, ...]
    #: Paths the parser reads but survives without.
    expected: tuple[Path, ...] = ()
    #: Passed to the connector as filters for the probe.
    probe_filters: dict[str, str] = field(default_factory=dict)
    #: True for the sources the frozen decision names for live canaries.
    canary: bool = False

    def required_labels(self) -> list[str]:
        return [_label(p) for p in self.required]

    def check(self, upstream: Any) -> dict[str, list[str]]:
        """Which declared paths are missing from an upstream document.

        Structural: it reports absence, not correctness. A required path with no
        values is drift; an expected path with none is degradation.
        """
        missing_required = [
            _label(p) for p in self.required if not resolve(upstream, p)
        ]
        missing_expected = [
            _label(p) for p in self.expected if not resolve(upstream, p)
        ]
        return {"required": missing_required, "expected": missing_expected}


#: Every source gets a manifest; the three the frozen decision names get a live
#: canary. The required set is deliberately minimal -- the container and the id,
#: the two things whose loss makes the connector return nothing -- because a
#: canary that alarms on an optional field going missing is a canary that gets
#: muted.
MANIFESTS: tuple[ConnectorManifest, ...] = (
    ConnectorManifest(
        id="uniprot",
        probe_query="insulin",
        required=(("results", EACH, "primaryAccession"),),
        expected=(
            (
                "results",
                EACH,
                "proteinDescription",
                "recommendedName",
                "fullName",
                "value",
            ),
            ("results", EACH, "organism", "scientificName"),
        ),
        canary=True,
    ),
    ConnectorManifest(
        id="pdb",
        probe_query="hemoglobin",
        required=(("result_set", EACH, "identifier"),),
        expected=(("result_set", EACH, "score"),),
        canary=True,
    ),
    ConnectorManifest(
        id="openalex",
        probe_query="CRISPR",
        required=(("results", EACH, "id"),),
        expected=(
            ("results", EACH, "display_name"),
            ("results", EACH, "authorships", EACH, "author", "display_name"),
        ),
        canary=True,
    ),
    ConnectorManifest(
        id="ensembl",
        probe_query="BRCA2",
        required=((EACH, "id"),),
        expected=((EACH, "type"),),
    ),
    ConnectorManifest(
        id="chembl",
        probe_query="aspirin",
        required=(("molecules", EACH, "molecule_chembl_id"),),
        expected=(("molecules", EACH, "pref_name"),),
    ),
    ConnectorManifest(
        id="pubchem",
        probe_query="aspirin",
        required=(("PropertyTable", "Properties", EACH, "CID"),),
        expected=(("PropertyTable", "Properties", EACH, "Title"),),
    ),
    ConnectorManifest(
        id="arxiv",
        probe_query="electron",
        required=(),  # arXiv returns Atom XML, not JSON; checked structurally elsewhere.
        expected=(),
    ),
)

MANIFEST_BY_ID = {manifest.id: manifest for manifest in MANIFESTS}

CANARY_IDS = tuple(manifest.id for manifest in MANIFESTS if manifest.canary)


__all__ = [
    "CANARY_IDS",
    "ConnectorManifest",
    "EACH",
    "MANIFESTS",
    "MANIFEST_BY_ID",
    "resolve",
]
