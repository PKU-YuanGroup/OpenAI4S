from __future__ import annotations

import base64
import json
import os
import re
import sys
import textwrap

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server.volcengine_arkcli import (
    ArkCliBridge,
    ArkCliError,
    CommandResult,
    _child_env,
    _normalize_device_code,
)
from openai4s.server.volcengine_connector import (
    ProvisioningMaterial,
    VolcengineConnectorService,
)
from openai4s.store import get_store


class _ScriptedRunner:
    def __init__(self, replies):
        self.replies = replies
        self.calls = []

    def __call__(self, argv, timeout_s, cancel_event):
        del timeout_s, cancel_event
        args = tuple(argv[1:])
        self.calls.append(args)
        reply = self.replies[args]
        if isinstance(reply, CommandResult):
            return reply
        return CommandResult(0, json.dumps(reply), "")


def _bridge(replies):
    runner = _ScriptedRunner(replies)
    bridge = ArkCliBridge(
        executable="arkcli",
        which=lambda _name: "/opt/arkcli",
        runner=runner,
    )
    return bridge, runner


def _fake_arkcli(tmp_path):
    script = tmp_path / "fake_arkcli.py"
    script.write_text(
        textwrap.dedent("""
            import json
            import sys

            args = sys.argv[1:]

            def emit(value):
                print(json.dumps(value, separators=(",", ":")))

            if args == ["--version"]:
                print("arkcli 1.0.15")
            elif args[:2] == ["auth", "whoami"]:
                emit({"logged_in": True, "auth_method": "sso", "name": "Alice",
                      "project_name": "default", "region": "cn-beijing"})
            elif args[:2] == ["plans", "get"]:
                emit({"plans": [{"key": "agent-plan", "name": "Agent Plan",
                                  "scope": "personal", "tier": "small",
                                  "status": "Effective"}]})
            elif args[:2] == ["usage", "plan"]:
                emit({"items": [{"product": "agent-plan", "subscribed": True,
                                  "periods": [{"label": "5h", "used": 1,
                                               "total": 100, "percent": 1}]}]})
            elif args[:2] == ["profile", "list"]:
                emit({"profiles": [{"name": "agent-plan_cn-beijing",
                                     "type": "agent-plan",
                                     "api_key_count": 1}]})
            elif args[:3] == ["profile", "models", "list"]:
                emit({"resources": {"text": {
                    "default": "doubao-seed-2-0-pro-260215"}}})
            elif args[:3] == ["profile", "keys", "list"]:
                emit({"default_api_key": "392****dab0"})
            elif args[:3] == ["profile", "keys", "refresh"]:
                emit({"ok": True})
            elif args[:2] == ["api", "apikey.list"]:
                emit({"Result": {"Items": [{"Id": 9, "Key": "392****dab0",
                                              "Status": "Active"}]}})
            elif args[:2] == ["api", "apikey.get_raw"]:
                emit({"Result": {"ApiKey": "ark-integration-secret"}})
            elif args[:3] == ["auth", "login", "--no-browser"]:
                if "--code" in args:
                    emit({"auth_method": "sso_no_browser"})
                else:
                    emit({"stage": "authorize_pending",
                          "authorize_url": "https://signin.volcengine.com/authorize/test",
                          "expires_in_sec": 600})
            elif args[:3] == ["auth", "login", "volc-sso"]:
                pass
            else:
                print("unsupported fake arkcli command", file=sys.stderr)
                raise SystemExit(3)
            """).strip() + "\n",
        encoding="utf-8",
    )
    if os.name == "nt":
        executable = tmp_path / "arkcli.cmd"
        executable.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
    else:
        executable = tmp_path / "arkcli"
        executable.write_text(
            f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n',
            encoding="utf-8",
        )
        executable.chmod(0o700)
    return executable


def test_child_process_environment_does_not_inherit_daemon_secrets():
    child = _child_env(
        {
            "PATH": "/bin",
            "HOME": "/home/alice",
            "DISPLAY": ":0",
            "OPENAI_API_KEY": "secret-openai",
            "VOLCENGINE_ARK_API_KEY": "secret-ark",
            "OPENAI4S_SECRET_LLM_LLM_API_KEY": "secret-broker",
        }
    )
    assert child == {"PATH": "/bin", "HOME": "/home/alice", "DISPLAY": ":0"}


