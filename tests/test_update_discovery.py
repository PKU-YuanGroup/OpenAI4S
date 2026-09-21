"""Is there a newer release — and, much more often, does this module know.

The assertions that matter here are the negative ones. An updater that reports
"you are on the latest version" because DNS failed is worse than one that
reports nothing, because the user stops looking; so every failure this module
can produce is forced, individually, and each one is checked to render as
`unknown` rather than as `up_to_date`.

Every test is offline. The release source is a seam (`discovery._SOURCE`), and
the tests that replace it carry `stubbed_backend` for the reason CLAUDE.md
gives: `scripts/capture_response_schemas.py` re-runs this suite with a recorder
installed, and an unmarked stub publishes a fabricated shape as a route's
"captured from real responses" contract.
"""

from __future__ import annotations

import ast
import json
import os
import stat
import urllib.error
from pathlib import Path

import pytest

from openai4s import egress, webtools
from openai4s.update import UpdateError, UpdateRefusal, discovery, verify

VERSION = "0.4.0"
RUNNING = "0.3.0"
WHEEL = f"openai4s-{VERSION}-py3-none-any.whl"
SDIST = f"openai4s-{VERSION}.tar.gz"
BUNDLE = f"OpenAI4S-{VERSION}-linux-x86_64.tar.gz"

A = "a" * 64
B = "b" * 64
C = "c" * 64


class _Cfg:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


class _Source(discovery.ReleaseSource):
    """A scripted release source. Any member may be an exception to raise."""

    def __init__(self, *, project=None, release=None, sums=None):
        self._project = project if project is not None else _project_doc()
        self._release = release if release is not None else _release_doc()
        self._sums = sums if sums is not None else _sums_text()
        self.calls: list[str] = []

    @staticmethod
    def _answer(value):
        if isinstance(value, BaseException):
            raise value
        return value

    def project(self, index, *, timeout):
        self.calls.append("project")
        return self._answer(self._project)

    def release(self, index, version, *, timeout):
        self.calls.append("release")
        return self._answer(self._release)

    def sums(self, version, *, timeout):
        self.calls.append("sums")
        return self._answer(self._sums)


class _Exploding(discovery.ReleaseSource):
    """Any read at all fails the test."""

    def project(self, index, *, timeout):
        raise AssertionError("discovery dialled pypi.org")

    def release(self, index, version, *, timeout):
        raise AssertionError("discovery dialled pypi.org")

    def sums(self, version, *, timeout):
        raise AssertionError("discovery dialled github.com")


def _project_doc(version=VERSION, yanked=False):
    return {"info": {"name": "openai4s", "version": version, "yanked": yanked}}


def _release_doc(*, wheel_digest=A, sdist_digest=B, wheel_yanked=False, url=None):
    return {
        "urls": [
            {
                "filename": WHEEL,
                "size": 4_000_000,
                "digests": {"sha256": wheel_digest, "md5": "x" * 32},
                "packagetype": "bdist_wheel",
                "yanked": wheel_yanked,
                "url": url or f"https://files.pythonhosted.org/packages/ab/{WHEEL}",
            },
            {
                "filename": SDIST,
                "size": 3_000_000,
                "digests": {"sha256": sdist_digest},
                "packagetype": "sdist",
                "yanked": False,
                "url": f"https://files.pythonhosted.org/packages/cd/{SDIST}",
            },
        ]
    }


def _sums_text(wheel_digest=A, sdist_digest=B, bundle_digest=C):
    return (
        f"{wheel_digest}  {WHEEL}\n"
        f"{sdist_digest}  {SDIST}\n"
        f"{bundle_digest}  {BUNDLE}\n"
    )


@pytest.fixture
def online(monkeypatch):
    """Lift the suite-wide offline pin for the tests that drive the seam."""
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)

    def install(source: discovery.ReleaseSource) -> discovery.ReleaseSource:
        monkeypatch.setattr(discovery, "_SOURCE", source)
        return source

    return install


@pytest.fixture
def clock():
    """A caller-injectable clock, so freshness arithmetic never sleeps."""

    class _Clock:
        def __init__(self) -> None:
            self.now_ms = 1_700_000_000_000

        def __call__(self) -> int:
            return self.now_ms

        def advance(self, ms: int) -> None:
            self.now_ms += ms

    return _Clock()


# --------------------------------------------------------------------------- #
#  the frozen vocabularies
# --------------------------------------------------------------------------- #


def test_the_status_vocabulary_has_exactly_three_members():
    """A fourth would be somebody's idea of a partial success."""
    assert discovery.STATUSES == ("update_available", "up_to_date", "unknown")


def test_the_reason_vocabulary_is_frozen():
    """The CLI maps these to exit codes and the route puts them in a body, so
    this tuple is the contract and `detail` is prose nobody may parse."""
    assert discovery.REASONS == (
        "ok",
        "offline_pin",
        "unparsable_running",
        "bad_index",
        "network_disabled",
        "egress_blocked",
        "ssrf_blocked",
        "unreachable",
        "timeout",
        "http_error",
        "too_large",
        "malformed",
        "yanked",
        "witness_disagreement",
        "witness_missing",
        "unknown",
    )
    assert len(set(discovery.REASONS)) == len(discovery.REASONS)
    assert discovery.SOURCES == ("pypi", "offline", "none")


