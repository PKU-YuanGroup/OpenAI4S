"""Sandboxed Dynamic Tools authored in a session and promoted by version.

Dynamic implementations are never imported into the Host process.  The Host
validates a small Python subset, freezes a content-addressed manifest, then
launches a fresh ``-I -S`` worker for smoke tests and every invocation.  The
worker receives the source and JSON arguments over stdin, runs inside the same
OS sandbox adapter as scientific kernels, inherits the strict non-secret
environment, and returns one bounded JSON value.

This module intentionally does not mutate the global built-in registry.
``ProxyDynamicTool`` is the trusted class exposed by a session catalog; its
``execute`` behaviour is visible here and only forwards to the isolated worker.
Project/global promotion and version activation are separate, explicitly
approved operations; their implementations still use the exact same worker.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from openai4s.kernel.environment import build_kernel_environment
from openai4s.security.sandbox import KernelSandbox, create_kernel_sandbox
from openai4s.tools.base import Tool
from openai4s.tools.dynamic_scopes import DynamicScopeStore
from openai4s.tools.schema import validate_json_schema

_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
# ``operator`` (attrgetter/methodcaller) and ``string`` (Formatter.get_field)
# perform attribute access from *string literals* the AST attribute guard below
# never sees, giving a full getattr-equivalent that escapes the restricted
# builtins.  They are deliberately excluded from the safe import allowlist.
_SAFE_IMPORTS = frozenset(
    {
        "collections",
        "csv",
        "datetime",
        "decimal",
        "fractions",
        "functools",
        "hashlib",
        "heapq",
        "itertools",
        "json",
        "math",
        "random",
        "re",
        "statistics",
    }
)
_BANNED_CALLS = frozenset(
    {
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "input",
        "locals",
        "open",
        "setattr",
        "vars",
    }
)
_BANNED_ATTRIBUTES = frozenset(
    {
        "__class__",
        "__dict__",
        "__globals__",
        "__mro__",
        "__subclasses__",
        "__code__",
        "__closure__",
        "__getattribute__",
        # String-literal-driven reflection helpers; defence in depth even though
        # their host modules are no longer importable above.
        "attrgetter",
        "methodcaller",
        "get_field",
    }
)
_MAX_SOURCE_CHARS = 100_000
_MAX_INPUT_CHARS = 200_000
_MAX_OUTPUT_CHARS = 1_000_000
_DEFAULT_TTL_S = 3600.0
_SCOPED_EXPIRES_AT = 253_402_300_799.0


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _scoped_manifest_core(
    *,
    manifest: "DynamicToolManifest",
    scope: str,
    scope_id: str,
    source_project_id: str,
    source_root_frame_id: str,
) -> dict[str, Any]:
    """Return the immutable, content-addressed v2 promotion payload."""

    return {
        "version": 2,
        "name": manifest.name,
        "description": manifest.description,
        "input_schema": dict(manifest.input_schema),
        "output_schema": dict(manifest.output_schema),
        "implementation": manifest.implementation,
        "imports": list(manifest.imports),
        "permissions": list(manifest.permissions),
        "scope": scope,
        "scope_id": scope_id,
        "session_id": source_root_frame_id,
        "ttl_s": manifest.ttl_s,
        "source_manifest_id": manifest.manifest_id,
        "source_project_id": source_project_id,
        "source_root_frame_id": source_root_frame_id,
    }


def validate_dynamic_source(source: str) -> tuple[str, ...]:
    """Return imported roots after rejecting Host-unsafe Python constructs."""

    if not isinstance(source, str) or not source.strip():
        raise ValueError("dynamic tool implementation must be non-empty Python")
    if len(source) > _MAX_SOURCE_CHARS:
        raise ValueError("dynamic tool implementation is too large")
    try:
        tree = ast.parse(source, filename="<dynamic-tool>", mode="exec")
    except SyntaxError as error:
        raise ValueError(f"dynamic tool does not parse: {error}") from error
    imports: set[str] = set()
    execute_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "execute"
    ]
    if len(execute_functions) != 1 or isinstance(
        execute_functions[0], ast.AsyncFunctionDef
    ):
        raise ValueError(
            "dynamic tool must define exactly one synchronous execute(args)"
        )
    function = execute_functions[0]
    positional = [*function.args.posonlyargs, *function.args.args]
    if len(positional) != 1 or function.args.vararg or function.args.kwarg:
        raise ValueError(
            "dynamic execute must accept exactly one positional args object"
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                raise ValueError("dynamic tool relative imports are forbidden")
            imports.add((node.module or "").split(".", 1)[0])
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BANNED_CALLS or node.func.id == "__import__":
                raise ValueError(f"dynamic tool call is forbidden: {node.func.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in _BANNED_ATTRIBUTES or node.attr.startswith("_"):
                raise ValueError(f"dynamic tool attribute is forbidden: {node.attr}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            raise ValueError("dynamic tool global/nonlocal state is forbidden")
    denied = sorted(imports - _SAFE_IMPORTS)
    if denied:
        raise ValueError("dynamic tool imports are not allowed: " + ", ".join(denied))
    return tuple(sorted(imports))


@dataclass(frozen=True)
class DynamicToolManifest:
    name: str
    description: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    implementation: str
    imports: tuple[str, ...]
    permissions: tuple[str, ...]
    scope: str
    session_id: str
    ttl_s: float
    created_at: float
    expires_at: float
    manifest_id: str
    record_version: int = 1
    scope_id: str | None = None
    source_manifest_id: str | None = None
    source_project_id: str | None = None
    source_root_frame_id: str | None = None

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def record(self) -> dict[str, Any]:
        record = {
            "version": self.record_version,
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
            "output_schema": dict(self.output_schema),
            "implementation": self.implementation,
            "imports": list(self.imports),
            "permissions": list(self.permissions),
            "scope": self.scope,
            "session_id": self.session_id,
            "ttl_s": self.ttl_s,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "manifest_id": self.manifest_id,
        }
        if self.record_version >= 2:
            record.update(
                {
                    "scope_id": self.scope_id,
                    "source_manifest_id": self.source_manifest_id,
                    "source_project_id": self.source_project_id,
                    "source_root_frame_id": self.source_root_frame_id,
                }
            )
        return record


_WORKER_CODE = r"""
import builtins
import json
import sys

