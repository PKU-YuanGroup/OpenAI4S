"""Whether a model profile is usable, and who decides to find out.

Two things had to be separated. Readiness is derived from local state — is
there a key, is the protocol dispatchable, is there a model — so opening
Customize answers "is this usable" for every profile at no network cost.
Reachability cannot be known without a request, and a request is something the
user asks for.

A readiness card that quietly probed on render would be precisely the implicit
outbound call P0-1 spent its time removing, except aimed at the user's own
paid quota.
"""

from __future__ import annotations

import pytest

from openai4s.config import Config, LLMConfig
from openai4s.llm import PROVIDERS, provider_specs
from openai4s.server.model_profiles import (
    PROFILE_PROTOCOLS,
    WITHHELD_PROTOCOLS,
    ModelProfileError,
    ModelProfileService,
)
from openai4s.store import get_store


def _service(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data",
        llm=LLMConfig(provider="deepseek", api_key="test-key"),
    )
    store = get_store(cfg.db_path)
    return store, ModelProfileService(store, cfg, providers=provider_specs)


def test_every_dispatchable_provider_is_selectable_or_declared_withheld():
    """`gemini` and `openai_responses` were neither.

    Both have complete provider specs and both are dispatched by the LLM layer,
    and neither could be chosen as a profile — so a user holding a Gemini key
    had no way to say so. The capability was built, shipped and unreachable,
    and nothing recorded a decision to leave it that way, because there wasn't
    one.

    A provider that genuinely should not be user-selectable is a decision worth
    writing down. Silence is how these two went unnoticed.
    """
    unreachable = set(PROVIDERS) - set(PROFILE_PROTOCOLS) - set(WITHHELD_PROTOCOLS)
    assert not unreachable, (
        "these providers can be dispatched but not selected, and are not "
        f"declared as deliberately withheld: {sorted(unreachable)}"
    )
    # And the reverse: offering a protocol the LLM layer cannot dispatch would
    # be a menu entry that fails at send time.
    assert not set(PROFILE_PROTOCOLS) - set(PROVIDERS)


def test_readiness_never_contacts_anyone(tmp_path, monkeypatch):
    """The load-bearing property. Rendering the card must cost nothing.

    Asserted by making any outbound call an error: `chat` is the only way this
    module could reach a provider, so a readiness path that grew a probe would
    fail here rather than quietly spending a user's quota on every page load.
    """
    from openai4s import llm

    def _explode(*args, **kwargs):
        raise AssertionError("readiness made a network call")

    monkeypatch.setattr(llm, "chat", _explode)
    store, service = _service(tmp_path)
    service.create(
        {
            "name": "G",
            "provider": "gemini",
            "base_url": "https://x",
            "model": "gemini-2.5-flash",
            "api_key": "abc123def456ghi789",
        }
    )
    for row in store.list_model_profiles():
        projected = service.public_profile(row)
        card = projected["readiness"]
        assert card["checked_endpoint"] is False
        assert card["state"] == "ready"
        assert projected.get("capability_receipt") is None


def test_readiness_names_what_is_missing(tmp_path):
    store, service = _service(tmp_path)
    service.create(
        {"name": "NoKey", "provider": "claude", "base_url": "", "model": "m"}
    )
    card = service.public_profile(store.list_model_profiles()[0])["readiness"]
    assert card["state"] == "needs_key"

    # A protocol this build cannot dispatch is its own answer, not "needs_key":
    # supplying a key would not help, and saying so would send the user to fix
    # the wrong thing.
    assert service.readiness({"provider": "not-a-thing"})["state"] == "unsupported"


def test_ready_does_not_claim_the_endpoint_answered(tmp_path):
    """ "Ready" means the local configuration is complete. A user who read it as
    "verified" would be reading a stronger claim than the data supports, so the
    detail line says which one it is."""
    store, service = _service(tmp_path)
    service.create(
        {
            "name": "C",
            "provider": "claude",
            "base_url": "https://y",
            "model": "m",
            "api_key": "abc123def456ghi789",
        }
    )
    card = service.public_profile(store.list_model_profiles()[0])["readiness"]
    assert card["state"] == "ready"
    assert "not been contacted" in card["detail"]


def test_the_probe_refuses_to_spend_a_request_it_knows_will_fail(tmp_path, monkeypatch):
    """A profile with no key cannot be probed usefully: the 401 that comes back
    reads like an endpoint problem rather than the missing credential it is."""
    from openai4s import llm

    monkeypatch.setattr(
        llm, "chat", lambda *a, **k: (_ for _ in ()).throw(AssertionError("contacted"))
    )
    store, service = _service(tmp_path)
    service.create(
        {"name": "NoKey", "provider": "claude", "base_url": "", "model": "m"}
    )
    profile_id = store.list_model_profiles()[0]["id"]

    result = service.probe(profile_id)
    assert result["contacted"] is False
    assert result["reachable"] is False
    assert result["state"] == "needs_key"


