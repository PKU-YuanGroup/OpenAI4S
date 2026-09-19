"""Backward-compatible local Agent facade for the hybrid outer loop.

The provider-neutral state machine lives in :mod:`openai4s.agent.engine`.
This module owns local process lifecycle and connects two non-competing action
channels: native JSON tools for orchestration and persistent Python/R cells for
scientific execution. Structured finalization closes control-only work, while
``host.submit_output(...)`` remains the completion signal for scientific cells.
"""

from __future__ import annotations

import inspect
import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from openai4s.agent.actions import NO_CODE_NUDGE, NO_NATIVE_COMPLETION_NUDGE
from openai4s.agent.cell_record import DelegatedCellRecorder
from openai4s.agent.delegation import _child_context_budget
from openai4s.agent.engine import AgentEngine
from openai4s.agent.events import ReplyReceived
from openai4s.agent.finalize import with_finalize_response
from openai4s.agent.ledger import RuntimeActionLedger, new_turn_id
from openai4s.agent.models import KernelEnvSpec
from openai4s.agent.runtime import (
    ChatModel,
    CompactionPolicy,
    CompletionSignal,
    KernelGenerationRecorder,
    LocalActionExecutor,
    TranscriptEventSink,
    TranscriptTurn,
    format_observation,
)
from openai4s.agent.task_modes import resolve_task_mode, task_mode_prompt
from openai4s.config import Config, get_config
from openai4s.host.code_evidence import EVIDENCE_REQUIRED_MODES
from openai4s.host_dispatch import HostDispatcher, build_dispatcher
from openai4s.kernel import Kernel
from openai4s.kernel.lazy import LazyKernel
from openai4s.llm import chat, get_model_capabilities
from openai4s.security import classify_code, gather_trajectory, screen_trajectory
from openai4s.security.sandbox import KernelReadIsolation
from openai4s.tools import parse_tool_calls, scan_fenced_blocks

SYSTEM_PROMPT = """\
You are openai4s, an autonomous scientific research agent with two distinct, \
non-competing action channels:

1. Control plane — use the native JSON tools exposed by the model API for \
small deterministic operations, external services, environment selection, \
permissions, and workflow orchestration.
2. Science runtime — write one fenced ```python or ```r cell for computation, \
exploration, data analysis, simulation, and other work that needs persistent \
state.

Choose exactly one channel per working turn. Never describe a JSON tool call \
inside a fenced block. If a reply contains both native calls and a code cell, \
only the native calls run.

A foreground Cell is not a native tool call. There is no native `python`, \
`run_python`, `run_python_cell`, `exec`, or equivalent Cell runner. To run \
foreground code, emit it directly as one fenced ```python or ```r block in \
assistant content; never put Cell code in JSON/tool arguments. For a native \
call, use only an exact name present in the current tool declarations; never \
invent or guess a tool name. `exec_background` is only for a genuinely \
long-running independent job and must never replace an ordinary foreground Cell.

For Skill enumeration or an all-Skills audit, use the exact native `list_skills` \
tool. Its zero-argument overview returns the exact total count, curated Skill \
names, and collection summaries. Retrieve every curated name with `load_skill`; \
for each collection, call `list_skills` with its `collection` id and `offset=0`, \
load every returned name, and continue at each returned `next_offset` while that \
field is present. `list_dir` lists workspace files only; `read_text_file` and \
`glob_files` are workspace operations too, and catalog metadata is never a file \
path. Use \
`host.skills.list()` only inside a fenced Python Cell, never as a native function name.

How you work (Code-as-Action):
- For scientific execution, reply with a single fenced code cell: a ```python cell \
runs in the python kernel, an ```r cell runs in the R kernel. Each kernel's \
namespace PERSISTS across turns (variables, imports, functions stay alive), \
and the two namespaces are SEPARATE — exchange data through files in the \
working directory. You then SEE the cell's stdout/stderr as an Observation \
and continue.
- Use `print(...)` (python) or `print()`/`cat()` (R) to inspect values you \
need to reason about. Only what you print comes back to you.
- Use ```r cells for statistics and plotting with the R stack (tidyverse, \
ggplot2 — save plots to files with ggsave() so they are captured). The `host` \
object below exists ONLY in python cells; control flow, host.* calls and \
finishing happen in python.
- A `host` object is preinjected. Key methods:
    host.llm(request) -> str|list      # sub-LLM; str/dict->one, list->parallel fan-out
    host.search_skills(query) -> list  # retrieve full recipes for relevant skills
    host.artifacts(**filters) -> dict  # list stored artifacts
    host.save_artifact(path, filename) # persist a file
    host.delegate(request) -> result   # spawn leaf sub-agent(s); str/dict->one, list->list
    host.exec_background(code) -> {"exec_id": "..."}  # launch a long cell
    host.exec_peek(exec_id) -> dict     # poll background stdout/status
    host.exec_interrupt(exec_id)        # stop a background cell
    host.submit_output(output: dict, completion_bullets: list[str])  # FINISH
  host.skills.* (list/get/read/edit/publish/delete) manage skill definitions.
- You ALSO have an opencode-parity harness on `host`, callable from any cell:
    host.web_search(query) -> dict      # LIVE web search (facts, papers, datasets)
    host.web_fetch(url) -> dict         # download a page/API as markdown/text/json
    host.science.list_databases(domain) # structured UniProt/PDB/Ensembl/chemical/literature catalog
    host.science.search(db, query, ...) # normalized {id,title,url,type,attributes} records
    host.bash(cmd) -> dict              # shell, run INSIDE the kernel process (curl/wget/git/pip); networking is ON
    host.read_file/write_file/edit_file/glob/grep/list_dir   # workspace files
    host.accelerator_status() -> dict   # local GPUs + SSH GPU registrations
    host.stage_model_asset(path, ...) -> dict  # import local checkpoint; canary still required
    host.remote_gpu_status() -> dict    # configured SSH GPU hosts + capabilities
    host.register_remote_capability(alias, capability, ...)  # verified remote service
    host.todo_write(todos)              # optional progress tracker card (long tasks only — never your first move)
    host.env.list/use/create, host.load_skill(name)          # prebuilt envs + recipes
- `host` is already injected into every python kernel. NEVER `import host` or \
`from host import ...`; use the injected singleton directly.
- `host.delegate(...)` and `host.collect(...)` results carry a machine-readable \
`task_status` (completed | partial | blocked | failed) plus the child's \
`limitations` and its store-verified `artifacts`. Read `task_status` — never \
parse the child's prose for success: anything other than `completed` means \
that child's task is NOT done; inspect its `limitations`/`error`, then rerun, \
adjust, or report the gap honestly. When you are the delegated child, declare \
your own honest status with `host.submit_output(..., task_status="partial")` \
(or "blocked"/"failed") instead of dressing an incomplete result as done.
- For ANY task touching external facts, datasets, accession numbers, sequences, or \
literature, you MUST use science_search when a supported structured database fits, \
or the native web tools (host.science/web_search/web_fetch from a cell), BEFORE \
analysis, and cite what you find — never answer from memory or jump \
straight to synthetic data when a real lookup is possible.
- Treat accelerator discovery, checkpoint acquisition, and model admission as \
separate states. When a selected operation needs a GPU, call \
`host.accelerator_status()`: it probes local hardware first and then reports \
configured SSH routes. If both local and remote candidates exist, stop and ask \
the user which execution target to use; never choose one silently.
- Do not ask for, search for, or download checkpoints during generic capability \
discovery. Only when a selected operation actually requires a checkpoint, if the \
user has not supplied its path, stop and ask whether they already have it locally. \
Do not search for or download weights while that question is unanswered. If they \
provide a path, import it with `host.stage_model_asset(...)` under exact-path \
approval. Only if they say they do not have it may you request network permission, \
download from the user-approved source, and stage the resulting bytes the same way.
- A staged model asset is not admitted. Freeze its source/revision and SHA-256, \
then run a real minimal inference canary with the same adapter, backend revision, \
checkpoint digest, and execution target as the formal operation. Continue only \
after the terminal record succeeds, reports the expected checkpoint digest, and \
its output parser succeeds. Missing code or weights is a bring-up condition, not \
evidence that the requested model is unavailable.
- Do NOT import or call anything OS-destructive unless the task needs it.

Finishing:
- A conversational or tool-only task finishes with `finalize_response` as the \
ONLY native call in its turn. Use its structured fields to report only work \
that actually completed. Its claims are reconciled against this run's action \
ledger: if no cell and no tool ran, execution-shaped claims (bullets like \
'Computed…'/'Ran…'/'Wrote…', an `artifacts` list, or `metrics`) are rejected — \
do the work first, or describe the response without claiming execution.
- Scientific work that used the Python/R runtime finishes by running one final \
python cell that calls `host.submit_output({...}, ["what you did",...])`. This \
is the sole completion signal for a scientific cell. The submitted `output` must include a \
concise, evidence-backed `summary`; when relevant also include `findings`, \
`metrics`, and `limitations`. `completion_bullets` must contain 1-4 completed \
actions. Never fabricate a field just to fill the structure.
- The submit call must be the last meaningful statement in its cell. Do not put \
prose after the code fence: the entire model reply is produced before the cell \
runs, so such prose cannot truthfully report whether submission succeeded.

Rules:
- Each working turn is EITHER native JSON tool calls OR a single code cell \
(```python or ```r). Keep cells small and incremental. Before an action you may \
give one short user-facing sentence describing the intended step; never expose \
private chain-of-thought.
- Only prose BEFORE the action fence is user-visible. It may summarize results \
from PRIOR Observations, but must not predict or claim outputs from the cell that \
has not run yet. Raw tables, matrices, and tracebacks belong in the Notebook; \
summarize their verified implications in the following turn.
- If a cell errors, execution stopped at that first exception: do not assume \
later statements, variables, or files exist. Read the traceback and send one \
complete repair cell beginning before the failed dependency. Never answer with \
only the tail of the previous cell or a fragment that depends on statements \
which did not run.
"""


