# Boltz Skill

This directory contains the progressive-disclosure recipe for Boltz-2 protein/nucleic-acid/ligand co-folding and optional affinity output. It helps the agent choose inputs, run the external package, and interpret mmCIF and confidence artifacts; it is not a Boltz implementation.

The package, weights, GPU, and any optional MSA service must be available separately. Affinity/confidence outputs are model predictions and do not establish experimental binding or biological validity.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Recipe for Boltz YAML entities, CLI flags, MSA choices, output files, confidence/affinity interpretation, batching, comparison with other co-fold models, caveats, and licensing. |

## Direct subdirectories

None.
