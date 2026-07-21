"""The credential store must not be readable by other local accounts.

The SQLite database holds plaintext credentials (model-profile API keys,
connector env, managed-endpoint tokens) and was created at the process umask —
0644 on most systems, i.e. any local login could read it. Encrypting those
secrets is the real fix; this closes the trivial read in the meantime, and
stops a backup or rsync of ~/.openai4s from carrying world-readable secrets to
wherever it lands.

Asserted against the filesystem rather than against the chmod call, and phrased
as "no group/other bits" rather than "== 0600" so the tests describe exposure
rather than an exact number.
"""
import os
import stat

import pytest

from openai4s.config import Config
from openai4s.security.permissions import (
    harden_dir,
    harden_file,
    is_owner_only,
    posture,
    verify,
)
from openai4s.store import get_store

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX modes are not enforced on this platform"
)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_new_database_is_not_world_readable(tmp_path):
    """The headline regression: the credential DB was born -rw-r--r--."""
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert is_owner_only(store.db_path), oct(_mode(store.db_path))
    assert _mode(store.db_path) == 0o600


def test_database_parent_dir_is_owner_only(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    assert is_owner_only(store.db_path.parent), oct(_mode(store.db_path.parent))


def test_hardening_survives_a_permissive_umask(tmp_path):
    """A permissive umask is exactly the case that produced the exposure, so
    the mode has to be set explicitly rather than inherited from mkdir(mode=)."""
    old = os.umask(0o000)
    try:
        store = get_store(Config(data_dir=tmp_path / "wide").db_path)
        assert _mode(store.db_path) == 0o600
        assert _mode(store.db_path.parent) == 0o700
    finally:
        os.umask(old)


def test_wal_sidecars_are_hardened_too(tmp_path):
    """-wal/-shm carry the same committed rows as the database. Hardening only
    the main file would leave the contents readable through them."""
    from openai4s.security.permissions import harden_db

    db = tmp_path / "x.db"
    db.write_bytes(b"")
    for suffix in ("-wal", "-shm"):
        sidecar = db.with_name(db.name + suffix)
        sidecar.write_bytes(b"")
        os.chmod(sidecar, 0o644)

    assert harden_db(db)
    for suffix in ("-wal", "-shm"):
        sidecar = db.with_name(db.name + suffix)
        assert is_owner_only(sidecar), f"{suffix}: {oct(_mode(sidecar))}"


def test_ensure_dirs_hardens_every_subdirectory(tmp_path):
    """artifacts/ and logs/ carry research data and prompts, not just the DB."""
    cfg = Config(data_dir=tmp_path / "data")
    cfg.ensure_dirs()
    assert is_owner_only(cfg.data_dir)
    for sub in ("logs", "artifacts", "tool-results", "compaction-history"):
        assert is_owner_only(cfg.data_dir / sub), sub


def test_harden_dir_and_file_report_the_result(tmp_path):
    d = tmp_path / "d"
    d.mkdir(mode=0o777)
    f = tmp_path / "f"
    f.write_text("x")
    os.chmod(f, 0o666)

    assert harden_dir(d) is True
    assert harden_file(f) is True
    assert verify(d, 0o700)
    assert verify(f, 0o600)


def test_harden_reports_false_rather_than_raising_on_a_missing_path(tmp_path):
    """Best-effort by contract: a filesystem that cannot represent the mode
    must not stop the daemon from starting."""
    assert harden_file(tmp_path / "nope") is False


def test_posture_reports_the_truth(tmp_path):
    store = get_store(Config(data_dir=tmp_path).db_path)
    p = posture(store.db_path.parent, store.db_path)
    assert p["supported"] is True
    assert p["data_dir_owner_only"] is True
    assert p["db_owner_only"] is True


def test_posture_does_not_claim_a_boundary_it_lacks(tmp_path):
    """A path with no hardening applied must report exposed, not assume."""
    wide = tmp_path / "wide"
    wide.mkdir(mode=0o755)
    os.chmod(wide, 0o755)
    p = posture(wide, wide / "absent.db")
    assert p["data_dir_owner_only"] is False
    assert p["db_owner_only"] is False
