# Internal simulation Lab contract

`openai4s.lab` implements OpenAI4S's own `openai4s.lab/v1-draft` vocabulary.
It is organized around public MHS design ideas; it does **not** claim official
MHS API compatibility. This package is standard-library-only and supports only
`simulation`. It does not connect to physical hardware. Importing it neither
registers devices nor starts providers.

## Objects and identity

Frozen dataclasses define quantities, discrete parameter specifications,
capabilities, resources, sensor channel specifications, device descriptors,
requests, normalized commands, dispatches, receipts, observations, evaluations,
session opening, stop results, budgets and caller context. List-shaped typed
fields are tuples; JSON serializers emit arrays. `from_dict` rejects unknown
keys, missing required fields, invalid types and nonfinite numbers; booleans
are not numeric values. Nullable fields are still required unless the value
class explicitly supplies a default. Unavailable channel specifications have
a reason; unavailable/unknown readings have a null value. An array channel may
declare `axes`, one per dimension, so a reader can tell what each index means:
ChemGymRL's `layers` channel labels its rows with the vessels they show and
leaves the pixel axis unlabelled.

A `DeviceDescriptor` fixes device, backend, profile, simulation mode, versions,
resources, capabilities, sensor channels and limits. Its time unit is
`model_time` and `wall_clock_equivalent` is null: model time is not seconds.
Reproducibility is `unverified`, `verified_for_profile`, or `not_reproducible`;
a verified claim requires evidence for that profile. Assumptions describe
additional model behavior without claiming it belongs to the upstream model.

`load_descriptor` validates unique identities, resource and channel references,
unambiguous operation/source/target matches and strictly increasing nonempty
settings. A supplied capability revision must match the recomputed digest.
When omitted at this loading boundary it is computed; the resulting value
always carries it.

Run, command, observation and evaluation IDs have prefixes `labrun`, `labcmd`,
`labobs`, `labeval`, followed by a hyphen and 12 random hexadecimal characters.
Provider command identity is the durable command identity. A simulation
resource key is `lab:<device_id>#<run_id>:<resource_id>`.

Canonical JSON sorts dictionary keys, uses compact separators, preserves
Unicode and refuses NaN. SHA-256 hashes its UTF-8 bytes. Capability revision
sorts capabilities by `capability_id`; request hash excludes idempotency key
and expected revision after unit normalization; config hash includes device,
profile, seed, budgets and options. An idempotency key contains 1–128 printable
ASCII characters. A new mode or profile requires a new run.

## State and failure semantics

The immutable tables in `models.py` are the transition authority.

```text
creating -> ready <-> busy
creating -> failed
ready/busy -> quarantined -> ready
creating/ready/busy/quarantined -> ended
```

`ended` and `failed` are terminal. Any nonterminal run can end (stop, budget,
idle timeout, provider loss, deletion); only a run that is still being created
can fail. Quarantine forbids new commands while an execution outcome remains
uncertain, and lifts only when reconciliation has resolved the run's last
unknown command. `run_sources_for` derives the ledger's CAS sources.

```text
created -> awaiting_approval -> admitted -> dispatching -> running
created -> admitted
created/awaiting_approval/admitted -> rejected | not_dispatched
dispatching/running -> succeeded | failed | outcome_unknown | stop_requested
stop_requested -> stopped | succeeded | failed | outcome_unknown
dispatching/outcome_unknown -> not_dispatched     (proof of non-receipt)
outcome_unknown -> succeeded | failed             (a queried receipt)
```

Command terminal states are `succeeded`, `failed`, `rejected`, `not_dispatched`
and `stopped`. `outcome_unknown` remains reconcilable; it is never automatic
permission to resend. It leaves only through reconciliation: a queried receipt,
or `not_dispatched` when the device proves it never received the command. A
device proves that by remembering every command id a session accepted, so
`query` returns `None` only for an id it never saw and raises
`LabError(OUTCOME_UNKNOWN)` for one whose receipt it no longer retains; it
refuses to execute such an id again. A provider receipt that was not applied
(`failed` or `rejected`) is recorded as a `failed` command; the command state
`rejected` is reserved for host refusals before dispatch. Once a command is
dispatched its exits also free leases and move the run, so only the ledger's
`record_receipt`, `mark_outcome_unknown` and `mark_not_dispatched` may take
them. `command_sources_for` derives CAS sources from the same table. A stop
request becomes `stopped` only after provider confirmation.
Run end reasons are `end_action`, `max_steps`, `env_terminated`, `stopped`,
`budget_exhausted`, `provider_lost`, `idle_timeout`, `create_failed`, `deleted`.

