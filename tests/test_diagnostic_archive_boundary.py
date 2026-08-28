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
from urllib.parse import urlsplit

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
    # The *category*, from a set this repository writes down. `HostileFailure`
    # is a name its author chose, and a name is not metadata just because it
    # parses as an identifier.
    assert record["exception"] == "RuntimeError"
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
    assert record["exception"] == "RuntimeError"
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
    # The safe metadata is what makes the archive worth collecting -- and it is
    # only the part drawn from a closed set. `canary:structured` is not a
    # surface this repository names, so it is fingerprinted rather than echoed;
    # a real one (`cell:attempt`) travels, and that is asserted separately.
    assert b"unhandled_exception" in blob
    assert b"canary:structured" not in blob
    assert b"<redacted:" in blob


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
    # Not even the event name: `something_happened` is not one this repository
    # emits, and "it looks like an event name" is a shape, not a source.
    assert b"something_happened" not in blob


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
    parsed = urlsplit(cleaned)
    assert parsed.scheme == "https"
    assert parsed.hostname == "idp.example.org"


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
    # The agent still learns enough to choose a different move: the family the
    # failure belongs to, which is what distinguishes "no such environment"
    # from "the kernel would not start".
    assert "RuntimeError" in notice
    assert "HostileFailure" not in notice
    assert "environment" in notice


def _bundle_parts(cfg, tmp_path, name="b.zip"):
    """Member names and member bytes, because a leak can be either."""
    target = tmp_path / name
    build_bundle(cfg, target)
    with zipfile.ZipFile(target) as archive:
        names = list(archive.namelist())
        blob = b"".join(archive.read(n) for n in names)
    return names, blob


def assert_archive_clean(cfg, tmp_path, *, where: str, name="b.zip") -> bytes:
    names, blob = _bundle_parts(cfg, tmp_path, name)
    joined = " ".join(names).encode("utf-8")
    for canary in CANARIES:
        assert canary.encode() not in blob, f"{canary!r} survived into {where}"
        assert (
            canary.encode() not in joined
        ), f"{canary!r} survived into the ZIP member names: {names}"
    return blob


# --- 1. every allowlisted field, challenged on its own ---------------------


