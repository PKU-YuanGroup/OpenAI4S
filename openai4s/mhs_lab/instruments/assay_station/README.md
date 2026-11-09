# openai4s/mhs_lab/instruments/assay_station

[中文说明](README_zh.md)

AS-96 enzyme-inhibition assay workstation (simulated): a 96-well plate, an 8-channel liquid handler and a kinetic absorbance reader. The model is competitive inhibition (`v0 = Vmax S / (Km (1 + (I/Ki)^h) + S)`), so a fitted IC50 follows Cheng-Prusoff. `load_sample` scans a LIMS vial and accepts it only after the xArm's own evidence shows it carried the vial from the reactor outlet to this deck.

## Files

| File | Responsibility |
| --- | --- |
| [`driver.py`](driver.py) | The OpenMHS driver: reads, actions with input/output schemas, limits citing MANUAL.md, e-stop, declared re-enable, fidelity contract. |
| [`sim.py`](sim.py) | The backend object: the analytic/kinetic model and its state. |
| [`MANUAL.md`](MANUAL.md) | The instrument's specification sheet; every limit in `driver.py` cites one of its lines. |
