"""Direct contracts for host delegation and steering."""

from __future__ import annotations

import pytest

from openai4s.host.delegation import DelegationService


class FakeStore:
    def __init__(self, profiles=None, error: Exception | None = None):
        self.profiles = profiles or {}
        self.error = error
        self.calls = []

    def get_agent(self, name):
        self.calls.append(name)
        if self.error:
            raise self.error
        return self.profiles.get(name)


def test_delegate_injects_profile_into_string_without_mutating_input():
    submitted = []
    store = FakeStore({"CHEMIST": {"system_prompt": "Think like a chemist."}})
    service = DelegationService(
        delegate=lambda spec: submitted.append(spec) or {"child": "c1"},
        steering={},
        store=store,
    )
    original = {
        "specialist": "CHEMIST",
        "request": "Analyze the sample.",
        "context": {"x": 1},
    }

    assert service.delegate(original) == {"child": "c1"}
    assert original["request"] == "Analyze the sample."
    assert submitted[0]["request"] == (
        "You are acting as the specialist **CHEMIST**.\n"
        "Think like a chemist.\n\nAnalyze the sample."
    )
    assert submitted[0]["context"] is original["context"]


def test_delegate_profile_overrides_fill_defaults_without_overwriting_call_site():
    submitted = []
    store = FakeStore(
        {
            "SCOUT": {
                "system_prompt": "Scout carefully.",
                "model": "profile-model",
                "steps": 6,
                "permissions": {"write_file": "deny"},
                "capabilities": ["web", "read_file"],
                "skill_names": ["literature-review"],
                "connectors": ["crossref"],
                "unrestricted": False,
            }
        }
    )
    service = DelegationService(
        delegate=lambda spec: submitted.append(spec) or "ok",
        steering={},
        store=store,
    )

    original = {
        "name": "SCOUT",
        "request": "Find sources.",
        "model": "call-site-model",
    }
    assert service.delegate(original) == "ok"

    sent = submitted[0]
    assert sent["model"] == "call-site-model"
    assert sent["steps"] == 6
    assert sent["permissions"] == {"write_file": "deny"}
    assert sent["capabilities"] == ["web", "read_file"]
    assert sent["skill_names"] == ["literature-review"]
    assert sent["connectors"] == ["crossref"]
    assert sent["unrestricted"] is False
    assert original == {
        "name": "SCOUT",
        "request": "Find sources.",
        "model": "call-site-model",
    }


def test_delegate_injects_nested_request_and_uses_builtin_on_store_failure():
    submitted = []
    store = FakeStore(error=RuntimeError("database unavailable"))
    service = DelegationService(
        delegate=lambda spec: submitted.append(spec),
        steering={},
        store=store,
    )
    request = {
        "name": "remote_gpu_provisioner",
        "request": {"request": "Set up folding.", "output_schema": {"ok": "bool"}},
    }

    service.delegate(request)

    nested = submitted[0]["request"]
    assert "remote-GPU provisioning specialist" in nested["request"]
    assert nested["request"].endswith("Set up folding.")
    assert nested["output_schema"] == {"ok": "bool"}
    assert request["request"]["request"] == "Set up folding."


def test_delegate_and_steering_sources_are_resolved_at_call_time():
    calls = []
    state = {"delegate": None, "steering": {}, "store": FakeStore()}
    service = DelegationService(
        delegate_provider=lambda: state["delegate"],
        steering=lambda: state["steering"],
        store=lambda: state["store"],
    )

    assert service.available() is False
    state["delegate"] = lambda spec: calls.append(("delegate", spec)) or "ok"
    state["steering"] = {
        "children": lambda: [{"id": "c1"}],
        "collect": lambda spec: calls.append(("collect", spec)) or [1],
        "stop_child": lambda child_id: calls.append(("stop", child_id)) or True,
        "send_message": lambda spec: calls.append(("send", spec)) or None,
        "delegation_stats": lambda: {"total": 1},
    }

    assert service.available() is True
    assert service.delegate({"request": "work"}) == "ok"
    assert service.children() == [{"id": "c1"}]
    assert service.collect({"ids": ["c1"]}) == [1]
    assert service.stop_child("c1") is True
    assert service.send_message({"child_id": "c1", "message": "hi"}) is None
    assert service.stats() == {"total": 1}


def test_missing_delegate_and_steering_keep_legacy_errors_and_defaults():
    service = DelegationService(delegate=None, steering={}, store=FakeStore())

    with pytest.raises(RuntimeError, match="no sub-agent runner wired"):
        service.delegate({})
    with pytest.raises(RuntimeError, match="collect not available"):
        service.collect({})
    with pytest.raises(RuntimeError, match="stop_child not available"):
        service.stop_child("c1")
    with pytest.raises(RuntimeError, match="send_message not available"):
        service.send_message({})
    assert service.children() == []
    assert service.stats() == {
        "total": 0,
        "running": 0,
        "done": 0,
        "failed": 0,
    }
