# Audit Dataset Skill

A structural audit of row-oriented tabular data, run before anything downstream is allowed to trust it, and written against the standard library only. Where `plan-ml-experiment` decides what the independent unit ought to be, this one looks at the rows you actually have and asks whether that boundary survives contact with them. When the Skill is loaded, its [`kernel.py`](kernel.py) sidecar puts reusable helpers into the persistent Python kernel. It does not read a dataset for you, and it will not decide how a domain-specific anomaly should be resolved.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install audit-dataset --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install audit-dataset`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/audit-dataset
python3 -m zipfile -c audit-dataset.zip audit-dataset
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/audit-dataset/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The pre-analysis workflow, built around the leakage question: a patient, a molecule scaffold, a time series or a near-duplicate sitting on both sides of the split inflates everything measured afterwards, and it does so even when the row IDs are all distinct. Around that: schema drift and mixed types as symptoms of a parsing or sentinel bug, missingness treated as a fact about the collection process rather than a hole to impute, duplicate IDs that may be legitimate repeated measures, target balance, and the machine-readable output the audit has to leave behind — a dataset may not be called clean without naming the leakage keys and missing-value policy that were checked. |
| [`kernel.py`](kernel.py) | Optional sidecar built around `audit_rows`. It validates the row and column arguments, canonicalizes values so comparisons are deterministic, summarizes missing values, observed types and unique counts column by column, counts duplicate rows and non-unique IDs, tallies the target, and reports entities that turn up on both sides of a split boundary. No pandas, no numpy. |

A clean structural pass does not establish representativeness, label quality, or the absence of near-duplicate leakage. Those still require domain review.
