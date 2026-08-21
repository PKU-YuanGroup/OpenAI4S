"""Build-first, replay-safe, verifiable scientific Kernel recovery.

This module is deliberately protocol-neutral.  A candidate may be the existing
Python manager, the R sibling, or a future Jupyter adapter; callers inject the
small build/bootstrap/execute/inspect/publish callbacks.  The old generation is
never stopped or replaced here.  ``publish`` is called exactly once and only
after every required validation succeeds.

Recovery recipes contain code, but an explicit ``safe`` label is not trusted by
itself.  Python AST and conservative language-neutral scans reject completion,
shell, credentials, external writes, background/delegated work, remote jobs,
and unknown Host calls.  Rejected/non-replayable steps produce ``partial``—the
system never claims that an arbitrary in-memory namespace survived.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

REPLAY_SAFE = "safe"
REPLAY_CONDITIONAL = "conditional"
REPLAY_NEVER = "never"
REPLAY_POLICIES = frozenset({REPLAY_SAFE, REPLAY_CONDITIONAL, REPLAY_NEVER})

_UNSAFE_HOST_METHODS = frozenset(
    {
        "submit_output",
        "bash",
        "credentials_set",
        "credentials_get",
        "exec_background",
        "exec_interrupt",
        "delegate",
        "stop_child",
        "send_message",
        "compute_submit",
        "compute_cancel",
        "compute_close",
        "fold",
        "score_mutations",
        "mcp_call",
        "write_file",
        "edit_file",
        "save_artifact",
        "request_network_access",
        "endpoints_register",
        "skills_edit",
        "skills_publish",
        "skills_delete",
        "env_setup",
    }
)
_SAFE_HOST_METHODS = frozenset(
    {
        "capabilities",
        "current_model",
        "list_models",
        "query",
        "query_schema",
        "artifacts",
        "artifact_path",
        "frames",
        "lineage_get",
        "lineage_graph",
        "skills_list",
        "skills_get",
        "skills_read",
        "search_skills",
        "env_list",
        "todo_read",
        "plan_read",
    }
)
_RISKY_TEXT = re.compile(
    r"(?i)\b(subprocess|os\.system|system2?|shell|requests?\.|urllib\.|"
    r"socket\.|curl\b|wget\b|ssh\b|scp\b|writeLines\s*\(|saveRDS\s*\()"
)
# ``importlib.import_module`` accepts package-directory spellings that are not
# Python identifiers (the Skill loader deliberately emits that form for names
# such as ``foo bar`` and ``x:y``).  Preserve that contract while rejecting
# hierarchy/path injection and control characters in an untrusted load event.
_SIDECAR_MODULE_SEGMENT = re.compile(r"[^./\\\x00-\x1f\x7f]+")
_UNFROZEN_BUILTIN_EXECUTORS = frozenset({"exec", "eval", "compile"})
_UNFROZEN_ATTRIBUTE_EXECUTORS = frozenset(
    {
        "spec_from_file_location",
        "SourceFileLoader",
        "SourcelessFileLoader",
        "load_module",
        "exec_module",
        "run_path",
    }
)
# Import roots that reach the process/filesystem/network directly.  A recovery
# cell that imports any of these is never replay-safe regardless of its label.
_REPLAY_RISKY_IMPORT_ROOTS = frozenset(
    {
        "os",
        "pathlib",
        "shutil",
        "subprocess",
        "socket",
        "socketserver",
        "requests",
        "urllib",
        "http",
        "ftplib",
        "smtplib",
        "telnetlib",
        "asyncio",
        "multiprocessing",
        "ctypes",
        "mmap",
        "tempfile",
        "ssl",
        "webbrowser",
        "pty",
    }
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class SidecarManifest:
    """Exact bytes of one sidecar that was actually loaded."""

    name: str
    source: bytes
    order: int
    exports: tuple[str, ...] = ()
    import_mode: str = "module"
    source_path: str | None = None
    local_import_roots: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("sidecar name must be non-empty")
        if isinstance(self.order, bool) or self.order < 0:
            raise ValueError("sidecar order must be non-negative")
        if not isinstance(self.source, bytes):
            raise TypeError("sidecar source must be bytes")
        name_parts = self.name.split(".")
        parent_parts = tuple(dict.fromkeys(name_parts[:-1]))
        raw_local_roots = self.local_import_roots
        if not raw_local_roots:
            # Direct construction is still useful for tests and migration
            # helpers.  Runtime records must carry the complete alias set, but
            # a manually created manifest can at least seal every parent named
            # by its module spelling (or its sole legacy module name).
            raw_local_roots = parent_parts or tuple(name_parts[:1])
        if not isinstance(raw_local_roots, (list, tuple)):
            raise TypeError("sidecar local import roots must be a sequence")
        local_import_roots = tuple(raw_local_roots)
        if (
            not local_import_roots
            or len(local_import_roots) > 2
            or len(local_import_roots) != len(set(local_import_roots))
            or any(
                not isinstance(root, str)
                or _SIDECAR_MODULE_SEGMENT.fullmatch(root) is None
                for root in local_import_roots
            )
        ):
            raise ValueError("sidecar local import roots are invalid")
        if not set(parent_parts).issubset(local_import_roots):
            raise ValueError("sidecar local import roots omit a module parent")
        object.__setattr__(self, "local_import_roots", local_import_roots)
        label = self.source_path or f"<{self.name}>"
        try:
            tree = compile(
                self.source,
                label,
                "exec",
                flags=ast.PyCF_ONLY_AST,
                dont_inherit=True,
            )
        except (SyntaxError, ValueError) as error:
            raise ValueError(
                f"sidecar {self.name!r} does not compile: {error}"
            ) from error
        # The runtime exposes direct and qualified aliases for the same Skill
        # package.  Every alias root can reach mutable siblings, so checking
        # only the spelling used for this event would miss e.g.
        # ``from skills.victim.helper import VALUE`` after a direct
        # ``victim.kernel`` load.
        local_roots = frozenset(local_import_roots)

        def is_local_import(imported: str) -> bool:
            root = imported.partition(".")[0]
            return root in local_roots

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__file__":
                raise ValueError(
                    f"sidecar {self.name!r} depends on mutable package resources"
                )
            if isinstance(node, ast.ImportFrom):
                imported = node.module or ""
                if node.level or is_local_import(imported):
                    raise ValueError(
                        f"sidecar {self.name!r} has an unfrozen local import"
                    )
                if any(
                    alias.name
                    in _UNFROZEN_BUILTIN_EXECUTORS | _UNFROZEN_ATTRIBUTE_EXECUTORS
                    for alias in node.names
                ):
                    raise ValueError(
                        f"sidecar {self.name!r} uses an unfrozen code loader"
                    )
            elif isinstance(node, ast.Import) and any(
                is_local_import(alias.name) for alias in node.names
            ):
                raise ValueError(f"sidecar {self.name!r} has an unfrozen local import")
            elif isinstance(node, ast.Call):
                target = node.func
                unsafe_executor = (
                    isinstance(target, ast.Name)
                    and target.id in _UNFROZEN_BUILTIN_EXECUTORS
                ) or (
                    isinstance(target, ast.Attribute)
                    and target.attr in _UNFROZEN_ATTRIBUTE_EXECUTORS
                )
                if unsafe_executor:
                    raise ValueError(
                        f"sidecar {self.name!r} uses an unfrozen code loader"
                    )

    @property
    def sha256(self) -> str:
        return _sha256(self.source)

    def record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "sha256": self.sha256,
            "source_b64": base64.b64encode(self.source).decode("ascii"),
            "order": self.order,
            "exports": list(self.exports),
            "import_mode": self.import_mode,
            "source_path": self.source_path,
            "local_import_roots": list(self.local_import_roots),
        }

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "SidecarManifest":
        try:
            source = base64.b64decode(str(value["source_b64"]), validate=True)
        except Exception as error:  # noqa: BLE001 — imported manifests are untrusted
            raise ValueError("invalid sidecar source encoding") from error
        raw_order = value.get("order", 0)
        if isinstance(raw_order, bool) or not isinstance(raw_order, int):
            raise ValueError("sidecar order must be an integer")
        raw_local_roots = value.get("local_import_roots")
        if not isinstance(raw_local_roots, (list, tuple)) or not raw_local_roots:
            raise ValueError("sidecar record requires local import roots")
        sidecar = cls(
            name=str(value.get("name") or ""),
            source=source,
            order=raw_order,
            exports=tuple(str(item) for item in (value.get("exports") or ())),
            import_mode=str(value.get("import_mode") or "module"),
            source_path=(
                str(value["source_path"]) if value.get("source_path") else None
            ),
            local_import_roots=tuple(raw_local_roots),
        )
        if value.get("sha256") != sidecar.sha256:
            raise ValueError(f"sidecar hash mismatch: {sidecar.name}")
        return sidecar


@dataclass(frozen=True)
class BootstrapManifest:
    language: str
    interpreter: str
    runtime_version: str
    working_directory: str
    environment: Mapping[str, Any] = field(default_factory=dict)
    sdk_version: str | None = None
    provenance_version: str | None = None
    host_capability_version: str | None = None
    package_manifest: tuple[tuple[str, str | None], ...] = ()
    locale: Mapping[str, str] = field(default_factory=dict)
    environment_hash: str | None = None
    random_seed_policy: str | None = None
    sidecars: tuple[SidecarManifest, ...] = ()
    init_hooks: tuple[str, ...] = ()
    version: int = 2

    def __post_init__(self) -> None:
        if self.version not in {1, 2}:
            raise ValueError("unsupported bootstrap manifest version")
        if self.language not in {"python", "r"}:
            raise ValueError("bootstrap language must be python or r")
        if not self.interpreter or not self.working_directory:
            raise ValueError("bootstrap interpreter and working_directory are required")
        orders = [sidecar.order for sidecar in self.sidecars]
        if orders != sorted(orders) or len(orders) != len(set(orders)):
            raise ValueError("sidecar load order must be unique and sorted")
        names = [name.casefold() for name, _version in self.package_manifest]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("bootstrap package manifest must be unique and sorted")
        if any(not name or len(name) > 512 for name, _ in self.package_manifest):
            raise ValueError("bootstrap package name is missing or too long")
        if any(
            version is not None and len(version) > 512
            for _, version in self.package_manifest
        ):
            raise ValueError("bootstrap package version is too long")
        if self.environment_hash is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.environment_hash
        ):
            raise ValueError("bootstrap environment hash must be sha256")

    def record(self) -> dict[str, Any]:
        record = {
            "version": self.version,
            "language": self.language,
            "interpreter": self.interpreter,
            "runtime_version": self.runtime_version,
            "working_directory": self.working_directory,
            "environment": dict(self.environment),
            "sdk_version": self.sdk_version,
            "provenance_version": self.provenance_version,
            "random_seed_policy": self.random_seed_policy,
            "sidecars": [sidecar.record() for sidecar in self.sidecars],
            "init_hooks": list(self.init_hooks),
        }
        if self.version >= 2:
            record.update(
                {
                    "host_capability_version": self.host_capability_version,
                    "package_manifest": [
                        {"name": name, "version": version}
                        for name, version in self.package_manifest
                    ],
                    "locale": dict(self.locale),
                    "environment_hash": self.environment_hash,
                }
            )
        return record

    @property
    def manifest_id(self) -> str:
        return "boot-" + _sha256(_canonical_bytes(self.record()))

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "BootstrapManifest":
        version = int(value.get("version") or 0)
        if version not in {1, 2}:
            raise ValueError("unsupported bootstrap manifest version")
        capture_status = value.get("sidecar_capture_status")
        if capture_status not in (None, "complete"):
            raise ValueError(
                "bootstrap sidecar capture is incomplete: "
                + str(value.get("sidecar_capture_error") or capture_status)
            )
        sidecars = tuple(
            SidecarManifest.from_record(item)
            for item in (value.get("sidecars") or ())
            if isinstance(item, Mapping)
        )
        packages: list[tuple[str, str | None]] = []
        if version >= 2:
            raw_packages = value.get("package_manifest") or ()
            if (
                not isinstance(raw_packages, (list, tuple))
                or len(raw_packages) > 50_000
            ):
                raise ValueError("bootstrap package manifest is invalid")
            for item in raw_packages:
                if not isinstance(item, Mapping):
                    raise ValueError("bootstrap package record is invalid")
                name = str(item.get("name") or "").strip()
                package_version = (
                    str(item["version"]) if item.get("version") is not None else None
                )
                packages.append((name, package_version))
        packages.sort(key=lambda item: item[0].casefold())
        raw_locale = value.get("locale") or {}
        if not isinstance(raw_locale, Mapping):
            raise ValueError("bootstrap locale manifest is invalid")
        return cls(
            language=str(value.get("language") or ""),
            interpreter=str(value.get("interpreter") or ""),
            runtime_version=str(value.get("runtime_version") or ""),
            working_directory=str(value.get("working_directory") or ""),
            environment=(
                dict(value["environment"])
                if isinstance(value.get("environment"), Mapping)
                else {}
            ),
            sdk_version=(
                str(value["sdk_version"]) if value.get("sdk_version") else None
            ),
            provenance_version=(
                str(value["provenance_version"])
                if value.get("provenance_version")
                else None
            ),
            host_capability_version=(
                str(value["host_capability_version"])
                if value.get("host_capability_version") is not None
                else None
            ),
            package_manifest=tuple(packages),
            locale={str(key): str(item) for key, item in raw_locale.items()},
            environment_hash=(
                str(value["environment_hash"])
                if value.get("environment_hash")
                else None
            ),
            random_seed_policy=(
                str(value["random_seed_policy"])
                if value.get("random_seed_policy")
                else None
            ),
            sidecars=sidecars,
            init_hooks=tuple(str(item) for item in (value.get("init_hooks") or ())),
            version=version,
        )

    def with_observed_environment(
        self, value: Mapping[str, Any]
    ) -> "BootstrapManifest":
        """Return a v2 manifest bound to facts measured inside one worker.

        Candidate recovery and normal generation bootstrap both use this path,
        so package/locale/runtime claims never come from the daemon's own
        interpreter by accident.
        """

        raw_packages = value.get("package_manifest") or ()
        packages: dict[str, tuple[str, str | None]] = {}
        if isinstance(raw_packages, (list, tuple)):
            for item in raw_packages[:50_000]:
                if not isinstance(item, Mapping):
                    continue
                name = str(item.get("name") or "").strip()
                if not name or len(name) > 512:
                    continue
                package_version = (
                    str(item["version"])[:512]
                    if item.get("version") is not None
                    else None
                )
                packages.setdefault(name.casefold(), (name, package_version))
        normalized_packages = tuple(
            sorted(packages.values(), key=lambda item: item[0].casefold())
        )
        raw_locale = value.get("locale") or {}
        locale_manifest = (
            {str(key)[:128]: str(item)[:512] for key, item in raw_locale.items()}
            if isinstance(raw_locale, Mapping)
            else {}
        )
        environment = dict(self.environment)
        for source_key, target_key in (
            ("prefix", "interpreter_prefix"),
            ("base_prefix", "base_prefix"),
            ("r_home", "r_home"),
        ):
            if value.get(source_key) is not None:
                environment[target_key] = str(value[source_key])
        bound = replace(
            self,
            version=2,
            interpreter=str(value.get("interpreter") or self.interpreter),
            runtime_version=str(value.get("runtime_version") or self.runtime_version),
            environment=environment,
            sdk_version=(
                str(value["sdk_version"])
                if value.get("sdk_version") is not None
                else self.sdk_version
            ),
            provenance_version=(
                str(value["provenance_version"])
                if value.get("provenance_version") is not None
                else self.provenance_version
            ),
            host_capability_version=(
                str(value["host_capability_version"])
                if value.get("host_capability_version") is not None
                else self.host_capability_version
            ),
            package_manifest=normalized_packages,
            locale=locale_manifest,
            environment_hash=None,
        )
        fingerprint = {
            "language": bound.language,
            "interpreter": bound.interpreter,
            "runtime_version": bound.runtime_version,
            "environment": dict(bound.environment),
            "sdk_version": bound.sdk_version,
            "provenance_version": bound.provenance_version,
            "host_capability_version": bound.host_capability_version,
            "package_manifest": [list(item) for item in bound.package_manifest],
            "locale": dict(bound.locale),
        }
        return replace(
            bound,
            environment_hash=_sha256(_canonical_bytes(fingerprint)),
        )


def sidecar_from_load_event(value: Mapping[str, Any]) -> SidecarManifest:
    """Validate one worker-observed successful sidecar import.

    The worker emits the exact source bytes used for that import.  Recovery
    never follows ``source_path`` back to the mutable filesystem; the path is
    retained only as provenance/traceback context.
    """

    if str(value.get("event") or "") != "sidecar_loaded":
        raise ValueError("invalid sidecar load event")
    module = str(value.get("module") or "").strip()
    parts = module.split(".")
    if (
        len(parts) not in {2, 3}
        or parts[-1] != "kernel"
        or any(_SIDECAR_MODULE_SEGMENT.fullmatch(part) is None for part in parts)
    ):
        raise ValueError("sidecar load event requires a *.kernel module")
    raw_order = value.get("order")
    if isinstance(raw_order, bool) or not isinstance(raw_order, int):
        raise ValueError("sidecar load event order must be an integer")
    observed_sha256 = value.get("sha256") or value.get("sidecar_sha256")
    if (
        not isinstance(value.get("expected_sha256"), str)
        or value.get("expected_sha256") != observed_sha256
    ):
        raise ValueError("sidecar load event does not match its bootstrap hash")
    raw_local_roots = value.get("local_import_roots")
    if not isinstance(raw_local_roots, (list, tuple)) or not raw_local_roots:
        raise ValueError("sidecar load event requires local import roots")
    if any(not isinstance(root, str) for root in raw_local_roots):
        raise ValueError("sidecar load event local roots are invalid")
    local_root_set = set(raw_local_roots)
    if not set(parts[:-1]).issubset(local_root_set):
        raise ValueError("sidecar load event local roots omit a module parent")
    if len(parts) == 3 and local_root_set != set(parts[:-1]):
        raise ValueError("qualified sidecar load event has unexpected local roots")
    return SidecarManifest.from_record(
        {
            "name": module,
            "sha256": observed_sha256,
            "source_b64": value.get("source_b64"),
            "order": raw_order,
            "exports": value.get("exports") or (),
            "import_mode": value.get("import_mode") or "module",
            "source_path": value.get("source_path"),
            "local_import_roots": raw_local_roots,
        }
    )


def merge_bootstrap_sidecar_loads(
    bootstrap: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Merge exact, ordered runtime imports into a generation manifest.

    Existing entries are immutable.  An identical already-recorded event is
    idempotent; a conflicting duplicate or an order gap fails closed so a
    corrupted worker/result cannot silently rewrite recovery history.
    """

    manifest = BootstrapManifest.from_record(bootstrap)
    sidecars = list(manifest.sidecars)
    summaries = [
        dict(item)
        for item in (bootstrap.get("loaded_sidecars") or ())
        if isinstance(item, Mapping)
    ]
    for value in events:
        sidecar = sidecar_from_load_event(value)
        if sidecar.order < len(sidecars):
            if sidecars[sidecar.order] != sidecar:
                raise ValueError(f"conflicting sidecar load at order {sidecar.order}")
            continue
        if sidecar.order != len(sidecars):
            raise ValueError(
                f"sidecar load order gap: expected {len(sidecars)}, "
                f"observed {sidecar.order}"
            )
        sidecars.append(sidecar)
        summaries.append(
            {
                "name": str(value.get("skill_name") or sidecar.name),
                "module": sidecar.name,
                "sha256": sidecar.sha256,
                "order": sidecar.order,
                "source_path": sidecar.source_path,
                "version": (
                    str(value["version"]) if value.get("version") is not None else None
                ),
            }
        )
    merged = replace(manifest, sidecars=tuple(sidecars))
    result = dict(bootstrap)
    result.update(merged.record())
    result["loaded_sidecars"] = summaries
    result["sidecar_capture_status"] = "complete"
    result.pop("sidecar_capture_error", None)
    return result