def test_every_refusal_code_discovery_can_forward_is_a_reason():
    """`classify` forwards an `UpdateRefusal`'s code straight through when it
    is a reason, so the two vocabularies must overlap where they meet."""
    forwarded = {"witness_disagreement", "witness_missing"}
    assert forwarded <= set(verify.REFUSAL_CODES)
    assert forwarded <= set(discovery.REASONS)


# --------------------------------------------------------------------------- #
#  no module here may name an outbound primitive
# --------------------------------------------------------------------------- #


def test_no_module_in_the_update_package_names_an_outbound_primitive():
    """The egress surface stays at thirteen by mechanism, not by intention.

    `tests/test_egress_surface.py` is the gate; this is the same question asked
    where a failure names the offending update module directly, so a future
    package does not have to work out why an unrelated test went red.
    """
    forbidden = {
        "urlopen",
        "urlretrieve",
        "build_opener",
        "install_opener",
        "Request",
        "HTTPConnection",
        "HTTPSConnection",
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
    }
    package = Path(discovery.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "urllib.request",
                "http.client",
                "socket",
            }:
                for alias in node.names:
                    if alias.name in forbidden:
                        offenders.append(f"{path.name}:{node.lineno} {alias.name}")
            if isinstance(node, ast.Attribute) and node.attr in forbidden:
                offenders.append(f"{path.name}:{node.lineno} .{node.attr}")
            if isinstance(node, ast.Name) and node.id in forbidden:
                offenders.append(f"{path.name}:{node.lineno} {node.id}")
    assert (
        offenders == []
    ), "openai4s/update/ must reach the network only through webtools:\n" + "\n".join(
        offenders
    )


def test_the_one_transport_this_package_uses_is_the_declared_one():
    assert callable(webtools.fetch_json)
    assert callable(webtools.web_download)


def test_fetch_json_decodes_an_object_and_refuses_anything_else(monkeypatch):
    """The one new public function in `webtools`, driven directly.

    A non-2xx is whatever `_open_http_response` raised for it rather than a
    status in the return value, because a caller that has to remember to check
    a status code is a caller that will parse an error page as data.
    """
    seen: dict = {}

    def fake_get(url, *, timeout=30.0, max_bytes=None, **_kwargs):
        seen.update(url=url, timeout=timeout, max_bytes=max_bytes)
        return seen["body"], url, "application/json"

    monkeypatch.setattr(webtools, "_http_get", fake_get)

    seen["body"] = b'{"info": {"version": "0.4.0"}}'
    assert webtools.fetch_json("https://pypi.org/x", timeout=3, max_bytes=99) == {
        "info": {"version": "0.4.0"}
    }
    assert seen["timeout"] == 3
    assert seen["max_bytes"] == 99

    for body in (b"[1, 2]", b'"a string"', b"null"):
        seen["body"] = body
        with pytest.raises(ValueError):
            webtools.fetch_json("https://pypi.org/x")

    seen["body"] = b"<html>502 Bad Gateway</html>"
    with pytest.raises(ValueError):
        webtools.fetch_json("https://pypi.org/x")


def test_fetch_json_lets_the_transports_own_failure_through(monkeypatch):
    def fake_get(*_args, **_kwargs):
        raise urllib.error.HTTPError("https://pypi.org/x", 503, "nope", {}, None)

    monkeypatch.setattr(webtools, "_http_get", fake_get)
    with pytest.raises(urllib.error.HTTPError):
        webtools.fetch_json("https://pypi.org/x")


def test_the_http_source_asks_webtools_for_exactly_the_two_documents(monkeypatch):
    """The path a stubbed `ReleaseSource` bypasses is the one that runs in
    production, so it is driven here with `webtools` replaced instead."""
    calls: list[tuple] = []

    def fake_fetch_json(url, *, timeout, max_bytes):
        calls.append(("json", url, timeout, max_bytes))
        return {"ok": True}

    monkeypatch.setattr(webtools, "fetch_json", fake_fetch_json)
    source = discovery.HttpSource()
    assert source.project("https://pypi.org", timeout=7.0) == {"ok": True}
    assert source.release("https://pypi.org", VERSION, timeout=7.0) == {"ok": True}
    assert calls == [
        ("json", "https://pypi.org/pypi/openai4s/json", 7.0, verify.MAX_JSON_BYTES),
        (
            "json",
            f"https://pypi.org/pypi/openai4s/{VERSION}/json",
            7.0,
            verify.MAX_JSON_BYTES,
        ),
    ]


def test_the_manifest_is_streamed_through_a_bounded_download(monkeypatch):
    """`web_download` rather than a text fetch: it is the read with a real
    ceiling, and a manifest is an untrusted release asset."""
    calls: list[tuple] = []

    def fake_download(url, destination, *, timeout, max_bytes):
        calls.append((url, timeout, max_bytes))
        Path(destination).write_text(f"{A}  {WHEEL}\n", encoding="utf-8")
        return {"path": str(destination)}

    monkeypatch.setattr(webtools, "web_download", fake_download)
    text = discovery.HttpSource().sums(VERSION, timeout=5.0)
    assert discovery.parse_sha256sums(text) == {WHEEL: A}
    assert calls == [
        (
            "https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/"
            f"v{VERSION}/SHA256SUMS",
            5.0,
            verify.MAX_MANIFEST_BYTES,
        )
    ]


