"""Best-effort, dependency-free Cell namespace metadata.

The execution log is immutable, so dependency metadata is captured beside the
source at record time.  Python uses :mod:`ast`; R uses a deliberately small
lexer that recognizes the common assignment/delete forms without importing an
R runtime into the stdlib-only host.

This is a conservative *projection*, not a security boundary and not a claim
that arbitrary Python/R effects can be proven statically.  ``uncertain`` is
set when a construct can mutate the namespace without exposing stable names.
"""

from __future__ import annotations

import ast
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

VISIBILITIES = frozenset({"scientific", "scratch", "recovery", "system"})
REPLAY_POLICIES = frozenset({"safe", "conditional", "never"})


@dataclass(frozen=True)
class CellDependencyMetadata:
    code_hash: str
    reads: tuple[str, ...]
    writes: tuple[str, ...]
    deletes: tuple[str, ...]
    uncertain: bool = False

    def as_record(self) -> dict[str, Any]:
        return {
            "code_hash": self.code_hash,
            "variable_reads": list(self.reads),
            "variable_writes": list(self.writes),
            "variable_deletes": list(self.deletes),
            "mutation_uncertain": self.uncertain,
        }


@dataclass(frozen=True)
class _Scope:
    locals: frozenset[str]
    globals: frozenset[str] = frozenset()
    deferred: bool = False


