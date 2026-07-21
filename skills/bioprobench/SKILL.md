---
name: bioprobench
description: >
  Score an LLM's biological-protocol reasoning on the BioProBench benchmark:
  protocol QA, step ordering, error detection, protocol generation, and
  LLM-judged error reasoning; or generate the responses.
origin: openai4s
category: model-evaluation
metadata:
  display-name: BioProBench
  upstream: https://github.com/YuyangSunshine/bioprotocolbench
---

# BioProBench — protocol understanding and reasoning

Biological protocols are where a plausible-sounding answer becomes a failed
experiment: a wrong dosage, a swapped step, an unflagged hazard. BioProBench
scores a model on five tasks over real wet-lab protocols, roughly 5,000
instances in the full release.

| Task | What it measures | Metrics returned |
| --- | --- | --- |
| `PQA` | Protocol question answering — reagents, dosages, parameters | `Accuracy`, `Brier_Score`, `Failed_Rate` |
| `ORD` | Step ordering — reconstructing procedural sequence | `Exact_Match`, `Kendall_Tau`, `Failed_Rate` |
| `ERR` | Error correction — is this modified step valid | `accuracy`, `precision`, `recall`, `f1`, `failed_rate` |
| `GEN` | Protocol generation — synthesising steps | `BLEU`, `METEOR`, `ROUGE-L`, `KW_F1`, `Step_Recall`, `Redundancy_Penalty`, `Failed_Rate` |
| `REA-ERR` | Error reasoning, graded by an LLM judge | `Consistency`, `Failure_Rate`, `Total`, `Failed`, `Total_Items`, `Judged`, `Unjudged`, `Coverage` |

Metric key casing differs per task — `ERR` returns lowercase keys, the rest are
capitalised. Read them off the table above rather than guessing.

## The input contract is the thing that bites

`run_bioprobench_eval` does **not** take a plain model-output file and compare
it against a separate answer key. It takes **one file that already has the
ground truth merged into each record alongside the model's response.** The
upstream inference scripts produce exactly this, by adding a
`generated_response` key to each benchmark record in place.

Hand it a file containing only model outputs and it does not raise: every
record simply fails to parse and the metrics come back at zero. The envelope
says so — `status` is `"failed"` when nothing scored and `"partial"` when some
records dropped out — but still check `Failed_Rate` on every run. A rate of
`1.0` means the input contract was violated, not that the model scored zero.

Required keys per record, per task:

| Task | Model output key | Ground-truth key(s) |
| --- | --- | --- |
| `PQA` | `generated_response` | `answer` |
| `ORD` | `generated_response` | `wrong_steps`, `correct_steps` |
| `ERR` | `generated_response` | `is_correct` (`true`/`false`, `1`/`0`, or `"true"`/`"false"`) |
| `GEN` | `generated_response` | `output` (string, or list of reference steps) |
| `REA-ERR` | `LLM_judge` | none — the judgment text is itself the signal |

`REA-ERR` scores only records that carry an `LLM_judge` key. `Consistency` and
`Failure_Rate` are over that judged subset, so read `Unjudged` and `Coverage`
alongside them — a partly-judged file reports `status: "partial"`. Populate
`LLM_judge` with `run_llm_judge_evaluation` first.

Except for `REA-ERR`, the parser wants the answer wrapped in
`[ANSWER_START] … [ANSWER_END]`. `PQA` additionally expects
`answer & confidence` inside those tags; without the `&` a trailing token is
read as the confidence only when it looks like one (`0-100`, optional `%`) and
something is left over for the answer, otherwise the record counts as failed
rather than being scored against a fabricated confidence. `ORD` expects a
Python list literal of indices that is a genuine permutation of
`range(len(wrong_steps))` — anything else counts that one record as failed and
leaves the rest of the run intact. Anything before a `</think>` or
`[/INST]` marker is stripped first. See
[`data/sample_pqa_output.json`](data/sample_pqa_output.json) for two records in
the exact expected shape.

## Getting the data

