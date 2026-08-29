"""OPENAI4S_WEBUI_NEXT: dist SPA shell, packaging globs, wheel sentinel.

Wire tests go through a real ThreadingHTTPServer so `/`, `/index.html`, and
an unknown non-API GET (SPA deep links) share the same `_serve_index` path
the production daemon uses. Packaging assertions are source-level so a
dropped glob fails before `uv build`.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import re
import sys
import threading
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from tests._ports import bound_gateway_server

ROOT = Path(__file__).resolve().parents[1]
_LEGACY = b"<!doctype html><title>legacy-shell</title>\n"
_NEXT = (
    b"<!doctype html><title>next-shell</title>\n"
    b'<script type="module" src="/static/dist/assets/index-BwbKMGnO.js"></script>\n'
)
_ASSET = b"console.log('next-shell')\n"


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


def _write(webui: Path, rel: str, data: bytes) -> Path:
    path = webui / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _load_verifier():
    path = ROOT / "scripts" / "verify_release_artifacts.py"
    spec = importlib.util.spec_from_file_location(
        "openai4s_test_verify_release_f04", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def webui_next_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Loopback gateway with both the legacy shell and a dist tree planted."""

    webui = tmp_path / "webui"
    webui.mkdir()
    _write(webui, "index.html", _LEGACY)
    _write(webui, "app.js", b"legacy-app\n")
    _write(webui, "dist/index.html", _NEXT)
    _write(webui, "dist/assets/index-BwbKMGnO.js", _ASSET)

    monkeypatch.setattr(gateway_mod, "WEBUI_DIR", webui)

    httpd, port = bound_gateway_server()
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
        host="127.0.0.1",
        port=port,
    )
    cfg.ensure_dirs()
    runner = gateway_mod.SessionRunner(cfg, _Hub(), start_idle_sweeper=False)
    handler_cls = gateway_mod.make_handler(cfg, _Hub(), runner)
    httpd.RequestHandlerClass = handler_cls
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    token = local_auth.load_or_mint(cfg.data_dir)
    try:
        yield {"port": port, "token": token, "webui": webui}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        try:
            runner.close()
        except Exception:  # noqa: BLE001 - teardown must not mask a failure
            pass


def _get(
    port: int,
    path: str,
    token: str,
) -> tuple[int, dict[str, str], bytes]:
    headers = {local_auth.TOKEN_HEADER: token}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        hdrs = {key.lower(): value for key, value in resp.getheaders()}
        return resp.status, hdrs, body
    finally:
        conn.close()


@pytest.mark.parametrize(
    "raw, enabled",
    [
        (None, False),
        ("", False),
        ("0", False),
        ("true", False),
        ("yes", False),
        ("on", False),
        ("1", True),
        (" 1 ", True),
    ],
)
def test_webui_next_flag_is_exact_one(monkeypatch: pytest.MonkeyPatch, raw, enabled):
    if raw is None:
        monkeypatch.delenv("OPENAI4S_WEBUI_NEXT", raising=False)
    else:
        monkeypatch.setenv("OPENAI4S_WEBUI_NEXT", raw)
    assert gateway_mod._webui_next_enabled() is enabled


def test_unset_flag_serves_legacy_shell_and_deep_links(webui_next_daemon):
    port, token = webui_next_daemon["port"], webui_next_daemon["token"]
    for path in ("/", "/index.html", "/projects/p1/frames/f1"):
        status, hdrs, body = _get(port, path, token)
        assert status == 200, path
        assert body == _LEGACY, path
        assert hdrs["content-type"].startswith("text/html")
        assert b"next-shell" not in body


def test_flag_serves_dist_index_for_shell_and_deep_links(
    webui_next_daemon, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENAI4S_WEBUI_NEXT", "1")
    port, token = webui_next_daemon["port"], webui_next_daemon["token"]
    for path in ("/", "/index.html", "/projects/p1/frames/f1"):
        status, hdrs, body = _get(port, path, token)
        assert status == 200, path
        assert body == _NEXT, path
        assert hdrs["content-type"].startswith("text/html")
        assert b"legacy-shell" not in body


def test_flag_does_not_hide_legacy_static_or_dist_tree(
    webui_next_daemon, monkeypatch: pytest.MonkeyPatch
):
    """`/static/` resolution is independent of the SPA-shell switch."""
    port, token = webui_next_daemon["port"], webui_next_daemon["token"]

    status, _, body = _get(port, "/static/dist/index.html", token)
    assert status == 200
    assert body == _NEXT

    status, _, body = _get(port, "/static/dist/assets/index-BwbKMGnO.js", token)
    assert status == 200
    assert body == _ASSET

    monkeypatch.setenv("OPENAI4S_WEBUI_NEXT", "1")
    status, _, body = _get(port, "/static/app.js", token)
    assert status == 200
    assert body == b"legacy-app\n"

    status, _, body = _get(port, "/static/dist/assets/index-BwbKMGnO.js", token)
    assert status == 200
    assert body == _ASSET


def test_flag_on_without_dist_index_is_404(
    webui_next_daemon, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("OPENAI4S_WEBUI_NEXT", "1")
    (webui_next_daemon["webui"] / "dist" / "index.html").unlink()
    status, _, body = _get(webui_next_daemon["port"], "/", webui_next_daemon["token"])
    assert status == 404
    assert json.loads(body.decode("utf-8"))["error"] == "not found"


def test_package_data_lists_dist_tree_explicitly():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    start = text.index("[tool.setuptools.package-data]")
    end = text.index("[tool.setuptools.exclude-package-data]")
    block = text[start:end]
    assert '"server/webui/dist/*.html"' in block
    assert '"server/webui/dist/assets/*"' in block


def test_wheel_required_pins_dist_index_sentinel():
    verifier = _load_verifier()
    assert "openai4s/server/webui/dist/index.html" in verifier._WHEEL_REQUIRED
    assert "openai4s/server/webui/dist/index.html" in verifier._SDIST_REQUIRED


def test_committed_dist_index_is_external_script_shell():
    path = ROOT / "openai4s" / "server" / "webui" / "dist" / "index.html"
    html = path.read_text(encoding="utf-8")
    assert "/static/dist/" in html
    assert re.search(r"<script\b(?![^>]*\bsrc\s*=)", html, flags=re.I) is None
    assert '<script type="module"' in html
