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
from openai4s.observability import redact_text, redact_url

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


def test_an_unresolvable_secret_broker_does_not_overwrite_the_schema_probe(
    cfg, monkeypatch
):
    """A SecretBroker fails closed where there is no secure store — a headless
    Linux server without libsecret, a container — which is exactly the kind of
    host `doctor` gets run on. Sharing the schema probe's `except` made that
    absence overwrite a schema state that had already been read, so the report
    blamed the wrong probe and dropped `secret_store` altogether."""
    from openai4s.security.secret_broker import SecretStoreUnavailable
    from openai4s.store import Store

    message = "refusing to handle credentials without a secure store"

    def _unresolvable(self):
        raise SecretStoreUnavailable(message)

    monkeypatch.setattr(Store, "secrets", property(_unresolvable))

    report = security_posture(cfg)

    # The schema probe succeeded, so it still reports a schema — not the other
    # probe's failure.
    assert report["schema"]["expected"] == report["schema"]["version"]
    assert report["schema"].get("status") != "unavailable"
    # And the secret store names its own failure instead of going missing.
    assert report["secret_store"] == {
        "status": "unavailable",
        "error_type": "RuntimeError",
    }
    # `_probe_failure`, not `str(e)`: this lands in a shareable archive.
    assert message not in json.dumps(report)


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


# --------------------------------------------------------------------------
# the log the daemon actually writes
# --------------------------------------------------------------------------

# The three canaries plan item 16 plants in a failure. Built here rather than
# imported so this module still states what it is asserting about, and shaped
# so no substring of this source is itself credential-shaped.
_CANARY_KEY = "canary-live-" + "7d41f8b0c25e93a6d1e4f70b"
_FOREIGN_HOME = "/Users/canary/Documents/grant-embargo.csv"
_OPERATOR_HOST = "root@10.0.0.4"
_SHELL = f"rsync -av --delete /srv/raw {_OPERATOR_HOST}:/backup"


def test_the_bundle_collects_the_log_the_daemon_actually_writes(cfg, tmp_path):
    """Every test above writes `d.log`, and nothing in the product ever does.

    `build_bundle` globs `*.log*`. The daemon writes its stdout and stderr to
    `logs/app.out` — that is the redirection in all three packaged launchers
    (`build_macos_dmg.sh`, `build_linux_bundle.sh`, the Windows script), it is
    the path their own README tells a user to `tail`, and it is where
    `observability.log_event` ends up, because `log_event` writes to stderr.
    `app.out` does not match `*.log*`.

    So a support bundle shipped from a real install carried `report.json`,
    `MANIFEST.json` and no logs at all — while `MANIFEST.json` listed what it
    did include and so read as complete. The suite did not notice because it
    only ever asked about a filename the product does not use.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        "daemon started\nsomething failed\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    result = build_bundle(cfg, target)

    # Named by the archive, not after the file on disk: a log's name is
    # attacker-influenced the moment something writes one named after a token,
    # and the member name and MANIFEST are two places no content scrubber
    # looks.
    members = [name for name in result["included"] if name.startswith("logs/")]
    assert members == ["logs/log-0001.json"], result["included"]
    with zipfile.ZipFile(target) as archive:
        shared = archive.read(members[0]).decode("utf-8")
    # Collected, and summarised rather than quoted: an unstructured line is
    # arbitrary text and the archive boundary is deny-by-default.
    assert "something failed" not in shared
    assert '"lines": 2' in shared


def test_a_credential_in_the_daemon_log_never_reaches_the_bundle(cfg, tmp_path):
    """The same guarantee the `d.log` tests make, asserted on the real file."""
    (cfg.data_dir / "logs" / "app.out").write_text(
        f"upstream refused (authorization: Bearer {_CANARY_KEY})\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    assert _CANARY_KEY.encode() not in _bundle_bytes(target)


def test_another_accounts_home_directory_is_collapsed_in_the_bundle(cfg, tmp_path):
    """The diagnostic used to collapse `$HOME` because "of an absolute path, the
    username is the part that identifies a person rather than a file".

    That reason does not stop at this account. A path under someone else's home
    — a collaborator's export, a shared machine, a mounted volume — names a
    person just as squarely, and `str.replace($HOME, "~")` cannot see it. The
    bundle is shipped to support, so the rule has to be about the *shape* of a
    home directory rather than about this process's own.

    The file name survives on purpose: it is what makes the line worth keeping.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        f"FileNotFoundError while reading {_FOREIGN_HOME}\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    blob = _bundle_bytes(target)

    assert b"/Users/canary" not in blob, blob[:400]
    # The file name goes too. It used to be kept deliberately -- "it is what
    # makes the line worth keeping" -- and that reasoning does not survive the
    # bundle being shared: a path under someone else's home names a person and
    # the file names their unpublished work. An unstructured line is summarised.
    assert b"grant-embargo.csv" not in blob
    assert b"classes" in blob


