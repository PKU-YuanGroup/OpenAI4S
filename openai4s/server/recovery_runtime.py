"""Concrete Python/R runtime adapter for verified session recovery.

The protocol-neutral recovery algorithm and mutation sequencing live in
``kernel.recovery`` and ``server.recovery_execution``.  This module binds those
ports to a session's KernelSupervisor, workspace CAS, Artifact-version reads,
and dispatcher without importing Gateway or SessionRunner.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openai4s.kernel import Kernel, KernelLease, KernelSupervisor
from openai4s.kernel.recovery import BootstrapManifest, frozen_sidecar_bootstrap_code
from openai4s.server.recovery_control import RecoveryActionPlan, RecoveryControlService
from openai4s.server.recovery_execution import (
    RecoveryExecutionPorts,
    RecoveryMutationExecutor,
)


@dataclass(frozen=True)
class PythonRuntimeSpec:
    interpreter: str
    runtime_version: str
    environment_name: str
    environment_root: str | None = None
    is_conda: bool = False
    sdk_version: str | None = None


@dataclass(frozen=True)
class RecoveryRuntimePorts:
    root_frame_id: str
    workspace: Path
    kernels: KernelSupervisor
    control: RecoveryControlService
    cas: Any
    checkpoint: Callable[[str], Mapping[str, Any] | None]
    artifact_version: Callable[[str], Mapping[str, Any] | None]
    dispatcher: Callable[[], Any]
    python_runtime: Callable[[], PythonRuntimeSpec]
    bootstrap_code: Callable[[], str]
    python_published: Callable[[str, Callable[[], Kernel], str | None], Any]
    r_published: Callable[[Any], Any]
    bind_candidate: Callable[[Any, Callable[[Any], bool]], Any]
    unbind_candidate: Callable[[Any], Any]
    cancelled: Callable[[], bool]
    event_sink: Callable[[dict[str, Any]], Any]


class _RecoveryKernelCandidate:
    """Unpublished worker owned by exactly one recovery attempt."""

    def __init__(
        self,
        *,
        language: str,
        key: Any,
        factory: Callable[[], Kernel],
        manifest: BootstrapManifest,
    ) -> None:
        self.language = language
        self.key = key
        self.factory = factory
        self.manifest = manifest
        # The durable row is intentionally allocated only during publish.
        self.generation_id = str(uuid.uuid4())
        self.kernel = factory()
        self.adopted = False
        self.observed_environment: dict[str, Any] = {}

    def shutdown(self) -> None:
        if not self.adopted:
            self.kernel.shutdown()

    def interrupt(self) -> bool:
        self.kernel.interrupt()
        return True


class SessionRecoveryRuntime:
    """Bind one session to the build-first verified recovery pipeline."""

    def __init__(self, ports: RecoveryRuntimePorts) -> None:
        self.ports = ports
        self.workspace = Path(ports.workspace).resolve()

    def fresh_manifests(self) -> tuple[BootstrapManifest, ...]:
        runtime = self.ports.python_runtime()
        bootstrap = str(self.ports.bootstrap_code() or "")
        return (
            BootstrapManifest(
                language="python",
                interpreter=runtime.interpreter,
                runtime_version=runtime.runtime_version,
                working_directory=str(self.workspace),
                environment={
                    "environment_name": runtime.environment_name,
                    "environment_root": runtime.environment_root,
                    "is_conda": runtime.is_conda,
                },
                sdk_version=runtime.sdk_version,
                init_hooks=((bootstrap,) if bootstrap.strip() else ()),
                random_seed_policy="fresh_namespace",
            ),
        )

    def run(self, plan: RecoveryActionPlan) -> dict[str, Any]:
        expected = {
            manifest.language: self.ports.kernels.lease(manifest.language)
            for manifest in plan.manifests
        }
        checkpoint = (
            self.ports.checkpoint(plan.checkpoint_id) if plan.checkpoint_id else None
        )
        allowed_tree = checkpoint.get("workspace_tree_id") if checkpoint else None
        allowed_versions = (
            {str(value) for value in (checkpoint.get("artifact_versions") or ())}
            if checkpoint
            else set()
        )

        def hydrate_workspace(_candidate, payload: Mapping[str, Any]) -> None:
            tree_id = str(payload.get("tree_id") or "")
            if not allowed_tree or tree_id != str(allowed_tree):
                raise RuntimeError(
                    "recovery recipe references an unexpected workspace tree"
                )
            restored = self.ports.cas.restore(
                tree_id,
                self.workspace,
                # The checkpoint is the baseline. Managed edits become
                # conflicts while untracked files remain untouched.
                baseline_tree_id=tree_id,
            )
            if not restored.get("applied"):
                conflicts = [
                    str(item.get("path") or "")
                    for item in (restored.get("conflicts") or ())
                ]
                raise RuntimeError(
                    "workspace recovery conflicts: "
                    + (", ".join(conflicts[:20]) or "unknown conflict")
                )

        def hydrate_artifact(_candidate, payload: Mapping[str, Any]) -> None:
            version_id = str(payload.get("version_id") or "")
            if version_id not in allowed_versions:
                raise RuntimeError("recovery recipe references an unexpected artifact")
            version = self.ports.artifact_version(version_id)
            if version is None:
                raise RuntimeError(f"artifact version is missing: {version_id}")
            source = version.get("snapshot_path") or version.get("path")
            if not source:
                raise RuntimeError(f"artifact version has no bytes: {version_id}")
            try:
                data = Path(str(source)).read_bytes()
            except OSError as error:
                raise RuntimeError(
                    f"artifact version bytes are unavailable: {version_id}"
                ) from error
            expected_digest = str(version.get("checksum") or "")
            if expected_digest and hashlib.sha256(data).hexdigest() != expected_digest:
                raise RuntimeError(f"artifact version checksum mismatch: {version_id}")
            live = self._artifact_live_path(version)
            if (
                live is None
                or not live.is_file()
                or (
                    expected_digest
                    and hashlib.sha256(live.read_bytes()).hexdigest() != expected_digest
                )
            ):
                raise RuntimeError(
                    "artifact version is not materialized in the restored "
                    f"workspace: {version_id}"
                )

        def publish(candidate, manifest, source_generation_id):
            published_manifest = manifest.with_observed_environment(
                candidate.observed_environment
            )
            lease = self.ports.kernels.publish_candidate(
                published_manifest.language,
                candidate.key,
                candidate.kernel,
                factory=candidate.factory,
                generation_id=candidate.generation_id,
                expected=expected.get(published_manifest.language),
                recovered_from_generation_id=source_generation_id,
                bootstrap=published_manifest.record(),
            )
            candidate.adopted = True
            if published_manifest.language == "python":
                name = str(
                    published_manifest.environment.get("environment_name")
                    or published_manifest.environment.get("name")
                    or "base"
                )
                root = published_manifest.environment.get("environment_root")
                bin_dir = Path(str(root)) / "bin" if root else None
                self.ports.python_published(
                    name,
                    candidate.factory,
                    (
                        str(bin_dir)
                        if bin_dir is not None and bin_dir.is_dir()
                        else None
                    ),
                )
            else:
                self.ports.r_published(candidate.key)
            return lease

        executor = RecoveryMutationExecutor(
            self.ports.control,
            RecoveryExecutionPorts(
                build_candidate=self._build_candidate,
                bootstrap_candidate=self._bootstrap_candidate,
                hydrate_workspace=hydrate_workspace,
                hydrate_artifact=hydrate_artifact,
                execute_cell=self._execute_cell,
                inspect_symbols=self._inspect_symbols,
                artifact_digest=self._artifact_digest,
                inspect_environment=lambda candidate: (candidate.observed_environment),
                publish_candidate=publish,
                cancelled=self.ports.cancelled,
                candidate_started=lambda candidate: self.ports.bind_candidate(
                    candidate, lambda current: current.interrupt()
                ),
                candidate_finished=self.ports.unbind_candidate,
                event_sink=self.ports.event_sink,
            ),
        )
        result = executor.run(plan)
        if result["status"] == "active" and plan.action_id == "restart_fresh":
            if not any(manifest.language == "r" for manifest in plan.manifests):
                self.ports.kernels.stop(
                    "r", manual=False, reason="fresh_recovery_restart"
                )
        return result

    def kernel_status_event(
        self, result: Mapping[str, Any], recovery_id: str
    ) -> dict[str, Any]:
        python = self.ports.kernels.lease("python")
        r_lease = self.ports.kernels.lease("r")
        return {
            "type": "kernel_status",
            "frame_id": self.ports.root_frame_id,
            "status": result["status"],
            "state": result["status"],
            "generation_id": python.generation_id if python is not None else None,
            "python_generation_id": (
                python.generation_id if python is not None else None
            ),
            "r_generation_id": (r_lease.generation_id if r_lease is not None else None),
            "recovery_id": recovery_id,
        }

    def _build_candidate(self, manifest: BootstrapManifest) -> _RecoveryKernelCandidate:
        if Path(manifest.working_directory).expanduser().resolve() != self.workspace:
            raise RuntimeError(
                "checkpoint working directory does not match this session workspace"
            )
        interpreter = Path(manifest.interpreter).expanduser()
        if not interpreter.is_file():
            raise RuntimeError(
                f"recovery interpreter is unavailable: {manifest.interpreter}"
            )
        environment = dict(manifest.environment or {})
        env_name = environment.get("environment_name") or environment.get("name")
        env_root = environment.get("environment_root") or environment.get("root")
        if manifest.language == "python":
            options = {
                "dispatcher": self.ports.dispatcher(),
                "cwd": str(self.workspace),
                "mode": "repl",
                "python": str(interpreter),
                "env_root": str(env_root) if env_root else None,
                "env_name": str(env_name) if env_name else None,
            }

            def factory() -> Kernel:
                return Kernel(**options)

            key = (
                str(env_name or "base"),
                str(interpreter),
                str(env_root) if env_root else None,
            )
        elif manifest.language == "r":
            from openai4s.kernel.r_kernel import spawn_r_kernel

            def factory() -> Kernel:
                return spawn_r_kernel(cwd=str(self.workspace), rscript=str(interpreter))

            key = str(env_name) if env_name else None
        else:
            raise RuntimeError(f"unsupported recovery language: {manifest.language}")
        return _RecoveryKernelCandidate(
            language=manifest.language,
            key=key,
            factory=factory,
            manifest=manifest,
        )

    def _bootstrap_candidate(
        self,
        candidate: _RecoveryKernelCandidate,
        manifest: BootstrapManifest,
    ) -> None:
        if manifest.language == "r" and manifest.sidecars:
            raise RuntimeError("Python sidecars cannot be loaded into an R recovery")
        # Bootstrap hooks establish the exact capability snapshot/import gate
        # that existed before any original sidecar import.  Frozen modules are
        # then installed from manifest bytes; recovery never follows their
        # informational source_path back to a mutable disk copy.
        for index, hook in enumerate(manifest.init_hooks):
            result = candidate.kernel.execute(str(hook), origin="recovery")
            if result.get("error"):
                raise RuntimeError(f"bootstrap hook {index} failed: {result['error']}")
        for sidecar in manifest.sidecars:
            source = frozen_sidecar_bootstrap_code(sidecar)
            result = candidate.kernel.execute(source, origin="recovery")
            if result.get("error"):
                raise RuntimeError(
                    f"sidecar {sidecar.name!r} failed: {result['error']}"
                )

        if manifest.language == "python":
            payload = _probe_python_environment(candidate.kernel, origin="recovery")
        else:
            payload = _probe_r_environment(
                candidate.kernel,
                interpreter=manifest.interpreter,
                origin="recovery",
            )
        if not payload.get("runtime_version"):
            raise RuntimeError("recovery runtime health check returned no version")
        observed_manifest = manifest.with_observed_environment(payload)
        candidate.observed_environment = {
            **dict(observed_manifest.environment),
            **payload,
            "environment_hash": observed_manifest.environment_hash,
            "package_manifest": [
                {"name": name, "version": version}
                for name, version in observed_manifest.package_manifest
            ],
            "locale": dict(observed_manifest.locale),
        }

    @staticmethod
    def _execute_cell(candidate, code: str, language: str):
        if language != candidate.language:
            return {"error": f"{language} cell cannot run in {candidate.language}"}
        return candidate.kernel.execute(code, origin="recovery")

    @staticmethod
    def _inspect_symbols(candidate, language: str):
        if language != candidate.language:
            return ()
        marker = f"__OPENAI4S_RECOVERY_SYMBOLS_{uuid.uuid4().hex}__"
        if language == "python":
            result = candidate.kernel.execute(
                "import json as __o4s_json\n"
                f"print({marker!r} + __o4s_json.dumps(sorted(globals())))",
                origin="recovery",
            )
            if result.get("error"):
                raise RuntimeError(f"symbol inspection failed: {result['error']}")
            value = _json_after_marker(str(result.get("stdout") or ""), marker)
            return value if isinstance(value, list) else ()
        result = candidate.kernel.execute(
            f'cat("{marker}", paste(ls(envir=.GlobalEnv), collapse="\\n"), "\\n", sep="")',
            origin="recovery",
        )
        if result.get("error"):
            raise RuntimeError(f"symbol inspection failed: {result['error']}")
        output = str(result.get("stdout") or "")
        return output.split(marker, 1)[1].splitlines() if marker in output else ()

    def _artifact_digest(self, _candidate, name: str) -> str | None:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            return None
        target = (self.workspace / relative).resolve()
        if self.workspace not in target.parents or not target.is_file():
            return None
        try:
            return hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            return None

    def _artifact_live_path(self, version: Mapping[str, Any]) -> Path | None:
        recorded_path = version.get("path")
        if recorded_path:
            try:
                relative = (
                    Path(str(recorded_path))
                    .expanduser()
                    .resolve()
                    .relative_to(self.workspace)
                )
                return (self.workspace / relative).resolve()
            except (OSError, ValueError):
                pass
        fallback = Path(str(version.get("filename") or ""))
        if not fallback.parts or fallback.is_absolute() or ".." in fallback.parts:
            return None
        live = (self.workspace / fallback).resolve()
        return live if self.workspace in live.parents else None


def _probe_python_environment(kernel: Kernel, *, origin: str) -> dict[str, Any]:
    """Collect the runtime manifest inside the exact Python worker."""

    marker = f"__OPENAI4S_PY_ENV_{uuid.uuid4().hex}__"
    source = (
        "def __o4s_environment_probe():\n"
        " import importlib.metadata as metadata, json, locale, platform, sys\n"
        " packages = {}\n"
        " try:\n"
        "  distributions = metadata.distributions()\n"
        "  for distribution in distributions:\n"
        "   try:\n"
        "    name = str(distribution.metadata.get('Name') or '').strip()\n"
        "    version = str(distribution.version) if distribution.version is not None else None\n"
        "   except Exception:\n"
        "    continue\n"
        "   if name:\n"
        "    packages.setdefault(name.casefold(), {'name': name, 'version': version})\n"
        " except Exception:\n"
        "  pass\n"
        " try:\n"
        "  import openai4s as sdk\n"
        "  sdk_version = getattr(sdk, '__version__', None)\n"
        " except Exception:\n"
        "  sdk_version = None\n"
        " try:\n"
        "  from openai4s.kernel.provenance import PROVENANCE_VERSION as provenance_version\n"
        " except Exception:\n"
        "  provenance_version = None\n"
        " try:\n"
        "  from openai4s.sdk.host import HOST_CAPABILITY_VERSION as host_capability_version\n"
        " except Exception:\n"
        "  host_capability_version = None\n"
        " locale_manifest = {\n"
        "  'default_encoding': sys.getdefaultencoding(),\n"
        "  'filesystem_encoding': sys.getfilesystemencoding(),\n"
        "  'filesystem_errors': sys.getfilesystemencodeerrors(),\n"
        "  'preferred_encoding': locale.getpreferredencoding(False),\n"
        "  'lc_ctype': locale.setlocale(locale.LC_CTYPE),\n"
        " }\n"
        " return {\n"
        "  'runtime_version': platform.python_version(),\n"
        "  'interpreter': sys.executable, 'prefix': sys.prefix,\n"
        "  'base_prefix': getattr(sys, 'base_prefix', sys.prefix),\n"
        "  'sdk_version': sdk_version,\n"
        "  'provenance_version': provenance_version,\n"
        "  'host_capability_version': host_capability_version,\n"
        "  'package_manifest': sorted(packages.values(), key=lambda item: item['name'].casefold()),\n"
        "  'locale': locale_manifest,\n"
        " }\n"
        f"print({marker!r} + __import__('json').dumps(__o4s_environment_probe(), sort_keys=True, separators=(',', ':')))\n"
        "del __o4s_environment_probe\n"
    )
    result = kernel.execute(source, origin=origin)
    if result.get("error"):
        raise RuntimeError(f"Python environment probe failed: {result['error']}")
    value = _json_after_marker(str(result.get("stdout") or ""), marker)
    if not isinstance(value, dict):
        raise RuntimeError("Python environment probe returned no object")
    return value


def _probe_r_environment(
    kernel: Kernel, *, interpreter: str, origin: str
) -> dict[str, Any]:
    """Collect installed packages and locale inside the exact R worker."""

    marker = f"__OPENAI4S_R_ENV_{uuid.uuid4().hex}__"
    source = (
        "local({\n"
        " enc <- function(value) utils::URLencode(enc2utf8(as.character(value)), reserved=TRUE)\n"
        f' cat("{marker}META\\truntime_version\\t", enc(R.version.string), "\\n", sep="")\n'
        f' cat("{marker}META\\tr_home\\t", enc(R.home()), "\\n", sep="")\n'
        f' cat("{marker}META\\tr_locale\\t", enc(Sys.getlocale()), "\\n", sep="")\n'
        " packages <- tryCatch(installed.packages()[, c('Package','Version'), drop=FALSE], error=function(e) matrix(character(), nrow=0, ncol=2))\n"
        " if (nrow(packages)) {\n"
        "  packages <- packages[order(tolower(packages[, 'Package'])), , drop=FALSE]\n"
        "  for (index in seq_len(nrow(packages))) {\n"
        f'   cat("{marker}PACKAGE\\t", enc(packages[index, "Package"]), "\\t", enc(packages[index, "Version"]), "\\n", sep="")\n'
        "  }\n"
        " }\n"
        "})"
    )
    result = kernel.execute(source, origin=origin)
    if result.get("error"):
        raise RuntimeError(f"R environment probe failed: {result['error']}")
    metadata: dict[str, str] = {}
    packages: list[dict[str, str]] = []
    for line in str(result.get("stdout") or "").splitlines():
        if line.startswith(marker + "META\t"):
            parts = line[len(marker) :].split("\t", 2)
            if len(parts) == 3:
                metadata[parts[1]] = urllib.parse.unquote(parts[2])
        elif line.startswith(marker + "PACKAGE\t"):
            parts = line[len(marker) :].split("\t", 2)
            if len(parts) == 3:
                packages.append(
                    {
                        "name": urllib.parse.unquote(parts[1]),
                        "version": urllib.parse.unquote(parts[2]),
                    }
                )
    if not metadata.get("runtime_version"):
        raise RuntimeError("R environment probe returned no runtime version")
    return {
        "runtime_version": metadata["runtime_version"],
        "interpreter": interpreter,
        "r_home": metadata.get("r_home"),
        "package_manifest": packages,
        "locale": {"r_locale": metadata.get("r_locale", "")},
    }


def bootstrap_r_generation(
    kernels: KernelSupervisor,
    workspace: str | Path,
    lease: KernelLease,
) -> dict[str, Any]:
    """Probe and persist a complete manifest for a newly spawned R slot."""

    workspace = Path(workspace).resolve()
    argv = getattr(lease.kernel, "argv", None) or ()
    interpreter = str(argv[-2]) if len(argv) >= 2 else "Rscript"
    try:
        observed = _probe_r_environment(
            lease.kernel, interpreter=interpreter, origin="system"
        )
    except Exception as error:
        kernels.shutdown_if_current(
            lease,
            reason="bootstrap_failed",
            terminal_state="failed",
        )
        raise RuntimeError(f"R kernel bootstrap failed: {error}") from error
    base_manifest = BootstrapManifest(
        language="r",
        interpreter=interpreter,
        runtime_version=str(observed["runtime_version"]),
        working_directory=str(workspace),
        environment={
            "environment_name": getattr(lease.kernel, "env_name", None),
            "environment_root": getattr(lease.kernel, "env_root", None),
        },
        random_seed_policy="namespace_process_state",
    )
    manifest = base_manifest.with_observed_environment(observed)
    metadata = {**manifest.record(), "status": "active"}
    kernels.record_bootstrap_if_current("r", lease.kernel, metadata, state="active")
    return metadata


def bootstrap_python_generation(
    kernel: Kernel,
    workspace: str | Path,
    bootstrap_code: str,
) -> dict[str, Any]:
    """Run bootstrap and return the complete manifest persisted for Python."""

    code = str(bootstrap_code or "")
    status = "skipped" if not code.strip() else "bootstrapping"
    error_text = None
    try:
        if code.strip():
            result = kernel.execute(code, origin="system")
            if result.get("error"):
                status = "failed"
                error_text = str(result["error"])[:500]
            else:
                status = "active"
    except Exception as error:  # noqa: BLE001 - failure stays durable
        status = "failed"
        error_text = str(error)[:500]

    try:
        observed = _probe_python_environment(kernel, origin="system")
    except Exception as error:  # noqa: BLE001 - failure stays durable
        observed = {
            "runtime_version": "unknown",
            "interpreter": str(getattr(kernel, "python", None) or sys.executable),
            "package_manifest": [],
            "locale": {},
        }
        status = "failed"
        probe_error = str(error)[:500]
        error_text = f"{error_text}; {probe_error}" if error_text else probe_error
    base_manifest = BootstrapManifest(
        language="python",
        interpreter=str(
            observed.get("interpreter")
            or getattr(kernel, "python", None)
            or sys.executable
        ),
        runtime_version=str(observed.get("runtime_version") or "unknown"),
        working_directory=str(
            Path(getattr(kernel, "cwd", None) or workspace).resolve()
        ),
        environment={
            "environment_name": getattr(kernel, "env_name", None),
            "environment_root": getattr(kernel, "env_root", None),
        },
        sdk_version=(
            str(observed["sdk_version"])
            if observed.get("sdk_version") is not None
            else None
        ),
        init_hooks=((code,) if code.strip() else ()),
        random_seed_policy="namespace_process_state",
    )
    manifest = base_manifest.with_observed_environment(observed)
    metadata = {
        **manifest.record(),
        "status": status,
        "bootstrap_code_sha256": (
            hashlib.sha256(code.encode("utf-8")).hexdigest() if code else None
        ),
        "loaded_sidecars": [],
        "sidecar_capture_status": "complete",
        "project_init_hooks": [],
        "environment_name": getattr(kernel, "env_name", None),
        "environment_root": getattr(kernel, "env_root", None),
    }
    if error_text:
        metadata["error"] = error_text
    return metadata


def python_runtime_spec(environment: Any) -> PythonRuntimeSpec:
    """Normalize one discovered Environment for fresh recovery."""

    try:
        from openai4s import __version__ as sdk_version
    except Exception:  # noqa: BLE001 - optional manifest metadata
        sdk_version = None
    return PythonRuntimeSpec(
        interpreter=str(environment.interpreter),
        runtime_version=str(environment.python_version() or "unknown"),
        environment_name=str(environment.name),
        environment_root=(str(environment.root) if environment.is_conda else None),
        is_conda=bool(environment.is_conda),
        sdk_version=sdk_version,
    )


def _json_after_marker(output: str, marker: str) -> Any:
    if marker not in output:
        raise RuntimeError("recovery health probe marker was not returned")
    payload = output.rsplit(marker, 1)[1].splitlines()[0]
    try:
        return json.loads(payload)
    except (TypeError, ValueError) as error:
        raise RuntimeError("recovery health probe returned invalid JSON") from error


__all__ = [
    "PythonRuntimeSpec",
    "RecoveryRuntimePorts",
    "SessionRecoveryRuntime",
    "bootstrap_python_generation",
    "bootstrap_r_generation",
    "python_runtime_spec",
]
