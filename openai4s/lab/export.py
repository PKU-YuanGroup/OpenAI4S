"""Recorded simulation evidence; never execute, reconcile or infer success.

The adapter holds the Store lock while taking this snapshot and committing it.
``commit`` owns file publication and returns a verified, exact Artifact version.
Every export deliberately creates new versions; observations retain their first
export reference. No arrays enter the returned envelope.

Simulation ground truth is never committed: an Artifact lives in the session
workspace, where the agent can read it (CONTRACT §3.9). Only a person's
workbench request (``include_evaluation``) receives it, inline in that one
response, for the browser to download.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import Any

from openai4s.lab.manifest import (
    observation_from_row,
    project_command,
    project_observation,
    project_run,
)
from openai4s.lab.models import DeviceDescriptor, ErrorCode, LabCaller, LabError
from openai4s.lab.models import canonical_json as _json
from openai4s.lab.ports import LabLedgerPort


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
    ground_truth = None
    if include_evaluation:
        evaluations = ledger.list_evaluations(run_id, limit=2**63 - 1)
        # The sole opt-in boundary: returned to the requesting person only,
        # never committed and never written into the report.
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
        ground_truth = {
            "filename": f"{run_id}-simulation-ground-truth.json",
            "label": "Simulation ground truth (仿真真值)",
            "content": _json(
                {
                    "label": "Simulation ground truth (仿真真值)",
                    "mode": "simulation",
                    "run_id": run_id,
                    "evaluations": summary,
                }
            )
            + "\n",
        }
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
        report.append(
            f'- {item["kind"]}: [{item["filename"]}](/api/v1/artifacts/versions/{item["version_id"]}) · SHA-256 `{item["checksum"]}`'
        )
    if ground_truth is not None:
        report += [
            "",
            "Simulation ground truth (仿真真值) was delivered to the person who "
            "requested this export as a separate download. It is not stored in "
            "this session.",
        ]
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
    result = {
        "run_id": run_id,
        "include_evaluation": include_evaluation,
        "command_count": len(commands),
        "observation_count": len(observations),
        "artifacts": artifacts,
    }
    if ground_truth is not None:
        result["ground_truth"] = ground_truth
    return result
