#!/usr/bin/env python3
"""Regenerate the Community Contributors wall in the READMEs.

Fetches the repository's contributors straight from the GitHub API (the same
source, and same commit-count order, as the sidebar / contributors graph) and
appends publicly recognized contributions that are not represented in the
commit graph. It then rewrites the block between the ``CONTRIBUTORS`` markers
in each README, and keeps the bilingual avatar-directory inventories in sync
with the PNG files that remain after the refresh.

Unlike a third-party image service (e.g. contrib.rocks, which calls the GitHub
API anonymously, gets rate-limited for this repo, and rendered only a single
avatar), this runs with the repo's own token and sees every attributed
contributor.

GitHub markdown strips inline CSS, so a plain ``<img>`` is always square, and a
committed SVG that embeds the avatar as a ``data:`` URI is blocked by
raw.githubusercontent's content-security-policy.  So each avatar is cropped to a
circle with transparent corners and committed as a small PNG under
``.github/contributors/``; a round raster image renders everywhere, and each is
wrapped in a link to the person's GitHub profile.

Run by the daily local automation; runnable manually for a preview with
``GITHUB_TOKEN`` set or a ``gh auth`` session. Requires Pillow.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "PKU-YuanGroup/OpenAI4S")
READMES = ("README.md", "README_zh.md")
AVATAR_DIR = os.path.join(".github", "contributors")
START = "<!-- CONTRIBUTORS:START -->"
END = "<!-- CONTRIBUTORS:END -->"
INVENTORY_START = "<!-- AVATAR-FILES:START -->"
INVENTORY_END = "<!-- AVATAR-FILES:END -->"
# Staged writes carry this prefix so a run can sweep what a killed one left.
TMP_PREFIX = ".update_contributors-"
# (file in AVATAR_DIR, table header, per-avatar description)
INVENTORIES = (
    (
        "README.md",
        "| File | Purpose |",
        "Render-ready avatar for contributor `{login}`.",
    ),
    (
        "README_zh.md",
        "| 文件 | 职责 |",
        "贡献者 `{login}` 的可直接渲染头像。",
    ),
)
SRC = 256  # source crop resolution for a crisp circle
DISPLAY = 64  # rendered avatar size in px, close to the original wall
# Bots and the automated co-author identity are not community members. The
# ``noreply@anthropic.com`` co-author has no GitHub account (it shows as an
# unlinked grey avatar in the sidebar) and the /contributors API omits it, so
# it is naturally excluded here too.
EXCLUDE = {"github-actions[bot]", "dependabot[bot]", "actions-user"}
# Publicly accepted contributions that are not represented in the commit graph.
# Keep this list limited to GitHub logins whose recognition is already public.
#
# `difficulttopickaname` is a maintainer (CODEOWNERS: /openai4s/server/,
# /tests/browser_smoke.mjs) whose commits ARE in the history -- authored as
# `minhan.tang <minhan.tang19@gmail.com>` -- but the /contributors API returns
# them with no linked account, so the graph cannot see them. The durable fix is
# on their side: adding that address to their GitHub account retroactively
# links every past commit. This entry carries the recognition until then, and
# `include_recognized_contributors` drops it automatically the moment the API
# starts returning the login, so nothing has to be cleaned up afterwards.
#
# `ChampionZhong` found and fixed the first-run wizard's fail-closed secret
# store read (#165). That commit lands, with its authorship, through #164. The
# entry keeps the recognition on the wall until the API lists the login, which
# it then does for the same reason as above.
RECOGNIZED_CONTRIBUTORS = ("EQSTLab", "difficulttopickaname", "ChampionZhong")
_UA = {"User-Agent": "openai4s-contributors-script"}


def _token() -> str | None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        return token
    try:  # local convenience only
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _get(url: str, token: str | None) -> bytes:
    req = urllib.request.Request(url, headers=dict(_UA))
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_contributors(token: str | None) -> list[dict]:
    people: list[dict] = []
    page = 1
    while True:
        url = (
            f"https://api.github.com/repos/{REPO}/contributors"
            f"?per_page=100&page={page}"
        )
        batch = json.loads(_get(url, token))
        if not isinstance(batch, list) or not batch:
            break
        people.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    seen: set[str] = set()
    kept: list[dict] = []
    for c in people:
        login = c.get("login")
        if c.get("type") != "User" or not login or login in EXCLUDE or login in seen:
            continue
        seen.add(login)
        kept.append(c)
    # Stable sort by commit count desc == GitHub's default contributor order.
    kept.sort(key=lambda c: c.get("contributions", 0), reverse=True)
    return kept


def include_recognized_contributors(people: list[dict]) -> list[dict]:
    """Append public non-commit contributors without duplicating API users.

    The same two guards `fetch_contributors` applies to an API row apply here:
    a hand-maintained list is not a reason to render a bot or an organization
    account inside a wall that is otherwise restricted to human users.
    """

    seen = {person["login"].casefold() for person in people}
    return people + [
        {"login": login}
        for login in RECOGNIZED_CONTRIBUTORS
        if login.casefold() not in seen and login not in EXCLUDE
    ]


def _circular_png(raw: bytes) -> bytes:
    from PIL import Image, ImageDraw, ImageOps

    im = ImageOps.fit(
        Image.open(io.BytesIO(raw)).convert("RGBA"),
        (SRC, SRC),
        method=Image.LANCZOS,
    )
    mask = Image.new("L", (SRC, SRC), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, SRC - 1, SRC - 1), fill=255)
    im.putalpha(mask)
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _avatar_name(login: str) -> str:
    return f"{login}.png"


def _target_name(login: str, committed: set[str]) -> str:
    """The file a refresh writes: the committed spelling, not a twin of it.

    A case-insensitive filesystem writes through to the committed file whatever
    name is passed; a case-sensitive one would grow a second avatar for the
    same person the first time their login's casing drifts.
    """
    name = _avatar_name(login)
    if name in committed:
        return name
    folded = name.casefold()
    variants = sorted(other for other in committed if other.casefold() == folded)
    return variants[0] if variants else name


def write_avatars(
    people: list[dict], token: str | None
) -> tuple[dict[str, str], int, list[str]]:
    """Refresh the avatars, prune the departed, and say what was written.

    Returns each login's PNG as it is actually spelled on disk, the number this
    run produced, and every PNG filename left there: "a file with that name
    exists" and "I refreshed it" are different facts, and only the second one
    says the run worked.
    """

    os.makedirs(AVATAR_DIR, exist_ok=True)
    committed = set(os.listdir(AVATAR_DIR))
    written = 0
    for c in people:
        login = c["login"]
        avatar_url = c.get("avatar_url")
        url = avatar_url or f"https://github.com/{login}.png"
        url += ("&" if "?" in url else "?") + "s=256"
        try:
            # Only the API's own avatar_url is fetched authenticated. The
            # github.com/<login>.png form 302s to avatars.githubusercontent.com,
            # and urllib copies every header except content-length/content-type
            # across a redirect -- so sending the token here would hand it to a
            # host that never asked for it.
            png = _circular_png(_get(url, token if avatar_url else None))
        except Exception as exc:  # noqa: BLE001
            print(f"  avatar failed for {login}: {exc}", file=sys.stderr)
            continue
        name = _target_name(login, committed)
        with open(os.path.join(AVATAR_DIR, name), "wb") as f:
            f.write(png)
        written += 1
    # Drop old identities and legacy SVGs, but keep a current contributor's
    # committed PNG when a transient refresh fails. Compared case-insensitively
    # because a login whose casing drifts from the committed filename writes
    # through to the existing inode under the OLD name on a case-preserving
    # filesystem, and an exact-match prune then deletes the file just written.
    # A staged file a killed run left here goes too: this runs on every
    # refresh, while the writer only sweeps a directory it is about to write.
    current = {_avatar_name(person["login"]).casefold() for person in people}
    for name in os.listdir(AVATAR_DIR):
        departed = name.casefold() not in current and name.endswith((".png", ".svg"))
        if departed or name.startswith(TMP_PREFIX):
            os.remove(os.path.join(AVATAR_DIR, name))
    # Read back from what survived the prune, so render() links a file under
    # the name it actually has rather than the one the login would imply.
    on_disk = set(os.listdir(AVATAR_DIR))
    have_png: dict[str, str] = {}
    for person in people:
        name = _target_name(person["login"], on_disk)
        if name in on_disk:
            have_png[person["login"]] = name
    surviving = sorted(
        name
        for name in on_disk
        if name.endswith(".png") and os.path.isfile(os.path.join(AVATAR_DIR, name))
    )
    return have_png, written, surviving


def render(people: list[dict], have_png: dict[str, str]) -> str:
    rows = []
    for c in people:
        login = c["login"]
        name = have_png.get(login)
        src = (
            f"{AVATAR_DIR.replace(os.sep, '/')}/{name}"
            if name
            else f"https://github.com/{login}.png"
        )
        rows.append(
            f'<a href="https://github.com/{login}" title="{login}">'
            f'<img src="{src}" width="{DISPLAY}" height="{DISPLAY}" '
            f'alt="{login}" /></a>'
        )
    return "\n".join(rows)


def _replace_block(text: str, start: str, end: str, block: str) -> str:
    """Replace what sits between the single ``start``/``end`` pair in ``text``."""
    head, tail = text.find(start), text.find(end)
    if text.count(start) != 1 or text.count(end) != 1 or head > tail:
        raise ValueError(f"expected exactly one {start} ... {end} pair")
    return f"{text[:head]}{start}\n{block}\n{end}{text[tail + len(end):]}"


def _write_texts(updates: dict[str, str]) -> None:
    """Stage every document before replacing any destination.

    Each replacement is atomic; the batch is not a multi-file transaction.
    """
    # A hard kill leaves a staged file behind, and every file in the avatar
    # directory has to be documented -- so sweep first, all of them, before
    # staging anything that a per-path sweep would then delete.
    for directory in {os.path.dirname(path) or "." for path in updates}:
        for stale in os.listdir(directory):
            if stale.startswith(TMP_PREFIX):
                os.remove(os.path.join(directory, stale))
    staged: list[tuple[str, str]] = []
    try:
        for path, text in updates.items():
            fd, tmp = tempfile.mkstemp(
                dir=os.path.dirname(path) or ".", prefix=TMP_PREFIX
            )
            staged.append((tmp, path))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            shutil.copymode(path, tmp)
        for tmp, path in staged:
            os.replace(tmp, path)
    finally:
        for tmp, _path in staged:
            if os.path.exists(tmp):
                os.remove(tmp)


def render_readme_update(text: str, block: str) -> str | None:
    """Render a changed wall without writing it."""
    updated = _replace_block(text, START, END, block)
    return None if updated == text else updated


def read_documents() -> dict[str, str]:
    """Read every document this run rewrites, before anything is written.

    Markers that cannot be spliced are fatal for a wall as much as for an
    inventory: rewriting one root README while skipping its sibling publishes
    two walls that disagree about who contributed, and a run that reports
    success while doing that is worse than one that refuses.
    """
    documents = [(path, START, END) for path in READMES if os.path.exists(path)]
    documents += [
        (os.path.join(AVATAR_DIR, filename), INVENTORY_START, INVENTORY_END)
        for filename, _header, _description in INVENTORIES
    ]
    texts: dict[str, str] = {}
    for path, start, end in documents:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        try:
            _replace_block(text, start, end, "")
        except ValueError as exc:
            raise ValueError(f"{path}: {exc}") from None
        texts[path] = text
    return texts


def avatar_readme_updates(texts: dict[str, str], names: list[str]) -> dict[str, str]:
    """Document surviving PNGs, preserving their on-disk filename spelling."""
    updates: dict[str, str] = {}
    for filename, header, description in INVENTORIES:
        path = os.path.join(AVATAR_DIR, filename)
        rows = [header, "| --- | --- |"]
        for name in names:
            detail = description.format(login=name[:-4])
            rows.append(f"| `{name}` | {detail} |")
        block = "\n".join(rows)
        updated = _replace_block(texts[path], INVENTORY_START, INVENTORY_END, block)
        if updated != texts[path]:
            updates[path] = updated
    return updates


def main() -> int:
    try:
        texts = read_documents()
    except (OSError, ValueError) as exc:
        print(f"readme not updatable: {exc}", file=sys.stderr)
        return 1
    token = _token()
    people = fetch_contributors(token)
    if not people:
        print("no contributors fetched (rate limit or auth?)", file=sys.stderr)
        return 1
    people = include_recognized_contributors(people)
    have_png, written, surviving = write_avatars(people, token)
    block = render(people, have_png)
    updates = {}
    for path in READMES:
        if path in texts:
            updated = render_readme_update(texts[path], block)
            if updated is not None:
                updates[path] = updated
    updates.update(avatar_readme_updates(texts, surviving))
    _write_texts(updates)
    changed = list(updates)
    print(
        f"{len(people)} contributors: "
        + ", ".join(c["login"] for c in people)
        + f"\ncircular pngs: {written} refreshed, {len(have_png)} linked locally"
        + f"; readmes updated: {changed or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
