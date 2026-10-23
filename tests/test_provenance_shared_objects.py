"""Whether a tag on an object is a claim about the read that produced it.

A tag lives on an object's identity -- its attribute, or its side-table entry --
so it says "this object came from that file" only while the object is this
read's own. Equal immutables are often one object: CPython keeps one `None`,
`True` and `False`, caches small ints, the empty string and one object per
latin-1 character; NumPy keeps one `True_` and `False_`; pandas parses every
missing string as `numpy.nan` and every missing timestamp as `pandas.NaT`.

`set_tags` tagged those too. So `json.loads` of a tracked file holding `false`,
`null`, `1` or `"\\n"` tagged every `False`, `None`, `1` and newline in the
process, and a later write of one -- by code that never read the file --
reported that file as its input. Indexing one missing cell of a tracked CSV did
the same to `numpy.nan`. A fabricated edge: provenance that is wrong rather
than absent, which is worse because it is the kind that gets believed.

A string's identity cannot be judged at all. The interpreter interns
identifier-like literals, equal constants of one Cell are one object, and a
frame hands back the very object assigned into it -- so `df["kind"] =
"control"; df["kind"][0]` on a tracked frame tagged the literal, and every
later `write("control")` named the CSV. An exact `str` is now never tagged in
place: a read that yields one hands back a `_TaggedStr` copy of its own.

Each refusal test is paired with one showing the read's own value still carries
its tag, because "tag nothing" would also pass the refusals.
"""

from __future__ import annotations

import copy
import io
import json
import os
import pickle
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.kernel import Kernel, provenance
from openai4s.store import get_store

TRACKED = "v-tracked"


@pytest.fixture(autouse=True)
def _clean_side_table():
    provenance._side_tags.clear()
    provenance._side_tags_bytes[0] = 0
    yield
    provenance._side_tags.clear()
    provenance._side_tags_bytes[0] = 0


@pytest.fixture
def lineage(tmp_path, monkeypatch):
    """One tracked file, and every lineage record a write sends to the host."""
    monkeypatch.chdir(tmp_path)
    tracked = tmp_path / "tracked.data"
    records: list[dict] = []

    def host_call(method, args):
        if method == "prov_resolve_path":
            return TRACKED if args[0] == os.path.realpath(tracked) else None
        records.append(args[0])
        return {"ok": True}

    monkeypatch.setattr(provenance, "_host_call", host_call)
    # The same wrapper `np.save` gets, on a sink that does not need numpy.
    sink = SimpleNamespace(save=lambda path, obj: None)
    provenance._prov_wrap_func_writer(sink, "save", path_argno=0, obj_argno=1)
    return SimpleNamespace(tracked=tracked, records=records, sink=sink)


def _read_tracked(state, document):
    state.tracked.write_text(document)
    with provenance._prov_open(state.tracked) as handle:
        return provenance._prov_json_loads(handle.read())


def _write(state, value, name):
    if isinstance(value, str):
        with provenance._prov_open(name, "w") as handle:
            handle.write(value)
    else:
        state.sink.save(name, value)


def _inputs(state):
    return [record["input_version_ids"] for record in state.records]


@pytest.mark.parametrize(
    "document, shared",
    [
        ('"\\n"', "\n"),
        ('""', ""),
        ('"a"', "a"),
        ("false", False),
        ("true", True),
        ("null", None),
        ("0", 0),
        ("1", 1),
        ("256", 256),
        ("-5", -5),
    ],
)
def test_a_shared_value_from_a_tracked_file_is_no_input_of_an_unrelated_write(
    lineage, document, shared
):
    assert (
        provenance._real_json_loads(document) is shared
    ), "precondition: the parser hands out the shared object"
    _read_tracked(lineage, document)

    _write(lineage, shared, "unrelated.out")

    assert _inputs(lineage) == [], "a write that never read the file named it"


@pytest.mark.parametrize("document", ["NaN", "Infinity", "-Infinity"])
def test_json_nonfinite_constants_do_not_contaminate_an_independent_parse(
    lineage, document
):
    loaded = _read_tracked(lineage, document)
    unrelated = json.loads(document)
    assert loaded is unrelated, "precondition: the decoder reused its constant"

    _write(lineage, unrelated, "unrelated.out")

    assert _inputs(lineage) == [], "an independent parse inherited the tracked source"


