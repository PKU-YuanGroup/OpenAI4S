"""Sandboxed, session-scoped tools authored by the model.

Dynamic implementations are never imported into the Host process.  The Host
validates a small Python subset, freezes a content-addressed manifest, then
launches a fresh ``-I -S`` worker for smoke tests and every invocation.  The
worker receives the source and JSON arguments over stdin, runs inside the same
OS sandbox adapter as scientific kernels, inherits the strict non-secret
environment, and returns one bounded JSON value.

This module intentionally does not mutate the global built-in registry.
``ProxyDynamicTool`` is the trusted class exposed by a session catalog; its
``execute`` behaviour is visible here and only forwards to the isolated worker.
Project/global promotion is a separate, explicitly approved operation.
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
from openai4s.tools.schema import validate_json_schema

_TOOL_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
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
        "operator",
        "random",
        "re",
        "statistics",
        "string",
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
    }
)
_MAX_SOURCE_CHARS = 100_000
_MAX_INPUT_CHARS = 200_000
_MAX_OUTPUT_CHARS = 1_000_000
_DEFAULT_TTL_S = 3600.0


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _manifest_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


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
    if len(execute_functions) != 1 or isinstance(execute_functions[0], ast.AsyncFunctionDef):
        raise ValueError("dynamic tool must define exactly one synchronous execute(args)")
    function = execute_functions[0]
    positional = [*function.args.posonlyargs, *function.args.args]
    if len(positional) != 1 or function.args.vararg or function.args.kwarg:
        raise ValueError("dynamic execute must accept exactly one positional args object")

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

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def record(self) -> dict[str, Any]:
        return {
            "version": 1,
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


_WORKER_CODE = r'''
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
'''.strip()


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

    def invoke(self, manifest: DynamicToolManifest, arguments: Mapping[str, Any]) -> Any:
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
    """Session-local manifests with TTL, smoke testing, and approved promotion."""

    def __init__(
        self,
        session_id: str,
        workspace: str | Path,
        storage_dir: str | Path,
        *,
        worker: DynamicToolWorker | None = None,
        approval: Approval | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session_id = session_id
        self.workspace = Path(workspace).resolve()
        self.storage_dir = Path(storage_dir).resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.worker = worker or DynamicToolWorker(self.workspace)
        self.approval = approval or (lambda _manifest, _operation: False)
        self.clock = clock
        self._manifests: dict[str, DynamicToolManifest] = {}

    def define(self, spec: Mapping[str, Any]) -> DynamicToolManifest:
        name = str(spec.get("name") or "")
        if not _TOOL_NAME.fullmatch(name):
            raise ValueError("dynamic tool name is not provider-portable")
        if name in {"bash", "submit_output", "finalize_response"}:
            raise ValueError(f"dynamic tool name is reserved: {name}")
        description = " ".join(str(spec.get("description") or "").split())
        if not description:
            raise ValueError("dynamic tool description is required")
        implementation = str(spec.get("implementation") or "")
        imports = validate_dynamic_source(implementation)
        input_schema = self._schema(spec.get("input_schema"), "input_schema")
        output_schema = self._schema(spec.get("output_schema"), "output_schema")
        permissions = tuple(sorted({str(item) for item in spec.get("permissions") or ()}))
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
        self._manifests[name] = manifest
        return manifest

    def invoke(self, name: str, arguments: Mapping[str, Any]) -> Any:
        manifest = self.get(name)
        if manifest is None:
            raise KeyError(f"unknown or expired dynamic tool {name!r}")
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
        return self._manifests.get(name)

    def tools(self) -> tuple["ProxyDynamicTool", ...]:
        self.purge_expired()
        return tuple(
            ProxyDynamicTool(manifest, self)
            for manifest in sorted(self._manifests.values(), key=lambda item: item.name)
        )

    def promote(self, name: str, scope: str) -> DynamicToolManifest:
        if scope not in {"project", "global"}:
            raise ValueError("dynamic tool promotion scope must be project or global")
        manifest = self.get(name)
        if manifest is None:
            raise KeyError(name)
        if not self.approval(manifest, f"promote:{scope}"):
            raise PermissionError("dynamic tool promotion requires human approval")
        # Promotion freezes a new manifest; it does not import implementation
        # into this process or mutate the session instance in place.
        core = manifest.record()
        core.pop("manifest_id", None)
        core.pop("created_at", None)
        core.pop("expires_at", None)
        core["scope"] = scope
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
            expires_at=253_402_300_799.0,
            manifest_id="dyn-" + _manifest_hash(core),
        )
        self._write_manifest(promoted)
        return promoted

    def purge_expired(self) -> int:
        now = self.clock()
        expired = [name for name, item in self._manifests.items() if now >= item.expires_at]
        for name in expired:
            self._manifests.pop(name, None)
        return len(expired)

    def _write_manifest(self, manifest: DynamicToolManifest) -> None:
        destination = self.storage_dir / f"{manifest.manifest_id}.json"
        if destination.exists():
            return
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(_canonical_json(manifest.record()), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)

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
        return self.registry.invoke(self.manifest.name, arguments)

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
