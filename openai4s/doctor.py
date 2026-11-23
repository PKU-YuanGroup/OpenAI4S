"""One command that answers "is this installation able to do the work?"

Every probe here already existed, each behind a separate HTTP route or import,
which meant the person best placed to need them — someone whose daemon will not
start, or whose first run failed — had no single thing to run and nothing
coherent to paste into a report.

Three properties make it useful rather than decorative:

* **It runs without the daemon.** A check that needs the server is unavailable
  in exactly the situation that motivates running it. Everything below reads
  config, the filesystem, and the store directly.
* **It never fails the process on a warning.** `warn` means degraded but
  usable; only `fail` means the work cannot proceed. Conflating them would
  train people to ignore the output.
* **It reports no secret values.** Whether a credential is configured is a
  diagnostic; the credential is not. The same rule the diagnostics bundle and
  the connector API already follow.
"""

from __future__ import annotations

import os
import shutil
import stat
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

OK = "ok"
WARN = "warn"
FAIL = "fail"


def _sanitize_endpoint(url: str) -> str:
    """A URL safe to print in a diagnostic that gets pasted into a bug report.

    A local endpoint can embed credentials — ``http://user:pass@127.0.0.1`` or
    a token in the query — and this report is meant to be shared. Keep the
    scheme, host, port and path (the routing detail a reader needs); drop the
    userinfo and query entirely.
    """
    if not url:
        return url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "<unparseable endpoint>"
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    cleaned = urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))
    if parts.query or parts.username or parts.password:
        cleaned = (cleaned or url.split("?", 1)[0]) + " (credentials redacted)"
    return cleaned or url


#: Below this, a scientific workload is likely to fail partway — which is worse
#: than refusing up front, because it fails after the expensive part.
_LOW_DISK_GB = 2.0

#: The one instruction for "the managed environments are missing". The startup
#: banner, the wizard and Settings each used to give a different one.
MISSING_ENVIRONMENTS_REMEDY = (
    "Run `openai4s setup` to build the managed Python + R environments; "
    "install single packages in Settings → Compute."
)

#: The API host each built-in science database is queried at, for
#: `openai4s doctor --network`. `tests/test_doctor.py` holds this to the
#: connector sources, so a connector that moves host cannot leave the probe
#: checking the old one.
SCIENCE_HOSTS: dict[str, str] = {
    "zenodo": "zenodo.org",
    "uniprot": "rest.uniprot.org",
    "pdb": "search.rcsb.org",
    "ensembl": "rest.ensembl.org",
    "chembl": "www.ebi.ac.uk",
    "pubchem": "pubchem.ncbi.nlm.nih.gov",
    "arxiv": "export.arxiv.org",
    "openalex": "api.openalex.org",
    "string": "string-db.org",
    "bindingdb": "www.bindingdb.org",
}
_PROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class _Said:
    """One sentence in both languages the CLI speaks (CLI-35)."""

    en: str
    zh: str

    def __add__(self, other: _Said) -> _Said:
        return _Said(self.en + other.en, self.zh + other.zh)

    def replace(self, old: _Said, new: _Said) -> _Said:
        return _Said(self.en.replace(old.en, new.en), self.zh.replace(old.zh, new.zh))


def _same(text: str) -> _Said:
    """Text that reads the same in both: a value, or prose raised elsewhere."""
    return _Said(text, text)


def _say(key: str, **params: Any) -> _Said:
    """Sentence ``key`` of `_COPY` in both languages, filled with ``params``.

    A `_Said` parameter is filled in each language's own words; any other
    value (a path, a provider id, an exception) is the same in both.
    """

    def fill(language: str) -> str:
        values = {
            name: getattr(value, language) if isinstance(value, _Said) else value
            for name, value in params.items()
        }
        return _COPY[language][key].format(**values)

    return _Said(fill("en"), fill("zh"))


@dataclass
class Check:
    """One probe's verdict."""

    name: str
    status: str
    detail: str
    #: What to do about it. Empty when there is nothing to do.
    remedy: str = ""
    #: Non-sensitive supporting facts, for the JSON form.
    facts: dict[str, Any] = field(default_factory=dict)
    #: ``detail`` and ``remedy`` in Chinese, for the CLI's human form when it
    #: speaks Chinese (`report(language="zh")`). Empty falls back to English.
    #: The English pair stays the JSON contract whatever the CLI speaks.
    zh_detail: str = ""
    zh_remedy: str = ""

    def said(self) -> tuple[_Said, _Said]:
        """``detail`` and ``remedy``, each in both languages."""
        return (
            _Said(self.detail, self.zh_detail or self.detail),
            _Said(self.remedy, self.zh_remedy or self.remedy),
        )

    def public(self, language: str = "en") -> dict[str, Any]:
        detail, remedy = self.said()
        zh = language == "zh"
        return {
            "name": self.name,
            "status": self.status,
            "detail": detail.zh if zh else detail.en,
            "remedy": remedy.zh if zh else remedy.en,
            "facts": self.facts,
        }


def _check(
    name: str,
    status: str,
    detail: _Said,
    remedy: _Said | None = None,
    facts: dict[str, Any] | None = None,
) -> Check:
    """A `Check` said in both languages; its JSON form is the English one."""
    remedy = remedy or _same("")
    return Check(
        name,
        status,
        detail.en,
        remedy.en,
        facts if facts is not None else {},
        zh_detail=detail.zh,
        zh_remedy=remedy.zh,
    )


def unopened_database(cfg: Any) -> dict[str, Any] | None:
    """Why a diagnosis must leave the database closed, or None when it may open it.

    Opening the Store is not a read. On a database older than this release it
    *is* the upgrade: a backup, the migrations, and the deletion of that backup
    once they commit -- irreversible by reinstalling the release that wrote the
    data, and meant to happen after the operator has made a copy. doctor used
    to open it from the model and connectors checks, so a diagnosis migrated a
    healthy older database in place, and an upgrade that failed was attempted,
    rolled back and reported as `[ok] data` with the error left in connector
    facts. doctor diagnoses; `serve` and `run` upgrade.

    So every probe that would open the Store asks this first -- doctor's
    model and connectors checks, and the schema and secret-store probes of the
    ``openai4s diagnostics`` bundle, which did the same. The version is read
    the read-only way ``serve`` and ``run`` preflight it; only a database
    already at this release's schema -- or none yet, which opening creates
    rather than upgrades -- is opened. A version that cannot be read without a
    read-write open (a hot journal) is left closed too: the open that recovers
    it would also upgrade it if it is older. So is one that could not be read
    at all; that refusal is returned under ``error`` for the caller to report.
    """
    db_path = getattr(cfg, "db_path", None)
    if db_path is None:
        return None
    path = Path(db_path)
    try:
        if path.stat().st_size == 0:
            return None
    except OSError:
        return None
    from openai4s.storage import migrations

    try:
        version = migrations.preflight_schema(path)
    except migrations.FutureSchemaError as e:
        return {
            "reason": "future_schema",
            "schema_version": e.actual_version,
            "supported_schema_version": e.supported_version,
            "error": e,
        }
    except Exception as e:  # noqa: BLE001 - unreadable is the data check's finding
        # Not only a corrupt file, which no open could upgrade: SQLITE_BUSY
        # past the timeout (an older daemon mid-commit) is an OperationalError
        # too, and the read-write open that followed it could get the lock and
        # run the upgrade.
        return {"reason": "unreadable", "error": e}
    if version is None:
        return {"reason": "interrupted_write"}
    if version < migrations.SCHEMA_VERSION:
        return {
            "reason": "upgrade_pending",
            "schema_version": version,
            "supported_schema_version": migrations.SCHEMA_VERSION,
        }
    return None


def _store_for(cfg: Any) -> Any:
    """The store, or None when it cannot or must not be opened.

    Several checks need to see what the UI configured, and none of them may
    fail because the database is missing — a fresh install is exactly when this
    command gets run. A database `unopened_database` names is never opened.
    """
    if unopened_database(cfg) is not None:
        return None
    try:
        from openai4s.store import get_store

        return get_store(cfg.db_path)
    except Exception:  # noqa: BLE001
        return None


def _model_source(
    provider: str, model: str, default_model: str, store: Any
) -> tuple[str, _Said | None, str]:
    """Where the reported model id came from: (source, note, overridden variable).

    A turn reads the settings saved by Settings -> Models or `openai4s init`
    before it reads the environment, so a model saved once keeps winning over
    an `OPENAI4S_LLM_MODEL` edited afterwards. The report named the model and
    not its origin, which left "I changed `.env` and nothing happened" with no
    explanation anywhere.
    """
    key = str(provider or "").strip().upper().replace("-", "_")
    env_name = env_value = ""
    for name in (f"OPENAI4S_{key}_MODEL", "OPENAI4S_LLM_MODEL"):
        value = (os.environ.get(name) or "").strip()
        if value:
            env_name, env_value = name, value
            break
    saved = ""
    if store is not None:
        try:
            saved = str(store.get_setting("llm_model") or "").strip()
        except Exception:  # noqa: BLE001 - an unreadable setting names no source
            saved = ""
    if saved and saved == model:
        note = _say("model.source.settings")
        if env_value and env_value != saved:
            return (
                "settings",
                _say(
                    "model.source.settings_overrides",
                    note=note,
                    env_name=env_name,
                    env_value=env_value,
                ),
                env_name,
            )
        return "settings", note, ""
    if env_value and env_value == model:
        return "environment", _say("model.source.environment", env_name=env_name), ""
    if model and model == default_model:
        return "default", _say("model.source.default"), ""
    return "config", None, ""


