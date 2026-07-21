# BioProBench Data

The full BioProBench test set is roughly 5,000 structured instances across five tasks. To keep this repository lightweight it is not hosted here — download it from Hugging Face (<https://huggingface.co/BioProBench>) into your working directory before running a real evaluation.

What ships here is a two-record sample, and its purpose is the schema rather than the content. Scoring reads the ground truth out of the same records that carry the model's response, so a file of plain model output scores zero while still reporting `success`. The sample is the reference for what a correctly shaped record looks like.

## Files

| File | Responsibility |
| --- | --- |
| [`sample_pqa_output.json`](sample_pqa_output.json) | Two PQA records in the exact shape `run_bioprobench_eval` expects: the benchmark fields (`question`, `answer`, `choices`, `type`, `id`) with a `generated_response` merged in alongside. `answer` is the ground truth the evaluator scores against, and each `generated_response` shows the required `[ANSWER_START] <choice> & <confidence> [ANSWER_END]` wrapper after an optional `</think>` prefix. |
