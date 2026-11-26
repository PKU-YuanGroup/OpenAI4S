META_GREY = "#888888"


def apply_figure_style(*, frame="open", font=None, sizes=(8, 7, 6), grid=False):
    """Set matplotlib rcParams for publication-grade output. Call once before plotting.

    This sets mechanics (role-mapped font-size ladder, outward ticks, frameless
    legends, 300-dpi save, Type-42 embedded fonts) — not a house aesthetic.
    Frame, font and the size ladder are parameters.

    frame : 'open' (bottom+left spines, default) | 'boxed' (all four) | 'none'
    font  : sans-serif family name; None = system default sans-serif
    sizes : (base, secondary, tick) — titles/axis-labels, legend/annotation, ticks
    grid  : whether to draw axes.grid (default False)
    """
    import matplotlib as mpl

    if frame not in ("open", "boxed", "none"):
        raise ValueError(f"frame must be 'open'|'boxed'|'none', got {frame!r}")
    # Register conda-installed fonts (mscorefonts lands in $CONDA_PREFIX/fonts, off mpl's scan path)
    try:
        import glob
        import os
        import sys

        import matplotlib.font_manager as fm

        fdir = os.path.join(os.environ.get("CONDA_PREFIX") or sys.prefix, "fonts")
        if os.path.isdir(fdir):
            known = {f.fname for f in fm.fontManager.ttflist}
            for f in glob.glob(os.path.join(fdir, "*.ttf")):
                if f not in known:
                    fm.fontManager.addfont(f)
    except Exception:
        pass
    base, secondary, tick = sizes
    boxed = frame == "boxed"
    rc = {
        "font.family": "sans-serif",
        "font.size": base,
        "axes.labelsize": base,
        "axes.titlesize": base,
        "legend.fontsize": secondary,
        "xtick.labelsize": tick,
        "ytick.labelsize": tick,
        "axes.linewidth": 0.6,
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size": 3,
        "ytick.major.size": 3,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "axes.spines.top": boxed,
        "axes.spines.right": boxed,
        "axes.spines.left": frame != "none",
        "axes.spines.bottom": frame != "none",
        "axes.grid": bool(grid),
        "legend.frameon": False,
        "figure.dpi": 200,
        "savefig.dpi": 300,
        "savefig.bbox": "tight",
        "axes.titleweight": "normal",
        "axes.titlelocation": "left",
        "axes.labelweight": "normal",
        "lines.linewidth": 1.2,
        "patch.linewidth": 0.6,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    }
    if font:
        rc["font.sans-serif"] = [font, "DejaVu Sans"]
    mpl.rcParams.update(rc)


def set_frame(ax, style="open"):
    """section 3: set spine visibility on an existing axes. style ∈ {'open','boxed','none'}."""
    show = {
        "open": (False, False, True, True),
        "boxed": (True, True, True, True),
        "none": (False, False, False, False),
    }[style]
    for side, vis in zip(("top", "right", "bottom", "left"), show):
        ax.spines[side].set_visible(vis)
        if vis:
            ax.spines[side].set_linewidth(0.6)
    ax.tick_params(direction="out", length=0 if style == "none" else 3, width=0.6)


def panel_letter(ax, letter, dx=-0.18, dy=1.02, case="lower", fontsize=None):
    """section 5.7: bold panel letter outside top-left of axes. case ∈ {'lower','upper'}."""
    import matplotlib.pyplot as plt

    if fontsize is None:
        fontsize = (
            plt.rcParams.get("font.size", 8) + 1
        )  # section 5.2: bold + one step above base
    s = letter.lower() if case == "lower" else letter.upper()
    ax.text(
        dx,
        dy,
        s,
        transform=ax.transAxes,
        fontweight="bold",
        fontsize=fontsize,
        va="bottom",
        ha="left",
    )


