# Repository governance

[简体中文](README_zh.md)

This directory contains GitHub-facing policy and automation. It does not run
inside the OpenAI4S daemon, Agent Engine, or kernels; it protects changes before
they enter those runtime surfaces.

## Files

| File | Purpose |
|---|---|
| `CODEOWNERS` | Assigns review ownership, including security-sensitive and subsystem-specific paths. |
| `dependabot.yml` | Configures automated dependency-update proposals. |
| `pull_request_template.md` | Defines the public PR checklist, branch policy, validation evidence, and disclosure rules. |

## Subdirectories

| Directory | Purpose |
|---|---|
| `ISSUE_TEMPLATE/` | Structured issue forms and issue-creation policy. |
| `contributors/` | Committed circular contributor avatars used by the root READMEs. |
| `workflows/` | CI, release, contributor, scorecard, and secret-scanning automation. |

## Framework relationship

Changes to routing, persistence, kernel protocol, permissions, or sandboxing
must satisfy the checks defined here, but this directory is not itself a
security boundary. GitHub Actions validate source; runtime enforcement remains
in `openai4s/security/`, `openai4s/host/`, and the kernel manager.
