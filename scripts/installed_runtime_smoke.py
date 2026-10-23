#!/usr/bin/env python3
"""Prove an installed OpenAI4S runtime can run a science workflow, not just import.

Three existing gates stop short of this. The offline suite runs the source
checkout. ``release_import_smoke.py`` proves an installed wheel imports and
ships its resources. ``release_pipeline.step_smoke`` starts the installed daemon
and loads its default Web shell. None of them executes a Cell, so a packaging
fault inside the daemon → kernel → capture chain — a missing worker module, a
``site-packages`` layout the interpreter cannot relocate into, a data directory
the daemon cannot write — passes all three and fails the first time a user runs
code.

This script runs the plan's frozen scenario version 1 (appendix B.4) inside
whichever runtime will actually ship:

    serve: boot the daemon in an isolated data directory on a random loopback
    port and wait for /health.
    auth: a request carrying the daemon's access token answers 2xx; without it
    401.
    kernel_origin: a Cell prints ``sys.executable``, ``openai4s.__file__`` and
    the interpreter version; the first two must resolve inside the runtime
    under test.
    state_shared: Cell 1 sets ``x = 41``; Cell 2 prints ``x + 1`` (42). Both
    ``cell.status == "ok"`` with an empty ``cell.error`` — an HTTP success, an
    ``accepted`` answer or a top-level ``completed`` do not count.
    artifact_capture: Cell 3 writes ``smoke.csv`` from Cell 1's variable; the
    production capture path registers an immutable version, and the bytes read
    back from ``/artifacts/versions/<vid>`` match their SHA-256.
    figure: bundle only. Cell 4 draws with matplotlib's Agg backend and no
    ``savefig``; the Cell captures exactly one figure, whose bytes are a PNG.
    A wheel has no science extras, so this check is ``not_applicable`` there.
    exact_version: Cell 5 rewrites ``smoke.csv``; the new version differs, the
    first version still returns its original bytes, and both are listed.
    restart_readback: stop the daemon, wait for its process group to exit,
    reopen the same data directory, and confirm both versions' bytes and
    digests are unchanged. This proves durable records, not memory variables.
    browser_open: with ``--browser`` the R2 browser script opens the captured
    artifacts in a real browser against this same daemon; without the flag the
    check is ``not_run``.
    clean_exit: the final stop left no process in this run's process group and
    the temporary directory was removed.

Isolation: a new temporary data directory and HOME, a random loopback port, and
a child environment scrubbed of ``PYTHONPATH``, ``PYTHONHOME``, ``VIRTUAL_ENV``,
``OPENAI4S_WEBUI``, every ``OPENAI4S_*_API_KEY`` and every ``OPENAI4S_LLM_*``
setting. The Notebook REPL is enabled for this test process only and written
into no shipped configuration; ``OPENAI4S_UPDATE_SOURCE=offline`` keeps the
updater off the network. No model is called and nothing but loopback is
contacted.

The bundle mode unpacks the tarball into a temporary directory whose name
contains a space and launches the bundle's own ``bin/openai4s``; the wheel mode
runs ``<python> -I -m openai4s``. Both stop through the same entry point's
``stop``. Every daemon starts in its own session; the ``finally`` block only
ever signals a process group this run created. F16's budgets are enforced:
90 s per service start, 60 s per Cell, 120 s for the browser scene, 30 s to
clean up, 420 s overall.

The report is the machine-readable document the release receipt binds
(appendix B.4). ``--self-test`` starts no daemon: it drives every decision
function with synthetic responses, accepting the good shape and proving the
wrong one is refused, so the checks are testable without a Linux host.

    python3 scripts/installed_runtime_smoke.py --bundle <tar.gz> --report <path> [--browser]
    python3 scripts/installed_runtime_smoke.py --python <interpreter> --report <path>
    python3 scripts/installed_runtime_smoke.py --self-test

``--artifact`` is an optional companion to ``--python``: the wheel file the
interpreter was installed from. Without it a wheel report cannot name the wheel
digest; the release pipeline passes it so the smoke step records one.
"""

from __future__ import annotations

import argparse
import hashlib
import io
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
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCENARIO_VERSION = 1

PASSED = "passed"
FAILED = "failed"
NOT_APPLICABLE = "not_applicable"
NOT_RUN = "not_run"

#: The frozen check list, in execution order. A report always carries all of
#: them, so a missing key is a malformed report rather than an implicit skip.
CHECKS = (
    "serve",
    "auth",
    "kernel_origin",
    "state_shared",
    "artifact_capture",
    "figure",
    "exact_version",
    "restart_readback",
    "browser_open",
    "clean_exit",
)

#: F16 budgets, seconds. The total is authoritative: each step gets the smaller
#: of its own budget and whatever is left.
BUDGETS = {
    "serve": 90.0,
    "cell": 60.0,
    "browser": 120.0,
    "cleanup": 30.0,
    "total": 420.0,
}

CSV_FILENAME = "smoke.csv"
CSV_FIRST = b"a,b\n1,42\n"
CSV_SECOND = b"a,b\n2,84\n"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: Bounded, redacted failure detail: a report is evidence and a diagnostic, not
#: a log file, and it never carries a credential.
DETAIL_LIMIT = 1200
HTTP_TIMEOUT_S = 30.0
TOKEN_FILENAME = "access-token"
BUNDLE_DIRNAME = re.compile(
    r"^OpenAI4S-(?P<version>[^-]+)-linux-(?P<arch>x86_64|aarch64)$"
)
ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class SmokeFailure(Exception):
    """One scenario check did not pass; the run stops and the report names it."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(detail)
        self.check = check
        self.detail = str(detail)


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


def bounded(text: Any, limit: int = DETAIL_LIMIT) -> str:
    value = str(text if text is not None else "")
    if len(value) <= limit:
        return value
    return f"{value[:limit]}… ({len(value) - limit} more characters)"


def redact(value: Any, token: str = "") -> str:
    """Remove a runtime credential from anything a report or a log can quote.

    The token is read from the daemon's own file, but a daemon also prints a
    bootstrap URL on startup; a failure quoting either must not carry it. Both
    the literal value and any ``token=`` query, bearer header or token header
    are replaced, so a value this function was not told about is covered too.
    """
    text = str(value if value is not None else "")
    if token:
        text = text.replace(token, "<redacted-token>")
    text = re.sub(r"(?i)(x-openai4s-token\s*:\s*)\S+", r"\1<redacted-token>", text)
    text = re.sub(
        r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1<redacted-token>", text
    )
    return re.sub(r"(?i)([?&]token=)[^&#\s\"']+", r"\1<redacted-token>", text)


def normalized_arch(machine: str) -> str:
    lowered = (machine or "").lower()
    return ARCH_ALIASES.get(lowered, lowered)


def host_can_execute(
    package_os: str,
    package_arch: str,
    *,
    host_os: str | None = None,
    host_arch: str | None = None,
) -> bool:
    """Whether this host can really run the package's binaries.

    A cross-built bundle can be inspected anywhere, but this scenario ends in
    executing Cells and a daemon; claiming it passed from a host that can only
    read the archive would be the static-check-as-a-run the release rules
    forbid.
    """
    host_os = (host_os or platform.system()).lower()
    host_arch = normalized_arch(host_arch or platform.machine())
    return host_os.startswith(package_os.lower()) and host_arch == package_arch


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def bundle_arch_from_name(name: str) -> str:
    matched = BUNDLE_DIRNAME.match(str(name))
    if not matched:
        raise SmokeFailure(
            "serve", f"bundle directory {name!r} is not OpenAI4S-<version>-linux-<arch>"
        )
    return matched.group("arch")


def bundle_metadata(tarball: Path) -> dict[str, Any]:
    """The digest and architecture of the tarball, before it is executed."""
    if not tarball.is_file():
        raise SmokeFailure("serve", f"bundle does not exist: {tarball}")
    arch = ""
    matched = re.search(r"-linux-(x86_64|aarch64)\.tar\.gz$", tarball.name)
    if matched:
        arch = matched.group(1)
    else:
        try:
            with tarfile.open(tarball, "r:gz") as archive:
                top = archive.getmembers()[0].name.split("/", 1)[0]
            arch = bundle_arch_from_name(top)
        except (tarfile.TarError, IndexError, SmokeFailure) as error:
            raise SmokeFailure(
                "serve", f"not a usable bundle archive: {error}"
            ) from None
    return {"sha256": sha256_file(tarball), "arch": arch}


def clean_environment(
    base: Mapping[str, str],
    *,
    data_dir: Path,
    home: Path,
    tmp_dir: Path,
    port: int,
) -> dict[str, str]:
    """The daemon's environment: the caller's, minus anything that can steer it.

    ``PYTHONPATH``/``PYTHONHOME``/``VIRTUAL_ENV`` would let another interpreter
    or a source checkout satisfy an import the packaged runtime is missing.
    ``OPENAI4S_WEBUI`` would swap the served shell. Model keys and every
    ``OPENAI4S_LLM_*`` setting would let the smoke inherit a real provider
    configuration it has no business holding. On top of the removals it sets
    its own data directory, host, port, HOME and temp directory, enables the
    Notebook REPL for this process only, and keeps the updater offline.
    """
    env = {
        key: value
        for key, value in base.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "OPENAI4S_WEBUI"}
        and not (key.startswith("OPENAI4S_") and key.endswith("_API_KEY"))
        and not key.startswith("OPENAI4S_LLM_")
    }
    env.update(
        {
            "HOME": str(home),
            "TMPDIR": str(tmp_dir),
            "OPENAI4S_DATA_DIR": str(data_dir),
            "OPENAI4S_HOST": "127.0.0.1",
            "OPENAI4S_PORT": str(port),
            # Enabled only for the smoke process; no shipped configuration or
            # data-directory default is changed by this script.
            "OPENAI4S_NOTEBOOK_REPL": "1",
            "OPENAI4S_UPDATE_SOURCE": "offline",
            # An isolated data directory holding no real secret: the plaintext
            # backend avoids a developer keychain or a CI secret-store probe.
            "OPENAI4S_SECRET_STORE": "plaintext",
            "OPENAI4S_UNATTENDED_APPROVAL": "deny",
            "MPLBACKEND": "Agg",
        }
    )
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    return env


def interpreter_version(interpreter: Path) -> str:
    try:
        completed = subprocess.run(
            [str(interpreter), "-I", "-c", "import sys; print(sys.version.split()[0])"],
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise SmokeFailure(
            "kernel_origin",
            f"the runtime interpreter could not run: {type(error).__name__}",
        ) from None
    if completed.returncode != 0:
        raise SmokeFailure(
            "kernel_origin",
            "the runtime interpreter failed to run: "
            + bounded((completed.stderr or completed.stdout).strip()),
        )
    return (completed.stdout or "").strip() or ""


# --------------------------------------------------------------------------- #
# Time budgets
# --------------------------------------------------------------------------- #
class Budget:
    """F16's overall and per-step limits, both enforced."""

    def __init__(self, total_s: float = BUDGETS["total"]) -> None:
        self.started = time.monotonic()
        self.deadline = self.started + float(total_s)

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def for_step(self, name: str) -> float:
        left = self.remaining()
        if left <= 0:
            raise SmokeFailure(
                name,
                f"the overall {BUDGETS['total']:.0f}s budget expired before {name}",
            )
        return max(0.5, min(float(BUDGETS[name]), left))


