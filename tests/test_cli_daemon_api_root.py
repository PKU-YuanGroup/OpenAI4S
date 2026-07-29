"""Whether the CLI's daemon calls reach a route at all.

`openai4s share` built its URLs from `/api/...`. The daemon serves the API only
under `/api/v1` and answers anything else with a deliberate refusal — "the API
is versioned; use /api/v1" — so all nine share subcommands 404'd. The feature
had never reached a route in any released form, including the
`openai4s share import <url>` line the generated share page tells a recipient
to run, which is the only client for that endpoint at all.

Measured against a real daemon before the fix: `openai4s share status` exited 2
printing the versioning error. After: `{"state": "disabled", "configured":
false}`.

The prefix now comes from `contract.API_ROOT`, the same constant the gateway
routes on, and `_daemon_request` refuses a path that carries its own `/api/`
rather than quietly producing another 404 nobody reads as a defect.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from openai4s.server import contract

# `openai4s.cli.__init__` re-exports a FUNCTION called `main`, which shadows the
# module of the same name — for `from ... import main` and for
# `import openai4s.cli.main as x` alike, since both bind the package attribute.
# `tests/test_cli_contract.py` resolves it the same way.
cli_main = importlib.import_module("openai4s.cli.main")

CLI_SOURCE = Path(cli_main.__file__).read_text(encoding="utf-8")


def test_the_gateway_and_the_cli_share_one_definition():
    """They drifted, and nothing noticed for as long as the feature existed."""
    from openai4s.server import gateway

    assert gateway._API_ROOT == contract.API_ROOT
    assert contract.API_ROOT == "/api/v1"


def test_every_daemon_call_site_passes_a_root_relative_path():
    """The property, not one example. Nine call sites were wrong the same way,
    so an assertion about `share status` alone would leave eight of them free
    to stay broken — and free to come back.
    """
    tree = ast.parse(CLI_SOURCE)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", None) or getattr(func, "attr", None)
        if name != "_daemon_request":
            continue
        # (cfg, method, path, body) — the path is the third positional
        if len(node.args) < 3:
            continue
        path_node = node.args[2]
        literal = None
        if isinstance(path_node, ast.Constant) and isinstance(path_node.value, str):
            literal = path_node.value
        elif isinstance(path_node, ast.JoinedStr):
            leading = path_node.values[0] if path_node.values else None
            if isinstance(leading, ast.Constant) and isinstance(leading.value, str):
                literal = leading.value
        if literal is not None and literal.startswith("/api/"):
            offenders.append(f"line {path_node.lineno}: {literal!r}")
    assert not offenders, "call sites supply their own API prefix: " + "; ".join(
        offenders
    )


def test_a_path_that_carries_its_own_prefix_is_refused_loudly():
    """Silently stripping it would be the wrong repair: the call site would
    stay wrong and the next one would copy it. A merely-wrong path produces a
    404, which reads as "the server does not have that" rather than "we asked
    for the wrong thing"."""
    with pytest.raises(ValueError, match="relative to the API root"):
        cli_main._daemon_request(object(), "GET", "/api/shares")


def test_the_url_is_built_under_the_versioned_root(monkeypatch):
    """What actually goes on the wire."""
    seen: dict[str, str] = {}

    class _Response:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        return _Response()

    # Signature-compatible with the real `_url`, which grew `with_token` so the
    # human-facing callers can print a URL that actually opens. A lambda that
    # only accepts the old shape turns a real change into a TypeError here and
    # says nothing about the API root this test is actually about.
    monkeypatch.setattr(cli_main, "_url", lambda _cfg, **_kw: "http://127.0.0.1:8760/")
    monkeypatch.setattr(cli_main, "_daemon_token", lambda _cfg: "t")
    monkeypatch.setattr(cli_main.urllib.request, "urlopen", _fake_urlopen)

    cli_main._daemon_request(object(), "GET", "/shares")
    assert seen["url"] == "http://127.0.0.1:8760/api/v1/shares"
