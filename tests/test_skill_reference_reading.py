"""Reading a reference file inside a Skill — from a cell and from the tool plane.

`admet_genetic`'s SKILL.md says to read `references/data_contracts.md` before
running the pipeline. The read path existed and was allowlist-aware, but its
only spelling was `host.skills.read(...)` from inside a Python cell: no native
control tool mapped to `skills_read`, so an agent working purely in the tool
plane — a delegated child that never runs a cell, most of all — structurally
could not follow that instruction, and its natural fallback (`read_text_file`,
confined to the workspace) failed with a path error indistinguishable from "the
file is not there".

These tests cover both spellings against a real fixture Skill, the loader
traversal guard that had no test at all, and the refusal a child scoped away
from the Skill gets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import openai4s.agent.loop as loop_mod
from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import build_dispatcher
from openai4s.skills_loader import SkillLoader

CONTRACT_MARKER = "MARKER-CONTRACT-9f31"
GA_MARKER = "MARKER-GA-4b7c"


def _fixture_skill(root: Path) -> Path:
    """A Skill with two reference documents carrying known markers."""
    skill = root / "skills" / "gene_opt"
    (skill / "references").mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: gene_opt\n"
        "description: Optimize sequences with a genetic search.\n"
        "---\n\n"
        "# Gene optimization\n\n"
        "Read `references/data_contracts.md` and `references/ga.md` first.\n",
        encoding="utf-8",
    )
    (skill / "references" / "data_contracts.md").write_text(
        f"# Data contracts\n\nEvery generation row carries {CONTRACT_MARKER}.\n",
        encoding="utf-8",
    )
    (skill / "references" / "ga.md").write_text(
        f"# GA design\n\nMutation and crossover are keyed by {GA_MARKER}.\n",
        encoding="utf-8",
    )
    # A second Skill, so "scoped away" is a real allowlist and not an empty
    # catalog.
    peer = root / "skills" / "assay_qc"
    peer.mkdir(parents=True, exist_ok=True)
    (peer / "SKILL.md").write_text(
        "---\nname: assay_qc\ndescription: QC an assay plate.\n---\n\nBody.\n",
        encoding="utf-8",
    )
    return skill


def _cfg(tmp_path) -> Config:
    root = tmp_path / "data"
    root.mkdir(parents=True, exist_ok=True)
    _fixture_skill(root)
    return Config(
        data_dir=root,
        skills_dir=root / "skills",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=4,
    )


# --------------------------------------------------------------------------
# the loader guard that had no test
# --------------------------------------------------------------------------


def test_the_loader_refuses_a_path_that_escapes_the_skill_directory(tmp_path):
    """`loader.read`'s containment check was the only guard on this path and
    the only `escapes skill dir` assertion in the suite was against the *edit*
    path, which is a different function."""
    cfg = _cfg(tmp_path)
    loader = SkillLoader(cfg.skills_dir)
    loader.discover()

    assert CONTRACT_MARKER in loader.read("gene_opt", "references/data_contracts.md")
    for escape in ("../../etc/passwd", "../assay_qc/SKILL.md", "references/../../x"):
        with pytest.raises(ValueError) as excinfo:
            loader.read("gene_opt", escape)
        assert "escapes skill dir" in str(excinfo.value)


def test_an_absolute_path_cannot_replace_the_skill_root(tmp_path):
    cfg = _cfg(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("not yours", encoding="utf-8")
    loader = SkillLoader(cfg.skills_dir)
    loader.discover()

    with pytest.raises(ValueError):
        loader.read("gene_opt", str(secret))


def test_a_symlink_out_of_the_skill_directory_is_refused(tmp_path):
    cfg = _cfg(tmp_path)
    secret = tmp_path / "outside.txt"
    secret.write_text("not yours", encoding="utf-8")
    link = cfg.skills_dir / "gene_opt" / "references" / "escape.md"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):  # pragma: no cover - platform guard
        pytest.skip("this host cannot create symlinks")
    loader = SkillLoader(cfg.skills_dir)
    loader.discover()

    with pytest.raises(ValueError):
        loader.read("gene_opt", "references/escape.md")


# --------------------------------------------------------------------------
# the native tool: the tool plane, with no kernel at all
# --------------------------------------------------------------------------


def test_a_native_tool_maps_to_skills_read_and_is_disclosed(tmp_path):
    from openai4s.tools.catalog import _TOOL_GROUP
    from openai4s.tools.registry import TOOL_TYPES

    tool = next(cls() for cls in TOOL_TYPES if cls.name == "read_skill_file")
    assert tool.host_method == "skills_read"
    assert tool.requires_approval is False
    # Matches load_skill: a reference cut mid-table is one the agent half-read
    # while believing it had all of it.
    assert tool.output_limit == 50_000
    assert _TOOL_GROUP["read_skill_file"] == "skills"


def test_the_tool_plane_can_read_a_reference_without_starting_a_kernel(tmp_path):
    """The whole point: no cell, no worker, and the reference still arrives."""
    from openai4s.tools import execute_tool_call

    cfg = _cfg(tmp_path)
    dispatcher = build_dispatcher(cfg)
    catalog = dispatcher.tool_catalog()

    text, ok = execute_tool_call(
        dispatcher,
        {
            "name": "read_skill_file",
            "arguments": {"name": "gene_opt", "path": "references/ga.md"},
        },
        catalog,
    )
    assert ok is True
    assert GA_MARKER in text
    # and nothing spawned a worker to get there
    assert dispatcher.background_kernel_factory is None


def test_the_tool_reads_both_references_and_defaults_to_skill_md(tmp_path):
    from openai4s.tools import execute_tool_call

    cfg = _cfg(tmp_path)
    dispatcher = build_dispatcher(cfg)
    catalog = dispatcher.tool_catalog()
    contract, ok1 = execute_tool_call(
        dispatcher,
        {
            "name": "read_skill_file",
            "arguments": {
                "name": "gene_opt",
                "path": "references/data_contracts.md",
            },
        },
        catalog,
    )
    default, ok2 = execute_tool_call(
        dispatcher,
        {"name": "read_skill_file", "arguments": {"name": "gene_opt"}},
        catalog,
    )
    assert ok1 and ok2
    assert CONTRACT_MARKER in contract
    assert "# Gene optimization" in default


def test_the_tool_cannot_escape_the_skill_directory(tmp_path):
    from openai4s.tools import execute_tool_call

    cfg = _cfg(tmp_path)
    dispatcher = build_dispatcher(cfg)

    text, ok = execute_tool_call(
        dispatcher,
        {
            "name": "read_skill_file",
            "arguments": {"name": "gene_opt", "path": "../../etc/passwd"},
        },
        dispatcher.tool_catalog(),
    )
    assert ok is False
    assert "escapes skill dir" in text


def test_a_scoped_away_skill_is_refused_the_same_way_an_absent_one_is(tmp_path):
    """A child narrowed to one Skill cannot read a peer's references, and the
    refusal is deliberately indistinguishable from "no such skill" so refusals
    cannot be used to enumerate the catalog."""
    from openai4s.host.delegation_policy import child_execution_policy
    from openai4s.tools import execute_tool_call

    cfg = _cfg(tmp_path)
    dispatcher = build_dispatcher(cfg)
    dispatcher.set_child_execution_policy(
        child_execution_policy(
            {"capabilities": ["skills"], "skill_names": ["assay_qc"]}
        )
    )
    catalog = dispatcher.tool_catalog()

    text, ok = execute_tool_call(
        dispatcher,
        {
            "name": "read_skill_file",
            "arguments": {"name": "gene_opt", "path": "references/ga.md"},
        },
        catalog,
    )
    assert ok is False
    assert "no such skill" in text
    assert GA_MARKER not in text


def test_a_child_without_the_skills_capability_is_denied_at_the_dispatcher(tmp_path):
    from openai4s.host.delegation_policy import child_execution_policy
    from openai4s.tools import execute_tool_call

    cfg = _cfg(tmp_path)
    dispatcher = build_dispatcher(cfg)
    dispatcher.set_child_execution_policy(
        child_execution_policy({"capabilities": ["files"]})
    )

    catalog = dispatcher.tool_catalog()
    assert "read_skill_file" not in {spec.name for spec in catalog.specs()}

    text, ok = execute_tool_call(
        dispatcher,
        {
            "name": "read_skill_file",
            "arguments": {"name": "gene_opt", "path": "references/ga.md"},
        },
        catalog,
    )
    assert ok is False
    assert GA_MARKER not in text


# --------------------------------------------------------------------------
# the cell plane: a delegated child, a scripted model, a REAL kernel
# --------------------------------------------------------------------------


def test_a_delegated_child_reads_both_references_through_a_real_kernel(
    tmp_path, monkeypatch
):
    """Scripted model, real Agent, real persistent Python kernel, real host
    RPC. The child reads both reference files in one cell and returns both
    markers in its structured completion."""
    cfg = _cfg(tmp_path)
    replies: list[int] = []

    def fake_chat(messages, chat_cfg, **kwargs):
        del messages, chat_cfg, kwargs
        replies.append(len(replies))
        if len(replies) == 1:
            return {
                "content": (
                    "```python\n"
                    "contract = host.skills.read("
                    "'gene_opt', 'references/data_contracts.md')\n"
                    "ga = host.skills.read('gene_opt', 'references/ga.md')\n"
                    "print(len(contract), len(ga))\n"
                    "```"
                ),
                "tool_calls": [],
            }
        return {
            "content": (
                "```python\n"
                "host.submit_output(\n"
                "    {'summary': 'read both references',\n"
                "     'contract': contract.strip().splitlines()[-1],\n"
                "     'ga': ga.strip().splitlines()[-1]},\n"
                "    ['Read both reference documents'],\n"
                ")\n"
                "```"
            ),
            "tool_calls": [],
        }

    monkeypatch.setattr(loop_mod, "chat", fake_chat)

    agent = loop_mod.Agent(
        cfg=cfg,
        max_turns=4,
        use_skills=True,
        allow_delegate=False,
        workspace=str(tmp_path / "ws"),
        delegate_depth=1,
    )
    (tmp_path / "ws").mkdir(exist_ok=True)
    agent.dispatcher.set_child_execution_policy(
        __import__(
            "openai4s.host.delegation_policy", fromlist=["child_execution_policy"]
        ).child_execution_policy({"capabilities": ["skills"]})
    )
    result = agent.run("read the gene_opt references and report their markers")

    assert result["stop_reason"] == "submitted", result
    output = result["submitted_output"]["output"]
    assert CONTRACT_MARKER in output["contract"]
    assert GA_MARKER in output["ga"]


def test_reading_a_reference_is_stepped_apart_from_loading_a_recipe():
    """Both used to render as a `skill` step ending in "loaded". A card that
    says the same thing for "pulled the whole recipe into context" and "read
    one reference file" hides which one happened."""
    from openai4s.host_dispatch import _step_begin, _step_end

    kind, title, payload = _step_begin(
        "skills_read", [{"name": "gene_opt", "path": "references/ga.md"}]
    )
    assert kind == "skill"
    assert title == "Reading gene_opt/references/ga.md"
    assert payload == {"name": "gene_opt", "path": "references/ga.md"}

    loading = _step_begin("load_skill", ["gene_opt"])
    assert loading[1] == "Loading gene_opt skill guidance"

    output, summary = _step_end("skills_read", "skill", "x" * 120, True)
    assert output["chars"] == 120
    assert "read" in summary
    assert "loaded" not in summary


def test_the_sdk_spelling_and_the_native_spelling_return_the_same_bytes(tmp_path):
    """One host method, two doors. A divergence here would mean the tool plane
    and the cell plane disagree about what a Skill's reference says."""
    from openai4s.tools import execute_tool_call

    cfg = _cfg(tmp_path)
    dispatcher = build_dispatcher(cfg)
    through_rpc = dispatcher(
        "skills_read", [{"name": "gene_opt", "path": "references/ga.md"}]
    )
    through_tool, ok = execute_tool_call(
        dispatcher,
        {
            "name": "read_skill_file",
            "arguments": {"name": "gene_opt", "path": "references/ga.md"},
        },
        dispatcher.tool_catalog(),
    )
    assert ok
    assert GA_MARKER in through_rpc
    # The native door adds only a tool header; the bytes underneath are the
    # same file the SDK door returns.
    assert through_tool.endswith(through_rpc)
    assert through_tool.startswith("[Tool: read_skill_file]")
