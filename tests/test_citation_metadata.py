"""The paper citation is written three times and must say the same thing.

The arXiv BibTeX sits in `README.md` and `README_zh.md`. `CITATION.cff`
carries the same paper as `preferred-citation`, which is what GitHub's "Cite
this repository" button renders. Nothing else checks the citation in any of
the three, so an author added to one copy and not the others would ship
without complaint.

The README entries are compared by BibTeX meaning, not by bytes: protective
braces, field order and line wrapping do not change what BibTeX reads, so a
reformat stays green and `{OpenAI4S Community}` may be braced or not.

The CFF checks follow ruby-cff, the library GitHub Docs names as its parser:

* The `generic` reference type renders as `@misc`, the entry type the READMEs
  use.
* A `name:` author renders as one braced entity. "OpenAI4S Community" written
  with given-names and family-names would come out as "Community, OpenAI4S".
* A person renders from given-names, family-names, name-particle,
  name-suffix and alias. Only the first two may appear here, so the README's
  "Given Family" form is exactly what GitHub prints.
* `repository-code` takes precedence over `url` in the BibTeX output. Inside
  `preferred-citation` it would replace the arXiv link with the repository
  link.
* `month`, `status`, `notes`, `start`/`end` and the date fields all add to or
  change the rendered entry, which the README entry would then not match.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
CFF = ROOT / "CITATION.cff"
PYPROJECT = ROOT / "pyproject.toml"
READMES = (ROOT / "README.md", ROOT / "README_zh.md")

README_FIELDS = {
    "title",
    "author",
    "year",
    "eprint",
    "archiveprefix",
    "primaryclass",
    "url",
}
#: Reference keys ruby-cff reads for a `generic` entry beyond the ones the
#: README carries. Any of them would make GitHub's citation differ from it.
RENDER_CHANGING_KEYS = {
    "repository-code",
    "month",
    "status",
    "notes",
    "start",
    "end",
    "date-released",
    "date-published",
}
PERSON_NAME_KEYS = {"given-names", "family-names"}
FORBIDDEN_PERSON_KEYS = {"name-particle", "name-suffix", "alias"}
#: `definitions.identifier` in the CFF 1.2.0 schema.
IDENTIFIER_TYPES = {"doi", "url", "swh", "other"}

_BIBTEX_BLOCK = re.compile(r"^```bibtex[ \t]*\n(.*?)^```", re.S | re.M)


def _bibtex_block(readme: Path) -> str:
    blocks = _BIBTEX_BLOCK.findall(readme.read_text(encoding="utf-8"))
    assert len(blocks) == 1, f"{readme.name}: expected one bibtex block"
    return blocks[0]


def _braced(text: str, start: int) -> tuple[str, int]:
    """The contents of the balanced `{...}` opening at `start`, and its end."""
    assert text[start] == "{", f"expected '{{' at offset {start}"
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
    raise AssertionError("unbalanced braces in bibtex entry")


def _parse_bibtex(block: str) -> tuple[str, str, dict[str, str]]:
    head = re.match(r"\s*@(\w+)\s*\{\s*([^,\s]+)\s*,", block)
    assert head, "unparsed bibtex entry head"
    body, end = _braced(block, block.index("{", head.start(1)))
    assert not block[end:].strip(), "text after the bibtex entry"
    fields: dict[str, str] = {}
    position = body.index(",") + 1
    field = re.compile(r"\s*(\w+)\s*=\s*")
    while body[position:].strip():
        match = field.match(body, position)
        assert match, f"unparsed bibtex field near {body[position:position + 40]!r}"
        name = match.group(1).lower()
        assert name not in fields, f"duplicate bibtex field {name}"
        value, position = _braced(body, match.end())
        fields[name] = " ".join(value.split())
        trailer = re.match(r"\s*,?", body[position:])
        position += trailer.end() if trailer else 0
    return head.group(1).lower(), head.group(2), fields


def _unbraced(value: str) -> str:
    return " ".join(value.replace("{", "").replace("}", "").split())


def _authors(value: str) -> list[str]:
    """Split a BibTeX author list on ` and ` outside braces."""
    names, depth, current = [], 0, ""
    for token in re.split(r"(\{|\}|\s+and\s+)", value):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        elif re.fullmatch(r"\s+and\s+", token or "") and depth == 0:
            names.append(current)
            current = ""
            continue
        current += token or ""
    names.append(current)
    return [_unbraced(name) for name in names]


def _render(author: dict) -> str:
    if "name" in author:
        return author["name"]
    return f"{author['given-names']} {author['family-names']}"


def _pyproject(key: str) -> str:
    """One single-line value from `[project]`/`[project.urls]`.

    Read as text rather than with `tomllib`, which is 3.11+ while the suite
    also runs on 3.10.
    """
    match = re.search(rf"^{re.escape(key)} = (.+)$", PYPROJECT.read_text("utf-8"), re.M)
    assert match, f"pyproject.toml has no single-line {key}"
    return match.group(1).strip()


@pytest.fixture(scope="module")
def cff() -> dict:
    return yaml.safe_load(CFF.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def bibtex() -> dict[str, str]:
    kind, _key, fields = _parse_bibtex(_bibtex_block(READMES[0]))
    assert kind == "misc"
    assert set(fields) == README_FIELDS
    return fields


def test_readme_halves_carry_the_same_bibtex():
    english, chinese = (_parse_bibtex(_bibtex_block(path)) for path in READMES)
    assert english == chinese


def test_cff_is_a_cff_1_2_0_file(cff):
    assert cff["cff-version"] == "1.2.0"
    for key in ("message", "title", "authors", "preferred-citation"):
        assert cff.get(key), f"CITATION.cff lacks {key}"
    # Not a release pin: the file's own header says so, and this holds it.
    assert "version" not in cff
    assert "date-released" not in cff


def test_cff_software_metadata_matches_pyproject(cff):
    assert cff["license"] == _pyproject("license").strip('"')
    assert cff["abstract"] == _pyproject("description").strip('"')
    keywords = re.findall(r'"([^"]+)"', _pyproject("keywords"))
    assert cff["keywords"] == keywords
    source = _pyproject("Source").strip('"')
    assert cff["repository-code"] == cff["url"] == source


def test_preferred_citation_is_the_readme_paper(cff, bibtex):
    paper = cff["preferred-citation"]
    eprint = bibtex["eprint"]
    assert bibtex["archiveprefix"] == "arXiv"
    assert paper["type"] == "generic"
    assert paper["title"] == _unbraced(bibtex["title"])
    assert [_render(a) for a in paper["authors"]] == _authors(bibtex["author"])
    assert paper["year"] == int(bibtex["year"])
    assert paper["url"] == bibtex["url"] == f"https://arxiv.org/abs/{eprint}"
    assert str(paper["doi"]).lower() == f"10.48550/arxiv.{eprint}".lower()
    assert paper["identifiers"] == [
        {
            "type": "other",
            "value": f"arXiv:{eprint}",
            "description": f"arXiv preprint, primary class {bibtex['primaryclass']}",
        }
    ]


def test_cff_renders_the_way_github_reads_it(cff):
    paper = cff["preferred-citation"]
    assert not RENDER_CHANGING_KEYS & set(paper)
    assert {i["type"] for i in paper["identifiers"]} <= IDENTIFIER_TYPES
    for authors in (cff["authors"], paper["authors"]):
        entities = {a["name"] for a in authors if "name" in a}
        assert entities == {"OpenAI4S Community"}
        for author in authors:
            if "name" in author:
                assert not PERSON_NAME_KEYS & set(author), author
                continue
            assert PERSON_NAME_KEYS <= set(author), author
            assert not FORBIDDEN_PERSON_KEYS & set(author), author


def test_software_authors_match_the_paper(cff):
    assert [_render(a) for a in cff["authors"]] == [
        _render(a) for a in cff["preferred-citation"]["authors"]
    ]
