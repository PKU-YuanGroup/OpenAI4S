"""Backward-compatible local Agent facade for the hybrid outer loop.

The provider-neutral state machine lives in :mod:`openai4s.agent.engine`.
This module owns local process lifecycle and connects two non-competing action
channels: native JSON tools for orchestration and persistent Python/R cells for
scientific execution.  Only ``host.submit_output(...)`` completes a task.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from openai4s.agent.engine import AgentEngine
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
from openai4s.skills_loader import SkillLoader
from openai4s.tools import control_tool_specs, parse_tool_calls, scan_fenced_blocks

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
- For ANY task touching external facts, datasets, accession numbers, sequences, or \
literature, you MUST use the native web tools (or host.web_search/web_fetch from a \
cell) BEFORE analysis, and cite what you find — never answer from memory or jump \
straight to synthetic data when a real lookup is possible.
- Do NOT import or call anything OS-destructive unless the task needs it.

Finishing:
- When (and only when) the task is fully done, run a code cell that calls \
`host.submit_output({...}, ["what you did",...])`. THAT call ends the task — \
there is no other completion signal. After it succeeds you may add a one-line \
prose summary, but do not emit further code blocks.

Rules:
- Each working turn is EITHER native JSON tool calls OR a single code cell \
(```python or ```r). Keep cells small and incremental. Think in prose before \
the action.
- If a cell errors, read the traceback in the Observation and fix it in the \
next cell.
"""


Turn = TranscriptTurn
_format_observation = format_observation


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
    _recorder: object | None = field(default=None, repr=False)
    # persistent R kernel for ```r cells — spawned lazily on first use,
    # retargeted when host.env.use() picks an R-only env, shut down with the run
    _r_kernel: object | None = field(default=None, repr=False)
    _r_kernel_env: str | None = field(default=None, repr=False)

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
            from openai4s.agent.delegation import DelegationRunner

            runner = DelegationRunner(
                self.cfg,
                depth=self.delegate_depth,
                parent_frame_id=self.frame_id,
                store=self.dispatcher.store,
            )
            self.dispatcher._delegate_fn = runner
            self.dispatcher.steer_fns = {
                "children": runner.children,
                "collect": runner.collect,
                "stop_child": runner.stop_child,
                "send_message": runner.send_message,
                "delegation_stats": runner.delegation_stats,
            }
        # replay: only the ROOT agent records a tape (children replay as
        # part of the parent's flow, not independently).
        if is_root and self.cfg.record_tape:
            from openai4s.replay import TapeRecorder

            self._recorder = TapeRecorder(self.cfg.tape_path)
            self.dispatcher.recorder = self._recorder
        self._skill_loader = SkillLoader(cfg=self.cfg) if self.use_skills else None

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
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": task},
        ]
        transcript: list[Turn] = []
        run_cwd = os.getcwd()
        self.dispatcher.background_kernel_factory = lambda: Kernel(
            dispatcher=self.dispatcher,
            cwd=run_cwd,
        )
        try:
            with Kernel(dispatcher=self.dispatcher, cwd=run_cwd) as kernel:
                if self._skill_loader is not None:
                    boot = self._skill_loader.bootstrap_code()
                    if boot.strip():
                        kernel.execute(boot, origin="agent")
                engine = AgentEngine(
                    ChatModel(
                        self.cfg.llm,
                        chat,
                        tools=control_tool_specs(),
                    ),
                    LocalActionExecutor(
                        kernel,
                        self.dispatcher,
                        self._pre_exec_gate,
                        self._execute_r,
                        log=self._log,
                    ),
                    context_policy=CompactionPolicy(self.cfg, log=self._log),
                    event_sink=TranscriptEventSink(transcript, log=self._log),
                    completion=CompletionSignal(
                        lambda: self.dispatcher.last_output
                    ),
                    max_turns=self.max_turns,
                )
                result = engine.run(messages)
        finally:
            self._close_run()

        final_reply = (
            result.last_reply.content
            if result.stop_reason == "submitted" and result.last_reply is not None
            else None
        )
        return self._finish(transcript, final_reply, result.stop_reason)

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
            self._r_kernel = k
            self._r_kernel_env = want_env
        try:
            return k.execute(code, origin="agent")
        except Exception as e:  # noqa: BLE001 — dead worker: drop it, soft-fail
            self._shutdown_r_kernel()
            return {"error": f"R kernel failed: {e}"}

    def _shutdown_r_kernel(self) -> None:
        k = self._r_kernel
        self._r_kernel = None
        self._r_kernel_env = None
        if k is not None:
            try:
                k.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def _finish(
        self, transcript: list[Turn], final_reply: str | None, reason: str
    ) -> dict:
        assert self.dispatcher is not None
        return {
            "stop_reason": reason,
            "final_message": final_reply,
            "submitted_output": self.dispatcher.last_output,
            "transcript": [{"role": t.role, "content": t.content} for t in transcript],
        }

    def _close_run(self) -> None:
        """Release run-scoped runtimes and persist the optional replay tape."""
        self._shutdown_r_kernel()
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