def _model(cfg: Any) -> Check:
    """Can we reach a model at all? Configuration only — no network call.

    Resolved the way a real turn resolves it: process config **plus** the
    Settings → Models settings held in the store. Reading `cfg.llm` alone
    diagnosed the documented setup path as broken — the daemon boots with no
    key and the model is configured from the UI — so an install that worked
    was told its model check had failed.
    """
    from openai4s.llm.resolve import is_loopback_endpoint, resolve_llm_config

    unopened = unopened_database(cfg)
    store = None if unopened else _store_for(cfg)
    llm = resolve_llm_config(cfg.llm, store)
    try:
        from openai4s.llm.registry import provider_spec

        spec = provider_spec(llm.provider)
    except Exception as e:  # noqa: BLE001 - an unknown provider is the finding
        return _check(
            "model",
            FAIL,
            _say("model.unknown_provider", provider=llm.provider, error=e),
            _say("model.unknown_provider.remedy"),
        )
    model = llm.model or spec.get("model")
    base_url = getattr(llm, "base_url", "") or ""
    # Ask the same question the client asks. The request path allows a keyless
    # call when `get_model_capabilities(...).local_endpoint` is true, which
    # covers `host.docker.internal`, `.local` hosts, and private/link-local
    # addresses — not only literal loopback. Using the narrower loopback rule
    # here reported a working keyless setup (Ollama behind `host.docker.
    # internal`, a `.local` box) as a model failure.
    keyless = is_loopback_endpoint(base_url)
    try:
        from openai4s.llm.capabilities import get_model_capabilities

        keyless = bool(
            get_model_capabilities(
                llm.provider, model, base_url=base_url or spec.get("base_url")
            ).local_endpoint
        )
    except Exception:  # noqa: BLE001 - fall back to the loopback rule
        pass
    source, source_note, overridden = _model_source(
        llm.provider, str(model or ""), str(spec.get("model") or ""), store
    )
    named = (
        _say("model.named_note", model=model, note=source_note)
        if source_note
        else _say("model.named", model=model)
    )
    facts = {
        "provider": llm.provider,
        "model": model,
        "model_source": source,
        "api_key_configured": bool(llm.api_key),
        "endpoint_is_local": keyless,
    }
    if overridden:
        facts["model_overrides_env"] = overridden
    if unopened is not None:
        facts["database_settings_read"] = False
    # The value is never reported, only whether one resolved.
    if not llm.api_key and not keyless and unopened is not None:
        # The key may be saved under Settings -> Models, in the database this
        # run left closed; "no key" would be a guess reported as a failure.
        return _check(
            "model",
            WARN,
            _say("model.no_key_db_closed", provider=llm.provider, model=model),
            _say("model.no_key_db_closed.remedy", upper=llm.provider.upper()),
            facts,
        )
    if not llm.api_key and not keyless:
        return _check(
            "model",
            FAIL,
            _say("model.no_key", provider=llm.provider, model=model),
            _say("model.no_key.remedy", upper=llm.provider.upper()),
            facts,
        )
    if keyless and not llm.api_key:
        # Ollama, LM Studio, vLLM and llama.cpp authenticate by being
        # unreachable from anywhere else. Demanding a key from them is
        # demanding a credential that does not exist.
        return _check(
            "model",
            OK,
            _say(
                "model.local",
                provider=llm.provider,
                named=named,
                endpoint=_sanitize_endpoint(base_url),
            ),
            facts=facts,
        )
    return _check(
        "model",
        OK,
        _say(
            "model.configured",
            provider=llm.provider,
            named=named,
            credential=_unverified_credential(),
        ),
        facts=facts,
    )


def _unverified_credential() -> _Said:
    """What the model line says while nothing has asked the provider.

    A configured key is not a working key, and "credential configured" beside
    "All checks passed" read as if it were. `_verify_model` swaps this phrase
    for what the provider answered.
    """
    return _say("model.credential_configured", verified=_say("model.not_verified"))


#: Ceilings for the one test request `report(live_model=...)` sends. A check
#: someone runs because things are broken must not hang on a dead network.
_LIVE_REQUEST_TIMEOUT_S = 15.0
_LIVE_TOTAL_TIMEOUT_S = 20.0


def send_test_request(llm: Any) -> None:
    """Send one tiny authenticated chat request; raise what the transport raises.

    The only network call this module can make, and only when a caller hands
    it to `report(live_model=...)`: the CLI does, the web Diagnostics route
    does not. One send, no retry budget, a short deadline.
    """
    import dataclasses

    from openai4s import llm as llm_api

    probe = dataclasses.replace(
        llm,
        timeout_s=min(float(llm.timeout_s), _LIVE_REQUEST_TIMEOUT_S),
        total_timeout_s=_LIVE_TOTAL_TIMEOUT_S,
        max_retries=0,
        retry_budget_s=0,
    )
    llm_api.chat(
        [{"role": "user", "content": "Reply with the single word: ok"}],
        probe,
        max_tokens=16,
    )


def _live_request_forbidden(cfg: Any) -> _Said | None:
    """Why no test request may be sent, or None when one may."""
    from openai4s import webtools

    if not webtools.network_allowed():
        return _same("OPENAI4S_ALLOW_NETWORK=0")
    store = _store_for(cfg)
    try:
        if store is not None and store.get_setting("network_enabled") == "0":
            return _say("model.network_switched_off")
    except Exception:  # noqa: BLE001 - an unreadable toggle forbids nothing
        pass
    return None


def _verify_model(cfg: Any, check: Check, send: Callable[[Any], None]) -> Check:
    """The model check, after asking the provider once whether it is true.

    Only a check that already passed on configuration and resolved an API key
    is tested: a failed one has said what is wrong, and a keyless local
    endpoint has no credential to verify. Nothing from the provider's reply is
    repeated -- only the HTTP status -- so the report stays safe to paste.
    """
    if check.status != OK or not check.facts.get("api_key_configured"):
        return check
    facts = dict(check.facts)
    detail, _remedy = check.said()
    forbidden = _live_request_forbidden(cfg)
    if forbidden is not None:
        facts["live_check"] = "skipped"
        return _check(
            "model",
            OK,
            detail.replace(
                _say("model.not_verified"),
                _say("model.not_verified_offline", why=forbidden),
            ),
            facts=facts,
        )
    from openai4s.llm.resolve import resolve_llm_config

    unopened = unopened_database(cfg)
    llm = resolve_llm_config(cfg.llm, None if unopened else _store_for(cfg))
    provider = facts.get("provider")
    model = facts.get("model")
    endpoint = _sanitize_endpoint(getattr(llm, "base_url", "") or "")
    try:
        send(llm)
    except Exception as e:  # noqa: BLE001 - every outcome is a finding
        status = getattr(e, "status", None)
        if isinstance(status, bool) or not isinstance(status, int):
            status = None
        facts["live_status"] = status
        if status in (401, 403):
            facts["live_check"] = "auth_failed"
            return _check(
                "model",
                FAIL,
                _say(
                    "model.auth_failed",
                    provider=provider,
                    status=status,
                    endpoint=endpoint,
                ),
                _say("model.auth_failed.remedy", upper=str(provider).upper()),
                facts,
            )
        if status == 404:
            facts["live_check"] = "model_not_found"
            return _check(
                "model",
                FAIL,
                _say(
                    "model.not_found", provider=provider, model=model, endpoint=endpoint
                ),
                _say("model.not_found.remedy"),
                facts,
            )
        if status is not None:
            facts["live_check"] = "inconclusive"
            return _check(
                "model",
                WARN,
                _say(
                    "model.inconclusive_status",
                    provider=provider,
                    status=status,
                    model=model,
                ),
                _say("model.inconclusive_status.remedy"),
                facts,
            )
        from openai4s.llm.models import LLMDeadlineExceeded, TransportError

        if isinstance(e, TransportError):
            facts["live_check"] = "unreachable"
            how = _say(
                "model.timed_out"
                if isinstance(e, LLMDeadlineExceeded)
                else "model.connection_failed"
            )
            return _check(
                "model",
                WARN,
                _say(
                    "model.unreachable",
                    endpoint=endpoint,
                    how=how,
                    provider=provider,
                    model=model,
                ),
                _say("model.unreachable.remedy"),
                facts,
            )
        facts["live_check"] = "inconclusive"
        return _check(
            "model",
            WARN,
            _say(
                "model.incomplete",
                provider=provider,
                model=model,
                error_type=type(e).__name__,
            ),
            _say("model.incomplete.remedy"),
            facts,
        )
    facts["live_check"] = "ok"
    return _check(
        "model",
        OK,
        detail.replace(_unverified_credential(), _say("model.credential_verified")),
        facts=facts,
    )


def credential_missing(cfg: Any) -> bool:
    """Whether the configured model needs an API key and none is configured.

    The model check's own answer, for `openai4s status`: a model name alone
    read as "ready". ``False`` whenever that cannot be told (the settings
    database was not opened, or the check itself failed) -- no claim.
    """
    try:
        facts = _model(cfg).facts
    except Exception:  # noqa: BLE001 - status must not fail on a diagnosis
        return False
    return (
        facts.get("api_key_configured") is False
        and not facts.get("endpoint_is_local")
        and facts.get("database_settings_read", True)
    )


