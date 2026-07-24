"""A bundled Skill must not tell the agent to call something that isn't there.

Two phantom APIs turned up in one day, and both had the same shape: a name the
recipes used confidently, that no module defined and no test exercised, sitting
at the point where the agent publishes its results.

  * ``save_artifacts(...)`` — the real facade is singular,
    ``host.save_artifact(path)``. Ten Skills ended their remote-compute recipe
    with the plural form, so an agent did the GPU work and then died with
    ``NameError`` before saving anything.
  * ``wait_for_notification`` — a "brain-tool" no registry ever contained, that
    the same recipes told the agent to park on.

Neither was catchable by the existing tests: SKILL.md prose is not imported,
and the code blocks are executed only by a live agent against a real provider.
This gate reads the recipes the way the kernel would.

The rule is deliberately narrow, so that it stays true rather than becoming a
thing people mute:

  * only **bare calls** count. ``pd.read_csv(...)`` is an attribute on a name
    that must itself resolve, and is checked that way; a method on a runtime
    object is not something this file can know about.
  * a name counts as resolvable if it is a Python builtin, is bound anywhere
    in the skill's own code blocks (assignment, import, ``def``, loop target,
    ``with ... as``, ``except ... as``), comes from the skill's ``kernel.py``
    sidecar, or is one of the two names the kernel actually injects.
"""
from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SKILLS = _REPO / "skills"

#: What `openai4s/kernel/worker.py` puts in a cell's namespace, and nothing
#: else. Keep this in step with `ns[...]` there — a third injected name is a
#: deliberate decision, and this test is where it gets recorded.
INJECTED_NAMES = frozenset({"host", "openai4s"})

_PY_BLOCK = re.compile(r"```(?:python|py)\n(.*?)```", re.S)


def _bindings_and_calls(source: str) -> tuple[set[str], set[str]] | None:
    """Names this source binds, and the bare names it calls.

    Returns None when the fragment does not parse — a recipe may legitimately
    show an elided snippet, and guessing at one is worse than skipping it. The
    count of skipped blocks is asserted below so this cannot quietly become a
    test that checks nothing.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    bound: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                bound.update(
                    a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
                )
                for extra in (args.vararg, args.kwarg):
                    if extra is not None:
                        bound.add(extra.arg)
        elif isinstance(node, ast.alias):
            bound.add((node.asname or node.name).split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.Lambda):
            args = node.args
            bound.update(
                a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            )
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            called.add(node.func.id)
    return bound, called


def _sidecar_names(skill_dir: Path) -> set[str]:
    """Top-level names a skill's `kernel.py` sidecar defines."""
    sidecar = skill_dir / "kernel.py"
    if not sidecar.is_file():
        return set()
    try:
        tree = ast.parse(sidecar.read_text("utf-8"))
    except SyntaxError:  # pragma: no cover - the loader compile-checks these
        return set()
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
    return names


def _bundled_skills() -> list[Path]:
    return sorted(p.parent for p in _SKILLS.glob("*/SKILL.md"))


def _unresolved(skill_dir: Path) -> tuple[set[str], int, int]:
    document = (skill_dir / "SKILL.md").read_text("utf-8")
    bound = set(INJECTED_NAMES) | _sidecar_names(skill_dir)
    called: set[str] = set()
    blocks = skipped = 0
    for block in _PY_BLOCK.findall(document):
        blocks += 1
        parsed = _bindings_and_calls(block)
        if parsed is None:
            skipped += 1
            continue
        block_bound, block_called = parsed
        bound |= block_bound
        called |= block_called
    unresolved = {
        name for name in called if name not in bound and not hasattr(builtins, name)
    }
    return unresolved, blocks, skipped


@pytest.mark.parametrize("skill_dir", _bundled_skills(), ids=lambda p: p.name)
def test_every_bare_call_in_a_recipe_resolves(skill_dir):
    """The gate. A name the recipe calls must exist by the time the agent runs
    it, or the recipe fails at exactly the step it was written to perform."""
    unresolved, _blocks, _skipped = _unresolved(skill_dir)
    assert not unresolved, (
        f"{skill_dir.name}/SKILL.md calls names nothing defines: "
        f"{sorted(unresolved)}. The kernel injects only {sorted(INJECTED_NAMES)}; "
        f"anything else must be a builtin, bound in the recipe itself, or "
        f"exported by the skill's kernel.py sidecar."
    )


def test_the_gate_actually_reads_the_recipes():
    """A parser that silently skipped every block would pass the test above
    while checking nothing. Assert it is really looking at code."""
    total_blocks = total_skipped = 0
    for skill_dir in _bundled_skills():
        _unresolved_names, blocks, skipped = _unresolved(skill_dir)
        total_blocks += blocks
        total_skipped += skipped

    assert total_blocks >= 50, f"only {total_blocks} python blocks found"
    assert total_skipped == 0, (
        f"{total_skipped} of {total_blocks} python blocks failed to parse; a "
        f"recipe the gate cannot read is a recipe it cannot check"
    )


def test_the_injected_namespace_matches_the_worker():
    """`INJECTED_NAMES` above is the assumption the whole gate rests on."""
    worker = (_REPO / "openai4s" / "kernel" / "worker.py").read_text("utf-8")
    injected = set(re.findall(r'ns\["([^"]+)"\]\s*=', worker))
    assert injected == set(INJECTED_NAMES), (
        f"the kernel now injects {sorted(injected)}; update INJECTED_NAMES and "
        f"decide deliberately whether recipes may rely on the new name"
    )


def test_the_gate_catches_a_phantom_call(tmp_path):
    """Drive the checker over a recipe with the exact defect it exists for."""
    skill = tmp_path / "phantom"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "# demo\n\n```python\nr = host.compute.create('ssh:x')\n"
        "save_artifacts(r['featured_files'])\n```\n",
        encoding="utf-8",
    )
    unresolved, blocks, skipped = _unresolved(skill)
    assert blocks == 1 and skipped == 0
    assert unresolved == {"save_artifacts"}