@pytest.mark.parametrize("shared", [(), b""], ids=["empty tuple", "empty bytes"])
def test_a_shared_value_from_a_tracked_pickle_is_no_input_of_an_unrelated_write(
    lineage, shared
):
    """JSON cannot produce these; `read_pickle` can, through the same wrapper."""
    lineage.tracked.write_bytes(pickle.dumps(shared))
    reader = provenance._prov_wrap_reader(lambda path: pickle.loads(path.read_bytes()))
    assert reader(lineage.tracked) is shared, "precondition: the shared object"

    lineage.sink.save("unrelated.out", shared)

    assert _inputs(lineage) == [], "a write that never read the file named it"


@pytest.mark.parametrize("document", ['"science"', "1000", "1.5", "[1, 2]", '{"a": 1}'])
def test_a_value_that_is_the_reads_own_keeps_its_lineage(lineage, document):
    loaded = _read_tracked(lineage, document)

    _write(lineage, loaded, "derived.out")

    assert _inputs(lineage) == [[TRACKED]]


def test_the_refusal_follows_the_interpreter_rather_than_a_restated_bound():
    """Which ints CPython shares is a build constant, not a language rule. A
    second, independent parse of the same text is the unrelated reader: where
    the interpreter hands it the tagged object, it must see no tag; where it
    hands it a new one, the first parse must keep its tag."""
    tags = frozenset({TRACKED})
    shared_seen = own_seen = 0
    for number in range(-300, 1300):
        mine = json.loads(str(number))
        provenance.set_tags(mine, tags)
        other = json.loads(str(number))
        if other is mine:
            shared_seen += 1
            assert provenance.get_tags(other) == frozenset(), number
        else:
            own_seen += 1
            assert provenance.get_tags(mine) == tags, number
            assert provenance.get_tags(other) == frozenset(), number
    assert shared_seen and own_seen, "the range no longer straddles the cache"

    for code in range(256):
        mine = json.loads(json.dumps(chr(code)))
        provenance.set_tags(mine, tags)
        other = json.loads(json.dumps(chr(code)))
        if other is mine:
            assert provenance.get_tags(other) == frozenset(), code


def test_a_library_constant_from_a_tracked_frame_is_no_input_of_an_unrelated_write(
    lineage, monkeypatch
):
    pd = pytest.importorskip("pandas")
    np = pytest.importorskip("numpy")
    for cls in (pd.DataFrame, pd.Series):
        # recorded so teardown restores pandas; the patch below is process-wide
        monkeypatch.setattr(cls, "__getitem__", cls.__getitem__)
    provenance._patch_dataframe_getitem(pd)

    frame = pd.read_csv(
        io.StringIO("name,flag,seen,count\nscience,True,2020-01-01,7\n,False,,8\n"),
        parse_dates=["seen"],
    )
    frame["label"] = pd.array(["a", None], dtype="string")
    # what the `read_csv` wrapper does for a tracked path
    provenance.set_tags(frame, frozenset({TRACKED}))
    try:
        cells = {
            "missing string": (frame["name"][1], np.nan),
            "true": (frame["flag"][0], np.True_),
            "false": (frame["flag"][1], np.False_),
            "missing timestamp": (frame["seen"][1], pd.NaT),
            "missing label": (frame["label"][1], pd.NA),
        }
        for label, (cell, constant) in cells.items():
            assert cell is constant, f"precondition: {label} is the library constant"

        for label, (_cell, constant) in cells.items():
            _write(lineage, constant, f"{label}.out")
        assert _inputs(lineage) == [], "a library constant carried the CSV's lineage"

        _write(lineage, frame["name"][0], "name.out")
        _write(lineage, frame["count"][0], "count.out")
        assert _inputs(lineage) == [[TRACKED], [TRACKED]]
    finally:
        # The unfixed code tagged these by attribute, process-wide.
        for constant in (pd.NaT, pd.NA):
            vars(constant).pop(provenance._PROV_ATTR, None)


@pytest.fixture
def tracked_frame(monkeypatch):
    """A frame read from the tracked file, indexed through the worker's wrapper."""
    pd = pytest.importorskip("pandas")
    for cls in (pd.DataFrame, pd.Series):
        # recorded so teardown restores pandas; the patch below is process-wide
        monkeypatch.setattr(cls, "__getitem__", cls.__getitem__)
    provenance._patch_dataframe_getitem(pd)
    frame = pd.read_csv(io.StringIO("name,count\nscience,7\nbiology,8\n"))
    # what the `read_csv` wrapper does for a tracked path
    return provenance.set_tags(frame, frozenset({TRACKED}))


