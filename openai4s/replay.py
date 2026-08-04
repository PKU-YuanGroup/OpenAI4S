"""Replay tape record + playback — offline reproducible notebooks.

A real run makes a sequence of `host.*` calls whose RESULTS come from the host
(sub-LLM completions, query results, artifact paths...). To make an exported
notebook reproducible OFFLINE — with no host attached — we record every
successful host_call into an `openai4s_tape.json` tape. The exported code then
runs against `_OpenAI4SReplay`, which returns the taped result for each call in
order.

The subtle part is DRIFT DETECTION. When the model exports the
notebook it commonly DROPS discovery/no-op probe calls (`list_models`,
`current_model`, `artifacts`, `list_artifacts`) because they don't affect the
data flow. If replay advanced the tape pointer on every mismatch, every
subsequent SDK call would be off by one. So `_next(m)` PEEKs forward past any
*skippable* taped methods using a LOCAL index `j` (never `self._i`) and only
commits `self._i` once it finds the matching method — a probe the model kept
still matches, a probe the model dropped is silently skipped WITHOUT moving the
pointer for the surrounding calls.

`raise`, not `assert`: asserts are stripped under `python -O`, so drift would
go undetected in optimized runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Probe/discovery methods the exporter commonly drops from extracted_code.
# On a method mismatch we PEEK past these instead of erroring.
_OPENAI4S_SKIPPABLE = frozenset(
    {
        "list_artifacts",
        "artifacts",
        "current_model",
        "list_models",
        "capabilities",
        "get_user_email",
    }
)

# Internal plumbing calls that never appear in exported notebook code and so
# must NOT be taped (provenance edges, credential reads, host-only logging).
# Recording them would inject phantom records the replayed code never issues.
_TAPE_EXCLUDE = frozenset(
    {
        "prov_resolve_path",
        "prov_record",
        "credentials_get",
        "credentials_issue",
        "credentials_redeem",
        "credentials_list",
        "view_image",
        "app_render",
    }
)


class ReplayDrift(RuntimeError):
    """Raised when the live call sequence diverges from the tape."""


class _JsonSafe:
    """Best-effort JSON coercion so arbitrary results can be taped."""

    @staticmethod
    def coerce(obj: Any) -> Any:
        try:
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            pass
        # pandas / numpy / paths / sets: degrade to a stable repr envelope.
        try:
            import pandas as pd  # noqa: F401

            if hasattr(obj, "to_dict"):
                return {
                    "__replay_type__": "dataframe",
                    "data": obj.to_dict(orient="list"),
                }
        except Exception:  # noqa: BLE001
            pass
        if isinstance(obj, (set, frozenset)):
            return {"__replay_type__": "set", "data": sorted(map(str, obj))}
        if isinstance(obj, Path):
            return str(obj)
        return {"__replay_type__": "repr", "data": repr(obj)}


class TapeRecorder:
    """Wired onto a HostDispatcher as `.recorder`; appends each host_call."""

    def __init__(self, tape_path: str | Path):
        self.tape_path = Path(tape_path)
        self.records: list[dict] = []

    def record(self, method: str, args: list, result: Any) -> None:
        if method in _TAPE_EXCLUDE:
            return
        self.records.append(
            {
                "method": method,
                "args": _JsonSafe.coerce(args),
                "result": _JsonSafe.coerce(result),
            }
        )

    def flush(self) -> Path:
        self.tape_path.parent.mkdir(parents=True, exist_ok=True)
        self.tape_path.write_text(
            json.dumps(
                {"version": 1, "tape": self.records}, ensure_ascii=False, indent=2
            )
        )
        return self.tape_path


class _OpenAI4SReplay:
    """Sequential tape playback with skippable-probe drift detection.

    Usage in an exported notebook:
        host = _OpenAI4SReplay(_OPENAI4S_TAPE)
        host.current_model  # may be absent in tape -> skip-safe
        out = host.llm({...})  # returns taped completion
    """

    def __init__(self, tape: list[dict]):
        self._tape = tape
        self._i = 0

    def _next(self, method: str) -> Any:
        # Check BEFORE consuming: a method mismatch must not advance the tape
        # position, or every subsequent SDK call is off by one.
        if self._i >= len(self._tape):
            raise ReplayDrift(
                f"replay: tape exhausted while expecting host.{method} "
                f"(consumed {self._i}/{len(self._tape)})"
            )
        rec = self._tape[self._i]
        j = self._i
        # PEEK past skips using a LOCAL j (not self._i): a dropped probe in the
        # exported code means the tape has an extra skippable record here.
        while rec["method"] != method and rec["method"] in _OPENAI4S_SKIPPABLE:
            j += 1
            if j >= len(self._tape):
                raise ReplayDrift(
                    f"replay: ran off the tape peeking for host.{method} "
                    f"past skippable probes"
                )
            rec = self._tape[j]
        if rec["method"] != method:
            raise ReplayDrift(
                f"replay drift at position {self._i}: tape has "
                f"host.{rec['method']} but code called host.{method}"
            )
        # commit: advance the REAL pointer past the (peeked) matched record.
        self._i = j + 1
        return _decode(rec["result"])

    # generic dispatch: host.<method>(...) -> taped result
    def __getattr__(self, method: str):
        def _call(*args, **kwargs):  # noqa: ANN001, ANN002
            return self._next(method)

        return _call


def _decode(value: Any) -> Any:
    if isinstance(value, dict) and "__replay_type__" in value:
        kind = value["__replay_type__"]
        if kind == "dataframe":
            try:
                import pandas as pd

                return pd.DataFrame(value["data"])
            except Exception:  # noqa: BLE001
                return value["data"]
        if kind == "set":
            return set(value["data"])
        return value["data"]
    return value


def load_tape(tape_path: str | Path) -> list[dict]:
    data = json.loads(Path(tape_path).read_text())
    return data["tape"] if isinstance(data, dict) else data


def make_replay(tape_path: str | Path) -> _OpenAI4SReplay:
    return _OpenAI4SReplay(load_tape(tape_path))
