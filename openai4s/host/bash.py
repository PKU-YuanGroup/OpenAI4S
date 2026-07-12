"""Host-side authorization service for kernel-local ``host.bash``.

The security boundary is deliberate: this module never imports ``subprocess``
and never executes a command.  It only validates a proposed execution, issues a
short-lived random bearer capability, consumes that capability exactly once,
and records the worker-reported result through injected audit/step sinks.

Capabilities are server-state-backed rather than self-authenticating.  The
random token is useful only while its exact command digest, canonical cwd,
worker generation, challenge, and session frame remain present in this
service's in-memory issuance table.  Restarting the daemon invalidates every
outstanding capability, which is the safe recovery behaviour for shell work.
"""
from __future__ import annotations

import fnmatch
import hashlib
import math
import re
import secrets
import shlex
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from openai4s.bash_capability import CAPABILITY_VERSION, command_digest

DEFAULT_TTL_SECONDS = 15.0
MAX_TTL_SECONDS = 60.0
_MAX_OUTSTANDING = 1024
_RESULT_RETENTION_SECONDS = 3600.0

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)"
    r"[A-Z0-9_]*)\s*=\s*(?:'[^']*'|\"[^\"]*\"|[^\s;&|]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_CREDENTIAL_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:sk|ark|ghp|github_pat|hf|xox[baprs])-" r"[A-Za-z0-9_\-]{8,}"
)


def redact_shell_text(value: Any, *, limit: int = 4000) -> str:
    """Return a bounded preview with common credential shapes removed.

    Raw commands and full stdout/stderr never cross the persistence boundary.
    This helper is intentionally conservative and shared by approval, activity,
    and audit projections so a command-line secret is not copied into any of
    those records.
    """

    text = str(value or "")
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    text = _CREDENTIAL_TOKEN_RE.sub("<redacted-token>", text)
    text = _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    return text[: max(0, int(limit))]


def _looks_secret_name(name: str) -> bool:
    return (
        name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key"))
        or name in {"id_rsa", "id_ed25519", ".netrc", ".pgpass"}
    )


# Representative secret basenames a shell glob could expand to.  Used to reject
# ``cat .e*`` / ``cat *.key`` whose literal token is not itself a secret name.
_SECRET_SAMPLE_NAMES = (
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    ".netrc",
    ".pgpass",
    "server.pem",
    "private.key",
)


def _contains_secret_path(command: str) -> bool:
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = re.split(r"\s+", command)
    candidates: list[str] = []
    for raw in words:
        value = raw.strip("'\";|&<>")
        candidates.append(value)
        # ``VAR=path`` hides the real target on the value side of the token
        # (e.g. ``f=.env; cat $f``); scan it too.
        if "=" in value and not value.startswith("="):
            candidates.append(value.split("=", 1)[1])
    for value in candidates:
        name = Path(value).name.lower()
        if _looks_secret_name(name):
            return True
        # A shell glob (``.e*``, ``*.key``, ``.env*``) expands at runtime to a
        # secret file even though the literal token is not one.
        if any(ch in name for ch in "*?[") and any(
            fnmatch.fnmatchcase(sample, name) for sample in _SECRET_SAMPLE_NAMES
        ):
            return True
    return False


def _safe_persisted_path(value: Any) -> str:
    path = str(value or "")
    name = Path(path).name.lower()
    if (
        name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key"))
        or name in {"id_rsa", "id_ed25519", ".netrc", ".pgpass"}
    ):
        return "<secret-path>"
    return path[:500]


