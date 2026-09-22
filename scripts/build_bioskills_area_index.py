#!/usr/bin/env python3
"""Build the bioSkills area index used by the skills.suggest judgment template.

Groups every ``skills/bioskills/`` member by the ``bio-<area>-`` directory
prefix. Labels and one-line descriptions are derived from member names and
frontmatter descriptions — nothing is invented. The same inputs always write
the same JSON.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
BIOSKILLS_ROOT = REPO_ROOT / "skills" / "bioskills"
MANIFEST_PATH = BIOSKILLS_ROOT / "MANIFEST.json"
OUTPUT_PATH = REPO_ROOT / "openai4s" / "judgment" / "templates" / "bioskills_areas.json"

INDEX_VERSION = "1"
MAX_AREA_MEMBERS = 254
MAX_DESCRIPTION_CHARS = 200
MEMBER_PREFIX = "bio-"

# Spellings that may replace a slug only when they actually occur in the
# members' names or descriptions (case-insensitive).
_CORPUS_SPELLINGS: tuple[tuple[str, str], ...] = (
    ("hi-c", "Hi-C"),
    ("atac-seq", "ATAC-seq"),
    ("chip-seq", "ChIP-seq"),
    ("clip-seq", "CLIP-seq"),
    ("ribo-seq", "Ribo-seq"),
    ("single-cell", "single-cell"),
    ("tcr-bcr", "TCR/BCR"),
    ("copy-number", "copy-number"),
    ("long-read", "long-read"),
    ("multi-omics", "multi-omics"),
    ("small-rna", "small-rna"),
    ("flow-cytometry", "flow cytometry"),
    ("liquid-biopsy", "liquid biopsy"),
    ("machine-learning", "machine learning"),
    ("spatial-transcriptomics", "spatial transcriptomics"),
    ("structural-biology", "structural biology"),
    ("systems-biology", "systems biology"),
    ("population-genetics", "population genetics"),
    ("workflow-management", "workflow management"),
    ("differential-expression", "differential expression"),
    ("variant-calling", "variant calling"),
    ("causal-genomics", "causal genomics"),
    ("comparative-genomics", "comparative genomics"),
    ("ecological-genomics", "ecological genomics"),
    ("epidemiological-genomics", "epidemiological genomics"),
    ("temporal-genomics", "temporal genomics"),
    ("alternative-splicing", "alternative splicing"),
    ("experimental-design", "experimental design"),
    ("expression-matrix", "expression matrix"),
    ("database-access", "database access"),
    ("data-visualization", "data visualization"),
    ("methylation-analysis", "methylation"),
    ("pathway-analysis", "pathway analysis"),
    ("phasing-imputation", "phasing and imputation"),
    ("primer-design", "primer design"),
    ("restriction-analysis", "restriction analysis"),
    ("imaging-mass-cytometry", "imaging mass cytometry"),
    ("gene-regulatory", "gene regulatory networks"),
    ("crispr-screens", "CRISPR screens"),
)

_GENERIC_TRAILING = frozenset({"analysis"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_frontmatter(text: str) -> dict[str, str]:
    """Top-level ``key: scalar`` frontmatter. Bioskills descriptions are one line."""

    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    meta: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if not line or line[0] in " \t#-":
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        meta[key] = value.strip().strip('"').strip("'")
    return meta


def longest_common_prefix(token_lists: Sequence[Sequence[str]]) -> list[str]:
    if not token_lists:
        return []
    prefix: list[str] = []
    for column in zip(*token_lists):
        if len(set(column)) != 1:
            break
        prefix.append(column[0])
    return prefix


def _corpus_contains(haystack: str, needle: str) -> bool:
    return needle.casefold() in haystack.casefold()


def format_label(slug: str, corpus: str) -> str:
    """Turn a directory slug into a readable label attested by the corpus."""

    chosen = slug
    for key, pretty in _CORPUS_SPELLINGS:
        if slug == key or slug.startswith(key + "-"):
            if _corpus_contains(corpus, pretty) or _corpus_contains(corpus, key):
                rest = slug[len(key) :].strip("-")
                rest_tokens = [
                    token
                    for token in rest.split("-")
                    if token and token not in _GENERIC_TRAILING
                ]
                chosen = pretty
                if rest_tokens:
                    chosen = pretty + " / " + " ".join(rest_tokens)
                break
    else:
        chosen = slug.replace("-", " ")
    return chosen


def clip_description(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_DESCRIPTION_CHARS:
        return collapsed
    return collapsed[: MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."


def area_description(label: str, count: int, tails: Sequence[str]) -> str:
    if tails:
        topics = ", ".join(tails)
        text = f"{count} bioSkills recipes for {label}: {topics}."
    else:
        text = f"{count} bioSkills recipes for {label}."
    return clip_description(text)


def iter_members(root: Path) -> Iterable[dict[str, Any]]:
    for child in sorted(root.iterdir(), key=lambda path: path.name):
        if not child.is_dir() or not child.name.startswith(MEMBER_PREFIX):
            continue
        tokens = child.name.split("-")
        if len(tokens) < 3:
            continue
        skill_md = child / "SKILL.md"
        meta = parse_frontmatter(
            skill_md.read_text("utf-8") if skill_md.is_file() else ""
        )
        declared = str(meta.get("name") or "").strip() or child.name
        description = str(meta.get("description") or "").strip()
        yield {
            "directory": child.name,
            "area": tokens[1],
            "rest": tokens[2:],
            "name": declared,
            "description": description,
        }


def build_index(
    *,
    bioskills_root: Path = BIOSKILLS_ROOT,
    manifest_path: Path = MANIFEST_PATH,
) -> dict[str, Any]:
    members = list(iter_members(bioskills_root))
    grouped: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        grouped.setdefault(str(member["area"]), []).append(member)

    areas: dict[str, Any] = {}
    for area_id in sorted(grouped):
        group = grouped[area_id]
        rest_lists = [list(item["rest"]) for item in group]
        common = longest_common_prefix(rest_lists)
        slug = "-".join([area_id, *common])
        corpus = " ".join(f"{item['name']} {item['description']}" for item in group)
        label = format_label(slug, corpus)
        tails: list[str] = []
        seen_tails: set[str] = set()
        for item in group:
            leftover = list(item["rest"])[len(common) :]
            if not leftover:
                continue
            tail = "-".join(leftover)
            if tail not in seen_tails:
                seen_tails.add(tail)
                tails.append(tail)
        names = sorted({str(item["name"]) for item in group})
        areas[area_id] = {
            "label": label,
            "description": area_description(label, len(names), tails),
            "members": names,
        }
        if len(names) > MAX_AREA_MEMBERS:
            raise ValueError(
                f"area {area_id!r} has {len(names)} members; "
                f"Choice options cap at {MAX_AREA_MEMBERS} plus none"
            )

    return {
        "version": INDEX_VERSION,
        "source_manifest_sha256": sha256_file(manifest_path),
        "areas": areas,
    }


def dumps_index(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_index(payload: Mapping[str, Any], *, output_path: Path = OUTPUT_PATH) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(dumps_index(payload), encoding="utf-8")
    return output_path


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    if not BIOSKILLS_ROOT.is_dir():
        print(f"missing bioSkills tree: {BIOSKILLS_ROOT}", file=sys.stderr)
        return 1
    if not MANIFEST_PATH.is_file():
        print(f"missing manifest: {MANIFEST_PATH}", file=sys.stderr)
        return 1
    payload = build_index()
    counts = Counter(len(area["members"]) for area in payload["areas"].values())
    write_index(payload)
    n_members = sum(len(area["members"]) for area in payload["areas"].values())
    print(
        f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}: "
        f"{len(payload['areas'])} areas, {n_members} members, "
        f"version={payload['version']}, "
        f"max_area={max(counts) if counts else 0}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