def focal_palette(labels, focal, focal_color, other="muted", base_colors=None):
    """section 4.2: map labels → colours with the focal series visually dominant.

    other='muted'   — desaturate base_colors (or a default cycle) toward grey
    other='grey'    — uniform light grey for all non-focal
    other='ordinal' — non-focal on a single light→dark grey ramp (input order)
    """
    import matplotlib.colors as mcolors
    import matplotlib.pyplot as plt

    focal_set = {focal} if isinstance(focal, str) else set(focal)
    n = len(labels)
    if not focal_set & set(labels):
        raise ValueError(f"focal {focal!r} not found in labels")
    if base_colors is None:
        base_colors = plt.rcParams["axes.prop_cycle"].by_key().get("color", ["#444444"])
    base_colors = [base_colors[i % len(base_colors)] for i in range(n)]
    if other == "grey":
        rest = ["#BCBCBC"] * n
    elif other == "ordinal":
        nf = max(1, n - len(focal_set))
        ramp = [
            mcolors.to_hex((v, v, v))
            for v in (
                [0.55] if nf == 1 else [0.80 - 0.35 * i / (nf - 1) for i in range(nf)]
            )
        ]
        rest, k = [], 0
        for l in labels:
            rest.append(ramp[min(k, nf - 1)])
            k += l not in focal_set
    else:  # 'muted'

        def mute(c):
            r, g, b = mcolors.to_rgb(c)
            m = (r + g + b) / 3
            return mcolors.to_hex(
                (0.3 * r + 0.7 * m, 0.3 * g + 0.7 * m, 0.3 * b + 0.7 * m)
            )

        rest = [mute(c) for c in base_colors]
    return [focal_color if l in focal_set else rest[i] for i, l in enumerate(labels)]


def bar_with_points(
    ax,
    x,
    ymat,
    labels,
    colors,
    jitter=0.08,
    show_points=True,
    errorbar=None,
    point_alpha=0.5,
    point_size=8,
):
    """section 6.1: bar = mean; optionally overlay raw points or draw an interval.

    colors   : per-label colour list (e.g. from focal_palette)
    errorbar : None | 'sd' | 'ci95' — drawn only when show_points is False.
               'ci95' is the t-distribution 95% CI of the mean
               (half-width t_{0.975,n-1} · s/√n); correct at small n where the
               z-approximation (1.96·s/√n) is markedly too narrow.
    """
    import numpy as np

    means = np.array([np.mean(y) for y in ymat], float)
    err = None
    if errorbar and not show_points:
        if errorbar == "sd":
            err = np.array(
                [np.std(y, ddof=1) if np.asarray(y).size > 1 else 0 for y in ymat]
            )
        elif errorbar == "ci95":
            from scipy.stats import t

            def _hw(y):
                n = np.asarray(y).size
                return (
                    t.ppf(0.975, n - 1) * np.std(y, ddof=1) / np.sqrt(n) if n > 1 else 0
                )

            err = np.array([_hw(y) for y in ymat])
    ax.bar(
        x,
        means,
        color=colors,
        width=0.7,
        edgecolor="none",
        yerr=err,
        error_kw={"elinewidth": 0.8, "capsize": 0},
    )
    if show_points:
        for xi, ys in zip(x, ymat):
            ys = np.asarray(ys)
            if ys.ndim and ys.size > 1:
                jit = (np.random.rand(ys.size) - 0.5) * 2 * jitter
                ax.scatter(
                    np.full(ys.size, xi) + jit,
                    ys,
                    s=point_size,
                    color="black",
                    alpha=point_alpha,
                    zorder=3,
                    linewidths=0,
                )
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    return ax


def strip_with_median(ax, groups, values, colors=None, jitter=0.12):
    """section 6.1: jittered points + bold horizontal median tick per group."""
    import numpy as np

    labs = list(groups)
    if colors is None:
        colors = ["#444444"] * len(labs)
    for i, (ys, c) in enumerate(zip(values, colors)):
        ys = np.asarray(ys)
        jit = (np.random.rand(ys.size) - 0.5) * 2 * jitter
        ax.scatter(
            np.full(ys.size, i) + jit,
            ys,
            s=10,
            color=c,
            alpha=0.6,
            linewidths=0,
            zorder=2,
        )
        m = np.median(ys)
        ax.plot([i - 0.22, i + 0.22], [m, m], color="black", lw=1.6, zorder=3)
    ax.set_xticks(range(len(labs)))
    ax.set_xticklabels(labs)
    return ax


