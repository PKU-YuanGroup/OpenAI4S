# OpenMHS integration

[中文说明](openmhs_zh.md)

[OpenMHS](https://mhs.openai4s.org) is the open-source Model Hardware Standard. Its
OpenAI4S reference host connects scientific devices and simulators through
MCP. Enable the integration when you configure an endpoint.

OpenAI4S contains the optional routing hooks, not the OpenMHS SDK or reference
host. Those remain in the OpenMHS repository: `MHS/sdk` (distribution
`openmhs`, import `mhs`) and `integration/openai4s/openai4s_mhs` (reference
host). Core OpenAI4S remains stdlib-only and runs without either package.

## Install from the OpenMHS source tree

Use Python 3.10+ and the same interpreter/environment that runs OpenAI4S.
From your OpenAI4S checkout, obtain a separate OpenMHS checkout and expose
both of its source packages:

```bash
git clone https://github.com/PKU-YuanGroup/OpenMHS.git ../OpenMHS
export OPENMHS_CHECKOUT="$(cd ../OpenMHS && pwd)"
export PYTHONPATH="$OPENMHS_CHECKOUT/MHS/sdk/src:$OPENMHS_CHECKOUT/integration/openai4s:$PWD${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "import openai4s, openai4s_mhs, mhs; print('imports ready')"
python3 -m openai4s_mhs status
```

This source-tree installation needs no third-party packages. Keep the same
`PYTHONPATH` when launching the OpenAI4S daemon; making the packages visible
only to the settings CLI does not make them visible to a running daemon.
The reference host currently runs from `integration/openai4s`; do not copy it
into the OpenAI4S package.

If you prefer to install the SDK in your chosen environment, OpenMHS documents
these commands from its repository root:

```bash
python3 MHS/sdk/tools/sync_data.py
python3 -m pip install ./MHS/sdk
```

Keep `integration/openai4s` on `PYTHONPATH` for the reference host even with
the SDK installed. This OpenAI4S tree already includes the core route and
auto-install hook; do not reapply OpenMHS's historical patches against an
older OpenAI4S commit. See the upstream
[reference host guide](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/README.md)
and [design contract](https://github.com/PKU-YuanGroup/OpenMHS/blob/main/integration/openai4s/DESIGN.md).

## Configure, enable and disable

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
- Only an explicit, device-bound `mhs__estop` (or the Host action
  `mhs_emergency_stop`) skips the ordinary approval prompt. Connector
  enablement, delegated child restrictions, endpoint/device binding and the
  reference host's safety policy still apply. The stop is audited as an
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
