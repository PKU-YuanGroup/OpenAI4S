"""Characterization tests for the supported Python import surface.

These tests intentionally describe caller-visible compatibility only.  They
do not pin implementation modules, function bodies, annotations, or private
dataclass fields, so the backend remains free to move behind these facades.
"""

from __future__ import annotations

import importlib
import inspect
import re

import pytest

# Public package facades documented by their __all__.  Additions are backward
# compatible, so each expected set is checked as a subset rather than requiring
# an exact match.
_PACKAGE_EXPORTS = {
    "openai4s.agent": {
        "Agent",
        "AgentEngine",
        "EngineResult",
        "ExecutionOutcome",
        "ModelReply",
        "RunState",
        "run_task",
    },
    "openai4s.cli": {"main"},
    "openai4s.compute": {"ComputeError", "ComputeManager"},
    "openai4s.execution": {
        "CaptureResult",
        "CellExecutionResult",
        "CellRequest",
        "WatchdogPolicy",
        "execute_with_watchdog",
    },
    "openai4s.kernel": {"Kernel", "KernelLease", "KernelSupervisor"},
    "openai4s.sdk": {"build_host"},
    "openai4s.security": {
        "Verdict",
        "classify_code",
        "is_always_safe",
        "InjectionVerdict",
        "scan_tool_result",
        "ScreenVerdict",
        "looks_biosecurity_relevant",
        "screen_trajectory",
    },
    "openai4s.server": {"build_server", "serve"},
    "openai4s.skills_loader": {"Skill", "SkillLoader", "discover_skills"},
    "openai4s.tools": {
        "Tool",
        "WorkspaceToolContext",
        "EnvironmentToolContext",
        "ControlToolContext",
        "FencedBlock",
        "REGISTRY",
        "get_tool",
        "register_tool",
        "all_tools",
        "parse_fence_delimiter",
        "parse_tool_calls",
        "render_tools_prompt",
        "execute_tool_call",
        "format_tool_result",
        "run_tool_calls",
        "scan_fenced_blocks",
        "strip_fenced_blocks",
        "finalize_tool_batch",
        "MAX_TOOL_CALLS_PER_TURN",
        "MAX_TOOL_OBS_CHARS",
    },
}


@pytest.mark.parametrize("module_name", sorted(_PACKAGE_EXPORTS))
def test_documented_package_exports_remain_importable(module_name):
    module = importlib.import_module(module_name)
    expected = _PACKAGE_EXPORTS[module_name]

    assert expected <= set(module.__all__)
    for symbol in expected:
        assert hasattr(module, symbol), f"{module_name} no longer exports {symbol}"


def test_package_version_remains_a_public_version_string():
    package = importlib.import_module("openai4s")
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", package.__version__)


# These module-level names are already consumed by integrations and tests even
# though their modules do not define __all__.  The assertion is deliberately
# limited to importability; it does not constrain where the implementation
# lives or whether the facade is a class, function, or alias.
_MODULE_SYMBOLS = {
    "openai4s.config": {
        "Config",
        "LLMConfig",
        "SecurityConfig",
        "get_config",
        "is_placeholder_api_key",
    },
    "openai4s.host_dispatch": {"HostDispatcher", "build_dispatcher"},
    "openai4s.permissions": {"PermissionBroker", "broker", "suggest_patterns"},
    "openai4s.store": {"Store", "get_store"},
}


@pytest.mark.parametrize("module_name", sorted(_MODULE_SYMBOLS))
def test_supported_module_symbols_remain_importable(module_name):
    module = importlib.import_module(module_name)
    for symbol in _MODULE_SYMBOLS[module_name]:
        assert hasattr(module, symbol), f"cannot import {symbol} from {module_name}"


def _parameters(module_name: str, symbol: str) -> dict[str, inspect.Parameter]:
    obj = getattr(importlib.import_module(module_name), symbol)
    return dict(inspect.signature(obj).parameters)


def _assert_parameter_prefix(
    module_name: str, symbol: str, expected: tuple[str, ...]
) -> dict[str, inspect.Parameter]:
    params = _parameters(module_name, symbol)
    assert tuple(params)[: len(expected)] == expected
    return params


@pytest.mark.parametrize(
    ("module_name", "symbol", "expected"),
    [
        (
            "openai4s.agent",
            "Agent",
            (
                "cfg",
                "max_turns",
                "verbose",
                "dispatcher",
                "use_skills",
                "allow_delegate",
                "frame_id",
                "delegate_depth",
            ),
        ),
        (
            "openai4s.kernel",
            "Kernel",
            (
                "dispatcher",
                "cwd",
                "mode",
                "python",
                "env_root",
                "env_name",
                "argv",
            ),
        ),
        (
            "openai4s.config",
            "LLMConfig",
            (
                "provider",
                "base_url",
                "model",
                "api_key",
                "max_tokens",
                "temperature",
                "timeout_s",
            ),
        ),
        (
            "openai4s.config",
            "Config",
            (
                "data_dir",
                "host",
                "port",
                "llm",
                "security",
                "share",
                "skills_dir",
                "max_turns",
                "explore_max_turns",
                "context_window_tokens",
                "compaction_trigger_ratio",
                "record_tape",
                "notebook_repl",
            ),
        ),
        (
            "openai4s.host_dispatch",
            "HostDispatcher",
            ("cfg", "delegate_fn", "frame_id"),
        ),
        (
            "openai4s.host_dispatch",
            "build_dispatcher",
            ("cfg", "delegate_fn", "frame_id"),
        ),
        ("openai4s.compute", "ComputeManager", ("cfg",)),
        ("openai4s.compute", "ComputeError", ("msg", "kind", "concurrency")),
        ("openai4s.skills_loader", "SkillLoader", ("skills_dir", "cfg")),
        ("openai4s.skills_loader", "discover_skills", ("skills_dir", "cfg")),
    ],
)
def test_key_constructor_parameter_names_remain_compatible(
    module_name, symbol, expected
):
    _assert_parameter_prefix(module_name, symbol, expected)


def test_run_task_keeps_positional_task_and_keyword_options():
    params = _assert_parameter_prefix(
        "openai4s.agent", "run_task", ("task", "verbose", "cfg")
    )
    assert params["task"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert params["task"].default is inspect.Parameter.empty
    assert params["verbose"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["verbose"].default is False
    assert params["cfg"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["cfg"].default is None


def test_host_and_server_facades_keep_calling_conventions():
    host = _assert_parameter_prefix("openai4s.sdk", "build_host", ("host_call", "mode"))
    assert host["host_call"].default is inspect.Parameter.empty
    assert host["mode"].default == "repl"

    build = _assert_parameter_prefix("openai4s.server", "build_server", ("cfg",))
    assert build["cfg"].default is None

    serve = _assert_parameter_prefix("openai4s.server", "serve", ("cfg", "block"))
    assert serve["cfg"].default is None
    assert serve["block"].kind is inspect.Parameter.KEYWORD_ONLY
    assert serve["block"].default is True


def test_store_factory_keeps_path_argument_required():
    params = _assert_parameter_prefix("openai4s.store", "get_store", ("db_path",))
    assert params["db_path"].default is inspect.Parameter.empty
