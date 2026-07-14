---
layout: home
title: OpenAI4S Documentation
description: Architecture, contributor, and operations documentation for the OpenAI4S scientific research workbench.
status: current
audience:
  - contributors
  - operators
  - users
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
hero:
  name: OpenAI4S
  text: Architecture & Operations
  tagline: A code-verified guide to the hybrid control plane, persistent scientific runtimes, and the boundaries that keep them operable.
  actions:
    - theme: brand
      text: Read the architecture
      link: /architecture
    - theme: alt
      text: Deploy and operate
      link: /operations/
    - theme: alt
      text: Contribute code
      link: /contributing/codebase-map
features:
  - title: Architecture contracts
    details: Stable routing, completion, kernel protocol, ownership, and persistence rules—separated from best-effort implementation details.
  - title: Contributor map
    details: Find the owning module, extension seam, tests, and compatibility boundary before changing the system.
  - title: Operations first
    details: Deployment, data layout, backups, security posture, failure modes, and recovery are first-class documentation.
  - title: Honest status
    details: Implemented, partial, prototype, planned, and historical capabilities are labeled explicitly.
---

## What this site documents

OpenAI4S is a local-first, single-user scientific research workbench. It uses
provider-native JSON tools as an orchestration and permission control plane,
and persistent Python/R cells as its scientific execution plane. A Python cell
can synchronously call audited Host services while it is still running.

This site serves two equal audiences:

- contributors who need to change the engine without breaking protocol,
  persistence, security, or compatibility contracts; and
- operators who need to install, secure, back up, upgrade, diagnose, and
  recover a real deployment.

Product concepts and user guides are included, but they do not replace the
implementation status and failure-boundary documentation.

::: warning Deployment boundary
The OpenAI4S Workbench is not a public multi-tenant service. Keep the daemon on
loopback or a trusted private network. The documentation site at
`openai4s.org/docs/` is a separate static deployment.
:::

## How to read status labels

<span class="status contract">Contract</span> is a behavior other components
may rely on. <span class="status implemented">Implemented</span> is wired and
tested now. <span class="status best-effort">Best-effort</span> can be partial
or degrade safely. <span class="status prototype">Prototype</span> is not an
operational guarantee.

See [Documentation policy](./reference/documentation-policy.md) for the full
truth hierarchy and review rules.
