"""`artifact_role`: which captured files are results and which are working files.

NB-5: the PETase session's ``tmp_lit/turbopetase_pmc10869840.xml`` -- a fetched
paper kept to grep -- was counted under the session's deliverables, and
``scratch/explore_helpers.py`` was a process file only because of its suffix.
"""

from __future__ import annotations

import pytest

from openai4s.artifact_paths import artifact_role


@pytest.mark.parametrize(
    "filename",
    [
        "tmp_lit/turbopetase_pmc10869840.xml",
        "scratch/explore_helpers.csv",
        "scratch/explore_helpers.py",
        "tmp/download.json",
        "temp/partial.tsv",
        "scratch_2/notes.md",
        "tmp-fetch/page.html",
        "TMP/upper.csv",
        "./scratch/dot-relative.csv",
        "scratch\\windows.csv",
    ],
)
def test_files_in_a_top_level_scratch_folder_are_process_files(filename):
    assert artifact_role(filename) == "process"


@pytest.mark.parametrize(
    "filename",
    [
        "results/volcano.png",
        "DE_results_final.csv",
        # Only a top-level working folder: a nested `tmp/` is the user's layout.
        "results/tmp/table.csv",
        # `temp` alone is scratch; `temp_curves` may well mean temperature.
        "temp_curves/melting.csv",
        "temperature/melting.csv",
        "tmpfile.csv",
        "scratchpad.md",
        "templates/report.md",
    ],
)
def test_other_files_stay_deliverables(filename):
    assert artifact_role(filename) == "deliverable"


def test_a_role_the_server_recorded_still_wins():
    assert artifact_role("tmp_lit/paper.xml", "deliverable") == "deliverable"
    assert artifact_role("report.md", "process") == "process"
