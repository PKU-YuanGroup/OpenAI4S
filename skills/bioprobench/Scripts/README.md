# bioprobench scripts

Runnable pieces of the BioProBench evaluation.

- [`prompt_format.py`](prompt_format.py) — builds the task prompts from the dataset.
- [`generate_response.py`](generate_response.py) — collects model responses through a hosted API.
- [`generate_response_local.py`](generate_response_local.py) — the same, against a locally served model.
- [`LLM-as-a-judge_for_REA-ERR.py`](LLM-as-a-judge_for_REA-ERR.py) — scores the REA-ERR task with a model judge.
