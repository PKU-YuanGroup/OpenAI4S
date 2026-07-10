"""Deterministic, stdlib-only scenario harness for OpenAI4S.

The harness is deliberately separate from :mod:`openai4s`: it drives scripted
scenarios and records contract traces, but production code never imports it.
"""

from .runner import ScenarioResult, run_scenario
from .schema import SCHEMA_VERSION, Scenario, load_scenario

__all__ = [
    "SCHEMA_VERSION",
    "Scenario",
    "ScenarioResult",
    "load_scenario",
    "run_scenario",
]
