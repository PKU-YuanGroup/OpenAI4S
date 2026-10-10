---
name: lab-simulation
description: Run simulation-only ChemGymRL lab experiments through the Web daemon; discover capabilities, execute bounded actions, reconcile unknown outcomes, analyze public observations, and report completion evidence.
origin: openai4s
category: simulation
capabilities:
  network:
    mode: none
    domains: []

---

# Simulation lab experiments

Use this recipe for a simulated extraction experiment, its public sensor data,
or a report on an existing run. Load it before using `lab_*` tools. Lab runs
are available only in the Web daemon; CLI calls fail as unavailable and
delegated children cannot operate Lab runs. Loading a Skill grants no
permissions: creation and execution still cross Host approval and validation.
The provider runs separately; do not import simulator packages in the kernel.

## Honest observations and bounded decisions

- This is a simulation, not a recommendation for a real chemical procedure.
  Never connect to, probe, or attempt to control a physical device.
- Read capabilities, resources, discrete parameter levels, units, channel
  availability, assumptions, and reproducibility information from the manifest.
  Do not invent capabilities or translate a model action to a real device.
- `model_time` is a model coordinate, not seconds. `layer_px` is a model layer
  index, not a physical volume. Keep each quantity's published unit.
- An unmodeled or unavailable quantity is unknown, never zero. Only channels
  declared as `simulated_sensor` are observations. A layer image is not a
  measurement of exact material composition or purity.
- Simulator ground truth and rewards are private evaluation data. Never query
  evaluation tables, import the private evaluator, inspect provider internals,
  or put hidden values in model context, logs, files, or the report. A high
  reward, successful command, or Gym `done` flag does not prove the goal.
- Choose one bounded action from the current manifest after inspecting the
  latest observation. Stop to inspect its result before choosing another;
  do not launch an unattended action loop or automatically retry execution.
- An `outcome_unknown` command is pending verification. Query the same
  `command_id` with `lab_status`; never resend it, including under a new
  idempotency key. A persistence error with a command ID may also mean the
  action already happened. Keep the uncertainty if reconciliation cannot resolve it.

## Discover, create, observe

The native sequence is `lab_list` → `lab_describe` → `lab_create` →
`lab_observe`. The equivalent Python calls are below. Inspect each result
before continuing. If the requested device/profile is absent or unavailable,
report that fact; do not silently substitute the toy backend or install packages.
This example selects the published WaterOil profile only after verifying it.

```python
from uuid import uuid4

catalog = host.lab.list()
print(catalog)
```

```python
device_id = "chemgym.extractor.01"
profile = "WaterOilExtract-v0"
device = next(
    (item for item in catalog["devices"] if item["device_id"] == device_id), None
)
if device is None or device["available"] is not True:
    raise RuntimeError("Requested simulation device is unavailable")
if profile not in device["profiles"]:
    raise RuntimeError("Requested simulation profile is unavailable")
descriptor = host.lab.describe(device_id, profile)
print(descriptor)
```

Confirm the requested objective and the manifest's assumptions before creating
one run. Record the create key before calling; retain it with the run ID.
Reusing this exact create request/key retrieves the same run. Re-executing a
cell that generates a new key creates a new request, so do not use that as a retry.

```python
create_key = "lab-skill-create-" + uuid4().hex
created = host.lab.create(
    device_id, profile, seed=42, idempotency_key=create_key
)
run_id = created["run"]["run_id"]
descriptor = created["descriptor"]
latest = host.lab.observe(run_id)
print({"run": latest["run"], "observation": latest["observation"]})
```

## Execute one action, then inspect

For a decision to settle the model once, this example uses its lowest
published duration level. Choose this only when settling is the intended next
step; it is not an extraction strategy or a promise of goal attainment. Other
actions must use the exact operation/source/target combination and allowed
quantity levels in the selected capability. `expected_revision` comes from
`run.revision` in the latest observation envelope, not the observation sequence.
Keep the command key stable for that one planned request.

```python
settle = next(
    cap for cap in descriptor["capabilities"] if cap["operation"] == "settle_model"
)
duration = settle["parameters"]["duration"]
latest = host.lab.observe(run_id)
if latest["run"]["status"] != "ready":
    raise RuntimeError("Run is not ready; inspect status before planning an action")
command_key = "lab-skill-command-" + uuid4().hex
request = {
    "source": settle["source"],
    "target": settle["target"],
    "parameters": {
        "duration": {"value": min(duration["allowed"]), "unit": duration["unit"]}
    },
    "expected_revision": latest["run"]["revision"],
    "idempotency_key": command_key,
}
try:
    result = host.lab.execute(run_id, settle["operation"], **request)
except RuntimeError as error:
    details = getattr(error, "details", None) or {}
    command_id = details.get("command_id")
    if command_id is None:
        raise
    result = host.lab.status(run_id, command_id)
print(result)
```

Check `result["command"]["state"]`; rejected and failed commands return
normally. Neither the absence of an exception nor the presence of an
observation proves that this action succeeded. For unresolved results, query
once when new evidence may be available, then return control rather than polling
forever. Do not execute another command while the run is quarantined.

