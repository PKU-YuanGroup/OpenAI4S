"""Nothing unknown leaves the machine in a shareable diagnostics ZIP.

The contract is the plan's, not this module's to soften: a bundle a user
attaches to a bug report carries no shell command, no arbitrary exception text
and no foreign absolute path. An earlier pass argued the bundle is
"operator-facing" and may keep a command quoted inside a failure. That argument
was wrong on its own evidence — the same pass proved `record_diagnostic`'s
original text reaches `logs/app.out`, and then made `app.out` the file the
bundle collects. The moment it leaves the machine, "operator-facing" stops
being a property of it.

So the boundary is **deny-by-default**, and the tests are the canary matrix
rather than a list of shapes someone thought of:

* structured lines survive only as an allowlist of validated, bounded metadata;
  every other value is replaced by a stable fingerprint;
* a plain line is never shared verbatim at all — it becomes a count, a class
  and a fingerprint;
* `report.json` goes through the same sanitizer, and its `default=str` escape
  hatch is gone, because "stringify anything" is exactly the bypass;
* the local operator log keeps its richness. Only the archive is narrowed.

`redact_text`/`redact_identities`/`redact_url` all still run. They are the
*inner* layer: they make the local log safer to read. They are not the thing
standing between a user's disk and a public issue tracker, and treating them as
if they were is what let a sentence in an ordinary `message` field carry a
credential straight through field-wise redaction.
"""
from __future__ import annotations

import json
import zipfile

import pytest

from openai4s.config import Config
from openai4s.diagnostics import build_bundle

# The canaries, built so no substring of this source is itself credential- or
# path-shaped enough to trip the scanners that read this repo.
RAW_PHRASE = "upstream refused while reconciling cohort"
FOREIGN_PATH = "/srv/raw/embargo/grant-2026.csv"
SHELL_COMMAND = "rsync -av --delete /srv/raw backup:/vault"
CREDENTIAL = "canary-live-" + "8f31d7b04ea25c96d1b3e70f"
TOKEN = "u4twvnEF" + "kYAgN3Ex2Sb89SPVbgjq5NBwiRaFa6cLaE0"
TOKEN_URL = f"http://127.0.0.1:8760/?token={TOKEN}"
FRAGMENT_TOKEN = "ya29ABCDEFGHIJ" + "KLMNOPQRSTUVWXYZ0123456789"
FRAGMENT_URL = f"https://idp.example.org/cb#access_token={FRAGMENT_TOKEN}"

CANARIES = (
    RAW_PHRASE,
    FOREIGN_PATH,
    SHELL_COMMAND,
    CREDENTIAL,
    TOKEN,
    FRAGMENT_TOKEN,
)


class HostileFailure(RuntimeError):
    """A message that is neither fixed, short, nor safe.

    `__str__` is computed, so anything that renders it to decide what to keep
    has already lost: the decision has to be made without ever asking.
    """

    def __init__(self, repeat: int = 1) -> None:
        super().__init__("built by __str__")
        self.repeat = repeat

    def __str__(self) -> str:
        body = (
            f"{RAW_PHRASE} {FOREIGN_PATH} `{SHELL_COMMAND}` "
            f"(token {CREDENTIAL}) {TOKEN_URL}"
        )
        return body * self.repeat


class UnrenderableFailure(RuntimeError):
    """Rendering it raises. Nothing on the diagnostic path may depend on it."""

    def __str__(self) -> str:
        raise ValueError("this exception refuses to be rendered")


@pytest.fixture
def cfg(tmp_path):
    config = Config(data_dir=tmp_path / "data")
    config.ensure_dirs()
    return config


def _bundle(cfg, tmp_path, name="b.zip") -> bytes:
    target = tmp_path / name
    build_bundle(cfg, target)
    with zipfile.ZipFile(target) as archive:
        return b"".join(archive.read(n) for n in archive.namelist())


def assert_no_canary(blob: bytes, *, where: str) -> None:
    for canary in CANARIES:
        assert canary.encode() not in blob, f"{canary!r} survived into {where}"


# --------------------------------------------------------------------------
# A. the production source: record_diagnostic never renders the exception
# --------------------------------------------------------------------------


