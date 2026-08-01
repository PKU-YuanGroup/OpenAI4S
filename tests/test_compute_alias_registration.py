"""An agent could name any ssh destination and the daemon would dial it.

`ComputeManager._safe_alias` checks that an alias cannot be read as an ssh
*option* or a second word. That is a check about argv parsing, and it was the
only one: `provider="ssh:<anything well-formed>"` went straight to
`ssh <anything>`, resolved by whatever a `Host *` stanza or a DNS search domain
happens to supply. On `host.compute(...)` the string comes from the model.

The refusal sits at that boundary rather than inside `_split`. `_split` is on
every path into the manager, including the CLI and the user's own Compute
panel, where the alias is something a person typed and demanding prior
registration would refuse names the product itself offers them. What is
different here is only the provenance of the string.

Registration means the compute host registry *or* `~/.ssh/config`, because the
Web UI lists the latter as remote-GPU candidates — a name from there has been
offered to the user by the product.

The test that matters is the fault-injected one: it fails if a subprocess is
spawned at all, because "refused" and "refused after dialling" are different
security outcomes and only one of them is the claim.
"""

from __future__ import annotations

import subprocess

import pytest

from openai4s.compute import registry
from openai4s.config import Config
from openai4s.host_dispatch import HostDispatcher


@pytest.fixture
def dispatcher(tmp_path):
    cfg = Config(data_dir=tmp_path)
    cfg.ensure_dirs()
    return HostDispatcher(cfg=cfg, frame_id="f-alias")


@pytest.fixture
def no_subprocess(monkeypatch):
    """Any spawn is a failure, not a slow path."""
    calls: list[tuple] = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError(f"a subprocess was spawned: {args!r}")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden, raising=False)
    return calls


def test_an_unregistered_alias_is_refused_before_any_subprocess(
    dispatcher, no_subprocess
):
    out = dispatcher._m_compute_submit(
        {"provider": "ssh:not-a-host-anyone-named", "script": "echo hi"}
    )

    # The soft-fail wire shape the SDK re-raises, not an exception through the
    # kernel protocol.
    assert isinstance(out, dict), out
    assert out.get("error_kind") == "not_found", out
    assert "not a host this daemon knows" in out["error"]
    assert no_subprocess == [], "the refusal happened after dialling"


def test_a_registered_alias_gets_past_the_gate(dispatcher, no_subprocess, tmp_path):
    """The gate must refuse only the unknown, or it is an outage.

    Registered, so the check passes and the manager takes over. What the
    manager then does with the payload is not this test's business -- only that
    the refusal is no longer the alias one, which is what proves the gate let
    this destination through.
    """
    registry.add_host(alias="lab", label="lab", data_dir=tmp_path)

    with pytest.raises(Exception) as caught:
        dispatcher._m_compute_submit({"provider": "ssh:lab", "script": "echo hi"})

    assert "not a host this daemon knows" not in str(caught.value)


def test_an_ssh_config_alias_counts_as_registered(dispatcher, monkeypatch):
    """The Web UI offers these, so refusing them would contradict the product."""
    monkeypatch.setattr(registry, "ssh_config_aliases", lambda: ["from-ssh-config"])
    monkeypatch.setattr(registry, "get_host", lambda alias, data_dir=None: None)

    assert registry.is_known_alias("from-ssh-config") is True
    assert registry.is_known_alias("neither-place") is False


def test_a_byoc_target_is_not_touched_by_the_alias_gate(dispatcher, no_subprocess):
    """The check is about ssh destinations; byoc ids resolve elsewhere."""
    out = dispatcher._m_compute_submit({"provider": "byoc:nope", "script": "echo hi"})

    assert isinstance(out, dict), out
    # Refused by the byoc provider lookup, not by the alias gate.
    assert "not a host this daemon knows" not in str(out.get("error") or "")


def test_registration_is_read_from_the_callers_data_dir(tmp_path):
    """A manager bound to one directory must not answer for another's hosts."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    registry.add_host(alias="only-here", label="only-here", data_dir=tmp_path)

    assert registry.is_known_alias("only-here", tmp_path) is True
    assert registry.is_known_alias("only-here", other) is False