def goodness_arrow(
    ax, text="higher = better", loc="upper left", axis="y", fontsize=None
):
    """section 3.6: small upright direction-of-goodness cue in the margin."""
    import matplotlib.pyplot as plt

    if fontsize is None:
        fontsize = plt.rcParams["legend.fontsize"]  # secondary / annotation role
    pos = {
        "upper left": (0.02, 0.98),
        "upper right": (0.98, 0.98),
        "lower left": (0.02, 0.02),
        "lower right": (0.98, 0.02),
    }[loc]
    ha = "left" if "left" in loc else "right"
    va = "top" if "upper" in loc else "bottom"
    arrow = "↑ " if axis == "y" else "→ "
    ax.text(
        pos[0],
        pos[1],
        arrow + text,
        transform=ax.transAxes,
        fontsize=fontsize,
        color=META_GREY,
        ha=ha,
        va=va,
    )


def two_tier_label(name, meta):
    """section 5: two-line label string (name / metadata). Meta line styled separately by caller."""
    return f"{name}\n{meta}"


def end_of_line_labels(ax, xs, ys, labels, colors=None, dx=0.01, fontsize=None):
    """section 6.3 / section 7.3: label each line series at its right end instead of a legend box."""
    import matplotlib.pyplot as plt

    if fontsize is None:
        fontsize = plt.rcParams["font.size"]  # base / series-identity role
    if colors is None:
        colors = [None] * len(labels)
    span = ax.get_xlim()[1] - ax.get_xlim()[0]
    for x, y, lab, c in zip(xs, ys, labels, colors):
        ax.text(
            x[-1] + dx * span,
            y[-1],
            lab,
            color=c,
            va="center",
            ha="left",
            fontsize=fontsize,
        )


