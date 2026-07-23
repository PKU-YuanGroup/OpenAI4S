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

The environment is built **at its final prefix**, not in a staging directory
that is renamed — Conda bakes the absolute prefix into scripts, activation
hooks and package metadata, so a relocated environment is broken. What is made
atomic is instead the generation's *visibility*: ``manifest.json`` is written
last, and the ``current`` pointer is flipped last. While a build is in flight
the directory holds a ``building.json`` and no manifest, so ``get``/``list``
ignore it and discovery never serves it; a crash therefore leaves something
visibly not a generation, cleaned up by ``recover``/``discard``.
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

#: Written while a generation is being built at its final location, and removed
#: (or replaced by ``manifest.json``) when it finishes. Its presence — with no
#: manifest — is what marks a directory as an abandoned build rather than a
#: generation, now that the bytes are no longer built in a separate staging dir
#: and renamed.
_BUILDING = "building.json"

Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[bytes]"]


class EnvironmentError_(RuntimeError):
    """A transaction that could not be carried out. The current env is intact."""


class ConcurrentApply(EnvironmentError_):
    """Another apply holds this environment's lock."""


class ImmutableGeneration(EnvironmentError_):
    """An applied generation may not be modified."""


def _now_ms() -> int:
    return int(time.time() * 1000)


#: A probe that has not answered in this long is a broken environment, not a
#: slow one — nothing here does work, it only starts an interpreter.
PROBE_TIMEOUT_S = 60.0


def _run_probe(argv: Sequence[str], *, label: str) -> None:
    """Start an interpreter and require it to exit cleanly."""
    try:
        completed = subprocess.run(  # noqa: S603 - argv is built here
            list(argv), capture_output=True, timeout=PROBE_TIMEOUT_S
        )
    except PermissionError as e:
        raise EnvironmentError_(f"{label} is not executable: {e}")
    except (FileNotFoundError, OSError) as e:
        raise EnvironmentError_(f"{label} could not start: {e}")
    except subprocess.TimeoutExpired:
        raise EnvironmentError_(f"{label} did not finish within {PROBE_TIMEOUT_S:.0f}s")
    if completed.returncode != 0:
        raise EnvironmentError_(
            f"{label} exited {completed.returncode}: "
            f"{_tail(completed.stderr) or 'no stderr'}"
        )


