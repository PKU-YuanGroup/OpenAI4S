"""Backward-compatible local Agent facade for the hybrid outer loop.

The provider-neutral state machine lives in :mod:`openai4s.agent.engine`.
This module owns local process lifecycle and connects two non-competing action
channels: native JSON tools for orchestration and persistent Python/R cells for
scientific execution.  Only ``host.submit_output(...)`` completes a task.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from openai4s.agent.engine import AgentEngine
from openai4s.agent.finalize import with_finalize_response
from openai4s.agent.runtime import (
    ChatModel,
    CompactionPolicy,
    CompletionSignal,
    LocalActionExecutor,
    TranscriptEventSink,
    TranscriptTurn,
    format_observation,
)
from openai4s.config import Config, get_config
from openai4s.host_dispatch import HostDispatcher, build_dispatcher
from openai4s.kernel import Kernel
from openai4s.llm import chat
from openai4s.security import classify_code, screen_trajectory
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
    host.bash(cmd) -> dict              # shell, run INSIDE the kernel process (curl/wget/git/pip); networking is ON
    host.read_file/write_file/edit_file/glob/grep/list_dir   # workspace files
    host.remote_gpu_status() -> dict    # configured SSH GPU hosts + capabilities
    host.register_remote_capability(alias, capability, ...)  # verified remote service
    host.todo_write(todos)              # optional progress tracker card (long tasks only — never your first move)
    host.env.list/use/create, host.load_skill(name)          # prebuilt envs + recipes
- `host` is already injected into every python kernel. NEVER `import host` or \
`from host import ...`; use the injected singleton directly.
- For ANY task touching external facts, datasets, accession numbers, sequences, or \
literature, you MUST use the native web tools (or host.web_search/web_fetch from a \
cell) BEFORE analysis, and cite what you find — never answer from memory or jump \
straight to synthetic data when a real lookup is possible.
- Do NOT import or call anything OS-destructive unless the task needs it.

Finishing:
- A conversational or tool-only task finishes with `finalize_response` as the \
ONLY native call in its turn. Use its structured fields to report only work \
that actually completed.
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
- If a cell errors, read the traceback in the Observation and fix it in the \
next cell.
"""


Turn = TranscriptTurn
_format_observation = format_observation


class _CancellationAwareModel:
    """Prevent a cancelled local Agent from executing a late model reply.

    ``urllib`` cannot reliably abort a response already in flight.  Checking on
    both sides of the blocking call still guarantees that cancellation starts
    no *new* request and that a late reply cannot dispatch tools, code, or a
    structured completion.  The engine observes cancellation immediately after
    the resulting no-op outcome and exits with ``stop_reason=cancelled``.
    """

    def __init__(self, delegate: Any, cancelled: Callable[[], bool]) -> None:
        self._delegate = delegate
        self._cancelled = cancelled

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        on_delta: Callable[[str], None],
    ) -> Mapping[str, Any]:
        if self._is_cancelled():
            return _cancelled_model_reply()
        reply = self._delegate.complete(messages, on_delta)
        return _cancelled_model_reply() if self._is_cancelled() else reply

    def _is_cancelled(self) -> bool:
        try:
            return bool(self._cancelled())
        except Exception:  # noqa: BLE001 - cancellation telemetry cannot crash a run
            return False