def test_the_diagnostic_record_never_renders_an_unknown_exception():
    """The record is built from what the *type* is, not from what it says.

    `str(exc)` on an unknown exception is unknown free text by definition, and
    a diagnostic outlives the request. Keeping a redacted rendering was the
    previous answer and it does not hold: redaction is a set of patterns, the
    message is arbitrary, and the patterns lost — a `/srv` path, a command and
    an ordinary English sentence all survived every one of them.
    """
    from openai4s.server.errors import record_diagnostic

    record = record_diagnostic(HostileFailure(), surface="canary:a", request_id="req-a")
    blob = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")

    assert_no_canary(blob, where="the diagnostic record")
    # ...and it is still a usable diagnostic.
    assert record["event"] == "unhandled_exception"
    assert record["exception"] == "HostileFailure"
    assert record["surface"] == "canary:a"
    assert record["request_id"] == "req-a"
    assert record["detail"]


def test_a_huge_hostile_message_is_never_materialised():
    """`__str__` can be enormous as easily as it can be hostile.

    Bounding the *stored* string is not enough — rendering it at all is the
    cost. This exception would produce roughly 20 MB if anything asked.
    """
    from openai4s.server.errors import record_diagnostic

    record = record_diagnostic(HostileFailure(repeat=200_000), surface="canary:huge")
    encoded = json.dumps(record, ensure_ascii=False, default=str).encode("utf-8")

    assert_no_canary(encoded, where="the diagnostic record")
    assert (
        len(encoded) < 4096
    ), f"the record grew with the message: {len(encoded)} bytes"


def test_an_exception_that_refuses_to_render_still_records():
    """A diagnostic that raises while reporting a failure loses both."""
    from openai4s.server.errors import record_diagnostic

    record = record_diagnostic(UnrenderableFailure(), surface="canary:unrenderable")
    assert record["exception"] == "UnrenderableFailure"
    assert record["event"] == "unhandled_exception"


def test_the_record_correlates_two_failures_of_the_same_kind():
    """Losing the message costs the operator something real, so what replaces
    it has to be worth having: two occurrences of the same failure at the same
    surface must be recognisable as the same failure."""
    from openai4s.server.errors import record_diagnostic

    first = record_diagnostic(HostileFailure(), surface="canary:same")
    second = record_diagnostic(HostileFailure(), surface="canary:same")
    other = record_diagnostic(UnrenderableFailure(), surface="canary:same")

    assert first["error_class"] == second["error_class"]
    assert first["error_class"] != other["error_class"]


# --------------------------------------------------------------------------
# D. the archive boundary
# --------------------------------------------------------------------------


def test_a_structured_diagnostic_line_is_sanitised_into_the_archive(cfg, tmp_path):
    """The real shape: a `record_diagnostic` line written to the real file."""
    from openai4s.server.errors import record_diagnostic

    record = record_diagnostic(HostileFailure(), surface="canary:structured")
    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    blob = _bundle(cfg, tmp_path)

    assert_no_canary(blob, where="the archive")
    # The safe metadata is what makes the archive worth collecting.
    assert b"unhandled_exception" in blob
    assert b"canary:structured" in blob
    assert b"HostileFailure" in blob