Only the two-record sample above ships here. Download the full benchmark from
Hugging Face (<https://huggingface.co/BioProBench>) into your working directory
before running a real evaluation.

## Scoring an existing response file

```python
from bioprobench.kernel import run_bioprobench_eval

result = run_bioprobench_eval(
    task_name="PQA",  # 'PQA' | 'ORD' | 'ERR' | 'GEN' | 'REA-ERR'
    response_file_path="/path/to/PQA_test_o3-mini.json",
)
```

On success the return value is:

```python
{"task": "PQA", "status": "success",
 "metrics": {"Accuracy": 1.0, "Brier_Score": 0.021, "Failed_Rate": 0.0}}
```

`status` is `"success"` only when every record scored; `"partial"` when some
were dropped (or, for `REA-ERR`, left unjudged); `"failed"` when none scored.

On a missing file, an unknown task name, a file that is not a non-empty JSON
list of records, or any exception raised inside the evaluator, it returns a dict
with **no `status` field**, so branch on `"error" in result`:

```python
{"error": "File not found: /path/to/PQA_test_o3-mini.json"}

{"error": "out.json contains no response records, so there is nothing to"
          " score for task PQA.",
 "task": "PQA"}

{"error": "Evaluation failed during execution of task GEN on out.json:"
          " KeyError: 'output'",
 "task": "GEN", "traceback": "Traceback (most recent call last): …"}
```

Completion goes through `host.submit_output`, which takes the structured output
**and** a required list of 1-4 completed-action bullets:

```python
if "error" in result:
    raise RuntimeError(result["error"])

metrics = result["metrics"]
host.submit_output(
    result,
    [
        f"Scored the model's BioProBench {result['task']} responses.",
        f"Reported accuracy {metrics['Accuracy']:.3f} at a"
        f" {metrics['Failed_Rate']:.1%} parse-failure rate.",
    ],
)
```

## Generating responses first

`trigger_batch_inference` runs the upstream inference scripts over a benchmark
file and writes `<TASK>_test_<model>_api.json` (or `..._local.json`) into the
current working directory. `task_name` and `model_name` are reduced to a safe
filename component first, so a slash-bearing model id lands beside the others
rather than somewhere else on disk. It returns `{"status": ..., "message": ...}` and
`{"error": ...}` when `mode="API"` is missing a key.

```python
from bioprobench.kernel import trigger_batch_inference

trigger_batch_inference(
    task_name="PQA",
    test_file_path="/path/to/PQA_test.json",
    mode="API",            # "API" (OpenAI-compatible) or "Local" (HuggingFace)
    model_name="o3-mini",
    api_key="...",         # required when mode="API"
)
```

Both modes leave the machine: `"API"` sends every prompt to the configured
endpoint, `"Local"` loads a HuggingFace checkpoint onto `cuda:0`. Neither is
sandboxed by this skill, and the API key you pass is handed straight to the
client.

Two smaller helpers:

```python
from bioprobench.kernel import get_task_prompt, run_llm_judge_evaluation

# Standardised prompt for one record. Returns the prompt, or an error *string*
# beginning "Error loading prompt format:" — it does not raise.
prompt = get_task_prompt(sample, "PQA")

# Populate `LLM_judge` for REA-ERR. Returns the judgment text, or an error
# string. Requires the `openai` package and a live endpoint.
judgment = run_llm_judge_evaluation(
    sample,
    api_key="...",
    base_url="https://api.deepseek.com",
    model_name="deepseek-chat",
)
```

## Dependencies and network access

Two things to know before the first call.

**Importing `kernel.py` always works.** Heavy dependencies are resolved per
task, at call time — so `ORD`, `ERR` and `REA-ERR` run on a bare kernel, and a
task whose dependencies are missing raises an `ImportError` naming exactly the
packages it needs. None of them ship in the default OpenAI4S venv.

**Only `GEN` touches the network for corpora.** It fetches the NLTK tokenizer
(`punkt_tab`, or `punkt` on nltk < 3.8.2) and `wordnet` on first use. A failure
there is not fatal: the fetch is reported under a `NLTK_Data_Warnings` metric
key. `trigger_batch_inference` and `run_llm_judge_evaluation` are the calls that
send your data off-machine.

What each task needs:

| Task | Runtime requirements |
| --- | --- |
| `ORD`, `ERR` | Standard library only |
| `PQA` | `numpy`, `scikit-learn` |
| `GEN` | `numpy`, `scikit-learn`, `nltk`, `rouge_score`, `keybert`, `sentence_transformers`, plus a first-call download of the `all-mpnet-base-v2` and `all-MiniLM-L6-v2` sentence-transformer checkpoints. Slowest task by a wide margin. |
| `REA-ERR` | Standard library to score; `openai` and a live endpoint to produce the judgments |
| `trigger_batch_inference` | `openai` for `"API"`; `transformers`, `torch`, and a GPU for `"Local"` |

## Citation

Adapted from the upstream BioProBench project
(<https://github.com/YuyangSunshine/bioprotocolbench>). If you use this
benchmark, cite the original work:

```bibtex
@article{liu2025bioprobench,
  title={BioProBench: Comprehensive Dataset and Benchmark in Biological Protocol Understanding and Reasoning},
  author={Liu, Yuyang and Lv, Liuzhenghao and Zhang, Xiancheng and Yuan, Li and Tian, Yonghong},
  journal={ICML},
  url={https://github.com/YuyangSunshine/bioprotocolbench/tree/main},
  year={2026}
}
```
