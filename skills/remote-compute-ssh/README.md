# Remote Compute SSH Skill

Dispatching work to an SSH/SLURM host the user has already configured: find out what the host actually offers, stage the files, submit and let the approval modal do its work, poll `.result()` from a later cell until the job is terminal, harvest the outputs, then write down what you learned about the host so the next session starts from it. Every submit puts a modal in front of the user and, once approved, spends their allocation — a string of failed submits costs their attention and their compute — so the recipe is shaped around landing the first one. It registers no SSH provider and grants access to no host.

Whether any of it works depends on the user's configuration, their credentials, the scheduler and allocation state, what software the remote actually has, and the approvals. Submitting a job spends real resources, so the recipe insists on validating the result and on explicit intent, and refuses to treat a queued command as a success.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install remote-compute-ssh --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install remote-compute-ssh`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/remote-compute-ssh
python3 -m zipfile -c remote-compute-ssh.zip remote-compute-ssh
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/remote-compute-ssh/` out. If you already run OpenAI4S
there is nothing to install — the wheel ships every bundled Skill, and a
bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook for `host.compute` and the control kernel: why these calls belong in the `repl` tool rather than the `python` tool, how to read the compute details doc and how much discovery is left, finding a working environment activation, staging inputs, submitting directly or through SLURM, polling `.result()` until the job is terminal, harvesting, cancellation and recovery, and updating the host notes afterwards. |