def test_bridge_matches_the_profile_default_before_getting_the_raw_key():
    profile = "agent-plan_cn-beijing"
    raw_key = "ark-live-secret-value"
    replies = {
        (
            "profile",
            "keys",
            "list",
            "--profile",
            profile,
            "--format",
            "json",
        ): {"default_api_key": "392****dab0"},
        (
            "api",
            "apikey.list",
            "--params",
            '{"PageSize":100}',
            "--page-all",
            "--profile",
            profile,
            "--format",
            "json",
        ): {
            "Result": {
                "Items": [
                    {"Id": 7, "Key": "abc****1234", "Status": "Active"},
                    {"Id": 9, "Key": "392****dab0", "Status": "Active"},
                ]
            }
        },
        (
            "api",
            "apikey.get_raw",
            "--params",
            '{"Id":9}',
            "--profile",
            profile,
            "--format",
            "json",
        ): {"Result": {"ApiKey": raw_key}},
    }
    bridge, runner = _bridge(replies)

    assert bridge.default_api_key(profile) == raw_key
    assert bridge.api_key(profile, 9) == raw_key
    assert runner.calls[-1][1] == "apikey.get_raw"
    assert not any(call[:3] == ("profile", "keys", "refresh") for call in runner.calls)
    assert all(raw_key not in " ".join(map(str, call)) for call in runner.calls)


def test_bridge_refuses_to_guess_when_multiple_keys_do_not_match():
    profile = "agent-plan_cn-beijing"
    replies = {
        (
            "profile",
            "keys",
            "list",
            "--profile",
            profile,
            "--format",
            "json",
        ): {"default_api_key": "not-a-match"},
        (
            "api",
            "apikey.list",
            "--params",
            '{"PageSize":100}',
            "--page-all",
            "--profile",
            profile,
            "--format",
            "json",
        ): {
            "Result": {
                "Items": [
                    {"Id": 7, "Key": "abc****1234", "Status": "Active"},
                    {"Id": 9, "Key": "392****dab0", "Status": "Active"},
                ]
            }
        },
    }
    bridge, _runner = _bridge(replies)

    with pytest.raises(ArkCliError) as raised:
        bridge.default_api_key(profile)
    assert raised.value.code == "ark_key_choice_required"


def test_bridge_uses_the_profile_text_default_model():
    profile = "agent-plan_cn-beijing"
    replies = {
        (
            "profile",
            "models",
            "list",
            "--profile",
            profile,
            "--format",
            "json",
        ): {"resources": {"text": {"default": "doubao-seed-2-0-pro-260215"}}}
    }
    bridge, _runner = _bridge(replies)

    assert bridge.default_model(profile) == "doubao-seed-2-0-pro-260215"


def test_bridge_lists_only_invocable_platform_text_endpoints():
    profile = "platform_cn-beijing_default"
    replies = {
        (
            "resources",
            "list",
            "--profile",
            profile,
            "--modality",
            "text",
            "--format",
            "json",
        ): {
            "current_default": "ep-ready",
            "items": [
                {
                    "id": "ep-ready",
                    "resource_kind": "endpoint",
                    "invocable": True,
                },
                {
                    "id": "ep-needs-key",
                    "resource_kind": "endpoint",
                    "invocable": False,
                },
            ],
        }
    }
    bridge, _runner = _bridge(replies)

    assert bridge.endpoint_inventory(profile) == [
        {"id": "ep-ready", "name": "", "selected": True}
    ]


def test_device_login_start_uses_the_cross_platform_no_browser_flow():
    bridge, runner = _bridge(
        {
            ("auth", "login", "--no-browser", "--format", "json"): {
                "stage": "authorize_pending",
                "authorize_url": "https://signin.volcengine.com/authorize/test",
                "expires_in_sec": 600,
            }
        }
    )

    result = bridge.login_device_start()

    assert result["authorize_url"].startswith("https://signin.volcengine.com/")
    assert runner.calls == [("auth", "login", "--no-browser", "--format", "json")]