class _Bindings(ast.NodeVisitor):
    """Collect bindings in one function scope, excluding nested scopes."""

    def __init__(self, arguments: ast.arguments | None = None) -> None:
        self.locals: set[str] = set()
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        if arguments is not None:
            for arg in (
                *arguments.posonlyargs,
                *arguments.args,
                *arguments.kwonlyargs,
            ):
                self.locals.add(arg.arg)
            if arguments.vararg is not None:
                self.locals.add(arguments.vararg.arg)
            if arguments.kwarg is not None:
                self.locals.add(arguments.kwarg.arg)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.locals.add(node.id)

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        self.nonlocals.update(node.names)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self.locals.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name != "*":
                self.locals.add(alias.asname or alias.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.locals.add(node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self.locals.add(node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self.locals.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        del node


def _function_scope(arguments: ast.arguments, body: Iterable[ast.AST]) -> _Scope:
    bindings = _Bindings(arguments)
    for node in body:
        bindings.visit(node)
    locals_ = bindings.locals - bindings.globals - bindings.nonlocals
    return _Scope(frozenset(locals_), frozenset(bindings.globals), deferred=True)


def _target_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for item in node.elts:
            names.update(_target_names(item))
    return names


def _root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


class _PythonDependencies(ast.NodeVisitor):
    _MUTATING_METHODS = frozenset(
        {
            "add",
            "append",
            "clear",
            "discard",
            "extend",
            "fit",
            "partial_fit",
            "insert",
            "itemset",
            "pop",
            "popitem",
            "put",
            "remove",
            "resize",
            "reverse",
            "setflags",
            "setdefault",
            "sort",
            "step",
            "update",
            "zero_grad",
            "__delitem__",
            "__setitem__",
        }
    )
    _DYNAMIC_MUTATORS = frozenset({"exec", "setattr", "delattr"})

    def __init__(self) -> None:
        self.reads: set[str] = set()
        self.writes: set[str] = set()
        self.deletes: set[str] = set()
        self.uncertain = False
        self._scopes: list[_Scope] = []
        self._defined_in_cell: set[str] = set()

    def _is_external(self, name: str) -> bool:
        for scope in reversed(self._scopes):
            if name in scope.globals:
                return True
            if name in scope.locals:
                return False
        return True

    def _is_deferred(self) -> bool:
        return any(scope.deferred for scope in self._scopes)

    def _write_name(self, name: str, *, delete: bool = False) -> None:
        if not self._scopes:
            (self.deletes if delete else self.writes).add(name)
            self._defined_in_cell.add(name)
            return
        if name in self._scopes[-1].globals and not self._is_deferred():
            (self.deletes if delete else self.writes).add(name)
            self._defined_in_cell.add(name)

    def _mutate_root(self, node: ast.AST, *, delete: bool = False) -> None:
        name = _root_name(node)
        if name is None or not self._is_external(name):
            return
        if name not in self._defined_in_cell:
            self.reads.add(name)
        if not self._is_deferred():
            (self.deletes if delete else self.writes).add(name)
            self._defined_in_cell.add(name)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, ast.Load):
            if self._is_external(node.id) and node.id not in self._defined_in_cell:
                self.reads.add(node.id)
        elif isinstance(node.ctx, ast.Store):
            self._write_name(node.id)
        elif isinstance(node.ctx, ast.Del):
            self._write_name(node.id, delete=True)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        for target in node.targets:
            self._visit_assignment_target(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self._visit_assignment_target(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        self._visit_assignment_target(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        if isinstance(node.target, ast.Name):
            if (
                self._is_external(node.target.id)
                and node.target.id not in self._defined_in_cell
            ):
                self.reads.add(node.target.id)
            self._write_name(node.target.id)
        else:
            self.visit(node.target)
            self._mutate_root(node.target)
        self.visit(node.value)

    def visit_Delete(self, node: ast.Delete) -> None:  # noqa: N802
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._write_name(target.id, delete=True)
            else:
                self.visit(target)
                # ``del value[key]`` mutates ``value``; it does not remove the
                # root binding itself, so record an in-place write.
                self._mutate_root(target)

    def _visit_assignment_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self._write_name(target.id)
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                self._visit_assignment_target(item)
            return
        self.visit(target)
        self._mutate_root(target)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._write_name(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name == "*":
                self.uncertain = True
            else:
                self._write_name(alias.asname or alias.name)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        self._write_name(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        if node.returns is not None:
            self.visit(node.returns)
        scope = _function_scope(node.args, node.body)
        self._scopes.append(scope)
        try:
            for item in node.body:
                self.visit(item)
        finally:
            self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        scope = _function_scope(node.args, (node.body,))
        self._scopes.append(scope)
        try:
            self.visit(node.body)
        finally:
            self._scopes.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._write_name(node.name)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        # A class body executes immediately.  Name stores belong to the class
        # namespace (not the kernel globals), while external loads still form
        # dependencies of the produced class object.
        class_bindings = _Bindings()
        for item in node.body:
            class_bindings.visit(item)
        self._scopes.append(
            _Scope(
                frozenset(class_bindings.locals - class_bindings.globals),
                frozenset(class_bindings.globals),
            )
        )
        try:
            for item in node.body:
                self.visit(item)
        finally:
            self._scopes.pop()

    def _visit_comprehension(
        self,
        generators: Sequence[ast.comprehension],
        values: Sequence[ast.AST],
    ) -> None:
        bound: set[str] = set()
        # The first iterable is evaluated in the enclosing scope.  Comprehension
        # targets are then local to the implicit scope in Python 3.
        for generator in generators:
            self.visit(generator.iter)
            bound.update(_target_names(generator.target))
        self._scopes.append(_Scope(frozenset(bound)))
        try:
            for generator in generators:
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)
        finally:
            self._scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.elt,))

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        self._visit_comprehension(node.generators, (node.key, node.value))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.generic_visit(node)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id in self._DYNAMIC_MUTATORS
            and not self._is_deferred()
        ):
            self.uncertain = True
        if isinstance(node.func, ast.Attribute):
            mutates = (
                node.func.attr in self._MUTATING_METHODS
                or (
                    node.func.attr.endswith("_") and not node.func.attr.startswith("__")
                )
                or any(
                    keyword.arg == "inplace"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                )
            )
            if mutates:
                root = _root_name(node.func.value)
                if root is None:
                    # e.g. ``globals().__setitem__(...)``
                    if not self._is_deferred():
                        self.uncertain = True
                elif self._is_external(root):
                    if root not in self._defined_in_cell:
                        self.reads.add(root)
                    if not self._is_deferred():
                        self.writes.add(root)
                        self._defined_in_cell.add(root)


_R_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9._])([A-Za-z.][A-Za-z0-9._]*)")
_R_RESERVED = frozenset(
    {
        "break",
        "else",
        "FALSE",
        "for",
        "function",
        "if",
        "Inf",
        "in",
        "NA",
        "NaN",
        "next",
        "NULL",
        "repeat",
        "TRUE",
        "while",
    }
)


def _strip_r_literals(code: str) -> tuple[str, bool]:
    """Blank comments/string bodies while preserving offsets and line breaks."""

    output = list(code)
    quote: str | None = None
    escaped = False
    comment = False
    for index, char in enumerate(code):
        if comment:
            if char == "\n":
                comment = False
            else:
                output[index] = " "
            continue
        if quote is not None:
            if escaped:
                escaped = False
                output[index] = " "
            elif char == "\\":
                escaped = True
                output[index] = " "
            elif char == quote:
                quote = None
            else:
                if char != "\n":
                    output[index] = " "
            continue
        if char == "#":
            comment = True
            output[index] = " "
        elif char in {"'", '"', "`"}:
            quote = char
    return "".join(output), quote is not None


def _r_dependencies(code: str) -> tuple[set[str], set[str], set[str], bool]:
    clean, uncertain = _strip_r_literals(code)
    writes: set[str] = set()
    deletes: set[str] = set()
    assignments: list[tuple[str, int, bool]] = []
    target_spans: set[tuple[int, int]] = set()
    ignored_read_spans: set[tuple[int, int]] = set()

    # Ordinary and in-place left assignment.  The optional selector makes
    # ``table$column <-`` and ``table[i] <-`` writes to ``table``.
    selector = r"(?:\$[A-Za-z.][A-Za-z0-9._]*|\[[^\n;]*?\])?"
    left_arrow = re.compile(
        r"(?<![A-Za-z0-9._])([A-Za-z.][A-Za-z0-9._]*)" rf"\s*({selector})\s*(<<-|<-)"
    )
    for match in left_arrow.finditer(clean):
        name = match.group(1)
        in_place = bool(match.group(2))
        writes.add(name)
        assignments.append((name, match.start(1), in_place))
        target_spans.add(match.span(1))
        if match.group(2).lstrip().startswith("$"):
            selector_name = re.search(r"[A-Za-z.][A-Za-z0-9._]*", match.group(2)[1:])
            if selector_name is not None:
                offset = match.start(2) + 1
                ignored_read_spans.add(
                    (
                        offset + selector_name.start(),
                        offset + selector_name.end(),
                    )
                )

    # ``=`` is assignment only at a statement boundary here.  This avoids
    # misclassifying named call arguments such as ``na.rm = TRUE``.
    left_equal = re.compile(
        rf"(?:^|[;\n{{}}])\s*([A-Za-z.][A-Za-z0-9._]*)" rf"\s*({selector})\s*=(?!=)",
        re.MULTILINE,
    )
    for match in left_equal.finditer(clean):
        name = match.group(1)
        in_place = bool(match.group(2))
        writes.add(name)
        assignments.append((name, match.start(1), in_place))
        target_spans.add(match.span(1))
        if match.group(2).lstrip().startswith("$"):
            selector_name = re.search(r"[A-Za-z.][A-Za-z0-9._]*", match.group(2)[1:])
            if selector_name is not None:
                offset = match.start(2) + 1
                ignored_read_spans.add(
                    (
                        offset + selector_name.start(),
                        offset + selector_name.end(),
                    )
                )

    for match in re.finditer(r"(?:->>|->)\s*([A-Za-z.][A-Za-z0-9._]*)", clean):
        writes.add(match.group(1))
        assignments.append((match.group(1), match.start(1), False))
        target_spans.add(match.span(1))

    for match in re.finditer(r"\bfor\s*\(\s*([A-Za-z.][A-Za-z0-9._]*)\s+in\b", clean):
        writes.add(match.group(1))
        assignments.append((match.group(1), match.start(1), False))
        target_spans.add(match.span(1))

    # Only literal symbol arguments are safe to resolve. ``rm(list=...)`` and
    # computed calls deliberately trip the conservative flag.
    for match in re.finditer(r"\b(?:rm|remove)\s*\(([^)]*)\)", clean):
        callee = re.search(r"(?:rm|remove)", match.group(0))
        if callee is not None:
            ignored_read_spans.add(
                (
                    match.start() + callee.start(),
                    match.start() + callee.end(),
                )
            )
        body = match.group(1)
        if re.search(r"\blist\s*=", body):
            uncertain = True
            continue
        pieces = [piece.strip() for piece in body.split(",") if piece.strip()]
        if pieces and all(re.fullmatch(r"[A-Za-z.][A-Za-z0-9._]*", p) for p in pieces):
            deletes.update(pieces)
            body_start = match.start(1)
            for symbol in re.finditer(r"[A-Za-z.][A-Za-z0-9._]*", body):
                ignored_read_spans.add(
                    (
                        body_start + symbol.start(),
                        body_start + symbol.end(),
                    )
                )
        elif pieces:
            uncertain = True

    if re.search(r"\b(?:assign|delayedAssign|dyn.load|load|source)\s*\(", clean):
        uncertain = True
    if re.search(r"\beval\s*\(\s*parse\s*\(", clean):
        uncertain = True

    for match in re.finditer(
        r"(?<![A-Za-z0-9._])([A-Za-z.][A-Za-z0-9._]*)\s*=(?!=)",
        clean,
    ):
        ignored_read_spans.add(match.span(1))

    reads: set[str] = set()
    for match in _R_IDENTIFIER.finditer(clean):
        name = match.group(1)
        span = match.span(1)
        if name in _R_RESERVED or span in target_spans or span in ignored_read_spans:
            continue
        # A value produced earlier in this same Cell is not an input from the
        # previous state revision.
        if any(
            assigned_name == name and position < match.start(1)
            for assigned_name, position, _in_place in assignments
        ):
            continue
        reads.add(name)
    for name, position, in_place in assignments:
        if in_place and not any(
            previous_name == name and previous_position < position
            for previous_name, previous_position, _ in assignments
        ):
            reads.add(name)
    return reads, writes, deletes, uncertain


def analyze_code(code: str, language: str = "python") -> CellDependencyMetadata:
    """Return deterministic static metadata; malformed source never escapes."""

    source = str(code or "")
    code_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    normalized_language = str(language or "python").strip().lower()
    if normalized_language == "python":
        try:
            tree = ast.parse(source, mode="exec")
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
            return CellDependencyMetadata(code_hash, (), (), (), True)
        analyzer = _PythonDependencies()
        try:
            analyzer.visit(tree)
        except (MemoryError, RecursionError):
            return CellDependencyMetadata(code_hash, (), (), (), True)
        return CellDependencyMetadata(
            code_hash,
            tuple(sorted(analyzer.reads)),
            tuple(sorted(analyzer.writes)),
            tuple(sorted(analyzer.deletes)),
            analyzer.uncertain,
        )
    if normalized_language == "r":
        reads, writes, deletes, uncertain = _r_dependencies(source)
        return CellDependencyMetadata(
            code_hash,
            tuple(sorted(reads)),
            tuple(sorted(writes)),
            tuple(sorted(deletes)),
            uncertain,
        )
    return CellDependencyMetadata(code_hash, (), (), (), True)


def default_visibility(origin: str | None) -> str:
    value = str(origin or "").strip().lower()
    if value == "system":
        return "system"
    if value == "recovery":
        return "recovery"
    return "scientific"


def default_replay_policy(visibility: str) -> str:
    if visibility == "system":
        return "never"
    if visibility == "recovery":
        return "safe"
    return "conditional"


def normalize_string_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(
        sorted(
            {item.strip() for item in value if isinstance(item, str) and item.strip()}
        )
    )


def _cell_id(cell: Mapping[str, Any], index: int) -> str:
    value = cell.get("producing_cell_id")
    if value:
        return str(value)
    revision = cell.get("state_revision") or cell.get("cell_index") or index + 1
    return f"cell-S{revision}"


def compute_stale_cells(cells: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project dependency-aware stale flags without altering immutable rows.

    A later named write/delete supersedes the prior value version.  Only Cells
    that consumed that old version (and their transitive consumers) become
    stale; the old producer and independent Cells remain valid historical
    results.  A later unknown namespace mutation invalidates all earlier Cells
    because independence cannot be proven.
    """

    count = len(cells)
    edges: list[dict[int, set[str]]] = [dict() for _ in range(count)]
    producer: dict[str, int] = {}
    external_consumers: dict[str, set[int]] = {}
    # ``roots_are_consumers`` distinguishes a superseded known producer from
    # pre-session/external consumers.  A producer itself remains a valid
    # historical result; only its downstream consumers become stale.
    invalidations: list[tuple[set[int], str, int, bool]] = []
    uncertain_at: list[int] = []

    for index, cell in enumerate(cells):
        reads = normalize_string_list(cell.get("variable_reads"))
        writes = normalize_string_list(cell.get("variable_writes"))
        deletes = normalize_string_list(cell.get("variable_deletes"))
        for name in reads:
            source = producer.get(name)
            if source is None:
                external_consumers.setdefault(name, set()).add(index)
            elif source != index:
                edges[source].setdefault(index, set()).add(name)
        for name in sorted(set(writes) | set(deletes)):
            old = producer.get(name)
            if old is not None and old != index:
                invalidations.append(({old}, name, index, False))
            else:
                consumers = {
                    item for item in external_consumers.get(name, set()) if item < index
                }
                if consumers:
                    invalidations.append((consumers, name, index, True))
            external_consumers.pop(name, None)
            # A tombstone is still a namespace version: a later read/error is
            # downstream of this delete, and a subsequent write supersedes it.
            producer[name] = index
        if bool(cell.get("mutation_uncertain")):
            uncertain_at.append(index)

    reasons: list[list[str]] = [[] for _ in range(count)]
    for roots, variable, invalidator, roots_are_consumers in invalidations:
        queue: list[int] = []
        seen: set[int] = set()
        if roots_are_consumers:
            queue.extend(root for root in roots if root < invalidator)
        else:
            for root in roots:
                queue.extend(target for target in edges[root] if target < invalidator)
        while queue:
            current = queue.pop(0)
            if current in seen or current >= invalidator:
                continue
            seen.add(current)
            queue.extend(target for target in edges[current] if target < invalidator)
        if not seen:
            continue
        by = _cell_id(cells[invalidator], invalidator)
        revision = cells[invalidator].get("state_revision")
        suffix = f" (S{revision})" if revision is not None else ""
        reason = f"variable '{variable}' was superseded by {by}{suffix}"
        for current in sorted(seen):
            if reason not in reasons[current]:
                reasons[current].append(reason)

    for invalidator in uncertain_at:
        if invalidator <= 0:
            continue
        by = _cell_id(cells[invalidator], invalidator)
        revision = cells[invalidator].get("state_revision")
        suffix = f" (S{revision})" if revision is not None else ""
        reason = f"{by}{suffix} may have mutated unknown namespace state"
        for current in range(invalidator):
            if reason not in reasons[current]:
                reasons[current].append(reason)

    return [{"stale": bool(items), "stale_reasons": items} for items in reasons]


__all__ = [
    "CellDependencyMetadata",
    "REPLAY_POLICIES",
    "VISIBILITIES",
    "analyze_code",
    "compute_stale_cells",
    "default_replay_policy",
    "default_visibility",
    "normalize_string_list",
]
