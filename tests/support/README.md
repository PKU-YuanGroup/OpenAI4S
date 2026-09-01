# Test runtime-prefix helpers

[中文说明](README_zh.md)

Shared fixtures for tests that observe environment binding through
`sys.executable`. A bare `bin/python -> sys.executable` symlink is not a
virtual environment: CPython 3.13 reports the symlink path and 3.14 reports
the resolved base binary, so provenance assertions that name the selected
interpreter fail on 3.14 only. A prefix with `pyvenv.cfg` is the real
selection mechanism and keeps those assertions strong on both versions.

## Files

| File | Responsibility |
| --- | --- |
| [`runtime_prefix.py`](runtime_prefix.py) | `install_runtime_prefix` and `install_named_runtime` create a real venv (`pyvenv.cfg` plus a symlink launcher) whose interpreter self-reports this prefix. |
