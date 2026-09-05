# Scenario queries

These frozen prompts are the inputs supplied to OpenAI4S when generating the
independent Scenario codebases in `../openai4s_codebases/`.  Filenames match the
GT and generated entry points exactly.  Each prompt defines the installed
public files, their fields, the required output, and the private paths that must
not be accessed.

## Files

| File | Purpose |
| --- | --- |
| `01_single_step_retrosynthesis.query.md` | Single-step retrosynthesis generation prompt. |
| `02_multistep_route_planning.query.md` | Budgeted multistep planning generation prompt. |
| `03_atom_mapping.query.md` | Atom-mapping generation prompt. |
| `04_forward_prediction.query.md` | Forward-prediction generation prompt. |
| `05_condition_recommendation.query.md` | Condition-recommendation generation prompt. |
| `06_yield_estimation.query.md` | Yield-estimation generation prompt. |
| `README.md` | English directory guide. |
| `README_zh.md` | Chinese directory guide. |
