"""Contracts that had frozen only a refusal.

Two halves of one defect. A route whose sole observed response is an error has
a published contract describing nothing a client depends on — and it does not
read as a gap, because an error *is* a contract and the coverage gate counts
it.

* Three routes answer a successful request with **bytes**: the notebook export,
  the Session package, and an artifact download. The parameterless sweep that
  produces both frozen artifacts has no session and no artifact to ask for, so
  each of them 404'd. Worse than uncovered: the four unimplemented verbs still
  supplied the dispatcher's 404, so `docs/response-contract.json` published a
  download endpoint as `kinds: ["json"], statuses: [404]` while the status, the
  content type, and the very fact that it is a download went unrecorded.

* `PATCH|POST|PUT /annotations/<id>` on an unknown id answered 404 with
  `{"annotation": null}` — the one refusal on this surface outside the
  PublicFailure envelope. No `error`, no stable `code`, no `request_id`, so
  `app.js`'s `api()`, which turns every non-2xx into an ApiError built from
  `j.error`, reported a failure that said nothing at all. That body was frozen
  into `docs/response-schemas.json` as the route's error contract.

Every status asserted below goes through `_route`, never a directly called
route method: a `GatewayError` raised out of a method call has already been
observed reaching HTTP as a 200 elsewhere in this server.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth, response_capture

CONTRACT_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "docs" / "response-contract.json"
)

#: The routes whose success is bytes, and the media types they serve it as.
#: Stated rather than derived, because the point of the check is that the
#: capture observed a *particular* download and not merely "something binary":
#: a notebook export that quietly started answering `application/json` would
#: still be binary-free and still pass a looser assertion.
DOWNLOADS: dict[str, set[str]] = {
    # No `language` is the zip bundle; a named language is one `.ipynb`. A
    # contract saying only "binary" cannot tell a client which it will get.
    r"/frames/([^/]+)/notebook/export": {
        "application/zip",
        "application/x-ipynb+json",
    },
    r"/frames/([^/]+)/session/export": {"application/vnd.openai4s.session+zip"},
    r"/artifacts/(.+)": {"application/octet-stream"},
}


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


class _Client:
    """Drives the real request path, not a route method."""

    def __init__(self, config, runner, data_dir: Path) -> None:
        self._handler = gateway_mod.make_handler(config, runner.hub, runner)
        self._token = local_auth.read_token(data_dir) or ""

    def request(self, method: str, path: str, body: dict | None = None):
        payload = json.dumps(body or {}).encode("utf-8") if body is not None else b""
        handler = object.__new__(self._handler)
        sent: dict = {}
        handler._send = lambda code, data, ctype, extra=None: sent.update(
            code=code, body=data, ctype=ctype
        )
        handler.command = method
        handler.path = f"/api/v1{path}"
        handler.rfile = io.BytesIO(payload)
        handler.headers = {
            "Content-Length": str(len(payload)),
            local_auth.TOKEN_HEADER: self._token,
        }
        handler._route(method)
        return sent["code"], json.loads(sent["body"].decode("utf-8"))


@pytest.fixture
def server(tmp_path):
    config = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    try:
        yield runner, _Client(config, runner, tmp_path)
    finally:
        runner.close()


@pytest.fixture(scope="module")
def driven(tmp_path_factory):
    """One drive of the whole surface, exactly as the capture scripts do it.

    Real Store, real handler, real routes: the claim these artifacts make is
    that they were captured from responses the code produced, so a stub here
    would let this file certify a download nothing serves.
    """
    tmp_path = tmp_path_factory.mktemp("downloads")
    config = Config(
        data_dir=tmp_path, llm=LLMConfig(provider="deepseek", api_key="test-key")
    )
    runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
    recorder = response_capture.Recorder()
    original = response_capture.install(gateway_mod, recorder)
    try:
        response_capture.drive_all_routes(
            recorder, gateway_mod.make_handler, config, runner
        )
    finally:
        gateway_mod.make_handler = original
    return recorder


def test_a_download_route_records_the_bytes_it_serves(driven):
    """The drive must reach each download's 200, not only its 404."""
    for route, content_types in DOWNLOADS.items():
        observed = driven.kinds.get(f"GET {route}")
        assert observed, f"GET {route} recorded nothing at all"
        assert response_capture.BINARY in observed["kinds"], (
            f"GET {route} serves bytes on success; the drive only saw "
            f"{sorted(observed['kinds'])}, so the published contract describes "
            f"how the route refuses and nothing else"
        )
        assert 200 in observed["statuses"], (
            f"GET {route} recorded statuses {sorted(observed['statuses'])} — no "
            f"success among them"
        )
        assert content_types <= observed["content_types"], (
            f"GET {route} recorded content types "
            f"{sorted(observed['content_types'])}, missing "
            f"{sorted(content_types - observed['content_types'])}"
        )


