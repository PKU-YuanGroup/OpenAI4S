# Code-adjacent documentation

[简体中文](README_zh.md)

This directory keeps documentation that travels with the OpenAI4S source and
preserves historical compatibility links. The public bilingual website is
maintained separately in
[`Nobody-Zhang/openai4s-docs`](https://github.com/Nobody-Zhang/openai4s-docs).
Internal plans in this directory are not published by that site.

## Files

| File | Purpose and status |
|---|---|
| `architecture.md` | Current dual-loop architecture and Host API overview; compatibility entry for contributors. |
| `ark-agent-plan-9.9.png` | Volcengine Ark Agent Plan pricing screenshot displayed by the repository's root README. |
| `backend-extension-guide.md` | Current extension seams for tools, Host services, storage, providers, Skills, and Web session services. |
| `backend-refactor-architecture.md` | Historical backend-refactor design record; not proof of current end-to-end behavior. |
| `compute.md` | Remote-compute, BYOC provider, and `host.fold` behavior and limits. |
| `configuration.md` | Provider, environment, daemon, kernel, and data-directory configuration. |
| `jupyter.md` | Jupyter adapter behavior, execution boundaries, and compatibility notes. |
| `package-architecture.md` | Historical package/ownership inventory used during decomposition work. |
| `plan-corecoder-refactor.md` | Internal historical refactor plan; excluded from public website content. |
| `refactor-plan.md` | Historical migration plan retained for decision context. |
| `release-validation.md` | Offline CI, package artifact, import, and external release gates. |
| `security.md` | Threat model, trust boundaries, enforcement layers, and known coverage gaps. |
| `skills.md` | Bundled/user Skill format, loading, sidecars, and lifecycle. |
| `webapp-api.md` | Detailed REST/WebSocket surface and compatibility behavior. |
| `webapp.md` | Web workbench concepts, projections, status, and operator-facing behavior. |

## Framework relationship

Executable behavior and tests outrank prose. When a historical plan conflicts
with `openai4s/` or `tests/`, treat the implementation and contract tests as the
current source of truth and update the standalone documentation repository.
