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
    assert s.read_only is True
    assert "descriptive" in s.description.lower()
    # keywords tokenized from name/description/body
    assert "quantile" in s.keywords


def test_system_context_is_progressive():
    ctx = SkillLoader().system_context()
    # name + one-line summary present
    assert "example_stats" in ctx
    assert "summary" in ctx
    # progressive disclosure: instructs retrieval, not full-doc dump
    assert "native `search_skills` / `load_skill`" in ctx
    assert "Inside a fenced Python Cell" in ctx
    assert "`host.search_skills(...)` / `host.load_skill(...)`" in ctx
    assert "native `list_skills`" in ctx
    assert "`host.skills.list()`" in ctx
    assert "Never use `list_dir` for the Skill catalog" in ctx
    assert "Cell-runner function" in ctx


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
    # The pinned bioSkills corpus is broad enough to mention ordinary physics
    # vocabulary in modeling recipes, so use deliberately nonexistent tokens.
    assert SkillLoader().search("zzqvorth blenxari ptuum") == []


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


def test_declared_name_collision_keeps_bundled_skill_authoritative(tmp_path):
    from openai4s.config import Config

    bundled = tmp_path / "bundled"
    trusted = bundled / "trusted-directory"
    trusted.mkdir(parents=True)
    (trusted / "SKILL.md").write_text(
        "---\nname: Canonical Skill\ndescription: trusted\n"
        "origin: personal\n---\n# Trusted\n",
        "utf-8",
    )
    data_dir = tmp_path / "data"
    forged = data_dir / "user-skills" / "different-directory"
    forged.mkdir(parents=True)
    (forged / "SKILL.md").write_text(
        "---\nname:  canonical   SKILL \ndescription: forged\n"
        "origin: personal\n---\n# Forged\n",
        "utf-8",
    )

    loader = SkillLoader(cfg=Config(data_dir=data_dir, skills_dir=bundled))
    discovered = loader.discover()

    assert set(discovered) == {"trusted-directory"}
    assert discovered["trusted-directory"].origin == "personal"
    assert discovered["trusted-directory"].read_only is True
    assert loader.get("Canonical Skill", include_disabled=True).root == trusted


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


def test_bioskills_collection_is_discovered_but_prompt_is_compact():
    loader = SkillLoader()
    skills = loader.discover()
    imported = [skill for skill in skills.values() if skill.collection == "bioskills"]

    assert len(imported) == 561
    assert loader.get("bio-structural-biology-structure-validation") is not None
    catalog = {row["name"]: row for row in loader.catalog()}
    assert catalog["bio-structural-biology-structure-validation"]["collection"] == (
        "bioskills"
    )
    assert catalog["example_stats"]["collection"] is None
    context = loader.system_context()
    assert "bioSkills collection: 561 pinned third-party" in context
    assert "For ANY bioinformatics task, search this collection" in context
    assert "using English method, tool, data-type" in context
    assert "bio-structural-biology-structure-validation:" not in context


def test_bioskills_exact_load_search_and_scoped_prompt():
    loader = SkillLoader()
    name = "bio-structural-biology-structure-validation"

    assert "R-free" in loader.get(name).doc
    hits = loader.search("MolProbity R-free predicted structure validation", limit=3)
    assert name in {hit["name"] for hit in hits}

    # A scoped specialist gets the collapsed line too, with the count of what
    # IT may load -- not 561, and not one summary line per permitted recipe.
    scoped = loader.system_context(only=frozenset({name}))
    assert f"- {name}:" not in scoped
    assert "bioSkills collection: 1 pinned third-party" in scoped
    assert len(scoped) < 10_000, "a scoped prompt must not re-expand the corpus"


def test_a_wide_search_loses_detail_rather_than_hits():
    """`format_tool_result` truncates from the tail, so an oversized result set
    does not degrade — it deletes the last hits, names and all, mid-JSON.

    With ~18k-char imported recipes, a default `limit=5` search rendered
    101,701 chars against a 20,000-char limit and the model saw one hit. Rank
    the results, then let the transport decide which of them survive, and the
    ranking was for nothing.
    """

    from openai4s.tools.registry import format_tool_result
    from openai4s.tools.skills import SearchSkillsTool

    loader = SkillLoader()
    loader.discover()
    tool = SearchSkillsTool()

    for query, limit in (
        ("rna seq differential expression deseq2", 5),
        ("variant calling gatk best practices", 20),
    ):
        hits = loader.search(query, limit=limit)
        if len(hits) < 2:
            continue
        rendered = format_tool_result(tool, tool.fit_to_budget(hits))
        assert not rendered.endswith(
            "… [truncated]"
        ), f"{query!r} at limit={limit} still overflows the tool observation"
        for hit in hits:
            assert f'"{hit["name"]}"' in rendered, (
                f"{hit['name']} was ranked into the results and then dropped "
                f"by the transport"
            )

    # A result set that already fits is handed back untouched.
    small = loader.search("descriptive statistics mean std quantile", limit=1)
    assert tool.fit_to_budget(small) == small
    # Non-list payloads (a soft-fail {"error": ...}) pass straight through.
    assert tool.fit_to_budget({"error": "nope"}) == {"error": "nope"}


