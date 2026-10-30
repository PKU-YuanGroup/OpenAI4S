"""The retrieval provenance a client is allowed to see.

`artifact_versions.source` records where retrieved data came from: the
database, the request URL, the query, when it happened, per-response hashes.
It has never been sent to a client, and it should be — "this figure is built on
data fetched from UniProt at 14:02, and here is the hash of what came back" is
the difference between a plot and a result.

But it cannot be sent as it stands, for three reasons that are each enough on
their own:

1. **A request URL carries credentials.** Plenty of scientific APIs take the
   key in the query string, and this envelope is written by whatever code did
   the retrieval — including a skill nobody has audited. Rendering it raw would
   publish an API key into the UI and into every stored frame that quotes it.
2. **The envelope is open-ended.** It is written as free-form JSON by the
   retrieval site, so "show the envelope" means showing whatever some future
   caller decided to put there. An allowlist is the only version of this that
   stays true as the callers change.
3. **It is unbounded.** A query can be a 200 KB POST body. A panel is not a
   place to discover that.

So: allowlist the keys, bound every value, and redact inside URLs and text
rather than trusting the whole-value check — a key in a query parameter sits in
the middle of a long string, which `redact` alone reads as "not opaque".
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai4s.observability import (
    CREDENTIAL_PARAMS,
    _looks_opaque,
    fingerprint,
    redact_text,
    redact_url,
)

#: Named here because this module's own contract is about a provenance URL;
#: the implementation moved to `observability` when the diagnostic bundle
#: turned out to need the identical guarantee for the daemon's startup
#: banner, and two copies of a redactor is how one of them goes stale.
_redact_url = redact_url

#: Fields a client may see, with what each is for. Anything else in the
#: envelope is dropped rather than rendered: this is written by retrieval code
#: that is not required to know about this panel, so the safe default when a
#: new key appears is that nobody sees it until somebody adds it here.
ALLOWED_FIELDS: dict[str, str] = {
    "database": "which resource was queried",
    "source": "the label the retrieval gave itself",
    "retrieved_at": "when",
    "request_url": "the exact request, credentials removed",
    "query": "the query terms",
    "normalization_version": "how the response was normalised",
    "response_sha256": "hash of what came back",
    "record_count": "how many records the response held",
    "dataset": "bounded dataset and file declarations recorded with this version",
}

DATASET_FIELDS = frozenset(
    {
        "provider",
        "record_id",
        "record_url",
        "record_doi",
        "concept_doi",
        "version",
        "title",
        "declared_license",
        "license_status",
        "access_right",
        "file_key",
        "declared_size_bytes",
        "declared_checksum",
        "file_verification",
        "local_sha256",
        "downloaded_bytes",
    }
)
_DATASET_INTEGER_FIELDS = frozenset({"declared_size_bytes", "downloaded_bytes"})

#: A version checksum as the Store records it (and as Artifact receipts must
#: carry it): bare SHA-256 hex.
_SHA256_HEX = re.compile(r"[0-9a-fA-F]{64}")

#: Public grammars for dataset identifiers. A value that matches its field's
#: grammar is shown as written; anything else is free text and goes through
#: the same shape-based redaction as every other source value.
#:
#: The whole-value opacity rule exists for strings that are nothing but a
#: credential-shaped run, and these identifiers have exactly that shape while
#: being public: Zenodo declares every file as `md5:<hex>`, and data file names
#: are long, space-free and mix letters with digits
#: (`uspto_rxn_n5_unique_templates.hdf5`). Judged whole, every declared
#: checksum and six of the fourteen PaRoutes file keys rendered as
#: `<redacted:…>`. Each grammar still refuses the shapes real credentials
#: take: a checksum is an algorithm plus that algorithm's exact digest length
#: (the importer's own `md5:`/`sha256:` contract), a DOI's suffix is still
#: judged for opacity, and a file name must end in a short alphabetic
#: extension, which no bare key, token prefix or JWT signature does.
_DECLARED_CHECKSUM = re.compile(r"md5:[0-9a-fA-F]{32}|sha256:[0-9a-fA-F]{64}")
_DOI = re.compile(r"10\.\d{4,9}/(\S+)")
_FILE_NAME = re.compile(r"[\w.-]+(?:/[\w.-]+)*\.[A-Za-z][A-Za-z0-9]{0,9}")
#: The importer's own bound on a file key; DOIs are shorter still. Longer
#: values are not identifiers this projection recognises.
_MAX_IDENTIFIER_CHARS = 512

#: Per-value ceiling. A query can be a large POST body, and a provenance panel
#: is not where anyone should discover that.
MAX_VALUE_CHARS = 2000


# --------------------------------------------------------------------------- #
#  read compatibility for the one kind that ever wrote the old names
# --------------------------------------------------------------------------- #

#: The retrieval kind `write_search_artifact` writes. Only this known kind has
#: historical aliases, and only this kind's are interpreted: an envelope of any
#: other shape is free-form JSON written by unaudited retrieval code, and
#: reading a key of it as a provenance field would let that code name a field
#: this projection then renders.
_SCIENCE_SEARCH_KIND = "science_search"

#: Historical field name -> the canonical name it became. Artifacts written
#: before the names were promoted carry the alias; they are not migrated (the
#: bytes are immutable) and have to stay readable.
LEGACY_SEARCH_ALIASES: dict[str, str] = {
    "endpoint": "request_url",
    "source_checksum": "response_sha256",
}

#: The plan's frozen compatibility field (F13): the canonical field names whose
#: historical alias disagreed, sorted. The canonical value is shown and the alias
#: value is never rendered, so no value is picked silently.
ALIAS_CONFLICTS_KEY = "alias_conflicts"

#: Optional projection keys beyond the plan's frozen field. Additive and
#: documented rather than implied: a client that shows a canonical value without
#: knowing it came from an alias, or shows the canonical value while the alias
#: disagreed, is describing a record it did not read. `alias_conflicts` is the
#: contract; these two only make the same facts legible in the panel and never
#: change which values are released.
LEGACY_FIELDS_KEY = "legacy_normalized"

#: The response digest's public state, spelled separately from the artifact's
#: own file checksum: hiding one must not read as "the retrieval was verified".
RESPONSE_HASH_FIELD = "response_sha256"
RESPONSE_HASH_STATE_KEY = "response_sha256_state"
RESPONSE_HASH_SHOWN = "shown"
RESPONSE_HASH_HIDDEN = "hidden"


def _has_value(value: Any) -> bool:
    return value is not None and value != ""


def _normalize_legacy_search(
    envelope: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Merge the historical ``science_search`` names into the canonical ones.

    The canonical field always wins a disagreement; the alias value is not
    rendered, and the conflict is named in `alias_conflicts` (F13), because
    silently picking one of two recorded request URLs would make the panel
    describe a request that may not be the one that produced the data. An alias that agrees is simply merged —
    one fact under two names, so it is neither a conflict nor an unknown field.
    Both the canonical value and a promoted alias value then go through the same
    redaction, clipping and unknown-field counting as everything else; nothing
    is exempt because of its name or its shape.
    """
    normalized = dict(envelope)
    legacy: list[str] = []
    conflicts: list[str] = []
    for alias, canonical in LEGACY_SEARCH_ALIASES.items():
        if alias not in envelope:
            continue
        alias_value = envelope[alias]
        normalized.pop(alias, None)
        canonical_value = envelope.get(canonical)
        if _has_value(canonical_value):
            if _has_value(alias_value) and alias_value != canonical_value:
                conflicts.append(canonical)
            continue
        if _has_value(alias_value):
            normalized[canonical] = alias_value
            legacy.append(canonical)
    return normalized, sorted(legacy), sorted(conflicts)


