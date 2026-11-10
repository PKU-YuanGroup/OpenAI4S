"""Offline tests of the optional OpenMHS simulated lab (openai4s/mhs_lab).

The lab needs the OpenMHS SDK (``mhs``); without it these tests skip, the same
way the core runs without it.  No endpoint process, network or LLM is used: the
instruments are driven in-process through ``mhs.driver.build_server``.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import pytest

import openai4s  # noqa: F401,E402  - finds OpenMHS (third_party/OpenMHS or $OPENAI4S_OPENMHS) first

if os.environ.get("OPENAI4S_OPENMHS", "").strip().lower() in {
    "off",
    "0",
    "none",
    "false",
}:
    pytest.skip("OpenMHS discovery is disabled", allow_module_level=True)

mhs = pytest.importorskip("mhs")

from mhs.driver import build_server, close_server  # noqa: E402
from mhs.testing import client_for  # noqa: E402

from openai4s.mhs_lab import INSTRUMENTS_DIR  # noqa: E402


@pytest.fixture()
def lab_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI4S_MHS_LAB_DIR", str(tmp_path / "lab"))
    return tmp_path / "lab"


def _load(name: str):
    from mhs.adapter import load_driver

    return load_driver(INSTRUMENTS_DIR / name, backend="sim")


def _session(server):
    client = client_for(server)
    device_id = client.hello()["devices"][0]["device_id"]
    session = client.open_session(device_ids=[device_id])["session_id"]

    def call(cap, payload):
        return client.invoke(cap, payload, device_id=device_id, session_id=session)

    return client, device_id, session, call


def test_assay_recovers_cheng_prusoff_and_enforces_limits(lab_dir):
    server = build_server(_load("assay_station"))
    try:
        _client, _dev, _ses, call = _session(server)
        ok = call(
            "x-cap.openai4s-lab.prepare_dose_series.v1",
            {
                "row": "A",
                "compound_id": "CPD-102",
                "top_concentration_uM": 100,
                "dilution_factor": 3,
                "points": 10,
            },
        )
        assert ok["status"] == "completed"
        hot = call(
            "x-cap.openai4s-lab.start_reaction.v1",
            {"rows": ["A"], "substrate_uM": 50, "temperature_C": 55},
        )
        assert hot["error"]["mhs_code"] == "MHS.SAFETY.ENVELOPE_VIOLATION"
        assert (
            call(
                "x-cap.openai4s-lab.start_reaction.v1",
                {"rows": ["A"], "substrate_uM": 50, "temperature_C": 30},
            )["status"]
            == "completed"
        )
        read = call(
            "x-cap.openai4s-lab.read_kinetics.v1",
            {"rows": ["A"], "wavelength_nm": 405, "duration_s": 600, "interval_s": 30},
        )
        row = read["output"]["rows"]["A"]
        span = row["vehicle_rate_mOD_per_min"] - row["blank_rate_mOD_per_min"]
        act = [
            100 * (v - row["blank_rate_mOD_per_min"]) / span
            for v in row["rate_mOD_per_min"]
        ]
        # IC50 = Ki (1 + S/Km) = 0.62 * 2 = 1.24 uM: activity crosses 50 % between the bracketing doses.
        pairs = list(zip(row["concentrations_uM"], act))
        above = [c for c, a in pairs if a > 50]
        below = [c for c, a in pairs if a < 50]
        assert min(below) > 0.3 and max(above) < 5.0
        assert "Ki" not in json.dumps(read)  # the hidden truth is never exposed
    finally:
        close_server(server)


def test_assay_refuses_a_vial_the_arm_never_delivered(lab_dir):
    from openai4s.mhs_lab import lims

    vial = lims.register_vial(
        {
            "compound_id": "FR-2",
            "concentration_mM": 300.0,
            "volume_mL": 10.0,
            "purity": 0.9,
            "location": "reactor_outlet",
        }
    )
    server = build_server(_load("assay_station"))
    try:
        _client, _dev, _ses, call = _session(server)
        refused = call(
            "x-cap.openai4s-lab.load_sample.v1", {"vial_id": vial["vial_id"]}
        )
        assert refused["status"] == "failed"
        assert "reactor outlet" in refused["error"]["message"]
    finally:
        close_server(server)


def test_reactor_has_an_interior_time_temperature_ridge():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "fr_sim", INSTRUMENTS_DIR / "flow_reactor" / "sim.py"
    )
    sim = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(sim)
    y = lambda t, tau: 100 * sim.integrate(t, tau, 2.0, 0.5)["P"] / 0.5  # noqa: E731
    assert y(90, 2) > y(60, 2) and y(90, 2) > y(140, 2)  # interior in temperature
    assert y(90, 2) > y(90, 0.5) and y(90, 2) > y(90, 20)  # interior in residence time
    assert all(math.isfinite(y(150, tau)) for tau in (0.5, 5, 20))  # stable when stiff


def test_activity_reads_persisted_chains_and_verifies(lab_dir, monkeypatch):
    from openai4s.mhs_lab import activity, supervisor

    state = Path(lab_dir) / "state" / "reactor"
    state.mkdir(parents=True)
    driver = _load("flow_reactor")
    server = (
        build_server(driver, state_dir=str(state))
        if "state_dir" in build_server.__code__.co_varnames
        else None
    )
    if server is None:
        pytest.skip("this SDK's build_server has no state_dir")
    try:
        _client, _dev, _ses, call = _session(server)
        call(
            "x-cap.openai4s-lab.run_condition.v1",
            {
                "temperature_C": 90,
                "residence_time_min": 2,
                "equiv_b": 2.0,
                "concentration_M": 0.5,
                "reagent_b": "B1",
            },
        )
        call(
            "x-cap.openai4s-lab.run_condition.v1",
            {
                "temperature_C": 180,
                "residence_time_min": 2,
                "equiv_b": 2.0,
                "concentration_M": 0.5,
                "reagent_b": "B1",
            },
        )
    finally:
        close_server(server)
    items = [i for i in activity.activity()["items"] if i["endpoint"] == "reactor"]
    assert [i["status"] for i in items] == ["completed", "rejected"]
    assert items[1]["code"] == "MHS.SAFETY.ENVELOPE_VIOLATION"
    assert activity.results()["reactor_runs"][0]["reagent_b"] == "B1"
    assert activity.verify()["chains"]["reactor"]["valid"] is True
    assert supervisor.lab_document()["endpoints"][1]["access"] == "native-http"


# -- market devices (need an OpenMHS checkout with MHS/drivers/market.json) ------------------------


def _market_or_skip():
    from openai4s.mhs_lab import supervisor

    try:
        market = supervisor._market()
        market.load()
    except Exception as exc:  # noqa: BLE001 - no checkout: the core runs without it
        pytest.skip(f"no OpenMHS market available: {exc}")
    return market


def test_add_and_remove_market_devices_on_both_surfaces(lab_dir, monkeypatch):
    from openai4s.mhs_lab import supervisor

    _market_or_skip()
    monkeypatch.setenv("OPENAI4S_MHS_DEVICE_PORT", "7790")
    monkeypatch.setattr(supervisor, "FIRST_DEVICE_PORT", 7790)
    mcp = supervisor.add_device("julabo", via="mcp", start_now=False)
    native = supervisor.add_device("gym-pusht", via="mhs", start_now=False)
    assert mcp["added"] and mcp["device"]["id"] == "julabo"
    assert native["device"]["port"] >= 7790
    doc = supervisor.lab_document()
    groups = {ep["id"]: ep["group"] for ep in doc["endpoints"]}
    assert groups == {
        "assay": "mcp",
        "reactor": "mhs",
        "xarm": "mcp",
        "julabo": "mcp",
        "gym-pusht": "mhs",
    }
    connectors = supervisor.connector_settings()["endpoints"]
    assert [e["id"] for e in connectors][-1] == "julabo"
    assert (
        "--stdio" in connectors[-1]["command"]
        and "--backend" in connectors[-1]["command"]
    )
    # julabo's setpoint range is operator-stated: the fake backend gets the package's example overlay.
    command = connectors[-1]["command"]
    limits = command[command.index("--site-limits") + 1]
    assert limits.endswith("labs/tiagocoutinho__julabo/site-limits.example.json")
    native_ids = [e["id"] for e in supervisor.native_settings()["endpoints"]]
    assert native_ids == ["reactor", "gym-pusht"]
    # Installing the same package on the other surface moves it; installing it again is a no-op.
    again = supervisor.add_device(
        "labs/tiagocoutinho__julabo", via="mcp", start_now=False
    )
    assert again["added"] is False
    supervisor.remove_device("gym-pusht")
    assert [d["id"] for d in supervisor.installed_devices()] == ["julabo"]
    with pytest.raises(ValueError):
        supervisor.remove_device("assay")
    with pytest.raises(LookupError):
        supervisor.add_device("no-such-device-anywhere", start_now=False)


def test_bootstrap_finds_an_explicit_checkout(monkeypatch, tmp_path):
    from openai4s.mhs_lab import openmhs

    fake = tmp_path / "OpenMHS"
    (fake / "MHS" / "sdk" / "src" / "mhs").mkdir(parents=True)
    (fake / "MHS" / "sdk" / "src" / "mhs" / "__init__.py").write_text("")
    (fake / "integration" / "openai4s" / "openai4s_mhs").mkdir(parents=True)
    (fake / "integration" / "openai4s" / "openai4s_mhs" / "__init__.py").write_text("")
    monkeypatch.setenv("OPENAI4S_OPENMHS", str(fake))
    assert openmhs.find_checkout() == ("OPENAI4S_OPENMHS", fake.resolve())
    monkeypatch.setenv("OPENAI4S_OPENMHS", str(tmp_path / "missing"))
    located = openmhs.find_checkout()
    assert located is None or located[0] != "OPENAI4S_OPENMHS"


def test_bootstrap_selects_the_lab_settings_only_without_an_operator_document(
    monkeypatch, tmp_path
):
    from openai4s.mhs_lab import openmhs

    data = tmp_path / "data"
    (data / "mhs-lab").mkdir(parents=True)
    monkeypatch.setenv("OPENAI4S_DATA_DIR", str(data))
    monkeypatch.delenv("OPENAI4S_MHS_LAB_DIR", raising=False)
    monkeypatch.delenv("MHS_CONFIG", raising=False)
    openmhs.bootstrap()
    assert "MHS_CONFIG" not in os.environ  # no lab settings yet
    (data / "mhs-lab" / "mhs.json").write_text("{}")
    openmhs.bootstrap()
    assert os.environ["MHS_CONFIG"] == str(data / "mhs-lab" / "mhs.json")
    monkeypatch.delenv("MHS_CONFIG")
    (data / "mhs.json").write_text("{}")  # the operator's own document wins
    openmhs.bootstrap()
    assert "MHS_CONFIG" not in os.environ


def test_lab_cli_lists_and_explains(lab_dir, capsys):
    from openai4s.mhs_lab import cli

    assert cli.main(["list"]) == 0
    assert "openai4s lab add" in capsys.readouterr().out
    _market_or_skip()
    assert cli.main(["show", "julabo"]) == 0
    out = capsys.readouterr().out
    assert "openai4s lab add labs/tiagocoutinho__julabo" in out
