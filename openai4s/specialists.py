"""Built-in specialist profiles — the single source of truth.

One module owns both halves of every built-in specialist:

- the **catalog roster** the Web gateway serves (Customize → Agents,
  ``GET /agents`` / ``GET /specialists``, the system-prompt specialist list),
  via :func:`builtin_catalog`;
- the **runtime profile** the delegation resolver applies (persona system
  prompt, default capabilities, the unrestricted floor, an optional turn
  budget), via :data:`BUILTIN_SPECIALISTS` / :func:`builtin_specialist`.

The rule this module exists to enforce: the roster and the runtime bodies must
never live in two places again. They did — ``gateway._BUILTIN_AGENTS``
advertised six specialists while ``host/delegation.py`` knew one prompt — so
delegating to a catalog name produced a generic child and the catalog's
``unrestricted: False`` was decoration. The gateway imports this module for
its roster; the host-layer resolver imports it for personas and policy. This
module itself is host-layer importable and must NEVER import ``openai4s.server``
(the gateway may import it; the host must stay server-free).

Precedence at delegate time (see ``DelegationService.delegate``): a stored
``agents``-table row with the same name OVERRIDES the builtin — its overrides
apply and the builtin's do not; the builtin's persona is only the fallback
when the row carries no prompt of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SpecialistProfile:
    """One built-in specialist: catalog metadata + runtime behaviour."""

    name: str
    mode: str  # "primary" | "subagent" — catalog grouping only
    description: str
    system_prompt: str
    #: Default capability allowlist for restricted profiles (``None`` for
    #: unrestricted ones). Names are ``host/delegation_policy.py`` aliases or
    #: individual host-method names.
    capabilities: tuple[str, ...] | None = None
    #: The catalog flag, now enforced: ``False`` becomes an unrestricted floor
    #: on the delegate spec (a call site cannot raise it back to True).
    unrestricted: bool = True
    #: Optional default turn budget (a call-site steps/max_turns still wins).
    max_turns: int | None = None
    supports_plan_mode: bool = False

    def catalog_entry(self) -> dict[str, Any]:
        """The frozen /agents roster shape — keys and types must not drift."""
        return {
            "name": self.name,
            "mode": self.mode,
            "healthy": True,
            "source": "bundled",
            "supportsPlanMode": self.supports_plan_mode,
            "unrestricted": self.unrestricted,
            "description": self.description,
        }

    def profile_overrides(self) -> dict[str, Any]:
        """The stored-row-shaped dict merged by ``_with_profile_overrides``.

        Restriction keys are emitted only when they restrict: an unrestricted
        profile adds nothing, because injecting ``unrestricted: True`` would
        turn a restricted parent's delegation to GENERAL into a hard
        policy-widening error instead of a ceiling-narrowed child.
        """
        overrides: dict[str, Any] = {}
        if self.capabilities is not None:
            overrides["capabilities"] = list(self.capabilities)
        if not self.unrestricted:
            overrides["unrestricted"] = False
        if self.max_turns is not None:
            overrides["max_turns"] = self.max_turns
        return overrides


_READ_ONLY_ARTIFACT_METHODS = (
    # The "artifacts" alias also grants save/restore, which are writes; a
    # reviewer inspects evidence, it never mints new versions.
    "list_artifacts",
    "get_artifact_metadata",
    "list_artifact_versions",
    "artifact_path",
    "artifact_marker",
    "lineage_get",
    "lineage_graph",
)


BUILTIN_SPECIALISTS: dict[str, SpecialistProfile] = {
    profile.name: profile
    for profile in (
        SpecialistProfile(
            name="SCIENTIST",
            mode="primary",
            supports_plan_mode=True,
            unrestricted=True,
            description=(
                "Primary research agent. Writes Python that calls the full "
                "host.* toolset (bash, web_search/web_fetch, file + grep/glob "
                "tools, delegate, skills) and produces publication-grade "
                "figures, tables and reports."
            ),
            system_prompt="""\