def test_the_manifest_download_leaves_nothing_behind(monkeypatch, tmp_path):
    landed: list[Path] = []

    def fake_download(url, destination, *, timeout, max_bytes):
        landed.append(Path(destination).parent)
        Path(destination).write_text("", encoding="utf-8")
        return {}

    monkeypatch.setattr(webtools, "web_download", fake_download)
    discovery.HttpSource().sums(VERSION, timeout=1.0)
    assert landed and not landed[0].exists()


# --------------------------------------------------------------------------- #
#  versions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,parsed",
    [("0.3.0", (0, 3, 0)), ("1.0.0", (1, 0, 0)), ("10.2.30", (10, 2, 30))],
)
def test_a_version_parses_as_three_integers(text, parsed):
    assert discovery.parse_version(text) == parsed


@pytest.mark.parametrize(
    "text", ["0.3", "0.3.0rc1", "v0.3.0", "", None, "0.3.0\n", "0.3.0.post1"]
)
def test_anything_else_does_not_parse(text):
    assert discovery.parse_version(text) is None


def test_comparison_is_numeric_not_lexicographic():
    assert discovery.is_newer("0.10.0", "0.9.0") is True
    assert discovery.is_newer("0.9.0", "0.10.0") is False
    assert discovery.is_newer("1.0.0", "0.99.99") is True
    assert discovery.is_newer("0.3.0", "0.3.0") is False


def test_an_unparseable_operand_never_compares_as_newer():
    assert discovery.is_newer("0.4.0", "dev") is False
    assert discovery.is_newer("dev", "0.3.0") is False


# --------------------------------------------------------------------------- #
#  the offline pin
# --------------------------------------------------------------------------- #


def test_the_offline_pin_dials_nothing_at_all(monkeypatch, tmp_path):
    """The mechanism that keeps the whole suite and the CI response prober off
    the network. Not a convenience: `webtools` itself is replaced with
    something that fails the test, so "no request was made" is proved rather
    than inferred from a status string."""
    monkeypatch.setenv("OPENAI4S_UPDATE_SOURCE", "offline")
    monkeypatch.setattr(discovery, "_SOURCE", _Exploding())

    def refuse(*_args, **_kwargs):
        raise AssertionError("the offline pin made a request")

    monkeypatch.setattr(webtools, "fetch_json", refuse)
    monkeypatch.setattr(webtools, "web_download", refuse)
    monkeypatch.setattr(webtools, "_http_get", refuse)

    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "offline_pin"
    assert answer["source"] == "offline"
    assert answer["update_available"] is False
    assert answer["latest"] == ""


def test_the_offline_pin_also_forces_the_source_seam(monkeypatch):
    monkeypatch.setenv("OPENAI4S_UPDATE_SOURCE", "offline")
    assert discovery.offline_pinned() is True
    assert isinstance(discovery.resolved_source(), discovery.OfflineSource)
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE")
    assert discovery.offline_pinned() is False
    assert isinstance(discovery.resolved_source(), discovery.HttpSource)


def test_the_offline_pin_neither_reads_nor_writes_the_cache(monkeypatch, tmp_path):
    """Deterministic on every machine: a cached answer would make the pinned
    result depend on what this host happened to check yesterday."""
    monkeypatch.setenv("OPENAI4S_UPDATE_SOURCE", "offline")
    path = discovery.cache_path(_Cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            discovery._document(
                status="update_available",
                reason="ok",
                running=RUNNING,
                latest="9.9.9",
                checked_at_ms=1,
                retry_after_at=10**15,
            )
        ),
        encoding="utf-8",
    )
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == "offline_pin"
    assert answer["latest"] == ""
    # And the pinned answer did not overwrite what was there.
    assert json.loads(path.read_text("utf-8"))["latest"] == "9.9.9"


def test_the_suite_is_pinned_offline_by_conftest():
    """The pin is the suite's posture, not something each test sets."""
    assert os.environ.get("OPENAI4S_UPDATE_SOURCE") == "offline"


# --------------------------------------------------------------------------- #
#  the happy paths
# --------------------------------------------------------------------------- #


@pytest.mark.stubbed_backend
def test_a_newer_release_with_two_agreeing_witnesses(online, tmp_path):
    source = online(_Source())
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "update_available"
    assert answer["reason"] == "ok"
    assert answer["update_available"] is True
    assert answer["latest"] == VERSION
    assert answer["running"] == RUNNING
    assert answer["witnesses"]["single_witness"] is False
    assert answer["witnesses"]["compared"] == sorted([WHEEL, SDIST])
    assert "not established" in answer["detail"]
    assert source.calls == ["project", "release", "sums"]