def _runtime(cfg: Any) -> Check:
    """Is there an interpreter for the scientific execution plane?"""
    import sys

    facts: dict[str, Any] = {"daemon_python": sys.version.split()[0]}
    roadmap = getattr(cfg, "roadmap_features", None)
    if bool(getattr(roadmap, "stage1_trusted_delivery", False)):
        from openai4s.kernel.readiness import (
            readiness_failure_message,
            standard_profile_readiness,
        )

        readiness = standard_profile_readiness(enabled=True)
        facts["standard_profile_readiness"] = readiness
        if readiness.get("ready") is True:
            return _check("runtime", OK, _say("runtime.standard_ready"), facts=facts)
        # The readiness message carries the exact plan/apply commands; it is
        # kernel/readiness.py's prose and stays as written there.
        return _check(
            "runtime",
            FAIL,
            _same(readiness_failure_message(readiness)),
            _say("runtime.standard_missing.remedy"),
            facts,
        )
    try:
        from openai4s.kernel.environments import discover_environments

        envs = discover_environments()
        facts["environments"] = sorted(getattr(e, "name", str(e)) for e in envs)
    except Exception as e:  # noqa: BLE001
        facts["environments_error"] = str(e)
        envs = []
    # `discover_environments()` always seeds a synthetic `base` (the daemon's own
    # interpreter), so `envs` is never empty. "Is a prebuilt environment present?"
    # must exclude it — otherwise a fresh install with a system Rscript reported
    # OK with "1 environment" and never recommended `openai4s setup`.
    #
    # It must also exclude the user's own conda environments. Discovery lists
    # them (marked `builtin=False`) so a person can pick one, but they are not
    # something OpenAI4S built: counting them reported "3 prebuilt
    # environment(s) available" on a machine where the setup wizard, correctly,
    # said the managed environments did not exist yet.
    others = [e for e in envs if getattr(e, "name", "") != "base"]
    prebuilt = [e for e in others if getattr(e, "builtin", True)]
    local = len(others) - len(prebuilt)
    facts["prebuilt_environments"] = len(prebuilt)
    facts["local_environments"] = local
    local_clause = _say("runtime.local_envs", local=local) if local else _same("")

    # The resolver the R kernel itself uses: the selected env's own Rscript, an
    # env literally named `r`, any env carrying one, then PATH. `which` alone
    # reported "R cells will not run" on the very installations `openai4s setup`
    # had just built an R environment for, because a conda env's bin directory
    # is not on the daemon's PATH — and it could not name which interpreter
    # would actually be chosen.
    try:
        from openai4s.kernel.r_kernel import resolve_r_interpreter

        r_path = resolve_r_interpreter()
    except Exception as e:  # noqa: BLE001 - resolution failing is the finding
        facts["rscript_error"] = str(e)
        r_path = None
    facts["rscript"] = bool(r_path)
    if r_path:
        facts["rscript_path"] = str(r_path)
    if not prebuilt:
        # The R channel does not require a prebuilt conda env: the resolver falls
        # back to PATH, and `r_worker.R` runs happily against a system Rscript.
        # Reporting "R cells will not run" whenever no env is built was wrong on
        # exactly those hosts — it named an interpreter the kernel *would* use as
        # unavailable. Report what the same resolver actually found.
        r_clause = (
            _say("runtime.r_runs", r_path=r_path)
            if r_path
            else _say("runtime.r_will_not_run")
        )
        return _check(
            "runtime",
            WARN,
            _say("runtime.no_managed", r_clause=r_clause, local_clause=local_clause),
            _say("runtime.missing_envs.remedy"),
            facts,
        )
    detail = _say("runtime.managed", count=len(prebuilt), local_clause=local_clause)
    if not r_path:
        return _check(
            "runtime",
            WARN,
            _say("runtime.no_rscript", detail=detail),
            _say("runtime.no_rscript.remedy"),
            facts,
        )
    return _check(
        "runtime", OK, _say("runtime.ok", detail=detail, r_path=r_path), facts=facts
    )


def _with_allowlist_boundary(check: Check) -> Check:
    """Fail an unproven kernel when Cells also require an allowlist boundary."""

    from openai4s.egress import cell_admission_refusal, egress_mode

    if egress_mode() != "allowlist":
        return check
    decision = cell_admission_refusal(check.facts)
    if decision is None:
        return check
    code = str(decision.get("code") or "egress_boundary_unavailable")
    detail, remedy = check.said()
    # A check that already failed has its own detail and remedy. Prefix the
    # stable code and leave that text in place. A warning or an otherwise
    # passing probe has no failure of its own, so the boundary copy is the
    # explanation (the egress module's own English, in both languages).
    prefix = f"{code}: "

    def prefixed(text: str) -> str:
        return text if text.startswith(prefix) else prefix + text

    if check.status == FAIL:
        return _check(
            check.name,
            FAIL,
            _Said(prefixed(detail.en), prefixed(detail.zh)),
            remedy,
            dict(check.facts),
        )
    boundary_remedy = " ".join(str(item) for item in decision.get("remedy") or [])
    reason = str(decision.get("reason") or "")
    return _check(
        check.name,
        FAIL,
        _same(prefix) + (_same(reason) if reason else detail),
        _same(boundary_remedy) if boundary_remedy else remedy,
        dict(check.facts),
    )


def _isolation(cfg: Any) -> Check:
    """Is the kernel sandbox actually going to be applied?"""
    return _with_allowlist_boundary(_isolation_report(cfg))


def _isolation_report(cfg: Any) -> Check:
    """Measured sandbox posture, before the allowlist Cell gate."""

    mode = (os.environ.get("OPENAI4S_KERNEL_SANDBOX") or "auto").strip().lower()
    facts: dict[str, Any] = {"mode": mode}
    if mode == "off":
        return _check(
            "isolation",
            WARN,
            _say("isolation.off"),
            _say("isolation.off.remedy"),
            facts,
        )
    # The binary being installed is not the boundary. `bwrap` present but
    # unprivileged user namespaces disabled by the kernel, or a Seatbelt
    # profile the OS rejects, both leave a host where the *runtime* degrades in
    # `auto` and refuses to start in `enforce` — while this check said "active"
    # and the release gate it feeds saw nothing. So build the real thing: it
    # runs the same self-test the kernel runs, in a temp workspace it removes
    # afterwards. A bounded, self-cleaning side effect is the price of an
    # answer that means something.
    # Imported before the try so the `except SandboxConfigurationError` clause
    # can always be evaluated, even if constructing the sandbox is what fails.
    from openai4s.security.sandbox import (
        SandboxConfigurationError,
        create_kernel_sandbox,
    )

    sandbox = None
    try:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="openai4s-doctor-sandbox-") as probe:
            sandbox = create_kernel_sandbox(probe, mode=mode)
            status = sandbox.status
            facts.update(
                {
                    "backend": status.backend,
                    "available": status.backend is not None,
                    "enforced": status.enforced,
                    "self_test_passed": status.self_test_passed,
                    "state": status.state,
                    "network_policy": status.network_policy,
                }
            )
            reason = status.detail or ""
            warning = status.warning or ""
    except SandboxConfigurationError as e:
        # A typo in OPENAI4S_KERNEL_SANDBOX, or a malformed raw-network flag,
        # makes *every* kernel spawn raise this — the system is unusable
        # regardless of which mode was intended. Reporting a warning unless the
        # mode happens to read `enforce` let a broken config exit as usable.
        facts.setdefault("available", False)
        return _check(
            "isolation",
            FAIL,
            _say("isolation.misconfigured", error=e),
            _say("isolation.misconfigured.remedy"),
            facts,
        )
    except Exception as e:  # noqa: BLE001 - in `enforce` this is the refusal
        facts.setdefault("available", False)
        if mode == "enforce":
            return _check(
                "isolation",
                FAIL,
                _say("isolation.enforce_unavailable", error=e),
                _say("isolation.enforce.remedy"),
                facts,
            )
        return _check(
            "isolation",
            WARN,
            _say("isolation.unprobed", error=e),
            _say("isolation.unprobed.remedy"),
            facts,
        )
    finally:
        if sandbox is not None:
            try:
                sandbox.close()
            except Exception:  # noqa: BLE001
                pass

    if facts.get("enforced"):
        return _check(
            "isolation",
            OK,
            _say("isolation.active", backend=facts["backend"], mode=mode),
            facts=facts,
        )
    # The sandbox's own reason is its module's English; only "unavailable",
    # said when it gives none, is this module's word.
    unavailable = _say("isolation.unavailable")
    if mode == "enforce":
        return _check(
            "isolation",
            FAIL,
            _say(
                "isolation.enforce_not_holding",
                reason=_same(reason) if reason else unavailable,
            ),
            _say("isolation.enforce.remedy"),
            facts,
        )
    # Present-but-not-holding is the case worth separating: it is the one a
    # user is most likely to have assumed was fine.
    if facts.get("available") and facts.get("self_test_passed") is False:
        why = warning or reason
        return _check(
            "isolation",
            WARN,
            _say(
                "isolation.self_test_failed",
                backend=facts["backend"],
                reason=_same(why) if why else unavailable,
            ),
            _say("isolation.self_test_failed.remedy"),
            facts,
        )
    return _check(
        "isolation",
        WARN,
        _say(
            "isolation.no_backend",
            reason=_same(reason) if reason else unavailable,
            mode=mode,
        ),
        _say("isolation.no_backend.remedy"),
        facts,
    )


