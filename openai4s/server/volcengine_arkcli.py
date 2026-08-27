"""Credential-safe adapter over the official Ark CLI executable."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, urlencode, urlsplit

from openai4s.host.bash import redact_shell_text

_MAX_OUTPUT_BYTES = 1_000_000
_DEFAULT_TIMEOUT_S = 30.0
_SAFE_ENV_NAMES = frozenset(
    {
        "APPDATA",
        "BROWSER",
        "COMSPEC",
        "DBUS_SESSION_BUS_ADDRESS",
        "DISPLAY",
        "HOME",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "LOGNAME",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WAYLAND_DISPLAY",
        "WINDIR",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
    }
)
_DEVICE_CODE = re.compile(r"^[A-Za-z0-9+/=_&-]{8,8192}$")
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,160}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,256}$")


class ArkCliError(RuntimeError):
    """A controlled Ark CLI failure safe to map to an API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


CommandRunner = Callable[[Sequence[str], float, threading.Event | None], CommandResult]


def _child_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Give Ark CLI desktop context without inheriting daemon credentials."""

    values = source if source is not None else os.environ
    return {
        key: str(value)
        for key, value in values.items()
        if key.upper() in _SAFE_ENV_NAMES and value
    }


def _command_env(argv: Sequence[str]) -> dict[str, str]:
    child_env = _child_env()
    domain = str(argv[1] if len(argv) > 1 else "").lower()
    skill_name = {
        "auth": "arkcli-auth",
        "plans": "arkcli-plans",
        "usage": "arkcli-usage",
        "profile": "arkcli-profile",
        "api": "arkcli-api-explorer",
    }.get(domain, "arkcli-auth")
    child_env.update(
        {
            "ARKCLI_CALLER_TYPE": "ai_agent",
            "ARKCLI_CALLER_NAME": "openai4s",
            "ARKCLI_SKILL_NAME": skill_name,
        }
    )
    return child_env


def _run_bounded(
    argv: Sequence[str],
    timeout_s: float,
    cancel_event: threading.Event | None = None,
) -> CommandResult:
    """Run one fixed argv with bounded stdout/stderr and cancellable waiting."""

    creationflags = 0
    if os.name == "nt":
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    process = subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_command_env(argv),
        shell=False,
        creationflags=creationflags,
    )
    stdout = bytearray()
    stderr = bytearray()

    def drain(pipe: Any, target: bytearray) -> None:
        try:
            while True:
                chunk = pipe.read(16_384)
                if not chunk:
                    return
                remaining = _MAX_OUTPUT_BYTES - len(target)
                if remaining > 0:
                    target.extend(chunk[:remaining])
        finally:
            try:
                pipe.close()
            except Exception:  # noqa: BLE001 - process cleanup is best effort
                pass

    readers = [
        threading.Thread(target=drain, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=drain, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()

    deadline = time.monotonic() + max(0.1, float(timeout_s))
    timed_out = False
    cancelled = False
    while process.poll() is None:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            process.kill()
            break
        if time.monotonic() >= deadline:
            timed_out = True
            process.kill()
            break
        time.sleep(0.05)
    try:
        returncode = process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        returncode = process.wait()
    for reader in readers:
        reader.join(timeout=2.0)
    return CommandResult(
        returncode=returncode,
        stdout=bytes(stdout).decode("utf-8", errors="replace"),
        stderr=bytes(stderr).decode("utf-8", errors="replace"),
        timed_out=timed_out,
        cancelled=cancelled,
    )


def _text(value: Any, limit: int = 160) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _named(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _rows(payload: Any, *names: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, Mapping):
        candidates = _named(payload, *names)
        if not isinstance(candidates, list):
            result = _mapping(_named(payload, "Result", "result", "Data", "data"))
            candidates = _named(result, *names)
    else:
        candidates = None
    if not isinstance(candidates, list):
        return []
    return [dict(item) for item in candidates if isinstance(item, Mapping)]


def _command_failure_detail(result: CommandResult) -> str:
    """Extract a short, non-secret reason from Ark CLI's failure output."""

    raw = str(result.stderr or result.stdout or "").strip()
    if not raw:
        return ""
    clean = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw).strip()
    detail: Any = clean
    payload: Any = None
    try:
        payload = json.loads(clean)
    except (TypeError, ValueError):
        for line in reversed(clean.splitlines()):
            try:
                payload = json.loads(line.strip())
                break
            except (TypeError, ValueError):
                continue
        if payload is None:
            start = clean.find("{")
            if start >= 0:
                try:
                    payload, _end = json.JSONDecoder().raw_decode(clean[start:])
                except (TypeError, ValueError):
                    payload = None
    if isinstance(payload, Mapping):
        error = _mapping(_named(payload, "error", "Error"))
        detail = _named(error, "message", "Message") or _named(
            payload, "message", "Message"
        )
    detail = _text(detail, 240)
    if not detail:
        return ""
    # Shared baseline first: the same conservative shapes (Bearer, sk-/ark-
    # tokens, NAME=value assignments) the approval/audit projections redact.
    detail = redact_shell_text(detail, limit=240)
    detail = re.sub(r"https?://[^\s<>\"']+", "[redacted URL]", detail)
    detail = re.sub(
        r"(?i)((?:\bauthorization\s+)?\bcode\s*[:=：]\s*"
        r"|授权\s*码?\s*[:=：]?\s*)[A-Za-z0-9+/=_&-]{8,}",
        r"\1[redacted]",
        detail,
    )

    def redact_token(match: re.Match[str]) -> str:
        token = match.group(0)
        if any(char in token for char in "=+/_-"):
            return "[redacted]"
        # Purely alphanumeric secrets exist too (an unpadded Base64 code has
        # no separator): treat digit+letter and long mixed-case runs as
        # credentials rather than prose.
        has_digit = any(c.isdigit() for c in token)
        has_alpha = any(c.isalpha() for c in token)
        if len(token) >= 10 and has_digit and has_alpha:
            return "[redacted]"
        has_upper = any(c.isupper() for c in token)
        has_lower = any(c.islower() for c in token)
        if len(token) >= 12 and has_upper and has_lower:
            return "[redacted]"
        return token

    detail = re.sub(
        r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{8,}(?![A-Za-z0-9+/=_-])",
        redact_token,
        detail,
    )
    return detail[:240]


