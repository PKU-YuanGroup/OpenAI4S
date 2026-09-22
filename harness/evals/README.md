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
| [`judgment_skills.py`](judgment_skills.py) | Frozen Skill-suggestion evaluation (plan §7). Scores B0 lexical search and B2 glossary expansion offline; B1 (LLM rewrite), J1 (first `suggest` request) and J2 (full `suggest`) require `--live`. Reports top-1 / top-3, error and unnecessary recommendation, abstention, latency, tokens, and bootstrap 95% CIs, split by language and category. `--split test` is locked behind `--frozen-thresholds`. |
| [`judgment_skills_cases.json`](judgment_skills_cases.json) | About 200 agent-authored zh/en queries with gold Skill names, stratified 50/50 into `dev`/`test` (`lang × category`, seed 20260920). Human review of at least 20% is still required. |
| [`judgment_skills_cases.lock`](judgment_skills_cases.lock) | SHA-256 of the test split (ids sorted, canonical JSON). Changing a test query or gold label fails the lock check. |
| [`judgment_skills_zh_en_terms.json`](judgment_skills_zh_en_terms.json) | ≤300-term zh→en glossary for B2. Built only from the development split; construction notes are in `_construction`. |
| [`judgment_literature.py`](judgment_literature.py) | Frozen literature claim-check evaluation (plan §7). Offline `CODE` scores quote location and numeric/unit compare; `LLM` (main-model three-way) and `J` (`check_claims`) require `--live`. Reports per-status accuracy, confusion, high-confidence error, support/contradiction recall, review rate, latency, tokens, and bootstrap 95% CIs, split by language. `--split test` is locked behind `--frozen-thresholds`. |
| [`judgment_literature_cases.json`](judgment_literature_cases.json) | About 300 OA (claim, passage) pairs with CC licenses and DOIs, ~30% Chinese, stratified 50/50 into `dev`/`test` (`lang × perturbation`, seed 20260920). Claims are synthetic rewrites; human review of at least 20% is still required. |
| [`judgment_literature_cases.lock`](judgment_literature_cases.lock) | SHA-256 of the test split (ids sorted, canonical JSON). Changing a test claim, quote, source, or gold label fails the lock check. |

Both evaluators are deterministic and need no provider key, network, kernel, GPU, or optional model package. The action-routing pytest contract is [`../../tests/test_action_routing_eval.py`](../../tests/test_action_routing_eval.py); the external-model protocol and replay contracts are exercised by [`../../tests/test_harness_contract.py`](../../tests/test_harness_contract.py).