SAFE_IMPORTS = set(sys.argv[1].split(",")) if len(sys.argv) > 1 and sys.argv[1] else set()
real_import = builtins.__import__
SAFE_BUILTINS = {
    name: getattr(builtins, name)
    for name in (
        "ArithmeticError", "AssertionError", "Exception", "IndexError", "KeyError",
        "LookupError", "RuntimeError", "TypeError", "ValueError", "ZeroDivisionError",
        "abs", "all", "any", "bool", "bytes", "callable", "chr", "complex",
        "dict", "divmod", "enumerate", "filter", "float", "format", "frozenset",
        "hash", "hex", "int", "isinstance", "issubclass", "iter", "len", "list",
        "map", "max", "min", "next", "oct", "ord", "pow", "range", "repr",
        "reversed", "round", "set", "slice", "sorted", "str", "sum", "tuple", "zip"
    )
}

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = str(name).split(".", 1)[0]
    if level or root not in SAFE_IMPORTS:
        raise ImportError("dynamic tool import denied: " + root)
    return real_import(name, globals, locals, fromlist, level)

payload = json.load(sys.stdin)
source = payload["implementation"]
namespace = {
    "__name__": "__openai4s_dynamic_tool__",
    "__builtins__": dict(SAFE_BUILTINS, __import__=guarded_import),
}
exec(compile(source, "<dynamic-tool-worker>", "exec"), namespace, namespace)
result = namespace["execute"](payload["arguments"])
encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
if len(encoded) > 1000000:
    raise RuntimeError("dynamic tool output exceeds 1000000 characters")
sys.stdout.write(encoded)
""".strip()


class DynamicToolWorker:
    """One-shot, no-secret worker.  The Host never imports implementation code."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        sandbox_factory: Callable[[str | Path], KernelSandbox] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.workspace = Path(workspace).expanduser().resolve()
        self._sandbox_factory = sandbox_factory or (
            lambda path: create_kernel_sandbox(path, mode="enforce")
        )
        self.timeout_s = float(timeout_s)

    def invoke(
        self, manifest: DynamicToolManifest, arguments: Mapping[str, Any]
    ) -> Any:
        payload = {
            "implementation": manifest.implementation,
            "arguments": dict(arguments),
        }
        encoded = _canonical_json(payload)
        if len(encoded) > _MAX_INPUT_CHARS:
            raise RuntimeError("dynamic tool input is too large")
        sandbox = self._sandbox_factory(self.workspace)
        try:
            status = getattr(sandbox, "status", None)
            if not bool(getattr(status, "enforced", False)):
                raise RuntimeError("dynamic tools require an enforced OS sandbox")
            command = sandbox.wrap_command(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    _WORKER_CODE,
                    ",".join(manifest.imports),
                ]
            )
            environment = build_kernel_environment(
                mode="dynamic-tool",
                cwd=str(self.workspace),
                repo_root=str(Path(__file__).resolve().parents[2]),
            )
            completed = subprocess.run(
                command,
                input=encoded,
                cwd=str(self.workspace),
                env=sandbox.apply_environment(environment),
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"dynamic tool timed out after {self.timeout_s:g}s"
            ) from error
        finally:
            sandbox.close()
        if completed.returncode != 0:
            detail = " ".join((completed.stderr or "").strip().split())[:1000]
            raise RuntimeError(
                "dynamic tool worker failed"
                + (f": {detail}" if detail else f" (exit {completed.returncode})")
            )
        if len(completed.stdout) > _MAX_OUTPUT_CHARS:
            raise RuntimeError("dynamic tool output is too large")
        try:
            return json.loads(completed.stdout)
        except (TypeError, ValueError) as error:
            raise RuntimeError("dynamic tool returned invalid JSON") from error


