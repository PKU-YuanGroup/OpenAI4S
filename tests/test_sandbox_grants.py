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
APP = "http://127.0.0.1:8760"
SANDBOX = "http://localhost:8760"


def mint(secret, frame, **kwargs):
    return grants.mint(
        secret,
        frame,
        app_origin=APP,
        artifact_id="a-1",
        version_id="v-1",
        **kwargs,
    )


def verify(secret, token, **kwargs):
    return grants.verify(secret, token, origin=SANDBOX, **kwargs).frame_id


def test_a_grant_round_trips_the_frame_it_names():
    token = mint(SECRET, "f-123")

    assert verify(SECRET, token) == "f-123"


def test_a_frame_id_survives_characters_that_would_break_the_path():
    """The frame id is base64'd into the token, not interpolated raw.

    A `/` in an id would otherwise split the path segment the grant occupies
    and silently truncate the scope.
    """
    token = mint(SECRET, "a/b.c?d")

    assert verify(SECRET, token) == "a/b.c?d"
    assert "/" not in token


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda t: t[:-1] + ("x" if t[-1] != "x" else "y"), id="signature"),
        pytest.param(
            lambda t: t.split(".")[0] + ".9999999999." + t.split(".")[-1], id="expiry"
        ),
        pytest.param(lambda t: "Zg" + t[2:], id="frame"),
        pytest.param(lambda t: "a.b.c", id="shape"),
        pytest.param(lambda t: "", id="empty"),
    ],
)
def test_every_tampered_grant_is_refused(mangle):
    token = mangle(mint(SECRET, "f-123"))

    with pytest.raises(grants.GrantError):
        verify(SECRET, token)


def test_a_grant_minted_by_another_daemon_is_refused():
    """The signing key is the daemon's own access token, so a grant does not
    survive the credential that authorised it being replaced."""
    token = mint("a-different-token", "f-123")

    with pytest.raises(grants.GrantError):
        verify(SECRET, token)


def test_a_grant_expires():
    token = mint(SECRET, "f-123", ttl_seconds=10, now=1000)

    assert verify(SECRET, token, now=1005) == "f-123"
    with pytest.raises(grants.GrantError):
        verify(SECRET, token, now=1010)


def test_without_a_secret_nothing_mints_and_nothing_verifies():
    """The posture where the daemon has no access token: no grant exists, so
    the client keeps the inert preview rather than getting an unsigned one."""
    with pytest.raises(grants.GrantError):
        mint("", "f-123")
    with pytest.raises(grants.GrantError):
        verify("", mint(SECRET, "f-123"))


def test_the_token_leads_the_path_so_relative_links_carry_it():
    """The reason the grant is a path segment and not a query or a cookie.

    A report's own `<img src="figure.png">` resolves against the document URL,
    so with the token first the sibling request carries the grant with no
    cookie on the sandbox origin at all -- which is what keeps that origin
    credential-free.
    """
    token = mint(SECRET, "f-123")
    path = grants.grant_path(token, "a-1")

    assert path.startswith(f"{grants.SANDBOX_PREFIX}{token}/preview/")
    sibling = path.rsplit("/", 1)[0] + "/figure.png"
    assert grants.split_path(sibling) == (token, "/preview/figure.png")


def test_an_identifier_that_would_escape_the_segment_is_quoted():
    token = mint(SECRET, "f-123")

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


@pytest.mark.parametrize(
    "origin",
    [APP, "http://localhost:8761", "https://localhost:8760", "http://evil.test:8760"],
)
def test_grant_cannot_be_spent_on_another_origin(origin):
    with pytest.raises(grants.GrantError):
        grants.verify(SECRET, mint(SECRET, "f-123"), origin=origin)


@pytest.mark.parametrize("frame", ["", " ", None])
def test_empty_scope_cannot_be_minted(frame):
    with pytest.raises(grants.GrantError):
        mint(SECRET, frame)


@pytest.mark.parametrize(
    "host",
    [
        "",
        "evil.test:8760",
        "localhost:8761",
        "localhost:8760/x",
        "user@localhost:8760",
        "localhost:8760#x",
        "localhost.:8760",
        "[::1]:8760",
        "[",
    ],
)
def test_origin_pair_refuses_ambiguous_or_unsupported_authorities(host):
    with pytest.raises(grants.GrantError):
        grants.origin_pair(host, 8760)


def test_origin_pair_supports_both_directions_and_default_port():
    assert grants.origin_pair("localhost:8760", 8760) == (SANDBOX, APP)
    assert grants.origin_pair("127.0.0.1:8760", 8760) == (APP, SANDBOX)
    assert grants.origin_pair("localhost", 80) == (
        "http://localhost",
        "http://127.0.0.1",
    )
    assert grants.origin_pair("localhost:80", 80) == grants.origin_pair("localhost", 80)


def test_origin_pair_agrees_with_the_rebind_allowlist_on_case():
    """Two parsers of one header must not disagree on what a browser sends.

    The gateway's DNS-rebind allowlist lowercases the Host before comparing;
    a second parser that refused `LOCALHOST:8760` would let a request past the
    first gate and then silently degrade the preview to inert.
    """
    assert grants.origin_pair("LOCALHOST:8760", 8760) == (SANDBOX, APP)
    assert grants.origin_pair(" 127.0.0.1:8760 ", 8760) == (APP, SANDBOX)


def test_grant_pins_primary_version_and_mint_origin():
    scope = grants.verify(SECRET, mint(SECRET, "f-123"), origin=SANDBOX)
    assert (scope.frame_id, scope.artifact_id, scope.version_id) == (
        "f-123",
        "a-1",
        "v-1",
    )
    assert (scope.app_origin, scope.sandbox_origin) == (APP, SANDBOX)


@pytest.mark.parametrize(
    "app_origin",
    [
        "https://127.0.0.1:8760",
        "http://evil.test:8760",
        "http://127.0.0.1:8760/",
        "http://LOCALHOST:8760",
        "http://[::1]:8760",
        "http://localhost:99999",
        "127.0.0.1:8760",
        "",
    ],
)
def test_mint_rejects_anything_but_an_exact_loopback_app_origin(app_origin):
    """The spend origin is derived, never supplied, so only the minting side
    can be wrong -- and it is refused unless it is exactly one loopback
    origin in the form `origin_pair` itself produces."""
    with pytest.raises(grants.GrantError):
        grants.mint(
            SECRET,
            "f-123",
            app_origin=app_origin,
            artifact_id="a-1",
            version_id="v-1",
        )
