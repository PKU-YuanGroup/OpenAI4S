"""SlurmBroker: every sbatch/squeue/sacct/scancel call, and all the parsing.

One module owns the subprocess boundary so two properties can be checked in
one place rather than argued about at a dozen call sites:

**Arguments are constructed, never concatenated (INV-9).** Every value that
reaches argv is either a literal this module wrote or a typed field that was
validated on the way in. There is no shell: `subprocess.run` gets a list,
`shell=False`, so a profile named ``gpu; rm -rf /`` is an argument, not a
command. Job names and comments are additionally restricted to a character
class, because they are the two fields that carry our own identifiers into
the scheduler's output and back out through a parser.

**Secrets never reach the scheduler (INV-9).** A comment carries the
submission token — an opaque, single-purpose id that is useless without our
database — and never a credential. The bootstrap secret of M3b travels as a
*path* to an 0600 file, which is why the environment allowlist below refuses
anything that looks like a credential rather than trusting the caller.

Parsing lives here and only here. `--parsable` and `--noheader --format` are
requested everywhere they exist so the output shape is ours to pin, and
every parser tolerates the fields it does not know: a scheduler upgrade that
adds a column must not turn into a crash in a daemon.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any

#: What a job name or comment may contain. Slurm accepts more, but these are
#: values WE mint and later match on, and a comma or a newline in a
#: `--format`-parsed field is a parser bug waiting for an unusual input.
_SAFE_TOKEN_RE = re.compile(r"\A[A-Za-z0-9_.:@-]{1,180}\Z")

#: Environment variables a submission may carry. An allowlist, because the
#: alternative is inheriting a daemon environment that holds API keys.
_ENV_NAME_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
#: What an environment *value* may contain. `--export` takes a
#: comma-separated list with no escaping, so a comma in a value does not
#: quote -- it adds list elements. `--export=X=1,ALL` is how a member turns
#: their own job into one that inherits the daemon's whole environment,
#: which is where this product's LLM credentials live. Newlines and NULs go
#: too: they are how a value stops being one argument.
_ENV_VALUE_RE = re.compile(r"\A[^,\r\n\x00]{0,4096}\Z")

#: The list elements `--export` treats as instructions rather than as
#: variables. Checked after assembly as well, so a future field that skips
#: the dataclass cannot reintroduce the same thing.
_EXPORT_KEYWORDS = frozenset({"all", "none", "nil"})
_CREDENTIAL_HINT_RE = re.compile(
    r"(SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|CREDENTIAL|PRIVATE)", re.IGNORECASE
)

#: Default timeout for a scheduler command. A cluster under load answers
#: slowly; a cluster that is gone does not answer at all, and the daemon's
#: reconciler must not block on either.
DEFAULT_TIMEOUT_S = 30.0


class SlurmCommandError(RuntimeError):
    """A scheduler command failed, timed out, or was not found.

    Carries the pieces an operator needs (`command`, `returncode`,
    `stderr`) and two flags that callers must branch on rather than
    collapse:

    * ``timed_out`` — the command did not answer. A submission that timed
      out may still have landed (INV-8).
    * ``unreachable`` — there was no scheduler to ask (not on PATH). This is
      NOT "the scheduler says it has no such job": conflating them turns a
      cluster outage into every running job being declared lost, which is
      the single most expensive way to be wrong here.
    """

    def __init__(
        self,
        message: str,
        *,
        command: tuple[str, ...] = (),
        returncode: int | None = None,
        stderr: str = "",
        timed_out: bool = False,
        unreachable: bool = False,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        self.timed_out = timed_out
        self.unreachable = unreachable


def _assert_no_export_keyword(assembled: str) -> None:
    """A belt on the joined string, in case a future field skips the
    dataclass that validated its parts."""
    for element in assembled.split(","):
        head = element.split("=", 1)[0].strip().lower()
        if head in _EXPORT_KEYWORDS:
            raise ValueError(f"refusing an --export list containing {head!r} (INV-9)")


def _validate_env_value(key: str, value: str) -> None:
    """INV-9's other half: a *value* can be an instruction.

    Keys were validated from the start, values were not, and `--export` is a
    comma-separated list with no escaping -- so `{"X": "1,ALL"}` assembles
    into `--export=X=1,ALL` and the scheduler propagates the submitting
    process's entire environment to the job. That environment is the
    daemon's, and the daemon's is where the LLM credentials are. The name
    rule refused a credential-shaped variable; this one refuses a value that
    turns the whole list into one.
    """
    text = str(value)
    if not _ENV_VALUE_RE.fullmatch(text):
        raise ValueError(
            f"refusing an environment value for {key!r} containing a comma, "
            f"newline or NUL (INV-9): --export is a comma-separated list "
            f"with no escaping, so a comma adds list elements rather than "
            f"quoting them"
        )
    if text.strip().lower() in _EXPORT_KEYWORDS:
        raise ValueError(
            f"refusing the environment value {text!r} for {key!r} (INV-9): "
            f"--export reads it as an instruction, not as a value"
        )


@dataclass(frozen=True)
class StepSpec:
    """A validated job step. Same discipline as `SubmitSpec`: every field
    here is already safe for argv, and the environment is filtered by the
    same credential-shaped-name rule — a step's environment is as readable
    as a job's."""

    command: tuple[str, ...]
    tasks: int = 1
    nodes: int = 1
    cpus_per_task: int = 1
    workdir: str | None = None
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("a step needs a command")
        for name in ("tasks", "nodes", "cpus_per_task"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        for key, value in self.environment.items():
            if not _ENV_NAME_RE.fullmatch(key):
                raise ValueError(f"invalid environment variable name {key!r}")
            if _CREDENTIAL_HINT_RE.search(key):
                raise ValueError(
                    f"refusing to put {key!r} in a step environment "
                    f"(INV-9: pass a path to a 0600 file instead)"
                )
            _validate_env_value(key, value)


@dataclass(frozen=True)
class SubmitSpec:
    """A validated submission. Every field here is already safe for argv."""

    job_name: str
    comment: str
    script: str
    cpus: int = 1
    memory_mb: int = 4096
    gpus: int = 0
    walltime_s: int = 3600
    nodes: int = 1
    partition: str | None = None
    qos: str | None = None
    workdir: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    environment: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("job_name", "comment"):
            value = getattr(self, name)
            if not _SAFE_TOKEN_RE.fullmatch(value):
                raise ValueError(
                    f"{name} must match {_SAFE_TOKEN_RE.pattern} (got {value!r}); "
                    f"these values are minted by us and matched back out of "
                    f"scheduler output, so their character class is the parser's "
                    f"contract"
                )
        for name in ("cpus", "memory_mb", "walltime_s", "nodes"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.gpus < 0:
            raise ValueError("gpus cannot be negative")
        if not self.script.strip():
            raise ValueError("script is required")
        for key, value in self.environment.items():
            if not _ENV_NAME_RE.fullmatch(key):
                raise ValueError(f"invalid environment variable name {key!r}")
            if _CREDENTIAL_HINT_RE.search(key):
                raise ValueError(
                    f"refusing to put {key!r} in a submission environment "
                    f"(INV-9): a credential belongs in an 0600 file whose "
                    f"*path* is passed instead"
                )
            _validate_env_value(key, value)


@dataclass(frozen=True)
class JobStatus:
    """One job as the scheduler currently describes it."""

    job_id: str
    state: str
    reason: str = ""
    exit_code: str = ""
    comment: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


def _hhmmss(seconds: int) -> str:
    """Slurm's walltime spelling. Explicit rather than clever: a wrong
    walltime is a job killed mid-run or a queue slot held for a day."""
    seconds = max(1, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


class SlurmBroker:
    """The subprocess boundary. Nothing else in the tree runs a scheduler."""

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        runner: Any = None,
    ) -> None:
        self._timeout_s = timeout_s
        # Injected in tests with a fake; production passes None and gets
        # subprocess.run. Not a mock-shaped seam for its own sake — the fake
        # scheduler scripts the plan asks for are driven through PATH, and
        # this exists for the unit-level checks of argv construction.
        self._runner = runner or subprocess.run

    # --- plumbing ---------------------------------------------------------

    def available(self) -> bool:
        """Is there a scheduler on PATH at all?"""
        return all(shutil.which(tool) for tool in ("sbatch", "squeue", "scancel"))

    def _run(self, command: list[str], *, stdin: str | None = None) -> str:
        try:
            completed = self._runner(
                command,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SlurmCommandError(
                f"{command[0]} not found on PATH",
                command=tuple(command),
                unreachable=True,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            # The caller must distinguish this from a refusal: a submission
            # that timed out may still have landed (INV-8).
            raise SlurmCommandError(
                f"{command[0]} timed out after {self._timeout_s}s",
                command=tuple(command),
                timed_out=True,
            ) from exc
        if completed.returncode != 0:
            raise SlurmCommandError(
                f"{command[0]} failed with exit {completed.returncode}",
                command=tuple(command),
                returncode=completed.returncode,
                stderr=(completed.stderr or "").strip()[:2000],
            )
        return completed.stdout or ""

    # --- submission -------------------------------------------------------

    def build_submit_argv(self, spec: SubmitSpec) -> list[str]:
        """The argv for one submission. Separated from `submit` so a test can
        assert what would be executed without executing anything."""
        argv = [
            "sbatch",
            "--parsable",
            f"--job-name={spec.job_name}",
            f"--comment={spec.comment}",
            f"--cpus-per-task={spec.cpus}",
            f"--mem={spec.memory_mb}M",
            f"--time={_hhmmss(spec.walltime_s)}",
            f"--nodes={spec.nodes}",
        ]
        if spec.gpus > 0:
            argv.append(f"--gpus={spec.gpus}")
        if spec.partition:
            argv.append(f"--partition={spec.partition}")
        if spec.qos:
            argv.append(f"--qos={spec.qos}")
        if spec.workdir:
            argv.append(f"--chdir={spec.workdir}")
        if spec.stdout_path:
            argv.append(f"--output={spec.stdout_path}")
        if spec.stderr_path:
            argv.append(f"--error={spec.stderr_path}")
        if spec.environment:
            # ALL means inherit; we name exactly what we want instead, so the
            # daemon's environment (which holds API keys) is not the default.
            pairs = ",".join(f"{k}={v}" for k, v in sorted(spec.environment.items()))
            _assert_no_export_keyword(pairs)
            argv.append(f"--export={pairs}")
        else:
            argv.append("--export=NONE")
        return argv

    def submit(self, spec: SubmitSpec) -> str:
        """Submit and return the scheduler's job id.

        The script arrives on stdin rather than as a temp file: one less
        thing to clean up, and nothing on a shared filesystem for the window
        between writing and submitting.
        """
        stdout = self._run(self.build_submit_argv(spec), stdin=spec.script)
        # `--parsable` prints "jobid" or "jobid;cluster".
        first = (stdout or "").strip().splitlines()[0] if stdout.strip() else ""
        job_id = first.split(";")[0].strip()
        if not job_id:
            raise SlurmCommandError(
                "sbatch returned no job id", command=("sbatch",), stderr=stdout[:500]
            )
        return job_id

    def build_step_argv(self, job_id: str, spec: "StepSpec") -> list[str]:
        """`srun --jobid=<existing>` — a step, never a new allocation.

        The `--jobid` is the whole point and is not optional: an `srun`
        without one *allocates*, which would turn an interactive task into a
        second job holding a second set of resources. That is precisely what
        INV-4 forbids, and it is a one-flag difference between the correct
        behaviour and the expensive one.
        """
        if not _SAFE_TOKEN_RE.fullmatch(job_id):
            raise ValueError(f"refusing an unsafe job id: {job_id!r}")
        argv = [
            "srun",
            f"--jobid={job_id}",
            f"--ntasks={int(spec.tasks)}",
            f"--nodes={int(spec.nodes)}",
            f"--cpus-per-task={int(spec.cpus_per_task)}",
        ]
        if spec.workdir:
            argv.append(f"--chdir={spec.workdir}")
        if spec.environment:
            pairs = ",".join(f"{k}={v}" for k, v in sorted(spec.environment.items()))
            _assert_no_export_keyword(pairs)
            argv.append(f"--export={pairs}")
        argv.append("--")
        argv.extend(spec.command)
        return argv

    def run_step(self, job_id: str, spec: "StepSpec") -> str:
        """Run a step and return what it wrote. Blocking, like `srun` is."""
        return self._run(self.build_step_argv(job_id, spec))

    # --- observation ------------------------------------------------------

    _SQUEUE_FIELDS = ("JobID", "State", "Reason", "Comment")
    _SACCT_FIELDS = ("JobIDRaw", "State", "ExitCode", "Comment")

    def queue_status(self, job_id: str) -> JobStatus | None:
        """A queued/running job, or None when the scheduler no longer lists it.

        None is not "gone": a finished job leaves the queue, so a caller that
        gets None must ask `accounting_status` before concluding anything.
        """
        argv = [
            "squeue",
            "--noheader",
            f"--job={job_id}",
            "--format=" + "|".join(f"%{code}" for code in ("i", "T", "r", "k")),
        ]
        try:
            stdout = self._run(argv)
        except SlurmCommandError as exc:
            # squeue exits non-zero for an unknown job on some builds.
            if exc.timed_out or exc.unreachable:
                # Absence of an answer is not an answer of absence.
                raise
            return None
        return self._parse_row(stdout, self._SQUEUE_FIELDS)

    def accounting_status(self, job_id: str) -> JobStatus | None:
        """The accounting record, which outlives the queue entry."""
        argv = [
            "sacct",
            "--noheader",
            "--parsable2",
            f"--jobs={job_id}",
            "--format=" + ",".join(self._SACCT_FIELDS),
        ]
        try:
            stdout = self._run(argv)
        except SlurmCommandError as exc:
            if exc.timed_out or exc.unreachable:
                # Absence of an answer is not an answer of absence.
                raise
            return None
        return self._parse_row(stdout, self._SACCT_FIELDS, separator="|")

    def find_by_comment(self, comment: str) -> str | None:
        """The INV-8 reconciliation: does anything carry this token?

        Asks the queue first (a submission that just landed is there) and
        then accounting (it may already have finished, or failed instantly).
        Returns the job id, never a status — the caller wants to know whether
        to retry, and a status would invite deciding that from the wrong
        field.
        """
        if not _SAFE_TOKEN_RE.fullmatch(comment):
            raise ValueError(f"invalid comment {comment!r}")
        argv = [
            "squeue",
            "--noheader",
            "--all",
            "--format=%i|%k",
        ]
        try:
            stdout = self._run(argv)
        except SlurmCommandError as exc:
            if exc.timed_out or exc.unreachable:
                # Absence of an answer is not an answer of absence.
                raise
            stdout = ""
        for line in (stdout or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and parts[1] == comment and parts[0]:
                return parts[0].split(".")[0]

        argv = [
            "sacct",
            "--noheader",
            "--parsable2",
            "--allusers",
            "--format=JobIDRaw,Comment",
        ]
        try:
            stdout = self._run(argv)
        except SlurmCommandError as exc:
            if exc.timed_out or exc.unreachable:
                # Absence of an answer is not an answer of absence.
                raise
            return None
        for line in (stdout or "").splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 2 and parts[1] == comment and parts[0]:
                return parts[0].split(".")[0]
        return None

    def cancel(self, job_id: str) -> None:
        """Ask the scheduler to kill it. Idempotent by contract: cancelling
        something already gone is success, because the cancel barrier can run
        twice and a second failure there would strand a workload."""
        try:
            self._run(["scancel", str(job_id)])
        except SlurmCommandError as exc:
            if exc.timed_out or exc.unreachable:
                # Absence of an answer is not an answer of absence.
                raise
            return

    # --- parsing ----------------------------------------------------------

    @staticmethod
    def _parse_row(
        stdout: str, fields: tuple[str, ...], *, separator: str = "|"
    ) -> JobStatus | None:
        for line in (stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(separator)]
            # Tolerate extra columns: a scheduler upgrade that adds a field
            # must not become a crash in a daemon.
            row = dict(zip(fields, parts))
            job_id = (row.get("JobID") or row.get("JobIDRaw") or "").split(".")[0]
            if not job_id:
                continue
            state = (
                (row.get("State") or "").split()[0].upper() if row.get("State") else ""
            )
            return JobStatus(
                job_id=job_id,
                state=state,
                reason=row.get("Reason") or "",
                exit_code=row.get("ExitCode") or "",
                comment=row.get("Comment") or "",
                raw=row,
            )
        return None


__all__ = [
    "DEFAULT_TIMEOUT_S",
    "JobStatus",
    "SlurmBroker",
    "SlurmCommandError",
    "SubmitSpec",
]
