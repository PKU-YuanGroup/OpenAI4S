# Security layers

[中文说明](README_zh.md)

Six of the layers around Code-as-Action live in this package: code classification, kernel OS confinement, the CPython audit hook, the shell precheck, prompt-injection annotation, and biosecurity screening. The other layers sit elsewhere on purpose. Child-environment filtering is in [`kernel/environment.py`](../kernel/environment.py), Host permissions and durable approval in [`host_dispatch.py`](../host_dispatch.py) and [`storage/permissions.py`](../storage/permissions.py), the shell capability itself in [`host/bash.py`](../host/bash.py) and [`sdk/bash.py`](../sdk/bash.py), application egress in [`egress.py`](../egress.py). There is no single boundary to point at, and that is the design: each control catches something different and fails in its own way, so none of them is the one that has to hold.

## Where this fits

- Before a Python/R Cell executes, the outer runtime can call [`classifier.py`](classifier.py). A refused Cell becomes an observation instead of reaching the worker.
- [`kernel/manager.py`](../kernel/manager.py) asks [`sandbox.py`](sandbox.py) to wrap the worker process, and publishes the sandbox status it actually measured.
- [`kernel/worker.py`](../kernel/worker.py) installs [`audit_hook.py`](audit_hook.py) inside CPython. `host.bash` runs [`shellcheck.py`](shellcheck.py) before capability-authorized execution.
- Tool/MCP/web output can be passed through [`injection.py`](injection.py), which annotates suspected instructions as untrusted data.
- [`biosecurity.py`](biosecurity.py) contributes calibrated prompt policy and an optional trajectory verdict. Host permissions and durable human approval live outside this directory, in [`HostDispatcher`](../host_dispatch.py) and [`storage/permissions.py`](../storage/permissions.py).

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Documents the layered model and re-exports the code-classification, injection, and biosecurity verdict APIs. |
| [`audit_hook.py`](audit_hook.py) | Installs a CPython audit hook inside the worker: a `ctypes.dlopen` of a shared library under a writable workspace, scratch, or Artifact root is refused, while loads from the interpreter and package prefixes still go through. It captures the functions it depends on as def-time keyword defaults and drops every Python-level handle to itself once installed, so rebinding names in the module namespace from a Cell does not disarm the check. That is resistance, not immunity: the guard is meant to be hard to defeat from inside a Cell, not impossible. |
| [`biosecurity.py`](biosecurity.py) | Holds the calibrated-accountability prompt and the trajectory screener. A cheap relevance trigger decides whether a screen is worth a model call at all; the call itself is independent, returns ALLOW, ESCALATE, or BLOCK, and is parsed loosely enough to survive a sloppy answer. |
| [`classifier.py`](classifier.py) | Classifies one Cell in three tiers: a fast path for code that touches no risk token, high-confidence static attack signatures, and, in `llm` mode, a model review of whatever is left. It returns structured SAFE/UNSAFE evidence and never raises. |
| [`injection.py`](injection.py) | Scans untrusted tool-returned text with static markers and, optionally, an LLM. Suspected content gets a warning prefix and keeps its payload; nothing is deleted. |
| [`sandbox.py`](sandbox.py) | Wraps the worker command in Seatbelt (macOS) or bubblewrap (Linux). Builds the workspace, private-temp, secret-read and network policy, proves it with a real deny/allow self-test, and reports back a status whose `state` is `enabled`, `disabled`, or `unavailable`. |
| [`shellcheck.py`](shellcheck.py) | A small static blocklist that runs before kernel-local `host.bash`. It catches a handful of unambiguously catastrophic literal commands and nothing subtler; parsing shell properly is not something it attempts, by design. |

## Enforcement and failure matrix

| Layer | What happens on a positive finding | Important failure behavior |
| --- | --- | --- |
| Code classifier | A static or LLM `UNSAFE` keeps that Cell from executing. | `off` disables it. Classifier exceptions and an unconfigured LLM fail open, while an unparseable response from an LLM that was actually invoked errs `UNSAFE`. Heuristic mode allows code that carries a risk token but matches no static attack signature. |
| OS sandbox | An enforced backend confines writes to the workspace and a private temp dir, masks reads of secret files, and blocks raw network unless it is explicitly allowed. | `enforce` fails closed. `auto` logs and returns a visible `state="unavailable"`, but it may still run the worker without OS confinement if detection or the self-test fails. |
| CPython audit hook | Refuses covered writable-path `ctypes.dlopen` events. | It is Python- and event-specific, not a general native-code or R sandbox, and the trusted-prefix policy has to stay correct for it to mean anything. |
| Shell precheck | Rejects a few unambiguous destructive command strings. | It is regex-based, fails open on its own errors, and is explicitly not an obfuscation-resistant sandbox. Host capability and OS confinement remain necessary. |
| Injection scan | Adds a warning banner to the content the model reads. | It does not drop or block content. After the static scan, errors, missing model configuration, and unparseable LLM output all fail open. |
| Biosecurity screen | Returns ALLOW, ESCALATE, or BLOCK for the caller's policy to act on. | It runs only after a keyword trigger and fails open to ALLOW when unavailable. A verdict alone is not execution isolation. |

## Operational cautions

- Never infer from configuration that a worker is sandboxed; read the runtime's measured `SandboxStatus` and its warning. A successful self-test is a statement about one backend and one policy, not a claim of complete containment.
- The sandbox's raw-network rule and Host/application egress policy are distinct. Allowing one does not authorize the other.
- Environment filtering in [`kernel/environment.py`](../kernel/environment.py) is an additional secret boundary. Neither name-based filtering nor output redaction can recognize every possible representation of a secret.
- This project is a local/trusted-user workbench, not a hardened public multi-tenant execution service. Binding the daemon publicly requires an external authentication and isolation design.

## Related documentation

- [Security model](../../docs/security.md)
- [System architecture](../../docs/architecture.md)
- [Configuration](../../docs/configuration.md)
