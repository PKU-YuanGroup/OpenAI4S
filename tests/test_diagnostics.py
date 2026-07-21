"""The diagnostic bundle must be safe to paste into a public issue.

When a user reports "it failed", the useful reply is one command whose output
they can attach. Doing that by hand means deciding under time pressure which of
the daemon's files are safe to share, and the failure mode of getting it wrong
is a credential in a public tracker. So the bundle is assembled by code that
knows what must never go in.

The tests assert on *values* leaving the process, not on which files were
chosen — a bundle that excludes the database but leaks a key from a log line is
not safe, and only a value assertion notices that.

The free-text case is the one that actually bit: `redact()` answers "is this
whole value a credential", which is right for a field and wrong for a log line,
where a token sits mid-sentence surrounded by spaces. A first version of this
bundle passed the structured lines and leaked the plain one.
"""
import json
import zipfile

import pytest

from openai4s.config import Config
from openai4s.diagnostics import (
    LOG_KEEP,
    build_bundle,
    environment_report,
    rotate_log,
    security_posture,
)
from openai4s.observability import redact_text

_KEY = "canary-live-9f3a1c7e4b2d8e6f0a1b2c3d"


@pytest.fixture
def cfg(tmp_path):
    config = Config(data_dir=tmp_path / "data")
    config.ensure_dirs()
    return config


def _bundle_bytes(path):
    with zipfile.ZipFile(path) as archive:
        return b"".join(archive.read(name) for name in archive.namelist())


# --------------------------------------------------------------------------
# redaction of free text — the case that bit
# --------------------------------------------------------------------------


def test_a_token_inside_a_sentence_is_redacted():
    """`redact` asks whether the WHOLE value is a credential. In a log line the
    surrounding spaces alone make that false, so a stray print sails through."""
    out = redact_text(f"connecting with {_KEY} to the provider")
    assert _KEY not in out
    assert "connecting with" in out


@pytest.mark.parametrize(
    "line",
    [
        "key={KEY},",
        "used ({KEY})",
        "token: {KEY}.",
        '"{KEY}"',
    ],
)
def test_punctuation_around_a_token_does_not_hide_it(line):
    """Prose abuts tokens with commas, quotes, and brackets."""
    assert _KEY not in redact_text(line.format(KEY=_KEY))


def test_ordinary_prose_survives():
    """Redaction that eats the message makes the log worthless, and a worthless
    log stops being read."""
    text = "kernel restarted after a failed cell in /api/v1/frames/abc"
    assert redact_text(text) == text


def test_the_same_token_redacts_to_the_same_fingerprint():
    """Two lines about one credential must stay correlatable without either
    revealing it."""
    a = redact_text(f"first {_KEY}")
    b = redact_text(f"second {_KEY}")
    tag = a.split("first ")[1]
    assert tag in b


# --------------------------------------------------------------------------
# the bundle
# --------------------------------------------------------------------------


def test_a_secret_in_a_structured_log_line_never_reaches_the_bundle(cfg, tmp_path):
    (cfg.data_dir / "logs" / "d.log").write_text(
        json.dumps({"event": "x", "api_key": _KEY}) + "\n"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    assert _KEY.encode() not in _bundle_bytes(target)


def test_a_secret_in_a_plain_log_line_never_reaches_the_bundle(cfg, tmp_path):
    """The regression this module was rewritten for."""
    (cfg.data_dir / "logs" / "d.log").write_text(f"oops printed {_KEY} here\n")
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    assert _KEY.encode() not in _bundle_bytes(target)


def test_the_database_is_never_collected(cfg, tmp_path):
    """It holds research work and, until fully brokered, credentials."""
    cfg.db_path.write_bytes(b"SQLite format 3\x00" + _KEY.encode())
    target = tmp_path / "b.zip"
    result = build_bundle(cfg, target)
    with zipfile.ZipFile(target) as archive:
        assert not [n for n in archive.namelist() if n.endswith(".db")]
    assert _KEY.encode() not in _bundle_bytes(target)
    assert any(e["path"] == "openai4s.db" for e in result["excluded"])


def test_the_manifest_says_what_was_left_out(cfg, tmp_path):
    """A bundle that silently omits things invites a second, manual, unredacted
    collection."""
    cfg.db_path.write_bytes(b"x")
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    with zipfile.ZipFile(target) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
    assert manifest["included"]
    assert manifest["excluded"][0]["reason"]


def test_the_report_records_every_boundary_posture(cfg):
    report = security_posture(cfg)
    for key in ("permissions", "kernel_sandbox", "compute_confinement", "schema"):
        assert key in report


def test_the_environment_report_does_not_leak_a_home_directory():
    """A version report is for a public issue; a path is a username."""
    import json as _json
    from pathlib import Path

    assert str(Path.home()) not in _json.dumps(environment_report())


def test_the_bundle_is_owner_only(cfg, tmp_path):
    import os

    if os.name != "posix":
        pytest.skip("POSIX modes only")
    from openai4s.security.permissions import is_owner_only

    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    assert is_owner_only(target)


def test_a_bundle_works_with_no_logs_at_all(cfg, tmp_path):
    target = tmp_path / "b.zip"
    result = build_bundle(cfg, target)
    assert "report.json" in result["included"]


# --------------------------------------------------------------------------
# retention
# --------------------------------------------------------------------------


def test_a_small_log_is_not_rotated(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("short")
    assert rotate_log(log, max_bytes=1024) is False
    assert log.exists()


def test_an_oversized_log_rotates(tmp_path):
    log = tmp_path / "a.log"
    log.write_text("x" * 2048)
    assert rotate_log(log, max_bytes=1024) is True
    assert (tmp_path / "a.log.1").exists()
    assert not log.exists()


def test_generations_are_bounded(tmp_path):
    """Unbounded logs are not a neutral default — they are a slow disk-full
    that arrives at the least convenient moment."""
    log = tmp_path / "a.log"
    for _ in range(LOG_KEEP + 3):
        log.write_text("x" * 2048)
        rotate_log(log, max_bytes=1024, keep=LOG_KEEP)
    generations = sorted(p.name for p in tmp_path.glob("a.log.*"))
    assert len(generations) == LOG_KEEP, generations


def test_rotating_a_missing_log_is_not_an_error(tmp_path):
    assert rotate_log(tmp_path / "absent.log") is False
