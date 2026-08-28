# Evaluate Model Skill

Held-out evaluation, plus a few pure-stdlib metric helpers. A score on its own means nothing, so the decision this Skill keeps pushing at you is the comparison: which baseline the number has to beat, and which metric was named before anyone looked at the test set. Loading the Skill may attach [`kernel.py`](kernel.py) to the persistent Python kernel. Nothing is produced on your behalf: no model, no predictions, no split, and no scientific conclusion.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install evaluate-model --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install evaluate-model`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/evaluate-model
python3 -m zipfile -c evaluate-model.zip evaluate-model
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/evaluate-model/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Fix the primary metric from the question, not from the results — a metric chosen after inspecting the test set has stopped being evidence — and score it against a simple baseline with an interval, never a bare point estimate. Also: confirming the evaluation rows and their related entities never touched fitting, preprocessing, feature selection or threshold choice; keeping probability quality, ranking quality and thresholded decisions apart, since accuracy rarely settles an imbalanced target; subgroup and failure-mode checks; how to call the binary and regression helpers; and the reporting contract, down to the fact that a bootstrap interval describes resampling variability and repairs no leakage, shift or dependence between rows. |
| [`kernel.py`](kernel.py) | Optional sidecar. `binary_classification_metrics` returns the confusion counts, accuracy, precision, recall, specificity, F1, balanced accuracy, and a tie-aware ROC AUC when scores are supplied; a ratio with an empty denominator comes back as `None` rather than zero. `regression_metrics` returns MAE, RMSE, bias and R². `bootstrap_ci` gives a percentile interval over scalar observations, deterministic for a given seed. |

These helpers summarize the observations you hand them. They cannot verify that the data were held out, independent, representative, or clinically meaningful.
