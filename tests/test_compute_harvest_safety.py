"""The harvest destination and the confinement gate, tried against attack.

Two P1s from the second review, plus one P2, all in the compute manager:

  * the hpc root lives inside the kernel-writable workspace, so a cell can
    replace ``hpc`` or a per-job directory with a symlink before a harvest and
    redirect the remote bytes anywhere the daemon can write. ``safe_extract_tar``
    guards the archive's contents; nothing guarded the directory they land in;
  * under ``OPENAI4S_COMPUTE_CONFINEMENT=enforce`` the helper wrapper can raise
    after the initial availability gate — and result/terminate never call that
    gate — so the fallback ran the credential and the provider shim unconfined
    despite enforce;
  * a declaration made entirely of hidden or stay-remote entries produced an
    empty pattern set, which ``reconcile`` read as "nothing declared" and
    featured every harvested file, surfacing the diagnostics the caller hid.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from openai4s.compute import manifest
from openai4s.compute.manager import ComputeError, ComputeManager
from openai4s.config import Config


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "skills").mkdir()
    (tmp_path / "ws").mkdir()
    return types.SimpleNamespace(
        data_dir=tmp_path,
        skills_dir=tmp_path / "skills",
        db_path=Config(data_dir=tmp_path).db_path,
    )


def _manager(cfg, workspace):
    return ComputeManager(cfg, workspace=workspace)


# --------------------------------------------------------------------------
# the harvest destination cannot be redirected by a symlink
# --------------------------------------------------------------------------


def test_a_symlinked_hpc_root_is_refused(cfg, tmp_path):
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    outside = tmp_path / "escape"
    outside.mkdir()
    # A cell replaces the hpc root with a link to somewhere it should not reach.
    hpc = ws / "hpc"
    if hpc.exists():
        for child in hpc.iterdir():
            child.unlink()
        hpc.rmdir()
    hpc.symlink_to(outside)

    with pytest.raises(ComputeError) as error:
        manager._safe_harvest_dest("job-abc")
    assert "symlink" in str(error.value)


def test_a_symlinked_per_job_dir_is_refused(cfg, tmp_path):
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    outside = tmp_path / "escape"
    outside.mkdir()
    (ws / "hpc" / "job-abc").symlink_to(outside)

    with pytest.raises(ComputeError) as error:
        manager._safe_harvest_dest("job-abc")
    assert "symlink" in str(error.value)
    assert not (outside / "leaked").exists()


def test_a_legitimate_harvest_dir_is_allowed_and_contained(cfg, tmp_path):
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    dest = manager._safe_harvest_dest("job-abc")
    assert dest.is_dir()
    assert Path(manager._hpc_root_real) in dest.resolve().parents


# --------------------------------------------------------------------------
# enforce mode fails closed when no boundary can be established
# --------------------------------------------------------------------------


def test_enforce_refuses_to_run_the_helper_unconfined(cfg, tmp_path, monkeypatch):
    """The wrapper raising after the availability gate must not degrade to the
    plain helper under enforce — that runs the credential unconfined."""
    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "enforce")
    manager = _manager(cfg, tmp_path / "ws")
    assert manager._confinement_mode == "enforce"

    from openai4s.security import byoc_confinement

    def refuse(*_a, **_k):
        raise byoc_confinement.ConfinementUnavailable("backend went away")

    monkeypatch.setattr(byoc_confinement, "wrap", refuse)

    spawned: list = []
    monkeypatch.setattr(
        manager,
        "_spawn_helper",
        lambda *a, **k: spawned.append(a) or types.SimpleNamespace(returncode=0),
    )

    prov = {"provider_py": str(tmp_path / "provider.py"), "meta": {}}
    (tmp_path / "provider.py").write_text("PROVIDER = object", encoding="utf-8")
    with pytest.raises(ComputeError) as error:
        manager._run_helper(prov, "wait", {}, {"token": "x"}, tmp_path / "ws")

    assert error.value.error_kind == "confinement_unavailable"
    assert error.value.indeterminate is False
    assert not spawned, "the helper must never be spawned unconfined under enforce"


def test_auto_still_degrades_visibly(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_COMPUTE_CONFINEMENT", "auto")
    manager = _manager(cfg, tmp_path / "ws")

    from openai4s.security import byoc_confinement

    monkeypatch.setattr(
        byoc_confinement,
        "wrap",
        lambda *a, **k: (_ for _ in ()).throw(
            byoc_confinement.ConfinementUnavailable("no backend")
        ),
    )
    import json

    spawned: list = []
    stage = tmp_path / "ws"

    def fake_spawn(argv, creds, env, deadline, op, st):
        spawned.append(argv)
        (Path(st) / "reply.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(manager, "_spawn_helper", fake_spawn)
    (tmp_path / "provider.py").write_text("PROVIDER = object", encoding="utf-8")
    prov = {"provider_py": str(tmp_path / "provider.py"), "meta": {}}

    # It must reach the spawn (degraded), not raise.
    reply = manager._run_helper(prov, "wait", {}, {"token": "x"}, stage)
    assert reply == {"ok": True}
    assert spawned, "auto must still run the helper"
    # ...with expect_confined turned off (the plain form ends in '0').
    assert spawned[0][-1] == "0"


# --------------------------------------------------------------------------
# a hidden-only declaration features nothing
# --------------------------------------------------------------------------


def test_a_declaration_of_only_hidden_outputs_features_nothing():
    entries = [
        {"path": "diagnostic.log", "sha256": "a" * 64},
        {"path": "trace.json", "sha256": "b" * 64},
    ]
    declared = [
        {"glob": "*.log", "visibility": "hidden"},
        {"glob": "*.json", "residency": "remote"},
    ]
    featured, unmatched = manifest.reconcile(entries, declared)
    assert featured == [], "a hidden/remote-only declaration must feature nothing"
    assert unmatched == []


def test_omitting_outputs_still_features_everything():
    entries = [{"path": "result.csv", "sha256": "c" * 64}]
    featured, _ = manifest.reconcile(entries, None)
    assert featured == ["result.csv"]
