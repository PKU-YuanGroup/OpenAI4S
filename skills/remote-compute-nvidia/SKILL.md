---
name: remote-compute-nvidia
description: Run GPU jobs on NVIDIA NIM microservices via host.compute.create('byoc:nvidia', ...). Covers both forms — self_hosted (an nvcr.io NIM container on a local GPU with --gpus all) and hosted (the fully-managed integrate.api.nvidia.com gateway, no local GPU) — sharing one submit→poll .result()→harvest flow. Load once you've decided to dispatch to NVIDIA NIM.
license: Apache-2.0
origin: openai4s
---

You're dispatching to an NVIDIA NIM microservice. This provider speaks two
forms that share ONE job contract, chosen per handle by
`provider_params={'nvidia': {'mode': ...}}`:

- **`self_hosted`** — pull and run an NVIDIA NIM container from `nvcr.io` on a
  local GPU host (`--gpus all`). The NIM server is the container's own
  long-lived process; the job curls it at `http://localhost:8000` and
  health-gates on `/v1/health/ready`. Needs Docker + the NVIDIA Container
  Toolkit, plus an NGC API key (`NGC_API_KEY`) with pull access to the image.
- **`hosted`** — no local GPU. A slim keepalive container is started and the
  job curls the fully-managed endpoint at `https://integrate.api.nvidia.com`
  with a `Bearer nvapi-…` key (`NVIDIA_API_KEY`). Use this when you have no
  local accelerator or just want the managed API.

Both forms create a Docker container that plays the "sandbox" role: inputs are
untarred into `/work`, the job wrapper runs, and `/work/out.tar.gz` is
harvested back into your workspace under `hpc/<jobId>/` — identical to every
other `byoc:` provider. The only prerequisite the open-source install needs is
Docker (and, for `self_hosted`, the NVIDIA Container Toolkit for `--gpus`).

If `compute.create('byoc:nvidia', …)` returns `unknown provider 'byoc:nvidia'`,
the provider isn't discoverable in this install — confirm
`skills/remote-compute-nvidia/` ships both `provider.json` and `provider.py`.

## Which form to pick

| you have | pick | auth env | where the job runs |
|---|---|---|---|
| a local NVIDIA GPU + Docker + Container Toolkit | `self_hosted` | `NGC_API_KEY` | `nvcr.io` NIM container, `localhost:8000` |
| no local GPU, an `nvapi-…` key | `hosted` | `NVIDIA_API_KEY` | managed `integrate.api.nvidia.com` |

`self_hosted` keeps weights and traffic on your machine and needs no per-request
egress; `hosted` needs no GPU but every job request leaves for NVIDIA's gateway.
Set the key in the environment before you submit — the host forwards only the
declared vars (`NGC_API_KEY`, `NVIDIA_API_KEY`) to the confined helper, never
the whole environment, and both are scrubbed from every log tail that leaves the
sandbox.

## Workflow

Every `host.compute.*` call here runs via the **`repl` tool** (the
control-plane kernel), not the `python` tool — job submission opens the
approval modal and talks to Docker from the orchestrator's own process, which
must happen outside the sandboxed data workspace. The two kernels share your
workspace directory but not memory, so the rhythm is: prepare inputs in a
`python` cell, run `create → submit_job` in a `repl` cell and let the cell
return (the kernel never blocks on compute), then poll `.result()` from a
later `repl` cell until the status is terminal, and read the harvested
`hpc/<jobId>/` files back in the `python` tool.

`host.compute.create('byoc:nvidia', provider_params={'nvidia': {...}})` is a
stateless constructor — the tier card and the actual container creation both
happen on the first `submit_job()`. `submit_job`/`result`/`attach_job`/`close`
then work exactly as for SSH: `.result()` is **non-blocking** and is what
drives the job forward — each call probes the container and, once the work is
terminal, harvests `out.tar.gz` into `hpc/<jobId>/`. Nothing runs in the
background: there is no daemon poller and no notification, so a job you never
poll is never harvested.

### Hosted form — call the managed API

```python
# repl tool — cell ① submits and RETURNS
c = host.compute.create('byoc:nvidia', provider_params={'nvidia': {
    'mode': 'hosted',
}})
job = c.submit_job(
    intent='esmfold2 fold on target.fasta via NVIDIA hosted NIM',
    # the job script curls $OPENAI4S_NIM_URL with $NVIDIA_API_KEY —
    # never hard-code the endpoint or the key
    command='bash run_infer.sh',
    inputs=[{'src': 'run_infer.sh', 'dst_filename': 'run_infer.sh'},
            {'src': 'target.fasta', 'dst_filename': 'target.fasta'}],
    outputs=[{'glob': 'out/*.pdb', 'visibility': 'featured'},
             {'glob': '*.log', 'visibility': 'hidden'}],
    timeout_seconds=900)
print('JOB_ID:', job.job_id)  # ← cell ends here
```

The job's `run_infer.sh` reads the endpoint and key from the injected env, so
it is form-agnostic:

```bash
#!/usr/bin/env bash
set -eo pipefail
mkdir -p out
curl -sS -X POST "$OPENAI4S_NIM_URL/v1/biology/nvidia/esmfold2/predict" \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -H "Content-Type: application/json" \
  -d @request.json > out/prediction.json
```

### Self-hosted form — run a NIM container on your GPU

