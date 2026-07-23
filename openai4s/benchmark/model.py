"""What a benchmark workflow and case *are*, and where they are read from.

A manifest is JSON rather than YAML for the same reason the rest of the core
is: no third-party import may be required to read the thing that decides
whether a release is good.

The fields are the ones the proposal names — inputs, steps, permissions,
artifacts, failure conditions, expected outcome — plus a version, because a
benchmark whose cases can change silently measures nothing across time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Where the manifests live. In the repo, so a case change is a reviewable
#: diff rather than something that happened in someone's working copy.
WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / "workflows"

#: The outcomes a case may declare. Deliberately more than "it worked": a
#: benchmark that only measures success measures the half of the system nobody
#: doubted.
OUTCOMES = frozenset(
    {
        "success",
        "failure",
        "cancelled",
        "recovered",
        "permission_denied",
        "provenance",
    }
)


@dataclass(frozen=True)
class Case:
    """One versioned run of a workflow, with what it must produce."""

    id: str
    workflow: str
    title: str
    outcome: str
    inputs: dict[str, Any] = field(default_factory=dict)
    expect: dict[str, Any] = field(default_factory=dict)
    #: Overrides the workflow's step list. A workflow's failure case often runs
    #: one step further than its happy case — tamper with the package it just
    #: produced, cancel the job it just submitted — and forcing every case
    #: through one fixed list would mean either not testing that or running it
    #: where it makes no sense.
    steps: tuple[str, ...] = ()
    notes: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "case_id": self.id,
            "workflow": self.workflow,
            "title": self.title,
            "outcome": self.outcome,
            "inputs": self.inputs,
            "expect": self.expect,
            "steps": list(self.steps),
        }


@dataclass(frozen=True)
class Workflow:
    """A representative piece of scientific work this system claims to do."""

    id: str
    version: str
    title: str
    summary: str
    steps: tuple[str, ...]
    permissions: tuple[str, ...]
    artifacts: tuple[str, ...]
    failure_conditions: tuple[str, ...]
    cases: tuple[Case, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "workflow_id": self.id,
            "version": self.version,
            "title": self.title,
            "summary": self.summary,
            "steps": list(self.steps),
            "permissions": list(self.permissions),
            "artifacts": list(self.artifacts),
            "failure_conditions": list(self.failure_conditions),
            "cases": [case.public() for case in self.cases],
        }


class ManifestError(ValueError):
    """A manifest that cannot be trusted to describe a case."""


def _require(record: dict, key: str, where: str) -> Any:
    if key not in record or record[key] in (None, "", [], {}):
        raise ManifestError(f"{where}: missing required field {key!r}")
    return record[key]


def load_workflow(path: Path) -> Workflow:
    try:
        record = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        raise ManifestError(f"{path}: {e}") from e
    where = str(path)
    cases = []
    for raw in _require(record, "cases", where):
        case_id = _require(raw, "id", where)
        outcome = _require(raw, "outcome", where)
        if outcome not in OUTCOMES:
            raise ManifestError(
                f"{where}: case {case_id!r} declares unknown outcome "
                f"{outcome!r}; expected one of {sorted(OUTCOMES)}"
            )
        cases.append(
            Case(
                id=case_id,
                workflow=str(record["id"]),
                title=_require(raw, "title", where),
                outcome=outcome,
                inputs=raw.get("inputs") or {},
                expect=raw.get("expect") or {},
                steps=tuple(raw.get("steps") or ()),
                notes=raw.get("notes", ""),
            )
        )
    return Workflow(
        id=str(_require(record, "id", where)),
        version=str(_require(record, "version", where)),
        title=str(_require(record, "title", where)),
        summary=str(_require(record, "summary", where)),
        steps=tuple(_require(record, "steps", where)),
        permissions=tuple(record.get("permissions") or ()),
        artifacts=tuple(record.get("artifacts") or ()),
        failure_conditions=tuple(_require(record, "failure_conditions", where)),
        cases=tuple(cases),
    )


def load_workflows(root: Path | None = None) -> list[Workflow]:
    directory = Path(root or WORKFLOW_ROOT)
    if not directory.is_dir():
        return []
    return [load_workflow(path) for path in sorted(directory.glob("*/workflow.json"))]


__all__ = [
    "Case",
    "ManifestError",
    "OUTCOMES",
    "WORKFLOW_ROOT",
    "Workflow",
    "load_workflow",
    "load_workflows",
]
