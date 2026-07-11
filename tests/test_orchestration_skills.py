"""End-to-end tests for the 4 runtime-orchestration bundled skills.

These drive a REAL Kernel subprocess (worker.py) bound to a REAL HostDispatcher
through the actual JSON-per-line wire protocol, exercising the exact host surface
each skill calls at runtime:

    customize               -> host.skills.{list,get,read,edit,publish,delete}
    self-awareness          -> host.query(sql=...), host.query.schema()
    managed-model-endpoints -> host.endpoints.{free_port,register,status,probe,list}
    compute-env-setup       -> pkgscan.scan_envs (+ host.capabilities compute flag)

A regression from this suite caught a real bug: endpoints.register allocated a
fresh random port on every call, so a byte-identical re-registration wrongly
tripped the approval card (port is the name's mutex and must be reused).
"""
import json
import shutil
import sqlite3

import pytest

from openai4s import pkgscan
from openai4s.config import Config
from openai4s.host_dispatch import build_dispatcher
from openai4s.kernel import Kernel


@pytest.fixture
def session(tmp_path, monkeypatch):
    """Isolated dispatcher + live kernel bound together, on a throwaway DB."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path))
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    disp = build_dispatcher(cfg=cfg)
    # Production is fail-closed without an attached approval channel.  This
    # lifecycle test explicitly authorizes the two destructive transitions it
    # intends to exercise.
    for tool in ("skills_publish", "skills_delete"):
        disp.store.set_permission_rule(
            scope="global",
            scope_id="",
            tool=tool,
            pattern="e2e-demo-skill",
            decision="allow",
        )
    disp.store.log_host_call(
        method="seed_ping", args=[{"x": 1}], ok=True, frame_id="frame-seed"
    )
    kernel = Kernel(dispatcher=disp, mode="repl")
    try:
        yield cfg, disp, kernel
    finally:
        kernel.shutdown()
        draft = cfg.data_dir / "user-skills" / "e2e-demo-skill"
        if draft.exists():
            shutil.rmtree(draft)


def _emit(kernel, expr):
    """Run `print(json.dumps(<expr>))` in the worker; parse the JSON back."""
    code = f"import json as _j; print(_j.dumps({expr}, default=str))"
    resp = kernel.execute(code)
    out = (resp.get("stdout") or "").strip().splitlines()
    assert out, f"no stdout for {expr!r}; error={resp.get('error')}"
    return json.loads(out[-1])


def test_kernel_boots_with_host_facade(session):
    _cfg, _disp, kernel = session
    assert _emit(kernel, "hasattr(host, 'skills') and hasattr(host, 'query')") is True


# --- customize -----------------------------------------------------------


def test_customize_skill_lifecycle(session):
    _cfg, _disp, kernel = session
    catalog = _emit(kernel, "host.skills.list()")
    assert any(s.get("origin") == "openai4s" for s in catalog)

    # read-only openai4s skills reject edits
    kernel.execute(
        "def _edit_ro(n):\n"
        "    try:\n"
        "        host.skills.edit(n, 'SKILL.md', 'HACKED'); return 'ALLOWED'\n"
        "    except Exception as e:\n"
        "        return type(e).__name__\n"
    )
    target = next(s["name"] for s in catalog if s.get("origin") == "openai4s")
    assert _emit(kernel, f"_edit_ro('{target}')") in ("PermissionError", "RuntimeError")

    # create draft -> publish -> edit -> delete
    kernel.execute(
        "host.skills.edit('e2e-demo-skill', 'SKILL.md',\n"
        "    '---\\nname: e2e-demo-skill\\ndescription: probe\\n"
        "origin: draft\\n---\\n# Demo\\nHello.\\n')"
    )
    assert (
        _emit(kernel, "host.skills.get('e2e-demo-skill')")["name"] == "e2e-demo-skill"
    )
    assert (
        _emit(kernel, "host.skills.publish('e2e-demo-skill')")["origin"] == "personal"
    )
    assert (
        _emit(
            kernel,
            "host.skills.edit('e2e-demo-skill','SKILL.md','x'," " old_string='Hello.')",
        )["ok"]
        is True
    )
    assert _emit(kernel, "host.skills.delete('e2e-demo-skill')")["ok"] is True


# --- self-awareness ------------------------------------------------------


def test_self_awareness_query(session):
    _cfg, _disp, kernel = session
    schema = _emit(kernel, "host.query.schema()")
    assert "frames" in schema

    rows = _emit(
        kernel,
        'host.query("SELECT name FROM sqlite_master '
        "WHERE type='table' ORDER BY name\", limit=100)",
    )
    tbls = {r["name"] for r in rows}
    assert {"frames", "execution_log", "artifacts"} <= tbls

    kernel.execute(
        "def _q_denied():\n"
        "    try:\n"
        "        host.query('SELECT * FROM host_call_log', limit=1); return 'ALLOWED'\n"
        "    except Exception as e:\n"
        "        return type(e).__name__\n"
    )
    assert _emit(kernel, "_q_denied()") != "ALLOWED"


# --- managed-model-endpoints ---------------------------------------------


def test_managed_endpoints_lifecycle(session):
    _cfg, _disp, kernel = session
    port = _emit(kernel, "host.endpoints.free_port()")
    assert isinstance(port, int) and 20000 <= port <= 29999

    reg = _emit(
        kernel,
        "host.endpoints.register('vllm', start='run', "
        "stop='kill', live='/health', credential='HF_TOKEN')",
    )
    assert reg["status"] == "registered" and reg["remote"] is False and reg["port"]

    # REGRESSION: byte-identical re-register must be a silent no-op. Broke when
    # register re-allocated a random port each call (url/fingerprint drift).
    reg2 = _emit(
        kernel,
        "host.endpoints.register('vllm', start='run', "
        "stop='kill', live='/health', credential='HF_TOKEN')",
    )
    assert reg2["changed"] is False

    reg3 = _emit(
        kernel,
        "host.endpoints.register('vllm', start='run --tp 2', "
        "stop='kill', live='/health', credential='HF_TOKEN')",
    )
    assert reg3["status"] == "awaiting_approval"
    assert reg3["approval"]["required"] is True

    remote = _emit(
        kernel, "host.endpoints.register('remote', " "url='https://api.example.com/v1')"
    )
    assert remote["remote"] is True and remote["port"] is None

    probe = _emit(kernel, "host.endpoints.probe('vllm')")
    assert probe["status"] in ("starting", "live")

    listing = _emit(kernel, "[e['name'] for e in host.endpoints.list()]")
    assert "vllm" in listing and "remote" in listing


# --- compute-env-setup ---------------------------------------------------


def test_compute_env_setup_pkgscan(session, tmp_path):
    _cfg, _disp, kernel = session
    caps = _emit(kernel, "host.capabilities()")
    # compute is advertised when a remote-compute provider/family is
    # installed (the repo ships remote-compute-ssh); r_kernel mirrors whether
    # an R interpreter is actually resolvable on this machine (live probe).
    from openai4s.kernel.r_kernel import resolve_r_interpreter

    assert caps["compute"] is True
    assert caps["r_kernel"] == (resolve_r_interpreter() is not None)

    env = tmp_path / "fakeenv"
    (env / "conda-meta").mkdir(parents=True)
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "python").write_text("#!/bin/sh\n")
    (env / "conda-meta" / "numpy-1.26.json").write_text(json.dumps({"name": "numpy"}))
    (env / "conda-meta" / "scikit_learn-1.4.json").write_text(
        json.dumps({"name": "scikit_learn"})
    )
    (env / ".openai4s_metadata.json").write_text(
        json.dumps({"kind": "conda", "language": "python"})
    )

    scan = pkgscan.scan_envs([env], require=["numpy", "scikit-learn", "torch"])[0]
    assert scan["language"] == "python"
    assert "numpy" in scan["has"] and "scikit-learn" in scan["has"]
    assert "torch" in scan["missing"]


# --- cross-cutting -------------------------------------------------------


def test_host_call_audit_trail(session):
    cfg, _disp, kernel = session
    _emit(kernel, "host.skills.list()")
    raw = sqlite3.connect(str(cfg.db_path))
    n = raw.execute("SELECT COUNT(*) FROM host_call_log").fetchone()[0]
    raw.close()
    assert n > 0
