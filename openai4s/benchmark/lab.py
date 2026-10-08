"""Offline Lab workflows; raw simulator evaluation never leaves this module."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
from threading import Event

from openai4s.lab.devices import DeviceRegistration, DeviceRegistry
from openai4s.lab.evaluation import Goal, compare, evaluate_run
from openai4s.lab.fake import FakeExtractorDevice, fake_registration
from openai4s.lab.manifest import load_descriptor
from openai4s.lab.models import (
    CommandOrigin,
    DeviceDescriptor,
    LabCaller,
    LabError,
    RunMode,
)
from openai4s.lab.policies import (
    FixedRulePolicy,
    ManagerPolicyEnv,
    RandomValidPolicy,
    run_episode,
)
from openai4s.lab.runtime import build_lab_manager


class LabBenchmarkRefusal(RuntimeError):
    """An observed refusal, containing only its normalized code."""


class _CountingFake(FakeExtractorDevice):
    def __init__(self):
        super().__init__()
        self.executions = 0
        self.queries = 0

    def execute(self, session_id, dispatch):
        self.executions += 1
        return super().execute(session_id, dispatch)

    def query(self, session_id, command_id):
        self.queries += 1
        return super().query(session_id, command_id)


@contextmanager
def _rig(ctx, backend="fake"):
    registry = DeviceRegistry()
    ports = []
    if backend == "fake":
        registration = fake_registration()
        factory = _CountingFake
    elif backend == "toy":
        from openai4s.lab.provider_process import ProviderProcessDevice
        from openai4s_lab_provider.toy import PROFILE, Backend

        descriptor = load_descriptor(Backend().describe(PROFILE))
        registration = DeviceRegistration(
            device_id=descriptor.device_id,
            backend="toy",
            mode=RunMode.SIMULATION,
            title="Offline toy",
            profiles=(PROFILE,),
            descriptor_loader=lambda profile: descriptor,
            port_factory=lambda: None,
        )

        def factory():
            return ProviderProcessDevice(
                descriptor.device_id,
                "toy",
                python=sys.executable,
                package_dir=Path(str(files("openai4s_lab_provider"))),
                runs_root=ctx.root / "lab/runs",
                sandbox_mode=os.environ.get("OPENAI4S_KERNEL_SANDBOX", "auto"),
            )

    else:
        raise ValueError("Lab benchmark backend must be fake or toy")

    def tracked_factory():
        port = factory()
        ports.append(port)
        return port

    registry.register(replace(registration, port_factory=tracked_factory))
    root = ctx.store.new_frame()
    caller = LabCaller(root, root, None, CommandOrigin.SYSTEM, None, None)
    manager = build_lab_manager(
        ledger_provider=lambda: ctx.store.lab, registry=registry
    )
    ctx.state["lab_manager"] = manager
    ctx.state["lab_ports"] = ports
    try:
        yield manager, caller, registration, ports
    finally:
        try:
            manager.close_all("benchmark teardown")
        finally:
            # Explicit checks, not asserts: python -O must not drop leak guards.
            if manager._live:
                raise AssertionError("Lab benchmark leaked a live provider")
            if any(port._sessions for port in ports):
                raise AssertionError("Device session leaked")


def _create(manager, caller, registration, seed=3):
    return manager.create_run(
        caller,
        {
            "device_id": registration.device_id,
            "profile": registration.profiles[0],
            "seed": seed,
            "budgets": {"max_steps": 16, "max_commands": 20},
        },
    )


def _command(created, *, revision=0):
    capability = next(
        c
        for c in created["descriptor"]["capabilities"]
        if c["operation"] == "transfer_liquid"
        and c["source"]
        == (
            "water_source" if created["descriptor"]["backend"] == "fake" else "beaker_1"
        )
    )
    return {
        "run_id": created["run"]["run_id"],
        "operation": capability["operation"],
        "source": capability["source"],
        "target": capability["target"],
        "parameters": {
            k: {"value": v["allowed"][0], "unit": v["unit"]}
            for k, v in capability["parameters"].items()
        },
        "expected_revision": revision,
        "idempotency_key": "benchmark-command",
    }


def _deny_approval(ctx, manager, caller, request):
    from openai4s.host_dispatch import build_dispatcher
    from openai4s.permissions import broker
    from openai4s.tools.registry import get_tool

    dispatcher = build_dispatcher(ctx.config, frame_id=caller.frame_id)
    dispatcher.set_lab_manager(lambda: manager)
    ctx.store.set_permission_rule(
        scope="conversation",
        scope_id=caller.root_frame_id,
        tool="lab_execute",
        pattern="*",
        decision="ask",
    )
    pending, cancelled = Event(), Event()
    events = []

    def emit(event):
        if event.get("type") == "await_permission":
            events.append(event)
            pending.set()

    permission_broker = broker()
    permission_broker.register_channel(
        caller.root_frame_id, emit, cancel_event=cancelled, store=ctx.store
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(get_tool("lab_execute").invoke, dispatcher, request)
            try:
                if not pending.wait(5):
                    raise AssertionError("Lab approval gate was not reached")
                if ctx.store.lab.list_commands(request["run_id"]):
                    raise AssertionError("A command was recorded before approval")
                if not permission_broker.resolve(
                    events[-1]["decision_id"], allow=False
                ):
                    raise AssertionError("The approval could not be denied")
                result = future.result(timeout=5)
            finally:
                cancelled.set()
    finally:
        permission_broker.unregister_channel(caller.root_frame_id)
    if ctx.store.lab.list_commands(request["run_id"]):
        raise AssertionError("A denied command was recorded")
    if str(result.get("error", "")).startswith("Permission denied:"):
        raise LabBenchmarkRefusal("approval_denied")
    return {"command_state": result.get("command", {}).get("state")}


def lab_simulation(ctx, inputs):
    """Normalize actual manager/Host evidence, never the requested outcome."""
    mode = inputs.get("scenario", "success")
    if mode not in {"success", "stale_revision", "approval_denied", "recovered"}:
        raise ValueError("Unknown Lab benchmark scenario")
    with _rig(ctx, inputs.get("backend", "fake")) as (manager, caller, reg, ports):
        created = _create(manager, caller, reg)
        run_id = created["run"]["run_id"]
        try:
            request = _command(created, revision=99 if mode == "stale_revision" else 0)
            if mode == "approval_denied":
                return _deny_approval(ctx, manager, caller, request)
            unknown = False
            if mode == "recovered":
                if reg.backend != "fake":
                    raise ValueError("Response-loss injection requires fake backend")
                ports[-1].lose_response_next()
                ports[-1].fail_query_next()
            try:
                result = manager.execute(caller, request)
            except LabError as exc:
                if mode != "recovered" or exc.code.value != "outcome_unknown":
                    raise LabBenchmarkRefusal(exc.code.value) from None
                unknown = True
                result = manager.status(caller, run_id, exc.details["command_id"])
            command = result["command"]
            if (
                command["state"] == "rejected"
                and reg.backend == "fake"
                and ports[-1].executions
            ):
                # A host-side refusal is decided before dispatch (review P3-2).
                raise AssertionError("A refused action was dispatched")
            if command["state"] in {"rejected", "failed", "not_dispatched"}:
                raise LabBenchmarkRefusal(command["error_code"])
            output = {
                "command_state": command["state"],
                "revision": result["run"]["revision"],
                "step_count": result["run"]["step_count"],
                "observed_unknown": unknown,
            }
            if reg.backend == "fake":
                output.update(
                    executions=ports[-1].executions, queries=ports[-1].queries
                )
            return output
        finally:
            manager.stop(caller, run_id, "benchmark episode complete")


class _LLMPolicy:
    """Inject a JSON completion callable receiving only public sensor inputs."""

    def __init__(self, completion):
        self.completion = completion

    def select_action(self, descriptor, observation, *, step):
        prompt = json.dumps(
            {"descriptor": descriptor, "observation": observation, "step": step}
        )
        return json.loads(self.completion(prompt))


class _ScriptedLLM:
    """Two canned JSON replies, independent of evaluation and hidden state."""

    def __init__(self):
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        if self.calls == 1:
            return json.dumps(
                {
                    "operation": "transfer_liquid",
                    "source": "extraction_vessel",
                    "target": "beaker_1",
                    "parameters": {"volume": {"value": 200, "unit": "mL"}},
                }
            )
        return json.dumps(
            {
                "operation": "end_experiment",
                "source": None,
                "target": None,
                "parameters": {},
            }
        )


def _snapshot(ledger, run_id, page_size=5):
    """Exhaust cursored streams; evaluations only support a growing prefix."""
    commands, observations = [], []
    for output, fetch, cursor_key, initial in (
        (commands, ledger.list_commands, "after_seq", 0),
        (observations, ledger.list_observations, "after_sequence", -1),
    ):
        cursor = initial
        while True:
            page = fetch(run_id, **{cursor_key: cursor}, limit=page_size)
            output.extend(page)
            if len(page) < page_size:
                break
            cursor = page[-1]["seq" if cursor_key == "after_seq" else "sequence"]
    limit = page_size
    while True:
        evaluations = ledger.list_evaluations(run_id, limit=limit)
        if len(evaluations) < limit:
            break
        limit *= 2
    return commands, observations, evaluations


def lab_compare_policies(ctx, inputs):
    seeds = tuple(inputs.get("seeds", (3, 17, 41)))
    if not seeds or any(
        type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds
    ):
        raise ValueError("Policy seeds must be a nonempty list of uint32 integers")
    goal = Goal(
        "toy-extract-v0",
        "toy_solute",
        "beaker_1",
        0.5,
        0.9,
        definition="Fictional toy collection goal; not a chemistry claim",
    )
    cohorts = {name: [] for name in ("fixed", "random", "scripted_llm")}
    counts = {}
    with _rig(ctx) as (manager, caller, reg, ports):
        for name in cohorts:
            states, reasons = Counter(), Counter()
            applied = observed = illegal = unresolved = llm_calls = 0
            for seed in seeds:
                fake_llm = _ScriptedLLM()
                policy = {
                    "fixed": lambda: FixedRulePolicy(),
                    "random": lambda: RandomValidPolicy(seed),
                    "scripted_llm": lambda: _LLMPolicy(fake_llm),
                }[name]()
                created = _create(manager, caller, reg, seed)
                run_id = created["run"]["run_id"]
                try:
                    episode = run_episode(
                        policy,
                        ManagerPolicyEnv(
                            manager,
                            caller,
                            run_id,
                            descriptor=DeviceDescriptor.from_dict(
                                created["descriptor"]
                            ),
                        ),
                        max_steps=16,
                    )
                    # A run that ended itself (end_experiment, a budget) gives
                    # its provider back then, not at the harness's stop below.
                    if ctx.store.lab.get_run(run_id)["status"] in {"ended", "failed"}:
                        if run_id in manager._live:
                            raise AssertionError("An ended run kept its provider")
                finally:
                    manager.stop(caller, run_id, "policy episode complete")
                if manager._live:
                    raise AssertionError("Policy episode leaked a provider")
                commands, observations, evaluations = _snapshot(ctx.store.lab, run_id)
                result = evaluate_run(
                    ctx.store.lab.get_run(run_id),
                    commands,
                    observations,
                    evaluations,
                    goal=goal,
                    episode=episode,
                )
                cohorts[name].append(result)
                states.update(c["state"] for c in commands)
                reasons[episode["stop_reason"]] += 1
                applied += result["applied_action_count"]
                observed += result["evidence_completeness"]["with_observation"]
                illegal += result["rejected_illegal_action_count"]
                unresolved += result["outcome_unknown"]["unresolved_count"]
                llm_calls += fake_llm.calls
            counts[name] = {
                "command_states": dict(sorted(states.items())),
                "episode_stop_reasons": dict(sorted(reasons.items())),
                "applied_action_count": applied,
                "with_observation": observed,
                "rejected_illegal_action_count": illegal,
                "unresolved_count": unresolved,
                "llm_calls": llm_calls,
            }
        # Never return evaluate_run's raw metrics, truth-derived amounts or ids.
        return {"comparison": compare(cohorts), "counts": counts, "seeds": list(seeds)}
