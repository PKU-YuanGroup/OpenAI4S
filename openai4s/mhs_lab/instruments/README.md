# openai4s/mhs_lab/instruments

[中文说明](README_zh.md)

Simulated instruments written as OpenMHS driver packages (`mhs.driver.Driver`), served with `python3 -m mhs serve <dir> --backend sim`. Each declares `mode = "simulated"`, a fidelity contract with its gaps, and limits that cite the instrument's `MANUAL.md`; the endpoint enforces them before the simulator runs. Hidden model constants never appear in a read, output or manifest.

## Directories

| Directory | Instrument |
| --- | --- |
| [`assay_station/`](assay_station/) | AS-96 enzyme-inhibition assay workstation: dose series, reaction start, kinetic read (competitive-inhibition Michaelis-Menten with seeded noise). |
| [`flow_reactor/`](flow_reactor/) | FR-10 continuous-flow reactor with inline HPLC (four-reaction Arrhenius kinetics, RK4) and LIMS product collection. |
