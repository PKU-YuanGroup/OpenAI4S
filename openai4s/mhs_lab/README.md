# openai4s/mhs_lab

[中文说明](README_zh.md)

Optional OpenMHS simulated lab: the instruments, the lab supervisor, the device market and the data the workbench's Lab panel shows. It needs OpenMHS (the SDK `mhs` and the reference host `openai4s_mhs`); [`openmhs.py`](openmhs.py) finds it in the `third_party/OpenMHS` submodule (or a pinned copy that `openai4s lab setup` downloads), so no `PYTHONPATH` is needed. The core imports only that finder (no network, no import of OpenMHS itself); `OPENAI4S_OPENMHS=off` turns it off. Nothing here controls hardware: the instruments are simulators and every other device runs on its fake backend.

The lab always has three built-in devices reached through three different OpenMHS access paths: the assay workstation through the MCP connector `mhs-assay` (HTTP endpoint), the flow reactor through the native `mhs_*` tools (MHS JSON-RPC over HTTP, no MCP) and the xArm 7 through the MCP connector `mhs-xarm` (a stdio driver process). Any of the 746 packages of the OpenMHS market can be added (`openai4s lab add <id>` or the Lab panel's market), each through either surface: **MHS** (native tools, an HTTP endpoint from port 7420) or **MCP** (a connector row whose projection spawns the driver over stdio). Kernel cells stay sandboxed; device commands are control-plane tool calls.

## Files

| File | Responsibility |
| --- | --- |
| [`__init__.py`](__init__.py) | Package description, `INSTRUMENTS_DIR`, `lab_dir()` (`OPENAI4S_MHS_LAB_DIR`, else `<data dir>/mhs-lab`). |
| [`openmhs.py`](openmhs.py) | Finds an OpenMHS checkout (`$OPENAI4S_OPENMHS`, the `third_party/OpenMHS` submodule, `<data dir>/openmhs`), puts its sources on `sys.path`/`PYTHONPATH`, selects the lab's `mhs.json` when the operator has none, and downloads the pinned release on request. |
| [`supervisor.py`](supervisor.py) | Starts and stops the endpoints (`--state-dir`), installs and removes market devices (`devices.json`), writes the two settings documents (`mhs.json` for the native tools, `mhs-connectors.json` for the connector rows), enables the rows, removes legacy automatic grants and writes the lab card. |
| [`cli.py`](cli.py) | `openai4s lab add\|remove\|search\|show\|list\|status\|up\|down\|reset\|setup\|card`. |
| [`labcard.py`](labcard.py) | The lab card: exact tool and capability names, arguments, units and limits read from the live connectors and manifests, including installed market devices; operator reference; live metadata is never promoted into project instructions. |
| [`activity.py`](activity.py) | The Lab panel's data: endpoint status, every receipt from the persisted evidence chains, chart data, offline chain verification, operator e-stop and reset. |
| [`lims.py`](lims.py) | A file-locked sample registry shared by the reactor (labels vials) and the assay workstation (scans them). |
| [`instruments/`](instruments/) | The simulated instruments, one `mhs.driver` package each. |

`runtime.py` serializes mutations across threads and processes and builds the least-privilege child environment. Commands use the host permission broker.
