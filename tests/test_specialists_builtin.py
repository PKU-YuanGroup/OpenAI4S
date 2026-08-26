"""D7: built-in specialist profiles — one source of truth, really armed.

The UI catalog advertised SCIENTIST/EXPLORE/GENERAL/PLAN/REVIEWER as full
specialists while the runtime resolver knew only REMOTE_GPU_PROVISIONER's
prompt: delegating to a catalog name produced a generic child and the
catalog's ``unrestricted: False`` was never enforced. ``openai4s/specialists``
now owns both the roster the gateway serves and the personas/policies the
delegation runtime applies.
"""

from __future__ import annotations

import pytest

from openai4s.host.delegation import BUILTIN_SPECIALIST_PROMPTS, DelegationService
from openai4s.host.delegation_policy import child_execution_policy
from openai4s.specialists import (
    BUILTIN_SPECIALISTS,
    SpecialistProfile,
    builtin_catalog,
    builtin_specialist,
)


class _EmptyStore:
    def get_agent(self, name, **kwargs):
        return None


class _RowStore:
    def __init__(self, row):
        self.row = row

    def get_agent(self, name, **kwargs):
        return self.row


def _service(store):
    sent: list[dict] = []
    service = DelegationService(
        delegate=lambda spec: (sent.append(spec), {"ok": True})[1],
        steering={},
        store=store,
    )
    return service, sent


# --------------------------------------------------------------------------
# the module is the single source of truth
# --------------------------------------------------------------------------


def test_every_builtin_has_a_real_persona_and_the_expected_roster():
    assert list(BUILTIN_SPECIALISTS) == [
        "SCIENTIST",
        "EXPLORE",
        "GENERAL",
        "REMOTE_GPU_PROVISIONER",
        "PLAN",
        "REVIEWER",
    ]
    for name, profile in BUILTIN_SPECIALISTS.items():
        assert isinstance(profile, SpecialistProfile)
        assert profile.name == name
        assert len(profile.system_prompt.strip()) > 80, f"{name} has no real persona"
        assert profile.description.strip()


def test_catalog_and_runtime_rosters_are_the_same_object_graph():
    """The consistency claim: every catalog entry resolves to a runtime
    profile with a non-empty persona, and vice versa — including the gateway's
    served list, which must derive from this module rather than duplicate it."""
    from openai4s.server import gateway as gateway_mod

    catalog = builtin_catalog()
    assert gateway_mod._BUILTIN_AGENTS == catalog
    catalog_names = [entry["name"] for entry in catalog]
    assert catalog_names == list(BUILTIN_SPECIALISTS)
    for entry in catalog:
        profile = builtin_specialist(entry["name"])
        assert profile is not None
        assert profile.system_prompt.strip()
        # The catalog's advertised keys/types stay exactly what the frozen
        # /agents payload always carried.
        assert set(entry) == {
            "name",
            "mode",
            "healthy",
            "source",
            "supportsPlanMode",
            "unrestricted",
            "description",
        }
        assert entry["healthy"] is True
        assert entry["source"] == "bundled"
        assert entry["unrestricted"] == profile.unrestricted
        assert entry["description"] == profile.description
    # And every runtime persona is reachable through the compat prompt map.
    assert set(BUILTIN_SPECIALIST_PROMPTS) == set(BUILTIN_SPECIALISTS)
    for name, prompt in BUILTIN_SPECIALIST_PROMPTS.items():
        assert prompt == BUILTIN_SPECIALISTS[name].system_prompt


def test_builtin_specialist_lookup_is_case_insensitive():
    assert builtin_specialist("explore") is BUILTIN_SPECIALISTS["EXPLORE"]
    assert builtin_specialist("Remote_Gpu_Provisioner") is not None
    assert builtin_specialist("NOSUCH") is None


def test_restricted_builtins_derive_a_fail_closed_execution_policy():
    for name in ("EXPLORE", "PLAN", "REVIEWER"):
        profile = BUILTIN_SPECIALISTS[name]
        assert profile.unrestricted is False
        overrides = profile.profile_overrides()
        assert overrides["unrestricted"] is False
        assert overrides["capabilities"], f"{name} restricted without capabilities"
        policy = child_execution_policy(overrides)
        assert policy.restricted is True
        # Read-only by construction: none of the restricted builtins may
        # write workspace files or run shell.
        assert not policy.allows("write_file")
        assert not policy.allows("authorize_bash")
        assert policy.allows("read_file")


def test_unrestricted_builtins_inject_no_restriction_keys():
    """SCIENTIST/GENERAL/REMOTE_GPU_PROVISIONER add persona only: injecting
    ``unrestricted: True`` would make a restricted parent's delegation to
    GENERAL a hard policy error instead of a ceiling-narrowed child."""
    for name in ("SCIENTIST", "GENERAL", "REMOTE_GPU_PROVISIONER"):
        overrides = BUILTIN_SPECIALISTS[name].profile_overrides()
        assert "unrestricted" not in overrides
        assert "capabilities" not in overrides


