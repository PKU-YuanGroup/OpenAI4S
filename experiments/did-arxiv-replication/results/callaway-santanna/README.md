# Callaway–Sant’Anna example

[中文说明](README_zh.md)

500 counties × five annual periods. The main numeric screen uses three-period windows and cannot screen this short panel; two-period sensitivity results are retained separately.

## Files

| File | Purpose |
| --- | --- |
| `candidate_events.csv` | Known dates, exposure transitions and exploratory numeric candidates. |
| `dynamic_att.csv` | C&S dynamic aggregates and changing cohort support. |
| `event_alignment.csv` | Candidate dates compared with independently recorded treatment dates. |
| `event_alignment.json` | Window eligibility and exact-date alignment counts; descriptive only. |
| `group_time_att.csv` | C&S group-time point estimates and pre-period pseudo-ATT. |
| `manifest.json` | Skill configuration, transformation provenance and exported-data hashes. |
| `metric-1.png` | Selected individual trajectories and event markers, PNG. |
| `metric-1.svg` | Selected individual trajectories and event markers, SVG. |
| `panel.csv` | Processed long panel, source-row positions and current exposure. |
| `sample-coverage.png` | Observed entities per period, using the entire panel, PNG. |
| `sample-coverage.svg` | Observed entities per period, using the entire panel, SVG. |
| `screening_sensitivity.json` | Complete two-/three-period screening specifications and candidates. |
| `summary.json` | Full-panel coverage, missingness and descriptive summaries. |
| `visualization.json` | Selected/excluded IDs, configuration and diagnostic-figure hashes. |
| `window-readiness.png` | Complete, unfilled-window readiness and variability diagnostics for the full sample, PNG. |
| `window-readiness.svg` | Vector version of the full-sample window-readiness figure. |
| `window_assessment.csv` | Coverage, exclusions and diagnostics for each entity, anchor and candidate window. |
| `window_assessment.json` | Complete window-audit configuration, summaries, anchor evidence and recommendations. |