def panel_crops(fig, dpi=None, pad_px=6, bbox_inches=None, pad_inches=None):
    """section 9.2: pixel-space crop boxes for each lettered panel in the SAVED PNG.

    Returns ``{letter: (x0, y0, x1, y1)}`` in image-space pixels (origin
    top-left, matching ``host.view_image(path, crop=...)`` and PIL's
    ``Image.crop``). Panels are detected as bold single-character ``Text``
    objects placed by :func:`panel_letter`; each panel's crop is its axes'
    tightbbox mapped into the saved file's pixel space, padded by ``pad_px``.
    For section 3.4 composites (abutting subplots sharing an axis, letter on the
    leftmost only) the crop unions in letterless ``sharex``/``sharey`` siblings
    on the same grid row/col so the whole composite is covered. When no axes
    carries a panel letter (standalone plot, or a figure-composer sub-agent),
    falls back to one crop per axes keyed by index.

    ``bbox_inches`` mirrors ``Figure.savefig`` semantics: ``None`` means
    *consult rcParams* (so under :func:`apply_figure_style` it resolves to
    ``'tight'``); pass an explicit ``Bbox`` only if you saved with one. The
    boxes are clamped to the saved image extent regardless.

        >>> fig.savefig("fig.png")            # bbox_inches='tight' via rcParams
        >>> for letter, box in panel_crops(fig).items():
        ...     host.view_image("fig.png", crop=box)
    """
    import matplotlib as mpl
    import matplotlib.text

    if dpi is None:
        dpi = mpl.rcParams.get("savefig.dpi", fig.dpi)
        if dpi == "figure":
            dpi = fig.dpi
    dpi = float(dpi)
    if bbox_inches is None:
        bbox_inches = mpl.rcParams.get("savefig.bbox")
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    # Saved-image frame in *inches*: origin (ox_in, oy_in), size (W_in, H_in).
    if bbox_inches == "tight":
        if pad_inches is None:
            pad_inches = mpl.rcParams.get("savefig.pad_inches", 0.1)
        tb = fig.get_tightbbox(r).padded(pad_inches)
        ox_in, oy_in = tb.x0, tb.y0
        W_in, H_in = tb.width, tb.height
    elif isinstance(bbox_inches, mpl.transforms.BboxBase):
        ox_in, oy_in = bbox_inches.x0, bbox_inches.y0
        W_in, H_in = bbox_inches.width, bbox_inches.height
    else:
        ox_in, oy_in = 0.0, 0.0
        W_in, H_in = fig.get_size_inches()
    W_px, H_px = int(round(W_in * dpi)), int(round(H_in * dpi))
    lettered = {}
    for ax in fig.axes:
        for t in ax.findobj(matplotlib.text.Text):
            s = (t.get_text() or "").strip()
            if len(s) == 1 and s.isalpha() and t.get_fontweight() in ("bold", 700):
                lettered[ax] = s
                break
    # No panel letters (standalone plot, or a figure-composer sub-agent which is
    # told NOT to draw its own letter): fall back to one crop per axes so the
    # section 9.2 loop still inspects something instead of silently iterating over {}.
    if not lettered:
        lettered = {ax: str(i) for i, ax in enumerate(fig.axes)}
    out = {}
    for ax, letter in lettered.items():
        bbs = [ax.get_tightbbox(r)]  # display px at fig.dpi
        # section 3.4: a composite panel (abutting subplots sharing an axis, letter on
        # the leftmost only) spans its letterless sharex/sharey siblings in the
        # same grid row/col — NOT the whole grid that `subplots(sharey=True)`
        # (== 'all') joins transitively.
        ss = ax.get_subplotspec()
        for sib in fig.axes:
            if sib is ax or sib in lettered:
                continue
            ssib = sib.get_subplotspec()
            same_row = ss is None or ssib is None or ss.rowspan == ssib.rowspan
            same_col = ss is None or ssib is None or ss.colspan == ssib.colspan
            if (ax.get_shared_y_axes().joined(ax, sib) and same_row) or (
                ax.get_shared_x_axes().joined(ax, sib) and same_col
            ):
                bbs.append(sib.get_tightbbox(r))
        bb = mpl.transforms.Bbox.union(bbs)
        # display-px → inches → saved-frame inches → saved px (y flipped)
        bx0 = (bb.x0 / fig.dpi - ox_in) * dpi
        bx1 = (bb.x1 / fig.dpi - ox_in) * dpi
        by0 = H_px - (bb.y1 / fig.dpi - oy_in) * dpi
        by1 = H_px - (bb.y0 / fig.dpi - oy_in) * dpi
        out[letter] = (
            max(int(bx0) - pad_px, 0),
            max(int(by0) - pad_px, 0),
            min(int(bx1) + pad_px, W_px),
            min(int(by1) + pad_px, H_px),
        )
    return out


#: section 9.1: how many times one figure may be re-rendered for geometry.
MAX_LAYOUT_RERENDERS = 2

_LAYOUT_ROUNDS = {}
"""figure name -> how many times `layout_issues` has checked it this kernel."""


def _undrawn_tick_labels(fig):
    """Tick labels matplotlib keeps but does not draw.

    A tick whose location lies outside its axis' view interval is skipped at
    draw time, yet its labels stay ``visible`` with a stale box beyond the
    axes. Counting them reported ``out-of-bounds=['−8', '6']`` for a volcano
    plot whose saved PNG showed neither.
    """
    skipped = set()
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            lo, hi = sorted(float(v) for v in axis.get_view_interval())
            tol = (hi - lo) * 1e-9
            for tick in axis.get_major_ticks() + axis.get_minor_ticks():
                loc = tick.get_loc()
                if loc is None or not (lo - tol <= float(loc) <= hi + tol):
                    skipped.update((tick.label1, tick.label2))
    return skipped


