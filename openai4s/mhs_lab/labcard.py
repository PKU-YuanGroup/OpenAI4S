"""Generate the lab card: a short, exact guide to the lab's devices for the agent.

A raw ``list_mcp_tools`` answer for one OpenMHS connector is about 20 KB of
JSON schema, and its capability tool names carry hash suffixes.  The card lists
what the agent needs instead -- every tool or capability with its arguments,
units and limits -- read from the live connectors and the live reactor
manifest, so the names are always the ones the endpoints serve.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from openai4s.mhs_lab import lab_dir

#: Large-manifest tools the card replaces; listing them only invites a 25 KB answer.
_OMIT_TOOLS = {"mhs__devices", "mhs__inspect"}
_SKIP_ARGS = {
    "command_id",
    "deadline_ms",
    "expected_revision",
    "permit_id",
    "session_id",
}


def _mcp_tools(
    argv: list[str], env: dict[str, str] | None, timeout: float = 30.0
) -> list[dict[str, Any]]:
    from openai4s.mcp_client import list_stdio_tools
    from openai4s.mhs_lab.runtime import child_environment

    return list_stdio_tools(argv, env=child_environment(env), timeout=timeout)


def _arg_text(name: str, schema: dict[str, Any], required: set[str]) -> str:
    kind = schema.get("type", "")
    if kind == "array":
        items = schema.get("items") or {}
        kind = f"array<{items.get('type', '')}{'|' + '/'.join(map(str, items['enum'])) if items.get('enum') else ''}>"
    unit = schema.get("x-mhs-unit") or schema.get("unit")
    bits = [f"`{name}`" + ("" if name in required else "?"), str(kind)]
    if unit:
        bits.append(str(unit))
    if "minimum" in schema or "maximum" in schema:
        bits.append(f"{schema.get('minimum', '')}..{schema.get('maximum', '')}")
    if schema.get("enum"):
        bits.append("one of " + "/".join(map(str, schema["enum"])))
    return " ".join(bits)


def _tool_lines(tools: list[dict[str, Any]]) -> list[str]:
    lines = []
    for tool in tools:
        if tool.get("name") in _OMIT_TOOLS:
            continue
        schema = tool.get("inputSchema") or {}
        props = {
            k: v
            for k, v in (schema.get("properties") or {}).items()
            if k not in _SKIP_ARGS
        }
        required = set(schema.get("required") or [])
        desc = (tool.get("description") or "").split(". Mode:")[0].split("\n")[0][:160]
        args = (
            "; ".join(_arg_text(k, v, required) for k, v in props.items())
            or "no arguments"
        )
        lines.append(f"- `{tool['name']}` -- {desc}. Args: {args}")
    return lines


def _reactor_lines(url: str) -> list[str]:
    from mhs.client import connect  # type: ignore

    client = connect(url, timeout=5.0)
    try:
        manifest = client.devices()[0]
        device_id = manifest["device_id"]
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass
    lines = [f"Device id `{device_id}` (resource `main`)."]
    for cap in manifest.get("capabilities", []):
        cid = cap["capability_id"]
        if cid.startswith("cap.internal") or cid == "cap.device.configure.v1":
            continue
        schema = cap.get("input_schema") or {}
        required = set(schema.get("required") or [])
        props = schema.get("properties") or {}
        args = (
            "; ".join(_arg_text(k, v, required) for k, v in props.items())
            or "no arguments"
        )
        limits = cap.get("limits") or {}
        if isinstance(limits, dict) and limits:
            spans = []
            for field, lim in limits.items():
                if isinstance(lim, dict) and ("min" in lim or "max" in lim):
                    spans.append(f"{field} {lim.get('min', '')}..{lim.get('max', '')}")
            if spans:
                args += " | limits: " + ", ".join(spans)
        title = (cap.get("title") or (cap.get("semantics") or {}).get("summary") or "")[
            :150
        ]
        lines.append(f"- `{cid}` -- {title}. Input: {args}")
    return lines


#: Installed devices whose tools are listed live (each listing starts a short-lived MCP process).
_LIVE_LISTING_LIMIT = 12


def _installed_sections(
    doc: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[str]:
    """One section per market device the operator installed, grouped by agent surface."""
    added = [ep for ep in doc["endpoints"] if not ep.get("builtin")]
    if not added:
        return []
    out = [
        "## Installed market devices (OpenMHS market, fake backends)",
        "",
        "Drivers adapted from public repositories, served on their fake backends (no instrument attached). "
        "MHS devices use the native tools (`mhs_read`, `mhs_command`, `mhs_safety`); MCP devices use "
        '`call_mcp_tool(server="mhs-<id>", tool=<name>, args={...})`.',
        "",
    ]
    for index, ep in enumerate(added):
        surface = (
            "native tools `mhs_*` (MHS, HTTP)"
            if ep.get("group") == "mhs"
            else f"MCP connector `{ep.get('connector')}`"
        )
        out += [
            f"### `{ep['id']}` -- {ep['title']} -- {surface}",
            "",
            f"Market package `{ep.get('package')}` ({ep.get('category') or 'device'}, risk {ep.get('risk')})."
            + (
                " Limits that only an operator can state come from the package's example site limits; "
                "a value outside them is refused (MHS.SAFETY.ENVELOPE_VIOLATION)."
                if ep.get("site_limits")
                else " A write whose limit only an operator can state is refused (MHS.SAFETY.LIMITS_UNSET); "
                "report it, do not retry."
            ),
            "",
        ]
        if index >= _LIVE_LISTING_LIMIT:
            out += [
                "(tool list omitted: many devices installed; read `mhs__read` / `mhs_inspect` for this one)",
                "",
            ]
            continue
        try:
            if ep.get("group") == "mhs" and ep.get("url"):
                out += _reactor_lines(ep["url"])
            elif entries.get(str(ep.get("connector"))):
                entry = entries[str(ep["connector"])]
                out += _tool_lines(_mcp_tools(entry["command"], entry.get("env")))
        except Exception as exc:  # noqa: BLE001 - the card says what is missing
            out.append(f"(listing failed: {type(exc).__name__}: {exc})")
        out.append("")
    return out


def build_card(doc: dict[str, Any] | None = None) -> str:
    from openai4s.mhs_lab import supervisor

    if doc is None:
        doc = supervisor.lab_document()
    eps = {ep["id"]: ep for ep in doc["endpoints"]}
    out = [
        "# OpenMHS simulated lab -- device card",
        "",
        "Every device here is a SIMULATED OpenMHS endpoint or a driver on its fake backend (no hardware). The endpoint enforces every limit, "
        "interlock and e-stop: a refused command comes back as a receipt with an `MHS.*` code -- report it, never "
        "work around it. Every receipt is hash-chained into the endpoint's evidence log.",
        "Do not call `list_mcp_tools`, `mhs__devices`, `mhs__inspect` or `mhs_inspect`: everything they return is "
        "in this card, and their 20-30 KB manifests only get archived and cost turns. Device commands are JSON tool "
        "calls; use Python cells only for analysis (fitting, optimisation, plots): write the analysis as a fenced "
        "```python code block so it runs as a Cell (no approval needed). Do not use `exec_background` and do not "
        "write script files to run them. Each reply is EITHER tool calls OR one ```python block, never both: a cell sent "
        "together with any tool call does not run. Kernel cells cannot reach the devices. Put independent device "
        "calls in one tool batch.",
        "",
        "Argument examples (copy the shapes):",
        '- `call_mcp_tool(server="mhs-assay", tool="mhs__read", args={"resource_id": "main"})`',
        '- assay prepare: `args={"row": "A", "compound_id": "CPD-101", "top_concentration_uM": 100, '
        '"dilution_factor": 3, "points": 10}`; start: `args={"rows": ["A","B"], "substrate_uM": 50, '
        '"temperature_C": 30}`; read: `args={"rows": ["A","B"], "wavelength_nm": 405, "duration_s": 600, '
        '"interval_s": 30}`; load: `args={"vial_id": "V-001"}`',
        '- reactor: `mhs_command(device_id="mhs://localhost/flow-reactor", capability_id="x-cap.openai4s-lab.run_condition.v1", '
        'input={"temperature_C": 90, "residence_time_min": 2, "equiv_b": 2.0, "concentration_M": 0.5, '
        '"reagent_b": "B1"})`',
        '- xArm move: `call_mcp_tool(server="mhs-xarm", tool=<cartesian tool>, args={"position": {"x": 300, "y": 0, '
        '"z": 300}})` (orientation optional); enable: `args={}`; pose: `mhs__read` `args={"resource_id": "main"}`',
        '- safety: `mhs__safety` / `mhs__estop` take `{"device_id": <device id>}` (+ `"reason"` for estop). Device ids: '
        "assay `mhs://localhost/assay-station`, reactor `mhs://localhost/flow-reactor`, xArm `mhs://localhost/xarm7/fake`.",
        '- evidence: `mhs__evidence` `args={"last_n": 5}`; native: `mhs_evidence` with `{"action": "verify"}`.',
        "",
    ]
    try:
        import openai4s_mhs  # type: ignore
        from openai4s_mhs.config import MHSConfig  # type: ignore
        from openai4s_mhs.mcpconfig import connector_entries  # type: ignore

        cfg_path = str(lab_dir() / "mhs-connectors.json")
        entries = {
            e["id"]: e
            for e in connector_entries(MHSConfig.load(cfg_path), enabled=True)
        }
    except Exception as exc:  # noqa: BLE001
        entries = {}
        out.append(f"(connector listing unavailable: {type(exc).__name__}: {exc})")
    sections = [
        (
            "assay",
            "1. AS-96 enzyme-inhibition assay workstation -- MCP connector `mhs-assay` (wire: HTTP)",
            'Call with `call_mcp_tool(server="mhs-assay", tool=<name>, args={...})`. Read state with '
            '`mhs__read` args `{"resource_id": "main"}` (plate, inventory, tips, temperature). '
            "Protocol: prepare_dose_series per row -> start_reaction(rows) -> read_kinetics(rows). Columns 1..points "
            "hold the dose series, column 11 the vehicle control (100 % activity), column 12 the no-enzyme blank "
            "(0 %). % activity = 100*(v - blank)/(vehicle - blank). Enzyme Km for the substrate is 50 uM "
            "(Cheng-Prusoff: Ki = IC50 / (1 + S/Km)).",
        ),
        (
            "xarm",
            "3. UFACTORY xArm 7 robot arm -- MCP connector `mhs-xarm` (wire: stdio)",
            "OpenMHS registry driver adapted from the real xArm-Python-SDK, served on its fake backend through "
            'a stdio child of the MCP projection. Call with `call_mcp_tool(server="mhs-xarm", ...)`. Enable motion '
            'once before moving. Read pose with `mhs__read` args `{"resource_id": "main"}`. Cartesian position in '
            "mm; the endpoint enforces x/y in -750..750 and z in -400..1000. Named poses: home x=300 y=0 z=300; "
            "reactor_outlet x=420 y=-180 z=120; assay_deck x=380 y=220 z=95; approach heights are +80 mm in z. "
            "Moving a LIMS vial = approach above source -> descend -> (vial picked) -> lift -> approach above "
            "destination -> descend -> lift.",
        ),
    ]
    for key, title, howto in sections[:1]:
        out += [f"## {title}", "", howto, ""]
        entry = entries.get("mhs-" + key)
        if entry:
            try:
                out += _tool_lines(_mcp_tools(entry["command"], entry.get("env")))
            except Exception as exc:  # noqa: BLE001
                out.append(f"(tool listing failed: {type(exc).__name__}: {exc})")
        out.append("")
    out += [
        "## 2. FR-10 continuous-flow reactor with inline HPLC -- native OpenAI4S tools (wire: direct HTTP JSON-RPC)",
        "",
        "Not an MCP connector: use the native tools `mhs_inspect`, `mhs_read` and `mhs_command`. "
        '`mhs_command(device_id="'
        + str(eps["reactor"]["device_id"])
        + '", capability_id=<id>, input={...})`. '
        "Several `mhs_command` calls may go in one tool batch (they run one after another). Each run_condition "
        "is one steady-state experiment analysed by inline HPLC. Product of amine B1/B2/B3 is FR-1/FR-2/FR-3. "
        "collect_product fills a LIMS vial at the reactor outlet.",
        "",
    ]
    try:
        out += _reactor_lines(eps["reactor"]["url"])
    except Exception as exc:  # noqa: BLE001
        out.append(f"(reactor manifest unavailable: {type(exc).__name__}: {exc})")
    out.append("")
    for key, title, howto in sections[1:]:
        out += [f"## {title}", "", howto, ""]
        entry = entries.get("mhs-" + key)
        if entry:
            try:
                out += _tool_lines(_mcp_tools(entry["command"], entry.get("env")))
            except Exception as exc:  # noqa: BLE001
                out.append(f"(tool listing failed: {type(exc).__name__}: {exc})")
        out.append("")
    out += _installed_sections(doc, entries)
    paths = {
        ep["id"]: str(Path(ep["state_dir"]) / "evidence.jsonl")
        for ep in doc["endpoints"]
    }
    out += [
        "## Analysis data: read the verified evidence, never retype numbers",
        "",
        "Every receipt (with its full output) is appended to the endpoint's hash-chained evidence log, which "
        "Python cells may read (read-only). Load the data for analysis from there instead of copying numbers "
        "by hand, and verify the chain first:",
        "",
        "```python",
        "import json",
        "from mhs.evidence import verify_chain",
        f"EVIDENCE = {json.dumps(paths, ensure_ascii=False)}",
        "def receipts(endpoint, capability=None):",
        "    recs = [json.loads(l) for l in open(EVIDENCE[endpoint])]",
        "    g = recs[0]; body = recs[1:]",
        "    rep = verify_chain(g['genesis_document'], body, genesis_digest=g['digest'])",
        "    print(endpoint, 'chain valid:', rep.valid, 'receipts:', rep.checked)",
        "    return [r for r in body if r.get('status') == 'completed'",
        "            and (capability is None or r.get('capability_id') == capability)]",
        "read = receipts('assay', 'x-cap.openai4s-lab.read_kinetics.v1')[-1]['output']   # rows -> per-well rates",
        "runs = [r['output'] for r in receipts('reactor', 'x-cap.openai4s-lab.run_condition.v1')]",
        "```",
        "",
    ]
    out += [
        "## Sample hand-off",
        "",
        "A vial filled by `collect_product` is registered in the LIMS at `reactor_outlet`. The assay deck has a "
        "presence sensor: `load_sample(vial_id)` succeeds only after the xArm has descended to reactor_outlet "
        "(z <= 125) and then to assay_deck (z <= 100) after the vial was filled -- the assay checks the arm's own "
        "evidence chain. Then the vial's compound (FR-n, concentration and HPLC purity from the LIMS) is in the "
        "assay inventory.",
        "",
        "## Safety",
        "",
        "`mhs__estop` (MCP) or `mhs_safety` with action estop (native) latches a device. Only a human operator can "
        "reset a latch (Lab panel); the agent never resets. After an e-stop, report and stop.",
    ]
    return "\n".join(out) + "\n"


def write_lab_card(*, expected_generation: str | None = None) -> str:
    from openai4s.mhs_lab import supervisor
    from openai4s.mhs_lab.runtime import mutation_lock

    path = lab_dir() / "LAB-CARD.md"
    with mutation_lock():
        if expected_generation is not None:
            supervisor._require_generation(expected_generation)
        generation = supervisor.running_generation()
        snapshot = supervisor.lab_document()
    card = build_card(snapshot)
    with mutation_lock():
        if expected_generation is not None:
            supervisor._require_generation(expected_generation)
        # A newer lifecycle or device mutation owns the replacement card.
        if (
            generation != supervisor.running_generation()
            or snapshot != supervisor.lab_document()
        ):
            return str(path)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        ) as stream:
            temporary = Path(stream.name)
            try:
                stream.write(card)
                stream.close()
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
    return str(path)
