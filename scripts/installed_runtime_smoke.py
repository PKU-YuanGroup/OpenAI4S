#!/usr/bin/env python3
"""Prove the *installed* runtime can run a science workflow, not just import.

The offline suite runs the source checkout, ``release_import_smoke.py`` proves
the installed package imports, and ``release_pipeline.step_smoke`` loads the
installed Web shell. None of them executes a Cell. A packaging fault that only
appears when the daemon spawns its kernel — a missing worker module, a
`site-packages` layout the interpreter cannot relocate into, a data directory
the daemon cannot write — passes all three and fails the first time a user runs
code. This probe closes that gap with the smallest real chain:

    boot the packaged daemon → POST two Python Cells to the production
    REPL route → the second Cell uses the first Cell's variable and writes a
    CSV → the capture path registers an immutable Artifact version → stop and
    wait for every process to exit → reopen the same data directory and read
    the same version bytes, digest and execution history back.

Two facts are deliberately *not* claimed. In-memory variables do not survive a
restart; only the durable Cell history and Artifact bytes are read back and
compared. And a green probe says the bytes execute, not that the science is
right — it runs one deterministic addition and one deterministic file write.

The probe is stdlib-only and runs against whichever runtime it is pointed at:
an unpacked desktop bundle (``--bundle``), the venv an installed wheel lives in
(``--python`` from the wheel's environment), or a source checkout served by a
prepared interpreter (``--python`` plus ``--source-root``). It never imports
``openai4s`` itself, so the host interpreter running this file cannot be
mistaken for the runtime under test, and it starts the daemon with a minimal
environment rather than the caller's: no model credentials, no inherited
``PYTHONPATH``, an isolated data directory and a random loopback port.

The machine-readable report has a fixed shape because release receipts consume
it (``validate_smoke_report``): package SHA-256, frozen source SHA, platform
and architecture, the module origin the runtime actually resolved, one result
per step, and a bounded diagnostic list. A bundle whose architecture this host
cannot execute is recorded as ``not_run`` with the missing run named — never as
a pass. Exit codes: 0 passed, 1 failed, 2 usage error, 3 not_run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

SMOKE_FORMAT = "openai4s-installed-runtime-smoke"
#: Bumped when the report shape changes in a way an older verifier must refuse.
SMOKE_SCHEMA_VERSION = 1
#: Bumped when the workflow this probe runs changes (cells, readback, limits).
SMOKE_SCENARIO_VERSION = 1

PASSED = "passed"
FAILED = "failed"
NOT_RUN = "not_run"

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_RUN = 3

#: Steps every passing report must carry. Named so a receipt can say which one
#: is missing instead of "the smoke is incomplete".
SMOKE_STEPS = ("boot", "execution", "capture", "reopen", "cleanup")

#: The two Cells. The second one fails with ``NameError`` unless the kernel
#: state from the first one is still alive, and its CSV contents encode the
#: added value, so the bytes read back later prove *this* execution produced
#: them rather than any pre-existing file.
CELL_ONE_CODE = (
    "shared_value = 41 + 1\nprint(f'cell-one shared_value={shared_value}')\n"
)
CELL_TWO_CODE = (
    "from pathlib import Path\n"
    "shared_value += 1\n"
    'Path("smoke.csv").write_text('
    '"shared,value\\nanswer," + str(shared_value) + "\\n", encoding="utf-8")\n'
    "print(f'cell-two shared_value={shared_value}')\n"
)
CELL_ONE_STDOUT = "cell-one shared_value=42"
CELL_TWO_STDOUT = "cell-two shared_value=43"
CSV_FILENAME = "smoke.csv"
EXPECTED_CSV_BYTES = b"shared,value\nanswer,43\n"

#: Per-phase budgets, in seconds. R6 proposed these and the release workflow
#: runs the probe inside the same window, so the total is authoritative: every
#: step takes ``min(its budget, time left)``.
STARTUP_TIMEOUT_S = 90.0
CELL_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 30.0
HTTP_TIMEOUT_S = 30.0
TOTAL_TIMEOUT_S = 420.0
#: How long to wait for kernel workers to leave after the daemon exits.
GRACE_EXIT_S = 15.0
#: Upper bound on any single diagnostic string in the report. A failure quotes
#: the daemon; it must not quote a whole traceback or a token.
DIAGNOSTIC_LIMIT = 4000

_TOKEN_FILENAME = "access-token"
_TOKEN_HEADER = "X-OpenAI4S-Token"
_BUNDLE_DIRNAME = re.compile(
    r"^OpenAI4S-(?P<version>[^-]+)-linux-(?P<arch>x86_64|aarch64)$"
)
_ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SmokeError(Exception):
    """The runtime did not prove the science chain. Do not release these bytes."""


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def bounded(text: str, limit: int = DIAGNOSTIC_LIMIT) -> str:
    """A diagnostic may quote a failure; it may not become a log file."""
    text = str(text)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… ({len(text) - limit} more characters)"


def redact_token(text: str, token: str) -> str:
    """Never let the daemon credential reach a release log or receipt.

    The token arrives through a header, but a daemon that echoes the bootstrap
    URL into its own stdout would otherwise be quoted verbatim by a failure
    message. Both the literal value and a ``token=`` query are replaced, so
    this holds even before the token file has been read.
    """
    text = str(text)
    if token:
        text = text.replace(token, "<redacted-token>")
    text = re.sub(r"(?i)(x-openai4s-token:\s*)\S+", r"\1<redacted-token>", text)
    return re.sub(r"(?i)(token=)[^&\s\"']+", r"\1<redacted-token>", text)


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def normalized_arch(machine: str) -> str:
    lowered = (machine or "").lower()
    return _ARCH_ALIASES.get(lowered, lowered)


def package_arch_from_name(name: str) -> str:
    """The architecture a bundle directory name promises to execute on."""
    matched = _BUNDLE_DIRNAME.match(name)
    if not matched:
        raise SmokeError(
            f"bundle directory {name!r} is not OpenAI4S-<version>-linux-<arch>"
        )
    return matched.group("arch")


def host_can_execute(
    package_os: str,
    package_arch: str,
    *,
    host_os: str | None = None,
    host_arch: str | None = None,
) -> bool:
    """Whether this host can really run the package's binaries.

    Cross-built bundles are verified statically elsewhere; claiming this probe
    passed on a host that can only read the archive would be exactly the
    "static check dressed as a run" the release rules forbid.
    """
    host_os = (host_os or platform.system()).lower()
    host_arch = normalized_arch(host_arch or platform.machine())
    return host_os.startswith(package_os.lower()) and host_arch == package_arch


def clean_environment(
    *,
    data_dir: Path,
    host: str,
    port: int,
    home: Path,
    temp_dir: Path,
    pythonpath: Path | None = None,
) -> dict[str, str]:
    """The daemon's whole environment, built rather than inherited.

    Starting the child from ``os.environ`` would let the release machine's
    ``OPENAI4S_LLM_*`` keys reach a probe that has no business holding them,
    and an inherited ``PYTHONPATH`` would let a source checkout satisfy an
    import the packaged runtime is missing — the one failure this file exists
    to catch.
    """
    env = {
        "PATH": os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temp_dir),
        "LANG": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        # The product's own isolation knobs. `plaintext` keeps a macOS keychain
        # prompt (or a CI secret store) out of a probe that stores no secrets,
        # and the deny posture is stricter than anything the probe needs.
        "OPENAI4S_DATA_DIR": str(data_dir),
        "OPENAI4S_HOST": host,
        "OPENAI4S_PORT": str(port),
        "OPENAI4S_NOTEBOOK_REPL": "1",
        "OPENAI4S_UNATTENDED_APPROVAL": "deny",
        "OPENAI4S_SECRET_STORE": "plaintext",
        "MPLBACKEND": "Agg",
    }
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    return env


# --------------------------------------------------------------------------- #
# The runtime under test
# --------------------------------------------------------------------------- #
class Runtime:
    """How to invoke the runtime under test, and where its code must live."""

    def __init__(
        self,
        *,
        kind: str,
        name: str,
        interpreter: Path,
        package_root: Path,
        source_root: Path | None,
        isolated: bool,
        package_sha256: str,
        source_sha: str,
    ) -> None:
        self.kind = kind
        self.name = name
        self.interpreter = Path(interpreter)
        self.package_root = Path(package_root)
        self.source_root = Path(source_root) if source_root else None
        self.isolated = isolated
        self.package_sha256 = package_sha256
        self.source_sha = source_sha

    def cli(self, *arguments: str) -> list[str]:
        # `-I` for an installed venv (ignore PYTHONPATH and the user site);
        # `-P` for a source tree or a bundle (honour the trusted PYTHONPATH but
        # never prepend the cwd, where a stray `openai4s.py` could shadow it).
        flag = "-I" if self.isolated else "-P"
        return [str(self.interpreter), flag, "-m", "openai4s", *arguments]

    def python(self, code: str) -> list[str]:
        flag = "-I" if self.isolated else "-P"
        return [str(self.interpreter), flag, "-c", code]

    def child_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        env = dict(base)
        if self.source_root is not None:
            env["PYTHONPATH"] = str(self.source_root)
        return env


def unpack_bundle(target: Path, work_root: Path) -> Path:
    """Return the app directory for an unpacked bundle or a ``.tar.gz``."""
    if target.is_dir():
        return target
    if not tarfile.is_tarfile(target):
        raise SmokeError(f"not a tar archive: {target.name}")
    unpacked = work_root / "unpacked"
    unpacked.mkdir()
    with tarfile.open(target, "r:gz") as archive:
        # `data` refuses absolute paths, parent escapes and escaping links:
        # verification must not be the step that writes outside its temp dir.
        archive.extractall(unpacked, filter="data")
    roots = [path for path in unpacked.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise SmokeError(
            f"expected exactly one top-level directory in the archive, found {len(roots)}"
        )
    return roots[0]


def runtime_for_bundle_app(
    app: Path,
    *,
    package_name: str,
    package_sha256: str,
    source_sha: str,
) -> Runtime:
    """The unpacked desktop bundle: embedded interpreter plus loose source."""
    runtime = app / "runtime" / "bin" / "python3"
    src = app / "src"
    for path, label in (
        (runtime, "embedded interpreter"),
        (src / "openai4s", "source tree"),
    ):
        if not path.exists():
            raise SmokeError(f"bundle is missing its {label}: {path}")
    return Runtime(
        kind="bundle",
        name=package_name,
        interpreter=runtime,
        package_root=app,
        source_root=src,
        isolated=False,
        package_sha256=package_sha256,
        source_sha=source_sha,
    )


def runtime_for_python(
    interpreter: Path,
    *,
    source_root: Path | None,
    package: Path | None,
    source_sha: str,
) -> Runtime:
    """An installed wheel's venv (no source root) or a prepared source venv."""
    if not interpreter.is_file():
        raise SmokeError(f"runtime interpreter does not exist: {interpreter}")
    if package is not None:
        name = package.name
    elif source_root is not None:
        name = Path(source_root).name
    else:
        name = interpreter.name
    package_root = (
        Path(source_root) if source_root is not None else interpreter.parent.parent
    )
    return Runtime(
        kind="source" if source_root is not None else "wheel",
        name=name,
        interpreter=interpreter,
        package_root=package_root,
        source_root=source_root,
        isolated=source_root is None,
        package_sha256=(
            sha256_file(package) if package is not None and package.is_file() else ""
        ),
        source_sha=source_sha,
    )


