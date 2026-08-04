"""The plan's 56 original proposals must each have exactly one recorded outcome.

Section 14 of the integrated report tracks 56 proposals from seven source reports,
and its own definition of done says none may be handled by "omission means
implicitly rejected". Prose cannot carry that: a table in a document can gain a
duplicate, lose a row, or say `Completed` about something no call chain reaches,
and nothing notices.

So the crosswalk is data ([`docs/plan-crosswalk.json`](../docs/plan-crosswalk.json))
and this file is the check. It asserts the shape — 56 unique `(source,
original_id)` keys, each appearing once — and it asserts that the *vocabulary* is
used honestly: `closed` requires a named test file that exists, and
`implemented_unverified` requires the missing run to be named rather than left as
an absence.

What it deliberately does not do is verify that a `closed` row is really closed.
No test can: that is what the row's named test file is for. This file makes the
claim locatable and refuses the claims that are structurally empty.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CROSSWALK = ROOT / "docs" / "plan-crosswalk.json"


@pytest.fixture(scope="module")
def crosswalk() -> dict:
    return json.loads(CROSSWALK.read_text("utf-8"))


def test_the_crosswalk_has_exactly_fifty_six_rows(crosswalk):
    assert crosswalk["expected_rows"] == 56
    assert len(crosswalk["items"]) == 56, (
        f"the plan tracks 56 proposals, the crosswalk has " f"{len(crosswalk['items'])}"
    )


def test_every_key_appears_exactly_once(crosswalk):
    """A duplicate is how a row gets two different answers; a missing key is the
    "omission means rejected" the plan's exit criteria forbid."""
    keys = [(item["source"], item["original_id"]) for item in crosswalk["items"]]
    duplicates = sorted(k for k, n in Counter(keys).items() if n > 1)
    assert not duplicates, f"these keys appear more than once: {duplicates}"
    assert len(set(keys)) == 56


def test_all_seven_source_reports_are_represented(crosswalk):
    """Each of the seven reports contributed eight proposals. A source that
    vanished entirely is the failure mode a row count alone cannot see."""
    per_source = Counter(item["source"] for item in crosswalk["items"])
    assert sorted(per_source) == ["R1", "R2", "R3", "R4", "R5", "R6", "R7"]
    assert all(count == 8 for count in per_source.values()), dict(per_source)


def test_every_row_carries_the_required_fields(crosswalk):
    required = {
        "source",
        "original_id",
        "original_priority",
        "original_proposal",
        "integrated",
        "plan_conclusion",
        "status",
        "status_note",
        "tests",
        "browser_evidence",
        "external_evidence",
    }
    for item in crosswalk["items"]:
        missing = sorted(required - set(item))
        assert not missing, f"{item.get('original_id')} is missing {missing}"


def test_every_status_is_from_the_declared_vocabulary(crosswalk):
    allowed = set(crosswalk["statuses"])
    for item in crosswalk["items"]:
        assert item["status"] in allowed, (
            f"{item['source']}/{item['original_id']} has status "
            f"{item['status']!r}, which is not in {sorted(allowed)}"
        )


def test_a_closed_row_names_a_test_file_that_exists(crosswalk):
    """`closed` is the only status that claims something was proved, so it is the
    only one that has to point at the proof. A `closed` row with no test, or with
    a test file that does not exist, is the label this whole exercise is about."""
    for item in crosswalk["items"]:
        if item["status"] != "closed":
            continue
        named = [part.strip() for part in item["tests"].split(",") if part.strip()]
        assert (
            named
        ), f"{item['source']}/{item['original_id']} is closed but names no test"
        for path in named:
            assert (ROOT / path).is_file(), (
                f"{item['source']}/{item['original_id']} names {path}, which does "
                f"not exist"
            )


def test_an_unverified_row_names_the_run_it_is_missing(crosswalk):
    """ "Implemented but unverified" is only honest if it says what would settle
    it. Without that it is indistinguishable from "we did not check"."""
    for item in crosswalk["items"]:
        if item["status"] != "implemented_unverified":
            continue
        assert item["external_evidence"].strip(), (
            f"{item['source']}/{item['original_id']} is implemented_unverified but "
            f"does not name the missing run"
        )


def test_only_p2_rows_may_be_deferred(crosswalk):
    """A P0 or P1 row cannot be parked under the P2 decision."""
    for item in crosswalk["items"]:
        if item["status"] == "deferred_p2":
            assert item["integrated"].startswith("P2"), (
                f"{item['source']}/{item['original_id']} is deferred as P2 but its "
                f"destination is {item['integrated']!r}"
            )


def test_no_row_is_silently_absent_from_a_destination(crosswalk):
    """Every row names at least one integrated destination, so "where did this
    proposal go" always has an answer."""
    for item in crosswalk["items"]:
        assert item[
            "integrated"
        ].strip(), f"{item['source']}/{item['original_id']} names no destination"


def test_the_crosswalk_records_what_it_was_audited_against(crosswalk):
    """A status is a statement about a commit. Without the commit it is a
    statement about nothing."""
    assert len(crosswalk["baseline"]) == 40
    assert len(crosswalk["audited_at"]) == 40


