"""Compatibility contract for backend modules that will become facades.

The refactor may move implementations into packages such as ``openai4s.host``
or ``openai4s.storage``.  Code in this repository already imports the legacy
module paths below, so those paths must remain small forwarding facades during
the migration.

This test deliberately does *not* inspect ``obj.__module__``, source files, or
class internals.  A facade export may therefore forward to any new
implementation.  The AST scan has two purposes:

* make the currently consumed public surface explicit; and
* reject new dependencies on undeclared legacy-module internals.

The small ``LEGACY_INTERNAL_IMPORT_DEBT`` allowlist records existing private
cross-module imports.  It is an upper bound, not a required surface, so entries
may disappear as consumers migrate without making those internals permanent.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_MODULE_REFERENCE = "<module>"

# Public names currently consumed by the project's own Python sources.  Keep
# these names importable from the old path even when their implementations move.
FACADE_EXPORTS: dict[str, frozenset[str]] = {
    "openai4s.host_dispatch": frozenset({"HostDispatcher", "build_dispatcher"}),
    "openai4s.store": frozenset(
        {"SECRET_ARG_HOST_CALLS", "Store", "get_store"}
    ),
    "openai4s.llm": frozenset(
        {"ARK_PLAN_MODELS", "LLMError", "PROVIDERS", "chat", "supports_vision"}
    ),
    "openai4s.webtools": frozenset(
        {"NetworkDisabled", "network_allowed", "web_fetch", "web_search"}
    ),
    "openai4s.mcp_client": frozenset({"manager"}),
    "openai4s.permissions": frozenset({"PermissionBroker", "broker"}),
    "openai4s.egress": frozenset(
        {
            "EGRESS_GROUPS",
            "EgressBlocked",
            "blocked_error",
            "blocked_message",
            "check_url",
            "command_domains",
            "domain_allowed",
            "domain_of",
            "egress_mode",
            "grant_domain",
            "granted_domains",
            "scan_command",
        }
    ),
    "openai4s.agent.loop": frozenset({"Agent", "run_task"}),
    "openai4s.server.gateway": frozenset({"build_app_server", "serve_app"}),
}

# Existing boundary violations.  Do not add to this list: move the consumer to
# a public facade instead.  These symbols are intentionally *not* checked as
# stable exports, so the refactor is free to remove them with their consumers.
LEGACY_INTERNAL_IMPORT_DEBT: dict[str, frozenset[str]] = {
    "openai4s.host_dispatch": frozenset({"_validate_schema"}),
    "openai4s.agent.loop": frozenset({"SYSTEM_PROMPT", "_format_observation"}),
}

# Tests are consumers of the public API but often import private helpers in
# order to characterize them.  Scan production packages and the deterministic
# harness, not tests themselves, so a unit-test fixture cannot accidentally
# enlarge the compatibility facade.
_SOURCE_ROOTS = (
    "openai4s",
    "openai4s_compute_provider",
    "openai4s_worker_runtime",
    "harness",
)
_FACADE_MODULES = frozenset(FACADE_EXPORTS)


def _module_name(path: Path) -> str:
    parts = list(path.relative_to(_REPO).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_from(node: ast.ImportFrom, current: str, path: Path) -> str:
    if node.level == 0:
        return node.module or ""
    package = current if path.name == "__init__.py" else current.rpartition(".")[0]
    return importlib.util.resolve_name(
        "." * node.level + (node.module or ""), package
    )


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else None
    return None


def _literal_import_module(node: ast.AST) -> str | None:
    """Recognize ``importlib.import_module("openai4s...")`` in the harness."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    if not isinstance(node.func, ast.Attribute) or node.func.attr != "import_module":
        return None
    first = node.args[0]
    if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
        return None
    return first.value if first.value in _FACADE_MODULES else None


def _scan_backend_imports() -> tuple[dict[str, set[str]], dict[tuple[str, str], list[str]]]:
    observed: dict[str, set[str]] = defaultdict(set)
    locations: dict[tuple[str, str], list[str]] = defaultdict(list)

    def record(module: str, name: str, path: Path, line: int) -> None:
        observed[module].add(name)
        locations[(module, name)].append(
            f"{path.relative_to(_REPO).as_posix()}:{line}"
        )

    for root_name in _SOURCE_ROOTS:
        root = _REPO / root_name
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            current = _module_name(path)
            module_aliases: dict[str, str] = {}

            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    base = _resolve_from(node, current, path)
                    if base in _FACADE_MODULES:
                        for alias in node.names:
                            record(base, alias.name, path, node.lineno)
                    for alias in node.names:
                        imported = f"{base}.{alias.name}" if base else alias.name
                        if imported in _FACADE_MODULES:
                            record(imported, _MODULE_REFERENCE, path, node.lineno)
                            module_aliases[alias.asname or alias.name] = imported
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name not in _FACADE_MODULES:
                            continue
                        record(alias.name, _MODULE_REFERENCE, path, node.lineno)
                        # ``import a.b`` binds ``a``; an explicit alias binds the
                        # whole module.  Fully-qualified attribute uses are also
                        # recognized in the second pass below.
                        if alias.asname:
                            module_aliases[alias.asname] = alias.name
                else:
                    imported = _literal_import_module(node)
                    if imported is not None:
                        record(imported, _MODULE_REFERENCE, path, node.lineno)

            # Track aliases assigned from literal importlib.import_module calls.
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                imported = _literal_import_module(node.value)
                if imported is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        module_aliases[target.id] = imported

            # ``from openai4s import egress`` and the harness' literal dynamic
            # imports consume attributes through a module alias.
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                    module = module_aliases.get(node.value.id)
                    if module is not None:
                        record(module, node.attr, path, node.lineno)

                dotted = _dotted_name(node) if isinstance(node, ast.Attribute) else None
                if dotted is None:
                    continue
                for module in _FACADE_MODULES:
                    prefix = module + "."
                    if dotted.startswith(prefix):
                        name = dotted[len(prefix) :].split(".", 1)[0]
                        record(module, name, path, node.lineno)

    return dict(observed), dict(locations)


def test_backend_imports_only_use_declared_facade_surface():
    observed, locations = _scan_backend_imports()
    unexpected: list[str] = []
    for module, names in sorted(observed.items()):
        allowed = (
            FACADE_EXPORTS[module]
            | LEGACY_INTERNAL_IMPORT_DEBT.get(module, frozenset())
            | {_MODULE_REFERENCE}
        )
        for name in sorted(names - allowed):
            refs = ", ".join(sorted(set(locations[(module, name)])))
            unexpected.append(f"{module}.{name} imported at {refs}")

    assert not unexpected, (
        "New imports from a legacy backend module must go through a declared "
        "public facade (or migrate to the new package); do not grow the private "
        "debt allowlist:\n" + "\n".join(unexpected)
    )


def test_backend_facade_exports_remain_importable():
    missing: list[str] = []
    for module_name, exports in sorted(FACADE_EXPORTS.items()):
        module = importlib.import_module(module_name)
        for name in sorted(exports):
            if not hasattr(module, name):
                missing.append(f"{module_name}.{name}")

    assert not missing, (
        "Backend compatibility facades may forward to new implementations, but "
        "must keep these currently consumed exports importable:\n"
        + "\n".join(missing)
    )
