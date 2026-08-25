# Golden traces

[中文说明](README_zh.md)

Reviewed reference data lives here. A golden trace freezes the normalized observations of a named contract or of a production characterization, so a later run can be compared against it byte for byte. It is not executable history, and nothing may replay side effects out of it.

## Files

| File | Responsibility |
| --- | --- |
| [`.gitkeep`](.gitkeep) | Keeps this root tracked in git; the traces themselves live one level down, under a schema-version directory. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`v1/`](v1/) | Reviewed trace assets at schema version 1. Today that is the r5 pre-change production characterization plus the independently reviewed Stage 0 Auto Mode contract and non-Guardian terminal-contract expectations. |

Updating a golden is always deliberate. For the characterization golden, run `uv run python -m harness.cli characterize --write`, then read the byte diff alongside the `current_behavior`, `desired_contract`, and `known_bug` fields it moved. The Auto Mode contract expectations have no `--write` path at all: they are edited by hand and reviewed as data, precisely so a scenario cannot regenerate the thing it is compared against.