@pytest.mark.parametrize(
    "field",
    ["detail", "surface", "status", "event", "error_type", "exception", "level"],
)
@pytest.mark.parametrize(
    "payload",
    [RAW_PHRASE, FOREIGN_PATH, "rsync -av --delete /srv/raw backup", CREDENTIAL],
)
def test_an_allowlisted_field_does_not_carry_arbitrary_content(
    cfg, tmp_path, field, payload
):
    """The allowlist constrained *keys* and left values to one shared regex.

    That regex admitted spaces, `/` and `.`, so plain English went through in
    `detail`, an absolute path went through in `surface`, and an unquoted
    command went through in `status`. The earlier test only ever put the
    payload in an *unknown* field, so it proved the unknown-field rule and
    nothing about the fields that are allowed.

    Each field needs its own validator, because what is legitimate differs per
    field: `detail` is one fixed sentence, `surface` is a colon-joined
    identifier, `level` is an enum. "Short enough" is not a property that makes
    any of them safe.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps({"event": "unhandled_exception", field: payload}) + "\n",
        encoding="utf-8",
    )
    assert_archive_clean(cfg, tmp_path, where=f"the {field} field")


def test_the_allowlisted_fields_still_carry_their_real_values(cfg, tmp_path):
    """Deny-by-default is only worth having if the safe values survive."""
    from openai4s.server.errors import DIAGNOSTIC_DETAIL

    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps(
            {
                "event": "unhandled_exception",
                "surface": "cell:attempt",
                "exception": "PermissionError",
                "detail": DIAGNOSTIC_DETAIL,
                "error_class": "a1b2c3d4e5f6",
                "request_id": "req-abc123",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    from openai4s.observability import fingerprint

    _names, blob = _bundle_parts(cfg, tmp_path)
    # Closed-set values travel as themselves...
    for kept in (b"cell:attempt", b"PermissionError"):
        assert kept in blob, kept
    # ...and `error_class` does not, because a fingerprint-shaped field read
    # out of a file is not evidence that we produced it. It is re-fingerprinted
    # like every other variable id, which keeps correlation stable.
    assert b"a1b2c3d4e5f6" not in blob
    assert fingerprint("a1b2c3d4e5f6").encode() in blob
    # ...and a variable id travels as its fingerprint, which is what a support
    # ticket is matched on.
    assert b"req-abc123" not in blob
    assert fingerprint("req-abc123").encode() in blob


# --- 2. report.json, one canary per leaf -----------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        RAW_PHRASE,
        FOREIGN_PATH,
        "rsync -av --delete /srv/raw backup",
        CREDENTIAL,
        TOKEN,
    ],
)
def test_a_report_leaf_carries_no_arbitrary_value(cfg, tmp_path, monkeypatch, payload):
    """Each canary on its own key, and none of them quoted.

    The earlier test concatenated all three into one string *with backticks* --
    and a backtick is not in the shared value pattern, so the whole field was
    omitted for the wrong reason. Split into separate leaves, and without the
    punctuation, every one of them rode through.
    """
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "environment_report", lambda: {"probe": payload})
    assert_archive_clean(cfg, tmp_path, where="report.json")


def test_a_hostile_mapping_key_is_never_rendered(cfg, tmp_path, monkeypatch):
    """`archive_safe` did `str(key)[:80]`, which is the same call-str-and-hope
    the record itself stopped doing -- on an object whose `__str__` the report
    does not own."""
    import openai4s.diagnostics as diagnostics

    class _Key:
        def __str__(self) -> str:
            return FOREIGN_PATH

        __repr__ = __str__

        def __hash__(self) -> int:
            return 1

    monkeypatch.setattr(diagnostics, "environment_report", lambda: {_Key(): 1})
    assert_archive_clean(cfg, tmp_path, where="report.json keys")


def test_the_report_still_carries_the_posture_it_exists_for(cfg, tmp_path):
    """The whole point of the report is the posture; a schema that drops it
    would be safe and useless."""
    _names, blob = _bundle_parts(cfg, tmp_path)
    for kept in (b"platform", b"data_dir_owner_only", b"kernel_sandbox"):
        assert kept in blob, kept


# --- 3. a credential-shaped file name --------------------------------------


def test_a_credential_shaped_log_name_reaches_neither_member_nor_manifest(
    cfg, tmp_path
):
    """A file name is attacker-influenced the moment anything writes a log
    named after a token, and it lands in two places the content scrubbers never
    look: the ZIP member name and the MANIFEST that lists it."""
    (cfg.data_dir / "logs" / f"{CREDENTIAL}.log").write_text("x\n", encoding="utf-8")
    names, blob = _bundle_parts(cfg, tmp_path)

    assert not any(CREDENTIAL in n for n in names), names
    assert CREDENTIAL.encode() not in blob
    # ...and the log is still collected, under a name the archive chose.
    assert any(n.startswith("logs/log-") for n in names), names


# --- 4. an exception type is not a safe string ------------------------------


def test_a_dynamic_exception_type_name_is_not_trusted():
    """`type(exc).__name__` reads like metadata and is not: a type created at
    runtime carries whatever its creator put in the name, and it reaches the
    record, the agent's observation and the posture report."""
    from openai4s.server.agent_run import _env_switch_notice
    from openai4s.server.errors import record_diagnostic

    Dynamic = type(f"E_{FOREIGN_PATH}", (RuntimeError,), {})

    record = record_diagnostic(Dynamic(), surface="canary:dynamic")
    assert FOREIGN_PATH not in json.dumps(record, default=str)
    assert record["exception"] == "RuntimeError"

    notice = _env_switch_notice(Dynamic())
    assert FOREIGN_PATH not in notice

    # A real, ordinary type is still named.
    assert record_diagnostic(ValueError(), surface="canary:ok")["exception"] == (
        "ValueError"
    )


# --- 5. the fragment, whatever the parameter is called ----------------------


