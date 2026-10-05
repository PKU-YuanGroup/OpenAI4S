"""Pure mappings over ordinary Python data, independent of NumPy and Gym."""

import hashlib
import json
import math
import re


def canonical_json(obj):
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def resource_ids(vessel_labels):
    ids = []
    for label in vessel_labels:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("empty vessel label")
        name = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        if not name or name in ids:
            raise ValueError("duplicate or invalid resource identity")
        ids.append(name)
    return ids


def _action(row, ids):
    resources = sorted(ids[i] for i in row["vessels"])
    source = target = None
    parameters = {}
    if row["terminal"]:
        operation, effect = "end_experiment", "ends_run"
        capability_id = operation
    else:
        event = row["event"]
        values = row["parameters"]
        if (
            len(values) != len(row["vessels"])
            or not values
            or any(len(v) != 1 or v != values[0] for v in values)
        ):
            raise ValueError("unsupported compound action")
        value = values[0][0]
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid action value")
        if event in ("pour by volume", "drain by pixel"):
            if len(row["vessels"]) != 1 or len(row["affected_vessels"] or []) != 1:
                raise ValueError("unsupported transfer topology")
            source, target = ids[row["vessels"][0]], ids[row["affected_vessels"][0]]
            resources = sorted({source, target})
            operation, key, unit = (
                ("transfer_liquid", "volume", "mL")
                if event == "pour by volume"
                else ("drain_layers", "pixels", "layer_px")
            )
            value = value * 1000 if operation == "transfer_liquid" else value
            effect = "moves_material"
            capability_id = f"{operation}:{source}->{target}"
        elif event == "mix" and value != 0:
            operation, key, unit, effect = (
                ("mix_model" if value < 0 else "settle_model"),
                "duration",
                "model_time",
                "changes_state",
            )
            # Keep the signed upstream control value; it is not physical duration.
            source = resources[0] if len(resources) == 1 else None
            capability_id = f"{operation}:{source}" if source else operation
        else:
            raise ValueError("unsupported upstream event")
        parameters = {key: {"unit": unit, "allowed": [value]}}
    return {
        "capability_id": capability_id,
        "operation": operation,
        "scope": "shared" if operation == "transfer_liquid" else "simulation_only",
        "source": source,
        "target": target,
        "parameters": parameters,
        "side_effect": effect,
        "resources": resources,
        "observes": ["layers"],
        "terminal": bool(row["terminal"]),
        "mapping_version": "1",
    }


def channel_specs(layout):
    if (
        layout["observation_list"] != ["layers", "targets"]
        or layout["order"] != "vessel_major"
    ):
        raise ValueError("unsupported observation layout")
    return [
        {
            "name": "layers",
            "kind": "array",
            "shape": [len(layout["vessel_indices"]), layout["sizes"]["layers"]],
            "unit": "dimensionless",
            "source": "simulated_sensor",
            "available": True,
            "description": "Simulated layer colors; no material legend.",
        },
        {
            "name": "targets",
            "kind": "category",
            "unit": "dimensionless",
            "source": "simulated_sensor",
            "available": True,
            "description": "Experiment target decoded from the observation one-hot.",
        },
    ]


def derive_descriptor(
    profile, action_rows, vessel_labels, observation_layout, limits, versions
):
    ids = resource_ids(vessel_labels)
    grouped = {}
    incoming, outgoing = set(), set()
    for row in action_rows:
        capability = _action(row, ids)
        if capability["source"] and capability["target"]:
            outgoing.add(capability["source"])
            incoming.add(capability["target"])
        key = capability["capability_id"]
        if key not in grouped:
            grouped[key] = capability
        else:
            existing = grouped[key]
            if {k: v for k, v in existing.items() if k != "parameters"} != {
                k: v for k, v in capability.items() if k != "parameters"
            } or set(existing["parameters"]) != set(capability["parameters"]):
                raise ValueError("ambiguous capability identity")
            for parameter, spec in capability["parameters"].items():
                if existing["parameters"][parameter]["unit"] != spec["unit"]:
                    raise ValueError("inconsistent parameter units")
                existing["parameters"][parameter]["allowed"].extend(spec["allowed"])
    capabilities = [grouped[key] for key in sorted(grouped)]
    for cap in capabilities:
        for spec in cap["parameters"].values():
            spec["allowed"] = sorted(set(spec["allowed"]))
    return {
        "contract": "openai4s.lab/v1-draft",
        "device_id": "chemgym.extractor.01",
        "mode": "simulation",
        "backend": "chemgymrl",
        "profile": profile,
        "backend_version": dict(versions),
        "resources": [
            {
                "resource_id": rid,
                "kind": (
                    "source" if rid in outgoing and rid not in incoming else "vessel"
                ),
                "label": label,
            }
            for rid, label in zip(ids, vessel_labels)
        ],
        "capabilities": capabilities,
        "capability_revision": hashlib.sha256(
            canonical_json(capabilities).encode()
        ).hexdigest(),
        "observation_channels": channel_specs(observation_layout),
        "limits": dict(limits),
        "stop": {"supported": True, "semantics": "end_session"},
        "time": {"unit": "model_time", "wall_clock_equivalent": None},
        "reproducibility": {"status": "unverified", "evidence": None},
        "assumptions": [],
    }


def command_to_action(action_rows, vessel_labels, normalized_command):
    ids = resource_ids(vessel_labels)
    matches = []
    for row in action_rows:
        cap = _action(row, ids)
        if any(
            normalized_command.get(key) != cap[key]
            for key in ("capability_id", "operation", "source", "target")
        ):
            continue
        params = normalized_command.get("parameters")
        if not isinstance(params, dict) or set(params) != set(cap["parameters"]):
            continue
        valid = True
        for key, spec in cap["parameters"].items():
            q = params[key]
            if (
                not isinstance(q, dict)
                or set(q) != {"value", "unit"}
                or q["unit"] != spec["unit"]
                or type(q["value"]) not in (int, float)
                or not math.isfinite(q["value"])
                or q["value"] != spec["allowed"][0]
            ):
                valid = False
        if valid:
            matches.append(row["index"])
    if len(matches) > 1:
        raise ValueError("ambiguous command mapping")
    return matches[0] if matches else None


def decode_observation(vector, layout):
    specs = channel_specs(layout)
    n = len(layout["vessel_indices"])
    layers_n, targets_n = layout["sizes"]["layers"], layout["sizes"]["targets"]
    stride = layers_n + targets_n
    if (
        len(vector) != n * stride
        or targets_n != len(layout["targets"])
        or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
    ):
        raise ValueError("invalid observation shape or values")
    layers, targets = [], []
    for i in range(n):
        start = i * stride
        layers.append(vector[start : start + layers_n])
        onehot = vector[start + layers_n : start + stride]
        if onehot.count(1) != 1 or any(value not in (0, 1) for value in onehot):
            raise ValueError("invalid target observation")
        targets.append(layout["targets"][onehot.index(1)])
    if not targets or len(set(targets)) != 1:
        raise ValueError("inconsistent target observation")
    channels = []
    for spec, value in zip(specs, [layers, targets[0]]):
        channel = {
            k: v for k, v in spec.items() if k not in ("available", "description")
        }
        channel.update(value=value, quality="ok")
        channels.append(channel)
    return channels


def ground_truth(vessels):
    ids = resource_ids([v["label"] for v in vessels])
    return {
        "vessels": [
            {
                "resource_id": rid,
                "temperature_K": v["temperature_K"],
                "volume_L": v["volume_L"],
                "moles": dict(v["moles"]),
            }
            for rid, v in zip(ids, vessels)
        ]
    }