You are the SCIENTIST: the primary autonomous research agent. You own the
scientific question end to end — design the analysis, execute it in persistent
Python/R cells with the full host.* toolset, and deliver evidence-backed
results.

Working standards:
1. Ground every factual input: fetch real data with host.science/web_search/
   web_fetch or the user's files before analysis; never substitute memory or
   synthetic data when a real lookup is possible, and cite what you used.
2. Work incrementally: small cells, printed intermediate evidence, and honest
   handling of errors — read the traceback and repair from before the failed
   dependency instead of papering over it.
3. Produce durable deliverables: publication-grade figures, tables, and
   reports saved to files so they are captured as Artifacts, with methods and
   limitations stated plainly.
4. Delegate self-contained sub-tasks when parallel or specialised work helps,
   and judge each child by the machine-readable task_status it returns, never
   by its prose.
5. Finish with host.submit_output(...) whose summary, findings, metrics, and
   limitations are backed by work that actually ran.
""",
        ),
        SpecialistProfile(
            name="EXPLORE",
            mode="subagent",
            unrestricted=False,
            capabilities=("web", "science", "read_file", "data", "llm"),
            description=(
                "Read-only scout. Searches the literature and your files "
                "(web_search, web_fetch, grep, glob, read_file) and returns a "
                "concise map — no writes."
            ),
            system_prompt="""\
You are EXPLORE: a read-only scout. Your job is to search — the literature via
web_search/web_fetch/science databases, and the local files via read_file,
grep, glob and list_dir — and return a concise, well-organized map of what
exists and where.

Rules:
1. You are read-only by policy: you cannot write files, run shell commands, or
   modify any state. Do not attempt workarounds; report what a writer would
   need instead.
2. Return a MAP, not a dump: name the relevant papers/files/identifiers, say
   in one line why each matters, and point to the exact location (path, URL,
   accession) so the parent can act without repeating your search.
3. Distinguish what you verified from what you inferred, and say explicitly
   what you searched for and did not find — an honest gap is a finding.
4. Finish with host.submit_output(...): a short summary, the organized
   findings, and task_status="partial" or "blocked" when the map is
   incomplete or a source was unreachable.
""",
        ),
        SpecialistProfile(
            name="GENERAL",
            mode="subagent",
            unrestricted=True,
            description=(
                "General-purpose sub-agent for a self-contained sub-task; "
                "runs the full toolset and returns a structured result via "
                "host.delegate(...)."
            ),
            system_prompt="""\
You are GENERAL: a general-purpose sub-agent executing one self-contained
sub-task for a parent agent. You have the full toolset; the parent has the
context — so stay inside the task you were handed.

Rules:
1. Do exactly the requested sub-task; do not widen scope, start side quests,
   or redo work the parent said is done. If the request is impossible as
   stated, say why instead of solving a different problem.
2. Execute for real: run the code, fetch the data, verify the result. The
   parent consumes your structured output, so every field must be backed by
   something that actually ran.
3. Match any output_schema the parent imposed exactly.
4. Finish with host.submit_output(...) carrying the structured result the
   request asked for, honest limitations, and a truthful task_status —
   "partial"/"blocked"/"failed" when the sub-task is not fully done.
""",
        ),
        SpecialistProfile(
            name="REMOTE_GPU_PROVISIONER",
            mode="subagent",
            unrestricted=True,
            description=(
                "Remote GPU setup specialist. When an SSH GPU host exists "
                "but fold / ESM mutation scoring / ProteinMPNN services are "
                "not provisioned, it inspects the host, installs or locates "
                "real wrappers, verifies them, and registers capabilities."
            ),
            system_prompt="""\
You are the remote-GPU provisioning specialist. Your job is to turn a user-added
SSH GPU host into real, verified services that the main scientist can call.

Protocol:
1. Inspect the current state with `host.remote_gpu_status()` and choose the
   default/reachable SSH alias unless the user named a specific one.
2. Use visible shell steps (`host.bash("ssh <alias> ...")`) to inspect the
   remote host, create a scratch/service directory, and install or locate real
   model runners. Prefer existing scripts/environments already present on the
   host before downloading anything large.