# --------------------------------------------------------------------------- #
# Loopback HTTP client
# --------------------------------------------------------------------------- #
class Api:
    """The daemon's loopback API, authenticated with its bearer token."""

    def __init__(self, port: int, token: str = "") -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float = HTTP_TIMEOUT_S,
        authenticated: bool = True,
        check: str = "serve",
    ) -> tuple[int, bytes]:
        if not (path.startswith("/api/") or path == "/health"):
            raise SmokeFailure(check, f"refusing a request outside the API: {path}")
        data = None
        headers = {"Accept": "application/json"}
        if authenticated and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
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
            except OSError:  # pragma: no cover - body already consumed
                detail = b""
            return int(error.code), detail
        except (OSError, urllib.error.URLError) as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise SmokeFailure(check, f"{method} {path} timed out") from None
            raise SmokeFailure(
                check, f"{method} {path} failed: {type(error).__name__}"
            ) from None

    def json(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | None = None,
        *,
        timeout: float = HTTP_TIMEOUT_S,
        check: str = "serve",
    ) -> Any:
        status, raw = self.request(method, path, body, timeout=timeout, check=check)
        if not (200 <= status < 300):
            raise SmokeFailure(
                check,
                f"{method} {path} answered HTTP {status}: "
                + bounded(redact(raw.decode("utf-8", "replace"), self.token), 400),
            )
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise SmokeFailure(check, f"{method} {path} did not answer JSON") from None

    def download(self, path: str, *, check: str = "artifact_capture") -> bytes:
        status, raw = self.request("GET", path, check=check)
        if not (200 <= status < 300):
            raise SmokeFailure(check, f"GET {path} answered HTTP {status}")
        return raw


# --------------------------------------------------------------------------- #
# The runtime under test
# --------------------------------------------------------------------------- #
class Runtime:
    """How to start and stop the daemon, and where its code must live."""

    def __init__(
        self,
        *,
        target: str,
        artifact_sha256: str,
        platform_name: str,
        arch: str,
        python_version: str,
        origin_root: Path,
        serve_argv: Sequence[str],
        stop_argv: Sequence[str],
        cwd: Path,
    ) -> None:
        self.target = target
        self.artifact_sha256 = artifact_sha256
        self.platform = platform_name
        self.arch = arch
        self.python = python_version
        self.origin_root = Path(os.path.abspath(str(origin_root)))
        self.serve_argv = list(serve_argv)
        self.stop_argv = list(stop_argv)
        self.cwd = Path(cwd)


def _unpack_bundle(tarball: Path, work_root: Path) -> Path:
    if not tarfile.is_tarfile(tarball):
        raise SmokeFailure("serve", f"not a tar archive: {tarball.name}")
    scratch = work_root / "unpacked"
    scratch.mkdir()
    with tarfile.open(tarball, "r:gz") as archive:
        # `data` refuses absolute paths, parent escapes and links that point
        # outside the archive: the smoke must not be the step that writes
        # outside its own temporary directory.
        archive.extractall(scratch, filter="data")
    roots = [path for path in scratch.iterdir() if path.is_dir()]
    if len(roots) != 1:
        raise SmokeFailure(
            "serve",
            f"expected exactly one top-level directory in the archive, found {len(roots)}",
        )
    return roots[0]


def runtime_for_bundle(tarball: Path, work_root: Path) -> Runtime:
    app = _unpack_bundle(tarball, work_root)
    arch = bundle_arch_from_name(app.name)
    launcher = app / "bin" / "openai4s"
    runtime = app / "runtime" / "bin" / "python3"
    for path, label in (
        (launcher, "bundled CLI"),
        (runtime, "embedded interpreter"),
        (app / "src" / "openai4s", "source tree"),
    ):
        if not path.exists():
            raise SmokeFailure("serve", f"bundle is missing its {label}: {path}")
    if not host_can_execute("linux", arch):
        raise SmokeFailure(
            "serve",
            f"a linux-{arch} bundle cannot be executed on "
            f"{platform.system().lower()}/{normalized_arch(platform.machine())}; "
            "run this smoke on the matching Linux release runner",
        )
    return Runtime(
        target="bundle",
        artifact_sha256=sha256_file(tarball),
        platform_name="linux",
        arch=arch,
        # The embedded interpreter reports its own version in kernel_origin;
        # this is only the value a preparation failure has to fall back to.
        python_version=platform.python_version(),
        origin_root=app,
        # The bundled CLI is a self-locating shell script: it sets PYTHONPATH
        # and PATH for the embedded interpreter itself. The caller's PYTHONPATH
        # was removed from the environment, so it cannot leak in.
        serve_argv=[str(launcher), "serve", "--no-open"],
        stop_argv=[str(launcher), "stop"],
        cwd=work_root,
    )


def runtime_for_python(
    interpreter: Path, *, artifact: Path | None, work_root: Path
) -> Runtime:
    if not interpreter.is_file():
        raise SmokeFailure(
            "serve", f"runtime interpreter does not exist: {interpreter}"
        )
    # `-I` runs isolated: no cwd on sys.path, no PYTHONPATH, no user site. The
    # venv's own site-packages still resolve, which is the runtime under test.
    prefix = [str(interpreter), "-I", "-m", "openai4s"]
    return Runtime(
        target="wheel",
        artifact_sha256=sha256_file(artifact) if artifact is not None else "",
        platform_name=platform.system().lower(),
        arch=normalized_arch(platform.machine()),
        python_version=interpreter_version(interpreter),
        # The venv prefix, lexically: `bin/python` is usually a symlink to a
        # base installation, so resolving would leave the environment under test.
        origin_root=interpreter.parent.parent,
        serve_argv=[*prefix, "serve", "--no-open"],
        stop_argv=[*prefix, "stop"],
        cwd=work_root,
    )


