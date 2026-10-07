"""Pinned single-bench adapter. Third-party code runs only inside this process."""

import copy
import ctypes
import functools
import hashlib
import importlib.metadata
import json
import os
import platform
import random
import secrets
import sys
from collections import OrderedDict
from pathlib import Path

from .. import ADAPTER_VERSION
from ..protocol import BackendError, failed_receipt
from . import PROFILES, SOURCE_SHA, mapping


def _flush_native():
    """Flush POSIX C stdio while its descriptors still point at the discard sink."""
    library = ctypes.CDLL(None)
    flush = library.fflush
    flush.argtypes = [ctypes.c_void_p]
    flush.restype = ctypes.c_int
    flush(None)


def _quiet(method):
    """Upstream output may contain material identities or rewards; discard at fd level."""

    @functools.wraps(method)
    def wrapped(*args, **kwargs):
        sys.stdout.flush()
        sys.stderr.flush()
        _flush_native()
        saved = [os.dup(fd) for fd in (1, 2)]
        try:
            with open(os.devnull, "wb") as sink:
                for fd in (1, 2):
                    os.dup2(sink.fileno(), fd)
                try:
                    return method(*args, **kwargs)
                finally:
                    sys.stdout.flush()
                    sys.stderr.flush()
                    _flush_native()
        finally:
            for fd, original in zip((1, 2), saved):
                os.dup2(original, fd)
                os.close(original)

    return wrapped


def _verified_runtime():
    """A profile result is evidence only for the actually measured runtime."""
    try:
        names = [
            line.split("==")[0]
            for line in Path(__file__)
            .with_name("requirements.in")
            .read_text()
            .splitlines()
            if line and not line.startswith("#")
        ]
        runtime = {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "packages": {name: importlib.metadata.version(name) for name in names},
        }
        fingerprint = hashlib.sha256(
            mapping.canonical_json(runtime).encode()
        ).hexdigest()
        return (
            fingerprint
            == "1183b4fe19b2ae3a83a3181b03a455ac399b42e008965bc428d67ab2e30831b2"
        )
    except (OSError, importlib.metadata.PackageNotFoundError):
        return False


def _seed(seed):
    import numba
    import numpy as np

    random.seed(seed)
    np.random.seed(seed)

    @numba.njit
    def seed_compiled(value):
        np.random.seed(value)

    seed_compiled(seed)


def _make(profile):
    if profile not in PROFILES:
        raise BackendError("device_not_found", "unsupported extraction profile")
    try:
        distribution = importlib.metadata.distribution("chemistrygym")
    except importlib.metadata.PackageNotFoundError:
        raise BackendError(
            "provider_unavailable",
            "ChemGymRL is not installed in this provider environment",
        ) from None
    direct = json.loads(distribution.read_text("direct_url.json") or "{}")
    if (
        distribution.version != "2.0.0"
        or direct.get("vcs_info", {}).get("commit_id") != SOURCE_SHA
    ):
        raise BackendError(
            "adapter_mismatch", "upstream installation does not match the pinned source"
        )
    try:
        import chemistrylab  # Registers only inside the isolated provider.
        import gymnasium as gym
    except ImportError:
        raise BackendError(
            "provider_unavailable",
            "the provider environment cannot import ChemGymRL",
        ) from None

    return gym.make(profile)


def _metadata(env):
    bench = env.unwrapped
    labels = [v.label for v in bench.shelf.get_vessels()]
    rows = []
    for index, (events, action) in enumerate(bench.actions):
        if any(event.name != action.event_name for _, event in events):
            raise ValueError("inconsistent upstream event")
        rows.append(
            {
                "index": index,
                "event": action.event_name,
                "parameters": [
                    [float(value) for value in event.parameter] for _, event in events
                ],
                "vessels": [int(v) for v in action.vessels],
                "affected_vessels": (
                    None
                    if action.affected_vessels is None
                    else [int(v) for v in action.affected_vessels]
                ),
                "dt": float(action.dt),
                "terminal": bool(action.terminal),
            }
        )
    characterization = bench.characterization_bench
    layout = {
        "order": "vessel_major",
        "vessel_indices": list(range(characterization.n_vessels)),
        "observation_list": list(characterization.observation_list),
        "sizes": {
            key: int(characterization.sizes[key])
            for key in characterization.observation_list
        },
        "targets": list(characterization.targets),
    }
    return rows, labels, layout