def test_bridge_surfaces_a_safe_ark_failure_reason_without_echoing_the_code():
    code = "YWJjZGVmZ2g="
    bridge, _runner = _bridge(
        {
            ("auth", "login", "--no-browser", "--code", code): CommandResult(
                1,
                "",
                json.dumps({"error": {"message": f"授权码 {code} 已过期，请重新登录"}}),
            )
        }
    )

    with pytest.raises(ArkCliError) as raised:
        bridge.login_device_complete(code)

    assert raised.value.code == "arkcli_failed"
    assert "已过期" in raised.value.message
    assert code not in raised.value.message
    assert "[redacted]" in raised.value.message


def test_inner_device_code_is_wrapped_with_the_pending_oauth_state():
    prepared = _normalize_device_code(
        "inner-code-123",
        "https://signin.volcengine.com/authorize/test?state=csrf-state-123",
    )
    decoded = base64.urlsafe_b64decode(prepared + "=" * (-len(prepared) % 4)).decode()

    assert decoded == "code=inner-code-123&state=csrf-state-123"


def test_bridge_resolves_the_ark_auto_router_alias():
    profile = "coding-plan_cn-beijing"
    replies = {
        (
            "profile",
            "models",
            "list",
            "--profile",
            profile,
            "--format",
            "json",
        ): {"Resources": {"Text": {"Default": "auto"}}}
    }
    bridge, _runner = _bridge(replies)

    assert bridge.default_model(profile) == "ark-code-latest"


def test_team_plan_never_falls_back_to_a_personal_profile():
    replies = {
        ("profile", "list", "--format", "json"): {
            "profiles": [
                {"name": "personal", "type": "agent-plan"},
                {"name": "team", "type": "agent-plan-team"},
            ]
        }
    }
    bridge, _runner = _bridge(replies)

    assert bridge.profile_for_plan("agent-plan-team") == "team"


class _ConnectedBridge:
    def __init__(self):
        pass

    def availability(self):
        return {"installed": True, "version": "1.0.15"}

    def whoami(self):
        return {
            "logged_in": True,
            "auth_method": "sso",
            "name": "Alice",
            "user_id": "private-user-id",
            "account_id": "private-account-id",
            "project_name": "default",
            "region": "cn-beijing",
        }

    def plans(self):
        return [
            {
                "key": "agent-plan",
                "name": "Agent Plan",
                "scope": "personal",
                "tier": "small",
                "status": "Effective",
            }
        ]

    def usage(self):
        return {
            "viewer": {"user_id": "must-not-reach-browser"},
            "items": [
                {
                    "product": "agent-plan",
                    "tier": "small",
                    "subscribed": True,
                    "periods": [
                        {
                            "label": "5h",
                            "used": 25,
                            "total": 100,
                            "percent": 25,
                            "reset_at": "2026-08-15T18:00:00+08:00",
                        }
                    ],
                }
            ],
        }

    def profiles(self):
        return [
            {
                "name": "agent-plan_cn-beijing",
                "type": "agent-plan",
                "api_key_count": 1,
            }
        ]

    def api_key_inventory(self, profile_name):
        assert profile_name == "agent-plan_cn-beijing"
        return [
            {
                "id": "apikey-private-id",
                "mask": "****abcd",
                "name": "OpenAI4S",
                "suffix": "abcd",
                "selected": True,
            }
        ]

    def profile_for_plan(self, plan_key):
        assert plan_key == "agent-plan"
        return "agent-plan_cn-beijing"

    def default_api_key(self, profile_name):
        assert profile_name == "agent-plan_cn-beijing"
        return "ark-live-secret-value"

    def api_key(self, profile_name, key_id=None):
        assert profile_name == "agent-plan_cn-beijing"
        assert key_id in (None, "apikey-private-id")
        return "ark-live-secret-value"

    def default_model(self, profile_name):
        assert profile_name == "agent-plan_cn-beijing"
        return "doubao-seed-2-0-pro-260215"


def test_connection_projection_contains_quota_but_no_cloud_identifiers_or_keys():
    service = VolcengineConnectorService(_ConnectedBridge(), cache_ttl_s=60)
    payload = service.connection()

    assert payload["state"] == "connected"
    assert payload["identity"] == {
        "logged_in": True,
        "auth_method": "sso",
        "name": "Alice",
        "project_name": "default",
        "region": "cn-beijing",
        "is_root": False,
        "sso_expired": False,
    }
    assert payload["usage"]["items"][0]["periods"][0]["percent"] == 25
    encoded = json.dumps(payload)
    assert "private-user-id" not in encoded
    assert "private-account-id" not in encoded
    assert "ark-live-secret-value" not in encoded


