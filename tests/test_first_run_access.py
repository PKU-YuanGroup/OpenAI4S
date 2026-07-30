"""Whether a first-run user can get into their own daemon.

The access token became required by default, on loopback too — correct, because
this daemon executes code. Nothing that hands a URL to a human was updated with
it. `_url()` returned the bare origin and fed the browser auto-open on `serve`,
the `status` line, the `url` command, and the macOS .app; every one of those
opens a page that answers 401.

The recovery paths a user would reach for are all closed:

- `/static/app.js` is behind the same gate, so the SPA cannot load and cannot
  offer a way in — the fix cannot live in `app.js`.
- The 401 body was JSON, which a browser renders as raw text.
- The one working URL is printed to stderr, and the .app redirects stderr into
  `logs/app.out`, which nobody is reading on first launch.

So a .dmg user had no in-product route to a working session at all. A terminal
user was merely confused: the good URL is one line above on stderr.

Two changes. `_url()` carries the token for the callers that hand it to a
person and omits it for the ones that build request paths, and an unauthorised
*browser navigation* gets an HTML page naming the command to run. A script
still gets the JSON error it can parse.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from openai4s.cli.main import _url
from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

# --------------------------------------------------------------------------
# the URL the CLI hands over
# --------------------------------------------------------------------------


def test_the_printed_url_carries_the_token(tmp_path):
    """The defect. Every human-facing caller used this string."""
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    token = local_auth.load_or_mint(tmp_path)
    assert token, "no token minted; this test proves nothing"
    assert f"token={token}" in _url(cfg)


def test_a_daemon_with_no_token_gets_a_plain_url(tmp_path):
    """Appending `?token=None` would be worse than the bare origin."""
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    assert _url(cfg) == f"http://{cfg.host}:{cfg.port}/"
    assert "token" not in _url(cfg)


def test_machine_callers_opt_out(tmp_path):
    """`status` builds `_url(cfg) + "health"` and the API helper appends a
    path. Against a URL carrying a query string those become
    `...?token=Xhealth` — so the same change that fixes the human path breaks
    both of them unless they opt out."""
    cfg = Config(data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="k"))
    local_auth.load_or_mint(tmp_path)
    plain = _url(cfg, with_token=False)
    assert plain.endswith("/") and "token" not in plain
    assert (plain + "health").endswith("/health")


def test_no_caller_concatenates_onto_the_token_url():
    """The regression this guards is silent: a 404 on a health probe reads as
    "daemon down" rather than "we built a nonsense URL"."""
    source = Path("openai4s/cli/main.py").read_text(encoding="utf-8")
    for line in source.splitlines():
        if "_url(cfg)" in line and "with_token" not in line:
            # Human-facing print/open only — never string concatenation.
            assert not any(
                bad in line for bad in ("_url(cfg) +", "_url(cfg).rstrip")
            ), line


# --------------------------------------------------------------------------
# what a browser gets
# --------------------------------------------------------------------------


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def unauthenticated(tmp_path, monkeypatch):
    """A real handler with the token gate armed and no credential presented."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)

    def get(path, accept):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}

        def _send(code, body, ctype, extra=None):
            sent.update(code=code, body=body, ctype=ctype)

        handler._send = _send
        handler.command = "GET"
        handler.path = path
        handler.headers = {"Content-Length": "0", "Accept": accept}
        handler.close_connection = False
        handler._route("GET")
        return sent

    return get


def test_a_browser_gets_a_page_it_can_act_on(unauthenticated):
    """The defect, as what a person actually sees. JSON in a browser window is
    not a recovery path."""
    sent = unauthenticated("/", "text/html,application/xhtml+xml,*/*")
    assert sent["code"] == 401
    assert "text/html" in sent["ctype"]
    body = sent["body"].decode("utf-8")
    assert "openai4s url" in body, "the page does not say what to run"


def test_a_script_still_gets_json(unauthenticated):
    """Turning every 401 into HTML would break every client that parses the
    error, which is the opposite failure."""
    sent = unauthenticated("/", "application/json")
    assert sent["code"] == 401
    assert "json" in sent["ctype"]
    assert json.loads(sent["body"].decode("utf-8"))["error"]


def test_the_page_carries_no_credential(unauthenticated):
    """It is served to an unauthenticated caller by definition. Embedding the
    token would hand it to exactly whoever was being refused."""
    token = local_auth.read_token(Path(_data_dir(unauthenticated))) or ""
    body = unauthenticated("/", "text/html").get("body", b"").decode("utf-8")
    assert token, "no token to leak; test is vacuous"
    assert token not in body


def _data_dir(_get) -> str:
    import os

    return os.environ["OPENAI4S_DATA_DIR"]


def test_the_page_fetches_nothing(unauthenticated):
    """Every asset it could reference — app.js, style.css — is behind this same
    gate, so an external reference would render a blank page."""
    body = unauthenticated("/", "text/html")["body"].decode("utf-8")
    for referencing in ("<script src", '<link rel="stylesheet"', "fetch("):
        assert referencing not in body, referencing


def test_health_is_still_open(unauthenticated):
    """The liveness probe must not start demanding a credential — `status`
    uses it to tell "running" from "down"."""
    assert unauthenticated("/health", "*/*")["code"] == 200


# --------------------------------------------------------------------------
# end to end, against a real daemon
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_the_url_the_cli_prints_actually_opens(tmp_path):
    """The whole claim, on the wire. Everything above could pass while the two
    halves still disagreed about the token's shape."""
    import os

    env = dict(os.environ)
    env.update(
        OPENAI4S_DATA_DIR=str(tmp_path),
        OPENAI4S_SECRET_STORE="plaintext",
        OPENAI4S_PORT="8791",
        OPENAI4S_NO_OPEN="1",
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "openai4s.cli.main", "serve", "--no-open"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        base = "http://127.0.0.1:8791"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            pytest.skip("daemon did not come up")

        token = local_auth.read_token(tmp_path)
        assert token, "the daemon minted no token"

        # The bare origin: what every human-facing caller used to hand over.
        try:
            urllib.request.urlopen(base + "/", timeout=5)
            bare_status = 200
        except urllib.error.HTTPError as err:
            bare_status = err.code
        assert bare_status == 401

        # The URL `openai4s url` prints now. Do NOT follow the redirect:
        # the 303 IS the bootstrap — it sets the cookie and sends the browser
        # to the same path with the credential stripped. urllib would follow it
        # without carrying the cookie and land on a legitimate 401, which says
        # nothing about whether the token was accepted.
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                return None

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(f"{base}/?token={token}", timeout=5) as response:
                assert response.status == 200
        except urllib.error.HTTPError as err:
            assert err.code == 303, f"the printed URL was refused: {err.code}"
            cookie = err.headers.get("Set-Cookie") or ""
            assert "os_token=" in cookie, "the bootstrap set no cookie"
    finally:
        proc.terminate()
        proc.wait(timeout=30)
