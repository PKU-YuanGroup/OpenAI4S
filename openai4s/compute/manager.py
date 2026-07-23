"""Host-side remote-compute transport.

The worker's ``host.compute`` SDK routes every call to
``host_call("compute_<op>", [kw])``; the dispatcher forwards those here. This
module owns the real work the SDK only describes:

  * provider discovery — scan ``skills/remote-compute-<id>/provider.json`` for
    an ``id`` and a ``provider.py`` that exports ``PROVIDER``.
  * byoc transport      — spawn the confined ``openai4s_compute_provider``
    helper (oneshot mode) per op, staging inputs/outputs through a temp dir and
    handing the credential on the helper's stdin so the process environment is
    never a secret carrier.
  * ssh transport       — run a job script / one-off command over an SSH alias.
  * on-demand harvest   — ``result()`` polls the remote and unpacks terminal
    outputs into ``hpc/<job_id>/``.

Two provider families share one manager:
  "byoc:<id>"  bring-your-own-compute sandbox (e.g. "byoc:nvidia").
  "ssh:<alias>" a job over an existing SSH connection.

Terminal states are mutually exclusive and never optimistic — the vocabulary
and its transition table live in ``compute/states.py`` and are enforced when a
status is written. ``succeeded`` requires evidence on both halves: a verified
rc==0 *and* every declared output harvested and hashed. A job that exits 0
while a pattern it declared in ``outputs`` matched nothing is ``failed`` with
``termination_reason: outputs_unverified``.

``unknown`` means the outcome could not be established. It is *not* a synonym
for failure, it must never be resolved to success by default, and it counts as
live — something may be running out there, and forgetting it is how a sandbox
bills unnoticed. Every path that cannot produce an exit code lands there
deliberately.

Jobs are durable. A remote job outlives this process — an ssh job keeps running
under ``nohup``, a byoc sandbox keeps billing — so every job is recorded in
``compute_jobs`` before it is submitted, its provider receipt (remote pid /
sandbox id) is stored on acknowledgement, and every transition appends to a
sequenced ``compute_job_events`` stream. A restart rehydrates whatever was live
and ``reconcile()`` surfaces it; nothing is resubmitted automatically, because
a job in ``submitted`` may or may not be running and guessing wrong costs either
a duplicate charge or a lost result.

Known limit, stated so it is not mistaken for a guarantee: no OS boundary is
applied to the byoc helper — see ``confinement_status``.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from openai4s.compute import manifest, states
from openai4s.compute.safe_archive import UnsafeArchiveError, safe_extract_tar

# Repo-root openai4s_compute_provider (the confined helper package).
_HELPER_MAIN = str(
    Path(__file__).resolve().parent.parent.parent
    / "openai4s_compute_provider"
    / "__main__.py"
)

# Job-wrapper templates (ported alongside this module).
_TMPL_DIR = Path(__file__).resolve().parent / "templates"

# Mirrors OPENAI4S_KERNEL_SANDBOX's vocabulary: auto | enforce | off.
_CONFINEMENT_ENV = "OPENAI4S_COMPUTE_CONFINEMENT"
_VALID_CONFINEMENT = frozenset({"auto", "enforce", "off"})


def _confinement_mode(value: str | None = None) -> str:
    mode = str(value if value is not None else os.environ.get(_CONFINEMENT_ENV, "auto"))
    mode = mode.strip().lower() or "auto"
    if mode not in _VALID_CONFINEMENT:
        raise ComputeError(
            f"{_CONFINEMENT_ENV} must be one of "
            f"{', '.join(sorted(_VALID_CONFINEMENT))}; got {mode!r}",
            "invalid_request",
        )
    return mode


# Error kinds that, by themselves, mean "the remote may or may not have acted".
# Everything else is a definite rejection *only* when the raiser says so —
# `indeterminate` is the explicit signal, and this set is merely its default.
_INDETERMINATE_KINDS = frozenset({"unknown_state"})


class ComputeError(RuntimeError):
    """Surface as {'error', 'error_kind', ...} on the wire; the SDK turns a
    non-status error into a RuntimeError carrying .error_kind.

    ``indeterminate`` is the field that decides whether a failed submit is
    recorded as terminal ``failed`` or as live ``unknown``. It is carried
    explicitly rather than inferred from ``kind`` because the two questions are
    different: ``kind`` says what went wrong, ``indeterminate`` says whether
    work may nonetheless be running (and billing) on the other end. A helper
    that dies before writing its reply is ``transient`` by kind and thoroughly
    indeterminate by fact.
    """

    def __init__(
        self,
        msg: str,
        kind: str = "transient",
        concurrency: dict | None = None,
        *,
        indeterminate: bool | None = None,
    ):
        super().__init__(msg)
        self.error_kind = kind
        self.concurrency = concurrency
        self.indeterminate = (
            (kind in _INDETERMINATE_KINDS)
            if indeterminate is None
            else bool(indeterminate)
        )
        # Set by _run_helper when the stage dir still names a sandbox: a live
        # billable resource the reply never got to mention.
        self.sandbox_id: str | None = None


# Ceiling on one direct scp. The job path stages inputs through a manifest and
# harvests through the safe-archive extractor; this compatibility surface has
# neither, so it gets an explicit cap instead of the implicit "whatever fits".
MAX_TRANSFER_BYTES = 512 * 1024 * 1024

# How long before the sandbox expires the wrapper must stop the job so that
# taring the outputs and writing .phase still fit. The wrapper defaults to the
# same value; sending it explicitly keeps the policy on the host, where the
# harvest actually happens.
HARVEST_MARGIN_S = 600
# Grace between SIGTERM and SIGKILL for the job's process group.
TERM_GRACE_S = 60

# GNU `timeout` reports expiry with this exit code. The ssh job body wraps the
# command in it whenever a deadline is requested.
_TIMEOUT_EXIT_CODE = 124

# The confined helper's own verdict when the boundary the host applied does not
# hold from inside. It exits before reading the credential and before any
# provider call, so nothing has happened when this is seen.
_EXIT_UNCONFINED = 71


def _safe_remote_path(value: str, *, label: str) -> str:
    """A remote path that cannot walk out of where the caller said it was going.

    `scp` happily accepts `../../etc/passwd` and a shell-quoted path is still a
    path — quoting stops word-splitting, not traversal. These are the same
    rejections the archive extractor applies, for the same reason: the string
    came from an agent, and the remote host is not ours to trust.
    """
    text = str(value or "").strip()
    if not text:
        raise ComputeError(f"{label} must not be empty", "invalid_request")
    if "\x00" in text:
        raise ComputeError(f"{label} must not contain a NUL byte", "invalid_request")
    if "\n" in text or "\r" in text:
        raise ComputeError(f"{label} must not contain a newline", "invalid_request")
    if ".." in Path(text).parts:
        raise ComputeError(
            f"{label} must not contain '..' ({text!r})", "invalid_request"
        )
    return text


def _safe_stage_name(value: str, *, label: str) -> str:
    """A staged input lands flat in the archive root, so its name is a name.

    ``work / dst`` is a join, and a join with an absolute path discards the
    left side entirely — ``work / "/etc/cron.d/x"`` is ``/etc/cron.d/x``. A
    relative ``../`` walks out just as effectively. Both write wherever the
    daemon can write, before the archive is ever built, and the caller picking
    the name is an agent.
    """
    text = str(value or "").strip()
    if not text:
        raise ComputeError(f"{label} must not be empty", "invalid_request")
    if "\x00" in text:
        raise ComputeError(f"{label} must not contain a NUL byte", "invalid_request")
    if text in (".", ".."):
        raise ComputeError(f"{label} must name a file ({text!r})", "invalid_request")
    if os.path.isabs(text) or Path(text).name != text:
        raise ComputeError(
            f"{label} must be a bare filename with no directory part ({text!r}); "
            f"staged inputs are placed flat in the job's work directory",
            "invalid_request",
        )
    return text


def _sandbox_lifetime_s(spec: Any) -> int:
    """The container lifetime the caller declared, in seconds.

    Documented on ``host.compute.create`` as ``timeout`` inside
    ``provider_params``. Absent or unreadable yields 0, which leaves the
    wrapper's watchdog unarmed — the same behaviour as before, rather than a
    guessed deadline that could kill a job early.
    """
    if not isinstance(spec, dict):
        return 0
    for key in ("timeout", "timeout_seconds", "lifetime_s"):
        try:
            value = int(spec.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


# Per-file ceiling on what an ssh harvest pulls back automatically, matching
# what the bundled skill documents. Anything larger stays on the cluster — it
# is usually already where the next job wants it — and comes back named in
# `left_on_remote_files` so it can still be chained or fetched deliberately.
HARVEST_MAX_FILE_BYTES = 100 * 1024 * 1024

# Staged inside the job's own work directory, so it is cleaned up with it.
_HARVEST_ARCHIVE = ".openai4s-harvest.tar.gz"

# Control files the job's own machinery wrote. They are the *protocol*, not the
# job's output, and re-harvesting them would put a second copy of the exit code
# and the pgid into the artifact record.
_HARVEST_EXCLUDES = (
    _HARVEST_ARCHIVE,
    f"{_HARVEST_ARCHIVE}.tmp",
    ".openai4s-harvest.list",
    ".openai4s-harvest.skipped",
    ".openai4s-harvest.stayed",
    "run.sh",
    ".rc",
    ".rc.tmp",
    ".pgid",
    ".pgid.tmp",
    ".timeout",
)

_HARVEST_TAG = "OPENAI4S_HARVEST"
#: Path lines are tagged rather than bare so a path containing a space, or a
#: chatty login profile, cannot be confused for one another.
_HARVEST_SKIPPED_TAG = "OPENAI4S_HARVEST_SKIPPED"
_HARVEST_STAYED_TAG = "OPENAI4S_HARVEST_STAYED"


def _find_stay_clause(patterns: list[str]) -> tuple[str, str]:
    """``(exclusion, selection)`` find expressions for stay-remote globs.

    Path *and* basename, mirroring ``manifest.matches_any`` — a declaration
    that keeps a file out of the reconciler must keep the same file out of the
    archive, or the two halves of the contract disagree.
    """
    if not patterns:
        return "", ""
    exclusion_parts: list[str] = []
    selection_parts: list[str] = []
    for pattern in patterns:
        rel = pattern.lstrip("/")
        if rel.startswith("./"):
            rel = rel[2:]
        path_form = shlex.quote(f"./{rel}")
        name_form = shlex.quote(rel)
        exclusion_parts.append(f"! -path {path_form} ! -name {name_form}")
        selection_parts.append(f"-path {path_form} -o -name {name_form}")
    return (
        " ".join(exclusion_parts),
        "\\( " + " -o ".join(selection_parts) + " \\)",
    )


def _ssh_harvest_script(
    workdir: str, max_file_bytes: int, exclude_patterns: list[str] | None = None
) -> str:
    """Stage the job's work directory into one archive, remotely.

    One archive rather than a file list: it is one transfer to check the return
    code of, and it goes through the same enumerate-then-extract safety gate as
    the byoc harvest. Oversized files are enumerated and *excluded* rather than
    silently truncating the transfer, so the caller learns what stayed behind.

    ``exclude_patterns`` are the caller's ``residency: remote`` declarations.
    They are applied *here*, in the ``find`` that builds the archive, because
    that is the only place the bytes can be stopped before they move. Excluding
    them after the download would satisfy the manifest and violate the promise.
    """
    patterns = list(exclude_patterns or [])
    excludes = " ".join(f"! -name {shlex.quote(name)}" for name in _HARVEST_EXCLUDES)
    stay_exclusion, stay_selection = _find_stay_clause(patterns)
    # `find -size +Nc` is POSIX and counts bytes; `-size +Nk` would round.
    size_test = f"-size +{int(max_file_bytes)}c"
    listing = ".openai4s-harvest.list"
    skipped = ".openai4s-harvest.skipped"
    stayed = ".openai4s-harvest.stayed"
    stay_scan = (
        f"find . -type f {excludes} {stay_selection} -print > {stayed} 2>/dev/null; "
        if stay_selection
        else f": > {stayed}; "
    )
    return (
        f"cd {workdir} 2>/dev/null || "
        f"{{ echo 'openai4s: work directory is gone' >&2; exit 3; }}; "
        f"rm -f {_HARVEST_ARCHIVE} {_HARVEST_ARCHIVE}.tmp {listing} {skipped} "
        f"{stayed}; "
        f"{stay_scan}"
        f"find . -type f {excludes} {stay_exclusion} {size_test} "
        f"-print > {skipped} 2>/dev/null; "
        f"find . -type f {excludes} {stay_exclusion} ! {size_test} "
        f"-print > {listing} 2>/dev/null; "
        f"if [ -s {listing} ]; then "
        # Same tar hardening the byoc wrapper applies, for the same reason: a
        # login profile is free to export TAR_OPTIONS or GZIP, and GNU tar and
        # its gzip both honour them, so the archive's shape would be partly
        # chosen by the remote environment. COPYFILE_DISABLE additionally stops
        # bsdtar (macOS) from emitting an AppleDouble `._name` sidecar beside
        # every file, which would arrive as a phantom extra "output".
        f"if env -u TAR_OPTIONS -u GZIP COPYFILE_DISABLE=1 "
        f"tar -czf {_HARVEST_ARCHIVE}.tmp -T {listing} 2>/dev/null; then "
        f"mv -f {_HARVEST_ARCHIVE}.tmp {_HARVEST_ARCHIVE}; "
        f"echo '{_HARVEST_TAG} archive'; "
        f"else rm -f {_HARVEST_ARCHIVE}.tmp; "
        f"echo 'openai4s: tar failed' >&2; exit 4; fi; "
        f"else echo '{_HARVEST_TAG} empty'; fi; "
        # The paths left behind, one per line, with find's leading "./" removed
        # so they read the same as the manifest's relative paths.
        f"sed 's|^\\./|{_HARVEST_SKIPPED_TAG} |' {skipped} 2>/dev/null; "
        f"sed 's|^\\./|{_HARVEST_STAYED_TAG} |' {stayed} 2>/dev/null; "
        f"rm -f {listing} {skipped} {stayed}; exit 0"
    )


def _prune_local_matches(dest: Path, patterns: list[str]) -> list[str]:
    """Delete anything under ``dest`` a stay-remote glob covers.

    Returns the relative paths removed. Silence here would be the worst of
    both worlds — the file gone locally *and* unmentioned — so the caller
    reports them exactly as if the remote had excluded them.
    """
    removed: list[str] = []
    for path in sorted(dest.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(dest).as_posix()
        if manifest.matches_any(rel, patterns):
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(rel)
    return removed


def _parse_harvest_ack(stdout: str) -> tuple[str, list[str], list[str]]:
    """Split the harvest reply into ``(marker, oversized, stayed)``.

    The tagged forms are checked before the bare marker because both begin
    with it; an untagged line after a marker is still read as oversized, which
    keeps a harvest staged by an older wrapper readable.
    """
    marker = ""
    oversized: list[str] = []
    stayed: list[str] = []
    for line in stdout.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(f"{_HARVEST_SKIPPED_TAG} "):
            oversized.append(text[len(_HARVEST_SKIPPED_TAG) + 1 :])
            continue
        if text.startswith(f"{_HARVEST_STAYED_TAG} "):
            stayed.append(text[len(_HARVEST_STAYED_TAG) + 1 :])
            continue
        if text.startswith(_HARVEST_TAG):
            parts = text.split()
            marker = parts[1] if len(parts) > 1 else ""
            continue
        if marker:
            oversized.append(text)
    return marker, oversized, stayed


# The submit acknowledgement line. Tagged so a login banner, a `.bashrc` echo,
# or a motd cannot be mistaken for the job's identity — an untagged `echo $!`
# meant the first line of any chatty profile became the "pid" we later signalled.
_SSH_SUBMIT_TAG = "OPENAI4S_JOB"


def _parse_ssh_submit_ack(stdout: str) -> tuple[str, str]:
    """Pull ``(pid, pgid)`` out of the tagged acknowledgement line.

    Returns empty strings when the host said nothing identifiable, which the
    caller must treat as an indeterminate submit rather than a success.
    """
    for line in reversed(stdout.splitlines()):
        parts = line.split()
        if len(parts) >= 2 and parts[0] == _SSH_SUBMIT_TAG:
            pid = parts[1].strip()
            pgid = parts[2].strip() if len(parts) >= 3 else ""
            if pid.isdigit():
                # 0 is "the caller's own group" and 1 is init's; a job's group
                # can be neither, and recording one would arm a cancel that
                # signals something catastrophic.
                if not (pgid.isdigit() and int(pgid) > 1):
                    pgid = ""
                return pid, pgid
    return "", ""


def _ssh_cancel_script(pgid: str, pid: str = "") -> str:
    """TERM the job's process group, escalate to KILL, then confirm.

    Four things this replaces, all of which let a cancel report success over a
    job that was still running:

      * ``kill -TERM <pid>`` signalled only the outer ``bash -c``. The whole
        command tree — `run.sh` and every process it started, which are the
        ones actually holding the allocation — lives in the job's process
        group, and only ``kill -- -<pgid>`` reaches it.
      * the pgid was *assumed* to equal ``$!``. It does not in a
        non-interactive login shell, so the group being signalled frequently
        did not exist. This script is now handed the group the host itself
        reported at submit.
      * a process that ignores SIGTERM was never escalated to SIGKILL.
      * nothing checked afterwards. The final ``kill -0`` is what makes the
        non-zero exit — and therefore the caller's ``unknown_state`` — mean
        "we looked, and it is still there".

    ``pid`` is the job's own process, signalled alongside the group as a belt:
    if the group turned out to be shared or stale, the leader still dies, and
    the confirmation below checks both.
    """
    group = shlex.quote(str(pgid))
    leader = shlex.quote(str(pid or ""))
    return (
        f"pgid={group}; pid={leader}; "
        # A job submitted before the pgid was recorded still has one; ask the
        # host for it rather than falling back to signalling the pid as if it
        # were a group. If the process is already gone `ps` says nothing, and
        # `alive` below is then false — which is a successful cancel.
        f'[ -n "$pgid" ] || pgid=$(ps -o pgid= -p "$pid" 2>/dev/null '
        f"| tr -d ' \\t\\n'); "
        # 0 means "my own process group" and 1 is init's. Signalling either is
        # catastrophic and neither can ever be a job's group, so an
        # unresolvable pgid becomes *no* group rather than a dangerous one.
        f"case \"$pgid\" in ''|0|1|-*) pgid= ;; esac; "
        # `kill -0` first: a job that already finished is a successful cancel,
        # not an error. Both identities are probed, because either one going
        # quiet is not on its own proof the other did.
        #
        # No `--` before the negative pid. dash's `kill` builtin rejects it
        # outright ("Illegal number: -"), and every signal here was wrapped in
        # `2>/dev/null`, so on a dash login shell — Debian/Ubuntu's /bin/sh,
        # and the login shell of most slim containers — nothing was ever
        # signalled, the follow-up `kill -0` failed the same way, and the
        # script exited 0. The user was told the allocation was freed. The
        # signal name already consumed the option slot, so `-PGID` is
        # unambiguous without it, and this form works in dash, ash and bash.
        f"alive() {{ "
        f'if [ -n "$pgid" ] && kill -0 -"$pgid" 2>/dev/null; then return 0; fi; '
        f'[ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; }}; '
        f"sig() {{ "
        f'[ -n "$pgid" ] && kill -"$1" -"$pgid" 2>/dev/null; '
        f'[ -n "$pid" ] && kill -"$1" "$pid" 2>/dev/null; return 0; }}; '
        f"alive || exit 0; "
        f"sig TERM; "
        f"for _ in 1 2 3 4 5 6 7 8 9 10; do "
        f"alive || exit 0; sleep 1; done; "
        f"sig KILL; sleep 1; "
        f"if alive; then "
        f"echo 'process group survived SIGKILL' >&2; exit 1; fi; exit 0"
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _open_store(cfg: Any):
    """The Store this manager records jobs in.

    Resolved from cfg rather than injected because the dispatcher builds the
    manager lazily from cfg alone. Returns None rather than raising: a manager
    that cannot reach the database degrades to in-memory bookkeeping, which is
    the old behaviour — worse, but better than refusing to run a job at all.
    """
    try:
        from openai4s.store import get_store

        return get_store(cfg.db_path)
    except Exception:  # noqa: BLE001
        return None


def _discover_providers(skills_dir: Path) -> dict[str, dict]:
    """Map provider id -> {id, dir, provider_py, meta}. A provider is a
    ``remote-compute-<id>`` skill dir with a ``provider.json`` (declaring its
    ``id``) and a ``provider.py`` exporting ``PROVIDER``."""
    out: dict[str, dict] = {}
    if not skills_dir.exists():
        return out
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir() or not child.name.startswith("remote-compute-"):
            continue
        # ssh is a built-in family (no confined helper), not a byoc provider.
        if child.name == "remote-compute-ssh":
            continue
        pj = child / "provider.json"
        pp = child / "provider.py"
        if not (pj.exists() and pp.exists()):
            continue
        try:
            meta = json.loads(pj.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        pid = meta.get("id")
        if not pid:
            continue
        out[str(pid)] = {
            "id": str(pid),
            "dir": child,
            "provider_py": str(pp),
            "meta": meta,
        }
    return out


class ComputeManager:
    """One per session/kernel. Owns provider discovery and durable job
    bookkeeping. Thread-safe for the handful of ops the dispatcher drives.

    There is no background poller: ``result()`` is what probes the remote and
    harvests. A job nobody polls is never harvested, which is why the SDK
    tells the agent to call ``.result()`` again rather than to wait.
    """

    # The cancel script waits out a 10s SIGTERM grace period plus a second
    # after SIGKILL, so the ssh call needs headroom over that or the client
    # gives up before the remote has finished confirming.
    _CANCEL_TIMEOUT_S = 45

    # Hard host-side ceiling on one helper op. The helper's own poll budget
    # bounds its .phase loop, not a wedged provider SDK socket.
    _HELPER_TIMEOUT_S = 300.0

    def __init__(self, cfg: Any, store: Any = None, workspace: Any = None):
        self.cfg = cfg
        # The containment base for the direct scp surface. None falls back to
        # the process cwd, which is right for the CLI and wrong for nothing —
        # the Web path supplies the session workspace explicitly.
        self._workspace = workspace
        self._providers = _discover_providers(Path(cfg.skills_dir))
        self._install_id = self._resolve_install_id()
        self._store = store if store is not None else _open_store(cfg)
        # In-memory view of the durable records. The database is the source of
        # truth; this is a cache so the hot path does not re-read on every poll.
        self._jobs: dict[str, dict] = {}
        # byoc sandbox reuse: provider-id -> sandbox_id (warm container).
        self._sandboxes: dict[str, str] = {}
        # When each warm sandbox expires, as an absolute epoch. Kept beside
        # `_sandboxes` rather than derived per submit because a *reused*
        # container has already spent part of its life: the second job into a
        # one-hour sandbox created forty minutes ago has twenty minutes, not
        # another hour. Only an absolute deadline can express that.
        self._sandbox_deadlines: dict[str, float] = {}
        self._lock = threading.RLock()
        self._limit: int | None = None
        # Harvest into the *session workspace*, which is what both the docs
        # ("harvests out.tar.gz back into the workspace under hpc/<job_id>/")
        # and the bundled skills describe. It used to land under the global
        # data dir, so every documented success path — harvest, then
        # `host.save_artifact(path)` on a featured file — hit "path escapes the
        # workspace" from the Host file service, which only resolves inside the
        # session root. A job could not publish its own outputs.
        #
        # Without a workspace (the CLI) the data dir is still the right home.
        self._hpc_root = Path(workspace or cfg.data_dir) / "hpc"
        self._hpc_root.mkdir(parents=True, exist_ok=True)
        # Resolved once, at construction, before any cell has run: the value a
        # later harvest must still be contained by. `_hpc_root` lives inside the
        # kernel-writable workspace, so a cell can replace it — or pre-create a
        # per-job dir — with a symlink and redirect the harvest anywhere the
        # daemon can write. `_safe_harvest_dest` checks against this.
        self._hpc_root_real = os.path.realpath(self._hpc_root)
        # Remote outputs are *extracted and hashed* here, always under the data
        # dir and never in the kernel-writable workspace. The workspace copy
        # under `_hpc_root` exists only so `host.save_artifact` (which requires a
        # path inside the session root) can reach the files — but building the
        # manifest there let a cell plant `hpc/<job_id>/<declared-output>` before
        # polling, so an exit-0 job was marked succeeded with locally forged
        # bytes. The trusted manifest is built from this host-owned staging, and
        # only then is the verified tree published into the workspace.
        self._hpc_stage_root = Path(cfg.data_dir) / "hpc-stage"
        self._hpc_stage_root.mkdir(parents=True, exist_ok=True)
        self._rehydrate()
        self._confinement_mode = _confinement_mode()
        # See _confinement_gate: no host-side byoc boundary exists yet, so the
        # helper is never asked to assert one it cannot have.
        self._require_confinement = False

    def _safe_harvest_dest(self, job_id: str) -> Path:
        """The per-job harvest directory, proven to be under the hpc root.

        The hpc root sits inside the kernel-writable workspace, so a cell can
        turn ``hpc``, ``hpc/<job_id>``, or a child into a symlink before a
        harvest runs — and the subsequent ``mkdir``, ``safe_extract_tar`` and
        ``shutil.copy2`` would follow it, writing remote bytes anywhere the
        daemon can. ``safe_extract_tar`` guards the archive's *contents*; this
        guards the destination the contents land in.

        A symlink anywhere on the path is refused, and the resolved directory
        is re-checked for containment against the root resolved at construction
        — before any cell had run.
        """
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(job_id or "")).strip("._") or "job"
        if os.path.islink(self._hpc_root):
            raise ComputeError(
                "the harvest root is a symlink; refusing to write through it",
                "unsafe_archive",
            )
        self._hpc_root.mkdir(parents=True, exist_ok=True)
        dest = self._hpc_root / name
        if dest.is_symlink():
            raise ComputeError(
                f"the harvest directory for {name!r} is a symlink; refusing",
                "unsafe_archive",
            )
        dest.mkdir(parents=True, exist_ok=True)
        real_dest = os.path.realpath(dest)
        if os.path.commonpath((self._hpc_root_real, real_dest)) != self._hpc_root_real:
            raise ComputeError(
                f"the harvest directory for {name!r} escapes the hpc root",
                "unsafe_archive",
            )
        return dest

    def _host_staging_dir(self, job_id: str) -> Path:
        """A fresh, host-owned, unguessable directory to extract a harvest into.

        Under the data dir, never the workspace, and with a random suffix so a
        cell cannot pre-create it. This is where the *trusted* manifest is
        built — the workspace copy is only for `save_artifact` reachability.
        """
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(job_id or "")).strip("._") or "job"
        return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=str(self._hpc_stage_root)))

    def _publish_harvest(self, staging: Path, dest: Path) -> None:
        """Replace the workspace harvest dir wholesale with the verified tree.

        The manifest was already built from ``staging`` (host-owned), so this
        move is only about making the files reachable to ``save_artifact``.
        Replacing wholesale discards anything a cell planted under ``dest``.
        """
        try:
            if dest.exists():
                shutil.rmtree(dest)
        except OSError:
            pass
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(staging, dest)
        except OSError:
            # Cross-device (staging under data dir, dest under a workspace on
            # another mount): fall back to a copy, then drop the staging tree.
            shutil.copytree(staging, dest, dirs_exist_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    # --- durability -------------------------------------------------------
    def _rehydrate(self) -> None:
        """Load jobs that may still be live remotely.

        Without this a restart stranded every in-flight job: the ssh process
        kept running under nohup and the byoc sandbox kept billing, while
        `result()` answered "no such job" and `_live_count()` reset to zero —
        so the session would happily oversubscribe a provider that was still
        busy with work it had forgotten.
        """
        if self._store is None:
            return
        try:
            for record in self._store.live_compute_jobs():
                self._jobs[record["job_id"]] = self._from_record(record)
        except Exception:  # noqa: BLE001 - a broken record must not stop startup
            pass

    @staticmethod
    def _from_record(record: dict) -> dict:
        job = {
            "job_id": record["job_id"],
            "provider": record["provider"],
            "status": record.get("status") or "running",
            "outputs": record.get("outputs"),
            "idempotency_key": record.get("idempotency_key"),
            "receipt": record.get("receipt"),
            "termination_reason": record.get("termination_reason"),
            "reason": record.get("reason"),
            "recovered": True,
        }
        for key in ("alias", "workdir", "pid", "pgid", "sandbox_id"):
            if record.get(key):
                job[key] = record[key]
        return job

    def _persist(self, job_id: str, **fields: Any) -> None:
        """Non-terminal bookkeeping. Never used for an end state — see
        :meth:`_commit_terminal`, which must not swallow anything."""
        if self._store is None:
            return
        try:
            self._store.update_compute_job(job_id, **fields)
        except Exception:  # noqa: BLE001 - never fail an op on bookkeeping
            pass

    def _persist_terminal(self, job_id: str, **fields: Any) -> bool:
        """Write a terminal state for a job that has no in-memory row yet.

        Returns whether the durable write actually landed. Unlike ``_persist``
        it does not swallow a storage failure into a false success: the submit
        paths that use it fall back to an indeterminate, reconcilable state
        when it returns ``False``, rather than reporting a rejection the ledger
        never recorded. An ``IllegalTransition`` counts as landed — the row is
        already terminal, which is a stronger guarantee than the one asked for.
        """
        if self._store is None:
            return True
        try:
            self._store.update_compute_job(job_id, **fields)
            return True
        except states.IllegalTransition:
            return True
        except Exception:  # noqa: BLE001 - a swallowed terminal write is the bug
            return False

    def _commit_terminal(
        self,
        job: dict,
        status: str,
        *,
        event: str | None = None,
        event_payload: dict | None = None,
        **fields: Any,
    ) -> dict | None:
        """Write an end state to the ledger *before* anything else believes it.

        Returns ``None`` when the write landed, and the row that actually won
        when the compare-and-swap lost — having first re-synced the in-memory
        job to it. The caller must report *that* state.

        The repository's compare-and-swap was already correct. What was not:
        every caller here set ``job["status"]`` first and then handed the write
        to ``_persist``, which swallows exceptions. A cancel racing a result
        therefore left SQLite at ``succeeded`` while memory *and the answer
        returned to the caller* said ``cancelled``. Nothing downstream could
        detect the disagreement, because both halves were confident.

        So the order is inverted and the exception is not swallowed:

          * persist first — a status nothing durable holds is not a status;
          * only then update memory and emit the event, so an observer never
            sees a terminal state the ledger has not accepted;
          * a lost compare-and-swap re-reads the row and reports the conflict;
          * any *other* persistence failure propagates. A terminal state we
            could not record is a fact the caller has to hear: the row stays
            live and ``reconcile`` will keep surfacing it, which is the safe
            direction, but claiming success over a failed write is not.
        """
        job_id = job["job_id"]
        if self._store is None:
            job["status"] = status
            if event:
                self._event(job_id, event, event_payload)
            return None
        try:
            self._store.update_compute_job(job_id, status=status, **fields)
        except states.IllegalTransition:
            row = self._store.get_compute_job(job_id) or {}
            actual = row.get("status") or status
            job["status"] = actual
            if row.get("exit_code") is not None:
                job["exit_code"] = row["exit_code"]
            self._event(
                job_id,
                "terminal_conflict",
                {"requested": status, "actual": actual},
            )
            return row or {"job_id": job_id, "status": actual}
        job["status"] = status
        if event:
            self._event(job_id, event, event_payload)
        return None

    @staticmethod
    def _terminal_conflict_result(
        job: dict, requested: str, row: dict, output_files: list[str]
    ) -> dict:
        """A poll that harvested real files onto a job that had already ended.

        The harvest is kept — the bytes are on disk and throwing them away
        helps nobody — but the *verdict* is the one the ledger holds, not the
        one this poll computed.
        """
        return {
            "status": row.get("status"),
            "job_id": job["job_id"],
            "exit_code": row.get("exit_code"),
            "output_files": output_files,
            "conflict": {"requested": requested, "actual": row.get("status")},
            "hint": (
                "this job reached a terminal state before the poll completed; "
                "the recorded outcome is authoritative and was not overwritten"
            ),
        }

    def _event(self, job_id: str, kind: str, payload: dict | None = None) -> None:
        if self._store is None:
            return
        try:
            self._store.append_compute_job_event(job_id, kind, payload)
        except Exception:  # noqa: BLE001
            pass

    def _track_live(
        self, job_id: str, provider: str, status: str, **fields: Any
    ) -> None:
        """Keep a live-but-unresolved job in the in-process view too.

        ``_live_count`` reads ``self._jobs``, and only a *successful* submit
        ever wrote there — so an indeterminate submit occupied no concurrency
        slot until a restart happened to rehydrate it from the database. That
        is backwards: a job whose fate is unknown is exactly the one that may
        be consuming the provider's capacity, and the session would cheerfully
        oversubscribe on top of it.
        """
        with self._lock:
            job = self._jobs.setdefault(
                job_id, {"job_id": job_id, "provider": provider}
            )
            job["status"] = status
            job.update({k: v for k, v in fields.items() if v})

    def _fail_submit(
        self,
        job_id: str,
        exc: BaseException,
        *,
        provider: str,
        sandbox_id: str | None = None,
    ) -> None:
        """Give a submit that raised an honest terminal state, never `staging`.

        The distinction that matters is not failed-vs-succeeded but *definitely
        nothing happened* vs *something may be running out there*. Only the
        second costs money and needs reconciling, so anything short of an
        explicit provider rejection is recorded as ``unknown``:

        - the host deadline fired and we killed the helper mid-call, so the
          remote op may have landed after we stopped listening;
        - the helper died without writing a reply, same reasoning;
        - a sandbox was already created, which is a live billable resource
          regardless of why the later step failed.

        The sandbox id is persisted here for exactly that last case. Until now
        it lived only in the in-memory ``_sandboxes`` map, so a create that
        succeeded followed by a submit that failed left a sandbox nobody could
        name after a restart.

        The rejection test reads ``exc.indeterminate``, never the error *kind*.
        Kind answers "what went wrong"; only the raiser knows whether anything
        may nonetheless be running. Inferring from kind meant every
        ``transient`` was treated as a definite refusal — including the helper
        dying before it could write a reply, which is the single case most
        likely to have left work behind.
        """
        kind = getattr(exc, "error_kind", None)
        sandbox_id = sandbox_id or getattr(exc, "sandbox_id", None)
        rejected = (
            isinstance(exc, ComputeError)
            and not getattr(exc, "indeterminate", True)
            and not sandbox_id
        )
        fields: dict[str, Any] = {"reason": str(exc)}
        if sandbox_id:
            fields["sandbox_id"] = sandbox_id
            fields["receipt"] = sandbox_id
        # A rejection is only durably a rejection if the terminal write lands.
        # `_persist` swallows every storage error, so a locked or full SQLite
        # let submit report a definite `failed` while the durable claim stayed
        # `staging` — rehydrated as a live job on restart. If the terminal write
        # cannot be made, this is no longer a clean rejection: fall through to
        # the indeterminate path, which keeps the job live and reconcilable.
        if rejected and self._persist_terminal(
            job_id,
            status=states.FAILED,
            terminal_at=_now_ms(),
            termination_reason=states.REASON_SUBMIT_REJECTED,
            **fields,
        ):
            self._event(job_id, "submit_rejected", {"error": str(exc), "kind": kind})
        else:
            self._persist(
                job_id,
                status=states.UNKNOWN,
                termination_reason=states.REASON_SUBMIT_INDETERMINATE,
                **fields,
            )
            self._track_live(
                job_id,
                provider,
                states.UNKNOWN,
                sandbox_id=sandbox_id,
                termination_reason=states.REASON_SUBMIT_INDETERMINATE,
                reason=str(exc),
            )
            self._event(
                job_id,
                "submit_indeterminate",
                {"error": str(exc), "kind": kind, "sandbox_id": sandbox_id},
            )

    def _claim(self, provider: str, idempotency_key: str | None, outputs: Any) -> str:
        """Reserve a job row *before* the submit is attempted.

        The ordering is the whole point. A row written only after a successful
        submit would be missing for exactly the case that matters: the provider
        accepted the work and the response never came back. Reserving first
        means a crash anywhere in the submit path still leaves something to
        reconcile against, rather than an orphan that bills forever.
        """
        job_id = "job-" + uuid.uuid4().hex[:12]
        if self._store is None:
            return job_id
        if idempotency_key:
            existing = self._store.compute_job_by_idempotency_key(idempotency_key)
            if existing is not None:
                raise ComputeError(
                    f"a job for idempotency key {idempotency_key!r} already "
                    f"exists ({existing['job_id']}, status "
                    f"{existing.get('status')!r}); reconcile it instead of "
                    f"submitting again",
                    "duplicate_request",
                )
        try:
            self._store.create_compute_job(
                job_id=job_id,
                provider=provider,
                status=states.STAGING,
                idempotency_key=idempotency_key,
                outputs=outputs,
            )
        except Exception:  # noqa: BLE001 - degrade to in-memory rather than
            # refuse to run; a job we cannot record is still better than none.
            pass
        return job_id

    def reconcile(self, kw: dict | None = None) -> dict:
        """Report jobs that were live when the daemon last stopped.

        Deliberately does NOT resubmit anything. A job in `submitted` may or
        may not be running remotely, and guessing wrong costs either a
        duplicate charge or a lost result. The honest move is to surface each
        one with its receipt and let a poll — or a human — resolve it.
        """
        recovered = [job for job in self._jobs.values() if job.get("recovered")]
        entries = []
        for job in recovered:
            # A submit whose outcome was never established is the row that
            # actually costs money: the provider may have taken the work, and
            # nothing here can name what it created. Say so, rather than
            # offering the same "poll it" hint as an ordinary running job.
            indeterminate = job.get("termination_reason") in (
                states.REASON_SUBMIT_INDETERMINATE,
            )
            entry = {
                "job_id": job["job_id"],
                "provider": job["provider"],
                "status": job.get("status"),
                "receipt": job.get("receipt"),
                "hint": (
                    "poll with .result() to resolve; it may have finished "
                    "while the daemon was down"
                ),
            }
            if job.get("workdir"):
                entry["remote_workdir"] = job["workdir"]
            if job.get("sandbox_id"):
                entry["sandbox_id"] = job["sandbox_id"]
            if indeterminate:
                entry["orphan_risk"] = True
                entry["reason"] = job.get("reason")
                entry["hint"] = (
                    "this submit's outcome was never established — the "
                    "provider may have accepted the work. Inspect the receipt "
                    "or workdir before resubmitting; it is not retried "
                    "automatically because guessing wrong costs either a "
                    "duplicate charge or a lost result"
                )
            entries.append(entry)
        return {
            "recovered": entries,
            "count": len(entries),
            "orphan_risk_count": sum(1 for e in entries if e.get("orphan_risk")),
        }

    def job_history(self, kw: dict) -> dict:
        """The append-only event stream for one job."""
        if self._store is None:
            return {"job_id": kw.get("job_id"), "events": []}
        return {
            "job_id": kw["job_id"],
            "events": self._store.compute_job_events(kw["job_id"]),
        }

    def confinement_status(self) -> dict:
        """Machine-readable posture for the UI/status surface.

        The description itself lives in ``security.byoc_confinement`` so that
        every surface answers from the same self-test. `doctor` had its own
        opinion and contradicted this one.

        Never silent about a degradation: a user must not read "the helper ran"
        as "the helper was confined".
        """
        from openai4s.security import byoc_confinement

        status = byoc_confinement.posture(self._confinement_mode)
        if status["state"] == "disabled":
            status["detail"] = f"{_CONFINEMENT_ENV}=off: " + status["detail"]
        elif status["state"] == "unavailable":
            status["detail"] += (
                f". Remote compute remains a Prototype capability on this "
                f"host. Set {_CONFINEMENT_ENV}=enforce to refuse byoc ops "
                f"rather than run unconfined."
            )
        return status

    def _confinement_gate(self, pid: str) -> None:
        """Fail closed when the caller demanded a boundary we cannot establish.

        The helper ships a confinement probe (`expect_confined`) and an exit
        code for failing it; the host now supplies the boundary that probe
        looks for, where one exists. Where none does — see
        ``security/byoc_confinement`` for why Linux is an open decision rather
        than an omission — `enforce` refuses the op outright, because passing
        `expect_confined=1` into an unconfined helper would only make it kill
        itself with exit 71 while proving nothing.
        """
        if self._confinement_mode != "enforce":
            return
        from openai4s.security import byoc_confinement

        can_confine, reason = byoc_confinement.available()
        if not can_confine:
            raise ComputeError(
                f"byoc provider {pid!r} refused: {_CONFINEMENT_ENV}=enforce "
                f"requires verified helper confinement, which this host cannot "
                f"establish ({reason}). Fix the deployment or set "
                f"{_CONFINEMENT_ENV}=auto to accept unconfined execution.",
                "confinement_unavailable",
            )

    # --- discovery / capability ------------------------------------------
    def has_any_provider(self) -> bool:
        return bool(self._providers) or self._has_ssh_skill()

    def _has_ssh_skill(self) -> bool:
        """The ssh:* family is enabled by the remote-compute-ssh skill being
        installed (it ships the worked example + gate), not merely by the user
        happening to have an ~/.ssh/config."""
        return (Path(self.cfg.skills_dir) / "remote-compute-ssh").is_dir()

    def provider_caps(self) -> dict:
        return {
            f"byoc:{pid}": p["meta"].get("max_concurrent")
            for pid, p in self._providers.items()
        }

    @staticmethod
    def _resolve_install_id() -> str:
        """A stable per-install id used as the byoc sandbox owner tag. Persist
        it under the data dir so reconcile can find sandboxes across runs."""
        env = os.environ.get("OPENAI4S_INSTALL_ID")
        if env:
            return env
        path = Path.home() / ".openai4s" / "install-id"
        try:
            if path.exists():
                return path.read_text("utf-8").strip()
            path.parent.mkdir(parents=True, exist_ok=True)
            iid = uuid.uuid4().hex
            path.write_text(iid, encoding="utf-8")
            return iid
        except OSError:
            return uuid.uuid4().hex

    # --- provider family routing -----------------------------------------
    def _split(self, provider: str) -> tuple[str, str]:
        fam, _, rest = provider.partition(":")
        if fam not in ("byoc", "ssh") or not rest:
            raise ComputeError(
                f"unknown provider target {provider!r}; expected "
                f"'byoc:<id>' or 'ssh:<alias>'",
                "invalid_request",
            )
        return fam, rest

    def _byoc(self, pid: str) -> dict:
        p = self._providers.get(pid)
        if p is None:
            raise ComputeError(
                f"byoc provider {pid!r} is not configured (no "
                f"skills/remote-compute-{pid}/provider.json found)",
                "not_found",
            )
        return p

    # --- concurrency ------------------------------------------------------
    def _live_count(self) -> int:
        # `states.is_live` rather than a local tuple: this list and the one the
        # rehydrating SQL used disagreed about `staging`, so a row left there
        # by a crash was reported by reconcile forever while occupying no slot.
        return sum(1 for j in self._jobs.values() if states.is_live(j.get("status")))

    def set_concurrency(self, kw: dict) -> dict:
        with self._lock:
            self._limit = int(kw["max_concurrent"])
        return {"live": self._live_count(), "limit": self._limit}

    def status(self, kw: dict) -> dict:
        return {
            "live": self._live_count(),
            "limit": self._limit,
            "daemon_live": True,
            "provider_caps": self.provider_caps(),
        }

    # --- byoc helper transport -------------------------------------------
    def _run_helper(
        self,
        prov: dict,
        op: str,
        req: dict,
        creds: dict,
        stage: Path,
        expect_confined: bool | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Spawn the confined helper in oneshot mode for one op. The credential
        rides on the helper's stdin (never its environment); req/reply cross
        via the stage dir.

        ``expect_confined`` defaults to the manager's policy rather than to
        False: an op that does not ask for confinement has not established it,
        so leaving the default off silently downgraded every call site.
        """
        if expect_confined is None:
            expect_confined = self._require_confinement
        (stage / "req.json").write_text(
            json.dumps({**req, "stage": str(stage), "install_id": self._install_id}),
            encoding="utf-8",
        )
        # Wrap in the OS boundary when this host can supply one. The helper's
        # own probe is what turns the wrapping into evidence: it checks from
        # inside, before it reads a credential, and exits 71 without acting if
        # the boundary does not hold. Asking for the check without supplying
        # the boundary would only make it kill itself, so the two travel
        # together.
        base = [
            sys.executable,
            "-I",
            _HELPER_MAIN,
            "oneshot",
            prov["provider_py"],
            op,
            str(stage),
        ]
        # The unconfined form, kept whatever happens: it is what a degraded
        # `auto` falls back to, and reconstructing it from a wrapped argv would
        # be a parsing trick waiting to be wrong.
        plain_argv = [*base, "1" if expect_confined else "0"]
        argv, confined = plain_argv, False
        if self._confinement_mode != "off":
            from openai4s.security import byoc_confinement

            try:
                argv = byoc_confinement.wrap(
                    [*base, "1"],
                    stage,
                    read_paths=tuple(
                        byoc_confinement.runtime_read_paths(
                            (str(Path(prov["provider_py"]).resolve().parent),)
                        )
                    ),
                )
                confined = True
            except byoc_confinement.ConfinementUnavailable as exc:
                # `_confinement_gate` only guards submit, and `available()` now
                # runs a real self-test that can newly report unavailable at
                # wrap() time even after that gate passed (a cleared cache, a
                # backend that stopped working). Falling through to the plain
                # helper here would run the credential and the provider shim
                # unconfined despite `enforce`. Only `auto` may degrade; enforce
                # fails closed, on every op, not just submit.
                if self._confinement_mode == "enforce":
                    raise ComputeError(
                        f"provider helper for op {op!r} cannot run: "
                        f"{_CONFINEMENT_ENV}=enforce and no OS boundary could be "
                        f"established ({exc}); refusing to run unconfined",
                        "confinement_unavailable",
                        indeterminate=False,
                    )
                # `auto`: the documented visible degradation.
                argv = plain_argv
        # Scrub inherited secrets from the child env; the helper's own prologue
        # also drops the provider's secret_env_prefixes.
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("NGC_", "NVIDIA_", "HF_"))
        }
        if confined:
            # The anchor the helper's own check compares against. It cannot be
            # obtained from inside the boundary, which is the point: the value
            # has to come from the side that is not confined.
            from openai4s.security import byoc_confinement

            env.update(byoc_confinement.probe_environment())
        # A hard host-side deadline. The helper has its own poll budget, but a
        # wedged exec stream (or a provider SDK blocking on a socket) leaves it
        # with none — and a bare wait() would block the dispatcher forever.
        deadline = timeout if timeout is not None else self._HELPER_TIMEOUT_S
        proc = self._spawn_helper(argv, creds, env, deadline, op, stage)
        if proc.returncode == _EXIT_UNCONFINED and confined:
            # The host wrapped the helper and the helper looked from inside and
            # disagreed. It exits before reading the credential and before any
            # provider call, so nothing happened and there is nothing to
            # reconcile — which is what makes a retry safe here and nowhere
            # else in this module.
            if self._confinement_mode == "enforce":
                raise ComputeError(
                    f"provider helper for op {op!r} refused to run: the OS "
                    f"boundary this host applied did not hold when the helper "
                    f"checked it from inside, and "
                    f"{_CONFINEMENT_ENV}=enforce does not accept unconfined "
                    f"execution",
                    "confinement_unavailable",
                    indeterminate=False,
                )
            self._audit(
                "compute_confinement_degraded",
                op=op,
                detail="helper reported the applied boundary did not hold",
            )
            proc = self._spawn_helper(
                [*plain_argv[:-1], "0"], creds, env, deadline, op, stage
            )
        reply_path = stage / "reply.json"
        if not reply_path.exists():
            # The helper died between doing the work and reporting it. It may
            # have created the sandbox, or submitted the job, or neither — and
            # this used to be classified `transient`, which _fail_submit read
            # as an explicit provider rejection and persisted as terminal
            # `failed`. A job the provider had already accepted then became
            # invisible: reconcile skips terminal rows, so it kept running and
            # kept billing with nothing left that would ever look for it.
            raise self._helper_error(
                stage,
                f"provider helper for op {op!r} exited (rc={proc.returncode}) "
                f"without a reply; the remote operation may or may not have "
                f"taken effect",
                "unknown_state",
                indeterminate=True,
            )
        reply = json.loads(reply_path.read_text("utf-8"))
        if not reply.get("ok"):
            # A written reply is the helper speaking for itself: it reached the
            # provider and the provider said no. That is a definite rejection —
            # *unless* the failure is transient. The resident protocol carries
            # only ok/kind/msg, never `indeterminate`, and a `transient` failure
            # is precisely the one whose effect is uncertain: a `docker run`
            # that timed out may already have created a container. Inferring
            # indeterminacy from the transient kind is what keeps that container
            # reconcilable instead of recording a terminal `failed` over a
            # resource that may still be billing.
            kind = reply.get("kind") or "transient"
            raise self._helper_error(
                stage,
                reply.get("msg") or "provider op failed",
                kind,
                indeterminate=bool(reply.get("indeterminate")) or kind == "transient",
            )
        return reply

    def _spawn_helper(
        self,
        argv: list[str],
        creds: dict,
        env: dict,
        deadline: float,
        op: str,
        stage: Path,
    ) -> Any:
        """One helper process, credential on stdin, under a hard deadline."""
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, env=env)
        proc.stdin.write((json.dumps({"op": "auth", **creds}) + "\n").encode("utf-8"))
        proc.stdin.close()
        try:
            proc.wait(timeout=deadline)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            raise self._helper_error(
                stage,
                f"provider helper for op {op!r} exceeded the {deadline}s host "
                f"deadline and was killed; the remote operation may or may not "
                f"have taken effect",
                "unknown_state",
                indeterminate=True,
            )
        return proc

    @staticmethod
    def _helper_error(
        stage: Path, msg: str, kind: str, *, indeterminate: bool
    ) -> ComputeError:
        """Build the error, carrying any sandbox the stage dir still names.

        ``_op_create`` writes ``stage/sandbox_id`` the instant the provider
        returns one — before the ownership read-back, before ``reply.json`` —
        precisely so a helper that dies mid-op still leaves the host able to
        name what it created. Nothing read that file, so a create that landed
        and then crashed produced an unnameable, unterminatable, billing
        sandbox. The helper removes the file again once it has *confirmed* the
        sandbox is gone, so its presence means "may still exist".
        """
        error = ComputeError(msg, kind, indeterminate=indeterminate)
        try:
            observed = (stage / "sandbox_id").read_text("utf-8").strip()
        except (OSError, ValueError):
            observed = ""
        if observed:
            error.sandbox_id = observed
            # A live resource we can name is never a clean rejection.
            error.indeterminate = True
        return error

    def _provider_creds(self, prov: dict) -> dict:
        """Collect the provider's declared secret env vars into the auth
        payload the helper reads from stdin. The provider.json's
        ``helperEnv``/``secret_env`` lists which env keys to forward."""
        keys = prov["meta"].get("secret_env") or []
        return {k: os.environ[k] for k in keys if k in os.environ}

    # --- submit -----------------------------------------------------------
    def submit(self, kw: dict) -> dict:
        provider = kw["provider"]
        fam, rest = self._split(provider)
        with self._lock:
            if self._limit is not None and self._live_count() >= self._limit:
                raise ComputeError(
                    "session concurrency limit reached",
                    "session_concurrency_full",
                    {"live": self._live_count(), "limit": self._limit},
                )
        if fam == "ssh":
            return self._submit_ssh(rest, kw)
        return self._submit_byoc(rest, kw)

    def _stage_inputs(
        self, stage: Path, inputs: list | None, command: str, timeout_s: int
    ) -> Path:
        """Build the in.tar.gz the helper untars into /work: the wrapper, the
        run.sh (command), and every staged input flat in the root."""
        work = stage / "work"
        work.mkdir()
        wrapper = (_TMPL_DIR / "wrapper.sh.tmpl").read_text("utf-8")
        run = (
            (_TMPL_DIR / "run.sh.tmpl")
            .read_text("utf-8")
            .replace("{{COMMAND}}", command)
        )
        (work / "_openai4s_wrapper.sh").write_text(wrapper, encoding="utf-8")
        (work / "run.sh").write_text(run, encoding="utf-8")
        for inp in inputs or []:
            src = inp.get("src") or inp.get("remote_path")
            if not src:
                continue
            # A source that does not exist used to be skipped in silence, so
            # the job ran to completion against missing data and reported
            # success. Refusing here is the difference between a failed job
            # and a wrong result nobody questions.
            src_path = self._safe_local_path(src, label="input src", must_exist=True)
            dst = _safe_stage_name(
                inp.get("dst_filename") or Path(src).name, label="input dst_filename"
            )
            shutil.copy2(src_path, work / dst)
        tgz = stage / "in.tar.gz"
        with tarfile.open(tgz, "w:gz") as tf:
            tf.add(work, arcname=".")
        return tgz

    def _submit_byoc(self, pid: str, kw: dict) -> dict:
        prov = self._byoc(pid)
        self._confinement_gate(pid)
        creds = self._provider_creds(prov)
        job_id = self._claim(
            f"byoc:{pid}", kw.get("idempotency_key"), kw.get("outputs")
        )
        timeout_s = int(kw.get("timeout_seconds") or 14400)
        sid: str | None = None
        try:
            with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
                stage = Path(td)
                # 1. create (or reuse) the sandbox.
                sid = self._sandboxes.get(pid) or kw.get("reuse_job_id")
                if not sid or not self._sandboxes.get(pid):
                    spec = (kw.get("provider_params") or {}).get(pid, {})
                    tags = {
                        "openai4s-session": self._install_id,
                        "openai4s-job": job_id,
                    }
                    rep = self._run_helper(
                        prov,
                        "create",
                        {"spec": spec, "tags": tags, "app_name": "openai4s"},
                        creds,
                        stage,
                        expect_confined=False,
                    )
                    sid = rep["sandbox_id"]
                    self._sandboxes[pid] = sid
                    # Stamp the container's expiry the moment it exists, so a
                    # later job reusing it inherits the time already spent.
                    lifetime = _sandbox_lifetime_s(spec)
                    if lifetime > 0:
                        self._sandbox_deadlines[pid] = time.time() + lifetime
                    else:
                        self._sandbox_deadlines.pop(pid, None)
                # 2. stage inputs then submit.
                self._stage_inputs(stage, kw.get("inputs"), kw["command"], timeout_s)
                # The wrapper implements a watchdog that stops the job with
                # `harvest_margin_s` to spare so its outputs can still be
                # staged, and the helper forwards these straight through — but
                # nothing on the host ever produced them, so the watchdog was
                # never armed and a container could be reclaimed mid-job,
                # taking the results with it.
                deadline = self._sandbox_deadlines.get(pid)
                submit_req: dict[str, Any] = {
                    "sandbox_id": sid,
                    "timeout": timeout_s,
                    "harvest_margin_s": HARVEST_MARGIN_S,
                    "term_grace_s": TERM_GRACE_S,
                }
                if deadline:
                    submit_req["sandbox_deadline_epoch"] = int(deadline)
                self._run_helper(prov, "submit", submit_req, creds, stage)
        except BaseException as exc:
            # Without this the row stays at `staging` forever: no terminal
            # state, no event, and -- worse -- a sandbox the provider may
            # already be billing for, whose id lived only in the in-memory
            # `_sandboxes` map and vanished with the process. The ssh arm has
            # had this discipline since it was written (see `_submit_ssh`);
            # the byoc arm never did.
            self._fail_submit(job_id, exc, provider=f"byoc:{pid}", sandbox_id=sid)
            raise
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "provider": f"byoc:{pid}",
                "sandbox_id": sid,
                "status": "running",
                "outputs": kw.get("outputs"),
                "creds": bool(creds),
            }
        # The sandbox id is the receipt — it is what reconcile/terminate need to
        # reach this work after a restart, and what stops an orphaned sandbox
        # from billing unnoticed.
        self._persist(
            job_id,
            status=states.RUNNING,
            sandbox_id=sid,
            receipt=sid,
            submitted_at=_now_ms(),
        )
        self._event(job_id, "submitted", {"sandbox_id": sid})
        return {
            "job_id": job_id,
            "status": "running",
            "concurrency": {"live": self._live_count(), "limit": self._limit},
            "egress": prov["meta"].get("egress"),
        }

    # --- ssh --------------------------------------------------------------
    def _submit_ssh(self, alias: str, kw: dict) -> dict:
        job_id = self._claim(
            f"ssh:{alias}", kw.get("idempotency_key"), kw.get("outputs")
        )
        workdir = f"~/.openai4s-jobs/{job_id}"
        script = kw["command"]
        # The job body runs under a wrapper whose only added responsibility is
        # to record the terminal exit code. Without a .rc on disk a finished
        # job is indistinguishable from a killed one, and _result_ssh has no
        # honest state to report — so this write is what makes a *failed* ssh
        # job observable at all. It lands via .rc.tmp + mv so a reader never
        # sees a half-written code (mv is atomic within one filesystem).
        # A deadline the job actually carries. `timeout_seconds` was accepted
        # and then never read, so an ssh job ran until the host reclaimed it.
        # `timeout` is asked for by name rather than assumed: a host without it
        # would otherwise silently run an unbounded job while the caller
        # believes a limit is in force.
        timeout_s = int(kw.get("timeout_seconds") or 0)
        body = "bash run.sh"
        marker = ""
        if timeout_s > 0:
            # -k gives the job a grace period to flush before SIGKILL.
            # `gtimeout` is the same binary under the name Homebrew's coreutils
            # installs, so a macOS host is not silently denied a deadline.
            body = (
                "_o4s_to=$(command -v timeout || command -v gtimeout); "
                f'"$_o4s_to" -k 10 -s TERM {timeout_s} bash run.sh'
            )
            # The deadline writes its OWN marker, and the host reads that
            # marker rather than sniffing the exit code. `timeout` reports
            # expiry as 124, but 124 is also just a number a command may exit
            # with — and with no deadline armed at all, a plain `exit 124` was
            # being reported to the user as `timed_out`, sending them to look
            # for a walltime that was never set. The wall-clock test is what
            # separates "the deadline fired" from "the command happened to
            # return the same code": only a run that reached its budget can
            # have been ended by it. (A command that exits 124 in the final
            # second of its own budget remains indistinguishable; that is
            # inherent, and is the same contract the byoc wrapper documents.)
            marker = (
                f'if {{ [ "$rc" -eq {_TIMEOUT_EXIT_CODE} ] || [ "$rc" -eq 137 ]; }} '
                f'&& [ "$_o4s_wall" -ge {max(timeout_s - 1, 0)} ]; '
                f"then : > .timeout; fi; "
            )
        # The job records its own identity before it does anything else.
        # `$!` is a pid, and in the non-interactive login shell that `ssh host
        # cmd` actually gets — dash, ash, or bash without job control — it is
        # NOT the process group id: `set -m` does not enable job control there,
        # so the background job simply inherits the login shell's group.
        # Cancellation signalled `-$!`, found no such group, and on several
        # shells still exited 0 — reporting a freed allocation over a command
        # tree that kept running. Reading the real pgid back off the host is
        # the only thing that makes the group we signal verifiable.
        inner = (
            "_o4s_pg=$(ps -o pgid= -p $$ 2>/dev/null | tr -d ' \\t\\n'); "
            'printf "%s %s" "$$" "$_o4s_pg" > .pgid.tmp; mv -f .pgid.tmp .pgid; '
            "_o4s_t0=$(date +%s); "
            f"{body} > stdout.log 2> stderr.log; rc=$?; "
            "_o4s_wall=$(( $(date +%s) - _o4s_t0 )); "
            f"{marker}"
            # .rc last: a reader that sees an exit code can trust every marker
            # beside it is already settled.
            'printf "%s" "$rc" > .rc.tmp; mv -f .rc.tmp .rc'
        )
        # setsid puts the job in a session of its own, so the group we signal
        # contains the job and nothing else. It is absent on stock macOS, which
        # is why it is probed rather than required — the read-back above is
        # what makes cancellation correct either way.
        #
        # The braces are load-bearing. `&` binds looser than `&&`, so
        # `mkdir && cd && cat > run.sh && nohup ... & echo $!` makes the WHOLE
        # and-list asynchronous — and POSIX assigns an async list's stdin to
        # /dev/null. `cat` then read nothing, run.sh was written empty, and
        # `bash run.sh` exited 0 without ever running the job. Grouping keeps
        # `&` scoped to the nohup alone, so `cat` stays in the foreground and
        # actually receives the script over the ssh channel.
        # Checked before anything is written, so a host without it fails the
        # submit loudly instead of running an unbounded job while the caller
        # believes a limit is in force.
        guard = (
            "{ command -v timeout >/dev/null 2>&1 || "
            "command -v gtimeout >/dev/null 2>&1; } || "
            "{ echo 'openai4s: neither timeout(1) nor gtimeout(1) is on this "
            "host, so timeout_seconds cannot be honoured' >&2; exit 127; } && "
            if timeout_s > 0
            else ""
        )
        launch = (
            "_o4s_ss=; command -v setsid >/dev/null 2>&1 && _o4s_ss=setsid; "
            f"nohup $_o4s_ss bash -c {shlex.quote(inner)} >/dev/null 2>&1 & "
            "_o4s_bg=$!; _o4s_n=0; "
            'while [ ! -s .pgid ] && [ "$_o4s_n" -lt 10 ]; do '
            "sleep 1; _o4s_n=$(( _o4s_n + 1 )); done; "
            "set -- $(cat .pgid 2>/dev/null); "
            f'printf "{_SSH_SUBMIT_TAG} %s %s\\n" "${{1:-$_o4s_bg}}" "${{2:-}}"'
        )
        remote = (
            f"mkdir -p {workdir} && cd {workdir} && "
            f"{guard}"
            f"cat > run.sh && rm -f .rc .rc.tmp .pgid .pgid.tmp .timeout && "
            f"{{ {launch}; }}"
        )
        try:
            proc = subprocess.run(
                ["ssh", alias, remote],
                input=script.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            # We do not know whether the remote shell ran. The claim row stays
            # at `staging` with the workdir recorded, which is what makes this
            # reconcilable instead of an orphan.
            self._persist(
                job_id,
                status=states.UNKNOWN,
                workdir=workdir,
                reason=str(e),
                termination_reason=states.REASON_SUBMIT_INDETERMINATE,
            )
            self._track_live(
                job_id,
                f"ssh:{alias}",
                states.UNKNOWN,
                alias=alias,
                workdir=workdir,
                termination_reason=states.REASON_SUBMIT_INDETERMINATE,
                reason=str(e),
            )
            self._event(job_id, "submit_indeterminate", {"error": str(e)})
            raise ComputeError(
                f"ssh submit failed: {e}", "unknown_state", indeterminate=True
            )
        if proc.returncode != 0:
            self._persist(
                job_id,
                status=states.FAILED,
                reason="ssh submit rejected",
                workdir=workdir,
                termination_reason=states.REASON_SUBMIT_REJECTED,
                terminal_at=_now_ms(),
            )
            self._event(job_id, "submit_rejected", {"rc": proc.returncode})
            raise ComputeError(
                proc.stderr.decode("utf-8", "replace") or "ssh submit failed",
                "transient",
                indeterminate=False,
            )
        pid, pgid = _parse_ssh_submit_ack(proc.stdout.decode("utf-8", "replace"))
        if not pid:
            # The shell exited 0 but said nothing we can identify the job by.
            # Anything may be running out there under a pid we never learned.
            self._persist(
                job_id,
                status=states.UNKNOWN,
                workdir=workdir,
                reason="ssh submit produced no job acknowledgement",
                termination_reason=states.REASON_SUBMIT_INDETERMINATE,
            )
            self._track_live(
                job_id,
                f"ssh:{alias}",
                states.UNKNOWN,
                alias=alias,
                workdir=workdir,
                termination_reason=states.REASON_SUBMIT_INDETERMINATE,
                reason="ssh submit produced no job acknowledgement",
            )
            self._event(job_id, "submit_indeterminate", {"stdout": proc.stdout[:200]})
            raise ComputeError(
                f"ssh submit to {alias} exited 0 without acknowledging a job "
                f"id; something may be running in {workdir}",
                "unknown_state",
                indeterminate=True,
            )
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "provider": f"ssh:{alias}",
                "alias": alias,
                "workdir": workdir,
                "status": "running",
                "pid": pid,
                "pgid": pgid,
                "outputs": kw.get("outputs"),
            }
        # The remote pid is the provider's receipt: evidence the job exists out
        # there, independent of anything this process chose to believe.
        self._persist(
            job_id,
            status=states.RUNNING,
            alias=alias,
            workdir=workdir,
            pid=pid,
            pgid=pgid,
            receipt=pid,
            submitted_at=_now_ms(),
        )
        self._event(job_id, "submitted", {"pid": pid, "pgid": pgid, "workdir": workdir})
        return {
            "job_id": job_id,
            "status": "running",
            "remote_workdir": workdir,
            "concurrency": {"live": self._live_count(), "limit": self._limit},
        }

    # --- result / harvest -------------------------------------------------
    def result(self, kw: dict) -> dict:
        job_id = kw["job_id"]
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise ComputeError(f"no such job {job_id!r}", "not_found")
        fam, rest = self._split(job["provider"])
        if fam == "ssh":
            return self._result_ssh(job)
        return self._result_byoc(job)

    def _result_byoc(self, job: dict) -> dict:
        prov = self._byoc(job["provider"].split(":", 1)[1])
        creds = self._provider_creds(prov)
        with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
            stage = Path(td)
            rep = self._run_helper(
                prov,
                "wait",
                {"sandbox_id": job["sandbox_id"], "poll_seconds": 5},
                creds,
                stage,
            )
            if not rep.get("ready"):
                return {
                    "status": "running",
                    "job_id": job["job_id"],
                    "hint": "job still running — call .result() again later",
                }
            exit_code = rep.get("job_exit_code")
            phase_err = rep.get("phase_read_error")
            if exit_code is None:
                # The helper reached a ready state but could not read a
                # terminal exit code out of .phase. Previously this fell
                # through to "done" because None is falsy — the single worst
                # false-success in this module.
                #
                # Not cached onto the job, for the same reason as the ssh path:
                # `unknown` is unresolved, not terminal, so a later poll must
                # stay free to resolve it. It also keeps the job inside
                # _live_count() — a job we cannot account for is conservatively
                # still occupying its slot, rather than freeing capacity we
                # have no evidence is free.
                return {
                    "status": "unknown",
                    "job_id": job["job_id"],
                    "exit_code": None,
                    "error_kind": "unknown_state",
                    "reason": phase_err
                    or "provider reported the job ready without a terminal exit code",
                    "stdout_tail": rep.get("stdout_tail", ""),
                    "stderr_tail": rep.get("stderr_tail", ""),
                    "left_on_remote": False,
                    "hint": (
                        "the job's outcome could not be established; treat any "
                        "harvested output as unverified"
                    ),
                }
            harvested: Path | None = None
            try:
                entries, harvested = self._harvest(job["job_id"], stage)
                harvest_error = None
            except UnsafeArchiveError as e:
                entries = []
                harvest_error = str(e)

        # `entries` was built from the host-owned staging dir, so reconcile is
        # immune to a planted file. Only now is the verified tree published into
        # the workspace copy that `save_artifact` can reach.
        dest = self._safe_harvest_dest(job["job_id"])
        if harvested is not None:
            self._publish_harvest(harvested, dest)
        out_files = [str(dest / item["path"]) for item in entries]
        featured, missing = manifest.reconcile(entries, job.get("outputs"))
        featured_files = [str(dest / rel) for rel in featured]

        if rep.get("deadline_fired") or rep.get("job_timeout_fired"):
            status = states.TIMED_OUT
        elif exit_code == 0:
            status = states.SUCCEEDED
        else:
            status = states.FAILED
        # Exited 0 but nothing verifiable came back. Calling that a success is
        # the single worst outcome available here, so it is a failure with the
        # cause recorded rather than a status of its own. `phase_err` is the
        # wrapper's own tar/mv losing the outputs; `missing` is the job simply
        # not producing what it promised, which nothing checked before; and
        # `unverified_files` is a file that arrived but could not be read, so
        # there is no content hash standing behind it.
        unverified_files = manifest.unverified(entries)
        unverified = bool(phase_err or harvest_error or missing or unverified_files)
        if unverified and status == states.SUCCEEDED:
            status = states.FAILED
        notes = []
        if missing:
            notes.append(f"declared outputs never arrived: {', '.join(missing)}")
        if unverified_files:
            notes.append(
                f"harvested but unreadable, so unverifiable: "
                f"{', '.join(unverified_files)}"
            )
        conflict = self._commit_terminal(
            job,
            status,
            event=status,
            event_payload={"exit_code": exit_code},
            exit_code=exit_code,
            terminal_at=_now_ms(),
            reason=phase_err or harvest_error or "; ".join(notes) or "",
            termination_reason=(
                states.REASON_OUTPUTS_UNVERIFIED if unverified else None
            ),
            artifact_manifest=entries,
            integrity_sha256=manifest.manifest_digest(entries),
        )
        if conflict is not None:
            return self._terminal_conflict_result(job, status, conflict, out_files)
        job["exit_code"] = exit_code
        result = {
            "status": status,
            "exit_code": exit_code,
            "output_files": out_files,
            "featured_files": featured_files,
            "artifact_manifest": entries,
            "integrity_sha256": manifest.manifest_digest(entries),
            "stdout_tail": rep.get("stdout_tail", ""),
            "stderr_tail": rep.get("stderr_tail", ""),
            "job_wall_s": rep.get("job_wall_s"),
            "left_on_remote": False,
        }
        if phase_err:
            result["phase_read_error"] = phase_err
        if harvest_error:
            result["harvest_error"] = harvest_error
            result["error_kind"] = "unsafe_archive"
        if missing:
            result["unharvested_outputs"] = missing
        if unverified_files:
            result["unverified_files"] = unverified_files
        stay_remote = manifest.remote_patterns(job.get("outputs"))
        if stay_remote:
            # Stated rather than silently ignored. The provider wrapper tars
            # the whole `out/` tree from inside the sandbox, so this transport
            # cannot keep a declared file behind — and a caller who asked for
            # that has to hear it did not happen, not discover it later from a
            # file listing.
            result["residency_unenforced"] = {
                "patterns": stay_remote,
                "note": (
                    "residency: remote is enforced on the ssh transport; this "
                    "provider archives the whole out/ directory in-sandbox, so "
                    "these files were harvested anyway"
                ),
            }
        return result

    def _harvest(self, job_id: str, stage: Path) -> tuple[list[dict], Path | None]:
        """Unpack the remote's out.tar.gz and record it.

        Returns ``(entries, harvested)``: the manifest — path, size and sha256
        per file — and the host-owned directory the bytes were extracted into,
        or ``None`` when there was nothing to harvest. The manifest is built
        from that host-owned directory, never the kernel-writable workspace, so
        a cell cannot plant a declared output and have it counted as produced.
        The caller publishes ``harvested`` into the workspace afterwards.

        Raises UnsafeArchiveError if the archive is hostile; the caller must
        treat that as a failed harvest, never a partial success.
        """
        tgz = stage / "out.tar.gz"
        if not tgz.exists():
            # No archive at all. Previously this returned an empty list with no
            # error and the caller went on to call the job succeeded.
            return [], None
        harvested = self._host_staging_dir(job_id)
        safe_extract_tar(tgz, harvested)
        return manifest.build_manifest(harvested), harvested

    def _result_ssh(self, job: dict) -> dict:
        alias, workdir = job["alias"], job["workdir"]
        pid = job.get("pid") or ""
        # Probe ordering matters. `kill -0` is asked first because the wrapper
        # writes .rc *before* it exits: a live pid is authoritatively running,
        # and a dead pid means .rc is already durable if it will ever be. The
        # reverse order would race a just-finished job into `unknown`.
        # The `.timeout` marker rides back with the exit code: the wrapper
        # writes it only when the deadline it was given is what ended the run,
        # so the host never has to infer a walltime kill from the number 124 —
        # which a command is free to exit with on its own, deadline or not.
        probe = (
            f"if kill -0 {shlex.quote(pid)} 2>/dev/null; then echo RUNNING; "
            f"elif [ -f {workdir}/.rc ]; then "
            f'printf "RC %s %s\\n" "$(cat {workdir}/.rc)" '
            f'"$([ -f {workdir}/.timeout ] && echo TIMEOUT || echo -)"; '
            f"else echo NORC; fi"
        )
        try:
            check = subprocess.run(
                ["ssh", alias, probe], capture_output=True, timeout=30
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return self._ssh_unknown(job, f"status probe failed to run: {e}")
        if check.returncode != 0:
            # We never reached the host (network, auth, host down). The job's
            # real state is untouched by our inability to observe it, so this
            # is explicitly not a terminal answer.
            return self._ssh_unknown(
                job,
                "status probe exited "
                f"{check.returncode}: "
                f"{check.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}",
            )
        out = check.stdout.decode("utf-8", "replace").strip()
        if out == "RUNNING":
            return {"status": "running", "job_id": job["job_id"]}
        if out == "NORC":
            # The process is gone but left no exit code: OOM-killed, host
            # rebooted, or SIGKILLed. Reporting success here is exactly the
            # false-success this state exists to prevent.
            return self._ssh_unknown(
                job,
                "remote process is no longer alive but wrote no exit code "
                "(killed, evicted, or the host restarted)",
            )
        parts = out.split()
        if len(parts) >= 2 and parts[0] == "RC":
            rc_text, timed_out = parts[1], (len(parts) > 2 and parts[2] == "TIMEOUT")
        else:
            # A job submitted before the marker existed still answers with a
            # bare exit code. Nothing claims a deadline fired for those.
            rc_text, timed_out = out, False
        try:
            exit_code = int(rc_text)
        except ValueError:
            return self._ssh_unknown(job, f"unparseable exit code {rc_text!r}")

        # Extract and hash under a host-owned staging dir, never the kernel-
        # writable workspace: a cell could otherwise plant `hpc/<job_id>/<file>`
        # before polling and have `build_manifest` count it as a produced
        # output, so an exit-0 job was marked succeeded with forged bytes.
        staging = self._host_staging_dir(job["job_id"])
        stay_remote = manifest.remote_patterns(job.get("outputs"))
        harvest_error, oversized, stayed = self._harvest_ssh(
            alias, workdir, staging, stay_remote
        )
        entries = manifest.build_manifest(staging)
        featured, missing = manifest.reconcile(entries, job.get("outputs"))
        unverified_files = manifest.unverified(entries)
        # The manifest is trusted (built from staging); publish it into the
        # workspace copy `save_artifact` can reach, replacing any planted files.
        dest = self._safe_harvest_dest(job["job_id"])
        self._publish_harvest(staging, dest)

        if timed_out:
            # The wrapper's own marker, not the exit code. `timeout` reports
            # expiry as 124, but so does any command that chooses to exit 124 —
            # and with no deadline armed at all there is nothing for a walltime
            # kill to have come from, so reading 124 as `timed_out` sent the
            # caller looking for a limit that was never set.
            status = states.TIMED_OUT
        elif exit_code == 0:
            status = states.SUCCEEDED
        else:
            status = states.FAILED
        if harvest_error:
            # The job's own verdict is known and trustworthy; only our copy of
            # its files is incomplete. Keep the verdict, but never claim a
            # clean harvest we did not get.
            status = states.FAILED
        # A promise the harvest cannot account for — either the file never
        # arrived, or it arrived unreadable and so carries no hash. Reporting
        # success over either is the false success the manifest exists for.
        unaccounted = bool(missing or unverified_files)
        if unaccounted and status == states.SUCCEEDED:
            status = states.FAILED
        notes = []
        if missing:
            notes.append(
                f"declared outputs were not harvested (still on "
                f"{alias}:{workdir}): {', '.join(missing)}"
            )
        if unverified_files:
            notes.append(
                f"harvested but unreadable, so unverifiable: "
                f"{', '.join(unverified_files)}"
            )
        conflict = self._commit_terminal(
            job,
            status,
            event=status,
            event_payload={"exit_code": exit_code},
            exit_code=exit_code,
            terminal_at=_now_ms(),
            reason=harvest_error or "; ".join(notes) or "",
            termination_reason=(
                states.REASON_OUTPUTS_UNVERIFIED
                if (harvest_error or unaccounted)
                else None
            ),
            artifact_manifest=entries,
            integrity_sha256=manifest.manifest_digest(entries),
        )
        if conflict is not None:
            return self._terminal_conflict_result(
                job,
                status,
                conflict,
                [str(dest / item["path"]) for item in entries],
            )
        job["exit_code"] = exit_code
        result = {
            "status": status,
            "exit_code": exit_code,
            "output_files": [str(dest / item["path"]) for item in entries],
            "featured_files": [str(dest / rel) for rel in featured],
            "artifact_manifest": entries,
            "integrity_sha256": manifest.manifest_digest(entries),
            "remote_workdir": workdir,
            # The workdir itself is never deleted by a poll, so the originals
            # are still there — but the declared outputs are now here too.
            "left_on_remote": True,
        }
        left_behind = [
            {"path": rel, "uri": f"{alias}:{workdir}/{rel}", "reason": "threshold"}
            for rel in oversized
        ] + [
            {"path": rel, "uri": f"{alias}:{workdir}/{rel}", "reason": "residency"}
            for rel in stayed
        ]
        if left_behind:
            result["left_on_remote_files"] = left_behind
        if harvest_error:
            result["harvest_error"] = harvest_error
        if missing:
            result["unharvested_outputs"] = missing
        if unverified_files:
            result["unverified_files"] = unverified_files
        return result

    def _ssh_unknown(self, job: dict, reason: str) -> dict:
        """Terminal-shaped answer for a job whose real state we cannot observe.

        `unknown` is deliberately distinct from `failed`: the job may well have
        succeeded. It means *we have no evidence either way*, and the caller
        must reconcile rather than assume. It is never cached onto the job, so
        a later poll can still resolve it.
        """
        return {
            "status": "unknown",
            "job_id": job["job_id"],
            "exit_code": None,
            "error_kind": "unknown_state",
            "reason": reason,
            "remote_workdir": job.get("workdir"),
            "left_on_remote": True,
            "hint": (
                "the remote job's outcome could not be established — inspect "
                f"{job.get('workdir')} on the host before re-submitting, as "
                "the original job may still have run to completion"
            ),
        }

    def _harvest_ssh(
        self,
        alias: str,
        workdir: str,
        dest: Path,
        exclude_patterns: list[str] | None = None,
    ) -> tuple[str | None, list[str], list[str]]:
        """Pull the job's whole work directory back.

        Returns ``(error, oversized, stayed)``: an error string or None, the
        relative paths left behind for being over the per-file ceiling, and the
        ones left behind because the caller declared ``residency: remote``.

        This used to copy ``stdout.log`` and ``stderr.log`` and nothing else,
        while ``submit_job`` accepted an ``outputs`` declaration and the
        bundled skill's worked example declares one. Every declared pattern
        therefore matched nothing, ``reconcile`` reported it missing, and a job
        that had exited 0 having written exactly what it promised was forced to
        ``failed``. The documented submit → poll → harvest path could not
        succeed at all on this transport.

        The archive is built remotely and extracted through the same
        hostile-input-safe extractor the byoc path uses, because the bytes come
        from a machine we do not control either way.
        """
        patterns = list(exclude_patterns or [])
        script = _ssh_harvest_script(workdir, HARVEST_MAX_FILE_BYTES, patterns)
        try:
            proc = subprocess.run(
                ["ssh", alias, script], capture_output=True, timeout=300
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return f"harvest failed to run on {alias}: {e}", [], []
        if proc.returncode != 0:
            return (
                (
                    f"harvest staging exited {proc.returncode} on {alias}: "
                    f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
                ),
                [],
                [],
            )
        marker, oversized, stayed = _parse_harvest_ack(
            proc.stdout.decode("utf-8", "replace")
        )
        if marker == "empty":
            # The job wrote nothing at all. That is a fact about the job, not a
            # transport failure; `reconcile` decides whether it is acceptable.
            return None, oversized, stayed
        if marker != "archive":
            return (
                (
                    f"harvest produced no acknowledgement on {alias}; "
                    f"the work directory may be gone"
                ),
                oversized,
                stayed,
            )
        with tempfile.TemporaryDirectory(prefix="openai4s-ssh-harvest-") as td:
            local = Path(td) / "harvest.tar.gz"
            try:
                self._run_scp(
                    [
                        "scp",
                        "-O",
                        "-q",
                        f"{alias}:{workdir}/{_HARVEST_ARCHIVE}",
                        str(local),
                    ],
                    f"harvest from {alias}:{workdir}",
                )
            except ComputeError as e:
                return str(e), oversized, stayed
            if not local.is_file():
                return f"harvest archive never arrived from {alias}", oversized, stayed
            try:
                safe_extract_tar(local, dest)
            except UnsafeArchiveError as e:
                return f"harvest archive rejected: {e}", oversized, stayed
        self._cleanup_remote_archive(alias, workdir)
        if patterns:
            # The remote `find` is the exclusion that matters, and it is the
            # one that stops the bytes moving. This is the second gate, on the
            # side we control: `find`'s pattern semantics are not identical to
            # fnmatch's on every host, and a stay-remote file that slipped
            # through must not survive on local disk to be listed as an output.
            stayed.extend(_prune_local_matches(dest, patterns))
        return None, oversized, sorted(set(stayed))

    @staticmethod
    def _cleanup_remote_archive(alias: str, workdir: str) -> None:
        """Best effort: the staged tarball doubles the job's remote footprint."""
        try:
            subprocess.run(
                ["ssh", alias, f"rm -f {workdir}/{_HARVEST_ARCHIVE}"],
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            pass

    # --- cancel / close / ssh command / scp -------------------------------
    def _terminate_ssh_job(self, job: dict) -> None:
        """Signal the job's process group and confirm it is gone.

        Raises on anything short of confirmation. There is no "probably".
        """
        pgid = str(job.get("pgid") or "")
        pid = str(job.get("pid") or "")
        if not (pgid or pid):
            raise ComputeError(
                f"job {job['job_id']!r} has no recorded remote process to "
                f"signal; inspect {job.get('workdir')} on {job.get('alias')} "
                f"by hand",
                "unknown_state",
                indeterminate=True,
            )
        try:
            proc = subprocess.run(
                ["ssh", job["alias"], _ssh_cancel_script(pgid, pid)],
                capture_output=True,
                timeout=self._CANCEL_TIMEOUT_S,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            raise ComputeError(
                f"cancel could not reach {job['alias']}: {e}; the remote "
                f"job may still be running",
                "unknown_state",
                indeterminate=True,
            )
        if proc.returncode != 0:
            # A kill we could not deliver is not a cancellation. Claiming one
            # leaves the caller believing the allocation is freed. The script
            # exits non-zero when the group outlived SIGKILL too, so this also
            # covers "signalled but still there".
            raise ComputeError(
                f"cancel failed on {job['alias']} (exit {proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}"
                f"; the remote job may still be running",
                "unknown_state",
                indeterminate=True,
            )

    def _terminate_sandbox(self, pid: str, sandbox_id: str) -> None:
        """Ask the provider to destroy one sandbox. Raises unless it agrees."""
        prov = self._byoc(pid)
        with tempfile.TemporaryDirectory(prefix="openai4s-byoc-stage-") as td:
            self._run_helper(
                prov,
                "terminate",
                {"sandbox_id": sandbox_id},
                self._provider_creds(prov),
                Path(td),
            )

    def cancel(self, kw: dict) -> dict:
        with self._lock:
            job = self._jobs.get(kw["job_id"])
        if job is None:
            raise ComputeError(f"no such job {kw['job_id']!r}", "not_found")
        fam, rest = self._split(job["provider"])
        if fam == "ssh":
            self._terminate_ssh_job(job)
        else:
            sandbox_id = job.get("sandbox_id")
            if not sandbox_id:
                raise ComputeError(
                    f"job {job['job_id']!r} has no recorded sandbox to "
                    f"terminate; it may still be running and billing",
                    "unknown_state",
                    indeterminate=True,
                )
            self._terminate_sandbox(rest, sandbox_id)
        conflict = self._commit_terminal(
            job,
            states.CANCELLED,
            event="cancelled",
            terminal_at=_now_ms(),
            termination_reason=states.REASON_USER_CANCELLED,
        )
        if conflict is not None:
            # The job had already ended by the time we stopped it. The remote
            # really was signalled, so the cancel was not a no-op — but the
            # outcome on record is the one that happened, and the caller is
            # told that rather than the one it asked for.
            return {
                "status": conflict.get("status"),
                "conflict": {
                    "requested": states.CANCELLED,
                    "actual": conflict.get("status"),
                },
                "hint": (
                    "the job reached a terminal state before the cancel "
                    "landed; the recorded outcome is unchanged"
                ),
            }
        return {"status": "cancelled"}

    def close(self, kw: dict) -> dict:
        """Release the handle, terminating what it was holding.

        ``cancelled`` is a *terminal* state, and reconcile skips terminal rows.
        Writing it over every live job on the strength of having asked to close
        the handle was therefore a false negative with teeth: an ssh job was
        never signalled at all, a byoc terminate could fail or — after a
        restart, with ``_sandboxes`` empty — never be attempted, and the job
        kept running and kept billing while the ledger said it had been
        cancelled and nothing would ever look at it again.

        So each job is terminated individually and marked ``cancelled`` only on
        confirmation. Anything unconfirmed stays live, keeps its concurrency
        slot, and is reported back by name — the caller is told what it still
        owns rather than being quietly told it owns nothing.
        """
        provider = kw["provider"]
        fam, rest = self._split(provider)
        released: list[str] = []
        unreleased: list[dict] = []
        already: list[dict] = []

        with self._lock:
            targets = [
                job
                for jid in (kw.get("job_ids") or [])
                for job in [self._jobs.get(str(jid))]
                # Every live state, `staging` and `unknown` included: work whose
                # fate we do not know is exactly what must not be forgotten.
                if job is not None and states.is_live(job.get("status"))
            ]

        # byoc jobs share one sandbox per provider, so terminate it once and
        # let every job riding on it inherit that single verdict.
        sandbox_verdicts: dict[str, BaseException | None] = {}

        def _release_sandbox(sandbox_id: str) -> BaseException | None:
            if sandbox_id not in sandbox_verdicts:
                try:
                    self._terminate_sandbox(rest, sandbox_id)
                    sandbox_verdicts[sandbox_id] = None
                except BaseException as exc:  # noqa: BLE001
                    sandbox_verdicts[sandbox_id] = exc
            return sandbox_verdicts[sandbox_id]

        for job in targets:
            jid = job["job_id"]
            job_fam = self._split(job["provider"])[0]
            try:
                if job_fam == "ssh":
                    self._terminate_ssh_job(job)
                else:
                    sandbox_id = str(job.get("sandbox_id") or "")
                    if not sandbox_id:
                        raise ComputeError(
                            "no recorded sandbox to terminate",
                            "unknown_state",
                            indeterminate=True,
                        )
                    failure = _release_sandbox(sandbox_id)
                    if failure is not None:
                        raise failure
            except BaseException as exc:  # noqa: BLE001
                unreleased.append(
                    {
                        "job_id": jid,
                        "provider": job["provider"],
                        "status": job.get("status"),
                        "receipt": job.get("receipt"),
                        "error": str(exc),
                    }
                )
                self._persist(jid, reason=f"close could not confirm: {exc}")
                self._event(jid, "close_unconfirmed", {"error": str(exc)})
                continue
            # In-memory only meant a restart rehydrated the job as live, so it
            # kept occupying a concurrency slot and kept being reconciled
            # against a provider that had already released it. Ledger first,
            # for the same reason as `cancel`: a close that lost the race to a
            # result used to report a release of a job it never ended.
            conflict = self._commit_terminal(
                job,
                states.CANCELLED,
                event="closed",
                event_payload={"provider": provider},
                terminal_at=_now_ms(),
                termination_reason=states.REASON_HANDLE_CLOSED,
            )
            if conflict is not None:
                already.append(
                    {
                        "job_id": jid,
                        "status": conflict.get("status"),
                        "note": "already terminal before this close",
                    }
                )
                continue
            released.append(jid)

        # The warm sandbox outlives the jobs that ran in it, so release it even
        # when no job ids were named. After a restart `_sandboxes` is empty and
        # the only surviving name for a container is the one on its job rows —
        # which is why those are consulted too.
        terminated = True
        if fam == "byoc":
            for sandbox_id in self._sandbox_ids_for(rest):
                if _release_sandbox(sandbox_id) is not None:
                    # Drop the id only once the provider has confirmed the
                    # sandbox is gone. Forgetting it on a failed terminate is
                    # how a sandbox bills forever with nothing left in this
                    # process able to name it.
                    terminated = False
            if terminated:
                self._sandboxes.pop(rest, None)
                self._sandbox_deadlines.pop(rest, None)

        result = {
            "status": "closed",
            "sandbox_released": terminated and not unreleased,
            "released": released,
        }
        if already:
            # Not a failure and not a release: the job ended on its own before
            # the close reached it, and the recorded outcome is its own.
            result["already_terminal"] = already
        if unreleased:
            result["unreleased"] = unreleased
            result["error_kind"] = "unknown_state"
            result["hint"] = (
                "these jobs could not be confirmed stopped and are still "
                "tracked as live; they may still be running and billing"
            )
        return result

    def _sandbox_ids_for(self, provider_id: str) -> list[str]:
        """Every sandbox this manager can still name for one byoc provider.

        The warm-reuse map is in-memory, so a restart left it empty and
        ``close`` had nothing to terminate. The job rows survive the restart,
        so they are the durable second source.
        """
        found: list[str] = []
        warm = self._sandboxes.get(provider_id)
        if warm:
            found.append(warm)
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.get("provider") != f"byoc:{provider_id}":
                continue
            sandbox_id = str(job.get("sandbox_id") or "")
            if sandbox_id and sandbox_id not in found:
                found.append(sandbox_id)
        return found

    def ssh(self, kw: dict) -> dict:
        """One synchronous command (call_command). byoc runs it inside the
        warm sandbox; ssh runs it over the alias."""
        provider = kw["provider"]
        fam, rest = self._split(provider)
        cmd = kw["command"]
        timeout_s = int(kw.get("timeout_seconds") or 60)
        if fam == "ssh":
            shell = ["ssh"]
            if kw.get("login_shell"):
                shell += ["-t"]
            shell += [rest, cmd]
            # Audited before it runs, not after: a command that hangs or kills
            # the daemon must still leave a record that it was attempted.
            self._audit("compute_ssh_command", alias=rest, command=cmd[:2000])
            proc = subprocess.run(shell, capture_output=True, timeout=timeout_s)
            return {
                "stdout": proc.stdout.decode("utf-8", "replace")[:65536],
                "stderr": proc.stderr.decode("utf-8", "replace")[:65536],
                "exit_code": proc.returncode,
            }
        raise ComputeError(
            "call_command on a byoc provider requires a live sandbox; "
            "submit a job instead",
            "invalid_request",
        )

    def scp(self, kw: dict) -> dict:
        """Direct file transfer over an ssh alias.

        A compatibility surface, deliberately kept but no longer looser than the
        job path it sits beside: paths are checked, size is capped, and every
        call is audited. Previously it forwarded an agent-supplied string
        straight to `scp`.
        """
        if self._split(kw["provider"])[0] != "ssh":
            raise ComputeError("download/upload is ssh-only", "invalid_request")
        alias = kw["provider"].split(":", 1)[1]
        remote = _safe_remote_path(kw.get("remote"), label="remote path")

        if kw["direction"] == "down":
            local = self._safe_local_path(
                kw.get("local") or Path(remote).name, label="local path"
            )
            self._audit("compute_scp_download", alias=alias, remote=remote)
            self._run_scp(
                ["scp", "-O", "-q", f"{alias}:{remote}", str(local)],
                f"download {remote!r} from {alias}",
            )
            self._enforce_transfer_cap(local)
            return {"local": str(local)}

        local = self._safe_local_path(kw["local"], label="local path", must_exist=True)
        self._enforce_transfer_cap(local)
        self._audit("compute_scp_upload", alias=alias, remote=remote)
        self._run_scp(
            ["scp", "-O", "-q", str(local), f"{alias}:{remote}"],
            f"upload to {remote!r} on {alias}",
        )
        return {"remote": remote}

    def _safe_local_path(
        self, value: Any, *, label: str, must_exist: bool = False
    ) -> Path:
        """Resolve a local path and require it to stay inside the workspace.

        Without this, `direction="down"` with `local="/etc/cron.d/x"` writes
        wherever the daemon can write — the agent choosing the destination is
        the whole risk. Symlinks are resolved BEFORE the containment check, so a
        link planted inside the workspace cannot redirect the write outside it.
        """
        text = str(value or "").strip()
        if not text:
            raise ComputeError(f"{label} must not be empty", "invalid_request")
        if "\x00" in text:
            raise ComputeError(
                f"{label} must not contain a NUL byte", "invalid_request"
            )
        base = Path(self._workspace or Path.cwd()).resolve()
        candidate = Path(text)
        resolved = base / candidate if not candidate.is_absolute() else candidate
        resolved = resolved.resolve()
        if resolved != base and base not in resolved.parents:
            raise ComputeError(
                f"{label} must stay inside the workspace ({resolved} is outside "
                f"{base})",
                "invalid_request",
            )
        if must_exist and not resolved.is_file():
            raise ComputeError(f"{label} {text!r} is not a file", "invalid_request")
        return resolved

    @staticmethod
    def _enforce_transfer_cap(path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size > MAX_TRANSFER_BYTES:
            raise ComputeError(
                f"transfer of {size} bytes exceeds the {MAX_TRANSFER_BYTES} byte "
                f"cap for the direct scp surface; stage it through a job instead",
                "invalid_request",
            )

    def _audit(self, event: str, **fields: Any) -> None:
        """Record a direct-surface call. Redaction is the emitter's job."""
        try:
            from openai4s.observability import log_event

            log_event(event, **fields)
        except Exception:  # noqa: BLE001 - auditing must not fail the operation
            pass

    @staticmethod
    def _run_scp(argv: list[str], what: str) -> None:
        """Run one scp, raising on failure.

        Returning a path the transfer never produced is what made a failed copy
        look like a delivered file to the caller.
        """
        try:
            proc = subprocess.run(argv, capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise ComputeError(f"{what} timed out after 300s", "transient")
        except OSError as e:
            raise ComputeError(f"{what} could not start: {e}", "transient")
        if proc.returncode != 0:
            raise ComputeError(
                f"{what} failed (scp exited {proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip() or 'no stderr'}",
                "transient",
            )