def _device_payload(value: str) -> dict[str, str]:
    """Read Ark's base64 ``code=...&state=...`` envelope when present."""

    candidates = [value]
    padded = value + "=" * (-len(value) % 4)
    try:
        candidates.append(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        pass
    for candidate in candidates:
        parsed = parse_qs(candidate, keep_blank_values=True)
        code = (parsed.get("code") or [""])[0].strip()
        state = (parsed.get("state") or [""])[0].strip()
        if code and state:
            return {"code": code, "state": state}
    return {}


def _normalize_device_code(value: Any, authorize_url: str) -> str:
    """Accept Ark's full envelope and recover gracefully from a pasted inner code."""

    raw = str(value or "").strip()
    if not _DEVICE_CODE.fullmatch(raw):
        raise ArkCliError("invalid_authorization_code", "Authorization code is invalid")
    parsed = parse_qs(raw, keep_blank_values=True)
    plain_code = (parsed.get("code") or [""])[0].strip()
    plain_state = (parsed.get("state") or [""])[0].strip()
    if plain_code and plain_state:
        # A pasted plaintext ``code=...&state=...`` envelope: re-wrap it in the
        # Base64 form the CLI expects instead of forwarding it verbatim.
        envelope = urlencode({"code": plain_code, "state": plain_state})
        return base64.urlsafe_b64encode(envelope.encode("utf-8")).decode("ascii")
    if _device_payload(raw):
        return raw
    state = (parse_qs(urlsplit(authorize_url).query).get("state") or [""])[0].strip()
    if not state:
        return raw
    code = (parse_qs(raw, keep_blank_values=True).get("code") or [raw])[0].strip()
    envelope = urlencode({"code": code, "state": state})
    return base64.urlsafe_b64encode(envelope.encode("utf-8")).decode("ascii")


class ArkCliBridge:
    """A narrow, injectable adapter over the official ``arkcli`` executable."""

    def __init__(
        self,
        *,
        executable: str | None = None,
        which: Callable[[str], str | None] = shutil.which,
        runner: CommandRunner = _run_bounded,
    ) -> None:
        self._configured_executable = executable or os.environ.get(
            "OPENAI4S_ARKCLI_PATH", ""
        )
        self._which = which
        self._runner = runner
        self._version: str | None = None

    def executable(self) -> str:
        configured = str(self._configured_executable or "").strip()
        if configured:
            expanded = Path(configured).expanduser()
            if expanded.is_file():
                return str(expanded.resolve())
            resolved = self._which(configured)
            if resolved:
                return resolved
            return ""
        packaged = (
            Path(__file__).resolve().parents[1]
            / "bin"
            / ("arkcli.exe" if os.name == "nt" else "arkcli")
        )
        if packaged.is_file():
            return str(packaged)
        return self._which("arkcli") or ""

    def availability(self) -> dict[str, Any]:
        executable = self.executable()
        if not executable:
            return {"installed": False, "version": ""}
        return {"installed": True, "version": self.version()}

    def version(self) -> str:
        if self._version is not None:
            return self._version
        try:
            result = self._run(("--version",), timeout_s=5.0)
        except ArkCliError:
            self._version = ""
        else:
            self._version = _text(result.stdout, 80)
        return self._version

    def _run(
        self,
        args: Sequence[str],
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        cancel_event: threading.Event | None = None,
    ) -> CommandResult:
        executable = self.executable()
        if not executable:
            raise ArkCliError("arkcli_not_installed", "Ark CLI is not installed")
        try:
            result = self._runner((executable, *args), timeout_s, cancel_event)
        except OSError as error:
            # A resolvable-but-unrunnable executable (no exec bit, a dead npm
            # shim interpreter, ENOEXEC) must surface as the projected error
            # state, not escape the ArkCliError envelope into a 500.
            raise ArkCliError(
                "arkcli_failed", "Ark CLI could not be executed"
            ) from error
        if result.cancelled:
            raise ArkCliError("login_cancelled", "Volcengine login was cancelled")
        if result.timed_out:
            raise ArkCliError("arkcli_timeout", "Ark CLI did not finish in time")
        if result.returncode != 0:
            detail = (result.stderr + "\n" + result.stdout).lower()
            if "project" in detail and (
                "non-interactive" in detail
                or "noninteractive" in detail
                or "非交互式终端" in detail
            ):
                raise ArkCliError(
                    "project_selection_required",
                    "Volcengine authorization succeeded, but Ark CLI still needs "
                    "a Project selection",
                )
            reason = _command_failure_detail(result)
            message = "Ark CLI could not complete the request"
            if reason:
                message += f": {reason}"
            raise ArkCliError("arkcli_failed", message)
        return result

    def _json(
        self, args: Sequence[str], *, timeout_s: float = _DEFAULT_TIMEOUT_S
    ) -> Any:
        result = self._run((*args, "--format", "json"), timeout_s=timeout_s)
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise ArkCliError(
                "arkcli_invalid_output", "Ark CLI returned an invalid response"
            ) from error

    def whoami(self) -> dict[str, Any]:
        payload = self._json(("auth", "whoami"))
        if not isinstance(payload, Mapping):
            raise ArkCliError(
                "arkcli_invalid_output", "Ark CLI returned an invalid identity"
            )
        return dict(payload)

    def plans(self) -> list[dict[str, Any]]:
        return _rows(self._json(("plans", "get")), "plans", "Plans")

    def usage(self) -> dict[str, Any]:
        payload = self._json(("usage", "plan"))
        if not isinstance(payload, Mapping):
            raise ArkCliError(
                "arkcli_invalid_output", "Ark CLI returned invalid usage data"
            )
        return dict(payload)

    def profiles(self) -> list[dict[str, Any]]:
        return _rows(
            self._json(("profile", "list")), "profiles", "Profiles", "items", "Items"
        )

    def endpoint_inventory(self, profile_name: str) -> list[dict[str, Any]]:
        """List invocable text endpoints for a platform profile."""

        if not _PROFILE_NAME.fullmatch(profile_name):
            raise ArkCliError("ark_profile_invalid", "Ark CLI profile name is invalid")
        payload = _mapping(
            self._json(
                (
                    "resources",
                    "list",
                    "--profile",
                    profile_name,
                    "--modality",
                    "text",
                ),
                timeout_s=60.0,
            )
        )
        current_default = _text(
            _named(payload, "current_default", "currentDefault", "CurrentDefault"),
            256,
        )
        candidates: list[dict[str, Any]] = []
        for item in _rows(payload, "items", "Items"):
            endpoint_id = _text(
                _named(item, "id", "Id", "ID", "endpoint_id", "EndpointId"),
                256,
            )
            kind = _text(_named(item, "resource_kind", "resourceKind"), 32).lower()
            if (
                not _MODEL_ID.fullmatch(endpoint_id)
                or (kind and kind != "endpoint")
                or _named(item, "invocable", "Invocable") is not True
            ):
                continue
            candidates.append(
                {
                    "id": endpoint_id,
                    "name": _text(_named(item, "name", "Name"), 100),
                    "selected": endpoint_id == current_default,
                }
            )
        return candidates

    def login_device_start(self) -> dict[str, Any]:
        payload = self._json(("auth", "login", "--no-browser"), timeout_s=60.0)
        if not isinstance(payload, Mapping):
            raise ArkCliError(
                "arkcli_invalid_output", "Ark CLI returned an invalid login response"
            )
        url = _text(_named(payload, "authorize_url", "authorizeUrl"), 2048)
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as error:
            raise ArkCliError(
                "arkcli_invalid_output", "Ark CLI returned an invalid login URL"
            ) from error
        if (
            parsed.scheme != "https"
            or parsed.hostname != "signin.volcengine.com"
            or port not in {None, 443}
            or parsed.username
            or parsed.password
        ):
            raise ArkCliError(
                "arkcli_invalid_output", "Ark CLI returned an invalid login URL"
            )
        expires = _named(payload, "expires_in_sec", "expiresInSec")
        try:
            expires_in = max(30, min(int(expires), 900))
        except (TypeError, ValueError):
            expires_in = 600
        return {"authorize_url": url, "expires_in_sec": expires_in}

    def login_device_complete(
        self, code: Any, cancel_event: threading.Event | None = None
    ) -> None:
        value = str(code or "").strip()
        if not _DEVICE_CODE.fullmatch(value):
            raise ArkCliError(
                "invalid_authorization_code", "Authorization code is invalid"
            )
        self._run(
            ("auth", "login", "--no-browser", "--code", value),
            timeout_s=90.0,
            cancel_event=cancel_event,
        )

    def logout(self) -> None:
        self._run(("auth", "logout"), timeout_s=30.0)

    @staticmethod
    def _plan_profile_types(plan_key: str) -> tuple[str, ...]:
        exact = _text(plan_key, 64).lower()
        return (exact,)

    def profile_for_plan(self, plan_key: str) -> str:
        wanted = self._plan_profile_types(plan_key)
        matches: list[str] = []
        for profile in self.profiles():
            profile_type = _text(
                _named(profile, "type", "Type", "profile_type", "profileType"), 64
            ).lower()
            name = _text(_named(profile, "name", "Name"), 160)
            if profile_type in wanted and _PROFILE_NAME.fullmatch(name):
                matches.append(name)
        if not matches:
            raise ArkCliError(
                "ark_profile_missing",
                "Ark CLI has no profile for the selected plan",
            )
        if len(matches) > 1:
            raise ArkCliError(
                "ark_profile_ambiguous",
                "Ark CLI has multiple profiles for the selected plan",
            )
        return matches[0]

    def default_model(self, profile_name: str) -> str:
        """Read the profile's text default, with Ark's stable router fallback."""

        if not _PROFILE_NAME.fullmatch(profile_name):
            raise ArkCliError("ark_profile_invalid", "Ark CLI profile name is invalid")
        try:
            payload = self._json(
                ("profile", "models", "list", "--profile", profile_name)
            )
        except ArkCliError:
            return "ark-code-latest"
        root = _mapping(payload)
        resources = _mapping(_named(root, "resources", "Resources"))
        text_resources = _mapping(_named(resources, "text", "Text"))
        defaults = _mapping(_named(root, "defaults", "Defaults"))
        candidate = _named(text_resources, "default", "Default")
        if candidate is None:
            candidate = _named(defaults, "text", "Text")
        if candidate is None:
            candidate = _named(root, "default_model", "defaultModel", "DefaultModel")
        model = _text(candidate, 256)
        if model.lower() == "auto":
            return "ark-code-latest"
        return model if _MODEL_ID.fullmatch(model) else "ark-code-latest"

    def default_api_key(self, profile_name: str) -> str:
        """Resolve one profile's selected key without returning it to a caller UI."""

        return self.api_key(profile_name)

    def api_key_inventory(self, profile_name: str) -> list[dict[str, Any]]:
        """List usable keys for one profile without retrieving raw credentials."""

        if not _PROFILE_NAME.fullmatch(profile_name):
            raise ArkCliError("ark_profile_invalid", "Ark CLI profile name is invalid")
        selected = self._json(("profile", "keys", "list", "--profile", profile_name))
        selected_map = _mapping(selected)
        masked = _text(
            _named(selected_map, "default_api_key", "defaultApiKey", "DefaultApiKey"),
            256,
        )
        listed = self._json(
            (
                "api",
                "apikey.list",
                "--params",
                json.dumps({"PageSize": 100}, separators=(",", ":")),
                "--page-all",
                "--profile",
                profile_name,
            ),
            timeout_s=60.0,
        )
        items = _rows(listed, "items", "Items", "api_keys", "ApiKeys")
        candidates: list[dict[str, Any]] = []
        for item in items:
            status = _text(_named(item, "status", "Status"), 32).lower()
            if status and status not in {"active", "running", "effective", "enabled"}:
                continue
            item_mask = _text(_named(item, "key", "Key", "api_key", "ApiKey"), 256)
            item_id = _named(item, "id", "Id", "ID", "api_key_id", "ApiKeyId")
            if item_id is None:
                continue
            name = _text(_named(item, "name", "Name", "api_key_name", "ApiKeyName"), 80)
            suffix_match = re.search(r"([A-Za-z0-9]{4})\s*$", item_mask)
            candidates.append(
                {
                    "id": item_id,
                    "mask": item_mask,
                    "name": name,
                    "suffix": suffix_match.group(1) if suffix_match else "",
                    "selected": bool(masked and item_mask == masked),
                }
            )
        return candidates

    def api_key(self, profile_name: str, key_id: str | None = None) -> str:
        """Retrieve one freshly validated raw key for backend-only provisioning."""

        candidates = self.api_key_inventory(profile_name)
        if key_id is not None:
            requested = str(key_id).strip()
            selected = [
                item for item in candidates if str(item["id"]).strip() == requested
            ]
            if len(selected) != 1:
                raise ArkCliError(
                    "ark_key_choice_invalid",
                    "The selected Ark API key is no longer available",
                )
            chosen = selected[0]
        else:
            selected = [item for item in candidates if item["selected"]]
            if len(selected) == 1:
                chosen = selected[0]
            elif len(candidates) == 1:
                chosen = candidates[0]
            elif not candidates:
                raise ArkCliError(
                    "ark_key_missing",
                    "No usable API key exists for the selected Ark profile",
                )
            else:
                raise ArkCliError(
                    "ark_key_choice_required",
                    "Choose which Ark API key OpenAI4S should use",
                )
        raw = self._json(
            (
                "api",
                "apikey.get_raw",
                "--params",
                json.dumps({"Id": chosen["id"]}, separators=(",", ":")),
                "--profile",
                profile_name,
            ),
            timeout_s=60.0,
        )
        result = _mapping(_named(_mapping(raw), "Result", "result", "Data", "data"))
        value = _named(result, "ApiKey", "api_key", "key", "Key")
        if value is None:
            value = _named(_mapping(raw), "ApiKey", "api_key", "key", "Key")
        key = str(value or "").strip()
        if not 8 <= len(key) <= 8192 or any(char in key for char in "\r\n\x00"):
            raise ArkCliError(
                "ark_key_unavailable", "Ark CLI did not return a usable API key"
            )
        return key


__all__ = ["ArkCliBridge", "ArkCliError", "CommandResult"]
