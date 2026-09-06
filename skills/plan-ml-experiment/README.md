# Plan ML Experiment Skill

Turning an ML question into a reproducible, leakage-aware plan, written down before training starts. One choice governs the rest: which unit has to stay independent — the patient, the scaffold, the site, the document, the point in time. Its pure-stdlib sidecar builds deterministic splits and manifests from metadata the caller supplies. It trains nothing, and it cannot tell you that a split is scientifically appropriate.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install plan-ml-experiment --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. Once the package is on npm,
`npx openai4s-skills install plan-ml-experiment --target claude` is the short
form of the same command.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/plan-ml-experiment
python3 -m zipfile -c plan-ml-experiment.zip plan-ml-experiment
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/plan-ml-experiment/` out
(`unzip main.zip 'OpenAI4S-main/skills/plan-ml-experiment/*'` unpacks only that
directory). If you already run OpenAI4S there is nothing to install — the wheel
ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Naming the unit of analysis first, then reading the split off it: grouped for patients, scaffolds, sites, documents or repeated measures; chronological when deployment means predicting the future; random only when the rows genuinely are independent. Everything else hangs from that — hypothesis, intervention, baseline, primary metric and decision rule written before test performance is seen, then frozen seeds and configs, one-factor ablations, and the artifact set (fingerprint, checksums, split indices, per-example predictions) that lets someone rerun the comparison. Determinism is not validity: repeating one biased split reproduces the bias exactly. |
| [`kernel.py`](kernel.py) | Optional sidecar. `random_split` shuffles row indices under a seed, `chronological_split` orders them by timestamp without shuffling, and `grouped_split` keeps every group in exactly one partition. Alongside those: a canonical fingerprint for a configuration, SHA-256 for a file, and a JSON-compatible experiment manifest that records what it was given and invents no environment state. |

Group identifiers and chronology have to come from domain knowledge. If the experimental unit was defined wrong, nothing in the helper output will show it.