```python
command_id = result["command"]["command_id"]
if result["command"]["state"] == "outcome_unknown":
    result = host.lab.status(run_id, command_id)
print({"run": result["run"], "command": result["command"]})
```

## Analyze complete public arrays inside Python

Native tool results summarize arrays larger than 256 elements. Read full
arrays with `host.lab.observe(run_id, full=True)` and keep those arrays in the
kernel. Print only the small derived result needed for the next decision;
`full=True` does not grant access to private evaluation values.

```python
full = host.lab.observe(run_id, full=True)
observation = full["observation"]
if observation is None:
    raise RuntimeError("No recorded observation is available")
channels = {channel["name"]: channel for channel in observation["channels"]}
layers = channels.get("layers")
if layers is None or layers["quality"] != "ok" or layers["value"] is None:
    layer_summary = {"status": "unavailable", "reason": "No usable layers channel"}
else:
    rows = layers["value"]
    layer_summary = {
        "status": "observed",
        "shape": layers["shape"],
        "unit": layers["unit"],
        "row_means": [sum(row) / len(row) if row else None for row in rows],
    }
print({
    "run_id": run_id,
    "observation_sequence": observation["sequence"],
    "layer_summary": layer_summary,
})
```

Use the manifest channel `axes` labels to associate rows with resources.
These means describe the public model sensor, not concentration or purity.
Choose the next single action from this observation, or end the experiment.

## End and report with evidence

End a ready run through the manifest's terminal `end_experiment` capability,
using a fresh observation revision and a stable key for this request. Handle an
unknown response by querying its command ID exactly as above. `lab_stop` is a
safety stop; it is not successful experiment completion.

```python
latest = host.lab.observe(run_id)
end_capability = next(
    cap for cap in descriptor["capabilities"]
    if cap["operation"] == "end_experiment" and cap["terminal"]
)
end_key = "lab-skill-end-" + uuid4().hex
try:
    ended = host.lab.execute(
        run_id, end_capability["operation"],
        source=end_capability["source"], target=end_capability["target"],
        parameters={}, expected_revision=latest["run"]["revision"],
        idempotency_key=end_key,
    )
except RuntimeError as error:
    details = getattr(error, "details", None) or {}
    command_id = details.get("command_id")
    if command_id is None:
        raise
    ended = host.lab.status(run_id, command_id)
print(ended)
```

Read `host.lab.commands(run_id, after_seq=0)` and
`host.lab.observations(run_id, after_sequence=-1)` for evidence, following
`next_after_seq` and `next_after_sequence` until no additional rows remain.
Reference the records' exact sequence numbers, not the array positions. The
report must name:

| Field | Required evidence |
| --- | --- |
| Simulation identity | `run_id`, device, profile, seed, adapter/source identity from the public descriptor, and manifest assumptions. |
| Actions | Exact command IDs and `seq`, requested quantities/units, resulting state, and associated observation IDs. |
| Observations | Exact `sequence` and command ID, channel/quality/unit, analysis method, and any unavailable quantities. |
| Evaluation basis | The profile's independent default collection goal, distinct from reward and Gym termination. WaterOil uses collected NaCl equivalents in `beaker_1`, excludes water from purity and counts oil contamination; GenWurtz uses the initial public target and non-solvent purity in `beaker_1`. These are benchmark choices, not real-world success criteria. Do not report private evaluation values. |
| Outcome and limits | Recorded run status/end reason, unresolved commands, missing evidence, and the next bounded decision. State that findings concern this simulation only. |

To hand over exact files, `lab_export` (or `host.lab.export(run_id)`) writes
the actions, observations JSON/CSV and a report as exact Artifact versions
after approval; cite the returned version IDs. It reads the ledger only and
never includes simulation ground truth.

When claiming completion, both `finalize_response` and `host.submit_output`
require `lab_runs: [{"run_id": <exact run>, "status": "completed"}]`, with one
entry for every run this turn created or sent a command to; leaving one out is
refused.
`finalize_response` takes this as a top-level field; `host.submit_output`
takes it inside its output dictionary. The Host independently checks that the
run ended with `end_action` or `max_steps`, has no unknown commands, has an
observation for every successful command, and passes the independent goal
evaluation. Started, Gym done, stopped, lost-provider, and unevaluable runs do
not satisfy this check. Do not weaken or omit the claim to bypass a refusal.

Use `task_status="partial"` and `status="running"` only for a genuinely active
run. Include its ID and the exact sentence “Simulation experiment is still
running.” (or “仿真实验仍在运行”) in the output summary. For example, after
checking the current status and only when leaving that experiment active:

```python
host.submit_output(
    {
        "summary": f"Simulation experiment is still running. Run: {run_id}.",
        "lab_runs": [{"run_id": run_id, "status": "running"}],
    },
    completion_bullets=[f"Recorded public observations for simulation run {run_id}."],
    task_status="partial",
)
```

For an ended, stopped, failed, or unresolved run that cannot meet the goal,
report the recorded state with `task_status` partial, failed, or blocked as
appropriate, and explain missing evidence. Never label it `running` to bypass
the completion check or call it a completed experiment.
State `stopped` or `unresolved` directly: in a Lab session the Host conservatively
requires verified completed-run evidence for completion/success wording in
public prose, even when the machine-readable task status is partial.
