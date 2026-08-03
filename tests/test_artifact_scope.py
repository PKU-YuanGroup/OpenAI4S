"""Frame/root/project ownership contracts for artifacts and Web sessions."""

from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server.gateway import SessionRunner
from openai4s.store import Store, get_store


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        pass


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def test_child_frames_and_artifacts_inherit_root_project_scope(tmp_path):
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="project-science")
    child = store.new_frame(parent_id=root, kind="delegate")
    grandchild = store.new_frame(
        parent_id=child,
        project_id="wrong-project",
        kind="delegate",
    )

    assert store.get_frame(child)["project_id"] == "project-science"
    assert store.get_frame(grandchild)["project_id"] == "project-science"
    assert store.resolve_frame_scope(grandchild) == {
        "frame_id": grandchild,
        "root_frame_id": root,
        "project_id": "project-science",
    }

    record = store.save_artifact(
        path="/workspace/result.csv",
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="result",
        producing_cell_id="cell-child",
        frame_id=grandchild,
    )
    artifact = store.get_artifact(record["artifact_id"])
    version = store.version_meta(record["version_id"])

    assert artifact["root_frame_id"] == root
    assert artifact["project_id"] == "project-science"
    assert version["frame_id"] == grandchild


def test_root_capture_finalizes_child_provenance_without_changing_producer(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    root = store.new_frame(kind="turn", project_id="project-science")
    child = store.new_frame(parent_id=root, kind="delegate")
    path = tmp_path / "result.csv"
    path.write_text("x\n1\n")

    provenance = store.record_cell_artifact(
        path=str(path),
        filename="result.csv",
        content_type=None,
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-child",
        frame_id=child,
    )
    capture = store.record_cell_artifact(
        path=str(path),
        filename="result.csv",
        content_type="text/csv",
        size_bytes=4,
        checksum="same",
        producing_cell_id="cell-child",
        frame_id=root,
        root_frame_id=root,
        project_id="project-science",
    )

    assert capture["artifact_id"] == provenance["artifact_id"]
    assert capture["version_id"] == provenance["version_id"]
    artifact = store.get_artifact(provenance["artifact_id"])
    metadata = store.version_meta(provenance["version_id"])
    assert artifact["root_frame_id"] == root
    assert artifact["project_id"] == "project-science"
    assert metadata["frame_id"] == child


def test_artifact_rejects_version_from_different_root(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    first_root = store.new_frame(kind="turn", project_id="project-one")
    second_root = store.new_frame(kind="turn", project_id="project-two")
    record = store.save_artifact(
        path="/workspace/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="one",
        frame_id=first_root,
    )

    with pytest.raises(ValueError, match="different root frame"):
        store.save_artifact(
            path="/other/result.txt",
            filename="result.txt",
            content_type="text/plain",
            size_bytes=3,
            checksum="two",
            frame_id=second_root,
            artifact_id=record["artifact_id"],
        )

    assert len(store.list_versions(record["artifact_id"])) == 1


def test_existing_artifact_without_explicit_scope_inherits_its_owner(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    root = store.new_frame(kind="turn", project_id="project-existing")
    first = store.save_artifact(
        path="/workspace/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="one",
        frame_id=root,
    )

    second = store.save_artifact(
        path="/workspace/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="two",
        artifact_id=first["artifact_id"],
    )

    artifact = store.get_artifact(first["artifact_id"])
    assert artifact["project_id"] == "project-existing"
    assert artifact["root_frame_id"] == root
    assert artifact["latest_version_id"] == second["version_id"]


def test_known_producer_rejects_conflicting_explicit_root(tmp_path):
    store = get_store(_config(tmp_path).db_path)
    root = store.new_frame(kind="turn", project_id="project-one")
    child = store.new_frame(parent_id=root, kind="delegate")
    other = store.new_frame(kind="turn", project_id="project-two")

    with pytest.raises(ValueError, match="conflicts with producer frame"):
        store.save_artifact(
            path="/workspace/result.txt",
            filename="result.txt",
            content_type="text/plain",
            size_bytes=3,
            checksum="bad-scope",
            frame_id=child,
            root_frame_id=other,
        )

    assert store.list_artifacts({"root_frame_id": other}) == []


def test_unknown_frame_keeps_legacy_scope_fallback(tmp_path):
    store = get_store(_config(tmp_path).db_path)

    orphan = store.new_frame(parent_id="missing-parent", kind="delegate")
    assert store.get_frame(orphan)["root_frame_id"] == orphan

    record = store.save_artifact(
        path="/legacy/result.txt",
        filename="result.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="legacy",
        frame_id="legacy-frame",
        project_id="legacy-project",
    )

    artifact = store.get_artifact(record["artifact_id"])
    assert artifact["root_frame_id"] == "legacy-frame"
    assert artifact["project_id"] == "legacy-project"


def test_store_migration_repairs_historical_child_scope(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="project-migrated")
    child = store.new_frame(parent_id=root, kind="delegate")
    record = store.save_artifact(
        path="/workspace/old.txt",
        filename="old.txt",
        content_type="text/plain",
        size_bytes=3,
        checksum="old",
        frame_id=child,
    )
    store._conn.execute(
        "UPDATE frames SET project_id='default' WHERE frame_id=?",
        (child,),
    )
    store._conn.execute(
        "UPDATE artifacts SET project_id='default',root_frame_id=? "
        "WHERE artifact_id=?",
        (child, record["artifact_id"]),
    )
    # Make the simulation of "historical" faithful. The rows above are hand-made
    # to look like data written by a version that predates project_id
    # inheritance, so the database they live in has to look that old too — the
    # repair is a one-time migration, not a healer that re-runs on every open.
    # (It used to re-run every open only because there was no version to know
    # better; that meant a full-table UPDATE over frames and artifacts on every
    # single Store construction. Real databases still get repaired: the upgrade
    # path is v0 -> run the baseline, which includes this repair -> stamp v1.)
    store._conn.execute("PRAGMA user_version = 0")
    store._conn.commit()
    store.close()

    reopened = Store(cfg.db_path)
    try:
        assert reopened.get_frame(child)["project_id"] == "project-migrated"
        artifact = reopened.get_artifact(record["artifact_id"])
        assert artifact["project_id"] == "project-migrated"
        assert artifact["root_frame_id"] == root
    finally:
        reopened.close()


def test_web_state_requires_root_id_and_uses_root_project_as_authority(tmp_path):
    cfg = _config(tmp_path)
    runner = SessionRunner(cfg, _Hub())
    root = runner.store.new_frame(kind="turn", project_id="project-web")
    child = runner.store.new_frame(parent_id=root, kind="delegate")

    with pytest.raises(ValueError, match="require a root frame id"):
        runner._state(child, "wrong-project")

    state = runner._state(root, "wrong-project")

    assert state.root_frame_id == root
    assert state.project_id == "project-web"
    assert state.workspace == runner.workspace_for(root)


def test_serving_an_artifact_by_filename_refuses_an_ambiguous_name(tmp_path):
    """`GET /artifacts/<name>` picked a project for you.

    The fallback resolved a filename with `ORDER BY created_at DESC LIMIT 1`
    across the whole installation, so a name shared by two projects served
    whichever one was written most recently -- correct content-type, plausible
    bytes, wrong file, no signal. For a tool whose artifacts are research data
    that is worse than serving nothing.

    Two matches is an ambiguous question, and the honest answer to an ambiguous
    question is not one of the candidates. The UI always addresses artifacts by
    id, so nothing first-party depended on the guess.
    """
    store = get_store(tmp_path / "ambiguous.db")
    try:
        shared = tmp_path / "report.pdf"
        shared.write_bytes(b"alpha bytes")
        first = store.save_artifact(
            path=str(shared),
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=shared.stat().st_size,
            checksum="a" * 64,
            frame_id="f-alpha",
            root_frame_id="f-alpha",
            project_id="alpha",
        )
        # A unique name still resolves.
        assert store.artifact_by_unique_filename("report.pdf")["artifact_id"] == (
            first["artifact_id"]
        )

        other = tmp_path / "beta-report.pdf"
        other.write_bytes(b"beta bytes")
        store.save_artifact(
            path=str(other),
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=other.stat().st_size,
            checksum="b" * 64,
            frame_id="f-beta",
            root_frame_id="f-beta",
            project_id="beta",
        )

        # Now two projects own the name, so it names nothing.
        assert store.artifact_by_unique_filename("report.pdf") is None
        # And the old lookup would still have answered, with one of them.
        assert store.artifact_by_filename("report.pdf") is not None
        assert store.artifact_by_unique_filename("never-created.pdf") is None
    finally:
        store.close()


def test_version_keyed_reads_are_confined_to_the_calling_session(tmp_path):
    """`lineage_get` handed out another project's provenance.

    `_scoped_artifact` covers the reads keyed on an artifact id. The
    version-keyed ones went straight to the store, so a kernel cell in one
    project could name any version id and read back the filename, checksum,
    frame, producing-cell **code** and input lineage of an artifact belonging
    to another project. Guessing a version id is the only barrier, and
    `lineage_graph` walks outward from whatever it is given.

    Scope lives on the parent `artifacts` row -- `artifact_versions` carries
    neither project_id nor root_frame_id -- so resolving the parent is not an
    extra query for convenience, it is the only place the answer exists.

    A foreign version and a nonexistent one fail identically. A distinct
    refusal would confirm the version exists, which is most of what an
    enumerator wants.
    """
    from openai4s.host_dispatch import build_dispatcher

    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, workspace=tmp_path / "ws")
    mine = dispatcher.store.new_frame(kind="turn", project_id="mine")
    theirs = dispatcher.store.new_frame(kind="turn", project_id="theirs")

    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    secret = workspace / "secret.csv"
    secret.write_text("private", encoding="utf-8")
    foreign = dispatcher.store.save_artifact(
        path=str(secret),
        filename="secret.csv",
        content_type="text/csv",
        size_bytes=secret.stat().st_size,
        checksum="c" * 64,
        frame_id=theirs,
    )
    ours_path = workspace / "ours.csv"
    ours_path.write_text("mine", encoding="utf-8")
    ours = dispatcher.store.save_artifact(
        path=str(ours_path),
        filename="ours.csv",
        content_type="text/csv",
        size_bytes=ours_path.stat().st_size,
        checksum="d" * 64,
        frame_id=mine,
    )

    dispatcher.frame_id = mine

    # Our own version still reads, or the confinement would be useless.
    assert (
        dispatcher("lineage_get", [{"version_id": ours["version_id"]}])["filename"]
        == "ours.csv"
    )
    dispatcher("lineage_graph", [{"version_id": ours["version_id"]}])

    for method in ("lineage_get", "lineage_graph"):
        with pytest.raises(KeyError):
            dispatcher(method, [{"version_id": foreign["version_id"]}])
        with pytest.raises(KeyError):
            dispatcher(method, [{"version_id": "v-does-not-exist"}])


def _materialisation_service(cfg, store, frame_id, workspace):
    """A real HostDataService over the real Store -- no double.

    The whole subject here is one transaction plus a scope check, and both are
    exactly what a fake would have to reimplement to be useful. A fake that got
    the scope rule subtly wrong would pass while the real one leaked.
    """
    from openai4s.host.data import HostDataService

    workspace.mkdir(parents=True, exist_ok=True)

    def _resolve(path, must_exist=False):
        target = (workspace / path).resolve()
        if must_exist and not target.exists():
            raise FileNotFoundError(target)
        return target

    return HostDataService(
        store=store, config=cfg, frame_id=lambda: frame_id, resolve_path=_resolve
    )


def _seed_version(cfg, store, root_frame_id, project_id, filename, payload):
    """One artifact version with real frozen bytes, as a cell write would make."""
    import hashlib
    import uuid

    versions = Path(cfg.data_dir) / "artifact-versions"
    versions.mkdir(parents=True, exist_ok=True)
    version_id = f"v-{uuid.uuid4().hex[:12]}"
    snapshot = versions / f"{version_id}__{filename}"
    snapshot.write_bytes(payload)
    row = store.record_cell_artifact(
        path=str(snapshot),
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
    return row


def test_materialising_a_sibling_session_artifact_copies_identity_not_the_bytes(
    tmp_path,
):
    """D3: nothing reads another session's file in place.

    A cross-session read leaves the borrowing session holding an analysis whose
    input has no version in its own history -- delete or revert the source and
    this session's provenance becomes unresolvable, silently. Materialising
    gives the target its own Artifact and version plus an edge back, so the
    question keeps an answer that does not depend on the other session.

    The bytes are shared by hardlink. A version snapshot is immutable by
    contract, so two rows may name one inode, and a materialised multi-gigabyte
    dataset costs a directory entry rather than a second copy.
    """
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    source_root = store.new_frame(kind="turn", project_id="proj-a")
    target_root = store.new_frame(kind="turn", project_id="proj-a")
    payload = b"col\n1\n2\n"
    seeded = _seed_version(cfg, store, source_root, "proj-a", "data.csv", payload)

    service = _materialisation_service(cfg, store, target_root, tmp_path / "ws-target")
    result = service.materialise_artifact({"version_id": seeded["version_id"]})

    assert result["materialised_from_version_id"] == seeded["version_id"]
    assert result["checksum"] == seeded["checksum"]

    # It is the target session's own artifact now.
    materialised = store.get_artifact(result["artifact_id"])
    assert materialised["root_frame_id"] == target_root
    assert materialised["project_id"] == "proj-a"
    assert materialised["artifact_id"] != seeded["artifact_id"]

    # ...with an edge back, so lineage crosses the session boundary even though
    # no read ever does.
    inputs = store.lineage_inputs(result["version_id"])
    assert [row["version_id"] for row in inputs] == [seeded["version_id"]]

    # One inode, two names: shared, not duplicated.
    source_snapshot = Path(store.version_meta(seeded["version_id"])["snapshot_path"])
    target_snapshot = Path(
        result["snapshot_path"]
        if "snapshot_path" in result
        else store.version_meta(result["version_id"])["snapshot_path"]
    )
    assert target_snapshot.read_bytes() == payload
    assert source_snapshot.stat().st_ino == target_snapshot.stat().st_ino


def test_a_version_in_another_project_is_indistinguishable_from_one_that_is_absent(
    tmp_path,
):
    """The refusal must not be a disclosure.

    A distinct "forbidden" would confirm the version exists, which is most of
    what an enumerator wants: version ids are short and a caller can grind
    them. Both answers are the same KeyError with the same message.
    """
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    other_root = store.new_frame(kind="turn", project_id="proj-other")
    mine_root = store.new_frame(kind="turn", project_id="proj-mine")
    seeded = _seed_version(cfg, store, other_root, "proj-other", "secret.csv", b"x")

    service = _materialisation_service(cfg, store, mine_root, tmp_path / "ws-mine")

    with pytest.raises(KeyError) as cross_project:
        service.materialise_artifact({"version_id": seeded["version_id"]})
    with pytest.raises(KeyError) as absent:
        service.materialise_artifact({"version_id": "v-000000000000"})

    # Same shape, differing only in the id the caller supplied.
    assert str(cross_project.value).replace(seeded["version_id"], "ID") == str(
        absent.value
    ).replace("v-000000000000", "ID")
    # And nothing was written for the refused call.
    assert store.list_artifacts({"root_frame_id": mine_root}) == []


def test_materialising_from_the_same_session_is_refused_as_pointless(tmp_path):
    """It would mint a second identity for a file the session already owns,
    and a lineage edge from a version to its own copy."""
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    root = store.new_frame(kind="turn", project_id="proj-a")
    seeded = _seed_version(cfg, store, root, "proj-a", "mine.csv", b"y")

    service = _materialisation_service(cfg, store, root, tmp_path / "ws")
    with pytest.raises(ValueError, match="already belongs to this session"):
        service.materialise_artifact({"version_id": seeded["version_id"]})


def test_a_version_whose_frozen_bytes_are_gone_says_so(tmp_path):
    """Distinct from "not found" on purpose: the version is real, and a caller
    that cannot tell the two apart cannot tell a scope refusal from a storage
    problem."""
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    source_root = store.new_frame(kind="turn", project_id="proj-a")
    target_root = store.new_frame(kind="turn", project_id="proj-a")
    seeded = _seed_version(cfg, store, source_root, "proj-a", "gone.csv", b"z")
    Path(store.version_meta(seeded["version_id"])["snapshot_path"]).unlink()

    service = _materialisation_service(cfg, store, target_root, tmp_path / "ws2")
    with pytest.raises(FileNotFoundError, match="no frozen snapshot"):
        service.materialise_artifact({"version_id": seeded["version_id"]})


# --- the capability gate in front of all of it -----------------------------
#
# Plan section 7.1 requires same-project cross-session access to pass an
# explicit capability. There was none. `save_artifact` -- persisting bytes the
# cell already had -- was in `GATEABLE_TOOLS`; `materialise_artifact`, which
# brings in bytes from a session the caller was never given, was not. The
# message path was worse than ungated: `_materialise_for_message` reached past
# `HostDispatcher.__call__` for the private `_data_service`, so the copy was
# also unaudited and produced no step event, and a `@mention` in
# model-authored plan text reaches that path.
#
# Every test above drives `service.materialise_artifact` directly, which is why
# none of them noticed: the gate is on the dispatcher, so a test that skips the
# dispatcher tests the byte-level copy and nothing about who may ask for it.


def _dispatcher_for(cfg, frame_id):
    from openai4s.host_dispatch import HostDispatcher

    return HostDispatcher(cfg=cfg, frame_id=frame_id)


def test_materialise_is_refused_without_approval(tmp_path, monkeypatch):
    """Deny-by-default is the suite's posture, so the refusal is the default."""
    monkeypatch.setenv("OPENAI4S_UNATTENDED_APPROVAL", "deny")
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    source_root = store.new_frame(kind="turn", project_id="proj-a")
    target_root = store.new_frame(kind="turn", project_id="proj-a")
    seeded = _seed_version(cfg, store, source_root, "proj-a", "data.csv", b"col\n1\n")

    dispatcher = _dispatcher_for(cfg, target_root)
    out = dispatcher(
        "materialise_artifact",
        [{"version_id": seeded["version_id"], "filename": "data.csv"}],
    )

    assert isinstance(out, dict) and set(out) == {"error"}, out
    assert "Permission denied" in out["error"]
    store.close()


def test_materialise_is_a_gateable_method_at_all(tmp_path):
    """The membership itself, because the refusal above could come from
    anywhere -- a missing artifact, a bad scope -- and still look right."""
    from openai4s import host_dispatch

    assert "materialise_artifact" in host_dispatch.GATEABLE_TOOLS
    # And the card is readable: without a view the broker renders the bare
    # method name, and a gate nobody can read is a gate everybody clicks
    # through.
    view = host_dispatch._step_begin(
        "materialise_artifact", [{"filename": "data.csv", "version_id": "v-1"}]
    )
    assert view is not None
    kind, title, meta = view
    assert kind == "artifact"
    assert "data.csv" in title
    assert meta.get("filename") == "data.csv"


def test_the_message_path_goes_through_the_dispatcher(tmp_path):
    """`_materialise_for_message` must not reach past the gate.

    Behavioural, not source-text: an earlier draft asserted `_data_service`
    was absent from the source and tripped on the word appearing in the new
    docstring. What matters is that the call arrives at the dispatcher, which
    is where the permission gate, `log_host_call` and the step event live.
    """
    from types import SimpleNamespace

    from openai4s.server.gateway import SessionRunner

    seen: list = []

    def fake_dispatcher(method, args):
        seen.append((method, args))
        return {"version_id": "v-new"}

    st = SimpleNamespace(dispatcher=fake_dispatcher)
    out = SessionRunner._materialise_for_message(
        SimpleNamespace(), st, "v-src", "data.csv"
    )

    assert out == {"version_id": "v-new"}
    assert seen == [
        ("materialise_artifact", [{"version_id": "v-src", "filename": "data.csv"}])
    ], seen
