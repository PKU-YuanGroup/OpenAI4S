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

import shutil
import subprocess
import types
from pathlib import Path

import pytest

from openai4s.compute import manifest, states
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


def test_publish_refuses_a_symlinked_hpc_root_swapped_after_validation(cfg, tmp_path):
    """`_safe_harvest_dest` validated the path earlier, but a cell can swap the
    `hpc` parent for a symlink before publication. The publish must not follow
    it and move the trusted tree outside the workspace."""
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    staging = manager._host_staging_dir("job-x")
    (staging / "result.csv").write_text("ok\n", encoding="utf-8")

    outside = tmp_path / "escape"
    outside.mkdir()
    # After validation, the cell replaces the hpc root with a symlink.
    hpc = ws / "hpc"
    for child in list(hpc.iterdir()):
        child.unlink() if child.is_file() else shutil.rmtree(child)
    hpc.rmdir()
    hpc.symlink_to(outside)

    with pytest.raises(ComputeError) as error:
        manager._publish_harvest(staging, hpc / "job-x")
    assert "not a real directory" in str(error.value)
    # The trusted tree did not land in the attacker's directory.
    assert not (outside / "job-x").exists()


def test_publish_replaces_a_symlinked_per_job_entry_without_following_it(cfg, tmp_path):
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    staging = manager._host_staging_dir("job-y")
    (staging / "result.csv").write_text("ok\n", encoding="utf-8")

    outside = tmp_path / "escape2"
    outside.mkdir()
    (outside / "sentinel").write_text("keep", encoding="utf-8")
    # The per-job entry is a symlink to the attacker's dir.
    (ws / "hpc" / "job-y").symlink_to(outside)

    manager._publish_harvest(staging, ws / "hpc" / "job-y")

    # The symlink was replaced by a real dir with the harvested file, and the
    # attacker's directory was not written into or removed.
    assert (ws / "hpc" / "job-y" / "result.csv").is_file()
    assert not (ws / "hpc" / "job-y").is_symlink()
    assert (outside / "sentinel").exists()


def test_the_staging_dir_is_host_owned_and_outside_the_workspace(cfg, tmp_path):
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    staging = manager._host_staging_dir("job-abc")
    assert staging.is_dir()
    # Never under the kernel-writable workspace, so a cell cannot pre-plant it.
    assert ws.resolve() not in staging.resolve().parents
    assert Path(cfg.data_dir).resolve() in staging.resolve().parents


def test_a_planted_output_is_not_counted_as_produced(cfg, tmp_path):
    """The trust-boundary defect: a cell creates the declared output under the
    workspace harvest dir before polling. The manifest is built from a host-
    owned staging tree, so the plant is not counted and the wholesale publish
    discards it."""
    ws = tmp_path / "ws"
    manager = _manager(cfg, ws)
    manager._jobs["job-plant"] = {
        "job_id": "job-plant",
        "provider": "ssh:lab",
        "alias": "lab",
        "workdir": str(tmp_path / "remote"),
        "status": states.RUNNING,
        "pid": "1",
        "pgid": "1",
        "outputs": ["model.pt"],
    }
    (tmp_path / "remote").mkdir()

    # The cell plants the declared output where the workspace harvest lands.
    planted_dir = ws / "hpc" / "job-plant"
    planted_dir.mkdir(parents=True)
    (planted_dir / "model.pt").write_bytes(b"forged-weights")

    # The remote produced nothing: the harvest is empty.
    def fake_harvest_ssh(alias, workdir, staging, exclude):
        return None, [], []  # no error, nothing oversized, nothing stayed

    manager._harvest_ssh = fake_harvest_ssh

    def probe_says_exit_zero(argv, *a, **k):
        return subprocess.CompletedProcess(argv, 0, b"RC 0 -\n", b"")

    import openai4s.compute.manager as mod

    original_run = mod.subprocess.run
    mod.subprocess.run = probe_says_exit_zero
    try:
        result = manager._result_ssh(manager._jobs["job-plant"])
    finally:
        mod.subprocess.run = original_run

    # The planted file must not be counted as a produced output, and the job
    # must not be marked succeeded off forged bytes.
    harvested = {Path(p).name for p in result["output_files"]}
    assert (
        "model.pt" not in harvested
    ), f"a planted output was counted as produced: {harvested}"
    assert (
        result["status"] != states.SUCCEEDED
    ), "an empty harvest with a planted file must not report success"
    # And the plant is gone from the published workspace dir.
    assert not (planted_dir / "model.pt").exists()


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
