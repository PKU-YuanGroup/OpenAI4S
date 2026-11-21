"""The figure-style Skill's own check has to run where the platform runs it.

The bug this pins (usability review ENV-5): section 9.1 told the model to call
`fig.canvas.get_renderer()`. The platform saves and closes every figure when a
cell ends, so a check written in a later cell ran on a blank figure and then
hit `'FigureCanvasBase' object has no attribute 'get_renderer'` -- and the
model spent about ten cells rediscovering "check in the cell that draws".

So the snippet is executed here exactly as the document prints it, under the
Agg backend the kernel uses, against a live figure and against a closed one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

SKILL = Path(__file__).resolve().parent.parent / "skills" / "figure-style" / "SKILL.md"


def _section_9() -> str:
    text = SKILL.read_text("utf-8")
    return text[text.index("## 9. Render-then-verify") :]


def _bbox_check_snippet() -> str:
    section = _section_9()
    start = section.index("**9.1 Geometric (bbox) check.**")
    [code] = re.findall(r"```python\n(.*?)```", section[start:], flags=re.S)[:1]
    return code


def _overlapping_figure():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.set_title("growth rate by condition")
    ax.text(0.5, 0.5, "label one")
    ax.text(0.5, 0.5, "label two, drawn over the first")
    return fig


def _run_snippet(fig) -> list:
    import matplotlib as mpl

    scope = {"fig": fig, "mpl": mpl}
    exec(_bbox_check_snippet(), scope)  # noqa: S102 - the Skill's own recipe
    return scope["overlaps"]


def test_the_bbox_check_runs_under_agg_and_finds_an_overlap():
    import matplotlib.pyplot as plt

    fig = _overlapping_figure()
    try:
        pairs = _run_snippet(fig)
    finally:
        plt.close(fig)

    found = {frozenset((a.get_text(), b.get_text())) for a, b in pairs}
    assert frozenset(("label one", "label two, drawn over the first")) in found


def test_the_bbox_check_needs_no_renderer_handle():
    """A figure the platform already closed has a canvas without
    `get_renderer`. The snippet must not be the thing that raises there."""
    import matplotlib.pyplot as plt

    fig = _overlapping_figure()
    plt.close(fig)
    with pytest.raises(AttributeError, match="get_renderer"):
        fig.canvas.get_renderer()

    assert _run_snippet(fig)


def test_a_clean_figure_reports_no_overlap():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    ax.set_xlabel("time (h)")
    ax.set_ylabel("OD600")
    try:
        assert _run_snippet(fig) == []
    finally:
        plt.close(fig)


def test_the_skill_states_the_one_cell_constraint():
    section = _section_9()
    intro = section[: section.index("**9.1 Geometric (bbox) check.**")]

    assert "in the cell that draws the figure" in intro
    assert "saved and closed when a cell ends" in intro
    # The old call survives only as the error message it produces.
    assert "canvas.get_renderer()" not in section
