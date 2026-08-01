"""A harvested artifact said it came from this machine.

`ArtifactManager.capture` takes `drain_remote_provenance` and every cell-path
caller passes it. The compute-refresh harvest — the one path whose files
genuinely came from another host — did not, and the omission costs two separate
things, both of them the failure this subsystem exists to prevent.

The artifact was stamped with the *local* environment snapshot and carried no
record of the host, the engine or the remote directory that produced it. And
because the drain never ran on that path, a buffered remote entry stayed in the
buffer and was attached to whatever cell wrote a file next: the fold in cell 3
becoming the provenance of a figure from cell 7, which the comment inside
`capture` describes as a fixed bug.

Both are asserted against `ArtifactManager.capture` itself with a recording
drain, because the claim is about what the capture call does with it — a test
that only checked the gateway line would pass against a `capture` that ignored
the argument.
"""

from __future__ import annotations

import hashlib
import types
from pathlib import Path

import pytest

from openai4s.config import Config
from openai4s.server.artifacts import ArtifactManager
from openai4s.store import get_store


class _Session:
    def __init__(self, workspace: Path, root_frame_id: str) -> None:
        self.workspace = workspace
        self.root_frame_id = root_frame_id
        self.project_id = "science"
        self.cell_index = 1


@pytest.fixture
def harness(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    store = get_store(cfg.db_path)
    store.create_project(name="Science", project_id="science")
    root = store.new_frame(kind="turn", project_id="science", status="ready")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    manager = ArtifactManager(
        data_dir=tmp_path,
        store=store,
        workspace_for=lambda _frame_id: workspace,
        broadcast=lambda _frame_id, _event: None,
        guess_content_type=lambda name: "application/octet-stream",
        checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    yield types.SimpleNamespace(
        cfg=cfg,
        store=store,
        root=root,
        workspace=workspace,
        manager=manager,
        session=_Session(workspace, root),
    )
    store.close()


_REMOTE = [
    {
        "service": "ssh",
        "host": "lab",
        "engine": "cuda-12",
        "remote_dir": "~/.openai4s-jobs/j-1",
        "env": {"python": "3.12.3", "packages": ["torch==2.4.0"]},
    }
]


def test_a_harvested_artifact_records_the_host_that_produced_it(harness):
    """Without the drain the snapshot describes this laptop, not the GPU box."""
    (harness.workspace / "result.npy").write_bytes(b"\x93NUMPY payload")
    drained: list[bool] = []

    def drain():
        drained.append(True)
        return _REMOTE

    result = harness.manager.capture(
        harness.session,
        1,
        None,
        {},
        lambda event: None,
        language="native",
        drain_remote_provenance=drain,
    )

    assert drained, "the harvest never drained the remote buffer"
    assert result.artifacts, "the harvested file was not registered"
    version_id = result.artifacts[0]["version_id"]
    meta = harness.store.version_meta(version_id)
    snapshot_id = meta.get("env_snapshot_id")
    assert snapshot_id, "the harvested version carries no environment at all"
    snapshot = harness.store.get_env_snapshot(snapshot_id) or {}
    remote = (snapshot.get("payload") or snapshot).get("remote")
    assert remote, f"the snapshot has no remote provenance: {snapshot}"
    assert remote[0]["host"] == "lab"
    assert remote[0]["engine"] == "cuda-12"


def test_the_buffer_is_drained_even_when_the_harvest_wrote_nothing(harness):
    """Otherwise the entry lands on whatever cell writes a file next.

    A remote job that produced no local output still has to clear its buffer:
    `capture`'s own comment says the drain happens every cell and only the
    environment freeze is skipped. The harvest path was skipping both.
    """
    drained: list[bool] = []

    def drain():
        drained.append(True)
        return _REMOTE

    result = harness.manager.capture(
        harness.session,
        1,
        None,
        {},
        lambda event: None,
        language="native",
        drain_remote_provenance=drain,
    )

    assert drained, "a harvest with no output left the remote entry buffered"
    assert not result.artifacts


def test_a_capture_with_no_remote_drain_is_still_local(harness):
    """The gate must not invent remote provenance for ordinary local writes."""
    (harness.workspace / "local.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    result = harness.manager.capture(
        harness.session, 1, None, {}, lambda event: None, language="python"
    )

    assert result.artifacts
    meta = harness.store.version_meta(result.artifacts[0]["version_id"])
    snapshot_id = meta.get("env_snapshot_id")
    if snapshot_id:
        snapshot = harness.store.get_env_snapshot(snapshot_id) or {}
        assert not (snapshot.get("payload") or snapshot).get("remote")


def test_the_refresh_route_passes_the_drain_down(tmp_path, monkeypatch):
    """The wiring, not the callee.

    The three tests above hand `capture` a drain explicitly, so they pass
    whether or not the harvest path supplies one — which is exactly the state
    this commit fixes. This one drives `SessionRunner.refresh_compute_task`,
    the production entry point, and records what reached `capture`.
    """
    from openai4s.config import LLMConfig
    from openai4s.server import gateway as gateway_mod

    class _Hub:
        def emitter(self, root_frame_id):
            return lambda event: None

        def broadcast(self, root_frame_id, event):
            return None

        def has_subscriber(self, root_frame_id):
            return False

        def drop_frame(self, root_frame_id):
            return None

    cfg = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test-key")
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    try:
        root = runner.store.new_frame(kind="turn", project_id="default", status="ready")
        job_id = "j-harvest-1"
        runner.store.create_compute_job(
            job_id=job_id,
            provider="ssh:lab",
            status="queued",
            owner_key=str(runner.active_workspace_for(root)),
        )
        for step in ("staging", "running"):
            runner.store.update_compute_job(job_id, status=step)

        seen: dict = {}
        real_capture = runner.artifacts.capture

        def recording(*args, **kwargs):
            seen.update(kwargs)
            return real_capture(*args, **kwargs)

        runner.artifacts.capture = recording
        # The remote call cannot succeed here; the harvest capture runs in the
        # `finally` either way, which is the point of it being there.
        try:
            runner.refresh_compute_task(root, job_id)
        except Exception:  # noqa: BLE001 - the refresh outcome is not the claim
            pass

        # Presence is the whole distinction: before this change the harvest
        # capture was called without the argument at all. Its *value* is `None`
        # here because this session has never run a cell and so has no
        # dispatcher to drain — which is the correct answer for that state, and
        # is pinned separately below.
        assert "drain_remote_provenance" in seen, (
            "the harvest capture was called without a remote drain; a harvested "
            "artifact would carry this machine's environment"
        )
    finally:
        runner.close()


def test_the_drain_resolves_to_the_dispatchers_buffer_when_there_is_one():
    """The other half: what `_remote_provenance_drain` hands back.

    Together with the test above this covers the chain — the route passes the
    argument, and the argument is the session dispatcher's own buffer rather
    than a stand-in.
    """
    from openai4s.server import gateway as gateway_mod

    class _Dispatcher:
        def pop_remote_provenance(self):
            return _REMOTE

    dispatcher = _Dispatcher()
    with_state = types.SimpleNamespace(dispatcher=dispatcher)
    without = types.SimpleNamespace(dispatcher=None)

    drain = gateway_mod.SessionRunner._remote_provenance_drain(with_state)
    assert drain is not None
    assert drain() == _REMOTE
    assert gateway_mod.SessionRunner._remote_provenance_drain(without) is None
