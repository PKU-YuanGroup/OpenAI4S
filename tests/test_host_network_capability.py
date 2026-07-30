"""The Host's outbound capability, and the guards that make it a capability.

Three bundled skills reached for raw ``urllib`` because `host.web_fetch` could
not express what they needed -- a HEAD existence probe, a contactable
User-Agent, a binary download. A request made that way is subject to neither the
egress allowlist nor the SSRF guard, so the gap in the API was not a
convenience problem: it was the reason part of the product's own network
traffic went around the fence built for it.

Closing it adds three powers, and a power granted without a test that it is
bounded is just a power. Every test here asserts a refusal or a limit; nothing
in this module touches the network.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from openai4s import webtools
from openai4s.host.files import WorkspaceFileService
from openai4s.tools.web_download import WebDownloadTool


class _Response(io.BytesIO):
    """Enough of an HTTP response for `_http_get` to read."""

    def __init__(self, body: bytes, *, url: str = "https://example.test/x", ctype=""):
        super().__init__(body)
        self._url = url
        self.headers = {"Content-Type": ctype}

    def geturl(self) -> str:
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None


@pytest.fixture(autouse=True)
def _network_on(monkeypatch):
    monkeypatch.setenv("OPENAI4S_ALLOW_NETWORK", "1")


def _stub_urlopen(monkeypatch, response_factory, recorder=None):
    """Intercept the outbound request and record what it was handed.

    Both seams, deliberately. `_http_get` stopped calling
    `urllib.request.urlopen` when redirects became something it follows
    itself — the stdlib opener follows them internally, which silently
    defeated the per-hop SSRF and egress checks — so it now goes through
    `build_opener`. Patching only `urlopen` would leave these tests exercising
    a function the module no longer calls: they would pass on a code path that
    does not exist.
    """
    monkeypatch.setattr(webtools, "requests", None, raising=False)
    import urllib.request

    def _fake(request, timeout=None):  # noqa: ANN001
        if recorder is not None:
            recorder.append(request)
        return response_factory(request)

    class _Opener:
        def open(self, request, timeout=None):  # noqa: ANN001
            return _fake(request, timeout)

    monkeypatch.setattr(urllib.request, "urlopen", _fake)
    monkeypatch.setattr(urllib.request, "build_opener", lambda *_a, **_k: _Opener())


# --------------------------------------------------------------------------
# the byte ceiling
# --------------------------------------------------------------------------


def test_a_response_is_bounded_while_it_is_read_not_after(monkeypatch):
    """`resp.read()` with no argument allocates whatever the server sends.

    For a capability an agent can point at an arbitrary URL, that is the remote
    host deciding how much memory this process uses. A cap applied to an
    already-allocated body would describe the allocation rather than bound it,
    so the read stops at the limit and says so.
    """
    seen = []

    class _Endless(io.RawIOBase):
        def read(self, size=-1):  # noqa: ANN001
            seen.append(size)
            return b"x" * (size if size and size > 0 else 65536)

    _stub_urlopen(monkeypatch, lambda _r: _Response(b""))
    with pytest.raises(webtools.ResponseTooLarge) as refused:
        webtools._read_capped(_Endless(), 100_000)
    assert "100000" in str(refused.value)
    # It gave up early rather than reading to the end of an endless stream.
    assert sum(s for s in seen if s and s > 0) < 1_000_000


def test_the_cap_is_the_callers_and_a_body_under_it_is_returned_whole(monkeypatch):
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"a" * 500))
    body, _url, _ctype = webtools._http_get("https://example.test/x", max_bytes=1000)
    assert body == b"a" * 500

    _stub_urlopen(monkeypatch, lambda _r: _Response(b"a" * 5000))
    with pytest.raises(webtools.ResponseTooLarge):
        webtools._http_get("https://example.test/x", max_bytes=1000)


# --------------------------------------------------------------------------
# HEAD and User-Agent
# --------------------------------------------------------------------------


def test_head_asks_for_no_body_and_does_not_pretend_to_have_one(monkeypatch):
    """A HEAD returns `exists`, not `content: ""`.

    An empty string reads as "the resource is empty"; what happened is that we
    did not ask for the body. Those are different answers and a caller acts on
    them differently.
    """
    import urllib.error
    import urllib.request

    seen = []

    class _Opener:
        def open(self, request, timeout=None):  # noqa: ANN001
            seen.append(request)
            raise urllib.error.HTTPError(
                request.full_url, 302, "Found", {"Location": "https://pub/x"}, None
            )

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _Opener())
    result = webtools.web_fetch("https://doi.org/10.1/x", method="HEAD")

    assert seen[-1].get_method() == "HEAD"
    assert "content" not in result
    assert result["method"] == "HEAD"
    # doi.org's OWN answer: a 302 means the DOI is registered. Following it
    # would have returned the publisher's status instead -- possibly a 403
    # paywall for a DOI that certainly exists.
    assert result["status"] == 302 and result["exists"] is True
    assert result["location"] == "https://pub/x"


def test_an_unsupported_method_is_refused_rather_than_coerced(monkeypatch):
    """A typo must not silently become a GET, and this is the only place that
    decides -- `web_fetch` passes whatever it is given straight through."""
    _stub_urlopen(monkeypatch, lambda _r: _Response(b""))
    for bad in ("DELETE", "POST", "get "):
        with pytest.raises(ValueError):
            webtools._http_get("https://example.test/x", method=bad)


def test_a_caller_supplied_user_agent_actually_reaches_the_request(monkeypatch):
    """Crossref and OpenAlex serve their polite pool only to a contactable
    identity. Without this option `literature-review` sent its own header via
    raw urllib, outside every guard."""
    requests_made = []
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"{}"), requests_made)

    webtools.web_fetch(
        "https://api.openalex.org/works", user_agent="OpenAI4S (me@example.test)"
    )
    sent = requests_made[-1]
    assert sent.get_header("User-agent") == "OpenAI4S (me@example.test)"

    # Absent, the default identity is still sent -- never nothing.
    webtools.web_fetch("https://api.openalex.org/works")
    assert requests_made[-1].get_header("User-agent")


# --------------------------------------------------------------------------
# the guards still apply to the new paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_the_ssrf_guard_applies_to_every_method(monkeypatch, method):
    """A HEAD is a request. Exempting it would turn the new option into an
    existence oracle for the host's private network -- which is precisely what
    the guard exists to deny."""
    monkeypatch.delenv("OPENAI4S_ALLOW_PRIVATE_FETCH", raising=False)
    _stub_urlopen(monkeypatch, lambda _r: _Response(b""))
    for target in ("http://127.0.0.1:8760/", "http://169.254.169.254/latest/meta-data"):
        with pytest.raises(webtools.SSRFBlocked):
            webtools._http_get(target, method=method)


def test_the_download_path_goes_through_the_same_guard(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI4S_ALLOW_PRIVATE_FETCH", raising=False)
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"data"))
    with pytest.raises(webtools.SSRFBlocked):
        webtools.web_download("http://169.254.169.254/x", tmp_path / "out.bin")
    assert not (tmp_path / "out.bin").exists()


# --------------------------------------------------------------------------
# workspace confinement
# --------------------------------------------------------------------------


def _workspace(root: Path) -> WorkspaceFileService:
    """The real confinement, not a stand-in for it.

    An earlier draft of this module reimplemented `resolve` as a test double.
    That would have been the wrong thing to assert against: if production's
    version were the weaker of the two -- one fewer `resolve()`, no symlink
    check -- every test here would still pass while the capability it is
    guarding was open. The service takes only a data dir and a frame id, so
    there is no reason to substitute it.
    """
    return WorkspaceFileService(data_dir=root, frame_id=lambda: "session-under-test")


def test_a_download_that_escapes_the_workspace_never_makes_the_request(
    monkeypatch, tmp_path
):
    """Order matters, not just the refusal.

    If the path were checked after the fetch, a rejected destination would
    still have told the caller whether the URL was reachable -- and would still
    have spent the request. The escape is decided first, with no network at
    all.
    """
    contacted = []
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"x"), contacted)
    workspace = _workspace(tmp_path)
    tool = WebDownloadTool()

    for escape in ("../outside.bin", "/etc/passwd", "sub/../../outside.bin"):
        result = tool.execute(
            workspace, {"url": "https://example.test/f.zip", "path": escape}
        )
        assert "escapes the workspace" in result["error"], escape
    assert contacted == [], "a refused path still made a request"


def test_a_download_reports_a_workspace_relative_path_and_its_digest(
    monkeypatch, tmp_path
):
    """The absolute path contains the data dir, and therefore $HOME. It must
    not reach the model or a stored frame; every other file-producing tool
    reports relative, and so does this one."""
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"PK\x03\x04payload"))
    workspace = _workspace(tmp_path)

    result = WebDownloadTool().execute(
        workspace, {"url": "https://example.test/spectra.zip", "path": "data/s.zip"}
    )
    assert result["path"] == str(Path("data/s.zip"))
    assert str(tmp_path) not in str(result)
    assert result["bytes"] == len(b"PK\x03\x04payload")
    assert result["sha256"] == hashlib.sha256(b"PK\x03\x04payload").hexdigest()
    assert (
        workspace.workspace() / "data" / "s.zip"
    ).read_bytes() == b"PK\x03\x04payload"


def test_an_oversized_download_is_a_soft_error_not_a_crash(monkeypatch, tmp_path):
    """The worker turns a single-key `{"error": ...}` into a RuntimeError the
    cell can catch. A traceback out of the dispatcher is not that contract."""

    class _Big(io.RawIOBase):
        def read(self, size=-1):  # noqa: ANN001
            return b"x" * (size if size and size > 0 else 65536)

    monkeypatch.setattr(webtools, "requests", None, raising=False)
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Big())
    workspace = _workspace(tmp_path)
    result = WebDownloadTool().execute(
        workspace,
        {"url": "https://example.test/huge.bin", "path": "huge.bin", "max_bytes": 1000},
    )
    assert "error" in result and len(result) == 1
    assert not (workspace.workspace() / "huge.bin").exists()


# --------------------------------------------------------------------------
# the tool declares what the registry requires of a network tool
# --------------------------------------------------------------------------


def test_the_download_tool_screens_its_output_like_every_network_tool():
    """The registry refuses to register a network tool that does not, and it
    is not a formality here even though the bytes never reach the model: the
    final URL after redirects and the server's Content-Type do, and both are
    chosen by the remote host.
    """
    tool = WebDownloadTool()
    assert tool.needs_network and tool.screen_untrusted_output
    assert tool.writes_files
    assert tool.permission_target(
        {"url": "https://rruff.info/zipped_data_files/x.zip"}
    ) == ("rruff.info")


def test_a_symlink_planted_inside_the_workspace_does_not_widen_it(
    monkeypatch, tmp_path
):
    """The escape a purely lexical check misses.

    `../out.bin` is caught by looking at the string. A symlink named `data`
    that points outside is not: every path under it *looks* workspace-relative
    and lands elsewhere. Agent code can create one -- the workspace is writable
    by the cell -- so this is reachable, not hypothetical. `resolve()` before
    the containment check is what closes it, and asserting it here is why this
    module drives the real service instead of a double.
    """
    contacted = []
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"x"), contacted)
    workspace = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.workspace() / "data").symlink_to(outside, target_is_directory=True)

    result = WebDownloadTool().execute(
        workspace, {"url": "https://example.test/f.zip", "path": "data/escaped.bin"}
    )
    assert "escapes the workspace" in result["error"]
    assert not (outside / "escaped.bin").exists()
    assert contacted == [], "the request was made before the path was judged"


def test_a_probe_is_guarded_like_any_other_request(monkeypatch):
    """Not following redirects makes a probe narrower, not exempt.

    `web_probe` builds its own opener rather than going through `_http_get`, so
    it would be easy for it to inherit none of the checks by accident. It calls
    them directly and this asserts it: a probe that answered for loopback would
    be an existence oracle for the host's private network, which is exactly
    what the guard denies for every other verb.
    """
    monkeypatch.delenv("OPENAI4S_ALLOW_PRIVATE_FETCH", raising=False)
    import urllib.request

    opened = []
    monkeypatch.setattr(
        urllib.request, "build_opener", lambda *a: opened.append(a) or None
    )
    for target in ("http://127.0.0.1:8760/", "http://169.254.169.254/x"):
        with pytest.raises(webtools.SSRFBlocked):
            webtools.web_probe(target)
    assert opened == [], "the probe was built before the target was judged"


def test_an_unreachable_probe_is_an_answer_not_an_exception(monkeypatch):
    """A caller probing a list of DOIs should not have one dead connection end
    the batch. Status 0 says "no status could be obtained", which is different
    from a 404 and is reported as such."""
    import urllib.error
    import urllib.request

    class _Dead:
        def open(self, request, timeout=None):  # noqa: ANN001
            raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _Dead())
    result = webtools.web_probe("https://example.test/gone")
    assert result["status"] == 0 and result["exists"] is False
    assert "connection refused" in result["error"]


def test_a_404_probe_reports_absence_rather_than_failing(monkeypatch):
    import urllib.error
    import urllib.request

    class _Missing:
        def open(self, request, timeout=None):  # noqa: ANN001
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "build_opener", lambda *a: _Missing())
    result = webtools.web_probe("https://doi.org/10.9999/nope")
    assert result["status"] == 404 and result["exists"] is False