def test_an_operator_host_is_not_shipped_in_the_bundle(cfg, tmp_path):
    """The part of a stray shell command that is worth removing.

    "No shell command in the bundle" is not implementable and would be the
    wrong rule anyway: there is no boundary between a command inside a failure
    message and the rest of that message, so a rule wide enough to catch this
    one also deletes the description the bundle exists to carry. What IS
    separable is the identity — `user@host` names an account on a machine, the
    same class of thing as a username in a path, and none of it helps anyone
    read the failure.

    Recorded residual: a bare address with no user attached (`10.0.0.4` alone)
    still survives. That is a deliberate stopping point, not an oversight — the
    rule here keys on the `user@host` shape, and guessing at every dotted quad
    in a traceback would start eating version numbers.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        f"CalledProcessError: `{_SHELL}` exited 23\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    blob = _bundle_bytes(target)

    assert _OPERATOR_HOST.encode() not in blob, blob[:400]
    assert b"root@" not in blob
    # The command goes too. This test used to assert `rsync` survived, on the
    # argument that the bundle is operator-facing and a command quoted inside a
    # failure is diagnostic content. That argument was wrong on its own
    # evidence: the same change made `app.out` the file the bundle collects, so
    # "operator-facing" stopped being a property of it. The plan's contract --
    # no shell command in a shareable ZIP -- was never mine to reinterpret.
    assert b"rsync" not in blob
    assert b"/srv/raw" not in blob


def test_a_real_diagnostic_record_is_scrubbed_in_the_bundle(cfg, tmp_path):
    """The shape the daemon actually writes, through the real recorder.

    With structured logs on, `app.out` holds JSON lines, and the structured
    branch is a different code path from the plain one the tests above take.
    It needs the identity pass just as much and for a subtler reason: `redact`
    asks whether a whole *field value* is a credential, and an exception detail
    is a sentence, so a home directory or an account sitting inside that
    sentence is not opaque and goes straight through field-wise redaction.

    Driven through `record_diagnostic` rather than a hand-written line, so what
    is asserted is what the production recorder emits — including its own
    `$HOME` collapse, which cannot see either of these.
    """
    from openai4s.server.errors import record_diagnostic

    class _CanaryFailure(RuntimeError):
        def __init__(self) -> None:
            super().__init__(
                f"upstream refused (authorization: Bearer {_CANARY_KEY}) "
                f"while reading {_FOREIGN_HOME} for `{_SHELL}`"
            )

    record = record_diagnostic(_CanaryFailure(), surface="bundle:canary")
    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    blob = _bundle_bytes(target)

    assert _CANARY_KEY.encode() not in blob
    assert b"/Users/canary" not in blob
    assert _OPERATOR_HOST.encode() not in blob
    # Still a diagnostic: the surface and the exception type, which are
    # allowlisted metadata. Not the sentence -- `record_diagnostic` no longer
    # renders the exception at all, so there is nothing to scrub.
    # `bundle:canary` is not a surface this repository names and
    # `_CanaryFailure` is not an exception category it names, so both are
    # reduced rather than echoed. A real surface travels as itself; that is
    # asserted in tests/test_diagnostic_archive_boundary.py.
    assert b"bundle:canary" not in blob
    assert b"_CanaryFailure" not in blob
    assert b"unhandled_exception" in blob
    assert b"upstream refused" not in blob
    # ...and the line is still parseable JSON, which the sanitising passes run
    # over as text and must not have broken.
    with zipfile.ZipFile(target) as archive:
        member = next(n for n in archive.namelist() if n.startswith("logs/"))
        line = archive.read(member).decode("utf-8").strip()
    parsed = json.loads(line)
    # The surface is reduced, not echoed, because `bundle:canary` is not one
    # this repository names. What survives is the shape of a diagnostic: a
    # known event, and a placeholder marking where the unknown value was.
    assert parsed["event"] == "unhandled_exception"
    assert parsed["surface"].startswith("<omitted:")


@pytest.mark.parametrize(
    "line, survives",
    [
        # A package spec is not an account, and it is exactly the sort of line
        # someone opens a bundle to read.
        ("installed scipy@1.14.1 into the r-mini env", True),
        ("resolved numpy@2.0 and pandas@2.2.3", True),
        # These are accounts on machines.
        ("ssh root@10.0.0.4 failed", False),
        ("notified alice@example.org", False),
        ("connected to admin@localhost", False),
    ],
)
def test_the_identity_pass_tells_an_account_from_a_version(line, survives):
    """A redactor that eats ordinary content stops being read, and then stops
    being run. Asserted on `redact_identities` directly because the boundary is
    the pattern, not the plumbing around it."""
    from openai4s.observability import redact_identities

    result = redact_identities(line)
    assert (result == line) is survives, result
    if not survives:
        assert "<redacted:" in result


# The daemon prints this at startup — twice — and every packaged launcher
# redirects that stdout into `logs/app.out`, which the bundle now collects.
# Copied from a real run rather than invented.
_TOKEN = "u4twvnEF" + "kYAgN3Ex2Sb89SPVbgjq5NBwiRaFa6cLaE0"
_LISTEN_LINE = (
    f"openai4s listening at http://127.0.0.1:8760/?token={_TOKEN} "
    "(model=doubao-seed-2.0-pro)"
)


def test_the_access_token_never_reaches_the_bundle(cfg, tmp_path):
    """The daemon's own startup banner is a credential in a URL.

    `redact_text` scans word by word and asks whether a word is opaque. A URL
    has no spaces, so the whole `http://…/?token=…` arrives as one word, and
    its scheme, dots and slashes stop it reading as opaque — the token rides
    through untouched. That is the same shape `retrieval_source` already
    guards a provenance URL against; the bundle did not have it.

    This became reachable the moment the bundle started collecting `app.out`,
    which is where every packaged launcher sends the banner. Collecting the
    right file and redacting it are one change, not two.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        _LISTEN_LINE + "\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    blob = _bundle_bytes(target)

    assert _TOKEN.encode() not in blob, blob[:400]
    # The banner is an unstructured line, so the archive keeps its shape rather
    # than its content. Inspect the log member itself: `report.json` contains
    # legitimate numeric facts such as migration timestamps, and an epoch can
    # coincidentally contain the four digits of the listening port.
    with zipfile.ZipFile(target) as archive:
        members = [name for name in archive.namelist() if name.startswith("logs/log-")]
        assert members == ["logs/log-0001.json"]
        log_blob = archive.read(members[0])
    assert b"http://127.0.0.1:8760" not in log_blob
    records = [json.loads(line) for line in log_blob.decode("utf-8").splitlines()]
    assert len(records) == 1
    assert set(records[0]) == {"archive_note", "lines", "classes", "fingerprint"}
    assert records[0]["lines"] == 1
    assert _TOKEN not in redact_url(_LISTEN_LINE.split(" ")[3])


