# Harness scenarios

[中文说明](README_zh.md)

Scenario inputs live here, one strict versioned JSON file each. A scenario names the surface and task it stands for, carries fixture metadata and a permission mode, scripts the sequence the fake provider replies with, and pins each fault to an exact occurrence of a named point. Its tags place it in a tier, and its expectations state the terminal reason and the event invariants the run has to satisfy. Nothing executes until [`../schema.py`](../schema.py) has loaded the file: an unknown or ambiguous field fails the load, and [`../runner.py`](../runner.py) never gets to run it.

## Files

| File | Responsibility |
| --- | --- |
| [`.gitkeep`](.gitkeep) | Keeps the scenario root tracked on its own, whatever scenario groups exist beneath it. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`baseline/`](baseline/) | The required offline `tier:pr` scenarios. They cover deterministic provider sequencing, terminal submission, and scheduled failure behavior. |

Scenario JSON is input to the declared Harness fakes and nothing else; it is not permission to replay production side effects. Run a tier with `uv run python -m harness.cli run --tier pr --offline`.
