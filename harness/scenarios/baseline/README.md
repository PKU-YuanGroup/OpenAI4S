# Baseline scenarios

[中文](README_zh.md)

These scenarios form the required, deterministic, offline `tier:pr` Harness gate. Each file is validated against [`../../schema.py`](../../schema.py), run by [`../../runner.py`](../../runner.py), and must consume its scripted model sequence and satisfy the declared ordered-event/terminal invariants.

## Direct files

| File | Responsibility |
| --- | --- |
| [`scheduled_timeout.json`](scheduled_timeout.json) | Injects a retryable timeout at the first `before_model` fault point and expects a single-attempt `model_error` lifecycle with an explicit `fault_injected` event. |
| [`single_response_submitted.json`](single_response_submitted.json) | Supplies one successful scripted response and expects one model attempt, a `submitted` terminal reason, ordered lifecycle events, and complete script consumption. |
| [`two_response_sequence.json`](two_response_sequence.json) | Supplies a two-step response sequence and checks that both attempts remain ordered before the final `submitted` terminal event. |

## Direct subdirectories

None.

Do not weaken expected invariants merely to accept drift; change a scenario only when the intended contract changes and review the resulting trace.
