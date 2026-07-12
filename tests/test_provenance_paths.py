"""Kernel-side filesystem identity contracts for object provenance."""

from pathlib import Path

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.kernel import Kernel, provenance
from openai4s.store import get_store


def test_canonical_path_uses_worker_cwd_and_relative_filename(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    outside = tmp_path / "outside"
    nested.mkdir(parents=True)
    outside.mkdir()
    (workspace / "~").mkdir()
    external_dir = outside / "dir"
    external_dir.mkdir()
    (workspace / "link").symlink_to(external_dir, target_is_directory=True)
    (outside / "secret.csv").write_text("outside")
    (workspace / "secret.csv").write_text("inside")
    monkeypatch.chdir(workspace)

    assert provenance._canonical_path(Path("nested/result.csv")) == (
        str(nested / "result.csv"),
        "nested/result.csv",
    )
    assert provenance._canonical_path(outside / "external.csv") == (
        str(outside / "external.csv"),
        "external.csv",
    )
    assert provenance._canonical_path("~/literal.csv") == (
        str(workspace / "~" / "literal.csv"),
        "~/literal.csv",
    )
    assert provenance._canonical_path("link/../secret.csv") == (
        str(outside / "secret.csv"),
        "secret.csv",
    )
    assert Path("link/../secret.csv").read_text() == "outside"
    assert provenance._canonical_path(7) is None

    provenance._execution_root[0] = str(workspace)
    try:
        monkeypatch.chdir(nested)
        assert provenance._canonical_path("after-chdir.csv") == (
            str(nested / "after-chdir.csv"),
            "nested/after-chdir.csv",
        )
    finally:
        provenance._execution_root[0] = None


def test_resolve_and_record_send_canonical_path_metadata(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    calls = []

    def host_call(method, args):
        calls.append((method, args))
        return "v-input" if method == "prov_resolve_path" else {"ok": True}

    monkeypatch.setattr(provenance, "_host_call", host_call)
    provenance.set_cell_id("cell-shared")
    try:
        assert provenance._resolve_version("inputs/raw.csv") == "v-input"
        provenance._report_write("results/out.csv", frozenset({"v-input"}))
    finally:
        provenance.set_cell_id(None)

    assert calls == [
        (
            "prov_resolve_path",
            [str(workspace / "inputs" / "raw.csv")],
        ),
        (
            "prov_record",
            [
                {
                    "path": str(workspace / "results" / "out.csv"),
                    "filename": "results/out.csv",
                    "input_version_ids": ["v-input"],
                    "producing_cell_id": "cell-shared",
                }
            ],
        ),
    ]


def test_resolve_version_ignores_non_str_host_result(tmp_path, monkeypatch):
    """A host that returns a non-scalar (e.g. an error/metadata dict) for an
    untracked path must not reach ``frozenset({vid})`` — an unhashable value
    there crashes the whole worker (e.g. opening /proc/self/status for RSS)."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        provenance, "_host_call", lambda method, args: {"error": "not tracked"}
    )
    assert provenance._resolve_version("system/status") is None


def test_open_writer_freezes_relative_location_before_cwd_changes(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(workspace)
    calls = []
    monkeypatch.setattr(
        provenance,
        "_host_call",
        lambda method, args: calls.append((method, args)),
    )
    provenance.set_cell_id("cell-open")
    writer = provenance._ProvFileWriter(
        (workspace / "result.txt").open("w"),
        "result.txt",
    )
    try:
        writer.write(provenance._tag_scalar("science", frozenset({"v-source"})))
        monkeypatch.chdir(nested)
        writer.close()
    finally:
        provenance.set_cell_id(None)

    assert calls == [
        (
            "prov_record",
            [
                {
                    "path": str(workspace / "result.txt"),
                    "filename": "result.txt",
                    "input_version_ids": ["v-source"],
                    "producing_cell_id": "cell-open",
                }
            ],
        )
    ]


def test_real_kernel_tracks_relative_read_to_nested_relative_write(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "input.txt"
    source.write_text("science")
    source_version = store.save_artifact(
        path=str(source),
        filename="input.txt",
        content_type="text/plain",
        size_bytes=7,
        checksum="source-checksum",
        frame_id=frame_id,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=cfg, frame_id=frame_id)

    with Kernel(dispatcher=dispatcher, cwd=str(workspace)) as kernel:
        result = kernel.execute(
            "import os\n"
            "text = open('input.txt').read()\n"
            "os.makedirs('nested', exist_ok=True)\n"
            "with open('nested/output.txt', 'w') as handle:\n"
            "    handle.write(text.upper())\n"
            "os.chdir('nested')\n"
            "with open('after-chdir.txt', 'w') as handle:\n"
            "    handle.write(text.lower())\n",
            cell_id="cell-relative",
        )

    assert result["error"] is None
    output = store.artifact_by_filename("nested/output.txt", frame_id, strict=True)
    assert output is not None
    assert len(store.list_versions(output["artifact_id"])) == 1
    metadata = store.version_meta(output["latest_version_id"])
    assert metadata["path"] == str(workspace / "nested" / "output.txt")
    assert metadata["producing_cell_id"] == "cell-relative"
    assert store.lineage_inputs(output["latest_version_id"]) == [
        {
            "version_id": source_version["version_id"],
            "filename": "input.txt",
            "path": str(source),
        }
    ]
    after_chdir = store.artifact_by_filename(
        "nested/after-chdir.txt", frame_id, strict=True
    )
    assert after_chdir is not None
    assert (
        store.lineage_inputs(after_chdir["latest_version_id"])[0]["version_id"]
        == source_version["version_id"]
    )