def probe_interpreter(prefix: Path) -> tuple[str, list[str]]:
    """Run what the build produced. Nothing short of that proves it works.

    The previous check asked whether a file *existed* at ``bin/python`` or
    ``bin/Rscript``, and then tried to freeze the Python one — with the freeze
    failure swallowed into an empty package list. So a prefix holding a
    non-executable file, or an interpreter that dies on startup, passed; and an
    R generation was never executed at all. The pointer could move onto an
    environment that no cell could run in, with the transaction reporting that
    it had verified it.

    Returns ``(interpreter, packages)``. The package list is documentation, not
    the gate: the probe above it is the gate, so a freeze that fails leaves the
    list empty without pretending the environment is broken.
    """
    prefix = Path(prefix)
    python = prefix / "bin" / "python"
    if not python.is_file():
        python = prefix / "bin" / "python3"
    rscript = prefix / "bin" / "Rscript"
    if not python.is_file() and not rscript.is_file():
        raise EnvironmentError_(
            f"the build produced no interpreter under {prefix}; refusing to "
            f"make it current"
        )

    interpreter = ""
    packages: list[str] = []
    if python.is_file():
        _run_probe(
            [
                str(python),
                "-c",
                "import sys, platform; print(platform.python_version())",
            ],
            label=f"python probe for {python}",
        )
        interpreter = str(python)
        try:
            from openai4s.kernel import preinstall

            frozen = preinstall.freeze_for(str(python)) or []
            packages = [f"{item['name']}=={item.get('version')}" for item in frozen]
        except Exception:  # noqa: BLE001 - a package list is not the gate
            packages = []
    if rscript.is_file():
        _run_probe(
            [str(rscript), "--vanilla", "-e", "cat(R.version.string)"],
            label=f"R probe for {rscript}",
        )
        if not interpreter:
            interpreter = str(rscript)
    return interpreter, packages


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
        # `get` keys on `manifest.json`, which a half-built or failed directory
        # does not have (it has `building.json`) — so an abandoned build is not
        # in the list, exactly as it was not when builds lived under `.staging-`.
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

            # A no-op is a claim too: "nothing changed since the plan". Validate
            # it under the *same* lock and the same checks, because a spec edited
            # or a pointer moved between plan and apply means that claim is now
            # false — and returning success without checking reported an env
            # that no longer matched the plan.
            if not plan.changes:
                live_spec = Path(spec)
                try:
                    current_sha = _sha256_file(live_spec)
                except OSError as e:
                    raise EnvironmentError_(
                        f"the spec for {name!r} could not be read at apply "
                        f"time: {e}"
                    )
                if current_sha != plan.spec_sha256:
                    raise EnvironmentError_(
                        f"the spec for {name!r} changed since the no-op plan "
                        f"was made ({plan.spec_sha256[:12]} -> "
                        f"{current_sha[:12]}); re-plan"
                    )
                return ApplyResult(
                    name, True, self.current(name), previous, plan.reason
                )
            # The spec is re-hashed *here*, under the lock, and then copied.
            # The manifest recorded `plan.spec_sha256` while the build read the
            # live YAML through a path captured at plan time, so a file edited
            # in between produced a generation whose recorded provenance did
            # not describe its contents — which is worse than no provenance,
            # because it is believed.
            live = Path(spec)
            try:
                observed_sha = _sha256_file(live)
            except OSError as e:
                raise EnvironmentError_(
                    f"the spec for {name!r} could not be read at apply time: {e}"
                )
            if observed_sha != plan.spec_sha256:
                raise EnvironmentError_(
                    f"the spec for {name!r} changed between plan and apply "
                    f"({plan.spec_sha256[:12]} -> {observed_sha[:12]}); "
                    f"re-plan against the file as it stands now"
                )

            generation_id = "env-" + uuid.uuid4().hex[:16]
            # Build *at the final prefix*, not in a staging directory that is
            # later renamed. Conda bakes the environment's absolute prefix into
            # scripts, activation hooks, and package metadata, so renaming
            # `.staging-<id>/prefix` to `generations/<id>/prefix` produced a
            # generally unusable environment — and the recorded interpreter
            # still named the vanished staging path. What must be atomic is the
            # *visibility* of the generation, not the location of its bytes.
            #
            # Visibility comes from two things, both flipped last: the `ready`
            # marker (so `get`/`list` ignore a half-built directory) and the
            # `current` pointer (so discovery never serves one). A crash mid-
            # build therefore leaves a `generations/<id>` with a `building.json`
            # and no `manifest.json` — visibly not a generation, and cleaned up
            # by `recover`/`discard` exactly as the old `.staging-` dir was.
            gen_dir = self._generations_dir(name) / generation_id
            if gen_dir.exists():  # pragma: no cover - a uuid collision
                shutil.rmtree(gen_dir, ignore_errors=True)
            gen_dir.mkdir(parents=True)
            # Build from an immutable copy. Re-hashing closes the plan→apply
            # window; the copy closes the apply→build one, which no hash can.
            staged_spec = gen_dir / f"spec{live.suffix or '.yml'}"
            shutil.copyfile(live, staged_spec)
            prefix = gen_dir / "prefix"
            record = {
                "generation_id": generation_id,
                "environment": name,
                "state": STAGING,
                "spec_sha256": plan.spec_sha256,
                "prefix": str(prefix),
                "created_at": _now_ms(),
                "tool": tool,
            }
            # `building.json`, not `manifest.json`: until the build succeeds this
            # directory is not a generation, and `get`/`list` key on the
            # manifest's presence.
            _write_json(gen_dir / _BUILDING, record)

            # `stderr` is bound before the try so a build() that raises before
            # the runner ever ran cannot reach the failure path with an unbound
            # name — the failure handler must never be the thing that fails.
            stderr = ""
            try:
                argv = build(prefix, staged_spec)
                completed = self._runner(argv, env_dir)
                stderr = _tail(getattr(completed, "stderr", b""))
                if completed.returncode != 0:
                    raise EnvironmentError_(
                        f"{tool} exited {completed.returncode} building {name!r}"
                    )
                # No verifier means the *default* verifier, never no check.
                # `("", [])` let a caller that simply did not pass one move the
                # pointer onto an unexamined prefix.
                interpreter, packages = (
                    verify(prefix) if verify is not None else probe_interpreter(prefix)
                )
            except Exception as e:  # noqa: BLE001
                detail = (
                    str(e)
                    if isinstance(e, EnvironmentError_)
                    else f"{type(e).__name__}: {e}"
                )
                return self._fail(name, gen_dir, record, detail, stderr)

            record.update(
                {
                    "state": READY,
                    "interpreter": interpreter,
                    "packages": list(packages),
                }
            )
            # The manifest is written last and atomically: a reader sees either
            # no manifest (not a generation) or a complete READY one. Only then
            # is the building marker removed.
            _write_json(gen_dir / "manifest.json", record)
            (gen_dir / _BUILDING).unlink(missing_ok=True)
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
        self, name: str, gen_dir: Path, record: dict, detail: str, stderr: str
    ) -> ApplyResult:
        """Record the failure without touching the current environment."""
        record.update({"state": FAILED, "detail": detail})
        try:
            # The failure marker, not a manifest: a directory with a
            # `building.json` and no `manifest.json` is evidence of an abandoned
            # build, which `get`/`list` ignore and `recover`/`discard` clean up.
            # Nothing points at it and it can never become current.
            _write_json(gen_dir / _BUILDING, record)
        except OSError:  # pragma: no cover
            pass
        self._log(name, "apply_failed", {"detail": detail})
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
        # `previous`, the pointer move, and the history append happen under one
        # lock. Reading `previous` before the lock let an apply that ran while
        # this rollback waited move the pointer X->Y, so the rollback recorded
        # and returned X though it displaced Y; appending history after the lock
        # let another operation be logged in between. All three inside keeps the
        # record consistent with what actually happened.
        with _apply_lock(self._env_dir(name)):
            previous = self.current_id(name)
            self._point_at(name, generation_id)
            self._log(
                name,
                "rolled_back",
                {"generation_id": generation_id, "previous": previous},
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
        # A build in flight has a `building.json` and no manifest — exactly the
        # shape of an abandoned one. But while `apply()` holds the lock and conda
        # is still writing the prefix, calling it abandoned invites a cleanup
        # (`discard()`) that deletes the prefix mid-write and fails the live
        # transaction. When a *non-stale* apply lock is present, a build is
        # potentially live, so it is not reported as abandoned. A stale lock, or
        # no lock, means no apply owns it and it really is abandoned.
        apply_in_progress = (env_dir / "apply.lock").exists() and not _stale_lock(
            env_dir
        )

        def _record_abandoned(child: Path) -> None:
            marker = child / _BUILDING
            try:
                record = json.loads(marker.read_text("utf-8"))
            except (OSError, ValueError):
                record = {"state": "unknown"}
            abandoned.append(
                {
                    "path": str(child),
                    "generation_id": record.get("generation_id") or child.name,
                    "state": record.get("state", "unknown"),
                    "detail": record.get("detail", ""),
                }
            )

        generations = self._generations_dir(name)
        if generations.is_dir():
            for child in sorted(generations.iterdir()):
                if not child.is_dir():
                    continue
                # A directory built at its final location is a generation once
                # it has a manifest; anything without one — a `building.json`
                # from a crashed or failed build, or a half-removed dir — is an
                # abandoned build, unless an apply is actively holding the lock,
                # in which case it may be the build in progress.
                if (child / "manifest.json").is_file():
                    continue
                if apply_in_progress:
                    continue
                _record_abandoned(child)
        # Legacy layout: builds that used to live under `.staging-` before the
        # move to final-prefix builds. Still cleaned up so an upgrade does not
        # strand them.
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
        legacy = candidate.name.startswith(".staging-")
        # A generation is a directory under `generations/` with a manifest.
        # A build that never got one — `building.json` present, or nothing —
        # is discardable; a real generation is immutable and never current.
        in_generations = (
            candidate.parent.resolve() == self._generations_dir(name).resolve()
        )
        is_generation = in_generations and (candidate / "manifest.json").is_file()
        if is_generation or (not legacy and not in_generations):
            raise ImmutableGeneration(
                f"{path!r} is not an abandoned build; applied generations are "
                f"immutable and are removed by pruning, not by discard"
            )
        if in_generations and candidate.name == self.current_id(name):
            raise ImmutableGeneration(
                f"{path!r} is the current generation and cannot be discarded"
            )
        shutil.rmtree(candidate, ignore_errors=True)
        return not candidate.exists()

    # --- internals --------------------------------------------------------
    def _point_at(self, name: str, generation_id: str) -> None:
        """Move the pointer atomically, and tell discovery it moved.

        A reader sees one id or the other. The cache invalidation is here, at
        the single place a pointer ever changes, rather than at each call site:
        `apply` and `rollback` both reported a new current generation while
        `kernel.environments` kept serving its module-wide cache, so the
        transaction took effect only for processes that had not looked yet.
        """
        pointer = self._pointer(name)
        temporary = pointer.with_name(f".current.{uuid.uuid4().hex[:8]}")
        temporary.write_text(generation_id + "\n", encoding="utf-8")
        os.replace(temporary, pointer)
        try:
            from openai4s.kernel import environments as envmod

            envmod.invalidate_cache()
        except Exception:  # noqa: BLE001 - a pointer move is not a discovery op
            pass

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
        #: Unique per instance, written into the lock so ownership can be
        #: re-verified — a pid alone is reused across a restart.
        self._token = uuid.uuid4().hex

    def _payload(self) -> bytes:
        return f"{os.getpid()}:{self._token}".encode("ascii")

    def _acquire_fresh(self) -> None:
        self._fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(self._fd, self._payload())

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._acquire_fresh()
            return self
        except FileExistsError:
            pass
        if not _stale_lock(self._path.parent):
            raise ConcurrentApply(
                f"another apply is in progress for this environment "
                f"({self._path}); wait for it or remove the lock if the "
                f"process that held it is gone"
            )
        # Reclaim a stale lock atomically. Unlink-then-create let two processes
        # both pass `_stale_lock`, both unlink, and both create — and the first
        # to exit then deleted the second's new lock. Moving the stale file
        # aside with `os.replace` is the atomic primitive: of all racers, only
        # the one that finds a file still present succeeds; the rest see it gone
        # and must not create their own, or two applies run.
        aside = self._path.with_name(f".apply.lock.stale.{self._token}")
        try:
            os.replace(self._path, aside)
        except FileNotFoundError:
            raise ConcurrentApply(
                f"another apply reclaimed the stale lock for {self._path} first"
            )
        # But `os.replace` moves *whatever is at the path* — and between two
        # racers both passing `_stale_lock`, the first can already have replaced
        # the stale file with its own fresh lock, which the second would then
        # move aside. Verify the file we moved is genuinely stale; if it is a
        # freshly-created lock, restore it and yield rather than displace a live
        # owner.
        try:
            still_stale = (time.time() - os.stat(aside).st_mtime) > LOCK_STALE_S
        except OSError:  # pragma: no cover
            still_stale = True
        if not still_stale:
            try:
                os.replace(aside, self._path)
            except OSError:  # pragma: no cover
                pass
            raise ConcurrentApply(
                f"another apply holds a fresh lock for {self._path}; not stale"
            )
        try:
            os.unlink(aside)
        except OSError:  # pragma: no cover
            pass
        try:
            self._acquire_fresh()
        except FileExistsError:
            # A fresh apply grabbed the slot in the gap after we reclaimed.
            raise ConcurrentApply(
                f"another apply acquired {self._path} during reclamation"
            )
        return self

    def __exit__(self, *_exc):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        # Only remove the lock if it is still *ours*. A token check stops this
        # process from deleting a lock a later apply legitimately created after
        # this one's was reclaimed as stale — the "first process deletes the
        # second's lock on exit" race.
        try:
            holder = self._path.read_bytes()
        except OSError:  # pragma: no cover
            return False
        if holder == self._payload():
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
