# Harness providers

[中文](README_zh.md)

This directory contains deterministic substitutes for external platform boundaries. They consume declared inputs, never contact a live service, and retain inspectable call records so scenarios can assert orchestration rather than transport availability.

## Direct files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Exports the scripted LLM and its structured error type. |
| [`scripted_llm.py`](scripted_llm.py) | Implements a queue-backed model callable: deep-copies messages, returns declared normalized responses in order, raises declared provider errors, exposes remaining steps, and fails on script exhaustion. |
| [`.gitkeep`](.gitkeep) | Keeps the provider extension directory present when no additional fake provider is committed. |

## Direct subdirectories

None.

Provider scripts are defined by [`../schema.py`](../schema.py) and consumed by [`../runner.py`](../runner.py). Future compute, endpoint, or lab fakes belong here only when they remain offline by default.