@pytest.mark.parametrize(
    "param", ["access_token", "code", "state", "ticket", "session", "anything"]
)
def test_every_fragment_value_is_masked(param):
    """A denylist of parameter names is the rule that fails on the next name.

    `#code=` and `#state=` are an authorization code and a CSRF token; a
    fragment is where a browser puts things it does not want sent to a server,
    so a value there is a credential by position rather than by name.
    """
    from openai4s.observability import redact_url

    cleaned = redact_url(f"https://idp.example.org/cb#{param}={CREDENTIAL}")
    assert CREDENTIAL not in cleaned, cleaned
    assert param in cleaned, "the parameter name is provenance"


# --- 6. the real logger, end to end ----------------------------------------


def test_the_real_structured_logger_reaches_the_archive_safely(
    cfg, tmp_path, monkeypatch, capsys
):
    """Driven through `log_event` writing to stderr, captured, and written to
    `app.out` -- the chain the daemon actually has, rather than a hand-rolled
    `json.dumps` that happens to produce the shape this code expects."""
    monkeypatch.setenv("OPENAI4S_STRUCTURED_LOGS", "1")
    from openai4s.server.errors import record_diagnostic

    record_diagnostic(HostileFailure(), surface="cell:attempt", request_id="req-real")
    emitted = capsys.readouterr().err.strip()
    assert emitted.startswith("{"), emitted[:200]

    (cfg.data_dir / "logs" / "app.out").write_text(emitted + "\n", encoding="utf-8")
    from openai4s.observability import fingerprint

    blob = assert_archive_clean(cfg, tmp_path, where="the real logger chain")
    assert b"cell:attempt" in blob
    assert b"req-real" not in blob
    assert fingerprint("req-real").encode() in blob


def test_the_local_log_is_richer_than_the_archive(cfg, tmp_path):
    """The narrowing is the *archive's*, not the log's.

    An operator reading `app.out` on the machine still sees the lines a
    dependency printed. Only what leaves is reduced -- and if these two ever
    became the same thing, the fix would have been to make the local log
    useless rather than to make the archive safe.
    """
    local = cfg.data_dir / "logs" / "app.out"
    local.write_text(
        "Traceback (most recent call last):\n"
        '  File "/srv/app/run.py", line 3, in <module>\n'
        "ValueError: something specific went wrong\n",
        encoding="utf-8",
    )
    on_disk = local.read_text(encoding="utf-8")
    assert "something specific went wrong" in on_disk
    assert "/srv/app/run.py" in on_disk

    _names, blob = _bundle_parts(cfg, tmp_path)
    assert b"something specific went wrong" not in blob
    assert b"/srv/app/run.py" not in blob
    assert b"traceback" in blob.lower()


def test_a_field_validator_admits_only_what_the_repository_named():
    """Asserted on the validators directly, not through the whole stack.

    Every one of these canaries satisfies the pattern that used to guard the
    field. `PRIVATE_COHORT_ALPHA_SEVEN` is a legal identifier; `HEX16` is the
    exact shape of a server-generated correlation id; `privatecohortalphaseven`
    is a fine bounded-lowercase architecture. Only membership separates them
    from the real values, so only membership is checked.
    """
    from openai4s.diagnostics import (
        _ARCHITECTURES,
        _v_enum,
        _v_identity,
        _v_type_name,
        _v_version,
    )

    assert _v_type_name("PermissionError") == "PermissionError"
    assert _v_type_name(PRIVATE_IDENT) is None
    assert _v_type_name(CREDENTIAL) is None

    machine = _v_enum(_ARCHITECTURES)
    assert machine("arm64") == "arm64"
    assert machine(PRIVATE_LOWER) is None

    assert _v_version("3.12.13") == "3.12.13"
    # Parsing stops at the first non-numeric component rather than salvaging
    # what follows it, so the kernel flavour never travels.
    assert _v_version("6.5.0-15-generic") == "6.5"
    # The numeric prefix survives and the phrase does not: reduction keeps what
    # a version *means* and discards everything a caller could have put there.
    # The ZIP-level canary above asserts the phrase is absent from the archive;
    # this asserts the rule that makes it so.
    assert _v_version(PRIVATE_VERSIONISH) == "3"
    assert PRIVATE_LOWER not in (_v_version(PRIVATE_VERSIONISH) or "")
    assert _v_version(HEX32) is None
    assert _v_version(PRIVATE_LOWER) is None

    # An id is never kept and never dropped: it becomes its fingerprint.
    assert (
        _v_identity(HEX16)
        == f"<redacted:{__import__('openai4s.observability', fromlist=['fingerprint']).fingerprint(HEX16)}>"
    )