# -- the re-audit's own mechanisms -------------------------------------------
#
# Everything above checks the crosswalk's *shape*. What it could not see is the
# three ways the content went wrong:
#
#   * `audited_at` was checked for being 40 characters long. Forty characters is
#     not a commit, and the field's whole job is to say which tree the 48
#     `closed` claims are about.
#   * four `closed` rows carried one verbatim `status_note` and one verbatim
#     four-file `tests` list. The note describes an SQL/artifact-scope closure;
#     the rows it was pasted onto are a bundled-skill network boundary, an SSH
#     alias closure, and model-profile identity. Three of the four rows were
#     therefore documented with another row's evidence, and every assertion
#     above passed on all of them.
#   * `browser_evidence` was empty on all 56 rows while nine `closed` notes
#     asserted a UI call site -- a field that exists, is required, and decides
#     nothing.


def _git(*args: str) -> tuple[int, str] | None:
    """`(exit code, stdout)`, or None when this is not a usable checkout.

    The exit code is returned, not folded away, because `git merge-base
    --is-ancestor` answers *only* through it: it prints nothing whether the
    answer is yes or no. A helper that collapsed both to the empty string made
    the ordering assertion below unfailable -- the first version of it did
    exactly that, and the mutation written to prove it could fail came back
    green.
    """
    import subprocess

    try:
        completed = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - no git
        return None
    return completed.returncode, completed.stdout.strip()


def _git_says(*args: str) -> bool:
    """True when git exited 0. Used for the predicate subcommands."""
    result = _git(*args)
    return result is not None and result[0] == 0


@pytest.fixture(scope="module")
def in_a_checkout() -> bool:
    return _git_says("rev-parse", "--git-dir")


@pytest.fixture(scope="module")
def shallow() -> bool:
    """Whether this clone may legitimately be missing old objects.

    `actions/checkout` fetches depth 1 by default, so in CI these commits are
    not merely unreachable -- they are not in the object store at all and
    `cat-file` exits 128. That is a fact about the checkout, not about the
    crosswalk, and the first version of these assertions could not tell the two
    apart: it reddened every `Offline tests` job while passing here, because
    this working copy is *also* shallow and happens to still hold the baseline.

    So the distinction is explicit. In a shallow clone a missing object is
    skipped and said out loud; in a full clone it is a hard failure, because
    there the only way for a named commit to be absent is that it was invented.
    `ci.yml` fetches full history for the py3.12 job, so the strict branch is
    the one CI actually takes.
    """
    result = _git("rev-parse", "--is-shallow-repository")
    return result is not None and result[1].strip() == "true"


def _present(sha: str) -> bool:
    return _git_says("cat-file", "-e", f"{sha}^{{commit}}")


def test_the_audited_commits_are_commits_in_this_repository(
    crosswalk, in_a_checkout, shallow
):
    """Forty characters was the whole check.

    A status is a statement about a tree. A `baseline`/`audited_at` that names
    no commit in this history is a statement about a tree nobody can produce --
    and it reads, to every consumer of this file, exactly like one that can.
    """
    if not in_a_checkout:
        pytest.skip("not a git checkout")
    for field in ("baseline", "audited_at"):
        sha = crosswalk[field]
        # Checkable in any clone, and the half that catches a placeholder:
        # forty lowercase hex characters. It needs no history.
        assert re.fullmatch(r"[0-9a-f]{40}", sha), f"{field} is not a full SHA: {sha!r}"
        if not _present(sha):
            if shallow:
                pytest.skip(
                    f"{field}={sha[:12]} is outside this shallow clone's history; "
                    "run in a full clone to check it names a real commit"
                )
            raise AssertionError(f"{field}={sha} is not a commit in this repository")
        assert _git("cat-file", "-t", sha) == (
            0,
            "commit",
        ), f"{field}={sha} exists but is not a commit"


def test_the_audit_is_ordered_and_reachable(crosswalk, in_a_checkout, shallow):
    """`baseline` is where the plan started and `audited_at` is where it was
    last read. An audit that predates its own baseline, or that sits on a commit
    this branch cannot reach, is describing a different history."""
    if not in_a_checkout:
        pytest.skip("not a git checkout")
    baseline, audited = crosswalk["baseline"], crosswalk["audited_at"]
    missing = [sha for sha in (baseline, audited) if not _present(sha)]
    if missing:
        if shallow:
            pytest.skip(
                f"{[s[:12] for s in missing]} outside this shallow clone; "
                "ancestry cannot be decided from a truncated history"
            )
        raise AssertionError(f"these are not commits here: {missing}")
    assert _git_says(
        "merge-base", "--is-ancestor", baseline, audited
    ), f"baseline {baseline[:12]} is not an ancestor of audited_at {audited[:12]}"
    assert _git_says("merge-base", "--is-ancestor", audited, "HEAD"), (
        f"audited_at {audited[:12]} is not reachable from HEAD; this crosswalk "
        "was audited against a tree this branch does not contain"
    )


