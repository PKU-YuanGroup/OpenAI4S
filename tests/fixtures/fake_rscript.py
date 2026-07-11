#!/usr/bin/env python3
"""Fake Rscript for offline R-kernel protocol tests.

Invoked exactly like the real thing through kernel/r_kernel.r_argv:

    sh -c 'exec "$0" --vanilla "$1" 3>&1 4<&0 </dev/null 1>&2' <this file> <r_worker.R>

so it ignores its argv and speaks the JSON-per-line frame protocol on the same
channels r_worker.R uses: protocol IN on fd 4, protocol OUT on fd 3, fd 1
aliased to stderr by the sh wrapper. Scripted behaviors, keyed on the cell code:

    COUNT    -> stdout is a persistent per-process counter (proves one live
                process serves every cell)
    NOISE    -> prints garbage to real stdout first (must land on stderr, never
                the wire) then responds normally
    FLOOD    -> writes 200KB to real stderr before responding (proves the
                manager drains the stderr pipe: >64KB used to deadlock)
    DIE      -> exits 1 without responding (worker-death path)
    SLEEP    -> sleeps 30s; SIGINT -> interrupted=True response
    ENV:name -> stdout is the child environment value or "<missing>"
    anything -> stdout "ran:<code>"
"""
import json
import os
import sys
import time

inp = os.fdopen(4, "r", buffering=1)
out = os.fdopen(3, "w", buffering=1)

counter = 0


def respond(frame_id, stdout="", stderr="", error=None, interrupted=False):
    out.write(
        json.dumps(
            {
                "type": "response",
                "id": frame_id,
                "stdout": stdout,
                "stderr": stderr,
                "error": error,
                "interrupted": interrupted,
                "trace": {"error_lineno": None, "error_call": None},
                "guards": {},
                "usage": {"wall_s": 0.0, "cpu_s": 0.0, "peak_rss_kb": 0},
            }
        )
        + "\n"
    )
    out.flush()


while True:
    line = inp.readline()
    if not line:
        break
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if req.get("type") == "shutdown":
        break
    if req.get("type") != "execute":
        continue
    rid = req.get("id", "unknown")
    code = (req.get("code") or "").strip()
    if code == "DIE":
        os._exit(1)
    if code == "NOISE":
        print("garbage that must never reach the protocol wire")
        sys.stdout.flush()
        respond(rid, stdout="quiet")
        continue
    if code == "FLOOD":
        sys.stderr.write("x" * 200_000)
        sys.stderr.flush()
        respond(rid, stdout="flooded")
        continue
    if code == "COUNT":
        counter += 1
        respond(rid, stdout=str(counter))
        continue
    if code == "SLEEP":
        try:
            time.sleep(30)
            respond(rid, stdout="woke")
        except KeyboardInterrupt:
            respond(rid, error="Interrupted", interrupted=True)
        continue
    if code.startswith("ENV:"):
        respond(rid, stdout=os.environ.get(code[4:], "<missing>"))
        continue
    respond(rid, stdout=f"ran:{code}")