def probe_runtime(runtime: Runtime, *, cwd: Path, env: Mapping[str, str]) -> dict:
    """Ask the runtime itself where its interpreter and code came from.

    This is the anti-substitution check: an installed layout that silently
    imports the source checkout, or a bundle whose interpreter did not
    relocate, answers with paths outside the tree being verified.
    """
    code = (
        "import json, sys\n"
        "import openai4s\n"
        "print(json.dumps({'executable': sys.executable, 'prefix': sys.prefix,"
        " 'python': sys.version.split()[0], 'version': openai4s.__version__,"
        " 'module_file': openai4s.__file__}))\n"
    )
    try:
        result = subprocess.run(
            runtime.python(code),
            cwd=str(cwd),
            env=dict(env),
            capture_output=True,
            text=True,
            timeout=STARTUP_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SmokeError(
            f"the runtime interpreter could not run an import probe: {type(error).__name__}"
        ) from None
    if result.returncode != 0:
        raise SmokeError(
            "the runtime interpreter failed to import openai4s: "
            + bounded((result.stderr or result.stdout).strip())
        )
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    try:
        facts = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise SmokeError("the runtime import probe printed no JSON") from None
    module_file = Path(str(facts.get("module_file") or "")).resolve()
    package_root = runtime.package_root.resolve()
    if not module_file.is_file():
        raise SmokeError(
            f"the runtime import probe reported no module file: {module_file}"
        )
    if package_root not in module_file.parents:
        raise SmokeError(
            "openai4s resolved outside the tree under test: "
            f"{module_file} is not under {package_root}"
        )
    executable = Path(str(facts.get("executable") or "")).resolve()
    if executable != runtime.interpreter.resolve():
        raise SmokeError(
            f"the runtime reported interpreter {executable}, not {runtime.interpreter}"
        )
    return {
        "interpreter": str(runtime.interpreter),
        "prefix": str(facts.get("prefix") or ""),
        "interpreter_version": str(facts.get("python") or ""),
        "version": str(facts.get("version") or ""),
        "module_file": str(module_file),
        "inside_package": True,
        "isolation": "-I" if runtime.isolated else "-P",
    }


# --------------------------------------------------------------------------- #
# HTTP client (loopback only, token header, bounded errors)
# --------------------------------------------------------------------------- #
class Api:
    def __init__(self, host: str, port: int, token: str) -> None:
        self.base = f"http://{host}:{port}"
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float = HTTP_TIMEOUT_S,
    ) -> tuple[int, bytes]:
        if not (path.startswith("/api/") or path == "/health"):
            raise SmokeError(f"refusing a request outside the API: {path}")
        data = None
        headers = {_TOKEN_HEADER: self.token, "Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(getattr(response, "status", 0)), response.read()
        except urllib.error.HTTPError as error:
            try:
                detail = error.read(8192)
            except OSError:
                detail = b""
            text = redact_token(detail.decode("utf-8", "replace"), self.token).strip()
            raise SmokeError(
                f"{method} {path} answered HTTP {error.code}: {bounded(text, 800)}"
            ) from None
        except (OSError, urllib.error.URLError) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise SmokeError(
                    f"{method} {path} timed out after {timeout:.0f}s"
                ) from None
            raise SmokeError(
                f"{method} {path} failed: {type(error).__name__}"
            ) from None

    def json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float = HTTP_TIMEOUT_S,
    ) -> Any:
        _status, raw = self.request(method, path, body, timeout=timeout)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SmokeError(f"{method} {path} did not answer JSON") from None

    def download(self, path: str, *, timeout: float = HTTP_TIMEOUT_S) -> bytes:
        status, raw = self.request("GET", path, timeout=timeout)
        if not (200 <= status < 300):
            raise SmokeError(f"GET {path} answered HTTP {status}")
        return raw


