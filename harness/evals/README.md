# Harness evaluations

[中文说明](README_zh.md)

Offline eval fixtures and the code that scores them. An eval measures one architecture or quality boundary across a whole set of cases, which is a different job from the focused assertions in [`../../tests/`](../../tests/); it complements them and does not replace them.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Exports the action-routing and retrosynthesis-backend evaluation surfaces. |
| [`action_routing.py`](action_routing.py) | Scores the deterministic router, `route_action`. Each fixture is one recorded model reply standing for a task class: a native Tool batch, a Python or R Cell, an Engine finalization, prose that must not be read as completion, an unsupported fence, and the two priority rules — a native Tool batch beats a fenced Cell, and only the first Cell in a reply is routed. The report carries accuracy, a confusion map, and every case with its pass/fail. |
| [`retrosynthesis_backends.py`](retrosynthesis_backends.py) | Replays versioned external-model responses through the production response normalizer without loading model weights. It scores schema validity, expected success/error behavior, prediction counts, checkpoint-provenance completeness, scored-prediction coverage, and deterministic response digests. |
| [`retrosynthesis_backend_cases.json`](retrosynthesis_backend_cases.json) | Synthetic, public-safe response tapes for one successful RetroChimera-shaped prediction batch and one checkpoint-download refusal. The fixtures contain no model weights, private chemistry, network result, or real checkpoint path. |
| [`.gitkeep`](.gitkeep) | Keeps the directory tracked in git, whatever the current set of scorers happens to be. |

Both evaluators are deterministic and need no provider key, network, kernel, GPU, or optional model package. The action-routing pytest contract is [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py); the external-model protocol and replay contracts are exercised by [`../../tests/test_harness_contract.py`](../../tests/test_harness_contract.py).
