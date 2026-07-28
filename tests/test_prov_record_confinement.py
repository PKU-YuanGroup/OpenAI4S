"""Which files a cell may register as artifacts of its session.

`provenance_record` resolved its path with `Path(path).expanduser()` and
checked only that the file existed. Any absolute path on the host was
therefore registerable: `~/.ssh/id_rsa`, `/etc/passwd`, or the daemon's own
access-token file became a session artifact — listed, downloadable, and
carried by every surface that shows artifacts, including a read-only share.

Verified before the fix: a private key written outside the workspace came back
with a version id.

The confinement was not missing, it was unused. `self._resolve_path` is
injected into this service and its siblings call it; this one did not.

The same function also read the whole file into daemon memory to checksum it,
while every other artifact path in this codebase streams — so recording a
large output cost that much daemon RSS in a process serving every session.
"""

from __future__ import annotations

import hashlib

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import build_dispatcher


@pytest.fixture
def session(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    dispatcher = build_dispatcher(cfg, frame_id="f-test", workspace=workspace)
    return dispatcher._data_service, workspace, tmp_path


def test_a_file_outside_the_workspace_is_refused(session):
    """The defect, with the file it would have leaked."""
    service, _workspace, tmp_path = session
    outside = tmp_path / "elsewhere" / "id_rsa"
    outside.parent.mkdir()
    outside.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\nSECRET\n", "utf-8")

    result = service.provenance_record({"path": str(outside)})
    assert result.get("error"), "a key outside the workspace was registered"
    assert "escapes the workspace" in result["error"]
    assert "version_id" not in result
    # ...and the refusal does not quote the file's contents back.
    assert "SECRET" not in str(result)


def test_a_parent_traversal_is_refused_too(session):
    """The absolute form is the obvious one; `../` is the one a resolver that
    only checked `is_absolute()` would miss."""
    service, workspace, tmp_path = session
    outside = tmp_path / "escape.txt"
    outside.write_text("not yours\n", "utf-8")

    result = service.provenance_record({"path": "../escape.txt"})
    assert result.get("error")
    assert "escapes the workspace" in result["error"]


def test_a_real_output_still_registers(session):
    """The refusal must not be "refuse everything" — this is how a cell records
    what it produced."""
    service, workspace, _tmp = session
    produced = workspace / "result.csv"
    produced.write_text("a,b\n1,2\n", "utf-8")

    result = service.provenance_record({"path": str(produced)})
    assert not result.get("error"), result
    assert result.get("version_id")
    assert result.get("filename") == "result.csv"


def test_a_relative_path_resolves_inside_the_workspace(session):
    """A cell's cwd is its workspace, so a bare filename is the common case."""
    service, workspace, _tmp = session
    (workspace / "figure.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = service.provenance_record({"path": "figure.png"})
    assert not result.get("error"), result
    assert result.get("filename") == "figure.png"


def test_a_missing_file_is_still_a_plain_not_found(session):
    """Distinct from the escape refusal: "you asked for something that is not
    there" and "you asked for something you may not have" are different facts,
    and collapsing them would tell a cell its own output had escaped."""
    service, _workspace, _tmp = session
    result = service.provenance_record({"path": "nope.txt"})
    assert result.get("error")
    assert "no such output file" in result["error"]
    assert "escapes" not in result["error"]


def test_the_checksum_and_size_match_the_file(session):
    """Streaming must produce the same answer `read_bytes()` did, or artifact
    integrity silently changes meaning."""
    service, workspace, _tmp = session
    payload = bytes(range(256)) * 8192  # 2 MiB, so it spans several chunks
    produced = workspace / "big.bin"
    produced.write_bytes(payload)

    result = service.provenance_record({"path": str(produced)})
    assert result["size_bytes"] == len(payload)
    assert result["checksum"] == hashlib.sha256(payload).hexdigest()


def test_the_file_is_not_read_whole_into_memory(session):
    """The other half. Asserted against the read *pattern* rather than against
    process RSS, which is too noisy to fail honestly."""
    service, workspace, _tmp = session
    produced = workspace / "big.bin"
    produced.write_bytes(b"x" * (3 * 1024 * 1024))

    reads: list[int | None] = []
    real_open = open

    class _CountingFile:
        def __init__(self, handle):
            self._handle = handle

        def read(self, size=-1):
            reads.append(size)
            return self._handle.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._handle.close()
            return False

    import builtins

    def _open(file, mode="r", *args, **kwargs):
        handle = real_open(file, mode, *args, **kwargs)
        if "b" in mode and str(file).endswith("big.bin"):
            return _CountingFile(handle)
        return handle

    builtins.open = _open
    try:
        service.provenance_record({"path": str(produced)})
    finally:
        builtins.open = real_open

    assert reads, "the file was not read through open() at all"
    assert all(
        size and size > 0 for size in reads
    ), f"an unbounded read reached the file: {reads}"
    assert len(reads) > 1, "a 3 MiB file was consumed in a single read"
