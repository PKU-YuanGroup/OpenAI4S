"""What the Web Skills catalogue tells a user about whether a Skill can run.

The loader has parsed `requirements:` and computed ready/needs_setup/unknown
for a while, and the *host* surface reads it. The Web projection in
`server/skills.py` dropped both fields, so the route never carried them and the
Customize list could not render them: a GPU-only Skill (sixteen of the bundled
ones declare `requirements: [gpu]`) looked exactly like one that runs anywhere,
and the user found out mid-task on a machine with no GPU.

The second contract here is the one that makes the first one safe. Browsing a
catalogue is not a request to contact anything or to start anything: readiness
is answered from local state alone -- `nvidia-smi` is looked for on PATH and
never executed -- so rendering thirty-four rows must not cost a subprocess or a
socket. Asserted by making both an error while the real route runs.
"""

from __future__ import annotations

import os
import subprocess
import urllib.request
from types import SimpleNamespace

import pytest

from openai4s.config import Config
from openai4s.server import gateway as gateway_mod
from openai4s.server.skills import SkillCustomizationService
from openai4s.skills_loader import SkillLoader
from openai4s.skills_loader.loader import NEEDS_SETUP, READY, UNKNOWN


def _write_skill(root, name: str, frontmatter: str) -> None:
    directory = root / name
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill\n"
        f"origin: openai4s\n{frontmatter}---\n\n# {name}\n",
        "utf-8",
    )


@pytest.fixture()
def bundled(tmp_path):
    """Three bundled Skills: one needing hardware, one needing something no
    check exists for, one needing nothing."""
    skills_dir = tmp_path / "bundled-skills"
    skills_dir.mkdir()
    _write_skill(skills_dir, "gpu-skill", "requirements: [gpu]\n")
    _write_skill(skills_dir, "odd-skill", "requirements: [quantum-annealer]\n")
    _write_skill(skills_dir, "plain-skill", "")
    return Config(data_dir=tmp_path / "data", skills_dir=skills_dir)


def _rows(payload):
    return {item["name"]: item for item in payload["skills"]}


def _call(handler, method, path, body=None):
    replies = []
    handler._query = lambda: {}
    handler._body = lambda: body or {}
    handler._json = lambda value, code=200: replies.append((code, value))
    handler._api(method, path)
    assert replies, f"{method} {path} answered nothing"
    return replies[-1]


def _handler(config):
    handler_class = gateway_mod.make_handler(
        config,
        gateway_mod.WSHub(),
        SimpleNamespace(),
    )
    return object.__new__(handler_class)


def test_the_catalogue_route_carries_requirements_and_readiness(bundled, monkeypatch):
    """Without this the UI cannot render needs_setup or unknown at all.

    The GPU check is pinned to "absent" rather than left to the host: this
    asserts the projection, and a CI runner with a GPU must not turn that into
    a different answer.
    """
    from openai4s.skills_loader import loader as loader_mod

    monkeypatch.setitem(loader_mod._REQUIREMENT_CHECKS, "gpu", lambda: False)

    code, payload = _call(_handler(bundled), "GET", "/skills/catalog")
    assert code == 200
    rows = _rows(payload)

    gpu = rows["gpu-skill"]
    assert gpu["requirements"] == ["gpu"]
    assert gpu["readiness"]["state"] == NEEDS_SETUP
    assert gpu["readiness"]["missing"] == ["gpu"]

    # `unknown` is a third answer and must not collapse into either of the
    # other two: guessing `ready` invites a failure deep into a task, guessing
    # `needs_setup` sends the user to install something they may already have.
    odd = rows["odd-skill"]
    assert odd["readiness"]["state"] == UNKNOWN
    assert odd["readiness"]["unverifiable"] == ["quantum-annealer"]
    assert odd["readiness"]["missing"] == []

    plain = rows["plain-skill"]
    assert plain["requirements"] == []
    assert plain["readiness"]["state"] == READY
    # Said by the payload itself, so no reader has to assume it.
    assert plain["readiness"]["checked_locally"] is True


def test_readiness_is_not_enabledness_on_the_wire(bundled, monkeypatch):
    """A Skill the user disabled is still ready, and one they enabled can still
    be missing its hardware. Folding the two together tells a user who flipped
    the toggle that they made the Skill work."""
    from openai4s.skills_loader import loader as loader_mod

    monkeypatch.setitem(loader_mod._REQUIREMENT_CHECKS, "gpu", lambda: False)

    handler = _handler(bundled)
    assert _call(
        handler,
        "PUT",
        "/skills/catalog/plain-skill/enabled",
        {"enabled": False},
    ) == (200, {"ok": True})

    rows = _rows(_call(handler, "GET", "/skills/catalog")[1])
    assert rows["plain-skill"]["enabled"] is False
    assert rows["plain-skill"]["readiness"]["state"] == READY
    assert rows["gpu-skill"]["enabled"] is True
    assert rows["gpu-skill"]["readiness"]["state"] == NEEDS_SETUP


def test_browsing_the_catalogue_starts_nothing_and_contacts_nobody(
    bundled, monkeypatch
):
    """The load-bearing property of a local-only readiness check.

    Deliberately not patching the GPU check away: the point is that the real
    one runs and still spawns nothing. `nvidia-smi` executed per Skill would be
    thirty-four subprocesses to draw a list, a cost that only shows up on a
    slow machine and is then blamed on something else.
    """
    handler = _handler(bundled)  # built before the guards, so setup is exempt

    def _spawned(*args, **kwargs):
        raise AssertionError("browsing the skill catalogue started a subprocess")

    def _dialled(*args, **kwargs):
        raise AssertionError("browsing the skill catalogue made a network call")

    monkeypatch.setattr(subprocess, "Popen", _spawned)
    monkeypatch.setattr(subprocess, "run", _spawned)
    monkeypatch.setattr(os, "system", _spawned)
    monkeypatch.setattr(os, "posix_spawn", _spawned, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", _dialled)

    code, payload = _call(handler, "GET", "/skills/catalog")
    assert code == 200
    rows = _rows(payload)
    # The guarded call really did compute readiness -- otherwise this test
    # would pass just as well against a projection that dropped the field.
    assert rows["gpu-skill"]["requirements"] == ["gpu"]
    assert rows["gpu-skill"]["readiness"]["checked_locally"] is True


def test_a_loader_without_readiness_is_answered_rather_than_guessed(bundled):
    """The service never publishes a readiness it did not derive.

    A loader row that carries `requirements` but no readiness block (an older
    loader, or a test double) still gets one from the same function the loader
    uses -- not a hardcoded `ready`, which would be a fabricated promise.
    """
    service = SkillCustomizationService(SkillLoader(cfg=bundled))

    class BareLoader:
        cfg = bundled

        def catalog(self, include_disabled=False):
            return [{"name": "gpu-skill", "requirements": ["quantum-annealer"]}]

        def skills(self, include_disabled=False):
            return {}

    service.loader = BareLoader()
    row = service.catalog(set())[0]
    assert row["requirements"] == ["quantum-annealer"]
    assert row["readiness"]["state"] == UNKNOWN
    assert row["readiness"]["unverifiable"] == ["quantum-annealer"]
