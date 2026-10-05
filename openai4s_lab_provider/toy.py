"""Deterministic stdlib simulation and explicitly enabled transport fault probes."""

import hashlib
import json
import math
import os
import time
import uuid

from . import ADAPTER_VERSION
from .protocol import MAX_FRAME_BYTES, BackendError, failed_receipt

PROFILE = "toy-extract-v0"
DEVICE_ID = "toy.extractor.01"
SOURCE = "beaker_1"
TARGET = "extraction_vessel"
TRANSFER = f"transfer_liquid:{SOURCE}->{TARGET}"
END = "end_experiment"
# Only names, captured before open(), to test the pre-import scrub boundary.
_ENV_KEYS_AT_IMPORT = frozenset(os.environ)


class Backend:
    name = "toy"

    def __init__(self):
        self._opened = False
        self._ended = False
        self._options = {}
        self._step_index = 0
        self._volumes = {SOURCE: 1000, TARGET: 0}

    @property
    def version(self):
        return {"package": "toy", "version": "1", "adapter_version": ADAPTER_VERSION}

    def describe(self, profile):
        if profile != PROFILE:
            raise BackendError("device_not_found", "unknown toy profile")
        capabilities = [
            {
                "capability_id": END,
                "operation": END,
                "scope": "shared",
                "source": None,
                "target": None,
                "parameters": {},
                "side_effect": "ends_run",
                "resources": sorted([SOURCE, TARGET]),
                "observes": ["layers"],
                "terminal": True,
                "mapping_version": "1",
            },
            {
                "capability_id": TRANSFER,
                "operation": "transfer_liquid",
                "scope": "shared",
                "source": SOURCE,
                "target": TARGET,
                "parameters": {
                    "volume": {"unit": "mL", "allowed": [200, 400, 600, 800, 1000]}
                },
                "side_effect": "moves_material",
                "resources": sorted([SOURCE, TARGET]),
                "observes": ["layers"],
                "terminal": False,
                "mapping_version": "1",
            },
        ]
        canonical = json.dumps(
            capabilities,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return {
            "contract": "openai4s.lab/v1-draft",
            "device_id": DEVICE_ID,
            "mode": "simulation",
            "backend": self.name,
            "profile": profile,
            "backend_version": self.version,
            "resources": [
                {"resource_id": SOURCE, "kind": "vessel", "label": "Beaker 1"},
                {
                    "resource_id": TARGET,
                    "kind": "vessel",
                    "label": "Extraction vessel",
                },
            ],
            "capabilities": capabilities,
            "capability_revision": hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest(),
            "observation_channels": [
                {
                    "name": "layers",
                    "kind": "array",
                    "shape": [2, 5],
                    "unit": "dimensionless",
                    "source": "simulated_sensor",
                    "available": True,
                    "description": "Five occupied-layer bins per toy vessel",
                }
            ],
            "limits": {"max_steps": 50},
            "stop": {"supported": True, "semantics": "end_session"},
            "time": {"unit": "model_time", "wall_clock_equivalent": None},
            "reproducibility": {"status": "unverified", "evidence": None},
            "assumptions": [],
        }

    @staticmethod
    def _validate_options(options):
        flags = {"print_to_stdout_on_open", "crash_on_execute", "oversize_frame"}
        if not isinstance(options, dict) or set(options) - flags - {
            "sleep_on_execute_s",
            "echo_env_keys",
        }:
            raise BackendError("invalid_parameters", "invalid toy options")
        if any(type(options[key]) is not bool for key in flags & options.keys()):
            raise BackendError("invalid_parameters", "toy fault flags must be boolean")
        delay = options.get("sleep_on_execute_s", 0)
        if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
            raise BackendError("invalid_parameters", "invalid toy execute delay")
        keys = options.get("echo_env_keys", [])
        if (
            not isinstance(keys, list)
            or len(keys) > 128
            or any(not isinstance(key, str) or not 1 <= len(key) <= 256 for key in keys)
        ):
            raise BackendError("invalid_parameters", "invalid environment key probe")

    def open(self, profile, seed, options, expected_capability_revision):
        if self._opened:
            raise BackendError("invalid_parameters", "a session was already opened")
        descriptor = self.describe(profile)
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise BackendError("invalid_parameters", "seed must be a uint32 integer")
        self._validate_options(options)
        if expected_capability_revision != descriptor["capability_revision"]:
            raise BackendError("adapter_mismatch", "capability revision differs")
        self._opened = True
        self._ended = False
        self._options = dict(options)
        if options.get("print_to_stdout_on_open"):
            # Exercise the OS descriptor boundary, including C-extension-style output.
            os.write(1, b"toy provider stdout probe\n")
        result = {
            "session_id": "toy-" + uuid.uuid4().hex,
            "descriptor": descriptor,
            "observation": self._observation(),
            "evaluation": self._evaluation(),
        }
        if "echo_env_keys" in options:
            result["echo_env_keys"] = sorted(
                set(options["echo_env_keys"]) & _ENV_KEYS_AT_IMPORT
            )
        return result

    def _observation(self):
        return {
            "sim_time": self._step_index,
            "channels": [
                {
                    "name": "layers",
                    "kind": "array",
                    "unit": "dimensionless",
                    "shape": [2, 5],
                    "value": [
                        [int(volume >= level) for level in (200, 400, 600, 800, 1000)]
                        for volume in self._volumes.values()
                    ],
                    "quality": "ok",
                    "source": "simulated_sensor",
                }
            ],
        }

    def _evaluation(self):
        return {
            "reward": self._volumes[TARGET] / 1000,
            "ground_truth": {
                "vessels": [
                    {
                        "resource_id": resource,
                        "temperature_K": 297,
                        "volume_L": volume / 1000,
                        "moles": {"toy_component": volume / 1000},
                    }
                    for resource, volume in self._volumes.items()
                ]
            },
            "metrics": {},
        }

    @staticmethod
    def _action(command):
        if not isinstance(command, dict):
            return None
        if (
            command.get("operation") == END
            and command.get("capability_id") == END
            and command.get("source") is None
            and command.get("target") is None
            and command.get("parameters") == {}
        ):
            return END
        if (
            command.get("operation") != "transfer_liquid"
            or command.get("capability_id") != TRANSFER
            or command.get("source") != SOURCE
            or command.get("target") != TARGET
        ):
            return None
        parameters = command.get("parameters")
        if not isinstance(parameters, dict) or set(parameters) != {"volume"}:
            return None
        volume = parameters["volume"]
        if (
            not isinstance(volume, dict)
            or set(volume) != {"value", "unit"}
            or volume["unit"] != "mL"
            or type(volume["value"]) not in (int, float)
            or volume["value"] not in (200, 400, 600, 800, 1000)
        ):
            return None
        return int(volume["value"])

    def execute(self, provider_command_id, command, fencing_tokens):
        if not self._opened:
            raise BackendError("run_not_found", "no open session")
        if self._ended:
            return failed_receipt(provider_command_id, "run_ended", "session has ended")
        action = self._action(command)
        if action is None:
            return failed_receipt(
                provider_command_id,
                "unsupported_action",
                "command does not match a toy capability",
                status="rejected",
            )
        if isinstance(action, int) and (
            action > self._volumes[SOURCE] or self._volumes[TARGET] + action > 1000
        ):
            return failed_receipt(
                provider_command_id,
                "precondition_failed",
                "insufficient source volume or target capacity",
            )
        if self._options.get("sleep_on_execute_s"):
            time.sleep(self._options["sleep_on_execute_s"])
        if self._options.get("crash_on_execute"):
            os.write(2, b"toy provider: deliberate execute crash\n")
            os._exit(17)
        if isinstance(action, int):
            self._volumes[SOURCE] -= action
            self._volumes[TARGET] += action
        self._step_index += 1
        self._ended = action == END
        result = {
            "provider_command_id": provider_command_id,
            "applied": True,
            "status": "succeeded",
            "error": None,
            "raw": {"terminated": self._ended, "truncated": False},
            "end_reason": "end_action" if self._ended else None,
            "sim_time": self._step_index,
            "step_index": self._step_index,
            "observation": self._observation(),
            "evaluation": self._evaluation(),
        }
        if self._options.get("oversize_frame"):
            result["test_padding"] = "x" * MAX_FRAME_BYTES
        return result

    def stop(self, reason):
        if not isinstance(reason, str) or not reason:
            raise BackendError("invalid_parameters", "stop reason must be a string")
        self._ended = True
        return {"stopped": True, "semantics": "end_session"}

    def close(self):
        self._ended = True
        self._options.clear()
