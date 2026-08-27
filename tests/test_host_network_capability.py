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
import ipaddress
import socket
import sys
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
    monkeypatch.setenv("OPENAI4S_ALLOW_FAKE_IP_DNS", "0")

    def _offline_dns(host, port, *_args, **_kwargs):
        if host == "localhost":
            address = "127.0.0.1"
        else:
            try:
                address = str(ipaddress.ip_address(host))
            except ValueError:
                address = "93.184.216.34"
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        return [
            (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port or 0))
        ]

    # Every HTTP upstream in this module is already intercepted below. DNS has
    # to be deterministic too: Clash-style host DNS may synthesize 198.18/15
    # answers before the fake opener is reached, turning an offline contract
    # into a machine-dependent SSRF failure.
    monkeypatch.setattr(socket, "getaddrinfo", _offline_dns)


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
    # Production imports this optional dependency inside the call. Patching a
    # module attribute would not intercept that import when the test runner has
    # requests installed, and could let this offline test contact the network.
    monkeypatch.setitem(sys.modules, "requests", None)
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


def _fake_ip_dns(host, port, *_args, **_kwargs):
    try:
        address = str(ipaddress.ip_address(host))
    except ValueError:
        address = "198.18.0.42"
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (address, port or 0),
        )
    ]


def test_fake_ip_dns_needs_the_narrow_opt_in(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_ip_dns)
    monkeypatch.setenv("OPENAI4S_ALLOW_FAKE_IP_DNS", "0")

    with pytest.raises(webtools.SSRFBlocked):
        webtools.guard_url("https://api.openalex.org/works")


def test_fake_ip_dns_allows_only_catalogued_hostnames(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _fake_ip_dns)
    monkeypatch.setenv("OPENAI4S_ALLOW_FAKE_IP_DNS", "1")
    _stub_urlopen(monkeypatch, lambda _r: _Response(b"{}"))

    body, _url, _ctype = webtools._http_get("https://api.openalex.org/works")
    assert body == b"{}"
    assert webtools.guard_url("https://open.feedcoopapi.com/search_api") is None

    for target in (
        "https://example.test/not-approved",
        "https://child.open.feedcoopapi.com/credential-trap",
        "http://198.18.0.42/literal",
        "http://10.0.0.7/private",
        "http://169.254.169.254/latest/meta-data",
    ):
        with pytest.raises(webtools.SSRFBlocked):
            webtools.guard_url(target)


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


def test_download_publishes_through_acquired_parent_after_directory_swap(
    monkeypatch, tmp_path
):
    """The network wait must not reopen a now-symlinked workspace parent."""

    body = b"guarded download body"
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    workspace = _workspace(tmp_path)
    live = workspace.workspace() / "downloads"
    detached = workspace.workspace() / "downloads-detached"
    outside = tmp_path / "outside-downloads"
    live.mkdir()
    outside.mkdir()
    real_secure_parent = workspace.secure_parent

    def acquire_then_swap(relative, *, create_parents=False):
        parent = real_secure_parent(relative, create_parents=create_parents)
        live.rename(detached)
        live.symlink_to(outside, target_is_directory=True)
        return parent

    monkeypatch.setattr(workspace, "secure_parent", acquire_then_swap)

    result = WebDownloadTool().execute(
        workspace,
        {"url": "https://example.test/archive.bin", "path": "downloads/out.bin"},
    )

    assert result["path"] == "downloads/out.bin"
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert (detached / "out.bin").read_bytes() == body
    assert not (outside / "out.bin").exists()


def test_a_download_streams_instead_of_collecting_the_response(monkeypatch, tmp_path):
    """Checkpoint-sized bodies must not be joined in daemon memory first."""

    body = b"checkpoint-chunk" * 10_000
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    monkeypatch.setattr(
        webtools,
        "_read_capped",
        lambda *_a, **_k: pytest.fail("download used the in-memory fetch collector"),
    )

    destination = tmp_path / "checkpoint.zip"
    result = webtools.web_download(
        "https://example.test/checkpoint.zip",
        destination,
        max_bytes=len(body),
    )

    assert destination.read_bytes() == body
    assert result["bytes"] == len(body)
    assert result["sha256"] == hashlib.sha256(body).hexdigest()


