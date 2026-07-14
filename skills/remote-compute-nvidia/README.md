# Remote Compute NVIDIA Provider Skill

A progressive-disclosure runbook for NVIDIA NIM, sitting on top of a **trusted compute-provider boundary** the Host recognizes. [`provider.py`](provider.py) is not a `kernel.py` sidecar: it is provider implementation code, loaded by the confined compute-provider helper, and [`provider.json`](provider.json) declares the narrow environment and egress surface that helper gets.

The files being here is what makes `byoc:nvidia` discoverable in a compatible OpenAI4S composition. It says nothing about whether a job will actually run. Hosted mode needs a valid NVIDIA API key and a network; self-hosted mode needs Docker, an NVIDIA GPU with the Container Toolkit, a NIM image you can pull, and usually NGC credentials. Submitting a job stays permission-gated and spends real resources.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | The runbook: choosing hosted versus self-hosted NIM, preparing and staging inputs, the `host.compute` create → submit → notification → harvest flow, which key goes where and what the job's environment looks like, what to do when a run goes wrong, and how to check a result before believing it. |
| [`provider.json`](provider.json) | The trusted manifest for provider ID `nvidia`. It declares exactly two secret inputs, `NGC_API_KEY` and `NVIDIA_API_KEY`, and nothing else; the helper env is bare Python 3.11; egress is pinned to NVIDIA's control, registry and blob hosts; at most eight jobs run concurrently. |
| [`provider.py`](provider.py) | The trusted implementation. Credentials arrive over the helper's auth channel, and Docker is checked up front so a missing CLI fails with a clear error rather than a bare `FileNotFoundError` mid-op. Creating a handle creates a labelled container: a GPU NIM container pulled from `nvcr.io` for the self-hosted form, a slim keepalive container for the hosted form. The endpoint URL and the hosted key are injected only at `docker exec` time, so the job script never hard-codes which form it is running under. Ownership rides Docker labels, which is what makes the list and owner reads exact per installation; terminate is idempotent; docker's stderr is mapped onto structured error kinds; and the secret prefixes and the `nvapi-`/`nvcf-` token shapes to scrub are declared on the class. |
