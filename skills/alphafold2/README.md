# AlphaFold2 Skill

This directory contains the progressive-disclosure recipe for AlphaFold2/AlphaFold2-Multimer through ColabFold. The loader initially exposes only the Skill name and summary; the agent reads [`SKILL.md`](SKILL.md) when the task calls for MSA-backed monomer or multimer folding and confidence/self-consistency review.

The directory does not bundle AlphaFold code, weights, an environment, or a running service. Execution depends on ColabFold, suitable compute, model parameters, and—when the public MSA path is selected—sending sequences to the external ColabFold MMseqs2 service. GPU metadata is a requirement signal, not proof that a GPU is available.

## Direct files

| File | Responsibility |
| --- | --- |
| [`SKILL.md`](SKILL.md) | Recipe for ColabFold input conventions, AF2/Multimer commands, output/confidence interpretation, design self-consistency, batching, caveats, external-service disclosure, and third-party licensing. |

## Direct subdirectories

None.
