"""What a Web-Customize edit does to the rest of a skill's frontmatter.

Saving a skill rebuilt its frontmatter from three fields — `name`,
`description`, `origin` — and everything else the author had written was gone.
Across the 34 bundled skills that is `license` and `category` on 23 of them, a
nested `metadata` block on 17, `requirements` on 13, and `fold_cue` on one:
deleted by someone fixing a typo, with nothing said.

`requirements` is what turns a metadata loss into a wrong answer. Readiness is
computed from it, so a skill that declared `[gpu]` and lost it stops reporting
`needs_setup` and starts reporting `ready` — it now claims it can run on a
machine where it cannot.
"""

from __future__ import annotations

from openai4s.skills_loader import frontmatter_edit
from openai4s.skills_loader.loader import _has_gpu, _parse_frontmatter, skill_readiness

ORIGINAL = """---
name: cryo-em-refine
description: Refine a cryo-EM map
origin: user
requirements: [gpu, cuda]
license: CC-BY-4.0
category: structural-biology
metadata:
  third_party:
    - name: RELION
      license: GPL-2.0
---

Original body.
"""


def _edited(**overrides):
    fields = {
        "name": "cryo-em-refine",
        "description": "Refine a cryo-EM map (v2)",
        "origin": "user",
        "body": "Edited body.",
    }
    fields.update(overrides)
    return frontmatter_edit.rewrite(ORIGINAL, **fields)


def test_editing_a_description_keeps_every_other_field():
    """The defect, stated directly."""
    before, _ = _parse_frontmatter(ORIGINAL)
    after, _ = _parse_frontmatter(_edited())
    assert set(before) == set(after)
    assert after["description"] == "Refine a cryo-EM map (v2)"


def test_the_field_that_makes_this_a_correctness_bug_survives():
    after, _ = _parse_frontmatter(_edited())
    assert after["requirements"] == "[gpu, cuda]"


def test_a_nested_block_survives_verbatim():
    """`_parse_frontmatter` ignores indented lines, so a fix that parsed and
    re-emitted would keep the keys it understands and silently drop the
    structure it does not — the same bug, narrower and harder to see. The
    nested block also contains its own `name:` and `license:`, which a
    line-oriented replace would happily mistake for the top-level ones.
    """
    out = _edited()
    assert "    - name: RELION" in out
    assert "      license: GPL-2.0" in out
    # ...and the top-level values are still the top-level ones.
    meta, _ = _parse_frontmatter(out)
    assert meta["name"] == "cryo-em-refine"
    assert meta["license"] == "CC-BY-4.0"


def test_the_body_comes_from_the_form_not_the_file():
    _meta, body = _parse_frontmatter(_edited())
    assert body.strip() == "Edited body."
    assert "Original body" not in _edited()


def test_a_block_scalar_description_is_replaced_with_its_continuation():
    """`description: >` owns the indented lines beneath it. Replacing only the
    first line would strand that text at the top level, where the next parse
    reads it as garbage — or as a key."""
    original = (
        "---\n"
        "name: s\n"
        "description: >\n"
        "  a long description\n"
        "  spread over lines\n"
        "license: MIT\n"
        "---\n\nBody.\n"
    )
    out = frontmatter_edit.rewrite(
        original, name="s", description="short now", origin="user", body="Body."
    )
    assert "spread over lines" not in out
    meta, _ = _parse_frontmatter(out)
    assert meta["description"] == "short now"
    assert meta["license"] == "MIT"


def test_a_brand_new_skill_still_gets_the_three_fields():
    out = frontmatter_edit.rewrite(
        "", name="fresh", description="d", origin="draft", body="Hello."
    )
    meta, body = _parse_frontmatter(out)
    assert meta == {"name": "fresh", "description": "d", "origin": "draft"}
    assert body.strip() == "Hello."


def test_editing_twice_does_not_accumulate_anything():
    """A save is not rare. Anything this adds per edit — a blank line, a
    duplicated key — compounds until the file is unreadable."""
    once = _edited()
    twice = frontmatter_edit.rewrite(
        once,
        name="cryo-em-refine",
        description="Refine a cryo-EM map (v2)",
        origin="user",
        body="Edited body.",
    )
    assert once == twice


def test_an_unknown_future_field_is_kept_too():
    """The reason this preserves raw lines rather than a known list: the
    vocabulary grows, and a rebuild-from-known-fields breaks every time it
    does, silently."""
    original = (
        "---\nname: s\ndescription: d\norigin: user\nnot_invented_yet: 42\n---\n\nB.\n"
    )
    out = frontmatter_edit.rewrite(
        original, name="s", description="d2", origin="user", body="B."
    )
    assert "not_invented_yet: 42" in out


