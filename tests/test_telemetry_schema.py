"""Telemetry may say counts and enumerations. This is what stops it saying more.

The frozen decision calls for "an allowlist ... asserting the outgoing payload
contains no key outside it". A key allowlist does not do that job, and the
attack is one line long:

    {"error_type": "ValueError"}
    {"error_type": "FileNotFoundError: /home/y/unpublished/cohort_2026.csv"}

Same key. The second carries a research subject and a person's home directory,
and this product exists to handle unpublished research data. So the tests below
attack **values**, using shapes taken from what this repository actually
produces rather than invented ones:

  * a kernel cell runs agent-authored code, so `type(e).__name__` is
    user-authored text, not an enumeration;
  * a skill's name comes from its SKILL.md frontmatter verbatim;
  * an environment name is a directory name under a user-configured root;
  * an LLM provider's `error.code` can be a whole sentence echoing the request.

Each of those looks like an enum to someone adding a field in a hurry.
"""
from __future__ import annotations

import pytest

from openai4s.telemetry import schema
from openai4s.telemetry.schema import (
    DOMAIN_KINDS,
    ENVELOPE,
    MAX_COUNT,
    RECORD,
    Bucket,
    Count,
    Enum,
    OpaqueId,
    Version,
    classify_error,
    sanitise_envelope,
    sanitise_record,
)

#: Values a real OpenAI4S install could produce that must never travel. Not
#: hypotheticals: each is the documented output of a real code path.
LEAKY_VALUES = [
    "FileNotFoundError: /Users/y/unpublished/cohort_2026_glioblastoma.csv",
    "/Users/y/Documents/lab/patients.parquet",
    "Cohort4471NonResponderMismatch",
    "Cohort 4471 - glioblastoma responders (Dr. Yan, unpublished)",
    "acme-pharma-cohort-2026",
    "This model's maximum context length is 8192 tokens, however you requested "
    "9001 tokens (8500 in your prompt: 'analyse the 4471 non-responders'...)",
    "sk-proj-abcdef0123456789",
    "SRR1234567",
]


# --------------------------------------------------------------------------
# the domains
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", LEAKY_VALUES)
@pytest.mark.parametrize("field", sorted(RECORD))
def test_no_declared_field_accepts_a_value_a_real_install_could_leak(field, value):
    """The whole point, as a cross-product. Every field, every leak shape."""
    assert sanitise_record({field: value}) == {}


@pytest.mark.parametrize("value", LEAKY_VALUES)
@pytest.mark.parametrize("field", sorted(ENVELOPE))
def test_no_envelope_field_accepts_one_either(field, value):
    assert sanitise_envelope({field: value}) == {}


def test_an_undeclared_field_is_dropped_rather_than_carried():
    """The safe direction: a caller who adds a field gets a field that does not
    travel. The authoring-time gates are what make the omission visible."""
    assert sanitise_record({"event": "tool_call", "detail": "anything"}) == {
        "event": "tool_call"
    }


def test_there_is_no_domain_that_can_hold_free_text():
    """Frozen deliberately. Adding a field that *could* carry prose requires
    adding a domain class -- a diff that reads as a privacy decision instead of
    one more line in a table that reads as routine."""
    kinds = {
        cls.kind
        for cls in vars(schema).values()
        if isinstance(cls, type)
        and issubclass(cls, schema.Domain)
        and cls.kind != "abstract"
    }
    assert kinds == DOMAIN_KINDS
    assert not {"string", "text", "json", "map", "list"} & kinds


def test_every_declared_field_has_a_domain_from_that_frozen_set():
    for name, domain in {**ENVELOPE, **RECORD}.items():
        assert domain.kind in DOMAIN_KINDS, f"{name} uses an unfrozen domain"


# --------------------------------------------------------------------------
# the field that looks most like an enumeration and is not
# --------------------------------------------------------------------------


def test_an_exception_class_from_agent_code_is_reported_as_other():
    """A cell defines its own exception class, so `type(e).__name__` is
    user-authored text. The name is exactly what must not travel, so an
    unrecognised class cannot be passed through and cannot be truncated."""

    class Cohort4471NonResponderMismatch(Exception):
        pass

    assert classify_error(Cohort4471NonResponderMismatch()) == "other"