def test_pending_or_expired_plans_are_not_available_for_configuration():
    bridge = _ConnectedBridge()
    bridge.plans = lambda: [
        {
            "key": "agent-plan",
            "name": "Agent Plan",
            "scope": "personal",
            "tier": "small",
            "status": "Expired",
        },
        {
            "key": "coding-plan",
            "name": "Coding Plan",
            "scope": "personal",
            "tier": "pro",
            "status": "Pending",
        },
    ]

    payload = VolcengineConnectorService(bridge).connection()

    assert [plan["available"] for plan in payload["plans"]] == [False, False]
    assert payload["access"]["state"] == "plan_inactive"


def test_active_plan_without_a_key_is_connected_but_not_ready():
    bridge = _ConnectedBridge()
    bridge.profiles = lambda: [
        {
            "name": "agent-plan_cn-beijing",
            "type": "agent-plan",
            "api_key_count": 0,
        }
    ]
    bridge.api_key_inventory = lambda _profile: []

    payload = VolcengineConnectorService(bridge).connection()

    assert payload["state"] == "connected"
    assert payload["access"] == {
        "state": "key_missing",
        "plan_key": "agent-plan",
        "has_api_key": False,
        "error_code": "",
    }
    assert payload["plans"][0]["key_state"] == "key_missing"


def test_connected_account_with_platform_profile_but_no_key_requests_a_key():
    bridge = _ConnectedBridge()
    bridge.plans = lambda: []
    bridge.usage = lambda: {"items": []}
    bridge.profiles = lambda: [
        {"name": "platform_cn-beijing_default", "type": "platform", "api_key_count": 0}
    ]
    bridge.api_key_inventory = lambda _profile: []

    payload = VolcengineConnectorService(bridge).connection()

    assert payload["state"] == "connected"
    assert payload["access"]["state"] == "key_missing"
    assert payload["access"]["plan_key"] == "platform"
    assert payload["access"]["has_api_key"] is False


def test_platform_key_check_failure_is_not_reported_as_an_inactive_plan():
    bridge = _ConnectedBridge()
    bridge.plans = lambda: [
        {
            "key": "agent-plan",
            "name": "Agent Plan",
            "status": "Expired",
        }
    ]
    bridge.usage = lambda: {"items": []}
    bridge.profiles = lambda: [
        {
            "name": "platform_cn-beijing_default",
            "type": "platform",
            "api_key_count": 1,
        }
    ]

    def unavailable(_profile):
        raise ArkCliError("arkcli_failed", "STS refresh failed")

    bridge.api_key_inventory = unavailable

    payload = VolcengineConnectorService(bridge).connection()

    assert payload["access"] == {
        "state": "key_check_failed",
        "plan_key": "platform",
        "has_api_key": None,
        "error_code": "arkcli_failed",
    }


def test_refresh_performs_one_live_projection_without_a_duplicate_key_sync():
    bridge = _ConnectedBridge()
    whoami_calls = []
    original_whoami = bridge.whoami

    def tracked_whoami():
        whoami_calls.append(True)
        return original_whoami()

    bridge.whoami = tracked_whoami
    service = VolcengineConnectorService(bridge)

    payload = service.refresh()

    assert payload["access"]["state"] == "ready"
    assert whoami_calls == [True]


def test_platform_key_and_single_endpoint_are_ready_for_provisioning():
    bridge = _ConnectedBridge()
    bridge.plans = lambda: []
    bridge.usage = lambda: {"items": []}
    bridge.profiles = lambda: [
        {
            "name": "platform_cn-beijing_default",
            "type": "platform",
            "api_key_count": 1,
        }
    ]
    bridge.api_key_inventory = lambda _profile: [
        {
            "id": "cloud-key-one",
            "mask": "****1111",
            "name": "OpenAI4S",
            "suffix": "1111",
            "selected": True,
        }
    ]
    bridge.endpoint_inventory = lambda _profile: [
        {
            "id": "ep-20260823010429-wkt45",
            "name": "",
            "selected": False,
        }
    ]
    bridge.api_key = lambda _profile, key_id=None: "ark-live-secret-value"
    service = VolcengineConnectorService(bridge)

    payload = service.connection()

    assert payload["access"]["state"] == "platform_ready"
    assert payload["access"]["plan_key"] == "platform"
    assert re.fullmatch(r"[a-f0-9]{32}", payload["access"]["endpoint_choice"])
    assert "ep-20260823010429-wkt45" not in json.dumps(payload)

    material = service.provisioning_material(
        "platform", endpoint_choice=payload["access"]["endpoint_choice"]
    )
    assert material.plan_key == "platform"
    assert material.model == "ep-20260823010429-wkt45"
    assert material.api_key == "ark-live-secret-value"


