# Golden traces

[中文](README_zh.md)

This directory holds reviewed, canonical comparison data. A golden trace freezes normalized observations of a named contract or production characterization; it is never executable history and must never be used to replay side effects.

## Direct files

| File | Responsibility |
| --- | --- |
| [`.gitkeep`](.gitkeep) | Keeps the versioned golden root present. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`v1/`](v1/) | Schema-version-1 reviewed trace assets, currently the r5 pre-change production characterization. |

Golden updates are explicit: run `uv run python -m harness.cli characterize --write`, then review the byte diff together with `current_behavior`, `desired_contract`, and `known_bug` fields.
