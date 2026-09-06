# Brand assets

[中文说明](README_zh.md)

Committed artwork: the packaged applications' icon and the demonstration media
the root READMEs embed. The daemon never loads these files; only the release
build and GitHub's README rendering read them.

## Files

| File | Purpose |
| --- | --- |
| `app-icon-1024.png` | The macOS app icon: the OpenAI4S mark — five bonded atoms around a terminal block holding the red prompt chevron — on the Big Sur icon grid. `scripts/build_macos_dmg.sh` slices it into the `.icns`, and the build fails if it is absent, because `Info.plist` declares the icon. Regenerate it with `scripts/make_app_icon.py`; do not hand-edit it. |

## Subdirectories

| Directory | Purpose |
| --- | --- |
| `readme-gifs-hd/` | The committed high-resolution GIFs the root READMEs embed: six product demos plus the animated banner. |

## Where this fits

The mark exists elsewhere in the repository only as raster at glyph size — the
banner in `readme-gifs-hd/openai4s_penta.gif` and the 64px Web favicon — and
neither survives being resampled up to the 1024px an `.icns` needs. The icon is
therefore rebuilt from the mark's measured geometry as flat vector primitives
and committed here, so the DMG build stays a pure `sips` + `iconutil` step with
no drawing code and no image dependency of its own.
