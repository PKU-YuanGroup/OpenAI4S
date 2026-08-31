# frontend/src/components

[中文说明](README_zh.md)

View components owned by F-series lanes. Each area is a subdirectory (`customize/`, later `timeline/`, `notebook/`, …). Lanes import `stores/` and do not edit them.
Preact view containers. Each F-series lane owns `components/<area>/` and does not edit another lane's files.
Lane-owned Preact views. Each F-series item adds `components/<area>/` and does not edit another lane's files.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`artifacts/`](artifacts/) | F-17 Files dock (M-03 search / filter / pagination / deep link). |
| [`customize/`](customize/) | F-19 Customize modal: nine tabs plus Volcengine / DataPro / Doubao vendor cards. |
| [`dashboard/`](dashboard/) | F-13 dashboard / workspace chrome (frozen ids, `#composer-hint`, disconnect banner). |
| [`timeline/`](timeline/) | F-15 `#dock-timeline` host. The ledger itself is the imperative island in `features/timeline/island.ts`. |
| [`onboarding/`](onboarding/) | M-01 first-run wizard overlay and tri-state capability badges. |