def test_no_two_closed_rows_share_a_verbatim_note(crosswalk):
    """A shared note is only honest when the reason is genuinely shared.

    Two groups here share one: three `deferred_p2` rows parked by decision D8,
    and five `implemented_unverified` rows waiting on the same single
    `workflow_dispatch`. Both are one fact about several rows.

    `closed` is different. It claims a specific thing was proved about a
    specific subject, so two closed rows with the same sentence means at least
    one of them is documented with the other's evidence -- which is what
    happened: one SQL/artifact-scope paragraph on four rows, three of which are
    about something else entirely.
    """
    from collections import defaultdict

    notes = defaultdict(list)
    for item in crosswalk["items"]:
        if item["status"] != "closed":
            continue
        notes[item["status_note"].strip()].append(
            f"{item['source']}/{item['original_id']}"
        )
    shared = {note: keys for note, keys in notes.items() if len(keys) > 1}
    assert not shared, "closed rows sharing one note: " + "; ".join(
        f"{keys} -> {note[:70]}..." for note, keys in shared.items()
    )


def test_every_named_test_file_is_actually_a_test_file(crosswalk):
    """Existence was the check, and a path that exists is not evidence.

    A row could name `tests/README.md` or a helper module and pass. What makes a
    named file evidence is that pytest can fail on it.
    """
    for item in crosswalk["items"]:
        for path in [p.strip() for p in item["tests"].split(",") if p.strip()]:
            assert path.startswith("tests/"), (
                f"{item['source']}/{item['original_id']} names {path}, which is "
                "not under tests/"
            )
            target = ROOT / path
            assert target.is_file(), f"{path} does not exist"
            assert "def test_" in target.read_text("utf-8"), (
                f"{item['source']}/{item['original_id']} names {path}, which "
                "defines no test"
            )


def test_a_row_does_not_name_the_same_test_twice(crosswalk):
    """Four files, one of them listed twice, is three files and a longer
    sentence."""
    for item in crosswalk["items"]:
        named = [p.strip() for p in item["tests"].split(",") if p.strip()]
        duplicates = sorted({p for p in named if named.count(p) > 1})
        assert (
            not duplicates
        ), f"{item['source']}/{item['original_id']} names {duplicates} twice"


def test_browser_evidence_names_a_browser_file_that_exists(crosswalk):
    """The field is declared, required on every row, and was empty on all 56.

    Nine `closed` notes assert a UI call site. Five of them are now driven by a
    browser file and name it; the other four are not driven by one and stay
    empty, which is the honest answer rather than the tidy one. What this check
    forbids is the field pointing at something that is not browser evidence.
    """
    populated = 0
    for item in crosswalk["items"]:
        evidence = item["browser_evidence"].strip()
        if not evidence:
            continue
        populated += 1
        for path in [p.strip() for p in evidence.split(",") if p.strip()]:
            assert path.startswith("tests/browser_") and path.endswith(".mjs"), (
                f"{item['source']}/{item['original_id']} names {path} as browser "
                "evidence, which is not a browser harness"
            )
            assert (ROOT / path).is_file(), f"{path} does not exist"
    assert populated, (
        "no row carries browser evidence; the field is declared, required and "
        "decides nothing -- which is the state this check was written for"
    )


def test_no_closed_row_rests_on_evidence_that_has_since_changed():
    """The claim's shelf life, which nothing was measuring.

    When this was written, 25 of the 48 `closed` rows named a test file that had
    been modified since the audit the document itself declares -- one of them
    across twelve commits. Every assertion above passed on all 25, because
    "names a file that exists" is a statement about the filesystem, not about
    the tree the claim was made against.

    Bumping `audited_at` once would reproduce that state over the next fifty
    commits, silently, which is how it arrived. So the digest is per row and
    over file *content*: a commit SHA cannot be checked from inside the commit
    that re-audits, which is precisely why the previous arrangement had no such
    check.
    """
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import reaudit_crosswalk
    finally:
        sys.path.pop(0)

    document = json.loads(CROSSWALK.read_text("utf-8"))
    drifted = reaudit_crosswalk.stale_rows(document)
    assert not drifted, (
        f"{len(drifted)} closed row(s) rest on evidence that has changed since "
        f"they were audited: {[key for key, _r, _o in drifted]}. Read them "
        "against their tests, then re-record with "
        "`uv run python scripts/reaudit_crosswalk.py`."
    )


def test_only_closed_rows_carry_an_evidence_digest():
    """`implemented_unverified` and `deferred_p2` claim nothing was proved, so
    there is nothing for a digest to attest. A digest on one of them would read
    as evidence and mean nothing -- the field's original failure mode, one
    column over."""
    for item in json.loads(CROSSWALK.read_text("utf-8"))["items"]:
        has_digest = bool(item.get("evidence_digest"))
        assert has_digest == (item["status"] == "closed"), (
            f"{item['source']}/{item['original_id']} is {item['status']} and "
            f"{'carries' if has_digest else 'lacks'} an evidence digest"
        )