def test_a_recognised_stdlib_error_still_reports_its_kind():
    assert classify_error(ValueError("anything at all")) == "ValueError"
    assert classify_error(FileNotFoundError("/secret/path")) == "FileNotFoundError"


def test_classifying_never_looks_at_the_message():
    """Two errors of the same class must be indistinguishable afterwards."""
    assert classify_error(ValueError("/home/y/cohort.csv")) == classify_error(
        ValueError("x")
    )


def test_classifying_accepts_a_class_as_well_as_an_instance():
    assert classify_error(TimeoutError) == "TimeoutError"
    assert classify_error(None) == "other"


# --------------------------------------------------------------------------
# counts and durations
# --------------------------------------------------------------------------


def test_a_count_is_clamped_because_an_exact_number_is_a_fingerprint():
    """41,318 cells identifies an install about as well as a name does."""
    assert sanitise_record({"count": 41318}) == {"count": MAX_COUNT}
    assert sanitise_record({"count": 3}) == {"count": 3}


def test_a_negative_or_non_integer_count_is_dropped():
    assert sanitise_record({"count": -1}) == {}
    assert sanitise_record({"count": 1.5}) == {}
    assert sanitise_record({"count": "12"}) == {}


def test_a_boolean_is_not_a_count():
    """`bool` is a subclass of `int`, so the naive check counts True as 1."""
    assert sanitise_record({"count": True}) == {}


def test_a_duration_travels_as_a_bucket_never_as_a_number():
    assert Bucket.of(0.4) == "lt_1s"
    assert Bucket.of(42.0) == "lt_60s"
    assert Bucket.of(99999.0) == "ge_1h"
    assert sanitise_record({"duration_bucket": 42.0}) == {}
    assert sanitise_record({"duration_bucket": "lt_60s"}) == {
        "duration_bucket": "lt_60s"
    }


def test_every_bucket_label_is_reachable():
    """A label nothing can produce is a label that will be misused later."""
    produced = {Bucket.of(s) for s in (0.5, 5, 30, 300, 1800, 7200)}
    assert produced == set(Bucket.LABELS)


# --------------------------------------------------------------------------
# identity and version
# --------------------------------------------------------------------------


def test_the_install_id_must_be_exactly_thirty_two_hex_characters():
    assert OpaqueId().accepts("0123456789abcdef0123456789abcdef")
    assert not OpaqueId().accepts("0123456789abcdef")
    assert not OpaqueId().accepts("Z123456789abcdef0123456789abcdef")
    assert not OpaqueId().accepts("/Users/y/cohort.csv")


def test_the_version_field_carries_this_package_and_nothing_else():
    """Not "a version-shaped string": accepting any `1.2.3` would let a caller
    pass a dependency's version, or a number derived from user data."""
    from openai4s import __version__

    assert Version().accepts(__version__)
    assert not Version().accepts("9.9.9")


def test_an_enum_domain_holds_literal_members_only():
    """The members are written in the source. Learning them from observed data
    or from a provider's response is how an enumeration becomes a channel."""
    tiny = Enum("a", "b")
    assert tiny.accepts("a")
    assert not tiny.accepts("c")
    assert isinstance(tiny.members, frozenset)


# --------------------------------------------------------------------------
# the gate that must not be reused
# --------------------------------------------------------------------------


def test_telemetry_does_not_reach_for_the_credential_redactor():
    """`observability.redact` is calibrated for credentials and exempts
    anything starting with `/` on purpose, with a test pinning that. Pointed at
    research data it passes an absolute path through untouched and redacts a
    harmless environment name -- protection-shaped, and worse than none."""
    import ast
    import pathlib

    package = pathlib.Path(schema.__file__).parent
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and "observability" in (
                node.module or ""
            ):
                pytest.fail(f"{path.name} imports the credential redactor")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "observability" not in alias.name, path.name


def test_the_declared_surface_is_small_enough_to_read():
    """A table nobody reads is a table nobody checks. If this assertion starts
    failing, the question is whether the new fields earned their risk."""
    assert len(RECORD) <= 12
    assert len(ENVELOPE) <= 8
