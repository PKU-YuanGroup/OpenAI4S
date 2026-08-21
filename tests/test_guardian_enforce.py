"""Stage 7 Guardian enforcement: allow_once only, never standing, fail closed."""

from __future__ import annotations

import pytest

from openai4s.server.guardian_enforce import decide_unattended


class _Flags:
    stage7_guardian_enforcement = True


class _Auto:
    approvals_reviewer = "auto_review"


class _Cfg:
    roadmap_features = _Flags()
    auto_mode = _Auto()


def test_low_risk_read_is_allow_once():
    allowed, message = decide_unattended(
        {"tool": "read_file", "target": "results.csv", "dangerous": False},
        canonical_arguments=[{"path": "results.csv"}],
        config=_Cfg(),
    )
    assert allowed is True
    assert "allow_once" in message


@pytest.mark.parametrize(
    ("tool", "target", "arguments"),
    [
        ("read_file", "credentials.json", {"path": "credentials.json"}),
        ("write_file", "token.json", {"path": "token.json", "content": "x"}),
        (
            "edit_file",
            "service-account.json",
            {"path": "service-account.json", "old_string": "a", "new_string": "b"},
        ),
        ("list_dir", ".aws", {"path": ".aws"}),
        ("glob", "*.csv", {"pattern": "*.csv", "path": ".ssh"}),
        ("grep", "needle", {"pattern": "needle", "path": ".config/gh"}),
        (
            "web_download",
            "example.com",
            {"url": "https://example.com/data", "path": "config.json"},
        ),
        ("save_artifact", "known_hosts", {"path": "known_hosts"}),
        (
            "materialise_artifact",
            "config.json",
            {"version_id": "v-source", "filename": "config.json"},
        ),
    ],
)
def test_credential_file_paths_fail_closed(tool, target, arguments):
    allowed, message = decide_unattended(
        {"tool": tool, "target": target, "dangerous": False},
        canonical_arguments=[arguments],
        config=_Cfg(),
    )
    assert allowed is False
    assert "credential path" in message


def test_resolved_alias_to_credential_basename_fails_closed():
    allowed, message = decide_unattended(
        {"tool": "read_file", "target": "notes.txt", "dangerous": False},
        canonical_arguments=[{"path": "notes.txt"}],
        resolved_file_path="config.json",
        config=_Cfg(),
    )
    assert allowed is False
    assert "credential path" in message


def test_resolved_credential_inode_alias_fails_closed():
    allowed, message = decide_unattended(
        {"tool": "read_file", "target": "notes.txt", "dangerous": False},
        canonical_arguments=[{"path": "notes.txt"}],
        resolved_file_path="notes.txt",
        resolved_file_is_credential=True,
        config=_Cfg(),
    )
    assert allowed is False
    assert "credential path" in message


def test_resolved_relative_path_does_not_reapply_trusted_workspace_parents():
    allowed, message = decide_unattended(
        {
            "tool": "read_file",
            "target": "/tmp/run/.aws/notes.txt",
            "dangerous": False,
        },
        canonical_arguments=[{"path": "/tmp/run/.aws/notes.txt"}],
        resolved_file_path="notes.txt",
        config=_Cfg(),
    )
    assert allowed is True
    assert "allow_once" in message


@pytest.mark.parametrize(
    "tool",
    [
        "read_file",
        "write_file",
        "edit_file",
        "list_dir",
        "save_artifact",
    ],
)
@pytest.mark.parametrize("canonical_arguments", [None, ["malformed"]])
def test_direct_path_targets_fail_closed_without_usable_canonical_arguments(
    tool, canonical_arguments
):
    allowed, message = decide_unattended(
        {"tool": tool, "target": "credentials.json", "dangerous": False},
        canonical_arguments=canonical_arguments,
        config=_Cfg(),
    )
    assert allowed is False
    assert "credential path" in message


@pytest.mark.parametrize(
    ("tool", "target", "arguments"),
    [
        (
            "web_download",
            "config.json",
            {
                "url": "https://config.json/archive",
                "path": "results.csv",
            },
        ),
        ("glob", ".aws/**", {"pattern": ".aws/**"}),
    ],
)
def test_non_path_targets_are_not_treated_as_credential_paths(tool, target, arguments):
    allowed, message = decide_unattended(
        {"tool": tool, "target": target, "dangerous": False},
        canonical_arguments=[arguments],
        config=_Cfg(),
    )
    assert allowed is True
    assert "allow_once" in message


@pytest.mark.parametrize(
    ("tool", "target"),
    [
        ("glob", ".aws/**"),
    ],
)
@pytest.mark.parametrize("canonical_arguments", [None, ["malformed"]])
def test_non_path_targets_remain_non_paths_without_usable_arguments(
    tool, target, canonical_arguments
):
    allowed, message = decide_unattended(
        {"tool": tool, "target": target, "dangerous": False},
        canonical_arguments=canonical_arguments,
        config=_Cfg(),
    )
    assert allowed is True
    assert "allow_once" in message


@pytest.mark.parametrize(
    ("tool", "target"),
    [
        ("web_download", "credentials.example"),
        ("materialise_artifact", "v-source"),
    ],
)
@pytest.mark.parametrize("canonical_arguments", [None, ["malformed"]])
def test_file_tools_without_a_reviewable_path_fail_closed(
    tool, target, canonical_arguments
):
    allowed, message = decide_unattended(
        {"tool": tool, "target": target, "dangerous": False},
        canonical_arguments=canonical_arguments,
        config=_Cfg(),
    )
    assert allowed is False
    assert "reviewable path" in message


@pytest.mark.parametrize("path", [None, ".", "reports"])
def test_content_search_requires_human_review_for_discovered_file_paths(path):
    arguments = {"pattern": "token"}
    if path is not None:
        arguments["path"] = path
    allowed, message = decide_unattended(
        {"tool": "grep", "target": "token", "dangerous": False},
        canonical_arguments=[arguments],
        config=_Cfg(),
    )
    assert allowed is False
    assert "data-dependent file search" in message


def test_dangerous_action_is_denied():
    allowed, message = decide_unattended(
        {"tool": "bash", "target": "rm -rf /", "dangerous": True},
        config=_Cfg(),
    )
    assert allowed is False
    assert allowed is not True


def test_flag_off_returns_none_so_legacy_path_remains():
    class Off:
        roadmap_features = type("F", (), {"stage7_guardian_enforcement": False})()
        auto_mode = _Auto()

    assert decide_unattended({"tool": "read_file"}, config=Off()) is None
