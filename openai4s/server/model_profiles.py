"""Model-provider profile lifecycle kept out of the HTTP gateway facade."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from openai4s.config import Config, LLMConfig, is_placeholder_api_key
from openai4s.endpoint_identity import normalize_endpoint
from openai4s.llm.catalog import ModelPreset, model_presets
from openai4s.llm.resolve import is_loopback_endpoint
from openai4s.security.secret_broker import is_ref

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
)

#: Registry providers deliberately not offered as a profile choice, each with a
#: reason. Empty today.
WITHHELD_PROTOCOLS: dict[str, str] = {}

#: What each readiness problem means, in the words a user reads. The `state`
#: code stays the thing a client branches on; this is only the sentence.
PROBLEM_DETAIL = {
    "needs_key": "no credential resolves for this profile",
    "needs_model": "no model is named and this protocol has no default",
}


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
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return (
            "the endpoint could not be reached; check the base URL and this "
            "machine's network access"
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
    ) -> None:
        self.store = store
        self.cfg = cfg
        self._providers = providers
        self._presets = presets
        self._id_factory = id_factory or (lambda: "mp-" + uuid.uuid4().hex[:8])
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

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

    def probe(self, profile_id: str) -> dict[str, Any]:
        """Contact the endpoint, once, because a user asked.

        Never called from a read path. `readiness` answers "is this configured"
        from local state alone precisely so that this — the only thing here
        that spends a request, a token allowance and a rate-limit slot — needs
        somebody to press a button.

        Reports what happened rather than a verdict. "ok" means the endpoint
        answered a minimal request; it does not mean the model is good, the
        quota is sufficient, or that a later call will succeed, and phrasing it
        as `reachable` rather than `verified` keeps that difference visible.
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
            }

        provider = str(profile.get("provider") or "")
        try:
            from openai4s.llm import chat

            chat(
                [{"role": "user", "content": "ping"}],
                LLMConfig(
                    provider=provider,
                    api_key=self.resolve_key(profile),
                    base_url=str(profile.get("base_url") or "") or None,
                    model=str(profile.get("model") or "") or None,
                ),
                max_tokens=1,
            )
        except Exception as error:  # noqa: BLE001 - reported, never raised
            from openai4s.server.errors import public_exception

            public, _status = public_exception(
                error, surface="model_profile:probe", error_code="probe_failed"
            )
            return {
                "reachable": False,
                "state": "unreachable",
                "detail": _probe_detail(error, public),
                "code": public.get("code"),
                "request_id": public.get("request_id"),
                "contacted": True,
            }
        return {
            "reachable": True,
            "state": "reachable",
            "detail": "the endpoint answered a minimal request",
            "contacted": True,
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
        if provider not in PROFILE_PROTOCOLS:
            return {
                "state": "unsupported",
                "detail": f"{provider or 'no protocol'} is not a protocol this "
                "build can dispatch",
                "checked_endpoint": False,
            }
        if not self.resolve_key(profile) and not is_loopback_endpoint(
            str(profile.get("base_url") or "")
        ):
            # A loopback endpoint is the exception `resolve.py` already names:
            # "demanding an API key from them is demanding a credential that
            # does not exist". `chat()` honours it -- a keyless request is
            # permitted when the endpoint is local -- and `doctor` honours it.
            # This surface did not, so a working Ollama or LM Studio profile
            # was reported `needs_key` and could not be probed at all. The UI
            # then hand-rolled its own loopback check for the badge while still
            # rendering the warning above it, which is what a rule wired to one
            # of three call sites looks like from the outside.
            problems.append("needs_key")
        if not str(profile.get("model") or "").strip():
            spec = self._providers().get(provider, {})
            if not spec.get("model"):
                problems.append("needs_model")
        if problems:
            # Prose, like the two states either side of this branch. `detail`
            # was the joined problem *codes*, which was fine while nothing
            # displayed it and became a card reading "needs_key" at a user the
            # moment one did. `state` is still the code a client branches on.
            return {
                "state": problems[0],
                "detail": "; ".join(
                    PROBLEM_DETAIL.get(item, item) for item in problems
                ),
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
            # Local-only readiness. Never a network call: see `readiness`.
            "readiness": self.readiness(profile),
            # The number a session binds to. Surfaced so a client can show
            # which configuration a session is pinned at, and tell "this is the
            # current one" from "this profile has moved on since".
            "revision": int(profile.get("revision") or 0) or None,
        }

    def models_payload(self, default_model_id: str) -> dict[str, Any]:
        """Build the header selector from the live model and the saved profiles.

        Built-in provider defaults are deliberately absent. An endpoint the user
        never configured must not be selectable: picking it would only fail at
        send time for want of a key. A profile that leaves `model` blank still
        appears, resolved through its protocol's default.
        """
        live = self.store.get_setting("llm_model") or self.cfg.llm.model or "default"
        models: list[dict[str, str]] = []
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
                            failed.append({"id": profile_id, "error": str(e)[:200]})
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
                    failed.append({"id": profile_id, "error": str(e)[:200]})

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


__all__ = [
    "ModelProfileError",
    "ModelProfileService",
    "PROFILE_PROTOCOLS",
    "clean_api_key",
    "migrate_provider_alias",
]
