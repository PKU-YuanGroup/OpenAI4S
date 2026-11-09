# OpenMHS integration

[中文说明](openmhs_zh.md)

[OpenMHS](https://mhs.openai4s.org) is the open-source Model Hardware Standard. Its
OpenAI4S reference host connects scientific devices and simulators through
MCP. Enable the integration when you configure an endpoint.

## Quick start

```bash
git clone --recursive https://github.com/PKU-YuanGroup/OpenAI4S.git
cd OpenAI4S && ./setup.sh && source .venv/bin/activate
openai4s lab add labs/tiagocoutinho__julabo   # any id from https://mhs.openai4s.org/market/
openai4s serve                               # open the Lab tab beside the Notebook
```

`--recursive` fetches the `third_party/OpenMHS` submodule, pinned to the OpenMHS
release this version is tested with. OpenAI4S finds it on its own: no
`PYTHONPATH`, no settings file to write. `openai4s lab add` installs the device
on its fake backend, starts the lab (three built-in demo devices plus what you
added), registers the connectors. Agent commands follow the ordinary host permission policy.

Already cloned without `--recursive`? Run `git submodule update --init third_party/OpenMHS`.
Installed OpenAI4S with `pip`? `openai4s lab setup` downloads the same pinned
OpenMHS release into `~/.openai4s/openmhs` (or the Lab panel's **Set up OpenMHS**
button does it). In the workbench everything else is one click: Lab tab →
**Device market** → **Install**.

## The device market: 746 devices

The [OpenMHS market](https://mhs.openai4s.org/market/) lists every driver package
that passed its contract check on the fake backend: 299 hardware drivers (served
on fake backends, so no instrument is needed) and 447 simulations. Each entry has
an introduction, what it measures, adjusts and does, its upstream repository and
licence.

```bash
openai4s lab search microscope        # find devices
openai4s lab show julabo              # introduce one
openai4s lab add julabo               # install (MCP connector, the default)
openai4s lab add gym-pusht --via mhs  # install on the native MHS tools
openai4s lab list                     # what you installed
openai4s lab status                   # every device, its surface and state
openai4s lab remove julabo
openai4s lab up | down | reset        # start, stop, erase evidence (keeps devices)
```

"contract_checked on the fake backend" is not a hardware qualification. Driving
real hardware stays an explicit operator decision outside the lab.

## MHS or MCP: two agent surfaces

Every lab device is reached through one of two surfaces, and the Lab panel groups
the devices the same way:

| Surface | How the agent calls it | Wire | Good for |
| --- | --- | --- | --- |
| **MHS** (native) | `mhs_read`, `mhs_command`, `mhs_safety` tools; the MHSHost inside OpenAI4S | MHS JSON-RPC over HTTP to an endpoint the lab starts (ports from 7420) | the shortest path; one tool for every device |
| **MCP** (connector) | `call_mcp_tool(server="mhs-<id>", ...)`; one connector row per device | the MCP projection `mhs.mcp`, which spawns the driver over stdio (or reaches an HTTP endpoint) | any MCP host; the governed route's journal, host ledger and evidence |

Pick one per device with `--via mhs|mcp` or the market's selector. Both go
through the same endpoint, so limits, interlocks, the e-stop and the receipts are
identical. The built-in reactor uses MHS; the built-in assay workstation (HTTP)
and xArm (stdio) use MCP.

## Advanced: your own OpenMHS checkout

OpenAI4S looks for OpenMHS in `$OPENAI4S_OPENMHS`, then the
`third_party/OpenMHS` submodule, then `<data dir>/openmhs`, and adds its two
source packages (`MHS/sdk/src`, distribution `openmhs`, import `mhs`; and the
reference host `integration/openai4s/openai4s_mhs`) to `sys.path` and to the
`PYTHONPATH` of the processes it starts. A `PYTHONPATH` or an installed
distribution that already provides both packages wins. Set
`OPENAI4S_OPENMHS=off` to turn discovery off; the core then runs exactly as
without OpenMHS. Core OpenAI4S remains stdlib-only and runs without either
package.

```bash
export OPENAI4S_OPENMHS=/path/to/OpenMHS
python3 -c "import openai4s, openai4s_mhs, mhs; print('imports ready')"
python3 -m openai4s_mhs status
```

This tree already includes the core route and auto-install hook; do not reapply
OpenMHS's historical patches against an older OpenAI4S commit. See the upstream
[reference host guide](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/README.md)
and [design contract](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/DESIGN.md).

## Advanced: configure your own endpoints

Create `~/.openai4s/mhs.json` (or select a separate file with `MHS_CONFIG`).
Start with a simulated endpoint and leave hardware access off:

```json
{
  "enabled": false,
  "allow_hardware": false,
  "endpoints": [
    {"id": "lab", "url": "http://127.0.0.1:7401", "label": "simulated lab"}
  ]
}
```

In another terminal using the same Python environment and source paths,
start OpenMHS's simulated lab:

```bash
python3 -m mhs.reference lab --port 7401
```

Then explicitly enable the integration as the operator:

```bash
python3 -m openai4s_mhs mcp-config --json  # preview one disabled row per endpoint
python3 -m openai4s_mhs enable
python3 -m openai4s_mhs status
./start.sh
```

`enable` persists the switch and synchronizes enabled connector rows into
OpenAI4S's store. Use the same `OPENAI4S_DATA_DIR` for this CLI and the daemon
if you selected a custom data directory. The settings path is selected
separately by `MHS_CONFIG`. Restart an already running daemon with the source
paths above (or reload its connector settings) to activate the tools. Each
connector starts `mhs.mcp` with its settings file; generated rows declare the
SDK source path in their environment because MCP children do not inherit the
daemon's `PYTHONPATH`.

Disable with:

```bash
unset MHS_ENABLED
python3 -m openai4s_mhs disable
python3 -m openai4s_mhs status
```

Restart the daemon to remove any native developer tools already registered
in that process. `disable` persists `enabled: false` and disables the MCP
rows; it does not cancel a device operation already in flight. `MHS_ENABLED=1`
is a process-local override for development and takes precedence over the
persisted switch, so remove it from the daemon's environment when disabling.
Importing `openai4s` calls the optional host's idempotent `install()` hook;
it registers native `mhs_*` developer tools only while enabled. With the
feature off, it registers no tools and opens no endpoint connection.

## What the governed route enforces

- Ordinary capability calls remain approval-gated. The external reference
  host journals command intent before sending it, appends outcomes to its host
  ledger, and records verified endpoint evidence over the same MCP transport.
  UNKNOWN and in-flight outcomes require reconciliation before retrying.
  Tool discovery must succeed before an ordinary call can proceed; a discovery
  failure never falls back to an unjournaled command.
- Only an explicit, device-bound `mhs__estop` (or the Host action
  `mhs_emergency_stop`) skips the ordinary approval prompt. Connector
  enablement, persisted MCP deny rules, delegated child restrictions,
  endpoint/device binding and the reference host's safety policy still apply.
  The stop is audited as an
  external write; its returned content is screened like other MCP output.
- Resets and permits belong to the separate human operator route, never the
  agent connector. Hardware access is separately disabled by default. A
  software stop is not a safety-rated hardware e-stop, and simulated evidence
  is not hardware validation.
- An MHS connector whose reference host cannot be imported is refused on
  invocation rather than executing commands without its journal and ledger.
  Other connectors retain their normal MCP behavior.

The journal, host ledger and endpoint evidence implementation is maintained
in OpenMHS. OpenAI4S's tests cover the optional routing and permission boundary;
the OpenMHS integration suite covers its reference host end to end.

## Simulated lab and the Lab panel

`openai4s/mhs_lab` is the optional simulated laboratory built on this
integration, and the workbench shows it in the **Lab** dock tab beside the
Notebook. It always has three built-in devices, each reached through a different
OpenMHS access path, plus any market device you install:

| Device | Surface | Wire |
| --- | --- | --- |
| AS-96 enzyme-inhibition assay workstation (new simulated instrument) | MCP connector `mhs-assay`, governed host route | HTTP JSON-RPC endpoint |
| FR-10 continuous-flow reactor with inline HPLC (new simulated instrument) | MHS: native `mhs_*` tools (no MCP) | HTTP JSON-RPC endpoint |
| UFACTORY xArm 7 (OpenMHS registry driver adapted from xArm-Python-SDK, fake backend) | MCP connector `mhs-xarm`, governed host route | stdio driver process |
| any OpenMHS market package (`openai4s lab add <id>`) | MCP connector `mhs-<id>` or MHS native tools | stdio, or an HTTP endpoint from port 7420 |

Kernel cells stay sandboxed: device commands are control-plane tool calls and
cells do the analysis. `openai4s lab up` (or **Start lab** in the panel, or the
first `openai4s lab add`) serves the endpoints and writes, under
`<data dir>/mhs-lab/`: `mhs.json` (the native tools' settings, selected
automatically when you have no `~/.openai4s/mhs.json` of your own),
`mhs-connectors.json` (the `--config` of the connector rows), `devices.json`
(what you installed), `lab.json` and `LAB-CARD.md` (exact tool names,
arguments and limits for operator reference). The project's context contains only
fixed discovery guidance; live driver metadata is obtained through screened tool
results. Installing or starting the lab does not add global allow rules. Previous
preview-generated grants are removed on start, and commands use the normal host
permission policy. Child processes receive runtime paths and the lab directory,
not inherited provider keys. `openai4s lab reset` erases the lab's evidence,
latches and LIMS; `openai4s lab down` stops its endpoints. After a reboot, an
operator opening the panel can restore a lab that was left running; a team
member's status poll cannot restart it.


The Lab panel reads every endpoint's persisted evidence chain (`--state-dir`),
so receipts from every access path appear in one live stream, with charts
derived from receipt outputs and offline chain verification. Its install,
start/stop, e-stop and operator reset buttons are human actions served by
`/api/v1/mhs-lab/*` routes; no tool reaches them. A reset releases a latch only;
the device's declared re-enable action then restores motion.