def _data_dir(cfg: Any) -> Check:
    """Can the data directory actually be used?

    The one bootstrap failure doctor is most likely to be run *for*, and the
    one it could not report: `get_config()` calls `ensure_dirs()`, so an
    `OPENAI4S_DATA_DIR` naming a file, or a directory nothing may write to,
    raised on the way in and produced a traceback instead of a verdict and its
    documented exit code. The CLI now hands doctor a config it did not have to
    create anything for; this is the check that says what is wrong with it.

    Non-mutating first, so the diagnosis does not depend on the repair
    succeeding. Only then is the daemon's own `ensure_dirs()` attempted, which
    is the sole way to answer "would starting work" without guessing.
    """
    target = Path(cfg.data_dir)
    facts: dict[str, Any] = {"data_dir": str(target)}
    remedy = _say("data.remedy")
    if target.exists() and not target.is_dir():
        return _check(
            "data",
            FAIL,
            _say("data.not_a_directory", target=target),
            remedy,
            facts,
        )
    probe = target if target.exists() else target.parent
    if probe.exists():
        try:
            mode = probe.stat().st_mode
        except OSError:
            mode = 0
        # ``os.access`` is the answer for an ordinary user, ACL grants
        # included. Only root bypasses mode bits, so a root-run doctor (a test
        # or container) also requires a write bit for a mode such as 0500.
        geteuid = getattr(os, "geteuid", None)
        is_root = callable(geteuid) and geteuid() == 0
        has_write_bit = bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
        if (is_root and not has_write_bit) or not os.access(probe, os.W_OK | os.X_OK):
            return _check(
                "data",
                FAIL,
                _say("data.not_writable", probe=probe),
                remedy,
                facts,
            )
    db_path = getattr(cfg, "db_path", None)
    if db_path is not None:
        from openai4s.storage.migrations import FutureSchemaError, preflight_schema

        # The refusal `serve` and `run` print, read the same read-only way and
        # before anything is prepared: a writable directory holding a database
        # this release will not open is not usable.
        try:
            preflight_schema(Path(db_path))
        except FutureSchemaError as e:
            return _check(
                "data",
                FAIL,
                _say("data.future_schema", db_path=db_path, error=e),
                _say("data.future_schema.remedy"),
                {
                    **facts,
                    "schema_version": e.actual_version,
                    "supported_schema_version": e.supported_version,
                },
            )
    prepare = getattr(cfg, "ensure_dirs", None)
    if callable(prepare):
        try:
            prepare()
        except Exception as e:  # noqa: BLE001 - the failure *is* the finding
            return _check(
                "data",
                FAIL,
                _say("data.unprepared", target=target, error=e),
                remedy,
                facts,
            )
    unopened = unopened_database(cfg)
    if unopened is not None and unopened["reason"] == "upgrade_pending":
        version = unopened["schema_version"]
        supported = unopened["supported_schema_version"]
        facts.update(
            schema_version=version,
            supported_schema_version=supported,
            upgrade_pending=True,
        )
        db = Path(db_path)
        # `backup_database` names the copy this way, and the migration deletes
        # it once the upgrade commits: a copy still beside a database at the
        # same old version is an attempt that did not complete.
        backup = db.with_name(f"{db.name}.v{version}.bak")
        if backup.exists():
            facts["kept_backup"] = str(backup)
            return _check(
                "data",
                FAIL,
                _say(
                    "data.migration_incomplete",
                    db=db,
                    version=version,
                    supported=supported,
                    backup=backup,
                ),
                _say("data.migration_incomplete.remedy"),
                facts,
            )
        return _check(
            "data",
            WARN,
            _say("data.upgrade_pending", db=db, version=version, supported=supported),
            _say("data.upgrade_pending.remedy"),
            facts,
        )
    if unopened is not None and unopened["reason"] == "interrupted_write":
        return _check(
            "data",
            WARN,
            _say("data.interrupted_write", db_path=db_path),
            _say("data.interrupted_write.remedy"),
            facts,
        )
    return _check("data", OK, _say("data.ok", target=target), facts=facts)


def _disk(cfg: Any) -> Check:
    """Is there room for a scientific run's artifacts?"""
    target = Path(cfg.data_dir)
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as e:
        return _check("disk", WARN, _say("disk.unmeasured", probe=probe, error=e))
    free_gb = round(usage.free / 1e9, 1)
    facts = {"data_dir": str(target), "free_gb": free_gb}
    if free_gb < _LOW_DISK_GB:
        return _check(
            "disk",
            FAIL,
            _say("disk.low", free_gb=free_gb, target=target),
            _say("disk.low.remedy"),
            facts,
        )
    return _check(
        "disk", OK, _say("disk.ok", free_gb=free_gb, target=target), facts=facts
    )


