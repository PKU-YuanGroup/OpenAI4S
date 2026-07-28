"""What `@file` in a message actually sends, and what it says when it cannot.

The old resolver read the artifact's *live path*. Three consequences, and only
the first is obvious: the reference was unpinned, so the same message meant
different bytes after a later cell overwrote the file; an unresolvable name was
dropped in silence, so the user asked about a file the model never received;
and every artifact was decoded as UTF-8 with `errors="replace"`, so referencing
a `.npz` injected a wall of U+FFFD that reads as corrupted text rather than as
"this is not text".
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import artifact_refs
from openai4s.store import get_store


def _cfg(tmp_path):
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def _seed(cfg, store, root_frame_id, project_id, filename, payload):
    """One artifact version, live file and frozen snapshot kept separate.

    They are two files in reality — the live one sits in the session workspace
    and a later cell may overwrite it, while the snapshot is the immutable copy
    that gives a version its identity. Pointing both at one path (as an earlier
    draft of this helper did) makes the pinning test unable to fail, because
    rewriting the "live" file rewrites the frozen bytes too.
    """
    versions = Path(cfg.data_dir) / "artifact-versions"
    versions.mkdir(parents=True, exist_ok=True)
    workspace = Path(cfg.data_dir) / "agent-workspaces" / root_frame_id
    workspace.mkdir(parents=True, exist_ok=True)
    version_id = f"v-{uuid.uuid4().hex[:12]}"
    snapshot = versions / f"{version_id}__{filename}"
    snapshot.write_bytes(payload)
    live = workspace / filename
    live.write_bytes(payload)
    return store.record_cell_artifact(
        path=str(live),
        filename=filename,
        content_type="text/csv",
        size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        producing_cell_id=None,
        frame_id=root_frame_id,
        root_frame_id=root_frame_id,
        project_id=project_id,
        snapshot_path=str(snapshot),
    )


# --------------------------------------------------------------------------
# the patterns
# --------------------------------------------------------------------------


def test_the_legacy_pattern_is_not_defeated_by_backtracking():
    """`(?!#v-)` alone was not enough, and this is a bug I wrote and caught.

    `\\w+` gives back the final character of "csv", the lookahead then sees "v"
    rather than "#", and `@a.csv#v-abc123` produced a *phantom* legacy
    reference to "a.cs" alongside the real pinned one. The guard has to reject
    a trailing word character too.
    """
    text = "see @a.csv#v-abc123 and @plain.csv"
    assert artifact_refs.PINNED_REF.findall(text) == [("a.csv", "v-abc123")]
    assert artifact_refs.LEGACY_REF.findall(text) == ["plain.csv"]


def test_an_email_address_is_not_a_reference():
    for text in ("email me@example.com about it", "ping a.b@c.io"):
        assert artifact_refs.PINNED_REF.findall(text) == []
        assert artifact_refs.LEGACY_REF.findall(text) == []


# --------------------------------------------------------------------------
# pinning
# --------------------------------------------------------------------------


def test_a_pinned_reference_sends_the_version_it_names_not_the_live_file(tmp_path):
    """The defect, stated as a test.

    A later cell overwrites `results.csv`. The live file now holds v2. A
    message pinned to v1 must still send v1 — otherwise a replayed session
    shows a prompt whose content nobody can reconstruct.
    """
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    first = _seed(cfg, store, root, "p", "results.csv", b"v1 contents\n")

    # The live path now says something else entirely.
    Path(store.get_artifact(first["artifact_id"])["path"]).write_bytes(
        b"v2 REWRITTEN\n"
    )

    resolved, problems = artifact_refs.resolve_message_refs(
        f"look at @results.csv#{first['version_id']}",
        store=store,
        root_frame_id=root,
        project_id="p",
    )
    assert problems == []
    assert "v1 contents" in resolved
    assert "v2 REWRITTEN" not in resolved


def test_an_unresolvable_reference_is_reported_rather_than_dropped(tmp_path):
    """It used to `continue`. The block was simply absent from the prompt and
    the user was told nothing, so they asked a question about a file the model
    never saw and had no way to notice."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")

    resolved, problems = artifact_refs.resolve_message_refs(
        "check @missing.csv#v-000000000000 please",
        store=store,
        root_frame_id=root,
        project_id="p",
    )
    assert resolved == "check @missing.csv#v-000000000000 please"
    assert len(problems) == 1
    assert problems[0]["code"] == "not_found"
    assert "v-000000000000" in problems[0]["message"]


