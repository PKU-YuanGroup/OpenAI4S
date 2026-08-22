"""Review scratch isolation: no workspace write, network, secrets, or submit_output."""

from __future__ import annotations

import pytest

from openai4s.server.review_scratch import (
    ReviewScratchError,
    cleanup_scratch,
    prepare_scratch,
    run_scratch_python,
)


def test_scratch_refuses_workspace_writes(tmp_path):
    workspace = tmp_path / "formal"
    workspace.mkdir()
    marker = workspace / "result.csv"
    marker.write_text("keep\n", encoding="utf-8")
    scratch = prepare_scratch({"complete": True}, workspace=workspace)
    try:
        result = run_scratch_python(
            "open(%r, 'w').write('mutated')\n" % str(marker),
            scratch=scratch,
            workspace=workspace,
            env_source={"PATH": "/usr/bin", "OPENAI4S_LLM_API_KEY": "sk-secret"},
        )
        assert result["returncode"] != 0
        assert "formal workspace" in (result["stderr"] + result["stdout"])
        assert marker.read_text(encoding="utf-8") == "keep\n"
    finally:
        cleanup_scratch(scratch)


def test_scratch_refuses_network_and_submit_output(tmp_path):
    scratch = prepare_scratch({"complete": True})
    try:
        with pytest.raises(ReviewScratchError, match="submit_output"):
            run_scratch_python("host.submit_output({'ok': True})\n", scratch=scratch)
        with pytest.raises(ReviewScratchError, match="socket"):
            run_scratch_python(
                "import socket\nsocket.create_connection(('example.com', 80))\n",
                scratch=scratch,
            )
        result = run_scratch_python(
            "mod = __import__('sock' + 'et')\n"
            "mod.create_connection(('example.com', 80))\n",
            scratch=scratch,
        )
        assert result["returncode"] != 0
        assert "network" in (result["stderr"] + result["stdout"]).lower()
    finally:
        cleanup_scratch(scratch)


def test_scratch_child_env_drops_secrets(tmp_path):
    scratch = prepare_scratch({"complete": True})
    try:
        result = run_scratch_python(
            "import os\nprint('KEY=' + os.environ.get('OPENAI4S_LLM_API_KEY', 'absent'))\n",
            scratch=scratch,
            env_source={
                "OPENAI4S_LLM_API_KEY": "sk-should-not-leak",
                "PATH": "/usr/bin",
            },
        )
        assert result["returncode"] == 0
        assert "sk-should-not-leak" not in result["stdout"]
        assert "KEY=absent" in result["stdout"]
    finally:
        cleanup_scratch(scratch)
