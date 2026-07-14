#!/usr/bin/env python3
"""Validate the source-level contracts for the public bilingual docs site."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

# This is intentionally the same maintained surface as the VitePress sidebar.
# Historical design records remain in Git but are not part of the published Wiki.
PUBLISHED_PAGES = (
    "index.md",
    "architecture.md",
    "architecture/system-context.md",
    "architecture/action-routing.md",
    "architecture/kernels-and-host-rpc.md",
    "architecture/web-runtime.md",
    "architecture/projections-and-persistence.md",
    "architecture/artifacts-and-provenance.md",
    "architecture/checkpoints-and-recovery.md",
    "architecture/failure-boundaries.md",
    "contributing/codebase-map.md",
    "backend-extension-guide.md",
    "contributing/testing.md",
    "release-validation.md",
    "operations/index.md",
    "operations/deployment.md",
    "configuration.md",
    "operations/data-management.md",
    "security.md",
    "operations/security-hardening.md",
    "compute.md",
    "reference/terminology.md",
    "reference/implementation-status.md",
    "reference/documentation-policy.md",
    "webapp.md",
    "webapp-api.md",
    "skills.md",
    "jupyter.md",
)

EXCLUDED_INTERNAL_RECORDS = (
    "backend-refactor-architecture.md",
    "package-architecture.md",
    "plan-corecoder-refactor.md",
    "refactor-plan.md",
)

OUTSIDE_DOC_SOURCE_LINK = re.compile(
    r"\]\((?:\.\./)+(?:openai4s(?:_[^/)]+)?|skills|envs|scripts|tests)(?:[/)])"
)


def _frontmatter(text: str) -> bool:
    return text.startswith("---\n") and "\n---\n" in text[4:]


def _structure(text: str) -> tuple[list[str], list[str], int]:
    headings = re.findall(r"^(#{1,6})\s+", text, flags=re.MULTILINE)
    fences = re.findall(r"^```([^\n]*)$", text, flags=re.MULTILINE)
    table_rows = sum(
        1
        for line in text.splitlines()
        if line.startswith("|") and line.endswith("|")
    )
    return headings, fences, table_rows


def main() -> int:
    errors: list[str] = []

    for relative in PUBLISHED_PAGES:
        english = DOCS / relative
        chinese = DOCS / "zh" / relative
        language_texts: dict[str, str] = {}
        for label, path in (("English", english), ("Chinese", chinese)):
            if not path.is_file():
                errors.append(f"missing {label} page: {path.relative_to(ROOT)}")
                continue
            text = path.read_text(encoding="utf-8")
            language_texts[label] = text
            if not _frontmatter(text):
                errors.append(f"missing frontmatter: {path.relative_to(ROOT)}")
            else:
                metadata = text.split("\n---\n", 1)[0]
                for key in (
                    "status:",
                    "audience:",
                    "verified_commit:",
                    "last_verified:",
                    "owner:",
                ):
                    if not re.search(rf"^{re.escape(key)}", metadata, re.MULTILINE):
                        errors.append(
                            f"missing {key[:-1]} metadata: {path.relative_to(ROOT)}"
                        )
            if OUTSIDE_DOC_SOURCE_LINK.search(text):
                errors.append(
                    "deployed page uses a relative link outside docs/: "
                    f"{path.relative_to(ROOT)}"
                )
        if len(language_texts) == 2:
            en_structure = _structure(language_texts["English"])
            zh_structure = _structure(language_texts["Chinese"])
            if en_structure != zh_structure:
                errors.append(
                    "English/Chinese structural parity mismatch: "
                    f"{relative} (headings/fences/table rows "
                    f"{tuple(map(len, en_structure[:2])) + (en_structure[2],)} vs "
                    f"{tuple(map(len, zh_structure[:2])) + (zh_structure[2],)})"
                )

    config_text = (DOCS / ".vitepress" / "config.mts").read_text(encoding="utf-8")
    for relative in EXCLUDED_INTERNAL_RECORDS:
        if f'"{relative}"' not in config_text:
            errors.append(f"internal/historical record is not excluded: {relative}")

    public_sources = [ROOT / "README.md", ROOT / "README_zh.md"]
    public_sources.extend(DOCS.rglob("*.md"))
    public_text = "\n".join(
        path.read_text(encoding="utf-8") for path in public_sources
    )
    # Deployment aliases and developer-machine paths must never enter public docs.
    for secret_shape in ("ssh bandwagon", "/Users/gongbozhang/"):
        if secret_shape.casefold() in public_text.casefold():
            errors.append(f"private deployment identifier found: {secret_shape!r}")

    skills = sorted(path.name for path in (ROOT / "skills").iterdir() if path.is_dir())
    skill_doc = (DOCS / "skills.md").read_text(encoding="utf-8")
    match = re.search(r"## Bundled Skills \((\d+)\)", skill_doc)
    if match is None or int(match.group(1)) != len(skills):
        stated = match.group(1) if match else "missing"
        errors.append(f"Skill inventory count is {stated}; repository has {len(skills)}")
    for name in skills:
        if f"`{name}`" not in skill_doc:
            errors.append(f"bundled Skill missing from docs/skills.md: {name}")

    for readme in (ROOT / "README.md", ROOT / "README_zh.md"):
        text = readme.read_text(encoding="utf-8")
        if re.search(r"\b(?:24|28) bundled Skills\b", text, re.IGNORECASE):
            errors.append(f"stale bundled Skill count in {readme.name}")
        if re.search(r"(?:24|28) 个内置 Skill", text):
            errors.append(f"stale bundled Skill count in {readme.name}")

    if errors:
        for error in errors:
            print(f"docs check: {error}", file=sys.stderr)
        return 1

    print(
        f"docs check: {len(PUBLISHED_PAGES)} English pages, complete Chinese mirror, "
        f"{len(skills)} bundled Skills"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
