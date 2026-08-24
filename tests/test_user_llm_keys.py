"""A user's own LLM credential (M4-1, decision D7's second half).

Four claims, and the negative ones are the ones worth having:

- Alice's key is used for Alice's session and **never** for Bob's. A
  per-user credential that leaks across sessions is worse than no feature
  at all, because the billing looks right while the trust boundary is
  gone.
- A user with no key of their own runs on the group's, unchanged. That is
  what makes this additive rather than a migration (INV-1).
- The override is per provider: a user with their own Anthropic account
  and no OpenAI key gets theirs for one and the group's for the other.
- The value is write-only. No route reads a key back, and the reference —
  which names a keychain slot — is not in any response either.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_team_auth_routes import (  # noqa: F401  (fixture reuse)
    _fast_pbkdf2,
    _get,
    _login,
    _post,
    _speak,
    _TeamDaemon,
)


def _body(raw: bytes) -> dict:
    return json.loads(raw.split(b"\r\n\r\n", 1)[1].decode("utf-8"))


def _request(port: int, method: str, path: str, body: dict, cookie: str):
    payload = json.dumps(body).encode("utf-8")
    head = (
        "\r\n".join(
            [
                f"{method} {path} HTTP/1.1",
                f"Host: 127.0.0.1:{port}",
                "Content-Type: application/json",
                f"Content-Length: {len(payload)}",
                f"Cookie: {cookie}",
                "Connection: close",
            ]
        )
        + "\r\n\r\n"
    ).encode("ascii")
    return _speak(port, head + payload)


@pytest.fixture()
def daemon(tmp_path: Path):
    node = _TeamDaemon(tmp_path)
    node.seed_user("root", "fake-pw-r", role="admin")
    node.seed_user("alice", "fake-pw-a")
    node.seed_user("bob", "fake-pw-b")
    try:
        yield node
    finally:
        node.close()


def _session_for(daemon, username: str) -> str:
    user = daemon.store.team.get_user_by_username(username)
    session_id = f"frame_{username}_1"
    daemon.store.team.set_session_owner(session_id, user["id"])
    return session_id


class _State:
    """Just enough SessionState for `_llm_cfg`'s owner lookup."""

    def __init__(self, root_frame_id: str):
        self.root_frame_id = root_frame_id
        self.model = None
        self.frozen_model_binding = None


def _effective_key(daemon, session_id: str) -> str:
    return daemon.runner._llm_cfg(_State(session_id)).api_key


# -- isolation ---------------------------------------------------------------


def test_one_users_key_is_never_used_for_another_users_session(daemon):
    """The claim that matters. Everything else here is bookkeeping."""
    alice = _login(daemon, "alice", "fake-pw-a")
    alice_session = _session_for(daemon, "alice")
    bob_session = _session_for(daemon, "bob")
    provider = daemon.runner.cfg.llm.provider

    status, raw = _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-only"},
        alice,
    )
    assert status == 200, raw.split(b"\r\n\r\n", 1)[-1][:400]

    assert _effective_key(daemon, alice_session) == "sk-alice-only"
    assert _effective_key(daemon, bob_session) == "test-key"


def test_a_user_without_a_key_of_their_own_runs_on_the_shared_one(daemon):
    """INV-1's shape for this feature: absence is the fallback, and it is
    the same code path a single-user install takes."""
    session_id = _session_for(daemon, "bob")
    assert _effective_key(daemon, session_id) == "test-key"


def test_the_override_is_per_provider(daemon):
    """A user with their own account for one provider and none for another
    is the ordinary arrangement, not an exotic one."""
    alice = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_for(daemon, "alice")
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": "claude", "api_key": "sk-alice-anthropic"},
        alice,
    )
    # the active provider is not the one she configured
    assert _effective_key(daemon, session_id) == "test-key"

    cfg = daemon.runner.cfg
    daemon.runner.cfg = replace(cfg, llm=replace(cfg.llm, provider="claude"))
    try:
        assert _effective_key(daemon, session_id) == "sk-alice-anthropic"
    finally:
        daemon.runner.cfg = cfg


def test_an_unowned_session_gets_the_shared_key(daemon):
    """Pre-team history and CLI runs have no owner row; there is nobody
    whose key it could be."""
    assert _effective_key(daemon, "frame_nobody") == "test-key"


# -- the value is write-only -------------------------------------------------


def test_no_route_reads_a_key_back(daemon):
    alice = _login(daemon, "alice", "fake-pw-a")
    provider = daemon.runner.cfg.llm.provider
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-secret"},
        alice,
    )
    status, raw = _get(daemon.port, "/api/v1/auth/me/llm-key", cookie=alice)
    assert status == 200
    payload = _body(raw)
    assert payload["keys"] == [
        {
            "provider": provider,
            "configured": True,
            "created_at": payload["keys"][0]["created_at"],
            "updated_at": payload["keys"][0]["updated_at"],
        }
    ]
    assert b"sk-alice-secret" not in raw
    # nor the reference: a slot name is part of a credential's identity
    record = daemon.store.user_keys.get(
        daemon.store.team.get_user_by_username("alice")["id"], provider
    )
    assert record.secret_ref.encode() not in raw