def test_a_long_name_equals_value_is_still_treated_as_opaque():
    """Recorded because it is a real cost, not because it is desirable.

    `_looks_opaque` admits `=`, so any `name=value` of 24 characters or more
    reads as one credential-shaped token -- the daemon's own
    `(model=doubao-seed-2.0-pro)` among them. Splitting on `=` and judging the
    right-hand side would keep the model name and still catch
    `Authorization=sk-...`, which is strictly better on both counts.

    Asserted on `redact_text` directly rather than through the bundle: the
    archive boundary now withholds an unstructured line whatever it contains,
    so routing this through a ZIP would pass for the wrong reason and stop
    pinning the thing it exists to pin.
    """
    assert "<redacted:" in redact_text("listening (model=doubao-seed-2.0-pro)")


def test_a_credential_in_a_url_path_is_redacted_in_the_bundle(cfg, tmp_path):
    """The other URL shape, which has no query at all.

    Path-style keys are ordinary on scientific APIs, and a URL carrying one is
    exactly the URL a query-parameter rule never inspects.
    """
    key = "sk-live-" + "5f2c81aa47d9e603b1c8f4a2"
    (cfg.data_dir / "logs" / "app.out").write_text(
        f"GET https://api.example.org/v1/{key}/records failed\n", encoding="utf-8"
    )
    target = tmp_path / "b.zip"
    build_bundle(cfg, target)
    blob = _bundle_bytes(target)

    assert key.encode() not in blob, blob[:400]
    # The host goes with the rest of the unstructured line; `redact_url` is
    # asserted directly below, where it is the thing under test.
    assert b"api.example.org" not in blob
    assert redact_url(f"https://api.example.org/v1/{key}/records").count(key) == 0


