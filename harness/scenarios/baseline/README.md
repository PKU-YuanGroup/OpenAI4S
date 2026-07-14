# Baseline scenarios

[中文说明](README_zh.md)

These scenarios are the required, deterministic, offline `tier:pr` Harness gate. Each file is validated against [`../../schema.py`](../../schema.py) and run by [`../../runner.py`](../../runner.py): the run has to end where the file says it ends, emitting the events it declares, in order. Where a scenario declares `script_consumed`, its scripted model sequence must be used up as well.

## Files

| File | Responsibility |
| --- | --- |
| [`scheduled_timeout.json`](scheduled_timeout.json) | Fires a retryable timeout the first time the run reaches the `before_model` fault point. One model attempt, a `model_error` terminal reason, and an explicit `fault_injected` event in the trace; the scripted response sitting behind the fault is deliberately left unconsumed. |
| [`single_response_submitted.json`](single_response_submitted.json) | The straight path: one successful scripted response, one model attempt, a `submitted` terminal reason, the lifecycle events in order, and nothing left in the script. |
| [`two_response_sequence.json`](two_response_sequence.json) | Two scripted responses, so the loop runs twice. Checks that both attempts stay ordered and that only the second one carries the run to its final `submitted` terminal event. |

Do not loosen an expectation just to make a drifting run pass. Change a scenario when the intended contract changes, and review the trace that comes out.