@pytest.mark.stubbed_backend
def test_up_to_date_is_only_reached_by_actually_asking(online, tmp_path):
    online(_Source(project=_project_doc(version=RUNNING)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "up_to_date"
    assert answer["reason"] == "ok"
    assert answer["latest"] == RUNNING
    assert answer["update_available"] is False


@pytest.mark.stubbed_backend
def test_a_development_build_ahead_of_the_index_is_not_a_failure(online, tmp_path):
    online(_Source(project=_project_doc(version="0.2.0")))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "up_to_date"
    assert answer["latest"] == "0.2.0"


@pytest.mark.stubbed_backend
def test_the_per_version_document_is_not_fetched_when_nothing_is_newer(
    online, tmp_path
):
    source = online(_Source(project=_project_doc(version=RUNNING)))
    discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert source.calls == ["project"]


@pytest.mark.stubbed_backend
def test_the_assets_carry_only_a_download_url_from_a_host_we_decided(online, tmp_path):
    """A URL from a response body is accepted only when its host is one this
    module already chose. That is different from taking a host from the body,
    and the difference is the control."""
    online(_Source(release=_release_doc(url="https://evil.example/packages/" + WHEEL)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    by_name = {asset["filename"]: asset for asset in answer["assets"]}
    assert by_name[WHEEL]["url"] == ""
    assert by_name[SDIST]["url"].startswith("https://files.pythonhosted.org/")


@pytest.mark.parametrize(
    "url",
    [
        "http://files.pythonhosted.org/x.whl",
        "https://user:pw@files.pythonhosted.org/x.whl",
        "https://files.pythonhosted.org.evil.test/x.whl",
        "https://github.com/x.whl",
        "",
        None,
        123,
    ],
)
def test_an_asset_url_outside_the_decided_hosts_is_dropped(url):
    assert discovery._asset_url(url) == ""


# --------------------------------------------------------------------------- #
#  the witnesses
# --------------------------------------------------------------------------- #


@pytest.mark.stubbed_backend
def test_a_forged_manifest_is_refused_and_nothing_is_offered(online, tmp_path):
    """`SHA256SUMS` agrees on the wheel and forges the sdist. The wheel
    agreeing buys the sdist nothing, and the whole release is refused."""
    online(_Source(sums=_sums_text(sdist_digest=C)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "witness_disagreement"
    assert answer["update_available"] is False
    assert SDIST in answer["detail"]


@pytest.mark.stubbed_backend
def test_a_manifest_that_forges_the_wheel_is_refused(online, tmp_path):
    online(_Source(sums=_sums_text(wheel_digest=C)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == "witness_disagreement"


@pytest.mark.stubbed_backend
def test_an_unreachable_manifest_degrades_to_one_witness_and_says_so(online, tmp_path):
    """A *read* must still report a new version when github.com is down. It
    records the weaker trust line instead of pretending there were two."""
    online(_Source(sums=urllib.error.URLError("no route to host")))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "update_available"
    assert answer["witnesses"]["single_witness"] is True
    assert answer["witnesses"]["trust"]["integrity"] == verify.TRUST_INTEGRITY_SINGLE
    assert "one witness only" in answer["detail"]


@pytest.mark.stubbed_backend
def test_apply_grade_discovery_refuses_a_missing_witness(online, tmp_path):
    """`apply` passes allow_single_witness=False: there, a missing witness
    installs nothing."""
    online(_Source(sums=urllib.error.URLError("no route to host")))
    answer = discovery.check(
        _Cfg(tmp_path), running=RUNNING, allow_single_witness=False
    )
    assert answer["status"] == "unknown"
    assert answer["reason"] == "unreachable"


@pytest.mark.stubbed_backend
def test_a_manifest_that_does_not_cover_the_wheel_is_not_a_single_witness(
    online, tmp_path
):
    """Present but uncorroborating is not the same as absent."""
    online(_Source(sums=f"{C}  {BUNDLE}\n"))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "witness_missing"


@pytest.mark.stubbed_backend
def test_a_release_with_no_wheel_has_nothing_to_compare(online, tmp_path):
    online(
        _Source(
            release={
                "urls": [
                    {
                        "filename": SDIST,
                        "size": 1,
                        "digests": {"sha256": B},
                        "packagetype": "sdist",
                        "yanked": False,
                    }
                ]
            }
        )
    )
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "witness_missing"


# --------------------------------------------------------------------------- #
#  yanked releases
# --------------------------------------------------------------------------- #


@pytest.mark.stubbed_backend
def test_a_yanked_version_is_not_offered(online, tmp_path):
    online(_Source(project=_project_doc(yanked=True)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "yanked"
    assert answer["update_available"] is False


@pytest.mark.stubbed_backend
def test_a_version_whose_files_are_all_yanked_is_not_offered(online, tmp_path):
    online(
        _Source(
            release={
                "urls": [
                    {
                        "filename": WHEEL,
                        "size": 1,
                        "digests": {"sha256": A},
                        "packagetype": "bdist_wheel",
                        "yanked": True,
                    },
                    {
                        "filename": SDIST,
                        "size": 1,
                        "digests": {"sha256": B},
                        "packagetype": "sdist",
                        "yanked": "withdrawn for a bad build",
                    },
                ]
            }
        )
    )
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == "yanked"


@pytest.mark.stubbed_backend
def test_a_yanked_file_never_reaches_the_digest_map(online, tmp_path):
    """Dropped at the source rather than filtered by a caller: a withdrawn
    artifact must not be comparable at all."""
    online(_Source(release=_release_doc(wheel_yanked=True)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == "witness_missing"
    assert WHEEL not in json.dumps(answer["witnesses"])


def test_the_deprecated_releases_key_is_never_consulted():
    """PyPI deprecated it and the per-version document does not carry it."""
    source = Path(discovery.__file__).read_text("utf-8")
    assert '"releases"' not in source
    assert "'releases'" not in source


# --------------------------------------------------------------------------- #
#  every failure, forced
# --------------------------------------------------------------------------- #


class _Boom(Exception):
    pass


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://pypi.org/pypi/openai4s/json", code, "nope", {}, None  # type: ignore[arg-type]
    )


_FAILURES = [
    ("network_disabled", webtools.NetworkDisabled("networking is disabled")),
    ("egress_blocked", egress.EgressBlocked("pypi.org is not allowlisted")),
    ("ssrf_blocked", webtools.SSRFBlocked("resolves to a loopback address")),
    ("too_large", webtools.ResponseTooLarge("body exceeds 4194304 bytes")),
    ("timeout", TimeoutError("timed out")),
    ("timeout", urllib.error.URLError(TimeoutError("timed out"))),
    ("http_error", _http_error(503)),
    ("http_error", _http_error(429)),
    ("unreachable", urllib.error.URLError("nodename nor servname provided")),
    ("unreachable", ConnectionResetError("reset by peer")),
    ("malformed", ValueError("Expecting value: line 1 column 1")),
    ("malformed", KeyError("info")),
    ("unknown", _Boom("something nobody classified")),
]


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "reason,error", _FAILURES, ids=[f"{r}-{i}" for i, (r, _e) in enumerate(_FAILURES)]
)
def test_every_failure_is_named_and_none_of_them_says_up_to_date(
    online, tmp_path, reason, error
):
    online(_Source(project=error))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == reason
    assert answer["status"] == "unknown"
    assert answer["status"] != "up_to_date"
    assert answer["update_available"] is False
    assert answer["latest"] == ""
    assert answer["detail"]
    assert "\n" not in answer["detail"]


@pytest.mark.stubbed_backend
def test_an_http_error_names_its_status_in_the_detail(online, tmp_path):
    online(_Source(project=_http_error(503)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["detail"].startswith("HTTP 503")


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "document",
    [
        {},
        {"info": None},
        {"info": []},
        {"info": {"version": None}},
        {"info": {"version": "not-a-version"}},
        {"info": {"version": "0.4"}},
        "a string",
        [],
    ],
)
def test_a_malformed_project_document_is_malformed_not_up_to_date(
    online, tmp_path, document
):
    online(_Source(project=document))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "malformed"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "document", [{}, {"urls": None}, {"urls": {}}, "x", {"urls": []}]
)
def test_a_malformed_release_document_is_malformed(online, tmp_path, document):
    online(_Source(release=document))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "malformed"


@pytest.mark.stubbed_backend
def test_a_file_without_a_usable_digest_is_not_a_file_we_can_use(online, tmp_path):
    online(
        _Source(
            release={
                "urls": [
                    {
                        "filename": WHEEL,
                        "digests": {"sha256": A.upper()},
                        "packagetype": "bdist_wheel",
                    }
                ]
            }
        )
    )
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == "malformed"


def test_an_unparseable_running_version_abstains_without_dialling(
    monkeypatch, tmp_path, online
):
    """Comparing an unparseable running version against a release would produce
    an ordering nobody can defend."""
    online(_Exploding())
    answer = discovery.check(_Cfg(tmp_path), running="0.4.0.dev1+g1234")
    assert answer["status"] == "unknown"
    assert answer["reason"] == "unparsable_running"
    assert answer["update_available"] is False
    assert answer["source"] == "none"


def test_every_reason_except_ok_resolves_to_unknown():
    """The property stated once over the whole vocabulary, rather than only
    over the failures a test happened to construct."""
    for reason in discovery.REASONS:
        if reason == "ok":
            continue
        document = discovery._document(
            status="unknown",
            reason=reason,
            running=RUNNING,
            checked_at_ms=0,
            retry_after_at=0,
        )
        assert document["status"] == "unknown"
        assert document["update_available"] is False


def test_the_document_builder_refuses_a_vocabulary_it_does_not_know():
    for kwargs in (
        {"status": "probably", "reason": "ok"},
        {"status": "unknown", "reason": "because"},
        {"status": "unknown", "reason": "ok", "source": "somewhere"},
    ):
        with pytest.raises(UpdateError):
            discovery._document(
                running=RUNNING, checked_at_ms=0, retry_after_at=0, **kwargs
            )


# --------------------------------------------------------------------------- #
#  the index
# --------------------------------------------------------------------------- #


def test_the_default_index_is_pypi(monkeypatch):
    monkeypatch.delenv("OPENAI4S_UPDATE_INDEX", raising=False)
    assert discovery.resolved_index() == "https://pypi.org"


def test_a_mirror_may_be_configured(monkeypatch):
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", "https://mirror.example.org/simple/")
    assert discovery.resolved_index() == "https://mirror.example.org/simple"


@pytest.mark.parametrize(
    "value",
    [
        "http://pypi.org",
        "ftp://pypi.org",
        "https://user:token@pypi.org",
        "https://pypi.org?token=x",
        "https://pypi.org#frag",
        "https:///nohost",
        "pypi.org",
    ],
)
def test_an_index_is_validated_exactly_as_strictly_as_the_default(monkeypatch, value):
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", value)
    with pytest.raises(UpdateError):
        discovery.resolved_index()


def test_a_refused_index_is_a_named_reason_not_a_traceback(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", "http://pypi.org")
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    monkeypatch.setattr(discovery, "_SOURCE", _Exploding())
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "bad_index"
    assert answer["source"] == "none"


@pytest.mark.stubbed_backend
def test_the_resolved_index_is_echoed_in_every_payload(online, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", "https://mirror.example.org")
    online(_Source())
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["index"] == "https://mirror.example.org"


def test_the_urls_are_templates_over_a_validated_index():
    assert (
        discovery._PYPI_PROJECT_URL.format(index="https://pypi.org", project="openai4s")
        == "https://pypi.org/pypi/openai4s/json"
    )
    assert (
        discovery._PYPI_VERSION_URL.format(
            index="https://pypi.org", project="openai4s", version=VERSION
        )
        == f"https://pypi.org/pypi/openai4s/{VERSION}/json"
    )
    assert discovery._RELEASE_SUMS_URL.format(version=VERSION) == (
        "https://github.com/PKU-YuanGroup/OpenAI4S/releases/download/"
        f"v{VERSION}/SHA256SUMS"
    )


# --------------------------------------------------------------------------- #
#  SHA256SUMS parsing
# --------------------------------------------------------------------------- #


def test_both_coreutils_formats_parse():
    text = f"{A}  {WHEEL}\n{B} *{SDIST}\n"
    assert discovery.parse_sha256sums(text) == {WHEEL: A, SDIST: B}


def test_blank_lines_and_comments_are_skipped():
    text = f"# generated by release.yml\n\n{A}  {WHEEL}\n\n"
    assert discovery.parse_sha256sums(text) == {WHEEL: A}


@pytest.mark.parametrize(
    "line",
    [
        f"{'a' * 63}  {WHEEL}",
        f"{'a' * 65}  {WHEEL}",
        f"{'A' * 64}  {WHEEL}",
        f"{'g' * 64}  {WHEEL}",
        f"{A}{WHEEL}",
        WHEEL,
        f"{A}  ",
    ],
)
def test_a_line_outside_the_digest_grammar_is_dropped(line):
    """Dropping is the safe direction: the name the manifest then fails to
    describe becomes a `witness_missing` refusal rather than a loose compare."""
    assert discovery.parse_sha256sums(line + "\n") == {}


@pytest.mark.parametrize("name", ["../../etc/passwd", "a/b.whl", "..", ".", "a\\b"])
def test_a_manifest_entry_naming_a_path_is_dropped(name):
    assert discovery.parse_sha256sums(f"{A}  {name}\n") == {}


def test_the_first_line_for_a_name_wins():
    text = f"{A}  {WHEEL}\n{C}  {WHEEL}\n"
    assert discovery.parse_sha256sums(text) == {WHEEL: A}


def test_a_manifest_that_is_not_text_is_empty_not_an_exception():
    assert discovery.parse_sha256sums(None) == {}
    assert discovery.parse_sha256sums("") == {}


# --------------------------------------------------------------------------- #
#  the cache
# --------------------------------------------------------------------------- #


@pytest.mark.stubbed_backend
def test_a_miss_reads_live_and_a_hit_does_not(online, tmp_path, clock):
    source = online(_Source())
    first = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert first["cached"] is False
    assert source.calls == ["project", "release", "sums"]

    clock.advance(60_000)
    second = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert second["cached"] is True
    assert second["age_s"] == 60
    assert second["latest"] == VERSION
    assert source.calls == ["project", "release", "sums"]


@pytest.mark.stubbed_backend
def test_a_successful_answer_expires_after_six_hours(online, tmp_path, clock):
    source = online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    clock.advance(discovery.SUCCESS_TTL_MS - 1)
    assert discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)["cached"]
    clock.advance(2)
    assert not discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)["cached"]
    assert source.calls.count("project") == 2


@pytest.mark.stubbed_backend
def test_a_failure_is_retried_much_sooner_than_a_success(online, tmp_path, clock):
    """Caching "the network was down" for six hours turns one blip into an
    afternoon of silence."""
    source = online(_Source(project=urllib.error.URLError("down")))
    first = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert first["reason"] == "unreachable"
    assert (
        first["retry_after_at"] - first["checked_at_ms"] == discovery.FAILURE_RETRY_MS
    )

    clock.advance(discovery.FAILURE_RETRY_MS + 1)
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert source.calls.count("project") == 2


@pytest.mark.stubbed_backend
def test_refresh_bypasses_a_fresh_cache(online, tmp_path, clock):
    source = online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    again = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock, refresh=True)
    assert again["cached"] is False
    assert source.calls.count("project") == 2


@pytest.mark.stubbed_backend
def test_an_upgrade_invalidates_the_cache_it_left_behind(online, tmp_path, clock):
    """After an upgrade, "an update is available" describes the version you
    just left."""
    source = online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    answer = discovery.check(_Cfg(tmp_path), running=VERSION, clock=clock)
    assert answer["cached"] is False
    assert source.calls.count("project") == 2


@pytest.mark.stubbed_backend
def test_repointing_the_index_invalidates_the_cache(
    online, tmp_path, clock, monkeypatch
):
    source = online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", "https://mirror.example.org")
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert answer["cached"] is False
    assert source.calls.count("project") == 2


@pytest.mark.stubbed_backend
def test_a_clock_that_moved_backwards_is_not_a_fresh_cache(online, tmp_path, clock):
    source = online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    clock.now_ms -= 10_000
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert answer["cached"] is False
    assert source.calls.count("project") == 2


@pytest.mark.stubbed_backend
def test_the_cache_lands_at_the_documented_path_and_is_owner_only(
    online, tmp_path, clock
):
    online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    path = tmp_path / "updates" / "check.json"
    assert path.is_file()
    stored = json.loads(path.read_text("utf-8"))
    assert set(stored) >= {
        "checked_at_ms",
        "source",
        "latest",
        "running",
        "status",
        "detail",
        "assets",
        "witnesses",
        "retry_after_at",
    }
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Published atomically: no staging file is left behind.
    assert sorted(p.name for p in path.parent.iterdir()) == ["check.json"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_the_cache_is_already_owner_only_when_it_is_published(monkeypatch, tmp_path):
    """0600 on the finished file is the easy half; the window is the other one.

    `open(..., "w")` creates at `0o666 & ~umask`, so the staging file is
    world-readable for as long as it takes to harden it. Hardening *after* the
    `os.replace` would leave `check.json` itself readable for that window, and
    a test that only reads the mode at the end cannot tell the two orderings
    apart — it passes either way. So the mode is sampled at the instant of the
    replace, which is the only moment the answer differs.
    """
    path = tmp_path / "updates" / "check.json"
    seen: list[int] = []
    real_replace = os.replace

    def sampling_replace(src, dst, *args, **kwargs):
        seen.append(stat.S_IMODE(os.stat(src).st_mode))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(discovery.os, "replace", sampling_replace)
    assert discovery.write_cache(path, {"status": "up_to_date"}) is True
    assert seen == [0o600], f"the staged cache was mode {[oct(m) for m in seen]}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.stubbed_backend
def test_a_failure_carries_the_last_answer_that_was_an_answer(online, tmp_path, clock):
    """A failure has to overwrite the cache — `retry_after_at` is what stops a
    broken network being re-dialled every time — so the last known answer rides
    along inside it, or the surfaces lose the line they are documented to
    print: "last known 0.4.0, checked three hours ago"."""
    source = online(_Source())
    known = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert known["previous"] == {}

    clock.advance(3 * 60 * 60 * 1000)
    source._project = urllib.error.URLError("down")
    failed = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock, refresh=True)
    assert failed["reason"] == "unreachable"
    assert failed["previous"] == {
        "status": "update_available",
        "latest": VERSION,
        "checked_at_ms": known["checked_at_ms"],
    }

    # And it survives a second failure rather than being lost one hop later.
    clock.advance(discovery.FAILURE_RETRY_MS + 1)
    again = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert again["previous"]["latest"] == VERSION


@pytest.mark.stubbed_backend
def test_a_last_known_answer_for_another_version_is_not_carried(
    online, tmp_path, clock
):
    source = online(_Source())
    discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    source._project = urllib.error.URLError("down")
    failed = discovery.check(_Cfg(tmp_path), running=VERSION, clock=clock)
    assert failed["previous"] == {}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        '{"status": "definitely", "reason": "ok", "source": "pypi"}',
        '{"status": "up_to_date", "reason": "because", "source": "pypi"}',
        '{"status": "up_to_date", "reason": "ok", "source": "somewhere"}',
        '{"status": "up_to_date", "reason": "ok"}',
        '{"status": "up_to_date"}',
    ],
)
def test_an_unreadable_or_unknown_cache_is_treated_as_absent(tmp_path, payload):
    """A hand-edited or half-written cache must not introduce a state the
    surfaces have never seen."""
    path = tmp_path / "check.json"
    path.write_text(payload, encoding="utf-8")
    assert discovery.read_cache(path) is None


def test_a_missing_cache_is_absent_not_an_error(tmp_path):
    assert discovery.read_cache(tmp_path / "nope.json") is None


def test_a_well_formed_cache_is_read(tmp_path):
    """The companion of the refusals above: the validation must not have made
    `read_cache` answer None for everything."""
    path = tmp_path / "check.json"
    document = discovery._document(
        status="up_to_date",
        reason="ok",
        running=RUNNING,
        latest=RUNNING,
        checked_at_ms=1,
        retry_after_at=2,
    )
    path.write_text(json.dumps(document), encoding="utf-8")
    assert discovery.read_cache(path) == document


@pytest.mark.stubbed_backend
def test_a_cache_written_by_an_older_build_still_answers_the_current_shape(
    online, tmp_path, clock
):
    """A hit is rebuilt through the document builder, so a stored file missing
    a key the shape promises cannot hand a caller a KeyError."""
    online(_Exploding())
    path = discovery.cache_path(_Cfg(tmp_path))
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "status": "up_to_date",
                "reason": "ok",
                "source": "pypi",
                "running": RUNNING,
                "index": discovery.DEFAULT_INDEX,
                "latest": RUNNING,
                "checked_at_ms": clock.now_ms,
                "retry_after_at": clock.now_ms + 1000,
            }
        ),
        encoding="utf-8",
    )
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert answer["cached"] is True
    assert answer["previous"] == {}
    assert answer["assets"] == []
    assert answer["witnesses"] == {}
    assert answer["update_available"] is False


def test_writing_a_cache_is_best_effort(tmp_path):
    """A full disk must not fail a check that otherwise succeeded."""
    blocked = tmp_path / "file-in-the-way"
    blocked.write_text("x", encoding="utf-8")
    assert discovery.write_cache(blocked / "updates" / "check.json", {}) is False


def test_the_cache_path_is_derived_from_the_config(tmp_path):
    assert discovery.cache_path(_Cfg(tmp_path)) == tmp_path / "updates" / "check.json"


def test_the_cache_path_falls_back_to_the_configured_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "elsewhere"))
    assert discovery.cache_path() == tmp_path / "elsewhere" / "updates" / "check.json"


# --------------------------------------------------------------------------- #
#  classification, directly
# --------------------------------------------------------------------------- #


def test_a_refusal_code_outside_the_reason_vocabulary_becomes_unknown():
    reason, detail = discovery.classify(UpdateRefusal("probe_timeout", "slow"))
    assert reason == "unknown"
    assert "probe_timeout" in detail


def test_a_timeout_wrapped_in_a_urlerror_is_a_timeout_not_a_dns_problem():
    """`URLError` carries the real failure in `reason`; classifying on the
    outermost class alone sends the operator to look at DNS."""
    reason, _detail = discovery.classify(urllib.error.URLError(TimeoutError("slow")))
    assert reason == "timeout"


def test_a_cyclic_exception_chain_does_not_hang_the_classifier():
    left = ValueError("left")
    right = ValueError("right")
    left.__cause__ = right
    right.__cause__ = left
    assert discovery.classify(left)[0] == "malformed"


def test_a_detail_is_one_line_and_bounded():
    reason, detail = discovery.classify(_Boom("x\ny\n" + "z" * 1000))
    assert reason == "unknown"
    assert "\n" not in detail
    assert len(detail) <= 240


@pytest.mark.stubbed_backend
@pytest.mark.parametrize("first_sums", [_sums_text(), urllib.error.URLError("down")])
def test_apply_grade_check_rechecks_witnesses_after_a_cached_read(
    online, tmp_path, clock, first_sums
):
    source = online(_Source(sums=first_sums))
    first = discovery.check(_Cfg(tmp_path), running=RUNNING, clock=clock)
    assert first["status"] == "update_available"
    source._sums = urllib.error.URLError("still down")
    strict = discovery.check(
        _Cfg(tmp_path), running=RUNNING, clock=clock, allow_single_witness=False
    )
    assert strict["cached"] is False
    assert strict["status"] == "unknown"
    assert strict["reason"] == "unreachable"
    assert source.calls.count("sums") == 2


def test_cache_publication_does_not_follow_a_leftover_temporary_symlink(tmp_path):
    path = tmp_path / "updates" / "check.json"
    path.parent.mkdir()
    victim = tmp_path / "operator-data"
    victim.write_text("keep me")
    path.with_suffix(".json.tmp").symlink_to(victim)
    assert discovery.write_cache(path, {"status": "unknown"})
    assert victim.read_text() == "keep me"
    assert json.loads(path.read_text()) == {"status": "unknown"}


def test_simultaneous_cache_writers_publish_complete_documents(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    ready = Barrier(2)
    original = discovery.json.dump

    def together(*args, **kwargs):
        ready.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(discovery.json, "dump", together)
    path = tmp_path / "updates" / "check.json"
    documents = [{"detail": "a" * 100}, {"detail": "b" * 10000}]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda doc: discovery.write_cache(path, doc), documents)
        )
    assert results == [True, True]
    assert json.loads(path.read_text()) in documents


@pytest.mark.parametrize(
    "index", ["https://[", "https://example.org:bad", "https://example.org:99999"]
)
def test_malformed_index_authorities_are_named_refusals(index, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", index)
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "bad_index"


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "version", ["9" * 5000 + ".0.0", "١.٢.٣"], ids=["oversized", "unicode"]
)
def test_hostile_release_versions_are_malformed_not_tracebacks(
    online, tmp_path, version
):
    online(_Source(project=_project_doc(version)))
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["status"] == "unknown"
    assert answer["reason"] == "malformed"


@pytest.mark.parametrize(
    "index",
    [
        "http://user:synthetic-password@example.org",
        "https://example.org?token=synthetic-password",
        "https://example.org#synthetic-password",
    ],
)
def test_bad_index_errors_do_not_echo_credentials(index, monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI4S_UPDATE_SOURCE", raising=False)
    monkeypatch.setenv("OPENAI4S_UPDATE_INDEX", index)
    answer = discovery.check(_Cfg(tmp_path), running=RUNNING)
    assert answer["reason"] == "bad_index"
    assert "synthetic-password" not in json.dumps(answer)
