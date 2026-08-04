#!/usr/bin/env python3
"""Re-audit the plan crosswalk, or check that it has been.

A `closed` row says a specific thing was proved, and points at the test files
that prove it. That claim has a shelf life nobody was measuring: at the time
this script was written, **25 of the 48 closed rows** named a test file that had
been modified since the audit the document itself declares -- one of them across
twelve commits. Every existing assertion passed on all 25, because they check
that the named file *exists*.

The fix is not to bump `audited_at` once. That reproduces the same state on the
next fifty commits, silently, which is how it got here. So each closed row
carries a digest of the evidence it was audited against, and this script is the
only way to move it:

    uv run python scripts/reaudit_crosswalk.py            # re-audit, rewriting
    uv run python scripts/reaudit_crosswalk.py --check    # what the suite runs

When a named test changes, `--check` fails and names the rows. The person then
has to look at those rows and decide whether the claim still holds -- which is
the whole content of "re-audit rather than patch". Regenerating without looking
is possible, as it is for every captured artifact in this repository; what is no
longer possible is not knowing.

Deliberately not a new CI job: `tests/test_plan_crosswalk.py` calls the same
function, so the check rides the suite that already reads this file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CROSSWALK = ROOT / "docs" / "plan-crosswalk.json"

#: The field each closed row carries. Absent on other statuses on purpose:
#: `implemented_unverified` and `deferred_p2` claim nothing was proved, so there
#: is no evidence for them to go stale.
DIGEST_FIELD = "evidence_digest"


def named_evidence(item: dict) -> list[str]:
    """Every file a row points at, tests and browser harness alike."""
    paths: list[str] = []
    for field in ("tests", "browser_evidence"):
        paths.extend(
            p.strip() for p in str(item.get(field) or "").split(",") if p.strip()
        )
    return sorted(set(paths))


def evidence_digest(item: dict) -> str:
    """A digest over the *content* of the row's evidence, not its mtime.

    Content rather than a commit SHA because a commit is not knowable while the
    commit is being written -- a check against "has anything moved since
    `audited_at`" fails on the very change that re-audits, which is the reason
    the previous arrangement had no such check at all.

    A missing file digests as the string `missing`, so a row whose evidence is
    deleted fails here as loudly as one whose evidence changed.
    """
    digest = hashlib.sha256()
    for path in named_evidence(item):
        target = ROOT / path
        body = target.read_bytes() if target.is_file() else b"missing"
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(body).hexdigest().encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def stale_rows(document: dict) -> list[tuple[str, str, str]]:
    """`(key, recorded, observed)` for every closed row whose evidence moved."""
    drifted = []
    for item in document.get("items", []):
        if item.get("status") != "closed":
            continue
        key = f"{item['source']}/{item['original_id']}"
        recorded = str(item.get(DIGEST_FIELD) or "")
        observed = evidence_digest(item)
        if recorded != observed:
            drifted.append((key, recorded, observed))
    return drifted


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift instead of re-recording it",
    )
    args = parser.parse_args()

    raw = CROSSWALK.read_text("utf-8")
    document = json.loads(raw)
    drifted = stale_rows(document)

    if args.check:
        if not drifted:
            closed = sum(1 for i in document["items"] if i["status"] == "closed")
            print(f"crosswalk up to date: {closed} closed rows, evidence unchanged")
            return 0
        print(
            f"{len(drifted)} closed row(s) name evidence that has changed since "
            f"they were audited:",
            file=sys.stderr,
        )
        for key, recorded, _observed in drifted:
            state = "never recorded" if not recorded else f"recorded {recorded[:12]}"
            print(f"  {key} ({state})", file=sys.stderr)
        print(
            "\nRead those rows against their tests, then re-record with:\n"
            "  uv run python scripts/reaudit_crosswalk.py",
            file=sys.stderr,
        )
        return 1

    for item in document["items"]:
        if item.get("status") == "closed":
            item[DIGEST_FIELD] = evidence_digest(item)
        else:
            item.pop(DIGEST_FIELD, None)
    CROSSWALK.write_text(
        json.dumps(document, ensure_ascii=False, indent=2)
        + ("\n" if raw.endswith("\n") else ""),
        encoding="utf-8",
    )
    print(f"re-audited {len(drifted)} row(s); {len(document['items'])} rows written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
