"""The capability grant that lets an artifact preview leave the app origin.

This is the only credential in the product that is deliberately spendable by a
document nobody trusts, so the tests are written as refusals first: what a
grant *cannot* do is the property that makes running model-authored script
acceptable at all.
"""

from __future__ import annotations

import pytest

from openai4s.server import sandbox_grants as grants

SECRET = "daemon-access-token"


def test_a_grant_round_trips_the_frame_it_names():
    token = grants.mint(SECRET, "f-123")

    assert grants.verify(SECRET, token) == "f-123"


def test_a_frame_id_survives_characters_that_would_break_the_path():
    """The frame id is base64'd into the token, not interpolated raw.

    A `/` in an id would otherwise split the path segment the grant occupies
    and silently truncate the scope.
    """
    token = grants.mint(SECRET, "a/b.c?d")

    assert grants.verify(SECRET, token) == "a/b.c?d"
    assert "/" not in token


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda t: t[:-1] + ("x" if t[-1] != "x" else "y"), id="signature"),
        pytest.param(
            lambda t: t.split(".")[0] + ".9999999999." + t.split(".")[2], id="expiry"
        ),
        pytest.param(lambda t: "Zg" + t[2:], id="frame"),
        pytest.param(lambda t: "a.b.c", id="shape"),
        pytest.param(lambda t: "", id="empty"),
    ],
)
def test_every_tampered_grant_is_refused(mangle):
    token = mangle(grants.mint(SECRET, "f-123"))

    with pytest.raises(grants.GrantError):
        grants.verify(SECRET, token)


def test_a_grant_minted_by_another_daemon_is_refused():
    """The signing key is the daemon's own access token, so a grant does not
    survive the credential that authorised it being replaced."""
    token = grants.mint("a-different-token", "f-123")

    with pytest.raises(grants.GrantError):
        grants.verify(SECRET, token)


def test_a_grant_expires():
    token = grants.mint(SECRET, "f-123", ttl_seconds=10, now=1000)

    assert grants.verify(SECRET, token, now=1005) == "f-123"
    with pytest.raises(grants.GrantError):
        grants.verify(SECRET, token, now=1010)


def test_without_a_secret_nothing_mints_and_nothing_verifies():
    """The posture where the daemon has no access token: no grant exists, so
    the client keeps the inert preview rather than getting an unsigned one."""
    with pytest.raises(grants.GrantError):
        grants.mint("", "f-123")
    with pytest.raises(grants.GrantError):
        grants.verify("", grants.mint(SECRET, "f-123"))


def test_the_token_leads_the_path_so_relative_links_carry_it():
    """The reason the grant is a path segment and not a query or a cookie.

    A report's own `<img src="figure.png">` resolves against the document URL,
    so with the token first the sibling request carries the grant with no
    cookie on the sandbox origin at all -- which is what keeps that origin
    credential-free.
    """
    token = grants.mint(SECRET, "f-123")
    path = grants.grant_path(token, "a-1")

    assert path.startswith(f"{grants.SANDBOX_PREFIX}{token}/preview/")
    sibling = path.rsplit("/", 1)[0] + "/figure.png"
    assert grants.split_path(sibling) == (token, "/preview/figure.png")


def test_an_identifier_that_would_escape_the_segment_is_quoted():
    token = grants.mint(SECRET, "f-123")

    path = grants.grant_path(token, "../../etc/passwd")

    assert "../" not in path
    assert grants.split_path(path)[1] == "/preview/..%2F..%2Fetc%2Fpasswd"


@pytest.mark.parametrize(
    "path",
    ["/preview/a-1", "/sandbox/", "/sandbox/only-a-token", "", "/sandboxed/x/y"],
)
def test_a_path_without_a_grant_segment_is_refused(path):
    with pytest.raises(grants.GrantError):
        grants.split_path(path)
