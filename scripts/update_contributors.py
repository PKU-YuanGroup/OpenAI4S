#!/usr/bin/env python3
"""Regenerate the Community Contributors wall in the READMEs.

Fetches the repository's contributors straight from the GitHub API (the same
source, and same commit-count order, as the sidebar / contributors graph) and
appends publicly recognized contributions that are not represented in the
commit graph. It then rewrites the block between the ``CONTRIBUTORS`` markers
in each README.

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
import re
import subprocess
import sys
import urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "PKU-YuanGroup/OpenAI4S")
READMES = ("README.md", "README_zh.md")
AVATAR_DIR = os.path.join(".github", "contributors")
START = "<!-- CONTRIBUTORS:START -->"
END = "<!-- CONTRIBUTORS:END -->"
SRC = 256  # source crop resolution for a crisp circle
DISPLAY = 64  # rendered avatar size in px, close to the original wall
# Bots and the automated co-author identity are not community members. The
# ``noreply@anthropic.com`` co-author has no GitHub account (it shows as an
# unlinked grey avatar in the sidebar) and the /contributors API omits it, so
# it is naturally excluded here too.
EXCLUDE = {"github-actions[bot]", "dependabot[bot]", "actions-user"}
# Publicly accepted contributions that are not represented in the commit graph.
# Keep this list limited to GitHub logins whose recognition is already public.
RECOGNIZED_CONTRIBUTORS = ("EQSTLab",)
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


def write_avatars(people: list[dict], token: str | None) -> tuple[set[str], int]:
    """Refresh the avatars, prune the departed, and say what was written.

    Returns the logins that have a usable committed PNG *and* the number this
    run actually produced: "a file with that name exists" and "I refreshed it"
    are different facts, and only the second one says the run worked.
    """

    os.makedirs(AVATAR_DIR, exist_ok=True)
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
        with open(os.path.join(AVATAR_DIR, _avatar_name(login)), "wb") as f:
            f.write(png)
        written += 1
    # Drop old identities and legacy SVGs, but keep a current contributor's
    # committed PNG when a transient refresh fails. Compared case-insensitively
    # because a login whose casing drifts from the committed filename writes
    # through to the existing inode under the OLD name on a case-preserving
    # filesystem, and an exact-match prune then deletes the file just written.
    current = {_avatar_name(person["login"]).casefold() for person in people}
    for name in os.listdir(AVATAR_DIR):
        if name.casefold() not in current and name.endswith((".png", ".svg")):
            os.remove(os.path.join(AVATAR_DIR, name))
    # Derived from what survived on disk, spelled exactly as render() will spell
    # it, so a local <img src> is never emitted for a name that is not there.
    on_disk = set(os.listdir(AVATAR_DIR))
    have_png = {
        person["login"] for person in people if _avatar_name(person["login"]) in on_disk
    }
    return have_png, written


def render(people: list[dict], have_png: set[str]) -> str:
    rows = []
    for c in people:
        login = c["login"]
        src = (
            f"{AVATAR_DIR.replace(os.sep, '/')}/{_avatar_name(login)}"
            if login in have_png
            else f"https://github.com/{login}.png"
        )
        rows.append(
            f'<a href="https://github.com/{login}" title="{login}">'
            f'<img src="{src}" width="{DISPLAY}" height="{DISPLAY}" '
            f'alt="{login}" /></a>'
        )
    return "\n".join(rows)


def update_readme(path: str, block: str) -> bool:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if START not in text or END not in text:
        print(f"markers not found in {path}", file=sys.stderr)
        return False
    replacement = f"{START}\n{block}\n{END}"
    updated = re.sub(
        re.escape(START) + r".*?" + re.escape(END),
        lambda _m: replacement,
        text,
        flags=re.DOTALL,
    )
    if updated == text:
        return False
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(updated)
    return True


def main() -> int:
    token = _token()
    people = fetch_contributors(token)
    if not people:
        print("no contributors fetched (rate limit or auth?)", file=sys.stderr)
        return 1
    people = include_recognized_contributors(people)
    have_png, written = write_avatars(people, token)
    block = render(people, have_png)
    changed = [p for p in READMES if os.path.exists(p) and update_readme(p, block)]
    print(
        f"{len(people)} contributors: "
        + ", ".join(c["login"] for c in people)
        + f"\ncircular pngs: {written} refreshed, {len(have_png)} linked locally"
        + f"; readmes updated: {changed or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