@pytest.mark.parametrize("future", [False, True])
def test_schema_probe_distinguishes_future_and_corrupt_database(cfg, future):
    import sqlite3

    from openai4s.diagnostics import archive_safe
    from openai4s.storage.migrations import SCHEMA_VERSION

    if future:
        with sqlite3.connect(cfg.db_path) as conn:
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    else:
        cfg.db_path.write_bytes(b"invalid SQLite database" * 30)
    before = cfg.db_path.read_bytes()
    schema = security_posture(cfg)["schema"]
    if future:
        assert schema["code"] == "future_schema"
        assert schema["version"] == SCHEMA_VERSION + 1
        assert schema["expected"] == SCHEMA_VERSION
        assert schema["current"] is False
        archived = archive_safe({"security": {"schema": schema}})
        assert archived["security"]["schema"]["code"] == "future_schema"
    else:
        assert schema.get("code") != "future_schema"
        assert schema["error_type"] == "DatabaseError"
    assert schema["status"] == "unavailable"
    assert str(cfg.db_path) not in json.dumps(schema)
    assert cfg.db_path.read_bytes() == before


def _cli_diagnostics(monkeypatch, cfg, target):
    """`openai4s diagnostics --output target` against `cfg`'s data directory."""
    import importlib

    import openai4s.config as config_mod

    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(cfg.data_dir))
    monkeypatch.setattr(config_mod, "_CONFIG", None, raising=False)
    cli = importlib.import_module("openai4s.cli.main")
    return cli.main(["diagnostics", "--output", str(target)])


@pytest.mark.parametrize("conflicting", [False, True])
def test_the_bundle_does_not_upgrade_a_database_from_an_older_release(
    cfg, tmp_path, monkeypatch, capsys, conflicting
):
    """UPG5-01, the second diagnosis command. `security_posture` opened the
    Store read-write to read the schema, and on a database older than this
    release that open *is* the upgrade: `openai4s diagnostics` migrated a
    healthy data directory in place, and attempted (then rolled back, keeping
    its backup) one whose upgrade fails -- the directory a user with an upgrade
    problem runs it on for a bug report. It now reads the version the
    read-only way and records the pending upgrade instead of performing it."""
    from openai4s.diagnostics import archive_safe
    from openai4s.storage.migrations import SCHEMA_VERSION
    from tests.test_doctor import _older_database, _user_version

    _older_database(cfg.db_path, conflicting=conflicting)
    before = cfg.db_path.read_bytes()
    backup = cfg.db_path.with_name(f"openai4s.db.v{SCHEMA_VERSION - 1}.bak")
    target = tmp_path / "bundle.zip"

    assert _cli_diagnostics(monkeypatch, cfg, target) == 0
    capsys.readouterr()

    assert cfg.db_path.read_bytes() == before
    assert _user_version(cfg.db_path) == SCHEMA_VERSION - 1
    assert not backup.exists()
    with zipfile.ZipFile(target) as archive:
        security = json.loads(archive.read("report.json"))["security"]
    assert security["schema"] == {
        "status": "skipped",
        "code": "upgrade_pending",
        "version": SCHEMA_VERSION - 1,
        "expected": SCHEMA_VERSION,
        "current": False,
    }
    assert security["secret_store"] == {"status": "skipped"}
    # The in-process report and its shareable projection agree.
    live = security_posture(cfg)
    assert archive_safe({"security": live})["security"]["schema"] == security["schema"]
    assert cfg.db_path.read_bytes() == before