def probe_science_hosts(
    probe: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Ask each built-in science host for one HEAD, concurrently.

    Through `webtools.web_probe`, so the answer is the one the agent would
    get: the same network switch, egress allowlist, SSRF guard and trust
    store. Any HTTP status counts as reachable -- a 404 for `/` still proves
    DNS, TCP and the TLS handshake -- and only "no status at all" does not.

    A timeout gets one retry: a slow host is not a missing one (one answered
    in 7.6 s on one try and missed 10 s on three others). Each host is still
    bounded at two attempts, and the hosts run in parallel.
    """
    from concurrent.futures import ThreadPoolExecutor

    if probe is None:
        from openai4s import webtools

        probe = webtools.web_probe

    def attempt(host: str) -> dict[str, Any]:
        try:
            answer = probe(f"https://{host}/", timeout=_PROBE_TIMEOUT_S)
        except TimeoutError as e:
            return {"host": host, "reachable": False, "error": str(e) or "timed out"}
        except Exception as e:  # noqa: BLE001 - a refusal is the finding
            return {"host": host, "reachable": False, "error": str(e)}
        status = int(answer.get("status") or 0)
        result: dict[str, Any] = {"host": host, "reachable": status > 0}
        if status > 0:
            result["http_status"] = status
        else:
            result["error"] = str(answer.get("error") or "no response")
        return result

    def timed_out(result: dict[str, Any]) -> bool:
        return not result["reachable"] and "timed out" in result["error"].lower()

    def one(host: str) -> dict[str, Any]:
        result = attempt(host)
        if not timed_out(result):
            return result
        result = attempt(host)
        result["attempts"] = 2
        if timed_out(result):
            result["timed_out"] = True
            result["error"] = (
                f"timed out: no answer within {_PROBE_TIMEOUT_S:g} s, on 2 tries"
            )
        return result

    with ThreadPoolExecutor(max_workers=len(SCIENCE_HOSTS)) as pool:
        answers = list(pool.map(one, SCIENCE_HOSTS.values()))
    return dict(zip(SCIENCE_HOSTS, answers))


def _connectors(cfg: Any, *, probe_hosts: bool = False) -> Check:
    """Are the scientific data sources and MCP servers wired up?"""
    facts: dict[str, Any] = {}
    try:
        from openai4s.host.science import DATABASES

        facts["science_databases"] = [db.id for db in DATABASES]
    except Exception as e:  # noqa: BLE001
        return _check("connectors", WARN, _say("connectors.unavailable", error=e))

    unopened = unopened_database(cfg)
    store_error: str | None = None
    if unopened is not None:
        # The data check reports why; opening it here would be the upgrade.
        facts["connector_store_not_read"] = unopened["reason"]
        if unopened["reason"] == "unreadable":
            # Not a state the data check explains away: the read itself failed.
            store_error = str(unopened["error"])
            facts["connector_store_error"] = store_error
    else:
        try:
            from openai4s.store import get_store

            store = get_store(cfg.db_path)
            # Names and configured-ness only. Values never leave the store.
            connectors = store.list_connectors()
            facts["configured_connectors"] = len(connectors)
        except Exception as e:  # noqa: BLE001
            store_error = str(e)
            facts["connector_store_error"] = store_error

    # The global kill switch is OPENAI4S_ALLOW_NETWORK; OPENAI4S_EGRESS selects
    # whether an *allowlist* is enforced, and its default — `off` — means
    # fail-open, i.e. everything is reachable. Reading `off` as "offline"
    # inverted both answers at once: a genuinely network-disabled install was
    # reported as able to reach seven databases, and the default configuration
    # was reported as unable to reach any.
    from openai4s import egress, webtools

    network = webtools.network_allowed()
    mode = egress.egress_mode()
    facts["network_allowed"] = network
    facts["egress_mode"] = mode
    if store_error is not None:
        # Kept only in facts, this was an `ok` beside a database that `serve`
        # and `run` refuse to open.
        return _check(
            "connectors",
            WARN,
            _say(
                "connectors.store_error",
                count=len(facts["science_databases"]),
                error=store_error,
            ),
            _say("connectors.store_error.remedy"),
            facts,
        )
    if not network:
        return _check(
            "connectors",
            WARN,
            _say("connectors.offline", count=len(facts["science_databases"])),
            _say("connectors.offline.remedy"),
            facts,
        )
    detail = _say("connectors.built_in", count=len(facts["science_databases"]))
    if "configured_connectors" in facts:
        detail += _say("connectors.configured", count=facts["configured_connectors"])
    if mode == "allowlist":
        granted = sorted(egress.granted_domains())
        facts["egress_allowlist_size"] = len(granted)
        detail += _say("connectors.allowlist", count=len(granted))
    facts["reachability_probed"] = probe_hosts
    if not probe_hosts:
        # Being built in says nothing about being reachable, and this run did
        # not look. Say so, rather than let "ok" be read as "connected".
        detail += _say("connectors.not_probed")
        return _check("connectors", OK, detail, facts=facts)
    reachability = probe_science_hosts()
    facts["reachability"] = reachability
    down = [db for db, answer in reachability.items() if not answer["reachable"]]
    detail += _say(
        "connectors.reachable",
        up=len(reachability) - len(down),
        total=len(reachability),
    )
    if down:
        return _check(
            "connectors",
            WARN,
            detail + _say("connectors.unreachable", names=", ".join(down)),
            _say("connectors.unreachable.remedy"),
            facts,
        )
    return _check("connectors", OK, detail, facts=facts)


def _judgment(cfg: Any) -> Check:
    """Experimental semantic judgment layer. Configuration only — no network."""

    from openai4s.judgment.flags import resolve
    from openai4s.judgment.settings import (
        TYPESAFE_HOST,
        egress_report,
        key_is_configured,
        resolve_settings_config,
    )

    facts: dict[str, Any] = {"experimental": True}
    try:
        from openai4s.judgment.shadow import stats as shadow_stats

        facts["safety_shadow"] = shadow_stats()
    except Exception:
        pass
    store = _store_for(cfg)
    cfg = resolve_settings_config(cfg, store)
    flags = resolve(cfg, store)
    facts["enabled"] = flags.master.enabled
    facts["source"] = flags.master.source
    facts["provider"] = flags.provider
    facts["model"] = flags.model
    is_llm = flags.provider == "llm"
    host = (
        urllib.parse.urlsplit(cfg.llm.base_url).hostname or ""
        if is_llm
        else TYPESAFE_HOST
    )
    report = egress_report(host)
    facts["egress_mode"] = report["mode"]
    facts["domain_allowed"] = report["domain_allowed"]
    if not flags.master.enabled:
        return _check("judgment", OK, _say("judgment.disabled"), facts=facts)
    try:
        if is_llm:
            from openai4s.judgment.settings import status as judgment_status

            configured = bool(judgment_status(cfg, store)["key_configured"])
        else:
            configured = key_is_configured(store)
    except Exception as e:  # noqa: BLE001 - a secret-store fault is a finding
        facts["key_configured"] = False
        facts["key_store_error"] = type(e).__name__
        return _check(
            "judgment",
            WARN,
            _say("judgment.key_unreadable"),
            _say("judgment.key_unreadable.remedy"),
            facts,
        )
    facts["key_configured"] = configured
    if not configured:
        if is_llm:
            return _check(
                "judgment",
                WARN,
                _say("judgment.no_main_key"),
                _say("judgment.no_main_key.remedy"),
                facts,
            )
        return _check(
            "judgment",
            WARN,
            _say("judgment.no_typesafe_key"),
            _say("judgment.no_typesafe_key.remedy"),
            facts,
        )
    if report["mode"] == "allowlist" and not report["domain_allowed"]:
        # The egress module's remediation is its own English.
        remedy = (
            _same(report["remediation"])
            if report["remediation"]
            else _say("judgment.not_allowlisted.remedy", host=host)
        )
        return _check(
            "judgment",
            WARN,
            _say("judgment.not_allowlisted", host=host),
            remedy,
            facts,
        )
    return _check(
        "judgment",
        OK,
        _say("judgment.enabled", provider=flags.provider, model=flags.model),
        facts=facts,
    )


def _remote(cfg: Any) -> Check:
    """Can heavy work leave this machine, and is that boundary provable?"""
    facts: dict[str, Any] = {}
    try:
        from openai4s.compute.manager import _discover_providers

        providers = _discover_providers(Path(cfg.skills_dir))
        facts["byoc_providers"] = sorted(providers)
    except Exception as e:  # noqa: BLE001
        facts["byoc_error"] = str(e)
        providers = {}

    ssh_available = (Path(cfg.skills_dir) / "remote-compute-ssh").is_dir()
    facts["ssh_family"] = ssh_available
    # Validate through the *runtime* parser, not a lowercase(). A value like
    # `enfore` makes `ComputeManager` construction raise, so remote compute is
    # unusable — but a bare string reached `posture()` as an auto-like mode (or
    # was skipped on an SSH-only setup) and doctor could report OK.
    raw_confinement = os.environ.get("OPENAI4S_COMPUTE_CONFINEMENT")
    try:
        from openai4s.compute.manager import _CONFINEMENT_ENV, _confinement_mode

        confinement = _confinement_mode(raw_confinement)
    except Exception as e:  # noqa: BLE001 - an invalid mode is a hard config fault
        facts["confinement_mode"] = (raw_confinement or "").strip()
        # `ComputeManager.__init__` parses this mode unconditionally
        # (manager.py: `self._confinement_mode = _confinement_mode()`), so an
        # invalid value makes construction raise for *any* remote setup — not
        # only BYOC providers. An SSH-only host used to slip through to WARN and
        # report "fixable" while the manager would refuse to start; that is a
        # hard fault whenever remote compute is configured at all.
        if providers or ssh_available:
            configured = (
                _say("remote.byoc", count=len(providers))
                if providers
                else _say("remote.ssh_family")
            )
            return _check(
                "remote",
                FAIL,
                _say("remote.invalid_confinement", configured=configured, error=e),
                _say("remote.invalid_confinement.remedy"),
                facts,
            )
        return _check(
            "remote",
            WARN,
            _say("remote.invalid_confinement_unused", error=e),
            _say("remote.invalid_confinement_unused.remedy"),
            facts,
        )
    facts["confinement_mode"] = confinement

    if not providers and not ssh_available:
        return _check("remote", OK, _say("remote.none"), facts=facts)
    if providers:
        # Ask the same module the runtime asks, and run the same bounded
        # boundary self-test it runs. This used to be an unconditional FAIL
        # under `enforce` — "no OS boundary is applied to the provider helper"
        # — on hosts where Seatbelt or bubblewrap was implemented, self-tested
        # and actually applied. It contradicted the runtime status and told the
        # user to weaken their configuration to fix a problem they did not have.
        from openai4s.security import byoc_confinement

        posture = byoc_confinement.posture(confinement)
        facts["confinement_state"] = posture["state"]
        facts["confinement_backend"] = posture.get("backend")
        facts["confinement_network_isolated"] = posture.get("network_isolated")
        if confinement == "enforce" and not posture["enforced"]:
            return _check(
                "remote",
                FAIL,
                _say(
                    "remote.enforce_unsatisfied",
                    count=len(providers),
                    detail=posture["detail"],
                ),
                _say("remote.enforce_unsatisfied.remedy"),
                facts,
            )
        if not posture["enforced"]:
            return _check(
                "remote",
                WARN,
                _say(
                    "remote.unconfined", count=len(providers), detail=posture["detail"]
                ),
                _say("remote.unconfined.remedy"),
                facts,
            )
    parts = []
    if providers:
        parts.append(_say("remote.byoc", count=len(providers)))
    if ssh_available:
        parts.append(_say("remote.ssh_enabled"))
    separator = _say("remote.separator")
    summary = parts[0]
    for part in parts[1:]:
        summary = summary + separator + part
    return _check("remote", OK, summary, facts=facts)


#: Order matters: it is the order the report prints in, and it runs from the
#: most fundamental ("can we reach a model") outward.
_CHECKS: tuple[tuple[str, Callable[[Any], Check]], ...] = (
    ("data", _data_dir),
    ("model", _model),
    ("runtime", _runtime),
    ("isolation", _isolation),
    ("disk", _disk),
    ("connectors", _connectors),
    ("remote", _remote),
    ("judgment", _judgment),
)

#: Probes `report()` still runs for the CLI, and that a page-load GET must not.
#: `model` and `connectors` open the Store too, but only a database already at
#: this release's schema (`unopened_database`): no probe upgrades one.
#: `data` calls `ensure_dirs()` (a write). `isolation` builds a temp sandbox
#: and runs the kernel self-test (a subprocess). `remote` runs the BYOC
#: confinement self-test (another subprocess). `runtime` walks environment
#: roots. The web Diagnostics GET uses `passive_status()` instead of this set.
SIDE_EFFECT_CHECKS = frozenset({"data", "isolation", "remote", "runtime"})


def run_checks(cfg: Any, *, network: bool = False) -> list[Check]:
    """Every probe, in order. A probe that raises becomes a failed check.

    A crash here would deny the report to the person who most needs it, so an
    unexpected exception is itself a finding rather than a traceback.

    ``network`` is the one opt-in: it lets the connectors check contact the
    built-in science hosts. Everything else stays offline either way.
    """
    results: list[Check] = []
    for name, probe in _CHECKS:
        try:
            if network and probe is _connectors:
                results.append(_connectors(cfg, probe_hosts=True))
                continue
            results.append(probe(cfg))
        except Exception as e:  # noqa: BLE001
            results.append(
                _check(name, FAIL, _say("check.crashed", name=name, error=e))
            )
    return results


def report(
    cfg: Any,
    *,
    network: bool = False,
    live_model: Callable[[Any], None] | None = None,
    language: str = "en",
) -> dict[str, Any]:
    """Full probe set, including checks that write or spawn.

    The CLI wants the real answer, so this still runs `SIDE_EFFECT_CHECKS`.
    The web Diagnostics GET must call `passive_status()` instead.

    ``live_model`` is the one request that leaves the machine: given
    `send_test_request` (the CLI does with `--network`), the model check is
    confirmed with the provider. Without it the check is configuration only
    and says so.

    ``language`` ``"zh"`` says each ``detail`` and ``remedy`` in Chinese, for
    the CLI's human form only. Everything else is identical, and the default
    English result is the JSON contract (`--json`, the web route).
    """
    checks = run_checks(cfg, network=network)
    if live_model is not None:
        checks = [
            _verify_model(cfg, c, live_model) if c.name == "model" else c
            for c in checks
        ]
    worst = (
        FAIL
        if any(c.status == FAIL for c in checks)
        else (WARN if any(c.status == WARN for c in checks) else OK)
    )
    return {"status": worst, "checks": [c.public(language) for c in checks]}


def passive_status(cfg: Any) -> dict[str, Any]:
    """Security posture a page-load GET may show.

    No Store open, no child process, no network call, no `ensure_dirs()`.
    Versions and permission stats are reads; posture knobs come from the
    process environment. Schema and secret-broker probes stay off this
    path because they open the database.
    """
    from openai4s.diagnostics import environment_report, passive_security_posture

    return {
        "security": passive_security_posture(cfg),
        "environment": environment_report(),
    }


_MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL"}


def render(result: dict[str, Any], language: str = "en") -> str:
    """The human form. One line per check, remedies indented beneath.

    ``language`` is the language of the frame around the checks; pass the
    one `report()` was asked for. The marks and check names stay as they are
    in every language: they are what a person greps for.
    """
    words = _COPY["zh" if language == "zh" else "en"]
    lines = []
    for check in result["checks"]:
        lines.append(
            f"[{_MARK[check['status']]}] {check['name']:<11} {check['detail']}"
        )
        if check["remedy"] and check["status"] != OK:
            lines.append(f"              -> {check['remedy']}")
        for database, answer in (check["facts"].get("reachability") or {}).items():
            if answer["reachable"]:
                outcome = f"ok    HTTP {answer['http_status']}"
                if answer.get("attempts", 1) > 1:
                    outcome += words["render.after_timeout"]
            else:
                error = answer["error"]
                if language == "zh" and answer.get("timed_out"):
                    # `facts` keep the probe's English error (the JSON reads
                    # it); the Chinese form says the same timeout.
                    error = words["render.timed_out"].format(seconds=_PROBE_TIMEOUT_S)
                outcome = f"FAIL  {error}"
            lines.append(f"              {database:<10} {answer['host']:<26} {outcome}")
    lines.append("")
    lines.append(words[f"render.verdict.{result['status']}"])
    return "\n".join(lines)


#: Every sentence a check says, in English and in Chinese (CLI-35). English is
#: the JSON contract (`--json`, `POST /diagnostics/checks`) and never changes
#: with the CLI's language; Chinese is only the CLI's human form under
#: `OPENAI4S_LANG=zh` or a Chinese locale. Prose that another module says --
#: an exception, the sandbox's reason, the egress module's remediation -- is
#: quoted as it was raised. Both sides hold the same keys
#: (`tests/test_cli_lang.py`).
_COPY: dict[str, dict[str, str]] = {
    "en": {
        # model
        "model.source.settings": "saved by Settings -> Models or `openai4s init`",
        "model.source.settings_overrides": (
            "{note}; this overrides {env_name}={env_value!r}"
        ),
        "model.source.environment": "from {env_name}",
        "model.source.default": "provider default",
        "model.unknown_provider": (
            "provider {provider!r} is not one this build knows: {error}"
        ),
        "model.unknown_provider.remedy": (
            "Set OPENAI4S_LLM_PROVIDER to a supported provider, or configure "
            "one in the UI under Settings -> Models."
        ),
        "model.named": "model {model!r}",
        "model.named_note": "model {model!r} ({note})",
        "model.no_key_db_closed": (
            "provider {provider!r} resolves to model {model!r} with no API "
            "key in the process configuration, and doctor did not open the "
            "database that holds the Settings -> Models settings (see the "
            "data check)"
        ),
        "model.no_key_db_closed.remedy": (
            "Resolve the data check and rerun `openai4s doctor`, or set "
            "OPENAI4S_{upper}_API_KEY (or the generic OPENAI4S_LLM_API_KEY)."
        ),
        "model.no_key": (
            "provider {provider!r} resolves to model {model!r}, but no API "
            "key is configured"
        ),
        "model.no_key.remedy": (
            "Set OPENAI4S_{upper}_API_KEY (or the generic OPENAI4S_LLM_API_KEY), "
            "or add it in the UI under Settings -> Models."
        ),
        "model.local": (
            "provider {provider!r}, {named}, local endpoint {endpoint} "
            "(no credential required)"
        ),
        "model.configured": "provider {provider!r}, {named}, {credential}",
        "model.credential_configured": "credential configured ({verified})",
        "model.not_verified": (
            "not verified: no request was sent to the provider; "
            "`openai4s doctor --network` sends one"
        ),
        "model.network_switched_off": "switched off in Settings -> Network",
        "model.not_verified_offline": "not verified: networking is disabled, {why}",
        "model.credential_verified": (
            "credential verified (the provider answered a test request)"
        ),
        "model.auth_failed": (
            "provider {provider!r} rejected the configured API key "
            "(HTTP {status} from {endpoint}), so no turn can run"
        ),
        "model.auth_failed.remedy": (
            "Enter a valid key with `openai4s init` or in the UI under "
            "Settings -> Models, or set OPENAI4S_{upper}_API_KEY (or the "
            "generic OPENAI4S_LLM_API_KEY)."
        ),
        "model.not_found": (
            "provider {provider!r} has no model {model!r} at {endpoint} "
            "(HTTP 404): the model id or the base URL is wrong"
        ),
        "model.not_found.remedy": (
            "Choose a model this endpoint serves with `openai4s init "
            "--model <id>` or in the UI under Settings -> Models."
        ),
        "model.inconclusive_status": (
            "provider {provider!r} answered HTTP {status} to a test request "
            "for model {model!r}, so the key and model are not verified"
        ),
        "model.inconclusive_status.remedy": (
            "Rerun `openai4s doctor` in a moment; if it persists, check the "
            "model id, base URL and account quota."
        ),
        "model.timed_out": "the request timed out",
        "model.connection_failed": "the connection failed",
        "model.unreachable": (
            "could not reach {endpoint} to verify the key and model ({how}); "
            "provider {provider!r}, model {model!r}"
        ),
        "model.unreachable.remedy": (
            "Check the network, the proxy and the base URL, then rerun; "
            "`openai4s doctor` without `--network` skips this request."
        ),
        "model.incomplete": (
            "the test request to provider {provider!r} for model {model!r} "
            "did not complete ({error_type}), so the key and model are not "
            "verified"
        ),
        "model.incomplete.remedy": (
            "Rerun `openai4s doctor --network`; without `--network` nothing " "is sent."
        ),
        # runtime
        "runtime.standard_ready": (
            "standard profile ready: all recommended Python and R packages "
            "are present in local metadata"
        ),
        "runtime.standard_missing.remedy": (
            "Run the explicit managed plan/apply commands above, then rerun "
            "`openai4s doctor`."
        ),
        "runtime.local_envs": (
            "; {local} local conda environment(s) of your own are selectable "
            "but not managed by OpenAI4S"
        ),
        "runtime.r_runs": "R cells will run against {r_path}",
        "runtime.r_will_not_run": "R cells will not run",
        "runtime.no_managed": (
            "no managed environment built; Python cells will run in the "
            "built-in interpreter (the daemon's own) and {r_clause}{local_clause}"
        ),
        "runtime.missing_envs.remedy": MISSING_ENVIRONMENTS_REMEDY,
        "runtime.managed": (
            "built-in interpreter + {count} managed environment(s){local_clause}"
        ),
        "runtime.no_rscript": (
            "{detail}, but no Rscript could be resolved — R cells will not run"
        ),
        "runtime.no_rscript.remedy": (
            "Run `openai4s setup --only r` if you need the R channel."
        ),
        "runtime.ok": "{detail}, Rscript at {r_path}",
        # isolation
        "isolation.off": (
            "kernel sandbox is disabled (OPENAI4S_KERNEL_SANDBOX=off); cells "
            "run with the daemon's own filesystem and network reach"
        ),
        "isolation.off.remedy": (
            "Leave it unset (auto) unless you are deliberately debugging the "
            "sandbox itself."
        ),
        "isolation.misconfigured": (
            "the kernel sandbox is misconfigured, so no cell can run: {error}"
        ),
        "isolation.misconfigured.remedy": (
            "Fix the OPENAI4S_KERNEL_SANDBOX / OPENAI4S_KERNEL_ALLOW_RAW_NETWORK "
            "value; the sandbox accepts only auto, enforce, or off."
        ),
        "isolation.enforce_unavailable": (
            "OPENAI4S_KERNEL_SANDBOX=enforce and the boundary could not be "
            "established: {error}"
        ),
        "isolation.enforce.remedy": (
            "Install bubblewrap (Linux) or run on macOS with Seatbelt "
            "available; enforce deliberately refuses to run unconfined."
        ),
        "isolation.unprobed": "the sandbox could not be probed: {error}",
        "isolation.unprobed.remedy": (
            "Run with OPENAI4S_KERNEL_SANDBOX=enforce to make this fatal rather "
            "than a degradation."
        ),
        "isolation.active": (
            "kernel sandbox active via {backend} (mode {mode}); self-test passed"
        ),
        "isolation.unavailable": "unavailable",
        "isolation.enforce_not_holding": (
            "OPENAI4S_KERNEL_SANDBOX=enforce but the boundary does not hold: "
            "{reason}"
        ),
        "isolation.self_test_failed": (
            "{backend} is installed but its self-test failed, so cells run "
            "unconfined: {reason}"
        ),
        "isolation.self_test_failed.remedy": (
            "Fix the backend (on Linux, unprivileged user namespaces are the "
            "usual cause) — OPENAI4S_KERNEL_SANDBOX=enforce refuses instead of "
            "degrading."
        ),
        "isolation.no_backend": (
            "no sandbox backend available ({reason}); cells run unconfined "
            "because the mode is {mode}"
        ),
        "isolation.no_backend.remedy": (
            "This is a visible degradation, not a silent one. Use "
            "OPENAI4S_KERNEL_SANDBOX=enforce to refuse instead."
        ),
        # data
        "data.remedy": (
            "Point OPENAI4S_DATA_DIR at a directory this user can write to, or "
            "fix the permissions on the one it names."
        ),
        "data.not_a_directory": (
            "{target} is not a directory, so nothing can be stored in it"
        ),
        "data.not_writable": "{probe} is not writable by this user",
        "data.future_schema": (
            "the database at {db_path} is from a newer release: {error}"
        ),
        "data.future_schema.remedy": (
            "Run the OpenAI4S release that upgraded this data directory, or "
            "point OPENAI4S_DATA_DIR at one this release created."
        ),
        "data.unprepared": (
            "the data directory at {target} could not be prepared: {error}"
        ),
        "data.migration_incomplete": (
            "the database at {db} is at schema {version}, and an earlier "
            "migration to schema {supported} did not complete: its "
            "pre-upgrade backup, which is deleted only when the upgrade "
            "succeeds, is still at {backup}"
        ),
        "data.migration_incomplete.remedy": (
            "Run `openai4s serve` or `openai4s run` in the foreground to see "
            "why: a failed migration is rolled back and printed as one "
            "`error:` line (exit 2), and re-running it is safe. Keep the "
            "backup until an upgrade succeeds (docs/upgrading.md)."
        ),
        "data.upgrade_pending": (
            "the database at {db} is at schema {version}; the next `openai4s "
            "serve` or `openai4s run` migrates it to schema {supported}, which "
            "reinstalling an older release does not undo. doctor does not "
            "migrate it, so the checks that read the database did not open it"
        ),
        "data.upgrade_pending.remedy": (
            "Back up the data directory first (docs/upgrading.md, the section "
            "for the release you are upgrading from), then start `openai4s "
            "serve` or run `openai4s run` once and rerun `openai4s doctor`."
        ),
        "data.interrupted_write": (
            "the database at {db_path} holds an interrupted write that only a "
            "read-write open recovers; doctor does not open it, so its schema "
            "version is unknown and the checks that read the database did not "
            "open it"
        ),
        "data.interrupted_write.remedy": (
            "Copy the database together with its -journal file, then start "
            "`openai4s serve` or run `openai4s run` once to let SQLite recover "
            "it, and rerun `openai4s doctor`."
        ),
        "data.ok": "usable at {target}",
        # disk
        "disk.unmeasured": "could not measure free space at {probe}: {error}",
        "disk.low": "{free_gb} GB free at {target} — a run is likely to fail partway",
        "disk.low.remedy": (
            "Free space, or point OPENAI4S_DATA_DIR at a larger volume."
        ),
        "disk.ok": "{free_gb} GB free at {target}",
        # connectors
        "connectors.unavailable": "science connectors unavailable: {error}",
        "connectors.store_error": (
            "{count} science databases are built in, but the configured "
            "connectors could not be read from the database: {error}"
        ),
        "connectors.store_error.remedy": (
            "Resolve the database error above; `openai4s serve` and `openai4s "
            "run` cannot open it either."
        ),
        "connectors.offline": (
            "{count} science databases are built in, but networking is "
            "disabled (OPENAI4S_ALLOW_NETWORK=0), so none can be reached"
        ),
        "connectors.offline.remedy": (
            "Enable it in Settings -> Network, or set OPENAI4S_ALLOW_NETWORK=1."
        ),
        "connectors.built_in": "{count} science databases built in",
        "connectors.configured": ", {count} connector(s) configured",
        "connectors.allowlist": "; egress allowlist enforced ({count} domain(s))",
        "connectors.not_probed": (
            "; reachability not probed (run `openai4s doctor --network`)"
        ),
        "connectors.reachable": "; {up} of {total} reachable",
        "connectors.unreachable": " (unreachable: {names})",
        "connectors.unreachable.remedy": (
            "See the per-host lines for the reason; a TLS failure names the "
            "host and what to set."
        ),
        # judgment
        "judgment.disabled": "disabled (experimental, default off)",
        "judgment.key_unreadable": (
            "enabled, but the TypeSafe API key could not be read"
        ),
        "judgment.key_unreadable.remedy": (
            "Retry after the secret store is available, or set "
            "OPENAI4S_TYPESAFE_API_KEY for headless use."
        ),
        "judgment.no_main_key": ("enabled, but no main model credential is configured"),
        "judgment.no_main_key.remedy": (
            "Configure the main model in Settings -> Models."
        ),
        "judgment.no_typesafe_key": "enabled, but no TypeSafe API key is configured",
        "judgment.no_typesafe_key.remedy": (
            "Set the key in Settings -> Experimental, or set "
            "OPENAI4S_TYPESAFE_API_KEY."
        ),
        "judgment.not_allowlisted": (
            "enabled, but {host} is not on the egress allowlist"
        ),
        "judgment.not_allowlisted.remedy": (
            "Call host.request_network_access(domain={host!r}) to ask the user "
            "to approve widening it."
        ),
        "judgment.enabled": "enabled ({provider}/{model})",
        # remote
        "remote.byoc": "{count} BYOC provider(s)",
        "remote.ssh_family": "the ssh remote-compute family",
        "remote.invalid_confinement": (
            "{configured} present but the confinement mode is invalid, so "
            "remote compute cannot start: {error}"
        ),
        "remote.invalid_confinement.remedy": (
            "Set OPENAI4S_COMPUTE_CONFINEMENT to auto, enforce, or off."
        ),
        "remote.invalid_confinement_unused": (
            "OPENAI4S_COMPUTE_CONFINEMENT is invalid ({error}); it must be "
            "auto, enforce, or off"
        ),
        "remote.invalid_confinement_unused.remedy": "Fix or unset it.",
        "remote.none": "no remote compute configured; everything runs locally",
        "remote.enforce_unsatisfied": (
            "{count} BYOC provider(s) present and "
            "OPENAI4S_COMPUTE_CONFINEMENT=enforce, which this host cannot "
            "satisfy: {detail}"
        ),
        "remote.enforce_unsatisfied.remedy": (
            "Install the backend for this platform (bubblewrap on Linux), or "
            "use the ssh family, which needs no helper confinement. Setting "
            "`auto` accepts unconfined remote execution."
        ),
        "remote.unconfined": (
            "{count} BYOC provider(s) run without an OS boundary: {detail}"
        ),
        "remote.unconfined.remedy": (
            "Set OPENAI4S_COMPUTE_CONFINEMENT=enforce to refuse byoc ops rather "
            "than run the provider helper unconfined."
        ),
        "remote.ssh_enabled": "ssh family enabled",
        "remote.separator": "; ",
        "check.crashed": "the {name} check itself failed: {error!r}",
        # render
        "render.after_timeout": " (after one timeout)",
        "render.timed_out": "timed out: no answer within {seconds:g} s, on 2 tries",
        f"render.verdict.{OK}": "All checks passed.",
        f"render.verdict.{WARN}": "Usable, with degradations noted above.",
        f"render.verdict.{FAIL}": "Not ready: at least one check failed.",
    },
    "zh": {
        # model
        "model.source.settings": "由 设置 → 模型 或 `openai4s init` 保存",
        "model.source.settings_overrides": (
            "{note}；它覆盖了 {env_name}={env_value!r}"
        ),
        "model.source.environment": "来自 {env_name}",
        "model.source.default": "服务商默认值",
        "model.unknown_provider": "这个版本不认识服务商 {provider!r}：{error}",
        "model.unknown_provider.remedy": (
            "把 OPENAI4S_LLM_PROVIDER 设为受支持的服务商，或在界面的 "
            "设置 → 模型 中配置。"
        ),
        "model.named": "模型 {model!r}",
        "model.named_note": "模型 {model!r}（{note}）",
        "model.no_key_db_closed": (
            "服务商 {provider!r} 解析到模型 {model!r}，进程配置里没有 API Key；"
            "doctor 没有打开保存 设置 → 模型 的数据库（见 data 检查）"
        ),
        "model.no_key_db_closed.remedy": (
            "先解决 data 检查的问题，再重新运行 `openai4s doctor`；或设置 "
            "OPENAI4S_{upper}_API_KEY（或通用的 OPENAI4S_LLM_API_KEY）。"
        ),
        "model.no_key": "服务商 {provider!r} 解析到模型 {model!r}，但没有配置 API Key",
        "model.no_key.remedy": (
            "设置 OPENAI4S_{upper}_API_KEY（或通用的 OPENAI4S_LLM_API_KEY），"
            "或在界面的 设置 → 模型 中添加。"
        ),
        "model.local": (
            "服务商 {provider!r}，{named}，本机接口 {endpoint}（不需要凭据）"
        ),
        "model.configured": "服务商 {provider!r}，{named}，{credential}",
        "model.credential_configured": "已配置凭据（{verified}）",
        "model.not_verified": (
            "未验证：没有向服务商发送请求；`openai4s doctor --network` 会发送一次"
        ),
        "model.network_switched_off": "已在 设置 → 网络 中关闭",
        "model.not_verified_offline": "未验证：网络已关闭，{why}",
        "model.credential_verified": "凭据已验证（服务商回应了测试请求）",
        "model.auth_failed": (
            "服务商 {provider!r} 拒绝了已配置的 API Key（{endpoint} 返回 "
            "HTTP {status}），因此无法运行任何一轮"
        ),
        "model.auth_failed.remedy": (
            "用 `openai4s init` 或在界面的 设置 → 模型 中填写有效的 Key，"
            "或设置 OPENAI4S_{upper}_API_KEY（或通用的 OPENAI4S_LLM_API_KEY）。"
        ),
        "model.not_found": (
            "服务商 {provider!r} 在 {endpoint} 没有模型 {model!r}（HTTP 404）："
            "模型 id 或 Base URL 有误"
        ),
        "model.not_found.remedy": (
            "用 `openai4s init --model <id>` 或在界面的 设置 → 模型 中选择"
            "这个接口提供的模型。"
        ),
        "model.inconclusive_status": (
            "服务商 {provider!r} 对模型 {model!r} 的测试请求返回 HTTP {status}，"
            "因此 Key 和模型都未验证"
        ),
        "model.inconclusive_status.remedy": (
            "稍后重新运行 `openai4s doctor`；如果一直如此，请检查模型 id、"
            "Base URL 和账户额度。"
        ),
        "model.timed_out": "请求超时",
        "model.connection_failed": "连接失败",
        "model.unreachable": (
            "无法连接 {endpoint} 来验证 Key 和模型（{how}）；服务商 "
            "{provider!r}，模型 {model!r}"
        ),
        "model.unreachable.remedy": (
            "检查网络、代理和 Base URL 后重新运行；不带 `--network` 的 "
            "`openai4s doctor` 不会发送这个请求。"
        ),
        "model.incomplete": (
            "发给服务商 {provider!r} 的模型 {model!r} 测试请求没有完成"
            "（{error_type}），因此 Key 和模型都未验证"
        ),
        "model.incomplete.remedy": (
            "重新运行 `openai4s doctor --network`；不带 `--network` 时不会"
            "发送任何请求。"
        ),
        # runtime
        "runtime.standard_ready": (
            "标准配置已就绪：本机元数据里有全部推荐的 Python 和 R 包"
        ),
        "runtime.standard_missing.remedy": (
            "运行上面列出的托管 plan/apply 命令，然后重新运行 `openai4s doctor`。"
        ),
        "runtime.local_envs": (
            "；另有 {local} 个你自己的本地 conda 环境可以选用，但不由 OpenAI4S 管理"
        ),
        "runtime.r_runs": "R 单元将使用 {r_path} 运行",
        "runtime.r_will_not_run": "R 单元无法运行",
        "runtime.no_managed": (
            "还没有构建托管环境；Python 单元将在内置解释器（守护进程自己的）"
            "中运行，{r_clause}{local_clause}"
        ),
        "runtime.missing_envs.remedy": (
            "运行 `openai4s setup` 构建托管的 Python + R 环境；单个软件包可在 "
            "设置 → 计算 里安装。"
        ),
        "runtime.managed": "内置解释器 + {count} 个托管环境{local_clause}",
        "runtime.no_rscript": "{detail}，但找不到可用的 Rscript，R 单元无法运行",
        "runtime.no_rscript.remedy": "如果需要 R，请运行 `openai4s setup --only r`。",
        "runtime.ok": "{detail}，Rscript 位于 {r_path}",
        # isolation
        "isolation.off": (
            "内核沙箱已关闭（OPENAI4S_KERNEL_SANDBOX=off）；单元以守护进程自己的"
            "文件系统和网络权限运行"
        ),
        "isolation.off.remedy": (
            "除非你在专门调试沙箱本身，否则不要设置它（保持 auto）。"
        ),
        "isolation.misconfigured": "内核沙箱配置有误，任何单元都无法运行：{error}",
        "isolation.misconfigured.remedy": (
            "修正 OPENAI4S_KERNEL_SANDBOX / OPENAI4S_KERNEL_ALLOW_RAW_NETWORK "
            "的值；沙箱只接受 auto、enforce 或 off。"
        ),
        "isolation.enforce_unavailable": (
            "OPENAI4S_KERNEL_SANDBOX=enforce，但无法建立隔离边界：{error}"
        ),
        "isolation.enforce.remedy": (
            "安装 bubblewrap（Linux），或在可用 Seatbelt 的 macOS 上运行；"
            "enforce 会有意拒绝在没有隔离的情况下运行。"
        ),
        "isolation.unprobed": "无法探测沙箱：{error}",
        "isolation.unprobed.remedy": (
            "用 OPENAI4S_KERNEL_SANDBOX=enforce 运行，让这种情况直接失败，"
            "而不是降级运行。"
        ),
        "isolation.active": "内核沙箱已通过 {backend} 启用（模式 {mode}）；自检通过",
        "isolation.unavailable": "不可用",
        "isolation.enforce_not_holding": (
            "OPENAI4S_KERNEL_SANDBOX=enforce，但隔离边界不成立：{reason}"
        ),
        "isolation.self_test_failed": (
            "{backend} 已安装，但自检失败，单元将在没有隔离的情况下运行：{reason}"
        ),
        "isolation.self_test_failed.remedy": (
            "修复这个后端（在 Linux 上通常是非特权用户命名空间被禁用）；"
            "OPENAI4S_KERNEL_SANDBOX=enforce 会直接拒绝，而不是降级运行。"
        ),
        "isolation.no_backend": (
            "没有可用的沙箱后端（{reason}）；因为模式是 {mode}，单元将在没有"
            "隔离的情况下运行"
        ),
        "isolation.no_backend.remedy": (
            "这是明确提示的降级，不是静默降级。改用 "
            "OPENAI4S_KERNEL_SANDBOX=enforce 则会直接拒绝。"
        ),
        # data
        "data.remedy": (
            "把 OPENAI4S_DATA_DIR 指向当前用户可写的目录，或修正它所指目录的权限。"
        ),
        "data.not_a_directory": "{target} 不是目录，无法在其中保存任何内容",
        "data.not_writable": "当前用户无法写入 {probe}",
        "data.future_schema": "{db_path} 处的数据库来自更新的版本：{error}",
        "data.future_schema.remedy": (
            "请运行升级过这个数据目录的 OpenAI4S 版本，或把 OPENAI4S_DATA_DIR "
            "指向由当前版本创建的目录。"
        ),
        "data.unprepared": "无法准备 {target} 处的数据目录：{error}",
        "data.migration_incomplete": (
            "{db} 处的数据库处于 schema {version}，之前升级到 schema "
            "{supported} 的迁移没有完成：升级前的备份只有在升级成功后才会删除，"
            "现在仍在 {backup}"
        ),
        "data.migration_incomplete.remedy": (
            "在前台运行 `openai4s serve` 或 `openai4s run` 查看原因：失败的迁移"
            "会被回滚，并打印为一行 `error:`（退出码 2），重新运行是安全的。"
            "在升级成功之前请保留这份备份（docs/upgrading.md）。"
        ),
        "data.upgrade_pending": (
            "{db} 处的数据库处于 schema {version}；下一次 `openai4s serve` 或 "
            "`openai4s run` 会把它迁移到 schema {supported}，重新安装旧版本也"
            "无法撤销。doctor 不做迁移，所以需要读取数据库的检查都没有打开它"
        ),
        "data.upgrade_pending.remedy": (
            "先备份数据目录（见 docs/upgrading.md 中你升级前所用版本的那一节），"
            "然后启动一次 `openai4s serve` 或运行一次 `openai4s run`，再重新运行 "
            "`openai4s doctor`。"
        ),
        "data.interrupted_write": (
            "{db_path} 处的数据库有一次中断的写入，只有以读写方式打开才能恢复；"
            "doctor 不会打开它，所以它的 schema 版本未知，需要读取数据库的检查"
            "也都没有打开它"
        ),
        "data.interrupted_write.remedy": (
            "把数据库连同它的 -journal 文件一起复制备份，然后启动一次 "
            "`openai4s serve` 或运行一次 `openai4s run`，让 SQLite 恢复它，"
            "再重新运行 `openai4s doctor`。"
        ),
        "data.ok": "可用，位于 {target}",
        # disk
        "disk.unmeasured": "无法测量 {probe} 的剩余空间：{error}",
        "disk.low": "{target} 只剩 {free_gb} GB，运行很可能中途失败",
        "disk.low.remedy": "请释放空间，或把 OPENAI4S_DATA_DIR 指向更大的磁盘。",
        "disk.ok": "{target} 剩余 {free_gb} GB",
        # connectors
        "connectors.unavailable": "科学数据连接器不可用：{error}",
        "connectors.store_error": (
            "内置了 {count} 个科学数据库，但无法从数据库读取已配置的连接器：{error}"
        ),
        "connectors.store_error.remedy": (
            "请先解决上面的数据库错误；`openai4s serve` 和 `openai4s run` "
            "也无法打开它。"
        ),
        "connectors.offline": (
            "内置了 {count} 个科学数据库，但网络已关闭"
            "（OPENAI4S_ALLOW_NETWORK=0），都无法访问"
        ),
        "connectors.offline.remedy": (
            "在 设置 → 网络 中开启，或设置 OPENAI4S_ALLOW_NETWORK=1。"
        ),
        "connectors.built_in": "内置 {count} 个科学数据库",
        "connectors.configured": "，已配置 {count} 个连接器",
        "connectors.allowlist": "；已启用出站白名单（{count} 个域名）",
        "connectors.not_probed": "；未探测可达性（运行 `openai4s doctor --network` 探测）",
        "connectors.reachable": "；{total} 个中有 {up} 个可达",
        "connectors.unreachable": "（不可达：{names}）",
        "connectors.unreachable.remedy": (
            "原因见下面每个主机的一行；TLS 失败会指出主机以及需要设置什么。"
        ),
        # judgment
        "judgment.disabled": "未启用（实验功能，默认关闭）",
        "judgment.key_unreadable": "已启用，但无法读取 TypeSafe API Key",
        "judgment.key_unreadable.remedy": (
            "等密钥存储可用后重试，或在无界面环境中设置 OPENAI4S_TYPESAFE_API_KEY。"
        ),
        "judgment.no_main_key": "已启用，但没有配置主模型的凭据",
        "judgment.no_main_key.remedy": "在 设置 → 模型 中配置主模型。",
        "judgment.no_typesafe_key": "已启用，但没有配置 TypeSafe API Key",
        "judgment.no_typesafe_key.remedy": (
            "在 设置 → 实验功能 中填写这个 Key，或设置 OPENAI4S_TYPESAFE_API_KEY。"
        ),
        "judgment.not_allowlisted": "已启用，但 {host} 不在出站白名单中",
        "judgment.not_allowlisted.remedy": (
            "调用 host.request_network_access(domain={host!r})，请用户批准放宽白名单。"
        ),
        "judgment.enabled": "已启用（{provider}/{model}）",
        # remote
        "remote.byoc": "{count} 个 BYOC 提供方",
        "remote.ssh_family": "ssh 远程计算",
        "remote.invalid_confinement": (
            "已配置 {configured}，但隔离模式无效，远程计算无法启动：{error}"
        ),
        "remote.invalid_confinement.remedy": (
            "把 OPENAI4S_COMPUTE_CONFINEMENT 设为 auto、enforce 或 off。"
        ),
        "remote.invalid_confinement_unused": (
            "OPENAI4S_COMPUTE_CONFINEMENT 无效（{error}）；它必须是 auto、"
            "enforce 或 off"
        ),
        "remote.invalid_confinement_unused.remedy": "请修正它，或取消设置。",
        "remote.none": "没有配置远程计算；全部在本机运行",
        "remote.enforce_unsatisfied": (
            "已配置 {count} 个 BYOC 提供方，且 OPENAI4S_COMPUTE_CONFINEMENT="
            "enforce，但这台主机无法满足：{detail}"
        ),
        "remote.enforce_unsatisfied.remedy": (
            "安装这个平台的隔离后端（Linux 上是 bubblewrap），或改用不需要辅助"
            "进程隔离的 ssh 方式。设为 `auto` 则意味着接受没有隔离的远程执行。"
        ),
        "remote.unconfined": (
            "{count} 个 BYOC 提供方在没有操作系统隔离边界的情况下运行：{detail}"
        ),
        "remote.unconfined.remedy": (
            "设置 OPENAI4S_COMPUTE_CONFINEMENT=enforce，拒绝 BYOC 操作，而不是"
            "在没有隔离的情况下运行提供方的辅助进程。"
        ),
        "remote.ssh_enabled": "已启用 ssh 远程计算",
        "remote.separator": "；",
        "check.crashed": "{name} 检查本身出错：{error!r}",
        # render
        "render.after_timeout": "（超时一次后成功）",
        "render.timed_out": "超时：两次尝试都没有在 {seconds:g} 秒内得到回应",
        f"render.verdict.{OK}": "所有检查都已通过。",
        f"render.verdict.{WARN}": "可以使用，但有上面列出的降级。",
        f"render.verdict.{FAIL}": "尚未就绪：至少有一项检查失败。",
    },
}


__all__ = [
    "Check",
    "FAIL",
    "MISSING_ENVIRONMENTS_REMEDY",
    "OK",
    "SCIENCE_HOSTS",
    "SIDE_EFFECT_CHECKS",
    "WARN",
    "passive_status",
    "probe_science_hosts",
    "render",
    "report",
    "run_checks",
    "send_test_request",
    "unopened_database",
]
