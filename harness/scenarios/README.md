# Harness scenarios

[中文](README_zh.md)

This directory stores strict, versioned JSON scenario inputs. A scenario declares its surface, task, fixture metadata, permission mode, scripted provider sequence, exact-occurrence faults, tags/tier, and expected terminal/event invariants. [`../schema.py`](../schema.py) rejects unknown or ambiguous fields before [`../runner.py`](../runner.py) executes anything.

## Direct files

| File | Responsibility |
| --- | --- |
| [`.gitkeep`](.gitkeep) | Keeps the scenario root present independently of individual scenario groups. |

## Direct subdirectories

| Directory | Responsibility |
| --- | --- |
| [`baseline/`](baseline/) | Required offline `tier:pr` scenarios for deterministic provider sequencing, terminal submission, and scheduled failure behavior. |

Scenario JSON is executable input only to declared Harness fakes; it is not permission to replay production side effects. Run it with `uv run python -m harness.cli run --tier pr --offline`.
