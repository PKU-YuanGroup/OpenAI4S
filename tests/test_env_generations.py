"""An environment change is a transaction, and these run it on a real disk.

`openai4s setup` installed in place: `conda env create --update` mutates the
environment the running kernels are using, so an interrupted build left a
half-changed environment nothing could describe and no previous state to return
to. And an artifact's environment provenance can only name a generation if
generations exist.

Everything below uses a real temp directory, real files, and a real
`os.replace`. Only the *package manager* is injected — the transaction is the
part worth testing, and it is exactly the part a mocked filesystem would stop
exercising. The properties, in the order they matter:

  * a failed apply leaves the current environment untouched;
  * a crash mid-build leaves something visibly not a generation;
  * two applies cannot both land;
  * rollback moves a pointer and rebuilds nothing;
  * an applied generation is never rewritten.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

import pytest

from openai4s.kernel import env_generations as eg


def _spec(tmp_path: Path, name: str = "python", body: str = "numpy\n") -> Path:
    path = tmp_path / f"{name}.yml"
    path.write_text(body, encoding="utf-8")
    return path


def _completed(returncode: int = 0, stderr: bytes = b""):
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stderr=stderr
    )


@pytest.fixture
def store(tmp_path):
    """A store whose package manager builds a plausible prefix on real disk."""

    def runner(argv, cwd):
        # argv[-1] is the prefix this build was asked to create.
        prefix = Path(argv[-1])
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        (prefix / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        return _completed()

    return eg.EnvironmentStore(tmp_path / "environments", runner=runner)


def _build(prefix: Path, staged_spec: Path):
    # `staged_spec` is the immutable copy taken under the apply lock, not the
    # caller's path: the manifest's hash has to describe the bytes that were
    # actually built from, and a live path can be edited after the hash.
    assert staged_spec.is_file()
    return ("fake-conda", "env", "create", "--prefix", str(prefix))


def _verify(prefix: Path):
    assert (prefix / "bin" / "python").is_file(), "verify runs against the real build"
    return str(prefix / "bin" / "python"), ["numpy==1.26.0"]


def _apply(store, spec, name="python"):
    plan = store.plan(name, spec, tool="fake-conda")
    return store.apply(plan, spec, tool="fake-conda", build=_build, verify=_verify)


# --------------------------------------------------------------------------
# plan touches nothing
# --------------------------------------------------------------------------


def test_a_plan_reports_a_create_and_changes_nothing(store, tmp_path):
    spec = _spec(tmp_path)
    plan = store.plan("python", spec, tool="fake-conda")

    assert plan.action == eg.CREATE and plan.changes
    assert store.current_id("python") is None
    assert not (
        tmp_path / "environments" / "python"
    ).exists(), "planning must not create anything on disk"


def test_an_unchanged_spec_plans_to_do_nothing(store, tmp_path):
    spec = _spec(tmp_path)
    _apply(store, spec)
    plan = store.plan("python", spec, tool="fake-conda")

    assert plan.action == eg.NOOP and not plan.changes
    assert plan.from_generation == store.current_id("python")


def test_a_changed_spec_plans_a_replacement(store, tmp_path):
    spec = _spec(tmp_path)
    first = _apply(store, spec)
    spec.write_text("numpy\npandas\n", encoding="utf-8")

    plan = store.plan("python", spec, tool="fake-conda")
    assert plan.action == eg.REPLACE
    assert plan.from_generation == first.generation.id
    assert plan.spec_sha256 != first.generation.spec_sha256


# --------------------------------------------------------------------------
# apply builds a new generation and only then switches
# --------------------------------------------------------------------------


def test_apply_creates_a_generation_and_points_at_it(store, tmp_path):
    result = _apply(store, _spec(tmp_path))

    assert result.ok
    generation = result.generation
    assert generation.state == eg.READY
    assert store.current_id("python") == generation.id
    assert Path(generation.prefix, "bin", "python").is_file()
    assert generation.packages == ("numpy==1.26.0",)


def test_a_second_apply_leaves_the_first_generation_on_disk(store, tmp_path):
    spec = _spec(tmp_path)
    first = _apply(store, spec).generation
    spec.write_text("numpy\npandas\n", encoding="utf-8")
    second = _apply(store, spec).generation

    assert store.current_id("python") == second.id
    assert first.id != second.id
    assert Path(
        first.prefix, "bin", "python"
    ).is_file(), "the previous environment is what rollback restores; it must survive"


def test_the_pointer_only_ever_names_a_ready_generation(store, tmp_path):
    _apply(store, _spec(tmp_path))
    current = store.current("python")
    assert current is not None and current.state == eg.READY


# --------------------------------------------------------------------------
# failure injection: the current environment is not collateral
# --------------------------------------------------------------------------


def test_a_failed_build_leaves_the_current_environment_untouched(tmp_path):
    """The property the whole design exists for. An in-place `--update` that
    dies half way leaves an environment that is neither the old one nor the new
    one, and nothing that can say which."""
    calls = {"n": 0}

    def flaky(argv, cwd):
        calls["n"] += 1
        if calls["n"] == 1:
            prefix = Path(argv[-1])
            (prefix / "bin").mkdir(parents=True, exist_ok=True)
            (prefix / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
            return _completed()
        return _completed(1, b"Solving environment: failed\nPackagesNotFoundError")

    store = eg.EnvironmentStore(tmp_path / "environments", runner=flaky)
    spec = _spec(tmp_path)
    good = _apply(store, spec).generation

    spec.write_text("numpy\nimpossible-package\n", encoding="utf-8")
    failed = _apply(store, spec)

    assert failed.ok is False
    assert failed.generation is None
    assert "exited 1" in failed.detail
    assert "PackagesNotFoundError" in failed.stderr_tail
    assert store.current_id("python") == good.id, "the pointer never moved"
    assert Path(good.prefix, "bin", "python").is_file()


def test_a_failed_build_never_becomes_a_generation(tmp_path):
    store = eg.EnvironmentStore(
        tmp_path / "environments", runner=lambda a, c: _completed(1, b"nope")
    )
    _apply(store, _spec(tmp_path))

    assert store.list("python") == [], "a failed build is not a generation"
    # The evidence lives at the generation's final location now (conda bakes in
    # its prefix, so builds can no longer be relocated), and it is visibly not
    # a generation: a `building.json` marked FAILED and no `manifest.json`.
    gens = tmp_path / "environments" / "python" / "generations"
    failed = [
        p for p in gens.iterdir() if p.is_dir() and not (p / "manifest.json").is_file()
    ]
    assert failed, "the evidence is kept, under a directory that is not a generation"
    record = json.loads((failed[0] / "building.json").read_text())
    assert record["state"] == eg.FAILED


def test_a_verify_that_refuses_fails_the_apply(tmp_path):
    """A build that exits 0 having produced nothing usable is the false success
    this step exists to catch."""

    def empty_build(argv, cwd):
        Path(argv[-1]).mkdir(parents=True, exist_ok=True)
        return _completed()

    store = eg.EnvironmentStore(tmp_path / "environments", runner=empty_build)
    spec = _spec(tmp_path)
    plan = store.plan("python", spec, tool="fake-conda")
    result = store.apply(plan, spec, tool="fake-conda", build=_build, verify=_verify)

    assert result.ok is False
    assert store.current_id("python") is None


def test_a_build_that_raises_before_running_still_reports(tmp_path):
    """The failure handler must never be the thing that fails: a build() that
    raises before the runner ran leaves no completed process to read stderr
    from, and reaching for one would be an UnboundLocalError instead of a
    finding."""
    store = eg.EnvironmentStore(
        tmp_path / "environments", runner=lambda a, c: _completed()
    )
    spec = _spec(tmp_path)
    plan = store.plan("python", spec, tool="fake-conda")

    def exploding_build(_prefix, _staged_spec):
        raise RuntimeError("the tool is not installed")

    result = store.apply(
        plan, spec, tool="fake-conda", build=exploding_build, verify=_verify
    )
    assert result.ok is False
    assert "the tool is not installed" in result.detail
    assert result.stderr_tail == ""


# --------------------------------------------------------------------------
# concurrency
# --------------------------------------------------------------------------


def test_two_concurrent_applies_cannot_both_land(tmp_path):
    """`O_CREAT | O_EXCL` is the atomic primitive: two processes racing to
    create the same lock path cannot both win. Without it, both builds would
    stage and the second pointer write would silently discard the first."""
    entered = threading.Barrier(2, timeout=10)

    def slow(argv, cwd):
        prefix = Path(argv[-1])
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        (prefix / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        try:
            entered.wait()
        except threading.BrokenBarrierError:
            pass
        return _completed()

    store = eg.EnvironmentStore(tmp_path / "environments", runner=slow)
    spec = _spec(tmp_path)
    plan = store.plan("python", spec, tool="fake-conda")
    outcomes: list[object] = []
    lock = threading.Lock()

    def run():
        try:
            result = store.apply(
                plan, spec, tool="fake-conda", build=_build, verify=_verify
            )
        except eg.ConcurrentApply as e:
            result = e
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    entered.abort()
    for thread in threads:
        thread.join(timeout=15)

    landed = [o for o in outcomes if isinstance(o, eg.ApplyResult) and o.ok]
    refused = [o for o in outcomes if isinstance(o, eg.ConcurrentApply)]
    assert len(landed) == 1, f"exactly one apply may land, got {outcomes}"
    assert len(refused) == 1
    assert store.current_id("python") == landed[0].generation.id


def test_an_apply_planned_against_a_stale_current_is_refused(store, tmp_path):
    """Two operators, one environment: the second's plan described a world that
    no longer exists, and building on it would silently discard the first."""
    spec = _spec(tmp_path)
    stale_plan = store.plan("python", spec, tool="fake-conda")
    _apply(store, spec)  # someone else got there first

    with pytest.raises(eg.ConcurrentApply, match="while this apply was planning"):
        store.apply(stale_plan, spec, tool="fake-conda", build=_build, verify=_verify)


def test_a_stale_lock_is_broken_rather_than_inherited(store, tmp_path, monkeypatch):
    """A lock nobody holds is a permanent outage."""
    env_dir = tmp_path / "environments" / "python"
    env_dir.mkdir(parents=True)
    lock = env_dir / "apply.lock"
    lock.write_text("99999", encoding="utf-8")
    old = os.stat(lock).st_mtime - eg.LOCK_STALE_S - 60
    os.utime(lock, (old, old))

    assert store.recover("python")["stale_lock"] is True
    assert _apply(store, _spec(tmp_path)).ok


# --------------------------------------------------------------------------
# rollback
# --------------------------------------------------------------------------


def test_rollback_moves_the_pointer_and_rebuilds_nothing(store, tmp_path):
    builds: list[str] = []
    spec = _spec(tmp_path)
    first = _apply(store, spec).generation
    spec.write_text("numpy\npandas\n", encoding="utf-8")
    second = _apply(store, spec).generation
    assert store.current_id("python") == second.id

    def counting_runner(argv, cwd):
        builds.append(str(argv))
        return _completed()

    store._runner = counting_runner
    result = store.rollback("python", first.id)

    assert result.ok and store.current_id("python") == first.id
    assert builds == [], "the environment that worked is still on disk"
    assert Path(first.prefix, "bin", "python").is_file()


def test_rollback_refuses_a_generation_that_never_finished(store, tmp_path):
    _apply(store, _spec(tmp_path))
    with pytest.raises(eg.EnvironmentError_, match="no generation"):
        store.rollback("python", "env-does-not-exist")


def test_rollback_refuses_when_the_prefix_is_gone(store, tmp_path):
    """A pointer to a directory somebody deleted is not a restored
    environment."""
    spec = _spec(tmp_path)
    first = _apply(store, spec).generation
    spec.write_text("numpy\npandas\n", encoding="utf-8")
    _apply(store, spec)

    import shutil

    shutil.rmtree(first.prefix)
    with pytest.raises(eg.EnvironmentError_, match="no longer has its prefix"):
        store.rollback("python", first.id)


def test_rolling_forward_again_works(store, tmp_path):
    spec = _spec(tmp_path)
    first = _apply(store, spec).generation
    spec.write_text("numpy\npandas\n", encoding="utf-8")
    second = _apply(store, spec).generation

    store.rollback("python", first.id)
    store.rollback("python", second.id)
    assert store.current_id("python") == second.id


# --------------------------------------------------------------------------
# immutability
# --------------------------------------------------------------------------


def test_an_applied_generation_may_not_be_modified(store, tmp_path):
    generation = _apply(store, _spec(tmp_path)).generation
    with pytest.raises(eg.ImmutableGeneration, match="may not be modified"):
        store.assert_mutable("python", generation.id)


def test_superseding_a_generation_does_not_rewrite_its_manifest(store, tmp_path):
    spec = _spec(tmp_path)
    first = _apply(store, spec).generation
    manifest = (
        tmp_path
        / "environments"
        / "python"
        / "generations"
        / first.id
        / "manifest.json"
    )
    before = manifest.read_bytes()

    spec.write_text("numpy\npandas\n", encoding="utf-8")
    _apply(store, spec)

    assert manifest.read_bytes() == before, "an applied manifest is never rewritten"
    assert (
        manifest.parent / "superseded_at"
    ).is_file(), "the fact is recorded beside it, not inside it"


def test_discard_refuses_to_touch_a_generation(store, tmp_path):
    generation = _apply(store, _spec(tmp_path)).generation
    path = tmp_path / "environments" / "python" / "generations" / generation.id
    with pytest.raises(eg.ImmutableGeneration):
        store.discard("python", str(path))
    assert path.is_dir()


def test_discard_refuses_a_path_outside_the_environment(store, tmp_path):
    _apply(store, _spec(tmp_path))
    with pytest.raises(eg.EnvironmentError_, match="is not inside"):
        store.discard("python", str(tmp_path / "elsewhere"))


# --------------------------------------------------------------------------
# restart recovery
# --------------------------------------------------------------------------


def test_a_restart_finds_the_current_generation_intact(store, tmp_path):
    generation = _apply(store, _spec(tmp_path)).generation
    restarted = eg.EnvironmentStore(tmp_path / "environments")

    assert restarted.current_id("python") == generation.id
    assert restarted.current("python").state == eg.READY


def test_a_restart_reports_a_build_that_died_mid_flight(store, tmp_path):
    """A crash between staging and the rename leaves a directory that is
    visibly not a generation — which is the point of renaming last."""
    _apply(store, _spec(tmp_path))
    env_dir = tmp_path / "environments" / "python"
    orphan = env_dir / ".staging-env-crashed"
    orphan.mkdir()
    (orphan / "manifest.json").write_text(
        json.dumps({"generation_id": "env-crashed", "state": eg.STAGING}),
        encoding="utf-8",
    )

    report = eg.EnvironmentStore(tmp_path / "environments").recover("python")
    assert report["current"] == store.current_id("python")
    assert [item["generation_id"] for item in report["abandoned"]] == ["env-crashed"]


def test_an_abandoned_build_can_be_discarded(store, tmp_path):
    _apply(store, _spec(tmp_path))
    env_dir = tmp_path / "environments" / "python"
    orphan = env_dir / ".staging-env-crashed"
    orphan.mkdir()

    assert store.discard("python", str(orphan)) is True
    assert store.recover("python")["abandoned"] == []


def test_the_history_records_what_happened(store, tmp_path):
    spec = _spec(tmp_path)
    first = _apply(store, spec).generation
    spec.write_text("numpy\npandas\n", encoding="utf-8")
    _apply(store, spec)
    store.rollback("python", first.id)

    kinds = [entry["kind"] for entry in store.history("python")]
    assert kinds == ["applied", "applied", "rolled_back"]


# --------------------------------------------------------------------------
# the CLI is the main path, so it is what gets driven
# --------------------------------------------------------------------------


def _cli_module():
    """`openai4s.cli.main` as a dotted string resolves to the *function*
    re-exported by the package, so the module object is fetched explicitly."""
    import importlib

    return importlib.import_module("openai4s.cli.main")


def _cli(argv, capsys):
    code = _cli_module().main(argv)
    return code, capsys.readouterr()


def test_the_cli_plans_without_creating_anything(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    code, out = _cli(["env", "plan", "python", "--json"], capsys)

    assert code == 0
    payload = json.loads(out.out)
    assert payload[0]["action"] == eg.CREATE
    assert not (tmp_path / "data" / "environments" / "python").exists()


def test_the_cli_apply_dry_run_changes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(_cli_module(), "_find_conda_tool", lambda: "fake-conda")
    code, out = _cli(["env", "apply", "python", "--dry-run"], capsys)

    assert code == 0 and "would create" in out.out
    assert not (tmp_path / "data" / "environments" / "python" / "current").exists()


def test_the_cli_reports_a_failed_apply_without_moving_the_pointer(
    tmp_path, monkeypatch, capsys
):
    """The message a user actually needs when a build fails: what broke, and
    that their working environment is still their working environment."""
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(_cli_module(), "_find_conda_tool", lambda: "fake-conda")
    monkeypatch.setattr(
        "openai4s.kernel.env_generations._default_runner",
        lambda argv, cwd: _completed(1, b"Solving environment: failed"),
    )
    code, out = _cli(["env", "apply", "python"], capsys)

    assert code == 1
    assert "FAILED" in out.err
    assert "current environment is unchanged" in out.err


def test_the_cli_lists_generations_and_the_current_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(tmp_path / "data"))

    def runner(argv, cwd):
        prefix = Path(argv[argv.index("--prefix") + 1])
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        (prefix / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        return _completed()

    monkeypatch.setattr(_cli_module(), "_find_conda_tool", lambda: "fake-conda")
    monkeypatch.setattr("openai4s.kernel.env_generations._default_runner", runner)
    monkeypatch.setattr(_cli_module(), "_env_verify", lambda prefix: (str(prefix), []))
    assert _cli(["env", "apply", "python"], capsys)[0] == 0

    code, out = _cli(["env", "list", "python", "--json"], capsys)
    payload = json.loads(out.out)["python"]
    assert payload["current"]
    assert payload["generations"][0]["generation_id"] == payload["current"]