# --------------------------------------------------------------------------- #
# Daemon lifecycle
# --------------------------------------------------------------------------- #
class Daemon:
    """One daemon this run started, in its own process session."""

    def __init__(
        self,
        *,
        runtime: Runtime,
        env: Mapping[str, str],
        log_path: Path,
    ) -> None:
        self.runtime = runtime
        self.env = dict(env)
        self.log_path = log_path
        self.proc: subprocess.Popen[bytes] | None = None
        self.pgid = 0
        self.port = 0
        self.token = ""

    def start(self, *, port: int) -> None:
        self.port = port
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("wb") as log:
            try:
                self.proc = subprocess.Popen(
                    self.runtime.serve_argv,
                    cwd=str(self.runtime.cwd),
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    # Its own session: the smoke can wait for, and if necessary
                    # stop, exactly this tree without signalling anything else.
                    start_new_session=True,
                )
            except OSError as error:
                raise SmokeFailure(
                    "serve",
                    f"the runtime daemon could not start: {type(error).__name__}",
                ) from None
        self.pgid = self.proc.pid

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def log_tail(self, limit: int = 2000) -> str:
        try:
            raw = self.log_path.read_bytes()[-16384:]
        except OSError:
            return ""
        return bounded(
            redact(raw.decode("utf-8", "replace").strip(), self.token), limit
        )

    def wait_until_healthy(self, timeout: float) -> None:
        if self.proc is None:
            raise SmokeFailure("serve", "daemon was never started")
        started = time.monotonic()
        last = "no response"
        while time.monotonic() - started < timeout:
            if not self.alive():
                raise SmokeFailure(
                    "serve",
                    "the runtime daemon exited before serving: " + self.log_tail(),
                )
            try:
                status, raw = Api(self.port).request(
                    "GET", "/health", timeout=min(2.0, timeout), authenticated=False
                )
            except SmokeFailure:
                # Connection refused while the listener is still binding is
                # "not ready yet", not a failed serve: keep polling until the
                # 90 s budget runs out.
                last = "connection not ready"
                time.sleep(0.2)
                continue
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                payload = {}
            if (
                status == 200
                and isinstance(payload, Mapping)
                and payload.get("status") == "ok"
            ):
                return
            last = f"health status {status}"
            time.sleep(0.2)
        raise SmokeFailure(
            "serve",
            f"the runtime daemon did not answer /health within {timeout:.0f}s ({last})",
        )

    def read_token(self, data_dir: Path, timeout: float = 20.0) -> str:
        token_file = data_dir / TOKEN_FILENAME
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                token = token_file.read_text("utf-8").strip()
            except OSError:
                token = ""
            if token:
                self.token = token
                return token
            if not self.alive():
                raise SmokeFailure(
                    "serve",
                    "the runtime daemon exited before minting its access token: "
                    + self.log_tail(),
                )
            time.sleep(0.1)
        raise SmokeFailure("serve", "the runtime daemon minted no access token")

    def wait_for_group_exit(self, grace: float = 10.0) -> bool:
        if not hasattr(os, "killpg"):  # pragma: no cover - Windows
            return False
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            try:
                os.killpg(self.pgid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:  # pragma: no cover - not our group
                return False
            time.sleep(0.1)
        return False

    def _wait(self, timeout: float) -> bool:
        assert self.proc is not None
        try:
            self.proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def stop(self, timeout: float) -> None:
        """Stop through the product's own CLI, then prove the group is gone.

        The daemon is this run's own child, so it must be reaped while the CLI
        polls for it: an unreaped process lingers as a zombie that a pid check
        reads as running. Kernel workers share the daemon's session, so an
        empty process group is the evidence that the worker left too; the check
        only sends signal 0.
        """
        if self.proc is None:
            return
        failure = ""
        with tempfile.TemporaryFile() as output:
            try:
                stopper = subprocess.Popen(
                    self.runtime.stop_argv,
                    cwd=str(self.runtime.cwd),
                    env=self.env,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                )
            except OSError as error:
                stopper = None
                failure = f"the runtime CLI could not run `stop`: {error}"
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
                    failure = f"`stop` did not return within {timeout:.0f}s"
                elif returncode != 0:
                    output.seek(0)
                    said = redact(
                        output.read().decode("utf-8", "replace").strip(), self.token
                    )
                    failure = f"`stop` exited {returncode}: {bounded(said, 600)}"
        settle = max(1.0, min(10.0, timeout / 2))
        if self.alive():
            if not failure:
                failure = "`stop` exited 0 but the daemon kept running"
            self._wait(settle)
        if self.alive():
            # Only this run's own child. Never a process-group kill and never a
            # name match: the machine may be running someone else's daemon.
            assert self.proc is not None
            self.proc.terminate()
            if not self._wait(settle):  # pragma: no cover - last resort
                self.proc.kill()
                self.proc.wait()
        if self.alive():  # pragma: no cover - terminate/kill cannot fail silently
            raise SmokeFailure("clean_exit", failure or "the daemon did not exit")
        if not self.wait_for_group_exit(timeout):
            raise SmokeFailure(
                "clean_exit",
                failure
                or "a kernel worker outlived the daemon (process group not empty)",
            )
        del failure  # a clean stop is a clean stop; only leaks raise


# --------------------------------------------------------------------------- #
# Decision checks: each accepts the real shape and refuses the wrong one
# --------------------------------------------------------------------------- #
def check_health(status: int, payload: Any) -> None:
    if (
        status != 200
        or not isinstance(payload, Mapping)
        or payload.get("status") != "ok"
    ):
        raise SmokeFailure("serve", f"/health answered {status} with {payload!r}")


def check_auth(authenticated_status: int, unauthenticated_status: int) -> None:
    if not (200 <= authenticated_status < 300):
        raise SmokeFailure(
            "auth", f"an authenticated API call answered HTTP {authenticated_status}"
        )
    if unauthenticated_status != 401:
        raise SmokeFailure(
            "auth",
            "an unauthenticated API call answered HTTP "
            f"{unauthenticated_status}; the daemon's token gate is not enforced",
        )


def path_is_inside(value: str, root: Path) -> bool:
    """Whether a reported path belongs to the runtime tree, symlinks considered.

    A venv's ``bin/python`` is usually a symlink to the base installation, so
    resolving only the reported path would land outside the venv; an unpacked
    bundle on macOS lives under ``/var`` while Python may report ``/private/var``.
    Both spellings are therefore checked on both sides.
    """
    candidates = {Path(os.path.abspath(str(value))), Path(str(value)).resolve()}
    roots = {Path(os.path.abspath(str(root))), Path(str(root)).resolve()}
    return any(
        candidate == inside_root or inside_root in candidate.parents
        for candidate in candidates
        for inside_root in roots
    )


def check_kernel_origin(stdout: str, origin_root: Path) -> tuple[str, str, str]:
    """Interpreter, imported package and version, all from the tested runtime.

    The first two lines are the plan's exact probe; the third is the interpreter
    version the report records, so it comes from the runtime that executed the
    Cell rather than from the host running this script.
    """
    lines = [line.strip() for line in str(stdout or "").splitlines() if line.strip()]
    if len(lines) < 3:
        raise SmokeFailure(
            "kernel_origin",
            "the origin Cell printed fewer than three lines; expected sys.executable, "
            "openai4s.__file__ and the interpreter version",
        )
    executable, module_file, version = lines[-3], lines[-2], lines[-1]
    for label, value in (
        ("interpreter", executable),
        ("openai4s package", module_file),
    ):
        if not Path(value).is_file():
            raise SmokeFailure(
                "kernel_origin", f"the Cell reported no {label} file: {value!r}"
            )
        if not path_is_inside(value, origin_root):
            raise SmokeFailure(
                "kernel_origin",
                f"the Cell's {label} is outside the runtime under test: "
                f"{value!r} is not under {origin_root}",
            )
    return executable, module_file, version


def check_cell(payload: Any, *, check: str) -> Mapping[str, Any]:
    """A Cell succeeded only when ``cell.status == "ok"`` and no error is set."""
    if not isinstance(payload, Mapping):
        raise SmokeFailure(check, "the execute request returned no result object")
    if payload.get("error"):
        raise SmokeFailure(
            check, f"the Cell was refused: {bounded(str(payload['error']))}"
        )
    cell = payload.get("cell")
    if not isinstance(cell, Mapping):
        raise SmokeFailure(
            check,
            "the execute request returned no executed Cell "
            f"(top-level status={payload.get('status')!r})",
        )
    if str(cell.get("status") or "") != "ok" or cell.get("error"):
        raise SmokeFailure(
            check,
            f"the Cell did not execute successfully: status={cell.get('status')!r} "
            f"error={bounded(str(cell.get('error') or ''), 500)!r}",
        )
    return cell


def check_state_shared(second_stdout: str) -> None:
    """The second Cell must have read the variable the first Cell created."""
    lines = [
        line.strip() for line in str(second_stdout or "").splitlines() if line.strip()
    ]
    if not lines or lines[-1] != "42":
        raise SmokeFailure(
            "state_shared",
            "the second Cell printed "
            f"{bounded(str(second_stdout or ''), 300)!r}, expected 42 from the first "
            "Cell's variable; the two Cells did not share one kernel state",
        )


def binding_for(
    entry: Mapping[str, Any], filename: str, *, check: str
) -> Mapping[str, Any]:
    rows = [
        row
        for row in (entry.get("output_artifacts") or [])
        if isinstance(row, Mapping) and row.get("filename") == filename
    ]
    if len(rows) != 1:
        raise SmokeFailure(
            check,
            f"the Cell captured {len(rows)} {filename!r} version(s), expected 1 "
            f"(files_written={entry.get('files_written')!r})",
        )
    row = rows[0]
    if not all(str(row.get(field) or "") for field in ("artifact_id", "version_id")):
        raise SmokeFailure(
            check, f"the captured {filename!r} row carries no version identity"
        )
    if not str(row.get("url") or "").startswith("/api/"):
        raise SmokeFailure(
            check, f"the captured {filename!r} row carries no exact-version link"
        )
    return row


def check_version_bytes(
    body: bytes,
    version_row: Mapping[str, Any],
    *,
    expected: bytes | None = None,
    check: str = "artifact_capture",
) -> str:
    """Bytes, version metadata and (when known) content must all agree."""
    recorded = str(version_row.get("checksum") or "").lower()
    if not _SHA256.match(recorded):
        raise SmokeFailure(check, "the captured version records no SHA-256 checksum")
    actual = sha256_bytes(body)
    if actual != recorded:
        raise SmokeFailure(
            check,
            "the captured bytes do not match the version checksum "
            f"({actual[:12]} != {recorded[:12]})",
        )
    if int(version_row.get("size_bytes") or -1) != len(body):
        raise SmokeFailure(
            check, "the captured size does not match the version metadata"
        )
    if expected is not None and body != expected:
        raise SmokeFailure(
            check,
            "the captured bytes are not the expected science result: "
            f"{bounded(body.decode('utf-8', 'replace'), 200)!r}",
        )
    return actual


def figure_name(entry: Mapping[str, Any]) -> str:
    """A bundle figure Cell must have captured exactly one figure, with a version."""
    figures = entry.get("figures") or []
    if not isinstance(figures, list) or len(figures) != 1:
        raise SmokeFailure(
            "figure",
            f"the figure Cell captured "
            f"{len(figures) if isinstance(figures, list) else '?'} figure(s), "
            "expected exactly 1",
        )
    name = str(figures[0])
    binding_for(entry, name, check="figure")
    return name


def check_png(body: bytes, *, check: str = "figure") -> None:
    if not body.startswith(PNG_MAGIC):
        raise SmokeFailure(check, "the captured figure is not a PNG")


def check_exact_version(
    *,
    first_version_id: str,
    second_version_id: str,
    first_body: bytes,
    first_sha: str,
    second_body: bytes,
) -> None:
    if first_version_id == second_version_id:
        raise SmokeFailure(
            "exact_version",
            "rewriting the same filename did not mint a new immutable version",
        )
    if first_body != CSV_FIRST or sha256_bytes(first_body) != first_sha:
        raise SmokeFailure(
            "exact_version",
            "the first version's bytes changed after the second version was written",
        )
    if second_body != CSV_SECOND:
        raise SmokeFailure(
            "exact_version", "the second version does not hold the new bytes"
        )


def check_restart_readback(
    *,
    listed: Mapping[str, str],
    first_expected: str,
    second_expected: str,
    first_body: bytes,
    second_body: bytes,
    history_entries: int,
) -> None:
    if listed.get("first") != first_expected or listed.get("second") != second_expected:
        raise SmokeFailure(
            "restart_readback",
            "the reopened daemon reports different digests for the same versions",
        )
    if first_body != CSV_FIRST or second_body != CSV_SECOND:
        raise SmokeFailure(
            "restart_readback",
            "the reopened daemon serves different bytes for a version",
        )
    if history_entries < 5:
        raise SmokeFailure(
            "restart_readback",
            f"the reopened daemon reports {history_entries} execution-history entries, "
            "expected at least 5",
        )


def check_browser_open(returncode: int | None, detail: str) -> None:
    if returncode != 0:
        raise SmokeFailure(
            "browser_open",
            f"the browser layer exited {returncode}: {bounded(detail, 600)}",
        )


def check_clean_exit(*, daemon_gone: bool, work_root_gone: bool) -> None:
    if not daemon_gone:
        raise SmokeFailure(
            "clean_exit", "a daemon or kernel worker from this run survives"
        )
    if not work_root_gone:
        raise SmokeFailure("clean_exit", "the temporary data directory was not removed")


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
#: The result each check may carry in a passing report. ``figure`` is bundle
#: only and ``browser_open`` is requested explicitly, so both have one extra
#: value that means "correctly not exercised" rather than a skip.
PASSING_RESULTS = {
    "serve": frozenset({PASSED}),
    "auth": frozenset({PASSED}),
    "kernel_origin": frozenset({PASSED}),
    "state_shared": frozenset({PASSED}),
    "artifact_capture": frozenset({PASSED}),
    "figure": frozenset({PASSED, NOT_APPLICABLE}),
    "exact_version": frozenset({PASSED}),
    "restart_readback": frozenset({PASSED}),
    "browser_open": frozenset({PASSED, NOT_RUN}),
    "clean_exit": frozenset({PASSED}),
}


def build_report(
    *,
    target: str,
    artifact_sha256: str,
    platform_name: str,
    arch: str,
    python_version: str,
    checks: Mapping[str, str],
    duration_s: float,
    failure: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The appendix-B.4 report: fixed shape, fixed check names, no log text.

    The verdict is derived from the checks rather than asserted: every check
    must be ``passed`` (``figure`` may be ``not_applicable`` for a wheel, and
    ``browser_open`` may be ``not_run`` when the browser layer was not asked
    for). Anything else — a failure, a skipped check, a timeout — is a failure
    report, and the failure names one check with a bounded, redacted detail.
    """
    if target not in {"bundle", "wheel"}:
        raise ValueError(f"target must be bundle or wheel, not {target!r}")
    normalized: dict[str, str] = {}
    for name in CHECKS:
        value = str(checks.get(name) or NOT_RUN)
        if value not in {PASSED, FAILED, NOT_APPLICABLE, NOT_RUN}:
            raise ValueError(f"check {name!r} has an unknown result {value!r}")
        normalized[name] = value
    bound = bool(_SHA256.match(str(artifact_sha256 or "").lower()))
    status = PASSED if bound else FAILED
    if failure is not None:
        status = FAILED
    if status == PASSED:
        for name, allowed in PASSING_RESULTS.items():
            if normalized[name] not in allowed:
                status = FAILED
                break
    # The figure check is target-specific: a bundle must have drawn one, and a
    # wheel has no science extras, so "not_applicable" is the only correct
    # value there. Recording the other target's value would be a lie about
    # what ran, not a harmless omission.
    if status == PASSED and (
        (target == "bundle" and normalized["figure"] != PASSED)
        or (target == "wheel" and normalized["figure"] != NOT_APPLICABLE)
    ):
        status = FAILED
    if status == PASSED:
        failure = None
    else:
        recorded = failure if isinstance(failure, Mapping) else {}
        detail = str(recorded.get("detail") or "")
        if not bound and not str(recorded.get("check") or ""):
            detail = (
                "no artifact SHA-256 was recorded; the report cannot bind its "
                "checks to the bytes being released"
            )
        failure = {
            "check": str(recorded.get("check") or "scenario"),
            "detail": bounded(redact(detail), DETAIL_LIMIT),
        }
    return {
        "scenario_version": SCENARIO_VERSION,
        "target": target,
        "artifact_sha256": str(artifact_sha256 or ""),
        "platform": str(platform_name or ""),
        "arch": str(arch or ""),
        "python": str(python_version or ""),
        "checks": normalized,
        "status": status,
        "duration_s": round(float(duration_s), 3),
        "failure": failure,
    }


# --------------------------------------------------------------------------- #
# The scenario
# --------------------------------------------------------------------------- #
class Scenario:
    """Run appendix-B.4's scenario version 1 against one runtime."""

    def __init__(
        self,
        *,
        runtime: Runtime,
        work_root: Path,
        browser: bool,
        env_base: Mapping[str, str] | None = None,
        repo_root: Path | None = None,
    ) -> None:
        self.runtime = runtime
        self.work_root = work_root
        self.browser = browser
        self.env_base = dict(env_base if env_base is not None else os.environ)
        self.repo_root = (
            Path(repo_root) if repo_root else Path(__file__).resolve().parents[1]
        )
        self.budget = Budget()
        self.checks: dict[str, str] = {name: NOT_RUN for name in CHECKS}
        self.daemons: list[Daemon] = []
        self.daemon: Daemon | None = None
        self.data_dir = work_root / "data"
        self.home = work_root / "home"
        self.tmp_dir = work_root / "tmp"
        self.logs = work_root / "logs"
        self.frame_id = ""
        self.project_id = ""
        self.csv_artifact_id = ""
        self.csv_version_first = ""
        self.csv_version_second = ""
        self.csv_sha_first = ""
        self.csv_sha_second = ""
        self.figure: dict[str, str] = {
            "artifact_id": "",
            "version_id": "",
            "checksum": "",
        }

    # -- helpers -----------------------------------------------------------
    def _prepare_dirs(self) -> None:
        for path in (self.data_dir, self.home, self.tmp_dir, self.logs):
            path.mkdir(parents=True, exist_ok=True)

    def _env(self, port: int) -> dict[str, str]:
        return clean_environment(
            self.env_base,
            data_dir=self.data_dir,
            home=self.home,
            tmp_dir=self.tmp_dir,
            port=port,
        )

    def _start_daemon(self) -> Daemon:
        port = free_loopback_port()
        daemon = Daemon(
            runtime=self.runtime,
            env=self._env(port),
            log_path=self.logs / f"daemon-{len(self.daemons) + 1}.log",
        )
        self.daemons.append(daemon)
        daemon.start(port=port)
        daemon.wait_until_healthy(self.budget.for_step("serve"))
        daemon.token = daemon.read_token(self.data_dir)
        return daemon

    def _api(self) -> Api:
        assert self.daemon is not None
        return Api(self.daemon.port, self.daemon.token)

    def _execute(self, code: str, *, check: str) -> Any:
        assert self.daemon is not None
        api = self._api()
        payload = api.json(
            "POST",
            f"/api/v1/frames/{self.frame_id}/kernel/execute",
            {"language": "python", "code": code, "wait": True},
            timeout=self.budget.for_step("cell"),
            check=check,
        )
        return check_cell(payload, check=check)

    def _entries(self) -> list[Mapping[str, Any]]:
        api = self._api()
        log = api.json("GET", f"/api/v1/frames/{self.frame_id}/execution-log")
        entries = log.get("entries") if isinstance(log, Mapping) else None
        if not isinstance(entries, list) or not entries:
            raise SmokeFailure("artifact_capture", "the execution history is empty")
        return [entry for entry in entries if isinstance(entry, Mapping)]

    @staticmethod
    def _entry_for(
        entries: Sequence[Mapping[str, Any]], marker: str, *, check: str
    ) -> Mapping[str, Any]:
        matches = [
            entry for entry in entries if marker in str(entry.get("source") or "")
        ]
        if len(matches) != 1:
            raise SmokeFailure(
                check,
                f"execution history has {len(matches)} Cell(s) matching {marker!r}, "
                "expected 1",
            )
        entry = matches[0]
        if str(entry.get("status") or "") != "ok" or entry.get("error"):
            raise SmokeFailure(
                check, f"the persisted Cell matching {marker!r} is not a success"
            )
        return entry

    def _version_row(
        self, artifact_id: str, version_id: str, *, check: str
    ) -> Mapping[str, Any]:
        api = self._api()
        quoted = urllib.parse.quote(artifact_id, safe="")
        listing = api.json("GET", f"/api/v1/artifacts/{quoted}/versions", check=check)
        versions = listing.get("versions") if isinstance(listing, Mapping) else None
        row = next(
            (
                item
                for item in (versions or [])
                if isinstance(item, Mapping) and item.get("version_id") == version_id
            ),
            None,
        )
        if row is None:
            raise SmokeFailure(
                f"{check}", f"artifact {artifact_id} does not list version {version_id}"
            )
        return row

    def _version_bytes(self, version_id: str, *, check: str) -> bytes:
        quoted = urllib.parse.quote(version_id, safe="")
        return self._api().download(f"/api/v1/artifacts/versions/{quoted}", check=check)

    # -- steps -------------------------------------------------------------
    def step_serve(self) -> None:
        self._prepare_dirs()
        self.daemon = self._start_daemon()
        self.checks["serve"] = PASSED

    def step_auth(self) -> None:
        api = self._api()
        authenticated, _ = api.request("GET", "/api/v1/projects?limit=1", check="auth")
        unauthenticated, _ = api.request(
            "GET", "/api/v1/projects?limit=1", authenticated=False, check="auth"
        )
        check_auth(authenticated, unauthenticated)
        self.checks["auth"] = PASSED

    def step_kernel_origin(self) -> None:
        api = self._api()
        project = api.json(
            "POST",
            "/api/v1/projects",
            {"name": "installed runtime smoke"},
            check="kernel_origin",
        )
        self.project_id = str(
            (project or {}).get("project_id") or (project or {}).get("id") or ""
        )
        if not self.project_id:
            raise SmokeFailure(
                "kernel_origin", "the runtime refused to create a project"
            )
        frame = api.json(
            "POST",
            "/api/v1/frames",
            {"project_id": self.project_id},
            check="kernel_origin",
        )
        self.frame_id = str(
            (frame or {}).get("id") or (frame or {}).get("frame_id") or ""
        )
        if not self.frame_id:
            raise SmokeFailure(
                "kernel_origin", "the runtime refused to create a session"
            )
        cell = self._execute(
            "import sys, openai4s\n"
            "print(sys.executable)\n"
            "print(openai4s.__file__)\n"
            "print(sys.version.split()[0])\n",
            check="kernel_origin",
        )
        _executable, _module, version = check_kernel_origin(
            str(cell.get("stdout") or ""), self.runtime.origin_root
        )
        if version:
            self.runtime.python = version
        self.checks["kernel_origin"] = PASSED

    def step_state_shared(self) -> None:
        self._execute("x = 41\nprint('smoke cell one')\n", check="state_shared")
        cell = self._execute("print(x + 1)\n", check="state_shared")
        check_state_shared(str(cell.get("stdout") or ""))
        self.checks["state_shared"] = PASSED

    def step_artifact_capture(self) -> None:
        self._execute(
            "from pathlib import Path\n"
            "Path('smoke.csv').write_text(f'a,b\\n1,{x + 1}\\n', encoding='utf-8')\n"
            "print('smoke cell three')\n",
            check="artifact_capture",
        )
        entries = self._entries()
        entry = self._entry_for(entries, "smoke cell three", check="artifact_capture")
        row = binding_for(entry, CSV_FILENAME, check="artifact_capture")
        self.csv_artifact_id = str(row["artifact_id"])
        self.csv_version_first = str(row["version_id"])
        version_row = self._version_row(
            self.csv_artifact_id, self.csv_version_first, check="artifact_capture"
        )
        body = self._api().download(str(row["url"]), check="artifact_capture")
        self.csv_sha_first = check_version_bytes(
            body, version_row, expected=CSV_FIRST, check="artifact_capture"
        )
        self.checks["artifact_capture"] = PASSED

    def step_figure(self) -> None:
        if self.runtime.target != "bundle":
            self.checks["figure"] = NOT_APPLICABLE
            return
        self._execute(
            "import matplotlib\n"
            "matplotlib.use('Agg')\n"
            "import matplotlib.pyplot as plt\n"
            "plt.figure()\n"
            "plt.plot([0, 1, 2], [0, 1, 0])\n"
            "print('smoke cell four')\n",
            check="figure",
        )
        entries = self._entries()
        entry = self._entry_for(entries, "smoke cell four", check="figure")
        name = figure_name(entry)
        row = binding_for(entry, name, check="figure")
        version_row = self._version_row(
            str(row["artifact_id"]), str(row["version_id"]), check="figure"
        )
        body = self._api().download(str(row["url"]), check="figure")
        check_png(body)
        check_version_bytes(body, version_row, check="figure")
        self.figure = {
            "artifact_id": str(row["artifact_id"]),
            "version_id": str(row["version_id"]),
            "checksum": str(version_row.get("checksum") or ""),
        }
        self.checks["figure"] = PASSED

    def step_exact_version(self) -> None:
        self._execute(
            "from pathlib import Path\n"
            "Path('smoke.csv').write_text('a,b\\n2,84\\n', encoding='utf-8')\n"
            "print('smoke cell five')\n",
            check="exact_version",
        )
        entries = self._entries()
        entry = self._entry_for(entries, "smoke cell five", check="exact_version")
        row = binding_for(entry, CSV_FILENAME, check="exact_version")
        if str(row["artifact_id"]) != self.csv_artifact_id:
            raise SmokeFailure(
                "exact_version",
                "rewriting the same filename minted a different Artifact",
            )
        self.csv_version_second = str(row["version_id"])
        version_row = self._version_row(
            self.csv_artifact_id, self.csv_version_second, check="exact_version"
        )
        second_body = self._api().download(str(row["url"]), check="exact_version")
        self.csv_sha_second = check_version_bytes(
            second_body, version_row, expected=CSV_SECOND, check="exact_version"
        )
        first_row = self._version_row(
            self.csv_artifact_id, self.csv_version_first, check="exact_version"
        )
        if str(first_row.get("checksum") or "") != self.csv_sha_first:
            raise SmokeFailure(
                "exact_version", "the first version's recorded digest changed"
            )
        first_body = self._version_bytes(self.csv_version_first, check="exact_version")
        check_exact_version(
            first_version_id=self.csv_version_first,
            second_version_id=self.csv_version_second,
            first_body=first_body,
            first_sha=self.csv_sha_first,
            second_body=second_body,
        )
        self.checks["exact_version"] = PASSED

    def step_restart_readback(self) -> None:
        assert self.daemon is not None
        self.daemon.stop(self.budget.for_step("cleanup"))
        self.daemon = self._start_daemon()
        first_row = self._version_row(
            self.csv_artifact_id, self.csv_version_first, check="restart_readback"
        )
        second_row = self._version_row(
            self.csv_artifact_id, self.csv_version_second, check="restart_readback"
        )
        first_body = self._version_bytes(
            self.csv_version_first, check="restart_readback"
        )
        second_body = self._version_bytes(
            self.csv_version_second, check="restart_readback"
        )
        entries = self._entries()
        check_restart_readback(
            listed={
                "first": str(first_row.get("checksum") or ""),
                "second": str(second_row.get("checksum") or ""),
            },
            first_expected=self.csv_sha_first,
            second_expected=self.csv_sha_second,
            first_body=first_body,
            second_body=second_body,
            history_entries=len(entries),
        )
        self.checks["restart_readback"] = PASSED

    def step_browser_open(self) -> None:
        if not self.browser:
            self.checks["browser_open"] = NOT_RUN
            return
        assert self.daemon is not None
        script = self.repo_root / "tests" / "browser_bundle_smoke.mjs"
        if not script.is_file():
            raise SmokeFailure(
                "browser_open", f"the browser script is missing: {script}"
            )
        expect = self.work_root / "browser-expect.json"
        expect.write_text(
            json.dumps(
                {
                    "frame_id": self.frame_id,
                    "csv": {
                        "artifact_id": self.csv_artifact_id,
                        "version_id": self.csv_version_first,
                        "checksum": self.csv_sha_first,
                    },
                    "png": self.figure,
                }
            )
            + "\n",
            "utf-8",
        )
        inherited = self._env(self.daemon.port)
        env = {
            key: inherited[key]
            for key in (
                "PATH",
                "HOME",
                "TMPDIR",
                "LANG",
                "LC_ALL",
                "OPENAI4S_DATA_DIR",
                "OPENAI4S_HOST",
                "OPENAI4S_PORT",
            )
            if key in inherited
        }
        env.update(
            {
                "OPENAI4S_BROWSER_URL": f"http://127.0.0.1:{self.daemon.port}/",
                "OPENAI4S_DATA_DIR": str(self.data_dir),
                "OPENAI4S_SMOKE_EXPECT": str(expect),
            }
        )
        timeout = self.budget.for_step("browser")
        try:
            completed = subprocess.run(
                ["node", str(script)],
                cwd=str(self.repo_root),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise SmokeFailure(
                "browser_open", "`node` is not available on this host"
            ) from None
        except subprocess.TimeoutExpired:
            raise SmokeFailure(
                "browser_open",
                f"the browser layer did not finish within {timeout:.0f}s",
            ) from None
        detail = redact(
            (completed.stderr or completed.stdout or "").strip(), self.daemon.token
        )
        check_browser_open(completed.returncode, detail)
        self.checks["browser_open"] = PASSED

    def step_clean_exit(self) -> None:
        assert self.daemon is not None
        self.daemon.stop(self.budget.for_step("cleanup"))
        # Remove the temporary directory before reporting: a leftover is
        # exactly what this check exists to notice.
        shutil.rmtree(self.work_root, ignore_errors=True)
        check_clean_exit(
            daemon_gone=not self.daemon.alive(),
            work_root_gone=not self.work_root.exists(),
        )
        self.checks["clean_exit"] = PASSED

    # -- orchestration -----------------------------------------------------
    STEPS = (
        ("serve", "step_serve"),
        ("auth", "step_auth"),
        ("kernel_origin", "step_kernel_origin"),
        ("state_shared", "step_state_shared"),
        ("artifact_capture", "step_artifact_capture"),
        ("figure", "step_figure"),
        ("exact_version", "step_exact_version"),
        ("restart_readback", "step_restart_readback"),
        ("browser_open", "step_browser_open"),
        ("clean_exit", "step_clean_exit"),
    )

    def run(self) -> dict[str, Any]:
        failure: dict[str, str] | None = None
        try:
            for name, method in self.STEPS:
                try:
                    getattr(self, method)()
                except SmokeFailure as error:
                    self.checks[error.check] = FAILED
                    failure = {
                        "check": error.check,
                        "detail": bounded(redact(error.detail), DETAIL_LIMIT),
                    }
                    break
        finally:
            self._cleanup()
        return build_report(
            target=self.runtime.target,
            artifact_sha256=self.runtime.artifact_sha256,
            platform_name=self.runtime.platform,
            arch=self.runtime.arch,
            python_version=self.runtime.python,
            checks=self.checks,
            duration_s=self.budget.elapsed(),
            failure=failure,
        )

    def _cleanup(self) -> None:
        """Leave no daemon or temp directory, on success, failure or timeout.

        Only daemons this run started are stopped, each by its own pid/CLI; the
        machine may be running someone else's daemon.
        """
        for daemon in self.daemons:
            if not daemon.alive():
                continue
            try:
                daemon.stop(BUDGETS["cleanup"])
            except SmokeFailure:
                # Best effort here: the report already names the real failure.
                try:
                    assert daemon.proc is not None
                    daemon.proc.kill()
                    daemon.proc.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):  # pragma: no cover
                    pass
        if self.work_root.exists():
            shutil.rmtree(self.work_root, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Self-test: drive every decision function with synthetic responses
# --------------------------------------------------------------------------- #
def _expect_failure(check: str, action: Callable[[], Any]) -> None:
    try:
        action()
    except SmokeFailure as error:
        if error.check != check:
            raise AssertionError(
                f"expected a {check!r} failure, got {error.check!r}: {error.detail}"
            ) from error
        return
    raise AssertionError(f"{check}: the wrong shape was accepted")


def _good_cell() -> dict[str, Any]:
    return {
        "status": "completed",
        "execution_id": "repl-self-test",
        "cell": {
            "status": "ok",
            "error": None,
            "stdout": "42\n",
            "files_written": ["smoke.csv"],
            "figures": [],
        },
    }


def _good_entry() -> dict[str, Any]:
    return {
        "producing_cell_id": "c-self-test",
        "status": "ok",
        "error": "",
        "source": "smoke cell three",
        "files_written": ["smoke.csv"],
        "output_artifacts": [
            {
                "filename": "smoke.csv",
                "artifact_id": "a-self-test",
                "version_id": "v-self-test",
                "url": "/api/v1/artifacts/versions/v-self-test",
            }
        ],
        "figures": [],
    }


def run_self_test(stream: Any = None) -> int:
    """Prove each check refuses its wrong shape, without starting a daemon.

    Every entry is a callable that must either complete (the good shape is
    accepted) or raise the expected ``SmokeFailure`` check (the wrong shape is
    refused). A negative case that stops raising is a hole in the gate, and the
    self-test exits non-zero naming it — which is what makes INV-13's "temporarily
    undo the fix and watch it fail" mechanical for this file.
    """
    out = stream if stream is not None else io.StringIO()
    cases: list[tuple[str, Callable[[], Any]]] = []

    def case(name: str, action: Callable[[], Any]) -> None:
        cases.append((name, action))

    # serve
    case("health accepts ok", lambda: check_health(200, {"status": "ok"}))
    case(
        "health refuses a non-ok body",
        lambda: _expect_failure("serve", lambda: check_health(200, {"status": "x"})),
    )
    case(
        "health refuses a 500",
        lambda: _expect_failure("serve", lambda: check_health(500, {"status": "ok"})),
    )
    # auth
    case("auth accepts 2xx + 401", lambda: check_auth(200, 401))
    case(
        "auth refuses an open API",
        lambda: _expect_failure("auth", lambda: check_auth(200, 200)),
    )
    case(
        "auth refuses a failing authenticated call",
        lambda: _expect_failure("auth", lambda: check_auth(503, 401)),
    )
    # kernel_origin. The fixture tree has to outlive registration, because the
    # cases run after every callable has been recorded.
    import tempfile as _tempfile

    fixture_root = Path(_tempfile.mkdtemp(prefix="openai4s smoke self-test "))
    _cleanup_paths: list[Path] = [fixture_root]
    root = fixture_root / "app"
    root.mkdir()
    interpreter = root / "runtime" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", "utf-8")
    package = root / "src" / "openai4s" / "__init__.py"
    package.parent.mkdir(parents=True)
    package.write_text("", "utf-8")
    outside = fixture_root / "elsewhere" / "python3"
    outside.parent.mkdir()
    outside.write_text("", "utf-8")
    good = f"{interpreter}\n{package}\n3.13.0\n"
    case(
        "kernel_origin accepts in-tree paths",
        lambda: check_kernel_origin(good, root),
    )
    case(
        "kernel_origin refuses a host interpreter",
        lambda: _expect_failure(
            "kernel_origin",
            lambda: check_kernel_origin(f"{outside}\n{package}\n3.13.0\n", root),
        ),
    )
    case(
        "kernel_origin refuses a host package",
        lambda: _expect_failure(
            "kernel_origin",
            lambda: check_kernel_origin(f"{interpreter}\n{outside}\n3.13.0\n", root),
        ),
    )
    case(
        "kernel_origin refuses a short probe",
        lambda: _expect_failure(
            "kernel_origin", lambda: check_kernel_origin("only one line\n", root)
        ),
    )
    # state_shared
    case("state_shared accepts 42", lambda: check_state_shared("42\n"))
    case(
        "state_shared refuses 41",
        lambda: _expect_failure("state_shared", lambda: check_state_shared("41\n")),
    )
    # cell result
    case("cell accepts ok", lambda: check_cell(_good_cell(), check="state_shared"))
    broken = _good_cell()
    broken["cell"]["status"] = "error"
    broken["cell"]["error"] = "NameError: name 'x' is not defined"
    case(
        "cell refuses an error status",
        lambda: _expect_failure(
            "state_shared", lambda: check_cell(broken, check="state_shared")
        ),
    )
    case(
        "cell refuses a missing cell",
        lambda: _expect_failure(
            "state_shared",
            lambda: check_cell({"status": "accepted"}, check="state_shared"),
        ),
    )
    # artifact capture
    entry = _good_entry()
    case(
        "capture accepts the bound version",
        lambda: binding_for(entry, CSV_FILENAME, check="artifact_capture"),
    )
    missing = _good_entry()
    missing["output_artifacts"] = []
    case(
        "capture refuses an uncaptured file",
        lambda: _expect_failure(
            "artifact_capture",
            lambda: binding_for(missing, CSV_FILENAME, check="artifact_capture"),
        ),
    )
    version_row = {
        "version_id": "v-self-test",
        "checksum": sha256_bytes(CSV_FIRST),
        "size_bytes": len(CSV_FIRST),
    }
    case(
        "capture accepts matching bytes",
        lambda: check_version_bytes(
            CSV_FIRST, version_row, expected=CSV_FIRST, check="artifact_capture"
        ),
    )
    case(
        "capture refuses a forged digest",
        lambda: _expect_failure(
            "artifact_capture",
            lambda: check_version_bytes(
                CSV_SECOND, version_row, expected=CSV_FIRST, check="artifact_capture"
            ),
        ),
    )
    case(
        "capture refuses an empty digest",
        lambda: _expect_failure(
            "artifact_capture",
            lambda: check_version_bytes(
                CSV_FIRST,
                {"checksum": "", "size_bytes": len(CSV_FIRST)},
                check="artifact_capture",
            ),
        ),
    )
    # figure
    figured = _good_entry()
    figured["figures"] = ["figure-self-test.png"]
    figured["output_artifacts"].append(
        {
            "filename": "figure-self-test.png",
            "artifact_id": "a-figure",
            "version_id": "v-figure",
            "url": "/api/v1/artifacts/versions/v-figure",
        }
    )
    case("figure accepts one captured figure", lambda: figure_name(figured))
    case(
        "figure refuses no figure",
        lambda: _expect_failure("figure", lambda: figure_name(_good_entry())),
    )
    two = _good_entry()
    two["figures"] = ["a.png", "b.png"]
    for name in ("a.png", "b.png"):
        two["output_artifacts"].append(
            {
                "filename": name,
                "artifact_id": f"a-{name}",
                "version_id": f"v-{name}",
                "url": f"/api/v1/artifacts/versions/v-{name}",
            }
        )
    case(
        "figure refuses two figures",
        lambda: _expect_failure("figure", lambda: figure_name(two)),
    )
    case("figure accepts a PNG payload", lambda: check_png(PNG_MAGIC + b"data"))
    case(
        "figure refuses a non-PNG payload",
        lambda: _expect_failure("figure", lambda: check_png(b"not-a-png")),
    )
    # exact_version
    first_sha = sha256_bytes(CSV_FIRST)
    case(
        "exact_version accepts two distinct versions",
        lambda: check_exact_version(
            first_version_id="v1",
            second_version_id="v2",
            first_body=CSV_FIRST,
            first_sha=first_sha,
            second_body=CSV_SECOND,
        ),
    )
    case(
        "exact_version refuses a reused version id",
        lambda: _expect_failure(
            "exact_version",
            lambda: check_exact_version(
                first_version_id="v1",
                second_version_id="v1",
                first_body=CSV_FIRST,
                first_sha=first_sha,
                second_body=CSV_SECOND,
            ),
        ),
    )
    case(
        "exact_version refuses changed first bytes",
        lambda: _expect_failure(
            "exact_version",
            lambda: check_exact_version(
                first_version_id="v1",
                second_version_id="v2",
                first_body=CSV_SECOND,
                first_sha=first_sha,
                second_body=CSV_SECOND,
            ),
        ),
    )
    # restart_readback
    case(
        "restart accepts the same bytes and history",
        lambda: check_restart_readback(
            listed={"first": first_sha, "second": sha256_bytes(CSV_SECOND)},
            first_expected=first_sha,
            second_expected=sha256_bytes(CSV_SECOND),
            first_body=CSV_FIRST,
            second_body=CSV_SECOND,
            history_entries=5,
        ),
    )
    case(
        "restart refuses a changed digest",
        lambda: _expect_failure(
            "restart_readback",
            lambda: check_restart_readback(
                listed={"first": "b" * 64, "second": sha256_bytes(CSV_SECOND)},
                first_expected=first_sha,
                second_expected=sha256_bytes(CSV_SECOND),
                first_body=CSV_FIRST,
                second_body=CSV_SECOND,
                history_entries=5,
            ),
        ),
    )
    case(
        "restart refuses a short history",
        lambda: _expect_failure(
            "restart_readback",
            lambda: check_restart_readback(
                listed={"first": first_sha, "second": sha256_bytes(CSV_SECOND)},
                first_expected=first_sha,
                second_expected=sha256_bytes(CSV_SECOND),
                first_body=CSV_FIRST,
                second_body=CSV_SECOND,
                history_entries=1,
            ),
        ),
    )
    # browser_open
    case("browser accepts a passing script", lambda: check_browser_open(0, ""))
    case(
        "browser refuses a failing script",
        lambda: _expect_failure(
            "browser_open", lambda: check_browser_open(1, "assertion failed")
        ),
    )
    # clean_exit
    case(
        "clean_exit accepts an empty group and removed dir",
        lambda: check_clean_exit(daemon_gone=True, work_root_gone=True),
    )
    case(
        "clean_exit refuses a surviving worker",
        lambda: _expect_failure(
            "clean_exit",
            lambda: check_clean_exit(daemon_gone=False, work_root_gone=True),
        ),
    )
    case(
        "clean_exit refuses a leftover directory",
        lambda: _expect_failure(
            "clean_exit",
            lambda: check_clean_exit(daemon_gone=True, work_root_gone=False),
        ),
    )
    # isolation
    base = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/caller",
        "PYTHONPATH": "/some/source/checkout",
        "PYTHONHOME": "/opt/python",
        "VIRTUAL_ENV": "/tmp/other-venv",
        "OPENAI4S_WEBUI": "legacy",
        "OPENAI4S_LLM_API_KEY": "sk-not-a-real-key",
        "OPENAI4S_CLAUDE_API_KEY": "sk-not-a-real-key",
        "OPENAI4S_NOTEBOOK_REPL": "0",
        "OPENAI4S_UPDATE_SOURCE": "",
        "KEEP_ME": "1",
    }
    cleaned = clean_environment(
        base,
        data_dir=Path("/tmp/smoke/data"),
        home=Path("/tmp/smoke/home"),
        tmp_dir=Path("/tmp/smoke/tmp"),
        port=8123,
    )

    def isolation_holds() -> None:
        for forbidden in (
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "OPENAI4S_WEBUI",
            "OPENAI4S_LLM_API_KEY",
            "OPENAI4S_CLAUDE_API_KEY",
        ):
            assert forbidden not in cleaned, f"the daemon environment kept {forbidden}"
        assert cleaned["KEEP_ME"] == "1", "harmless caller variables must pass through"
        assert cleaned["OPENAI4S_DATA_DIR"] == "/tmp/smoke/data"
        assert cleaned["HOME"] == "/tmp/smoke/home"
        assert cleaned["OPENAI4S_HOST"] == "127.0.0.1"
        assert cleaned["OPENAI4S_PORT"] == "8123"
        assert cleaned["OPENAI4S_NOTEBOOK_REPL"] == "1"
        assert cleaned["OPENAI4S_UPDATE_SOURCE"] == "offline"

    case("isolation scrubs the child environment", isolation_holds)
    # redaction + bounded diagnostics
    secret = "tok-" + "z" * 24

    def redaction_holds() -> None:
        quoted = f"failed http://127.0.0.1:1/?token={secret}"
        cleaned_text = redact(quoted, secret)
        assert secret not in cleaned_text, "the literal token survived redaction"
        assert "token=" in redact(f"?token={secret}", secret), "the query form was lost"

    case("redaction removes the credential", redaction_holds)
    long_detail = "x" * (DETAIL_LIMIT + 50)

    def detail_is_bounded() -> None:
        assert len(bounded(long_detail)) <= DETAIL_LIMIT + 40
        assert "more characters" in bounded(long_detail)

    case("failure detail is bounded", detail_is_bounded)
    # report shape and verdict
    good_checks = {name: PASSED for name in CHECKS}
    good_checks["browser_open"] = NOT_RUN
    good_checks["figure"] = PASSED
    report = build_report(
        target="bundle",
        artifact_sha256="c" * 64,
        platform_name="linux",
        arch="x86_64",
        python_version="3.13.0",
        checks=good_checks,
        duration_s=12.0,
    )

    def report_passed() -> None:
        assert report["status"] == PASSED
        assert report["failure"] is None

    case("report derives passed", report_passed)
    failed_report = build_report(
        target="bundle",
        artifact_sha256="c" * 64,
        platform_name="linux",
        arch="x86_64",
        python_version="3.13.0",
        checks={**good_checks, "state_shared": FAILED},
        duration_s=12.0,
        failure={"check": "state_shared", "detail": "x"},
    )

    def report_failed() -> None:
        assert failed_report["status"] == FAILED
        assert failed_report["failure"]["check"] == "state_shared"
        assert failed_report["failure"]["detail"]

    case("report derives failed with a named check", report_failed)
    wheel_checks = {**good_checks, "figure": NOT_APPLICABLE}
    wheel_report = build_report(
        target="wheel",
        artifact_sha256="d" * 64,
        platform_name="darwin",
        arch="aarch64",
        python_version="3.13.0",
        checks=wheel_checks,
        duration_s=3.0,
    )

    def wheel_figure_not_applicable() -> None:
        assert wheel_report["status"] == PASSED
        assert wheel_report["checks"]["figure"] == NOT_APPLICABLE

    case(
        "report accepts figure not_applicable for a wheel", wheel_figure_not_applicable
    )
    refused_checks = {**good_checks, "figure": NOT_RUN}

    def bundle_needs_a_figure() -> None:
        refused = build_report(
            target="bundle",
            artifact_sha256="c" * 64,
            platform_name="linux",
            arch="x86_64",
            python_version="3.13.0",
            checks=refused_checks,
            duration_s=1.0,
        )
        assert refused["status"] == FAILED

    case("report refuses a bundle report without a figure check", bundle_needs_a_figure)

    def report_needs_a_digest() -> None:
        refused = build_report(
            target="bundle",
            artifact_sha256="",
            platform_name="linux",
            arch="x86_64",
            python_version="3.13.0",
            checks=good_checks,
            duration_s=1.0,
        )
        assert refused["status"] == FAILED
        assert refused["failure"]["detail"]

    case("report refuses a missing artifact digest", report_needs_a_digest)

    def unknown_result_refused() -> None:
        assert _raises_value_error(
            lambda: build_report(
                target="bundle",
                artifact_sha256="c" * 64,
                platform_name="linux",
                arch="x86_64",
                python_version="3.13.0",
                checks={**good_checks, "serve": "maybe"},
                duration_s=1.0,
            )
        )

    case("report refuses an unknown check result", unknown_result_refused)
    # budgets
    expired = Budget(total_s=0.01)

    def expire() -> None:
        time.sleep(0.02)
        expired.for_step("serve")

    case(
        "budgets fail closed when the total expires",
        lambda: _expect_failure("serve", expire),
    )

    def step_is_capped() -> None:
        assert Budget(total_s=5.0).for_step("cell") <= 5.0

    case("budgets cap a step at the total remaining", step_is_capped)
    # digest binding
    artifact_path = fixture_root / "artifact.bin"
    artifact_path.write_bytes(b"artifact-bytes")

    def digest_binds() -> None:
        assert (
            sha256_file(artifact_path) == hashlib.sha256(b"artifact-bytes").hexdigest()
        )
        assert sha256_file(artifact_path) != sha256_bytes(b"other-bytes")

    case("artifact digest matches the input bytes", digest_binds)

    failures = 0
    for name, action in cases:
        try:
            action()
        except AssertionError as error:
            failures += 1
            out.write(f"self-test: FAILED {name}: {error}\n")
        except Exception as error:  # noqa: BLE001 - a harness bug is a failure
            failures += 1
            out.write(f"self-test: ERROR {name}: {type(error).__name__}: {error}\n")
        else:
            out.write(f"self-test: ok {name}\n")
    for path in _cleanup_paths:
        shutil.rmtree(path, ignore_errors=True)
    out.write(
        f"self-test: {'passed' if failures == 0 else 'FAILED'} "
        f"({len(cases) - failures}/{len(cases)} cases)\n"
    )
    return 0 if failures == 0 else 1


def _raises_value_error(action: Callable[[], Any]) -> bool:
    try:
        action()
    except ValueError:
        return True
    return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the installed OpenAI4S runtime science smoke (scenario version 1): "
            "serve, authenticate, execute Cells in the packaged kernel, capture and "
            "re-read an artifact version, restart, and confirm no process leaks."
        )
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="the Linux .tar.gz whose embedded runtime should be exercised",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="the interpreter of an installed wheel environment to exercise",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        default=None,
        help=(
            "with --python: the wheel file the interpreter was installed from, so "
            "the report can name the wheel digest"
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="where to write the B.4 JSON report",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="also run the R2 browser layer against the captured artifacts",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="drive every decision check with synthetic responses (no daemon)",
    )
    return parser


def _usage(parser: argparse.ArgumentParser, message: str) -> int:
    """A usage error is exit 2, matching argparse, without raising out of main."""
    parser.print_usage(sys.stderr)
    print(f"{parser.prog}: error: {message}", file=sys.stderr)
    return 2


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", "utf-8")


def _preparation_failure_report(
    *,
    target: str,
    artifact_sha256: str,
    arch: str,
    failure: SmokeFailure,
) -> dict[str, Any]:
    return build_report(
        target=target,  # type: ignore[arg-type]
        artifact_sha256=artifact_sha256,
        platform_name="linux" if target == "bundle" else platform.system().lower(),
        arch=arch,
        python_version=platform.python_version(),
        checks={},
        duration_s=0.0,
        failure={"check": failure.check, "detail": failure.detail},
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    try:
        options = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exit_code:  # argparse already printed usage
        return int(exit_code.code or 0)
    if options.self_test:
        return run_self_test(sys.stdout)
    if options.bundle is not None and options.python is not None:
        return _usage(parser, "pass exactly one of --bundle or --python")
    if options.bundle is None and options.python is None:
        return _usage(parser, "pass --bundle, --python or --self-test")
    if options.report is None:
        return _usage(parser, "--report is required")
    if options.browser and options.bundle is None:
        return _usage(parser, "--browser applies to the bundle mode")

    started = time.monotonic()
    work_root = Path(tempfile.mkdtemp(prefix="openai4s runtime smoke "))
    report: dict[str, Any]
    try:
        if options.bundle is not None:
            tarball = Path(options.bundle)
            try:
                metadata = bundle_metadata(tarball)
            except SmokeFailure as error:
                report = _preparation_failure_report(
                    target="bundle", artifact_sha256="", arch="", failure=error
                )
            else:
                try:
                    runtime = runtime_for_bundle(tarball, work_root)
                except SmokeFailure as error:
                    report = _preparation_failure_report(
                        target="bundle",
                        artifact_sha256=str(metadata["sha256"]),
                        arch=str(metadata["arch"]),
                        failure=error,
                    )
                else:
                    report = Scenario(
                        runtime=runtime,
                        work_root=work_root,
                        browser=bool(options.browser),
                    ).run()
        else:
            interpreter = Path(options.python)
            try:
                runtime = runtime_for_python(
                    interpreter,
                    artifact=Path(options.artifact) if options.artifact else None,
                    work_root=work_root,
                )
            except SmokeFailure as error:
                report = _preparation_failure_report(
                    target="wheel",
                    artifact_sha256="",
                    arch=normalized_arch(platform.machine()),
                    failure=error,
                )
            else:
                report = Scenario(
                    runtime=runtime,
                    work_root=work_root,
                    browser=False,
                ).run()
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    report["duration_s"] = round(time.monotonic() - started, 3)

    _write_report(Path(options.report), report)
    if report["status"] == PASSED:
        print(
            "installed runtime smoke: passed — "
            f"{report['target']} {report['artifact_sha256'][:12]} "
            f"({report['duration_s']}s)"
        )
        return 0
    failure = report.get("failure") or {}
    print(
        "installed runtime smoke: FAILED — "
        f"{failure.get('check', 'scenario')}: {failure.get('detail', '')}",
        file=sys.stderr,
    )
    print(f"report: {Path(options.report)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
