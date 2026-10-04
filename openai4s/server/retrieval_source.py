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

    out: dict[str, Any] = {}
    truncated: list[str] = []
    unprojected = 0
    for field in ALLOWED_FIELDS:
        if field not in envelope:
            continue
        value = envelope[field]
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
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        text = _redact_url(text) if field == "request_url" else redact_text(text)
        text, was_clipped = _clip(text)
        if was_clipped:
            truncated.append(field)
        out[field] = text

    if not out:
        return None
    # Named, not implied. A field silently cut at 2000 characters reads as the
    # whole value, and a provenance panel that shows a shortened URL as if it
    # were the request is worse than showing nothing.
    if truncated:
        out["truncated_fields"] = sorted(truncated)
    # What was dropped, counted rather than listed: the names themselves come
    # from unaudited retrieval code and are not safe to render.
    dropped = len([k for k in envelope if k not in ALLOWED_FIELDS]) + unprojected
    if dropped:
        out["undisclosed_field_count"] = dropped
    return out


__all__ = [
    "ALLOWED_FIELDS",
    "CREDENTIAL_PARAMS",
    "MAX_VALUE_CHARS",
    "public_source",
]