def _saved_frame(fig, bbox_inches=None, pad_inches=None):
    """The saved image's extent in display pixels (``savefig`` semantics)."""
    import matplotlib as mpl

    if bbox_inches is None:
        bbox_inches = mpl.rcParams.get("savefig.bbox")
    if bbox_inches == "tight":
        if pad_inches is None:
            pad_inches = mpl.rcParams.get("savefig.pad_inches", 0.1)
        if pad_inches == "layout":
            pad_inches = 0.0
        frame = fig.get_tightbbox().padded(pad_inches)
    elif isinstance(bbox_inches, mpl.transforms.BboxBase):
        frame = bbox_inches
    else:
        return fig.bbox
    return mpl.transforms.TransformedBbox(frame, fig.dpi_scale_trans)


def layout_issues(fig, name, *, bbox_inches=None, pad_inches=None):
    """section 9.1 geometric check, in the cell that draws ``fig``.

    Returns ``{"overlaps", "spine_hits", "out_of_bounds", "round", "rerender",
    "note"}``; the first three list label text. Only labels a draw actually
    renders count, and out-of-bounds means outside the SAVED image: under
    ``bbox_inches='tight'`` (what :func:`apply_figure_style` sets) a label
    beyond the canvas is inside the saved PNG, so it is not a finding. Pass
    the ``bbox_inches``/``pad_inches`` you gave ``savefig``, if any.

    ``name`` identifies the figure across re-renders (use its file stem).
    ``rerender`` is True only while issues remain and fewer than
    ``MAX_LAYOUT_RERENDERS`` re-renders of that figure have been checked;
    after that, keep the saved figure and report what is left under
    ``limitations`` instead of rendering it again.
    """
    import matplotlib as mpl

    fig.draw_without_rendering()
    undrawn = _undrawn_tick_labels(fig)
    texts = [
        (t, t.get_window_extent())
        for t in fig.findobj(mpl.text.Text)
        if t.get_text().strip() and t.get_visible() and t not in undrawn
    ]
    spines = [
        (s, s.get_window_extent())
        for ax in fig.axes
        for s in ax.spines.values()
        if s.get_visible()
    ]
    ticklabels = {
        ax: set(ax.get_xticklabels(which="both") + ax.get_yticklabels(which="both"))
        for ax in fig.axes
    }
    overlaps = [
        (a.get_text(), b.get_text())
        for i, (a, ba) in enumerate(texts)
        for b, bb in texts[i + 1 :]
        if ba.overlaps(bb)
    ]
    spine_hits = [
        t.get_text()
        for t, bt in texts
        for s, bs in spines
        if bt.overlaps(bs) and t not in ticklabels[s.axes]
    ]
    frame = _saved_frame(fig, bbox_inches, pad_inches)
    out_of_bounds = [
        t.get_text()
        for t, bt in texts
        if bt.x0 < frame.x0 - 1
        or bt.y0 < frame.y0 - 1
        or bt.x1 > frame.x1 + 1
        or bt.y1 > frame.y1 + 1
    ]
    checked = _LAYOUT_ROUNDS.get(name, 0)
    _LAYOUT_ROUNDS[name] = checked + 1
    issues = bool(overlaps or spine_hits or out_of_bounds)
    rerender = issues and checked < MAX_LAYOUT_RERENDERS
    if not issues:
        note = "clean"
    elif rerender:
        note = (
            f"fix and re-render; {MAX_LAYOUT_RERENDERS - checked} geometry "
            f"re-render(s) left for {name!r}"
        )
    else:
        note = (
            f"re-render budget for {name!r} is spent: keep the saved figure and "
            "list these issues under limitations instead of rendering it again"
        )
    return {
        "overlaps": overlaps,
        "spine_hits": spine_hits,
        "out_of_bounds": out_of_bounds,
        "round": checked,
        "rerender": rerender,
        "note": note,
    }
