# Harness smoke checks

[中文](README_zh.md)

This directory contains small, explicitly opt-in checks that cross a real runtime or platform boundary. They are not imported by the offline core and are not part of default pytest collection.

## Direct files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Marks the opt-in smoke package; contains no automatic execution. |
| [`macos_sandbox.py`](macos_sandbox.py) | Fail-closed Darwin/Seatbelt smoke: requires enforced sandboxing, proves outside-workspace writes and network are blocked, proves workspace writes work, and verifies secrets are absent from worker subprocesses. |
| [`.gitkeep`](.gitkeep) | Keeps the smoke extension directory present. |

## Direct subdirectories

None.

Run the macOS check only on Darwin in its scheduled/explicit environment; it intentionally raises on unsupported or degraded sandbox state. See the root [Harness rules](../README.md#ground-rules).
