"""Whether the environment panel's claim matches what the row can support.

`env_snapshots` carries two columns whose whole purpose is to qualify the
claim. `generation_confidence` is `verified` when the snapshot's address
includes its generation — so no later kernel can share the row — and
`legacy_unverified` when it was written before that was true. `provenance`
carries the fallback path's own words, e.g. "assumed: no kernel generation on
record". The migration that added the confidence column says, in its own
docstring, that "a reader that needs certainty filters on the label".

No reader did. `grep -c generation_confidence app.js` was 0. The panel drew one
distinction — captured vs live — so every captured snapshot rendered as
"Recorded from the kernel environment at the time this artifact was produced",
including the ones the store had explicitly marked as unable to support that.
The label was computed, migrated, stored, and shipped over the wire to be
ignored, which left the UI making the strong claim on the strength of data
saying it should not.

This is the display-layer version of what `capture_environment` was fixed for:
provenance that is wrong rather than absent, which is worse because it gets
believed.

Both halves are checked here — the payload really carries the fields, and the
branch really reads them — because either alone would pass while the panel
stayed wrong.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.server import gateway as gateway_mod
from openai4s.server import local_auth

APP_JS = Path("openai4s/server/webui/app.js").read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the wire: the fields have to arrive before anything can read them
# --------------------------------------------------------------------------


class _Hub:
    def emitter(self, root_frame_id):
        return lambda event: None

    def broadcast(self, root_frame_id, event):
        return None


@pytest.fixture
def api(tmp_path):
    cfg = Config(
        data_dir=tmp_path,
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
        max_turns=1,
    )
    runner = gateway_mod.SessionRunner(cfg, _Hub())
    handler_class = gateway_mod.make_handler(cfg, _Hub(), runner)
    token = local_auth.read_token(tmp_path) or ""

    def get(path):
        handler = object.__new__(handler_class)
        handler._correlation_id = "req-1"
        sent: dict = {}
        handler._send = lambda code, body, ctype, extra=None: sent.update(
            code=code, body=json.loads(body.decode("utf-8"))
        )
        handler.command = "GET"
        handler.path = f"/api/v1{path}"
        handler.headers = {"Content-Length": "0", local_auth.TOKEN_HEADER: token}
        handler._route("GET")
        return sent

    return runner, get


def _artifact_with_snapshot(runner, snapshot):
    """An artifact whose latest version points at `snapshot`."""
    store = runner.store
    project_id = store.create_project(name="p", description="", context="")
    if isinstance(project_id, dict):
        project_id = project_id["project_id"]
    frame = runner.create_session(project_id)
    snapshot_id = store.upsert_env_snapshot(snapshot)
    path = Path(runner.cfg.data_dir) / "out.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    rec = store.save_artifact(
        path=str(path),
        filename="out.csv",
        content_type="text/csv",
        size_bytes=8,
        checksum="deadbeef",
        root_frame_id=frame,
        env_snapshot_id=snapshot_id,
    )
    return rec["artifact_id"]


def test_an_unverified_snapshot_says_so_on_the_wire(api):
    """A snapshot with no generation cannot be attributed to one production
    run, and the row records both facts. Neither is any use to the panel if the
    route drops them."""
    runner, get = api
    artifact_id = _artifact_with_snapshot(
        runner,
        {
            "kind": "python",
            "python_version": "3.12.0",
            "platform": "test",
            "packages": [],
            "provenance": "assumed: no kernel generation on record",
        },
    )
    sent = get(f"/artifacts/{artifact_id}/environment")
    assert sent["code"] == 200
    env = sent["body"]
    assert env["source"] == "captured"
    assert env["generation_confidence"] is None
    assert env["provenance"] == "assumed: no kernel generation on record"


def test_a_generation_backed_snapshot_is_marked_verified(api):
    """The other side of the same wire. Without this, a fix that hard-coded
    every snapshot to "unverified" would pass the test above."""
    runner, get = api
    artifact_id = _artifact_with_snapshot(
        runner,
        # Populated the way a real generation-backed snapshot is. The frozen
        # response shape is the union of what the suite observes, so a fixture
        # that leaves fields null pins them to null-only — and the first real
        # artifact with an interpreter path then "breaks" a contract that was
        # only ever describing this fixture's omissions.
        {
            "kind": "python",
            "python_version": "3.12.0",
            "implementation": "CPython",
            "platform": "test",
            "packages": [{"name": "numpy", "version": "2.1.0"}],
            "package_count": 1,
            "interpreter": "/opt/envs/protein/bin/python",
            "environment_name": "protein",
            "generation_id": "gen-42",
            "packages_unavailable": None,
            "provenance": "captured from kernel generation gen-42",
        },
    )
    env = get(f"/artifacts/{artifact_id}/environment")["body"]
    assert env["generation_confidence"] == "verified"
    assert env["generation_id"] == "gen-42"
    assert env["interpreter"] == "/opt/envs/protein/bin/python"
    assert env["environment_name"] == "protein"


# --------------------------------------------------------------------------
# the panel: the real branch, run
# --------------------------------------------------------------------------

_HARNESS = """
'use strict';
function render(env) {
  const keys = [];
  const rendered = [];
  const t = (k) => { keys.push(k); return "<" + k + ">"; };
  const publicText = (s, n) => String(s).slice(0, n);
  const iconEl = (name) => ({ icon: name, appendChild() {} });
  const el = (tag, cls, text) => ({ tag, cls, text, appendChild() {} });
  const body = { appendChild: (node) => rendered.push(node) };
__SNIPPET__
  return { keys, rendered };
}
const env = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(render(env)));
"""


def _snippet() -> str:
    """The branch as it is actually shipped, not a paraphrase of it."""
    start = APP_JS.index('  const captured = env.source !== "live";')
    end = APP_JS.index("  const remote = env.remote || [];", start)
    return APP_JS[start:end]


def _render(env: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    script = _HARNESS.replace("__SNIPPET__", _snippet())
    result = subprocess.run(
        [node, "-e", script, json.dumps(env)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_snippet_extraction_still_finds_the_branch():
    """If the anchors go stale, every test below renders nothing and passes."""
    snippet = _snippet()
    assert "generation_confidence" in snippet
    assert snippet.count("t(") >= 3


def test_a_legacy_snapshot_does_not_claim_to_be_recorded():
    """The defect, as the label a user reads.

    `legacy_unverified` means the named generation produced this environment
    but may not be the only one that did — which is exactly the claim the old
    label made unconditionally.
    """
    out = _render(
        {
            "source": "captured",
            "generation_confidence": "legacy_unverified",
            "packages": [],
        }
    )
    assert "prov.env.recordedUnverified" in out["keys"]
    assert "prov.env.recorded" not in out["keys"]


def test_a_snapshot_with_no_confidence_at_all_is_not_promoted():
    """The commonest unverified row: written by the fallback path, so the
    column is NULL rather than a string. A check for the literal
    `"legacy_unverified"` would let this one through."""
    out = _render({"source": "captured", "packages": []})
    assert "prov.env.recordedUnverified" in out["keys"]
    assert "prov.env.recorded" not in out["keys"]


def test_a_verified_snapshot_still_makes_the_strong_claim():
    """The claim is correct here and has to survive. A fix that qualified
    everything would be as wrong as one that qualified nothing — it would just
    be wrong in the safe direction, and stop telling anyone anything."""
    out = _render(
        {"source": "captured", "generation_confidence": "verified", "packages": []}
    )
    assert "prov.env.recorded" in out["keys"]
    assert "prov.env.recordedUnverified" not in out["keys"]


def test_the_live_fallback_is_unchanged():
    """This distinction already worked and is not what was being fixed."""
    out = _render({"source": "live", "generation_confidence": None, "packages": []})
    assert "prov.env.liveFallback" in out["keys"]
    assert "prov.env.recorded" not in out["keys"]
    assert "prov.env.recordedUnverified" not in out["keys"]


def test_an_unverified_snapshot_is_styled_as_a_caveat():
    """The wording alone is easy to miss in a panel of chips; the warn class is
    what makes it read as a qualification rather than a detail."""
    verified = _render(
        {"source": "captured", "generation_confidence": "verified", "packages": []}
    )
    unverified = _render({"source": "captured", "packages": []})
    assert "ok" in verified["rendered"][0]["cls"]
    assert "warn" in unverified["rendered"][0]["cls"]


def test_the_rows_own_explanation_is_shown_when_it_has_one():
    """`provenance` is written next to the code that could not establish the
    provenance. Rewording it here would lose why, and there are several whys."""
    out = _render(
        {
            "source": "captured",
            "provenance": "assumed: no kernel generation on record",
            "packages": [],
        }
    )
    texts = [str(node.get("text") or "") for node in out["rendered"]]
    assert any("no kernel generation on record" in text for text in texts)


def test_a_verified_snapshot_does_not_argue_with_itself(api):
    """A verified row can still carry a `provenance` note. Showing a caveat
    under a confident label would read as a contradiction."""
    out = _render(
        {
            "source": "captured",
            "generation_confidence": "verified",
            "provenance": "captured from kernel generation gen-42",
            "packages": [],
        }
    )
    texts = [str(node.get("text") or "") for node in out["rendered"]]
    assert not any("gen-42" in text for text in texts)


def test_the_provenance_string_is_escaped_like_every_other_field():
    """It reaches the store from a code path, not a user — but it is rendered
    beside fields that are treated as untrusted, and the day one of those paths
    interpolates a filename this is the difference."""
    out = _render({"source": "captured", "provenance": "x" * 500, "packages": []})
    texts = [str(node.get("text") or "") for node in out["rendered"]]
    assert any(len(text) == 200 for text in texts), "the note is not length-capped"


def test_both_languages_have_the_new_string():
    """A missing key renders as the raw dotted name, which is worse than the
    over-confident label it replaced."""
    for anchor in ('"prov.env.recordedUnverified":',):
        assert APP_JS.count(anchor) == 2, "the string is not in both dictionaries"


def test_a_non_python_snapshot_reaches_the_route_with_its_explanation(api):
    """The other populated shape: `packages_unavailable` carries a sentence on
    an R artifact and is null on a Python one, so the frozen contract has to
    admit both."""
    runner, get = api
    snapshot = runner.artifacts._snapshot_for(None, "r")
    artifact_id = _artifact_with_snapshot(runner, snapshot)
    env = get(f"/artifacts/{artifact_id}/environment")["body"]
    assert env["kind"] == "r"
    assert isinstance(env["packages_unavailable"], str)
    assert env["packages"] == []


def test_an_r_artifacts_environment_has_no_python_implementation(api):
    """Why the frozen shape for this route had to widen.

    `implementation` was frozen as a string because every captured sample came
    from an artifact the daemon's own interpreter produced. Production has two
    cases where it is null and always did: an R artifact (the non-Python branch
    never sets it) and a Python cell in a selected conda environment (the
    interpreter differs, and stamping this process's implementation onto it is
    the confidently-wrong provenance `capture_environment` exists to avoid).

    So the contract was captured from an unrepresentative sample, and a client
    written against it breaks on the first R artifact. This drives the real
    capture path rather than a hand-built row, so the widened shape is
    justified by what the product produces.
    """
    runner, _get = api
    snapshot = runner.artifacts._snapshot_for(None, "r")
    assert snapshot["kind"] == "r"
    assert snapshot.get("implementation") is None
    assert snapshot.get("python_version") is None
    assert "kernel" in (snapshot.get("packages_unavailable") or "")


def test_a_python_cell_in_another_interpreter_is_not_stamped_with_this_one(api):
    """The second null case, and the one that would otherwise look like a bug
    in the snapshot rather than a deliberate refusal."""
    runner, _get = api
    generation = {
        "runtime": "python",
        "interpreter": "/somewhere/else/bin/python",
        "environment_name": "protein",
        "generation_id": "gen-7",
    }
    snapshot = runner.artifacts._snapshot_for(generation, "python")
    assert snapshot.get("implementation") is None
    assert snapshot.get("python_version") is None
