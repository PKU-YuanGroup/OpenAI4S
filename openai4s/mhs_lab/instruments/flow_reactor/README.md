# openai4s/mhs_lab/instruments/flow_reactor

[中文说明](README_zh.md)

FR-10 continuous-flow reactor with inline HPLC (simulated): a 10 mL coil fed by two pumps. Four competing reactions (product formation, thermal degradation, hydrolysis, over-addition) integrated with RK4 give a time-temperature ridge in yield. `collect_product` fills a LIMS-labelled vial at the reactor outlet.

## Files

| File | Responsibility |
| --- | --- |
| [`driver.py`](driver.py) | The OpenMHS driver: reads, actions with input/output schemas, limits citing MANUAL.md, e-stop, declared re-enable, fidelity contract. |
| [`sim.py`](sim.py) | The backend object: the analytic/kinetic model and its state. |
| [`MANUAL.md`](MANUAL.md) | The instrument's specification sheet; every limit in `driver.py` cites one of its lines. |
