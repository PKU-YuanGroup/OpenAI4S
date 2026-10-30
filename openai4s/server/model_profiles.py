"""Model-provider profile lifecycle kept out of the HTTP gateway facade."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from openai4s.config import (
    Config,
    LLMConfig,
    is_placeholder_api_key,
    provider_env_api_key,
)
from openai4s.endpoint_identity import normalize_endpoint
from openai4s.llm.capabilities import (
    CAPABILITY_PROBE_TOOL,
    PROBE_VERSION,
    capability_probe_window,
    classify_probe_error,
    drop_receipt_overlays,
)
from openai4s.llm.capabilities import (
    explain_model_capabilities as _explain_model_capabilities,
)
from openai4s.llm.capabilities import (
    profile_receipt_identity,
    receipt_evidence_key,
    receipt_evidence_rank,
    replace_receipt_overlay,
    reset_receipt_overlay,
    streaming_evidence,
    tool_call_matches_schema,
)
from openai4s.llm.catalog import ModelPreset, model_presets
from openai4s.llm.resolve import keyless_endpoint
from openai4s.security.secret_broker import is_ref
from openai4s.storage.model_capability_receipts import (
    EVIDENCE_FALSE,
    EVIDENCE_TRUE,
    EVIDENCE_UNKNOWN,
    public_receipt,
)

# Model profiles select a transport contract, not an arbitrary vendor name.
# Keep the persisted ids compatible with the existing LLM registry while the
# UI presents these as human-readable protocol choices.
#
# `gemini` and `openai_responses` were missing, and not because anyone decided
# they should be: both have complete provider specs, both are dispatched by the
# LLM layer, and neither could be chosen. A user with a Gemini key had no way
# to say so — the capability was built, shipped, and unreachable.
#
# Kept in step with the registry by `tests/test_model_catalog.py`, which fails
# when a provider exists that is neither selectable nor listed below as
# deliberately withheld. A provider that should not be user-selectable is a
# decision worth writing down; silence is how the last two went unnoticed.
PROFILE_PROTOCOLS = (
    "chatgpt",
    "claude",
    "ark",
    "gemini",
    "openai_responses",
    "ollama",
)

#: Registry providers deliberately not offered as a profile choice, each with a
#: reason. Empty today.
WITHHELD_PROTOCOLS: dict[str, str] = {}

#: What each readiness problem means, in the words a user reads. The `state`
#: code stays the thing a client branches on; this is only the sentence.
PROBLEM_DETAIL = {
    "needs_key": "no credential resolves for this profile",
    "needs_model": "no model is named and this protocol has no default",
    "credential_revoked": (
        "the API key stored for this profile can no longer be read; enter it again"
    ),
}

#: Where a profile's credential comes from. The first three are usable.
CREDENTIAL_PROFILE = "profile"
CREDENTIAL_ENVIRONMENT = "environment"
CREDENTIAL_LOCAL = "local"
CREDENTIAL_REVOKED = "revoked"
CREDENTIAL_DELETED = "deleted"
CREDENTIAL_MISSING = "missing"
#: A pinned revision whose provider or endpoint is not the one the profile
#: names now. The profile's credential belongs to its current configuration.
CREDENTIAL_SCOPE_MISMATCH = "revision_scope_mismatch"
_USABLE_CREDENTIALS = frozenset(
    (CREDENTIAL_PROFILE, CREDENTIAL_ENVIRONMENT, CREDENTIAL_LOCAL)
)


@dataclass(frozen=True)
class ProfileCredential:
    """The one answer to "what would this profile be dispatched under".

    `api_key` is empty for a keyless local endpoint and for every unusable
    source. Never serialised: callers project `source` or a boolean.
    """

    api_key: str
    source: str

    @property
    def usable(self) -> bool:
        return self.source in _USABLE_CREDENTIALS


class ModelProfileError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def clean_api_key(value: Any) -> str:
    """Trim API keys and collapse obvious template stubs to empty."""
    key = str(value or "").strip()
    return "" if is_placeholder_api_key(key) else key


def resolve_profile_key(store: Any, profile: Mapping[str, Any]) -> str:
    """A profile's actual API key, whether brokered or legacy plaintext.

    Module-level because two unrelated scopes need it — the profile service and
    SessionRunner's review ports — and a second copy of this rule is exactly how
    one of them would end up shipping a reference to a provider.

    Every read of ``profile["api_key"]`` must come through here. Once migrated
    the field holds a broker reference: a truthy string that is not a key.
    Handed to a provider it fails auth in a way that looks like a bad key;
    tested with ``if key:`` it reports a revoked credential as present.
    """
    raw = str(profile.get("api_key") or "")
    if not raw:
        return ""
    if not is_ref(raw):
        return clean_api_key(raw)
    try:
        return clean_api_key(store.secrets.get(raw))
    except Exception:  # noqa: BLE001 - an unreadable secret is an absent one
        return ""


def _network_failure(error: BaseException | None) -> bool:
    """A connect, DNS or socket failure: no HTTP status came back.

    `transport.py` wraps every `URLError` in a `TransportError` with no
    `status`, and that is not an `OSError`, so an `isinstance` check alone
    missed every refused connection, unknown host and connect timeout. The
    probe reported them as "internal error" and then sent its second request
    to an endpoint that had just failed to answer the first. The gateway's
    turn path (`_friendly_error`) already reads a status-less transport error
    this way. The only other status-less transport errors are a stream read
    that broke mid-reply and a caller's cancel, which the probe never sends.
    """
    from openai4s.llm.models import TransportError

    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    return isinstance(error, TransportError) and error.status is None


def _probe_detail(error: Exception, public: dict) -> str:
    """What a failed probe may say, chosen from controlled signals only.

    The old line was `redact_text(f"{type(error).__name__}: {error}")[:400]`,
    defended by a comment arguing that a rewritten message "would lose the one
    detail that tells a user whether it is their key, their model name or their
    network". The concern is right and this answers it -- by branching on
    `status` and `error_code`, which `TransportError` sets deliberately
    (`llm/models.py`), rather than on the provider's prose.

    `redact_text` is not sufficient here and `errors.py` already removed it for
    this reason. Measured on a 403 body: a credential-shaped token is replaced,
    but `10.4.2.17:8443`, `/Users/<name>/.certs/corp-ca.pem` and
    `org-Acme-Research-Lab` all survive. The redaction is shape-based and a
    provider body carries more than credentials -- an absolute path with the
    account name on it, internal network topology, private model identifiers --
    into a 200 body and the Customize -> Models panel.

    `gateway._friendly_error` solved exactly this for the turn path and its
    docstring lays out the same reasoning. This is the surface that treatment
    was never extended to.
    """
    status = getattr(error, "status", None)
    code = str(getattr(error, "error_code", "") or "")
    from openai4s.llm import llm_failure_code

    failure_code = llm_failure_code(error)
    if failure_code == "llm_deadline_exceeded":
        return "the model call reached its total time limit; try a smaller request"
    if failure_code == "llm_response_too_large":
        return "the model response exceeded its size limit; request smaller output"
    if failure_code == "llm_request_burst":
        return (
            "the provider's burst-traffic protection was triggered; the "
            "credential is not the problem, so wait briefly and probe again"
        )
    if failure_code == "llm_upstream_overloaded":
        return (
            "the provider is currently overloaded; this is not a configuration "
            "problem, so wait briefly and probe again"
        )
    if status == 401 or code in ("invalid_api_key", "unauthorized"):
        return (
            "the provider rejected the credential; check the API key for this "
            "profile in Customize -> Models"
        )
    if status == 403 or code == "forbidden":
        return (
            "the credential is valid but not permitted to use this model; "
            "check the model name and the account's entitlements"
        )
    if status == 404 or code in ("model_not_found", "not_found"):
        return "the provider does not have a model by that name at this endpoint"
    if failure_code == "llm_rate_limited":
        return "the provider is rate-limiting this credential; try again shortly"
    if isinstance(status, int) and 500 <= status < 600:
        return (
            "the provider returned a server error; this is not a configuration problem"
        )
    if _network_failure(error):
        return (
            "the endpoint could not be reached, or the connection dropped; "
            "check the base URL and this machine's network access"
        )
    if status == 408:
        return (
            "the endpoint timed out answering the probe; check this machine's "
            "network access and try again"
        )
    if isinstance(status, int) and 400 <= status < 500:
        # The status is a transport-set integer, never provider prose.
        return (
            f"the provider rejected the probe request (HTTP {status}); check "
            "the model name and the protocol selected for this profile"
        )
    # The transport normalises a body it could not decode into its own typed
    # `LLMError` and keeps the decode failure as the cause, so the check has to
    # read the cause too -- still a type, never the provider's prose.
    if isinstance(error, json.JSONDecodeError) or isinstance(
        getattr(error, "__cause__", None), (json.JSONDecodeError, UnicodeDecodeError)
    ):
        return (
            "the endpoint answered, but not like a model API; check the base "
            "URL (OpenAI-compatible endpoints usually end in /v1)"
        )
    # Unknown provenance. `public_exception` already chose the generic sentence
    # and wrote the original to the diagnostic log under its own surface.
    return str(public.get("error") or "the probe failed")


class ModelProfileService:
    """Own migration, CRUD, activation, and public projection of model profiles."""

    def __init__(
        self,
        store: Any,
        cfg: Config,
        *,
        providers: Callable[[], Mapping[str, Mapping[str, Any]]],
        presets: Callable[[], Sequence[ModelPreset]] = model_presets,
        id_factory: Callable[[], str] | None = None,
        clock_ms: Callable[[], int] | None = None,
        receipts: Any | None = None,
    ) -> None:
        self.store = store
        self.cfg = cfg
        self._providers = providers
        self._presets = presets
        self._id_factory = id_factory or (lambda: "mp-" + uuid.uuid4().hex[:8])
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._receipts_repo = receipts

    def effective_model_id(self, provider: Any, model: Any) -> str:
        explicit = str(model or "").strip()
        if explicit:
            return explicit
        provider_id = str(provider or "").strip().lower()
        spec = self._providers().get(provider_id, {})
        return str(spec.get("model") or self.cfg.llm.model or "default")

    # --- credentials -----------------------------------------------------
    PROFILE_SCOPE = "model_profile"

    def resolve_key(self, profile: Mapping[str, Any]) -> str:
        """See :func:`resolve_profile_key`."""
        return resolve_profile_key(self.store, profile)

    def credential(
        self,
        profile: Mapping[str, Any],
        configuration: Mapping[str, Any] | None = None,
    ) -> ProfileCredential:
        """The credential rule, in one place, for every caller that needs it.

        Readiness, both pin-writing branches of `bind_model_revision` and the
        pinned dispatch used to answer this three different ways: readiness
        accepted a loopback endpoint, the fresh bind checked nothing, and the
        bound bind and dispatch accepted only the profile's own stored key. So a
        profile that readiness called `ready` -- a keyless Ollama endpoint -- or
        one relying on `OPENAI4S_ARK_API_KEY` was pinned on the first send and
        refused at dispatch as "no longer usable", and the rebind that error
        asks for pinned the same profile again.

        In order:

        1. the profile's own key, brokered or legacy plaintext;
        2. a brokered key that no longer resolves is a refusal, not a fallback:
           the profile asked for *that* credential;
        3. keyless, when the endpoint is local by the rule `chat()` and
           `doctor` apply -- BEFORE any inherited key. A same-provider
           environment key is a cloud credential (`OPENAI_API_KEY` for the
           OpenAI-compatible protocol local discovery adds), and a local
           endpoint is plain http to a loopback, private or `.local` host. A
           local server that wants a key gets the one saved on its profile;
        4. a key the daemon's environment holds for the SAME provider -- never
           another provider's (see :func:`provider_env_api_key`).

        `configuration` is the revision being dispatched, when that is not the
        profile's current one: an inherited or keyless answer is about the
        provider and endpoint the request will actually reach.

        0. Before any of that, a revision whose provider or effective endpoint
           is not the profile's current one is refused
           (`CREDENTIAL_SCOPE_MISMATCH`). The key is shared across revisions
           and `edit()` stores a replacement while provider or base_url moves,
           so "the profile's own key" is the key entered for the configuration
           the profile names NOW. Returned first, it sent that key to a pinned
           older revision's endpoint -- the real OpenAI key to the third-party
           proxy the profile used to name, an Ark key to Anthropic. No
           environment fallback either: `OPENAI_API_KEY` to that same proxy is
           the same disclosure. A model-only revision keeps provider and
           endpoint, so it still dispatches; the rest are answered by
           `POST /frames/{id}/model-binding`.
        """
        if profile.get("deleted_at"):
            # Delete destroyed the key; an environment fallback must not bring
            # a tombstone back to life.
            return ProfileCredential("", CREDENTIAL_DELETED)
        if configuration is not None and self._credential_scope(
            configuration
        ) != self._credential_scope(profile):
            return ProfileCredential("", CREDENTIAL_SCOPE_MISMATCH)
        own = self.resolve_key(profile)
        if own:
            return ProfileCredential(own, CREDENTIAL_PROFILE)
        if is_ref(str(profile.get("api_key") or "")):
            return ProfileCredential("", CREDENTIAL_REVOKED)
        target = configuration if configuration is not None else profile
        provider = str(target.get("provider") or "").strip().lower()
        if self._keyless_endpoint(
            provider, str(target.get("model") or ""), str(target.get("base_url") or "")
        ):
            return ProfileCredential("", CREDENTIAL_LOCAL)
        inherited = self._provider_key(provider, self._credential_scope(target))
        if inherited:
            return ProfileCredential(inherited, CREDENTIAL_ENVIRONMENT)
        return ProfileCredential("", CREDENTIAL_MISSING)

    def _credential_scope(self, configuration: Mapping[str, Any]) -> tuple[str, str]:
        """`(provider, effective endpoint)`: where a request under it is sent.

        An empty base_url is resolved by the code that dispatches it, not by a
        copy of its rule: the pinned dispatch hands `base_url=None` to
        `LLMConfig.__post_init__`, which reads `OPENAI4S_<P>_BASE_URL`, then
        `OPENAI4S_LLM_BASE_URL`, and only then the protocol's default. Judged
        against the static default alone, a revision pinned to `""` while the
        environment named a proxy matched a revision spelling out the official
        endpoint, and the key entered for that endpoint went to the proxy. With
        no override, `""` and the default spelled out are still one endpoint;
        `normalize_endpoint` makes a trailing slash or a stripped query the same
        one too.
        """
        provider = str(configuration.get("provider") or "").strip().lower()
        endpoint = normalize_endpoint(str(configuration.get("base_url") or ""))
        if not endpoint:
            dispatched = provider or str(getattr(self.cfg.llm, "provider", "") or "")
            try:
                resolved = LLMConfig(
                    provider=dispatched, base_url="", model="unused", api_key="unused"
                ).base_url
            except Exception:  # noqa: BLE001 - fall back to the static default
                resolved = ""
            endpoint = normalize_endpoint(str(resolved or ""))
        if not endpoint:
            spec = self._providers().get(provider, {})
            endpoint = normalize_endpoint(str(spec.get("base_url") or ""))
        return provider, endpoint

    def destination_credential(self, configuration: Any) -> ProfileCredential | None:
        """What a request to exactly this destination may carry, if configured.

        `configuration` is the `LLMConfig` a delegated child would be
        dispatched under (read for `provider`, `base_url`, `model`) after an
        override moved it off its parent's destination. The parent's key was
        resolved for the parent's `(provider, endpoint)` and never follows.

        A destination is configured when a saved profile's current scope is
        that destination -- its `credential` answers, the first usable one
        winning, since each was entered for this very endpoint -- or when it is
        a registered provider's own endpoint or the daemon's configured
        endpoint, answered by the same rule for a configuration with no key of
        its own: keyless when local, then the key scoped to that endpoint.
        Anything else is
        `None`: no credential was ever entered for it, and the caller refuses
        rather than sending the request there at all.
        """
        provider = str(getattr(configuration, "provider", "") or "").strip().lower()
        target = {
            "provider": provider,
            "base_url": str(getattr(configuration, "base_url", "") or ""),
            "model": str(getattr(configuration, "model", "") or ""),
        }
        scope = self._credential_scope(target)
        if not provider or not scope[1]:
            return None
        found: ProfileCredential | None = None
        for profile in self.store.list_model_profiles():
            if profile.get("deleted_at") or self._credential_scope(profile) != scope:
                continue
            credential = self.credential(profile)
            if credential.usable:
                return credential
            found = found or credential
        provider_scope = self._credential_scope({"provider": provider, "base_url": ""})
        daemon_scope = self._credential_scope(
            {
                "provider": str(getattr(self.cfg.llm, "provider", "") or ""),
                "base_url": str(getattr(self.cfg.llm, "base_url", "") or ""),
            }
        )
        if provider in self._providers() and scope in {provider_scope, daemon_scope}:
            credential = self.credential(target)
            if credential.usable or found is None:
                return credential
        return found

    def user_key_applies(self, configuration: Any) -> bool:
        """Whether a member's own key (team mode, M4-1) may go out under this.

        `configuration` is the `LLMConfig` a turn is about to be dispatched
        under, read for `provider`, `base_url` and `model`. `SessionRunner.
        _apply_user_llm_key` asks this once per resolved agent or Reviewer
        configuration and is the only caller.

        A per-user key is stored against a provider *name* and nothing else, so
        the one endpoint it can be said to have been entered for is the one
        that name means: the base URL the registry gives the provider -- the
        vendor's own for a built-in, which registration cannot replace, and
        the registered one for a custom provider. `credential` already refuses
        to send a profile or environment key to an endpoint it was not entered
        for, and checks a keyless local server before any inherited key; a
        per-user key used to be swapped in afterwards by provider name alone,
        so a member's OpenAI key went to whatever `base_url` the pin named --
        plain http to a LAN Ollama, or an admin's third-party proxy. It is
        withheld when:

        1. the provider is not in the registry: there is no endpoint the key
           was entered for. `chat()` refuses such a provider too; this answer
           does not rely on it doing so;
        2. the request would reach any endpoint but that one, resolved the way
           `chat()` resolves it (`cfg.base_url or spec["base_url"]`, where
           `cfg.base_url` already carries `OPENAI4S_<P>_BASE_URL`): an admin's
           proxy, an operator's gateway, another region or path of the same
           vendor. Compared normalised, so a trailing slash or the host's case
           is still the same endpoint;
        3. that endpoint is plain http; or
        4. it is local by the rule `chat()` and `doctor` apply (loopback,
           private, link-local, `.local`) -- which only a custom provider
           registered at a local server can still reach past (2).

        Withheld is not refused. The configuration's own credential goes
        instead -- the profile's key, the same provider's environment key, or
        none for a keyless local server -- exactly as for a member with no key
        of their own, and the turn is metered to the member either way. The
        member never asked for their key to be sent to a proxy, so declining
        to send it there overrides nothing they chose; refusing would instead
        turn every admin-configured proxy or local model into an outage for
        precisely the members who had set a key.
        """
        provider = str(getattr(configuration, "provider", "") or "").strip().lower()
        spec = self._providers().get(provider) if provider else None
        if not spec:
            return False
        own = normalize_endpoint(str(spec.get("base_url") or ""))
        target = normalize_endpoint(
            str(getattr(configuration, "base_url", "") or "") or own
        )
        if not own or target != own:
            return False
        if urlsplit(target).scheme != "https":
            return False
        return not self._keyless_endpoint(
            provider, str(getattr(configuration, "model", "") or ""), target
        )

    def _provider_key(self, provider: str, target_scope: tuple[str, str]) -> str:
        if not provider:
            return ""
        # A provider-specific env key belongs to that provider's configured
        # endpoint, not every saved same-provider proxy profile.
        own_scope = self._credential_scope({"provider": provider, "base_url": ""})
        if target_scope == own_scope:
            key = provider_env_api_key(provider)
            if key:
                return key
        base = str(getattr(self.cfg.llm, "provider", "") or "").strip().lower()
        if provider != base:
            return ""
        # The daemon's resolved (possibly generic) key belongs to its own
        # endpoint. A saved profile at another same-provider endpoint must
        # supply its own key instead of borrowing the daemon's; delegated
        # children can select any saved profile, including an inactive one.
        daemon_scope = self._credential_scope(
            {"provider": base, "base_url": getattr(self.cfg.llm, "base_url", "")}
        )
        if target_scope != daemon_scope:
            return ""
        # The daemon's own provider: its resolved key (the only place the
        # generic `OPENAI4S_LLM_API_KEY` may apply), then an operator-injected
        # `llm` credential. Not the `llm_api_key` settings row itself: behind a
        # writable backend that row is a copy another profile's activation made.
        key = clean_api_key(getattr(self.cfg.llm, "api_key", ""))
        if key:
            return key
        return self._injected_llm_key()

    def _injected_llm_key(self) -> str:
        broker = getattr(self.store, "secrets", None)
        if broker is None or not getattr(broker, "read_only", False):
            return ""
        try:
            from openai4s.security.secret_broker import make_ref

            return clean_api_key(
                broker.get(
                    make_ref("llm", "llm_api_key", getattr(broker, "namespace", None))
                )
            )
        except Exception:  # noqa: BLE001 - an unreadable injection is an absent one
            return ""

    @staticmethod
    def _keyless_endpoint(provider: str, model: str, base_url: str) -> bool:
        return keyless_endpoint(provider, model, base_url)

    def _store_key(self, profile_id: str, key: str) -> str:
        """Put a profile's key behind a reference. Returns what to persist."""
        if not key:
            return ""
        ref = self.store.secrets.put(self.PROFILE_SCOPE, profile_id, key)
        if self.store.secrets.get(ref) != key:
            raise ModelProfileError(
                "could not store the API key securely; it was not saved", 500
            )
        return ref

    def _forget_key(self, profile: Mapping[str, Any]) -> None:
        raw = str(profile.get("api_key") or "")
        if is_ref(raw):
            try:
                self.store.secrets.delete(raw)
            except Exception:  # noqa: BLE001 - removing the row still matters
                pass

    def _receipts(self) -> Any | None:
        if self._receipts_repo is not None:
            return self._receipts_repo
        return getattr(self.store, "model_capability_receipts", None)

    def _receipt_identity(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """See :func:`openai4s.llm.capabilities.profile_receipt_identity`."""
        return profile_receipt_identity(profile, default_model=self.cfg.llm.model)

    def _current_profile(self, profile_id: str) -> Mapping[str, Any] | None:
        wanted = str(profile_id or "")
        for item in self.store.list_model_profiles():
            if str(item.get("id") or "") == wanted:
                return item
        return None

    def _receipt_matches_current_revision(self, identity: Mapping[str, Any]) -> bool:
        """F10 validity: the profile is current (not deleted) at this revision.

        A probe can outlive the configuration it started on: the user edits the
        profile while the request is in flight.  The receipt is still persisted
        for display, but it may only change the effective evidence when its
        revision is the profile's current revision; otherwise the next request
        must keep deciding from the configuration the profile names now.
        """
        profile = self._current_profile(str(identity.get("profile_id") or ""))
        if profile is None or profile.get("deleted_at"):
            return False
        return int(profile.get("revision") or 0) == int(identity.get("revision") or 0)

    def _valid_receipts(self) -> list[dict[str, Any]]:
        """Every receipt that may be effective evidence right now (F10).

        A receipt is valid when its profile is not deleted, its revision is
        that profile's current revision, and its probe version is current.
        """
        repo = self._receipts()
        if repo is None:
            return []
        current = {
            str(item.get("id") or ""): int(item.get("revision") or 0)
            for item in self.store.list_model_profiles()
            if not item.get("deleted_at")
        }
        valid: list[dict[str, Any]] = []
        for row in repo.list_all():
            if int(row.get("probe_version") or 0) != PROBE_VERSION:
                continue
            if current.get(str(row.get("profile_id") or "")) != int(
                row.get("revision") or 0
            ):
                continue
            valid.append(row)
        return valid

    def _receipt_keys_for_profile(self, profile_id: str) -> set[tuple[str, str, str]]:
        """Every overlay key this profile ever observed evidence for."""
        repo = self._receipts()
        if repo is None:
            return set()
        wanted = str(profile_id or "")
        keys: set[tuple[str, str, str]] = set()
        for row in repo.list_all():
            if str(row.get("profile_id") or "") != wanted:
                continue
            key = receipt_evidence_key(row)
            if key is not None:
                keys.add(key)
        return keys

    def _recompute_receipt_evidence(self, keys: set[tuple[str, str, str]]) -> None:
        """Re-select each affected key's effective evidence after a change.

        F10: the effective receipt for a key is the valid one (profile not
        deleted, revision current, probe version current) with the largest
        ``observed_at``, ties broken by the larger ``receipt_id``.  Deleting a
        profile -- or moving its configuration -- can invalidate the receipt
        that was adopted for a key while an older receipt from another current
        profile is still valid; re-selecting from the repository is what lets
        that survivor take over, exactly as a restart would read it.  With no
        survivor the key's overlay is removed rather than left promoted.
        """
        if not keys:
            return
        repo = self._receipts()
        if repo is None:
            return
        # A concurrent probe must publish either before this snapshot or after
        # its replacement, never between the read and reset of the overlay.
        with repo.locked():
            chosen: dict[tuple[str, str, str], dict[str, Any]] = {}
            for row in self._valid_receipts():
                key = receipt_evidence_key(row)
                if key is None or key not in keys:
                    continue
                best = chosen.get(key)
                if best is None or receipt_evidence_rank(row) > receipt_evidence_rank(
                    best
                ):
                    chosen[key] = row
            for key in keys:
                reset_receipt_overlay(key)
                survivor = chosen.get(key)
                if survivor is not None:
                    replace_receipt_overlay(survivor)

    def capability_receipt(self, profile: Mapping[str, Any]) -> dict[str, Any] | None:
        """The profile's exact probe receipt, or its stale predecessor.

        SQLite only, like ``public_profile``'s field, but without that
        projection's credential resolution: a Session package export wants the
        receipt and has no business touching the keychain for it.
        """
        return self._public_receipt(profile)

    def _public_receipt(self, profile: Mapping[str, Any]) -> dict[str, Any] | None:
        repo = self._receipts()
        if repo is None:
            return None
        identity = self._receipt_identity(profile)
        exact = None
        if identity["wire"]:
            exact = repo.get_exact(
                profile_id=identity["profile_id"],
                revision=identity["revision"],
                endpoint_sha256=identity["endpoint_sha256"],
                model=identity["model"],
                wire=identity["wire"],
                probe_version=PROBE_VERSION,
            )
        if exact is not None:
            return public_receipt(exact, stale=False, probe_version=PROBE_VERSION)
        latest = repo.latest_for_profile(identity["profile_id"])
        return public_receipt(latest, stale=True, probe_version=PROBE_VERSION)

    def effective_capabilities(
        self, profile: Mapping[str, Any]
    ) -> dict[str, dict[str, Any]] | None:
        """Read-only projection of what the next request would decide (B.3).

        ``explain_model_capabilities`` reports exactly ``tool_calling`` and
        ``streaming`` with a ``catalog`` / ``explicit_override`` / ``probe``
        source.  Never contacts anything.  ``None`` when the protocol is not
        one this build dispatches: such a profile has no effective capability
        to describe, and inventing one would be the guess this field exists to
        avoid.
        """
        try:
            return _explain_model_capabilities(
                str(profile.get("provider") or ""),
                self.effective_model_id(profile.get("provider"), profile.get("model")),
                base_url=str(profile.get("base_url") or "") or None,
            )
        except Exception:  # noqa: BLE001 - unsupported protocol has no decision
            return None

    def _persist_receipt(
        self,
        profile: Mapping[str, Any],
        *,
        reachable: bool,
        native_tool_call: str,
        streaming: str,
        observed_at: int | None = None,
    ) -> dict[str, Any] | None:
        """Persist one probe observation, then -- only then -- make it effective.

        The order is load-bearing: the durable write happens first, so a
        ``put`` failure leaves the previous effective capability untouched and
        the exception propagates to the caller.  After a successful write the
        profile's *current* revision is re-read: a receipt whose revision is no
        longer current is stored for display only, and only a current one goes
        through ``replace_receipt_overlay`` -- which adopts positive evidence and
        withdraws an older promotion when the new observation is non-positive
        (the P0-01 defect).
        """
        repo = self._receipts()
        identity = self._receipt_identity(profile)
        if repo is None or not identity["wire"] or not identity["profile_id"]:
            return None
        # Share the profile mutation lock through adoption: a revision check
        # alone leaves a window for deletion/edit to finish before installation.
        with repo.locked():
            row = repo.put(
                profile_id=identity["profile_id"],
                revision=identity["revision"],
                endpoint_sha256=identity["endpoint_sha256"],
                model=identity["model"],
                wire=identity["wire"],
                probe_version=PROBE_VERSION,
                reachable=reachable,
                native_tool_call=native_tool_call,
                streaming=streaming,
                observed_at=observed_at,
            )
            row["endpoint"] = identity["endpoint"]
            current = self._receipt_matches_current_revision(identity)
            if current:
                replace_receipt_overlay(row)
            return public_receipt(row, stale=not current, probe_version=PROBE_VERSION)

    def probe(
        self, profile_id: str, *, actor_user_id: str | None = None
    ) -> dict[str, Any]:
        """Contact the endpoint because a user asked, and record a receipt.

        Never called from a read path. `readiness` answers "is this configured"
        from local state alone precisely so that this — the only thing here
        that spends a request, a token allowance and a rate-limit slot — needs
        somebody to press a button.

        At most two tiny requests.  Tools are schema-validated and never
        executed.  ``true`` on the receipt is only a schema-valid native tool
        call or a fully terminated stream; timeout, auth failure, 5xx, and an
        uncooperative model are ``unknown``.
        """
        profile = next(
            (
                item
                for item in self.store.list_model_profiles()
                if item.get("id") == profile_id
            ),
            None,
        )
        if profile is None:
            raise ModelProfileError("profile not found", 404)

        local = self.readiness(profile)
        if local["state"] != "ready":
            # No request at all: a profile with no key cannot be probed, and
            # sending one anyway would produce a 401 that reads like an
            # endpoint problem rather than the missing credential it is.
            return {
                "reachable": False,
                "state": local["state"],
                "detail": local["detail"],
                "contacted": False,
                "outbound": 0,
                "tool_execution": 0,
                "capability_receipt": None,
                "effective_capabilities": self.effective_capabilities(profile),
            }

        provider = str(profile.get("provider") or "")
        model = self.effective_model_id(provider, profile.get("model"))
        base_url = str(profile.get("base_url") or "") or None
        credential = self.credential(profile)
        cfg = LLMConfig(
            provider=provider,
            api_key=credential.api_key,
            base_url=base_url,
            model=str(profile.get("model") or "") or None,
        )
        # `__post_init__` re-resolves an empty key from the environment, which
        # for a keyless local endpoint would send the daemon's generic key to it.
        cfg.api_key = credential.api_key
        outbound = 0
        native = EVIDENCE_UNKNOWN
        streaming = EVIDENCE_UNKNOWN
        reachable = False
        last_error: Exception | None = None
        public: dict[str, Any] = {}

        from openai4s.llm import chat as _chat

        def _meter(usage: Any) -> None:
            # A probe has no session to charge, but it does have the person who
            # pressed the button. `llm_probe_*` is outside ENFORCED_QUOTA_KINDS
            # on purpose: the spend shows up in /team/usage and can never refuse
            # anything. A probe able to close its own window would go dark
            # exactly when an operator is diagnosing what broke it.
            from openai4s.storage.governance import record_principal_llm_usage

            record_principal_llm_usage(
                self.store, actor_user_id, usage, prefix="llm_probe"
            )

        def _request(**kwargs: Any) -> dict[str, Any]:
            nonlocal outbound
            outbound += 1
            if outbound > 2:
                raise RuntimeError("capability probe exceeded two requests")
            try:
                reply = (
                    _chat(
                        kwargs.pop("messages"),
                        cfg,
                        **kwargs,
                    )
                    or {}
                )
            except BaseException as error:
                if not getattr(error, "llm_not_started", False):
                    _meter(getattr(error, "usage", None))
                raise
            _meter(reply.get("usage"))
            return reply

        # The observation is stamped when it *starts*, not when it finishes: a
        # delayed probe (a slow or retried endpoint) must not be able to
        # overwrite evidence a newer probe adopted while it was in flight.
        observed_at = self._clock_ms()
        identity = self._receipt_identity(profile)
        adapter_streaming = bool(identity["adapter_streaming"])
        if not adapter_streaming:
            # The adapter has no streaming transport.  That is stable
            # protocol-level evidence; it is not a timeout or a 5xx.
            streaming = EVIDENCE_FALSE

        try:
            with capability_probe_window(
                provider, model, base_url=base_url, tool_calling=True, streaming=False
            ):
                reply = _request(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "Call openai4s_capability_probe with ok=true. "
                                "Do nothing else."
                            ),
                        }
                    ],
                    max_tokens=32,
                    tools=[CAPABILITY_PROBE_TOOL],
                    tool_choice=(
                        None if provider == "ollama" else CAPABILITY_PROBE_TOOL["name"]
                    ),
                    parallel_tool_calls=False,
                )
            reachable = True
            calls = reply.get("tool_calls") or ()
            if any(
                tool_call_matches_schema(call, CAPABILITY_PROBE_TOOL) for call in calls
            ):
                native = EVIDENCE_TRUE
        except Exception as error:  # noqa: BLE001 - reported, never raised
            last_error = error
            native = classify_probe_error(error)
            from openai4s.server.errors import public_exception

            public, _status = public_exception(
                error, surface="model_profile:probe", error_code="probe_failed"
            )

        status = getattr(last_error, "status", None) if last_error else None
        stop_after_first = last_error is not None and (
            _network_failure(last_error)
            or status in (401, 403, 429)
            or (isinstance(status, int) and 500 <= status < 600)
        )
        if adapter_streaming and not stop_after_first:
            deltas: list[Any] = []

            def _on_delta(text: Any) -> None:
                deltas.append(text)

            try:
                with capability_probe_window(
                    provider,
                    model,
                    base_url=base_url,
                    tool_calling=False,
                    streaming=True,
                ):
                    reply = _request(
                        messages=[{"role": "user", "content": "Reply with pong."}],
                        max_tokens=8,
                        on_delta=_on_delta,
                    )
                reachable = True
                streaming = streaming_evidence(
                    deltas_seen=bool(deltas),
                    finish_reason=reply.get("finish_reason"),
                )
            except Exception as error:  # noqa: BLE001 - reported, never raised
                streaming = classify_probe_error(error)
                if last_error is None:
                    last_error = error
                    from openai4s.server.errors import public_exception

                    public, _status = public_exception(
                        error, surface="model_profile:probe", error_code="probe_failed"
                    )

        receipt = self._persist_receipt(
            profile,
            reachable=reachable,
            native_tool_call=native,
            streaming=streaming,
            observed_at=observed_at,
        )
        current = self._current_profile(str(profile.get("id") or ""))
        projection = (
            self.effective_capabilities(current) if current is not None else None
        )
        if last_error is not None and not reachable:
            return {
                "reachable": False,
                "state": "unreachable",
                "detail": _probe_detail(last_error, public),
                "code": public.get("code"),
                "request_id": public.get("request_id"),
                "contacted": True,
                "outbound": outbound,
                "tool_execution": 0,
                "capability_receipt": receipt,
                "effective_capabilities": projection,
            }
        return {
            "reachable": True,
            "state": "reachable",
            "detail": "the endpoint answered a minimal request",
            "contacted": True,
            "outbound": outbound,
            "tool_execution": 0,
            "capability_receipt": receipt,
            "effective_capabilities": projection,
        }

    def readiness(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """What can be said about this profile *without contacting anyone*.

        The distinction is the whole design. Everything here is derived from
        local state — is there a key, is the protocol one we can dispatch, is
        there an endpoint and a model — so opening Customize answers "is this
        usable" for every profile at zero network cost and with no chance of a
        page load spending someone's API quota or waking a rate limiter.

        Reachability is deliberately *not* here. It cannot be known without a
        request, and a request is a thing the user asks for (see `probe`). A
        readiness card that quietly probed on render would be exactly the
        implicit outbound call this version spent P0-1 removing.

        `state` is one of:
          ready        — everything local checks out; the endpoint is untested
          needs_key    — no credential resolves
          needs_model  — no model named and the protocol has no default
          unsupported  — the protocol is not one the LLM layer dispatches
        """
        provider = str(profile.get("provider") or "").strip().lower()
        problems: list[str] = []
        details: list[str] = []
        if provider not in PROFILE_PROTOCOLS:
            return {
                "state": "unsupported",
                "detail": f"{provider or 'no protocol'} is not a protocol this "
                "build can dispatch",
                "checked_endpoint": False,
            }
        credential = self.credential(profile)
        if not credential.usable:
            # The same rule bind and dispatch apply (see `credential`). A local
            # endpoint is the exception `resolve.py` already names: "demanding
            # an API key from them is demanding a credential that does not
            # exist". This card used to be the only surface honouring it, so a
            # profile it called `ready` could never run a turn.
            problems.append("needs_key")
            details.append(
                PROBLEM_DETAIL["credential_revoked"]
                if credential.source == CREDENTIAL_REVOKED
                else PROBLEM_DETAIL["needs_key"]
            )
        if not str(profile.get("model") or "").strip():
            spec = self._providers().get(provider, {})
            if not spec.get("model"):
                problems.append("needs_model")
                details.append(PROBLEM_DETAIL["needs_model"])
        if problems:
            # Prose, like the two states either side of this branch. `detail`
            # was the joined problem *codes*, which was fine while nothing
            # displayed it and became a card reading "needs_key" at a user the
            # moment one did. `state` is still the code a client branches on.
            return {
                "state": problems[0],
                "detail": "; ".join(details),
                "checked_endpoint": False,
            }
        return {
            "state": "ready",
            # Said out loud rather than implied. "Ready" here means the local
            # configuration is complete, not that anyone answered -- and a card
            # that let a user read the stronger claim into it would be the
            # confident-wrong-answer shape all over again.
            "detail": "configuration is complete; the endpoint has not been contacted",
            "checked_endpoint": False,
        }

    def public_profile(self, profile: Mapping[str, Any]) -> dict[str, Any]:
        """Return a profile projection that never includes the raw API key.

        ``has_api_key`` resolves rather than testing the field, so a profile
        whose keychain entry was revoked reports honestly instead of claiming a
        credential it can no longer produce.
        """
        return {
            "id": profile.get("id"),
            "name": profile.get("name") or "",
            "provider": profile.get("provider") or "",
            "base_url": profile.get("base_url") or "",
            "model": profile.get("model") or "",
            "has_api_key": bool(self.resolve_key(profile)),
            # Where the credential this profile is dispatched under comes from:
            # `profile`, `environment` (the same provider's key in the daemon's
            # environment), `local` (keyless local endpoint), `revoked` or
            # `missing`. `has_api_key` alone made an environment-keyed profile
            # read "No key" beside a `ready` card. Never the key itself.
            "credential_source": self.credential(profile).source,
            # Local-only readiness. Never a network call: see `readiness`.
            "readiness": self.readiness(profile),
            # The number a session binds to. Surfaced so a client can show
            # which configuration a session is pinned at, and tell "this is the
            # current one" from "this profile has moved on since".
            "revision": int(profile.get("revision") or 0) or None,
            # Exact probe receipt, or a stale prior one.  Never contacts the
            # endpoint: SQLite only.  This is the *observation*: it is not the
            # request decision.
            "capability_receipt": self._public_receipt(profile),
            # The request decision, per field, with the layer that supplied it
            # (`catalog` / `explicit_override` / `probe`).  Read-only and
            # resolved through the same function `client.chat` uses, so a badge
            # built from this cannot disagree with the next request.  Absent
            # detail here is honest: an undispatchable protocol has no decision.
            "effective_capabilities": self.effective_capabilities(profile),
        }

    def models_payload(self, default_model_id: str) -> dict[str, Any]:
        """Build the header selector from the live model and the saved profiles.

        Built-in provider defaults are deliberately absent. An endpoint the user
        never configured must not be selectable: picking it would only fail at
        send time for want of a key. A profile that leaves `model` blank still
        appears, resolved through its protocol's default.
        """
        live = self.store.get_setting("llm_model") or self.cfg.llm.model or "default"
        models: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def add(entry_id: Any, name: Any, description: Any, **extra: Any) -> None:
            normalized = str(entry_id or "").strip()
            if not normalized or normalized in seen_ids:
                return
            seen_ids.add(normalized)
            models.append(
                {
                    "id": normalized,
                    "name": str(name or normalized),
                    "description": str(description or ""),
                    **extra,
                }
            )

        add(
            live,
            live,
            f"{self.store.get_setting('llm_provider') or self.cfg.llm.provider} (当前)",
        )
        # One entry per profile, keyed on `profile_id`. This deduped on a
        # `seen: set[str]` of bare model *names*, so two profiles naming the same
        # model against different providers collapsed to one and the second
        # endpoint was simply unreachable from the selector -- and the value the
        # browser persisted was that bare name, which cannot say which profile
        # was meant. The model name is a display field; the identity is the id.
        for profile in self.store.list_model_profiles():
            if profile.get("deleted_at"):
                continue
            profile_id = str(profile.get("id") or "").strip()
            if not profile_id:
                continue
            model_id = self.effective_model_id(
                profile.get("provider"), profile.get("model")
            )
            provider = str(profile.get("provider") or "")
            base_url = str(profile.get("base_url") or "")
            add(
                profile_id,
                profile.get("name") or model_id or "profile",
                # Enough for a human to tell two same-named models apart.
                " · ".join(part for part in (provider, model_id, base_url) if part),
                profile_id=profile_id,
                model=model_id,
                provider=provider,
                base_url=base_url,
                # The revision a choice of this entry pins now. A session pinned
                # to an earlier one of the same profile shows the difference
                # instead of this entry, so choosing it is a change that re-pins.
                revision=int(profile.get("revision") or 0),
            )
        return {"models": {"default": models}, "default_model_id": default_model_id}

    def profiles_payload(self) -> tuple[dict[str, Any], str | None]:
        """Return saved profiles without materializing built-in endpoints.

        Older releases seeded the model catalog into every user's saved profile
        list. Remove rows matching those generated preset identities once,
        while preserving profiles with customized names, providers, or models.
        """
        if not self.store.get_setting("builtin_profiles_removed"):
            removed_ids: set[str] = set()
            if self.store.get_setting("builtin_profiles_seeded"):
                seeded_signatures = {
                    (preset.profile_name, preset.provider, preset.model)
                    for preset in self._presets()
                }

                def remove_seeded(profiles: list[dict[str, Any]]) -> None:
                    kept = []
                    for profile in profiles:
                        signature = (
                            str(profile.get("name") or ""),
                            str(profile.get("provider") or ""),
                            str(profile.get("model") or ""),
                        )
                        if signature in seeded_signatures:
                            removed_ids.add(str(profile.get("id") or ""))
                        else:
                            kept.append(profile)
                    profiles[:] = kept

                self.store.mutate_model_profiles(remove_seeded)
            active_id = self.store.get_setting("active_model_profile") or ""
            if active_id in removed_ids:
                self.store.set_setting("active_model_profile", "")
            self.store.set_setting("builtin_profiles_removed", "1")

        # Tombstoned profiles stay in the store so a session pinned to one keeps
        # its audit answer, but they are not offered anywhere a user chooses from.
        profiles = [
            profile
            for profile in self.store.list_model_profiles()
            if not profile.get("deleted_at")
        ]
        return (
            {
                "profiles": [self.public_profile(profile) for profile in profiles],
                "active_id": self.store.get_setting("active_model_profile") or "",
                "protocols": list(PROFILE_PROTOCOLS),
            },
            None,
        )

    #: The fields whose change is a different *configuration*, and therefore a
    #: new revision. Deliberately not `name`, and deliberately not `api_key`.
    #:
    #: A rename is a label change; a replayed session that reports the model it
    #: used should not claim a different one because someone tidied the list.
    #:
    #: The key is the load-bearing exclusion. `make_ref` derives the broker
    #: reference from `(scope, profile_id)` alone, so a revision that forked the
    #: profile id would fork the credential with it: rotating a key would strand
    #: earlier revisions on a secret nobody can read, and deleting any revision
    #: would destroy the key every other one still points at. Revisions share
    #: the profile id, which is also what D2 asks for -- a session binds
    #: `profile_id + revision`.
    REVISIONED_FIELDS = ("provider", "base_url", "model")

    @classmethod
    def _configuration(cls, profile: Mapping[str, Any]) -> tuple[str, ...]:
        return tuple(str(profile.get(field) or "") for field in cls.REVISIONED_FIELDS)

    @classmethod
    def _seal_revision(cls, profile: dict[str, Any], *, now_ms: int) -> int:
        """Append the profile's current configuration as a new revision.

        Append-only: an existing entry is never rewritten, because the entire
        point is that a session bound to revision 3 can still say what
        revision 3 was after the profile has moved on.

        Returns the revision number now current.
        """
        history = profile.get("revisions")
        if not isinstance(history, list):
            history = []
        current = cls._configuration(profile)
        previous = int(profile.get("revision") or 0)
        if history:
            last = history[-1]
            if (
                tuple(str(last.get(field) or "") for field in cls.REVISIONED_FIELDS)
                == current
            ):
                # Nothing that identifies the configuration changed, so this is
                # still the same revision. Editing a name repeatedly must not
                # produce a history of identical entries.
                return int(last.get("revision") or 1)
            revision = int(last.get("revision") or 0) + 1
        else:
            revision = 1
        if previous and revision != previous:
            drop_receipt_overlays(str(profile.get("id") or ""))
        entry = {
            "revision": revision,
            "created_at": now_ms,
            **{field: str(profile.get(field) or "") for field in cls.REVISIONED_FIELDS},
        }
        history.append(entry)
        profile["revisions"] = history
        profile["revision"] = revision
        return revision

    @classmethod
    def revision_config(
        cls, profile: Mapping[str, Any], revision: int
    ) -> dict[str, Any] | None:
        """The exact configuration a given revision named, or None if unknown.

        None is the 409 case: the profile exists but the revision it was bound
        to does not, which happens when a database predates the history or when
        a profile was rebuilt. Guessing the nearest revision would be the
        "silently follow latest" behaviour D2 exists to remove.
        """
        for entry in profile.get("revisions") or []:
            if int(entry.get("revision") or 0) == int(revision):
                return dict(entry)
        return None

    @staticmethod
    def _protocol(value: Any) -> str:
        protocol = str(value or "").strip().lower()
        if protocol not in PROFILE_PROTOCOLS:
            raise ModelProfileError(
                "protocol must be one of: " + ", ".join(PROFILE_PROTOCOLS)
            )
        return protocol

    def create(self, body: Mapping[str, Any]) -> dict[str, Any]:
        name = str(body.get("name") or "").strip()
        if not name:
            raise ModelProfileError("name required")
        profile_id = self._id_factory()
        profile = {
            "id": profile_id,
            "name": name,
            "provider": self._protocol(body.get("provider")),
            # Normalised and credential-free at the point of storage. Stored
            # raw, this string reached `GET /model-profiles` and an immutable
            # revision with whatever userinfo or query the user typed in it --
            # 7.2's "secrets do not enter the snapshot", violated through the
            # endpoint rather than the key.
            "base_url": normalize_endpoint(body.get("base_url")),
            "model": str(body.get("model") or "").strip(),
            # The blob records a reference; the key itself goes to the broker.
            "api_key": self._store_key(profile_id, clean_api_key(body.get("api_key"))),
        }
        self._seal_revision(profile, now_ms=self._clock_ms())
        self.store.mutate_model_profiles(lambda profiles: profiles.append(profile))
        return self.public_profile(profile)

    def activate(self, profile_id: str) -> tuple[dict[str, Any], str]:
        profile = next(
            (
                item
                for item in self.store.list_model_profiles()
                if item.get("id") == profile_id and not item.get("deleted_at")
            ),
            None,
        )
        if profile is None:
            # A tombstoned profile is `not found` here on purpose. Its row stays so
            # sessions pinned to it keep their audit answer, and its credential is
            # already gone -- activating it would copy an empty key into the live
            # settings and read as a working configuration.
            raise ModelProfileError("profile not found", 404)
        for field, setting in (
            ("provider", "llm_provider"),
            ("base_url", "llm_base_url"),
            ("model", "llm_model"),
        ):
            self.store.set_setting(setting, str(profile.get(field) or "").strip())
        # resolve_key, not the raw field: activating must copy the *key* into
        # llm_api_key, not the reference that stands for it.
        self.store.set_secret_setting(
            "llm_api_key", self.resolve_key(profile), scope="llm"
        )
        self.store.set_setting("active_model_profile", profile["id"])

        def to_front(profiles: list[dict[str, Any]]) -> None:
            index = next(
                (i for i, item in enumerate(profiles) if item.get("id") == profile_id),
                -1,
            )
            if index > 0:
                profiles.insert(0, profiles.pop(index))

        self.store.mutate_model_profiles(to_front)
        return (
            {"ok": True, "active_id": profile["id"]},
            self.effective_model_id(profile.get("provider"), profile.get("model")),
        )

    def edit(
        self, profile_id: str, body: Mapping[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        protocol = self._protocol(body["provider"]) if "provider" in body else None

        def mutate(profiles: list[dict[str, Any]]) -> dict[str, Any] | None:
            profile = next(
                (item for item in profiles if item.get("id") == profile_id), None
            )
            if profile is None:
                return None
            provider_changed = bool(
                protocol is not None
                and protocol != str(profile.get("provider") or "").strip().lower()
            )
            replacement_key = (
                clean_api_key(body.get("api_key")) if body.get("api_key") else ""
            )
            for field in ("name", "base_url", "model"):
                if field in body and body[field] is not None:
                    profile[field] = (
                        normalize_endpoint(body[field])
                        if field == "base_url"
                        else str(body[field]).strip()
                    )
            if protocol is not None:
                profile["provider"] = protocol
            # Credentials are provider-bound.  Keeping a Claude/OpenAI key
            # while changing only the protocol to Ark would make the next
            # activation (and the managed DataPro fallback) send that old key
            # to Volcengine.  A real replacement is the sole case where a
            # cross-provider edit may retain a configured credential.
            if provider_changed and not replacement_key:
                self._forget_key(profile)
                profile["api_key"] = ""
            if body.get("api_key"):
                self._forget_key(profile)
                profile["api_key"] = self._store_key(profile_id, replacement_key)
            if body.get("clear_api_key"):
                self._forget_key(profile)
                profile["api_key"] = ""
            # After the field writes, before the copy is returned: a no-op for
            # a rename or a key rotation, a new revision when the configuration
            # actually moved. Also backfills revision 1 for a profile written
            # before revisions existed, which is what lets an old session bind.
            self._seal_revision(profile, now_ms=self._clock_ms())
            return dict(profile)

        profile = self.store.mutate_model_profiles(mutate)
        if profile is None:
            raise ModelProfileError("profile not found", 404)
        # A configuration change invalidates this profile's earlier receipts;
        # another configuration sharing the endpoint may still own valid
        # evidence for the same key.  Re-select each affected key from the
        # repository with F10's rule so the in-process decision equals what a
        # restart would adopt from the same rows.
        self._recompute_receipt_evidence(self._receipt_keys_for_profile(profile_id))
        selected_model: str | None = None
        if self.store.get_setting("active_model_profile") == profile["id"]:
            for field, setting in (
                ("provider", "llm_provider"),
                ("base_url", "llm_base_url"),
                ("model", "llm_model"),
            ):
                self.store.set_setting(setting, str(profile.get(field) or ""))
            self.store.set_secret_setting(
                "llm_api_key", self.resolve_key(profile), scope="llm"
            )
            selected_model = self.effective_model_id(
                profile.get("provider"), profile.get("model")
            )
        return self.public_profile(profile), selected_model

    def delete(self, profile_id: str) -> None:
        """Tombstone the profile, keeping the revisions history depends on.

        This used to remove the row outright and then NULL the pin on every
        frame that named it. Both halves lost information that cannot be
        recovered: a pin is the audit answer to "what configuration did this
        session run under", and the revisions live in the row's own JSON blob, so
        deleting the row deleted the history of every session that ran on it. The
        justification given was that a session pinned to a deleted profile "must
        not be left unsendable" -- but the remedy for that already exists and is
        explicit: 409 `model_revision_unavailable` plus
        `POST /frames/{id}/model-binding`. Clearing the pin instead made the next
        send silently re-pin somewhere else, which is the silent substitution D2
        is about.

        The credential is still destroyed: a profile the user removed must not
        leave its key in the keychain. That makes the tombstone unbindable by
        construction as well as by the `deleted_at` check, since `bind_model_revision`
        now requires the key to resolve.
        """
        tombstoned: list[dict[str, Any]] = []
        now = int(time.time() * 1000)

        def mark(profiles: list[dict[str, Any]]) -> None:
            for profile in profiles:
                if profile.get("id") != profile_id or profile.get("deleted_at"):
                    continue
                tombstoned.append(dict(profile))
                profile["deleted_at"] = now
                # The key goes; the identity, the revisions and the non-secret
                # provider/endpoint/model fields stay so history reads correctly.
                profile["api_key"] = ""

        self.store.mutate_model_profiles(mark)
        for profile in tombstoned:
            self._forget_key(profile)
        if tombstoned:
            # Its receipts stay for audit, but a deleted configuration's probe
            # must not keep promoting capability.  Re-select each key it had
            # observed from the surviving current configurations with F10's
            # rule; that is how an older receipt from a profile still sharing
            # the endpoint becomes effective again, exactly as a restart reads
            # it.
            self._recompute_receipt_evidence(self._receipt_keys_for_profile(profile_id))
        if self.store.get_setting("active_model_profile") == profile_id:
            self.store.set_setting("active_model_profile", "")
            # The live value is a provider credential copied by activate().
            # Tombstoning its owner must not leave that copy available to the
            # LLM transport or the Ark-backed managed connectors.
            self.store.set_secret_setting("llm_api_key", "", scope="llm")

    def migrate_profile_keys(self) -> dict:
        """Move any plaintext profile key behind a reference.

        Same ordering as every other credential migration: write, verify by
        reading back, and only then replace the field. A profile whose key
        cannot be stored keeps its plaintext and keeps working.
        """
        migrated: list[str] = []
        reentry_required: list[str] = []
        failed: list[dict] = []

        def convert(profiles: list[dict[str, Any]]) -> None:
            for profile in profiles:
                raw = str(profile.get("api_key") or "")
                profile_id = str(profile.get("id") or "")
                if not raw:
                    continue
                if is_ref(raw):
                    # `describe` reaches the secret backend: a locked keychain
                    # times out, a hand-edited ref fails to parse. Outside a
                    # guard, one such profile aborted the whole conversion and
                    # left every later profile's key in plaintext.
                    try:
                        description = self.store.secrets.describe(raw)
                    except Exception as e:  # noqa: BLE001 - one bad profile must
                        # not strand the others; its ref stays as it is.
                        if profile_id:
                            failed.append({"id": profile_id, "error": type(e).__name__})
                        continue
                    if profile_id and (
                        description["reentry_required"] or not description["configured"]
                    ):
                        reentry_required.append(profile_id)
                    continue
                if not profile_id:
                    continue
                try:
                    profile["api_key"] = self._store_key(profile_id, raw)
                    migrated.append(profile_id)
                except Exception as e:  # noqa: BLE001 - one bad key must not
                    # strand the others, and the plaintext stays authoritative.
                    failed.append({"id": profile_id, "error": type(e).__name__})

        self.store.mutate_model_profiles(convert)
        return {
            "migrated": migrated,
            "reentry_required": reentry_required,
            "failed": failed,
        }


def migrate_provider_alias(
    store: Any,
    providers: Mapping[str, Mapping[str, Any]],
    *,
    old: str,
    new: str,
) -> None:
    """Idempotently rewrite a retired provider identity in settings/profiles."""
    new_spec = providers.get(new)
    if new_spec is None:
        raise ValueError(f"unknown replacement provider {new!r}")
    base_url = str(new_spec.get("base_url") or "")
    if str(store.get_setting("llm_provider") or "").strip() == old:
        store.set_setting("llm_provider", new)
        if not str(store.get_setting("llm_base_url") or "").strip():
            store.set_setting("llm_base_url", base_url)

    now_ms = int(time.time() * 1000)

    def migrate(profiles: list[dict[str, Any]]) -> None:
        for profile in profiles:
            if str(profile.get("provider") or "").strip() == old:
                profile["provider"] = new
                if not str(profile.get("base_url") or "").strip():
                    profile["base_url"] = base_url
                # Both of those are `REVISIONED_FIELDS`, so this is a
                # configuration change and has to seal one. It did not, and
                # this runs at every daemon boot: a session pinned to the old
                # revision kept dispatching with the retired provider id, which
                # `provider_spec` rejects -- so the turn died with
                # `unknown provider` instead of the 409 rebind that exists for
                # exactly the case "the configuration you were pinned to is
                # gone". `_seal_revision` is append-only, so the old entry
                # still says what it said.
                ModelProfileService._seal_revision(profile, now_ms=now_ms)

    store.mutate_model_profiles(migrate)
    loader = getattr(store, "_load_capability_receipt_overlays", None)
    if callable(loader):
        # The alias rewrite seals a new revision for every touched profile, so
        # the receipts adopted at Store open now describe retired revisions.
        loader()


__all__ = [
    "ModelProfileError",
    "ModelProfileService",
    "PROFILE_PROTOCOLS",
    "clean_api_key",
    "migrate_provider_alias",
]
