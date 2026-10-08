#!/usr/bin/env python3
"""Development DevicePort probes; not an LLM/manager benchmark or a lab recipe.

The private in-memory snapshot is evaluated before anything is serialized.
Policies receive only the standard public projections. No daemon, persistent
Store, user settings, credentials, or live equipment are used by this tool.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from openai4s.lab.evaluation import Goal, compare, default_goal, evaluate_run
from openai4s.lab.fake import FakeExtractorDevice
from openai4s.lab.manifest import (
    load_descriptor,
    match_command,
    observation_from_row,
    project_command,
    project_descriptor,
    project_observation,
    project_receipt,
    project_run,
)
from openai4s.lab.models import (
    Budgets,
    CommandRequest,
    Dispatch,
    ErrorCode,
    LabError,
    Receipt,
    SessionOpened,
    SessionOpenRequest,
    StopResult,
    canonical_json,
    sha256_hex,
)
from openai4s.lab.policies import FixedRulePolicy, RandomValidPolicy, run_episode
from openai4s_lab_provider.client import DEFAULT_TIMEOUTS, ProviderClient, ProviderError

BOUNDARY = "device_contract_probe"


def _now():
    return time.time_ns() // 1_000_000


class _ProviderDevice:
    """One isolated subprocess session, using the existing wire client."""

    def __init__(self, backend, python, work_dir):
        self.device_id = ""
        self._session_id = None
        work_dir.mkdir(parents=True, exist_ok=True)
        directories = {
            name: work_dir / name for name in ("home", "tmp", "cache", "mpl", "numba")
        }
        for directory in directories.values():
            directory.mkdir(exist_ok=True)
        # Deliberately construct, rather than filter/copy, the child environment.
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(directories["home"]),
            "TMPDIR": str(directories["tmp"]),
            "XDG_CACHE_HOME": str(directories["cache"]),
            "MPLCONFIGDIR": str(directories["mpl"]),
            "NUMBA_CACHE_DIR": str(directories["numba"]),
            "MPLBACKEND": "Agg",
            "LANG": "C.UTF-8",
        }
        self._client = ProviderClient(
            [
                python,
                "-I",
                "-B",
                str(ROOT / "openai4s_lab_provider/__main__.py"),
                "--backend",
                backend,
            ],
            env=env,
            cwd=str(work_dir),
            stderr_tail_bytes=0,
        )
        try:
            self._client.start()
            hello = self._request("hello", {})
            if hello.get("protocol") != 1 or hello.get("backend") != backend:
                raise LabError(
                    ErrorCode.PROVIDER_PROTOCOL_ERROR,
                    "Provider identity does not match",
                )
        except BaseException:
            self._client.close()
            raise

    def _request(self, operation, args):
        try:
            return self._client.request(
                operation, args, timeout=DEFAULT_TIMEOUTS[operation]
            )
        except ProviderError as exc:
            try:
                code = ErrorCode(exc.code)
            except ValueError:
                code = ErrorCode.PROVIDER_PROTOCOL_ERROR
            raise LabError(code, "Development provider operation failed") from None

    def _check_session(self, session_id):
        if session_id != self._session_id or not self._client.alive():
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Provider session is unavailable"
            )

    def describe(self, profile):
        descriptor = load_descriptor(self._request("describe", {"profile": profile}))
        if descriptor.profile != profile:
            raise LabError(
                ErrorCode.ADAPTER_MISMATCH, "Provider profile does not match"
            )
        self.device_id = descriptor.device_id
        return descriptor

    def open(self, request):
        opened = SessionOpened.from_dict(self._request("open", request.to_dict()))
        if (
            opened.descriptor.device_id != self.device_id
            or opened.descriptor.profile != request.profile
            or opened.descriptor.capability_revision
            != request.expected_capability_revision
        ):
            self._client.close()
            raise LabError(
                ErrorCode.ADAPTER_MISMATCH, "Opened provider descriptor does not match"
            )
        self._session_id = opened.session_id
        return opened

    def execute(self, session_id, dispatch):
        self._check_session(session_id)
        receipt = Receipt.from_dict(self._request("execute", dispatch.to_dict()))
        if receipt.provider_command_id != dispatch.provider_command_id:
            self._client.close()
            raise LabError(
                ErrorCode.PROVIDER_PROTOCOL_ERROR,
                "Provider command identity does not match",
            )
        return receipt

    def query(self, session_id, provider_command_id):
        self._check_session(session_id)
        result = self._request("query", {"provider_command_id": provider_command_id})
        if result == {"known": False}:
            return None
        if result == {"known": True, "receipt": None}:
            raise LabError(
                ErrorCode.OUTCOME_UNKNOWN, "Provider receipt is no longer retained"
            )
        receipt = Receipt.from_dict(result)
        if receipt.provider_command_id != provider_command_id:
            raise LabError(
                ErrorCode.PROVIDER_PROTOCOL_ERROR,
                "Provider command identity does not match",
            )
        return receipt

    def stop(self, session_id, reason):
        self._check_session(session_id)
        return StopResult.from_dict(self._request("stop", {"reason": reason}))

    def close(self, session_id=None):
        self._client.close()
        self._session_id = None

    def alive(self, session_id):
        return session_id == self._session_id and self._client.alive()


class _ProbeEnv:
    """In-memory contract probe, deliberately without manager/lease claims."""

    def __init__(self, port, profile, seed, max_steps):
        self._port = port
        descriptor = port.describe(profile)
        opened = port.open(
            SessionOpenRequest(profile, seed, {}, descriptor.capability_revision)
        )
        self._session_id = opened.session_id
        try:
            self._initialize(opened, profile, seed, max_steps)
        except BaseException:
            port.close(self._session_id)
            raise

    def _initialize(self, opened, profile, seed, max_steps):
        descriptor = opened.descriptor
        self._descriptor = opened.descriptor
        self._commands = []
        self._observations = []
        self._evaluations = []
        self._keys = {}
        self._dispatches = {}
        self._unknown = []
        budgets = Budgets(max_steps=min(max_steps, descriptor.limits["max_steps"]))
        config = {
            "execution_boundary": BOUNDARY,
            "profile": profile,
            "seed": seed,
            "episode_attempt_limit": max_steps,
            "budgets": budgets.to_dict(),
        }
        version = descriptor.backend_version
        now = _now()
        self._run = {
            "run_id": "labrun-" + uuid4().hex,
            "mode": "simulation",
            "backend": descriptor.backend,
            "device_id": descriptor.device_id,
            "profile": profile,
            "adapter_version": version["adapter_version"],
            "backend_source_sha": version["source_sha"],
            "capability_revision": descriptor.capability_revision,
            "descriptor": descriptor.to_dict(),
            "config": config,
            "config_hash": sha256_hex(canonical_json(config)),
            "seed": seed,
            "budgets": budgets.to_dict(),
            "status": "ready",
            "revision": 0,
            "step_count": 0,
            "command_count": 0,
            "consecutive_failures": 0,
            "end_reason": None,
            "raw_terminated": False,
            "raw_truncated": False,
            "created_at": now,
            "updated_at": now,
            "ended_at": None,
        }
        self._append_evidence(None, opened.observation, opened.evaluation)

    def _append_evidence(self, command_id, observation, evaluation):
        sequence = self._run["revision"]
        obs = {
            "observation_id": "labobs-" + uuid4().hex,
            "run_id": self._run["run_id"],
            "command_id": command_id,
            "sequence": sequence,
            "sim_time": observation["sim_time"],
            "sim_time_unit": "model_time",
            "wall_time_ms": _now(),
            "channels": deepcopy(observation["channels"]),
            "artifact_version_id": None,
        }
        # Re-validate and serialize channels into decoded ledger-row form.
        obs = observation_from_row(obs).to_dict()
        self._observations.append(obs)
        self._evaluations.append(
            {
                "evaluation_id": "labeval-" + uuid4().hex,
                "run_id": self._run["run_id"],
                "command_id": command_id,
                "sequence": sequence,
                **deepcopy(evaluation),
            }
        )
        return obs

    def _observation_view(self, observation):
        return project_observation(
            observation_from_row(observation),
            channels=self._descriptor.observation_channels,
            full=True,
        )

    def observe(self):
        return self._observation_view(self._observations[-1])

    def status(self):
        return {
            "run": project_run(self._run),
            "descriptor": project_descriptor(self._descriptor),
            "command": project_command(self._commands[-1]) if self._commands else None,
        }

    def _result(self, command):
        observation = next(
            (o for o in self._observations if o["command_id"] == command["command_id"]),
            None,
        )
        return {
            "run": project_run(self._run),
            "command": project_command(command),
            "observation": self._observation_view(observation) if observation else None,
        }

    def _end(self, reason):
        now = _now()
        self._run.update(
            status="ended", end_reason=reason, ended_at=now, updated_at=now
        )

    def execute(self, request):
        parsed = CommandRequest.from_dict(request)
        if parsed.run_id != self._run["run_id"]:
            raise LabError(ErrorCode.RUN_NOT_FOUND, "Probe run does not match")
        digest = sha256_hex(canonical_json(parsed.to_dict()))
        previous = self._keys.get(parsed.idempotency_key)
        if previous:
            if previous[0] != digest:
                raise LabError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "Probe idempotency key differs"
                )
            return self._result(previous[1])
        if self._run["status"] != "ready":
            raise LabError(ErrorCode.RUN_ENDED, "Probe run has ended")
        budgets = self._run["budgets"]
        if (
            self._run["step_count"] >= budgets["max_steps"]
            or len(self._commands) >= budgets["max_commands"]
            or self._run["consecutive_failures"] >= budgets["max_consecutive_failures"]
            or _now() - self._run["created_at"] >= budgets["max_wall_ms"]
        ):
            self._end("budget_exhausted")
            raise LabError(ErrorCode.BUDGET_EXHAUSTED, "Probe budget exhausted")
        now = _now()
        command = {
            "command_id": "labcmd-" + uuid4().hex,
            "run_id": parsed.run_id,
            "seq": len(self._commands) + 1,
            "idempotency_key": parsed.idempotency_key,
            "request_hash": digest,
            "operation": parsed.operation,
            "request": parsed.to_dict(),
            "expected_revision": parsed.expected_revision,
            "applied_revision": None,
            "origin": "system",
            "state": "created",
            "error_code": None,
            "error": None,
            "observation_id": None,
            "created_at": now,
            "updated_at": now,
            "dispatched_at": None,
            "completed_at": None,
            "receipt": None,
        }
        self._commands.append(command)
        self._keys[parsed.idempotency_key] = (digest, command)
        self._run["command_count"] = len(self._commands)
        try:
            if parsed.expected_revision != self._run["revision"]:
                raise LabError(ErrorCode.STALE_REVISION, "Probe revision differs")
            normalized = match_command(self._descriptor, parsed)
        except LabError as exc:
            command.update(
                state="rejected",
                error_code=exc.code.value,
                error="Probe admission refused",
            )
            self._run["consecutive_failures"] += 1
        else:
            command.update(
                capability_id=normalized.capability_id,
                request=normalized.to_dict(),
                state="dispatching",
                dispatched_at=_now(),
            )
            capability = next(
                c
                for c in self._descriptor.capabilities
                if c.capability_id == normalized.capability_id
            )
            self._dispatches[command["command_id"]] = (
                self._dispatches.get(command["command_id"], 0) + 1
            )
            try:
                receipt = self._port.execute(
                    self._session_id,
                    Dispatch(
                        command["command_id"],
                        normalized,
                        {resource: command["seq"] for resource in capability.resources},
                    ),
                )
            except LabError as exc:
                command.update(
                    state="outcome_unknown",
                    error_code="outcome_unknown",
                    error="Provider outcome is unknown",
                )
                self._unknown.append(
                    {
                        "command_id": command["command_id"],
                        "transport_code": exc.code.value,
                    }
                )
                self._run["status"] = "quarantined"
            else:
                command["receipt"] = {
                    "provider_command_id": receipt.provider_command_id,
                    **project_receipt(receipt),
                }
                command.update(
                    state="succeeded" if receipt.applied else "failed",
                    error_code=receipt.error["code"] if receipt.error else None,
                    error="Provider refused the command" if receipt.error else None,
                )
                if receipt.applied:
                    self._run["revision"] += 1
                    self._run["step_count"] += 1
                    self._run["consecutive_failures"] = 0
                    command["applied_revision"] = self._run["revision"]
                    observation = self._append_evidence(
                        command["command_id"], receipt.observation, receipt.evaluation
                    )
                    command["observation_id"] = observation["observation_id"]
                    self._run.update(
                        raw_terminated=receipt.raw["terminated"],
                        raw_truncated=receipt.raw["truncated"],
                    )
                    if receipt.end_reason is not None:
                        self._end(receipt.end_reason.value)
                else:
                    self._run["consecutive_failures"] += 1
        command.update(completed_at=_now(), updated_at=_now())
        self._run["updated_at"] = _now()
        return self._result(command)

    def finish(self):
        if self._run["status"] != "ended":
            if self._port.alive(self._session_id):
                try:
                    self._port.stop(self._session_id, "development probe finished")
                except LabError:
                    self._end("provider_lost")
                else:
                    self._end("stopped")
            else:
                self._end("provider_lost")

    def evaluate(self, goal):
        result = evaluate_run(
            self._run, self._commands, self._observations, self._evaluations, goal=goal
        )
        result["execution_boundary"] = BOUNDARY
        result["duplicate_dispatch_count"] = sum(
            max(0, count - 1) for count in self._dispatches.values()
        )
        result["duplicate_dispatch_reason"] = (
            "Counted at this in-memory probe's actual DevicePort.execute calls."
        )
        result["outcome_unknown"].update(
            historical_count=len(self._unknown),
            subsequent_results=[],
            reason="This probe stops on uncertainty and performs no automatic replay or reconciliation.",
        )
        return result

    def close(self):
        self._port.close(self._session_id)


def _goal(backend, profile, observation):
    if backend == "fake":
        return Goal(
            profile,
            "toy_solute",
            "beaker_1",
            0.1,
            0.9,
            definition="Fictional fake-device solute collection benchmark",
        )
    if backend == "toy":
        return Goal(
            profile,
            "toy_component",
            "extraction_vessel",
            0.1,
            0.9,
            definition="Fictional subprocess-toy component collection benchmark; missing composition is unknown",
        )
    target = next(
        (
            c["value"]
            for c in observation["channels"]
            if c["name"] == "targets" and c["quality"] == "ok"
        ),
        None,
    )
    return default_goal(profile, target=target)


def run_probe(
    *,
    backend="fake",
    profile=None,
    python=None,
    work_dir=None,
    episodes=1,
    seed=0,
    max_steps=50,
    policies=("fixed", "random"),
):
    """Return metric cohorts and their comparison; never return raw snapshots.

    Policy names are ``fixed`` and ``random``. Episode i uses seed+i for every
    policy, and each episode owns and closes a fresh device/client session.
    Provider interpreters must already exist; this function installs nothing.
    """
    policies = tuple(policies)
    if (
        backend not in {"fake", "toy", "chemgymrl"}
        or type(episodes) is not int
        or not 1 <= episodes <= 1000
        or type(max_steps) is not int
        or not 1 <= max_steps <= 10000
        or type(seed) is not int
        or not 0 <= seed < 2**32
        or seed + episodes > 2**32
        or not policies
        or len(set(policies)) != len(policies)
        or any(name not in {"fixed", "random"} for name in policies)
    ):
        raise ValueError("Invalid development probe configuration")
    profile = profile or (
        "WaterOilExtract-v0" if backend == "chemgymrl" else "toy-extract-v0"
    )
    work_dir = Path(work_dir or ROOT.parent / "_data/W2-C/probe").expanduser().resolve()
    real_data = (Path.home() / ".openai4s").resolve()
    if work_dir == real_data or real_data in work_dir.parents:
        raise ValueError("Development probes require an isolated work directory")
    work_dir.mkdir(parents=True, exist_ok=True)
    if backend != "fake":
        python = python or (
            os.environ.get("OPENAI4S_LAB_CHEMGYMRL_PYTHON")
            if backend == "chemgymrl"
            else sys.executable
        )
        python = shutil.which(str(python)) if python else None
        if not python:
            raise LabError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "An existing provider interpreter is required",
            )
        python = os.path.abspath(python)
    cohorts = {name: [] for name in policies}
    for episode in range(episodes):
        for name in policies:
            scratch = work_dir / f"{name}-{episode}-{uuid4().hex}"
            port = (
                FakeExtractorDevice()
                if backend == "fake"
                else _ProviderDevice(backend, python, scratch)
            )
            env = None
            try:
                env = _ProbeEnv(port, profile, seed + episode, max_steps)
                goal = _goal(backend, profile, env.observe())
                policy = (
                    FixedRulePolicy()
                    if name == "fixed"
                    else RandomValidPolicy(seed + episode)
                )
                trace = run_episode(policy, env, max_steps=max_steps)
                env.finish()
                result = env.evaluate(goal)
                result.update(
                    policy=name,
                    episode=episode,
                    seed=seed + episode,
                    policy_stop_reason=trace["stop_reason"],
                    policy_error_code=trace["error_code"],
                )
                cohorts[name].append(result)
            finally:
                if env is not None:
                    env.close()
                elif isinstance(port, _ProviderDevice):
                    port.close()
    return {
        "execution_boundary": BOUNDARY,
        "backend": backend,
        "profile": profile,
        "results_by_policy": cohorts,
        "comparison": compare(cohorts),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", choices=("fake", "toy", "chemgymrl"), default="fake"
    )
    parser.add_argument(
        "--python",
        help="Existing provider interpreter; ChemGymRL also accepts OPENAI4S_LAB_CHEMGYMRL_PYTHON",
    )
    parser.add_argument("--profile")
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=50)
    parser.add_argument(
        "--policies",
        nargs="+",
        choices=("fixed", "random"),
        default=["fixed", "random"],
    )
    parser.add_argument(
        "--work-dir", type=Path, default=ROOT.parent / "_data/W2-C/probe"
    )
    parser.add_argument(
        "--output", type=Path, help="Metric JSON artifact; defaults to stdout"
    )
    args = parser.parse_args(argv)
    output = args.output
    try:
        if output is not None:
            output = output.expanduser().resolve()
            real_data = (Path.home() / ".openai4s").resolve()
            if output == real_data or real_data in output.parents:
                raise ValueError("Development output requires an isolated path")
        result = run_probe(
            **{key: value for key, value in vars(args).items() if key != "output"}
        )
        payload = (
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        )
        if output is None:
            sys.stdout.write(payload)
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(payload, encoding="utf-8")
    except (LabError, ProviderError, ValueError, OSError):
        # Do not print arbitrary exception messages, stderr, or provider frames.
        sys.stderr.write(
            "Lab development probe failed; check configuration and the isolated provider environment.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