def test_one_user_cannot_see_or_set_anothers(daemon):
    alice = _login(daemon, "alice", "fake-pw-a")
    bob = _login(daemon, "bob", "fake-pw-b")
    provider = daemon.runner.cfg.llm.provider
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-secret"},
        alice,
    )
    # The route is self-service by construction: it has no user parameter,
    # so Bob asking gets Bob's answer, which is nothing.
    status, raw = _get(daemon.port, "/api/v1/auth/me/llm-key", cookie=bob)
    assert status == 200
    assert _body(raw)["keys"] == []


# -- clearing ----------------------------------------------------------------


def test_clearing_falls_back_to_the_shared_key(daemon):
    alice = _login(daemon, "alice", "fake-pw-a")
    session_id = _session_for(daemon, "alice")
    provider = daemon.runner.cfg.llm.provider
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-only"},
        alice,
    )
    assert _effective_key(daemon, session_id) == "sk-alice-only"

    status, raw = _request(
        daemon.port, "DELETE", "/api/v1/auth/me/llm-key", {"provider": provider}, alice
    )
    assert status == 200 and _body(raw)["removed"] is True
    assert _effective_key(daemon, session_id) == "test-key"


def test_clearing_a_key_that_was_never_set_is_not_an_error(daemon):
    """The user's intent — "do not use my key" — is satisfied either way."""
    alice = _login(daemon, "alice", "fake-pw-a")
    status, raw = _request(
        daemon.port, "DELETE", "/api/v1/auth/me/llm-key", {"provider": "claude"}, alice
    )
    assert status == 200
    assert _body(raw)["removed"] is False


def test_disabling_an_account_takes_its_keys_with_it(daemon):
    """A credential that outlives its account is one nobody is watching,
    and the row was the only thing that would ever name its slot again."""
    alice = _login(daemon, "alice", "fake-pw-a")
    root = _login(daemon, "root", "fake-pw-r")
    session_id = _session_for(daemon, "alice")
    provider = daemon.runner.cfg.llm.provider
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-only"},
        alice,
    )
    user_id = daemon.store.team.get_user_by_username("alice")["id"]

    status, _ = _post(
        daemon.port, f"/api/v1/team/users/{user_id}/disable", {}, cookie=root
    )
    assert status == 200
    assert daemon.store.user_keys.list_for_user(user_id) == []
    assert _effective_key(daemon, session_id) == "test-key"


# -- refusal rather than a silent fallback -----------------------------------


def test_a_configured_but_unreadable_key_refuses_the_turn(daemon):
    """The user asked for their own credential to be used. Quietly charging
    the group instead is a decision they did not make."""
    from openai4s.server.errors import GatewayError

    user_id = daemon.store.team.get_user_by_username("alice")["id"]
    session_id = _session_for(daemon, "alice")
    provider = daemon.runner.cfg.llm.provider
    # a row pointing at a slot that holds nothing — a revoked keychain
    # entry, a database moved between machines
    daemon.store.user_keys.set_ref(user_id, provider, "secret:v2:gone/llm-user/x")

    with pytest.raises(GatewayError) as caught:
        _effective_key(daemon, session_id)
    assert caught.value.code == 409
    assert caught.value.error_code == "user_key_unreadable"


def test_team_mode_off_has_no_such_route(tmp_path):
    """INV-1: a single-user install is unchanged, including its surface."""
    node = _TeamDaemon(tmp_path, team_mode=False)
    try:
        status, _ = _get(node.port, "/api/v1/auth/me/llm-key", token=node.token)
        assert status in (403, 404)
    finally:
        node.close()


def test_clearing_a_key_deletes_the_broker_value_too(daemon):
    """The row is a reference. Deleting only it left the provider key in
    the broker with nothing in the product that named the slot: "cleared"
    reported to the user, credential still on disk."""
    alice = _login(daemon, "alice", "fake-pw-a")
    provider = daemon.runner.cfg.llm.provider
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-transient"},
        alice,
    )
    user_id = daemon.store.team.get_user_by_username("alice")["id"]
    ref = daemon.store.user_keys.get(user_id, provider).secret_ref
    assert daemon.store.secrets.get(ref) == "sk-alice-transient"

    status, _ = _request(
        daemon.port, "DELETE", "/api/v1/auth/me/llm-key", {"provider": provider}, alice
    )
    assert status == 200
    assert daemon.store.secrets.get(ref) in (
        None,
        "",
    ), "the broker still holds a key the product says is cleared"


def test_disabling_an_account_deletes_its_broker_values(daemon):
    alice = _login(daemon, "alice", "fake-pw-a")
    root = _login(daemon, "root", "fake-pw-r")
    provider = daemon.runner.cfg.llm.provider
    _request(
        daemon.port,
        "PUT",
        "/api/v1/auth/me/llm-key",
        {"provider": provider, "api_key": "sk-alice-transient"},
        alice,
    )
    user_id = daemon.store.team.get_user_by_username("alice")["id"]
    ref = daemon.store.user_keys.get(user_id, provider).secret_ref

    status, _ = _post(
        daemon.port, f"/api/v1/team/users/{user_id}/disable", {}, cookie=root
    )
    assert status == 200
    assert daemon.store.secrets.get(ref) in (None, "")
