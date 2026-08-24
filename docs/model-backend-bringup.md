# Model backend bring-up and admission

[中文说明](model-backend-bringup_zh.md)

OpenAI4S treats model discovery, accelerator routing, model-asset acquisition,
backend bring-up, and formal scientific execution as separate states. This is a
framework-level contract used by every Agent entry point; it is not a workflow
that exists only in one Skill.

The protein-design MCP connector is the first adopter. Other checkpoint-backed
connectors can reuse the same Host tools and admission state machine, but each
connector must still implement a real inference canary and parse its own output.
The framework cannot infer model readiness from a visible GPU, an installed
package, or a file that merely has a checkpoint-like name.

## State model

| State | Meaning | What it does not prove |
| --- | --- | --- |
| discovered | A connector or tool is visible to the Agent. | The backend, GPU, or weights exist. |
| route selected | The user selected `local` or one `ssh:<alias>` execution target. | The route is reachable or provisioned. |
| staged | Exact model bytes were imported into `model-assets/` and hashed. | The backend can load or run those bytes. |
| admitted | A real minimal inference succeeded and its parsed terminal record reported the expected checkpoint digest on the selected route. | Experimental validity or scientific success for later inputs. |
| formal | The requested scientific operation reused the exact admitted identity. | Evidence beyond what that operation actually returned. |

An admission identity binds all of the following:

- connector namespace and operation;
- immutable backend revision;
- checkpoint SHA-256;
- execution target.

Changing any one of them requires another canary. Admission is deliberately
process-local: restarting the backend process also requires another canary. A
new process has not demonstrated that its environment, imports, driver access,
model load, inference path, and output parser still work.

## User-visible workflow

1. Select a concrete operation. Generic connector discovery must not ask for or
   download checkpoints.
2. If the operation needs a GPU, call `host.accelerator_status()`. It probes
   local NVIDIA hardware first, then reports configured SSH GPU routes. When
   more than one candidate exists, the Agent asks the user to choose; it does
   not silently prefer local or remote execution.
3. If the operation needs a checkpoint and no path was supplied, stop and ask
   whether the user already has the file. Do not search for or download weights
   while that question is unanswered.
4. If the user has the file, call `host.stage_model_asset(source_path, ...)`.
   This is an approval-gated exact-path import. It rejects secret paths and
   symlink sources, copies in bounded chunks, and verifies an optional expected
   SHA-256.
5. If the user does not have the file, ask for network authorization and use
   the ordinary controlled download path. The source is not restricted to a
   framework-maintained model allowlist: the approved source, revision, and
   resulting SHA-256 become evidence for this particular bring-up. Import the
   downloaded file through the same staging tool.
6. Run a real minimal inference with `run_mode="canary"`, using the same
   adapter, backend revision, staged checkpoint digest, and execution target as
   the intended formal call.
7. Admit only when the connector returns a successful terminal record, the
   parsed output is usable, and the observed checkpoint digest equals the
   requested digest. Failed canaries remain failed terminal evidence.
8. Retry the original operation with `run_mode="formal"`. The connector refuses
   it if no exact live-process admission exists.

Missing source code, an environment, or weights is a bring-up condition. The
Agent must not conclude that a backend is unavailable merely because it was not
preinstalled.

## Framework surfaces

### Accelerator routing

`host.accelerator_status()` returns local probe evidence, configured SSH routes,
ordered candidate targets, and `selection_required`. Hardware visibility is
kept separate from provider registration and model readiness. An empty SSH
registry therefore says nothing about local hardware, and a successful
`nvidia-smi` probe says nothing about whether a model backend is installed.

The existing `host.remote_gpu_status()` remains the detailed SSH capability
view. Selecting an SSH route still requires a live reachability and backend
preflight.

### Portable asset staging

`host.stage_model_asset(...)` imports one operator-owned regular file into the
current session workspace under `model-assets/`. The result includes its
portable relative path, byte size, SHA-256, `status: "staged"`, and
`admitted: false`.

The tool never downloads a file and never admits it. Download approval and
backend admission are independent controls. Connector-specific bundles may use
a manifest that pins every data file rather than treating a directory name as
a stable model identity.

### Reusable admission ledger

`openai4s.host.model_admission.ModelAdmissionLedger` supplies the common
identity and state transition. A connector owns the ledger inside the live
backend process and must provide the canary's observed checkpoint digest only
after its real handler and output parser have succeeded.

This separation is intentional: a generic framework can validate identities
and state transitions, but only the connector understands whether an
RFdiffusion `.trb`, a folding confidence payload, a language-model score file,
or another backend-specific output is complete and parseable.

## Protein-design adoption

The bundled **Protein Design** connector applies the contract to RFdiffusion,
ProteinMPNN, ColabFold model-data bundles, and ESM-2 checkpoint-backed calls.
Its connector manager enables admission enforcement for Agent-facing calls. A
formal call without a matching canary ends in a persisted failed terminal
record rather than disappearing from attempt accounting.

The connector currently executes as a local stdio process. It reports a
selected SSH execution target as unsupported until a verified remote adapter
exists; it does not reinterpret a remote selection as local work. Rosetta and
OpenMM operations that do not identify a checkpoint still retain their normal
revision, seed, terminal-record, and output-validation contracts.

See [the connector package documentation](../openai4s/mcp_servers/protein_design/README.md)
for backend variables, network isolation requirements, individual tool schemas,
and scientific evidence boundaries.

## Connector portability and lifecycle

In-tree Python connector rows persist `@openai4s/python`, not an absolute path
to one server's virtual environment. The token resolves to the current daemon
interpreter only when the connector is spawned. Startup migrates matching legacy
built-in rows while leaving arbitrary custom commands unchanged.

Adding a connector from **Customize → Connectors** enables its configuration;
it does not eagerly start the server. Discovery or the first call starts it
lazily. Editing launch configuration disconnects the cached process so the next
operation starts the updated command. Environment values are write-only in the
browser: existing values are not returned, selected values can be replaced, and
selected names can be explicitly removed.

## Adding another checkpoint-backed connector

A new connector should:

1. expose an immutable backend revision, checkpoint digest, execution target,
   and `canary|formal` run mode in its closed schema;
2. use `ModelAdmissionLedger` with a connector-specific namespace;
3. verify the checkpoint bytes before starting the backend;
4. emit a terminal record for success and every failure path;
5. run the same real handler for canary and formal calls, changing only the
   minimal input or workload required for the canary;
6. parse and validate backend-specific outputs before calling `admit()`;
7. require an exact admission before formal execution; and
8. keep execution-route adapters honest—an unsupported remote route must fail
   explicitly.

Connector authors should not persist admission across backend restarts unless
they can independently re-establish all runtime facts that the canary proves.

## Verification

The offline suite covers local/SSH route ordering and user selection,
approval-gated asset staging, digest mismatch and symlink refusal, admission
identity and process restart behavior, Protein Design canary/formal enforcement,
MCP lifecycle and shape normalization, connector command migration, launch
configuration editing, and static UI contracts. Real GPU inference remains a
deployment acceptance gate because default CI is deliberately offline and does
not install scientific model runtimes or checkpoints.