# --------------------------------------------------------------------------- #
# Daemon lifecycle
# --------------------------------------------------------------------------- #
class Daemon:
    """One daemon this probe started, in its own process session."""

    def __init__(
        self,
        *,
        runtime: Runtime,
        env: Mapping[str, str],
        cwd: Path,
        log_path: Path,
        port: int,
    ) -> None:
        self.runtime = runtime
        self.env = dict(env)
        self.cwd = cwd
        self.log_path = log_path
        self.port = port
        self.token = ""
        self.proc: subprocess.Popen[bytes] | None = None
        self.pgid = 0
        self.stop_result: dict[str, Any] | None = None

    # -- start / readiness --------------------------------------------------
    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("wb") as log:
            try:
                self.proc = subprocess.Popen(
                    self.runtime.cli("serve", "--no-open"),
                    cwd=str(self.cwd),
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    # Its own session: the probe can wait for and, if needed,
                    # stop exactly this tree without ever signalling a process
                    # it did not create.
                    start_new_session=True,
                )
            except OSError as error:
                raise SmokeError(
                    f"the runtime daemon could not start: {type(error).__name__}"
                ) from None
        self.pgid = self.proc.pid

    def log_tail(self) -> str:
        try:
            raw = self.log_path.read_bytes()[-16384:]
        except OSError:
            return ""
        return bounded(redact_token(raw.decode("utf-8", "replace").strip(), self.token))

    def wait_until_serving(self, timeout: float) -> None:
        if self.proc is None:
            raise SmokeError("daemon was never started")
        deadline = time.monotonic() + timeout
        last = "no response"
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SmokeError(
                    "the runtime daemon exited before serving: " + self.log_tail()
                )
            try:
                status, raw = Api("127.0.0.1", self.port, "").request(
                    "GET", "/health", timeout=2.0
                )
                payload = json.loads(raw.decode("utf-8"))
            except (SmokeError, ValueError) as error:
                last = type(error).__name__
                time.sleep(0.2)
                continue
            if (
                status == 200
                and isinstance(payload, Mapping)
                and payload.get("status") == "ok"
            ):
                return
            last = f"health status {status}"
            time.sleep(0.2)
        raise SmokeError(
            f"the runtime daemon did not answer /health within {timeout:.0f}s ({last})"
        )

    def live(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    # -- stop ---------------------------------------------------------------
    def stop(self, timeout: float) -> dict[str, Any]:
        """Stop through the product's own CLI, then prove the tree is gone.

        The daemon is this probe's own child, so it must be reaped while the
        CLI polls for it — an unreaped process lingers as a zombie that a
        pid-existence check reads as running. Kernel workers share the
        daemon's process group (``start_new_session``), so an empty group is
        the evidence that the worker left too; the check only sends signal 0.
        """
        if self.stop_result is not None:
            return self.stop_result
        result: dict[str, Any] = {
            "attempted": True,
            "daemon_exited": False,
            "workers_exited": False,
            "cli_returncode": None,
            "note": "",
        }
        if self.proc is None:
            result["attempted"] = False
            result["note"] = "no daemon was started"
            self.stop_result = result
            return result
        failure = ""
        with tempfile.TemporaryFile() as output:
            try:
                stopper = subprocess.Popen(
                    self.runtime.cli("stop"),
                    cwd=str(self.cwd),
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            except OSError as error:
                stopper = None
                failure = f"the runtime CLI could not run `openai4s stop`: {error}"
            if stopper is not None:
                deadline = time.monotonic() + timeout
                returncode = stopper.poll()
                while returncode is None and time.monotonic() < deadline:
                    self.proc.poll()  # reap the daemon the moment it exits
                    time.sleep(0.05)
                    returncode = stopper.poll()
                if returncode is None:
                    stopper.kill()
                    stopper.wait()
                    failure = f"`openai4s stop` did not return within {timeout:.0f}s"
                else:
                    result["cli_returncode"] = returncode
                    if returncode != 0:
                        output.seek(0)
                        said = redact_token(
                            output.read().decode("utf-8", "replace").strip(),
                            self.token,
                        )
                        failure = (
                            f"`openai4s stop` exited {returncode}: {bounded(said, 800)}"
                        )
        if self.proc.poll() is None:
            # Proportional to the caller's stop budget, so a test can exercise
            # the escalation path without waiting ten real seconds; a release
            # run keeps the upper bound.
            settle = max(1.0, min(10.0, timeout * 0.5))
            if not failure:
                try:
                    self.proc.wait(timeout=settle)
                except subprocess.TimeoutExpired:
                    failure = "`openai4s stop` exited 0 but the daemon kept running"
            if self.proc.poll() is None:
                # Only this probe's own child. Never a process-group kill and
                # never a pattern match: the probe shares a machine.
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=max(1.0, min(10.0, timeout)))
                except subprocess.TimeoutExpired:  # pragma: no cover - last resort
                    self.proc.kill()
                    self.proc.wait()
        result["daemon_exited"] = self.proc.poll() is not None
        if result["daemon_exited"]:
            result["workers_exited"] = self._wait_for_process_group_exit()
        if not result["daemon_exited"]:
            result["note"] = failure or "the daemon did not exit"
        elif not result["workers_exited"]:
            result["note"] = failure or "a kernel worker outlived the daemon"
        else:
            result["note"] = failure
        self.stop_result = result
        return result

    def _wait_for_process_group_exit(self, grace_s: float | None = None) -> bool:
        if not hasattr(os, "killpg"):  # pragma: no cover - Windows
            return False
        deadline = time.monotonic() + (GRACE_EXIT_S if grace_s is None else grace_s)
        while time.monotonic() < deadline:
            try:
                os.killpg(self.pgid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:  # pragma: no cover - not our group's owner
                return False
            time.sleep(0.1)
        return False


# --------------------------------------------------------------------------- #
# Step verification helpers
# --------------------------------------------------------------------------- #
def cell_facts(payload: Any, *, label: str) -> dict[str, Any]:
    """Validate one Cell response, and never let HTTP success stand for it.

    The REPL route answers 200 for a Cell whose ``status`` is ``error``, and a
    cancelled job answers without a ``cell`` at all. Only ``cell.status ==
    "ok"`` with an empty ``cell.error`` is an executed success.
    """
    if not isinstance(payload, Mapping):
        raise SmokeError(f"{label} returned no result object")
    if payload.get("error"):
        raise SmokeError(f"{label} was refused: {bounded(str(payload.get('error')))}")
    cell = payload.get("cell")
    if not isinstance(cell, Mapping):
        raise SmokeError(
            f"{label} returned no executed Cell (status={payload.get('status')!r})"
        )
    status = str(cell.get("status") or "")
    error = cell.get("error")
    if status != "ok" or error:
        raise SmokeError(
            f"{label} did not execute successfully: cell.status={status!r} "
            f"cell.error={bounded(str(error or ''), 800)!r}"
        )
    return {
        "status": "ok",
        "label": label,
        "execution_id": str(payload.get("execution_id") or ""),
        "cell_status": status,
        "error": str(error or ""),
        "stdout": bounded(str(cell.get("stdout") or "")),
        "generation_id": cell.get("generation_id"),
        "cell_index": cell.get("cell_index"),
        "files_written": [str(name) for name in (cell.get("files_written") or [])],
    }


def entry_for(entries: Sequence[Mapping[str, Any]], marker: str) -> Mapping[str, Any]:
    matches = [entry for entry in entries if marker in str(entry.get("source") or "")]
    if len(matches) != 1:
        raise SmokeError(
            f"execution history has {len(matches)} Cell(s) matching {marker!r}, expected 1"
        )
    entry = matches[0]
    if str(entry.get("status") or "") != "ok" or entry.get("error"):
        raise SmokeError(
            f"the persisted Cell matching {marker!r} is not a success: "
            f"status={entry.get('status')!r} "
            f"error={bounded(str(entry.get('error') or ''), 800)!r}"
        )
    return entry


def same_generation(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool | None:
    """Whether both Cells ran in the same kernel generation, when reported."""
    one, two = first.get("generation_id"), second.get("generation_id")
    if one is None or two is None:
        return None
    return str(one) == str(two)


def cell_chain_facts(cells: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The two Cells must have shared one persistent kernel, not just answered 200.

    Each Cell's own stdout carries the running value: the first writes 42 and
    the second prints 43 only if it read the variable the first created. A
    kernel that lost state between the two raises ``NameError`` and never
    reaches this check, but a Cell that somehow redefined the value does — so
    the arithmetic is asserted, not inferred from ``status``.
    """
    if len(cells) != 2:
        raise SmokeError(
            f"the runtime smoke ran {len(cells)} Cell(s), expected the two-Cell chain"
        )
    for cell, expected in ((cells[0], CELL_ONE_STDOUT), (cells[1], CELL_TWO_STDOUT)):
        if expected not in str(cell.get("stdout") or ""):
            raise SmokeError(
                f"{cell.get('label', 'a Cell')} printed {cell.get('stdout')!r}, "
                f"expected {expected!r}; the persistent kernel did not carry the "
                "variable across Cells"
            )
    return {
        "status": "ok",
        "cells": [dict(cell) for cell in cells],
        # The second Cell executed without a ``NameError`` and printed the
        # incremented value; the generation is recorded beside that proof.
        "shared_state": True,
        "same_kernel_generation": same_generation(cells[0], cells[1]),
    }


def read_capture(
    api: Api, frame_id: str, entries: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """The CSV must have been captured through the production chain.

    Not an inserted Artifact row: the Cell's own execution-log entry names the
    version the capture path registered, and that version's metadata is then
    read back and its bytes downloaded.
    """
    entry = entry_for(entries, "cell-two")
    bindings = [
        row
        for row in (entry.get("output_artifacts") or [])
        if isinstance(row, Mapping) and row.get("filename") == CSV_FILENAME
    ]
    if len(bindings) != 1:
        raise SmokeError(
            f"the CSV Cell captured {len(bindings)} `{CSV_FILENAME}` version(s), "
            f"expected 1 (files_written={entry.get('files_written')!r})"
        )
    artifact_id = str(bindings[0].get("artifact_id") or "")
    version_id = str(bindings[0].get("version_id") or "")
    path = str(bindings[0].get("url") or "")
    if not artifact_id or not version_id or not path.startswith("/api/"):
        raise SmokeError("the captured CSV row carries no exact-version link")
    listed = api.json("GET", f"/api/v1/artifacts/{artifact_id}/versions")
    versions = listed.get("versions") if isinstance(listed, Mapping) else None
    version = next(
        (
            row
            for row in (versions or [])
            if isinstance(row, Mapping) and row.get("version_id") == version_id
        ),
        None,
    )
    if version is None:
        raise SmokeError("the captured CSV version is not listed by its Artifact")
    recorded = str(version.get("checksum") or "").lower()
    if not _SHA256.match(recorded):
        raise SmokeError("the captured CSV version records no SHA-256 checksum")
    body = api.download(path)
    actual = sha256_bytes(body)
    if actual != recorded:
        raise SmokeError(
            f"{CSV_FILENAME} bytes do not match the version checksum "
            f"({actual[:12]} != {recorded[:12]})"
        )
    if body != EXPECTED_CSV_BYTES:
        raise SmokeError(
            f"{CSV_FILENAME} does not contain the deterministic science result; "
            f"got {bounded(body.decode('utf-8', 'replace'), 400)!r}"
        )
    if int(version.get("size_bytes") or -1) != len(body):
        raise SmokeError(f"{CSV_FILENAME} size does not match the version metadata")
    return {
        "status": "ok",
        "artifact_id": artifact_id,
        "version_id": version_id,
        "filename": CSV_FILENAME,
        "sha256": actual,
        "size_bytes": len(body),
        "version_url": path,
        "bytes_match_metadata": True,
        "producing_cell_id": str(entry.get("producing_cell_id") or ""),
        "cell_ids": sorted(
            str(item.get("producing_cell_id") or "") for item in entries
        ),
    }


def read_back(
    api: Api,
    frame_id: str,
    capture: Mapping[str, Any],
) -> dict[str, Any]:
    """Read the same version and history from a freshly started daemon."""
    log = api.json("GET", f"/api/v1/frames/{frame_id}/execution-log")
    entries = log.get("entries") if isinstance(log, Mapping) else None
    if not isinstance(entries, list) or len(entries) != 2:
        raise SmokeError(
            f"the reopened daemon reports {len(entries or [])} history entries, expected 2"
        )
    cell_ids = sorted(str(entry.get("producing_cell_id") or "") for entry in entries)
    if cell_ids != [str(item) for item in capture.get("cell_ids") or []]:
        raise SmokeError(
            "the reopened daemon's Cell identities differ from the first run"
        )
    for entry in entries:
        if str(entry.get("status") or "") != "ok" or entry.get("error"):
            raise SmokeError("the reopened daemon reports a Cell that is not a success")
    artifact_id = str(capture["artifact_id"])
    version_id = str(capture["version_id"])
    listed = api.json("GET", f"/api/v1/artifacts/{artifact_id}/versions")
    versions = listed.get("versions") if isinstance(listed, Mapping) else None
    version = next(
        (
            row
            for row in (versions or [])
            if isinstance(row, Mapping) and row.get("version_id") == version_id
        ),
        None,
    )
    if version is None:
        raise SmokeError(
            "the exact CSV version disappeared from the reopened data directory"
        )
    body = api.download(str(capture["version_url"]))
    digest = sha256_bytes(body)
    if body != EXPECTED_CSV_BYTES or digest != str(capture["sha256"]):
        raise SmokeError(
            "the reopened daemon serves different bytes for the same Artifact version"
        )
    artifacts = api.json("GET", f"/api/v1/frames/{frame_id}/artifacts")
    latest = next(
        (
            row
            for row in (artifacts if isinstance(artifacts, list) else [])
            if isinstance(row, Mapping)
            and str(row.get("artifact_id") or row.get("id") or "") == artifact_id
        ),
        None,
    )
    if latest is None or str(latest.get("version_id") or "") != version_id:
        raise SmokeError(
            "the reopened daemon does not report the captured version as latest"
        )
    return {
        "status": "ok",
        "version_id": version_id,
        "bytes_identical": True,
        "digest_identical": True,
        "sha256": digest,
        "history_entries": len(entries),
        "same_cell_ids": True,
        "version_still_latest": True,
    }


def cleanup_step(stops: Mapping[str, Any], *, daemons_started: int) -> dict[str, Any]:
    """Both shutdowns, each required to have really happened."""

    def one(key: str) -> dict[str, Any]:
        result = stops.get(key)
        if not isinstance(result, Mapping):
            return {
                "attempted": False,
                "daemon_exited": False,
                "workers_exited": False,
                "cli_returncode": None,
                "note": "shutdown was never reached",
            }
        return {
            "attempted": bool(result.get("attempted")),
            "daemon_exited": bool(result.get("daemon_exited")),
            "workers_exited": bool(result.get("workers_exited")),
            "cli_returncode": result.get("cli_returncode"),
            "note": bounded(str(result.get("note") or ""), 1000),
        }

    first = one("first")
    final = one("final")
    ok = all(
        bool(result["attempted"])
        and bool(result["daemon_exited"])
        and bool(result["workers_exited"])
        for result in (first, final)
    )
    return {
        "status": "ok" if ok else "failed",
        "daemons_started": daemons_started,
        "first": first,
        "final": final,
    }


# --------------------------------------------------------------------------- #
# Report construction and validation
# --------------------------------------------------------------------------- #
def build_report(
    *,
    package: Mapping[str, Any],
    platform_facts: Mapping[str, Any],
    runtime_facts: Mapping[str, Any],
    steps: Mapping[str, Any],
    diagnostics: Sequence[str],
    elapsed_s: float,
    budgets: Mapping[str, float],
) -> dict[str, Any]:
    """Assemble the machine-readable report, deriving the verdict from steps."""
    status = FAILED
    if all(
        isinstance(steps.get(name), Mapping) and steps[name].get("status") == "ok"
        for name in SMOKE_STEPS
    ):
        status = PASSED
    return {
        "format": SMOKE_FORMAT,
        "schema_version": SMOKE_SCHEMA_VERSION,
        "scenario_version": SMOKE_SCENARIO_VERSION,
        "status": status,
        "package": {
            "kind": str(package.get("kind") or ""),
            "name": str(package.get("name") or ""),
            "sha256": str(package.get("sha256") or ""),
            "source_sha": str(package.get("source_sha") or ""),
        },
        "platform": {
            "os": str(platform_facts.get("os") or ""),
            "arch": str(platform_facts.get("arch") or ""),
            "host_os": str(platform_facts.get("host_os") or ""),
            "host_arch": str(platform_facts.get("host_arch") or ""),
            "executable_here": bool(platform_facts.get("executable_here")),
            "python": str(platform_facts.get("python") or ""),
        },
        "runtime": dict(runtime_facts),
        "steps": {name: dict(steps.get(name) or {}) for name in SMOKE_STEPS},
        "diagnostics": [bounded(str(item)) for item in diagnostics][-8:],
        "timing": {
            "elapsed_s": round(float(elapsed_s), 3),
            "budgets_s": {key: float(value) for key, value in budgets.items()},
        },
    }


def build_not_run_report(
    *,
    package: Mapping[str, Any],
    platform_facts: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    """A package this host cannot execute is not a pass and not a failure.

    The reason names the missing run ("linux-x86_64 on darwin/arm64"), which is
    what a release record needs; an empty report would read as "nothing to do".
    """
    return {
        "format": SMOKE_FORMAT,
        "schema_version": SMOKE_SCHEMA_VERSION,
        "scenario_version": SMOKE_SCENARIO_VERSION,
        "status": NOT_RUN,
        "not_run_reason": bounded(str(reason)),
        "package": {
            "kind": str(package.get("kind") or ""),
            "name": str(package.get("name") or ""),
            "sha256": str(package.get("sha256") or ""),
            "source_sha": str(package.get("source_sha") or ""),
        },
        "platform": {
            "os": str(platform_facts.get("os") or ""),
            "arch": str(platform_facts.get("arch") or ""),
            "host_os": str(platform_facts.get("host_os") or ""),
            "host_arch": str(platform_facts.get("host_arch") or ""),
            "executable_here": False,
        },
        "runtime": {},
        "steps": {},
        "diagnostics": [],
        "timing": {},
    }


def validate_smoke_report(
    document: Any,
    *,
    expected_package_sha256: str = "",
    expected_source_sha: str = "",
    expected_kind: str = "",
    expected_platform: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """Refuse a runtime-smoke record that does not prove the real chain.

    Consumed by the release receipts: a receipt may quote the probe, but only
    this function decides whether the quote is evidence. Every required fact is
    checked explicitly — missing step, skipped step, timeout, a non-executed
    Cell, an uncaptured or mis-digested Artifact, a reopened run that serves
    different bytes, a shutdown that left a daemon or worker, an older schema
    or scenario, and a report from another platform are each a refusal with a
    message an operator can act on.
    """
    if not isinstance(document, Mapping):
        raise SmokeError("runtime smoke report is not an object")
    if document.get("format") != SMOKE_FORMAT:
        raise SmokeError("runtime smoke report has an unrecognised format")
    if document.get("schema_version") != SMOKE_SCHEMA_VERSION:
        raise SmokeError(
            "runtime smoke report has schema_version "
            f"{document.get('schema_version')!r}, this gate requires {SMOKE_SCHEMA_VERSION}"
        )
    if document.get("scenario_version") != SMOKE_SCENARIO_VERSION:
        raise SmokeError(
            "runtime smoke report has scenario_version "
            f"{document.get('scenario_version')!r}, this gate requires "
            f"{SMOKE_SCENARIO_VERSION}; rebuild the package and rerun the probe"
        )
    status = str(document.get("status") or "")
    if status == NOT_RUN:
        raise SmokeError(
            "runtime smoke is not_run: "
            + str(document.get("not_run_reason") or "no reason was recorded")
        )
    if status != PASSED:
        raise SmokeError(f"runtime smoke status is {status!r}, not {PASSED!r}")

    package = document.get("package")
    if not isinstance(package, Mapping):
        raise SmokeError("runtime smoke report records no package identity")
    if expected_kind and str(package.get("kind") or "") != expected_kind:
        raise SmokeError(
            f"runtime smoke was run against a {package.get('kind')!r} runtime, not "
            f"{expected_kind!r}"
        )
    recorded_package_sha = str(package.get("sha256") or "").lower()
    if expected_package_sha256:
        if not _SHA256.match(recorded_package_sha):
            raise SmokeError("runtime smoke report records no package SHA-256")
        if recorded_package_sha != str(expected_package_sha256).lower():
            raise SmokeError(
                "runtime smoke is bound to package "
                f"{recorded_package_sha[:12]}, not the artifact being released "
                f"({str(expected_package_sha256).lower()[:12]})"
            )
    recorded_source_sha = str(package.get("source_sha") or "")
    if expected_source_sha and recorded_source_sha != expected_source_sha:
        raise SmokeError(
            "runtime smoke is bound to source "
            f"{recorded_source_sha[:12] or '<none>'}, not the frozen "
            f"{str(expected_source_sha)[:12]}"
        )

    platform_facts = document.get("platform")
    if not isinstance(platform_facts, Mapping):
        raise SmokeError("runtime smoke report records no platform")
    recorded_platform = (
        str(platform_facts.get("os") or "").lower(),
        str(platform_facts.get("arch") or "").lower(),
    )
    if not all(recorded_platform):
        raise SmokeError("runtime smoke report records no OS/architecture")
    if expected_platform is not None:
        expected = (
            str(expected_platform[0]).lower(),
            str(expected_platform[1]).lower(),
        )
        if recorded_platform != expected:
            raise SmokeError(
                f"runtime smoke ran on {recorded_platform[0]}/{recorded_platform[1]}, "
                f"not {expected[0]}/{expected[1]}"
            )

    runtime = document.get("runtime")
    if not isinstance(runtime, Mapping):
        raise SmokeError("runtime smoke report records no runtime origin")
    if runtime.get("inside_package") is not True:
        raise SmokeError(
            "runtime smoke did not prove that openai4s imported from the tree under "
            f"test (module_file={runtime.get('module_file')!r})"
        )
    if not str(runtime.get("interpreter") or "") or not str(
        runtime.get("module_file") or ""
    ):
        raise SmokeError("runtime smoke report records no interpreter or module path")

    steps = document.get("steps")
    if not isinstance(steps, Mapping):
        raise SmokeError("runtime smoke report records no steps")
    for name in SMOKE_STEPS:
        step = steps.get(name)
        if not isinstance(step, Mapping):
            raise SmokeError(f"runtime smoke report is missing its {name} step")
        if str(step.get("status") or "") != "ok":
            raise SmokeError(
                f"runtime smoke step {name} is {step.get('status')!r}, not a pass"
            )

    execution = steps["execution"]
    cells = execution.get("cells")
    if not isinstance(cells, list) or len(cells) != 2:
        raise SmokeError(
            f"runtime smoke ran {len(cells or [])} Cell(s), expected the two-Cell chain"
        )
    for index, cell in enumerate(cells):
        if not isinstance(cell, Mapping):
            raise SmokeError("runtime smoke report has a malformed Cell row")
        if str(cell.get("status") or "") != "ok" or cell.get("error"):
            raise SmokeError(
                f"runtime smoke Cell {index + 1} did not execute successfully"
            )
    if execution.get("shared_state") is not True:
        raise SmokeError(
            "the runtime smoke Cells did not share one kernel state; the second Cell "
            "could not have used the first Cell's variable"
        )

    capture = steps["capture"]
    for field in ("artifact_id", "version_id", "filename"):
        if not str(capture.get(field) or ""):
            raise SmokeError(f"runtime smoke captured no {field}")
    if not _SHA256.match(str(capture.get("sha256") or "").lower()):
        raise SmokeError("runtime smoke records no captured Artifact SHA-256")
    if capture.get("bytes_match_metadata") is not True:
        raise SmokeError(
            "runtime smoke's captured bytes do not match the version metadata"
        )
    if int(capture.get("size_bytes") or 0) <= 0:
        raise SmokeError("runtime smoke captured an empty Artifact")
    if str(capture.get("filename") or "") != CSV_FILENAME:
        raise SmokeError(
            f"runtime smoke captured {capture.get('filename')!r}, not {CSV_FILENAME!r}"
        )

    reopen = steps["reopen"]
    if str(reopen.get("version_id") or "") != str(capture.get("version_id") or ""):
        raise SmokeError(
            "the reopened runtime served a different Artifact version than the one captured"
        )
    for field in ("bytes_identical", "digest_identical", "version_still_latest"):
        if reopen.get(field) is not True:
            raise SmokeError(
                f"runtime smoke reopen did not prove {field.replace('_', ' ')}"
            )
    if int(reopen.get("history_entries") or 0) != 2:
        raise SmokeError(
            "the reopened runtime did not return both persisted execution-history entries"
        )

    cleanup = steps["cleanup"]
    for key in ("first", "final"):
        shutdown = cleanup.get(key)
        if not isinstance(shutdown, Mapping):
            raise SmokeError(f"runtime smoke cleanup records no {key} shutdown")
        if shutdown.get("daemon_exited") is not True:
            raise SmokeError(
                f"the runtime smoke daemon from the {key} run did not exit; the probe "
                "leaked a process"
            )
        if shutdown.get("workers_exited") is not True:
            raise SmokeError(f"a kernel worker from the {key} run outlived its daemon")
    return dict(document)


# --------------------------------------------------------------------------- #
# The probe
# --------------------------------------------------------------------------- #
class Probe:
    def __init__(self, options: argparse.Namespace) -> None:
        self.options = options
        self.started = time.monotonic()
        self.deadline = self.started + float(options.total_timeout)
        self.steps: dict[str, Any] = {}
        self.diagnostics: list[str] = []
        self.daemons: list[Daemon] = []
        self.stops: dict[str, Any] = {}
        self.runtime: Runtime | None = None
        self.runtime_facts: dict[str, Any] = {}
        self.package: dict[str, Any] = {}
        self.platform_facts: dict[str, Any] = {}
        self.work_root = Path(
            tempfile.mkdtemp(prefix="openai4s runtime smoke ")
        ).resolve()
        self.keep = bool(options.keep_work_dir)
        self.not_run = False

    # -- budgets -----------------------------------------------------------
    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def budget(self, step_s: float) -> float:
        left = self.remaining()
        if left <= 0:
            raise SmokeError(
                f"the overall {float(self.options.total_timeout):.0f}s probe "
                "deadline expired"
            )
        return max(0.5, min(float(step_s), left))

    # -- environment -------------------------------------------------------
    def environment(self, *, data_dir: Path, port: int) -> dict[str, str]:
        assert self.runtime is not None
        for path in (self.work_root / "home", self.work_root / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        return self.runtime.child_environment(
            clean_environment(
                data_dir=data_dir,
                host="127.0.0.1",
                port=port,
                home=self.work_root / "home",
                temp_dir=self.work_root / "tmp",
                pythonpath=self.runtime.source_root,
            )
        )

    # -- steps -------------------------------------------------------------
    def step_boot(self) -> tuple[dict[str, Any], dict[str, Any]]:
        assert self.runtime is not None
        data_dir = self.work_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        port = free_loopback_port()
        env = self.environment(data_dir=data_dir, port=port)
        daemon = Daemon(
            runtime=self.runtime,
            env=env,
            cwd=self.work_root,
            log_path=self.work_root / "logs" / "daemon-first.log",
            port=port,
        )
        self.daemons.append(daemon)
        daemon.start()
        # Read the minted credential before readiness: if the daemon dies on
        # startup, its own log must be quoted redacted rather than verbatim.
        daemon.token = self._read_token(data_dir, daemon)
        daemon.wait_until_serving(self.budget(STARTUP_TIMEOUT_S))
        api = Api("127.0.0.1", port, daemon.token)
        project = api.json(
            "POST", "/api/v1/projects", {"name": "installed runtime smoke"}
        )
        project_id = str(
            (project or {}).get("project_id") or (project or {}).get("id") or ""
        )
        if not project_id:
            raise SmokeError("the runtime refused to create a project")
        frame = api.json("POST", "/api/v1/frames", {"project_id": project_id})
        frame_id = str((frame or {}).get("id") or (frame or {}).get("frame_id") or "")
        if not frame_id:
            raise SmokeError("the runtime refused to create a session")
        return (
            {
                "status": "ok",
                "project_id": project_id,
                "frame_id": frame_id,
                "port": port,
                "data_dir": str(data_dir),
                "notebook_repl": True,
            },
            {
                "api": api,
                "daemon": daemon,
                "data_dir": data_dir,
                "frame_id": frame_id,
                "port": port,
            },
        )

    def _read_token(self, data_dir: Path, daemon: Daemon) -> str:
        token_file = data_dir / _TOKEN_FILENAME
        deadline = time.monotonic() + min(20.0, max(1.0, self.remaining()))
        while time.monotonic() < deadline:
            try:
                token = token_file.read_text("utf-8").strip()
            except OSError:
                token = ""
            if token:
                return token
            if daemon.live() is False:
                raise SmokeError(
                    "the runtime daemon exited before minting its access token: "
                    + daemon.log_tail()
                )
            time.sleep(0.1)
        raise SmokeError(
            "the runtime daemon minted no access token; refusing to call an "
            "unauthenticated API"
        )

    def step_execution(self, state: Mapping[str, Any]) -> dict[str, Any]:
        api: Api = state["api"]
        frame_id = str(state["frame_id"])
        cells = [
            cell_facts(
                api.json(
                    "POST",
                    f"/api/v1/frames/{frame_id}/kernel/execute",
                    {"language": "python", "code": code, "wait": True},
                    timeout=self.budget(CELL_TIMEOUT_S),
                ),
                label=label,
            )
            for label, code in (
                ("cell-one", CELL_ONE_CODE),
                ("cell-two", CELL_TWO_CODE),
            )
        ]
        return cell_chain_facts(cells)

    def step_capture(
        self, state: Mapping[str, Any], execution: Mapping[str, Any]
    ) -> dict[str, Any]:
        api: Api = state["api"]
        frame_id = str(state["frame_id"])
        log = api.json("GET", f"/api/v1/frames/{frame_id}/execution-log")
        entries = log.get("entries") if isinstance(log, Mapping) else None
        if not isinstance(entries, list) or len(entries) != 2:
            raise SmokeError(
                f"the execution history holds {len(entries or [])} entries after two "
                "Cells, expected 2"
            )
        captured = read_capture(api, frame_id, entries)
        captured["cell_ids"] = sorted(
            str(item.get("producing_cell_id") or "") for item in entries
        )
        return captured

    def step_reopen(
        self, state: Mapping[str, Any], capture: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Stop, reopen the same data directory, read the same bytes and history."""
        first: Daemon = state["daemon"]
        self.stops["first"] = first.stop(self.budget(STOP_TIMEOUT_S))
        if not (
            self.stops["first"]["daemon_exited"]
            and self.stops["first"]["workers_exited"]
        ):
            raise SmokeError(
                "the first daemon (or its kernel worker) did not exit, so the "
                "reopen would not be a fresh process"
            )

        assert self.runtime is not None
        data_dir = Path(str(state["data_dir"]))
        port = free_loopback_port()
        env = self.environment(data_dir=data_dir, port=port)
        daemon = Daemon(
            runtime=self.runtime,
            env=env,
            cwd=self.work_root,
            log_path=self.work_root / "logs" / "daemon-second.log",
            port=port,
        )
        self.daemons.append(daemon)
        daemon.start()
        # The same data directory mints the same token; read it again rather
        # than assuming, so a daemon that minted a fresh credential fails here.
        daemon.token = self._read_token(data_dir, daemon)
        daemon.wait_until_serving(self.budget(STARTUP_TIMEOUT_S))
        api = Api("127.0.0.1", port, daemon.token)
        reopened = read_back(api, str(state["frame_id"]), capture)
        self.stops["final"] = daemon.stop(self.budget(STOP_TIMEOUT_S))
        if not (
            self.stops["final"]["daemon_exited"]
            and self.stops["final"]["workers_exited"]
        ):
            raise SmokeError("the reopened daemon (or its kernel worker) did not exit")
        return reopened

    # -- orchestration -----------------------------------------------------
    def run(self) -> dict[str, Any]:
        try:
            self._prepare()
            if self.not_run:
                return self._not_run_report()
            boot, state = self.step_boot()
            self.steps["boot"] = boot
            self.steps["execution"] = self.step_execution(state)
            capture = self.step_capture(state, self.steps["execution"])
            self.steps["capture"] = capture
            self.steps["reopen"] = self.step_reopen(state, capture)
            self.steps["cleanup"] = cleanup_step(
                self.stops, daemons_started=len(self.daemons)
            )
        except SmokeError as error:
            self.diagnostics.append(bounded(str(error)))
        except Exception as error:  # noqa: BLE001 - report, never a traceback
            self.diagnostics.append(
                f"unexpected {type(error).__name__}: {bounded(str(error), 800)}"
            )
        finally:
            self._stop_everything()
        return self._report()

    # -- setup / teardown --------------------------------------------------
    def _prepare(self) -> None:
        options = self.options
        if options.python is not None:
            self.runtime = runtime_for_python(
                Path(options.python),
                source_root=Path(options.source_root) if options.source_root else None,
                package=Path(options.package) if options.package else None,
                source_sha=str(options.source_sha or ""),
            )
            host_os = platform.system().lower()
            host_arch = normalized_arch(platform.machine())
            self.package = {
                "kind": self.runtime.kind,
                "name": self.runtime.name,
                "sha256": self.runtime.package_sha256,
                "source_sha": self.runtime.source_sha,
            }
            self.platform_facts = {
                "os": host_os,
                "arch": host_arch,
                "host_os": host_os,
                "host_arch": host_arch,
                "executable_here": True,
                "python": platform.python_version(),
            }
        else:
            target = Path(options.bundle)
            if target.is_dir():
                app = target
                package_sha = str(options.package_sha256 or "")
                package_name = target.name
            else:
                app = unpack_bundle(target, self.work_root)
                package_sha = sha256_file(target)
                package_name = target.name
            arch = package_arch_from_name(app.name)
            host_os = platform.system().lower()
            host_arch = normalized_arch(platform.machine())
            executable_here = host_can_execute(
                "linux", arch, host_os=host_os, host_arch=host_arch
            )
            self.package = {
                "kind": "bundle",
                "name": package_name,
                "sha256": package_sha,
                "source_sha": str(options.source_sha or ""),
            }
            self.platform_facts = {
                "os": "linux",
                "arch": arch,
                "host_os": host_os,
                "host_arch": host_arch,
                "executable_here": executable_here,
                "python": platform.python_version(),
            }
            if not executable_here:
                self.not_run = True
                self.not_run_reason = (
                    f"a linux-{arch} bundle cannot be executed on "
                    f"{host_os}/{host_arch}; run the probe on the matching Linux "
                    "release runner"
                )
                return
            self.runtime = runtime_for_bundle_app(
                app,
                package_name=package_name,
                package_sha256=package_sha,
                source_sha=str(options.source_sha or ""),
            )
        for path in (self.work_root / "home", self.work_root / "tmp"):
            path.mkdir(parents=True, exist_ok=True)
        env = clean_environment(
            data_dir=self.work_root / "probe",
            host="127.0.0.1",
            port=0,
            home=self.work_root / "home",
            temp_dir=self.work_root / "tmp",
            pythonpath=self.runtime.source_root,
        )
        self.runtime_facts = probe_runtime(
            self.runtime, cwd=self.work_root, env=self.runtime.child_environment(env)
        )
        self.platform_facts["python"] = str(
            self.runtime_facts.get("interpreter_version")
            or self.platform_facts["python"]
        )

    def _not_run_report(self) -> dict[str, Any]:
        return build_not_run_report(
            package=self.package,
            platform_facts=self.platform_facts,
            reason=str(
                getattr(self, "not_run_reason", "this host cannot execute the bundle")
            ),
        )

    def _stop_everything(self) -> None:
        """Leave no daemon behind, on success, failure, or an expired deadline.

        Only daemons this probe started are ever signalled, and only by their
        own pid; the machine may be running someone else's daemon.
        """
        for daemon in self.daemons:
            if daemon.stop_result is not None:
                continue
            try:
                outcome = daemon.stop(float(self.options.stop_timeout))
            except Exception as error:  # noqa: BLE001
                outcome = {
                    "attempted": True,
                    "daemon_exited": not daemon.live(),
                    "workers_exited": False,
                    "cli_returncode": None,
                    "note": f"shutdown raised {type(error).__name__}",
                }
                self.diagnostics.append(str(outcome["note"]))
            if "first" not in self.stops:
                self.stops["first"] = outcome
            elif "final" not in self.stops:
                self.stops["final"] = outcome
        self.steps.setdefault(
            "cleanup", cleanup_step(self.stops, daemons_started=len(self.daemons))
        )
        if not self.keep:
            shutil.rmtree(self.work_root, ignore_errors=True)

    def _report(self) -> dict[str, Any]:
        if self.not_run:
            return self._not_run_report()
        return build_report(
            package=self.package,
            platform_facts=self.platform_facts,
            runtime_facts=self.runtime_facts,
            steps=self.steps,
            diagnostics=self.diagnostics,
            elapsed_s=time.monotonic() - self.started,
            budgets={
                "startup": float(self.options.startup_timeout),
                "cell": float(self.options.cell_timeout),
                "stop": float(self.options.stop_timeout),
                "total": float(self.options.total_timeout),
            },
        )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a real two-Cell science workflow inside an installed OpenAI4S "
            "runtime, capture the resulting Artifact through the production path, "
            "restart, and read the same bytes back."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--bundle",
        type=Path,
        help="the Linux .tar.gz to unpack, or an already unpacked app directory",
    )
    group.add_argument(
        "--python",
        type=Path,
        help=(
            "interpreter of the runtime under test (for example the venv an "
            "installed wheel lives in); pair with --source-root for a source tree"
        ),
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        default=None,
        help="source tree the --python runtime must import openai4s from",
    )
    parser.add_argument(
        "--package",
        type=Path,
        default=None,
        help="the artifact the --python runtime was installed from (wheel or tarball)",
    )
    parser.add_argument("--source-sha", default="", help="the frozen source commit")
    parser.add_argument(
        "--package-sha256",
        default="",
        help="override the package digest (for an already unpacked bundle directory)",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="where to write JSON"
    )
    parser.add_argument("--total-timeout", type=float, default=TOTAL_TIMEOUT_S)
    parser.add_argument("--startup-timeout", type=float, default=STARTUP_TIMEOUT_S)
    parser.add_argument("--cell-timeout", type=float, default=CELL_TIMEOUT_S)
    parser.add_argument("--stop-timeout", type=float, default=STOP_TIMEOUT_S)
    parser.add_argument(
        "--keep-work-dir",
        action="store_true",
        help="keep the temporary data directory for debugging (never in CI)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        options = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exit_code:  # argparse already printed usage
        return int(exit_code.code or 0)
    report = Probe(options).run()
    output: Path = Path(options.output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")
    except OSError as error:
        print(f"runtime smoke: cannot write {output}: {error}", file=sys.stderr)
        return EXIT_USAGE

    status = str(report.get("status") or "")
    if status == NOT_RUN:
        print(f"runtime smoke: not_run — {report.get('not_run_reason', '')}")
        return EXIT_NOT_RUN
    if status != PASSED:
        first = (report.get("diagnostics") or ["no diagnostic recorded"])[0]
        print(f"runtime smoke: FAILED — {first}", file=sys.stderr)
        print(f"report: {output}", file=sys.stderr)
        return EXIT_FAILED
    capture = (report.get("steps") or {}).get("capture") or {}
    print(
        "runtime smoke: passed — "
        f"{capture.get('filename')} {str(capture.get('sha256'))[:12]} "
        "read back unchanged after restart "
        f"({report.get('timing', {}).get('elapsed_s', '?')}s)"
    )
    return EXIT_PASSED


if __name__ == "__main__":
    raise SystemExit(main())
