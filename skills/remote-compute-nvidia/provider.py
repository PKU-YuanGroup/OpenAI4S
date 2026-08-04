"""NvidiaProvider — the NVIDIA-NIM shim for host.compute.create("byoc:nvidia").

The only provider file that knows how NVIDIA NIM microservices are reached. It
speaks two forms that share ONE JSON schema and ONE exec contract, selected by
``provider_params={'nvidia': {'mode': ...}}``:

  * self_hosted — pull and run an NVIDIA NIM container from ``nvcr.io`` on a
    local GPU host (``--gpus all``). The NIM server is the container's own
    long-lived process; the job script curls it at ``http://localhost:8000``
    and health-gates on ``/v1/health/ready``. Auth is an NGC API key
    (``NGC_API_KEY``) used only to ``docker login nvcr.io`` at create time.

  * hosted — no GPU needed locally. A slim keepalive container is started and
    the job script curls the fully-managed endpoint at
    ``https://integrate.api.nvidia.com`` with a ``Bearer nvapi-…`` key
    (``NVIDIA_API_KEY``).

Both forms create a Docker container that plays the role of the byoc
"sandbox": the base ``ByocResident`` untars inputs into ``/work``, launches the
job wrapper, and harvests ``/work/out.tar.gz`` — identical to every other byoc
provider. Ownership + reconciliation ride Docker labels. Stdlib + the ``docker``
CLI only; no third-party SDK, so the open-source install needs nothing beyond
Docker (and the NVIDIA Container Toolkit for the self-hosted GPU form).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from typing import Any, Callable, Iterable, NoReturn

from openai4s_compute_provider import WORK, ByocError, ExecResult

# Ownership label — the tenancy boundary every op checks. A container this
# install did not create reads back a different owner and is refused.
OWNER_LABEL = "openai4s-install-id"
SESSION_LABEL = "openai4s-session"
JOB_LABEL = "openai4s-job"
# Marks every container we manage, so list_owned can filter cheaply.
MANAGED_LABEL = "openai4s-managed=nvidia"

# Managed endpoints (both forms). The self-hosted server always listens here
# inside its own container; the hosted API is NVIDIA's fully-managed gateway.
SELF_HOSTED_URL = "http://localhost:8000"
HOSTED_URL = "https://integrate.api.nvidia.com"
# Slim image for the hosted form's keepalive container — only needs curl + a
# writable /work. Overridable via provider_params for air-gapped mirrors.
HOSTED_KEEPALIVE_IMAGE = "curlimages/curl:8.10.1"
# NIM readiness probe (self-hosted). The wrapper's run.sh gates on it.
HEALTH_PATH = "/v1/health/ready"
DOCKER_TIMEOUT_S = 300


class NvidiaProvider:
    # Scrubbed from the process env in the base prologue BEFORE any op runs,
    # so a forwarded key can never leak through a child's environment.
    secret_env_prefixes = ("NGC_", "NVIDIA_")
    # nvapi-… (hosted) and nvcr.io NGC keys (self-hosted, nvcf-/base64-ish) —
    # redacted from every stdout/stderr tail that leaves the confined helper.
    token_scrub_regex = re.compile(
        r"\bnvapi-[A-Za-z0-9_\-]{8,}|\bnvcf-[A-Za-z0-9_\-]{8,}"
    )

    def __init__(self, *, repl: bool = False):
        self._repl = repl
        self._creds: dict[str, str] = {}
        self._mode = "hosted"

    # ── auth + import ─────────────────────────────────────────────────────
    def apply_auth(self, creds: dict[str, str]) -> None:
        """Stash the credential handed over on the helper's control channel.
        The keys are the env-var names declared under ``secret_env`` in
        provider.json (``NGC_API_KEY`` / ``NVIDIA_API_KEY``): the host
        forwards each declared var's value under its own name. For the
        self-hosted form the NGC key runs ``docker login nvcr.io``; for the
        hosted form the ``nvapi-…`` key is injected into the job container's
        env as ``NVIDIA_API_KEY``."""
        self._creds = dict(creds or {})

    def import_and_patch(self) -> None:
        """No third-party SDK to import — the provider shells out to the
        ``docker`` CLI. Verify it's on PATH so a missing-Docker install fails
        with a clear, structured error instead of a bare FileNotFoundError
        mid-op."""
        if not self._docker_available():
            raise ByocError(
                "provider_degraded",
                "the `docker` CLI is not available — the NVIDIA byoc provider "
                "runs NIM microservices in Docker (self-hosted needs the "
                "NVIDIA Container Toolkit for --gpus). Install Docker and, for "
                "the self-hosted GPU form, the NVIDIA Container Toolkit.",
            )

    def install_unauth_hook(self, on_expired: Callable[[], NoReturn]) -> None:
        # REST auth is per-request (the job script carries the key); there is
        # no long-lived authenticated channel to watch for expiry, so this is
        # a no-op. Kept to satisfy the ByocProvider protocol.
        return None

    # ── helper-mode ops ───────────────────────────────────────────────────
    def create_sandbox(
        self, spec: dict[str, Any], install_id: str, tags: dict[str, str] | None = None
    ) -> str:
        mode = (spec.get("mode") or "hosted").replace("-", "_")
        if mode not in ("hosted", "self_hosted"):
            raise ByocError(
                "invalid_request",
                f"nvidia mode must be 'hosted' or 'self_hosted'; got "
                f"{spec.get('mode')!r}",
            )
        self._mode = mode
        name = "openai4s-nvidia-" + uuid.uuid4().hex[:12]
        labels = self._label_args(
            {
                **{k: v for k, v in (tags or {}).items() if k != OWNER_LABEL},
                # owner stamped LAST so an incoming tag can never override it.
                OWNER_LABEL: install_id,
            }
        )
        if mode == "self_hosted":
            cid = self._create_self_hosted(spec, name, labels)
        else:
            cid = self._create_hosted(spec, name, labels)
        return cid

    def _create_self_hosted(self, spec: dict, name: str, labels: list[str]) -> str:
        image = spec.get("image")
        if not image:
            raise ByocError(
                "invalid_request",
                "self_hosted nvidia needs provider_params['nvidia']['image'] "
                "— an nvcr.io NIM image, e.g. "
                "'nvcr.io/nim/meta/alphafold2:1.0.0'",
            )
        # NGC login so the private nvcr.io pull succeeds. The key is '$oauthtoken'
        # username + the NGC API key as password (NGC's documented scheme).
        key = self._creds.get("NGC_API_KEY") or os.environ.get("NGC_API_KEY")
        if key:
            self._docker(
                ["login", "nvcr.io", "--username", "$oauthtoken", "--password-stdin"],
                stdin=key.encode(),
            )
        run = [
            "run",
            "-d",
            "--name",
            name,
            "--gpus",
            "all",
            "--shm-size=16g",
            *labels,
            # The NIM server is PID 1 and keeps the container alive; we
            # docker-exec the job wrapper alongside it. NGC key is the
            # container's own model-download credential.
            "-e",
            "NGC_API_KEY",
            image,
        ]
        env = dict(os.environ)
        if key:
            env["NGC_API_KEY"] = key
        cid = self._docker(run, env=env).strip()
        return cid

    def _create_hosted(self, spec: dict, name: str, labels: list[str]) -> str:
        image = spec.get("keepalive_image") or HOSTED_KEEPALIVE_IMAGE
        # A slim keepalive container: no GPU, just a writable /work the base
        # resident untars into. The job script curls the managed endpoint.
        run = [
            "run",
            "-d",
            "--name",
            name,
            "--entrypoint",
            "sleep",
            *labels,
            image,
            "infinity",
        ]
        cid = self._docker(run).strip()
        return cid

    def exec(
        self,
        sandbox_id: str,
        argv: list[str],
        *,
        stdin: Iterable[bytes] | None = None,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> ExecResult:
        """Run argv inside the container via ``docker exec``. The managed
        endpoint URL and the hosted API key are injected as env so the job's
        run.sh is provider-agnostic: it curls ``$OPENAI4S_NIM_URL`` with
        ``$NVIDIA_API_KEY`` and never hard-codes hosted vs self-hosted."""
        cmd = ["exec", "-i"]
        # Inject the endpoint + key the job script reads.
        exec_env = {
            "OPENAI4S_NIM_URL": (
                HOSTED_URL if self._mode == "hosted" else SELF_HOSTED_URL
            ),
            "OPENAI4S_NIM_HEALTH": HEALTH_PATH,
        }
        if self._mode == "hosted":
            key = self._creds.get("NVIDIA_API_KEY") or os.environ.get("NVIDIA_API_KEY")
            if key:
                exec_env["NVIDIA_API_KEY"] = key
        exec_env.update(env or {})
        for k, v in exec_env.items():
            cmd += ["-e", f"{k}={v}"]
        cmd += [sandbox_id, *argv]
        try:
            proc = subprocess.Popen(
                ["docker", *cmd],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as e:
            raise self._map_err(e) from None
        if stdin is not None:
            try:
                for chunk in stdin:
                    proc.stdin.write(chunk)
                proc.stdin.close()
            except BrokenPipeError:
                pass
        return _ExecAdapter(proc)

    def list_owned(self, install_id: str) -> list[dict[str, Any]]:
        out = self._docker(
            [
                "ps",
                "-a",
                "--no-trunc",
                "--filter",
                f"label={MANAGED_LABEL}",
                "--filter",
                f"label={OWNER_LABEL}={install_id}",
                "--format",
                "{{.ID}}\t{{.Labels}}",
            ]
        )
        sandboxes: list[dict[str, Any]] = []
        for line in out.splitlines():
            if not line.strip():
                continue
            cid, _, raw = line.partition("\t")
            sandboxes.append(
                {"sandbox_id": cid.strip(), "tags": self._parse_labels(raw)}
            )
        return sandboxes

    def read_owner(self, sandbox_id: str) -> str | None:
        try:
            out = self._docker(
                [
                    "inspect",
                    "-f",
                    '{{index .Config.Labels "' + OWNER_LABEL + '"}}',
                    sandbox_id,
                ]
            )
        except ByocError as e:
            if e.kind == "not_found":
                return None
            raise
        owner = out.strip()
        return owner or None

    def terminate(self, sandbox_id: str) -> None:
        try:
            self._docker(["rm", "-f", sandbox_id])
        except ByocError as e:
            if e.kind == "not_found":
                return  # already gone — terminate is idempotent
            raise

    # ── internals ─────────────────────────────────────────────────────────
    @staticmethod
    def _docker_available() -> bool:
        try:
            subprocess.run(["docker", "version"], capture_output=True, timeout=30)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _docker(
        self, args: list[str], *, stdin: bytes | None = None, env: dict | None = None
    ) -> str:
        try:
            proc = subprocess.run(
                ["docker", *args],
                input=stdin,
                capture_output=True,
                timeout=DOCKER_TIMEOUT_S,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise ByocError(
                "transient", f"docker {args[0]} timed out after " f"{DOCKER_TIMEOUT_S}s"
            ) from e
        except OSError as e:
            raise self._map_err(e) from None
        if proc.returncode != 0:
            raise self._map_err_output(proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8", "replace")

    @staticmethod
    def _label_args(labels: dict[str, str]) -> list[str]:
        args: list[str] = ["--label", MANAGED_LABEL]
        for k, v in labels.items():
            args += ["--label", f"{k}={v}"]
        return args

    @staticmethod
    def _parse_labels(raw: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for pair in raw.split(","):
            k, _, v = pair.partition("=")
            if k:
                out[k.strip()] = v.strip()
        return out

    def _map_err_output(self, stderr: str) -> ByocError:
        s = stderr.lower()
        if "no such container" in s or "no such object" in s:
            return ByocError("not_found", stderr.strip()[:400])
        if "unauthorized" in s or "denied" in s or "login" in s:
            return ByocError(
                "unauthorized",
                "NGC/nvcr.io rejected the credential — check NGC_API_KEY has "
                f"pull access to the NIM image. ({stderr.strip()[:300]})",
            )
        if (
            "could not select device" in s
            or "nvidia-container" in s
            or "gpu" in s
            and "not" in s
        ):
            return ByocError(
                "provider_degraded",
                "the NVIDIA Container Toolkit / GPU runtime is not available "
                "for --gpus. Install it, or use the hosted form "
                f"(mode='hosted'). ({stderr.strip()[:300]})",
            )
        if "toomanyrequests" in s or "rate" in s:
            return ByocError("rate_limited", stderr.strip()[:400])
        return ByocError("transient", stderr.strip()[:400] or "docker error")

    def _map_err(self, e: Exception) -> ByocError:
        return ByocError("transient", f"docker invocation failed: {e!r}")


class _ExecAdapter:
    """Adapts a docker-exec subprocess to the base package's ExecResult
    (stdout iterator + stderr iterator + wait())."""

    def __init__(self, proc: subprocess.Popen):
        self._p = proc

    @property
    def stdout(self) -> Iterable[bytes]:
        if self._p.stdout is None:
            return
        for chunk in iter(lambda: self._p.stdout.read(65536), b""):
            yield chunk

    @property
    def stderr(self) -> Iterable[bytes]:
        if self._p.stderr is None:
            return
        for chunk in iter(lambda: self._p.stderr.read(65536), b""):
            yield chunk

    def wait(self) -> int:
        rc = self._p.wait()
        return rc if rc is not None else 137


PROVIDER = NvidiaProvider