def test_inactive_plan_does_not_hide_a_ready_platform_endpoint():
    bridge = _ConnectedBridge()
    bridge.plans = lambda: [
        {
            "key": "agent-plan",
            "name": "Agent Plan",
            "status": "Expired",
        }
    ]
    bridge.usage = lambda: {"items": []}
    bridge.profiles = lambda: [
        {
            "name": "platform_cn-beijing_default",
            "type": "platform",
            "api_key_count": 1,
        }
    ]
    bridge.api_key_inventory = lambda _profile: [
        {
            "id": "cloud-key-one",
            "name": "OpenAI4S",
            "suffix": "1111",
            "selected": True,
        }
    ]
    bridge.endpoint_inventory = lambda _profile: [
        {
            "id": "ep-20260823010429-wkt45",
            "name": "Doubao",
            "selected": True,
        }
    ]

    payload = VolcengineConnectorService(bridge).connection()

    assert payload["access"]["state"] == "platform_ready"
    assert payload["access"]["plan_key"] == "platform"


def test_multiple_api_keys_require_an_explicit_opaque_choice():
    bridge = _ConnectedBridge()
    bridge.api_key_inventory = lambda _profile: [
        {
            "id": "cloud-key-one",
            "mask": "****1111",
            "name": "First",
            "suffix": "1111",
            "selected": False,
        },
        {
            "id": "cloud-key-two",
            "mask": "****2222",
            "name": "Second",
            "suffix": "2222",
            "selected": False,
        },
    ]
    chosen = []
    bridge.api_key = (
        lambda _profile, key_id=None: chosen.append(key_id) or "ark-live-secret-value"
    )
    service = VolcengineConnectorService(bridge)

    payload = service.connection()

    assert payload["access"]["state"] == "key_choice_required"
    choices = payload["plans"][0]["key_choices"]
    assert [choice["name"] for choice in choices] == ["First", "Second"]
    assert all(re.fullmatch(r"[a-f0-9]{32}", choice["id"]) for choice in choices)
    encoded = json.dumps(payload)
    assert "cloud-key-one" not in encoded
    assert "cloud-key-two" not in encoded

    material = service.provisioning_material("agent-plan", choices[1]["id"])

    assert material.api_key == "ark-live-secret-value"
    assert chosen == ["cloud-key-two"]


def test_device_login_is_single_flight_until_the_code_is_submitted():
    class DeviceBridge(_ConnectedBridge):
        def login_device_start(self):
            return {
                "authorize_url": "https://signin.volcengine.com/authorize/test",
                "expires_in_sec": 600,
            }

    bridge = DeviceBridge()
    service = VolcengineConnectorService(bridge)
    first = service.start_device_login()
    second = service.start_device_login()

    assert first["state"] == "awaiting_code"
    assert second["login_id"] == first["login_id"]
    assert second["state"] == "awaiting_code"


def test_the_offline_suite_never_resolves_a_real_arkcli(tmp_path, monkeypatch):
    """conftest pins OPENAI4S_ARKCLI_PATH so pytest can never spawn a real CLI."""

    _fake_arkcli(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))

    assert ArkCliBridge().executable() == ""


def test_chinese_project_selection_failure_maps_to_the_recovery_state():
    code = "YWJjZGVmZ2g="
    bridge, _runner = _bridge(
        {
            ("auth", "login", "--no-browser", "--code", code): CommandResult(
                1, "", "Error: Project 尚未选择，当前为非交互式终端"
            )
        }
    )

    with pytest.raises(ArkCliError) as raised:
        bridge.login_device_complete(code)

    assert raised.value.code == "project_selection_required"


