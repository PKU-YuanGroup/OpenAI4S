"""Recorded simulation evidence; never execute, reconcile or infer success.

The adapter holds the Store lock while taking this snapshot and committing it.
``commit`` owns file publication and returns a verified, exact Artifact version.
Every export deliberately creates new versions; observations retain their first
export reference. No arrays or evaluation values enter the returned envelope.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from typing import Any

from openai4s.lab.manifest import (
    observation_from_row,
    project_command,
    project_observation,
    project_run,
)
from openai4s.lab.models import DeviceDescriptor, ErrorCode, LabCaller, LabError
from openai4s.lab.ports import LabLedgerPort


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    )


def export_run(
    ledger: LabLedgerPort,
    caller: LabCaller,
    run_id: str,
    include_evaluation: bool,
    commit: Callable[[str, str, str, dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    if caller.execution_owner == "recovery":
        raise LabError(ErrorCode.REPLAY_FORBIDDEN, "Lab exports cannot be replayed")
    if (
        not isinstance(run_id, str)
        or not run_id
        or type(include_evaluation) is not bool
    ):
        raise LabError(ErrorCode.INVALID_PARAMETERS, "Invalid Lab export arguments")
    run = ledger.get_run(run_id)
    if (
        run is None
        or run["root_frame_id"] != caller.root_frame_id
        or run["owner_user_id"] != caller.owner_user_id
    ):
        raise LabError(ErrorCode.RUN_NOT_FOUND, "Lab run was not found")
    descriptor = DeviceDescriptor.from_dict(run["descriptor"])
    commands = [
        project_command(row) for row in ledger.list_commands(run_id, limit=2**63 - 1)
    ]
    rows = ledger.list_observations(run_id, limit=2**63 - 1)
    observations = []
    for row in rows:
        obs = project_observation(
            observation_from_row(row),
            channels=descriptor.observation_channels,
            full=True,
        )
        # Self-references cannot name the version whose bytes are being made.
        # The report records each observation's immutable first export binding.
        obs.pop("artifact_version_id", None)
        observations.append(obs)
    source = {
        "kind": "lab_export",
        "mode": "simulation",
        "run_id": run_id,
        "command_ids": [row["command_id"] for row in commands],
        "observation_ids": [row["observation_id"] for row in observations],
    }
    artifacts = []

    def save(kind: str, suffix: str, content: str) -> dict[str, Any]:
        result = commit(kind, suffix, content, {**source, "format": kind})
        if not all(
            isinstance(result.get(k), str) and result[k]
            for k in ("artifact_id", "version_id", "filename", "checksum")
        ):
            raise LabError(
                ErrorCode.PERSISTENCE_UNAVAILABLE, "Lab export Artifact commit failed"
            )
        item = {
            "kind": kind,
            **{
                key: result[key]
                for key in ("artifact_id", "version_id", "filename", "checksum")
            },
        }
        artifacts.append(item)
        return item

    save("actions", "actions.jsonl", "".join(_json(row) + "\n" for row in commands))
    observation_artifact = save(
        "observations_json",
        "observations.json",
        _json({"run_id": run_id, "mode": "simulation", "observations": observations})
        + "\n",
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "run_id",
            "observation_id",
            "command_id",
            "sequence",
            "sim_time",
            "sim_time_unit",
            "wall_time_ms",
            "channel",
            "kind",
            "unit",
            "source",
            "quality",
            "shape_json",
            "value_json",
        ]
    )
    for obs in observations:
        for channel in obs["channels"]:
            writer.writerow(
                [
                    *(
                        obs[k]
                        for k in (
                            "run_id",
                            "observation_id",
                            "command_id",
                            "sequence",
                            "sim_time",
                            "sim_time_unit",
                            "wall_time_ms",
                        )
                    ),
                    *(
                        channel[k]
                        for k in ("name", "kind", "unit", "source", "quality")
                    ),
                    _json(channel["shape"]),
                    _json(channel["value"]),
                ]
            )
    save("observations_csv", "observations.csv", buffer.getvalue())
    if include_evaluation:
        evaluations = ledger.list_evaluations(run_id, limit=2**63 - 1)
        # This is the sole opt-in boundary. Never inline it in the reply/report.
        summary = [
            {
                key: row.get(key)
                for key in (
                    "evaluation_id",
                    "run_id",
                    "command_id",
                    "sequence",
                    "reward",
                    "ground_truth",
                    "metrics",
                )
            }
            for row in evaluations
        ]
        save(
            "simulation_ground_truth",
            "simulation-ground-truth.json",
            _json(
                {
                    "label": "Simulation ground truth (仿真真值)",
                    "mode": "simulation",
                    "run_id": run_id,
                    "evaluations": summary,
                }
            )
            + "\n",
        )
    ledger.attach_observation_artifacts(
        run_id,
        {
            row["observation_id"]: observation_artifact["version_id"]
            for row in rows
            if row.get("artifact_version_id") is None
        },
    )
    bindings = [
        {
            "observation_id": row["observation_id"],
            "command_id": row["command_id"],
            "artifact_version_id": row.get("artifact_version_id")
            or observation_artifact["version_id"],
        }
        for row in rows
    ]
    report = [
        "# Recorded simulation experiment",
        "",
        "Recorded evidence only. Run state and unknown outcomes are preserved; this report does not certify scientific success.",
        "",
        "## Run",
        "",
        "```json",
        _json(project_run(run)),
        "```",
        "",
        "## Exact Artifact versions",
        "",
    ]
    for item in artifacts:
        label = (
            "Simulation ground truth (仿真真值)"
            if item["kind"] == "simulation_ground_truth"
            else item["kind"]
        )
        report.append(
            f'- {label}: [{item["filename"]}](/api/v1/artifacts/versions/{item["version_id"]}) · SHA-256 `{item["checksum"]}`'
        )
    report += [
        "",
        "## Recorded observation associations",
        "",
        "```json",
        _json(bindings),
        "```",
        "",
        "Each export creates new Artifact versions. Observation associations retain the first committed observations JSON version.",
        "",
    ]
    save("report", "report.md", "\n".join(report))
    return {
        "run_id": run_id,
        "include_evaluation": include_evaluation,
        "command_count": len(commands),
        "observation_count": len(observations),
        "artifacts": artifacts,
    }
