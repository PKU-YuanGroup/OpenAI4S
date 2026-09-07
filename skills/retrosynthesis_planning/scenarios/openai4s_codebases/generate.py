#!/usr/bin/env python3
"""Generate independent Scenario codebases through the real OpenAI4S CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import socket
import stat
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

NAMES = (
    "01_single_step_retrosynthesis",
    "02_multistep_route_planning",
    "03_atom_mapping",
    "04_forward_prediction",
    "05_condition_recommendation",
    "06_yield_estimation",
)

PUBLIC_MODULES = (
    "benchmark_common.py",
    "single_step_benchmark.py",
    "multistep_benchmark.py",
    "atom_mapping_benchmark.py",
    "forward_benchmark.py",
    "condition_benchmark.py",
    "yield_benchmark.py",
    "route_review.py",
    "kernel.py",
)


#: Every field a manifest entry may carry. ``_entry`` writes the machine subset
#: and a maintainer may add the disclosed subset by hand, but neither side may
#: invent a field the other has never heard of -- that is how the committed
#: rows drifted into a shape this generator cannot reproduce.
ENTRY_FIELDS = frozenset(
    {
        "name",
        "query",
        "query_sha256",
        "gt_codebase",
        "gt_sha256",
        "generated_codebase",
        "generated_sha256",
        "verified_artifact_sha256",
        "generation_interface",
        "openai4s_command",
        "openai4s_output_sha256",
        "openai4s_exit_code",
        "post_generation_conformance_repair",
        "status",
        "verification_error",
        "error",
        # Disclosed hand-recorded provenance: the model behind the endpoint and
        # the state of an Agent run cannot be observed from this process.
        "model",
        "run_completion",
        "post_generation_review_repair",
    }
)
#: Fields every entry must carry, whoever wrote it.
REQUIRED_ENTRY_FIELDS = frozenset({"name", "status"})
#: Interfaces an entry may claim. ``_entry`` can only ever produce the first.
GENERATION_INTERFACES = frozenset(
    {"openai4s run --mode codebase_change", "openai4s.llm.chat"}
)


class VerificationError(RuntimeError):
    """A fixed, public diagnostic safe to include in generation provenance."""


def _diagnostic(error: Exception) -> str:
    # Parser/compiler/OS errors can contain source text, paths or child output.
    # Only our own fixed diagnostics may be serialized into the manifest.
    return str(error) if isinstance(error, VerificationError) else type(error).__name__


def _environment(workspace: Path, temporary: Path) -> dict[str, str]:
    """No operator variables, import paths or credentials cross this boundary."""
    return {
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "HOME": str(temporary),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "PWD": str(workspace),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _runtime_roots() -> tuple[Path, ...]:
    # -I -S uses the base interpreter and stdlib without loading site-packages,
    # .pth files or the operator's virtualenv. Never allow the repository root.
    candidates = (
        Path(sys.executable).resolve().parent,
        Path(sysconfig.get_path("stdlib")).resolve(),
        Path(sysconfig.get_path("platstdlib")).resolve(),
        Path(sys.base_prefix).resolve() / "lib",
        Path("/usr/lib"),
        Path("/lib"),
        Path("/lib64"),
        Path("/System/Library"),
    )
    # Preserve /lib and /lib64 as mount destinations on merged-/usr Linux:
    # the ELF loader still names those lexical paths in an otherwise empty root.
    return tuple(dict.fromkeys(path.absolute() for path in candidates if path.is_dir()))


def _runtime_denials() -> tuple[Path, ...]:
    # -S suppresses imports, but filesystem access needs an independent deny:
    # an installed distribution can include the same GT and private fixtures.
    paths = {Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")}
    for root in _runtime_roots():
        for pattern in (
            "site-packages",
            "dist-packages",
            "python*/site-packages",
            "python*/dist-packages",
        ):
            for path in root.glob(pattern):
                if path.is_dir():
                    paths.update((path.absolute(), path.resolve()))
    return tuple(sorted(path for path in paths if path.is_dir()))


def _sandbox_command(
    command: list[str], *, readonly: Path, results: Path, temporary: Path
) -> list[str]:
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise VerificationError("verification requires an available OS sandbox")
    allowed = (*_runtime_roots(), readonly)
    if sys.platform == "darwin":
        from openai4s.security.sandbox import (
            KernelReadIsolation,
            wrap_seatbelt_command,
        )

        executable = shutil.which("sandbox-exec")
        if executable:
            wrapped = wrap_seatbelt_command(
                command,
                executable=executable,
                workspace=results,
                temp_dir=temporary,
                allow_raw_network=False,
                deny_read=tuple(("subpath", str(path)) for path in _runtime_denials()),
                read_isolation=KernelReadIsolation(
                    roots=("/",), allowed_roots=(*allowed, temporary, Path("/dev"))
                ),
            )
            # This verifier only normalizes files. It needs no subprocesses,
            # parent-process metadata, Apple Events or Mach service clients.
            wrapped[
                2
            ] += "(deny process-fork process-info* mach-lookup appleevent-send)\n"
            # KERN_PROCARGS2 is a separate sysctl path around process-info*;
            # its name includes the PID, so an exact kern.procargs2 rule misses it.
            wrapped[2] += '(deny sysctl-read (sysctl-name-prefix "kern.proc"))\n'
            # dyld opens the mount root during interpreter startup. Grant only
            # that directory itself; no descendant data becomes readable.
            wrapped[2] += '(allow file-read-data (literal "/"))\n'
            return wrapped
    elif sys.platform.startswith("linux"):
        executable = shutil.which("bwrap")
        if executable:
            # Start with an empty root, not a host-root bind with path masks.
            # Private PID and network namespaces close /proc and socket aliases.
            wrapped = [
                executable,
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--tmpfs",
                "/",
            ]
            for path in allowed:
                wrapped.extend(("--ro-bind", str(path), str(path)))
            for path in _runtime_denials():
                if any(path == root or root in path.parents for root in allowed):
                    wrapped.extend(("--tmpfs", str(path), "--remount-ro", str(path)))
            wrapped.extend(
                (
                    "--bind",
                    str(results),
                    str(results),
                    "--bind",
                    str(temporary),
                    str(temporary),
                    "--dev",
                    "/dev",
                    "--proc",
                    "/proc",
                    "--remount-ro",
                    "/",
                    "--chdir",
                    str(results),
                    "--",
                    *command,
                )
            )
            return wrapped
    raise VerificationError("verification requires an available OS sandbox")


def _python(command: list[str], imports: Path) -> list[str]:
    bootstrap = (
        "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
        "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    return [
        str(Path(sys.executable).resolve()),
        "-I",
        "-S",
        "-B",
        "-c",
        bootstrap,
        str(imports),
        *command,
    ]


def _probe_boundary(
    *, readonly: Path, results: Path, temporary: Path, denied: tuple[Path, ...]
) -> None:
    probe = readonly / "boundary_probe.py"
    probe.write_text(
        "import os, pathlib, socket, sys\n"
        "port = int(sys.argv[1])\n"
        "allowed, results, *denied = map(pathlib.Path, sys.argv[2:])\n"
        "allowed.read_bytes()\n"
        "(results / 'probe').write_text('ok')\n"
        "(results / 'probe').unlink()\n"
        "try:\n allowed.write_bytes(b'changed')\n"
        "except OSError: pass\n"
        "else: raise SystemExit(21)\n"
        "for path in denied:\n"
        " try: path.read_bytes()\n"
        " except OSError: pass\n"
        " else: raise SystemExit(22)\n"
        " link = results / 'probe-link'\n"
        " try:\n  link.symlink_to(path); link.read_bytes()\n"
        " except OSError: pass\n"
        " else: raise SystemExit(23)\n"
        " finally: link.unlink(missing_ok=True)\n"
        " try:\n  os.link(path, link); link.read_bytes()\n"
        " except OSError: pass\n"
        " else: raise SystemExit(24)\n"
        " finally: link.unlink(missing_ok=True)\n"
        "try:\n socket.create_connection(('127.0.0.1', port), timeout=2).close()\n"
        "except OSError: pass\n"
        "else: raise SystemExit(25)\n"
        "if sys.platform == 'darwin':\n"
        " import ctypes\n"
        " mib = (ctypes.c_int * 3)(1, 49, os.getppid())\n"
        " size = ctypes.c_size_t(262144)\n"
        " buffer = ctypes.create_string_buffer(size.value)\n"
        " if ctypes.CDLL(None).sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) == 0:\n"
        "  raise SystemExit(26)\n",
        encoding="utf-8",
    )
    # The exit statuses the probe source above uses to report a breach it
    # detected, as opposed to any other reason the child failed to run.
    violations = frozenset({21, 22, 23, 24, 25, 26})
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            command = _python(
                [
                    str(probe),
                    str(listener.getsockname()[1]),
                    str(readonly / "workspace" / "installation.json"),
                    str(results),
                    *(str(path) for path in denied),
                ],
                readonly / "skills",
            )
            wrapped = _sandbox_command(
                command, readonly=readonly, results=results, temporary=temporary
            )
            try:
                _checked(
                    wrapped,
                    root=results,
                    environment=_environment(results, temporary),
                )
            except VerificationError as error:
                # The probe reports each violation with its own exit status. A
                # breach it actually detected must never be reported with the
                # same words as a missing sandbox, or a caller that tolerates
                # "no isolation here" silently tolerates "isolation failed".
                if getattr(error, "exit_code", None) in violations:
                    raise VerificationError(
                        "verification OS sandbox boundary was not honoured"
                    ) from error
                raise VerificationError(
                    "verification OS sandbox boundary probe could not run"
                ) from error
    except VerificationError:
        raise
    except Exception as error:
        raise VerificationError(
            "verification OS sandbox boundary probe could not run"
        ) from error
    finally:
        probe.unlink()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _root() -> Path:
    return Path(__file__).resolve().parents[4]


def _command(root: Path, query: Path) -> list[str]:
    local = root / ".venv" / "bin" / "openai4s"
    executable = str(local) if local.is_file() else (shutil.which("openai4s") or "")
    if not executable:
        raise RuntimeError("openai4s executable not found")
    task = (
        f"Read {query.relative_to(root)} completely and implement exactly the "
        "requested codebase. Do not read private_evaluator, gt_codebase.py, "
        "scenarios/gt_codebases, or scenarios/pipelines. Run focused public-input "
        "tests and finish only after saving the requested source file."
    )
    return [executable, "run", task, "--mode", "codebase_change", "--json"]


def _checked(command: list[str], *, root: Path, environment: dict[str, str]) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise VerificationError("verification process could not complete") from error
    if completed.returncode:
        failure = VerificationError(
            f"verification command failed (exit {completed.returncode}); output omitted"
        )
        # Set dynamically so the exception stays declared above the frozen
        # egress-inventory line numbers in tests/test_egress_surface.py.
        failure.exit_code = completed.returncode  # type: ignore[attr-defined]
        raise failure


def _verify_case(root: Path, name: str, gt: Path, generated: Path) -> str:
    test_cases = gt.parent.parent / "test_cases"
    case = test_cases / f"{name}.json"
    installer = test_cases / "install.py"
    evaluator = test_cases / "evaluate.py"
    scenario = json.loads(case.read_text(encoding="utf-8"))["scenario"]
    with tempfile.TemporaryDirectory(prefix=f"openai4s-{name}-") as temporary:
        scratch = Path(temporary).resolve()
        gt_workspace = scratch / "gt"
        generated_workspace = scratch / "generated"
        environment = _environment(scratch, scratch)
        for workspace in (gt_workspace, generated_workspace):
            _checked(
                _python(
                    [
                        str(installer),
                        "--case",
                        str(case),
                        "--workspace",
                        str(workspace),
                    ],
                    root / "skills",
                ),
                root=root,
                environment=environment,
            )
        _checked(
            _python([str(gt), "--workspace", str(gt_workspace)], root / "skills"),
            root=root,
            environment=environment,
        )
        readonly = scratch / "runner"
        public_workspace = readonly / "workspace"
        public_workspace.mkdir(parents=True)
        shutil.copytree(generated_workspace / "public", public_workspace / "public")
        shutil.copyfile(
            generated_workspace / "installation.json",
            public_workspace / "installation.json",
        )
        results = scratch / "results"
        results.mkdir()
        (public_workspace / "results").symlink_to(results, target_is_directory=True)
        worker_temp = scratch / "worker-temp"
        worker_temp.mkdir()
        modules = readonly / "skills" / "retrosynthesis_planning"
        modules.mkdir(parents=True)
        for module in PUBLIC_MODULES:
            shutil.copyfile(
                root / "skills" / "retrosynthesis_planning" / module, modules / module
            )
        candidate = readonly / "candidate.py"
        shutil.copyfile(generated, candidate)
        _probe_boundary(
            readonly=readonly,
            results=results,
            temporary=worker_temp,
            denied=(
                gt_workspace / "results" / "intermediate_results.json",
                generated_workspace / "private_evaluator" / "references.json",
                gt,
                gt.parent.parent / "pipelines" / gt.name,
                root / "skills" / "retrosynthesis_planning" / "gt_codebase.py",
                case,
            ),
        )
        _checked(
            _sandbox_command(
                _python(
                    [str(candidate), "--workspace", str(public_workspace)],
                    readonly / "skills",
                ),
                readonly=readonly,
                results=results,
                temporary=worker_temp,
            ),
            root=results,
            environment=_environment(results, worker_temp),
        )
        gt_artifact = gt_workspace / "results" / "intermediate_results.json"
        generated_artifact = results / "intermediate_results.json"
        expected = gt_artifact.read_bytes()
        descriptor = os.open(
            generated_artifact, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
        )
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise VerificationError("generated artifact must be a regular file")
            artifact_bytes = handle.read(len(expected) + 1)
        if expected != artifact_bytes:
            raise VerificationError(
                "generated artifact does not exactly match GT artifact"
            )
        (generated_workspace / "results" / "intermediate_results.json").write_bytes(
            artifact_bytes
        )
        _checked(
            _python(
                [
                    str(evaluator),
                    "--scenario",
                    scenario,
                    "--workspace",
                    str(generated_workspace),
                ],
                root / "skills",
            ),
            root=root,
            environment=environment,
        )
        return hashlib.sha256(artifact_bytes).hexdigest()


def _entry(root: Path, name: str, *, overwrite: bool) -> dict[str, object]:
    base = Path(__file__).resolve().parent
    query = base.parent / "queries" / f"{name}.query.md"
    gt = base.parent / "gt_codebases" / f"{name}.py"
    generated = base / f"{name}.py"
    if generated.exists() and not overwrite:
        raise VerificationError(
            "refusing to overwrite existing source; pass --overwrite"
        )
    command = _command(root, query)
    preserved = generated.read_bytes() if generated.is_file() else None
    try:
        return _generate(root, name, query, gt, generated, command)
    finally:
        # A run that produced nothing is an attempt, not a replacement: the
        # previously verified source is restored so its provenance still holds.
        # This has to be a finally — the record is built after the child runs,
        # so any failure in between used to leave the file deleted.
        if preserved is not None and not generated.exists():
            generated.write_bytes(preserved)


def _generate(
    root: Path, name: str, query: Path, gt: Path, generated: Path, command: list[str]
) -> dict[str, object]:
    if generated.exists():
        generated.unlink()
    completed = subprocess.run(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    output_sha256 = hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest()
    record: dict[str, object] = {
        "name": name,
        "query": str(query.relative_to(root)),
        "query_sha256": _sha256(query),
        "gt_codebase": str(gt.relative_to(root)),
        "gt_sha256": _sha256(gt),
        "openai4s_command": ["openai4s", *command[1:]],
        "openai4s_output_sha256": output_sha256,
        "openai4s_exit_code": completed.returncode,
        "generated_codebase": str(generated.relative_to(root)),
        "generation_interface": "openai4s run --mode codebase_change",
        "post_generation_conformance_repair": False,
        "status": "failed",
    }
    if completed.returncode == 0 and generated.is_file():
        source = generated.read_text(encoding="utf-8")
        # Mirrors the four paths the task forbids. "gt_codebases" is already a
        # substring of "gt_codebase"; "pipelines" was missing, and that
        # directory is a byte-identical copy of the GT entry points.
        forbidden = ("gt_codebase", "private_evaluator", "pipelines")
        hits = [item for item in forbidden if item in source]
        if hits:
            record["verification_error"] = f"forbidden source references: {hits}"
        else:
            record["generated_sha256"] = _sha256(generated)
            try:
                py_compile.compile(str(generated), doraise=True)
                record["verified_artifact_sha256"] = _verify_case(
                    root, name, gt, generated
                )
                record["status"] = "generated_verified"
            except Exception as error:
                record["verification_error"] = _diagnostic(error)
                record["status"] = "generated_verification_failed"
    elif completed.returncode == 0:
        record["verification_error"] = "OpenAI4S exited successfully without source"
    # Bind the writer to the declared schema, so a new field here is a red test
    # rather than a manifest shape nothing can reproduce.
    undeclared = set(record) - ENTRY_FIELDS
    if undeclared:
        raise VerificationError(f"undeclared manifest fields: {sorted(undeclared)}")
    return record


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate matched retrosynthesis codebases through OpenAI4S"
    )
    parser.add_argument("--scenario", choices=(*NAMES, "all"), default="all")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    root = _root()
    selected = NAMES if args.scenario == "all" else (args.scenario,)
    manifest_path = Path(__file__).with_name("generation_manifest.json")
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"schema_version": 3, "generator": "OpenAI4S", "entries": []}
    )
    previous = {entry["name"]: entry for entry in manifest["entries"]}
    records = []
    failed = False
    changed = False
    for name in selected:
        generated = Path(__file__).with_name(f"{name}.py")
        before = _sha256(generated) if generated.is_file() else None
        try:
            record = _entry(root, name, overwrite=args.overwrite)
        except Exception as error:  # Keep per-Scenario provenance on failure.
            record = {"name": name, "status": "failed", "error": _diagnostic(error)}
        records.append(record)
        failed = failed or record["status"] != "generated_verified"
        after = _sha256(generated) if generated.is_file() else None
        # A refused overwrite or failure before a source change is an attempt,
        # not replacement provenance for the unchanged artifact.
        if (
            record["status"] == "generated_verified"
            or before != after
            or name not in previous
        ):
            previous[name] = record
            changed = True
    if changed:
        manifest["entries"] = list(previous.values())
        manifest.setdefault("generation_kind", "per_entry_generation")
        _write_json(manifest_path, manifest)
    print(
        json.dumps({"attempts": records, "manifest_updated": changed}, sort_keys=True)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
