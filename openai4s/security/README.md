# Security layers

[中文说明](README_zh.md)

This package supplies defense-in-depth components around Code-as-Action. It does not define one monolithic security boundary: code classification, kernel OS confinement, child-environment filtering, CPython audit hooks, Host permissions/approvals, shell capabilities, application egress, prompt-injection annotation, and biosecurity screening are separate controls with different enforcement and failure behavior.

## Place in the architecture

- Before a Python/R Cell executes, the outer runtime can call [`classifier.py`](classifier.py). A refused Cell becomes an observation instead of reaching the worker.
- [`kernel/manager.py`](../kernel/manager.py) asks [`sandbox.py`](sandbox.py) to wrap the worker process and publishes the measured sandbox status.
- [`kernel/worker.py`](../kernel/worker.py) installs [`audit_hook.py`](audit_hook.py) inside CPython; `host.bash` also applies [`shellcheck.py`](shellcheck.py) before capability-authorized execution.
- Tool/MCP/web output can be passed through [`injection.py`](injection.py), which annotates suspected instructions as untrusted data.
- [`biosecurity.py`](biosecurity.py) contributes calibrated prompt policy and an optional trajectory verdict. Host permissions and durable human approval live outside this directory in [`HostDispatcher`](../host_dispatch.py) and [`storage/permissions.py`](../storage/permissions.py).

## Files

| File | Responsibility |
|---|---|
| [`__init__.py`](__init__.py) | Documents the layered model and re-exports code-classification, injection, and biosecurity verdict APIs. |
| [`audit_hook.py`](audit_hook.py) | Installs an in-process CPython audit hook that blocks `ctypes.dlopen` from writable workspace/scratch/Artifact roots while allowing interpreter and package prefixes; it captures dependencies to resist later monkeypatching. |
| [`biosecurity.py`](biosecurity.py) | Defines the calibrated-accountability prompt, cheap relevance trigger, independent ALLOW/ESCALATE/BLOCK trajectory-screening call, and robust verdict parsing. |
| [`classifier.py`](classifier.py) | Classifies a Cell through fast-path safe tokens, high-confidence static attack signatures, and optionally an LLM review of residual risky code; returns structured SAFE/UNSAFE evidence without raising. |
| [`injection.py`](injection.py) | Scans untrusted tool-returned text with static markers and an optional LLM, then prepends a warning to suspected content rather than deleting it. |
| [`sandbox.py`](sandbox.py) | Detects Seatbelt (macOS) or bubblewrap (Linux), constructs workspace/private-temp/read-denial/network policy, runs a real deny/allow self-test, wraps worker commands, and reports enforced, disabled, or unavailable status. |
| [`shellcheck.py`](shellcheck.py) | Performs a small static best-effort blocklist check for clearly catastrophic literal shell commands before kernel-local `host.bash`; it intentionally does not attempt complete shell parsing. |

## Subdirectories

There are no tracked child directories in this package.

## Enforcement and failure matrix

| Layer | What happens on a positive finding | Important failure behavior |
|---|---|---|
| Code classifier | Static or LLM `UNSAFE` prevents that Cell from executing. | `off` disables it; classifier exceptions or an unconfigured LLM fail open, while an unparseable response from an invoked LLM errs `UNSAFE`. Heuristic mode allows residual risk-token code not matching a static attack signature. |
| OS sandbox | Enforced backend confines writes to workspace/private temp, applies secret read masks, and blocks raw network unless explicitly allowed. | `enforce` fails closed. `auto` logs and returns visible `state="unavailable"` but may run the worker without OS confinement if detection or self-test fails. |
| CPython audit hook | Refuses covered writable-path `ctypes.dlopen` events. | It is Python/event-specific, not a general native-code or R sandbox, and trusted-prefix policy must remain correct. |
| Shell precheck | Rejects a few unambiguous destructive command strings. | It is regex-based, fail-open on its own errors, and explicitly not an obfuscation-resistant sandbox. Host capability and OS confinement remain necessary. |
| Injection scan | Adds a warning banner to content for the model. | It does not drop or block content; errors, missing model configuration, and unparseable LLM output fail open after the static scan. |
| Biosecurity screen | Returns ALLOW, ESCALATE, or BLOCK for caller policy. | It runs only after a keyword trigger and fails open to ALLOW when unavailable; a verdict alone is not execution isolation. |

## Operational cautions

- Never infer that a worker is sandboxed from configuration alone; inspect the runtime's measured `SandboxStatus` and warning. A successful self-test is per backend/policy, not a claim of complete containment.
- The sandbox's raw-network rule and Host/application egress policy are distinct. Allowing one does not authorize the other.
- Environment filtering in [`kernel/environment.py`](../kernel/environment.py) is an additional secret boundary. Neither name-based filtering nor output redaction can recognize every possible secret representation.
- This project is a local/trusted-user workbench, not a hardened public multi-tenant execution service. Binding the daemon publicly requires an external authentication and isolation design.

## Related documentation

- [Security model](../../docs/security.md)
- [System architecture](../../docs/architecture.md)
- [Configuration](../../docs/configuration.md)
