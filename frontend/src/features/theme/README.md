# frontend/src/features/theme

[中文说明](README_zh.md)

Workbench appearance (light / dark / system). `openai4s/server/webui/theme-bootstrap.js` stays a classic `<script src>` in the SPA head so the first paint already has `data-theme`. This module re-syncs after load, persists `os-theme`, and follows `prefers-color-scheme` while the preference is `system`. It writes only `data-theme` on `<html>` — not `body.theme-dark`.

## Files

| File | Responsibility |
| --- | --- |
| [`theme.ts`](theme.ts) | `applyTheme` / `setTheme` / `cycleTheme` / `installTheme`. localStorage key `os-theme`. |
| [`theme.test.ts`](theme.test.ts) | Single source of truth, storage key, cycle, system follow, classic head script, no `body.theme-dark`. |