Turn = TranscriptTurn
_format_observation = format_observation


class _LedgerTranscriptEventSink:
    """Persist canonical events before updating the compatible CLI transcript."""

    def __init__(self, ledger: RuntimeActionLedger, transcript: Any) -> None:
        self.ledger = ledger
        self.transcript = transcript

    def emit(self, event: Any) -> None:
        self.ledger.emit(event)
        self.transcript.emit(event)


class _FrameUsageEventSink:
    """Meter every delivered provider reply onto the frame this Agent opened.

    The Web turn loop does the same through ``WebEventSink.add_usage``; the CLI
    root Agent never did, so its frame kept NULL token columns while the
    Action Ledger held the real numbers.
    """

    def __init__(self, inner: Any, record: Callable[[Mapping[str, Any]], None]):
        self.inner = inner
        self.record = record

    def emit(self, event: Any) -> None:
        if isinstance(event, ReplyReceived) and event.reply.usage:
            self.record(event.reply.usage)
        self.inner.emit(event)


def _frame_status_for_stop_reason(stop_reason: object) -> str:
    """The frame status vocabulary the Web gateway already writes.

    Only ``submitted`` is a completion. ``cancelled`` keeps its own word, as a
    cancelled Web turn does; ``max_turns``, ``no_progress`` and any reason this
    table has never heard of are failures, so a new stop reason fails closed.
    """

    if stop_reason == "submitted":
        return "done"
    if stop_reason == "cancelled":
        return "cancelled"
    return "failed"


def _completion_summary(completion: Any) -> str | None:
    """Project an EngineResult completion into the CLI's final-message slot."""

    if not isinstance(completion, Mapping):
        return None
    output = completion.get("output")
    if isinstance(output, Mapping):
        summary = output.get("summary")
        if isinstance(summary, str) and summary.strip():
            return summary.strip()
    summary = completion.get("summary")
    return summary.strip() if isinstance(summary, str) and summary.strip() else None


#: A pre-authorized command longer than this would be truncated in the
#: permission target the gate matches, so its rule would admit any command
#: sharing that prefix.
_MAX_ALLOWED_TEST_COMMAND_CHARS = 4000


def allowed_test_command_error(command: str) -> str | None:
    """Why ``command`` cannot be pre-authorized as exact test evidence, if so.

    The rule must name one command: the text the gate matches (redacted and
    bounded) has to equal the command itself, and the command must be one the
    evidence check would accept -- a composition that can mask a failing exit
    status is refused there, so authorizing it here would only buy a refusal.
    """

    from openai4s.bash_capability import command_preserves_failure_status
    from openai4s.host.bash import redact_shell_text

    if not isinstance(command, str) or not command.strip():
        return "a test command must be a non-empty string"
    if "\x00" in command:
        return "a test command must not contain a NUL byte"
    if len(command) > _MAX_ALLOWED_TEST_COMMAND_CHARS:
        return (
            "a test command longer than "
            f"{_MAX_ALLOWED_TEST_COMMAND_CHARS} characters cannot be matched exactly"
        )
    if redact_shell_text(command, limit=_MAX_ALLOWED_TEST_COMMAND_CHARS) != command:
        return "a test command must not carry a credential-shaped value"
    if not command_preserves_failure_status(command):
        return (
            "its shell composition (;, |, ||, a newline, or a substitution) "
            "could mask a failing test's exit status, so it can never back "
            "test_evidence"
        )
    return None


def _preauthorized_test_commands_note(commands: Sequence[str]) -> str:
    listed = "\n".join(f"- host.bash({command!r})" for command in commands)
    return (
        "Pre-authorized test commands for this run. host.bash is approved for "
        "exactly these command strings, so run each test with the string "
        "verbatim and cite that same string as the test_evidence command:\n"
        f"{listed}\n"
        "Any other host.bash command needs an approval this run may not have: "
        "check everything else (an entry-point smoke, an import check) in "
        "Python inside the cell, by importing the module and calling its "
        "functions."
    )


