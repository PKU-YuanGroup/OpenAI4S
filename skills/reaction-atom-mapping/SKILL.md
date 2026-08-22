---
name: reaction-atom-mapping
description: Map atoms and changed bonds for a complete reaction with RXNMapper. Use for reactant/product correspondence and reaction-centre audits, not target-only retrosynthesis or feasibility.
license: MIT
origin: openai4s
metadata:
  third_party:
    - kind: model
      name: RXNMapper
      license: MIT
      terms_url: https://github.com/rxn4chemistry/rxnmapper/blob/main/LICENSE
---

# Reaction atom mapping

Answer one scientific question: for a reaction whose reactant and product sides
are already specified, which atoms correspond across the transformation?
Changed bonds derived from that mapping define a machine-readable reaction
centre. This task does not propose precursors and does not establish feasibility.

Use RXNMapper, an attention-guided ALBERT mapper distributed with a local Python
API and a mapping confidence value.

## Install and run

Install it in the optional chemistry environment:

```bash
conda create -n rxnmapper python=3.11 -y
conda run -n rxnmapper python -m pip install "rxnmapper[rdkit]==0.4.3"
```

Select that interpreter in its own OpenAI4S Python Cell. The switch is applied
before the following Cell, so do not combine this call with the import:

```python
host.env.use("rxnmapper")
```

After the switch succeeds, run the mapping code in a new Cell:

```python
from rxnmapper import BatchedMapper

mapper = BatchedMapper(batch_size=32)
reaction = "CCBr.OCC>>CCOCC"
record = next(mapper.map_reactions_with_info([reaction]))
print(record["mapped_rxn"], record["confidence"])
```

Outside a Web session, where `host.env.use(...)` is unavailable, save the same
code as a workspace script and execute it with `conda run -n rxnmapper python`.
Do not run the bare import in the original kernel after installing into another
environment.

Use `BatchedMapper` for campaigns because it handles invalid records without
aborting the whole batch. Keep the original reaction string alongside the
mapped result.

## Derive the reaction centre

Parse the mapped reactant and product sides with RDKit. Build bond dictionaries
keyed by sorted atom-map-number pairs, with bond type as the value. Report:

- bonds present only on the reactant side as broken;
- bonds present only on the product side as formed;
- pairs present on both sides with a changed bond order;
- mapped atoms missing from either side;
- unmapped atoms and duplicate map numbers.

Do not silently repair unbalanced reactions, strip reagents, or move molecules
between reaction fields. Those choices change the scientific object being
mapped and must be recorded as preprocessing.

## Acceptance checks

- Both `>>` sides are present and parseable.
- Atom-map numbers are unique within each side after mapping.
- Every changed bond references mapped atoms.
- Mapping confidence is preserved as the model's own score, not converted to a
  probability that the proposed reaction is correct.
- For evaluation, use atom-mapping and bond-change ground truth only after the
  predicted mapping is fixed.

## Output contract

Return original reaction SMILES, mapped reaction SMILES, mapper confidence,
formed/broken/order-changed bonds, parse warnings, unmapped atoms, model version,
and environment provenance.

## Failure modes

| Symptom | Action |
| --- | --- |
| input contains only a product | Stop and use `single-step-retrosynthesis`; atom mapping requires both sides. |
| mapper returns `>>` or an empty record | Mark the reaction invalid and preserve the original input. |
| confidence is low | Keep the mapping for review but do not use its reaction centre as an unquestioned label. |
| atom conservation fails | Report imbalance separately; do not force a cosmetically balanced mapping. |

Primary source: <https://github.com/rxn4chemistry/rxnmapper>.