def test_profile_overrides_carries_an_optional_max_turns():
    profile = SpecialistProfile(
        name="X",
        mode="subagent",
        description="d",
        system_prompt="persona " * 20,
        unrestricted=True,
        max_turns=9,
    )
    assert profile.profile_overrides() == {"max_turns": 9}


# --------------------------------------------------------------------------
# runtime resolution through DelegationService
# --------------------------------------------------------------------------


def test_delegating_to_a_builtin_injects_persona_and_policy():
    service, sent = _service(_EmptyStore())
    service.delegate({"request": "map the repo", "name": "EXPLORE"})

    spec = sent[0]
    assert "You are acting as the specialist **EXPLORE**." in spec["request"]
    assert BUILTIN_SPECIALISTS["EXPLORE"].system_prompt in spec["request"]
    assert spec["unrestricted"] is False
    assert spec["capabilities"] == list(
        BUILTIN_SPECIALISTS["EXPLORE"].profile_overrides()["capabilities"]
    )


def test_call_site_settings_still_win_over_builtin_defaults():
    service, sent = _service(_EmptyStore())
    service.delegate(
        {
            "request": "scout",
            "name": "EXPLORE",
            "capabilities": ["read_file"],
            "unrestricted": True,  # floor: the builtin's False cannot be raised
        }
    )
    spec = sent[0]
    assert spec["capabilities"] == ["read_file"]
    assert spec["unrestricted"] is False


def test_a_stored_row_with_the_same_name_overrides_the_builtin():
    row = {
        "name": "EXPLORE",
        "system_prompt": "You are the user's own explorer.",
        "skill_names": ["literature-review"],
        "unrestricted": True,
    }
    service, sent = _service(_RowStore(row))
    service.delegate({"request": "scout", "name": "EXPLORE"})

    spec = sent[0]
    assert "You are the user's own explorer." in spec["request"]
    assert BUILTIN_SPECIALISTS["EXPLORE"].system_prompt not in spec["request"]
    # The row's overrides applied; the builtin's restriction keys did not.
    assert spec["skill_names"] == ["literature-review"]
    assert "capabilities" not in spec
    assert spec.get("unrestricted") is True


def test_general_and_scientist_get_persona_without_restrictions():
    for name in ("GENERAL", "SCIENTIST"):
        service, sent = _service(_EmptyStore())
        service.delegate({"request": "solve it", "name": name})
        spec = sent[0]
        assert BUILTIN_SPECIALISTS[name].system_prompt in spec["request"]
        assert "capabilities" not in spec
        assert "unrestricted" not in spec


def test_builtin_policy_is_armed_on_the_real_child_dispatcher(monkeypatch):
    """End to end through the existing choke point: DelegationService injects
    the EXPLORE profile into the spec, `_run_one` derives the policy and arms
    it via set_child_execution_policy, and the child's own dispatcher then
    refuses a write."""
    import openai4s.agent.loop as loop_mod
    from openai4s.agent.delegation import DelegationRunner
    from openai4s.config import get_config

    observed = {}

    def probing_run(self, task):
        policy = self.dispatcher._child_execution_policy
        observed["restricted"] = policy.restricted if policy else None
        observed["write_allowed"] = policy.allows("write_file") if policy else None
        observed["read_allowed"] = policy.allows("read_file") if policy else None
        observed["persona_in_task"] = "read-only scout" in task
        return {
            "stop_reason": "submitted",
            "submitted_output": {
                "output": {"ok": True},
                "completion_bullets": ["Completed the scouting"],
            },
            "final_message": None,
            "turns": 1,
        }

    monkeypatch.setattr(loop_mod.Agent, "run", probing_run)
    runner = DelegationRunner(get_config(), child_max_turns=3)
    service = DelegationService(delegate=runner, steering={}, store=_EmptyStore())
    try:
        result = service.delegate({"request": "map the repo", "name": "EXPLORE"})
    finally:
        runner.close()

    assert result["task_status"] == "completed"
    assert observed == {
        "restricted": True,
        "write_allowed": False,
        "read_allowed": True,
        "persona_in_task": True,
    }


def test_remote_gpu_provisioner_prompt_moved_without_rewording():
    prompt = BUILTIN_SPECIALIST_PROMPTS["REMOTE_GPU_PROVISIONER"]
    assert "remote-GPU provisioning specialist" in prompt
    assert "host.register_remote_capability" in prompt
    assert "Never claim a model is configured until verified." in prompt
