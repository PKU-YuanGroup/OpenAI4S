---
title: Documentation policy
description: Truth hierarchy, status vocabulary, language policy, and review gates for OpenAI4S documentation.
status: current
audience:
  - contributors
  - operators
verified_commit: a92e736
last_verified: 2026-07-14
owner: OpenAI4S maintainers
---

# Documentation policy

OpenAI4S documentation is an engineering interface. It must describe what the
current code guarantees, what it merely attempts, and what is not wired yet.

## Truth hierarchy

When sources disagree, use this order:

1. executable protocol and persistence behavior;
2. focused tests and contract tests;
3. composition code and public schemas;
4. current canonical documentation;
5. historical plans, comments, and marketing copy.

A class, route, or repository is not sufficient evidence that a feature is
available end to end. Product availability also requires composition, UI or
client wiring where applicable, and a successful validation path.

## Status vocabulary

| Label | Meaning |
|---|---|
| **Contract** | A stable invariant that callers may rely on. Changing it requires migration and contract-test review. |
| **Implemented** | Composed into the supported product path and covered by relevant tests. |
| **Best-effort** | Useful behavior with documented coverage gaps, degradation, or non-transactional failure modes. |
| **Partial** | Some layers or controls are available, but the end-to-end capability has explicit limits. |
| **Prototype** | Experimental integration that must not be presented as an operational guarantee. |
| **Planned** | Target behavior with no claim of current availability. Internal roadmaps are not published on this site. |
| **Historical** | A preserved decision or migration record that is not current product truth. |

## Verification metadata

Canonical pages carry:

- `status`
- `audience`
- `verified_commit`
- `last_verified`
- `owner`

The website root describes `main`. Stable release documentation will be
published under versioned paths once the project has stable release tags.

## Language policy

English is the canonical source. Every current page in the published
navigation has a Simplified Chinese counterpart under `/zh/`. A content change
is incomplete until both paths are updated or the pull request explicitly
marks and tracks a temporary translation gap.

Historical source records may remain in their original language, but their
current status and relevance must have bilingual summaries.

## Public and private content

Public documentation includes architecture, limitations, failure modes,
security boundaries, and implementation maturity. It excludes credentials,
host-specific access data, private backup locations, incident contacts,
internal machine aliases, and internal roadmaps.

## Pull-request gate

Documentation changes must:

1. build the English and Chinese site;
2. preserve existing public paths or provide an intentional redirect/stub;
3. validate internal links and navigation targets;
4. render Mermaid diagrams without client errors;
5. smoke-test English and Chinese search terms;
6. avoid hand-maintained dynamic counts when an inventory can be generated;
7. update status tables when behavior moves between Prototype, Partial, and
   Implemented;
8. pass the repository secret scan.

Historical plans belong under a clearly labeled History/ADR surface and must
not use unqualified “current” wording.