IMPORT_SEED = """---
name: cryo-import
description: Import me whole
origin: draft
requirements: [gpu, cuda]
capabilities:
  network:
    mode: host_only
    domains:
      - api.openalex.org
      - doi.org
license: CC-BY-4.0
category: structural-biology
# keep this comment
x-vendor-ext:
  nested:
    keep: me
    list:
      - one
      - two
---

Original imported body.
"""


def test_import_rewrite_keeps_unknown_nested_keys_comments_and_network():
    """The import seed is the pasted document. Empty original is the bug:
    rewrite would then emit only name/description/origin and this fixture
    would still pass if it only asserted those three."""
    out = frontmatter_edit.rewrite_import(
        IMPORT_SEED,
        name="cryo-import",
        description="Import me whole",
        body="Original imported body.",
    )
    meta, body = _parse_frontmatter(out)
    assert meta["name"] == "cryo-import"
    assert meta["origin"] == "user"
    assert meta["origin"] != "draft"
    assert meta["requirements"] == "[gpu, cuda]"
    assert meta["license"] == "CC-BY-4.0"
    assert meta["category"] == "structural-biology"
    assert "mode: host_only" in out
    assert "api.openalex.org" in out
    assert "doi.org" in out
    assert "# keep this comment" in out
    assert "x-vendor-ext:" in out
    assert "    keep: me" in out
    assert "      - two" in out
    assert body.strip() == "Original imported body."


def test_import_rewrite_from_empty_seed_cannot_invent_dropped_fields():
    """If import forgets to pass the pasted document, nothing but the
    owned fields exists to keep. This is the hollow-test trap: a fixture
    with only name/description/body is green either way."""
    out = frontmatter_edit.rewrite_import(
        "",
        name="cryo-import",
        description="Import me whole",
        body="Original imported body.",
    )
    assert "requirements:" not in out
    assert "x-vendor-ext:" not in out
    assert "license:" not in out


# --------------------------------------------------------------------------
# through the service a user actually reaches
# --------------------------------------------------------------------------


def test_a_real_save_does_not_strip_a_skills_requirements(tmp_path):
    """End to end, because the unit above proves the helper is right and
    proves nothing about whether the save path calls it."""
    from openai4s.config import Config
    from openai4s.server.skills import SkillCustomizationService
    from openai4s.skills_loader import SkillLoader

    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    service = SkillCustomizationService(SkillLoader(cfg=config))

    service.create_or_update("gpu-thing", "first description", "# Recipe\nRun it.\n")
    document = next((tmp_path / "data").rglob("gpu-thing/SKILL.md"))
    document.write_text(
        document.read_text("utf-8").replace(
            "origin: user",
            "origin: user\nrequirements: [gpu]\nlicense: MIT",
        ),
        "utf-8",
    )

    service.create_or_update(
        "gpu-thing", "second description", "# Recipe\nRun it faster.\n", existing=True
    )
    saved = document.read_text("utf-8")
    meta, _body = _parse_frontmatter(saved)
    assert meta["description"] == "second description"
    assert (
        meta["requirements"] == "[gpu]"
    ), "editing the description dropped the GPU requirement"
    assert meta["license"] == "MIT"


def test_readiness_does_not_flip_to_ready_because_of_an_edit(tmp_path):
    """The consequence, asserted as behaviour rather than as a stored string:
    a skill that needs a GPU must not start claiming it can run anywhere
    because somebody fixed a typo."""
    from openai4s.config import Config
    from openai4s.server.skills import SkillCustomizationService
    from openai4s.skills_loader import SkillLoader

    bundled = tmp_path / "bundled-skills"
    bundled.mkdir()
    config = Config(data_dir=tmp_path / "data", skills_dir=bundled)
    loader = SkillLoader(cfg=config)
    service = SkillCustomizationService(loader)

    service.create_or_update("needs-gpu", "before", "# Recipe\nx\n")
    document = next((tmp_path / "data").rglob("needs-gpu/SKILL.md"))
    document.write_text(
        document.read_text("utf-8").replace(
            "origin: user", "origin: user\nrequirements: [gpu]"
        ),
        "utf-8",
    )

    service.create_or_update("needs-gpu", "after", "# Recipe\nx\n", existing=True)
    # A fresh loader, so this reads what is on disk rather than a cached object.
    reloaded = SkillLoader(cfg=config)
    skill = reloaded.get("needs-gpu", include_disabled=True)
    assert skill is not None
    assert "gpu" in [str(r).lower() for r in (skill.requirements or [])]
    # ...and readiness still reflects it. `skill_readiness` is a module
    # function over the requirement list, so this asserts the consequence
    # rather than restating the stored string.
    state = skill_readiness(skill.requirements)["state"]
    assert state == ("ready" if _has_gpu() else "needs_setup")
