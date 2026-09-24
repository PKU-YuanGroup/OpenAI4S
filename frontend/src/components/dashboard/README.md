# frontend/src/components/dashboard

[中文说明](README_zh.md)

F-13 workbench chrome. Frozen ids match `tests/webui-contract.md` (`#dashboard`, `#dash-projects`, `#dash-sessions`, `#workspace`, `#messages`, `#dock-notebook`). `#composer-hint` is `role=status aria-live=polite`. `#tab-close` is a real button. The disconnect banner replaces the missing `#conn-dot`.

## Files

| File | Responsibility |
| --- | --- |
| [`DashHero.tsx`](DashHero.tsx) | `DashHeroText`: the dashboard headline and one-line pitch from `copy.ts`, repainted on a language switch through `onLanguageChange`. |
| [`ModelSelect.tsx`](ModelSelect.tsx) | Composer `#model-select`, rendered from the Customize model stores; a change calls `chooseComposerModel`. |
| [`ModelSelect.test.tsx`](ModelSelect.test.tsx) | One option per `/models` entry with the default selected, an empty option before any load, change → `PUT /models/default`, a refused change restores the previous choice. |
| [`Shell.tsx`](Shell.tsx) | Dashboard + workspace + composer + project modal markup. The dashboard hero (`.dash-hero`) holds the headline, `#dash-new-project` (the primary action) and `#dash-import-session`; the attention stream mounts into it as the second column. `#dash-project-search` is the home project filter. A path that routes into the workspace (`routesToWorkspace`) first paints neither view, only `#route-loading`, so a deep link does not show the dashboard while the locale chunks load. The Shell repaints on a language switch, so nothing in it binds a value the router writes: `#conv-title` takes a `defaultValue`. |
| [`Shell.test.tsx`](Shell.test.tsx) | The first paint before routing: a session or project deep link hides `#dashboard` and shows `#route-loading`; the root shows the dashboard with the indicator hidden. New project is the hero's primary (`solid-btn`) action, and it and Import live in the hero, not the header. A repaint leaves the session title alone. |
| [`copy.ts`](copy.ts) | `dashT`: the hero copy (zh/en), kept out of the generated `i18n/en.ts` / `zh.ts` extract. |
| [`dashboard.css`](dashboard.css) | `#conn-banner`, menu focus, `#route-loading` (a sibling rule retires it once either view is shown), and the hero layout. Global tokens stay with F-21. |
| [`index.ts`](index.ts) | `Shell` plus keyboard-activate helpers for later tile/tab lanes. |