@dataclass
class Agent:
    cfg: Config = field(default_factory=get_config)
    max_turns: int | None = None
    verbose: bool = False
    dispatcher: HostDispatcher | None = None
    use_skills: bool = True
    allow_delegate: bool = True
    frame_id: str | None = None  # this agent's frame in the store
    delegate_depth: int = 0  # 0 = root; children carry depth+1
    # Optional run-control seams used by delegated Agents. Standalone callers
    # leave both unset and retain the exact historical behavior.
    cancellation: object | None = field(default=None, repr=False)
    context_policy: object | None = field(default=None, repr=False)
    # Explicit working directory for this run. A Web-delegated child must run
    # in its parent session's workspace, not in the daemon's launch directory;
    # unset falls back to os.getcwd(), which is the CLI contract.
    workspace: str | Path | None = None
    # Optional OS read-isolation policy supplied by the embedding team Web
    # session. Standalone CLI Agents leave this unset and preserve their
    # historical filesystem-read behavior.
    read_isolation: KernelReadIsolation | None = field(default=None, repr=False)
    # Interpreter/environment selection for this Agent's kernels. Delegated
    # children inherit the parent session's selection through the delegation
    # runner; None preserves the historical contract (sys.executable, no env).
    env: KernelEnvSpec | None = None
    # Explicit task-mode selection (``openai4s run --mode``). None lets the run
    # classify its own task text conservatively; the result decides which
    # per-turn prompt fragment is appended and whether the Host demands
    # verified source/entry-point/test evidence at completion.
    task_mode: str | None = None
    # Exact test commands the operator pre-authorized for an explicit code
    # mode (``openai4s run --allow-test-command``). Each becomes a
    # conversation-scoped exact-command ``bash`` allow rule, so the Host can
    # issue the receipt ``test_evidence`` is verified against in a run that has
    # nobody to approve it. The receipt requirement itself is unchanged.
    allowed_test_commands: Sequence[str] = ()
    # Record this root Agent's cells to `execution_log` even without an
    # explicit code mode. `openai4s run --auto` sets it: its post-run review
    # can only judge the executed code and output the run actually recorded.
    record_cells: bool = False
    # Durable kernel-generation store handle (duck-typed Store). When set (or
    # defaulted from the dispatcher's store), each worker lifetime writes a
    # kernel_generations row under this Agent's frame so artifact environment
    # provenance can resolve a real generation instead of the daemon fallback.
    generations: Any | None = field(default=None, repr=False)
    # Optional runtime observation owned by the embedding Web session.  A
    # delegated Agent shares that session's workspace, so its Cell writes must
    # be captured under the child's frame before the parent's outer sweep.
    cell_execution_hooks: object | None = field(default=None, repr=False)
    delegated_cell_hooks_factory: Callable[[str], object] | None = field(
        default=None, repr=False
    )
    _recorder: object | None = field(default=None, repr=False)
    # persistent R kernel for ```r cells — spawned lazily on first use,
    # retargeted when host.env.use() picks an R-only env, shut down with the run
    _r_kernel: object | None = field(default=None, repr=False)
    _r_kernel_env: str | None = field(default=None, repr=False)
    _foreground_kernel: object | None = field(default=None, init=False, repr=False)
    _foreground_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _delegation_runner: object | None = field(default=None, init=False, repr=False)
    # host.env.use() request recorded by the dispatcher callback, applied at
    # the next Python-cell boundary (never mid-cell — the Web pending model).
    _pending_env: str | None = field(default=None, init=False, repr=False)
    _generation_recorder: KernelGenerationRecorder | None = field(
        default=None, init=False, repr=False
    )
    # True only for a root Agent that opened its own turn frame. Nobody else
    # closes that row: a delegated child's frame belongs to the DelegationRunner
    # and an embedder's frame to the embedder, so neither is ever written here.
    _owns_frame: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_turns is None:
            self.max_turns = self.cfg.max_turns
        is_root = False
        if self.dispatcher is None:
            # Build the dispatcher first so we can share its store with the
            # delegation runner (single backbone per process).
            self.dispatcher = build_dispatcher(self.cfg, frame_id=self.frame_id)
            # A root agent (no frame handed down) opens its OWN turn frame so
            # its delegation subtree nests under it ( topology). Children
            # already receive frame_id from the delegation runner.
            if self.frame_id is None:
                is_root = True
                self.frame_id = self.dispatcher.store.new_frame(
                    kind="turn", model=self.cfg.llm.model, depth=self.delegate_depth
                )
                self.dispatcher.frame_id = self.frame_id
                self._owns_frame = True
            # What `search_capabilities` reports as visible must be what this
            # Agent offers the provider, or a model calls a tool it was never
            # offered and loses the turn to the gate's refusal.
            self.dispatcher.model_tool_offered = self._tool_offered
        # Durable generation registration defaults to the dispatcher's store:
        # the CLI root and every delegated child then record real kernel
        # generations under their own frame with no extra wiring.
        if self.generations is None:
            self.generations = getattr(self.dispatcher, "store", None)
        # A real env switch for Agent-owned sessions: validate against live
        # discovery and record a pending switch applied between cells. Only a
        # fresh dispatcher is wired — an embedder's own sink is preserved.
        if self.dispatcher.on_env_switch is None:
            self.dispatcher.on_env_switch = self._queue_env_switch
        if self.env is not None:
            # Seed the R channel pin so the child's ```r cells respawn against
            # the parent session's R selection (existing retarget mechanism).
            if self.env.r_env and self.dispatcher.active_r_env is None:
                self.dispatcher.active_r_env = self.env.r_env
            # env_list/env_use derive the "current" name from active_env_bin's
            # parent directory, so only seed it when that projection is true
            # (a conda-shaped root named after the env).
            if (
                self.env.env_root
                and self.env.env_name
                and self.dispatcher.active_env_bin is None
                and Path(self.env.env_root).name == self.env.env_name
            ):
                self.dispatcher.active_env_bin = str(Path(self.env.env_root) / "bin")
        # Wire a real delegation runner unless this IS a leaf. The runner owns
        # a ThreadPoolExecutor and is therefore run-scoped; ``run()`` recreates
        # it after teardown when this Agent instance is reused.
        self._ensure_delegation_runner()
        # replay: only the ROOT agent records a tape (children replay as
        # part of the parent's flow, not independently).
        if is_root and self.cfg.record_tape:
            from openai4s.replay import TapeRecorder

            self._recorder = TapeRecorder(self.cfg.tape_path)
            self.dispatcher.recorder = self._recorder
        self.dispatcher.set_capability_scope(self.frame_id)
        # The allowlist-aware view, not the raw corpus. The attribute name
        # stays `_skill_loader` because tests assert on it by name; only the
        # object changes, from every skill on disk to the ones this session
        # may be told about.
        self._skill_loader = (
            (
                getattr(self.dispatcher, "skill_disclosure", None)
                or self.dispatcher.skill_loader
            )
            if self.use_skills
            else None
        )

    def _log(self, *a: object) -> None:
        if self.verbose:
            print(*a, flush=True)

    def _python_read_isolation(self) -> KernelReadIsolation | None:
        """Add only sidecars visible to this Agent to its exact read grants."""

        policy = self.read_isolation
        disclosure = self._skill_loader
        if policy is None or disclosure is None:
            return policy
        loader = getattr(disclosure, "loader", disclosure)
        visible: set[str] | None = None
        if loader is not disclosure:
            rows = disclosure.list()
            visible = {
                str(row.get("name") or "")
                for row in rows
                if isinstance(row, Mapping) and row.get("name")
            }
        roots = []
        for skill in loader.skills().values():
            if not getattr(skill, "has_kernel", False):
                continue
            if visible is not None and str(getattr(skill, "name", "")) not in visible:
                continue
            root = Path(getattr(skill, "root"))
            source = str(getattr(skill, "source", ""))
            expected = None
            if source == "project":
                expected = loader.project_skills_dir()
            elif source == "user":
                expected = loader.user_skills_dir()
            if root.is_symlink() or (
                expected is not None and Path(expected).is_symlink()
            ):
                raise RuntimeError("Skill sidecar scope contains a symlink")
            resolved = root.resolve()
            if (
                expected is not None
                and Path(expected).resolve() not in resolved.parents
            ):
                raise RuntimeError("Skill sidecar root escapes its authorized scope")
            roots.append(resolved)
        return policy.with_allowed_roots(roots)

    def _system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT
        # Splice the safety fragments (report biO + oiO) unless disabled. These
        # are prompt-level guidance; the pre-exec classifier + screeners are the
        # enforcement side.
        sec = self.cfg.security
        extra: list[str] = []
        if sec.code_gate_enabled:
            from openai4s import prompts as _prompts

            extra.append(_prompts.SECURITY_GENERAL)
        if sec.biosecurity:
            from openai4s.security.biosecurity import BIOSECURITY_PROMPT

            extra.append(BIOSECURITY_PROMPT)
        if extra:
            prompt = prompt + "\n\n" + "\n\n".join(extra)
        if self._skill_loader is not None:
            ctx = self._skill_loader.system_context()
            if ctx:
                prompt = prompt + "\n\n" + ctx
        return prompt

    def _pre_exec_gate(self, code: str, messages: list[dict]) -> str | None:
        """Run the pre-exec safety layer on a cell about to execute.

        Returns None to proceed, or an Observation string to feed back to the
        model INSTEAD of executing (the `SAFE?` / biosecurity BLOCK branches of
        the outer loop). Never raises — a failure here fails open.
        """
        sec = self.cfg.security
        # Layer 2: code-safety classifier (report e6w).
        if sec.code_gate_enabled:
            try:
                verdict = classify_code(
                    code, self.cfg, usage_sink=self._record_screening_usage
                )
            except Exception:  # noqa: BLE001 - gate must not crash the turn
                verdict = None
            if verdict is not None and not verdict.safe:
                self._log(f"[safety] refused cell: {verdict.reason}")
                return "[Observation]\n" + verdict.as_observation()
        # Biosecurity trajectory screener (report diO): only BLOCK stops a cell;
        # ESCALATE is advisory in the autonomous loop (the oiO prompt guides the
        # agent to seek context) so we don't deadlock without a human.
        if sec.biosecurity:
            try:
                user_text, actions = gather_trajectory(messages, code)
                screen = screen_trajectory(
                    user_text,
                    actions,
                    self.cfg,
                    usage_sink=self._record_screening_usage,
                )
            except Exception:  # noqa: BLE001
                screen = None
            if screen is not None and screen.blocked:
                self._log(f"[biosecurity] BLOCK: {screen.reason}")
                return (
                    "[Observation]\n[BLOCKED by the biosecurity trajectory "
                    f"screener] {screen.reason}. This cell was NOT executed. "
                    "If this is legitimate research, stop and explain the "
                    "scientific context and safeguards to the user rather "
                    "than proceeding."
                )
            if screen is not None and screen.escalated:
                self._log(f"[biosecurity] ESCALATE (advisory): {screen.reason}")
        return None

    def _admit_cell(self, _action: object) -> None:
        """Fail closed on standard-profile readiness before any local runtime.

        This is deliberately a Cell boundary, not a task boundary: native
        control tools and ``finalize_response`` do not need a Python/R worker
        and must retain the ``LazyKernel`` zero-spawn contract.
        """

        # B-02's Cell sink reaches here too. `CellExecutionService` is the Web
        # path; a CLI run and every delegated child execute their Cells through
        # `LocalActionExecutor`, so a Skill bound `raw_required` used to run
        # unconfined here while the Web parent refused the identical Cell.
        # Before the first worker exists, apply the unconditional half: a
        # declared `raw_required` manifest is refused whatever the sandbox
        # reports.  Once a Python/R worker exists, use its measured posture so
        # a later-loaded `host_only` Skill cannot bypass the Web Cell sink.
        # First-spawn posture is checked in `_admit_spawned_cell_kernel`, before
        # Skill bootstrap or user code executes.
        from openai4s.server.skill_network_admission import raw_required_binding

        language = str(getattr(_action, "language", "python") or "python")
        with self._foreground_lock:
            kernel = self._r_kernel if language == "r" else self._foreground_kernel
        if kernel is not None:
            self._admit_spawned_cell_kernel(kernel)
        else:
            refused = raw_required_binding(self.frame_id)
            if refused is not None:
                raise PermissionError(
                    f"skill {refused.skill_id!r} requires raw kernel network and "
                    "is blocked in this version"
                )

        if not self.cfg.roadmap_features.stage1_trusted_delivery:
            return
        from openai4s.kernel.readiness import (
            EnvironmentReadinessError,
            standard_profile_readiness,
        )

        readiness = standard_profile_readiness(enabled=True)
        if readiness.get("ready") is not True:
            raise EnvironmentReadinessError(readiness)

    def _admit_spawned_cell_kernel(self, kernel: object) -> None:
        """Apply the full Skill-network Cell policy to an actual worker.

        Creating a worker is not executing a Cell.  This seam lets CLI and
        delegated runs inspect the same measured posture as the Web path while
        still preserving lazy zero-spawn turns that use only native tools or
        structured finalization.
        """

        from openai4s.server.skill_network_admission import admit_cell

        try:
            sandbox_status = getattr(kernel, "sandbox_status", None)
        except Exception:  # noqa: BLE001 - unavailable posture must fail closed
            sandbox_status = None
        decision = admit_cell(
            frame_id=self.frame_id,
            sandbox_status=sandbox_status,
        )
        if not decision.allowed:
            raise PermissionError(decision.refusal_message())

    def _install_cell_recorder(self) -> None:
        """Give this Agent durable ``execution_log`` recording for its cells.

        Delegated children get a recorder from the delegation runner; a root
        CLI Agent historically recorded nothing, which made an explicit
        code-mode completion contract unsatisfiable (its ``test_evidence``
        must name a stored cell row). Idempotent: hooks handed in by an
        embedder — or installed by a previous ``run`` — are left alone, and
        the rows are keyed under this Agent's own frame with the same
        ``origin="agent"`` the Web path records.
        """

        if self.cell_execution_hooks is not None or not self.frame_id:
            return
        store = getattr(self.dispatcher, "store", None)
        if store is None:
            return
        recorder = DelegatedCellRecorder(
            store, str(self.frame_id), origin="agent", log=self._log
        )
        recorder.bind_generation_source(self.current_kernel_generation_id)
        self.cell_execution_hooks = recorder

    def _explicit_evidence_mode(self) -> str | None:
        """The explicitly selected mode, when it arms the code-evidence gate."""

        raw = str(self.task_mode or "").strip()
        if not raw:
            return None
        try:
            mode = resolve_task_mode("", explicit=raw).value
        except ValueError:
            # `run` raises the loud error for an unknown name; nothing to arm.
            return None
        return mode if mode in EVIDENCE_REQUIRED_MODES else None

    def authorize_test_commands(self) -> list[str]:
        """Install ``allowed_test_commands`` as exact ``bash`` allow rules.

        Only for an explicit evidence mode, only in this Agent's own
        conversation, and only for the exact command text: the rule pattern is
        glob-escaped, and a command whose permission target would not equal its
        own text (a credential-shaped token the gate redacts, or an overlong
        command it truncates) is refused, because either would turn one
        command into a family. Idempotent. Returns the installed commands.
        """

        commands = [str(command) for command in self.allowed_test_commands]
        if not commands or self._explicit_evidence_mode() is None:
            return []
        for command in commands:
            problem = allowed_test_command_error(command)
            if problem is not None:
                raise ValueError(f"--allow-test-command {command!r}: {problem}")
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return []
        from openai4s.storage.permissions import literal_permission_pattern

        root = str(self.frame_id)
        try:
            frame = store.get_frame(root)
            if isinstance(frame, Mapping) and frame.get("root_frame_id"):
                root = str(frame["root_frame_id"])
        except Exception:  # noqa: BLE001 - the Agent's own frame is the scope
            pass
        for command in commands:
            store.set_permission_rule(
                scope="conversation",
                scope_id=root,
                tool="bash",
                pattern=literal_permission_pattern(command),
                decision="allow",
            )
        return commands

    def code_mode_preflight_refusal(self) -> str | None:
        """Why an explicit code mode cannot complete here, before any model call.

        Its completion needs a Host-authorized ``host.bash`` receipt for each
        cited test command. When no channel, rule, or unattended policy could
        ever authorize ``host.bash`` in this process, every turn of the run is
        spent on a contract it cannot meet; say so instead. ``None`` when the
        run has no such requirement or the runner is authorizable.
        """

        mode = self._explicit_evidence_mode()
        if mode is None:
            return None
        self.authorize_test_commands()
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return None
        from openai4s.permissions import broker

        permission_broker = broker()
        if permission_broker.approval_reachable(
            store=store,
            frame_id=str(self.frame_id),
            method="bash",
            side_effect_class="runtime_mutation",
            guardian_config=self.cfg,
        ):
            return None
        if self.allowed_test_commands:
            blocker = "a standing 'bash' deny rule blocks every command, including the pre-authorized ones"
        elif permission_broker.guardian_adjudicates(
            store=store, frame_id=str(self.frame_id), guardian_config=self.cfg
        ):
            blocker = (
                "no standing 'bash' allow rule applies, and the Auto Mode Guardian "
                "never approves a shell command"
            )
        else:
            blocker = (
                "no interactive approval channel is attached, no standing 'bash' "
                "allow rule applies, and OPENAI4S_UNATTENDED_APPROVAL is not 'allow'"
            )
        return (
            f"--mode {mode} completes only with test_evidence backed by a "
            "Host-authorized host.bash receipt for each test command, and nothing "
            f"in this run can authorize host.bash: {blocker}. Pre-authorize the "
            "exact test command(s) the run will cite with --allow-test-command "
            "CMD (repeatable), or run the task in the Web UI, where a person can "
            "approve the command."
        )

    def run(self, task: str) -> dict:
        """Run one task through the shared engine and local runtime adapters."""
        if not self._owns_frame:
            return self._run_task(task)
        # This Agent opened its turn frame, so it is the only writer that can
        # close it. Reopened first: a reused Agent must not report the last
        # run's terminal while this one is still going.
        self._persist_frame_status("processing")
        try:
            result = self._run_task(task)
        except KeyboardInterrupt:
            # Ctrl-C is the CLI user's Stop; a Web Stop records "cancelled" too.
            self._persist_frame_status("cancelled")
            raise
        except BaseException:
            self._persist_frame_status("failed")
            raise
        self._persist_frame_status(
            _frame_status_for_stop_reason(result.get("stop_reason"))
        )
        return result

    def close_unrun_frame(self, status: str = "failed") -> None:
        """Close the turn frame this Agent opened for a run that never starts.

        ``__post_init__`` opens the root frame and only ``run`` closes it, so a
        caller that refuses after construction (the CLI's code-mode preflight)
        would leave the row ``processing`` forever. A frame this Agent did not
        open -- a delegated child's, an embedder's -- is never written.
        """

        if self._owns_frame:
            self._persist_frame_status(status)

    def _persist_frame_status(self, status: str) -> None:
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return
        try:
            store.update_frame(str(self.frame_id), status=status)
        except Exception:  # noqa: BLE001 - a status write cannot break the run
            pass

    def _record_overhead_usage(self, usage: Any) -> None:
        """Charge daemon-owned overhead (the context summarizer) to this run.

        Both halves, unlike ``_record_frame_usage``: an ordinary reply reaches
        the governance ledger through the Action Ledger, but a summarizer call
        never becomes an action, so without this it reached neither the frame
        nor the quota the next turn is checked against.
        """
        from openai4s.storage.governance import record_session_llm_usage

        self._record_frame_usage(usage if isinstance(usage, Mapping) else {})
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return
        record_session_llm_usage(store, str(self.frame_id), usage)

    def _record_screening_usage(self, usage: Any) -> None:
        """Charge a pre-execution screener's tokens to this run.

        The code classifier and the biosecurity trajectory screen are real
        billed calls the user never asked for, and both reached the provider
        on every gated cell without reaching any ledger. They are recorded
        through the non-enforcing kind: an unmeasured screen must never be
        able to refuse the next turn, because the alternative to screening is
        not "save the tokens", it is "run the cell unscreened".
        """
        from openai4s.storage.governance import record_screening_llm_usage

        self._record_frame_usage(usage if isinstance(usage, Mapping) else {})
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return
        record_screening_llm_usage(store, str(self.frame_id), usage)

    def _session_quota_gate(self) -> None:
        """Refuse a provider request this session may not afford.

        Used by the turn itself and by the context summarizer. Compaction is
        the daemon's largest single burst -- several chunks, each with a retry
        on truncation, all carrying ~48k-token prompts -- and all of it ran
        before the turn's own call without being checked. The turn's own call
        was not checked here either: the Web `ChatModel` has had this gate
        since it was written, this one never got it, and a delegated child runs
        its whole outer loop through exactly this path.

        `enforce_session_llm_quota` resolves the frame to its session root
        first, which is what makes it correct for a child: a delegated frame
        has no owner row of its own, and the quota belongs to the root's owner.

        A CHECK, not a reservation. `ChatModel.complete` runs the provider call
        in a detached daemon thread whose reply can outlive the turn and still
        bill (`abandoned_reply`), so a promise held across it could outlive its
        operation -- the trade this PR has already refused twice. Concurrent
        children can therefore still overshoot a nearly-exhausted window by the
        fan-out; what this closes is starting at all against an exhausted one.
        """
        from openai4s.storage.governance import enforce_session_llm_quota

        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return
        enforce_session_llm_quota(store, str(self.frame_id))

    def _record_frame_usage(self, usage: Mapping[str, Any]) -> None:
        """Add one reply's usage to the owned frame, as the Web loop does."""

        if not self._owns_frame or not self.frame_id:
            return
        store = getattr(self.dispatcher, "store", None)
        if store is None:
            return
        from openai4s.llm.usage import measured_usage

        # "As the Web loop does" is the whole contract, and it was not kept:
        # ``gateway.add_usage`` measures first, this read the public *display*
        # keys. So one non-final streamed reply charged this frame 1200/340
        # while the identical object charged 0/0 on the Web -- and while this
        # same run recorded it as ``llm_*_tokens_unknown`` in the governance
        # ledger. The display dict is what a user should see; only the
        # evidence may be charged.
        counters = measured_usage(usage)
        try:
            store.add_frame_tokens(
                str(self.frame_id),
                input_tokens=int(counters.get("input_tokens", 0) or 0),
                output_tokens=int(counters.get("output_tokens", 0) or 0),
            )
        except Exception:  # noqa: BLE001 - metering cannot break the run
            pass

    def _run_task(self, task: str) -> dict:
        assert self.dispatcher is not None
        assert self.max_turns is not None
        # An Agent can be reused.  A previous submission must never make the
        # next task appear complete before its own scientific cell submits.
        self.dispatcher.last_output = None
        if self._cancelled():
            self._close_run()
            return self._finish([], None, "cancelled")
        self._ensure_delegation_runner()
        # The per-turn seam the Web path already had and this one did not: the
        # mode fragment rides on the USER message, never on the system prompt
        # (which a delegated child and a reused Agent both compose once).
        # Only an EXPLICIT selection (`openai4s run --mode`, or an embedder
        # setting `task_mode`) arms the completion contract; a detected mode
        # guides the prompt and stamps no binding mode, because a classifier
        # over prose has false positives and each one that armed the
        # requirement refused an honest completion. Delegated children run
        # through this same path with task_mode=None, so a child whose request
        # text trips a signal inherits only the guidance, never the gate.
        explicit = bool(self.task_mode is not None and str(self.task_mode).strip())
        mode = resolve_task_mode(task, explicit=self.task_mode)
        set_mode = getattr(self.dispatcher, "set_task_mode", None)
        if callable(set_mode):
            set_mode(mode.value if explicit else None)
        if self.record_cells or (explicit and mode.value in EVIDENCE_REQUIRED_MODES):
            # The armed contract demands test_evidence naming real
            # execution_log rows, and a root CLI Agent historically recorded
            # none — which made the requirement unsatisfiable and the refusal
            # ("this run never executed that cell") actively false. Recording
            # rides the explicit contract, or an explicit `record_cells`
            # request (`--auto`'s reviewer), so every other CLI run keeps its
            # historical no-rows behaviour.
            self._install_cell_recorder()
        fragment = task_mode_prompt(mode, explicit=explicit)
        authorized_tests = self.authorize_test_commands() if explicit else []
        if authorized_tests:
            fragment += "\n\n" + _preauthorized_test_commands_note(authorized_tests)
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": (task + "\n\n" + fragment) if fragment else task,
            },
        ]
        transcript: list[Turn] = []
        run_cwd = str(self.workspace) if self.workspace else os.getcwd()
        self.dispatcher.set_workspace(run_cwd)
        python_read_isolation = self._python_read_isolation()

        def make_python_kernel() -> Kernel:
            # `self.env` is read at spawn time, not captured, so a pending
            # host.env.use() switch retargets both the foreground worker and
            # any later background worker.
            env = self.env
            return Kernel(
                dispatcher=self.dispatcher,
                cwd=run_cwd,
                python=(env.python if env is not None else None),
                env_root=(env.env_root if env is not None else None),
                env_name=(env.env_name if env is not None else None),
                read_isolation=python_read_isolation,
            )

        self.dispatcher.background_kernel_factory = make_python_kernel

        def publish_foreground(kernel: object | None) -> None:
            with self._foreground_lock:
                self._foreground_kernel = kernel

        def bootstrap(kernel: Any) -> None:
            # The worker exists, so its actual sandbox posture is now
            # measurable. Refuse before any executable Skill sidecar bootstrap.
            self._admit_spawned_cell_kernel(kernel)
            if self._skill_loader is None or self._cancelled():
                return
            boot = self._skill_loader.bootstrap_code()
            if boot.strip():
                kernel.execute(boot, origin="system")

        lazy_kernel = LazyKernel(
            make_python_kernel,
            bootstrap=bootstrap,
            publish=publish_foreground,
        )
        self._generation_recorder = (
            KernelGenerationRecorder(self.generations, str(self.frame_id))
            if self.generations is not None and self.frame_id
            else None
        )
        try:
            with lazy_kernel:
                tool_catalog = self.dispatcher.tool_catalog()
                prose_nudge = NO_CODE_NUDGE
                try:
                    capabilities = get_model_capabilities(
                        self.cfg.llm.provider,
                        self.cfg.llm.model,
                        base_url=self.cfg.llm.base_url,
                    )
                    if not capabilities.tool_calling:
                        prose_nudge = NO_NATIVE_COMPLETION_NUDGE
                except Exception:  # noqa: BLE001 - compatible provider fallback
                    pass
                transcript_events = TranscriptEventSink(transcript, log=self._log)
                action_ledger = self._action_ledger(tool_catalog, task)
                event_sink: Any = (
                    _LedgerTranscriptEventSink(action_ledger, transcript_events)
                    if action_ledger is not None
                    else transcript_events
                )
                if self._owns_frame:
                    event_sink = _FrameUsageEventSink(
                        event_sink, self._record_frame_usage
                    )

                def _account_abandoned_reply(reply: Mapping[str, Any]) -> None:
                    """Meter a reply that landed after this run was cancelled.

                    Only the Web loop used to wire this, so a cancelled CLI run
                    or delegated child quarantined its late reply (right) and
                    then never reached ``record_session_llm_usage`` (wrong) --
                    the quota-ledger hole ``record_abandoned_usage`` exists to
                    close, left open on every non-Web path.
                    """

                    usage = reply.get("usage")
                    # Billed like a delivered reply, as the Web loop bills it.
                    if isinstance(usage, Mapping) and usage:
                        self._record_frame_usage(usage)
                    if action_ledger is None:
                        return
                    # Reached even with no counters, exactly as the Web loop's
                    # ``account_abandoned_reply`` does: an unmeasured late reply
                    # is recorded as *unknown* rather than as free, which is
                    # what the team quota gate reads.
                    action_ledger.record_abandoned_usage(usage)

                model: Any = ChatModel(
                    self.cfg.llm,
                    chat,
                    tools=lambda messages: self._model_tool_specs(
                        tool_catalog, messages
                    ),
                    # ChatModel owns cancellation end to end: it refuses to
                    # start a cancelled call, returns the canonical no-op reply
                    # for one that is cancelled mid-flight while the request is
                    # quarantined and metered through ``abandoned_reply``, and
                    # lets the transport abandon a retry backoff it is merely
                    # sleeping through. A second wrapper re-checking the raw
                    # signal after ChatModel returned could only discard a reply
                    # that was already delivered as real -- unmetered, since
                    # ``abandon()`` never ran for it.
                    cancellation=self.cancellation,
                    # The same gate the Web `ChatModel` has had all along, and
                    # the one this path never received -- so a delegated child
                    # ran its entire outer loop against a window nothing asked
                    # about. Inert without an owner row, so a single-user CLI
                    # run is unchanged (INV-1).
                    quota_gate=self._session_quota_gate,
                    abandoned_reply=_account_abandoned_reply,
                    call_scope=self._provider_call_scope(),
                    drain_cancelled_stream=self._session_is_metered(),
                )
                # The providers only this run can build: the live tool
                # catalogue prices the trigger and the kernel cwd receives
                # the kernel-readable copy of an oversized output.
                policy_providers: dict[str, Any] = dict(
                    log=self._log,
                    context_budget_provider=_child_context_budget(self.cfg),
                    tool_schema_provider=lambda state: self._model_tool_specs(
                        tool_catalog, state.messages
                    ),
                    workspace_provider=lambda _s: run_cwd,
                    should_cancel=(
                        (lambda: bool(self.cancellation.cancelled()))
                        if self.cancellation is not None
                        else None
                    ),
                    # Compaction spends real tokens on the session's behalf, so
                    # it answers to the session's quota and lands in its ledger.
                    # Both are inert without a team owner (INV-1), so a
                    # single-user CLI run is unchanged.
                    quota_gate=self._session_quota_gate,
                    usage_sink=self._record_overhead_usage,
                )
                context_policy = self.context_policy or CompactionPolicy(
                    self.cfg, **policy_providers
                )
                bind_runtime = getattr(context_policy, "bind_runtime", None)
                if self.context_policy is not None and callable(bind_runtime):
                    # A delegated child's steering wrapper is built before the
                    # run exists and cannot know the catalogue or cwd itself.
                    bind_runtime(**policy_providers)
                engine = AgentEngine(
                    model,
                    LocalActionExecutor(
                        lazy_kernel,
                        self.dispatcher,
                        self._pre_exec_gate,
                        self._execute_r,
                        admit_cell=self._admit_cell,
                        cell_hooks=self.cell_execution_hooks,
                        log=self._log,
                        tool_catalog=tool_catalog,
                        prose_nudge=prose_nudge,
                        action_ledger=action_ledger,
                        apply_pending_env=(
                            lambda: self._apply_pending_env(lazy_kernel)
                        ),
                        generation_recorder=self._generation_recorder,
                    ),
                    context_policy=context_policy,
                    event_sink=event_sink,
                    cancellation=self.cancellation,
                    completion=CompletionSignal(lambda: self.dispatcher.last_output),
                    max_turns=self.max_turns,
                )
                result = engine.run(messages)
        finally:
            self._close_run()

        final_reply = None
        if result.stop_reason == "submitted":
            final_reply = _completion_summary(result.completion)
            if final_reply is None and result.last_reply is not None:
                final_reply = result.last_reply.content or None
        return self._finish(
            transcript,
            final_reply,
            result.stop_reason,
            completion=result.completion,
            turns=result.turns,
        )

    def _model_tool_specs(
        self, tool_catalog: Any, messages: Sequence[Mapping[str, Any]]
    ) -> tuple[Any, ...]:
        """The tool declarations this run offers the model.

        The session catalog's progressive projection, minus every
        approval-required tool no approval path in this process could allow:
        with no channel attached, no standing allow rule, and an unattended
        posture (or the Guardian) that refuses it, each call is only a wasted
        turn. A Web session attaches a channel for its root, so its delegated
        children keep everything. Hiding is a projection, never the control:
        the catalog, the ledger's resolver, and the permission gate still see
        every tool, so one called anyway is refused and audited as before.
        """

        specs = tool_catalog.specs_for(messages)
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return with_finalize_response(specs)
        reachable: dict[str, bool] = {}
        visible = [
            spec
            for spec in specs
            if self._tool_offered(
                tool_catalog.get(getattr(spec, "name", "")), _reachable=reachable
            )
        ]
        return with_finalize_response(visible)

    def _tool_offered(
        self, tool: Any, *, _reachable: dict[str, bool] | None = None
    ) -> bool:
        """Whether this run's projection offers ``tool`` to the model.

        The one predicate behind both the provider ``tools=`` list and what
        ``search_capabilities`` calls visible. A tool that needs no approval
        is always offered; an approval-required one only when some approval
        path in this process could allow its method. ``_reachable`` memoizes
        per method within one projection.
        """

        if tool is None or not getattr(tool, "requires_approval", False):
            return True
        store = getattr(self.dispatcher, "store", None)
        if store is None or not self.frame_id:
            return True
        method = str(tool.host_method)
        if _reachable is not None and method in _reachable:
            return _reachable[method]
        from openai4s.permissions import broker

        reachable = broker().approval_reachable(
            store=store,
            frame_id=str(self.frame_id),
            method=method,
            dangerous=bool(getattr(tool, "dangerous", False)),
            side_effect_class=str(getattr(tool, "side_effect_class", "")),
            guardian_config=self.cfg,
        )
        if _reachable is not None:
            _reachable[method] = reachable
        return reachable

    def _session_is_metered(self) -> bool:
        """Whether the team ledger charges this run's session root.

        A delegated child of a team-owned Web session meters through
        ``_account_abandoned_reply`` like its parent, so its abandoned streams
        must be drained for their usage too. No Store, no root, or no owner
        reads as unmetered.
        """

        frame_id = str(self.frame_id or "")
        if not frame_id:
            return False
        store = getattr(self.dispatcher, "store", None)
        team = getattr(store, "team", None)
        resolve = getattr(store, "resolve_frame_scope", None)
        if team is None or not callable(resolve):
            return False
        try:
            scope = resolve(frame_id)
            root = scope.get("root_frame_id") if isinstance(scope, Mapping) else None
            return team.session_owner(str(root or frame_id)) is not None
        except Exception:  # noqa: BLE001 - metering must never fail the turn
            return False

    def _provider_call_scope(self) -> str | None:
        """The session a turn's detached provider calls are bounded against.

        The budget exists because the quota gate reads a ledger that has not
        been charged for calls still in flight, and that ledger charges the
        session root (``record_session_llm_usage`` resolves the same way). So
        for the session's own turn the bound is keyed on the root, not on
        whatever branch frame the turn happens to run in.

        Delegated children are deliberately *not* keyed on the root (nor on
        their own frame): see the comment below. Their detached calls are
        bounded by the process ceiling alone.
        """

        if self.delegate_depth > 0:
            # A delegated child's detached calls count against the process
            # ceiling only. Keyed on the session root they shared the root's
            # four accounting slots, so one Stop on a fan-out -- or a parent
            # cell's ordinary ``host.stop_child`` on a few in-flight siblings,
            # with no Stop pressed at all -- filled them, and the parent's very
            # next model call was refused as an internal error until the
            # abandoned sockets closed. The per-session bound exists to keep a
            # user's Stop-and-resend from stacking billed requests against a
            # ledger not yet charged for them; children are stopped by code,
            # and a fan-out of 48 cannot fit a four-slot bound.
            return None
        frame_id = str(self.frame_id or "")
        if not frame_id:
            return None
        store = getattr(self.dispatcher, "store", None)
        resolve = getattr(store, "resolve_frame_scope", None)
        if not callable(resolve):
            return frame_id
        try:
            scope = resolve(frame_id)
        except Exception:  # noqa: BLE001 - a bound must never fail the turn
            return frame_id
        root = scope.get("root_frame_id") if isinstance(scope, Mapping) else None
        return str(root or frame_id)

    def _action_ledger(
        self, tool_catalog: Any, task: str
    ) -> RuntimeActionLedger | None:
        """Bind local/child runs to their authoritative session tool view."""

        assert self.dispatcher is not None
        store = getattr(self.dispatcher, "store", None)
        root_frame_id = self.frame_id or getattr(self.dispatcher, "frame_id", None)
        if store is None or not str(root_frame_id or "").strip():
            return None
        ledger = RuntimeActionLedger(
            store,
            str(root_frame_id),
            new_turn_id(),
            provider=getattr(self.cfg.llm, "provider", None),
            model=getattr(self.cfg.llm, "model", None),
            tool_resolver=tool_catalog.get,
            tool_policy_resolver=getattr(self.dispatcher, "control_tool_policy", None),
        )
        bind_evidence_scope = getattr(self.dispatcher, "set_task_evidence_scope", None)
        if callable(bind_evidence_scope):
            bind_evidence_scope(
                turn_id=ledger.turn_id,
                branch_id=ledger.branch_id or str(root_frame_id),
            )
        ledger.append_user({"role": "user", "content": task})
        return ledger

    def _queue_env_switch(self, name: str) -> None:
        """``host.env.use()`` callback for Agent-owned (CLI/delegated) runs.

        Validates the request against live discovery and records it to apply
        at the next cell boundary. Raising here surfaces as the tool's
        "env switch refused" error inside the calling cell.
        """
        from openai4s.kernel.environments import discover_environments, get_environment

        environment = get_environment(name)
        if environment is None:
            available = ", ".join(env.name for env in discover_environments())
            raise RuntimeError(f"unknown environment {name!r}; available: {available}")
        if environment.interpreter is None:
            # R-only env: the tool already retargeted active_r_env; the python
            # kernel is untouched (same as the Web pending-env application),
            # but the immutable selection carried to future descendants must
            # still reflect the newly selected R channel.
            current = self.env
            self.env = KernelEnvSpec(
                python=(current.python if current is not None else None),
                env_root=(current.env_root if current is not None else None),
                env_name=(current.env_name if current is not None else None),
                r_env=environment.name,
            )
            runner = self._delegation_runner
            if runner is not None:
                runner.env = self.env
            return
        self._pending_env = name

    def _apply_pending_env(self, lazy_kernel: Any) -> None:
        """Apply a recorded env switch between cells, never mid-cell.

        The worker is replaced build-lazily: the current kernel is shut down
        and the next cell spawns against the new interpreter through the same
        factory. The namespace reset is inherent and matches the Web session
        model. A new durable generation row is written on the respawn.
        """
        name = self._pending_env
        self._pending_env = None
        if not name:
            return
        from openai4s.kernel.environments import get_environment

        environment = get_environment(name)
        if environment is None or environment.interpreter is None:
            # Discovery changed between the request and this boundary; keep
            # the current kernel rather than guessing.
            return
        current = self.env
        current_name = (
            current.env_name if current is not None and current.env_name else "base"
        )
        if environment.name == current_name:
            return
        self.env = KernelEnvSpec(
            python=environment.interpreter,
            env_root=(str(environment.root) if environment.is_conda else None),
            env_name=environment.name,
            r_env=(current.r_env if current is not None else None),
        )
        runner = self._delegation_runner
        if runner is not None:
            # Future (grand)children follow the switched selection.
            runner.env = self.env
        assert self.dispatcher is not None
        self.dispatcher.active_env_bin = environment.bin_dir
        if self._generation_recorder is not None:
            self._generation_recorder.close(language="python", reason="env_switch")
        self._log(f"[env] switching python kernel to '{environment.name}'")
        lazy_kernel.shutdown()

    def _execute_r(self, code: str, *, cell_id: str | None = None) -> dict:
        """Run one ```r cell on the persistent R kernel, spawning it lazily.

        The kernel is respawned when host.env.use() retargeted the R channel
        (dispatcher.active_r_env changed) or the worker died. A missing R is a
        soft error observation — the model can fall back to python — never a
        crash of the run.
        """
        want_env = getattr(self.dispatcher, "active_r_env", None)
        k = self._r_kernel
        if k is not None and (not k.is_alive() or self._r_kernel_env != want_env):
            self._shutdown_r_kernel()
            k = None
        if k is None:
            from openai4s.kernel.environments import get_environment
            from openai4s.kernel.r_kernel import spawn_r_kernel

            try:
                k = spawn_r_kernel(
                    cwd=(str(self.workspace) if self.workspace is not None else None),
                    env=get_environment(want_env),
                    read_isolation=self.read_isolation,
                )
            except Exception as e:  # noqa: BLE001 — soft-fail into the observation
                return {"error": f"R kernel unavailable: {e}"}
            with self._foreground_lock:
                self._r_kernel = k
                self._r_kernel_env = want_env
        try:
            # R has no sidecar bootstrap, but admission still precedes the first
            # user expression and uses this exact worker's measured posture.
            self._admit_spawned_cell_kernel(k)
        except BaseException:
            self._shutdown_r_kernel()
            raise
        if self._generation_recorder is not None:
            self._generation_recorder.observe(k, language="r")
        if self.cancellation is not None:
            try:
                if self.cancellation.cancelled():
                    return {"error": "Interrupted", "interrupted": True}
            except Exception:  # noqa: BLE001 - cancellation probe is best effort
                pass
        try:
            execute = k.execute
            kwargs: dict[str, Any] = {"origin": "agent"}
            if cell_id is not None:
                try:
                    parameters = inspect.signature(execute).parameters.values()
                except (TypeError, ValueError):
                    parameters = ()
                if any(
                    parameter.name == "cell_id"
                    or parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters
                ):
                    kwargs["cell_id"] = cell_id
            return execute(code, **kwargs)
        except Exception as e:  # noqa: BLE001 — dead worker: drop it, soft-fail
            self._shutdown_r_kernel()
            return {"error": f"R kernel failed: {e}"}

    def _shutdown_r_kernel(self) -> None:
        with self._foreground_lock:
            k = self._r_kernel
            self._r_kernel = None
            self._r_kernel_env = None
        if k is not None:
            try:
                k.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def current_kernel_generation_id(self, language: str = "python") -> str | None:
        """Durable generation id of this Agent's live worker, if registered.

        The delegated-cell recorder reads this at each Cell boundary — the
        same seam that registers the row — so a recorded child cell names the
        exact worker generation that ran it.
        """
        recorder = self._generation_recorder
        if recorder is None:
            return None
        return recorder.current(language)

    def interrupt_foreground(self) -> bool:
        """Interrupt only this Agent's current Python/R worker(s).

        This is the narrow exact-owner seam used by ``stop_child``.  It never
        reaches a process-global kernel registry, and it snapshots references
        under a lock before making the potentially blocking signal calls.
        """

        with self._foreground_lock:
            workers = [self._foreground_kernel, self._r_kernel]
        delivered = False
        seen: set[int] = set()
        for worker in workers:
            if worker is None or id(worker) in seen:
                continue
            seen.add(id(worker))
            try:
                delivery = worker.interrupt()
            except Exception:  # noqa: BLE001 - interruption is best effort
                continue
            # `Kernel.interrupt` reports whether a signal actually reached the
            # worker; `None` is a kernel double making no claim and keeps the
            # old answer. Counting a stop the sandbox says it dropped would
            # make `stop_child` report a cancellation it did not perform.
            if delivery is None or delivery:
                delivered = True
        return delivered

    def _cancelled(self) -> bool:
        if self.cancellation is None:
            return False
        try:
            return bool(self.cancellation.cancelled())
        except Exception:  # noqa: BLE001 - cancellation probe is best effort
            return False

    def _finish(
        self,
        transcript: list[Turn],
        final_reply: str | None,
        reason: str,
        *,
        completion: Any = None,
        turns: int = 0,
    ) -> dict:
        assert self.dispatcher is not None
        return {
            "stop_reason": reason,
            "final_message": final_reply,
            "submitted_output": (
                completion if completion is not None else self.dispatcher.last_output
            ),
            "turns": turns,
            "transcript": [{"role": t.role, "content": t.content} for t in transcript],
        }

    def _close_run(self) -> None:
        """Release run-scoped runtimes and persist the optional replay tape."""
        self._shutdown_r_kernel()
        recorder = self._generation_recorder
        self._generation_recorder = None
        if recorder is not None:
            try:
                recorder.close(reason="run_finished")
            except Exception:  # noqa: BLE001 - provenance cannot break teardown
                pass
        runner = self._delegation_runner
        if runner is not None:
            cancelled = self._cancelled()
            if cancelled:
                try:
                    runner.cancel_all("parent agent cancelled")
                except Exception:  # noqa: BLE001 - cancellation cleanup is best effort
                    pass
            # Always shut down the delegation ThreadPoolExecutor.  A per-run
            # runner is created for every (sub-)agent, so leaving its non-daemon
            # worker threads open leaks threads for the daemon's whole lifetime
            # and eventually exhausts "can't start new thread".
            try:
                runner.close(cancel=cancelled)
            except Exception:  # noqa: BLE001 - pool teardown is best effort
                pass
            finally:
                # A closed ThreadPoolExecutor is not reusable. Clear both the
                # owned runner and the dispatcher's bound-method views so the
                # next ``run()`` installs a fresh, consistently wired runner.
                if self._delegation_runner is runner:
                    self._delegation_runner = None
                if (
                    self.dispatcher is not None
                    and self.dispatcher._delegate_fn is runner
                ):
                    self.dispatcher._delegate_fn = None
                    self.dispatcher.steer_fns = {}
        if self._recorder is not None:
            try:
                self._recorder.flush()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    def _ensure_delegation_runner(self) -> None:
        """Install this run's delegation facade and executor when needed."""

        if not self.allow_delegate or self._delegation_runner is not None:
            return
        from openai4s.agent.delegation import MAX_DEPTH, DelegationRunner

        # Defense in depth: depth-MAX_DEPTH Agents are leaves even when an
        # embedder accidentally passes allow_delegate=True.
        if self.delegate_depth >= MAX_DEPTH:
            self.allow_delegate = False
            return
        assert self.dispatcher is not None
        runner = DelegationRunner(
            self.cfg,
            depth=self.delegate_depth,
            parent_frame_id=self.frame_id,
            store=self.dispatcher.store,
            workspace=self.workspace,
            read_isolation=self.read_isolation,
            cell_hooks_factory=self.delegated_cell_hooks_factory,
            env=self.env,
            cancelled=self._cancelled,
        )
        self._delegation_runner = runner
        self.dispatcher._delegate_fn = runner
        self.dispatcher.steer_fns = {
            "children": runner.children,
            "collect": runner.collect,
            "stop_child": runner.stop_child,
            "send_message": runner.send_message,
            "delegation_stats": runner.delegation_stats,
        }


