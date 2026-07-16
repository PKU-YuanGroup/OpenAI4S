"""`openai4s serve` must not modify the Python environment.

Startup used to call ``preinstall.ensure_core(background=True)``, which took
~23 *unpinned* package names, resolved them against PyPI at boot, and installed
them with ``--break-system-packages`` into whatever interpreter the daemon ran
under — on a daemon thread whose failures nobody saw. Three consequences:
launching the daemon mutated the user's environment, two cold starts a week
apart could produce different environments, and an offline start failed
invisibly.

Diagnosis and mutation are now separate. ``core_plan()`` reports; only an
explicit user action (``openai4s setup``, ``POST /api/kernel/install``,
``host.pip_install``) applies. These tests pin that split — the assertion that
matters is that *no pip subprocess is spawned* during a plan, not merely that
the return value looks right.
"""
import subprocess

import pytest

from openai4s.kernel import preinstall


@pytest.fixture
def no_pip_allowed(monkeypatch):
    """Any pip invocation during the guarded block is a test failure.

    Asserted at the subprocess boundary rather than by inspecting a return
    value: the old bug ran pip on a background thread, so a function could
    return "nothing to do" while an install was already under way.
    """
    calls = []

    def forbidden(cmd, *a, **k):
        calls.append(cmd)
        raise AssertionError(f"startup must not run a subprocess: {cmd}")

    monkeypatch.setattr(subprocess, "run", forbidden, raising=True)
    return calls


def test_core_plan_never_installs(no_pip_allowed, monkeypatch):
    """Even with the whole stack missing, planning stays read-only."""
    monkeypatch.setattr(
        preinstall, "missing_core", lambda: [("numpy", "numpy"), ("scipy", "scipy")]
    )
    plan = preinstall.core_plan()
    assert plan["missing"] == ["numpy", "scipy"]
    assert plan["satisfied"] is False
    assert no_pip_allowed == []


def test_core_plan_reports_satisfied_when_nothing_missing(no_pip_allowed, monkeypatch):
    monkeypatch.setattr(preinstall, "missing_core", lambda: [])
    plan = preinstall.core_plan()
    assert plan["satisfied"] is True
    assert plan["missing"] == []


def test_plan_sets_needs_provision_rather_than_pretending_ready(monkeypatch):
    """The resting state of a cold install is 'needs_provision', not 'ready'.
    Claiming ready with packages missing would put the surprise at task time."""
    monkeypatch.setattr(preinstall, "missing_core", lambda: [("numpy", "numpy")])
    preinstall.STATUS.update(phase="idle", missing=[])
    preinstall.core_plan()
    assert preinstall.STATUS["phase"] == "needs_provision"
    assert preinstall.STATUS["missing"] == ["numpy"]
    assert "openai4s setup" in preinstall.STATUS["message"]


def test_plan_reports_ready_when_satisfied(monkeypatch):
    monkeypatch.setattr(preinstall, "missing_core", lambda: [])
    preinstall.STATUS.update(phase="idle", missing=["stale"])
    preinstall.core_plan()
    assert preinstall.STATUS["phase"] == "ready"
    assert preinstall.STATUS["missing"] == []


def test_plan_does_not_stomp_an_install_in_flight(monkeypatch):
    """A plan refreshed while an explicit install runs must not relabel it."""
    monkeypatch.setattr(preinstall, "missing_core", lambda: [("numpy", "numpy")])
    preinstall.STATUS.update(phase="installing", installing=["numpy"])
    preinstall.core_plan()
    assert preinstall.STATUS["phase"] == "installing"


def test_the_gateway_startup_path_plans_and_never_applies(monkeypatch, tmp_path):
    """The regression that matters: build_app_server must call the read-only
    plan. Guarded at ensure_core itself so the test fails loudly if anyone
    reintroduces the implicit install, whatever thread it runs on."""
    from openai4s.config import Config
    from openai4s.server import gateway

    def forbidden(*a, **k):
        raise AssertionError("build_app_server must not call ensure_core")

    monkeypatch.setattr(preinstall, "ensure_core", forbidden, raising=True)
    planned = []
    monkeypatch.setattr(
        preinstall,
        "core_plan",
        lambda: (planned.append(True) or {"missing": [], "satisfied": True}),
        raising=True,
    )
    # Demo seeding runs on a daemon thread that outlives the test and then
    # writes to the store the fixture already closed. Unrelated to what is
    # under test here, and its traceback would otherwise land in this file's
    # output and read like a failure.
    monkeypatch.setattr(gateway, "_seed_demo_session", lambda *a, **k: None)

    httpd = gateway.build_app_server(Config(data_dir=tmp_path, port=0))
    try:
        assert planned == [True], "startup must plan the environment"
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------
# the explicit path must still work
# --------------------------------------------------------------------------


def test_ensure_core_still_installs_when_explicitly_asked(monkeypatch):
    """Removing the implicit install must not break the deliberate one."""
    monkeypatch.setattr(preinstall, "missing_core", lambda: [("numpy", "numpy")])
    seen = {}

    def fake_pip(names, **kw):
        seen["names"] = names
        return True, "ok"

    monkeypatch.setattr(preinstall, "_pip_install", fake_pip, raising=True)
    result = preinstall.ensure_core(background=False)
    assert result["ok"] is True
    assert seen["names"] == ["numpy"]


def test_install_endpoint_path_still_works(monkeypatch):
    def fake_pip(names, **kw):
        return True, "installed"

    monkeypatch.setattr(preinstall, "_pip_install", fake_pip, raising=True)
    out = preinstall.install(["scanpy"])
    assert out["ok"] is True
    assert out["installed"] == ["scanpy"]
