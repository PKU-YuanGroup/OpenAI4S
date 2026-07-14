# Using Model Endpoint Skill (Planned / Not Wired)

This directory describes a planned endpoint-scoped inference kernel and includes a trusted provider shim/manifest. **The current OpenAI4S composition does not wire this directory into an executable provider path:** `ComputeManager` discovers only `remote-compute-*` providers and supports the BYOC/SSH families, while `host.endpoints.*` currently registers/probes endpoints but does not create the scoped inference kernel described here.

Accordingly, [`SKILL.md`](SKILL.md) is progressive runbook/design material and the provider files are dormant implementation assets, not evidence that `compute_provider({'provider': ...})` works today. They must not be presented as an available end-to-end capability until discovery, lifecycle, and routing are connected and tested.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Planned recipe for calling a registered endpoint's native HTTP API from a kernel with preloaded `BASE_URL`, endpoint-scoped proxy egress, optional `INFER_API_KEY`, and no submit/harvest lifecycle. |
| [`provider.json`](provider.json) | Trusted provider manifest design for ID `infer`: declares a Python 3.11/pip helper with `httpx==0.28.1` and a placeholder control egress target; currently not discovered by `ComputeManager`. |
| [`provider.py`](provider.py) | Trusted but currently unwired `InferProvider`: scrubs inference/NVIDIA-shaped secrets; accepts an API key through the auth channel and exports canonical/safely validated aliases; requires cells to use HTTP directly; and rejects every create/exec/list/owner/terminate job-lifecycle operation. |
| [`requirements.lock`](requirements.lock) | Hash-pinned transitive helper dependencies for `httpx` (`anyio`, `certifi`, `h11`, `httpcore`, `idna`, and conditional `typing-extensions`); it does not install them unless a future wired provider builds this helper. |

## Direct subdirectories

None.
