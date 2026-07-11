"""Skill loader + example_stats sidecar tests (offline)."""
import ast
from pathlib import Path

import pytest

from openai4s.skills_loader import SkillLoader
from openai4s.skills_loader.loader import _parse_frontmatter


def test_discovers_example_stats():
    skills = SkillLoader().discover()
    assert "example_stats" in skills
    s = skills["example_stats"]
    assert s.has_kernel
    assert "example_stats.kernel" in (s.import_hint or "")


def test_frontmatter_parsed():
    s = SkillLoader().discover()["example_stats"]
    assert s.origin == "personal"
    assert "descriptive" in s.description.lower()
    # keywords tokenized from name/description/body
    assert "quantile" in s.keywords


def test_system_context_is_progressive():
    ctx = SkillLoader().system_context()
    # name + one-line summary present
    assert "example_stats" in ctx
    assert "summary" in ctx
    # progressive disclosure: instructs retrieval, not full-doc dump
    assert "search_skills" in ctx


def test_bootstrap_code_adds_skills_path():
    boot = SkillLoader().bootstrap_code()
    assert "sys.path" in boot
    assert "skills" in boot


def test_sidecar_functions():
    # skills dir is importable in-process for this assertion
    import sys

    from openai4s.config import get_config

    sys.path.insert(0, str(get_config().skills_dir))
    from example_stats.kernel import correlation, quantile, summary, zscore

    s = summary([10, 20, 30, 40, 50])
    assert s["mean"] == 30.0
    assert s["median"] == 30.0
    assert quantile([10, 20, 30, 40, 50], 0.9) == 46.0
    assert correlation([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    z = zscore([1, 2, 3])
    assert z[1] == pytest.approx(0.0, abs=1e-9)


def test_sidecar_raises_on_empty():
    import sys

    from openai4s.config import get_config

    sys.path.insert(0, str(get_config().skills_dir))
    from example_stats.kernel import summary

    with pytest.raises(ValueError):
        summary([])


# ---- progressive-disclosure retrieval -----------------------------------


def test_search_matches_by_keyword():
    loader = SkillLoader()
    hits = loader.search("compute correlation and zscore of numbers")
    assert hits and hits[0]["name"] == "example_stats"
    # search returns the FULL doc for use, plus the sidecar gate
    assert "summary" in hits[0]["doc"]
    assert hits[0]["sidecar_gate"]["ok"] is True


def test_search_no_match_returns_empty():
    assert SkillLoader().search("quantum chromodynamics lattice gauge") == []


def test_sidecar_gate_ok_for_example():
    s = SkillLoader().discover()["example_stats"]
    assert s.sidecar_gate() == {"ok": True, "error": None}


# ---- lifecycle CRUD via the host dispatcher ------------------------------


def test_skills_crud_roundtrip(tmp_path, monkeypatch):
    """Create a draft skill, gate a broken sidecar, publish, then delete."""
    from openai4s.config import get_config
    from openai4s.host_dispatch import build_dispatcher

    cfg = get_config()
    monkeypatch.setattr(cfg, "data_dir", tmp_path)
    # point skills_dir at a temp location so we don't touch the real one
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(cfg, "skills_dir", skills_dir)

    disp = build_dispatcher(cfg)
    for tool in ("skills_publish", "skills_delete"):
        disp.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=tool,
            pattern="*",
            decision="allow",
        )

    # create a draft skill's SKILL.md
    r = disp(
        "skills_edit",
        [
            {
                "name": "demo",
                "path": "SKILL.md",
                "content": "---\nname: demo\norigin: draft\n---\n# demo\nadds numbers",
                "old_string": None,
            }
        ],
    )
    assert r["ok"] and r["mode"] == "overwrite"
    assert Path(r["path"]).is_relative_to(tmp_path / "user-skills")
    assert not (skills_dir / "demo").exists()

    # write a BROKEN sidecar -> gate should report not ok
    r2 = disp(
        "skills_edit",
        [
            {
                "name": "demo",
                "path": "kernel.py",
                "content": "def add(a, b)\n    return a+b\n",  # missing colon
                "old_string": None,
            }
        ],
    )
    assert r2["sidecar_gate"]["ok"] is False

    # fix the sidecar -> gate ok
    r3 = disp(
        "skills_edit",
        [
            {
                "name": "demo",
                "path": "kernel.py",
                "content": "def add(a, b):\n    return a + b\n",
                "old_string": None,
            }
        ],
    )
    assert r3["sidecar_gate"]["ok"] is True

    # it starts as draft; publish -> personal
    disp("skills_publish", ["demo"])
    meta = disp("skills_get", ["demo"])
    assert meta["origin"] == "personal"

    # listed in catalog
    names = [c["name"] for c in disp("skills_list", [])]
    assert "demo" in names

    # delete
    assert disp("skills_delete", ["demo"])["ok"] is True
    names2 = [c["name"] for c in disp("skills_list", [])]
    assert "demo" not in names2


def test_skills_read_only_origin_blocked(tmp_path, monkeypatch):
    from openai4s.config import get_config
    from openai4s.host_dispatch import build_dispatcher

    cfg = get_config()
    skills_dir = tmp_path / "skills"
    (skills_dir / "vendor").mkdir(parents=True)
    (skills_dir / "vendor" / "SKILL.md").write_text(
        "---\nname: vendor\norigin: openai4s\n---\n# vendor\n", "utf-8"
    )
    monkeypatch.setattr(cfg, "skills_dir", skills_dir)

    disp = build_dispatcher(cfg)
    # Exercise the service's immutable-origin guard rather than stopping at
    # the production default approval gate for destructive skill deletion.
    disp.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="skills_delete",
        pattern="*",
        decision="allow",
    )
    with pytest.raises(PermissionError):
        disp("skills_delete", ["vendor"])
    with pytest.raises(PermissionError):
        disp(
            "skills_edit",
            [
                {
                    "name": "vendor",
                    "path": "SKILL.md",
                    "content": "x",
                    "old_string": None,
                }
            ],
        )