def test_a_version_in_another_project_is_indistinguishable_from_absent(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    theirs = store.new_frame(kind="turn", project_id="theirs")
    mine = store.new_frame(kind="turn", project_id="mine")
    seeded = _seed(cfg, store, theirs, "theirs", "secret.csv", b"classified\n")

    _resolved, problems = artifact_refs.resolve_message_refs(
        f"@secret.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=mine,
        project_id="mine",
    )
    assert problems[0]["code"] == "not_found"
    assert "classified" not in problems[0]["message"]


# --------------------------------------------------------------------------
# cross-session materialisation happens at send, and only then
# --------------------------------------------------------------------------


def test_a_sibling_session_reference_materialises_when_the_turn_is_sent(tmp_path):
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    source = store.new_frame(kind="turn", project_id="p")
    target = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, source, "p", "shared.csv", b"col\n1\n")

    brought: list[str] = []

    def _materialise(version_id, name):
        brought.append(version_id)
        return {"version_id": version_id}  # same bytes; identity is 2.6's job

    resolved, problems = artifact_refs.resolve_message_refs(
        f"@shared.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=target,
        project_id="p",
        materialise=_materialise,
    )
    assert problems == []
    assert brought == [seeded["version_id"]]
    assert "col" in resolved


def test_without_a_materialiser_a_sibling_reference_refuses_rather_than_reads(
    tmp_path,
):
    """D3 in one assertion: there is no path that reads another session's file
    in place. If the caller cannot bring it in, the answer is no."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    source = store.new_frame(kind="turn", project_id="p")
    target = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, source, "p", "shared.csv", b"col\n1\n")

    resolved, problems = artifact_refs.resolve_message_refs(
        f"@shared.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=target,
        project_id="p",
        materialise=None,
    )
    assert problems[0]["code"] == "cross_session_not_allowed"
    assert "col\n1" not in resolved


# --------------------------------------------------------------------------
# binary content
# --------------------------------------------------------------------------


def test_a_binary_artifact_is_named_rather_than_pasted_as_replacement_chars(
    tmp_path,
):
    """`decode(errors="replace")` turned a `.npz` into a wall of U+FFFD, which
    a model reads as corrupted text rather than as "this is a binary file" —
    a worse answer than saying so."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, root, "p", "matrix.npz", bytes(range(256)) * 8)

    resolved, problems = artifact_refs.resolve_message_refs(
        f"@matrix.npz#{seeded['version_id']}",
        store=store,
        root_frame_id=root,
        project_id="p",
    )
    assert problems[0]["code"] == "not_text"
    assert "�" not in resolved


def test_an_undeclared_binary_is_caught_by_how_badly_it_decodes(tmp_path):
    """A suffix allowlist cannot know every binary format. A `.csv` holding
    raw bytes is still not text, and the replacement-character density says so.
    """
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, root, "p", "mislabelled.csv", bytes(range(128, 256)) * 8)

    _resolved, problems = artifact_refs.resolve_message_refs(
        f"@mislabelled.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=root,
        project_id="p",
    )
    assert problems[0]["code"] == "not_text"


# --------------------------------------------------------------------------
# budgets
# --------------------------------------------------------------------------


