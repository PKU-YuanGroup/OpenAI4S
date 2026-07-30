"""Scope closure for the artifact read paths, and a real authorizer for agent SQL.

Three defects, all on complete production call chains, all reachable from one
line of agent code in a kernel cell.

**Agent SQL reads every project.** `QUERY_DENYLIST` does not contain `artifacts`,
`artifact_versions`, `lineage_edges`, `environment_snapshots` or `frames` — so
`host.query("SELECT * FROM artifacts")` returns every session's and every
project's artifacts with their filenames, checksums and absolute snapshot paths.
The scoped helpers next door go to some trouble to prevent exactly that, one
version id at a time. And the restriction that does exist is a substring match on
the SQL *text*, which a bound parameter is invisible to: the table name in
`SELECT * FROM pragma_table_info(?)` never appears in the string being scanned.

**Foreign and missing are distinguishable.** `_scoped_version` raises the same
`KeyError` for both and its docstring explains why: "A distinct refusal would
confirm the version exists, which is most of what an enumerator wants."
`_scoped_artifact`, twelve lines above it, raises `PermissionError` for foreign
and `KeyError` for missing. Two helpers, contradictory rules, one file.

**`view_image` and `input_version_ids` are unscoped.** `view_image`'s comment says
its scope check "belongs with the rest of the artifact read paths, not here" — and
no artifact read path performs it, so the check was deferred to nowhere. And
`save_artifact(input_version_ids=[...])` writes lineage edges with no validation
at all, so one call creates a cross-project edge that the properly scoped readers
then follow and republish.

Every test drives the real `HostDispatcher`/`HostDataService` over a real `Store`.
A fake would have to reimplement the scope rule to be useful, and a fake that got
it subtly wrong would pass while the real one leaked.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.store import get_store


def _config(tmp_path: Path) -> Config:
    return Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )


def _service(cfg, store, frame_id, workspace: Path):
    from openai4s.host.data import HostDataService

    workspace.mkdir(parents=True, exist_ok=True)

    def _resolve(path, must_exist=False):
        target = (workspace / path).resolve()
        if must_exist and not target.exists():
            raise FileNotFoundError(target)
        return target

    return HostDataService(
        store=store, config=cfg, frame_id=lambda: frame_id, resolve_path=_resolve
    )


def _seed_version(cfg, store, root_frame_id, project_id, filename, payload):
    versions = Path(cfg.data_dir) / "artifact-versions"
    versions.mkdir(parents=True, exist_ok=True)
    version_id = f"v-{uuid.uuid4().hex[:12]}"
    snapshot = versions / f"{version_id}__{filename}"
    snapshot.write_bytes(payload)
    return store.record_cell_artifact(
        path=str(snapshot),
        filename=filename,
        content_type="text/csv",
        size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        producing_cell_id=None,
        frame_id=root_frame_id,
        root_frame_id=root_frame_id,
        project_id=project_id,
        snapshot_path=str(snapshot),
    )


@pytest.fixture
def two_projects(tmp_path):
    """Two sessions in two different projects, each with one artifact version."""
    cfg = _config(tmp_path)
    store = get_store(cfg.db_path)
    mine = store.new_frame(kind="turn", project_id="proj-mine")
    theirs = store.new_frame(kind="turn", project_id="proj-theirs")
    ours = _seed_version(cfg, store, mine, "proj-mine", "ours.csv", b"a,b\n1,2\n")
    foreign = _seed_version(
        cfg, store, theirs, "proj-theirs", "secret-cohort.csv", b"patient,dose\n"
    )
    workspace = Path(cfg.data_dir) / "ws"
    service = _service(cfg, store, mine, workspace)
    try:
        yield cfg, store, service, ours, foreign
    finally:
        store.close()


# --- item 9: agent SQL must not read the internal artifact tables -----------


def test_agent_sql_cannot_read_the_artifacts_table(two_projects):
    """The most direct form of the leak, and the one nothing guarded.

    `artifacts` is not on `QUERY_DENYLIST` at all, so this returned every
    project's rows -- filename, checksum and absolute snapshot path -- while the
    scoped helpers beside it refused the same information one id at a time.
    """
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises((PermissionError, ValueError)):
        service.query({"sql": "SELECT filename FROM artifacts"})


@pytest.mark.parametrize(
    "table",
    ["artifacts", "artifact_versions", "lineage_edges", "frames"],
)
def test_agent_sql_cannot_read_any_internal_artifact_table(two_projects, table):
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises((PermissionError, ValueError)):
        service.query({"sql": f"SELECT * FROM {table} LIMIT 1"})


def test_a_cte_cannot_launder_a_denied_table(two_projects):
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises((PermissionError, ValueError)):
        service.query(
            {"sql": "WITH x AS (SELECT * FROM artifacts) SELECT filename FROM x"}
        )


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT * FROM "artifacts"',
        "SELECT * FROM [artifacts]",
        "SELECT * FROM `artifacts`",
        "SELECT a.filename FROM artifacts AS a",
        "SELECT a.filename FROM main.artifacts a",
    ],
)
def test_quoting_and_aliasing_do_not_get_past_the_gate(two_projects, sql):
    """Five spellings of the same table. A text filter has to enumerate them all
    and will miss one; an authorizer is told the resolved table name."""
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises((PermissionError, ValueError)):
        service.query({"sql": sql})


def test_a_bound_parameter_cannot_hide_a_table_name(two_projects):
    """The bypass a substring filter cannot close, by construction.

    The scan runs over the SQL text; here the table name is in `params` and never
    appears in the text at all. `pragma_table_info` also slips the ` pragma `
    keyword check, which requires surrounding spaces.
    """
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises((PermissionError, ValueError)):
        service.query(
            {"sql": "SELECT name FROM pragma_table_info(?)", "params": ["settings"]}
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM pragma_table_list",
        "SELECT * FROM pragma_database_list",
        "SELECT name FROM sqlite_schema",
        "SELECT name FROM sqlite_temp_master",
    ],
)
def test_the_sqlite_catalog_stays_closed(two_projects, sql):
    """`sqlite_master` was denied by name after it was found returning the DDL of
    denied tables. `sqlite_schema` is its alias, `pragma_table_list` answers the
    same question, and `sqlite_sequence`/`sqlite_stat1` fall outside the four
    names that were listed."""
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises((PermissionError, ValueError)):
        service.query({"sql": sql})


def test_a_legitimate_query_still_works(two_projects):
    """The gate must not pass by refusing everything: `host.query` is a product
    feature and something has to still be readable through it."""
    _cfg, _store, service, _ours, _foreign = two_projects
    rows = service.query({"sql": "SELECT 1 AS one"})
    assert rows == [{"one": 1}]


def test_agent_sql_cannot_write(two_projects):
    """Refused, and the connection is `mode=ro` so it could not have written
    even if the statement had been accepted."""
    import sqlite3

    _cfg, _store, service, _ours, _foreign = two_projects
    for sql in (
        "SELECT 1; DROP TABLE artifacts",
        "UPDATE artifacts SET filename='x'",
        "INSERT INTO artifacts (artifact_id) VALUES ('x')",
        "CREATE TABLE leak AS SELECT * FROM artifacts",
    ):
        with pytest.raises((PermissionError, ValueError, sqlite3.Error)):
            service.query({"sql": sql})


def test_the_authorizer_denies_every_catalog_table_by_rule():
    """`sqlite_sequence` and `sqlite_stat1` only exist in some databases, so a
    query test cannot cover them. The rule is a prefix, asserted directly."""
    import sqlite3

    from openai4s.store import _QueryAuthorizer

    for table in (
        "sqlite_master",
        "sqlite_schema",
        "sqlite_sequence",
        "sqlite_stat1",
        "sqlite_temp_master",
        "pragma_table_info",
        "pragma_table_list",
    ):
        guard = _QueryAuthorizer()
        assert (
            guard(sqlite3.SQLITE_READ, table, "name", "main", None)
            == sqlite3.SQLITE_DENY
        ), table
        assert guard.denied == [table]


def test_a_scoped_view_may_read_its_base_table_but_a_cell_may_not():
    """The distinction the authorizer's fifth argument exists for. Without it the
    views could not work and the base tables would have to stay open -- a bundled
    Skill legitimately reads `artifact_versions.source`."""
    import sqlite3

    from openai4s.store import _QueryAuthorizer

    guard = _QueryAuthorizer()
    assert (
        guard(sqlite3.SQLITE_READ, "artifact_versions", "source", "main", None)
        == sqlite3.SQLITE_DENY
    )
    guard = _QueryAuthorizer()
    assert (
        guard(
            sqlite3.SQLITE_READ,
            "artifact_versions",
            "source",
            "main",
            "my_artifact_versions",
        )
        == sqlite3.SQLITE_OK
    )
    # And a view name the caller invented does not count.
    guard = _QueryAuthorizer()
    assert (
        guard(sqlite3.SQLITE_READ, "artifact_versions", "source", "main", "my_own_view")
        == sqlite3.SQLITE_DENY
    )


# --- item 10: foreign and missing must be indistinguishable ----------------


@pytest.mark.parametrize(
    "method",
    ["artifact_metadata", "artifact_versions"],
)
def test_a_foreign_artifact_is_reported_exactly_like_an_absent_one(
    two_projects, method
):
    """`_scoped_artifact` raised `PermissionError` for foreign and `KeyError` for
    missing, which is a working existence oracle: the exception type alone tells a
    cell whether an id it guessed belongs to a real artifact somewhere.

    `_scoped_version`, twelve lines below in the same file, already collapses the
    two and its docstring explains why.
    """
    _cfg, _store, service, _ours, foreign = two_projects
    call = getattr(service, method)

    with pytest.raises(Exception) as foreign_error:
        call({"artifact_id": foreign["artifact_id"]})
    with pytest.raises(Exception) as absent_error:
        call({"artifact_id": "art-does-not-exist"})

    # Compared by name rather than by identity: `type(a) is type(b)` is the
    # thing being asserted, but ruff reads it as the isinstance mistake.
    assert (
        type(foreign_error.value).__name__ == type(absent_error.value).__name__
    ), "the exception type distinguishes a foreign artifact from an absent one"
    assert str(foreign_error.value) == str(absent_error.value).replace(
        "art-does-not-exist", foreign["artifact_id"]
    ), "the message distinguishes a foreign artifact from an absent one"


def test_a_foreign_artifacts_identifiers_do_not_appear_in_the_refusal(two_projects):
    """The refusal must not leak the filename, checksum or path either."""
    _cfg, _store, service, _ours, foreign = two_projects
    with pytest.raises(Exception) as error:
        service.artifact_metadata({"artifact_id": foreign["artifact_id"]})
    text = str(error.value)
    assert "secret-cohort" not in text
    assert foreign["checksum"][:16] not in text


# --- item 11: view_image and input_version_ids ------------------------------


def test_view_image_refuses_a_foreign_version(two_projects):
    """The comment said the check "belongs with the rest of the artifact read
    paths, not here". No artifact read path performed it, so it was deferred to
    nowhere -- and this branch returns the resolved absolute path."""
    _cfg, _store, service, _ours, foreign = two_projects
    with pytest.raises(KeyError):
        service.view_image({"version_id": foreign["version_id"]})


def test_view_image_still_renders_our_own_version(two_projects):
    _cfg, _store, service, ours, _foreign = two_projects
    result = service.view_image({"version_id": ours["version_id"]})
    assert result["status"] == "ok"


def test_view_image_refuses_an_absent_version_the_same_way(two_projects):
    _cfg, _store, service, _ours, _foreign = two_projects
    with pytest.raises(KeyError):
        service.view_image({"version_id": "v-nope"})


def test_save_artifact_cannot_declare_a_foreign_lineage_input(two_projects):
    """One call creates an edge the scoping model says cannot exist.

    `record_cell_artifact` skips only empty, self and duplicate ids and then
    INSERTs; `lineage_edges` declares no foreign key, so even an invented id is
    accepted. The properly scoped readers then walk that edge and republish the
    other project's filename and path through it.
    """
    cfg, store, service, _ours, foreign = two_projects
    workspace = Path(cfg.data_dir) / "ws"
    (workspace / "out.csv").write_text("x\n", encoding="utf-8")

    with pytest.raises((KeyError, PermissionError, ValueError)):
        service.save_artifact(
            {
                "path": "out.csv",
                "input_version_ids": [foreign["version_id"]],
            }
        )


def test_save_artifact_cannot_declare_an_invented_lineage_input(two_projects):
    cfg, _store, service, _ours, _foreign = two_projects
    workspace = Path(cfg.data_dir) / "ws"
    (workspace / "out2.csv").write_text("x\n", encoding="utf-8")

    with pytest.raises((KeyError, PermissionError, ValueError)):
        service.save_artifact(
            {"path": "out2.csv", "input_version_ids": ["v-never-existed"]}
        )


def test_save_artifact_still_accepts_our_own_lineage_input(two_projects):
    """The success path, so the validation cannot pass by rejecting everything --
    real lineage is the feature this whole subsystem exists for."""
    cfg, store, service, ours, _foreign = two_projects
    workspace = Path(cfg.data_dir) / "ws"
    (workspace / "derived.csv").write_text("y\n", encoding="utf-8")

    record = service.save_artifact(
        {"path": "derived.csv", "input_version_ids": [ours["version_id"]]}
    )
    edges = store.lineage_inputs(record["version_id"])
    assert [row.get("version_id") or row.get("input_version_id") for row in edges] == [
        ours["version_id"]
    ]
