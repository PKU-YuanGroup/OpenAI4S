#!/usr/bin/env python3
"""Render (or with ``--check``, verify) the Install section on every Skill page.

Every ``skills/<name>/README.md`` + ``README_zh.md`` pair carries a
``## Install`` / ``## 安装`` section with that Skill's own directory name
already substituted, so a reader who followed a link into the directory can
install from the page they landed on. ``skills/README.md`` carries the
``--all`` form and a collection root (a directory holding ``COLLECTION.json``)
the ``--collection`` form.

The section is one template per language, stamped per page. Nothing about it
is hand-maintained: the Node floor is read from ``cli.mjs``, the repository
from ``source.mjs``, and the member count from the collection itself, so a
change to any of those regenerates every page from here rather than through
ninety hand edits. ``--check`` is the gate ``tests/test_skills_installer_contract.py``
runs — a Skill directory whose pages lack the section, or carry a stale or
hand-edited one, fails the suite until this script has been run.

Bytes written are stdlib-only and deterministic; the script never touches a
page outside the section it owns.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "skills"
INSTALLER_DIR = ROOT / "tools" / "skills-installer"

#: The discovery rule the installer and the loader share, restated.
SKILL_MARKER = "SKILL.md"
COLLECTION_MARKER = "COLLECTION.json"

HEADINGS = {"README.md": "## Install", "README_zh.md": "## 安装"}

#: Two curated Skills the index page uses as its worked example.
INDEX_EXAMPLES = ("alphafold2", "boltz")
#: One real member each collection page names, keyed by collection directory.
COLLECTION_EXAMPLE_MEMBER = {"bioskills": "bio-differential-expression-deseq2-basics"}

#: Recipes added after npm 0.2.0; do not advertise unavailable npm installs.
NPM_020_UNAVAILABLE_SKILLS = frozenset({"single-cell-rna-analysis"})

#: English prose wraps here; a code span or a link is never split. Chinese
#: prose is left as one line per paragraph, like the rest of each page — a
#: hard wrap inside a CJK word can render as a space.
WIDTH = 80

_NODE_FLOOR = re.compile(r"^const MIN_NODE_MAJOR = (\d+);", re.MULTILINE)
_DEFAULT_REPO = re.compile(r'^export const DEFAULT_REPO = "([^"]+)";', re.MULTILINE)
_TOKEN = re.compile(r"[^\s`]*`[^`]+`[^\s`]*|[^\s\[]*\[[^\]]+\]\([^)]+\)\S*|\S+")


def installer_constants() -> dict[str, str]:
    """The two facts the pages state that the installer owns."""
    cli = (INSTALLER_DIR / "cli.mjs").read_text(encoding="utf-8")
    source = (INSTALLER_DIR / "source.mjs").read_text(encoding="utf-8")
    node = _NODE_FLOOR.search(cli)
    repo = _DEFAULT_REPO.search(source)
    if not node or not repo:
        raise SystemExit(
            "cli.mjs / source.mjs no longer declare MIN_NODE_MAJOR / DEFAULT_REPO"
        )
    return {"node": node.group(1), "repo": repo.group(1)}


def iter_pages(skills_dir: Path = SKILLS_DIR) -> list[tuple[Path, str, str]]:
    """Every directory that owns an Install section: ``(directory, kind, name)``.

    ``kind`` is ``skill`` for a directory holding ``SKILL.md``, ``collection``
    for one holding ``COLLECTION.json``, and ``index`` for ``skills/`` itself.
    """
    pages: list[tuple[Path, str, str]] = [(skills_dir, "index", skills_dir.name)]
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / COLLECTION_MARKER).is_file():
            pages.append((child, "collection", child.name))
        elif (child / SKILL_MARKER).is_file():
            pages.append((child, "skill", child.name))
    return pages


def collection_members(directory: Path) -> list[str]:
    return sorted(
        member.name
        for member in directory.iterdir()
        if member.is_dir() and (member / SKILL_MARKER).is_file()
    )


def wrap(paragraph: str, width: int = WIDTH) -> str:
    """Greedy wrap that keeps inline code spans and Markdown links whole."""
    lines: list[str] = []
    current: list[str] = []
    length = 0
    for token in _TOKEN.findall(paragraph):
        if current and length + 1 + len(token) > width:
            lines.append(" ".join(current))
            current, length = [], 0
        current.append(token)
        length += len(token) + (1 if length else 0)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def _targets_en() -> str:
    return (
        "`--target claude` writes to `~/.claude/skills`, `claude-project` to "
        "`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and "
        "`--dir <path>` to anywhere you name. The resolved absolute path is printed "
        "before anything is written there, and `--dry-run` stops at that plan. A "
        "reinstall refuses to overwrite a copy you have edited or one it did not "
        "install, and `uninstall` removes only the files it wrote."
    )


def _targets_zh() -> str:
    return (
        "`--target claude` 写入 `~/.claude/skills`，`claude-project` 写入 "
        "`./.claude/skills`，`openai4s` 写入 `<data_dir>/user-skills`，`--dir <path>` "
        "则写到你指定的任意位置。往目标写任何东西之前，都会先打印解析出的绝对路径；"
        "`--dry-run` 到此为止，不再写入。重装时，若目标副本被你改过、或者不是它自己装的，"
        "就拒绝覆盖；`uninstall` 也只删除它自己写过的文件。"
    )


def _prereq_en(node: str) -> str:
    return (
        f"With Node {node}+ and `git` on `PATH` (npm resolves a `github:` spec "
        "through it), and nothing cloned:"
    )


def _prereq_zh(node: str) -> str:
    return (
        f"有 Node {node}+、且 `PATH` 上有 `git`（npm 靠它解析 `github:` 形式的包名）"
        "即可，无需先克隆仓库："
    )


def _tarball(repo: str, member: str) -> str:
    top = repo.split("/")[1] + "-main"
    return (
        f"curl -L https://codeload.github.com/{repo}/tar.gz/refs/heads/main \\\n"
        f"  | tar -xz --strip-components=2 {top}/skills/{member}"
    )


def _zip_url(repo: str) -> str:
    return f"https://github.com/{repo}/archive/main.zip"


def render(
    kind: str, name: str, lang: str, constants: dict[str, str], **facts: str
) -> str:
    """The section for one page, heading included, without a trailing blank line."""
    node, repo = constants["node"], constants["repo"]
    top = repo.split("/")[1] + "-main"
    zip_url = _zip_url(repo)
    en = lang == "README.md"
    installer = (
        "../tools/skills-installer/"
        if kind == "index"
        else "../../tools/skills-installer/"
    )
    installer_doc = installer + ("README.md" if en else "README_zh.md")
    paragraphs: list[str] = [HEADINGS[lang]]

    if kind == "skill":
        if en:
            paragraphs += [
                "A Skill is a directory of files, so installing one is copying that "
                "directory somewhere an agent looks. " + _prereq_en(node),
                f"```bash\nnpx github:{repo} install {name} --target claude\n```",
                _targets_en()
                + (
                    " npm 0.2.0 does not include this recipe. Use the GitHub "
                    "installation command above."
                    if name in NPM_020_UNAVAILABLE_SKILLS
                    else " For a recipe included in the npm release, "
                    f"`npx @pku-yuangroup/openai4s-skills install {name} --target claude` "
                    "installs the published copy. The npm catalog can differ from this "
                    "repository."
                ),
                "Without Node, take the directory itself — and turn it into a `.zip` "
                "if an upload field wants one. The tarball is the whole repository "
                "(over 100 MB), and the pipe is a POSIX shell recipe (macOS, Linux, "
                "WSL) that extracts only this directory:",
                f"```bash\n{_tarball(repo, name)}\n"
                f"python3 -m zipfile -c {name}.zip {name}\n```",
                "The click-through form of that same download is the "
                f"[repository zip]({zip_url}), which is also the route to take on "
                f"Windows: extract it and copy `skills/{name}/` out "
                f"(`unzip main.zip '{top}/skills/{name}/*'` unpacks only that "
                "directory). If you already run OpenAI4S there is nothing to "
                "install — the wheel ships every bundled Skill, and a bundled Skill "
                "takes precedence over a same-named copy in `<data_dir>/user-skills`. "
                "Targets, provenance, and what the installer refuses to do: "
                f"[`tools/skills-installer/`]({installer_doc}).",
            ]
        else:
            paragraphs += [
                "一个 Skill 就是一个文件目录，所谓安装，就是把这个目录复制到 Agent "
                "会去读的地方。" + _prereq_zh(node),
                f"```bash\nnpx github:{repo} install {name} --target claude\n```",
                _targets_zh()
                + (
                    "npm 0.2.0 不包含本配方，请使用上面的 GitHub 安装命令。"
                    if name in NPM_020_UNAVAILABLE_SKILLS
                    else "对于 npm 发布版中包含的配方，可用 "
                    f"`npx @pku-yuangroup/openai4s-skills install {name} --target claude` "
                    "安装发布版本。npm 目录可能与当前仓库不同。"
                ),
                "没有 Node 时，直接取这个目录——需要 `.zip` 上传时再打包。tarball "
                "是整个仓库（超过 100 MB），下面这条管道是 POSIX shell（macOS、Linux、WSL）"
                "写法，只解出这一个目录：",
                f"```bash\n{_tarball(repo, name)}\n"
                f"python3 -m zipfile -c {name}.zip {name}\n```",
                f"同一份内容的图形界面版本是点击下载整个[仓库 zip 包]({zip_url})，"
                f"Windows 上也走这条路：解压后把 `skills/{name}/` 拷出来即可"
                f"（`unzip main.zip '{top}/skills/{name}/*'` 只解这一个目录）。"
                "如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel "
                "自带全部内置 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` "
                "里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事："
                f"[`tools/skills-installer/`]({installer_doc})。",
            ]
    elif kind == "collection":
        count, member = facts["count"], facts["member"]
        flat = (
            f"{_tarball(repo, name)}\n"
            f"mkdir -p ~/.claude/skills && mv {name}/*/ ~/.claude/skills/"
        )
        if en:
            paragraphs += [
                f"This is a collection rather than one Skill: {count} recipe "
                "directories behind a single catalog entry. " + _prereq_en(node),
                f"```bash\nnpx github:{repo} install --collection {name} --target claude\n```",
                _targets_en() + " Name one recipe instead of the whole collection "
                f"when one is all you want — `npx github:{repo} install {member}` — "
                "and the `directory` field of `MANIFEST.json` is where those names "
                "come from; it is also the name `uninstall` and `installed` answer "
                f"to. Installing all {count} into `~/.claude/skills` puts {count} "
                "descriptions into every Claude Code session's prompt — the cost the "
                "*Discovery and context cost* section below exists to avoid — so on "
                "that target name the recipes you need, and keep `--collection` for a "
                "directory an agent searches (`--dir`). For a collection included in "
                "the npm release, "
                f"`npx @pku-yuangroup/openai4s-skills install --collection {name} --target claude` "
                "installs the published copy. The npm catalog can differ from this "
                "repository.",
                "Without Node, take the recipes themselves. They must sit flat — "
                "`<target>/<recipe>/`, exactly what `--collection` writes — because "
                f"a `{name}/` folder nested inside a skills directory is not "
                "discovered. The tarball is the whole repository (over 100 MB), and "
                "the pipe is a POSIX shell recipe (macOS, Linux, WSL):",
                f"```bash\n{flat}\n```",
                "The click-through form of that same download is the "
                f"[repository zip]({zip_url}), which is also the route to take on "
                f"Windows: extract it and copy the directories inside `skills/{name}/` "
                "out — the recipes, not the folder itself. One recipe becomes an "
                "upload-field `.zip` with `python3 -m zipfile -c <recipe>.zip "
                "<recipe>`. If you already run OpenAI4S there is nothing to install "
                "— the wheel ships the pinned collection exactly as it stands here. "
                "Targets, provenance, and what the installer refuses to do: "
                f"[`tools/skills-installer/`]({installer_doc}).",
            ]
        else:
            paragraphs += [
                f"这里是一个集合，而不是单个 Skill：一个目录条目背后是 {count} 份配方。"
                + _prereq_zh(node),
                f"```bash\nnpx github:{repo} install --collection {name} --target claude\n```",
                _targets_zh() + "只想要其中一份配方时直接点名即可——"
                f"`npx github:{repo} install {member}`——名字取 `MANIFEST.json` "
                "里的 `directory` 字段，`uninstall` 和 `installed` 认的也是它。"
                f"把全部 {count} 份装进 `~/.claude/skills`，等于让每个 Claude Code 会话的 "
                f"prompt 都带上 {count} 条 description——正是下文“发现机制与上下文成本”一节"
                "要避免的开销——所以在这个目标上请只点名你需要的配方，`--collection` "
                "留给 agent 会去搜索的目录（`--dir`）。"
                "对于 npm 发布版中包含的集合，可用 "
                f"`npx @pku-yuangroup/openai4s-skills install --collection {name} --target claude` "
                "安装发布版本。npm 目录可能与当前仓库不同。",
                "没有 Node 时，直接取配方本身。各份配方要平铺摆放——`<目标>/<配方>/`，"
                f"也就是 `--collection` 写出来的样子——因为嵌在 skills 目录里的 `{name}/` "
                "文件夹不会被发现。tarball 是整个仓库（超过 100 MB），下面这条管道是 "
                "POSIX shell（macOS、Linux、WSL）写法：",
                f"```bash\n{flat}\n```",
                f"同一份内容的图形界面版本是点击下载整个[仓库 zip 包]({zip_url})，"
                f"Windows 上也走这条路：解压后把 `skills/{name}/` 里面的各个目录拷出来"
                "——拷配方，不要连文件夹一起拷。单份配方用 `python3 -m zipfile -c "
                "<配方>.zip <配方>` 就能打成上传框认的压缩包。如果你本来就在跑 "
                "OpenAI4S，那这里没有任何东西需要安装——wheel 自带的就是此处这份固定版"
                "集合。目标目录、来源记录，以及安装器拒绝去做的那些事："
                f"[`tools/skills-installer/`]({installer_doc})。",
            ]
    elif kind == "index":
        first, second, collection = facts["first"], facts["second"], facts["collection"]
        commands = (
            f"npx github:{repo} install --all --target claude\n"
            f"npx github:{repo} install --collection {collection} --target claude\n"
            f"npx github:{repo} install {first} {second} --dir ./my-skills"
        )
        if en:
            paragraphs += [
                "These recipes are not OpenAI4S-specific, and every Skill page below "
                "repeats this section with its own name filled in. " + _prereq_en(node),
                f"```bash\n{commands}\n```",
                _targets_en() + " To install recipes included in the npm release, use "
                "`npx @pku-yuangroup/openai4s-skills …`. The npm catalog can differ "
                "from this repository; use `@pku-yuangroup/openai4s-skills@0.2.0` "
                "to fix the release at 603 Skills (42 curated + 561 bioSkills).",
                "Without Node, one directory comes out of the source tarball on its "
                "own, and `python3 -m zipfile -c <name>.zip <name>` turns it into "
                "something an upload field will take. The tarball is the whole "
                "repository (over 100 MB), and the pipe is a POSIX shell recipe "
                "(macOS, Linux, WSL):",
                f"```bash\n{_tarball(repo, first)}\n```",
                "The click-through form of that same download is the "
                f"[repository zip]({zip_url}), which is also the route to take on "
                "Windows: extract it and copy `skills/<name>/` out. If you already "
                "run OpenAI4S there is nothing to install — the wheel ships every "
                "Skill in this tree, and a bundled Skill takes precedence over a "
                "same-named copy in `<data_dir>/user-skills`. Targets, provenance, "
                "and what the installer refuses to do: "
                f"[`../tools/skills-installer/`]({installer_doc}).",
            ]
        else:
            paragraphs += [
                "这些配方并不依赖 OpenAI4S，下面每个 Skill 页面都会重复这一节，并换上"
                "它自己的名字。" + _prereq_zh(node),
                f"```bash\n{commands}\n```",
                _targets_zh() + "对于 npm 发布版中包含的配方，可用 "
                "`npx @pku-yuangroup/openai4s-skills …` 安装。npm 目录可能与当前仓库"
                "不同；使用 `@pku-yuangroup/openai4s-skills@0.2.0` 可固定安装包含 "
                "603 个 Skill（42 个精选 + 561 个 bioSkills）的发布版本。",
                "没有 Node 时，可以只从源码 tarball 里取出一个目录，再用 "
                "`python3 -m zipfile -c <name>.zip <name>` 打成上传框认的压缩包。"
                "tarball 是整个仓库（超过 100 MB），下面这条管道是 POSIX shell"
                "（macOS、Linux、WSL）写法：",
                f"```bash\n{_tarball(repo, first)}\n```",
                f"同一份内容的图形界面版本是点击下载整个[仓库 zip 包]({zip_url})，"
                "Windows 上也走这条路：解压后把 `skills/<name>/` 拷出来即可。"
                "如果你本来就在跑 OpenAI4S，那这里没有任何东西需要安装——wheel "
                "自带本目录树中的全部 Skill，且内置 Skill 优先于 `<data_dir>/user-skills` "
                "里的同名副本。目标目录、来源记录，以及安装器拒绝去做的那些事："
                f"[`../tools/skills-installer/`]({installer_doc})。",
            ]
    else:
        raise ValueError(f"unknown page kind {kind!r}")

    rendered = []
    for block in paragraphs:
        if block.startswith("```") or block.startswith("## ") or not en:
            rendered.append(block)
        else:
            rendered.append(wrap(block))
    return "\n\n".join(rendered) + "\n"


def stamp(text: str, section: str, heading: str) -> str:
    """Return ``text`` with its Install section replaced by (or given) ``section``.

    The section runs from the heading line to the line before the next ``#``
    heading, or to the end of the file. A page without one gets it before its
    first ``##`` heading, or at the end when there is none.
    """
    lines = text.split("\n")
    section_lines = section.rstrip("\n").split("\n")
    heading_at = next((i for i, line in enumerate(lines) if line == heading), None)
    if heading_at is None:
        insert_at = next(
            (i for i, line in enumerate(lines) if line.startswith("## ")), None
        )
        if insert_at is None:
            body = lines[:]
            while body and body[-1] == "":
                body.pop()
            return "\n".join(body + [""] + section_lines) + "\n"
        return "\n".join(lines[:insert_at] + section_lines + [""] + lines[insert_at:])
    end = next(
        (i for i in range(heading_at + 1, len(lines)) if lines[i].startswith("#")),
        len(lines),
    )
    tail = lines[end:]
    if end == len(lines):
        return "\n".join(lines[:heading_at] + section_lines) + "\n"
    return "\n".join(lines[:heading_at] + section_lines + [""] + tail)


def _facts(kind: str, directory: Path, skills_dir: Path) -> dict[str, str]:
    if kind == "collection":
        members = collection_members(directory)
        member = COLLECTION_EXAMPLE_MEMBER.get(
            directory.name, members[0] if members else ""
        )
        if member not in members:
            raise SystemExit(
                f"{directory.name}: example member {member!r} is not a member"
            )
        return {"count": str(len(members)), "member": member}
    if kind == "index":
        first, second = INDEX_EXAMPLES
        for example in (first, second):
            if not (skills_dir / example / SKILL_MARKER).is_file():
                raise SystemExit(f"index example {example!r} is not a bundled Skill")
        collections = [
            d for d in skills_dir.iterdir() if (d / COLLECTION_MARKER).is_file()
        ]
        if len(collections) != 1:
            raise SystemExit("the index template names exactly one collection")
        return {"first": first, "second": second, "collection": collections[0].name}
    return {}


def expected(skills_dir: Path = SKILLS_DIR) -> dict[Path, str]:
    """Every page's full expected text, keyed by path."""
    constants = installer_constants()
    out: dict[Path, str] = {}
    for directory, kind, name in iter_pages(skills_dir):
        facts = _facts(kind, directory, skills_dir)
        for lang, heading in HEADINGS.items():
            page = directory / lang
            current = page.read_text(encoding="utf-8") if page.is_file() else ""
            out[page] = stamp(
                current, render(kind, name, lang, constants, **facts), heading
            )
    return out


def check(skills_dir: Path = SKILLS_DIR) -> list[str]:
    """Pages whose Install section is missing, stale, or hand-edited."""
    stale = []
    for page, text in expected(skills_dir).items():
        current = page.read_text(encoding="utf-8") if page.is_file() else None
        if current != text:
            stale.append(str(page.relative_to(ROOT)))
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 naming every page whose section differs from the template",
    )
    args = parser.parse_args(argv)
    if args.check:
        stale = check()
        for page in stale:
            print(page)
        if stale:
            print(
                f"{len(stale)} page(s) need `python scripts/render_skill_install_sections.py`"
            )
            return 1
        print("install sections: every Skill page matches the template")
        return 0
    written = 0
    for page, text in expected().items():
        current = page.read_text(encoding="utf-8") if page.is_file() else None
        if current != text:
            page.write_text(text, encoding="utf-8")
            written += 1
    print(f"install sections: {written} page(s) rewritten")
    return 0


if __name__ == "__main__":
    sys.exit(main())