```python
# repl tool — cell ① submits and RETURNS
c = host.compute.create('byoc:nvidia', provider_params={'nvidia': {
    'mode': 'self_hosted',
    'image': 'nvcr.io/nim/meta/esmfold2:1.0.0',   # the nvcr.io NIM image
}})
job = c.submit_job(
    intent='esmfold2 fold on target.fasta — local GPU NIM',
    command='bash run_infer.sh',
    inputs=[{'src': 'run_infer.sh', 'dst_filename': 'run_infer.sh'},
            {'src': 'target.fasta', 'dst_filename': 'target.fasta'}],
    outputs=[{'glob': 'out/*.pdb', 'visibility': 'featured'}],
    timeout_seconds=1800)
print('JOB_ID:', job.job_id)  # ← cell ends here
```

The NIM server boots inside the container; gate on readiness before the first
request, then curl `localhost`:

```bash
#!/usr/bin/env bash
set -eo pipefail
mkdir -p out
# health-gate: the NIM server takes a moment to load weights on cold start
for i in $(seq 1 60); do
  curl -fsS "$OPENAI4S_NIM_URL$OPENAI4S_NIM_HEALTH" && break
  sleep 5
done
curl -sS -X POST "$OPENAI4S_NIM_URL/v1/biology/nvidia/esmfold2/predict" \
  -H "Content-Type: application/json" \
  -d @request.json > out/prediction.json
```

`OPENAI4S_NIM_URL` is `http://localhost:8000` (self_hosted) or
`https://integrate.api.nvidia.com` (hosted); `OPENAI4S_NIM_HEALTH` is the
`/v1/health/ready` path. Writing your job against these two variables means the
same script runs unchanged on both forms.

Then exit the cell and poll from a later one. `.result()` returns
`{job_id, status, exit_code, featured_files, output_files, stdout_tail,
stderr_tail, ...}` once the job is terminal; while it's still running you get
`{'status': 'running', ...}` — end the cell and call it again later rather
than waiting inside the cell.

```python
# repl tool — cell ② polls; re-run this cell until the status is terminal
r = c.attach_job('<JOB_ID>').result()  # one probe — harvests when terminal
print(r['status'])
if r['status'] != 'running':
    save_artifacts(r['featured_files'])
    c.close()
```

## `submit_job` details

`inputs=` stage **flat** into the workdir root — `dst_filename` is a bare
filename (a `/` is rejected at submit). `src` can be a path or the literal
`{{artifact:ID}}` marker. Need a dir layout? `mkdir -p` it inside `command=`.

Only `./out/` (plus `stdout.log`/`stderr.log`) is harvested. If your tool
writes elsewhere, end `command=` with `cp -r <results> out/`. `outputs=` globs
are a post-harvest featured/hidden filter, not a what-to-collect directive.

`command=` is interpolated into a `run.sh` and run via `bash run.sh`. For
anything beyond a single program-with-args — nested quotes, heredocs,
pipelines — write the script to a workspace file, ship it via `inputs=`, and
use `command='bash script.sh'`. Multi-layer shell escaping inside `command=`
is the most common cause of `syntax error near unexpected token`.

`timeout_seconds` guards one job; at the deadline the job is TERMed, its
partial outputs staged, and it lands as `status: 'timed_out'` (not a generic
failure). Keep `./out/` checkpoints small — the harvest stream runs in a
bounded window, so a multi-GB `out/` risks `harvest_failed`.

## When the user gives you a budget

`host.compute.set_concurrency_limit(k)` makes the user's ceiling a property of
the session: call it once before delegating and the daemon counts every
sub-agent's live job against the same `k`, holding any submit that would go
over. The provider also has its own ceiling (`max_concurrent`, 8 by default) —
`host.compute.status()` returns both your `k` and the provider ceiling so you
can pick a value that actually queues rather than errors.

## When the job fails

Read `r['exit_code']`, `r['stdout_tail']`, and `r['stderr_tail']`. The errors
that come back as `kind` rather than a non-zero exit code map cleanly onto
where to look:

`unauthorized` — NGC/nvcr.io rejected the credential. For `self_hosted`, check
`NGC_API_KEY` has pull access to the NIM image; for `hosted`, check
`NVIDIA_API_KEY` is a valid `nvapi-…` key. The user fixing the key is the whole
fix — resubmit on a fresh handle.

`provider_degraded` — Docker or the NVIDIA Container Toolkit isn't available for
`--gpus`. Install the Container Toolkit, or switch to `mode='hosted'` (no local
GPU needed).

`rate_limited` — a request-rate throttle (hosted gateway) or an `nvcr.io` pull
throttle. Back off ~60s and stagger fan-out submissions; closing containers
frees nothing here.

`not_found` — the container was already gone (a preemption or a prior
terminate). The submit cold-starts a fresh one; nothing to do beyond noting it.

A plain non-zero `exit_code` with logs is the NIM tool failing on inputs —
inspect `stdout_tail`/`stderr_tail`. A `4xx`/`5xx` in the curl output means the
endpoint was reachable and answered: an application/request problem (wrong
model path, malformed request body), not the provider.

## Network egress from the job

For `hosted`, the job must reach `integrate.api.nvidia.com` (declared in this
provider's egress). For `self_hosted`, the model call is to `localhost` inside
the container and needs no outbound egress at all — only image pull and NGC
login touch the network (`nvcr.io`, `api.ngc.nvidia.com`, `authn.nvidia.com`,
also declared). Move big one-time fetches — model weights — into image build or
a warmed container rather than a fetch inside every job.

## Warm reuse and `close()`

One handle = one container. The first `submit_job()` creates it; subsequent
calls reuse it warm (weights stay hot in the NIM server). A `.result()` harvest
does **not** terminate the container — it runs until `c.close()`. Every handle
ends with `c.close()` after its last job, which is what tears the container
down (`docker rm -f`). **Sequential only**: each submit wipes `/work`, so call
job N+1's `submit_job()` only after `.result()` has reported job N terminal;
for parallel jobs use separate handles.