def _cell(source):
    """Evaluate source as its own compilation, the way the worker runs a Cell."""
    return eval(compile(source, "<cell>", "eval"))


def _held_by_the_user():
    label = "-".join(["arm", "B"])
    return label, label


@pytest.mark.parametrize(
    "shared",
    [
        # identifier-like literals are interned: a later Cell's is this one
        pytest.param(lambda: ("control", _cell("'control'")), id="interned literal"),
        # equal constants of one compilation are one object, interned or not
        pytest.param(
            lambda: _cell("('control group', 'control group')"),
            id="constant of one cell",
        ),
        pytest.param(_held_by_the_user, id="string the user holds"),
    ],
)
def test_a_string_assigned_into_a_tracked_frame_is_no_input_of_an_unrelated_write(
    lineage, tracked_frame, shared
):
    pd = pytest.importorskip("pandas")
    assigned, elsewhere = shared()
    assert elsewhere is assigned, "precondition: other code holds this very object"
    frame = tracked_frame
    # Object dtype pins what pandas stores: pyarrow-backed strings would copy.
    frame["kind"] = pd.Series([assigned] * len(frame), dtype=object)
    assert frame["kind"].iloc[0] is assigned, "precondition: the frame holds it"

    indexed = frame["kind"][0]
    _write(lineage, elsewhere, "unrelated.out")
    assert _inputs(lineage) == [], "a string written elsewhere named the CSV"

    _write(lineage, indexed, "derived.out")
    assert _inputs(lineage) == [[TRACKED]]


def test_no_exact_string_is_tagged_in_place():
    """Whichever caller passes one; `_tag_read` returns a tagged copy instead."""
    provenance.set_tags("control", frozenset({TRACKED}))
    provenance.set_tags(_held_by_the_user()[0], frozenset({TRACKED}))

    assert provenance.get_tags(_cell("'control'")) == frozenset()
    assert provenance._side_tags == {}


def test_a_string_parsed_from_a_tracked_csv_keeps_its_lineage(lineage, tracked_frame):
    """It comes back as a tagged copy, and string operations keep the tag."""
    indexed = tracked_frame["name"][0]
    assert indexed == "science"
    assert (
        provenance.get_tags(tracked_frame["name"].iloc[0]) == frozenset()
    ), "the frame's own object was tagged in place"

    _write(lineage, indexed, "name.out")
    _write(lineage, indexed.upper(), "upper.out")

    assert _inputs(lineage) == [[TRACKED], [TRACKED]]


def test_a_string_a_tracked_reader_returns_keeps_its_lineage(lineage):
    lineage.tracked.write_bytes(pickle.dumps("science"))
    reader = provenance._prov_wrap_reader(lambda path: pickle.loads(path.read_bytes()))

    _write(lineage, reader(lineage.tracked), "derived.out")

    assert _inputs(lineage) == [[TRACKED]]


@pytest.mark.parametrize(
    "convert",
    [
        str,
        lambda value: f"{value}",
        lambda value: f"{value!s}",
        format,
        lambda value: format(value, ">10"),
        "{}".format,
        "{:>10}".format,
        lambda value: "%s" % value,
        lambda value: "%10s" % value,
    ],
    ids=[
        "str",
        "f-string",
        "f-string-s",
        "format",
        "format-width",
        "template",
        "template-width",
        "percent",
        "percent-width",
    ],
)
def test_string_output_conversions_keep_the_read_lineage(
    lineage, tracked_frame, convert
):
    value = tracked_frame["name"][0]
    converted = convert(value)
    assert converted == convert("science")

    _write(lineage, converted, "converted.txt")

    assert _inputs(lineage) == [[TRACKED]]
    assert provenance.get_tags("science") == frozenset()


def test_plain_separator_join_is_an_explicit_untracked_boundary(lineage, tracked_frame):
    # str.join does not dispatch to its elements' methods; do not regain a tag
    # by tagging the plain result (which may itself be a shared constant).
    joined = ",".join([tracked_frame["name"][0]])
    assert type(joined) is str and joined == "science"
    _write(lineage, joined, "joined.txt")
    assert _inputs(lineage) == []