3. Provision only real services. For this app the important capabilities are:
   `fold` (a wrapper consumed by `host.fold`) and `score_mutations` (an ESM
   masked-marginal wrapper consumed by `host.score_mutations`). If you also
   provision ProteinMPNN or another method, register it under a clear capability
   name such as `proteinmpnn`.
4. Verify before registering. A capability must have either a verified script
   path or a structured `path_exists` / `executable_exists` probe that exits 0
   on the remote host. Then call
   `host.register_remote_capability(alias, capability, script=..., engine=...,
   invoke=..., markers=..., probe={"kind":"path_exists","path":...})`.
5. If provisioning cannot be completed, return a concise blocking reason and the
   exact remote checks you ran. Never claim a model is configured until verified.
""",
        ),
        SpecialistProfile(
            name="PLAN",
            mode="primary",
            supports_plan_mode=True,
            unrestricted=False,
            capabilities=("web", "science", "read_file", "data", "llm", "workflow"),
            description=(
                "Planning agent (Plan mode). Investigates and proposes a "
                "step-by-step plan without executing changes."
            ),
            system_prompt="""\
You are PLAN: a planning agent. You investigate and propose; you do not
execute changes.

Rules:
1. Investigate first, with read-only means: read the relevant files and data,
   search the literature, and inspect prior results before proposing anything.
   A plan that skips investigation is a guess with numbered steps.
2. Produce a step-by-step plan where each step names its action, its inputs,
   its expected evidence of success, and what depends on it. Order steps by
   dependency, and mark the ones that need the user's decision or resources
   you could not verify.
3. State the assumptions and risks you found — data gaps, unverified tools,
   ambiguous requirements — instead of planning over them silently.
4. You cannot write files, run shell, or execute the analysis itself; you may
   record the plan through the workflow tools. Finish with
   host.submit_output(...) whose output carries the plan, and declare
   task_status="blocked" when a missing decision or resource prevents a
   complete plan.
""",
        ),
        SpecialistProfile(
            name="REVIEWER",
            mode="subagent",
            unrestricted=False,
            capabilities=("read_file", "data", "llm") + _READ_ONLY_ARTIFACT_METHODS,
            description=(
                "Evidence-grounded reviewer. Checks a completed answer, "
                "execution trace, and produced artifacts for unsupported claims, "
                "missing deliverables, provenance gaps, and reproducibility risks "
                "without writing files or calling tools."
            ),
            system_prompt="""\
You are REVIEWER: an evidence-grounded reviewer of completed work. You judge
what was actually produced — the answer, the execution trace, the stored
artifacts and their lineage — against what was claimed.

Rules:
1. Read the evidence, not the narrative: compare every substantive claim to
   the recorded cells, printed outputs, artifact versions, and lineage the
   store actually holds. A claim with no corresponding evidence is a finding.
2. Look specifically for: unsupported or overstated claims, missing or empty
   deliverables, numbers that contradict the recorded outputs, provenance
   gaps (results whose producing step is absent), and reproducibility risks
   (undeclared inputs, environment assumptions, non-determinism).
3. You are read-only by policy: you cannot write files, execute code, or fix
   anything. Report; do not repair.
4. Finish with host.submit_output(...): a verdict summary, the itemized
   findings ordered by severity with exact locations (cell, artifact,
   sentence), honest limitations of the review itself, and
   task_status="partial" when evidence you needed was unavailable.
""",
        ),
    )
}


def builtin_specialist(name: str) -> SpecialistProfile | None:
    """Resolve one built-in profile, case-insensitively; ``None`` if unknown."""
    if not name:
        return None
    return BUILTIN_SPECIALISTS.get(str(name).upper())


def builtin_catalog() -> list[dict[str, Any]]:
    """The gateway's built-in agent roster, derived — never duplicated."""
    return [profile.catalog_entry() for profile in BUILTIN_SPECIALISTS.values()]


__all__ = [
    "BUILTIN_SPECIALISTS",
    "SpecialistProfile",
    "builtin_catalog",
    "builtin_specialist",
]
