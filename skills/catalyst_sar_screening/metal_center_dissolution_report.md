# SAC Dissolution Potential Report (demo shell)

## Computation model

- Calculator: **`uma`** (only allowed mode)
- MLIP model: **`uma-s-1p1`**
- FAIRChem task: **`oc20`**
- Protocol id: `catalyst-design-agent/uma-s-1p1+oc20`
- Reference: Catalyst-Design-Agent CalculationTools (FAIRChem UMA / OC20)
- Runtime env: `catagent`

## Summary

- Structures evaluated: **3**
- Converged: **0**
- Mode: **dissolution**
- Passing filters: **0**

## Key insights

- Synthetic demo fixture only — not experimental results.
- Live runs must call run_pipeline into a fresh workdir and present only that run's deliverables.
- Example prompt metals: Mn-N4, Fe-N4, Cu-N4 on graphene (pyridineN).
- Synthetic developer demo only — not experimental results. Figures and metrics for a real request are written under the run workdir deliverables, never as metal_center_dissolution_* demo files.

## Figures


## Structure renders


## Ranked candidates

| Rank | Name | Metal | Source | OP (V) | U_diss (V) | Pass |
|---:|---|---|---|---:|---:|:---:|
| 1 | `Mn-N4` | Mn | catalog | n/a | n/a | no |
| 2 | `Fe-N4` | Fe | catalog | n/a | n/a | no |
| 3 | `Cu-N4` | Cu | catalog | n/a | n/a | no |

## Methods note

Structures come from the embedded POSCAR texts in `contcar_catalog.json` (exact lookup, else derived by metal/coordination edits). Energies follow Catalyst-Design-Agent FAIRChem UMA (`uma-s-1p1`, task `oc20`): `U_diss = E°_red − E_bind / n_e`; ORR overpotential from *O/*OH/*OOH Gibbs bindings with 4.92 eV / 1.23 V references.
