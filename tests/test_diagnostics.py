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
    # than its content. `redact_url` still runs -- it is what makes the *local*
    # log safe to read -- and this asserts the outer boundary on top of it.
    assert b"8760" not in blob
    assert b"lines" in blob
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
