# Using Model Endpoint Skill (Planned / Not Wired)

A design for an endpoint-scoped inference kernel, together with the trusted provider shim and manifest it would need. **No part of this directory is wired into an executable provider path in a running OpenAI4S today:** `ComputeManager` discovers only `remote-compute-*` providers and supports the BYOC and SSH families, and `host.endpoints.*` registers and probes endpoints without ever creating the scoped inference kernel described here.

The Skill itself is still discoverable — the loader picks up every `skills/<name>/SKILL.md`, this one included, so an agent can list and load it through progressive disclosure. What it cannot do is run it. Read [`SKILL.md`](SKILL.md) as a runbook written ahead of its implementation, and the provider files as dormant assets. Their presence is not evidence that `compute_provider({'provider': ...})` works. Until discovery, lifecycle and routing are connected and tested, none of this may be presented as an end-to-end capability that exists.

## Install

A Skill is a directory of files, so installing one is copying that directory
somewhere an agent looks. With Node 18+ and `git` on `PATH` (npm resolves a
`github:` spec through it), and nothing cloned:

```bash
npx github:PKU-YuanGroup/OpenAI4S install using-model-endpoint --target claude
```

`--target claude` writes to `~/.claude/skills`, `claude-project` to
`./.claude/skills`, `openai4s` to `<data_dir>/user-skills`, and `--dir <path>`
to anywhere you name. The resolved absolute path is printed before anything is
written there, and `--dry-run` stops at that plan. A reinstall refuses to
overwrite a copy you have edited or one it did not install, and `uninstall`
removes only the files it wrote. Once the package is on npm,
`npx openai4s-skills install using-model-endpoint --target claude` is the short
form of the same command.

Without Node, take the directory itself — and turn it into a `.zip` if an upload
field wants one. The tarball is the whole repository (over 100 MB), and the pipe
is a POSIX shell recipe (macOS, Linux, WSL) that extracts only this directory:

```bash
curl -L https://codeload.github.com/PKU-YuanGroup/OpenAI4S/tar.gz/refs/heads/main \
  | tar -xz --strip-components=2 OpenAI4S-main/skills/using-model-endpoint
python3 -m zipfile -c using-model-endpoint.zip using-model-endpoint
```

The click-through form of that same download is the
[repository zip](https://github.com/PKU-YuanGroup/OpenAI4S/archive/main.zip),
which is also the route to take on Windows: extract it and copy
`skills/using-model-endpoint/` out
(`unzip main.zip 'OpenAI4S-main/skills/using-model-endpoint/*'` unpacks only
that directory). If you already run OpenAI4S there is nothing to install — the
wheel ships every bundled Skill, and a bundled Skill takes precedence over a
same-named copy in `<data_dir>/user-skills`. Targets, provenance, and what the
installer refuses to do:
[`tools/skills-installer/`](../../tools/skills-installer/README.md).

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The planned recipe: a cell calls a registered endpoint's own HTTP API, building request URLs from a preloaded `BASE_URL`, sending `Authorization: Bearer $INFER_API_KEY` when the endpoint is hosted, and letting the endpoint-scoped sandbox proxy carry the egress. Request and response only, with no submit/harvest lifecycle. |
| [`provider.json`](provider.json) | The manifest that would register provider ID `infer`: a Python 3.11 pip helper env carrying `httpx==0.28.1`, and a control-egress target that is still a placeholder. `ComputeManager` does not look here. |
| [`provider.py`](provider.py) | `InferProvider`, trusted but unreachable. Ambient `INFER_*` and `NVIDIA_*` variables are scrubbed before auth, so an API key can only arrive over the host's auth channel; it is then re-exported under the canonical name `INFER_API_KEY`, and under the registration's own credential name as well once that alias passes validation. Tokens shaped like `nvapi-…` are redacted from output. There is no SDK to import, because cells are expected to speak HTTP for themselves, and every job-lifecycle op (create, exec, list, read owner, terminate) refuses. |
| [`requirements.lock`](requirements.lock) | Hash-pinned dependencies for the `httpx` helper: `anyio`, `certifi`, `h11`, `httpcore`, `idna`, and `typing-extensions` below Python 3.13. Nothing installs from it unless a future wired provider builds this helper env. |
