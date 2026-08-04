"""The progress record's own structure, which nothing was checking.

`docs/next-version-progress.md` is the per-item completion record, and it is
read by section number: `§6`, `§12`, `§13` and `§15` all appear inside it as
cross-references. Two defects followed from nobody checking that:

* **It had two `## 15` sections**, with `## 16` sitting between them, so a `§15`
  reference had two possible referents and a reader following it landed on
  whichever came first. The one at the end of the file meant the second.
* **Its last section stopped 67 non-merge commits behind the tree.** A record
  that is far enough behind does not read as incomplete, it reads as "nothing
  has happened since" -- the same wrong-rather-than-absent shape this repository
  fixes everywhere else.

The second one cannot be checked by counting commits: a working copy is allowed
to be mid-change, and a gate that fired on every commit would be suppressed
within a week. What *is* checkable is that the two documents which both claim to
describe a tree agree about which tree that is. `plan-crosswalk.json` carries
`audited_at` and is now gated on its evidence digests, so tying this file's
latest audit to that field means the progress record cannot silently fall behind
the crosswalk -- and the crosswalk cannot silently fall behind the code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROGRESS = ROOT / "docs" / "next-version-progress.md"
CROSSWALK = ROOT / "docs" / "plan-crosswalk.json"

#: `## 12. Externally unverifiable` -- the numbered sections only. Unnumbered
#: headings (`## Status vocabulary`) are prose and are deliberately not matched.
SECTION = re.compile(r"^## (\d+)\. (.+)$", re.M)

#: `§13`, and `§13 above`. Not `§13.4`: the plan's own item numbering uses that
#: form and it refers into the plan, not into this file.
REFERENCE = re.compile(r"§(\d+)(?!\.\d)")


def _sections() -> list[tuple[int, str]]:
    text = PROGRESS.read_text("utf-8")
    return [(int(number), title) for number, title in SECTION.findall(text)]


def test_no_section_number_is_used_twice():
    """Two `## 15` sections is not a typo, it is an ambiguous address.

    Every `§N` in this file resolves by number, so a duplicate silently gives
    one reference two meanings -- and the reader follows the first, which was
    the wrong one for the reference that existed.
    """
    numbers = [number for number, _title in _sections()]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not duplicates, (
        f"these section numbers appear more than once: {duplicates}; "
        f"sections are {[f'{n}. {t[:30]}' for n, t in _sections()]}"
    )


def test_the_sections_are_in_ascending_order():
    """The file is read top to bottom and its sections are chronological. A
    number that goes backwards means a section was inserted where it does not
    belong -- which is how `## 16` came to sit between the two `## 15`s."""
    numbers = [number for number, _title in _sections()]
    out_of_order = [
        (numbers[i - 1], numbers[i])
        for i in range(1, len(numbers))
        if numbers[i] < numbers[i - 1]
    ]
    assert not out_of_order, f"section numbers go backwards at: {out_of_order}"


def test_the_numbering_has_no_gaps():
    """A gap means a section was deleted and the references into it now point at
    nothing. Renumbering the rest is the alternative and it breaks every
    reference at once, so the rule is: sections are 1..N."""
    numbers = [number for number, _title in _sections()]
    assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_every_cross_reference_resolves_to_a_section():
    """A `§N` pointing past the end of the file is a citation to nothing, and it
    reads exactly like one that resolves."""
    text = PROGRESS.read_text("utf-8")
    known = {number for number, _title in _sections()}
    dangling = sorted({int(n) for n in REFERENCE.findall(text)} - known)
    assert not dangling, f"these cross-references name no section: {dangling}"


def test_the_progress_record_names_the_tree_the_crosswalk_was_audited_against():
    """The two documents must describe the same tree.

    Both claim to record the state of this repository, and only one of them was
    gated. Without this, the crosswalk can be re-audited while the progress
    record keeps describing a commit two months older -- which is the state this
    check was written in, at 67 commits.

    The progress file names the commit in prose rather than in a field, so the
    assertion is that the crosswalk's `audited_at` appears in it at all. That is
    a low bar deliberately: it forces the person re-auditing to write the new
    commit down here too, and it cannot be satisfied by leaving this file alone.
    """
    audited_at = json.loads(CROSSWALK.read_text("utf-8"))["audited_at"]
    text = PROGRESS.read_text("utf-8")
    assert audited_at[:12] in text, (
        f"the crosswalk was audited at {audited_at[:12]} and this file does not "
        "mention that commit; one of the two documents is describing a tree the "
        "other one is not"
    )