# ---- frontmatter parsing: folded / literal / quoted scalars --------------


def test_folded_description_not_literal_gt():
    """`description: >` folded block scalars must parse to real text, never `>`."""
    meta, _ = _parse_frontmatter(
        "---\n"
        "name: demo\n"
        "description: >\n"
        "  First folded line describing the\n"
        "  skill across two source lines.\n"
        "origin: openai4s\n"
        "---\n"
        "# body\n"
    )
    assert meta["description"] != ">"
    assert meta["description"] == (
        "First folded line describing the skill across two source lines."
    )
    assert meta["origin"] == "openai4s"  # key after the block still parsed


def test_literal_description_preserves_newlines():
    meta, _ = _parse_frontmatter(
        "---\n" "name: demo\n" "description: |\n" "  line one\n" "  line two\n" "---\n"
    )
    assert meta["description"] == "line one\nline two"


def test_folded_chomping_indicator_accepted():
    meta, _ = _parse_frontmatter("---\ndescription: >-\n  hello\n  world\n---\n")
    assert meta["description"] == "hello world"


def test_quoted_description_strips_quotes_keeps_hash():
    meta, _ = _parse_frontmatter(
        '---\nname: demo\ndescription: "read a #tag off a chart"\n---\n'
    )
    assert meta["description"] == "read a #tag off a chart"


def test_inline_comment_stripped_on_unquoted_scalar():
    meta, _ = _parse_frontmatter("---\norigin: openai4s  # trusted\n---\n")
    assert meta["origin"] == "openai4s"


def test_no_bundled_skill_summary_is_literal_gt():
    """Regression: folded-scalar skills must not show up as `>` in the catalog."""
    loader = SkillLoader()
    loader.discover()
    for c in loader.catalog():
        assert c["description"] != ">", f"{c['name']} summary is literal '>'"
        assert c["description"], f"{c['name']} has an empty summary"


# ---- import-hint validity for hyphenated skill dirs ----------------------


def test_all_import_hints_are_valid_python():
    """Every kernel-bearing skill's import hint must be executable Python,
    including hyphenated dirs like `pdf-explore` (import * would be a
    SyntaxError there)."""
    loader = SkillLoader()
    for s in loader.discover().values():
        hint = s.import_hint
        if hint is None:
            assert not s.has_kernel
            continue
        # strip a trailing ` # comment` and any ` # or: ...` alt form
        code = hint.split(" #", 1)[0]
        ast.parse(code)  # raises SyntaxError if the hint is invalid Python


def test_hyphenated_skill_uses_importlib_hint(tmp_path, monkeypatch):
    from openai4s.config import get_config

    cfg = get_config()
    skills_dir = tmp_path / "skills"
    d = skills_dir / "pdf-explore"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: pdf-explore\norigin: openai4s\n---\n# pdf\n", "utf-8"
    )
    (d / "kernel.py").write_text("X = 1\n", "utf-8")
    monkeypatch.setattr(cfg, "skills_dir", skills_dir)

    s = SkillLoader(cfg=cfg).discover()["pdf-explore"]
    assert s.has_kernel
    hint = s.import_hint
    assert "from pdf-explore" not in hint  # not invalid `import *`
    assert "importlib.import_module" in hint
    ast.parse(hint.split(" #", 1)[0])


def test_identifier_skill_uses_import_star_hint():
    s = SkillLoader().discover()["example_stats"]
    assert s.import_hint.startswith("from example_stats.kernel import *")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
