"""Checkpoint import is explicit, confined, hashed, and not self-admitting."""

from __future__ import annotations

import hashlib

from openai4s.host.files import WorkspaceFileService
from openai4s.tools.model_assets import StageModelAssetTool


def _workspace(tmp_path):
    return WorkspaceFileService(
        data_dir=tmp_path / "data",
        frame_id=lambda: None,
        workspace=lambda: tmp_path / "workspace",
    )


def test_existing_local_checkpoint_is_streamed_into_workspace_and_hashed(tmp_path):
    source = tmp_path / "existing" / "checkpoint.pt"
    source.parent.mkdir()
    source.write_bytes(b"checkpoint-bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    result = StageModelAssetTool().execute(
        _workspace(tmp_path),
        {
            "source_path": str(source),
            "asset_name": "Complex_base_ckpt.pt",
            "expected_sha256": digest,
        },
    )

    assert result == {
        "status": "staged",
        "path": "model-assets/Complex_base_ckpt.pt",
        "sha256": digest,
        "size": len(b"checkpoint-bytes"),
        "source_basename": "checkpoint.pt",
        "admitted": False,
        "note": "Run and verify a real inference canary before formal use.",
    }
    assert (
        tmp_path / "workspace" / "model-assets" / "Complex_base_ckpt.pt"
    ).read_bytes() == b"checkpoint-bytes"


def test_digest_mismatch_leaves_no_staged_checkpoint(tmp_path):
    source = tmp_path / "checkpoint.pt"
    source.write_bytes(b"wrong")
    result = StageModelAssetTool().execute(
        _workspace(tmp_path),
        {
            "source_path": str(source),
            "asset_name": "model.pt",
            "expected_sha256": "0" * 64,
        },
    )

    assert "SHA-256 mismatch" in result["error"]
    assert not (tmp_path / "workspace" / "model-assets" / "model.pt").exists()


def test_symlink_source_is_refused(tmp_path):
    source = tmp_path / "real.pt"
    source.write_bytes(b"weights")
    link = tmp_path / "link.pt"
    link.symlink_to(source)

    result = StageModelAssetTool().execute(
        _workspace(tmp_path), {"source_path": str(link)}
    )

    assert "must not be a symlink" in result["error"]
