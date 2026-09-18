# frontend/src/features/icons

[中文说明](README_zh.md)

The shared table of the workbench's line icons (lucide paths from `app.js:7-77`). The port had split app.js's table into per-lane copies that drifted apart: `panel-left`, `panel-right`, `moon` and `sun` ended up in no table `paintIcons()` reads, so the sidebar, dock and theme buttons painted an empty `<svg>`. `features/sessions/icon.ts`, `features/chrome/dom.ts` and `features/notebook/chrome.ts` draw from here; add a new name to this table, not to a lane. Four lanes still keep their own tables and are not folded in yet: `features/send/icon.ts`, `features/timeline/dom.ts`, `islands/dom.ts` and `features/artifacts/api.ts`.

`paintIcons()` runs once, when the Shell is bound. A node created after that must be drawn where it is made, with `paintIcon(node, name, size)`; a bare `data-icon` attribute on a late node stays an empty button.

Entries are static markup injected as innerHTML, the way app.js did. Never build one from data.

## Files

| File | Responsibility |
| --- | --- |
| [`paths.ts`](paths.ts) | `ICON_PATHS`, `iconSvg(name, size, cls)` and `paintIcon(node, name, size)`. An unknown name draws nothing. |
| [`icons.coverage.test.ts`](icons.coverage.test.ts) | Every `data-icon="…"` / `setAttribute("data-icon", "…")` name in the source, `paintIcon(node, "…")`, plus the theme toggle's sun/moon, draws a shape through `paintIcons`; no source outside this directory sets `data-icon` without drawing it; the notebook's `iconEl` draws every name it is given; the sessions and chrome helpers draw the same picture for a name. |