def test_the_streaming_copier_applies_backpressure_between_reads():
    chunks = [b"first", b"second"]

    class _BackpressureReader:
        def __init__(self):
            self.index = 0
            self.may_read = True

        def read(self, size=-1):  # noqa: ANN001
            assert size == 64 * 1024
            assert self.may_read, "the next chunk was read before the last was written"
            if self.index == len(chunks):
                return b""
            chunk = chunks[self.index]
            self.index += 1
            self.may_read = False
            return chunk

    reader = _BackpressureReader()

    class _BackpressureWriter(io.BytesIO):
        def write(self, data):  # noqa: ANN001
            written = super().write(data)
            reader.may_read = True
            return written

    writer = _BackpressureWriter()

    size, digest = webtools._copy_capped(reader, writer, 100)

    assert writer.getvalue() == b"firstsecond"
    assert size == len(b"firstsecond")
    assert digest == hashlib.sha256(b"firstsecond").hexdigest()


def test_a_download_binds_publication_to_the_streamed_inode(monkeypatch, tmp_path):
    body = b"reviewed response bytes"
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")
    malicious = tmp_path / "swapped.bin"
    malicious.write_bytes(b"E" * len(body))
    real_link = webtools.os.link

    def swap_before_link(source, target, **kwargs):
        webtools.os.replace(malicious, source)
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(webtools.os, "link", swap_before_link)

    with pytest.raises(RuntimeError, match="staging path changed"):
        webtools.web_download(
            "https://example.test/checkpoint.zip",
            destination,
            max_bytes=len(body),
        )

    assert destination.read_bytes() == b"previous-good-checkpoint"
    assert list(tmp_path.glob(".checkpoint.zip.download-*")) == []


def test_a_download_rehashes_the_staged_inode_before_replacing_destination(
    monkeypatch, tmp_path
):
    body = b"reviewed response bytes"
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")
    real_link = webtools.os.link

    def mutate_after_link(source, target, **kwargs):
        real_link(source, target, **kwargs)
        Path(source).write_bytes(b"E" * len(body))

    monkeypatch.setattr(webtools.os, "link", mutate_after_link)

    with pytest.raises(RuntimeError, match="bytes changed before publication"):
        webtools.web_download(
            "https://example.test/checkpoint.zip",
            destination,
            max_bytes=len(body),
        )

    assert destination.read_bytes() == b"previous-good-checkpoint"
    assert list(tmp_path.glob(".checkpoint.zip.download-*")) == []


def test_a_download_falls_back_when_the_filesystem_has_no_hardlinks(
    monkeypatch, tmp_path
):
    body = b"complete response on a hardlink-free filesystem"
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")

    def unsupported_link(*_args, **_kwargs):
        raise OSError(webtools.errno.EOPNOTSUPP, "hard links are unavailable")

    monkeypatch.setattr(webtools.os, "link", unsupported_link)

    result = webtools.web_download(
        "https://example.test/checkpoint.zip",
        destination,
        max_bytes=len(body),
    )

    assert destination.read_bytes() == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert list(tmp_path.glob(".checkpoint.zip.download-*")) == []