Approval = Callable[[DynamicToolManifest, str], bool]


class DynamicToolRegistry:
    """Scope-aware Dynamic Tool versions with session-first resolution.

    Session manifests remain in the existing per-root directory and keep their
    v1 format.  Project/global manifests and append-only activation events live
    in a shared :class:`DynamicScopeStore`.  Effective resolution is always
    ``session > exact project > global``.
    """

    def __init__(
        self,
        session_id: str,
        workspace: str | Path,
        storage_dir: str | Path,
        *,
        project_id: str | None = None,
        scope_storage_dir: str | Path | None = None,
        worker: DynamicToolWorker | None = None,
        approval: Approval | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session_id = str(session_id).strip()
        if not self.session_id:
            raise ValueError("dynamic tools require a root session id")
        # Standalone callers from the v1 API did not know project_id.  Binding
        # them to their own root preserves compatibility without creating an
        # unscoped project namespace.  Production composition always supplies
        # the canonical Store project id.
        self.project_id = str(project_id or self.session_id).strip()
        self.workspace = Path(workspace).resolve()
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        shared_root = (
            Path(scope_storage_dir).expanduser().resolve()
            if scope_storage_dir is not None
            else self.storage_dir / "_scoped"
        )
        self.scope_store = DynamicScopeStore(shared_root, clock=clock)
        self.worker = worker or DynamicToolWorker(self.workspace)
        self.approval = approval or (lambda _manifest, _operation: False)
        self.clock = clock
        self._session_manifests: dict[str, DynamicToolManifest] = {}
        self._project_manifests: dict[str, DynamicToolManifest] = {}
        self._global_manifests: dict[str, DynamicToolManifest] = {}
        # Kept as an effective compatibility snapshot for callers that only
        # inspected this old private attribute in a debugger.
        self._manifests: dict[str, DynamicToolManifest] = {}
        self.load_errors: list[str] = []
        self._load_error_set: set[str] = set()
        self.last_audit_event: dict[str, Any] | None = None
        self._load_session_manifests()
        self._refresh_scoped_manifests()

    def define(self, spec: Mapping[str, Any]) -> DynamicToolManifest:
        name = str(spec.get("name") or "")
        if not _TOOL_NAME.fullmatch(name):
            raise ValueError("dynamic tool name is not provider-portable")
        if name in {"bash", "submit_output", "finalize_response"}:
            raise ValueError(f"dynamic tool name is reserved: {name}")
        if self.session_manifest(name) is not None:
            raise ValueError(f"session dynamic tool already exists: {name!r}")
        description = " ".join(str(spec.get("description") or "").split())
        if not description:
            raise ValueError("dynamic tool description is required")
        implementation = str(spec.get("implementation") or "")
        imports = validate_dynamic_source(implementation)
        input_schema = self._schema(spec.get("input_schema"), "input_schema")
        output_schema = self._schema(spec.get("output_schema"), "output_schema")
        permissions = tuple(
            sorted({str(item) for item in spec.get("permissions") or ()})
        )
        if permissions:
            raise ValueError(
                "session dynamic tools cannot request Host/filesystem/network permissions"
            )
        ttl = self._ttl(spec.get("ttl_s", _DEFAULT_TTL_S))
        created = self.clock()
        core = {
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "implementation": implementation,
            "imports": list(imports),
            "permissions": list(permissions),
            "scope": "session",
            "session_id": self.session_id,
            "ttl_s": ttl,
        }
        manifest = DynamicToolManifest(
            **core,
            created_at=created,
            expires_at=created + ttl,
            manifest_id="dyn-" + _manifest_hash(core),
        )
        smoke_args = spec.get("smoke_args")
        if smoke_args is None:
            smoke_args = self._minimal_smoke_args(input_schema)
        error = self._validation_error(smoke_args, input_schema)
        if error:
            raise ValueError("dynamic tool smoke_args: " + error)
        result = self.worker.invoke(manifest, smoke_args)
        error = self._validation_error(result, output_schema)
        if error:
            raise ValueError("dynamic tool smoke output: " + error)
        self._write_manifest(manifest)
        self._session_manifests[name] = manifest
        self._rebuild_effective()
        return manifest

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        manifest = self.get(name)
        if manifest is None:
            raise KeyError(f"unknown or expired dynamic tool {name!r}")
        return self.invoke_manifest(manifest, arguments)

    def invoke_manifest(
        self,
        manifest: DynamicToolManifest,
        arguments: Mapping[str, Any],
    ) -> Any:
        """Invoke the exact version resolved by the trusted ProxyTool.

        Re-resolving by name here would allow an activation race to validate
        against one schema and execute another implementation.
        """

        error = self._validation_error(arguments, manifest.input_schema)
        if error:
            raise ValueError("dynamic tool arguments: " + error)
        result = self.worker.invoke(manifest, arguments)
        error = self._validation_error(result, manifest.output_schema)
        if error:
            raise RuntimeError("dynamic tool output: " + error)
        return result

    def get(self, name: str) -> DynamicToolManifest | None:
        self.purge_expired()
        self._refresh_scoped_manifests()
        return self._manifests.get(name)

    def session_manifest(self, name: str) -> DynamicToolManifest | None:
        self.purge_expired()
        return self._session_manifests.get(name)

    def tools(self) -> tuple["ProxyDynamicTool", ...]:
        self.purge_expired()
        self._refresh_scoped_manifests()
        return tuple(
            ProxyDynamicTool(manifest, self)
            for manifest in sorted(self._manifests.values(), key=lambda item: item.name)
        )

    def promote(
        self,
        name: str,
        scope: str,
        *,
        approved: bool = False,
    ) -> DynamicToolManifest:
        if scope not in {"project", "global"}:
            raise ValueError("dynamic tool promotion scope must be project or global")
        manifest = self.session_manifest(name)
        if manifest is None:
            raise KeyError(f"no session Dynamic Tool to promote: {name!r}")
        if not approved and not self.approval(manifest, f"promote:{scope}"):
            raise PermissionError("dynamic tool promotion requires human approval")
        scope_id = self._scope_id(scope)
        core = _scoped_manifest_core(
            manifest=manifest,
            scope=scope,
            scope_id=scope_id,
            source_project_id=self.project_id,
            source_root_frame_id=self.session_id,
        )
        now = self.clock()
        promoted = DynamicToolManifest(
            name=manifest.name,
            description=manifest.description,
            input_schema=manifest.input_schema,
            output_schema=manifest.output_schema,
            implementation=manifest.implementation,
            imports=manifest.imports,
            permissions=manifest.permissions,
            scope=scope,
            session_id=manifest.session_id,
            ttl_s=manifest.ttl_s,
            created_at=now,
            expires_at=_SCOPED_EXPIRES_AT,
            manifest_id="dyn-" + _manifest_hash(core),
            record_version=2,
            scope_id=scope_id,
            source_manifest_id=manifest.manifest_id,
            source_project_id=self.project_id,
            source_root_frame_id=self.session_id,
        )
        self.scope_store.write_manifest(promoted.record())
        # Reuse the canonical immutable record when the exact content version
        # had already been promoted earlier (its original created_at wins).
        promoted = self._scoped_version(name, scope, scope_id, promoted.manifest_id)
        self.last_audit_event = self.scope_store.append_activation(
            operation="promote",
            scope=scope,
            scope_id=scope_id,
            name=name,
            manifest_id=promoted.manifest_id,
            actor_root_frame_id=self.session_id,
            actor_project_id=self.project_id,
        )
        self._refresh_scoped_manifests()
        return promoted

    def activate(
        self,
        name: str,
        scope: str,
        manifest_id: str,
        *,
        approved: bool = False,
    ) -> DynamicToolManifest:
        if not approved:
            raise PermissionError("dynamic tool activation requires human approval")
        scope_id = self._scope_id(scope)
        manifest = self._scoped_version(name, scope, scope_id, manifest_id)
        self.last_audit_event = self.scope_store.append_activation(
            operation="activate",
            scope=scope,
            scope_id=scope_id,
            name=name,
            manifest_id=manifest.manifest_id,
            actor_root_frame_id=self.session_id,
            actor_project_id=self.project_id,
        )
        self._refresh_scoped_manifests()
        return manifest

    def rollback(
        self,
        name: str,
        scope: str,
        *,
        approved: bool = False,
    ) -> DynamicToolManifest:
        if not approved:
            raise PermissionError("dynamic tool rollback requires human approval")
        scope_id = self._scope_id(scope)
        versions = self._scoped_versions(scope, scope_id, name=name)
        self.last_audit_event = self.scope_store.append_rollback(
            scope=scope,
            scope_id=scope_id,
            name=name,
            available_manifest_ids={item.manifest_id for item in versions},
            actor_root_frame_id=self.session_id,
            actor_project_id=self.project_id,
        )
        target = self._scoped_version(
            name,
            scope,
            scope_id,
            str(self.last_audit_event["manifest_id"]),
        )
        self._refresh_scoped_manifests()
        return target

    def versions(
        self,
        *,
        name: str | None = None,
        scope: str | None = None,
    ) -> dict[str, Any]:
        self.purge_expired()
        self._refresh_scoped_manifests()
        effective_ids = {
            item_name: manifest.manifest_id
            for item_name, manifest in self._manifests.items()
        }
        scopes = (scope,) if scope else ("project", "global")
        versions: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        for item_scope in scopes:
            scope_id = self._scope_id(item_scope)
            parsed = self._scoped_versions(item_scope, scope_id, name=name)
            active, active_errors = self._active_ids(item_scope, scope_id, parsed)
            self._record_load_errors(active_errors)
            for manifest in parsed:
                versions.append(
                    {
                        **self._public_version(manifest),
                        "active": active.get(manifest.name) == manifest.manifest_id,
                        "effective": (
                            effective_ids.get(manifest.name) == manifest.manifest_id
                        ),
                    }
                )
            audit, audit_errors = self.scope_store.events(
                scope=item_scope,
                scope_id=scope_id,
                name=name,
            )
            self._record_load_errors(audit_errors)
            events.extend(self._public_audit_event(event) for event in audit)
        versions.sort(
            key=lambda item: (
                str(item.get("name") or ""),
                str(item.get("scope") or ""),
                float(item.get("created_at") or 0),
                str(item.get("manifest_id") or ""),
            )
        )
        events.sort(
            key=lambda item: (
                int(item.get("created_at_ns") or 0),
                str(item.get("event_id") or ""),
            )
        )
        return {"versions": versions, "events": events}

    def purge_expired(self) -> int:
        now = self.clock()
        expired = [
            name
            for name, item in self._session_manifests.items()
            if now >= item.expires_at
        ]
        for name in expired:
            self._session_manifests.pop(name, None)
        if expired:
            self._rebuild_effective()
        return len(expired)

    def _write_manifest(self, manifest: DynamicToolManifest) -> None:
        if manifest.scope != "session":
            raise ValueError("scoped dynamic manifests use DynamicScopeStore")
        destination = self.storage_dir / f"{manifest.manifest_id}.json"
        if destination.exists():
            return
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(_canonical_json(manifest.record()), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)

    def _load_session_manifests(self) -> None:
        """Restore valid, unexpired session manifests without importing code."""

        for path in sorted(self.storage_dir.glob("dyn-*.json")):
            try:
                if path.stat().st_size > _MAX_SOURCE_CHARS * 2:
                    raise ValueError("manifest is too large")
                record = json.loads(path.read_text("utf-8"))
                if not isinstance(record, dict) or record.get("version") != 1:
                    raise ValueError("unsupported manifest record")
                if record.get("scope") != "session":
                    continue
                if record.get("session_id") != self.session_id:
                    raise ValueError("session identity mismatch")
                source = str(record.get("implementation") or "")
                imports = validate_dynamic_source(source)
                if tuple(record.get("imports") or ()) != imports:
                    raise ValueError("validated import set mismatch")
                permissions = tuple(record.get("permissions") or ())
                if permissions:
                    raise ValueError("session manifest requests permissions")
                name = str(record.get("name") or "")
                description = " ".join(str(record.get("description") or "").split())
                if not _TOOL_NAME.fullmatch(name) or not description:
                    raise ValueError("manifest identity is invalid")
                if name in {"bash", "submit_output", "finalize_response"}:
                    raise ValueError("manifest identity is reserved")
                input_schema = self._schema(record.get("input_schema"), "input_schema")
                output_schema = self._schema(
                    record.get("output_schema"), "output_schema"
                )
                ttl = self._ttl(record.get("ttl_s"))
                created = float(record.get("created_at"))
                expires = float(record.get("expires_at"))
                if not math.isfinite(created) or not math.isfinite(expires):
                    raise ValueError("manifest timestamps are invalid")
                if abs((created + ttl) - expires) > 1e-6:
                    raise ValueError("manifest expiry does not match its TTL")
                core = {
                    "name": name,
                    "description": description,
                    "input_schema": input_schema,
                    "output_schema": output_schema,
                    "implementation": source,
                    "imports": list(imports),
                    "permissions": list(permissions),
                    "scope": "session",
                    "session_id": self.session_id,
                    "ttl_s": ttl,
                }
                manifest_id = "dyn-" + _manifest_hash(core)
                if record.get("manifest_id") != manifest_id or path.stem != manifest_id:
                    raise ValueError("manifest content hash mismatch")
                manifest = DynamicToolManifest(
                    name=core["name"],
                    description=core["description"],
                    input_schema=input_schema,
                    output_schema=output_schema,
                    implementation=source,
                    imports=imports,
                    permissions=permissions,
                    scope="session",
                    session_id=self.session_id,
                    ttl_s=ttl,
                    created_at=created,
                    expires_at=expires,
                    manifest_id=manifest_id,
                )
                if self.clock() < expires:
                    self._session_manifests[manifest.name] = manifest
            except Exception as error:  # noqa: BLE001 - corrupt files stay inert
                self._record_load_error(f"{path.name}: {error}")
        self._rebuild_effective()

    def _refresh_scoped_manifests(self) -> None:
        project_versions = self._scoped_versions("project", self.project_id)
        global_versions = self._scoped_versions("global", "")
        project_active, project_errors = self._active_ids(
            "project", self.project_id, project_versions
        )
        global_active, global_errors = self._active_ids("global", "", global_versions)
        self._record_load_errors([*project_errors, *global_errors])
        self._project_manifests = {
            manifest.name: manifest
            for manifest in project_versions
            if project_active.get(manifest.name) == manifest.manifest_id
        }
        self._global_manifests = {
            manifest.name: manifest
            for manifest in global_versions
            if global_active.get(manifest.name) == manifest.manifest_id
        }
        self._rebuild_effective()

    def _rebuild_effective(self) -> None:
        # Later updates win. Session definitions intentionally shadow promoted
        # methods without mutating or deactivating the durable lower scope.
        self._manifests = {
            **self._global_manifests,
            **self._project_manifests,
            **self._session_manifests,
        }

    def _scoped_versions(
        self,
        scope: str,
        scope_id: str,
        *,
        name: str | None = None,
    ) -> list[DynamicToolManifest]:
        records, errors = self.scope_store.manifest_records()
        self._record_load_errors(errors)
        result: list[DynamicToolManifest] = []
        for record in records:
            try:
                manifest = self._scoped_manifest_from_record(record)
                if manifest.scope != scope or manifest.scope_id != scope_id:
                    continue
                if name is None or manifest.name == name:
                    result.append(manifest)
            except Exception as error:  # noqa: BLE001 - invalid versions stay inert
                self._record_load_error(
                    f"{record.get('manifest_id') or 'scoped manifest'}: {error}"
                )
        result.sort(key=lambda item: (item.created_at, item.manifest_id))
        return result

    def _scoped_manifest_from_record(
        self, record: Mapping[str, Any]
    ) -> DynamicToolManifest:
        if int(record.get("version") or 0) != 2:
            raise ValueError("unsupported scoped dynamic manifest version")
        scope = str(record.get("scope") or "")
        scope_id = str(record.get("scope_id") or "")
        if scope not in {"project", "global"}:
            raise ValueError("scoped manifest scope is invalid")
        if scope == "project" and not scope_id:
            raise ValueError("project scoped manifest lacks project_id")
        if scope == "global" and scope_id:
            raise ValueError("global scoped manifest has a scope_id")
        source_root = str(record.get("source_root_frame_id") or "")
        source_project = str(record.get("source_project_id") or "")
        if not source_root or not source_project:
            raise ValueError("scoped manifest lacks bound source scope")
        if scope == "project" and source_project != scope_id:
            raise ValueError("project scoped manifest source/scope mismatch")
        source = str(record.get("implementation") or "")
        imports = validate_dynamic_source(source)
        if tuple(record.get("imports") or ()) != imports:
            raise ValueError("validated import set mismatch")
        permissions = tuple(str(item) for item in (record.get("permissions") or ()))
        if permissions:
            raise ValueError("dynamic scoped manifest requests permissions")
        name = str(record.get("name") or "")
        description = " ".join(str(record.get("description") or "").split())
        if not _TOOL_NAME.fullmatch(name) or not description:
            raise ValueError("scoped manifest identity is invalid")
        if name in {"bash", "submit_output", "finalize_response"}:
            raise ValueError("scoped manifest identity is reserved")
        input_schema = self._schema(record.get("input_schema"), "input_schema")
        output_schema = self._schema(record.get("output_schema"), "output_schema")
        ttl = self._ttl(record.get("ttl_s"))
        created = float(record.get("created_at"))
        expires = float(record.get("expires_at"))
        if (
            not math.isfinite(created)
            or not math.isfinite(expires)
            or expires <= created
        ):
            raise ValueError("scoped manifest timestamps are invalid")
        if expires != _SCOPED_EXPIRES_AT:
            raise ValueError("scoped manifest expiry is invalid")
        source_manifest_id = str(record.get("source_manifest_id") or "")
        if not re.fullmatch(r"dyn-[0-9a-f]{64}", source_manifest_id):
            raise ValueError("scoped manifest source id is invalid")
        source_session = str(record.get("session_id") or "")
        if source_session != source_root:
            raise ValueError("scoped manifest root identity mismatch")
        core = {
            "version": 2,
            "name": name,
            "description": description,
            "input_schema": input_schema,
            "output_schema": output_schema,
            "implementation": source,
            "imports": list(imports),
            "permissions": list(permissions),
            "scope": scope,
            "scope_id": scope_id,
            "session_id": source_session,
            "ttl_s": ttl,
            "source_manifest_id": source_manifest_id,
            "source_project_id": source_project,
            "source_root_frame_id": source_root,
        }
        manifest_id = "dyn-" + _manifest_hash(core)
        if record.get("manifest_id") != manifest_id:
            raise ValueError("scoped manifest content hash mismatch")
        return DynamicToolManifest(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            implementation=source,
            imports=imports,
            permissions=permissions,
            scope=scope,
            session_id=source_session,
            ttl_s=ttl,
            created_at=created,
            expires_at=expires,
            manifest_id=manifest_id,
            record_version=2,
            scope_id=scope_id,
            source_manifest_id=source_manifest_id,
            source_project_id=source_project,
            source_root_frame_id=source_root,
        )

    def _active_ids(
        self,
        scope: str,
        scope_id: str,
        versions: Iterable[DynamicToolManifest],
    ) -> tuple[dict[str, str], list[str]]:
        by_id = {manifest.manifest_id: manifest for manifest in versions}
        names = {manifest.name for manifest in versions}
        active: dict[str, str] = {}
        errors: list[str] = []
        for name in names:
            manifest_id, event_errors = self.scope_store.active_manifest_id(
                scope=scope,
                scope_id=scope_id,
                name=name,
            )
            errors.extend(event_errors)
            if manifest_id is None:
                continue
            manifest = by_id.get(manifest_id)
            if manifest is None or manifest.name != name:
                errors.append(
                    f"activation for {scope}:{name} references unavailable manifest "
                    f"{manifest_id}"
                )
                continue
            active[name] = manifest_id
        return active, errors

    def _scoped_version(
        self,
        name: str,
        scope: str,
        scope_id: str,
        manifest_id: str,
    ) -> DynamicToolManifest:
        match = next(
            (
                manifest
                for manifest in self._scoped_versions(scope, scope_id, name=name)
                if manifest.manifest_id == manifest_id
            ),
            None,
        )
        if match is None:
            raise KeyError(
                f"manifest {manifest_id!r} is not a {scope} version of {name!r} "
                "in this scope"
            )
        return match

    def _scope_id(self, scope: str) -> str:
        if scope == "project":
            if not self.project_id:
                raise ValueError("project dynamic tools require project_id")
            return self.project_id
        if scope == "global":
            return ""
        raise ValueError("dynamic tool scope must be project or global")

    def _record_load_error(self, message: str) -> None:
        if message not in self._load_error_set:
            self._load_error_set.add(message)
            self.load_errors.append(message)

    def _record_load_errors(self, messages: Iterable[str]) -> None:
        for message in messages:
            self._record_load_error(str(message))

    def _public_version(self, manifest: DynamicToolManifest) -> dict[str, Any]:
        same_source_project = manifest.source_project_id == self.project_id
        return {
            "name": manifest.name,
            "description": manifest.description,
            "input_schema": dict(manifest.input_schema),
            "output_schema": dict(manifest.output_schema),
            "imports": list(manifest.imports),
            "scope": manifest.scope,
            "scope_id": manifest.scope_id,
            "source_manifest_id": manifest.source_manifest_id,
            # A global tool is consumable across projects, but its originating
            # session/project identifiers are not model-facing cross-project
            # metadata. The immutable record and Host audit retain them.
            "source_project_id": (
                manifest.source_project_id if same_source_project else None
            ),
            "source_root_frame_id": (
                manifest.source_root_frame_id if same_source_project else None
            ),
            "created_at": manifest.created_at,
            "manifest_id": manifest.manifest_id,
        }

    def _public_audit_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        public = self.scope_store.public_event(event)
        if public.get("actor_project_id") != self.project_id:
            public["actor_project_id"] = None
            public["actor_root_frame_id"] = None
        return public

    @staticmethod
    def _schema(value: Any, name: str) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise ValueError(f"dynamic tool {name} must be a JSON schema object")
        schema = dict(value)
        if schema.get("type") is None:
            schema["type"] = "object"
        if schema.get("type") == "object":
            schema.setdefault("properties", {})
            schema.setdefault("additionalProperties", False)
        # Validate the schema definition by running a benign value through the
        # shared validator. Definition errors are raised by that implementation.
        validate_json_schema({}, schema)
        return schema

    @staticmethod
    def _validation_error(value: Any, schema: Mapping[str, Any]) -> str | None:
        issues = validate_json_schema(value, dict(schema))
        return "; ".join(str(issue) for issue in issues) if issues else None

    @staticmethod
    def _minimal_smoke_args(schema: Mapping[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        properties = schema.get("properties") or {}
        for name in schema.get("required") or ():
            prop = properties.get(name) or {}
            kind = prop.get("type")
            if "default" in prop:
                result[name] = prop["default"]
            elif prop.get("enum"):
                result[name] = prop["enum"][0]
            elif kind == "string":
                result[name] = "smoke"
            elif kind in {"number", "integer"}:
                result[name] = max(0, prop.get("minimum", 0))
            elif kind == "boolean":
                result[name] = False
            elif kind == "array":
                result[name] = []
            elif kind == "object":
                result[name] = {}
            else:
                raise ValueError(f"cannot synthesize smoke value for required {name!r}")
        return result

    @staticmethod
    def _ttl(value: Any) -> float:
        try:
            ttl = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("dynamic tool ttl_s must be a number") from error
        if not math.isfinite(ttl) or ttl <= 0 or ttl > 24 * 3600:
            raise ValueError("dynamic tool ttl_s must be within (0, 86400]")
        return ttl


class ProxyDynamicTool(Tool):
    """Trusted Tool subclass whose behaviour remains an isolated worker call."""

    read_only = True
    requires_approval = False
    side_effect_class = "read_only"
    resource_key_prefix = "dynamic_tool"
    unknown_properties = "forbid"

    def __init__(
        self, manifest: DynamicToolManifest, registry: DynamicToolRegistry
    ) -> None:
        object.__setattr__(self, "manifest", manifest)
        object.__setattr__(self, "registry", registry)
        super().__init__(
            name=manifest.name,
            host_method=f"dynamic:{manifest.manifest_id}",
            description=manifest.description,
            parameters={
                "properties": dict(manifest.input_schema.get("properties") or {}),
                "required": list(manifest.input_schema.get("required") or []),
            },
            read_only=True,
            requires_approval=False,
            side_effect_class="read_only",
            resource_key_prefix="dynamic_tool",
            resource_target_default=manifest.manifest_id,
            unknown_properties=(
                "allow"
                if manifest.input_schema.get("additionalProperties") is True
                else "forbid"
            ),
        )

    def execute(self, context: Any, arguments: dict) -> Any:
        del context
        return self.registry.invoke_manifest(self.manifest, arguments)

    def input_schema(self) -> dict:
        """Preserve the complete manifest schema for provider and Host checks."""

        return dict(self.manifest.input_schema)


__all__ = [
    "DynamicToolManifest",
    "DynamicToolRegistry",
    "DynamicToolWorker",
    "ProxyDynamicTool",
    "validate_dynamic_source",
]
