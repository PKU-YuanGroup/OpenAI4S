"""Environments as a transaction: plan, apply into a new generation, or roll back.

`openai4s setup` installed *in place*. `conda env create` into an existing name
either refuses or, with `--update`, mutates the environment the running kernels
are using — so an interrupted or failed update left a half-changed environment
that nothing could describe, and there was no previous state to return to. An
artifact's environment provenance can name a generation only if generations
exist.

The model, decided by the owner:

* **plan** produces a change plan and touches nothing;
* **apply** builds a *new* generation, verifies it, and only then moves the
  current pointer;
* a failed apply leaves the current environment exactly as it was;
* **rollback** is a pointer move back to a generation that is still on disk;
* an applied generation is immutable — nothing is ever rewritten in place.

## Why the pointer is a file and the swap is a rename

``os.replace`` is atomic on POSIX and on Windows, so a reader either sees the
whole old pointer or the whole new one — never a truncated file, and never a
directory mid-rebuild. The alternative, mutating a well-known prefix, has no
instant at which the change is complete: a kernel spawning during the write
gets an environment that is neither.

## Layout

    <data_dir>/environments/<name>/
        current                       # one line: the active generation id
        generations/<id>/
            manifest.json             # spec hash, tool, state, packages, times
            prefix/                   # the environment itself
        history.jsonl                 # append-only, what happened and when

A generation directory is created under a ``.staging-`` name and renamed into
``generations/`` only once its manifest says ``ready``. So a crash mid-build
leaves rubbish that is visibly not a generation, rather than a generation that
is quietly incomplete.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

#: Generation states. Only ``ready`` may ever be pointed at.
STAGING = "staging"
READY = "ready"
FAILED = "failed"
SUPERSEDED = "superseded"

#: Plan actions.
CREATE = "create"
REPLACE = "replace"
NOOP = "noop"

Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[bytes]"]


class EnvironmentError_(RuntimeError):
    """A transaction that could not be carried out. The current env is intact."""


class ConcurrentApply(EnvironmentError_):
    """Another apply holds this environment's lock."""


class ImmutableGeneration(EnvironmentError_):
    """An applied generation may not be modified."""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _default_runner(argv: Sequence[str], cwd: Path):
    return subprocess.run(
        [str(part) for part in argv], cwd=str(cwd), capture_output=True, timeout=3600
    )


@dataclass(frozen=True)
class Generation:
    """One immutable build of one environment."""

    id: str
    name: str
    state: str
    spec_sha256: str
    prefix: str
    created_at: int
    tool: str = ""
    packages: tuple[str, ...] = ()
    interpreter: str = ""
    detail: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "generation_id": self.id,
            "environment": self.name,
            "state": self.state,
            "spec_sha256": self.spec_sha256,
            "prefix": self.prefix,
            "created_at": self.created_at,
            "tool": self.tool,
            "package_count": len(self.packages),
            "interpreter": self.interpreter,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Plan:
    """What an apply would do. Produced without touching anything."""

    name: str
    action: str
    spec_sha256: str
    reason: str
    from_generation: str | None = None
    commands: tuple[tuple[str, ...], ...] = ()

    @property
    def changes(self) -> bool:
        return self.action != NOOP

    def public(self) -> dict[str, Any]:
        return {
            "environment": self.name,
            "action": self.action,
            "spec_sha256": self.spec_sha256,
            "reason": self.reason,
            "from_generation": self.from_generation,
            "commands": [list(c) for c in self.commands],
            "changes": self.changes,
        }


@dataclass
class ApplyResult:
    """The outcome of one apply, whichever way it went."""

    name: str
    ok: bool
    generation: Generation | None
    previous: str | None
    detail: str = ""
    stderr_tail: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "environment": self.name,
            "ok": self.ok,
            "generation": self.generation.public() if self.generation else None,
            "previous_generation": self.previous,
            "detail": self.detail,
            "stderr_tail": self.stderr_tail,
        }


