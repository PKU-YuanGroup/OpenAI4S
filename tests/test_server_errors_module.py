"""The HTTP failure type has to be importable without importing the gateway.

This looks like tidying and is not. `Handler._api` is one method of ~2100 lines,
and carving route groups out of it is the agreed direction. Every route module
that comes out of it needs to raise `GatewayError` -- and `GatewayError` was
defined around line 5870 of gateway.py, roughly 5,800 lines below that file's
own import block. A sibling doing the obvious
`from openai4s.server.gateway import GatewayError` at module scope therefore
hit a circular import, and the daemon failed at *boot*, not at request time.

So this is a prerequisite, not a cleanup: without it the first extraction
discovers the cycle, and so does every extraction after it.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from openai4s.server import gateway as gateway_mod
from openai4s.server.errors import ERROR_CODES, GatewayError, error_code_for


def test_a_sibling_module_can_import_the_error_without_a_cycle():
    """The whole point. Run in a fresh interpreter: importing `errors` first and
    `gateway` second must work, which is the order a route module forces."""
    program = textwrap.dedent(
        """
        from openai4s.server.errors import GatewayError
        import openai4s.server.gateway as gateway
        assert gateway.GatewayError is GatewayError
        print("ok")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_the_gateway_still_exposes_the_name_it_always_did():
    """Ten test modules and the daemon import it from gateway. A move that
    renames the public path is a break dressed as a refactor."""
    assert gateway_mod.GatewayError is GatewayError
    assert gateway_mod._error_code_for is error_code_for
    assert gateway_mod._ERROR_CODES is ERROR_CODES


def test_the_error_still_carries_status_message_and_optional_code():
    plain = GatewayError(404, "session not found")
    assert (plain.code, plain.message, plain.error_code) == (
        404,
        "session not found",
        None,
    )
    assert str(plain) == "session not found"

    specific = GatewayError(400, "bad cursor", "invalid_cursor")
    assert specific.error_code == "invalid_cursor"


@pytest.mark.parametrize(
    "status,expected",
    [
        (400, "bad_request"),
        (423, "locked"),
        (429, "rate_limited"),
        (503, "unavailable"),
    ],
)
def test_known_statuses_keep_their_stable_code(status, expected):
    assert error_code_for(status) == expected


def test_an_unmapped_status_degrades_by_class_not_to_a_single_default():
    """A client retrying `internal_error` the way it retries a 4xx is the bug
    the split exists to prevent."""
    assert error_code_for(418) == "error"
    assert error_code_for(507) == "internal_error"
