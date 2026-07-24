#!/usr/bin/env python3
"""Regenerate the Community Contributors wall in the READMEs.

Fetches the repository's contributors straight from the GitHub API (the same
source, and same commit-count order, as the sidebar / contributors graph) and
rewrites the block between the ``CONTRIBUTORS`` markers in each README.

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


def write_avatars(people: list[dict], token: str | None) -> set[str]:
    os.makedirs(AVATAR_DIR, exist_ok=True)
    ok: set[str] = set()
    for c in people:
        login = c["login"]
        url = c.get("avatar_url") or f"https://github.com/{login}.png"
        url += ("&" if "?" in url else "?") + "s=256"
        try:
            png = _circular_png(_get(url, token))
        except Exception as exc:  # noqa: BLE001
            print(f"  avatar failed for {login}: {exc}", file=sys.stderr)
            continue
        with open(os.path.join(AVATAR_DIR, f"{login}.png"), "wb") as f:
            f.write(png)
        ok.add(login)
    # Drop anything (old SVGs, departed contributors) that is not a current png.
    keep = {f"{login}.png" for login in ok}
    for name in os.listdir(AVATAR_DIR):
        if name not in keep and (name.endswith(".png") or name.endswith(".svg")):
            os.remove(os.path.join(AVATAR_DIR, name))
    return ok


def render(people: list[dict], have_png: set[str]) -> str:
    rows = []
    for c in people:
        login = c["login"]
        src = (
            f".github/contributors/{login}.png"
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
    have_png = write_avatars(people, token)
    block = render(people, have_png)
    changed = [p for p in READMES if os.path.exists(p) and update_readme(p, block)]
    print(
        f"{len(people)} contributors: "
        + ", ".join(c["login"] for c in people)
        + f"\ncircular pngs: {len(have_png)}; readmes updated: {changed or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
