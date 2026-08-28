"""How the access token is allowed to travel, driven through the real Handler.

Four claims, each of which was either wrong or unproven before this file:

1. The `?token=` bootstrap is for the ROOT PAGE ONLY. The gate used to allow it
   on anything that was not `/api/v1/` or `/static/` -- a subtraction whose own
   comment called it "paths that serve the SPA shell". `/preview/<id>` is not
   the SPA shell; it streams artifact bytes. So `/preview/<id>?token=...` was a
   link that set a durable cookie and then delivered the file to whoever held
   it, which is exactly the leak the restriction existed to prevent.
2. A `token` in the query string of a MUTATING request is refused outright --
   not merely "not used to authenticate". It used to be ignored whenever the
   request also carried a valid cookie, so a caller shipping a secret in a URL
   got a 200 and no way to discover it.
3. The 303 that ends the bootstrap carries no credential in its Location, and
   the cookie it sets is `HttpOnly` and `SameSite=Strict`.
4. `/health` stays open, and a header credential still authenticates -- the
   things the tightening must not break.

Everything here goes through `Handler._route`, not a reimplementation of it: a
gate asserted from a helper function can be correct while the request path that
reaches HTTP is not (see MEMORY: "direct-call tests miss route status").
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

COOKIE_NAME = "os_token"


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


class _Response:
    """What the Handler actually put on the wire, per request."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: list[tuple[str, str]] = []
        self.body: object = None
        self.api_calls: list[tuple[str, str]] = []
        self.last_status: int | None = None

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None


@pytest.fixture
def gate(tmp_path, monkeypatch):
    """A real Handler class with the token gate armed, plus its token."""
    monkeypatch.setenv("OPENAI4S_REQUIRE_TOKEN", "1")
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(cfg.data_dir)
    assert token, "no token minted; every assertion below would be vacuous"

    def request(method: str, path: str, headers: dict | None = None) -> _Response:
        out = _Response()
        handler = object.__new__(handler_cls)
        handler._correlation_id = "req-test"
        handler.command = method
        handler.path = path
        handler.close_connection = False
        handler.headers = {"Content-Length": "0", **(headers or {})}
        handler._json = lambda obj, code=200: (
            setattr(out, "status", code),
            setattr(out, "body", obj),
        )
        handler._send = lambda code, body, ctype, extra=None, security=None: (
            setattr(out, "status", code),
            setattr(out, "body", body),
        )
        handler.send_response = lambda code: setattr(out, "status", code)
        handler.send_header = lambda key, value: out.headers.append((key, value))
        handler.end_headers = lambda: None
        handler._prepare_request_body = lambda *a, **k: None
        # Records whether the request reached a route at all. A 401 that is
        # asserted only on the status could coexist with the handler having
        # already run the mutation.
        handler._api = lambda m, sub: out.api_calls.append((m, sub))
        handler._route(method)
        out.last_status = getattr(handler, "_last_status", None)
        return out

    try:
        yield request, token
    finally:
        runner.close()


# --------------------------------------------------------------------------
# 1. the bootstrap is the root page only
# --------------------------------------------------------------------------


def test_the_root_page_bootstraps(gate):
    request, token = gate
    response = request("GET", f"/?token={token}")
    assert response.status == 303
    assert (response.header("Set-Cookie") or "").startswith(f"{COOKIE_NAME}={token}")


def test_index_html_bootstraps_like_the_root(gate):
    """`_serve_static` treats "/" and "/index.html" as the same page; the gate
    has to agree or the two spellings of one URL behave differently."""
    request, token = gate
    assert request("GET", f"/index.html?token={token}").status == 303


def test_a_data_path_cannot_be_bootstrapped(gate):
    """The defect. `/preview/<id>` answers with artifact bytes and is not under
    the API prefix, so the old subtractive rule bootstrapped it: one pasted
    link set a cookie for the whole daemon and then served the file."""
    request, token = gate
    response = request("GET", f"/preview/some-artifact?token={token}")
    assert response.status == 401
    assert response.header("Set-Cookie") is None


def test_a_deep_link_cannot_be_bootstrapped(gate):
    """Every unknown GET falls through to the SPA shell, so "not an API path"
    is an ever-growing surface. Nothing in the product ever emitted a deep link
    carrying a token -- `_url()` builds the origin and `/?token=`."""
    request, token = gate
    response = request("GET", f"/session/abc?token={token}")
    assert response.status == 401
    assert response.header("Set-Cookie") is None


