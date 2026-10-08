# Lab contracts

[中文说明](README_zh.md)

OpenAI4S's internal simulation contract follows public MHS design ideas; it
neither implements nor claims compatibility with an official MHS API.
No device is registered and no process is started at import time.

## Files

| File | Purpose |
| --- | --- |
| `__init__.py` | Public contract exports. |
| `models.py` | Frozen values, strict JSON decoding, state machines and hashes. |
| `ports.py` | Device, ledger and manager protocols. |
| `manifest.py` | Descriptor validation, unit matching and public projections. |
| `devices.py` | Thread-safe explicit device registration. |
| `fake.py` | Deterministic in-process toy device and fault injection. |
| `manager.py` | Process-owned admission, dispatch, reconciliation and lifecycle. |
| `policy.py` | Pure admission and budget decisions. |
| `reconcile.py` | Startup reconciliation without provider replay. |
| `runtime.py` | Manager composition and startup reconciliation. |
| `README.md` | English directory guide. |
| `README_zh.md` | Chinese directory guide. |
