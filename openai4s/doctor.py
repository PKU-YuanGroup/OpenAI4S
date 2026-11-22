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

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "remedy": self.remedy,
            "facts": self.facts,
        }


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
) -> tuple[str, str, str]:
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
        note = "saved by Settings -> Models or `openai4s init`"
        if env_value and env_value != saved:
            return (
                "settings",
                f"{note}; this overrides {env_name}={env_value!r}",
                env_name,
            )
        return "settings", note, ""
    if env_value and env_value == model:
        return "environment", f"from {env_name}", ""
    if model and model == default_model:
        return "default", "provider default", ""
    return "config", "", ""


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
        return Check(
            "model",
            FAIL,
            f"provider {llm.provider!r} is not one this build knows: {e}",
            "Set OPENAI4S_LLM_PROVIDER to a supported provider, or configure "
            "one in the UI under Settings -> Models.",
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
    named = f"model {model!r}" + (f" ({source_note})" if source_note else "")
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
        return Check(
            "model",
            WARN,
            f"provider {llm.provider!r} resolves to model {model!r} with no API "
            f"key in the process configuration, and doctor did not open the "
            f"database that holds the Settings -> Models settings (see the "
            f"data check)",
            f"Resolve the data check and rerun `openai4s doctor`, or set "
            f"OPENAI4S_{llm.provider.upper()}_API_KEY (or the generic "
            f"OPENAI4S_LLM_API_KEY).",
            facts,
        )
    if not llm.api_key and not keyless:
        return Check(
            "model",
            FAIL,
            f"provider {llm.provider!r} resolves to model {model!r}, but no API "
            f"key is configured",
            f"Set OPENAI4S_{llm.provider.upper()}_API_KEY (or the generic "
            f"OPENAI4S_LLM_API_KEY), or add it in the UI under "
            f"Settings -> Models.",
            facts,
        )
    if keyless and not llm.api_key:
        # Ollama, LM Studio, vLLM and llama.cpp authenticate by being
        # unreachable from anywhere else. Demanding a key from them is
        # demanding a credential that does not exist.
        return Check(
            "model",
            OK,
            f"provider {llm.provider!r}, {named}, local endpoint "
            f"{_sanitize_endpoint(base_url)} (no credential required)",
            facts=facts,
        )
    return Check(
        "model",
        OK,
        f"provider {llm.provider!r}, {named}, credential configured "
        f"({_NOT_VERIFIED})",
        facts=facts,
    )


#: What the model line says while nothing has asked the provider. A configured
#: key is not a working key, and "credential configured" beside "All checks
#: passed" read as if it were.
_NOT_VERIFIED = (
    "not verified: no request was sent to the provider; "
    "`openai4s doctor --network` sends one"
)

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


def _live_request_forbidden(cfg: Any) -> str:
    """Why no test request may be sent, or "" when one may."""
    from openai4s import webtools

    if not webtools.network_allowed():
        return "OPENAI4S_ALLOW_NETWORK=0"
    store = _store_for(cfg)
    try:
        if store is not None and store.get_setting("network_enabled") == "0":
            return "switched off in Settings -> Network"
    except Exception:  # noqa: BLE001 - an unreadable toggle forbids nothing
        pass
    return ""


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
    forbidden = _live_request_forbidden(cfg)
    if forbidden:
        facts["live_check"] = "skipped"
        return Check(
            "model",
            OK,
            check.detail.replace(
                _NOT_VERIFIED, f"not verified: networking is disabled, {forbidden}"
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
            return Check(
                "model",
                FAIL,
                f"provider {provider!r} rejected the configured API key "
                f"(HTTP {status} from {endpoint}), so no turn can run",
                f"Enter a valid key with `openai4s init` or in the UI under "
                f"Settings -> Models, or set "
                f"OPENAI4S_{str(provider).upper()}_API_KEY (or the generic "
                f"OPENAI4S_LLM_API_KEY).",
                facts,
            )
        if status == 404:
            facts["live_check"] = "model_not_found"
            return Check(
                "model",
                FAIL,
                f"provider {provider!r} has no model {model!r} at {endpoint} "
                f"(HTTP 404): the model id or the base URL is wrong",
                "Choose a model this endpoint serves with `openai4s init "
                "--model <id>` or in the UI under Settings -> Models.",
                facts,
            )
        if status is not None:
            facts["live_check"] = "inconclusive"
            return Check(
                "model",
                WARN,
                f"provider {provider!r} answered HTTP {status} to a test "
                f"request for model {model!r}, so the key and model are not "
                f"verified",
                "Rerun `openai4s doctor` in a moment; if it persists, check "
                "the model id, base URL and account quota.",
                facts,
            )
        from openai4s.llm.models import LLMDeadlineExceeded, TransportError

        if isinstance(e, TransportError):
            facts["live_check"] = "unreachable"
            how = (
                "the request timed out"
                if isinstance(e, LLMDeadlineExceeded)
                else "the connection failed"
            )
            return Check(
                "model",
                WARN,
                f"could not reach {endpoint} to verify the key and model "
                f"({how}); provider {provider!r}, model {model!r}",
                "Check the network, the proxy and the base URL, then rerun; "
                "`openai4s doctor` without `--network` skips this request.",
                facts,
            )
        facts["live_check"] = "inconclusive"
        return Check(
            "model",
            WARN,
            f"the test request to provider {provider!r} for model {model!r} "
            f"did not complete ({type(e).__name__}), so the key and model are "
            f"not verified",
            "Rerun `openai4s doctor --network`; without `--network` nothing is sent.",
            facts,
        )
    facts["live_check"] = "ok"
    return Check(
        "model",
        OK,
        check.detail.replace(
            f"credential configured ({_NOT_VERIFIED})",
            "credential verified (the provider answered a test request)",
        ),
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
            return Check(
                "runtime",
                OK,
                "standard profile ready: all recommended Python and R "
                "packages are present in local metadata",
                facts=facts,
            )
        return Check(
            "runtime",
            FAIL,
            readiness_failure_message(readiness),
            "Run the explicit managed plan/apply commands above, then rerun "
            "`openai4s doctor`.",
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
    local_clause = (
        f"; {local} local conda environment(s) of your own are selectable but "
        "not managed by OpenAI4S"
        if local
        else ""
    )

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
            f"R cells will run against {r_path}" if r_path else "R cells will not run"
        )
        return Check(
            "runtime",
            WARN,
            "no managed environment built; Python cells will run in the "
            f"built-in interpreter (the daemon's own) and {r_clause}{local_clause}",
            MISSING_ENVIRONMENTS_REMEDY,
            facts,
        )
    detail = (
        f"built-in interpreter + {len(prebuilt)} managed environment(s)"
        f"{local_clause}"
    )
    if not r_path:
        return Check(
            "runtime",
            WARN,
            f"{detail}, but no Rscript could be resolved — R cells will not run",
            "Run `openai4s setup --only r` if you need the R channel.",
            facts,
        )
    return Check("runtime", OK, f"{detail}, Rscript at {r_path}", facts=facts)


def _with_allowlist_boundary(check: Check) -> Check:
    """Fail an unproven kernel when Cells also require an allowlist boundary."""

    from openai4s.egress import cell_admission_refusal, egress_mode

    if egress_mode() != "allowlist":
        return check
    decision = cell_admission_refusal(check.facts)
    if decision is None:
        return check
    code = str(decision.get("code") or "egress_boundary_unavailable")
    # A check that already failed has its own detail and remedy. Prefix the
    # stable code and leave that text in place. A warning or an otherwise
    # passing probe has no failure of its own, so the boundary copy is the
    # explanation.
    if check.status == FAIL:
        detail = str(check.detail or "")
        prefix = f"{code}: "
        if not detail.startswith(prefix):
            detail = prefix + detail
        return Check(
            check.name,
            FAIL,
            detail,
            check.remedy,
            dict(check.facts),
        )
    remedy = " ".join(str(item) for item in decision.get("remedy") or [])
    reason = str(decision.get("reason") or check.detail)
    return Check(
        check.name,
        FAIL,
        f"{code}: {reason}",
        remedy or check.remedy,
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
        return Check(
            "isolation",
            WARN,
            "kernel sandbox is disabled (OPENAI4S_KERNEL_SANDBOX=off); cells "
            "run with the daemon's own filesystem and network reach",
            "Leave it unset (auto) unless you are deliberately debugging the "
            "sandbox itself.",
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
        return Check(
            "isolation",
            FAIL,
            f"the kernel sandbox is misconfigured, so no cell can run: {e}",
            "Fix the OPENAI4S_KERNEL_SANDBOX / OPENAI4S_KERNEL_ALLOW_RAW_NETWORK "
            "value; the sandbox accepts only auto, enforce, or off.",
            facts,
        )
    except Exception as e:  # noqa: BLE001 - in `enforce` this is the refusal
        facts.setdefault("available", False)
        if mode == "enforce":
            return Check(
                "isolation",
                FAIL,
                f"OPENAI4S_KERNEL_SANDBOX=enforce and the boundary could not be "
                f"established: {e}",
                "Install bubblewrap (Linux) or run on macOS with Seatbelt "
                "available; enforce deliberately refuses to run unconfined.",
                facts,
            )
        return Check(
            "isolation",
            WARN,
            f"the sandbox could not be probed: {e}",
            "Run with OPENAI4S_KERNEL_SANDBOX=enforce to make this fatal "
            "rather than a degradation.",
            facts,
        )
    finally:
        if sandbox is not None:
            try:
                sandbox.close()
            except Exception:  # noqa: BLE001
                pass

    if facts.get("enforced"):
        return Check(
            "isolation",
            OK,
            f"kernel sandbox active via {facts['backend']} (mode {mode}); "
            f"self-test passed",
            facts=facts,
        )
    if mode == "enforce":
        return Check(
            "isolation",
            FAIL,
            f"OPENAI4S_KERNEL_SANDBOX=enforce but the boundary does not hold: "
            f"{reason or 'unavailable'}",
            "Install bubblewrap (Linux) or run on macOS with Seatbelt "
            "available; enforce deliberately refuses to run unconfined.",
            facts,
        )
    # Present-but-not-holding is the case worth separating: it is the one a
    # user is most likely to have assumed was fine.
    if facts.get("available") and facts.get("self_test_passed") is False:
        return Check(
            "isolation",
            WARN,
            f"{facts['backend']} is installed but its self-test failed, so "
            f"cells run unconfined: {warning or reason or 'unavailable'}",
            "Fix the backend (on Linux, unprivileged user namespaces are the "
            "usual cause) — OPENAI4S_KERNEL_SANDBOX=enforce refuses instead of "
            "degrading.",
            facts,
        )
    return Check(
        "isolation",
        WARN,
        f"no sandbox backend available ({reason or 'unavailable'}); cells run "
        f"unconfined because the mode is {mode}",
        "This is a visible degradation, not a silent one. Use "
        "OPENAI4S_KERNEL_SANDBOX=enforce to refuse instead.",
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
    remedy = (
        "Point OPENAI4S_DATA_DIR at a directory this user can write to, or fix "
        "the permissions on the one it names."
    )
    if target.exists() and not target.is_dir():
        return Check(
            "data",
            FAIL,
            f"{target} is not a directory, so nothing can be stored in it",
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
            return Check(
                "data",
                FAIL,
                f"{probe} is not writable by this user",
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
            return Check(
                "data",
                FAIL,
                f"the database at {db_path} is from a newer release: {e}",
                "Run the OpenAI4S release that upgraded this data directory, or "
                "point OPENAI4S_DATA_DIR at one this release created.",
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
            return Check(
                "data",
                FAIL,
                f"the data directory at {target} could not be prepared: {e}",
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
            return Check(
                "data",
                FAIL,
                f"the database at {db} is at schema {version}, and an earlier "
                f"migration to schema {supported} did not complete: its "
                f"pre-upgrade backup, which is deleted only when the upgrade "
                f"succeeds, is still at {backup}",
                "Run `openai4s serve` or `openai4s run` in the foreground to see "
                "why: a failed migration is rolled back and printed as one "
                "`error:` line (exit 2), and re-running it is safe. Keep the "
                "backup until an upgrade succeeds (docs/upgrading.md).",
                facts,
            )
        return Check(
            "data",
            WARN,
            f"the database at {db} is at schema {version}; the next `openai4s "
            f"serve` or `openai4s run` migrates it to schema {supported}, which "
            f"reinstalling an older release does not undo. doctor does not "
            f"migrate it, so the checks that read the database did not open it",
            "Back up the data directory first (docs/upgrading.md, the section "
            "for the release you are upgrading from), "
            "then start `openai4s serve` or run `openai4s run` once and rerun "
            "`openai4s doctor`.",
            facts,
        )
    if unopened is not None and unopened["reason"] == "interrupted_write":
        return Check(
            "data",
            WARN,
            f"the database at {db_path} holds an interrupted write that only a "
            f"read-write open recovers; doctor does not open it, so its schema "
            f"version is unknown and the checks that read the database did not "
            f"open it",
            "Copy the database together with its -journal file, then start "
            "`openai4s serve` or run `openai4s run` once to let SQLite recover "
            "it, and rerun `openai4s doctor`.",
            facts,
        )
    return Check("data", OK, f"usable at {target}", facts=facts)


def _disk(cfg: Any) -> Check:
    """Is there room for a scientific run's artifacts?"""
    target = Path(cfg.data_dir)
    probe = target if target.exists() else target.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError as e:
        return Check("disk", WARN, f"could not measure free space at {probe}: {e}")
    free_gb = round(usage.free / 1e9, 1)
    facts = {"data_dir": str(target), "free_gb": free_gb}
    if free_gb < _LOW_DISK_GB:
        return Check(
            "disk",
            FAIL,
            f"{free_gb} GB free at {target} — a run is likely to fail partway",
            "Free space, or point OPENAI4S_DATA_DIR at a larger volume.",
            facts,
        )
    return Check("disk", OK, f"{free_gb} GB free at {target}", facts=facts)


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
        return Check("connectors", WARN, f"science connectors unavailable: {e}")

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
        return Check(
            "connectors",
            WARN,
            f"{len(facts['science_databases'])} science databases are built in, "
            f"but the configured connectors could not be read from the "
            f"database: {store_error}",
            "Resolve the database error above; `openai4s serve` and `openai4s "
            "run` cannot open it either.",
            facts,
        )
    if not network:
        return Check(
            "connectors",
            WARN,
            f"{len(facts['science_databases'])} science databases are built in, "
            f"but networking is disabled (OPENAI4S_ALLOW_NETWORK=0), so none "
            f"can be reached",
            "Enable it in Settings -> Network, or set " "OPENAI4S_ALLOW_NETWORK=1.",
            facts,
        )
    detail = f"{len(facts['science_databases'])} science databases built in"
    if "configured_connectors" in facts:
        detail += f", {facts['configured_connectors']} connector(s) configured"
    if mode == "allowlist":
        granted = sorted(egress.granted_domains())
        facts["egress_allowlist_size"] = len(granted)
        detail += f"; egress allowlist enforced ({len(granted)} domain(s))"
    facts["reachability_probed"] = probe_hosts
    if not probe_hosts:
        # Being built in says nothing about being reachable, and this run did
        # not look. Say so, rather than let "ok" be read as "connected".
        detail += "; reachability not probed (run `openai4s doctor --network`)"
        return Check("connectors", OK, detail, facts=facts)
    reachability = probe_science_hosts()
    facts["reachability"] = reachability
    down = [db for db, answer in reachability.items() if not answer["reachable"]]
    detail += f"; {len(reachability) - len(down)} of {len(reachability)} reachable"
    if down:
        return Check(
            "connectors",
            WARN,
            detail + f" (unreachable: {', '.join(down)})",
            "See the per-host lines for the reason; a TLS failure names the "
            "host and what to set.",
            facts,
        )
    return Check("connectors", OK, detail, facts=facts)


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
        return Check(
            "judgment",
            OK,
            "disabled (experimental, default off)",
            facts=facts,
        )
    try:
        if is_llm:
            from openai4s.judgment.settings import status as judgment_status

            configured = bool(judgment_status(cfg, store)["key_configured"])
        else:
            configured = key_is_configured(store)
    except Exception as e:  # noqa: BLE001 - a secret-store fault is a finding
        facts["key_configured"] = False
        facts["key_store_error"] = type(e).__name__
        return Check(
            "judgment",
            WARN,
            "enabled, but the TypeSafe API key could not be read",
            "Retry after the secret store is available, or set "
            "OPENAI4S_TYPESAFE_API_KEY for headless use.",
            facts,
        )
    facts["key_configured"] = configured
    if not configured:
        if is_llm:
            return Check(
                "judgment",
                WARN,
                "enabled, but no main model credential is configured",
                "Configure the main model in Settings -> Models.",
                facts,
            )
        return Check(
            "judgment",
            WARN,
            "enabled, but no TypeSafe API key is configured",
            "Set the key in Settings -> Experimental, or set "
            "OPENAI4S_TYPESAFE_API_KEY.",
            facts,
        )
    if report["mode"] == "allowlist" and not report["domain_allowed"]:
        remedy = report["remediation"] or (
            f"Call host.request_network_access(domain={host!r}) "
            "to ask the user to approve widening it."
        )
        return Check(
            "judgment",
            WARN,
            f"enabled, but {host} is not on the egress allowlist",
            remedy,
            facts,
        )
    return Check(
        "judgment",
        OK,
        f"enabled ({flags.provider}/{flags.model})",
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
                f"{len(providers)} BYOC provider(s)"
                if providers
                else "the ssh remote-compute family"
            )
            return Check(
                "remote",
                FAIL,
                f"{configured} present but the confinement mode is invalid, so "
                f"remote compute cannot start: {e}",
                "Set OPENAI4S_COMPUTE_CONFINEMENT to auto, enforce, or off.",
                facts,
            )
        return Check(
            "remote",
            WARN,
            f"OPENAI4S_COMPUTE_CONFINEMENT is invalid ({e}); it must be auto, "
            f"enforce, or off",
            "Fix or unset it.",
            facts,
        )
    facts["confinement_mode"] = confinement

    if not providers and not ssh_available:
        return Check(
            "remote",
            OK,
            "no remote compute configured; everything runs locally",
            facts=facts,
        )
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
            return Check(
                "remote",
                FAIL,
                f"{len(providers)} BYOC provider(s) present and "
                f"OPENAI4S_COMPUTE_CONFINEMENT=enforce, which this host cannot "
                f"satisfy: {posture['detail']}",
                "Install the backend for this platform (bubblewrap on Linux), "
                "or use the ssh family, which needs no helper confinement. "
                "Setting `auto` accepts unconfined remote execution.",
                facts,
            )
        if not posture["enforced"]:
            return Check(
                "remote",
                WARN,
                f"{len(providers)} BYOC provider(s) run without an OS "
                f"boundary: {posture['detail']}",
                "Set OPENAI4S_COMPUTE_CONFINEMENT=enforce to refuse byoc ops "
                "rather than run the provider helper unconfined.",
                facts,
            )
    parts = []
    if providers:
        parts.append(f"{len(providers)} BYOC provider(s)")
    if ssh_available:
        parts.append("ssh family enabled")
    return Check("remote", OK, "; ".join(parts), facts=facts)


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
            results.append(Check(name, FAIL, f"the {name} check itself failed: {e!r}"))
    return results


def report(
    cfg: Any,
    *,
    network: bool = False,
    live_model: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Full probe set, including checks that write or spawn.

    The CLI wants the real answer, so this still runs `SIDE_EFFECT_CHECKS`.
    The web Diagnostics GET must call `passive_status()` instead.

    ``live_model`` is the one request that leaves the machine: given
    `send_test_request` (the CLI does with `--network`), the model check is
    confirmed with the provider. Without it the check is configuration only
    and says so.
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
    return {"status": worst, "checks": [c.public() for c in checks]}


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


def render(result: dict[str, Any]) -> str:
    """The human form. One line per check, remedies indented beneath."""
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
                    outcome += " (after one timeout)"
            else:
                outcome = f"FAIL  {answer['error']}"
            lines.append(f"              {database:<10} {answer['host']:<26} {outcome}")
    lines.append("")
    if result["status"] == OK:
        lines.append("All checks passed.")
    elif result["status"] == WARN:
        lines.append("Usable, with degradations noted above.")
    else:
        lines.append("Not ready: at least one check failed.")
    return "\n".join(lines)


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
