"""The team file area (M1-8): allowlisted roots, traversal, streaming upload.

FileArea's path policy is tested directly; the routes over a real socket.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openai4s.server.file_area import FileArea, FileAreaError
from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _get,
    _login,
    _speak,
    _TeamDaemon,
)

# -- FileArea path policy -----------------------------------------------------


def test_unconfigured_area_refuses_everything(tmp_path):
    area = FileArea([])
    assert not area.configured
    with pytest.raises(FileAreaError) as e:
        area.resolve(str(tmp_path))
    assert e.value.code == "no_data_roots"


def test_resolution_is_contained(tmp_path):
    root = tmp_path / "datasets"
    root.mkdir()
    (root / "a.txt").write_text("x")
    area = FileArea([root])

    assert area.resolve(str(root / "a.txt")) == (root / "a.txt").resolve()
    for outside in (
        "/etc/passwd",
        str(tmp_path),  # the parent of the root
        str(root / ".." / "other"),
        str(root) + "-sibling",  # prefix-string trap: /x/datasets-sibling
    ):
        with pytest.raises(FileAreaError) as e:
            area.resolve(outside)
        assert e.value.status == 404, outside


def test_symlink_escape_is_refused(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("s")
    (root / "link").symlink_to(secret)
    area = FileArea([root])
    with pytest.raises(FileAreaError):
        area.resolve(str(root / "link"))


def test_upload_target_rules(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    area = FileArea([root])
    assert area.resolve_upload_target(str(root), "ok.txt") == root / "ok.txt"
    for bad in ("", ".", "..", "a/b", "a\\b", "a\x00b"):
        with pytest.raises(FileAreaError):
            area.resolve_upload_target(str(root), bad)
    with pytest.raises(FileAreaError):
        area.resolve_upload_target("/etc", "x")


def test_listing_shape(tmp_path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "b.txt").write_text("bb")
    area = FileArea([root])
    roots = area.list_roots()
    assert roots["roots"][0]["path"] == str(root.resolve())
    listing = area.list_dir(str(root))
    names = [e["name"] for e in listing["entries"]]
    assert names == ["sub", "b.txt"]  # dirs first
    assert listing["entries"][1]["size"] == 2


# -- the routes ---------------------------------------------------------------


@pytest.fixture()
def daemon(tmp_path: Path):
    root = tmp_path / "datasets"
    root.mkdir()
    (root / "hello.txt").write_bytes(b"hello world")
    node = _TeamDaemon(tmp_path / "home", data_roots=[root])
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    node.seed_user("visitor", "fake-pw-v", role="guest")
    node.root = root
    try:
        yield node
    finally:
        node.close()


def _body_json(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _upload(daemon, cookie: str, directory: str, name: str, data: bytes, extra_q=""):
    from urllib.parse import quote

    path = f"/api/v1/files/upload?dir={quote(directory)}&name={quote(name)}{extra_q}"
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Cookie: {cookie}",
        "Content-Type: application/octet-stream",
        f"Content-Length: {len(data)}",
        "Connection: close",
    ]
    head = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")
    return _speak(daemon.port, head + data)


def test_list_roots_and_dir(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, raw = _get(daemon.port, "/api/v1/files", cookie=cookie)
    assert status == 200
    assert _body_json(raw)["roots"][0]["path"] == str(daemon.root.resolve())

    from urllib.parse import quote

    status, raw = _get(
        daemon.port, f"/api/v1/files?path={quote(str(daemon.root))}", cookie=cookie
    )
    assert status == 200
    assert [e["name"] for e in _body_json(raw)["entries"]] == ["hello.txt"]


def test_download_streams_the_bytes(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    from urllib.parse import quote

    status, raw = _get(
        daemon.port,
        f"/api/v1/files/download?path={quote(str(daemon.root / 'hello.txt'))}",
        cookie=cookie,
    )
    assert status == 200
    assert raw.endswith(b"hello world")
    assert b"attachment" in raw


def test_traversal_is_404_over_http(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    from urllib.parse import quote

    for target in ("/etc/passwd", str(daemon.root / ".." / "home")):
        status, raw = _get(
            daemon.port,
            f"/api/v1/files/download?path={quote(target)}",
            cookie=cookie,
        )
        assert status == 404, (target, raw[:200])


def test_upload_round_trip_and_conflict(daemon):
    """Uploads land in the member's own subtree of the root.

    Not decoration: every file here is written by the daemon's uid, so
    "is this yours?" cannot be answered after the fact -- the writable area
    has to be scoped by construction or `overwrite=1` is a cross-user
    clobber of anything a colleague put in the shared area.
    """
    cookie = _login(daemon, "alice", "fake-pw-a")
    mine = daemon.root / "alice"
    status, raw = _upload(daemon, cookie, str(daemon.root), "up.bin", b"A" * 4096)
    assert status == 201, raw[:300]
    assert (mine / "up.bin").read_bytes() == b"A" * 4096
    # no temp file left behind
    assert [p.name for p in mine.glob(".up.bin.upload-*")] == []

    status, _ = _upload(daemon, cookie, str(daemon.root), "up.bin", b"B")
    assert status == 409
    status, _ = _upload(
        daemon, cookie, str(daemon.root), "up.bin", b"B", extra_q="&overwrite=1"
    )
    assert status == 201
    assert (mine / "up.bin").read_bytes() == b"B"


def test_one_member_cannot_overwrite_anothers_upload(daemon):
    """The defect: containment inside the roots says where a file may live,
    never whose it is, so `overwrite=1` reached across users."""
    alice = _login(daemon, "alice", "fake-pw-a")
    bob = _login(daemon, "bob", "fake-pw-b")

    status, _ = _upload(daemon, alice, str(daemon.root), "shared.csv", b"alice data")
    assert status == 201
    victim = daemon.root / "alice" / "shared.csv"
    assert victim.read_bytes() == b"alice data"

    # bob aims at her file by its full path, with overwrite
    status, _ = _upload(
        daemon,
        bob,
        str(daemon.root / "alice"),
        "shared.csv",
        b"bob was here",
        extra_q="&overwrite=1",
    )
    assert victim.read_bytes() == b"alice data", "bob overwrote alice's file"
    # his bytes went to his own area, if anywhere
    stray = daemon.root / "bob" / "shared.csv"
    if stray.exists():
        assert stray.read_bytes() == b"bob was here"


def test_upload_outside_root_is_404(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, _ = _upload(daemon, cookie, "/etc", "evil.txt", b"x")
    assert status == 404
    assert not os.path.exists("/etc/evil.txt")


def test_upload_bad_filename_is_400(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    status, _ = _upload(daemon, cookie, str(daemon.root), "..", b"x")
    assert status == 400


def test_oversize_upload_is_413_before_reading(daemon):
    cookie = _login(daemon, "alice", "fake-pw-a")
    from urllib.parse import quote

    path = f"/api/v1/files/upload?dir={quote(str(daemon.root))}&name=big.bin"
    lines = [
        f"POST {path} HTTP/1.1",
        f"Host: 127.0.0.1:{daemon.port}",
        f"Content-Length: {600 * 1024 * 1024}",
        f"Cookie: {cookie}",
        "Connection: close",
    ]
    status, raw = _speak(daemon.port, ("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    assert status == 413, raw[:200]
    assert not (daemon.root / "big.bin").exists()


def test_guest_is_read_nothing_here(daemon):
    cookie = _login(daemon, "visitor", "fake-pw-v")
    status, raw = _get(daemon.port, "/api/v1/files", cookie=cookie)
    assert status == 403
    assert _body_json(raw).get("code") == "guest_readonly"
    status, _ = _upload(daemon, cookie, str(daemon.root), "g.txt", b"x")
    assert status == 403


def test_unauthenticated_files_access_is_401(daemon):
    status, _ = _get(daemon.port, "/api/v1/files")
    assert status == 401


def test_unconfigured_roots_shape(tmp_path):
    node = _TeamDaemon(tmp_path / "home2")
    node.seed_user("alice", "fake-pw-a")
    try:
        cookie = _login(node, "alice", "fake-pw-a")
        status, raw = _get(node.port, "/api/v1/files", cookie=cookie)
        assert status == 404
        assert _body_json(raw).get("code") == "no_data_roots"
    finally:
        node.close()
