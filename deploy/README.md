# Kubernetes deployment manifests

[中文说明](README_zh.md)

The cluster half of containerized OpenAI4S. The image itself is built by the
repository-root [`Dockerfile`](../Dockerfile), and single-node deployment is
[`compose.yaml`](../compose.yaml); both are documented together with these
manifests in [docs/docker.md](../docs/docker.md).

These are plain manifests rather than a Helm chart on purpose. There is one
deployable unit here — a stateful singleton with one volume, one Service and no
optional components — so a chart would add a templating layer over four objects
whose values a reader can simply edit.

## Files

| File | Purpose |
| --- | --- |
| `kubernetes.yaml` | The whole deployment: a `ReadWriteOnce` PersistentVolumeClaim for the data dir, a single-replica `Recreate` Deployment with startup/readiness/liveness probes on `/health`, and a ClusterIP Service. No namespace is set on any object, so it applies into whichever one you name. Three values in it are load-bearing rather than conventional, and each is commented in place: `replicas: 1` (the store is SQLite and the daemon is a pidfile singleton), `automountServiceAccountToken: false` (this pod runs model-authored code and must not hold a credential for the cluster's own API), and `enableServiceLinks: false` (a Service named `openai4s` injects `OPENAI4S_PORT=tcp://…`, which the daemon reads as `int(...)` — a crash, not clutter). The LLM credential arrives through the secret broker's environment-injection backend — `OPENAI4S_SECRET_STORE=env`, `OPENAI4S_SECRET_ENV=1`, and the derived `OPENAI4S_SECRET_LLM_LLM_API_KEY` read from an optional Kubernetes Secret — so nothing credential-shaped is ever written to the volume, and the UI refuses to overwrite what the Secret supplies. |
| `kubernetes-ingress.yaml` | Optional, and the piece most likely to be got wrong. Publishing the workbench needs five things a default Ingress does not guarantee — a WebSocket-survivable read timeout, a body limit large enough for real datasets, buffering off so a streamed reply is not held until the turn ends, `ssl-redirect` pinned on so the session cookie (which is not `Secure`) can never ride a plaintext listener a cluster-wide ConfigMap flip would open, and above all the original `Host` header preserved, because mutating API calls and the WebSocket upgrade are refused when `Origin` and `Host` disagree. Applying it publishes endpoints that execute arbitrary code, so it also carries the proxy-authentication annotations commented out rather than absent. |

## Before you expose it

A wildcard bind necessarily disables the daemon's Host-header rebinding
allowlist, which leaves the access token as the only control in front of
`kernel/execute` and the rest of the code-execution surface. The manifests
therefore stop at a ClusterIP: reach the workbench with `kubectl port-forward`,
or put an authenticating TLS proxy in front of it. The reasoning, and what the
container boundary does and does not replace, is in
[docs/security.md](../docs/security.md).