def test_the_bundle_leaves_a_database_that_needs_recovery_closed(cfg, monkeypatch):
    """A hot journal hides the version from a read-only handle, and the
    read-write open that recovers it would also upgrade an older database."""
    import openai4s.storage.migrations as migrations
    from openai4s.diagnostics import archive_safe
    from openai4s.store import Store

    Store(cfg.db_path).close()
    monkeypatch.setattr(migrations, "preflight_schema", lambda *_a, **_k: None)
    opened = []
    monkeypatch.setattr(
        "openai4s.store.get_store", lambda path: opened.append(path) or None
    )

    security = security_posture(cfg)
    assert opened == []
    assert security["schema"]["code"] == "interrupted_write"
    assert security["schema"]["current"] is False
    assert "version" not in security["schema"]
    archived = archive_safe({"security": security})["security"]
    assert archived["schema"]["code"] == "interrupted_write"
    assert archived["secret_store"] == {"status": "skipped"}


def test_the_bundle_does_not_open_a_database_whose_version_it_could_not_read(
    cfg, monkeypatch
):
    """Busy past SQLite's timeout (an older daemon mid-commit) is an
    OperationalError, not a corrupt file: the read-write open that followed it
    could get the lock and run the upgrade. The failure is recorded instead."""
    import sqlite3

    import openai4s.storage.migrations as migrations
    from openai4s.store import Store

    Store(cfg.db_path).close()

    def busy(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(migrations, "preflight_schema", busy)
    opened = []
    monkeypatch.setattr(
        "openai4s.store.get_store", lambda path: opened.append(path) or None
    )

    security = security_posture(cfg)
    assert opened == []
    assert security["schema"] == {
        "status": "unavailable",
        "error_type": "OperationalError",
    }
    assert security["secret_store"] == security["schema"]


# --------------------------------------------------------------------------
# what a bundle attached to "it stopped" has to say about the stop
# --------------------------------------------------------------------------
#
# A WSL user on 0.3.0 reported "已停止重复动作" two steps into every turn. The
# bundle carried postures, versions and log-line counts; the version itself
# was missing and nothing said how any turn had ended.

_MODEL_CANARY = "glm-private-canary-7"
_PROMPT_CANARY = "the unpublished cohort-seven assay results"
_PRIVATE_REASON = "private_cohort_alpha_stop"
_PRIVATE_PROVIDER = "acme-internal-relay"


def test_the_version_falls_back_to_the_package_when_metadata_is_missing(monkeypatch):
    """`importlib.metadata` had no dist-info to read there, `_version()`
    answered "unknown", and `_v_version` rightly dropped that -- leaving
    `environment.fields_omitted: 1` where the version should have been."""
    import importlib.metadata

    import openai4s
    from openai4s.diagnostics import archive_safe

    def no_dist_info(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", no_dist_info)

    environment = environment_report()
    archived = archive_safe({"environment": environment})["environment"]

    assert environment["openai4s"] == openai4s.__version__
    assert archived["openai4s"]
    assert openai4s.__version__.startswith(archived["openai4s"])
    assert "fields_omitted" not in archived


@pytest.mark.parametrize(
    "release, banner, wsl, reduced",
    [
        # What the WSL user's report showed: the release reduced to 6.6.87.
        ("6.6.87.2-microsoft-standard-WSL2", "", True, "6.6.87"),
        ("4.4.0-19041-Microsoft", "", True, "4.4"),
        (
            "6.6.87.2",
            "Linux version 6.6.87.2-microsoft-standard-WSL2 (root@build) #1 SMP",
            True,
            "6.6.87.2",
        ),
        (
            "6.5.0-15-generic",
            "Linux version 6.5.0-15-generic (buildd@lcy02-amd64-027) #15-Ubuntu",
            False,
            "6.5",
        ),
    ],
)
def test_a_wsl_kernel_is_named_without_its_release_string(
    monkeypatch, tmp_path, release, banner, wsl, reduced
):
    """The release is reduced to its numeric prefix, so a WSL report used to
    read exactly like any other Linux host. The answer travels as a boolean;
    the strings it was read from do not."""
    import openai4s.diagnostics as diagnostics
    from openai4s.diagnostics import archive_safe

    proc_version = tmp_path / "version"
    proc_version.write_text(banner, encoding="utf-8")
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Linux")
    monkeypatch.setattr(diagnostics.platform, "release", lambda: release)
    monkeypatch.setattr(diagnostics, "_PROC_VERSION", proc_version)

    archived = archive_safe({"environment": environment_report()})["environment"]

    assert archived["wsl"] is wsl
    assert archived["release"] == reduced
    shared = json.dumps(archived).lower()
    assert "microsoft" not in shared and "wsl2" not in shared


def test_only_a_linux_kernel_is_asked_whether_it_is_wsl(monkeypatch, tmp_path):
    import openai4s.diagnostics as diagnostics

    proc_version = tmp_path / "version"
    proc_version.write_text("Linux version 6.6.87.2-microsoft-standard-WSL2")
    monkeypatch.setattr(diagnostics, "_PROC_VERSION", proc_version)
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(diagnostics.platform, "release", lambda: "24.0.0")

    assert environment_report()["wsl"] is False


def _ledger_turn(store, frame_id, reason, *, provider="ark", progress_reason=None):
    """One real turn's ledger rows: the user message, then its terminal."""
    from openai4s.agent.ledger import RuntimeActionLedger, new_turn_id

    ledger = RuntimeActionLedger(
        store, frame_id, new_turn_id(), provider=provider, model=_MODEL_CANARY
    )
    ledger.append_user({"content": _PROMPT_CANARY})
    ledger.append_terminal(reason, progress_reason=progress_reason)


def test_the_bundle_says_how_the_recent_turns_ended(cfg, tmp_path):
    from openai4s.diagnostics import TURN_WINDOW
    from openai4s.store import Store

    store = Store(cfg.db_path)
    try:
        root = store.new_frame()
        child = store.new_frame(parent_id=root, kind="delegate", depth=1)
        _ledger_turn(store, root, "submitted", provider="claude")
        _ledger_turn(store, root, "no_progress", progress_reason="same_action")
        # A delegated child records its own terminal under its own frame; it
        # is not a turn the user saw end.
        _ledger_turn(store, child, "max_turns")
        _ledger_turn(store, root, _PRIVATE_REASON, provider=_PRIVATE_PROVIDER)
        _ledger_turn(
            store, root, "no_progress", progress_reason="consecutive_malformed"
        )
    finally:
        store.close()
    target = tmp_path / "b.zip"

    build_bundle(cfg, target)

    with zipfile.ZipFile(target) as archive:
        turns = json.loads(archive.read("report.json"))["agent_turns"]
    assert turns == {
        "status": "ok",
        "window": TURN_WINDOW,
        "terminals": 4,
        "reasons": {"no_progress": 2, "other": 1, "submitted": 1},
        "progress_reasons": {"consecutive_malformed": 1, "same_action": 1},
        "wires": {"anthropic": 1, "openai": 2, "unknown": 1},
        "latest": {
            "reason": "no_progress",
            "progress_reason": "consecutive_malformed",
            "wire": "openai",
        },
    }
    # Codes from a set written down in source travel; names an operator or a
    # model chose do not, and neither does anything the turn was about.
    blob = _bundle_bytes(target)
    for private in (_PRIVATE_REASON, _PRIVATE_PROVIDER, _MODEL_CANARY, _PROMPT_CANARY):
        assert private.encode() not in blob, private


def test_the_window_holds_the_newest_turns(cfg):
    from openai4s.diagnostics import turn_stop_report
    from openai4s.store import Store

    store = Store(cfg.db_path)
    try:
        root = store.new_frame()
        _ledger_turn(store, root, "max_turns")
        for _ in range(3):
            _ledger_turn(store, root, "submitted")
    finally:
        store.close()

    report = turn_stop_report(cfg, window=3)

    assert report["terminals"] == 3
    assert report["reasons"] == {"submitted": 3}


def test_reading_the_ledger_writes_nothing(cfg):
    """The same `mode=ro` read the schema probe makes: no upgrade, no journal,
    not a byte of the database changed."""
    from openai4s.diagnostics import turn_stop_report
    from openai4s.store import Store

    store = Store(cfg.db_path)
    try:
        _ledger_turn(store, store.new_frame(), "no_progress", progress_reason="x")
    finally:
        store.close()
    before = cfg.db_path.read_bytes()

    report = turn_stop_report(cfg)

    assert report["progress_reasons"] == {"other": 1}
    assert cfg.db_path.read_bytes() == before
    assert not cfg.db_path.with_name(cfg.db_path.name + "-journal").exists()


def test_a_database_that_needs_recovery_is_left_for_the_open_that_replays_it(
    tmp_path,
):
    """A hot journal can only be replayed by a read-write open. The read-only
    handle reports that it could not read, rather than failing the bundle or
    touching either file."""
    from types import SimpleNamespace

    from openai4s.diagnostics import turn_stop_report
    from openai4s.store import Store
    from tests.test_schema_migrations import _leave_hot_rollback_journal

    path = tmp_path / "crashed.db"
    Store(path).close()
    _leave_hot_rollback_journal(path)
    journal = path.with_name(path.name + "-journal")
    before = (path.read_bytes(), journal.read_bytes())

    assert turn_stop_report(SimpleNamespace(db_path=path)) == {
        "status": "skipped",
        "code": "interrupted_write",
    }
    assert (path.read_bytes(), journal.read_bytes()) == before


def test_a_database_without_turns_to_count_says_why(tmp_path):
    import sqlite3
    from types import SimpleNamespace

    from openai4s.diagnostics import archive_safe, turn_stop_report

    absent = tmp_path / "absent.db"
    predates_ledger = tmp_path / "old.db"
    with sqlite3.connect(predates_ledger) as conn:
        conn.execute("CREATE TABLE frames (frame_id TEXT PRIMARY KEY)")
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"invalid SQLite database" * 30)

    reports = [
        turn_stop_report(SimpleNamespace(db_path=path))
        for path in (absent, predates_ledger, corrupt)
    ]

    assert reports == [
        {"status": "skipped", "code": "no_database"},
        {"status": "skipped", "code": "no_ledger"},
        {"status": "unavailable", "error_type": "DatabaseError"},
    ]
    assert [archive_safe({"agent_turns": r})["agent_turns"] for r in reports] == (
        reports
    )
    assert not absent.exists()


def _recordable_stop_reasons() -> set[str]:
    """Every terminal reason the source can hand to the Action Ledger.

    Read out of the code rather than listed, so a reason added later fails
    `test_every_recordable_stop_reason_is_nameable` instead of arriving in
    bundles as `other`.
    """
    import ast
    from pathlib import Path

    import openai4s
    from openai4s.agent.progress_circuit import NO_PROGRESS_STOP_REASON
    from openai4s.agent.recovery import recovery_message

    def literals(node):
        return {
            item.value
            for item in ast.walk(node)
            if isinstance(item, ast.Constant) and isinstance(item.value, str)
        }

    found = {NO_PROGRESS_STOP_REASON}
    package = Path(openai4s.__file__).parent
    for source in package.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name == "append_terminal" and node.args:
                found |= literals(node.args[0])
            elif name == "_finish" and len(node.args) >= 3:
                found |= literals(node.args[2])
            elif name == "AutoBudgetDenied" and node.args:
                if isinstance(node.args[0], ast.Constant):
                    found |= literals(node.args[0])
            for keyword in node.keywords:
                if keyword.arg == "stop_reason" and isinstance(
                    keyword.value, ast.Constant
                ):
                    found |= literals(keyword.value)
        if source.name == "models.py" and source.parent.name == "llm":
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.FunctionDef)
                    and node.name == "llm_failure_code"
                ):
                    # The gateway records a model-call failure by its own code
                    # only when a stopped turn can be continued from it.
                    found |= {
                        code
                        for code in literals(node)
                        if code.startswith("llm_") and recovery_message(code)
                    }
    return found


def test_every_recordable_stop_reason_is_nameable():
    from openai4s.diagnostics import _TURN_REASONS

    recordable = _recordable_stop_reasons()

    # The scan itself has to find the reasons this investigation turned on.
    assert {"no_progress", "max_turns", "submitted", "llm_stream_timeout"} <= (
        recordable
    )
    assert recordable - _TURN_REASONS == set()


def test_the_code_sets_track_their_sources():
    from openai4s.agent.progress_circuit import PROGRESS_REASONS
    from openai4s.diagnostics import _PROGRESS_REASONS, _WIRE_FAMILIES
    from openai4s.llm import SUPPORTED_WIRES

    assert _PROGRESS_REASONS == set(PROGRESS_REASONS) | {"other"}
    assert _WIRE_FAMILIES == set(SUPPORTED_WIRES) | {"unknown"}
