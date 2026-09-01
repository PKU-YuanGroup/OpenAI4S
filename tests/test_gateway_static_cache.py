"""Wire contracts for static UI caching: ETag, 304, gzip, immutable, symlink.

These go through a real `ThreadingHTTPServer` rather than a stubbed `_send`.
304-without-a-body and gzip Content-Length are properties of the status line
and the bytes on the socket; a captured `_send` call cannot see them.
"""

from __future__ import annotations

import gzip
import http.client
import json
import threading
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth
from openai4s.server.security_headers import security_headers
from tests._ports import bound_gateway_server

_PAYLOAD = ("static-cache-payload\n" * 80).encode("ascii")  # >1KB, gzip-friendly
assert len(_PAYLOAD) > 1024


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


@pytest.fixture()
def static_daemon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Loopback gateway whose `/static/` tree is a fixture, not the real webui."""

    webui = tmp_path / "webui"
    webui.mkdir()
    _write(webui, "index.html", b"<!doctype html><title>fixture</title>\n" + _PAYLOAD)
    _write(
        webui,
        "dist/index.html",
        b"<!doctype html><title>dist-fixture</title>\n" + _PAYLOAD,
    )
    _write(webui, "app.js", _PAYLOAD)
    _write(webui, "tiny.js", b"ok")
    _write(webui, "style.css", _PAYLOAD)
    _write(webui, "main.8617f334.js", _PAYLOAD)
    _write(webui, "622.365fdd8e.chunk.css", _PAYLOAD)
    _write(webui, "AnthropicSans-Roman-Variable-CFxw3nG7.woff2", _PAYLOAD[:400])
    _write(webui, "plain.bin", _PAYLOAD)
    _write(webui, "vendor/ketcher/index.html", b"<html>ketcher</html>\n" + _PAYLOAD)

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
        yield {
            "port": port,
            "token": token,
            "webui": webui,
            "handler_cls": handler_cls,
            "outside": tmp_path / "outside.txt",
        }
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
    extra: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {local_auth.TOKEN_HEADER: token}
    if extra:
        headers.update(extra)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        hdrs = {key.lower(): value for key, value in resp.getheaders()}
        return resp.status, hdrs, body
    finally:
        conn.close()


def _security_keys() -> set[str]:
    return {key.lower() for key in security_headers()}


@pytest.mark.parametrize(
    "name, expected",
    [
        ("main.8617f334.js", True),
        ("622.365fdd8e.chunk.css", True),
        ("159.6bd8317f.chunk.js", True),
        ("AnthropicSans-Roman-Variable-CFxw3nG7.woff2", True),
        ("AnthropicSerif-Roman-Variable-C-BHYa_K.woff2", True),
        ("AnthropicMono-Roman-Web-B88FVziN.woff2", True),
        ("AnthropicMono-Italic-Web-DHGc3er-.woff2", True),
        ("anthropicons-variable-DICoRAgs.woff2", True),
        ("app.js", False),
        ("style.css", False),
        ("index.html", False),
        ("theme-bootstrap.js", False),
        ("scientific_renderers.js", False),
        ("3Dmol-min.js", False),
        ("favicon_anim_64.gif", False),
        ("ketcher-page.js", False),
    ],
)
def test_fingerprint_names_are_the_hashed_vendor_assets(name: str, expected: bool):
    assert gateway_mod._is_fingerprinted_name(name) is expected


def test_index_html_defers_body_scripts_and_preloads_roman_fonts():
    html = (Path(gateway_mod.WEBUI_DIR) / "index.html").read_text(encoding="utf-8")
    assert '<script src="/static/theme-bootstrap.js"></script>' in html
    assert '<script src="/static/theme-bootstrap.js" defer>' not in html
    for src in (
        "/static/favicon.js",
        "/static/scientific_renderers.js",
        "/static/app.js",
    ):
        assert f'<script src="{src}" defer></script>' in html
    for href in (
        "/static/vendor/fonts/AnthropicSans-Roman-Variable-CFxw3nG7.woff2",
        "/static/vendor/fonts/AnthropicSerif-Roman-Variable-C-BHYa_K.woff2",
        "/static/vendor/fonts/AnthropicMono-Roman-Web-B88FVziN.woff2",
    ):
        assert (
            f'rel="preload" href="{href}" as="font" type="font/woff2" crossorigin'
            in html
        )


def test_etag_miss_then_hit_is_empty_304_with_security_headers(static_daemon):
    port, token = static_daemon["port"], static_daemon["token"]
    status, hdrs, body = _get(port, "/static/app.js", token)
    assert status == 200
    assert body == _PAYLOAD
    etag = hdrs["etag"]
    assert etag.startswith("W/")
    assert "-gz" not in etag
    assert hdrs["cache-control"] == "no-cache"

    miss, miss_hdrs, miss_body = _get(
        port, "/static/app.js", token, {"If-None-Match": 'W/"deadbeef-1"'}
    )
    assert miss == 200
    assert miss_body == _PAYLOAD
    assert miss_hdrs["etag"] == etag

    hit, hit_hdrs, hit_body = _get(
        port, "/static/app.js", token, {"If-None-Match": etag}
    )
    assert hit == 304
    assert hit_body == b""
    assert hit_hdrs.get("content-length") in ("0", None)
    assert hit_hdrs["etag"] == etag
    for key in _security_keys():
        assert key in hit_hdrs, f"304 dropped {key}"
    script_src = (
        hit_hdrs["content-security-policy"].split("script-src", 1)[-1].split(";", 1)[0]
    )
    assert "'unsafe-eval'" not in script_src
    assert "'unsafe-inline'" not in script_src


def test_gzip_negotiation_vary_and_identity_fallback(static_daemon):
    port, token = static_daemon["port"], static_daemon["token"]
    identity_status, identity_hdrs, identity_body = _get(port, "/static/app.js", token)
    assert identity_status == 200
    assert identity_body == _PAYLOAD
    assert "content-encoding" not in identity_hdrs
    assert identity_hdrs["vary"] == "Accept-Encoding"
    identity_etag = identity_hdrs["etag"]
    assert not identity_etag.endswith('-gz"')

    gz_status, gz_hdrs, gz_body = _get(
        port, "/static/app.js", token, {"Accept-Encoding": "gzip"}
    )
    expected = gzip.compress(_PAYLOAD, compresslevel=6, mtime=0)
    assert gz_status == 200
    assert gz_hdrs["content-encoding"] == "gzip"
    assert gz_hdrs["vary"] == "Accept-Encoding"
    assert gz_body == expected
    assert len(gz_body) < len(_PAYLOAD)
    gz_etag = gz_hdrs["etag"]
    assert gz_etag.endswith('-gz"')
    assert gz_etag != identity_etag

    # A gzip ETag is a different representation; identity must not 304 on it.
    cross, _, cross_body = _get(
        port, "/static/app.js", token, {"If-None-Match": gz_etag}
    )
    assert cross == 200
    assert cross_body == _PAYLOAD

    gz_hit, gz_hit_hdrs, gz_hit_body = _get(
        port,
        "/static/app.js",
        token,
        {"Accept-Encoding": "gzip", "If-None-Match": gz_etag},
    )
    assert gz_hit == 304
    assert gz_hit_body == b""
    assert gz_hit_hdrs["etag"] == gz_etag
    assert gz_hit_hdrs["content-encoding"] == "gzip"
    assert gz_hit_hdrs["vary"] == "Accept-Encoding"
    for key in _security_keys():
        assert key in gz_hit_hdrs

    refused, refused_hdrs, refused_body = _get(
        port, "/static/app.js", token, {"Accept-Encoding": "gzip;q=0"}
    )
    assert refused == 200
    assert refused_body == _PAYLOAD
    assert "content-encoding" not in refused_hdrs

    tiny_status, tiny_hdrs, tiny_body = _get(
        port, "/static/tiny.js", token, {"Accept-Encoding": "gzip"}
    )
    assert tiny_status == 200
    assert tiny_body == b"ok"
    assert "content-encoding" not in tiny_hdrs
    assert "vary" not in tiny_hdrs

    bin_status, bin_hdrs, bin_body = _get(
        port, "/static/plain.bin", token, {"Accept-Encoding": "gzip"}
    )
    assert bin_status == 200
    assert bin_body == _PAYLOAD
    assert "content-encoding" not in bin_hdrs
    assert "vary" not in bin_hdrs


def test_immutable_cache_control_is_only_for_fingerprint_names(static_daemon):
    port, token = static_daemon["port"], static_daemon["token"]
    hashed_js = _get(port, "/static/main.8617f334.js", token)
    hashed_css = _get(port, "/static/622.365fdd8e.chunk.css", token)
    font = _get(port, "/static/AnthropicSans-Roman-Variable-CFxw3nG7.woff2", token)
    app = _get(port, "/static/app.js", token)
    css = _get(port, "/static/style.css", token)
    index = _get(port, "/", token)

    for status, hdrs, _body in (hashed_js, hashed_css, font):
        assert status == 200
        assert hdrs["cache-control"] == "public, max-age=31536000, immutable"
    for status, hdrs, _body in (app, css, index):
        assert status == 200
        assert hdrs["cache-control"] == "no-cache"

    _, hit_hdrs, hit_body = _get(
        port,
        "/static/main.8617f334.js",
        token,
        {"If-None-Match": hashed_js[1]["etag"]},
    )
    assert hit_body == b""
    assert hit_hdrs["cache-control"] == "public, max-age=31536000, immutable"
    for key in _security_keys():
        assert key in hit_hdrs


def test_files_over_the_stream_threshold_still_etag_and_304(
    static_daemon, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(gateway_mod, "_STATIC_STREAM_BYTES", 32)
    port, token = static_daemon["port"], static_daemon["token"]
    webui = static_daemon["webui"]
    _write(webui, "blob.bin", b"x" * 64)
    _write(webui, "main.aaaaaaaa.js", b"y" * 64)

    streamed: list[str] = []
    orig = static_daemon["handler_cls"]._stream_file

    def _wrap(self, path, ctype, extra=None, security=None):
        streamed.append(Path(path).name)
        return orig(self, path, ctype, extra=extra, security=security)

    monkeypatch.setattr(static_daemon["handler_cls"], "_stream_file", _wrap)

    status, hdrs, body = _get(port, "/static/blob.bin", token)
    assert status == 200
    assert body == b"x" * 64
    assert "blob.bin" in streamed
    etag = hdrs["etag"]

    hit, hit_hdrs, hit_body = _get(
        port, "/static/blob.bin", token, {"If-None-Match": etag}
    )
    assert hit == 304
    assert hit_body == b""
    for key in _security_keys():
        assert key in hit_hdrs

    js_status, js_hdrs, js_body = _get(port, "/static/main.aaaaaaaa.js", token)
    assert js_status == 200
    assert js_body == b"y" * 64
    assert "main.aaaaaaaa.js" in streamed
    assert js_hdrs["cache-control"] == "public, max-age=31536000, immutable"


def test_symlink_escape_is_forbidden_after_realpath(static_daemon):
    port, token = static_daemon["port"], static_daemon["token"]
    outside = static_daemon["outside"]
    outside.write_bytes(b"should-not-leak")
    link = static_daemon["webui"] / "leak.js"
    link.symlink_to(outside)

    status, hdrs, body = _get(port, "/static/leak.js", token)
    assert status == 403
    payload = json.loads(body.decode("utf-8"))
    assert payload["error"] == "forbidden"
    assert b"should-not-leak" not in body
    assert "content-security-policy" in hdrs

    parent = _get(port, "/static/../outside.txt", token)
    assert parent[0] == 403
    assert b"should-not-leak" not in parent[2]

    missing = _get(port, "/static/no-such-file.js", token)
    assert missing[0] == 404
    assert json.loads(missing[2].decode("utf-8"))["error"] == "not found"