def frozen_sidecar_bootstrap_code(sidecar: SidecarManifest) -> str:
    """Return worker-side code that installs one frozen sidecar module.

    The generated code decodes and re-hashes the manifest bytes inside the
    candidate kernel, creates namespace-package parents without executing Host
    code, and only publishes the module to its parent after ``exec`` succeeds.
    It never reads ``source_path``.
    """

    if sidecar.import_mode != "module":
        raise ValueError(
            f"unsupported frozen sidecar import mode: {sidecar.import_mode!r}"
        )
    record = sidecar.record()
    module_parts = sidecar.name.split(".")
    alias_packages: set[str] = set()
    if len(module_parts) >= 2 and module_parts[-1] == "kernel":
        skill_directory = module_parts[-2]
        for root in sidecar.local_import_roots:
            alias_packages.add(
                skill_directory
                if root == skill_directory
                else f"{root}.{skill_directory}"
            )
    current_parent = sidecar.name.rpartition(".")[0]
    if current_parent:
        alias_packages.add(current_parent)
    ordered_alias_packages = tuple(
        sorted(alias_packages, key=lambda name: (name.count("."), name))
    )
    alias_names = tuple(
        sorted({sidecar.name, *(f"{package}.kernel" for package in alias_packages)})
    )
    return (
        "import base64 as __o4s_b64, hashlib as __o4s_hashlib\n"
        "import importlib.machinery as __o4s_machinery\n"
        "import sys as __o4s_sys, types as __o4s_types\n"
        f"__o4s_record = {record!r}\n"
        "__o4s_source = __o4s_b64.b64decode("
        "__o4s_record['source_b64'], validate=True)\n"
        "if __o4s_hashlib.sha256(__o4s_source).hexdigest() != "
        "__o4s_record['sha256']:\n"
        "    raise RuntimeError('frozen sidecar hash mismatch')\n"
        "__o4s_name = __o4s_record['name']\n"
        "__o4s_name_parts = __o4s_name.split('.')\n"
        "if len(__o4s_name_parts) not in (2, 3) or "
        "__o4s_name_parts[-1] != 'kernel':\n"
        "    raise RuntimeError('invalid frozen sidecar module name')\n"
        "__o4s_skill_directory = __o4s_name_parts[-2]\n"
        "__o4s_policy = globals()\n"
        "__o4s_skill_dirs = __o4s_policy.get('_o4s_skill_dirs')\n"
        "__o4s_skill_entries = __o4s_policy.get('_o4s_skill_entries')\n"
        "__o4s_direct_dirs = __o4s_policy.get('_o4s_direct_skill_dirs')\n"
        "__o4s_collections = __o4s_policy.get('_o4s_collection_members')\n"
        "__o4s_catalog = __o4s_policy.get('_o4s_catalog_namespace')\n"
        "__o4s_denied = __o4s_policy.get('_o4s_denied_skills')\n"
        "__o4s_disabled = __o4s_policy.get('_o4s_disabled_skills')\n"
        "__o4s_order_state = __o4s_policy.get('_o4s_skill_load_order')\n"
        "if (\n"
        "    not isinstance(__o4s_skill_dirs, set)\n"
        "    or not isinstance(__o4s_skill_entries, dict)\n"
        "    or not isinstance(__o4s_direct_dirs, set)\n"
        "    or not isinstance(__o4s_collections, dict)\n"
        "    or not isinstance(__o4s_catalog, str)\n"
        "    or not isinstance(__o4s_denied, set)\n"
        "    or not isinstance(__o4s_disabled, set)\n"
        "    or not isinstance(__o4s_order_state, list)\n"
        "    or len(__o4s_order_state) != 1\n"
        "    or not isinstance(__o4s_order_state[0], int)\n"
        "    or isinstance(__o4s_order_state[0], bool)\n"
        "):\n"
        "    raise RuntimeError('frozen sidecar alias policy is unavailable')\n"
        "__o4s_entry = __o4s_skill_entries.get(__o4s_skill_directory)\n"
        "if (\n"
        "    __o4s_skill_directory not in __o4s_skill_dirs\n"
        "    or not isinstance(__o4s_entry, dict)\n"
        "    or (__o4s_entry.get('sidecar') or {}).get('sha256')\n"
        "       != __o4s_record['sha256']\n"
        "):\n"
        "    raise RuntimeError('frozen sidecar is not bound to the bootstrap')\n"
        "if (\n"
        "    __o4s_skill_directory in __o4s_denied\n"
        "    or __o4s_skill_directory in __o4s_disabled\n"
        "):\n"
        "    raise RuntimeError('frozen sidecar is denied by capability policy')\n"
        "__o4s_expected_roots = {__o4s_skill_directory}\n"
        "if __o4s_skill_directory in __o4s_direct_dirs:\n"
        "    __o4s_expected_roots.add(__o4s_catalog)\n"
        "for __o4s_prefix, __o4s_members in __o4s_collections.items():\n"
        "    if __o4s_skill_directory in __o4s_members:\n"
        "        __o4s_expected_roots.add(__o4s_prefix)\n"
        "if set(__o4s_record['local_import_roots']) != __o4s_expected_roots:\n"
        "    raise RuntimeError('frozen sidecar alias policy mismatch')\n"
        "if len(__o4s_name_parts) == 3:\n"
        "    __o4s_qualifier = __o4s_name_parts[0]\n"
        "    if __o4s_qualifier == __o4s_catalog:\n"
        "        __o4s_qualified = __o4s_skill_directory in __o4s_direct_dirs\n"
        "    else:\n"
        "        __o4s_qualified = __o4s_skill_directory in "
        "__o4s_collections.get(__o4s_qualifier, ())\n"
        "    if not __o4s_qualified:\n"
        "        raise RuntimeError('frozen sidecar module alias is not trusted')\n"
        "__o4s_label = '<recovery-sidecar:' + __o4s_name + '>'\n"
        "__o4s_code = compile(__o4s_source, __o4s_label, 'exec')\n"
        "__o4s_parent_name = __o4s_name.rpartition('.')[0]\n"
        f"__o4s_alias_package_names = {ordered_alias_packages!r}\n"
        f"__o4s_alias_names = {alias_names!r}\n"
        "__o4s_alias_packages = {}\n"
        "for __o4s_alias_package_name in __o4s_alias_package_names:\n"
        "    __o4s_parts = __o4s_alias_package_name.split('.')\n"
        "    __o4s_parent = None\n"
        "    for __o4s_index in range(len(__o4s_parts)):\n"
        "        __o4s_package_name = '.'.join(__o4s_parts[:__o4s_index + 1])\n"
        "        __o4s_package = __o4s_sys.modules.get(__o4s_package_name)\n"
        "        __o4s_exact_alias = __o4s_index == len(__o4s_parts) - 1\n"
        "        __o4s_spec = getattr(__o4s_package, '__spec__', None)\n"
        "        if (\n"
        "            __o4s_package is not None\n"
        "            and getattr(__o4s_spec, 'loader', None) is not None\n"
        "        ):\n"
        "            raise RuntimeError(\n"
        "                'frozen sidecar alias collides with a loaded module: '"
        "                + __o4s_package_name\n"
        "            )\n"
        "        if __o4s_package is None or __o4s_exact_alias:\n"
        "            __o4s_package = __o4s_types.ModuleType(__o4s_package_name)\n"
        "            __o4s_package.__package__ = __o4s_package_name\n"
        "            __o4s_package.__spec__ = __o4s_machinery.ModuleSpec("
        "__o4s_package_name, loader=None, is_package=True)\n"
        "            __o4s_sys.modules[__o4s_package_name] = __o4s_package\n"
        "            if __o4s_parent is not None:\n"
        "                setattr(__o4s_parent, __o4s_parts[__o4s_index], "
        "__o4s_package)\n"
        "        # Every exact alias is a sealed namespace. The shared root is\n"
        "        # sealed too, while the Skill finder can still synthesize other\n"
        "        # known packages during ordered recovery.\n"
        "        __o4s_package.__path__ = []\n"
        "        __o4s_parent = __o4s_package\n"
        "    __o4s_alias_packages[__o4s_alias_package_name] = __o4s_parent\n"
        "__o4s_missing = object()\n"
        "__o4s_previous = {\n"
        "    __o4s_alias: __o4s_sys.modules.get(__o4s_alias, __o4s_missing)\n"
        "    for __o4s_alias in __o4s_alias_names\n"
        "}\n"
        "__o4s_module = __o4s_types.ModuleType(__o4s_name)\n"
        "__o4s_module.__file__ = __o4s_label\n"
        "__o4s_module.__package__ = __o4s_parent_name\n"
        "__o4s_module.__loader__ = None\n"
        "__o4s_module.__spec__ = __o4s_machinery.ModuleSpec("
        "__o4s_name, loader=None, origin=__o4s_label)\n"
        "for __o4s_alias in __o4s_alias_names:\n"
        "    __o4s_sys.modules[__o4s_alias] = __o4s_module\n"
        "    __o4s_alias_parent = __o4s_alias.rpartition('.')[0]\n"
        "    __o4s_alias_package = __o4s_alias_packages.get(__o4s_alias_parent)\n"
        "    if __o4s_alias_package is not None:\n"
        "        setattr(__o4s_alias_package, 'kernel', __o4s_module)\n"
        "__o4s_previous_recovery_active = __o4s_policy.get(\n"
        "    '_o4s_frozen_recovery_active', False\n"
        ")\n"
        "__o4s_policy['_o4s_frozen_recovery_active'] = True\n"
        "try:\n"
        "    exec(__o4s_code, __o4s_module.__dict__)\n"
        "except BaseException:\n"
        "    for __o4s_alias, __o4s_old in __o4s_previous.items():\n"
        "        if __o4s_old is __o4s_missing:\n"
        "            __o4s_sys.modules.pop(__o4s_alias, None)\n"
        "        else:\n"
        "            __o4s_sys.modules[__o4s_alias] = __o4s_old\n"
        "        __o4s_alias_parent = __o4s_alias.rpartition('.')[0]\n"
        "        __o4s_alias_package = __o4s_alias_packages.get("
        "__o4s_alias_parent)\n"
        "        if (\n"
        "            __o4s_alias_package is not None\n"
        "            and getattr(__o4s_alias_package, 'kernel', None) "
        "is __o4s_module\n"
        "        ):\n"
        "            if __o4s_old is __o4s_missing:\n"
        "                delattr(__o4s_alias_package, 'kernel')\n"
        "            else:\n"
        "                setattr(__o4s_alias_package, 'kernel', __o4s_old)\n"
        "    raise\n"
        "finally:\n"
        "    __o4s_policy['_o4s_frozen_recovery_active'] = "
        "__o4s_previous_recovery_active\n"
        "__o4s_order_state[0] = max(\n"
        "    __o4s_order_state[0], __o4s_record['order'] + 1\n"
        ")\n"
        "__o4s_module.__openai4s_frozen_sidecar_sha256__ = "
        "__o4s_record['sha256']\n"
    )


