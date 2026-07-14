# Harness evaluations

[中文](README_zh.md)

This directory contains reviewable, offline evaluation fixtures and scorers. Evaluations measure an architecture or quality boundary across a set of cases; they complement, but do not replace, focused assertions in [`../../tests/`](../../tests/).

## Direct files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Exports the action-routing evaluation surface. |
| [`action_routing.py`](action_routing.py) | Defines recorded model-reply cases for native Tool batches, Python/R Cells, Engine finalization, prose/no-action, unsupported fences, and priority rules; scores `route_action` and reports accuracy/confusion/failures. |
| [`.gitkeep`](.gitkeep) | Keeps the evaluation extension directory present independently of the current scorer set. |

## Direct subdirectories

None.

The evaluator is deterministic and requires no provider key, network, kernel, or optional package. Its pytest contract is [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py).