def test_a_bootstrapped_data_path_does_not_serve_the_data(gate):
    """The consequence, not just the status: no route ran."""
    request, token = gate
    response = request("GET", f"/api/v1/artifacts/x/download?token={token}")
    assert response.status == 401
    assert response.api_calls == []


# --------------------------------------------------------------------------
# 2. a query-string credential on a mutation is refused, always
# --------------------------------------------------------------------------


def test_a_mutation_with_a_query_token_is_refused_even_when_authenticated(gate):
    """Accepted-with-a-warning is the failure being removed. The cookie made
    this request valid, so the leaked URL worked, so nobody found out."""
    request, token = gate
    response = request(
        "POST",
        f"/api/v1/frames?token={token}",
        {"Cookie": f"{COOKIE_NAME}={token}"},
    )
    assert response.status == 401
    assert response.api_calls == [], "the mutation ran despite the refusal"


def test_the_same_mutation_without_the_query_token_is_allowed(gate):
    """The refusal must be about the query string, not about mutations."""
    request, token = gate
    response = request("POST", "/api/v1/frames", {"Cookie": f"{COOKIE_NAME}={token}"})
    assert response.api_calls == [("POST", "/frames")]


def test_the_refusal_covers_every_mutating_method(gate):
    request, token = gate
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        response = request(
            method,
            f"/api/v1/frames?token={token}",
            {"Cookie": f"{COOKIE_NAME}={token}"},
        )
        assert response.status == 401, method
        assert response.api_calls == [], method


def test_the_refusal_names_a_credential_that_is_not_the_token(gate):
    """A 401 a caller cannot act on sends them back to the query string."""
    request, token = gate
    response = request(
        "POST",
        f"/api/v1/frames?token={token}",
        {"Cookie": f"{COOKIE_NAME}={token}"},
    )
    message = str((response.body or {}).get("error", ""))
    assert local_auth.TOKEN_HEADER in message
    assert token not in message, "the refusal echoed the credential back"


# --------------------------------------------------------------------------
# 3. what the 303 hands the browser
# --------------------------------------------------------------------------


def test_the_redirect_strips_the_credential_and_keeps_the_rest(gate):
    request, token = gate
    response = request("GET", f"/?token={token}&panel=timeline")
    location = response.header("Location")
    assert location == "/?panel=timeline"
    assert "token" not in location
    assert token not in location


def test_no_response_header_but_the_cookie_carries_the_token(gate):
    """History, Referer and proxy logs all read the *URL*. The cookie is the
    one place the credential is allowed to land."""
    request, token = gate
    response = request("GET", f"/?token={token}")
    leaking = [
        (key, value)
        for key, value in response.headers
        if token in str(value) and key.lower() != "set-cookie"
    ]
    assert leaking == []


def test_the_cookie_is_httponly_and_samesite_strict(gate):
    """HttpOnly keeps a script (or an XSS in agent-authored artifact HTML) from
    reading it; SameSite=Strict keeps a foreign page from riding it."""
    request, token = gate
    cookie = request("GET", f"/?token={token}").header("Set-Cookie") or ""
    assert "HttpOnly" in cookie
    assert "SameSite=Strict" in cookie
    assert "Path=/" in cookie


def test_the_bootstrap_is_visible_in_the_access_log(gate):
    """`_route` logs `_last_status`, which only `_send` used to set -- so the
    one request that hands out a credential was logged as `status=None`,
    indistinguishable from a request that died before answering."""
    request, token = gate
    assert request("GET", f"/?token={token}").last_status == 303


def test_a_wrong_token_on_the_root_page_sets_no_cookie(gate):
    request, _token = gate
    response = request("GET", "/?token=not-the-token", {"Accept": "application/json"})
    assert response.status == 401
    assert response.header("Set-Cookie") is None


# --------------------------------------------------------------------------
# 4. what must not break
# --------------------------------------------------------------------------


def test_health_stays_open(gate):
    request, _token = gate
    assert request("GET", "/health").status == 200


def test_a_bearer_header_still_authenticates(gate):
    request, token = gate
    response = request("GET", "/api/v1/frames", {"Authorization": f"Bearer {token}"})
    assert response.api_calls == [("GET", "/frames")]


def test_a_get_with_a_query_token_on_a_data_path_is_not_a_mutation_refusal(gate):
    """Both refusals answer 401; they must not be the same refusal. A GET is
    turned away by the bootstrap allowlist, which is what keeps the message
    about opening the printed URL rather than about mutations."""
    request, token = gate
    response = request(
        "GET", f"/preview/x?token={token}", {"Accept": "application/json"}
    )
    assert response.status == 401
    assert "query string" not in str((response.body or {}).get("error", ""))