def _clip(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_VALUE_CHARS:
        return value, False
    return value[:MAX_VALUE_CHARS], True


def _is_public_identifier(field: str, value: str) -> bool:
    """Whether `value` is a well-formed public identifier for `field`."""
    if len(value) > _MAX_IDENTIFIER_CHARS:
        return False
    if field == "declared_checksum":
        return _DECLARED_CHECKSUM.fullmatch(value) is not None
    if field in ("record_doi", "concept_doi"):
        doi = _DOI.fullmatch(value)
        return doi is not None and not _looks_opaque(doi.group(1))
    if field == "file_key":
        return _FILE_NAME.fullmatch(value) is not None
    if field == RESPONSE_HASH_FIELD:
        # The hash of what came back is the point of the panel. Judged whole,
        # every real SHA-256 is an opaque 64-hex run and rendered as
        # `<redacted:…>`, so the digest could never be shown at all.
        return _SHA256_HEX.fullmatch(value) is not None
    return False


def _public_dataset(
    value: Any, artifact_sha256: str | None, *, dataset_verified: bool
) -> dict[str, Any] | None:
    """Project recorded source data without treating it as an attestation.

    The source column can also be supplied by a script. Consumers display the
    record as provenance, not as proof of publisher identity or scientific
    validity. Unknown values are retained and private fields are never copied.
    """
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    clipped: list[str] = []
    redacted: list[str] = []
    allowed = (
        DATASET_FIELDS
        if dataset_verified is True
        else DATASET_FIELDS - {"file_verification"}
    )
    dropped = len(set(value) - allowed)
    for field in sorted(allowed & value.keys()):
        item = value[field]
        if item is None:
            result[field] = None
        elif field in _DATASET_INTEGER_FIELDS:
            if (
                isinstance(item, int)
                and not isinstance(item, bool)
                and 0 <= item <= 9_007_199_254_740_991
            ):
                result[field] = item
            else:
                dropped += 1
        elif isinstance(item, str):
            if (
                field == "local_sha256"
                and isinstance(artifact_sha256, str)
                and _SHA256_HEX.fullmatch(artifact_sha256)
                and item.lower() == artifact_sha256.lower()
            ):
                # This value is already public as the same version's checksum.
                # An untrusted source cannot use a hash-shaped field to release
                # a different opaque value; those still pass through redaction.
                item = artifact_sha256.lower()
            elif _is_public_identifier(field, item):
                pass
            else:
                safe = redact_url(item) if field == "record_url" else redact_text(item)
                if safe != item:
                    redacted.append(field)
                item = safe
            result[field], truncated = _clip(item)
            if truncated:
                clipped.append(field)
        else:
            dropped += 1
    if dataset_verified is not True and (result or "file_verification" in value):
        # Missing local evidence is unknown, not a failed or fabricated import.
        # A null keeps that state visible through the existing viewer renderer.
        result["file_verification"] = None
    if not result:
        return None
    if clipped:
        result["truncated_fields"] = clipped
    if redacted:
        result["redacted_fields"] = redacted
    if dropped:
        result["undisclosed_field_count"] = dropped
    return result


def public_source(
    envelope: Any, *, artifact_sha256: str | None = None, dataset_verified: bool = False
) -> dict[str, Any] | None:
    """The client-safe projection of one version's retrieval provenance.

    Returns None when there is nothing to show, which is the common case: most
    artifacts are computed rather than retrieved, and an empty panel that says
    "no provenance" is worse than no panel, because it reads as a finding.
    """
    if isinstance(envelope, str):
        try:
            envelope = json.loads(envelope)
        except (TypeError, ValueError):
            return None
    if not isinstance(envelope, dict) or not envelope:
        return None

    # Read compatibility only for the one known kind that ever wrote the old
    # names. Everything below sees canonical fields and one envelope whose
    # unknown-field count no longer includes a legacy key that was consumed.
    working = envelope
    legacy: list[str] = []
    conflicts: list[str] = []
    if envelope.get("kind") == _SCIENCE_SEARCH_KIND:
        working, legacy, conflicts = _normalize_legacy_search(envelope)

    out: dict[str, Any] = {}
    truncated: list[str] = []
    unprojected = 0
    hash_state: str | None = None
    for field in ALLOWED_FIELDS:
        if field not in working:
            continue
        value = working[field]
        if field == "dataset":
            dataset = _public_dataset(
                value, artifact_sha256, dataset_verified=dataset_verified
            )
            if dataset is not None:
                out[field] = dataset
            elif value not in (None, "", {}, []):
                # Present but nothing in it could be shown: not an object, or
                # only fields outside the allowlist. Counted like any other
                # withheld field, or the panel reads as "no dataset declared".
                unprojected += 1
            continue
        if value is None or value == "":
            continue
        if isinstance(value, (int, float, bool)):
            out[field] = value
            continue
        raw_text = value if isinstance(value, str) else json.dumps(value, default=str)
        if field == RESPONSE_HASH_FIELD and _is_public_identifier(field, raw_text):
            safe = raw_text
        elif field == "request_url":
            safe = _redact_url(raw_text)
        else:
            safe = redact_text(raw_text)
        text, was_clipped = _clip(safe)
        if was_clipped:
            truncated.append(field)
        out[field] = text
        if field == RESPONSE_HASH_FIELD:
            # A bare SHA-256 is shown as written; any other value is treated as
            # text like every other value, and its public state is said out
            # loud so "hidden" cannot be mistaken for "verified".
            hash_state = (
                RESPONSE_HASH_HIDDEN if safe != raw_text else RESPONSE_HASH_SHOWN
            )

    if not out:
        return None
    if hash_state is not None:
        out[RESPONSE_HASH_STATE_KEY] = hash_state
    if legacy:
        out[LEGACY_FIELDS_KEY] = legacy
    if conflicts:
        out[ALIAS_CONFLICTS_KEY] = conflicts
    # Named, not implied. A field silently cut at 2000 characters reads as the
    # whole value, and a provenance panel that shows a shortened URL as if it
    # were the request is worse than showing nothing.
    if truncated:
        out["truncated_fields"] = sorted(truncated)
    # What was dropped, counted rather than listed: the names themselves come
    # from unaudited retrieval code and are not safe to render.
    dropped = len([k for k in working if k not in ALLOWED_FIELDS]) + unprojected
    if dropped:
        out["undisclosed_field_count"] = dropped
    return out


__all__ = [
    "ALIAS_CONFLICTS_KEY",
    "ALLOWED_FIELDS",
    "CREDENTIAL_PARAMS",
    "LEGACY_FIELDS_KEY",
    "LEGACY_SEARCH_ALIASES",
    "MAX_VALUE_CHARS",
    "RESPONSE_HASH_FIELD",
    "RESPONSE_HASH_HIDDEN",
    "RESPONSE_HASH_SHOWN",
    "RESPONSE_HASH_STATE_KEY",
    "public_source",
]