def _truth(env):
    return mapping.ground_truth(
        [
            {
                "label": v.label,
                "temperature_K": float(v.temperature),
                "volume_L": float(v.filled_volume()),
                "moles": {key: float(mat.mol) for key, mat in v.material_dict.items()},
            }
            for v in env.unwrapped.shelf.get_vessels()
        ]
    )


# Litres. Upstream levels come from np.linspace and its sums round differently
# (0.6000000000000001 L poured into a vessel that then holds 0.6 L), so an
# exact comparison refuses legitimate commands at volume boundaries.
_VOLUME_TOLERANCE_L = 1e-9


def transfer_precondition(source, target, event_name, parameter):
    """Why a pour or drain must be refused before stepping, or None.

    Overflow is decided by upstream itself: its event functions return -1 when
    the target overflows (and then rescale its contents). Running the same
    event on copies of the two vessels gives upstream's own answer without
    mutating the simulation, consuming random numbers (the layer update that
    draws them is not part of the event) or re-deriving upstream arithmetic.
    A pour that asks for more than the source holds is refused rather than
    silently clamped, as upstream would do.
    """
    available = float(source.filled_volume())
    if event_name == "pour by volume" and (
        available <= 1e-12 or float(parameter) - available > _VOLUME_TOLERANCE_L
    ):
        return "source volume is insufficient"
    trial_source, trial_target = copy.deepcopy((source, target))
    event = type(source)._event_dict[event_name]
    status = event(trial_source, 0, trial_target, parameter)
    if available - float(trial_source.filled_volume()) <= 1e-12:
        return "source volume is insufficient"
    if status == -1:
        return "target capacity would be exceeded"
    return None


