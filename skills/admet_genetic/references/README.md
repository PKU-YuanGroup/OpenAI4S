# ADMET Genetic References

Supplementary recipe material, loaded on demand (through `host.skills.read`, for example) so that the main Skill stays progressively disclosed. These files write down contracts and design choices. They install nothing, and they supply no experimental validation.

## Files

| File | Responsibility |
| --- | --- |
| [`admet.md`](admet.md) | How to install and invoke ADMET-AI, which direction each endpoint points in and how to aggregate them, what to expect at runtime, and what to do when it breaks. |
| [`data_contracts.md`](data_contracts.md) | The contracts the pipeline is held to: molecule identity, the columns every candidate and generation record must carry, the parent fields, the canonical `operation_detail` JSON, the lineage invariants, and what the visualization assumes about all of it. |
| [`ga.md`](ga.md) | A starter GA to build from: structure, mutation and crossover options, chemistry filters, SA scoring, the composite score, and how to select for diversity. |
