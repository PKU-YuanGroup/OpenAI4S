# Code-adjacent documentation

[中文说明](README_zh.md)

The documentation that travels with the OpenAI4S source lives here, along with
the historical paths older references still link to. The public bilingual
website is maintained separately in
[`Nobody-Zhang/openai4s-docs`](https://github.com/Nobody-Zhang/openai4s-docs),
and the internal plans in this directory are not published by that site.

## Files

| File | Purpose and status |
| --- | --- |
| `architecture.md` | Current dual-loop architecture and Host API overview; compatibility entry for contributors. |
| `ark-agent-plan-9.9.png` | Volcengine Ark Agent Plan pricing screenshot displayed by the repository's root README. |
| `backend-extension-guide.md` | Current extension seams: where a new tool, Host service, storage repository, provider, Skill, or Web session service is meant to plug in. |
| `backend-refactor-architecture.md` | Historical backend-refactor design record. It says what was agreed, so it is not proof of current end-to-end behavior. |
| `compute.md` | Remote compute, the BYOC providers, and `host.fold`: how they behave and where their limits are. |
| `configuration.md` | How the provider, environment, daemon, kernel, and data directory are configured. |
| `jupyter.md` | The optional Jupyter adapter: what it exposes, the execution boundaries it keeps, and the compatibility notes. |
| `package-architecture.md` | Historical package/ownership inventory used during decomposition work. |
| `plan-corecoder-refactor.md` | Internal historical refactor plan; excluded from public website content. |
| `refactor-plan.md` | Historical migration plan retained for decision context. |
| `release-validation.md` | The gates a release passes: offline CI, package artifacts, import smoke, and the external gates that deliberately stay outside CI. |
| `security.md` | Threat model, trust boundaries, enforcement layers, and known coverage gaps. |
| `skills.md` | Bundled/user Skill format, loading, sidecars, and lifecycle. |
| `webapp-api.md` | Detailed REST/WebSocket surface and compatibility behavior. |
| `webapp.md` | Web workbench concepts, projections, status, and operator-facing behavior. |

## Where this fits

Executable behavior and tests outrank prose. When a historical plan conflicts
with `openai4s/` or `tests/`, treat the implementation and contract tests as the
current source of truth and update the standalone documentation repository.
