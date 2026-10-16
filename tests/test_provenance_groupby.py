"""Real pandas group reductions retain known inputs without changing their values."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.host_dispatch import HostDispatcher
from openai4s.kernel import Kernel, provenance
from openai4s.server.artifacts import ArtifactManager
from openai4s.store import get_store

_PROBE = r"""
import json, os, sys
from pathlib import Path
import pandas as pd
from openai4s.kernel import provenance

folder, mode = Path(sys.argv[1]), sys.argv[2]
paths = {'input.csv': 'v-input', 'keys.csv': 'v-keys', 'unrelated.csv': 'v-unrelated'}
events = []
def host_call(method, args):
    if method == 'prov_resolve_path':
        return paths.get(Path(args[0]).name) if Path(args[0]).parent == folder else None
    if method == 'prov_record':
        events.append(args[0])
    return {'ok': True}

if mode == 'disabled':
    os.environ['OPENAI4S_PROVENANCE_OFF'] = '1'
provenance.install(host_call)
provenance.install(host_call)  # install remains idempotent
data = pd.read_csv(folder / 'input.csv')
keys = pd.read_csv(folder / 'keys.csv')
unrelated = pd.read_csv(folder / 'unrelated.csv')
error = None
if mode in ('named', 'aggregate'):
    grouped = data.groupby('group', as_index=False)
    result = getattr(grouped, 'agg' if mode == 'named' else 'aggregate')(total=('value', 'sum'))
elif mode in ('scalar-agg', 'scalar-aggregate', 'callable-agg', 'callable-aggregate'):
    grouped = data.groupby('group')['value']
    method = 'aggregate' if mode.endswith('aggregate') else 'agg'
    func = (lambda values: values.sum()) if mode.startswith('callable') else 'sum'
    result = getattr(grouped, method)(func)
elif mode == 'series':
    result = data['value'].groupby(data['group']).sum()
elif mode == 'external':
    result = data['value'].groupby(by=keys['key']).sum()
elif mode == 'dataframe-selection':
    result = data.groupby('group')[['value']].sum()
elif mode == 'attribute-selection':
    result = data.groupby('group').value.sum()
elif mode == 'untracked':
    literal = pd.DataFrame({'group': ['a', 'a', 'b'], 'value': [1, 2, 4]})
    result = literal.groupby('group')['value'].sum()
elif mode == 'native-error':
    try:
        data.groupby('missing').sum()
    except Exception as exc:
        error = type(exc).__name__
    result = None
elif mode == 'tagging-error':
    grouped = data.groupby('group')['value']
    def broken_tags(*args):
        raise RuntimeError('synthetic tracing failure')
    provenance.merge_tags = broken_tags
    result = grouped.sum()
else:
    grouped = data.groupby('group')['value']
    result = getattr(grouped, 'sum' if mode == 'disabled' else mode)()

if result is not None:
    result.to_json(folder / 'result.json', orient='records')
    values = json.loads((folder / 'result.json').read_text())
    tags = sorted(provenance.get_tags(result))
else:
    values, tags = None, []
provenance.uninstall()
print(json.dumps({'pandas': pd.__version__, 'values': values, 'tags': tags,
                  'recorded_inputs': sorted({vid for event in events for vid in event['input_version_ids']}),
                  'error': error}))