def test_purely_alphanumeric_secrets_in_cli_errors_are_redacted():
    token = "YWJjZGVmZ2hp"
    bridge, _runner = _bridge(
        {("auth", "logout"): CommandResult(1, "", f"invalid session token {token}")}
    )

    with pytest.raises(ArkCliError) as raised:
        bridge.logout()

    assert token not in raised.value.message
    assert "[redacted]" in raised.value.message


def test_an_unrunnable_executable_is_a_controlled_failure_not_a_crash():
    def raising_runner(argv, timeout_s, cancel_event):
        del argv, timeout_s, cancel_event
        raise PermissionError(13, "Permission denied")

    bridge = ArkCliBridge(
        executable="arkcli", which=lambda _name: "/opt/arkcli", runner=raising_runner
    )

    with pytest.raises(ArkCliError) as raised:
        bridge.whoami()
    assert raised.value.code == "arkcli_failed"

    payload = VolcengineConnectorService(bridge).connection()
    assert payload["state"] == "error"


def test_plaintext_envelope_paste_is_accepted_and_rewrapped():
    prepared = _normalize_device_code(
        "code=inner-code-123&state=csrf-state-123",
        "https://signin.volcengine.com/authorize/test?state=other-state",
    )
    decoded = base64.urlsafe_b64decode(prepared + "=" * (-len(prepared) % 4)).decode()

    assert decoded == "code=inner-code-123&state=csrf-state-123"


def test_an_expired_pending_login_mints_a_fresh_authorization():
    starts = []

    class DeviceBridge(_ConnectedBridge):
        def login_device_start(self):
            starts.append(1)
            return {
                "authorize_url": "https://signin.volcengine.com/authorize/test",
                "expires_in_sec": 60,
            }

    clock = {"now": 1_000_000.0}
    service = VolcengineConnectorService(
        DeviceBridge(), wall_clock=lambda: clock["now"]
    )

    first = service.start_device_login()
    clock["now"] += 3600
    second = service.start_device_login()

    assert len(starts) == 2
    assert second["state"] == "awaiting_code"
    assert second["login_id"] != first["login_id"]


def test_a_local_paste_format_error_keeps_the_pending_authorization():
    class DeviceBridge(_ConnectedBridge):
        def login_device_start(self):
            return {
                "authorize_url": "https://signin.volcengine.com/authorize/test",
                "expires_in_sec": 600,
            }

    service = VolcengineConnectorService(DeviceBridge())
    started = service.start_device_login()

    with pytest.raises(ArkCliError) as raised:
        service.complete_device_login("bad code\nwith whitespace")

    assert raised.value.code == "invalid_authorization_code"
    login = service.start_device_login()
    assert login["state"] == "awaiting_code"
    assert login["login_id"] == started["login_id"]


def test_cancel_during_token_exchange_stays_cancelled():
    holder = {}

    class CancelRace(_ConnectedBridge):
        def login_device_start(self):
            return {
                "authorize_url": "https://signin.volcengine.com/authorize/test",
                "expires_in_sec": 600,
            }

        def login_device_complete(self, code, cancel_event=None):
            del code, cancel_event
            holder["service"].cancel_login()
            raise ArkCliError("login_cancelled", "Volcengine login was cancelled")

    service = VolcengineConnectorService(CancelRace())
    holder["service"] = service
    service.start_device_login()

    with pytest.raises(ArkCliError):
        service.complete_device_login("code=abc12345&state=xyz67890")

    assert service.connection()["login"]["state"] == "cancelled"


def test_key_choice_projection_surfaces_a_pending_endpoint_choice():
    bridge = _ConnectedBridge()
    bridge.plans = lambda: []
    bridge.usage = lambda: {"items": []}
    bridge.profiles = lambda: [
        {
            "name": "platform_cn-beijing_default",
            "type": "platform",
            "api_key_count": 2,
        }
    ]
    bridge.api_key_inventory = lambda _profile: [
        {
            "id": "cloud-key-one",
            "mask": "****1111",
            "name": "First",
            "suffix": "1111",
            "selected": False,
        },
        {
            "id": "cloud-key-two",
            "mask": "****2222",
            "name": "Second",
            "suffix": "2222",
            "selected": False,
        },
    ]
    bridge.endpoint_inventory = lambda _profile: [
        {"id": "ep-one", "name": "A", "selected": False},
        {"id": "ep-two", "name": "B", "selected": False},
    ]
    bridge.api_key = lambda _profile, key_id=None: "ark-live-secret-value"
    service = VolcengineConnectorService(bridge)

    access = service.connection()["access"]

    assert access["state"] == "key_choice_required"
    assert len(access["key_choices"]) == 2
    assert len(access["endpoint_choices"]) == 2

    material = service.provisioning_material(
        "platform",
        access["key_choices"][0]["id"],
        access["endpoint_choices"][1]["id"],
    )
    assert material.model == "ep-two"


