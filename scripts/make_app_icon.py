#!/usr/bin/env python3
"""Render the macOS app icon from the OpenAI4S brand mark.

The mark — five bonded atoms around a terminal block holding a red prompt
chevron — exists in the repository only as raster: the 150px glyph at the left
of ``readme-gifs-hd/openai4s_penta.gif`` and a 64px favicon.  Resampling either
one up to the 1024px an .icns needs produces a visibly soft icon, so this script
rebuilds the mark from its measured geometry as flat vector primitives and
supersamples it down instead.  The numbers below are the mark's own, read off
the banner frame; the output is pixel-crisp at every size macOS asks for.

Dev-only: needs Pillow, is never imported by the daemon, and its output
(``assets/app-icon-1024.png``) is committed so the DMG build stays dependency-free.

    uv run python scripts/make_app_icon.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

# --------------------------------------------------------------------------- #
# The brand mark, measured from readme-gifs-hd/openai4s_penta.gif (final frame).
# Coordinates are in that glyph's own 154x131 space; everything below is a pure
# scale of these, so the icon cannot drift from the banner.
# --------------------------------------------------------------------------- #
MARK_W, MARK_H = 154.0, 131.0

RED = (140, 10, 15)
DARK = (20, 22, 28)
GREY = (151, 153, 159)
WHITE = (255, 255, 255)

ATOM_R = 12.75
ATOMS = [  # (x, y, colour)
    (75.5, 15.5, RED),  # top
    (15.0, 53.5, RED),  # upper left
    (138.0, 51.5, DARK),  # right
    (42.0, 115.0, DARK),  # lower left
    (113.0, 114.5, RED),  # lower right
]
BOND_W = 4.4

BLOCK = (52.5, 51.5, 100.5, 92.5)  # the terminal block, x0 y0 x1 y1
BLOCK_R = 6.0

CHEVRON = [(56.5, 57.5), (73.0, 72.0), (56.5, 86.5)]  # the `>` prompt
CHEVRON_W = 9.5

CURSOR = (75.0, 81.0, 91.0, 87.0)  # the white cursor bar
CURSOR_R = 1.5

# macOS Big Sur icon grid: a 1024 canvas whose artwork is an 824 rounded square,
# so the app sits at the same visual size as every system icon beside it.
CANVAS = 1024
PLATE = 824
PLATE_R = 185.0
SS = 4  # supersampling factor


def _plate(size: int, radius: float) -> Image.Image:
    """The rounded plate, with a whisper of a gradient so it is not dead flat."""

    plate = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (size, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        value = int(round(255 - 13 * t))  # #ffffff -> #f2f2f2
        gradient.paste((value, value, value, 255), (0, y, size, y + 1))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=radius, fill=255
    )
    plate.paste(gradient, (0, 0), mask)
    return plate


def render(size: int = CANVAS) -> Image.Image:
    scale = size / CANVAS
    work = int(size * SS)
    icon = Image.new("RGBA", (work, work), (0, 0, 0, 0))
    icon.alpha_composite(
        _plate(int(PLATE * scale * SS), PLATE_R * scale * SS),
        (int((work - PLATE * scale * SS) / 2), int((work - PLATE * scale * SS) / 2)),
    )

    # Fit the mark inside the plate with room to breathe on every side. The mark
    # is wider than it is tall, so width is the binding dimension.
    mark_w = PLATE * scale * SS * 0.86
    k = mark_w / MARK_W
    ox = (work - MARK_W * k) / 2
    oy = (work - MARK_H * k) / 2

    def px(x: float, y: float) -> tuple[float, float]:
        return ox + x * k, oy + y * k

    draw = ImageDraw.Draw(icon)

    # Bonds first: every one runs from its atom into the block's centre, and the
    # block and atoms are painted over the stubs.
    cx, cy = (BLOCK[0] + BLOCK[2]) / 2, (BLOCK[1] + BLOCK[3]) / 2
    for ax, ay, _ in ATOMS:
        draw.line([px(ax, ay), px(cx, cy)], fill=GREY, width=int(round(BOND_W * k)))

    for ax, ay, colour in ATOMS:
        x, y = px(ax, ay)
        r = ATOM_R * k
        draw.ellipse([x - r, y - r, x + r, y + r], fill=colour)

    x0, y0 = px(BLOCK[0], BLOCK[1])
    x1, y1 = px(BLOCK[2], BLOCK[3])
    draw.rounded_rectangle([x0, y0, x1, y1], radius=BLOCK_R * k, fill=DARK)

    draw.line(
        [px(*point) for point in CHEVRON],
        fill=RED,
        width=int(round(CHEVRON_W * k)),
        joint="curve",
    )

    cx0, cy0 = px(CURSOR[0], CURSOR[1])
    cx1, cy1 = px(CURSOR[2], CURSOR[3])
    draw.rounded_rectangle([cx0, cy0, cx1, cy1], radius=CURSOR_R * k, fill=WHITE)

    return icon.resize((size, size), Image.LANCZOS)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=root / "assets" / "app-icon-1024.png"
    )
    args = parser.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    icon = render()
    icon.save(args.out)
    print(f"wrote {args.out.relative_to(root)}  ({icon.width}x{icon.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
