#!/usr/bin/env python3
"""Regenerate `docs/response-contract.json` by driving every known route.

    uv run python scripts/capture_response_contract.py
    uv run python scripts/capture_response_contract.py --check   # CI form

The companion to `capture_response_schemas.py`. That one freezes the *shape* of
JSON bodies observed while the suite runs; this one freezes what *kind* of
answer each route gives at all — json, stream, redirect, binary or empty — and
with which status codes.

Both are captured, neither is written by hand, and the distinction matters for
the same reason in both cases: a description derived from what the code does
cannot describe something the code does not do. A hand-maintained list of
"these routes stream" is correct on the day it is written and silently wrong
afterwards.

Routes are driven against a real handler and a real Store with no parameters,
so most answer 4xx. That is deliberate and it is not a way to tick a box: an
error response is a promise too, and a route that cannot answer at all shows up
as a missing entry rather than as an entry full of nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from openai4s.config import Config, LLMConfig  # noqa: E402
from openai4s.server import contract  # noqa: E402
from openai4s.server import response_capture  # noqa: E402
from openai4s.server import gateway as gateway_mod  # noqa: E402

ARTIFACT = ROOT / "docs" / "response-contract.json"


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None

    def has_subscriber(self, root_frame_id):
        return False

    def drop_frame(self, root_frame_id):
        return None


def drive() -> dict[str, dict]:
    with tempfile.TemporaryDirectory(prefix="openai4s-contract-") as temp:
        config = Config(
            data_dir=Path(temp),
            llm=LLMConfig(provider="deepseek", api_key="capture-only"),
        )
        runner = gateway_mod.SessionRunner(config, _Hub(), start_idle_sweeper=False)
        recorder = response_capture.Recorder()
        original = response_capture.install(gateway_mod, recorder)
        try:
            response_capture.drive_all_routes(
                recorder, gateway_mod.make_handler, config, runner
            )
        finally:
            gateway_mod.make_handler = original
    # Collapse "METHOD route" into "route": the kind of answer a route gives
    # does not vary by verb on this surface, and keying by verb would publish
    # five entries per route of which four say "that method is not allowed".
    merged: dict[str, dict] = {}
    for key, record in recorder.contracts().items():
        route = key.split(" ", 1)[1]
        target = merged.setdefault(
            route, {"kinds": set(), "statuses": set(), "content_types": set()}
        )
        target["kinds"] |= set(record["kinds"])
        target["statuses"] |= set(record["statuses"])
        target["content_types"] |= set(record["content_types"])
    return {
        route: {
            "kinds": sorted(value["kinds"]),
            "statuses": sorted(value["statuses"]),
            "content_types": sorted(value["content_types"]),
        }
        for route, value in sorted(merged.items())
    }


def document(routes: dict[str, dict]) -> dict:
    return {
        "schema_version": 1,
        "note": (
            "Captured by driving every known route against a real handler, not "
            "written by hand. Regenerate with "
            "scripts/capture_response_contract.py. `kinds` is derived from the "
            "status and content type the handler actually sent; a route absent "
            "here is one nothing could drive, which is a gap in the tests."
        ),
        "routes": routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the artifact is out of date instead of rewriting it",
    )
    args = parser.parse_args()

    routes = drive()
    known = contract.http_routes()
    covered = sorted(set(routes) & known)
    missing = sorted(known - set(routes))
    payload = document(routes)

    if args.check:
        current = json.loads(ARTIFACT.read_text("utf-8")) if ARTIFACT.is_file() else {}
        if current.get("routes") != payload["routes"]:
            print("response contract is out of date; regenerate it", file=sys.stderr)
            return 1
        print(f"contract up to date: {len(covered)}/{len(known)} routes")
        return 0

    ARTIFACT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {ARTIFACT.relative_to(ROOT)}")
    print(f"  covered: {len(covered)}/{len(known)} routes")
    if missing:
        print(f"  NOT reached ({len(missing)}):")
        for route in missing:
            print(f"    {route}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