def _cancelled_model_reply() -> dict[str, Any]:
    return {
        "content": "",
        "tool_calls": [],
        "assistant_message": {"role": "assistant", "content": ""},
        "finish_reason": "cancelled",
    }


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
        # Wire a real delegation runner unless this IS a leaf. It carries
        # our depth/frame so children nest correctly and steering
        # is scoped to our direct children.
        if self.allow_delegate:
            from openai4s.agent.delegation import MAX_DEPTH, DelegationRunner

            # Defense in depth: depth-MAX_DEPTH Agents are leaves even when an
            # embedder accidentally passes allow_delegate=True.
            if self.delegate_depth < MAX_DEPTH:
                runner = DelegationRunner(
                    self.cfg,
                    depth=self.delegate_depth,
                    parent_frame_id=self.frame_id,
                    store=self.dispatcher.store,
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
            else:
                self.allow_delegate = False
        # replay: only the ROOT agent records a tape (children replay as
        # part of the parent's flow, not independently).
        if is_root and self.cfg.record_tape:
            from openai4s.replay import TapeRecorder

            self._recorder = TapeRecorder(self.cfg.tape_path)
            self.dispatcher.recorder = self._recorder
        self.dispatcher.set_capability_scope(self.frame_id)
        self._skill_loader = (
            self.dispatcher.skill_loader if self.use_skills else None
        )

    def _log(self, *a: object) -> None:
        if self.verbose:
            print(*a, flush=True)

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
                verdict = classify_code(code, self.cfg)
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
                user_text, actions = _gather_trajectory(messages, code)
                screen = screen_trajectory(user_text, actions, self.cfg)
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

    def run(self, task: str) -> dict:
        """Run one task through the shared engine and local runtime adapters."""
        assert self.dispatcher is not None
        assert self.max_turns is not None
        # An Agent can be reused.  A previous submission must never make the
        # next task appear complete before its own scientific cell submits.
        self.dispatcher.last_output = None
        if self._cancelled():
            self._close_run()
            return self._finish([], None, "cancelled")
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": task},
        ]
        transcript: list[Turn] = []
        run_cwd = os.getcwd()
        self.dispatcher.set_workspace(run_cwd)
        self.dispatcher.background_kernel_factory = lambda: Kernel(
            dispatcher=self.dispatcher,
            cwd=run_cwd,
        )
        try:
            with Kernel(dispatcher=self.dispatcher, cwd=run_cwd) as kernel:
                with self._foreground_lock:
                    self._foreground_kernel = kernel
                try:
                    if self._skill_loader is not None and not self._cancelled():
                        boot = self._skill_loader.bootstrap_code()
                        if boot.strip():
                            kernel.execute(boot, origin="agent")
                    tool_catalog = self.dispatcher.tool_catalog()
                    model: Any = ChatModel(
                        self.cfg.llm,
                        chat,
                        tools=lambda messages: with_finalize_response(
                            tool_catalog.specs_for(messages)
                        ),
                    )
                    if self.cancellation is not None:
                        model = _CancellationAwareModel(
                            model,
                            lambda: bool(self.cancellation.cancelled()),
                        )
                    engine = AgentEngine(
                        model,
                        LocalActionExecutor(
                            kernel,
                            self.dispatcher,
                            self._pre_exec_gate,
                            self._execute_r,
                            log=self._log,
                            tool_catalog=tool_catalog,
                        ),
                        context_policy=(
                            self.context_policy
                            or CompactionPolicy(self.cfg, log=self._log)
                        ),
                        event_sink=TranscriptEventSink(transcript, log=self._log),
                        cancellation=self.cancellation,
                        completion=CompletionSignal(
                            lambda: self.dispatcher.last_output
                        ),
                        max_turns=self.max_turns,
                    )
                    result = engine.run(messages)
                finally:
                    with self._foreground_lock:
                        if self._foreground_kernel is kernel:
                            self._foreground_kernel = None
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
        )

    def _execute_r(self, code: str) -> dict:
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
                k = spawn_r_kernel(env=get_environment(want_env))
            except Exception as e:  # noqa: BLE001 — soft-fail into the observation
                return {"error": f"R kernel unavailable: {e}"}
            with self._foreground_lock:
                self._r_kernel = k
                self._r_kernel_env = want_env
        if self.cancellation is not None:
            try:
                if self.cancellation.cancelled():
                    return {"error": "Interrupted", "interrupted": True}
            except Exception:  # noqa: BLE001 - cancellation probe is best effort
                pass
        try:
            return k.execute(code, origin="agent")
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
                worker.interrupt()
                delivered = True
            except Exception:  # noqa: BLE001 - interruption is best effort
                continue
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
    ) -> dict:
        assert self.dispatcher is not None
        return {
            "stop_reason": reason,
            "final_message": final_reply,
            "submitted_output": (
                completion if completion is not None else self.dispatcher.last_output
            ),
            "transcript": [{"role": t.role, "content": t.content} for t in transcript],
        }

    def _close_run(self) -> None:
        """Release run-scoped runtimes and persist the optional replay tape."""
        self._shutdown_r_kernel()
        if self._delegation_runner is not None and self._cancelled():
            try:
                self._delegation_runner.cancel_all("parent agent cancelled")
            except Exception:  # noqa: BLE001 - cancellation cleanup is best effort
                pass
        if self._recorder is not None:
            try:
                self._recorder.flush()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass


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


def _gather_trajectory(messages: list[dict], current_code: str) -> tuple[str, str]:
    """Split the running conversation into (user_text, agent_actions) for the
    biosecurity screener: all user turns vs. all assistant turns + this cell."""
    user_parts: list[str] = []
    action_parts: list[str] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if not isinstance(content, str):
            continue
        if role == "user":
            user_parts.append(content)
        elif role == "assistant":
            action_parts.append(content)
    action_parts.append(current_code)
    return ("\n\n".join(user_parts[-6:]), "\n\n".join(action_parts[-8:]))


def run_task(task: str, *, verbose: bool = False, cfg: Config | None = None) -> dict:
    return Agent(cfg=cfg or get_config(), verbose=verbose).run(task)
