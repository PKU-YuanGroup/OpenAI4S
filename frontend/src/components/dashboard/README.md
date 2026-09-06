# frontend/src/components/dashboard

[中文说明](README_zh.md)

F-13 workbench chrome. Frozen ids match `tests/webui-contract.md` (`#dashboard`, `#dash-projects`, `#dash-sessions`, `#workspace`, `#messages`, `#dock-notebook`). `#composer-hint` is `role=status aria-live=polite`. `#tab-close` is a real button. The disconnect banner replaces the missing `#conn-dot`.

## Files

| File | Responsibility |
| --- | --- |
| [`Shell.tsx`](Shell.tsx) | Dashboard + workspace + composer + project modal markup. `#dash-project-search` is the home project filter. |
| [`dashboard.css`](dashboard.css) | `#conn-banner` and menu focus. Global tokens stay with F-21. |
| [`index.ts`](index.ts) | `Shell` plus keyboard-activate helpers for later tile/tab lanes. |
