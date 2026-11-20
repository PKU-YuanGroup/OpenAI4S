"""Static Cell metadata and conservative stale-projection contracts."""

from __future__ import annotations

import hashlib

from openai4s.execution.dependencies import analyze_code, compute_stale_cells


def test_python_analysis_tracks_external_inputs_and_namespace_mutations():
    source = "x = raw + 1\nvalues.append(x)\ndel old\n"

    metadata = analyze_code(source, "python")

    assert metadata.code_hash == hashlib.sha256(source.encode()).hexdigest()
    assert metadata.reads == ("raw", "values")
    assert metadata.writes == ("values", "x")
    assert metadata.deletes == ("old",)
    assert metadata.uncertain is False


def test_python_analysis_excludes_function_locals_but_keeps_global_inputs():
    metadata = analyze_code(
        "def score(values):\n"
        "    total = sum(values)\n"
        "    return total + baseline\n",
        "python",
    )

    assert metadata.reads == ("baseline", "sum")
    assert metadata.writes == ("score",)
    assert metadata.deletes == ()
    assert metadata.uncertain is False


def test_python_dynamic_namespace_and_parse_failures_are_uncertain():
    assert analyze_code("exec(payload)", "python").uncertain is True
    malformed = analyze_code("if:", "python")
    assert malformed.uncertain is True
    assert malformed.reads == malformed.writes == malformed.deletes == ()
    assert analyze_code("x = 1", "unknown").uncertain is True


def test_r_analysis_tracks_common_assignment_in_place_and_delete_forms():
    metadata = analyze_code(
        "x <- source + 1\n" "y <- x * 2\n" "table$score <- y\n" "rm(old)\n",
        "r",
    )

    assert metadata.reads == ("source", "table")
    assert metadata.writes == ("table", "x", "y")
    assert metadata.deletes == ("old",)
    assert metadata.uncertain is False
    assert analyze_code("assign(name, value)", "r").uncertain is True
    assert analyze_code("rm(list = names)", "r").uncertain is True


def test_stale_projection_handles_external_values_and_uncertain_mutations():
    cells = []
    for revision, (cell_id, source) in enumerate(
        (
            ("external-reader", "result = bootstrap_value + 1"),
            ("independent", "other = 2"),
            ("bootstrap-writer", "bootstrap_value = 3"),
            ("dynamic", "exec(payload)"),
        ),
        1,
    ):
        metadata = analyze_code(source)
        cells.append(
            {
                "producing_cell_id": cell_id,
                "state_revision": revision,
                **metadata.as_record(),
            }
        )

    projection = compute_stale_cells(cells)

    # The named write first invalidates only its external-value consumer.  The
    # later unknown mutation must then conservatively invalidate all prior rows.
    assert all(item["stale"] for item in projection[:3])
    assert projection[3] == {"stale": False, "stale_reasons": []}
    assert any("bootstrap_value" in reason for reason in projection[0]["stale_reasons"])
    assert any(
        "unknown namespace state" in reason for reason in projection[1]["stale_reasons"]
    )


def _rows(*specs):
    """Execution-log-shaped rows: (cell_id, source[, language[, extra]])."""
    rows = []
    for revision, (cell_id, source, *rest) in enumerate(specs, 1):
        language = rest[0] if rest else "python"
        extra = rest[1] if len(rest) > 1 else {}
        rows.append(
            {
                "producing_cell_id": cell_id,
                "state_revision": revision,
                "language": language,
                "code": source,
                "status": "ok",
                **analyze_code(source, language).as_record(),
                **extra,
            }
        )
    return rows


def test_a_cell_that_never_compiled_does_not_stale_earlier_cells():
    """NB-8: one SyntaxError cell made every earlier cell Stale."""
    cells = _rows(
        ("a", "x = 1"),
        ("b", "y = x + 1"),
        (
            "bad",
            "if:",
            "python",
            {
                "status": "error",
                "error": '  File "<kernel:3>", line 1\n    if:\n      ^\n'
                "SyntaxError: invalid syntax\n",
            },
        ),
        (
            "legacy-bad",
            "x = (",
            "python",
            {
                "status": "error",
                "error": "Traceback (most recent call last):\n"
                '  File "/x/worker.py", line 1105, in _run_cell\n'
                '    compiled = compile(code, tag, "eval")\n'
                '  File "<kernel:4>", line 1\n    x = (\n        ^\n'
                "SyntaxError: '(' was never closed\n",
            },
        ),
        (
            "r-bad",
            "x <- (",
            "r",
            {"status": "error", "error": "ParseError: <text>:2:0: unexpected end"},
        ),
    )
    assert [item["stale"] for item in compute_stale_cells(cells)] == [False] * 5


def test_a_syntax_error_raised_while_the_cell_ran_still_counts():
    cells = _rows(
        ("a", "x = 1"),
        (
            "dynamic",
            "exec('x = (')",
            "python",
            {
                "status": "error",
                "error": "Traceback (most recent call last):\n"
                '  File "<kernel:2>", line 1, in <module>\n'
                '  File "<string>", line 1\nSyntaxError: invalid syntax\n',
            },
        ),
    )
    projection = compute_stale_cells(cells)
    assert projection[0]["stale"] is True
    assert "unknown namespace state" in projection[0]["stale_reasons"][0]


def test_a_runtime_error_inside_a_numbered_cell_still_counts():
    """NB-9 renamed a cell's code `<cell N>`; the frame that proves the cell
    ran must still be recognised, or a ran-then-failed cell would read as
    never having run."""
    cells = _rows(
        ("a", "x = 1"),
        (
            "dynamic",
            "exec('x = (')",
            "python",
            {
                "status": "error",
                "error": "Traceback (most recent call last):\n"
                '  File "<cell 2>", line 1, in <module>\n'
                '  File "<string>", line 1\nSyntaxError: invalid syntax\n',
            },
        ),
    )
    projection = compute_stale_cells(cells)
    assert projection[0]["stale"] is True
    assert "unknown namespace state" in projection[0]["stale_reasons"][0]


