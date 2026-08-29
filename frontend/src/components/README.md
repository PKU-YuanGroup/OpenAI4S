# frontend/src/components

[中文说明](README_zh.md)

Preact view containers. Each F-series lane owns `components/<area>/` and does not edit another lane's files.
Lane-owned Preact views. Each F-series item adds `components/<area>/` and does not edit another lane's files.

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`dashboard/`](dashboard/) | F-13 dashboard / workspace chrome (frozen ids, `#composer-hint`, disconnect banner). |
| [`timeline/`](timeline/) | F-15 `#dock-timeline` host. The ledger itself is the imperative island in `features/timeline/island.ts`. |
