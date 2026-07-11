"""Direct contracts for the session-local host credential service."""

from __future__ import annotations

import pytest

from openai4s.config import Config
from openai4s.host.credentials import CredentialService
from openai4s.host_dispatch import HostDispatcher


def test_credential_service_round_trip_overwrite_and_sorted_names():
    service = CredentialService()

    assert service.set({"name": "Z_TOKEN", "value": "z-secret"}) == {
        "ok": True,
        "name": "Z_TOKEN",
    }
    assert service.set({"name": "A_TOKEN"}) == {
        "ok": True,
        "name": "A_TOKEN",
    }
    service.set({"name": "Z_TOKEN", "value": "new-secret"})

    assert service.get("A_TOKEN") == {"name": "A_TOKEN", "value": ""}
    assert service.get("Z_TOKEN") == {
        "name": "Z_TOKEN",
        "value": "new-secret",
    }
    names = service.list()
    assert names == ["A_TOKEN", "Z_TOKEN"]
    assert "secret" not in repr(names)


def test_credential_service_preserves_key_errors_and_session_isolation():
    first = CredentialService()
    second = CredentialService()
    first.set({"name": "ONLY_FIRST", "value": "private"})

    with pytest.raises(KeyError, match="no credential 'ONLY_FIRST'"):
        second.get("ONLY_FIRST")
    with pytest.raises(KeyError, match="name"):
        first.set({"value": "missing-name"})
    assert second.list() == []


def test_host_dispatcher_credentials_wrappers_share_one_service(tmp_path):
    dispatcher = HostDispatcher(Config(data_dir=tmp_path))

    assert dispatcher._m_credentials_set(
        {"name": "HF_TOKEN", "value": "test-secret"}
    ) == {"ok": True, "name": "HF_TOKEN"}
    assert dispatcher._m_credentials_get("HF_TOKEN") == {
        "name": "HF_TOKEN",
        "value": "test-secret",
    }
    assert dispatcher._m_credentials_list() == ["HF_TOKEN"]
    assert dispatcher._credential_service.list() == ["HF_TOKEN"]


def test_host_dispatchers_do_not_share_credentials(tmp_path):
    config = Config(data_dir=tmp_path)
    first = HostDispatcher(config)
    second = HostDispatcher(config)

    first._m_credentials_set({"name": "SESSION_ONLY", "value": "private"})
    with pytest.raises(KeyError, match="no credential 'SESSION_ONLY'"):
        second._m_credentials_get("SESSION_ONLY")


def test_dispatcher_permission_denial_precedes_credential_mutation(tmp_path):
    frame_id = "credential-frame"
    dispatcher = HostDispatcher(Config(data_dir=tmp_path), frame_id=frame_id)
    dispatcher.store.set_permission_rule(
        scope="conversation",
        scope_id=frame_id,
        tool="credentials_set",
        pattern="*",
        decision="deny",
    )

    result = dispatcher(
        "credentials_set",
        [{"name": "BLOCKED_TOKEN", "value": "must-not-be-stored"}],
    )

    assert set(result) == {"error"}
    assert "Permission denied" in result["error"]
    assert dispatcher._credential_service.list() == []


def test_replay_excludes_all_credential_methods_and_values(tmp_path):
    from openai4s.replay import TapeRecorder

    dispatcher = HostDispatcher(Config(data_dir=tmp_path))
    # The production default for injecting a credential is deliberately
    # ``ask``.  This headless replay contract is testing redaction rather than
    # unattended approval, so authorize the mutation explicitly.
    dispatcher.store.set_permission_rule(
        scope="global",
        scope_id="",
        tool="credentials_set",
        pattern="*",
        decision="allow",
    )
    recorder = TapeRecorder(tmp_path / "credentials-tape.json")
    dispatcher.recorder = recorder
    secret = "synthetic-secret-never-record"

    dispatcher("credentials_set", [{"name": "TOKEN", "value": secret}])
    assert dispatcher("credentials_get", ["TOKEN"])["value"] == secret
    assert dispatcher("credentials_list", []) == ["TOKEN"]

    assert not {
        "credentials_set",
        "credentials_get",
        "credentials_list",
    } & {record["method"] for record in recorder.records}
    assert secret not in repr(recorder.records)