@dataclass(frozen=True)
class RecoveryStep:
    kind: str
    payload: Mapping[str, Any]
    replay_policy: str = REPLAY_NEVER
    step_id: str = field(default_factory=lambda: f"rs-{uuid.uuid4().hex[:12]}")

    def __post_init__(self) -> None:
        if self.replay_policy not in REPLAY_POLICIES:
            raise ValueError(f"unknown replay policy: {self.replay_policy}")
        if not self.kind.strip():
            raise ValueError("recovery step kind must be non-empty")


@dataclass(frozen=True)
class RecoveryRecipe:
    steps: tuple[RecoveryStep, ...] = ()
    required_symbols: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    artifact_hashes: Mapping[str, str] = field(default_factory=dict)
    environment_requirements: Mapping[str, Any] = field(default_factory=dict)
    namespace_coverage: str = "empty"

    def __post_init__(self) -> None:
        if self.namespace_coverage not in {"empty", "verified", "unverified"}:
            raise ValueError(f"unknown namespace coverage: {self.namespace_coverage!r}")


@dataclass(frozen=True)
class RecoveryResult:
    recovery_id: str
    status: str
    source_generation_id: str | None
    candidate_generation_id: str | None
    manifest_id: str
    issues: tuple[dict[str, Any], ...] = ()
    replayed_steps: tuple[str, ...] = ()
    skipped_steps: tuple[str, ...] = ()