def test_an_ordinary_field_holding_a_sentence_does_not_ride_through(cfg, tmp_path):
    """Field-wise redaction asks "is this whole value a credential".

    A sentence is never opaque, so a `message` field carrying one delivered a
    credential and a token URL intact — the single worst row of the matrix,
    and the one that shows why an allowlist is the only workable rule: the
    field is not called `token`, and it never will be.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps(
            {
                "event": "something_happened",
                "message": (
                    f"{RAW_PHRASE} {FOREIGN_PATH} `{SHELL_COMMAND}` "
                    f"token={CREDENTIAL} {TOKEN_URL}"
                ),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    blob = _bundle(cfg, tmp_path)

    assert_no_canary(blob, where="the archive")
    assert b"something_happened" in blob, "the event name is allowlisted metadata"


def test_a_plain_log_line_is_never_shared_verbatim(cfg, tmp_path):
    """`app.out` is the daemon's whole stdout and stderr — every `print`, every
    `traceback.print_exc`, every library's chatter. There is no pattern set
    that makes arbitrary text safe, so the archive carries what it can count
    and classify instead of what it hopes it can scrub."""
    (cfg.data_dir / "logs" / "app.out").write_text(
        f"{RAW_PHRASE} {FOREIGN_PATH} `{SHELL_COMMAND}` "
        f"token={CREDENTIAL} {TOKEN_URL} {FRAGMENT_URL}\n"
        "Traceback (most recent call last):\n",
        encoding="utf-8",
    )
    blob = _bundle(cfg, tmp_path)

    assert_no_canary(blob, where="the archive")
    # Still evidence: how much there was, and roughly what it was.
    assert b"2" in blob
    assert b"traceback" in blob.lower()


def test_the_report_is_sanitised_like_everything_else(cfg, tmp_path, monkeypatch):
    """`report.json` is assembled in-process, so it reads as trusted — and it
    is built from `environment_report()` and `security_posture()`, both of
    which reach out to the machine. Anything they pick up is free text the
    moment it lands in the archive."""
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "environment_report",
        lambda: {"nested": {"free": f"{RAW_PHRASE} {FOREIGN_PATH} `{SHELL_COMMAND}`"}},
    )
    blob = _bundle(cfg, tmp_path)
    assert_no_canary(blob, where="report.json")


def test_the_report_has_no_stringify_anything_escape_hatch(cfg, tmp_path, monkeypatch):
    """`json.dumps(..., default=str)` silently renders any object the encoder
    does not understand, which is the same "call str() and hope" the record
    itself just stopped doing."""
    import openai4s.diagnostics as diagnostics

    class _Sneaky:
        def __repr__(self) -> str:
            return f"{RAW_PHRASE} {FOREIGN_PATH}"

        __str__ = __repr__

    monkeypatch.setattr(
        diagnostics, "environment_report", lambda: {"object": _Sneaky()}
    )
    blob = _bundle(cfg, tmp_path)
    assert_no_canary(blob, where="report.json")


# --------------------------------------------------------------------------
# C. security_posture
# --------------------------------------------------------------------------


def test_a_posture_probe_that_throws_reports_a_type_not_a_message(cfg, tmp_path):
    """Two `except` clauses returned `str(e)` into the posture dict, which goes
    into `report.json` — so a permission or schema probe that failed put its
    own exception text into the archive, credential and all."""
    from openai4s.diagnostics import security_posture

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError(
                f"{RAW_PHRASE} {FOREIGN_PATH} `{SHELL_COMMAND}` (token {CREDENTIAL})"
            )

    posture = security_posture(_Boom())
    blob = json.dumps(posture, ensure_ascii=False, default=str).encode("utf-8")

    assert_no_canary(blob, where="security_posture")
    # It still says a probe failed, and what kind of failure it was.
    assert b"RuntimeError" in blob


# --------------------------------------------------------------------------
# E. the URL sanitizer's blind spot
# --------------------------------------------------------------------------


def test_a_credential_in_a_url_fragment_is_redacted():
    """The implicit-flow shape. A fragment never reaches a server, which is
    exactly why credentials are put there — and why one appearing in a local
    log is a credential someone's browser handed to this machine."""
    from openai4s.observability import redact_url

    cleaned = redact_url(FRAGMENT_URL)
    assert FRAGMENT_TOKEN not in cleaned, cleaned
    assert "idp.example.org" in cleaned


# --------------------------------------------------------------------------
# B. the agent's observation
# --------------------------------------------------------------------------


def test_the_env_switch_notice_carries_no_arbitrary_text():
    """This one does not even reach the archive to be a problem: the notice is
    appended to the model's history, which goes to the provider on the next
    turn and into the exported session package."""
    from openai4s.server.agent_run import _env_switch_notice

    notice = _env_switch_notice(HostileFailure())
    assert_no_canary(notice.encode("utf-8"), where="the agent observation")
    # The agent still learns enough to choose a different move.
    assert "HostileFailure" in notice
    assert "environment" in notice