class Backend:
    name = "chemgymrl"

    def __init__(self):
        self.version = {
            "package": "chemistrygym",
            "version": "2.0.0",
            "source_sha": SOURCE_SHA,
            "adapter_version": ADAPTER_VERSION,
        }
        self.env = None
        self.opened = False
        self.ended = False
        self.step_index = 0
        self.sim_time = 0.0
        self.receipts = OrderedDict()

    def _descriptor(self, profile, env):
        rows, labels, layout = _metadata(env)
        descriptor = mapping.derive_descriptor(
            profile,
            rows,
            labels,
            layout,
            {"max_steps": int(env.unwrapped.max_steps)},
            self.version,
        )
        verified = _verified_runtime()
        descriptor["reproducibility"] = {
            "status": "verified_for_profile" if verified else "unverified",
            # Evidence backs a claim; an unverified runtime has none to cite.
            "evidence": (
                "openai4s_lab_provider/chemgymrl/SOURCE.md#reproducibility"
                if verified
                else None
            ),
        }
        return descriptor, rows, labels, layout

    @_quiet
    def describe(self, profile):
        if self.opened:
            if profile != self.descriptor["profile"]:
                raise BackendError(
                    "invalid_parameters", "active session fixes the provider profile"
                )
            return self.descriptor
        env = _make(profile)
        try:
            return self._descriptor(profile, env)[0]
        finally:
            env.close()

    @_quiet
    def open(self, profile, seed, options, expected_capability_revision):
        if self.opened:
            raise BackendError("invalid_parameters", "a session was already opened")
        if not isinstance(options, dict) or options:
            raise BackendError("invalid_parameters", "unsupported provider options")
        if seed is not None and (type(seed) is not int or not 0 <= seed < 2**32):
            raise BackendError("invalid_parameters", "seed must be a uint32 or null")
        env = _make(profile)
        try:
            _seed(secrets.randbits(32) if seed is None else seed)
            vector, _ = env.reset(seed=seed)
            descriptor, rows, labels, layout = self._descriptor(profile, env)
            if descriptor["capability_revision"] != expected_capability_revision:
                raise BackendError(
                    "adapter_mismatch", "capability revision does not match"
                )
            observation = {
                "channels": mapping.decode_observation(vector.tolist(), layout),
                "sim_time": 0.0,
            }
            # Upstream reset computes this baseline before returning its observation.
            evaluation = {
                "reward": float(env.unwrapped.initial_reward),
                "ground_truth": _truth(env),
            }
        except BaseException:
            env.close()
            raise
        self.env, self.rows, self.labels, self.layout = env, rows, labels, layout
        self.opened = True
        self.descriptor = descriptor
        return {
            "session_id": "labprovider-" + secrets.token_hex(6),
            "descriptor": descriptor,
            "observation": observation,
            "evaluation": evaluation,
        }

    def _failure(self, command_id, code, message, *, status="failed"):
        return failed_receipt(
            command_id,
            code,
            message,
            status=status,
            sim_time=self.sim_time,
            step_index=self.step_index,
        )

    @_quiet
    def execute(self, provider_command_id, command, fencing_tokens):
        if provider_command_id in self.receipts:
            return self.receipts[provider_command_id]
        if not self.opened or self.ended:
            return self._failure(provider_command_id, "run_ended", "session has ended")
        index = mapping.command_to_action(self.rows, self.labels, command)
        if index is None:
            return self._failure(
                provider_command_id,
                "unsupported_action",
                "command has no exact action mapping",
                status="rejected",
            )
        row = self.rows[index]
        if row["event"] in ("pour by volume", "drain by pixel"):
            vessels = self.env.unwrapped.shelf.get_vessels()
            parameter = row["parameters"][0][0]
            refusal = transfer_precondition(
                vessels[row["vessels"][0]],
                vessels[row["affected_vessels"][0]],
                row["event"],
                parameter if row["event"] == "pour by volume" else int(parameter),
            )
            if refusal is not None:
                return self._failure(
                    provider_command_id, "precondition_failed", refusal
                )
        vector, reward, terminated, truncated, _ = self.env.step(index)
        self.step_index += 1
        # Adapter clock: event dt plus a positive settling interval once per action.
        self.sim_time += row["dt"] + (
            max(0.0, row["parameters"][0][0]) if row["event"] == "mix" else 0.0
        )
        end_reason = (
            "end_action"
            if row["terminal"]
            else (
                "max_steps"
                if self.step_index >= self.env.unwrapped.max_steps
                else "env_terminated" if terminated or truncated else None
            )
        )
        self.ended = end_reason is not None
        result = {
            "provider_command_id": provider_command_id,
            "applied": True,
            "status": "succeeded",
            "error": None,
            "raw": {"terminated": bool(terminated), "truncated": bool(truncated)},
            "end_reason": end_reason,
            "sim_time": self.sim_time,
            "step_index": self.step_index,
            "provider_action": {"gym_action": index},
            "observation": {
                "sim_time": self.sim_time,
                "channels": mapping.decode_observation(vector.tolist(), self.layout),
            },
            "evaluation": {"reward": float(reward), "ground_truth": _truth(self.env)},
        }
        self.receipts[provider_command_id] = result
        if len(self.receipts) > 256:
            self.receipts.popitem(last=False)
        return result

    def query(self, provider_command_id):
        return self.receipts.get(provider_command_id, {"known": False})

    def stop(self, reason):
        self.ended = True
        return {"stopped": True, "semantics": "end_session"}

    @_quiet
    def close(self):
        if self.env is not None:
            self.env.close()
            self.env = None
        self.ended = True