def test_the_number_of_references_is_bounded_and_says_when_it_cuts(tmp_path):
    """A reference is cheap to type and expensive to send, so the prompt is the
    scarce resource. Cutting silently would be the same failure as dropping an
    unresolvable one."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    refs = []
    for index in range(artifact_refs.MAX_REFS + 2):
        seeded = _seed(cfg, store, root, "p", f"f{index}.csv", b"x\n")
        refs.append(f"@f{index}.csv#{seeded['version_id']}")

    _resolved, problems = artifact_refs.resolve_message_refs(
        " ".join(refs), store=store, root_frame_id=root, project_id="p"
    )
    cut = [p for p in problems if p["code"] == "too_many_refs"]
    assert len(cut) == 2, "the cut was not reported for every dropped reference"


def test_the_legacy_spelling_still_works_and_says_it_is_unpinned(tmp_path):
    """Kept for one minor release. It resolves through the artifact's latest
    *version* rather than the live path, so at least the bytes sent are a
    version that exists rather than whatever a concurrent cell left mid-write.
    """
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    _seed(cfg, store, root, "p", "legacy.csv", b"still here\n")

    resolved, problems = artifact_refs.resolve_message_refs(
        "look at @legacy.csv", store=store, root_frame_id=root, project_id="p"
    )
    assert problems == []
    assert "still here" in resolved
    assert "unpinned" in resolved


def test_a_legacy_reference_never_reaches_another_session(tmp_path):
    """Widening the unpinned spelling to the project would let a guessed
    filename pull in another session's file. The pinned form asks for that
    explicitly and gets checked for it; this one must not."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    theirs = store.new_frame(kind="turn", project_id="p")
    mine = store.new_frame(kind="turn", project_id="p")
    _seed(cfg, store, theirs, "p", "theirs.csv", b"not yours\n")

    resolved, problems = artifact_refs.resolve_message_refs(
        "@theirs.csv", store=store, root_frame_id=mine, project_id="p"
    )
    assert problems[0]["code"] == "not_found"
    assert "not yours" not in resolved


# --------------------------------------------------------------------------
# the budget the count and the per-file cap did not add up to
# --------------------------------------------------------------------------


def test_references_share_one_character_budget(tmp_path):
    """`MAX_REFS` bounds how many and `MAX_REF_BYTES` bounds each; neither
    bounds the product. Eight at the per-file cap is 1,600,000 characters —
    about 400,000 tokens against a 262,144-token window, so one message could
    exceed the whole context by half again, and be eight times the cap on the
    message a person actually types.

    Asserted against a fixed number rather than against the constant under
    test: writing `<= MAX_TOTAL_REF_BYTES` would keep passing if the budget
    were raised back to 1.6 MB, which is the state being fixed.
    """
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    payload = b"x" * 150_000
    refs = []
    for index in range(6):
        seeded = _seed(cfg, store, root, "p", f"big{index}.csv", payload)
        refs.append(f"@big{index}.csv#{seeded['version_id']}")

    resolved, problems = artifact_refs.resolve_message_refs(
        " ".join(refs), store=store, root_frame_id=root, project_id="p"
    )
    assert (
        len(resolved) < 500_000
    ), f"{len(resolved):,} characters reached the prompt from 6 references"
    cut = [p for p in problems if p["code"] == "ref_budget_exhausted"]
    assert cut, "references were dropped with nothing said"


def test_the_budget_cut_names_every_file_it_dropped(tmp_path):
    """Same rule as an unresolvable reference: the user asked a question about
    a file the model never received, and running out of budget is a different
    reason for that, not an exemption from saying so."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    payload = b"y" * 150_000
    refs = []
    for index in range(6):
        seeded = _seed(cfg, store, root, "p", f"f{index}.csv", payload)
        refs.append(f"@f{index}.csv#{seeded['version_id']}")

    _resolved, problems = artifact_refs.resolve_message_refs(
        " ".join(refs), store=store, root_frame_id=root, project_id="p"
    )
    dropped = [p for p in problems if p["code"] == "ref_budget_exhausted"]
    assert dropped
    for problem in dropped:
        assert problem["ref"].startswith("f")
        assert "budget" in problem["message"]


def test_a_single_ordinary_reference_is_unaffected(tmp_path):
    """A budget that catches the common case is a bug, not a bound."""
    cfg = _cfg(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="p")
    seeded = _seed(cfg, store, root, "p", "small.csv", b"a,b\n1,2\n")

    resolved, problems = artifact_refs.resolve_message_refs(
        f"@small.csv#{seeded['version_id']}",
        store=store,
        root_frame_id=root,
        project_id="p",
    )
    assert problems == []
    assert "1,2" in resolved
