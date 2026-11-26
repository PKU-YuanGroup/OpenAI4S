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


def _kernel():
    import importlib

    return importlib.import_module("figure-style.kernel")


@pytest.fixture(autouse=True)
def _skill_on_path(monkeypatch, tmp_path):
    # The snippet imports the sidecar and saves its figure in the cwd.
    monkeypatch.syspath_prepend(str(SKILL.parent.parent))
    monkeypatch.chdir(tmp_path)


def _run_snippet(fig) -> dict:
    import matplotlib as mpl

    scope = {"fig": fig, "mpl": mpl}
    exec(_bbox_check_snippet(), scope)  # noqa: S102 - the Skill's own recipe
    return scope["report"]


def test_the_bbox_check_runs_under_agg_and_finds_an_overlap():
    import matplotlib.pyplot as plt

    fig = _overlapping_figure()
    try:
        report = _run_snippet(fig)
    finally:
        plt.close(fig)

    found = {frozenset(pair) for pair in report["overlaps"]}
    assert frozenset(("label one", "label two, drawn over the first")) in found


def test_the_bbox_check_needs_no_renderer_handle():
    """A figure the platform already closed has a canvas without
    `get_renderer`. The snippet must not be the thing that raises there."""
    import matplotlib.pyplot as plt

    fig = _overlapping_figure()
    plt.close(fig)
    with pytest.raises(AttributeError, match="get_renderer"):
        fig.canvas.get_renderer()

    assert _run_snippet(fig)["overlaps"]


def test_a_clean_figure_reports_no_overlap():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    ax.set_xlabel("time (h)")
    ax.set_ylabel("OD600")
    try:
        report = _kernel().layout_issues(fig, "clean")
    finally:
        plt.close(fig)
    assert report["overlaps"] == report["out_of_bounds"] == []
    assert report["spine_hits"] == [] and report["rerender"] is False


def test_tick_labels_outside_the_axis_limits_are_not_out_of_bounds():
    """CHAT-3: a volcano plot reported `out-of-bounds=['−8', '6']` -- labels of
    ticks beyond the view limits, which matplotlib keeps `visible` with a
    stale box but never draws -- and the figure was re-rendered for them."""
    import matplotlib.pyplot as plt
    import numpy as np

    rng = np.random.default_rng(0)
    fig, ax = plt.subplots(figsize=(5.6, 4.6))
    ax.scatter(rng.normal(0, 2.5, 500), np.abs(rng.normal(0, 3, 500)), s=5)
    ax.margins(0.06)
    fig.tight_layout()
    try:
        fig.draw_without_rendering()
        stale = [
            t.get_text()
            for t in ax.get_xticklabels() + ax.get_yticklabels()
            if not fig.bbox.contains(*t.get_window_extent().p0)
        ]
        # Judged against the bare canvas, the strictest frame there is.
        report = _kernel().layout_issues(
            fig, "volcano-ticks", bbox_inches=fig.bbox_inches
        )
    finally:
        plt.close(fig)
    assert stale, "the fixture no longer has an undrawn tick label"
    assert report["out_of_bounds"] == [], report


def test_a_label_past_the_canvas_is_inside_a_tight_save():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    ax.text(1.3, 0.5, "outside the canvas", transform=ax.transAxes)
    try:
        tight = _kernel().layout_issues(fig, "edge-tight", bbox_inches="tight")
        canvas = _kernel().layout_issues(
            fig, "edge-canvas", bbox_inches=fig.bbox_inches
        )
    finally:
        plt.close(fig)
    assert tight["out_of_bounds"] == []
    assert canvas["out_of_bounds"] == ["outside the canvas"]


def test_geometry_rerenders_stop_after_two_rounds():
    """CHAT-3: ten of 24 cells re-rendered three figures for near-overlaps
    until the user said stop. The third check of one figure says to stop."""
    import matplotlib.pyplot as plt

    fig = _overlapping_figure()
    try:
        rounds = [_kernel().layout_issues(fig, "capped") for _ in range(3)]
    finally:
        plt.close(fig)
    assert [r["rerender"] for r in rounds] == [True, True, False]
    assert [r["round"] for r in rounds] == [0, 1, 2]
    assert "limitations" in rounds[-1]["note"]
    assert _kernel().MAX_LAYOUT_RERENDERS == 2


def test_the_skill_states_the_one_cell_constraint():
    section = _section_9()
    intro = section[: section.index("**9.1 Geometric (bbox) check.**")]

    assert "in the cell that draws the figure" in intro
    assert "saved and closed when a cell ends" in intro
    # The old call survives only as the error message it produces.
    assert "canvas.get_renderer()" not in section


def test_the_skill_caps_rerenders_and_sends_the_rest_to_limitations():
    section = _section_9()
    assert "at most 2 geometry re-renders per figure" in section
    assert "`limitations`" in section
    assert "re-save until clean" not in section