def test_a_specialist_still_retrieves_its_own_skills():
    """The allowlist filters inside the ranking, not after the slice.

    `SkillService.search` used to take the loader's global top 5 and drop the
    names the specialist could not see. Over 36 curated recipes that was "ask
    for 5, get 3". With 561 collection recipes in the same lexical index the
    top 5 can be entirely third-party, so a child whose allowlist is its whole
    reason to exist retrieved nothing at all.
    """

    import tempfile
    from pathlib import Path

    from openai4s.config import Config
    from openai4s.host.skills import SkillService

    cfg = Config(data_dir=Path(tempfile.mkdtemp()))
    cfg.ensure_dirs()
    service = SkillService(cfg)
    service.loader.discover()
    allowed = ["rfdiffusion", "proteinmpnn"]
    if any(service.loader.get(name) is None for name in allowed):
        pytest.skip("the curated protein-design Skills are not present")
    service.set_allowed_skills(allowed)

    # Ranked 6th and 13th globally, so both are past a global top-5 slice.
    for query in (
        "protein structure prediction and design pipeline",
        "alphafold structure prediction of a complex",
    ):
        names = [hit["name"] for hit in service.search({"query": query, "limit": 5})]
        assert names, f"a specialist retrieved nothing for {query!r}"
        assert set(names) <= set(allowed), names


def test_a_curated_skill_is_still_retrievable_by_its_own_query():
    """561 full-text bodies must not push a curated recipe out of reach.

    Rank 1 is not asserted -- a longer, keyword-dense collection document can
    legitimately edge one out -- but a recipe that does not survive into the
    result set at all is one the agent cannot use.
    """

    loader = SkillLoader()
    loader.discover()
    for name, query in (
        ("alphafold2", "alphafold2 colabfold msa protein structure prediction"),
        ("rfdiffusion", "rfdiffusion contigs hotspot backbone generation trb"),
        ("proteinmpnn", "proteinmpnn inverse folding backbone to sequence"),
        ("boltz", "boltz open weights cofolding affinity"),
        ("evaluate-model", "evaluate a model against a benchmark"),
        ("example_stats", "zscore mean std quantile descriptive statistics"),
        ("retrosynthesis_planning", "aizynthfinder retrosynthesis route dashboard"),
    ):
        if loader.get(name) is None:
            continue
        hits = [hit["name"] for hit in loader.search(query, limit=5)]
        assert name in hits, f"{name} fell out of the top 5 for {query!r}: {hits}"


def test_load_refuses_a_near_miss_name_rather_than_answering_with_another_skill():
    """`load_skill` promises one Skill's guidance BY NAME.

    The unguarded fuzzy fallback answered `boltz2` with
    `bio-ml-docking-rescoring` and `alpha-fold2` with a CRISPR screen recipe --
    a different skill's full document under a name the caller never asked for.
    A descriptive phrase may still match on content; a bare token may not.
    """

    import tempfile
    from pathlib import Path

    from openai4s.config import Config
    from openai4s.host.skills import SkillService

    cfg = Config(data_dir=Path(tempfile.mkdtemp()))
    cfg.ensure_dirs()
    service = SkillService(cfg)
    service.loader.discover()

    for typo in ("alpha-fold2", "boltz2", "esmfold"):
        assert service.load(typo) == {"error": f"no such skill: {typo!r}"}
    # The cases the fallback exists for still resolve.
    assert service.load("proteinMPNN")["name"] == "proteinmpnn"
    assert service.load("retrosynthesis")["name"] == "retrosynthesis_planning"
    assert service.load("figure composer")["name"] == "figure-composer"


def test_a_collection_skill_resolves_by_directory_and_by_declared_name():
    """143 imported directories declare a different name than they live under.

    `catalog()`, `search()`, capability state and `system_context(only=...)`
    all key on the declared name; the loader's own map is keyed by directory.
    Both spellings have to reach the same Skill, or an allowlist and a listing
    disagree about what the agent may open.
    """

    loader = SkillLoader()
    skills = loader.discover()
    mismatched = [
        (key, skill)
        for key, skill in skills.items()
        if skill.collection and skill.name != key
    ]
    if not mismatched:
        pytest.skip("no imported Skill declares a name other than its directory")
    directory, skill = mismatched[0]

    assert loader.get(directory) is skill
    assert loader.get(skill.name) is skill
    # The public identity is the declared name, on every agent-facing surface.
    catalog = {row["name"] for row in loader.catalog()}
    assert skill.name in catalog and directory not in catalog
    # An allowlist keys on the declared name -- the collection then collapses
    # to one line counting what this caller may actually load.
    scoped = loader.system_context(only=frozenset({skill.name}))
    assert f"{skill.collection} collection: 1 " in scoped.replace(
        "bioSkills", "bioskills"
    )
    assert loader.system_context(only=frozenset({directory})) == ""


def test_the_pinned_collection_still_matches_its_manifest():
    """`MANIFEST.json` is a claim until something rechecks it.

    `skills/bioskills/README.md` calls it "the authoritative inventory", and
    the tree is excluded from pre-commit and from the directory-README gate —
    so nothing else in CI reads a single one of those 1,962 hashes. Without
    this, the first typo fix, bad merge, or line-ending rewrite leaves the
    manifest asserting a digest the checkout no longer has, with every gate
    still green.
    """

    import importlib.util
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    collection = repo / "skills" / "bioskills"
    if not collection.is_dir():
        pytest.skip("the pinned bioSkills collection is not present")

    spec = importlib.util.spec_from_file_location(
        "import_bioskills", repo / "scripts" / "import_bioskills.py"
    )
    assert spec is not None and spec.loader is not None
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)

    problems = importer.verify_collection(collection)
    assert problems == [], "\n".join(problems)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
