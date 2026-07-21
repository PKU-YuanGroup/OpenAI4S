# Issue templates

[中文说明](README_zh.md)

These YAML forms route reports into reproducible engineering or scientific
work. They gather evidence, and nothing in them changes runtime state on its
own.

## Files

| File | Purpose |
| --- | --- |
| `bug_report.yml` | Reports a defect you can reproduce. Asks for the affected area, current versus expected behavior, the smallest public reproduction, version, environment, and logs, and requires both a severity and a public-disclosure acknowledgement. |
| `config.yml` | Not a form. It turns off blank issues and sends security reports to a private advisory and open-ended questions to Discussions. |
| `engineering_task.yml` | Tracks a bounded implementation, cleanup, test, or documentation task. Objective, scope, non-goals, acceptance criteria, verification plan, and the hotspots reviewers should look at hardest. |
| `feature_request.yml` | Proposes a capability: the problem behind it, the behavior or interface wanted, alternatives considered, how we would know it works, and a priority. |
| `science_engineering.yml` | Requests public engineering work on skills, harness plumbing, runtime, or examples. Asks for a minimal public example, the dependency and GPU impact, and a verification plan reviewers can run without private data or unreleased results. |

When changing a form, preserve public-data warnings: issue bodies can be
world-readable and must not solicit credentials or private research material.