def test_a_failed_probe_names_the_cause_without_quoting_the_provider(
    tmp_path, monkeypatch
):
    """The concern this test used to encode is real; the mechanism was not.

    It was named `..._reports_the_providers_own_words_redacted` and asserted
    that the provider's text reached `detail`, on the argument that rewriting it
    "would lose the one detail that tells a user whether it is their key, their
    model name or their network". Keeping that distinction is right. Getting it
    by publishing the provider's prose is not: `redact_text` is shape-based, and
    measured against a real 403 body it replaces a credential-shaped token while
    `10.4.2.17:8443`, `/Users/<name>/.certs/corp-ca.pem` and
    `org-Acme-Research-Lab` all survive into a 200 body and the Customize ->
    Models panel. `errors.py` had already removed `redact_text` for exactly this.

    So the distinction now comes from `status`/`error_code` -- fields this
    codebase sets on `TransportError` -- which is what `gateway._friendly_error`
    does for the turn path. Same information, no provider text.
    """
    import openai4s.llm as llm_module
    from openai4s.llm.models import TransportError

    def _fail(*a, **k):
        raise TransportError(
            "chat failed (404): {'error': 'no model bogus-1 at /srv/models'}",
            provider="claude",
            status=404,
            error_code="model_not_found",
        )

    monkeypatch.setattr(llm_module, "chat", _fail)
    store, service = _service(tmp_path)
    service.create(
        {
            "name": "C",
            "provider": "claude",
            "base_url": "https://y",
            "model": "bogus-1",
            "api_key": "abc123def456ghi789",
        }
    )
    result = service.probe(store.list_model_profiles()[0]["id"])
    assert result["contacted"] is True and result["reachable"] is False
    # The cause is still distinguishable -- a wrong model name, not a bad key.
    assert "does not have a model by that name" in result["detail"]
    # And the provider's own text is not in it.
    assert "/srv/models" not in result["detail"]
    assert "bogus-1" not in result["detail"]
    # Correlatable instead: a stable code. `request_id` is the gateway's, so it
    # is present when a request made this call and absent for a direct service
    # call like this one -- the field exists either way.
    assert result["code"] == "probe_failed"
    assert "request_id" in result


@pytest.mark.stubbed_backend
def test_a_burst_refusal_is_not_reported_as_a_bad_model_or_key(tmp_path, monkeypatch):
    import openai4s.llm as llm_module
    from openai4s.llm.models import TransportError

    def _fail(*_args, **_kwargs):
        raise TransportError(
            "System protection triggered by request burst; request id private",
            provider="ark",
            status=429,
            error_code="RequestBurstTooFast",
            retryable=True,
        )

    monkeypatch.setattr(llm_module, "chat", _fail)
    store, service = _service(tmp_path)
    service.create(
        {
            "name": "Ark",
            "provider": "ark",
            "base_url": "https://ark.invalid/v3",
            "model": "doubao-seed-2.0-pro",
            "api_key": "abc123def456ghi789",
        }
    )

    result = service.probe(store.list_model_profiles()[0]["id"])
    assert result["contacted"] is True and result["reachable"] is False
    assert "burst-traffic protection" in result["detail"]
    assert "credential is not the problem" in result["detail"]
    assert "request id private" not in result["detail"]


def test_probing_an_unknown_profile_is_a_404(tmp_path):
    _store, service = _service(tmp_path)
    with pytest.raises(ModelProfileError) as refused:
        service.probe("mp-nope")
    assert refused.value.status_code == 404


def test_a_probe_never_publishes_what_redaction_does_not_catch(tmp_path, monkeypatch):
    """The measurement that decided this, kept as a test.

    `redact_text` is shape-based. Against a real provider body it replaces a
    credential-shaped token and lets everything else through, and "everything
    else" on a failed TLS or auth call includes the absolute path of a cert
    (with the account name in it), an internal address, and an org identifier.
    All three reached a 200 body and the Customize -> Models panel.
    """
    import openai4s.llm as llm_module
    from openai4s.llm.models import TransportError

    def _fail(*a, **k):
        raise TransportError(
            "LLM HTTP 403: connect 10.4.2.17:8443 via "
            "/Users/alice/.certs/corp-ca.pem for org-Acme-Research-Lab",
            provider="claude",
            status=403,
            error_code="forbidden",
        )

    monkeypatch.setattr(llm_module, "chat", _fail)
    store, service = _service(tmp_path)
    service.create(
        {
            "name": "C",
            "provider": "claude",
            "base_url": "https://y",
            "model": "m",
            "api_key": "abc123def456ghi789",
        }
    )
    detail = service.probe(store.list_model_profiles()[0]["id"])["detail"]

    for leaked in ("10.4.2.17", "/Users/alice", "corp-ca.pem", "org-Acme-Research-Lab"):
        assert leaked not in detail, detail
    assert "not permitted to use this model" in detail