def test_a_mapping_key_that_refuses_to_render_does_not_break_the_bundle(
    cfg, tmp_path, monkeypatch
):
    """The guard is `isinstance(key, str)` — it declines to *call* `str()`.

    An unknown key is dropped by the schema either way, so stringifying it
    first leaks nothing. What it does do is hand an untrusted object control of
    the report: a `__str__` that raises takes the whole bundle down with it,
    and a `__str__` that returns 50 MB makes the archive that size.
    """
    import openai4s.diagnostics as diagnostics

    class _Explosive:
        def __str__(self) -> str:
            raise RuntimeError("this key refuses to render")

        __repr__ = __str__

        def __hash__(self) -> int:
            return 7

    monkeypatch.setattr(
        diagnostics, "environment_report", lambda: {_Explosive(): 1, "python": "3.12.0"}
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    # The bundle is still produced, and still carries the key beside it.
    assert b"3.12.0" in blob


# passes it and a rule that checks membership does not.

# A legal Python identifier, no digits, so it satisfies every identifier and
# token pattern and never reads as opaque. The earlier dynamic-type test used
# `E_/srv/...`, which is not valid syntax, so it only ever proved that the
# regex rejects malformed names.
PRIVATE_IDENT = "PRIVATE_COHORT_ALPHA_SEVEN"
PRIVATE_LOWER = "privatecohortalphaseven"
PRIVATE_SNAKE = "private_cohort_alpha"
PRIVATE_VERSIONISH = "3.privatecohortalpha"
HEX16 = "a1b2c3d4e5f60718"
HEX32 = "a1b2c3d4e5f60718a1b2c3d4e5f60718"


# --- the exception type is a category, not a name --------------------------


def test_a_private_class_name_is_reported_as_its_base_family():
    """`type('PRIVATE_COHORT_ALPHA_SEVEN', (RuntimeError,), {})`.

    A perfectly legal identifier, so every pattern-based check admits it. The
    only rule that does not is membership of a set this repository writes down,
    so the reported category is the nearest *known* base and the caller's own
    name never appears. An operator loses nothing that identifies the failure:
    the family is what tells them what kind of thing broke, and `error_class`
    still ties two occurrences together.
    """
    from openai4s.server.errors import safe_type_name

    assert safe_type_name(type(PRIVATE_IDENT, (RuntimeError,), {})()) == "RuntimeError"
    assert safe_type_name(type(PRIVATE_IDENT, (OSError,), {})()) == "OSError"
    # An ordinary exception is still named exactly.
    assert safe_type_name(ValueError()) == "ValueError"
    # Something with no recognisable ancestry at all is `unknown`.
    assert safe_type_name(type(PRIVATE_IDENT, (BaseException,), {})()) in (
        "BaseException",
        "unknown",
    )


def test_a_private_class_name_reaches_no_reporting_surface(cfg, tmp_path):
    from openai4s.server.agent_run import _env_switch_notice
    from openai4s.server.errors import record_diagnostic

    Dynamic = type(PRIVATE_IDENT, (RuntimeError,), {})

    record = record_diagnostic(Dynamic(), surface="cell:attempt")
    assert PRIVATE_IDENT not in json.dumps(record, default=str)
    assert record["exception"] == "RuntimeError"

    assert PRIVATE_IDENT not in _env_switch_notice(Dynamic())

    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps({"event": "unhandled_exception", "exception": PRIVATE_IDENT}) + "\n",
        encoding="utf-8",
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_IDENT.encode() not in blob


# --- every remaining archive field ------------------------------------------


@pytest.mark.parametrize(
    "field", ["event", "surface", "request_id", "correlation_id", "status", "level"]
)
@pytest.mark.parametrize("payload", [PRIVATE_IDENT, PRIVATE_LOWER, HEX16, HEX32])
def test_a_well_formed_value_still_does_not_pass_on_its_shape(
    cfg, tmp_path, field, payload
):
    """Each of these fields had a pattern, and each pattern admits the canaries.

    `request_id` and `correlation_id` are the sharpest case: they *are*
    server-generated in the daemon, so it is tempting to keep a value that
    matches the server's own 16-hex shape. But the archive reads them out of
    `app.out`, and a structured line there can carry any same-shaped string --
    including a 16- or 32-hex credential. Support does not need the literal: a
    fingerprint of the id the user quotes matches the fingerprint in the
    archive.
    """
    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps({"event": "unhandled_exception", field: payload}) + "\n",
        encoding="utf-8",
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert payload.encode() not in blob, f"{field}={payload!r} passed on its shape"


def test_a_request_id_is_matchable_by_fingerprint(cfg, tmp_path):
    """Fingerprinting is only acceptable if the support workflow survives it."""
    from openai4s.observability import fingerprint

    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps({"event": "unhandled_exception", "request_id": HEX16}) + "\n",
        encoding="utf-8",
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert fingerprint(HEX16).encode() in blob


def test_the_known_vocabulary_still_travels(cfg, tmp_path):
    """Closed sets are the point: the values this repository writes down are
    exactly the ones worth keeping, and they must survive."""
    from openai4s.server.errors import DIAGNOSTIC_DETAIL

    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps(
            {
                "event": "unhandled_exception",
                "surface": "cell:attempt",
                "exception": "PermissionError",
                "status": "unavailable",
                "level": "error",
                "detail": DIAGNOSTIC_DETAIL,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    for kept in (
        b"unhandled_exception",
        b"cell:attempt",
        b"PermissionError",
        b"unavailable",
        b"error",
    ):
        assert kept in blob, kept


# --- the report's own token leaves ------------------------------------------


@pytest.mark.parametrize(
    "key, payload",
    [
        ("machine", PRIVATE_LOWER),
        ("platform", PRIVATE_IDENT),
        ("python", PRIVATE_VERSIONISH),
        ("openai4s", PRIVATE_VERSIONISH),
        ("release", PRIVATE_VERSIONISH),
        ("python", HEX32),
    ],
)
def test_a_report_leaf_does_not_pass_on_its_shape(
    cfg, tmp_path, monkeypatch, key, payload
):
    """`machine` bounded-lowercase, a numeric-leading version, a snake_case
    migration name -- all three were patterns, and each canary is built to
    satisfy exactly the pattern aimed at it."""
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(diagnostics, "environment_report", lambda: {key: payload})
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert payload.encode() not in blob, f"{key}={payload!r} passed on its shape"


def test_a_migration_name_does_not_travel(cfg, tmp_path, monkeypatch):
    """A migration name is variable text from a table this archive does not own.

    The version number already says which migrations ran, so the name is
    fingerprinted rather than enumerated -- an enumerated closed set would go
    stale the day someone adds a migration, and go stale *silently*.
    """
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "security_posture",
        lambda cfg_: {
            "schema": {
                "version": 13,
                "applied": [
                    {
                        "version": 1,
                        "name": PRIVATE_SNAKE,
                        "applied_at": 1,
                        "checksum": HEX32,
                    }
                ],
            }
        },
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_SNAKE.encode() not in blob
    assert HEX32.encode() not in blob
    # The facts that are numbers still travel.
    assert b'"version": 13' in blob or b'"version":13' in blob


def test_the_real_environment_report_still_travels(cfg, tmp_path):
    """The closed sets have to actually contain this machine, or the archive is
    safe and empty."""
    import platform as _platform

    _names, blob = _bundle_parts(cfg, tmp_path)
    assert _platform.system().encode() in blob
    assert _platform.machine().encode() in blob
    # A real version keeps its numeric components.
    assert _platform.python_version().split(".")[0].encode() in blob


def test_a_non_string_surface_or_id_is_not_stringified():
    """`str(surface or "unknown")` and `str(request_id or ...)` were coercions.

    A caller that passes an object rather than a string hands its `__str__` to
    a record that outlives the request — the same call-str-and-hope the
    exception message no longer gets. There is nothing to gain by coercing: a
    surface that is not a string is not a surface.
    """
    from openai4s.server.errors import record_diagnostic

    class _NotAString:
        def __str__(self) -> str:
            return f"{RAW_PHRASE} {FOREIGN_PATH}"

        __repr__ = __str__

    record = record_diagnostic(
        ValueError(), surface=_NotAString(), request_id=_NotAString()
    )
    blob = json.dumps(record, ensure_ascii=False, default=str)
    assert RAW_PHRASE not in blob, blob
    assert FOREIGN_PATH not in blob, blob
    assert record["surface"] == "unknown"
    assert record["request_id"] == ""


# --------------------------------------------------------------------------
# a value's *type* is part of the boundary, not just its content
# --------------------------------------------------------------------------

HEX_MIXED = "a1b2c3d4e5f60718"
HEX_ALPHA = "a" * 32
HEX_DIGIT = "1" * 32


@pytest.mark.parametrize("value", [HEX_MIXED, HEX_ALPHA, HEX_DIGIT])
def test_an_error_class_is_re_fingerprinted_rather_than_passed_on_its_shape(
    cfg, tmp_path, value
):
    """`error_class` was the last field still judged by shape: 6-64 hex.

    That is the identical mistake this module removed everywhere else, left
    standing in one place because a fingerprint *looks* like a safe thing. A
    line in `app.out` can put anything of that shape in the field, and two of
    the three canaries below sail through untouched — the third was stopped
    only by a later opacity pass, which is luck rather than a boundary.

    Re-fingerprinting is free: it is stable, so correlation survives, and the
    archive stops depending on the field having been produced by us.
    """
    from openai4s.observability import fingerprint

    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps({"event": "unhandled_exception", "error_class": value}) + "\n",
        encoding="utf-8",
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert value.encode() not in blob, f"{value!r} passed on its shape"
    assert fingerprint(value).encode() in blob


class _Alias(str):
    """Content is the secret; identity claims to be something allowlisted.

    `isinstance(x, str)` is True for a subclass, so the membership test asks
    *this object* whether it equals an allowed value and it answers yes — while
    the JSON encoder writes the real buffer. Every check downstream of an
    `isinstance` gate is consulting the attacker.
    """

    def __new__(cls, real: str, pretend: str) -> "_Alias":
        obj = super().__new__(cls, real)
        obj._pretend = pretend  # type: ignore[attr-defined]
        return obj

    def __hash__(self) -> int:  # type: ignore[override]
        return hash(self._pretend)  # type: ignore[attr-defined]

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        return other == self._pretend  # type: ignore[attr-defined]


class _LowerAlias(str):
    """Passes a lower-cased enum by lying in `lower()`."""

    def lower(self) -> str:  # type: ignore[override]
        return "failed"


class _Exploding(str):
    """Records whether the boundary ever called its methods."""

    called = False

    def __str__(self) -> str:  # type: ignore[override]
        type(self).called = True
        raise RuntimeError("this string refuses to render")

    def encode(self, *args, **kwargs):  # type: ignore[override]
        type(self).called = True
        raise RuntimeError("this string refuses to encode")


def test_a_lying_string_subclass_cannot_impersonate_an_allowed_value(
    cfg, tmp_path, monkeypatch
):
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "environment_report",
        lambda: {"platform": _Alias(PRIVATE_IDENT, "Darwin")},
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_IDENT.encode() not in blob


def test_a_lying_string_subclass_cannot_impersonate_an_allowed_key(
    cfg, tmp_path, monkeypatch
):
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "environment_report",
        lambda: {_Alias(PRIVATE_IDENT, "python"): "3.12.13"},
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_IDENT.encode() not in blob


def test_a_lower_cased_enum_is_not_decided_by_the_value(cfg, tmp_path):
    """`_v_lower_enum` called `value.lower()`, which the value defines."""
    (cfg.data_dir / "logs" / "app.out").write_text(
        json.dumps({"event": "unhandled_exception", "status": "ok"}) + "\n",
        encoding="utf-8",
    )
    from openai4s.diagnostics import _ARCHIVE_STATUSES, _v_lower_enum

    check = _v_lower_enum(_ARCHIVE_STATUSES)
    assert check(_LowerAlias(PRIVATE_IDENT)) is None
    # ...and a real value still passes, as the canonical literal.
    result = check("OK")
    assert result == "ok" and type(result) is str


def test_a_string_that_refuses_to_render_is_never_asked_to(cfg, tmp_path, monkeypatch):
    """The boundary must not call a method the value defines — not `str()`,
    not `encode()`. Asserting the bundle merely survives is not enough: it
    would also survive a `try/except` that called the method first."""
    import openai4s.diagnostics as diagnostics

    _Exploding.called = False
    monkeypatch.setattr(
        diagnostics,
        "environment_report",
        lambda: {"machine": _Exploding(PRIVATE_IDENT), "python": "3.12.13"},
    )
    _names, blob = _bundle_parts(cfg, tmp_path)

    assert PRIVATE_IDENT.encode() not in blob
    assert b"3.12.13" in blob
    assert _Exploding.called is False, "the boundary called a method the value owns"


def test_an_enum_returns_the_repositorys_own_literal(cfg, tmp_path):
    """Returning the *input* is what lets a subclass through even after the
    type check is right: the object travels, and the encoder writes its
    buffer. Returning the source literal makes the class of bug unreachable
    rather than merely caught."""
    from openai4s.diagnostics import _ARCHITECTURES, _SYSTEMS, _v_enum

    machine = _v_enum(_ARCHITECTURES)
    got = machine("arm64")
    assert got == "arm64" and type(got) is str
    assert machine(_Alias(PRIVATE_IDENT, "arm64")) is None
    assert _v_enum(_SYSTEMS)(_Alias(PRIVATE_IDENT, "Darwin")) is None


def test_an_unknown_secret_backend_does_not_travel(cfg, tmp_path, monkeypatch):
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "security_posture",
        lambda cfg_: {"secret_store": {"backend": PRIVATE_LOWER, "secure": True}},
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_LOWER.encode() not in blob
    assert b"true" in blob.lower()


def test_a_migration_row_travels_only_as_fingerprints(cfg, tmp_path, monkeypatch):
    import openai4s.diagnostics as diagnostics
    from openai4s.observability import fingerprint

    monkeypatch.setattr(
        diagnostics,
        "security_posture",
        lambda cfg_: {
            "schema": {
                "version": 13,
                "applied": [
                    {
                        "version": 1,
                        "applied_at": 2,
                        "name": PRIVATE_SNAKE,
                        "checksum": HEX_ALPHA,
                    }
                ],
            }
        },
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_SNAKE.encode() not in blob
    assert HEX_ALPHA.encode() not in blob
    assert fingerprint(PRIVATE_SNAKE).encode() in blob
    assert fingerprint(HEX_ALPHA).encode() in blob


class _AliasNamed(type):
    """A metaclass whose `__name__` is a lying `str` subclass.

    `type(exc).__name__` reads like a fact about the class. It is a value the
    class's author controls, and with a metaclass it need not even be a real
    string — so the category lookup can be answered by the object being asked
    about.
    """

    @property
    def __name__(cls):  # type: ignore[override]
        return _Alias(PRIVATE_IDENT, "ValueError")


def test_a_class_that_lies_about_its_own_name_is_not_believed():
    """The MRO walk is not enough on its own: this class's *own* name claims
    to be an allowed category, so a check that trusts `__name__` returns at the
    first step and never reaches the real base."""
    from openai4s.server.errors import safe_type_name

    Lying = _AliasNamed("Lying", (RuntimeError,), {})
    reported = safe_type_name(Lying())

    assert PRIVATE_IDENT not in reported
    assert reported == "RuntimeError"
    assert type(reported) is str


def test_an_alias_key_would_travel_without_the_exact_type_check(
    cfg, tmp_path, monkeypatch
):
    """The key path has no canonical-return to fall back on.

    A value that slips the type check is still replaced by the repository's own
    literal on the way out. A *key* is not: it is written into the output dict
    as the object it is, and the encoder emits its buffer. So the exact-type
    check is the only thing standing there, and this asserts it directly.
    """
    import openai4s.diagnostics as diagnostics

    monkeypatch.setattr(
        diagnostics,
        "environment_report",
        lambda: {_Alias(PRIVATE_IDENT, "python"): "3.12.13"},
    )
    _names, blob = _bundle_parts(cfg, tmp_path)
    assert PRIVATE_IDENT.encode() not in blob
    # The impersonated key does not appear either -- it was dropped, not
    # silently accepted under the name it claimed.
    assert b'"python"' not in blob
