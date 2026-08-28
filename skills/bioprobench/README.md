# BioProBench Skill

The progressive-disclosure recipe for BioProBench, a five-task benchmark over real wet-lab protocols: question answering, step ordering, error correction, protocol generation, and LLM-judged error reasoning. The benchmark data is not vendored here — only a two-record sample of the expected file shape.

The one thing worth knowing before reading anything else: scoring takes a single file that already carries the ground truth merged in beside each model response, not a model-output file plus a separate answer key. Feed it plain model output and every record fails to parse and the metrics land at zero; the envelope reports `status: "failed"`, and `Failed_Rate` is the field that tells you which of the two happened. The sidecar itself always imports — heavy dependencies are resolved per task at call time, so `ORD`, `ERR` and `REA-ERR` run on a bare kernel.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install bioprobench --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install bioprobench`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/bioprobench
python3 -m zipfile -c bioprobench.zip bioprobench
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/bioprobench/` out. If you already run OpenAI4S there
is nothing to install — the wheel ships every bundled Skill, and a bundled
Skill takes precedence over a same-named copy in `<data_dir>/user-skills`.
Targets, provenance, and what the installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The whole recipe: the five tasks and the metric keys each one returns (casing differs per task), the per-task record schema that scoring actually requires, and the `[ANSWER_START] … [ANSWER_END]` wrapper the parser expects. Then the real signatures — `run_bioprobench_eval` returning either a `{task, status, metrics}` envelope or an `{"error": …}` dict with no `status` field, so completion branches on `"error" in result`; `host.submit_output` needing its required completion bullets; and `trigger_batch_inference`, `get_task_prompt`, and `run_llm_judge_evaluation`. Ends with an honest per-task account of which third-party packages and which network calls each path needs. |
| [`kernel.py`](kernel.py) | The importable sidecar. One evaluator per task behind the `run_bioprobench_eval` dispatch, plus the bridge that dynamically loads the upstream scripts in `Scripts/` by file path (their hyphenated filenames are not importable) and overrides their module-level config before calling `main()`. Third-party dependencies are imported lazily per task, so the module itself imports anywhere. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`Scripts/`](Scripts/) | The upstream BioProBench inference and LLM-judge scripts, adapted unmodified. Loaded by path from `kernel.py`, not imported as a package. |
| [`data/`](data/) | A two-record PQA sample showing the exact expected record shape. The full ~5,000-instance benchmark is downloaded separately from Hugging Face. |