class Candidate(Protocol):
    generation_id: str

    def shutdown(self) -> None: ...


JournalSink = Callable[[dict[str, Any]], Any]


class RecoveryCancelled(RuntimeError):
    """Raised internally when the exact recovery ticket is cancelled."""


def replay_safety_error(
    code: str,
    *,
    language: str,
    declared_host_methods: Iterable[str] = (),
) -> str | None:
    """Return why a cell cannot be replayed, or ``None`` when admissible."""

    if not isinstance(code, str) or not code.strip():
        return "empty recovery cell"
    methods = {str(item) for item in declared_host_methods}
    unsafe = sorted(methods & _UNSAFE_HOST_METHODS)
    if unsafe:
        return "unsafe Host methods: " + ", ".join(unsafe)
    unknown = sorted(methods - _UNSAFE_HOST_METHODS - _SAFE_HOST_METHODS)
    if unknown:
        return "unknown Host methods: " + ", ".join(unknown)
    if _RISKY_TEXT.search(code):
        return "cell contains direct process, filesystem, or network execution"
    if language == "python":
        try:
            tree = ast.parse(code)
        except SyntaxError as error:
            return f"cell does not parse: {error}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # ``from <module> import <name>`` — the risky root is the module
                # (``node.module``), never the imported symbol names.  Inspecting
                # ``alias.name`` let ``from shutil import rmtree`` slip past.
                module = (node.module or "").split(".", 1)[0]
                roots = [module] if module else []
            else:
                roots = []
            if roots and any(root in _REPLAY_RISKY_IMPORT_ROOTS for root in roots):
                return "cell imports a direct process/filesystem/network module"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"open", "exec", "eval", "compile"}:
                    return f"recovery cell uses unsafe builtin: {node.func.id}"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id == "host":
                    method = node.func.attr
                    if method in _UNSAFE_HOST_METHODS:
                        return f"unsafe Host method: {method}"
                    if method not in _SAFE_HOST_METHODS:
                        return f"unknown Host method: {method}"
                if node.func.attr in {
                    "mkdir",
                    "rename",
                    "replace",
                    "rmdir",
                    "save",
                    "savefig",
                    "to_csv",
                    "to_excel",
                    "to_json",
                    "to_parquet",
                    "to_pickle",
                    "unlink",
                    "write",
                    "write_bytes",
                    "write_text",
                }:
                    return f"recovery cell may write external state: {node.func.attr}"
    else:
        lowered = code.lower()
        if any(
            marker in lowered
            for marker in (
                "host$",
                "submit_output",
                "system(",
                "system2(",
                "pipe(",
                "write.csv",
                "write.table",
                "writelines(",
                "saverds(",
                "ggsave(",
                "file.copy",
                "file.remove",
                "file.rename",
                "unlink(",
                "download.file",
            )
        ):
            return "R recovery cell contains Host/shell/filesystem/network side effects"
    return None


