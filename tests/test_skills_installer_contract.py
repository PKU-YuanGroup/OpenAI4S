"""The assumptions the npm-side Skill installer makes about this repository.

`tools/skills-installer/` is JavaScript, and its own behaviour is gated by
`node tools/skills-installer/selftest.mjs`. What that self-test cannot own is
the *other* half of the contract: the shape of `skills/` and the packaging
manifest that decides what a published package contains. Both live on this side
of the language boundary and both drift silently — a Skill added without
`SKILL.md`, a `bin` path renamed, a `files` glob that stops matching — so they
are asserted here, in the suite that runs on every commit with nothing but
Python installed.

Deliberately NOT here: anything that would require Node. A test that shells out
to `node` reports success by skipping on the machine that does not have it,
which is the failure mode the marker policy exists to prevent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.skills

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"
PACKAGE_JSON = ROOT / "package.json"

#: The installer's discovery rule, restated: a directory is a Skill when it
#: holds this file, and a collection when it holds the marker below with its
#: members one level down. Both names are also what `openai4s/skills_loader/
#: loader.py` looks for; a rename on either side must break something.
SKILL_MARKER = "SKILL.md"
COLLECTION_MARKER = "COLLECTION.json"


def _package() -> dict:
    return json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))


def _skill_dirs() -> list[Path]:
    found: list[Path] = []
    for child in sorted(SKILLS.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / COLLECTION_MARKER).is_file():
            found.extend(
                member
                for member in sorted(child.iterdir())
                if member.is_dir() and (member / SKILL_MARKER).is_file()
            )
            continue
        if (child / SKILL_MARKER).is_file():
            found.append(child)
    return found


def test_the_published_description_counts_the_skills_it_actually_ships():
    """The count npm shows a stranger is the one nothing was checking.

    Three hand-written counts of the same library drifted apart: this
    description said 603, `install.mjs` said 602, and the tree held 604 — none
    of them wrong when they were written, all of them wrong together. A number
    in prose that no gate reads has a shelf life, so this is the gate: adding a
    Skill now fails here until the published description follows. The comment
    in `install.mjs` dropped its number instead, because nothing can check a
    comment and a second uncheckable count is what produced this.
    """

    description = _package()["description"]
    stated = re.search(r"(\d+)\s+scientific recipes", description)
    assert stated, f"description no longer states a recipe count: {description!r}"
    assert int(stated.group(1)) == len(_skill_dirs()), (
        f"package.json advertises {stated.group(1)} recipes; "
        f"skills/ holds {len(_skill_dirs())}"
    )


def test_the_installer_can_name_every_bundled_skill():
    """Every Skill the loader exposes must be one the installer can copy.

    The installer addresses a Skill by its directory name and proves it is a
    Skill by `SKILL.md`. A bundled directory that satisfies the loader through
    some other route would be listed by the daemon and unreachable from
    `npx openai4s-skills install <name>` -- present in one catalogue and absent
    from the other, with nothing saying so.
    """
    dirs = _skill_dirs()
    assert len(dirs) >= 500, f"only {len(dirs)} Skills discovered under {SKILLS}"
    for skill in dirs:
        assert (skill / SKILL_MARKER).is_file()
        assert (
            skill.name == skill.name.strip()
        ), f"padded directory name: {skill.name!r}"
        assert "/" not in skill.name and "\\" not in skill.name


def test_every_skill_document_starts_with_frontmatter():
    """The installer reads `name` and `description` out of the leading `---`
    block to list a Skill. A document without one still installs, but lists as
    a bare directory name with no summary -- a catalogue entry that says
    nothing, which is how a user picks the wrong recipe."""
    missing = [
        str(skill.relative_to(ROOT))
        for skill in _skill_dirs()
        if not (skill / SKILL_MARKER)
        .read_text(encoding="utf-8", errors="replace")
        .lstrip()
        .startswith("---")
    ]
    assert not missing, missing


def test_the_skill_tree_contains_no_symlinks():
    """The installer refuses link members when it extracts an archive, and
    copies file-by-file when it does not. A symlink inside `skills/` would be
    resolved and its *target's* bytes written into the user's directory -- a
    file that came from outside the tree the manifest claims to describe."""
    links = [
        str(path.relative_to(ROOT)) for path in SKILLS.rglob("*") if path.is_symlink()
    ]
    assert not links, links


def test_the_npm_manifest_points_at_a_command_that_exists():
    package = _package()
    assert package["name"] == "openai4s-skills"
    binaries = package["bin"]
    assert binaries, "the package declares no command, so `npx` has nothing to run"
    for name, target in binaries.items():
        path = ROOT / target
        assert path.is_file(), f"bin {name} -> {target} does not exist"
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first.startswith("#!"), f"bin {name} has no shebang"


def test_the_npm_manifest_ships_the_command_and_the_skills():
    """`files` is the entire contract between this tree and what `npx` gets.

    It is a list of globs with nothing checking it against the repository, and
    the way it fails is not a crash: drop `skills/` and the published command
    still runs, finds nothing bundled, and silently falls back to downloading
    a 100 MB source tarball on every install.
    """
    package = _package()
    shipped = package["files"]
    target = next(iter(package["bin"].values()))
    assert any(
        target.startswith(entry.rstrip("/") + "/") or entry == target
        for entry in shipped
        if not entry.startswith("!")
    ), f"`files` does not ship {target}"
    assert any(
        entry.rstrip("/") == "skills" for entry in shipped if not entry.startswith("!")
    ), "`files` does not ship skills/"


def test_the_npm_package_is_not_shipped_inside_the_python_wheel():
    """Two distributions, one tree. `tools/` is Node and must stay out of the
    wheel: `pip install openai4s` promising a stdlib-only core and then
    carrying JavaScript is a claim the packaging would quietly break."""
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    include = pyproject.split("[tool.setuptools.packages.find]", 1)[1]
    include = include.split("[", 1)[0]
    assert '"tools' not in include, "tools/ is being packaged into the wheel"


def test_node_and_python_agree_on_the_supported_node_floor():
    """The CLI refuses to run below the floor `package.json` advertises. Two
    numbers that can disagree are one number and one lie."""
    package = _package()
    declared = package["engines"]["node"]
    cli = (ROOT / "tools" / "skills-installer" / "cli.mjs").read_text(encoding="utf-8")
    assert "MIN_NODE_MAJOR = 18" in cli
    assert declared == ">=18", declared
