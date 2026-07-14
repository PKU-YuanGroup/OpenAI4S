# Remote Compute NVIDIA Provider Skill

This directory combines a progressive-disclosure NVIDIA NIM runbook with a host-recognized **trusted compute-provider boundary**. Unlike `kernel.py` sidecars, [`provider.py`](provider.py) is provider implementation code loaded by the confined compute-provider helper; [`provider.json`](provider.json) declares the narrow environment/egress surface.

File presence makes `byoc:nvidia` discoverable in compatible OpenAI4S composition, but does not prove operational availability. Hosted mode needs a valid NVIDIA API key and network; self-hosted mode needs Docker, an NVIDIA GPU/Container Toolkit, an accessible NIM image, and usually NGC credentials. Job submission remains permission-gated and consumes real resources.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Progressive runbook for choosing hosted versus self-hosted NIM, preparing/staging inputs, `host.compute` create/submit/notification/harvest flow, auth/environment rules, recovery, and result validation. |
| [`provider.json`](provider.json) | Trusted provider manifest: registers ID `nvidia`; declares only `NGC_API_KEY`/`NVIDIA_API_KEY` as secret inputs; specifies the Python 3.11 helper, NVIDIA control/registry/blob egress, and maximum concurrency of eight. |
| [`provider.py`](provider.py) | Trusted provider implementation: receives credentials over the helper auth channel; checks Docker; creates labelled hosted keepalive or self-hosted GPU NIM containers; injects endpoint/key only for execution; adapts `docker exec`; lists/reads exact installation ownership; terminates idempotently; maps errors; and declares secret/token scrubbing. |

## Direct subdirectories

None.
