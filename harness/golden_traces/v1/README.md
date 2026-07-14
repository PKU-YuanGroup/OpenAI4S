# Golden trace schema v1

[中文](README_zh.md)

This directory is the first versioned namespace for reviewed Harness trace data.

## Direct files

| File | Responsibility |
| --- | --- |
| [`r5_prechange.json`](r5_prechange.json) | Canonical normalized snapshot of selected r5 production behavior: CLI max turns, transport retry/partial-stream behavior, provider compaction projection, oversized observations, headless permission denial, and disabled MCP handling. Each case records current behavior, desired contract, and whether the snapshot captures a known bug. |

## Direct subdirectories

None.

Compare without writing via `uv run python -m harness.cli characterize`. A mismatch is a review signal, not permission to overwrite the golden automatically.