def test_a_post_publish_interrupt_does_not_unlink_the_public_path(
    monkeypatch, tmp_path
):
    body = b"complete streamed response"
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")
    real_replace = webtools.os.replace

    def publish_then_interrupt(source, target):
        real_replace(source, target)
        if Path(target) == destination:
            raise KeyboardInterrupt

    monkeypatch.setattr(webtools.os, "replace", publish_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        webtools.web_download(
            "https://example.test/checkpoint.zip",
            destination,
            max_bytes=len(body),
        )

    # The call cannot know whether a concurrent process replaced this path
    # after publication. Leaving the published inode is safer than a
    # check-then-unlink rollback that can delete the concurrent file.
    assert destination.read_bytes() == body
    assert list(tmp_path.glob(".checkpoint.zip.download-*")) == []


def test_a_download_streams_through_the_optional_requests_transport(
    monkeypatch, tmp_path
):
    body = b"requests-checkpoint-chunk" * 10_000
    closed = []

    class _RequestsResponse:
        raw = _Response(body)
        is_redirect = False
        status_code = 200
        headers = {"Content-Type": "application/zip"}
        url = "https://example.test/checkpoint.zip"

        @staticmethod
        def raise_for_status():
            return None

        def close(self):
            closed.append(True)

    class _Requests:
        @staticmethod
        def request(*_args, **kwargs):
            assert kwargs["stream"] is True
            assert kwargs["allow_redirects"] is False
            return _RequestsResponse()

    monkeypatch.setitem(sys.modules, "requests", _Requests())
    monkeypatch.setattr(
        webtools,
        "_read_capped",
        lambda *_a, **_k: pytest.fail("download used the in-memory fetch collector"),
    )
    destination = tmp_path / "checkpoint.zip"

    result = webtools.web_download(
        "https://example.test/checkpoint.zip",
        destination,
        max_bytes=len(body),
    )

    assert destination.read_bytes() == body
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert closed == [True]


def test_a_cancelled_download_preserves_the_previous_file(monkeypatch, tmp_path):
    class _Interrupted(_Response):
        def __init__(self):
            super().__init__(b"")
            self.calls = 0

        def read(self, size=-1):  # noqa: ANN001
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise KeyboardInterrupt

    _stub_urlopen(monkeypatch, lambda _request: _Interrupted())
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")

    with pytest.raises(KeyboardInterrupt):
        webtools.web_download("https://example.test/checkpoint.zip", destination)

    assert destination.read_bytes() == b"previous-good-checkpoint"
    assert list(tmp_path.glob(".checkpoint.zip.*.part")) == []


def test_requests_http_error_preserves_the_previous_file(monkeypatch, tmp_path):
    closed = []

    class _ErrorResponse:
        raw = _Response(b"server error body")
        is_redirect = False
        status_code = 503
        headers = {"Content-Type": "text/plain"}
        url = "https://example.test/checkpoint.zip"

        @staticmethod
        def raise_for_status():
            raise RuntimeError("503 Service Unavailable")

        def close(self):
            closed.append(True)

    class _Requests:
        @staticmethod
        def request(*_args, **_kwargs):
            return _ErrorResponse()

    monkeypatch.setitem(sys.modules, "requests", _Requests())
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")

    with pytest.raises(RuntimeError, match="503 Service Unavailable"):
        webtools.web_download("https://example.test/checkpoint.zip", destination)

    assert destination.read_bytes() == b"previous-good-checkpoint"
    assert list(tmp_path.glob(".checkpoint.zip.*.part")) == []
    assert closed == [True]


def test_stdlib_http_error_preserves_the_previous_file(monkeypatch, tmp_path):
    import urllib.error

    def missing(request):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    _stub_urlopen(monkeypatch, missing)
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")

    with pytest.raises(urllib.error.HTTPError):
        webtools.web_download("https://example.test/checkpoint.zip", destination)

    assert destination.read_bytes() == b"previous-good-checkpoint"
    assert list(tmp_path.glob(".checkpoint.zip.*.part")) == []


def test_an_oversized_download_preserves_the_previous_file(monkeypatch, tmp_path):
    body = b"x" * 1001
    _stub_urlopen(monkeypatch, lambda _r: _Response(body))
    destination = tmp_path / "checkpoint.zip"
    destination.write_bytes(b"previous-good-checkpoint")

    with pytest.raises(webtools.ResponseTooLarge):
        webtools.web_download(
            "https://example.test/checkpoint.zip",
            destination,
            max_bytes=1000,
        )

    assert destination.read_bytes() == b"previous-good-checkpoint"
    assert list(tmp_path.glob(".checkpoint.zip.*.part")) == []


def test_an_oversized_download_is_a_soft_error_not_a_crash(monkeypatch, tmp_path):
    """The worker turns a single-key `{"error": ...}` into a RuntimeError the
    cell can catch. A traceback out of the dispatcher is not that contract."""

    class _Big(_Response):
        def __init__(self):
            super().__init__(b"")

        def read(self, size=-1):  # noqa: ANN001
            return b"x" * (size if size and size > 0 else 65536)

    _stub_urlopen(monkeypatch, lambda _request: _Big())
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