class EnvironmentStore:
    """The on-disk transaction. One instance per data directory."""

    def __init__(self, root: str | os.PathLike[str], *, runner: Runner | None = None):
        self.root = Path(root)
        self._runner = runner or _default_runner

    # --- layout -----------------------------------------------------------
    def _env_dir(self, name: str) -> Path:
        safe = str(name or "").strip()
        if not safe or "/" in safe or safe in (".", ".."):
            raise EnvironmentError_(f"invalid environment name {name!r}")
        return self.root / safe

    def _generations_dir(self, name: str) -> Path:
        return self._env_dir(name) / "generations"

    def _pointer(self, name: str) -> Path:
        return self._env_dir(name) / "current"

    # --- reads ------------------------------------------------------------
    def current_id(self, name: str) -> str | None:
        try:
            text = self._pointer(name).read_text("utf-8").strip()
        except OSError:
            return None
        return text or None

    def current(self, name: str) -> Generation | None:
        generation_id = self.current_id(name)
        return self.get(name, generation_id) if generation_id else None

    def get(self, name: str, generation_id: str) -> Generation | None:
        manifest = self._generations_dir(name) / generation_id / "manifest.json"
        try:
            record = json.loads(manifest.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return Generation(
            id=str(record.get("generation_id") or generation_id),
            name=str(record.get("environment") or name),
            state=str(record.get("state") or STAGING),
            spec_sha256=str(record.get("spec_sha256") or ""),
            prefix=str(record.get("prefix") or ""),
            created_at=int(record.get("created_at") or 0),
            tool=str(record.get("tool") or ""),
            packages=tuple(record.get("packages") or ()),
            interpreter=str(record.get("interpreter") or ""),
            detail=str(record.get("detail") or ""),
        )

    def list(self, name: str) -> list[Generation]:
        directory = self._generations_dir(name)
        if not directory.is_dir():
            return []
        found = [self.get(name, child.name) for child in sorted(directory.iterdir())]
        return [g for g in found if g is not None]

    def environments(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(
            child.name
            for child in self.root.iterdir()
            if child.is_dir() and not child.name.startswith(".")
        )

    def history(self, name: str, limit: int = 200) -> list[dict[str, Any]]:
        path = self._env_dir(name) / "history.jsonl"
        try:
            lines = path.read_text("utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    # --- plan -------------------------------------------------------------
    def plan(self, name: str, spec: str | os.PathLike[str], *, tool: str) -> Plan:
        """What would change. Reads only."""
        spec_path = Path(spec)
        try:
            digest = _sha256_file(spec_path)
        except OSError as e:
            raise EnvironmentError_(f"cannot read the spec for {name!r}: {e}") from e
        active = self.current(name)
        if active is None:
            return Plan(
                name=name,
                action=CREATE,
                spec_sha256=digest,
                reason="no generation is current for this environment",
                commands=(("<create>", tool, str(spec_path)),),
            )
        if active.spec_sha256 == digest:
            return Plan(
                name=name,
                action=NOOP,
                spec_sha256=digest,
                reason=(
                    f"generation {active.id} already matches this spec; "
                    f"nothing to do"
                ),
                from_generation=active.id,
            )
        return Plan(
            name=name,
            action=REPLACE,
            spec_sha256=digest,
            reason=(
                f"the spec changed since generation {active.id} "
                f"({active.spec_sha256[:12]} -> {digest[:12]})"
            ),
            from_generation=active.id,
            commands=(("<create>", tool, str(spec_path)),),
        )

    # --- apply ------------------------------------------------------------
    def apply(
        self,
        plan: Plan,
        spec: str | os.PathLike[str],
        *,
        tool: str,
        build: Callable[[Path], Sequence[str]],
        verify: Callable[[Path], tuple[str, list[str]]] | None = None,
    ) -> ApplyResult:
        """Build a new generation and, only if it verifies, make it current.

        ``build(prefix) -> argv`` is the command that installs into a prefix
        that does not exist yet; ``verify(prefix) -> (interpreter, packages)``
        is what proves it works. Both are injected so the transaction can be
        tested without a package manager, and so a caller can supply conda,
        mamba or micromamba without this module knowing about any of them.
        """
        name = plan.name
        previous = self.current_id(name)
        if not plan.changes:
            return ApplyResult(name, True, self.current(name), previous, plan.reason)

        env_dir = self._env_dir(name)
        env_dir.mkdir(parents=True, exist_ok=True)
        self._generations_dir(name).mkdir(parents=True, exist_ok=True)

        with _apply_lock(env_dir):
            # Compared against the generation the *plan* was made against, not
            # against a value re-read a line earlier — that would always agree
            # with itself and check nothing. Another apply landing between the
            # plan and here means the plan describes a world that no longer
            # exists, and building on it is how two applies both "succeed"
            # while one silently loses.
            observed = self.current_id(name)
            if observed != plan.from_generation:
                raise ConcurrentApply(
                    f"environment {name!r} moved from {plan.from_generation!r} "
                    f"to {observed!r} while this apply was planning; re-plan "
                    f"against the new current generation"
                )
            previous = observed
            generation_id = "env-" + uuid.uuid4().hex[:16]
            staging = env_dir / f".staging-{generation_id}"
            if staging.exists():  # pragma: no cover - a uuid collision
                shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            prefix = staging / "prefix"
            record = {
                "generation_id": generation_id,
                "environment": name,
                "state": STAGING,
                "spec_sha256": plan.spec_sha256,
                "prefix": str(prefix),
                "created_at": _now_ms(),
                "tool": tool,
            }
            _write_json(staging / "manifest.json", record)

            # `stderr` is bound before the try so a build() that raises before
            # the runner ever ran cannot reach the failure path with an unbound
            # name — the failure handler must never be the thing that fails.
            stderr = ""
            try:
                argv = build(prefix)
                completed = self._runner(argv, env_dir)
                stderr = _tail(getattr(completed, "stderr", b""))
                if completed.returncode != 0:
                    raise EnvironmentError_(
                        f"{tool} exited {completed.returncode} building {name!r}"
                    )
                interpreter, packages = (
                    verify(prefix) if verify is not None else ("", [])
                )
            except Exception as e:  # noqa: BLE001
                detail = (
                    str(e)
                    if isinstance(e, EnvironmentError_)
                    else f"{type(e).__name__}: {e}"
                )
                return self._fail(name, staging, record, detail, stderr)

            record.update(
                {
                    "state": READY,
                    "interpreter": interpreter,
                    "packages": list(packages),
                }
            )
            _write_json(staging / "manifest.json", record)
            # Only now does it become a generation. A crash before this leaves
            # a `.staging-` directory, which is visibly not one.
            final = self._generations_dir(name) / generation_id
            os.replace(staging, final)
            record["prefix"] = str(final / "prefix")
            _write_json(final / "manifest.json", record)
            self._point_at(name, generation_id)
            self._log(
                name,
                "applied",
                {
                    "generation_id": generation_id,
                    "previous": previous,
                    "spec_sha256": plan.spec_sha256,
                },
            )
            if previous:
                self._mark_superseded(name, previous)
        return ApplyResult(name, True, self.get(name, generation_id), previous)

    def _fail(
        self, name: str, staging: Path, record: dict, detail: str, stderr: str
    ) -> ApplyResult:
        """Record the failure without touching the current environment."""
        record.update({"state": FAILED, "detail": detail})
        try:
            _write_json(staging / "manifest.json", record)
        except OSError:  # pragma: no cover
            pass
        self._log(name, "apply_failed", {"detail": detail})
        # Deliberately left on disk under `.staging-`: it is evidence, it is
        # not a generation, and nothing can point at it.
        return ApplyResult(
            name,
            False,
            None,
            self.current_id(name),
            detail=detail,
            stderr_tail=stderr,
        )

    # --- rollback ---------------------------------------------------------
    def rollback(self, name: str, generation_id: str) -> ApplyResult:
        """Point at a generation that is already on disk.

        Nothing is rebuilt, which is the whole value of keeping them: the
        environment that worked an hour ago is still byte-for-byte there.
        """
        target = self.get(name, generation_id)
        if target is None:
            raise EnvironmentError_(
                f"no generation {generation_id!r} for environment {name!r}"
            )
        if target.state not in (READY, SUPERSEDED):
            raise EnvironmentError_(
                f"generation {generation_id!r} is {target.state!r}; only a "
                f"generation that finished building can be made current"
            )
        if not Path(target.prefix).exists():
            raise EnvironmentError_(
                f"generation {generation_id!r} no longer has its prefix on "
                f"disk ({target.prefix}); it cannot be restored by pointer"
            )
        previous = self.current_id(name)
        with _apply_lock(self._env_dir(name)):
            self._point_at(name, generation_id)
        self._log(
            name, "rolled_back", {"generation_id": generation_id, "previous": previous}
        )
        return ApplyResult(name, True, target, previous, "pointer moved")

    # --- immutability -----------------------------------------------------
    def assert_mutable(self, name: str, generation_id: str) -> None:
        """Refuse to modify a generation that has finished building."""
        generation = self.get(name, generation_id)
        if generation is not None and generation.state != STAGING:
            raise ImmutableGeneration(
                f"generation {generation_id!r} of {name!r} is {generation.state!r} "
                f"and may not be modified; apply a new generation instead"
            )

    # --- recovery ---------------------------------------------------------
    def recover(self, name: str) -> dict[str, Any]:
        """What a restart should know about this environment.

        The pointer only ever moves to a verified generation, so `current` is
        always usable — the interesting part is what was abandoned mid-build.
        """
        env_dir = self._env_dir(name)
        abandoned = []
        if env_dir.is_dir():
            for child in sorted(env_dir.iterdir()):
                if not child.name.startswith(".staging-"):
                    continue
                try:
                    record = json.loads((child / "manifest.json").read_text("utf-8"))
                except (OSError, ValueError):
                    record = {"state": "unknown"}
                abandoned.append(
                    {
                        "path": str(child),
                        "generation_id": record.get("generation_id"),
                        "state": record.get("state", "unknown"),
                        "detail": record.get("detail", ""),
                    }
                )
        stale_lock = _stale_lock(env_dir)
        return {
            "environment": name,
            "current": self.current_id(name),
            "abandoned": abandoned,
            "stale_lock": stale_lock,
        }

    def discard(self, name: str, path: str) -> bool:
        """Remove one abandoned build. Refuses anything that is a generation."""
        candidate = Path(path)
        env_dir = self._env_dir(name).resolve()
        try:
            candidate.resolve().relative_to(env_dir)
        except ValueError:
            raise EnvironmentError_(f"{path!r} is not inside {env_dir}")
        if not candidate.name.startswith(".staging-"):
            raise ImmutableGeneration(
                f"{path!r} is not an abandoned build; applied generations are "
                f"immutable and are removed by pruning, not by discard"
            )
        shutil.rmtree(candidate, ignore_errors=True)
        return not candidate.exists()

    # --- internals --------------------------------------------------------
    def _point_at(self, name: str, generation_id: str) -> None:
        """Move the pointer atomically. A reader sees one id or the other."""
        pointer = self._pointer(name)
        temporary = pointer.with_name(f".current.{uuid.uuid4().hex[:8]}")
        temporary.write_text(generation_id + "\n", encoding="utf-8")
        os.replace(temporary, pointer)

    def _mark_superseded(self, name: str, generation_id: str) -> None:
        """Note that a generation is no longer current — without editing it.

        The manifest of an applied generation is never rewritten, so this lives
        beside it. `superseded` is still restorable; that is the point.
        """
        marker = self._generations_dir(name) / generation_id / "superseded_at"
        try:
            marker.write_text(str(_now_ms()), encoding="utf-8")
        except OSError:  # pragma: no cover
            pass

    def _log(self, name: str, kind: str, payload: dict[str, Any]) -> None:
        path = self._env_dir(name) / "history.jsonl"
        line = json.dumps({"at": _now_ms(), "kind": kind, **payload}, sort_keys=True)
        try:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:  # pragma: no cover
            pass


# --- helpers --------------------------------------------------------------


def _tail(data: Any, limit: int = 4000) -> str:
    """The end of a build's stderr — the part that says what went wrong."""
    if isinstance(data, bytes):
        text = data.decode("utf-8", "replace")
    else:
        text = str(data or "")
    return text[-limit:]


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write atomically: a half-written manifest is an unreadable generation."""
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


#: A lock older than this is assumed to belong to a process that died.
LOCK_STALE_S = 6 * 3600


def _stale_lock(env_dir: Path) -> bool:
    lock = env_dir / "apply.lock"
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    return age > LOCK_STALE_S


class _apply_lock:
    """One apply per environment at a time, enforced by the filesystem.

    ``O_CREAT | O_EXCL`` is the atomic primitive here; two processes racing to
    create the same path cannot both win. A stale lock — older than
    ``LOCK_STALE_S``, i.e. left by a process that died — is broken rather than
    inherited, because a lock nobody holds is a permanent outage.
    """

    def __init__(self, env_dir: Path):
        self._path = env_dir / "apply.lock"
        self._fd: int | None = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _stale_lock(self._path.parent):
                try:
                    os.unlink(self._path)
                except OSError:  # pragma: no cover
                    pass
                self._fd = os.open(
                    self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
                )
            else:
                raise ConcurrentApply(
                    f"another apply is in progress for this environment "
                    f"({self._path}); wait for it or remove the lock if the "
                    f"process that held it is gone"
                )
        os.write(self._fd, str(os.getpid()).encode("ascii"))
        return self

    def __exit__(self, *_exc):
        if self._fd is not None:
            os.close(self._fd)
        try:
            os.unlink(self._path)
        except OSError:  # pragma: no cover
            pass
        return False


__all__ = [
    "ApplyResult",
    "CREATE",
    "ConcurrentApply",
    "EnvironmentStore",
    "EnvironmentError_",
    "FAILED",
    "Generation",
    "ImmutableGeneration",
    "NOOP",
    "Plan",
    "READY",
    "REPLACE",
    "STAGING",
    "SUPERSEDED",
]
