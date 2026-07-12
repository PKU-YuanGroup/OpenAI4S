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
