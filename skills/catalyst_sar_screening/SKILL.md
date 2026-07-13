---
name: catalyst_sar_screening
description: HARD-LOCKED Catalyst-Design-Agent FAIRChem UMA (uma-s-1p1, oc20) SAC SAR screening for dissolution potential / adsorption / overpotential. Always call run_pipeline with the user's metals/metrics into a fresh workdir and present ONLY that run's result["deliverables"]. FORBIDDEN to return committed demo shells (metal_center_dissolution_*) as user results. FORBIDDEN tabular/heuristic/other MLIPs. If HF_TOKEN or hub unreachable, STOP and ask (HF_TOKEN / HF_ENDPOINT). Keywords: MLFF, UMA, OC20, catagent, M–N4, dissolution, graphene.
origin: openai4s
category: chemistry
requirements: [gpu]
---
# Skill: catalyst SAR screening

SAC structure–activity screening with a fixed pipeline: build graphene M–N–C
POSCARs from the embedded catalog, evaluate metrics with FAIRChem UMA only,
analyze SAR trends, and write a lean visual report.

All skill files live flat under this directory (no `data/` or `examples/`
subfolders).

## HARD LOCK

- Energy engine: Catalyst-Design-Agent FAIRChem **UMA** `uma-s-1p1`, task `oc20`
- Runtime: conda env `catagent`
- **Forbidden:** tabular / heuristic / lookup / other MLIPs; skipping UMA when
  `HF_TOKEN` or Hugging Face is missing; returning committed demo shells
  (`metal_center_dissolution_*`) as the user answer

**When blocked:** ask the user for `HF_TOKEN` and/or `HF_ENDPOINT`
(e.g. `https://hf-mirror.com`). Do not substitute another method.

## Example user prompt

Use this as the canonical prompt shape (replace the token placeholder; **never
commit a real token**):

```text
请使用 catalyst_sar_screening 这个 skill，基于 UMA 模型评估载体为石墨烯的
M–N4 的电化学稳定性；电化学稳定性用溶解电位量化；金属中心搜索空间包括
Mn、Fe、Cu；分析金属中心与电化学稳定性的关系并生成可视化报告。
huggingface 的 API 使用 <HF_TOKEN_PLACEHOLDER>；
运行代码前 export HF_ENDPOINT=https://hf-mirror.com。
```

Mapped run:

```python
import os

os.environ["HF_TOKEN"] = "<HF_TOKEN_PLACEHOLDER>"  # from the user message only
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from catalyst_sar_screening.kernel import check_uma_readiness, run_pipeline

ready = check_uma_readiness()
if not ready["ok"]:
    raise SystemExit(ready["ask_user"])

result = run_pipeline(
    ["Mn-N4", "Fe-N4", "Cu-N4"],
    workdir="outputs/sac_mn_fe_cu_udiss",
    metrics=["dissolution"],
    min_dissolution=0.0,
)
# Present ONLY these paths to the user (never demo shells in this skill dir).
deliverables = result["deliverables"]
```

## Fixed pipeline

1. Map the user request → descriptions + metrics (example above →
   `["Mn-N4","Fe-N4","Cu-N4"]`, `metrics=["dissolution"]`).
2. Set `HF_TOKEN` / `HF_ENDPOINT` from the user if provided (do not hardcode).
3. `check_uma_readiness()`; if not ok → stop and ask.
4. `run_pipeline(...)` builds POSCARs from `contcar_catalog.json`
   (embedded POSCAR text; exact lookup first, else derive). Do not read an
   external `chem/` tree.
5. Evaluate with UMA only; analyze SAR; write report/figures under `workdir`.
6. Present **only** `result["deliverables"]`.

## Module layout

```text
skills/catalyst_sar_screening/
├── SKILL.md
├── kernel.py
├── contcar_catalog.json              # minimal graphene M–N4 slab POSCAR texts
├── build_example.py                  # regenerate demo shells
├── metal_center_dissolution_*.json   # synthetic demos — NOT user outputs
├── metal_center_dissolution_*.md
└── metal_center_dissolution_*.html
```

The catalog is intentionally limited to **graphene / pyridineN / slab** entries
for the public skill fixture. It is not an experimental dataset release.

## Import

```python
from catalyst_sar_screening.kernel import check_uma_readiness, run_pipeline
```

## Backend setup

Pinned runtime packages for conda env `catagent`:

- `fairchem-core==2.19.0`
- `pandas==3.0.2`
- `pymatgen==2026.3.23`

```bash
conda create -n catagent python=3.11 -y
conda activate catagent
python -m pip install ase numpy matplotlib pandas==3.0.2 pymatgen==2026.3.23
python -m pip install fairchem-core==2.19.0 fairchem-data-oc==1.0.2 torch
export HF_TOKEN=...        # ask user if unset — never commit real tokens
export HF_ENDPOINT=https://hf-mirror.com
```

## Deliverables

Only paths under the run `workdir` listed in `result["deliverables"]`:

```text
<workdir>/
├── catalyst_sar_report.md
├── catalyst_sar_dashboard.html
├── summary.json
└── figures/
    ├── fig01_*.png
    └── structures_collage.png
```

Do **not** return committed demo shells such as:

```text
skills/catalyst_sar_screening/metal_center_dissolution_*
```

## Developer demos

Synthetic placeholder HTML/Markdown/JSON live flat in this skill directory for
maintainers. They must not include unpublished numeric screening results or
figure PNGs. Regenerate text shells with:

```bash
uv run python skills/catalyst_sar_screening/build_example.py
```

## Analyst checklist

- ran `run_pipeline` for **this** request's metals/metrics
- every presented file is under that run's `deliverables`
- no committed `metal_center_dissolution_*` demo was attached
- report includes model + statistical figures + structure collage + SAR insights
- no real HF token was written into files or commits
