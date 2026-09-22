"""Seams between the three W1 packages, which neither side's own tests cover.

W1-B's tests always hand ``JudgmentService`` a lambda and a fake backend.
W1-C's route test stubs ``probe_connection`` away. So the shapes they exchange
were never once exercised against each other, and the first two assertions
here both failed before the MERGE-W1 integration commit.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from typing import Any

import pytest

from openai4s.config import Config
from openai4s.host.judgment import JudgmentService
from openai4s.judgment.settings import probe_connection
from openai4s.store import get_store


def _cfg_and_store(tmp_path: pathlib.Path) -> tuple[Config, Any]:
    data_dir = tmp_path / "openai4s-data"
    data_dir.mkdir(exist_ok=True)
    cfg = Config(data_dir=data_dir)
    store = get_store(data_dir / "openai4s.db")
    return cfg, store


def test_probe_connection_accepts_the_route_s_store_instance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway hands over the request's Store, not a factory.

    Before the fix this raised ``TypeError: 'Store' object is not callable``
    inside the route, i.e. a 500 on POST /api/v1/experimental/judgment/test
    the moment the master switch was on.
    """

    monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
    cfg, store = _cfg_and_store(tmp_path)
    try:
        out = probe_connection(cfg, store)
    finally:
        store.close()
    assert set(out) == {"status", "error_code", "latency_ms", "model"}
    # No key configured, so the honest answer is unavailable -- not a crash,
    # and not an invented score.
    assert out["status"] == "unavailable"
    assert out["error_code"] == "unconfigured"


def test_store_provider_takes_an_instance_or_a_factory(
    tmp_path: pathlib.Path,
) -> None:
    cfg, store = _cfg_and_store(tmp_path)
    try:
        assert JudgmentService(cfg, store)._store() is store
        assert JudgmentService(cfg, lambda: store)._store() is store
        assert JudgmentService(cfg, None)._store() is None
    finally:
        store.close()


def test_a_fake_endpoint_answer_is_marked_as_fake_end_to_end(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store key -> TypeSafeBackend -> loopback fake -> service result.

    This is the whole W1 chain with nothing injected but the endpoint, and it
    pins the one thing that must never be lost on the way back: an answer that
    came from the loopback fake is not reported as a real backend answer.
    """

    from harness.providers.typesafe_fake import start_fake

    url, stop = start_fake(port=0)
    try:
        monkeypatch.setenv("OPENAI4S_EXPERIMENTAL_JUDGMENT", "1")
        monkeypatch.setenv("OPENAI4S_JUDGMENT_FAKE_ENDPOINT", url)
        monkeypatch.delenv("OPENAI4S_TYPESAFE_API_KEY", raising=False)
        cfg, store = _cfg_and_store(tmp_path)
        try:
            store.set_secret_setting(
                "typesafe_api_key", "fake-key-DO-NOT-LEAK-123", scope="judgment"
            )
            audited: list[dict[str, Any]] = []
            service = JudgmentService(cfg, store, audit_sink=audited.append)
            result = service.probe()
            payload = result.to_dict()
        finally:
            store.close()
    finally:
        stop()

    assert payload["status"] == "ok", payload
    assert payload["fake"] is True
    assert payload["answers"]["alive"]["kind"] == "noul"
    assert audited and audited[-1]["fake"] is True
    # The key travelled from the Store to the transport; it must not travel
    # back out through the result or the audit row.
    assert "fake-key-DO-NOT-LEAK-123" not in json.dumps(payload)
    assert "fake-key-DO-NOT-LEAK-123" not in json.dumps(audited, default=str)


def test_the_default_backend_is_the_typesafe_transport(
    tmp_path: pathlib.Path,
) -> None:
    from openai4s.judgment.typesafe import TypeSafeBackend

    cfg, store = _cfg_and_store(tmp_path)
    try:
        service = JudgmentService(cfg, store)
        backend = service._default_backend(service._flags())
        assert isinstance(backend, TypeSafeBackend)
    finally:
        store.close()