@pytest.mark.parametrize("value", ["science", b"science"], ids=["str", "bytes"])
def test_a_tagged_string_pickles_as_the_plain_value(value):
    """A pickle names the class, and the tagged one is local to a function, so
    pickling it raised -- which `df["name"][0]` on a tracked frame now returns."""
    tagged = provenance._tag_scalar(value, frozenset({TRACKED}))
    for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
        restored = pickle.loads(pickle.dumps(tagged, protocol))
        assert type(restored) is type(value) and restored == value, protocol

    for duplicate in (copy.copy(tagged), copy.deepcopy(tagged)):
        assert provenance.get_tags(duplicate) == frozenset({TRACKED})


def test_real_kernel_reports_no_lineage_for_values_it_never_read(tmp_path):
    """The same defect through the worker's installed wrappers, end to end."""
    pytest.importorskip("pandas")
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame_id = store.new_frame(kind="turn", project_id="default")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    constants = {"nan": "NaN", "inf": "Infinity", "neg-inf": "-Infinity"}
    sources = {
        "tracked.json": '"\\n"',
        "tracked.csv": "name,flag\nscience,True\n,False\n",
        **{f"{name}.json": token for name, token in constants.items()},
    }
    versions = {}
    for filename, text in sources.items():
        (workspace / filename).write_text(text)
        versions[filename] = store.save_artifact(
            path=str(workspace / filename),
            filename=filename,
            content_type="text/plain",
            size_bytes=len(text),
            checksum=f"{filename}-checksum",
            frame_id=frame_id,
            project_id="default",
        )["version_id"]
    dispatcher = HostDispatcher(cfg=cfg, frame_id=frame_id, workspace=workspace)

    with Kernel(dispatcher=dispatcher, cwd=str(workspace)) as kernel:
        result = kernel.execute(
            "import json\n"
            "import numpy as np\n"
            "import pandas as pd\n"
            "newline = json.loads(open('tracked.json').read())\n"
            "for name in ('nan', 'inf', 'neg-inf'):\n"
            "    value = json.loads(open(name + '.json').read())\n"
            "frame = pd.read_csv('tracked.csv')\n"
            "missing, flag = frame['name'][1], frame['flag'][0]\n"
            "frame.to_csv('derived.csv')\n"
            "with open('note.txt', 'w') as handle:\n"
            "    handle.write('\\n')\n"
            "np.save('flag.npy', (np.arange(1, 4) > 0).all())\n"
            "np.save('missing.npy', np.nan)\n",
            cell_id="cell-shared",
        )
        assert result["error"] is None, result["error"]
        result = kernel.execute(
            "for name, token in [('nan', 'NaN'), ('inf', 'Infinity'), "
            "('neg-inf', '-Infinity')]:\n"
            "    np.save('unrelated-' + name + '.npy', json.loads(token))\n",
            cell_id="cell-unrelated",
        )
        assert result["error"] is None, result["error"]
        assigned = kernel.execute(
            "frame['kind'] = pd.Series(['control'] * len(frame), dtype=object)\n"
            "kind, name = frame['kind'][0], frame['name'][0]\n",
            cell_id="cell-assign",
        )
        # a later Cell's literal is the interned object the frame handed back
        later = kernel.execute(
            "with open('kind.txt', 'w') as handle:\n"
            "    handle.write('control')\n"
            "with open('name.txt', 'w') as handle:\n"
            "    handle.write(name)\n"
            "with open('printed.txt', 'w') as handle:\n"
            "    print(name, file=handle)\n"
            "with open('formatted.txt', 'w') as handle:\n"
            "    handle.write(f'{name}')\n",
            cell_id="cell-later",
        )
    for outcome in (result, assigned, later):
        assert outcome["error"] is None, outcome["error"]

    def inputs(filename):
        artifact = store.artifact_by_filename(filename, frame_id, strict=True)
        if artifact is None:
            return []
        return [
            row["version_id"]
            for row in store.lineage_inputs(artifact["latest_version_id"])
        ]

    assert inputs("derived.csv") == [versions["tracked.csv"]]
    assert inputs("name.txt") == [versions["tracked.csv"]]
    for filename in ("printed.txt", "formatted.txt"):
        assert (workspace / filename).read_text().strip() == "science"
        assert inputs(filename) == [versions["tracked.csv"]]
    unrelated = (
        "note.txt",
        "flag.npy",
        "missing.npy",
        "kind.txt",
        *(f"unrelated-{name}.npy" for name in constants),
    )
    assert all((workspace / name).is_file() for name in unrelated)
    assert {name: inputs(name) for name in unrelated} == {
        name: [] for name in unrelated
    }, "a file named an input it never read"
