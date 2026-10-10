"""Explicit, atomic ChemGymRL provider installation (stdlib only).

The daemon interpreter is never a provider fallback. A single JSON ``current``
pointer publishes both the selected and previous verified generations.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import unquote, urlsplit

import openai4s_lab_provider
from openai4s.kernel.environment import build_kernel_environment
from openai4s.lab.models import ErrorCode, LabError
from openai4s_lab_provider.chemgymrl import PROFILES, SOURCE_SHA

_OVERRIDE = "OPENAI4S_LAB_CHEMGYMRL_PYTHON"
_SOURCE_URL = "https://github.com/chemgymrl/chemgymrl"
_REMEDY = "Run openai4s lab setup chemgymrl."
_GENERATION = re.compile(r"gen-[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}\Z")
_PYTHON_PROBE = (
    "import json,sys; print(json.dumps({'implementation':sys.implementation.name,"
    "'version':list(sys.version_info[:2])}))"
)
# The two download steps (pip, git) are the operator's own installation, so
# they keep the operator's proxy and index mirror. No other setup step and no
# provider process sees them: a proxy URL can embed a credential.
_DOWNLOAD_PASSTHROUGH = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "PIP_INDEX_URL",
)
_REDACTED = b"<redacted>"
# The userinfo of any URL a tool prints, whatever the tool already did to it:
# pip masks a password but leaves a token given as the user name.
_URL_USERINFO = re.compile(rb"([A-Za-z][A-Za-z0-9+.\-]*://)[^/\s]*@")
# A shorter credential is replaced only inside a URL: replacing every bare
# "ab" would shred the log without protecting anything worth the name.
_MIN_BARE_SECRET = 4
# Some private indexes put the token in the URL path. A path segment cannot be
# told from a directory name for certain, so only one shaped like a token is
# treated as one: long, with both letters and digits.
_PATH_TOKEN = re.compile(r"(?=[^/]*[A-Za-z])(?=[^/]*[0-9])[^/]{16,}\Z")
_SOURCE_PROBE = (
    "import importlib.metadata as m,json; d=m.distribution('chemistrygym'); "
    "print(json.dumps({'version':d.version, "
    "'direct_url':json.loads(d.read_text('direct_url.json'))}))"
)


def _root(data_dir: str | Path) -> Path:
    return Path(data_dir).expanduser().absolute() / "lab" / "providers" / "chemgymrl"


def _package() -> Path:
    return Path(openai4s_lab_provider.__file__).resolve().parent


def _python_path(generation: Path) -> Path:
    return generation / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _read_pointer(root: Path) -> dict[str, Any]:
    try:
        value = json.loads((root / "current").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"generation": None, "previous_generation": None}
    if not isinstance(value, dict) or set(value) != {
        "generation",
        "previous_generation",
    }:
        raise ValueError("invalid provider generation pointer")
    for key, name in value.items():
        if name is None and key == "previous_generation":
            continue
        if not isinstance(name, str) or not _GENERATION.fullmatch(name):
            raise ValueError("invalid provider generation name")
    return value


def _lock_sha256() -> str:
    return hashlib.sha256(
        (_package() / "chemgymrl" / "requirements.lock").read_bytes()
    ).hexdigest()


def _verified_python(root: Path, name: str | None) -> str | None:
    if not name or not _GENERATION.fullmatch(name):
        return None
    generation = root / name
    if generation.is_symlink():
        return None
    try:
        metadata = json.loads((generation / "verified.json").read_text("utf-8"))
        if (
            not isinstance(metadata, dict)
            or metadata.get("source_sha") != SOURCE_SHA
            or metadata.get("profiles") != list(PROFILES)
            or metadata.get("generation") != name
            # A generation built from another lock is not this release's
            # verified environment, however healthy it looks.
            or metadata.get("requirements_sha256") != _lock_sha256()
        ):
            return None
        executable = _python_path(generation)
        return str(executable) if _executable(executable) else None
    except (OSError, ValueError):
        return None


def provider_environment_status(data_dir: str | Path) -> dict[str, Any]:
    """Read local installation state without spawning or creating anything."""
    root = _root(data_dir)
    status: dict[str, Any] = {
        "available": False,
        "python": None,
        "source": None,
        "generation": None,
        "previous_generation": None,
        "detail": "ChemGymRL provider is not installed. " + _REMEDY,
    }
    override = os.environ.get(_OVERRIDE)
    if override is not None:
        try:
            candidate = Path(override).expanduser().absolute() if override else None
        except RuntimeError:  # "~nosuchuser/...": no home directory to expand
            candidate = None
        ready = candidate is not None and _executable(candidate)
        status.update(
            available=ready,
            python=str(candidate) if ready else None,
            source="override",
            detail=(
                "Explicit provider interpreter configured; runtime verification occurs on use."
                if ready
                else f"{_OVERRIDE} is not an executable file. " + _REMEDY
            ),
        )
        return status
    try:
        pointer = _read_pointer(root)
        status.update(pointer)
        executable = _verified_python(root, pointer["generation"])
        if executable:
            status.update(
                available=True,
                python=executable,
                source="generation",
                detail="Verified provider generation selected.",
            )
        elif pointer["generation"]:
            status["detail"] = (
                "Provider generation is unavailable, unverified or built from "
                "another provider lock. " + _REMEDY
            )
    except (OSError, ValueError):
        status["detail"] = (
            "Provider generation pointer is unreadable or invalid. " + _REMEDY
        )
    return status


def resolve_provider_python(data_dir: str | Path) -> str | None:
    """Resolve the explicit override, then current, without a daemon fallback."""
    return provider_environment_status(data_dir)["python"]


def _environment(data_dir: Path, generation: Path) -> dict[str, str]:
    env = build_kernel_environment(mode="lab-provider-setup", cwd=str(generation))
    for key in ("PYTHONPATH", "PYTHONUSERBASE", "VIRTUAL_ENV"):
        env.pop(key, None)
    private = generation / "runtime"
    paths = {
        "HOME": private / "home",
        "USERPROFILE": private / "home",
        "TMPDIR": private / "tmp",
        "TMP": private / "tmp",
        "TEMP": private / "tmp",
        "XDG_CACHE_HOME": private / "cache",
        "XDG_CONFIG_HOME": private / "config",
        "XDG_DATA_HOME": private / "data",
        "XDG_STATE_HOME": private / "state",
        "PIP_CACHE_DIR": private / "cache" / "pip",
        "UV_CACHE_DIR": private / "cache" / "uv",
        "NUMBA_CACHE_DIR": private / "cache" / "numba",
        "MPLCONFIGDIR": private / "cache" / "matplotlib",
    }
    for key, path in paths.items():
        path.mkdir(parents=True, exist_ok=True)
        env[key] = str(path)
    env.update(
        OPENAI4S_DATA_DIR=str(data_dir),
        MPLBACKEND="Agg",
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
        PIP_CONFIG_FILE=os.devnull,
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_NO_INPUT="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_TERMINAL_PROMPT="0",
        UV_PYTHON_DOWNLOADS="never",
        UV_OFFLINE="1",
    )
    return env


def _download_redactor(passed: Mapping[str, str]) -> Callable[[bytes], bytes]:
    """Keep what the passed-through proxy and index settings carry out of the log.

    ``setup.log`` outlives the setup and sits in a provider directory that
    agent cells can read, so a download tool's output is never written as it
    came. Two rules apply. Every credential found in a passed value is
    replaced wherever it appears: its password, a token given as the user
    name, query values, a token-shaped path segment, their percent-decoded
    forms and the Basic-auth encoding. And the userinfo of any URL is removed,
    which also covers a credential a tool rewrote before printing it. Hosts
    and ordinary paths stay, because they are what explains a failed download.
    """
    found: set[str] = set()
    for name, value in passed.items():
        if name.lower() == "no_proxy":
            continue
        try:
            # curl and pip also take a proxy without a scheme, as
            # ``user:password@host:port``; read that as an authority too.
            parts = urlsplit(value if "://" in value else "//" + value)
            username, password = parts.username, parts.password
        except ValueError:
            # Not a URL this module can take apart: the whole value is secret.
            found.add(value)
            continue
        pieces = [pair.partition("=")[2] for pair in parts.query.split("&")]
        pieces += [part for part in parts.path.split("/") if _PATH_TOKEN.match(part)]
        if password:
            pieces += [password, f"{username}:{password}"]
            pair = f"{unquote(username or '')}:{unquote(password)}"
            pieces.append(base64.b64encode(pair.encode("utf-8")).decode("ascii"))
        elif username:
            pieces.append(username)
        for piece in pieces:
            found.update((piece, unquote(piece)))
    secrets_ = sorted(
        (item.encode("utf-8") for item in found if len(item) >= _MIN_BARE_SECRET),
        key=len,
        reverse=True,
    )

    def redact(data: bytes) -> bytes:
        for secret in secrets_:
            data = data.replace(secret, _REDACTED)
        return _URL_USERINFO.sub(rb"\1" + _REDACTED + b"@", data)

    return redact


def _run(
    argv: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    log: BinaryIO,
    capture: bool = False,
    timeout: float = 900,
    redact: Callable[[bytes], bytes] | None = None,
) -> bytes:
    """Run one setup command with its output in the log.

    With ``redact`` the child never holds the log: its output comes back
    through a pipe and only the redacted bytes are written, including what a
    killed or timed-out command had printed.
    """
    if capture and redact is not None:
        raise ValueError("a redacted command's output is not returned")
    command_line = ("\n$ " + shlex.join(argv) + "\n").encode("utf-8")
    log.write(redact(command_line) if redact is not None else command_line)
    log.flush()
    piped = redact is not None
    with subprocess.Popen(
        argv,
        env=env,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture or piped else log,
        stderr=subprocess.STDOUT if piped else log,
        start_new_session=os.name != "nt",
    ) as process:
        output: bytes | None = b""
        try:
            try:
                output, _ = process.communicate(timeout=timeout)
            except BaseException:
                if process.poll() is None:
                    if os.name == "nt":
                        process.kill()
                    else:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                if piped:
                    # Retrying loses nothing the command had already printed.
                    output, _ = process.communicate()
                else:
                    process.wait()
                raise
        finally:
            if redact is not None:
                log.write(redact(output or b""))
                log.flush()
        if process.returncode:
            raise RuntimeError(
                f"installation command exited with status {process.returncode}"
            )
    return b"" if piped else output or b""


def _find_python(
    python: str | Path | None, *, env: dict[str, str], cwd: Path, log: BinaryIO
) -> str:
    candidates: list[str] = []
    if python is not None:
        candidates.append(str(Path(python).expanduser().absolute()))
    else:
        uv = shutil.which("uv")
        if uv:
            # uv may read its existing managed interpreter inventory in the
            # user's home; all writable runtime/cache paths remain private.
            discovery_env = dict(env)
            for name in ("HOME", "USERPROFILE", "XDG_DATA_HOME"):
                if name in os.environ:
                    discovery_env[name] = os.environ[name]
                else:
                    discovery_env.pop(name, None)
            try:
                found = (
                    _run(
                        [uv, "--no-config", "python", "find", "--no-project", "3.10"],
                        env=discovery_env,
                        cwd=cwd,
                        log=log,
                        capture=True,
                        timeout=30,
                    )
                    .decode("utf-8")
                    .strip()
                )
                if found:
                    candidates.append(found)
            except (OSError, RuntimeError, UnicodeError, subprocess.TimeoutExpired):
                pass
        on_path = shutil.which("python3.10")
        if on_path and on_path not in candidates:
            candidates.append(on_path)
    for candidate in candidates:
        try:
            identity = json.loads(
                _run(
                    [candidate, "-I", "-B", "-c", _PYTHON_PROBE],
                    env=env,
                    cwd=cwd,
                    log=log,
                    capture=True,
                    timeout=30,
                )
            )
            if identity == {"implementation": "cpython", "version": [3, 10]}:
                return candidate
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
            continue
    raise RuntimeError(
        "CPython 3.10 is required. Run uv python install 3.10, then "
        "openai4s lab setup chemgymrl --python /path/to/python3.10."
    )


def _commands(python: str, generation: Path) -> list[list[str]]:
    executable = str(_python_path(generation))
    package = _package()
    return [
        [python, "-I", "-B", "-m", "venv", str(generation)],
        [
            executable,
            "-I",
            "-B",
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "--no-deps",
            "-r",
            str(package / "chemgymrl" / "requirements.lock"),
        ],
        [
            executable,
            "-I",
            "-B",
            "-m",
            "pip",
            "install",
            "--use-pep517",
            # Build with the hash-locked setuptools/wheel just installed, not
            # with unpinned tooling an isolated build would download.
            "--no-build-isolation",
            "--no-deps",
            f"git+{_SOURCE_URL}@{SOURCE_SHA}",
        ],
        [executable, "-I", "-B", "-c", _SOURCE_PROBE],
        *[
            [
                executable,
                "-I",
                "-B",
                str(package / "__main__.py"),
                "--backend",
                "chemgymrl",
                "--describe",
                profile,
                "--portable",
            ]
            for profile in PROFILES
        ],
    ]


def _publish(root: Path, pointer: dict[str, Any]) -> None:
    staging = root / (".current-" + secrets.token_hex(6))
    try:
        with staging.open("x", encoding="utf-8") as stream:
            json.dump(pointer, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, root / "current")
    except BaseException:
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _keep_only_log(generation: Path) -> None:
    """A failed generation is not an environment: keep what explains it."""
    try:
        for entry in generation.iterdir():
            if entry.name == "setup.log":
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
    except OSError:
        pass


def _prune(root: Path, keep: set[str | None]) -> None:
    """Remove generations the pointer no longer names; never fail activation."""
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if (
            _GENERATION.fullmatch(entry.name)
            and entry.name not in keep
            and not entry.is_symlink()
        ):
            shutil.rmtree(entry, ignore_errors=True)


def setup_provider(
    data_dir: str | Path,
    *,
    python: str | Path | None = None,
    dry_run: bool = False,
    rollback: bool = False,
) -> dict[str, Any]:
    """Build and verify a fresh generation, then atomically activate it.

    A failed generation keeps only its setup log, and a successful activation
    removes every generation other than the current and previous one. Rollback
    exchanges current/previous in that same atomic pointer. Dry run is strictly
    read-only.
    """
    if rollback and python is not None:
        raise LabError(
            ErrorCode.INVALID_PARAMETERS, "--rollback cannot be combined with --python"
        )
    data_path = Path(data_dir).expanduser().absolute()
    root = _root(data_path)
    if dry_run:
        generation = root / "gen-<timestamp>-<hex>"
        return {
            "status": "dry_run",
            "rollback": rollback,
            "python": str(python) if python is not None else None,
            "discovery": ["--python", "uv python find 3.10", "python3.10 on PATH"],
            "commands": (
                []
                if rollback
                else _commands(str(python or "<CPython 3.10>"), generation)
            ),
            "current": str(root / "current"),
        }
    generation: Path | None = None
    log_path: Path | None = None
    locked = False
    lock_path = root / ".setup-lock"
    try:
        root.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            try:
                holder = lock_path.read_text(encoding="utf-8").strip()
            except OSError:
                holder = "unknown"
            raise RuntimeError(
                f"Provider setup is already running or was interrupted (lock "
                f"{lock_path}, PID {holder or 'unknown'}). If no setup is "
                "running, delete the lock and retry"
            ) from None
        locked = True
        with os.fdopen(descriptor, "w") as lock:
            lock.write(str(os.getpid()))
        try:
            previous = _read_pointer(root)
        except ValueError:
            if rollback:
                raise RuntimeError(
                    "The provider generation pointer is invalid; run setup to "
                    "build a fresh generation"
                ) from None
            # An unreadable pointer names nothing to keep: a fresh generation
            # replaces it (atomically, only once verified).
            previous = {"generation": None, "previous_generation": None}
        if rollback:
            target = previous["previous_generation"]
            executable = _verified_python(root, target)
            if executable is None:
                raise RuntimeError(
                    "No verified previous provider generation is available"
                )
            pointer = {
                "generation": target,
                "previous_generation": previous["generation"],
            }
            result = dict(
                status="rolled_back", python=executable, log_path=None, **pointer
            )
            _publish(root, pointer)
            return result
        name = (
            "gen-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            + "-"
            + secrets.token_hex(6)
        )
        generation = root / name
        generation.mkdir(mode=0o700)
        log_path = generation / "setup.log"
        with log_path.open("xb") as log:
            env = _environment(data_path, generation)
            interpreter = _find_python(python, env=env, cwd=generation, log=log)
            commands = _commands(interpreter, generation)
            _run(commands[0], env=env, cwd=generation, log=log)
            passed = {
                key: os.environ[key]
                for key in _DOWNLOAD_PASSTHROUGH
                if os.environ.get(key)
            }
            download_env = {**env, **passed}
            redact = _download_redactor(passed)
            for command in commands[1:3]:
                _run(command, env=download_env, cwd=generation, log=log, redact=redact)
            receipt = json.loads(
                _run(commands[3], env=env, cwd=generation, log=log, capture=True)
            )
            direct = receipt.get("direct_url", {})
            if (
                receipt.get("version") != "2.0.0"
                or direct.get("url") != _SOURCE_URL
                or direct.get("vcs_info", {}).get("vcs") != "git"
                or direct.get("vcs_info", {}).get("commit_id") != SOURCE_SHA
            ):
                raise RuntimeError(
                    "Installed chemistrygym version or source commit does not match the pinned source"
                )
            for profile, command in zip(PROFILES, commands[4:]):
                actual = _run(
                    command, env=env, cwd=generation, log=log, capture=True, timeout=180
                )
                expected = (
                    _package() / "chemgymrl" / "manifests" / (profile + ".json")
                ).read_bytes()
                if actual != expected:
                    raise RuntimeError(
                        f"Portable descriptor differs from committed manifest for {profile}"
                    )
            metadata = {
                "generation": name,
                "source_sha": SOURCE_SHA,
                "profiles": list(PROFILES),
                "requirements_sha256": _lock_sha256(),
            }
            (generation / "verified.json").write_text(
                json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8"
            )
            if _verified_python(root, name) is None:
                raise RuntimeError(
                    "Verified generation has no executable provider interpreter"
                )
            log.write(b"All source and portable descriptor checks passed.\n")
        pointer = {"generation": name, "previous_generation": previous["generation"]}
        result = dict(
            status="installed",
            python=str(_python_path(generation)),
            log_path=str(log_path),
            **pointer,
        )
        _publish(root, pointer)
        _prune(root, {name, previous["generation"]})
        return result
    except (
        OSError,
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ) as exc:
        message = (
            str(exc)
            if isinstance(exc, RuntimeError)
            else f"Provider setup failed ({type(exc).__name__})"
        )
        if log_path is not None:
            message += f". Installation log: {log_path}"
        if generation is not None:
            _keep_only_log(generation)
        raise LabError(ErrorCode.PROVIDER_UNAVAILABLE, message) from exc
    finally:
        if locked:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                # Activation is already committed; cleanup cannot turn its
                # success into a reported failure with a changed current.
                pass