def test_the_seeded_drive_reports_a_failure_rather_than_skipping_it(driven):
    """A seeded probe that raised would silently restore the 404-only contract.

    The four unimplemented verbs keep answering, so a crashed GET leaves the
    route looking covered. That is the exact shape of the original defect, and
    it has to fail loudly rather than regress quietly.
    """
    seeded = {
        key: value for key, value in driven.drive_failures.items() if "(seeded)" in key
    }
    assert seeded == {}, f"the seeded download probes raised: {seeded}"


def test_the_frozen_contract_carries_what_the_download_routes_answered(driven):
    """The committed artifact is the deliverable, so it is checked against the
    same drive rather than trusted."""
    frozen = json.loads(CONTRACT_ARTIFACT.read_text("utf-8")).get("routes") or {}
    for route, content_types in DOWNLOADS.items():
        record = frozen.get(route)
        assert record, f"{route} has no entry in {CONTRACT_ARTIFACT.name}"
        assert response_capture.BINARY in record["kinds"], (
            f"{route} is frozen as {record['kinds']}; regenerate with "
            f"`uv run python scripts/capture_response_contract.py`"
        )
        assert 200 in record["statuses"]
        assert content_types <= set(record["content_types"])


def test_updating_an_unknown_annotation_answers_the_public_failure_envelope(server):
    _runner, client = server
    for method in ("PATCH", "POST", "PUT"):
        code, body = client.request(
            method, "/annotations/a-nothing-here", {"body": "revised"}
        )
        assert code == 404
        # The envelope, field by field. Asserting only on `error` would have
        # passed for a body that still dropped the machine-readable half.
        assert body["error"] == "annotation not found"
        assert body["code"] == "not_found"
        assert body["status"] == 404
        assert isinstance(body["request_id"], str) and body["request_id"]
        assert "annotation" not in body


def test_updating_a_real_annotation_still_answers_with_it(server):
    """The refusal changed; the success did not."""
    runner, client = server
    frame_id = runner.store.new_frame(
        kind="turn", project_id="annotations", status="ready"
    )
    annotation = runner.store.add_annotation(
        root_frame_id=frame_id,
        artifact_id="figure-a",
        artifact_name="figure-a.png",
        rel_x=0.25,
        rel_y=0.75,
        body="inspect this region",
        # Bound, as the creating route binds it. Leaving it unset would freeze
        # `version_id: null` into this route's success shape -- a property of
        # the fixture published as a property of the API, and one that
        # contradicts the sibling `POST /frames/<id>/annotations`, which types
        # the same field as a string.
        version_id="v-annotation-capture",
        checksum="0" * 64,
    )
    code, body = client.request(
        "PATCH",
        f"/annotations/{annotation['annotation_id']}",
        {"body": "revised", "status": "resolved"},
    )
    assert code == 200
    assert body["annotation"]["body"] == "revised"
    assert body["annotation"]["status"] == "resolved"
