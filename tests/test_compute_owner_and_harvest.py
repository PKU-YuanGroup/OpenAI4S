"""Owner-scoped idempotency, and a harvest that becomes an Artifact.

Two defects on complete production call chains.

**The idempotency namespace is installation-global while every other view of
`compute_jobs` is per-owner.** `by_idempotency_key` is `WHERE idempotency_key=?`
with no owner predicate, and the UNIQUE index covers the key alone — although the
same table carries `owner_key` and `live(scoped=True)`/`for_owner` both use it.
The repository's own docstring for `for_owner` says an optional scope flag is
"exactly the shape that leaks"; the reasoning was never applied to the key lookup.
So session B submitting under a key session A happened to use gets back A's
`job_id` and A's status in the refusal text, and cannot use that key for its own
work — while `job_history` and `reconcile` cannot even see the row that is
blocking it.

**`GetRemoteComputeJobResultTool` does not declare `writes_files = True`.** The
Web control-tool wrapper gates on exactly that attribute, so on the native path a
harvest of N files produces **zero** Artifact versions: the bytes land in
`<workspace>/hpc/<job_id>/` invisible to the Timeline, the artifact list, lineage
and the completion projection. The in-kernel SDK path captures them correctly,
which is why this looked fine — the two paths disagreed and only one was
exercised.

No test here contacts a provider. The manager's remote calls are the part these
defects are *not* in: both are decided in SQLite and in the Gateway wrapper,
before and after the remote respectively.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.store import get_store


def _cfg(tmp_path) -> Config:
    return Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


# --- item 14: idempotency is the caller's namespace, not the installation's ---


@pytest.fixture
def store(tmp_path):
    st = get_store(_cfg(tmp_path).db_path)
    try:
        yield st
    finally:
        st.close()


def _create(store, *, job_id, owner_key, key="shared-key"):
    store.create_compute_job(
        job_id=job_id,
        provider="fake",
        status="staging",
        idempotency_key=key,
        outputs=["out.txt"],
        owner_key=owner_key,
    )


def test_one_owners_key_is_invisible_to_another(store):
    """The lookup that decides the refusal must not see another owner's row.

    This is the information-disclosure half: the refusal text names the other
    session's `job_id` and status.
    """
    _create(store, job_id="job-aaaa", owner_key="owner-a")

    assert (
        store.compute_job_by_idempotency_key("shared-key", owner_key="owner-b") is None
    ), "owner B can see owner A's job through the idempotency key"


def test_an_owner_still_sees_its_own_key(store):
    """The success path: idempotency is the feature, so the scoping must not
    disable it."""
    _create(store, job_id="job-aaaa", owner_key="owner-a")

    found = store.compute_job_by_idempotency_key("shared-key", owner_key="owner-a")
    assert found is not None
    assert found["job_id"] == "job-aaaa"


def test_two_owners_may_hold_the_same_key(store):
    """The denial-of-service half: B could not submit distinct work under a key A
    happened to use, and could not clear it -- the row is A's, and B's
    `job_history`/`reconcile` cannot see it."""
    _create(store, job_id="job-aaaa", owner_key="owner-a")
    _create(store, job_id="job-bbbb", owner_key="owner-b")

    assert (
        store.compute_job_by_idempotency_key("shared-key", owner_key="owner-a")[
            "job_id"
        ]
        == "job-aaaa"
    )
    assert (
        store.compute_job_by_idempotency_key("shared-key", owner_key="owner-b")[
            "job_id"
        ]
        == "job-bbbb"
    )


def test_one_owner_still_cannot_reuse_its_own_key(store):
    """Scoping the namespace must not weaken the guarantee inside it. The UNIQUE
    index is the only thing that stops a raced second billable remote run, so it
    has to still bite for one owner."""
    import sqlite3

    _create(store, job_id="job-aaaa", owner_key="owner-a")
    with pytest.raises(sqlite3.IntegrityError):
        _create(store, job_id="job-cccc", owner_key="owner-a")


def test_two_cli_rows_still_cannot_share_a_key(store):
    """`owner_key` is NULL for CLI rows, and SQLite treats NULLs as *distinct* in
    a composite UNIQUE index -- so the obvious `(owner_key, idempotency_key)`
    index would silently stop protecting the CLI. The index has to collapse NULL
    owners into one namespace."""
    import sqlite3

    _create(store, job_id="job-cli1", owner_key=None)
    with pytest.raises(sqlite3.IntegrityError):
        _create(store, job_id="job-cli2", owner_key=None)


def test_a_cli_row_and_a_session_row_may_share_a_key(store):
    """...and a NULL owner is still a different namespace from a named one."""
    _create(store, job_id="job-cli1", owner_key=None)
    _create(store, job_id="job-sess", owner_key="owner-a")
    assert (
        store.compute_job_by_idempotency_key("shared-key", owner_key=None)["job_id"]
        == "job-cli1"
    )


def test_the_manager_refusal_names_only_the_callers_own_job(tmp_path):
    """Through the real `_claim`, which is what the production submit path calls.

    A scoped repository is only load-bearing if the manager passes its owner.
    """
    from openai4s.compute.manager import ComputeManager

    cfg = _cfg(tmp_path)
    st = get_store(cfg.db_path)
    try:
        _create(st, job_id="job-other", owner_key="somebody-else")
        manager = ComputeManager(cfg, store=st, workspace=tmp_path / "mine")
        # Same key, different owner: this must be claimable, and the claim must
        # not mention the other job.
        job_id = manager._claim("fake", "shared-key", ["out.txt"])
        assert job_id.startswith("job-")
        assert job_id != "job-other"
    finally:
        st.close()


def test_the_manager_still_refuses_the_callers_own_duplicate(tmp_path):
    from openai4s.compute.manager import ComputeError, ComputeManager

    cfg = _cfg(tmp_path)
    st = get_store(cfg.db_path)
    try:
        manager = ComputeManager(cfg, store=st, workspace=tmp_path / "mine")
        first = manager._claim("fake", "shared-key", ["out.txt"])
        with pytest.raises(ComputeError) as error:
            manager._claim("fake", "shared-key", ["out.txt"])
        assert first in str(error.value)
    finally:
        st.close()


# --- item 15: a native harvest has to become Artifact versions ---------------


def test_the_result_tool_declares_that_it_writes_files():
    """The Web wrapper gates on exactly this attribute and returns early without
    it, so the declaration *is* the wiring."""
    from openai4s.tools.registry import get_tool

    tool = get_tool("compute_result")
    assert tool is not None
    assert tool.writes_files is True


def test_a_native_harvest_creates_one_artifact_version_per_file(tmp_path):
    """Driven through `_invoke_control_with_artifacts`, the real Web wrapper.

    Without `writes_files` the wrapper returns at its first branch, no `before`
    snapshot is taken, and the harvested bytes cannot be recovered as artifacts
    afterwards -- the mtime diff has nothing to diff against.
    """
    from openai4s.server import gateway as gateway_mod

    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    events: list = []

    harvest_dir = state.workspace / "hpc" / "job-abc123"

    def harvest():
        harvest_dir.mkdir(parents=True, exist_ok=True)
        (harvest_dir / "model.pdb").write_text("ATOM\n", encoding="utf-8")
        (harvest_dir / "confidence.json").write_text("{}", encoding="utf-8")
        return {"status": "succeeded"}, True

    result = runner._invoke_control_with_artifacts(
        state,
        SimpleNamespace(name="compute_result"),
        events.append,
        harvest,
    )
    assert result == ({"status": "succeeded"}, True)

    # Registered under the workspace-relative path, so a nested harvest keeps
    # its shape rather than colliding on basenames across jobs.
    for filename in (
        "hpc/job-abc123/model.pdb",
        "hpc/job-abc123/confidence.json",
    ):
        artifact = runner.store.artifact_by_filename(filename, frame_id, strict=True)
        assert artifact is not None, f"{filename} was harvested but never registered"
        versions = runner.store.list_versions(artifact["artifact_id"])
        assert len(versions) == 1, (
            f"{filename} produced {len(versions)} versions; a harvest must create "
            f"exactly one per new file"
        )


def test_polling_again_does_not_capture_the_same_files_twice(tmp_path):
    """A poll is idempotent on the manager side, and the wrapper must not turn a
    repeat into a second version of unchanged bytes."""
    from openai4s.server import gateway as gateway_mod

    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    state = runner._state(frame_id, "default")
    events: list = []
    harvest_dir = state.workspace / "hpc" / "job-abc123"

    def harvest():
        # Faithful to `_publish_harvest`, which does not republish on a repeat
        # poll: the archive is extracted and published once, and a later poll
        # only reads the already-terminal state. A test that rewrote the file
        # every time would be measuring its own fixture.
        harvest_dir.mkdir(parents=True, exist_ok=True)
        target = harvest_dir / "model.pdb"
        if not target.exists():
            target.write_text("ATOM\n", encoding="utf-8")
        return {"status": "succeeded"}, True

    for _ in range(3):
        runner._invoke_control_with_artifacts(
            state, SimpleNamespace(name="compute_result"), events.append, harvest
        )

    artifact = runner.store.artifact_by_filename(
        "hpc/job-abc123/model.pdb", frame_id, strict=True
    )
    versions = runner.store.list_versions(artifact["artifact_id"])
    assert len(versions) == 1, (
        f"three identical polls produced {len(versions)} versions; unchanged bytes "
        f"must not become a new version"
    )


def test_a_writing_tool_without_a_caller_named_path_is_still_covered(tmp_path):
    """`secret_path_key` refuses a caller-supplied destination that names a
    secret. `compute_result` has no such argument -- the destination is derived
    from a regex-sanitised, containment-checked `job_id` -- so the existing
    every-writing-tool invariant has to distinguish the two rather than be
    loosened, and the exemption has to be backed by the confinement it claims.
    """
    from openai4s.compute.manager import ComputeError, ComputeManager
    from openai4s.tools.registry import get_tool

    tool = get_tool("compute_result")
    assert tool.derived_write_path is True
    assert tool.secret_path_key is None

    cfg = _cfg(tmp_path)
    st = get_store(cfg.db_path)
    try:
        workspace = tmp_path / "ws"
        workspace.mkdir(parents=True, exist_ok=True)
        manager = ComputeManager(cfg, store=st, workspace=workspace)
        # A traversing job id cannot leave the harvest root.
        dest = manager._safe_harvest_dest("../../../../etc/passwd")
        assert (
            manager._hpc_root.resolve() in dest.resolve().parents
            or dest.resolve() == (manager._hpc_root.resolve())
        )
        assert ".." not in str(dest.resolve())
        # And a symlinked harvest directory is refused rather than followed.
        manager._hpc_root.mkdir(parents=True, exist_ok=True)
        link = manager._hpc_root / "linked"
        link.symlink_to(tmp_path)
        with pytest.raises(ComputeError):
            manager._safe_harvest_dest("linked")
    finally:
        st.close()


def test_the_browser_refresh_route_registers_what_it_harvested(tmp_path, monkeypatch):
    """The same gap on a narrower path: `POST /frames/<id>/compute/tasks/<job>/refresh`
    took no snapshot at all, so a person clicking Refresh got the bytes published
    into `hpc/<job_id>/` and no Artifact version, no Timeline entry, no lineage.
    The route's docstring claimed the manager "registers artifacts"; it does not.
    """
    from openai4s.server import gateway as gateway_mod

    runner = gateway_mod.SessionRunner(_cfg(tmp_path), _Hub())
    frame_id = runner.store.new_frame(kind="turn", project_id="default", status="ready")
    workspace = runner.active_workspace_for(frame_id)

    class _Manager:
        def result(self, spec):
            out = workspace / "hpc" / spec["job_id"]
            out.mkdir(parents=True, exist_ok=True)
            (out / "plddt.csv").write_text("1,0.9\n", encoding="utf-8")
            return {"status": "succeeded"}

    monkeypatch.setattr(
        gateway_mod,
        "build_dispatcher",
        lambda *a, **k: SimpleNamespace(compute=_Manager()),
    )
    runner.store.create_compute_job(
        job_id="job-refresh",
        provider="fake",
        status="succeeded",
        idempotency_key=None,
        outputs=["plddt.csv"],
        owner_key=str(workspace),
    )

    task = runner.refresh_compute_task(frame_id, "job-refresh")
    assert task["polled"] is True

    artifact = runner.store.artifact_by_filename(
        "hpc/job-refresh/plddt.csv", frame_id, strict=True
    )
    assert (
        artifact is not None
    ), "the refresh harvested a file and registered no Artifact version"


def test_the_idempotency_index_is_replaced_on_an_existing_database(tmp_path):
    """Replacing an index is not additive, so it needs a real migration step.

    Ordering matters and is asserted by the step itself: the new index is built
    *before* the old one is dropped, so a database holding a same-owner duplicate
    fails with the old constraint still in place rather than losing it first.
    """
    import sqlite3

    from openai4s.store import get_store

    db = tmp_path / "old.db"
    store = get_store(db)
    store.close()

    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX IF EXISTS ix_compute_jobs_idem_owner")
    conn.execute(
        "CREATE UNIQUE INDEX ix_compute_jobs_idem ON compute_jobs(idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    conn.execute("PRAGMA user_version = 10")
    conn.commit()
    conn.close()

    store = get_store(db)
    try:
        state = store.schema_state()
        assert state["version"] == state["expected"]
        names = {
            row["name"]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name LIKE 'ix_compute_jobs_idem%'"
            ).fetchall()
        }
        assert names == {
            "ix_compute_jobs_idem_owner"
        }, f"the global index survived the upgrade: {sorted(names)}"
        # And the upgraded database has the new semantics, not just the new name.
        _create(store, job_id="j-a", owner_key="a")
        _create(store, job_id="j-b", owner_key="b")
        with pytest.raises(sqlite3.IntegrityError):
            _create(store, job_id="j-a2", owner_key="a")
    finally:
        store.close()


class _Hub:
    """The WSHub seam, as `tests/test_gateway_engine.py` uses it."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def emitter(self, root_frame_id: str):
        def emit(event: dict) -> None:
            event.setdefault("root_frame_id", root_frame_id)
            self.events.append(event)

        return emit

    def broadcast(self, root_frame_id: str, event: dict) -> None:
        event.setdefault("root_frame_id", root_frame_id)
        self.events.append(event)
