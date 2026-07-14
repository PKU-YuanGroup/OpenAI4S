"""Direct contracts for versioned workspace artifact capture."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.kernel import Kernel
from openai4s.server.artifacts import ArtifactManager
from openai4s.store import get_store


class ArtifactHarness:
    def __init__(self, tmp_path: Path) -> None:
        cfg = Config(
            data_dir=tmp_path / "data",
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
        )
        self.cfg = cfg
        self.store = get_store(cfg.db_path)
        self.frame_id = self.store.new_frame(
            kind="turn", project_id="default", status="ready"
        )
        self.workspace = cfg.data_dir / "agent-workspaces" / self.frame_id
        self.workspace.mkdir(parents=True)
        self.broadcasts: list[tuple[str, dict]] = []
        self.environment_calls = 0
        self.manager = ArtifactManager(
            data_dir=cfg.data_dir,
            store=self.store,
            workspace_for=lambda frame_id: self.workspace,
            broadcast=lambda frame_id, event: self.broadcasts.append((frame_id, event)),
            environment_snapshot=self.environment_snapshot,
            guess_content_type=lambda name: "text/csv"
            if name.endswith(".csv")
            else "application/octet-stream",
            checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        self.session = SimpleNamespace(
            root_frame_id=self.frame_id,
            project_id="default",
            workspace=self.workspace,
        )

    def environment_snapshot(self) -> dict:
        self.environment_calls += 1
        return {
            "kind": "python",
            "python_version": "3.14.0",
            "implementation": "CPython",
            "platform": "test",
            "packages": [{"name": "numpy", "version": "2.0"}],
            "package_count": 1,
        }


def test_register_freezes_version_before_emitting_event(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.csv"
    observed = []

    def emit(event):
        version_id = event["artifact"]["version_id"]
        meta = harness.store.version_meta(version_id)
        snapshot = Path(meta["snapshot_path"])
        observed.append((version_id, snapshot.read_bytes()))

    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(harness.session, path, "cell-1", emit)
    path.write_bytes(b"BETA")
    second = harness.manager.register_file(harness.session, path, "cell-2", emit)

    assert first["artifact_id"] == second["artifact_id"]
    assert observed == [
        (first["version_id"], b"ALPHA"),
        (second["version_id"], b"BETA"),
    ]
    assert (
        Path(
            harness.store.version_meta(first["version_id"])["snapshot_path"]
        ).read_bytes()
        == b"ALPHA"
    )


def test_capture_finalizes_provenance_version_without_duplicating_it(tmp_path):
    harness = ArtifactHarness(tmp_path)
    source = harness.workspace / "input.txt"
    source.write_text("science")
    source_record = harness.store.save_artifact(
        path=str(source),
        filename=source.name,
        content_type="text/plain",
        size_bytes=7,
        checksum="source",
        frame_id=harness.frame_id,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)
    events = []

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        before = harness.manager.snapshot(harness.workspace)
        first_result = kernel.execute(
            "text = open('input.txt').read()\n"
            "with open('derived.txt', 'w') as handle:\n"
            "    handle.write(text.upper())\n",
            cell_id="cell-derived-1",
        )
        assert first_result["error"] is None
        output = harness.store.artifact_by_filename(
            "derived.txt", harness.frame_id, strict=True
        )
        assert output is not None
        provenance_version = output["latest_version_id"]
        assert len(harness.store.list_versions(output["artifact_id"])) == 1

        first_capture = harness.manager.capture(
            harness.session,
            1,
            "cell-derived-1",
            before,
            events.append,
            language="python",
        )

        assert first_capture.artifacts[0]["version_id"] == provenance_version
        assert events[0]["artifact"]["version_id"] == provenance_version
        assert events[0]["producing_cell_id"] == "cell-derived-1"
        assert events[0]["artifact"]["producing_cell_id"] == "cell-derived-1"
        output = harness.store.get_artifact(output["artifact_id"])
        assert output["latest_version_id"] == provenance_version
        assert len(harness.store.list_versions(output["artifact_id"])) == 1
        metadata = harness.store.version_meta(provenance_version)
        assert metadata["env_snapshot_id"] is not None
        assert Path(metadata["snapshot_path"]).read_text() == "SCIENCE"
        assert harness.store.lineage_inputs(provenance_version) == [
            {
                "version_id": source_record["version_id"],
                "filename": "input.txt",
                "path": str(source),
            }
        ]

        before_second = harness.manager.snapshot(harness.workspace)
        second_result = kernel.execute(
            "text = open('input.txt').read()\n"
            "with open('derived.txt', 'w') as handle:\n"
            "    handle.write(text.lower())\n",
            cell_id="cell-derived-2",
        )
        assert second_result["error"] is None
        second_capture = harness.manager.capture(
            harness.session,
            2,
            "cell-derived-2",
            before_second,
            events.append,
            language="python",
        )

    assert second_capture.artifacts[0]["artifact_id"] == output["artifact_id"]
    second_version = second_capture.artifacts[0]["version_id"]
    assert second_version != provenance_version
    assert len(harness.store.list_versions(output["artifact_id"])) == 2
    assert Path(
        harness.store.resolve_artifact_path(provenance_version)
    ).read_text() == ("SCIENCE")
    assert Path(harness.store.resolve_artifact_path(second_version)).read_text() == (
        "science"
    )
    assert (
        len(
            harness.store.list_artifacts(
                {"root_frame_id": harness.frame_id, "filename": "derived.txt"}
            )
        )
        == 1
    )


def test_explicit_save_merges_provenance_and_capture_into_one_complete_version(
    tmp_path,
):
    harness = ArtifactHarness(tmp_path)
    source = harness.workspace / "input.txt"
    source.write_text("science")
    source_record = harness.store.save_artifact(
        path=str(source),
        filename=source.name,
        content_type="text/plain",
        size_bytes=7,
        checksum="source",
        frame_id=harness.frame_id,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)
    events = []

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        before = harness.manager.snapshot(harness.workspace)
        result = kernel.execute(
            "text = open('input.txt').read()\n"
            "with open('manual.csv', 'w') as handle:\n"
            "    handle.write(text.upper())\n"
            "saved = host.save_artifact(\n"
            "    'manual.csv', 'published/result.csv',\n"
            "    content_type='application/x-science',\n"
            f"    input_version_ids=['{source_record['version_id']}'],\n"
            "    producing_cell_id='declared-producer',\n"
            "    priority=2,\n"
            ")\n"
            "print(saved['version_id'])\n"
            "print(saved['path'])\n",
            cell_id="cell-explicit",
        )
        assert result["error"] is None
        saved_version, returned_path = result["stdout"].splitlines()
        before_capture = harness.store.version_meta(saved_version)
        capture = harness.manager.capture(
            harness.session,
            1,
            "cell-explicit",
            before,
            events.append,
            language="python",
        )

    artifact = harness.store.artifact_by_filename(
        "published/result.csv", harness.frame_id, strict=True
    )
    assert artifact is not None
    assert artifact["priority"] == 2
    assert artifact["latest_version_id"] == saved_version
    assert len(harness.store.list_versions(artifact["artifact_id"])) == 1
    assert capture.files_written == ["manual.csv"]
    assert capture.artifacts[0]["version_id"] == saved_version
    assert capture.artifacts[0]["filename"] == "published/result.csv"
    assert events[0]["artifact"]["filename"] == "published/result.csv"
    metadata = harness.store.version_meta(saved_version)
    assert metadata["producing_cell_id"] == "cell-explicit"
    assert metadata["content_type"] == "application/x-science"
    assert artifact["content_type"] == "application/x-science"
    assert capture.artifacts[0]["content_type"] == "application/x-science"
    assert metadata["path"] == str(harness.workspace / "manual.csv")
    assert metadata["snapshot_path"] == before_capture["snapshot_path"]
    assert returned_path == metadata["snapshot_path"]
    assert Path(metadata["snapshot_path"]).read_text() == "SCIENCE"
    assert metadata["env_snapshot_id"] is not None
    assert harness.store.lineage_inputs(saved_version) == [
        {
            "version_id": source_record["version_id"],
            "filename": "input.txt",
            "path": str(source),
        }
    ]
    assert (
        harness.store.artifact_by_filename("manual.csv", harness.frame_id, strict=True)
        is None
    )


def test_repeated_explicit_saves_remain_versions_and_capture_adds_no_third(tmp_path):
    harness = ArtifactHarness(tmp_path)
    dispatcher = HostDispatcher(cfg=harness.cfg, frame_id=harness.frame_id)

    with Kernel(dispatcher=dispatcher, cwd=str(harness.workspace)) as kernel:
        before = harness.manager.snapshot(harness.workspace)
        result = kernel.execute(
            "open('repeat.txt', 'w').write('same')\n"
            "first = host.save_artifact('repeat.txt')\n"
            "second = host.save_artifact('repeat.txt')\n"
            "print(first['version_id'])\n"
            "print(second['version_id'])\n",
            cell_id="cell-repeat",
        )
        assert result["error"] is None
        first_version, second_version = result["stdout"].splitlines()
        capture = harness.manager.capture(
            harness.session,
            1,
            "cell-repeat",
            before,
            lambda event: None,
            language="python",
        )

    artifact = harness.store.artifact_by_filename(
        "repeat.txt", harness.frame_id, strict=True
    )
    assert first_version != second_version
    assert artifact["latest_version_id"] == second_version
    assert capture.artifacts[0]["version_id"] == second_version
    versions = harness.store.list_versions(artifact["artifact_id"])
    assert {version["version_id"] for version in versions} == {
        first_version,
        second_version,
    }
    assert all(
        Path(
            harness.store.version_meta(version["version_id"])["snapshot_path"]
        ).is_file()
        for version in versions
    )


def test_protect_latest_backfills_live_bytes_for_legacy_version(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "legacy.txt"
    path.write_bytes(b"ALPHA")
    legacy = harness.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/plain",
        size_bytes=5,
        checksum=hashlib.sha256(b"ALPHA").hexdigest(),
        producing_cell_id="cell-legacy",
        frame_id=harness.frame_id,
        project_id="default",
    )

    harness.manager.protect_latest(harness.session)

    metadata = harness.store.version_meta(legacy["version_id"])
    assert Path(metadata["snapshot_path"]).read_bytes() == b"ALPHA"


def test_restore_backfills_legacy_latest_before_broadcast(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "report.txt"
    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(
        harness.session, path, "cell-1", lambda event: None
    )

    # Simulate a pre-snapshot version: latest points at the mutable live path.
    path.write_bytes(b"BETA")
    legacy = harness.store.save_artifact(
        path=str(path),
        filename=path.name,
        content_type="text/plain",
        size_bytes=4,
        checksum=hashlib.sha256(b"BETA").hexdigest(),
        producing_cell_id="cell-2",
        frame_id=harness.frame_id,
        project_id="default",
        artifact_id=first["artifact_id"],
    )
    checked_during_broadcast = []

    def broadcast(frame_id, event):
        legacy_meta = harness.store.version_meta(legacy["version_id"])
        checked_during_broadcast.append(
            (
                path.read_bytes(),
                Path(legacy_meta["snapshot_path"]).read_bytes(),
                harness.store.get_artifact(first["artifact_id"])["latest_version_id"],
            )
        )

    harness.manager.broadcast = broadcast
    result = harness.manager.restore(first["artifact_id"], first["version_id"])

    assert result["ok"] is True
    restored_version_id = result["version_id"]
    assert restored_version_id not in {
        first["version_id"],
        legacy["version_id"],
    }
    assert result["restored_from_version_id"] == first["version_id"]
    assert checked_during_broadcast == [(b"ALPHA", b"BETA", restored_version_id)]
    assert harness.store.lineage_edges_for(restored_version_id, "up") == [
        first["version_id"]
    ]


def test_restore_rejects_corrupt_snapshot_and_workspace_drift(tmp_path):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.txt"
    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(
        harness.session, path, "cell-1", lambda event: None
    )
    path.write_bytes(b"BETA")
    second = harness.manager.register_file(
        harness.session, path, "cell-2", lambda event: None
    )

    source = harness.store.version_meta(first["version_id"])
    Path(source["snapshot_path"]).write_bytes(b"tampered")
    result = harness.manager.restore(first["artifact_id"], first["version_id"])
    assert "checksum verification failed" in result["error"]
    assert path.read_bytes() == b"BETA"
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == second["version_id"]
    )
    assert len(harness.store.list_versions(first["artifact_id"])) == 2

    outside = tmp_path / "outside-snapshot"
    outside.write_bytes(b"ALPHA")
    harness.store.set_version_snapshot(first["version_id"], str(outside))
    result = harness.manager.restore(first["artifact_id"], first["version_id"])
    assert "outside trusted storage" in result["error"]
    assert path.read_bytes() == b"BETA"

    Path(source["snapshot_path"]).write_bytes(b"ALPHA")
    harness.store.set_version_snapshot(first["version_id"], source["snapshot_path"])
    path.write_bytes(b"external edit")
    result = harness.manager.restore(first["artifact_id"], first["version_id"])
    assert "unversioned changes" in result["error"]
    assert path.read_bytes() == b"external edit"
    assert len(harness.store.list_versions(first["artifact_id"])) == 2


def test_restore_expected_latest_cas_rolls_back_live_and_new_snapshot(
    tmp_path, monkeypatch
):
    harness = ArtifactHarness(tmp_path)
    path = harness.workspace / "result.txt"
    path.write_bytes(b"ALPHA")
    first = harness.manager.register_file(
        harness.session, path, "cell-1", lambda event: None
    )
    path.write_bytes(b"BETA")
    second = harness.manager.register_file(
        harness.session, path, "cell-2", lambda event: None
    )
    snapshots_before = set(harness.manager.versions_dir().iterdir())
    original_record = harness.store.record_artifact_restore
    raced = {}

    def race_then_record(**fields):
        race_path = harness.workspace / "race.txt"
        race_path.write_bytes(b"GAMMA")
        race_snapshot = harness.manager.versions_dir() / "race-gamma"
        race_snapshot.write_bytes(b"GAMMA")
        raced.update(
            harness.store.save_artifact(
                path=str(race_path),
                filename="result.txt",
                content_type="text/plain",
                size_bytes=5,
                checksum=hashlib.sha256(b"GAMMA").hexdigest(),
                frame_id=harness.frame_id,
                artifact_id=first["artifact_id"],
                snapshot_path=str(race_snapshot),
            )
        )
        return original_record(**fields)

    monkeypatch.setattr(harness.store, "record_artifact_restore", race_then_record)
    result = harness.manager.restore(first["artifact_id"], first["version_id"])

    assert "changed concurrently" in result["error"]
    assert path.read_bytes() == b"BETA"
    assert (
        harness.store.get_artifact(first["artifact_id"])["latest_version_id"]
        == raced["version_id"]
    )
    assert harness.store.version_meta(second["version_id"])["checksum"] == (
        hashlib.sha256(b"BETA").hexdigest()
    )
    assert len(harness.store.list_versions(first["artifact_id"])) == 3
    assert harness.store.lineage_edges_for(first["version_id"], "down") == []
    added_snapshots = set(harness.manager.versions_dir().iterdir()) - snapshots_before
    assert added_snapshots == {harness.manager.versions_dir() / "race-gamma"}


def test_python_capture_uses_one_environment_and_orders_figure_first(tmp_path):
    harness = ArtifactHarness(tmp_path)
    before = harness.manager.snapshot(harness.workspace)
    (harness.workspace / "table.csv").write_text("x\n1\n")
    remote_calls = 0
    events = []

    def run_system_cell(code):
        assert "matplotlib" in code
        (harness.workspace / "figure_cell1_1.png").write_bytes(b"PNG")
        return {"stdout": '__OSFIGS__["figure_cell1_1.png"]\n'}

    def drain_remote():
        nonlocal remote_calls
        remote_calls += 1
        return [{"provider": "gpu-test", "job_id": "job-1"}]

    def emit(event):
        version = harness.store.version_meta(event["artifact"]["version_id"])
        assert Path(version["snapshot_path"]).is_file()
        events.append(event)

    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-1",
        before,
        emit,
        language="python",
        run_system_cell=run_system_cell,
        drain_remote_provenance=drain_remote,
    )

    assert harness.environment_calls == remote_calls == 1
    assert captured.figures == ["figure_cell1_1.png"]
    assert captured.files_written == ["table.csv"]
    assert [item["filename"] for item in captured.artifacts] == [
        "figure_cell1_1.png",
        "table.csv",
    ]
    assert [event["artifact"]["filename"] for event in events] == [
        "figure_cell1_1.png",
        "table.csv",
    ]
    env_ids = {
        harness.store.version_meta(item["version_id"])["env_snapshot_id"]
        for item in captured.artifacts
    }
    assert len(env_ids) == 1
    snapshot = harness.store.get_env_snapshot(env_ids.pop())
    assert snapshot["remote"] == [{"provider": "gpu-test", "job_id": "job-1"}]


def test_r_capture_never_runs_python_figure_probe(tmp_path):
    harness = ArtifactHarness(tmp_path)
    before = harness.manager.snapshot(harness.workspace)
    (harness.workspace / "Rplots.pdf").write_bytes(b"PDF")

    def forbidden_probe(code):
        raise AssertionError("R capture must not execute a Python system cell")

    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-r",
        before,
        lambda event: None,
        language="r",
        run_system_cell=forbidden_probe,
    )

    assert captured.figures == []
    assert captured.files_written == ["Rplots.pdf"]
    assert [item["filename"] for item in captured.artifacts] == ["Rplots.pdf"]


def test_no_changes_skip_environment_and_remote_provenance(tmp_path):
    harness = ArtifactHarness(tmp_path)
    before = harness.manager.snapshot(harness.workspace)
    remote_calls = 0

    def drain_remote():
        nonlocal remote_calls
        remote_calls += 1
        return [{"job_id": "should-remain-buffered"}]

    captured = harness.manager.capture(
        harness.session,
        1,
        "cell-empty",
        before,
        lambda event: None,
        language="r",
        drain_remote_provenance=drain_remote,
    )

    assert captured.artifacts == []
    assert harness.environment_calls == remote_calls == 0


def test_snapshot_ignores_hidden_junk_and_nested_git_repositories(tmp_path):
    harness = ArtifactHarness(tmp_path)
    (harness.workspace / "deliverable.txt").write_text("keep")
    (harness.workspace / ".hidden.txt").write_text("ignore")
    junk = harness.workspace / "node_modules"
    junk.mkdir()
    (junk / "dependency.js").write_text("ignore")
    nested = harness.workspace / "cloned-tool"
    (nested / ".git").mkdir(parents=True)
    (nested / "weights.bin").write_bytes(b"ignore")

    snapshot = harness.manager.snapshot(harness.workspace)

    assert set(snapshot) == {str(harness.workspace / "deliverable.txt")}


def test_promote_cell_freezes_code_and_output_as_markdown_artifact(tmp_path):
    harness = ArtifactHarness(tmp_path)
    events: list[dict] = []
    cell = {
        "producing_cell_id": "cell-abc",
        "cell_index": 2,
        "language": "python",
        "source": "print('hi')\ndf.to_csv('out.csv')",
        "stdout": "hi",
        "figures": ["figure_cell2_1.png"],
        "files_written": ["out.csv"],
    }

    meta = harness.manager.promote_cell(harness.session, cell, events.append)

    assert meta is not None
    assert meta["filename"].endswith(".md")
    promoted = list((harness.workspace / "promoted").glob("*.md"))
    assert len(promoted) == 1
    text = promoted[0].read_text("utf-8")
    assert "```python" in text
    assert "print('hi')" in text
    assert "## Output" in text and "hi" in text
    assert "figure_cell2_1.png" in text  # figure reference preserved
    assert "`out.csv`" in text  # produced-file pointer preserved
    # A real artifact_created event fires so the Files panel refreshes.
    assert any(event.get("type") == "artifact_created" for event in events)


def test_promote_cell_reuses_one_artifact_and_versions_on_change(tmp_path):
    harness = ArtifactHarness(tmp_path)
    cell = {"producing_cell_id": "cell-x", "cell_index": 1, "source": "x = 1"}

    first = harness.manager.promote_cell(harness.session, cell, lambda event: None)
    same = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    # Re-promoting the identical cell rewrites the same stable path: one
    # artifact, one file, no duplicate (identical bytes dedupe to one version).
    assert same["artifact_id"] == first["artifact_id"]
    assert same["version_id"] == first["version_id"]
    assert len(list((harness.workspace / "promoted").glob("*.md"))) == 1

    # An edited cell (same id) writes a fresh version of that same artifact.
    cell["source"] = "x = 2"
    changed = harness.manager.promote_cell(harness.session, cell, lambda event: None)
    assert changed["artifact_id"] == first["artifact_id"]
    assert changed["version_id"] != first["version_id"]
    assert len(list((harness.workspace / "promoted").glob("*.md"))) == 1


def test_promote_cell_fences_longer_than_backtick_runs_in_output(tmp_path):
    harness = ArtifactHarness(tmp_path)
    # A cell whose output contains a Markdown fence must not break out of the
    # code block — the surrounding fence has to be longer than any run inside.
    cell = {
        "producing_cell_id": "cell-md",
        "cell_index": 3,
        "source": "print('markdown')",
        "stdout": "```\nnested fence\n```",
    }

    meta = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    assert meta is not None
    text = list((harness.workspace / "promoted").glob("*.md"))[0].read_text("utf-8")
    assert "````" in text  # output fence grew to 4 backticks around the 3-run body


def test_promote_cell_survives_symlinked_workspace_prefix(tmp_path):
    harness = ArtifactHarness(tmp_path)
    # A workspace reached through a symlinked parent (mirrors /tmp -> /private/tmp
    # on macOS, or a relative OPENAI4S_DATA_DIR): _write_confined_text returns a
    # resolved path while register_file relativizes against the unresolved
    # workspace. If the two diverge, promotion must still succeed rather than
    # raising an uncaught ValueError.
    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "linked-root"
    link.symlink_to(real, target_is_directory=True)
    workspace = link / "ws"
    workspace.mkdir()
    session = SimpleNamespace(
        root_frame_id=harness.frame_id,
        project_id="default",
        workspace=workspace,
    )

    meta = harness.manager.promote_cell(
        session,
        {"producing_cell_id": "cell-sym", "cell_index": 7, "source": "x = 1"},
        lambda event: None,
    )

    assert meta is not None
    assert meta["filename"].endswith(".md")
    assert list((workspace / "promoted").glob("*.md"))


def test_promote_cell_rejects_symlinked_output_directory(tmp_path):
    harness = ArtifactHarness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (harness.workspace / "promoted").symlink_to(outside, target_is_directory=True)

    result = harness.manager.promote_cell(
        harness.session,
        {"producing_cell_id": "cell-link", "cell_index": 4, "source": "x = 1"},
        lambda event: None,
    )

    assert result is None
    assert list(outside.iterdir()) == []


def test_promote_cell_rejects_symlinked_output_file(tmp_path):
    harness = ArtifactHarness(tmp_path)
    cell = {"producing_cell_id": "cell-link", "cell_index": 4, "source": "x = 1"}
    first = harness.manager.promote_cell(harness.session, cell, lambda event: None)
    assert first is not None
    target = next((harness.workspace / "promoted").glob("*.md"))
    outside = tmp_path / "outside.md"
    outside.write_text("keep", encoding="utf-8")
    target.unlink()
    target.symlink_to(outside)

    result = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    assert result is None
    assert outside.read_text(encoding="utf-8") == "keep"


def test_promote_cell_embeds_workspace_figures_as_safe_data_urls(tmp_path):
    harness = ArtifactHarness(tmp_path)
    figure = harness.workspace / "figure_cell5_1.png"
    figure.write_bytes(b"\x89PNG\r\n\x1a\nfigure-bytes")
    cell = {
        "producing_cell_id": "cell-figure",
        "cell_index": 5,
        "source": "plot()",
        "figures": [figure.name],
    }

    result = harness.manager.promote_cell(harness.session, cell, lambda event: None)

    assert result is not None
    text = next((harness.workspace / "promoted").glob("*.md")).read_text("utf-8")
    assert f"![{figure.name}](data:image/png;base64," in text
    assert f"]({figure.name})" not in text
