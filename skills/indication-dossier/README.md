# Indication Dossier Skill

Researching one therapeutic indication and writing it up, with the indication treated as a patient population rather than a disease entity: who these patients are, how many of them there are, what is going wrong biologically, how they are treated today, what regulators have accepted before, and which trials shaped the field. Some populations do not map onto a billable diagnosis at all, and saying so is part of the job, because it changes the regulatory path. This is a research and writing recipe. It gives no medical recommendation, and nothing it produces has been checked against a verified evidence database.

The agent does the retrieval itself, and it has to reach current authoritative sources, cite them precisely, leave uncertainty and disagreement between sources on the page instead of smoothing them over, and obey the anti-fabrication gates the reference phases impose. No sidecar and no live data source ships with this Skill.

## Files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Drives the run: five phases, each writing a waypoint file, with an identity check after Phase 1 and a resume path when the workdir already holds waypoints. It also fixes the inputs, the tools the Skill expects (and the fallback when an MCP is not connected), the output layout, and what the synthesis phase may still fetch rather than name as a gap. |

## Subdirectories

| Directory | Responsibility |
| --- | --- |
| [`references/`](references/) | Loaded on demand: the cross-phase research standards, one instruction file per phase, the writing style rules, and the JSON schemas for the waypoint files. |
