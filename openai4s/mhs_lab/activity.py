"""One view of the simulated lab for the Lab panel: devices, receipts, evidence, results.

Every lab endpoint runs with ``--state-dir``, so its hash-chained evidence log
(``evidence.jsonl``) and its e-stop latch (``latch.json``) are files.  This module
reads them, whatever access path produced the receipts (MCP connector, native
tools over HTTP, MCP over stdio), verifies each chain with the OpenMHS SDK and
derives the panel's charts from the receipts' outputs.  It never writes an
endpoint's state; the operator actions (e-stop, reset) go through the endpoint.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path
from typing import Any

from openai4s.mhs_lab import lab_dir

_SHORT = {
    "x-cap.openai4s-lab.prepare_dose_series.v1": "prepare_dose_series",
    "x-cap.openai4s-lab.start_reaction.v1": "start_reaction",
    "x-cap.openai4s-lab.read_kinetics.v1": "read_kinetics",
    "x-cap.openai4s-lab.new_plate.v1": "new_plate",
    "x-cap.openai4s-lab.load_sample.v1": "load_sample",
    "x-cap.openai4s-lab.resume.v1": "resume",
    "x-cap.openai4s-lab.run_condition.v1": "run_condition",
    "x-cap.openai4s-lab.collect_product.v1": "collect_product",
    "cap.motion.cartesian_pose.v1": "move_pose",
    "cap.motion.joint_position.v1": "move_joints",
    "cap.motion.joint_velocity.v1": "jog_joints",
    "x-cap.xarm-developer.enable_motion.v1": "enable_motion",
    "cap.internal.safety_estop.v1": "E-STOP",
    "cap.internal.safety_reset.v1": "operator reset",
}


def _lab() -> dict[str, Any]:
    from openai4s.mhs_lab import supervisor

    return supervisor.lab_document()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue  # a line still being written
        if isinstance(item, dict):
            out.append(item)
    return out


def _evidence(ep: dict[str, Any]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    records = _read_jsonl(Path(ep["state_dir"]) / "evidence.jsonl")
    genesis = records[0] if records and records[0].get("kind") == "genesis" else None
    return genesis, [r for r in records if r.get("kind") != "genesis"]


def _latched(ep: dict[str, Any]) -> dict[str, Any]:
    try:
        doc = json.loads(
            (Path(ep["state_dir"]) / "latch.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {"latched": False}
    for value in (doc.get("devices") or {}).values():
        if value.get("latched"):
            return {
                "latched": True,
                "reason": value.get("reason"),
                "since": value.get("since"),
            }
    return {"latched": False}


def _fmt(value: Any, digits: int = 3) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:.{digits}g}"


def _summary(short: str, out: dict[str, Any]) -> str:
    if short == "prepare_dose_series":
        conc = out.get("concentrations_uM") or []
        return f"row {out.get('row')} · {out.get('compound_id')} · {len(conc)} pts from {_fmt(conc[0] if conc else '')} µM"
    if short == "start_reaction":
        return f"rows {','.join(out.get('rows') or [])} · S={_fmt(out.get('substrate_uM'))} µM · {_fmt(out.get('temperature_C'))} °C"
    if short == "read_kinetics":
        rows = out.get("rows") or {}
        return f"{out.get('read_id')} · " + ", ".join(
            f"{r}:{v.get('compound_id')}" for r, v in rows.items()
        )
    if short == "load_sample":
        return f"{out.get('vial_id')} → {out.get('compound_id')} · {_fmt(out.get('stock_mM'))} mM · purity {_fmt(out.get('purity'))}"
    if short == "run_condition":
        return (
            f"{out.get('reagent_b')} · {_fmt(out.get('temperature_C'))} °C · {_fmt(out.get('residence_time_min'))} min · "
            f"{_fmt(out.get('equiv_b'))} eq → yield {_fmt(out.get('yield_pct'), 4)} %"
        )
    if short == "collect_product":
        return (
            f"{out.get('vial_id')} · {out.get('product_id')} · {_fmt(out.get('volume_mL'))} mL · "
            f"purity {_fmt(out.get('hplc_purity_pct'), 4)} %"
        )
    if short == "move_pose":
        p = out.get("position") or {}
        return f"→ ({_fmt(p.get('x'), 4)}, {_fmt(p.get('y'), 4)}, {_fmt(p.get('z'), 4)}) mm"
    if short == "move_joints":
        return "joints → " + ", ".join(
            _fmt(v, 2) for v in (out.get("joint_positions") or [])
        )
    if short == "E-STOP":
        return f"latched{' · ' + str(out.get('reason')) if out.get('reason') else ''}"
    if short == "new_plate":
        return str(out.get("plate_id") or "")
    return ""


def _principal(rec: dict[str, Any]) -> str:
    p = rec.get("principal") or {}
    return str(p.get("id") or "")


def activity(limit: int = 300) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for ep in _lab()["endpoints"]:
        _genesis, records = _evidence(ep)
        for rec in records:
            cap = str(rec.get("capability_id") or "")
            short = _SHORT.get(
                cap, cap.rsplit(".", 2)[-2] if cap.count(".") >= 2 else cap
            )
            out = rec.get("output") if isinstance(rec.get("output"), dict) else {}
            err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
            items.append(
                {
                    "endpoint": ep["id"],
                    "access": ep["access"],
                    "seq": (rec.get("evidence") or {}).get("seq"),
                    "ts": rec.get("ended_at") or rec.get("accepted_at"),
                    "capability": short,
                    "capability_id": cap,
                    "status": rec.get("status"),
                    "code": err.get("mhs_code"),
                    "message": err.get("message"),
                    "summary": (
                        _summary(short, out) if rec.get("status") == "completed" else ""
                    ),
                    "principal": _principal(rec),
                    "receipt_id": rec.get("receipt_id"),
                    "digest": (rec.get("evidence") or {}).get("digest"),
                }
            )
    items.sort(
        key=lambda item: (
            str(item.get("ts") or ""),
            item["endpoint"],
            item.get("seq") or 0,
        )
    )
    counts: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = counts.setdefault(
            item["endpoint"], {"completed": 0, "rejected": 0, "failed": 0, "other": 0}
        )
        key = (
            item["status"]
            if item["status"] in ("completed", "rejected", "failed")
            else "other"
        )
        bucket[key] += 1
    return {
        "items": items[-max(1, min(int(limit), 2000)) :],
        "total": len(items),
        "counts": counts,
    }


def _activity_curve(data: dict[str, Any]) -> list[float]:
    vehicle = float(data.get("vehicle_rate_mOD_per_min") or 0.0)
    blank = float(data.get("blank_rate_mOD_per_min") or 0.0)
    span = vehicle - blank
    if abs(span) < 1e-9:
        return []
    return [
        round(100.0 * (float(v) - blank) / span, 2)
        for v in data.get("rate_mOD_per_min") or []
    ]


def _ic50_estimate(conc: list[float], act: list[float]) -> float | None:
    """Log-linear interpolation at 50 % activity (a quick panel estimate, not the agent's fit)."""
    pairs = sorted(zip(conc, act))
    for (c1, a1), (c2, a2) in zip(pairs, pairs[1:]):
        if c1 > 0 and c2 > 0 and (a1 - 50.0) * (a2 - 50.0) <= 0 and a1 != a2:
            t = (a1 - 50.0) / (a1 - a2)
            return round(
                10 ** (math.log10(c1) + t * (math.log10(c2) - math.log10(c1))), 4
            )
    return None


def results() -> dict[str, Any]:
    """Chart data derived from receipt outputs: dose-response, reactor campaign, arm path."""
    eps = {ep["id"]: ep for ep in _lab()["endpoints"]}
    curves = []
    _g, assay = _evidence(eps["assay"])
    for rec in assay:
        if (
            rec.get("capability_id") == "x-cap.openai4s-lab.read_kinetics.v1"
            and rec.get("status") == "completed"
        ):
            out = rec.get("output") or {}
            for row, data in (out.get("rows") or {}).items():
                conc = [float(c) for c in data.get("concentrations_uM") or []]
                act = _activity_curve(data)
                curves.append(
                    {
                        "read_id": out.get("read_id"),
                        "plate_id": out.get("plate_id"),
                        "row": row,
                        "compound_id": data.get("compound_id"),
                        "concentrations_uM": conc,
                        "activity_pct": act,
                        "ic50_estimate_uM": _ic50_estimate(conc, act),
                        "ts": rec.get("ended_at"),
                    }
                )
    if curves:  # the panel shows the current plate; older plates stay in the evidence
        latest = curves[-1].get("plate_id")
        curves = [c for c in curves if c.get("plate_id") == latest]
    runs = []
    vials = []
    _g, reactor = _evidence(eps["reactor"])
    for rec in reactor:
        out = rec.get("output") or {}
        if rec.get("status") != "completed":
            continue
        if rec.get("capability_id") == "x-cap.openai4s-lab.run_condition.v1":
            runs.append(
                {
                    k: out.get(k)
                    for k in (
                        "experiment_id",
                        "reagent_b",
                        "product_id",
                        "temperature_C",
                        "residence_time_min",
                        "equiv_b",
                        "concentration_M",
                        "yield_pct",
                        "conversion_pct",
                        "selectivity_pct",
                        "hplc_purity_pct",
                    )
                }
                | {"ts": rec.get("ended_at")}
            )
        elif rec.get("capability_id") == "x-cap.openai4s-lab.collect_product.v1":
            vials.append(dict(out, ts=rec.get("ended_at")))
    path = []
    _g, arm = _evidence(eps["xarm"])
    for rec in arm:
        if rec.get("capability_id") == "cap.motion.cartesian_pose.v1":
            pos = (rec.get("output") or {}).get("position") or {}
            path.append(
                {
                    "x": pos.get("x"),
                    "y": pos.get("y"),
                    "z": pos.get("z"),
                    "status": rec.get("status"),
                    "code": (rec.get("error") or {}).get("mhs_code"),
                    "ts": rec.get("ended_at"),
                }
            )
    best: dict[str, Any] = {}
    for run in runs:
        key = str(run.get("reagent_b"))
        if run.get("yield_pct") is not None and (
            key not in best or run["yield_pct"] > best[key]["yield_pct"]
        ):
            best[key] = run
    return {
        "dose_response": curves,
        "reactor_runs": runs,
        "reactor_best": best,
        "vials": vials,
        "arm_path": path,
    }


def verify() -> dict[str, Any]:
    """Verify every endpoint chain and the host ledger offline."""
    from mhs.evidence import verify_chain  # type: ignore

    chains = {}
    for ep in _lab()["endpoints"]:
        genesis, records = _evidence(ep)
        if genesis is None:
            chains[ep["id"]] = {
                "valid": None,
                "checked": 0,
                "head_digest": None,
                "note": "no evidence yet",
            }
            continue
        report = verify_chain(
            genesis["genesis_document"], records, genesis_digest=genesis["digest"]
        )
        chains[ep["id"]] = {
            "valid": bool(report.valid),
            "checked": int(report.checked),
            "head_digest": report.head_digest,
            "errors": list(report.errors)[:5],
            "schema": getattr(report, "schema", None),
        }
    ledger_path = lab_dir() / "mhs-host-ledger.jsonl"
    ledger: dict[str, Any] = {"path": str(ledger_path), "records": 0}
    try:
        from openai4s_mhs.host_evidence import HostLedger  # type: ignore

        if ledger_path.exists():
            ledger.update(HostLedger(ledger_path).verify())
            ledger["records"] = len(_read_jsonl(ledger_path))
    except Exception as exc:  # noqa: BLE001 - reported, not hidden
        ledger["error"] = f"{type(exc).__name__}: {exc}"
    return {"chains": chains, "host_ledger": ledger, "verified_at": time.time()}


def _http_state(url: str) -> dict[str, Any] | None:
    try:
        from mhs.client import connect  # type: ignore

        client = (
            connect(url, timeout=2.0)
            if "timeout" in connect.__code__.co_varnames
            else connect(url)
        )
        try:
            device_id = client.devices()[0]["device_id"]
            session = client.open_session(device_ids=[device_id])["session_id"]
            return dict(
                client.read(
                    device_id=device_id, resource_id="main", session_id=session
                ).get("state")
                or {}
            )
        finally:
            client.close()
    except Exception:  # noqa: BLE001 - a stopped endpoint is shown as offline
        return None


def status() -> dict[str, Any]:
    from openai4s.mhs_lab import supervisor

    doc = supervisor.status()
    res = None
    for ep in doc["endpoints"]:
        ep["latch"] = _latched(ep)
        if ep.get("url"):
            ep["state"] = _http_state(ep["url"]) if ep.get("running") else None
        else:
            _g, records = _evidence(ep)
            pose = None
            for rec in reversed(records):
                pos = (rec.get("output") or {}).get("position")
                if rec.get("status") == "completed" and pos:
                    pose = pos
                    break
            ep["state"] = {"last_pose_mm": pose, "receipts": len(records)}
            ep["running"] = None  # started on demand by its MCP projection (stdio)
    try:
        from openai4s.mhs_lab import lims

        res = lims.list_vials()
    except Exception:  # noqa: BLE001
        res = []
    doc["lims"] = res
    return doc


# -- operator actions -----------------------------------------------------------


def _stdio_once(ep: dict[str, Any], action: str, reason: str) -> dict[str, Any]:
    """Run one safety action on the stdio endpoint through a short-lived process on its state dir."""
    from mhs.client import connect  # type: ignore

    client = connect(list(ep["argv"]))
    try:
        device_id = client.devices()[0]["device_id"]
        session = client.open_session(device_ids=[device_id])["session_id"]
        if action == "estop":
            receipt = client.estop(reason, device_id=device_id, session_id=session)
        else:
            receipt = client.reset(device_id=device_id, session_id=session)
        return dict(receipt)
    finally:
        client.close()


def _disconnect_connector(connector_id: str) -> None:
    try:
        from openai4s.mcp_client import disconnect_if_initialized

        disconnect_if_initialized(connector_id)
    except Exception:  # noqa: BLE001
        pass
    time.sleep(0.5)


def operator_action(
    action: str, endpoint: str, *, operator: str = "human:operator", reason: str = ""
) -> dict[str, Any]:
    """E-stop or reset one endpoint (``all`` for an e-stop of every device)."""
    if action not in ("estop", "reset"):
        raise ValueError("action must be estop or reset")
    eps = {ep["id"]: ep for ep in _lab()["endpoints"]}
    targets = list(eps) if endpoint == "all" else [endpoint]
    if action == "reset" and endpoint == "all":
        raise ValueError("reset one endpoint at a time")
    results_: dict[str, Any] = {}
    note = reason or ("operator e-stop from the Lab panel" if action == "estop" else "")
    for name in targets:
        ep = eps.get(name)
        if ep is None:
            results_[name] = {"error": "unknown endpoint"}
            continue
        try:
            if ep.get("url"):
                from mhs.client import connect  # type: ignore

                client = connect(ep["url"])
                try:
                    device_id = client.devices()[0]["device_id"]
                    session = client.open_session(device_ids=[device_id])["session_id"]
                    receipt = (
                        client.estop(note, device_id=device_id, session_id=session)
                        if action == "estop"
                        else client.reset(device_id=device_id, session_id=session)
                    )
                finally:
                    client.close()
            else:
                # The stdio driver lives inside the MCP projection's process: stop that
                # connection (its child exits), then act on the persisted state dir.
                _disconnect_connector(ep.get("connector") or "")
                receipt = _stdio_once(ep, action, note)
            results_[name] = {
                "status": receipt.get("status"),
                "code": (receipt.get("error") or {}).get("mhs_code"),
                "receipt_id": receipt.get("receipt_id"),
            }
        except Exception as exc:  # noqa: BLE001 - shown to the operator
            results_[name] = {"error": f"{type(exc).__name__}: {exc}"}
    _audit(
        {
            "action": action,
            "endpoint": endpoint,
            "operator": operator,
            "reason": note,
            "results": results_,
        }
    )
    return {"action": action, "operator": operator, "results": results_}


def _audit(entry: dict[str, Any]) -> None:
    path = lab_dir() / "operator-actions.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry, ts=time.time()), ensure_ascii=False) + "\n")


def lab_card() -> str:
    path = lab_dir() / "LAB-CARD.md"
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = ["activity", "lab_card", "operator_action", "results", "status", "verify"]


if __name__ == "__main__":  # pragma: no cover - manual inspection
    import sys

    verb = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"status": status, "activity": activity, "results": results, "verify": verify}[
        verb
    ]
    print(json.dumps(fn(), indent=1, ensure_ascii=False, default=str)[:6000])
    _ = subprocess  # keep the import for the stdio helpers' type checkers