def classify_command(command: str, domains: Iterable[str] = ()) -> str:
    """Classify a command for the authorization/audit record.

    This is policy metadata, not a shell parser.  The independent static safety
    gate and permission target remain authoritative.
    """

    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.strip().split()
    executable = Path(words[0]).name.lower() if words else ""
    lowered = command.lower()
    if tuple(domains) or executable in {"curl", "wget", "ssh", "scp", "ftp"}:
        return "network"
    if executable in {"pip", "pip3", "conda", "mamba", "npm", "uv"} and any(
        marker in words for marker in ("install", "add", "sync")
    ):
        return "package_install"
    writes_files = executable in {
        "rm",
        "mv",
        "cp",
        "mkdir",
        "touch",
        "chmod",
        "chown",
    }
    if writes_files or re.search(r"(?:^|[^>])>>?\s*[^&]", lowered):
        return "filesystem_write"
    if executable in {"cat", "head", "tail", "less", "ls", "find", "grep", "rg"}:
        return "filesystem_read"
    return "process"


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _finite_number(value: Any, *, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"bash authorization: {name} must be a number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"bash authorization: {name} must be finite and positive")
    return number


@dataclass
class _IssuedCapability:
    token: str
    command_sha256: str
    cwd: str
    workspace: str
    allowed_root: str
    frame_id: str | None
    generation: str
    challenge: str
    category: str
    domains: tuple[str, ...]
    issued_at: float
    expires_at: float
    consumed_at: float | None = None
    recorded_at: float | None = None
    step_id: str | None = None
    step_kind: str = "bash"
    step_title: str = "Running shell command"

    def public_payload(self) -> dict[str, Any]:
        return {
            "version": CAPABILITY_VERSION,
            "token": self.token,
            "command_sha256": self.command_sha256,
            "cwd": self.cwd,
            "workspace": self.workspace,
            "allowed_root": self.allowed_root,
            "frame_id": self.frame_id,
            "generation": self.generation,
            "challenge": self.challenge,
            "category": self.category,
            "domains": list(self.domains),
            "issued_at_ms": int(self.issued_at * 1000),
            "expires_at_ms": int(self.expires_at * 1000),
        }


class BashAuthorizationService:
    """Issue, consume, and audit one-shot kernel shell capabilities."""

    def __init__(
        self,
        *,
        workspace: Callable[[], str | Path],
        frame_id: Callable[[], str | None],
        generation: Callable[[], str | int | None] | None = None,
        allowed_roots: Callable[[], Iterable[str | Path]] | None = None,
        audit: Callable[..., Any] | None = None,
        step_sink: Callable[[], Callable[[dict], Any] | None] | None = None,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] | None = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._workspace = workspace
        self._frame_id = frame_id
        self._generation = generation or (lambda: None)
        self._allowed_roots = allowed_roots or (lambda: ())
        self._audit = audit
        self._step_sink = step_sink or (lambda: None)
        self._clock = clock
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._ttl = min(
            MAX_TTL_SECONDS,
            _finite_number(ttl_seconds, name="capability TTL"),
        )
        self._lock = threading.RLock()
        self._issued: dict[str, _IssuedCapability] = {}

    def authorize(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Validate a proposal and issue a short-lived random capability."""

        command = spec.get("command")
        if not isinstance(command, str) or not command.strip():
            return {"error": "bash authorization: command must be a non-empty string"}
        if "\x00" in command:
            return {"error": "bash authorization: command contains a NUL byte"}
        challenge = spec.get("challenge")
        generation = spec.get("generation")
        if not isinstance(challenge, str) or len(challenge) < 16:
            return {"error": "bash authorization: missing worker challenge"}
        if not isinstance(generation, str) or not generation.strip():
            return {"error": "bash authorization: missing worker generation"}
        trusted_generation = self._generation()
        if trusted_generation is not None:
            trusted_generation = str(trusted_generation)
            if generation != trusted_generation:
                return {
                    "error": "bash authorization: worker generation does not match "
                    "the active Host generation"
                }
            generation = trusted_generation

        try:
            timeout = _finite_number(spec.get("timeout", 120), name="timeout")
        except ValueError as exc:
            return {"error": str(exc)}
        if timeout > 7 * 24 * 3600:
            return {"error": "bash authorization: timeout exceeds the 7 day limit"}

        try:
            workspace = Path(self._workspace()).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"error": f"bash authorization: workspace unavailable: {exc}"}
        if not workspace.is_dir():
            return {"error": "bash authorization: workspace is not a directory"}

        requested_workspace = spec.get("workspace")
        if requested_workspace:
            try:
                worker_workspace = (
                    Path(str(requested_workspace)).expanduser().resolve(strict=True)
                )
            except (OSError, RuntimeError, ValueError):
                return {"error": "bash authorization: worker workspace is invalid"}
            if worker_workspace != workspace:
                return {
                    "error": (
                        "bash authorization: worker workspace does not match "
                        "the session"
                    )
                }

        raw_cwd = spec.get("cwd")
        if not isinstance(raw_cwd, str) or not raw_cwd:
            return {"error": "bash authorization: cwd is required"}
        try:
            cwd = Path(raw_cwd).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return {"error": "bash authorization: cwd does not exist"}
        if not cwd.is_dir():
            return {"error": "bash authorization: cwd is not a directory"}

        roots = [workspace]
        try:
            for root in self._allowed_roots():
                resolved = Path(root).expanduser().resolve(strict=True)
                if resolved.is_dir() and resolved not in roots:
                    roots.append(resolved)
        except (OSError, RuntimeError, ValueError) as exc:
            return {"error": f"bash authorization: an allowed root is invalid: {exc}"}
        allowed_root = next((root for root in roots if _within(cwd, root)), None)
        if allowed_root is None:
            return {
                "error": (
                    "bash authorization: workdir escapes the workspace/allowed roots"
                )
            }

        # Repeat the worker's cheap safety and egress checks in the trusted host;
        # never trust worker-supplied category/domain metadata.
        try:
            from openai4s.security.shellcheck import precheck_command

            reason = precheck_command(command)
        except Exception as exc:  # noqa: BLE001 — authorization must fail closed
            return {"error": f"bash authorization: safety precheck unavailable: {exc}"}
        if reason:
            return {"error": f"bash: blocked by static safety precheck: {reason}"}
        if _contains_secret_path(command):
            return {
                "error": (
                    "bash authorization: commands referencing secret files are blocked"
                )
            }

        try:
            from openai4s import egress

            domains = tuple(egress.command_domains(command))
            if egress.egress_mode() == "allowlist":
                blocked = next(
                    (domain for domain in domains if not egress.domain_allowed(domain)),
                    None,
                )
                if blocked is not None:
                    return {"error": egress.blocked_message(blocked)}
        except Exception as exc:  # noqa: BLE001 — host policy lookup fails closed
            return {"error": f"bash authorization: egress policy unavailable: {exc}"}

        digest = command_digest(command)
        claimed_digest = spec.get("command_sha256")
        if claimed_digest is not None and claimed_digest != digest:
            return {"error": "bash authorization: command digest mismatch"}

        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            if len(self._issued) >= _MAX_OUTSTANDING:
                return {
                    "error": "bash authorization: too many outstanding capabilities"
                }
            token = self._token_factory()
            if not isinstance(token, str) or len(token) < 24 or token in self._issued:
                return {"error": "bash authorization: token generation failed closed"}
            capability = _IssuedCapability(
                token=token,
                command_sha256=digest,
                cwd=str(cwd),
                workspace=str(workspace),
                allowed_root=str(allowed_root),
                frame_id=self._frame_id(),
                generation=generation,
                challenge=challenge,
                category=classify_command(command, domains),
                domains=domains,
                issued_at=now,
                expires_at=now + self._ttl,
            )
            self._issued[token] = capability
            return capability.public_payload()

    def attach_step(
        self,
        token: str,
        *,
        step_id: str | None,
        view: tuple[str, str, dict] | None,
    ) -> bool:
        """Attach the dispatcher's already-emitted running step to a token."""

        if not token or not step_id:
            return False
        with self._lock:
            capability = self._issued.get(token)
            if capability is None:
                return False
            capability.step_id = step_id
            if view:
                capability.step_kind = str(view[0] or "bash")
                capability.step_title = str(view[1] or "Running shell command")
            return True

    def consume(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Atomically consume an issued capability immediately before spawn."""

        token = spec.get("token")
        if not isinstance(token, str) or not token:
            return {"error": "bash authorization: token is required"}
        now = self._clock()
        with self._lock:
            capability = self._issued.get(token)
            if capability is None:
                return {"error": "bash authorization: unknown or expired token"}
            if capability.consumed_at is not None:
                return {"error": "bash authorization: token was already consumed"}
            if now >= capability.expires_at:
                self._issued.pop(token, None)
                return {"error": "bash authorization: token expired before execution"}
            mismatch = self._binding_mismatch(capability, spec)
            if mismatch:
                return {
                    "error": (
                        f"bash authorization: token binding mismatch ({mismatch})"
                    )
                }
            capability.consumed_at = now
            return {
                "ok": True,
                "token": token,
                "consumed_at_ms": int(now * 1000),
            }

    def record_result(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Record a bounded/redacted result reported by the worker.

        Result recording cannot grant or execute anything.  The token must have
        been consumed, may be recorded once, and is removed after the record is
        projected so replayed reports cannot overwrite an earlier audit event.
        """

        token = spec.get("token")
        if not isinstance(token, str) or not token:
            return {"error": "bash result: token is required"}
        now = self._clock()
        with self._lock:
            capability = self._issued.get(token)
            if capability is None:
                return {"error": "bash result: unknown token"}
            if capability.consumed_at is None:
                return {"error": "bash result: token was not consumed"}
            if capability.recorded_at is not None:
                return {"error": "bash result: token was already recorded"}
            mismatch = self._binding_mismatch(capability, spec)
            if mismatch:
                return {"error": f"bash result: token binding mismatch ({mismatch})"}
            capability.recorded_at = now

            safe = self._safe_result(capability, spec)
            step_id = capability.step_id
            self._issued.pop(token, None)

        ok = safe["status"] == "completed" and safe["exit_code"] == 0
        if self._audit is not None:
            try:
                self._audit(
                    method="bash",
                    args=[safe],
                    ok=ok,
                    frame_id=capability.frame_id,
                )
            except Exception:  # noqa: BLE001 — command already ran; do not lie
                pass
        sink = self._step_sink()
        if sink is not None and step_id:
            try:
                sink(
                    {
                        "phase": "end",
                        "step_id": step_id,
                        "status": "done" if ok else "error",
                        "output": safe,
                        "summary": self._summary(safe),
                    }
                )
            except Exception:  # noqa: BLE001 — audit projection is best-effort
                pass
        return {"ok": True, "audit": safe}

    @staticmethod
    def _binding_mismatch(
        capability: _IssuedCapability, spec: dict[str, Any]
    ) -> str | None:
        expected = {
            "command_sha256": capability.command_sha256,
            "cwd": capability.cwd,
            "generation": capability.generation,
            "challenge": capability.challenge,
        }
        for key, value in expected.items():
            if spec.get(key) != value:
                return key
        return None

    @staticmethod
    def _safe_result(
        capability: _IssuedCapability, spec: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            exit_code = int(spec.get("exit_code", -1))
        except (TypeError, ValueError):
            exit_code = -1
        status = str(spec.get("status") or "failed")
        if status not in {"completed", "timed_out", "launch_failed", "failed"}:
            status = "failed"
        stdout = str(spec.get("stdout") or "")
        stderr = str(spec.get("stderr") or "")
        diff = spec.get("workspace_diff")
        if not isinstance(diff, dict):
            diff = {}
        duration = spec.get("duration_ms") or 0
        try:
            duration_ms = max(0, int(duration))
        except (TypeError, ValueError):
            duration_ms = 0

        def safe_paths(key: str) -> list[str]:
            values = diff.get(key) or ()
            if not isinstance(values, (list, tuple)):
                return []
            return [_safe_persisted_path(value) for value in values[:100]]

        return {
            "command_sha256": capability.command_sha256,
            "command_category": capability.category,
            "cwd": capability.cwd,
            "generation": capability.generation,
            "status": status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "stdout": {
                "chars": len(stdout),
                "sha256": hashlib.sha256(stdout.encode("utf-8", "replace")).hexdigest(),
                "preview": redact_shell_text(stdout, limit=2000),
            },
            "stderr": {
                "chars": len(stderr),
                "sha256": hashlib.sha256(stderr.encode("utf-8", "replace")).hexdigest(),
                "preview": redact_shell_text(stderr, limit=1200),
            },
            "workspace_diff": {
                "created": safe_paths("created"),
                "modified": safe_paths("modified"),
                "deleted": safe_paths("deleted"),
                "truncated": bool(diff.get("truncated")),
            },
        }

    @staticmethod
    def _summary(safe: dict[str, Any]) -> str:
        if safe["status"] == "timed_out":
            return "timed out"
        if safe["status"] == "launch_failed":
            return "failed to launch"
        changed = sum(
            len(safe["workspace_diff"].get(key) or ())
            for key in ("created", "modified", "deleted")
        )
        suffix = f" · {changed} file change" + ("s" if changed != 1 else "")
        return f"exit {safe['exit_code']}{suffix}"

    def _purge_locked(self, now: float) -> None:
        stale = [
            token
            for token, capability in self._issued.items()
            if (capability.consumed_at is None and now >= capability.expires_at)
            or (
                capability.recorded_at is not None
                and now - capability.recorded_at >= _RESULT_RETENTION_SECONDS
            )
            or (
                capability.consumed_at is not None
                and capability.recorded_at is None
                and now
                >= max(capability.expires_at, capability.consumed_at)
                + _RESULT_RETENTION_SECONDS
            )
        ]
        for token in stale:
            self._issued.pop(token, None)


__all__ = [
    "BashAuthorizationService",
    "CAPABILITY_VERSION",
    "DEFAULT_TTL_SECONDS",
    "MAX_TTL_SECONDS",
    "classify_command",
    "command_digest",
    "redact_shell_text",
]
