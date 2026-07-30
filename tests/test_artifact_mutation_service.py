"""Direct contracts for interactive artifact mutations."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server.artifacts import ArtifactManager, ArtifactOperationError
from openai4s.store import get_store


class MutationHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.cfg = Config(
            data_dir=tmp_path / "data",
            llm=LLMConfig(provider="deepseek", api_key="test-key"),
        )
        self.store = get_store(self.cfg.db_path)
        self.frame_id = self.store.new_frame(
            kind="turn", project_id="default", status="ready"
        )
        self.workspace = self.cfg.data_dir / "workspaces" / self.frame_id
        self.workspace.mkdir(parents=True)
        self.events: list[tuple[str, dict]] = []
        self.manager = ArtifactManager(
            data_dir=self.cfg.data_dir,
            store=self.store,
            workspace_for=lambda frame_id: self.workspace,
            broadcast=lambda frame_id, event: self.events.append((frame_id, event)),
            guess_content_type=lambda name: (
                "text/plain; charset=utf-8"
                if name.endswith(".txt")
                else "application/octet-stream"
            ),
            checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    def artifact(self, filename: str, data: bytes, content_type: str) -> dict:
        path = self.workspace / filename
        path.write_bytes(data)
        return self.store.save_artifact(
            path=str(path),
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            frame_id=self.frame_id,
            project_id="default",
        )


def raised_operation(call, code: int, message: str) -> None:
    with pytest.raises(ArtifactOperationError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.message == message
    assert str(caught.value) == message


def test_edit_versions_live_text_and_preserves_exact_event_shape(tmp_path):
    harness = MutationHarness(tmp_path)
    first = harness.artifact("notes.txt", b"version one", "text/plain")
    artifact_id = first["artifact_id"]
    override: list[tuple[str, dict]] = []

    result = harness.manager.edit(
        artifact_id,
        "version two",
        broadcast=lambda frame_id, event: override.append((frame_id, event)),
    )

    assert result == {
        "ok": True,
        "artifact_id": artifact_id,
        "version_id": result["version_id"],
        "size_bytes": len(b"version two"),
    }
    assert (harness.workspace / "notes.txt").read_text() == "version two"
    assert (
        Path(
            harness.store.version_meta(first["version_id"])["snapshot_path"]
        ).read_bytes()
        == b"version one"
    )
    assert (
        Path(
            harness.store.version_meta(result["version_id"])["snapshot_path"]
        ).read_bytes()
        == b"version two"
    )
    assert len(harness.store.list_versions(artifact_id)) == 2
    assert harness.events == []
    assert override == [
        (
            harness.frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": "notes.txt",
                    "version_id": result["version_id"],
                    "root_frame_id": harness.frame_id,
                },
            },
        )
    ]


def test_edit_rejects_missing_binary_and_write_failure(tmp_path, monkeypatch):
    harness = MutationHarness(tmp_path)
    raised_operation(
        lambda: harness.manager.edit("missing", "content"),
        404,
        "artifact not found",
    )
    image = harness.artifact("figure.png", b"PNG", "image/png")
    raised_operation(
        lambda: harness.manager.edit(image["artifact_id"], "content"),
        415,
        "artifact is not text-editable",
    )
    text = harness.artifact("write.txt", b"old", "text/plain")

    def fail_write(self, data, encoding=None, errors=None, newline=None):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_write)
    raised_operation(
        lambda: harness.manager.edit(text["artifact_id"], "new"),
        500,
        "write failed: disk full",
    )


def test_log_extension_preserves_legacy_text_editability(tmp_path):
    harness = MutationHarness(tmp_path)
    log = harness.artifact("run.log", b"old", "application/octet-stream")

    result = harness.manager.edit(log["artifact_id"], "new")

    assert result["ok"] is True
    assert (harness.workspace / "run.log").read_text() == "new"


def test_rename_changes_metadata_only_and_preserves_exact_event_shape(tmp_path):
    harness = MutationHarness(tmp_path)
    record = harness.artifact("before.txt", b"science", "text/plain")
    artifact_id = record["artifact_id"]

    result = harness.manager.rename(artifact_id, "after.txt")

    assert result == {
        "ok": True,
        "artifact_id": artifact_id,
        "filename": "after.txt",
    }
    assert harness.store.get_artifact(artifact_id)["filename"] == "after.txt"
    assert (harness.workspace / "before.txt").read_bytes() == b"science"
    assert not (harness.workspace / "after.txt").exists()
    assert harness.events == [
        (
            harness.frame_id,
            {
                "type": "artifact_created",
                "artifact": {
                    "id": artifact_id,
                    "filename": "after.txt",
                    "root_frame_id": harness.frame_id,
                },
            },
        )
    ]
    raised_operation(
        lambda: harness.manager.rename("missing", None),
        400,
        "filename required",
    )
    raised_operation(
        lambda: harness.manager.rename("missing", "new.txt"),
        404,
        "artifact not found",
    )


def test_artifact_mutations_fail_closed_on_workspace_escape_metadata(tmp_path):
    harness = MutationHarness(tmp_path)
    record = harness.artifact("safe.txt", b"safe", "text/plain")
    outside = tmp_path / "outside.txt"
    outside.write_text("sentinel", encoding="utf-8")

    raised_operation(
        lambda: harness.manager.rename(record["artifact_id"], "../../outside.txt"),
        400,
        "artifact live path escapes its workspace",
    )
    assert harness.store.get_artifact(record["artifact_id"])["filename"] == "safe.txt"

    harness.store.rename_artifact(record["artifact_id"], "../../outside.txt")
    raised_operation(
        lambda: harness.manager.edit(record["artifact_id"], "compromised"),
        400,
        "artifact live path escapes its workspace",
    )
    assert outside.read_text("utf-8") == "sentinel"


def test_upload_versioning_and_event_contracts(tmp_path):
    harness = MutationHarness(tmp_path)

    first = harness.manager.upload(
        {
            "filename": "../result.txt",
            "content_base64": base64.b64encode(b"alpha").decode(),
            "frame_id": harness.frame_id,
        }
    )
    # Line-wrapped base64 is transport formatting and still decodes. The
    # payload here used to be "YmV0YQ==!", whose trailing "!" was silently
    # discarded -- this test pinned that leniency, and leniency is what makes
    # a corrupted upload undetectable.
    second = harness.manager.upload(
        {
            "filename": "result.txt",
            "content_base64": "YmV0\nYQ==\n",
            "frame_id": harness.frame_id,
        }
    )

    assert first["artifact_id"] == first["id"] == second["artifact_id"]
    assert first["filename"] == second["filename"] == "result.txt"
    versions = harness.store.list_versions(first["artifact_id"])
    assert len(versions) == 2
    by_ordinal = {version["ordinal"]: version for version in versions}
    assert (
        Path(
            harness.store.resolve_artifact_path(by_ordinal[1]["version_id"])
        ).read_bytes()
        == b"alpha"
    )
    assert (
        Path(
            harness.store.resolve_artifact_path(by_ordinal[2]["version_id"])
        ).read_bytes()
        == b"beta"
    )
    assert harness.events[-1] == (
        harness.frame_id,
        {
            "type": "artifact_created",
            "artifact": {
                "id": first["artifact_id"],
                "filename": "result.txt",
                "content_type": "text/plain; charset=utf-8",
                "root_frame_id": harness.frame_id,
            },
        },
    )

    # This used to succeed, storing the literal text as the file's bytes and
    # hashing that. Upload a `.npy` whose payload lost a character in transit
    # and the artifact carried the base64 string instead of the array --
    # versioned, checksummed and indistinguishable from data. A caller who
    # means to upload text says `content_text`.
    with pytest.raises(ArtifactOperationError) as refused:
        harness.manager.upload(
            {
                "filename": "fallback.bin",
                "content_base64": "%%% not base64 %%%",
                "frame_id": harness.frame_id,
            }
        )
    assert refused.value.code == 400
    assert "not valid base64" in refused.value.message
    # Nothing was written: a rejected upload leaves no artifact behind.
    assert not (
        harness.manager.workspace_for(harness.frame_id) / "fallback.bin"
    ).exists()

    event_count = len(harness.events)
    loose = harness.manager.upload(
        {
            "filename": "loose.bin",
            "content_base64": base64.b64encode(b"outside").decode(),
        }
    )
    assert len(harness.events) == event_count
    assert (harness.cfg.data_dir / "uploads" / "loose.bin").read_bytes() == b"outside"
    assert harness.store.get_artifact(loose["artifact_id"])["root_frame_id"] is None


def test_delete_reclaims_versions_and_emits_bare_refresh_event(tmp_path):
    harness = MutationHarness(tmp_path)
    first = harness.artifact("delete.txt", b"one", "text/plain")
    artifact_id = first["artifact_id"]
    second = harness.manager.edit(artifact_id, "two")
    version_paths = []
    for version_id in (first["version_id"], second["version_id"]):
        metadata = harness.store.version_meta(version_id)
        version_paths.extend([metadata["path"], metadata["snapshot_path"]])
    harness.events.clear()

    assert harness.manager.delete(artifact_id) == {"ok": True}

    assert harness.store.get_artifact(artifact_id) is None
    assert all(not Path(path).exists() for path in set(version_paths))
    assert harness.events == [
        (
            harness.frame_id,
            {
                "type": "artifact_created",
                "root_frame_id": harness.frame_id,
            },
        )
    ]
    harness.events.clear()
    assert harness.manager.delete("missing") == {"ok": True}
    assert harness.events == []


def test_upload_refuses_corrupted_base64_instead_of_decoding_it_to_other_bytes(
    tmp_path,
):
    """`b64decode` without `validate=True` silently discards stray characters.

    That is the half that never raised. A payload corrupted in transit decodes
    to *different bytes* and reports success, so the artifact carries a
    checksum computed over content nobody sent -- provenance that is wrong
    rather than absent, which is worse because it is believed.

    Whitespace is exempt because it is transport formatting: plenty of tools
    wrap base64 at 76 columns, and rejecting that would break honest callers
    while catching nothing.
    """
    harness = MutationHarness(tmp_path)

    payload = base64.b64encode(b"\x00\x01measurement\xff").decode()

    clean = harness.manager.upload(
        {
            "filename": "clean.bin",
            "content_base64": payload,
            "frame_id": harness.frame_id,
        }
    )
    stored = Path(
        harness.store.resolve_artifact_path(clean["artifact_id"])
    ).read_bytes()
    assert stored == b"\x00\x01measurement\xff"

    # Wrapped at 76 columns: still the same bytes.
    wrapped = "\n".join(payload[i : i + 4] for i in range(0, len(payload), 4))
    ok = harness.manager.upload(
        {
            "filename": "wrapped.bin",
            "content_base64": wrapped,
            "frame_id": harness.frame_id,
        }
    )
    assert (
        Path(harness.store.resolve_artifact_path(ok["artifact_id"])).read_bytes()
        == b"\x00\x01measurement\xff"
    )

    # A stray non-alphabet character is corruption, not formatting.
    for corrupt in (payload[:4] + "!" + payload[4:], payload[:4] + "*" + payload[4:]):
        with pytest.raises(ArtifactOperationError) as refused:
            harness.manager.upload(
                {
                    "filename": "corrupt.bin",
                    "content_base64": corrupt,
                    "frame_id": harness.frame_id,
                }
            )
        assert refused.value.code == 400


def test_upload_will_not_guess_which_content_field_is_authoritative(tmp_path):
    """Three fields, one meaning. Two supplied is a question, not a payload."""
    harness = MutationHarness(tmp_path)

    with pytest.raises(ArtifactOperationError) as refused:
        harness.manager.upload(
            {
                "filename": "both.txt",
                "content_base64": base64.b64encode(b"a").decode(),
                "content_text": "b",
                "frame_id": harness.frame_id,
            }
        )
    assert refused.value.code == 400
    assert "exactly one" in refused.value.message

    # And content_text is the sanctioned way to upload text.
    text = harness.manager.upload(
        {
            "filename": "note.txt",
            "content_text": "plain text, not base64",
            "frame_id": harness.frame_id,
        }
    )
    assert (
        Path(harness.store.resolve_artifact_path(text["artifact_id"])).read_bytes()
        == b"plain text, not base64"
    )