def test_connection_scan_is_single_flight_across_threads():
    import threading as _threading
    import time as _time

    calls = []
    started = _threading.Event()
    gate = _threading.Event()

    class SlowBridge(_ConnectedBridge):
        def whoami(self):
            calls.append(1)
            started.set()
            gate.wait(10.0)
            return super().whoami()

    service = VolcengineConnectorService(SlowBridge(), cache_ttl_s=60)
    results = []
    threads = [
        _threading.Thread(target=lambda: results.append(service.connection(force=True)))
        for _ in range(2)
    ]
    threads[0].start()
    assert started.wait(5.0)
    threads[1].start()
    _time.sleep(0.3)
    gate.set()
    for thread in threads:
        thread.join(10.0)

    assert len(calls) == 1
    assert all(result["state"] == "connected" for result in results)


class _Hub:
    def emitter(self, _root_frame_id):
        return lambda _event: None

    def broadcast(self, _root_frame_id, _event):
        return None


class _GatewayConnector:
    def connection(self, *, force=False):
        return {
            "installed": True,
            "version": "1.0.15",
            "state": "connected",
            "identity": {
                "logged_in": True,
                "name": "Alice",
                "project_name": "default",
                "region": "cn-beijing",
            },
            "plans": [
                {
                    "key": "agent-plan",
                    "name": "Agent Plan",
                    "scope": "personal",
                    "tier": "small",
                    "status": "Effective",
                    "available": True,
                    "key_state": "ready",
                    "has_api_key": True,
                }
            ],
            "usage": {"items": []},
            "access": {
                "state": "ready",
                "plan_key": "agent-plan",
                "has_api_key": True,
                "error_code": "",
            },
            "login": {"state": "idle"},
            "cached": not force,
        }

    def refresh(self):
        return self.connection(force=True)

    def provisioning_material(
        self, plan_key=None, key_choice=None, endpoint_choice=None
    ):
        assert plan_key in (None, "agent-plan")
        assert key_choice in (None, "")
        assert endpoint_choice in (None, "")
        return ProvisioningMaterial(
            api_key="ark-route-secret-value",
            plan_key="agent-plan",
            plan_name="Agent Plan",
            profile_name="agent-plan_cn-beijing",
            model="doubao-seed-2-0-pro-260215",
            region="cn-beijing",
            account_name="Alice",
        )

    def start_device_login(self):
        return {
            "state": "awaiting_code",
            "login_id": "volc-login-test",
            "authorize_url": "https://signin.volcengine.com/authorize/test",
        }

    def complete_device_login(self, _code):
        return {"state": "succeeded", "login_id": "volc-login-test"}

    def cancel_login(self):
        return {"state": "cancelled", "login_id": "volc-login-test"}


