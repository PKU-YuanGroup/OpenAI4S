# Model-backed retrosynthesis task map

[中文说明](MODEL_TASKS_zh.md)

This document records the model audit behind the retrosynthesis Skills. It
separates scientific tasks by their input, output, and independently testable
hypothesis. Route rendering, de-duplication, provenance, and report generation
remain important engineering functions, but they are not counted as scientific
model tasks.

## Admission rule

A default model must have all of the following:

1. public inference code;
2. obtainable pretrained weights;
3. an explicit code and weight license;
4. a scriptable local inference path;
5. enough task and dataset disclosure to state where its score is meaningful.

Paper accuracy alone is not an admission criterion. A model is also rejected as
a default when its public artifact only supports training, silently requires a
commercial service, or cannot identify the checkpoint that produced a result.

## Selected tasks and models

| Independent task | Input | Output | Selected implementation | Engineering verdict |
| --- | --- | --- | --- | --- |
| Atom mapping and reaction-centre extraction | complete reactant/product reaction SMILES | atom-mapped reaction, mapping confidence, changed bonds | **RXNMapper** | Mature local package, MIT, pretrained model, CPU/GPU. It cannot map a target-only query because the product and reactant sides must both be known. |
| Single-step precursor generation | one product SMILES | ranked precursor sets for one disconnection | **RetroChimera 1** | Preferred proposal model: MIT code and checkpoints, public Pistachio/USPTO checkpoints, direct Python/Syntheseus API. Keep only 5–10 candidates and never interpret its probability as experimental success. |
| Multi-step route search | target SMILES, expansion policy, stock | solved/unsolved route trees | **AiZynthFinder** | Mature MIT planner with downloadable public policies and stock. The code license does not by itself settle the separately downloaded artifact terms. This is search guided by learned expansion/filter policies, not one end-to-end neural model. |
| Forward outcome and round-trip validation | reactants plus reagents | ranked product SMILES | **ReactionT5v2-forward** | 2025 peer-reviewed model, MIT 0.2B safetensors on Hugging Face, direct Transformers inference. RetroChimera's released forward checkpoint is a useful same-dataset alternative, not independent evidence. |
| Reaction-condition recommendation | full reaction SMILES | ranked catalyst/reagent/solvent classes and, for the appropriate checkpoint, temperature | **Parrot (conditional candidate; not a default)** | MIT code, an official checkpoint downloader, and a CPU-capable CLI. The external checkpoint archives have no separately declared machine-readable license, so the exact checkpoint's terms must be reviewed and an explicit allow/deny decision recorded in the model manifest before any checkpoint download or inference. Outputs are dataset label classes; temperature support and vocabulary depend on the checkpoint. |
| Reaction-yield estimation | reactants, reagents, and product | predicted isolated yield percentage | **ReactionT5v2-yield (quarantined release)** | The 2025 MIT checkpoint loads locally, but the pinned release failed its published canary after upstream preprocessing was reproduced (about 19.1666 expected, 65.924858 observed). Keep it protocol-only until independently resolved and validated. |

These six tasks form a useful model-backed planning and review surface, but
Parrot remains a conditional candidate. Do not download or run a Parrot
checkpoint unless its terms have been reviewed and the model manifest records an
allow decision; a missing or deny decision requires refusal. These tasks do
**not** make a synthesis experimentally complete. Procurement, EHS review,
scale-up, work-up, purification, analytical release, and literature/ELN
verification still require databases, rules, experiments, and chemists.

## Why these are independent scientific questions

- Atom mapping asks for correspondence between atoms in an already specified
  reaction.
- Single-step retrosynthesis asks which precursor set could produce one target.
- Multi-step planning asks whether repeated disconnections reach the declared
  stock under a bounded search objective.
- Forward prediction asks which product follows from a proposed reaction input.
- Condition recommendation asks which experimental context is plausible for a
  fixed transformation.
- Yield estimation asks how much desired product may be isolated for a fully
  specified reaction context.

The tasks can therefore be trained and evaluated separately. Their outputs also
must not be collapsed into one confidence number: mapping confidence, beam
likelihood, search score, product rank, condition top-k accuracy, and yield
regression error have different meanings.

## Models retained as alternatives, not defaults

- **ReactionT5v2-retrosynthesis** is easy to deploy and valuable as a
  sequence-model diversity check. The ORD-pretrained checkpoint's zero-shot
  USPTO-50K result is much weaker than its task-fine-tuned result, so checkpoint
  identity is mandatory and it is not the primary single-step model.
- **RXN-Sandbox** is an unusually convenient 2026 container bundle for forward,
  single-step, and tree inference with 2025Q2 Pistachio-trained weights. It is
  promising, but the public repository is very new and uses OpenMDW-1.1 rather
  than the simpler permissive licenses used by the defaults. Treat it as a
  deployment candidate pending local regression tests.
- **Chemformer** remains scientifically useful, but its original repository was
  archived in 2026 and replaced by `aizynthmodels`; new deployments should not
  start from the archived Python 3.7 stack.
- **Yield-BERT** is reproducible for its reported HTE datasets, but its official
  environment starts at Python 3.6 and its own documentation warns that patent
  yield data is noisy. ReactionT5v2 has the more maintainable runtime, but the
  pinned release cannot become the default until its canary discrepancy is
  resolved.

## Primary sources checked

- RetroChimera code, inference API, released checkpoints, and warning:
  <https://github.com/microsoft/retrochimera>
- AiZynthFinder installation, learned policies, public data, and license:
  <https://github.com/MolecularAI/aizynthfinder>
- ReactionT5v2 code and task CLIs:
  <https://github.com/sagawatatsuya/ReactionT5v2>
- ReactionT5v2 forward and yield model cards:
  <https://huggingface.co/sagawa/ReactionT5v2-forward> and
  <https://huggingface.co/sagawa/ReactionT5v2-yield>
- RXNMapper package, API, confidence output, and license:
  <https://github.com/rxn4chemistry/rxnmapper>
- Parrot code license, checkpoint downloader, and CPU/GPU CLI:
  <https://github.com/wangxr0526/Parrot>
- RXN-Sandbox container and model license:
  <https://github.com/rxn4chemistry/rxn-sandbox>

Sources were last rechecked on 2026-08-19. Pin repository revisions and model
artifact hashes in any regulated or long-lived deployment; a moving model name
is not provenance.