A receipt preserves raw `terminated` and `truncated` flags. Provider receipt
status is `succeeded`, `failed` or `rejected`; the latter two cannot be applied.
Its end reason is null, `end_action`, `max_steps` or `env_terminated`.
Provider observation payloads contain `channels` and `sim_time`; evaluation
payloads contain `reward`, `ground_truth` and optional `metrics`. The host adds
ledger IDs, sequence, ownership and timestamps when persisting them. Initial
observation sequence is zero; subsequent applied commands advance it.

## Errors and units

`LabError` carries a code, a safe human message and optional details. Messages
must not contain simulator truth. All 21 error codes are fixed:

| Code | Meaning |
| --- | --- |
| `invalid_parameters` | Wrong shape, type, key or numeric value. |
| `unsupported_action` | Missing capability or unavailable exact setting. |
| `unit_mismatch` | No permitted unit conversion. |
| `precondition_failed` | Provider refused without changing state. |
| `stale_revision` | Caller revision differs from the current state. |
| `idempotency_conflict` | Same key, different request. |
| `run_not_found` | Run absent or outside caller ownership. |
| `device_not_found` | Device absent, or the device has no such profile. |
| `run_ended` | Run is terminal. |
| `resource_busy` | Resource occupied or stale fencing token. |
| `resource_quarantined` | Resource has an unknown outcome. |
| `budget_exhausted` | A forced limit was exhausted. |
| `approval_denied` | Approval refused. |
| `persistence_unavailable` | Durable write failed. |
| `provider_unavailable` | Provider/session absent or unavailable. |
| `provider_timeout` | Receipt did not arrive. |
| `provider_protocol_error` | Invalid, oversized or incompatible frame. |
| `outcome_unknown` | Execution may have happened without a receipt. |
| `adapter_mismatch` | Runtime capabilities differ from expected revision. |
| `replay_forbidden` | Side effect requested from recovery. |
| `mode_mismatch` | Request and run modes differ. |

Matching first selects exact operation/source/target, then checks the exact
parameter-name set, then converts units and matches a setting. Units are `L`,
`mL`, `layer_px`, `model_time` and `dimensionless`. Only L/mL conversion is
allowed (factor 1000). Settings use `math.isclose` with both tolerances `1e-9`;
the normalized value is the advertised setting, never rounded, clipped or
chosen by nearest neighbor. Unsupported-setting details carry the parameter,
allowed values and unit. The standalone quantity normalizer calls the unnamed
parameter `value`; command matching replaces that with the actual name.
Source capacity and target overflow are provider preconditions.

Budgets default to 50 steps (override with the chosen profile's limit), 200
commands, 30 minutes wall time, 3 consecutive failures and 60 minutes idle time.
The future manager owns enforcement and the four-live-provider daemon limit;
this core package defines values, not a scheduler.

## Ports and registration

`DevicePort` supplies describe, open, execute, query, stop, close and alive.
Explicit refusals return unapplied receipts. Transport failures are `LabError`,
never bare `TimeoutError`/`ConnectionError`. A timeout can leave a live session:
query its command ID before drawing conclusions. Unavailable/protocol-error
sessions are dead; failed capability matching at open creates no live session.

`LabLedgerPort` defines atomic admission/dispatch, fencing, receipt persistence,
unknown-outcome quarantine, initial observations and ordered reads/events.
Its dictionaries decode JSON columns and remove their `_json` suffix. Missing
rows are null, timestamps are integer milliseconds. It neither opens a database
nor implements migrations here. `LabManagerPort` carries caller context into
every method. Caller origins are `agent_tool`, `host_sdk`, `manual_ui`, `system`;
execution ownership is `agent`, `user_repl`, `lifecycle`, `recovery` or null.
Mutating manager operations must reject recovery contexts.

`DeviceRegistry` is explicit and thread safe, lists sorted device IDs, rejects
duplicate IDs and checks profile membership before calling a descriptor loader.
`fake_registration()` creates a registration without installing it.

## Projections and honesty

Persistence serializers include evaluation where the contract requires it;
they are not agent views. `project_observation` exposes simulated sensors only.
An array larger than 256 elements becomes a shape, min/max/mean summary and a
truncation flag inside its value. `full=True` preserves the complete array.
Unavailable/unknown channels always carry null, even if an in-memory stale
value was supplied. Categories cannot carry arbitrary dictionaries.
`project_descriptor` selects the public descriptor fields, strips private
provider/evaluation fields recursively and validates the remaining schema.

Evaluation is separate: reward and exact material composition never belong in
observations, default projections, logs or error messages. Missing pressure
is not a measurement of zero. Unknown execution is not success. No time
conversion, uncertainty or reproducibility claim is invented.

`FakeExtractorDevice` is an unrelated deterministic toy model for offline
integration tests. It provides transfers, layer draining, mixing, settling and
ending, sensor-shaped outputs, per-command deduplication and per-resource
fencing. Fault hooks simulate explicit refusal, response loss with a queryable
receipt, process death and capability mismatch. It provides no evidence about
ChemGymRL accuracy or physical hardware behavior.