@pytest.mark.stubbed_backend
def test_gateway_connector_routes_through_the_real_cli_bridge(tmp_path, monkeypatch):
    executable = str(_fake_arkcli(tmp_path))
    monkeypatch.setenv("OPENAI4S_ARKCLI_PATH", executable)
    bridge = ArkCliBridge(
        executable=executable,
    )
    connector = VolcengineConnectorService(bridge)
    monkeypatch.setattr(
        gateway_mod,
        "VolcengineConnectorService",
        lambda: connector,
    )
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    hub = _Hub()
    runner = gateway_mod.SessionRunner(cfg, hub, start_idle_sweeper=False)
    seen = []
    body = {}
    try:
        handler = object.__new__(gateway_mod.make_handler(cfg, hub, runner))
        handler._query = lambda: {}
        handler._body = lambda: dict(body)
        handler._json = lambda obj, code=200: seen.append((obj, code))

        def call(method, path, payload=None):
            body.clear()
            body.update(payload or {})
            handler._api(method, path)
            return seen[-1]

        connection, status = call("GET", "/volcengine/connection")
        assert status == 200
        assert connection["state"] == "connected"
        assert connection["plans"][0]["key"] == "agent-plan"
        assert connection["linked"] is False

        refreshed, status = call("POST", "/volcengine/refresh")
        assert status == 200
        assert refreshed["usage"]["items"][0]["periods"][0]["percent"] == 1

        device, status = call("POST", "/volcengine/login", {"mode": "device"})
        assert status == 200
        assert device["state"] == "awaiting_code"

        completed, status = call(
            "POST", "/volcengine/login/complete", {"code": "YWJjZGVmZ2g="}
        )
        assert status == 200
        assert completed["state"] == "succeeded"

        browser, status = call("POST", "/volcengine/login", {"mode": "browser"})
        assert status == 200
        assert browser["state"] == "awaiting_code"

        cancelled, status = call("POST", "/volcengine/login/cancel")
        assert status == 200
        assert cancelled["state"] == "cancelled"

        configured, status = call(
            "POST", "/volcengine/configure", {"plan_key": "agent-plan"}
        )
        assert status == 201
        assert configured["connection"]["configured"] is True
        assert "ark-integration-secret" not in json.dumps(configured)

        disconnected, status = call("POST", "/volcengine/disconnect", {"confirm": True})
        assert status == 200
        assert disconnected["connection"]["linked"] is False
    finally:
        runner.close()


@pytest.mark.stubbed_backend
def test_gateway_configure_brokers_the_key_and_never_returns_it(tmp_path, monkeypatch):
    fake = _GatewayConnector()
    monkeypatch.setattr(gateway_mod, "VolcengineConnectorService", lambda: fake)
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    seen = []
    try:
        handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
        handler._query = lambda: {}
        handler._json = lambda obj, code=200: seen.append((obj, code))
        handler._body = lambda: {"plan_key": "agent-plan"}

        handler._api("POST", "/volcengine/configure")

        payload, status = seen[-1]
        assert status == 201
        assert payload["profile"]["has_api_key"] is True
        assert payload["connection"]["linked"] is True
        assert payload["connection"]["configured"] is True
        assert payload["connection"]["configured_plan_key"] == "agent-plan"
        assert "ark-route-secret-value" not in json.dumps(payload)
        store = get_store(cfg.db_path)
        profile_id = store.get_setting("volcengine_model_profile_id")
        profile = next(
            item for item in store.list_model_profiles() if item["id"] == profile_id
        )
        assert profile["api_key"].startswith("secret://")
        assert "ark-route-secret-value" not in json.dumps(profile)
        assert store.get_secret_setting("llm_api_key") == "ark-route-secret-value"
        assert store.get_setting("llm_provider") == "ark"
        assert store.get_setting("llm_model") == "doubao-seed-2-0-pro-260215"

        handler._body = lambda: {"confirm": True}
        handler._api("POST", "/volcengine/disconnect")

        disconnected, status = seen[-1]
        assert status == 200
        assert disconnected["connection"]["linked"] is False
        assert disconnected["connection"]["configured"] is False
        assert disconnected["connection"]["configured_plan_key"] == ""
        assert store.get_secret_setting("llm_api_key") == ""
        removed = next(
            item for item in store.list_model_profiles() if item["id"] == profile_id
        )
        assert removed["deleted_at"]
        assert removed["api_key"] == ""
    finally:
        runner.close()


@pytest.mark.stubbed_backend
def test_gateway_browser_login_returns_cross_platform_authorization(
    tmp_path, monkeypatch
):
    fake = _GatewayConnector()
    monkeypatch.setattr(gateway_mod, "VolcengineConnectorService", lambda: fake)
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=3,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    seen = []
    try:
        handler = object.__new__(gateway_mod.make_handler(cfg, _Hub(), runner))
        handler._query = lambda: {}
        handler._json = lambda obj, code=200: seen.append((obj, code))
        handler._body = lambda: {"mode": "browser"}

        handler._api("POST", "/volcengine/login")

        assert seen == [
            (
                {
                    "state": "awaiting_code",
                    "login_id": "volc-login-test",
                    "authorize_url": "https://signin.volcengine.com/authorize/test",
                },
                200,
            )
        ]
    finally:
        runner.close()
