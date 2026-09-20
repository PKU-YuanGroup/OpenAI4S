# Harness providers

[中文说明](README_zh.md)

The fake providers a scenario runs against. Each one stands in for an external platform boundary: it answers only from what the scenario declared, never opens a connection to a live service, and keeps an inspectable record of the calls it received. That is what lets a scenario assert what the orchestration did instead of whether some service happened to be up.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Exports the scripted LLM and its structured error type. |
| [`scripted_llm.py`](scripted_llm.py) | A model callable backed by a queue of scripted steps. It returns the declared normalized responses in order (filling in the usual `reasoning`, `usage`, `finish_reason` and `raw` defaults), raises `ScriptedProviderError` wherever the script declared an error, and reports how many steps are left; every incoming message list is deep-copied into `calls`, so a scenario can inspect the prompt afterwards. Running off the end of the script raises `AssertionError` rather than repeating the last reply. |
| [`.gitkeep`](.gitkeep) | Keeps the directory tracked in git so the next fake provider has somewhere to land. |
| [`typesafe_fake.py`](typesafe_fake.py) | Loopback TypeSafe System One fake: `POST /v1/systemone` with a non-empty Bearer, deterministic Noul/Choice/Score answers, `--script` / `--fail` / `--latency-ms` / `--log`, and in-process `start_fake(port=0) -> (url, stop)`. Refuses to bind a non-loopback address; the JSONL log records the body and never Authorization. |

The script a provider replays is the `provider_script` field validated by [`../schema.py`](../schema.py), and [`../runner.py`](../runner.py) is what drives it. A future compute, endpoint, or lab fake belongs here too, as long as it stays offline by default.
