# BioProBench Scripts

The upstream inference and LLM-judge scripts from the BioProBench project (<https://github.com/YuyangSunshine/bioprotocolbench>), adapted here with only a one-line provenance comment added at the top of each file.

They are written as standalone command-line scripts with module-level configuration constants, not as a library. `kernel.py` therefore loads them by file path through `importlib` rather than importing them — one filename contains hyphens and could not be imported as a module anyway — and overwrites those constants before calling `main()`. Each one performs network or GPU work when run: expect outbound API calls or a HuggingFace checkpoint load.

## Files

| File | Responsibility |
| --- | --- |
| [`prompt_format.py`](prompt_format.py) | `generate_user_prompt(sample, task_name)` — the standardised prompt per task, including the `[ANSWER_START] … [ANSWER_END]` output contract the evaluators later parse. The only script with no side effects and no third-party imports. |
| [`generate_response.py`](generate_response.py) | Batch inference against an OpenAI-compatible endpoint, with retry and periodic checkpointing. Adds a `generated_response` key to each record in place and skips records that already have one. Requires `openai` and `tqdm`. |
| [`generate_response_local.py`](generate_response_local.py) | The same batch loop against a local HuggingFace causal-LM through a `text-generation` pipeline pinned to `cuda:0`. Requires `transformers`, `torch`, and a GPU. |
| [`LLM-as-a-judge_for_REA-ERR.py`](LLM-as-a-judge_for_REA-ERR.py) | Grades REA-ERR responses for consistency with the reference error description, writing the verdict into an `LLM_judge` key that the REA-ERR evaluator then reads. Defaults to `deepseek-chat`; requires `openai`. |