class KernelRecoveryOrchestrator:
    """Run one recovery transaction and atomically publish only verified state."""

    def __init__(
        self,
        *,
        build_candidate: Callable[[BootstrapManifest], Candidate],
        bootstrap_candidate: Callable[[Candidate, BootstrapManifest], Any],
        hydrate_workspace: Callable[[Candidate, Mapping[str, Any]], Any],
        hydrate_artifact: Callable[[Candidate, Mapping[str, Any]], Any],
        execute_cell: Callable[[Candidate, str, str], Mapping[str, Any]],
        inspect_symbols: Callable[[Candidate, str], Iterable[str]],
        artifact_digest: Callable[[Candidate, str], str | None],
        inspect_environment: Callable[[Candidate], Mapping[str, Any]],
        publish: Callable[[Candidate], Any],
        journal: JournalSink | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self._build = build_candidate
        self._bootstrap = bootstrap_candidate
        self._hydrate_workspace = hydrate_workspace
        self._hydrate_artifact = hydrate_artifact
        self._execute = execute_cell
        self._symbols = inspect_symbols
        self._artifact_digest = artifact_digest
        self._environment = inspect_environment
        self._publish = publish
        self._journal = journal or (lambda _event: None)
        self._cancelled = cancelled or (lambda: False)

    def restore(
        self,
        *,
        root_frame_id: str,
        branch_id: str | None,
        manifest: BootstrapManifest,
        recipe: RecoveryRecipe,
        source_generation_id: str | None,
        recovery_id: str | None = None,
    ) -> RecoveryResult:
        recovery_id = recovery_id or f"recovery-{uuid.uuid4().hex[:16]}"
        branch_id = branch_id or root_frame_id
        candidate: Candidate | None = None
        candidate_id: str | None = None
        replayed: list[str] = []
        skipped: list[str] = []
        issues: list[dict[str, Any]] = []
        published = False

        def check_cancelled() -> None:
            if self._cancelled():
                raise RecoveryCancelled("recovery execution was cancelled")

        def record(phase: str, status: str, detail: Any = None) -> None:
            event = {
                "recovery_id": recovery_id,
                "root_frame_id": root_frame_id,
                "branch_id": branch_id,
                "source_generation_id": source_generation_id,
                "candidate_generation_id": candidate_id,
                "phase": phase,
                "status": status,
                "detail": detail if detail is not None else {},
            }
            self._journal(event)

        record("restore", "started", {"manifest_id": manifest.manifest_id})
        try:
            check_cancelled()
            candidate = self._build(manifest)
            candidate_id = str(candidate.generation_id)
            record("build", "completed")
            check_cancelled()
            self._bootstrap(candidate, manifest)
            record(
                "bootstrap",
                "completed",
                {
                    "manifest_id": manifest.manifest_id,
                    "sidecar_hashes": {
                        sidecar.name: sidecar.sha256 for sidecar in manifest.sidecars
                    },
                },
            )

            for step in recipe.steps:
                check_cancelled()
                if step.kind == "hydrate_workspace":
                    self._hydrate_workspace(candidate, step.payload)
                    record(step.kind, "completed", {"step_id": step.step_id})
                    continue
                if step.kind == "hydrate_artifact":
                    self._hydrate_artifact(candidate, step.payload)
                    record(step.kind, "completed", {"step_id": step.step_id})
                    continue
                if step.kind != "replay_cell":
                    skipped.append(step.step_id)
                    issues.append(
                        {
                            "type": "unknown_step",
                            "step_id": step.step_id,
                            "kind": step.kind,
                        }
                    )
                    record(step.kind, "skipped", {"step_id": step.step_id})
                    continue
                code = str(step.payload.get("code") or "")
                language = str(step.payload.get("language") or manifest.language)
                expected_code_hash = step.payload.get("code_hash")
                observed_code_hash = _sha256(code.encode("utf-8"))
                error = (
                    "step is not explicitly replay-safe"
                    if step.replay_policy != REPLAY_SAFE
                    else (
                        "recovery Cell source hash does not match its recipe"
                        if expected_code_hash is not None
                        and str(expected_code_hash) != observed_code_hash
                        else replay_safety_error(
                            code,
                            language=language,
                            declared_host_methods=step.payload.get("host_methods")
                            or (),
                        )
                    )
                )
                if error:
                    skipped.append(step.step_id)
                    issues.append(
                        {
                            "type": "non_replayable",
                            "step_id": step.step_id,
                            "reason": error,
                        }
                    )
                    record(
                        "replay",
                        "skipped",
                        {"step_id": step.step_id, "reason": error},
                    )
                    continue
                response = self._execute(candidate, code, language)
                if response.get("error"):
                    issues.append(
                        {
                            "type": "replay_failed",
                            "step_id": step.step_id,
                            "error": str(response["error"]),
                        }
                    )
                    record(
                        "replay",
                        "failed",
                        {"step_id": step.step_id, "error": str(response["error"])},
                    )
                    break
                replayed.append(step.step_id)
                record("replay", "completed", {"step_id": step.step_id})

            check_cancelled()
            issues.extend(self._validate(candidate, manifest, recipe))
            if issues:
                record("validate", "partial", {"issues": issues})
                self._shutdown(candidate)
                return RecoveryResult(
                    recovery_id,
                    "partial",
                    source_generation_id,
                    candidate_id,
                    manifest.manifest_id,
                    tuple(issues),
                    tuple(replayed),
                    tuple(skipped),
                )

            record("validate", "completed")
            check_cancelled()
            self._publish(candidate)
            published = True
            record("publish", "completed")
            return RecoveryResult(
                recovery_id,
                "active",
                source_generation_id,
                candidate_id,
                manifest.manifest_id,
                (),
                tuple(replayed),
                tuple(skipped),
            )
        except RecoveryCancelled as error:
            record("restore", "cancelled", {"error": str(error)})
            if candidate is not None and not published:
                self._shutdown(candidate)
            return RecoveryResult(
                recovery_id,
                "cancelled",
                source_generation_id,
                candidate_id,
                manifest.manifest_id,
                ({"type": "recovery_cancelled", "error": str(error)},),
                tuple(replayed),
                tuple(skipped),
            )
        except Exception as error:  # noqa: BLE001 — failure is durable state
            record(
                "restore",
                "failed",
                {"error": str(error), "error_type": type(error).__name__},
            )
            if candidate is not None and not published:
                self._shutdown(candidate)
            return RecoveryResult(
                recovery_id,
                "active" if published else "failed",
                source_generation_id,
                candidate_id,
                manifest.manifest_id,
                (
                    {
                        "type": (
                            "publish_journal_failed" if published else "recovery_failed"
                        ),
                        "error": str(error),
                    },
                ),
                tuple(replayed),
                tuple(skipped),
            )

    def _validate(
        self,
        candidate: Candidate,
        manifest: BootstrapManifest,
        recipe: RecoveryRecipe,
    ) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        if recipe.namespace_coverage == "unverified":
            issues.append(
                {
                    "type": "namespace_unverified",
                    "reason": (
                        "checkpoint contains prior Cells without a verified "
                        "namespace recovery recipe"
                    ),
                }
            )
        for language, required in recipe.required_symbols.items():
            observed = {str(item) for item in self._symbols(candidate, language)}
            missing = sorted(set(required) - observed)
            if missing:
                issues.append(
                    {"type": "missing_symbols", "language": language, "names": missing}
                )
        for artifact, expected in recipe.artifact_hashes.items():
            observed = self._artifact_digest(candidate, artifact)
            if observed != expected:
                issues.append(
                    {
                        "type": "artifact_hash_mismatch",
                        "artifact": artifact,
                        "expected": expected,
                        "observed": observed,
                    }
                )
        environment = dict(self._environment(candidate) or {})
        for key, expected in recipe.environment_requirements.items():
            if environment.get(key) != expected:
                issues.append(
                    {
                        "type": "environment_mismatch",
                        "key": key,
                        "expected": expected,
                        "observed": environment.get(key),
                    }
                )
        if environment.get("interpreter") not in {None, manifest.interpreter}:
            issues.append(
                {
                    "type": "environment_mismatch",
                    "key": "interpreter",
                    "expected": manifest.interpreter,
                    "observed": environment.get("interpreter"),
                }
            )
        observed_runtime = environment.get("runtime_version") or environment.get(
            "python_version"
        )
        if (
            manifest.runtime_version
            and manifest.runtime_version not in {"?", "unknown"}
            and observed_runtime != manifest.runtime_version
        ):
            issues.append(
                {
                    "type": "environment_mismatch",
                    "key": "runtime_version",
                    "expected": manifest.runtime_version,
                    "observed": observed_runtime,
                }
            )
        for key, expected in (
            ("sdk_version", manifest.sdk_version),
            ("provenance_version", manifest.provenance_version),
            ("host_capability_version", manifest.host_capability_version),
            ("environment_hash", manifest.environment_hash),
        ):
            if expected is not None and environment.get(key) != expected:
                issues.append(
                    {
                        "type": "environment_mismatch",
                        "key": key,
                        "expected": expected,
                        "observed": environment.get(key),
                    }
                )
        return issues

    @staticmethod
    def _shutdown(candidate: Candidate) -> None:
        try:
            candidate.shutdown()
        except Exception:  # noqa: BLE001 — candidate was never published
            pass


def sidecar_from_path(
    name: str,
    path: str | Path,
    *,
    order: int,
    exports: Sequence[str] = (),
    import_mode: str = "module",
) -> SidecarManifest:
    source_path = Path(path).resolve()
    return SidecarManifest(
        name=name,
        source=source_path.read_bytes(),
        order=order,
        exports=tuple(exports),
        import_mode=import_mode,
        source_path=str(source_path),
    )


__all__ = [
    "BootstrapManifest",
    "Candidate",
    "KernelRecoveryOrchestrator",
    "REPLAY_CONDITIONAL",
    "REPLAY_NEVER",
    "REPLAY_SAFE",
    "RecoveryRecipe",
    "RecoveryCancelled",
    "RecoveryResult",
    "RecoveryStep",
    "SidecarManifest",
    "frozen_sidecar_bootstrap_code",
    "merge_bootstrap_sidecar_loads",
    "replay_safety_error",
    "sidecar_from_load_event",
    "sidecar_from_path",
]
