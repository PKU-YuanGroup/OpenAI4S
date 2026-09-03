# Remote Compute NVIDIA Provider Skill

A progressive-disclosure runbook for NVIDIA NIM, sitting on top of a **trusted compute-provider boundary** the Host recognizes. [`provider.py`](provider.py) is not a `kernel.py` sidecar: it is provider implementation code, loaded by the confined compute-provider helper, and [`provider.json`](provider.json) declares the narrow environment and egress surface that helper gets.

The files being here is what makes `byoc:nvidia` discoverable in a compatible OpenAI4S composition. It says nothing about whether a job will actually run. Hosted mode needs a valid NVIDIA API key and a network; self-hosted mode needs Docker, an NVIDIA GPU with the Container Toolkit, a NIM image you can pull, and usually NGC credentials. Submitting a job stays permission-gated and spends real resources.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install remote-compute-nvidia --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name; `--dry-run` prints the resolved absolute path and writes
nothing. A reinstall refuses to overwrite a copy you have edited, and
`uninstall` removes only the files it wrote. The same command under the
package's published name, which is not on npm yet, is
`npx openai4s-skills install remote-compute-nvidia`.

Without Node, take the directory itself — and turn it into a `.zip` if an
upload field wants one:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/remote-compute-nvidia
python3 -m zipfile -c remote-compute-nvidia.zip remote-compute-nvidia
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip):
unzip it and copy `skills/remote-compute-nvidia/` out. If you already run
OpenAI4S there is nothing to install — the wheel ships every bundled Skill, and
a bundled Skill takes precedence over a same-named copy in
`<data_dir>/user-skills`. Targets, provenance, and what the installer refuses
to do: [`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook: choosing hosted versus self-hosted NIM, preparing and staging inputs, the `host.compute` create → submit → poll `.result()` → harvest flow, which key goes where and what the job's environment looks like, what to do when a run goes wrong, and how to check a result before believing it. |
| [`provider.json`](provider.json) | The trusted manifest for provider ID `nvidia`. It declares exactly two secret inputs, `NGC_API_KEY` and `NVIDIA_API_KEY`, and nothing else; the helper env is bare Python 3.11; egress is pinned to NVIDIA's control, registry and blob hosts; at most eight jobs run concurrently. |
| [`provider.py`](provider.py) | The trusted implementation. Credentials arrive over the helper's auth channel, and Docker is checked up front so a missing CLI fails with a clear error rather than a bare `FileNotFoundError` mid-op. Creating a handle creates a labelled container: a GPU NIM container pulled from `nvcr.io` for the self-hosted form, a slim keepalive container for the hosted form. The endpoint URL and the hosted key are injected only at `docker exec` time, so the job script never hard-codes which form it is running under. Ownership rides Docker labels, which is what makes the list and owner reads exact per installation; terminate is idempotent; docker's stderr is mapped onto structured error kinds; and the secret prefixes and the `nvapi-`/`nvcf-` token shapes to scrub are declared on the class. |
