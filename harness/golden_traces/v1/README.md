# Golden trace schema v1

[中文说明](README_zh.md)

The first versioned namespace for reviewed Harness trace data. Everything here is at schema version 1.

## Files

| File | Responsibility |
| --- | --- |
| [`r5_prechange.json`](r5_prechange.json) | The reviewed snapshot of selected r5 production behavior, normalized to canonical bytes: CLI max turns, a 429 carrying a `Retry-After`, a stream that fails after a delta has already been committed, how the compaction summary projects onto provider payloads, an oversized observation, headless permission denial, and a disabled MCP connector. Each case records what production does today, the contract that is wanted instead, and whether the snapshot is freezing a known bug — and "pre-change" is by now only the file's name: four of the seven were recorded as known bugs, all four have since been fixed, and their `current_behavior` lines were rewritten to describe the fix. That is the file doing its job, not drifting. |

`uv run python -m harness.cli characterize` compares without writing. A mismatch means someone has to look at it; it is not permission to overwrite the golden automatically.
