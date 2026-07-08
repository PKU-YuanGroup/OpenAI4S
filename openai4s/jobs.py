"""Local compute-jobs manager.

Runs a shell command or Python snippet as a tracked background subprocess so the
UI (Customize → Compute → Jobs) can submit long-running work, watch its status
and output, and cancel it — the local-machine analogue of the reference daemon's
remote compute/jobs. Jobs run in a per-job workspace under the data dir.

Kept intentionally simple + stdlib-only: threads + subprocess.Popen, in-memory
registry (bounded), live output capture.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_MAX_OUTPUT = 200_000  # per-job captured output cap (bytes)
_MAX_JOBS = 200  # registry cap (oldest finished pruned)


class Job:
    def __init__(self, kind: str, command: str, cwd: str) -> None:
        self.id = "job-" + uuid.uuid4().hex[:12]
        self.kind = kind  # "bash" | "python"
        self.command = command
        self.cwd = cwd
        self.status = "queued"  # queued|running|done|failed|cancelled
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.exit_code: int | None = None
        self._out: list[str] = []
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def append(self, text: str) -> None:
        with self._lock:
            self._out.append(text)
            # keep bounded
            total = sum(len(x) for x in self._out)
            while total > _MAX_OUTPUT and len(self._out) > 1:
                total -= len(self._out.pop(0))
            # a single line larger than the cap must still be truncated, or the
            # per-job memory bound is defeated by one giant no-newline blob
            if total > _MAX_OUTPUT and len(self._out) == 1:
                self._out[0] = self._out[0][-_MAX_OUTPUT:]

    def output(self) -> str:
        with self._lock:
            return "".join(self._out)

    def to_dict(self, *, with_output: bool = False) -> dict:
        d = {
            "id": self.id,
            "kind": self.kind,
            "command": self.command,
            "status": self.status,
            "exit_code": self.exit_code,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": (
                round((self.finished_at or time.time()) - self.started_at, 1)
                if self.started_at
                else None
            ),
        }
        if with_output:
            d["output"] = self.output()
        return d


class JobManager:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def submit(self, command: str, kind: str = "bash", cwd: str | None = None) -> dict:
        command = (command or "").strip()
        if not command:
            return {"error": "empty command"}
        kind = kind if kind in ("bash", "python") else "bash"
        # Confine the working directory to the jobs root: normalize a caller-supplied
        # cwd and require it to share the root as a common path prefix, so it cannot
        # escape via ".." traversal or an absolute path (no path injection).
        base = os.path.realpath(str(self.root))
        if cwd:
            wd = os.path.normpath(os.path.join(base, cwd))
            if os.path.commonpath((base, wd)) != base:
                return {"error": "cwd escapes the jobs root"}
        else:
            wd = base
        Path(wd).mkdir(parents=True, exist_ok=True)
        job = Job(kind, command, wd)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._prune_locked()
        threading.Thread(
            target=self._run, args=(job,), daemon=True, name=f"os-job-{job.id}"
        ).start()
        return job.to_dict()

    def _run(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        if job.kind == "python":
            argv = [sys.executable, "-u", "-c", job.command]
        else:
            argv = ["bash", "-lc", job.command]
        try:
            proc = subprocess.Popen(
                argv,
                cwd=job.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            job._proc = proc
            assert proc.stdout is not None
            for line in proc.stdout:
                job.append(line)
            proc.wait()
            job.exit_code = proc.returncode
            # only claim done/failed if cancel() didn't already win the race
            with job._lock:
                if job.status != "cancelled":
                    job.status = "done" if proc.returncode == 0 else "failed"
        except Exception as e:  # noqa: BLE001
            job.append(f"\n[job error] {e}\n")
            with job._lock:
                if job.status != "cancelled":
                    job.status = "failed"
            job.exit_code = -1
        finally:
            job.finished_at = time.time()

    def cancel(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "job not found"}
        with job._lock:  # atomic with _run's terminal-status write
            if job.status in ("done", "failed", "cancelled"):
                return {"ok": True, "status": job.status}
            job.status = "cancelled"
        if job._proc:
            try:
                job._proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        return {"ok": True, "status": "cancelled"}

    def list(self) -> list[dict]:
        with self._lock:
            ids = list(reversed(self._order))
        return [self._jobs[i].to_dict() for i in ids if i in self._jobs]

    def get(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if not job:
            return {"error": "job not found"}
        return job.to_dict(with_output=True)

    def _prune_locked(self) -> None:
        while len(self._order) > _MAX_JOBS:
            old = self._order.pop(0)
            j = self._jobs.get(old)
            if j and j.status in ("done", "failed", "cancelled"):
                self._jobs.pop(old, None)
            elif j:  # still running — keep it, drop from prune scan
                self._order.append(old)
                break
