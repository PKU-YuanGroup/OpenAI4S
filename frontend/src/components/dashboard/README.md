# frontend/src/components/dashboard

[中文说明](README_zh.md)

F-13 workbench chrome. Frozen ids match `tests/webui-contract.md` (`#dashboard`, `#dash-projects`, `#dash-sessions`, `#workspace`, `#messages`, `#dock-notebook`). `#composer-hint` is `role=status aria-live=polite`. `#tab-close` is a real button. The disconnect banner replaces the missing `#conn-dot`.

## Files

| File | Responsibility |
| --- | --- |
| [`ModelSelect.tsx`](ModelSelect.tsx) | Composer `#model-select`, rendered from the Customize model stores; a change calls `chooseComposerModel`. |
| [`ModelSelect.test.tsx`](ModelSelect.test.tsx) | One option per `/models` entry with the default selected, an empty option before any load, change → `PUT /models/default`, a refused change restores the previous choice. |
| [`Shell.tsx`](Shell.tsx) | Dashboard + workspace + composer + project modal markup. `#dash-project-search` is the home project filter. A path that routes into the workspace (`routesToWorkspace`) first paints neither view, only `#route-loading`, so a deep link does not show the dashboard while the locale chunks load. |
| [`Shell.test.tsx`](Shell.test.tsx) | The first paint before routing: a session or project deep link hides `#dashboard` and shows `#route-loading`; the root shows the dashboard with the indicator hidden. |
| [`dashboard.css`](dashboard.css) | `#conn-banner`, menu focus, and `#route-loading`, which a sibling rule retires once either view is shown. Global tokens stay with F-21. |
| [`index.ts`](index.ts) | `Shell` plus keyboard-activate helpers for later tile/tab lanes. |