def _extract_code(text: str) -> str | None:
    """Return the first complete top-level Python cell in a model reply.

    The shared fence scanner preserves labelled fenced examples nested inside
    the cell (notably a literal ```tool block in a triple-quoted README). An
    incomplete outer fence is never executable.
    """
    for block in scan_fenced_blocks(text):
        if (
            block.closed
            and block.fence_char == "`"
            and block.info in ("", "python", "py")
        ):
            return block.body
    return None


def run_task(task: str, *, verbose: bool = False, cfg: Config | None = None) -> dict:
    return Agent(cfg=cfg or get_config(), verbose=verbose).run(task)


#: Environment this process sets for `openai4s run --auto`. Named rather than
#: inlined so the CLI can report exactly what the flag turned on: a run that
#: silently widens its own authority is the thing Auto Mode is supposed to
#: prevent, so the flag says so in its output.
AUTO_RUN_ENVIRONMENT = {
    "OPENAI4S_AUTO_MODE": "autonomous",
    "OPENAI4S_STAGE3_SCIENTIFIC_REVIEW_SHADOW": "1",
    "OPENAI4S_STAGE7_GUARDIAN_ENFORCEMENT": "1",
}


def enable_auto_run_environment(
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Turn on autonomous Auto Mode for THIS process, and report what changed.

    Deliberately not a blanket grant. `approvals_reviewer=auto_review` hands
    boundary actions to the Guardian, whose active surface is a read-only
    allowlist bound to a verified action digest -- so an unattended run can read
    and list, and still cannot write, shell out, or reach the network without a
    standing policy established before the run.
    """

    target = os.environ if environ is None else environ
    applied: dict[str, str] = {}
    for key, value in AUTO_RUN_ENVIRONMENT.items():
        # An operator who already set one of these keeps their value: --auto
        # asks for autonomous, it does not overrule an explicit choice.
        if not str(target.get(key, "")).strip():
            target[key] = value
            applied[key] = value
    return applied


def _cli_run_evidence(
    store: Any, root_frame_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The run's native tool ledger, and the evidence gaps its record shows.

    A CLI dispatcher projects no activity steps, so the tool calls live only
    in the canonical action ledger: each ``result`` event is projected into
    the reviewer's ``kind/title/status/summary`` shape. Every started Cell
    allocates an execution attempt whether or not a recorder is armed, so an
    attempt with no ``execution_log`` row is a Cell that ran and cannot be
    shown -- declared, rather than letting a packet without it read complete.
    """

    ledger: list[dict[str, Any]] = []
    for group in store.list_action_groups(root_frame_id, include_events=True):
        if group.get("kind") != "native_tools":
            continue
        events = list(group.get("events") or [])
        names = {
            event.get("action_id"): str(
                (event.get("canonical_arguments") or {}).get("name") or ""
            )
            for event in events
            if event.get("type") == "proposed"
            and isinstance(event.get("canonical_arguments"), Mapping)
        }
        for event in events:
            message = event.get("result")
            if event.get("type") != "result" or not isinstance(message, Mapping):
                continue
            ledger.append(
                {
                    "kind": "tool",
                    "title": names.get(event.get("action_id"))
                    or str(message.get("name") or ""),
                    "status": "error" if message.get("is_error") else "done",
                    "summary": str(message.get("content") or "")[:2_000],
                }
            )
    recorded = {
        str(cell.get("producing_cell_id") or "")
        for cell in store.list_cells(root_frame_id)
    }
    unrecorded = sorted(
        {
            str(attempt.get("producing_cell_id") or "")
            for attempt in store.list_execution_attempts(root_frame_id=root_frame_id)
            if attempt.get("terminal_state") != "prepare_failed"
        }
        - recorded
        - {""}
    )
    gaps: list[dict[str, Any]] = []
    if unrecorded:
        gaps.append({"kind": "cells_unrecorded", "count": len(unrecorded)})
    return ledger, gaps


def review_cli_result(
    task: str,
    result: Mapping[str, Any],
    *,
    cfg: Config,
    store: Any = None,
    root_frame_id: str | None = None,
    chat_call: Any = None,
) -> dict[str, Any]:
    """Post-run Scientific Reviewer adapter for the CLI.

    The Web path reviews through `CompletionGateService`, which needs durable
    branch and turn rows the one-shot CLI never creates. This reviews the
    evidence the run actually recorded under its root frame -- the cells it
    executed (code, output, errors), its native tool ledger, its artifacts and
    lineage -- through the same collector the Web path uses, and returns the
    same terminal vocabulary, so `--auto` reports a real verdict rather than a
    placeholder. Without a Store and frame, or when the record cannot be read
    or is missing executed cells, the snapshot says so as an omission: a review
    of an answer with no evidence behind it is never reported as verified.
    Scope: the root run's own cells. A delegated child records its cells under
    its own frame, and those are not collected (nor declared missing), the same
    as on the Web path; the reviewer sees a child's work only through the
    parent cell that received its result.
    """

    from openai4s.server.completion_gate import terminal_for_review
    from openai4s.server.evidence_snapshot import (
        collect_turn_evidence,
        freeze_evidence_snapshot,
    )
    from openai4s.server.scientific_review import ScientificReviewService

    answer = str(result.get("final_message") or "")
    structured = result.get("submitted_output")
    # A one-shot run has no durable turn row, but it does have an identity, and
    # leaving the block empty is not the same as saying so: the reviewer read
    # four blank ids as missing provenance and raised a finding about the
    # harness rather than the answer. `cli:<uuid>` is true and self-describing.
    run_id = f"cli:{uuid.uuid4().hex[:16]}"
    root = str(root_frame_id or "").strip()
    snapshot: dict[str, Any] | None = None
    unavailable = "the run's durable execution record was not supplied"
    if store is not None and root:
        try:
            tool_ledger, gaps = _cli_run_evidence(store, root)
            snapshot = collect_turn_evidence(
                store,
                root_frame_id=root,
                branch_id=root,
                turn_id=run_id,
                execution_id=run_id,
                user_request=task,
                candidate_answer=answer,
                structured_completion=structured,
                tool_ledger=tool_ledger,
                environment_defaults={"runtime": "cli"},
                collection_omissions=gaps,
            )
        except Exception as error:  # noqa: BLE001 - unreadable evidence is a gap
            snapshot = None
            unavailable = (
                f"the execution record could not be read ({type(error).__name__})"
            )
    if snapshot is None:
        snapshot = freeze_evidence_snapshot(
            {
                "identity": {
                    "root_frame_id": root or run_id,
                    "branch_id": root or run_id,
                    "turn_id": run_id,
                    "execution_id": run_id,
                },
                "user_request": task,
                "candidate_answer": answer,
                "structured_completion": structured,
                "environment": {"runtime": "cli"},
                "collection_omissions": [
                    {"kind": "execution_evidence_unavailable", "reason": unavailable}
                ],
            }
        )
    service = ScientificReviewService(store=None, config=cfg, chat_call=chat_call)
    try:
        review = service.evaluate(
            snapshot,
            result_review_mode="review_only",
            agent_cfg=cfg.llm,
            reviewer_cfg=cfg.llm,
            # One model is all a CLI run has. `review_only` is honest about
            # that; `auto_fix` would refuse for want of an independent
            # reviewer, which is the correct refusal but a useless default here.
            allow_same_model=True,
        )
    except Exception as error:  # noqa: BLE001 — a failed review is not a pass
        return {
            "terminal": "review_unavailable",
            "user_truth": f"Unavailable · not verified ({type(error).__name__})",
            "verdict": None,
            "findings": [],
            "unverified": True,
        }
    terminal, user_truth = terminal_for_review(review)
    return {
        "terminal": terminal,
        "user_truth": user_truth,
        "verdict": review.get("verdict"),
        "findings": [
            {
                "severity": item.get("severity"),
                "category": item.get("category"),
                "claim_ref": item.get("claim_ref"),
            }
            for item in (review.get("findings") or [])
        ],
        "unverified": terminal != "verified",
    }