def test_python_and_r_cells_do_not_share_a_namespace():
    """NB-8: an R `wt <- ...` superseded the Python `wt`."""
    cells = _rows(
        ("py-wt", "wt = 1"), ("py-use", "z = wt * 2"), ("r-wt", "wt <- 3", "r")
    )
    assert [item["stale"] for item in compute_stale_cells(cells)] == [False] * 3
    within_r = _rows(
        ("r1", "wt <- 1", "r"), ("r2", "z <- wt * 2", "r"), ("r3", "wt <- 3", "r")
    )
    assert [item["stale"] for item in compute_stale_cells(within_r)] == [
        False,
        True,
        False,
    ]


def test_reimporting_the_same_module_supersedes_nothing():
    """NB-8: the agent re-importing numpy staled the cell defining a function."""
    cells = _rows(
        ("imp", "import numpy as np"),
        ("fn", "def total(v):\n    return np.sum(v)"),
        ("again", "import numpy as np\nimport pandas as pd\nx = np.zeros(3)"),
    )
    assert [item["stale"] for item in compute_stale_cells(cells)] == [False] * 3
    rebound = _rows(
        ("imp", "import numpy as np"),
        ("fn", "def total(v):\n    return np.sum(v)"),
        ("other", "import numpy.ma as np"),
    )
    projection = compute_stale_cells(rebound)
    assert projection[1]["stale"] is True
    assert "'np' was superseded" in projection[1]["stale_reasons"][0]


def test_staleness_travels_only_along_the_variable_the_consumer_read():
    """NB-1: overwriting one product of a cell staled every reader of that cell."""
    cells = _rows(
        ("load", "data = [1, 2]\ntmp = 0"),
        ("use", "total = sum(data)"),
        ("scratch", "tmp = 1"),
    )
    assert [item["stale"] for item in compute_stale_cells(cells)] == [False] * 3

    superseded = _rows(
        ("load", "data = [1, 2]\ntmp = 0"),
        ("use", "total = sum(data)"),
        ("report", "print(total)"),
        ("reload", "data = [3]"),
    )
    projection = compute_stale_cells(superseded)
    assert [item["stale"] for item in projection] == [False, True, True, False]
    assert projection[1]["stale_reasons"] == [
        "variable 'data' was superseded by reload (S4)"
    ]
    # `report` never read `data`: its reason names the stale cell it read from.
    assert projection[2]["stale_reasons"] == [
        "depends on use (S2): variable 'data' was superseded by reload (S4)"
    ]


def test_loop_targets_and_underscore_temporaries_carry_no_staleness():
    """NB-1: `i`, `f` and `_seq` were blamed for most of a 47-cell Notebook."""
    cells = _rows(
        ("first", "rows = [1, 2]\nfor i in rows:\n    last = i\n_seq = 'a'"),
        ("peek", "print(i, _seq)"),
        ("again", "for i in range(3):\n    pass\n_seq = 'b'"),
        ("handle", "with open('demo.csv') as f:\n    text = f.read()"),
        ("later", "print(f.closed)"),
        ("reopen", "with open('demo.csv') as f:\n    pass"),
    )
    assert [item["stale"] for item in compute_stale_cells(cells)] == [False] * 6

    # A name the cell also assigns outright is a result, not scratch.
    assigned = _rows(
        ("first", "i = 5"),
        ("use", "double = i * 2"),
        ("loop", "for i in range(3):\n    pass"),
        ("rebind", "i = 7"),
    )
    assert [item["stale"] for item in compute_stale_cells(assigned)] == [
        False,
        True,
        False,
        False,
    ]


def test_a_nested_comprehension_reads_its_own_earlier_target():
    """NB-1: `for b in rows[i + 1:]` recorded a read of a kernel-level `i`."""
    metadata = analyze_code(
        "pairs = [(a, b) for i, a in enumerate(rows) for b in rows[i + 1 :]]",
        "python",
    )
    assert metadata.reads == ("enumerate", "rows")
    # Only the first iterable is evaluated outside the comprehension.
    assert analyze_code("out = [x for x in x]", "python").reads == ("x",)


def test_an_unknown_mutation_the_agent_ran_in_order_does_not_stale_the_record():
    """NB-1: two `model(...).fit()` cells staled all 46 cells before them."""
    source = "model = build(data).fit()"
    assert analyze_code(source, "python").uncertain is True

    def record(origin):
        extra = {"origin": origin} if origin else {}
        return _rows(
            ("load", "data = [1, 2]", "python", extra),
            ("report", "print(data)", "python", extra),
            ("fit", source, "python", extra),
        )

    for origin in ("agent", "delegate"):
        assert [item["stale"] for item in compute_stale_cells(record(origin))] == [
            False,
            False,
            False,
        ]
    # A manual cell, a replay, or a row of unknown origin stays conservative.
    for origin in ("user", "recovery", None):
        projection = compute_stale_cells(record(origin))
        assert [item["stale"] for item in projection] == [True, True, False]
        assert "unknown namespace state" in projection[0]["stale_reasons"][0]

    # A manual cell after the agent's record still invalidates all of it.
    mixed = record("agent") + _rows(("manual", "exec(payload)", "python"))
    mixed[-1].update({"origin": "user", "state_revision": 4})
    assert [item["stale"] for item in compute_stale_cells(mixed)] == [
        True,
        True,
        True,
        False,
    ]