"""


@pytest.mark.stubbed_backend
@pytest.mark.parametrize(
    "mode,values,tags,error",
    [
        (
            "named",
            [{"group": "a", "total": 3}, {"group": "b", "total": 4}],
            ["v-input"],
            None,
        ),
        (
            "aggregate",
            [{"group": "a", "total": 3}, {"group": "b", "total": 4}],
            ["v-input"],
            None,
        ),
        ("scalar-agg", [3, 4], ["v-input"], None),
        ("scalar-aggregate", [3, 4], ["v-input"], None),
        ("callable-agg", [3, 4], ["v-input"], None),
        ("callable-aggregate", [3, 4], ["v-input"], None),
        ("sum", [3, 4], ["v-input"], None),
        ("mean", [1.5, 4.0], ["v-input"], None),
        ("min", [1, 4], ["v-input"], None),
        ("max", [2, 4], ["v-input"], None),
        ("count", [2, 1], ["v-input"], None),
        ("size", [2, 1], ["v-input"], None),
        ("series", [3, 4], ["v-input"], None),
        ("external", [3, 4], ["v-input", "v-keys"], None),
        ("dataframe-selection", [{"value": 3}, {"value": 4}], ["v-input"], None),
        ("attribute-selection", [3, 4], ["v-input"], None),
        ("untracked", [3, 4], [], None),
        ("disabled", [3, 4], [], None),
        ("tagging-error", [3, 4], [], None),
        ("native-error", None, [], "KeyError"),
    ],
    ids=[
        "named",
        "aggregate",
        "scalar-agg",
        "scalar-aggregate",
        "callable-agg",
        "callable-aggregate",
        "sum",
        "mean",
        "min",
        "max",
        "count",
        "size",
        "series",
        "external-grouper",
        "dataframe-selection",
        "attribute-selection",
        "untracked",
        "disabled",
        "tracing-failure",
        "native-error",
    ],
)
def test_real_pandas_grouped_values_and_known_input_tags(
    tmp_path, mode, values, tags, error
):
    pytest.importorskip("pandas")
    # Isolate persistent class/builtin patches from the parent pytest process.
    (tmp_path / "input.csv").write_text(
        "group,value\na,1\na,2\nb,4\n", encoding="utf-8"
    )
    (tmp_path / "keys.csv").write_text("key\nx\nx\ny\n", encoding="utf-8")
    (tmp_path / "unrelated.csv").write_text("value\n900\n", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(_PROBE), str(tmp_path), mode],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    observed = json.loads(completed.stdout)
    assert observed["values"] == values
    assert observed["error"] == error
    assert observed["tags"] == tags
    assert observed["recorded_inputs"] == tags
    assert "v-unrelated" not in observed["recorded_inputs"]


def test_grouped_result_preserves_preexisting_known_tags():
    class Operand:
        pass

    parent = Operand()
    result = Operand()
    provenance.set_tags(parent, frozenset({"v-parent"}))
    provenance.set_tags(result, frozenset({"v-existing"}))
    wrapped = provenance._prov_wrap_grouped_result(lambda _parent: result)
    assert wrapped(parent) is result
    assert provenance.get_tags(result) == frozenset({"v-parent", "v-existing"})


def test_real_kernel_grouped_writer_and_store_reopen(tmp_path):
    pytest.importorskip("pandas")
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    frame = store.new_frame(kind="turn", project_id="default")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "input.csv"
    source.write_text("group,value\na,1\na,2\nb,4\n", encoding="utf-8")
    input_version = store.save_artifact(
        path=str(source),
        filename="input.csv",
        content_type="text/csv",
        size_bytes=source.stat().st_size,
        checksum=hashlib.sha256(source.read_bytes()).hexdigest(),
        frame_id=frame,
        project_id="default",
    )
    dispatcher = HostDispatcher(cfg=cfg, frame_id=frame, workspace=workspace)
    manager = ArtifactManager(
        data_dir=cfg.data_dir,
        store=store,
        workspace_for=lambda _frame: workspace,
        broadcast=lambda *_args: None,
        guess_content_type=lambda _name: "application/json",
        checksum=lambda path: hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    session = SimpleNamespace(
        root_frame_id=frame, project_id="default", workspace=workspace
    )
    before = manager.snapshot(workspace)
    expected = [{"group": "a", "total": 3}, {"group": "b", "total": 4}]
    try:
        with Kernel(dispatcher=dispatcher, cwd=str(workspace)) as kernel:
            result = kernel.execute(
                "import pandas as pd\n"
                "data = pd.read_csv('input.csv')\n"
                "grouped = data.groupby('group', as_index=False).agg(total=('value', 'sum'))\n"
                "grouped.to_json('result.json', orient='records')\n",
                cell_id="cell-groupby",
            )
        assert result["error"] is None
        assert (
            json.loads((workspace / "result.json").read_text(encoding="utf-8"))
            == expected
        )
        output = store.artifact_by_filename("result.json", frame, strict=True)
        assert (
            output is not None
        ), "the real grouped writer must register its output without an explicit SDK save"
        output_version = output["latest_version_id"]
        assert store.version_meta(output_version)["producing_cell_id"] == "cell-groupby"
        assert [row["version_id"] for row in store.lineage_inputs(output_version)] == [
            input_version["version_id"]
        ]
        captured = manager.capture(
            session, 1, "cell-groupby", before, lambda _event: None
        )
        assert [artifact["version_id"] for artifact in captured.artifacts] == [
            output_version
        ]
    finally:
        store.close()
    reopened = get_store(cfg.db_path)
    try:
        assert (
            reopened.artifact_by_filename("result.json", frame, strict=True)[
                "latest_version_id"
            ]
            == output_version
        )
        assert [
            row["version_id"] for row in reopened.lineage_inputs(output_version)
        ] == [input_version["version_id"]]
        snapshot = reopened.version_meta(output_version)["snapshot_path"]
        assert (
            snapshot
            and json.loads(Path(snapshot).read_text(encoding="utf-8")) == expected
        )
    finally:
        reopened.close()
