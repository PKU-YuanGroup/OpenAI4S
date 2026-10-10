"""Seams between the three Lab packages that were written in parallel.

The models and ports, the durable ledger and the provider process were built
side by side against one written contract, so none of them could test the
others. These cases pin the places where one package's output is another's
input. A later change on either side then fails here, not inside the manager
that composes all three.
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

from openai4s.lab.fake import FakeExtractorDevice
from openai4s.lab.manifest import load_descriptor, match_command
from openai4s.lab.models import (
    CommandRequest,
    Dispatch,
    Quantity,
    Receipt,
    SessionOpened,
    SessionOpenRequest,
    capability_revision,
)
from openai4s.lab.ports import LabLedgerPort
from openai4s.storage.lab import LabLedger
from openai4s_lab_provider.client import ProviderClient

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "openai4s_lab_provider" / "chemgymrl" / "manifests"
ENTRYPOINT = ROOT / "openai4s_lab_provider" / "__main__.py"
TOY = "toy-extract-v0"


def _port_methods(protocol: type) -> list[str]:
    return sorted(
        name
        for name, value in vars(protocol).items()
        if callable(value) and not name.startswith("_")
    )


def test_the_ledger_matches_every_ledger_port_signature():
    names = _port_methods(LabLedgerPort)
    assert len(names) == 21
    for name in names:
        expected = inspect.signature(getattr(LabLedgerPort, name)).parameters
        actual = inspect.signature(getattr(LabLedger, name)).parameters
        # Names, kinds and defaults; the ledger itself is not annotated.
        assert [(p.name, p.kind, p.default) for p in actual.values()] == [
            (p.name, p.kind, p.default) for p in expected.values()
        ], name


def test_both_chemgymrl_profiles_ship_a_manifest():
    assert {path.stem for path in MANIFESTS.glob("*.json")} == {
        "WaterOilExtract-v0",
        "GenWurtzExtract-v2",
    }


@pytest.mark.parametrize(
    "path", sorted(MANIFESTS.glob("*.json")), ids=lambda path: path.stem
)
def test_every_committed_manifest_loads_through_the_shared_validator(path):
    text = path.read_text("utf-8")
    # load_descriptor recomputes capability_revision and refuses a mismatch.
    descriptor = load_descriptor(json.loads(text))
    assert descriptor.device_id == "chemgym.extractor.01"
    assert descriptor.profile == path.stem
    assert descriptor.capability_revision == capability_revision(
        descriptor.capabilities
    )
    for internal in ("gym_action", "provider_action", "ground_truth", "reward"):
        assert internal not in text


@pytest.fixture
def toy(tmp_path):
    client = ProviderClient(
        [sys.executable, "-I", str(ENTRYPOINT), "--backend", "toy"],
        env={"HOME": str(tmp_path), "PYTHONNOUSERSITE": "1"},
        cwd=tmp_path,
    ).start()
    yield client
    client.close()


def _request(client, op, args):
    return client.request(op, args, timeout=10)


def _transfer(descriptor, command_id):
    capability = next(
        cap for cap in descriptor.capabilities if cap.operation == "transfer_liquid"
    )
    request = CommandRequest(
        "labrun-integration",
        capability.operation,
        capability.source,
        capability.target,
        {
            name: Quantity(spec.allowed[0], spec.unit)
            for name, spec in capability.parameters.items()
        },
        0,
        command_id,
    )
    return capability, match_command(descriptor, request)


def _receipt(raw):
    # provider_action is stored by the ledger and never projected; the shared
    # Receipt model deliberately has no field for it.
    payload = dict(raw)
    payload.pop("provider_action", None)
    return Receipt.from_dict(payload)


def test_the_toy_provider_payloads_decode_into_the_shared_models(toy):
    descriptor = load_descriptor(_request(toy, "describe", {"profile": TOY}))
    opened = SessionOpened.from_dict(
        _request(
            toy,
            "open",
            {
                "profile": TOY,
                "seed": 7,
                "options": {},
                "expected_capability_revision": descriptor.capability_revision,
            },
        )
    )
    assert opened.descriptor == descriptor
    capability, command = _transfer(descriptor, "first")
    tokens = {resource: 1 for resource in capability.resources}
    args = {
        "provider_command_id": "first",
        "command": command.to_dict(),
        "fencing_tokens": tokens,
    }
    first = _receipt(_request(toy, "execute", args))
    assert first.applied and first.status.value == "succeeded"
    # The same id again is the same receipt, not a second step.
    assert _receipt(_request(toy, "execute", args)) == first
    assert _receipt(_request(toy, "query", {"provider_command_id": "first"})) == first
    stale = _receipt(
        _request(
            toy,
            "execute",
            {
                "provider_command_id": "stale",
                "command": command.to_dict(),
                "fencing_tokens": {resource: 0 for resource in capability.resources},
            },
        )
    )
    assert not stale.applied and stale.error["code"] == "resource_busy"
    assert stale.step_index == first.step_index


def test_the_fake_device_and_the_toy_provider_refuse_a_stale_fence_alike(toy):
    device = FakeExtractorDevice()
    fake_descriptor = device.describe(TOY)
    opened = device.open(
        SessionOpenRequest(TOY, 7, {}, fake_descriptor.capability_revision)
    )
    capability, command = _transfer(opened.descriptor, "fake-first")
    device.execute(
        opened.session_id,
        Dispatch("fake-first", command, {r: 5 for r in capability.resources}),
    )
    fake_stale = device.execute(
        opened.session_id,
        Dispatch("fake-stale", command, {r: 4 for r in capability.resources}),
    )

    descriptor = load_descriptor(_request(toy, "describe", {"profile": TOY}))
    _request(
        toy,
        "open",
        {
            "profile": TOY,
            "seed": 7,
            "options": {},
            "expected_capability_revision": descriptor.capability_revision,
        },
    )
    toy_capability, toy_command = _transfer(descriptor, "toy-first")
    _request(
        toy,
        "execute",
        {
            "provider_command_id": "toy-first",
            "command": toy_command.to_dict(),
            "fencing_tokens": {r: 5 for r in toy_capability.resources},
        },
    )
    toy_stale = _receipt(
        _request(
            toy,
            "execute",
            {
                "provider_command_id": "toy-stale",
                "command": toy_command.to_dict(),
                "fencing_tokens": {r: 4 for r in toy_capability.resources},
            },
        )
    )
    for stale in (fake_stale, toy_stale):
        assert not stale.applied
        assert stale.status.value == "failed"
        assert stale.error["code"] == "resource_busy"
