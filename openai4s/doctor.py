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


def _model(cfg: Any) -> Check:
    """Can we reach a model at all? Configuration only — no network call.

    Resolved the way a real turn resolves it: process config **plus** the
    Customize → Models settings held in the store. Reading `cfg.llm` alone
    diagnosed the documented setup path as broken — the daemon boots with no
    key and the model is configured from the UI — so an install that worked
    was told its model check had failed.
    """
    from openai4s.llm.resolve import is_loopback_endpoint, resolve_llm_config

    unopened = unopened_database(cfg)
    llm = resolve_llm_config(cfg.llm, None if unopened else _store_for(cfg))
    try:
        from openai4s.llm.registry import provider_spec

        spec = provider_spec(llm.provider)
    except Exception as e:  # noqa: BLE001 - an unknown provider is the finding
        return Check(
            "model",
            FAIL,
            f"provider {llm.provider!r} is not one this build knows: {e}",
            "Set OPENAI4S_LLM_PROVIDER to a supported provider, or configure "
            "one in the UI under Customize -> Models.",
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
    facts = {
        "provider": llm.provider,
        "model": model,
        "api_key_configured": bool(llm.api_key),
        "endpoint_is_local": keyless,
    }
    if unopened is not None:
        facts["database_settings_read"] = False
    # The value is never reported, only whether one resolved.
    if not llm.api_key and not keyless and unopened is not None:
        # The key may be saved under Customize -> Models, in the database this
        # run left closed; "no key" would be a guess reported as a failure.
        return Check(
            "model",
            WARN,
            f"provider {llm.provider!r} resolves to model {model!r} with no API "
            f"key in the process configuration, and doctor did not open the "
            f"database that holds the Customize -> Models settings (see the "
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
            f"Customize -> Models.",
            facts,
        )
    if keyless and not llm.api_key:
        # Ollama, LM Studio, vLLM and llama.cpp authenticate by being
        # unreachable from anywhere else. Demanding a key from them is
        # demanding a credential that does not exist.
        return Check(
            "model",
            OK,
            f"provider {llm.provider!r}, model {model!r}, local endpoint "
            f"{_sanitize_endpoint(base_url)} (no credential required)",
            facts=facts,
        )
    return Check(
        "model",
        OK,
        f"provider {llm.provider!r}, model {model!r}, credential configured",
        facts=facts,
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
    prebuilt = [e for e in envs if getattr(e, "name", "") != "base"]
    facts["prebuilt_environments"] = len(prebuilt)

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
            "no prebuilt environment found; Python cells will run in the "
            f"daemon's own interpreter and {r_clause}",
            "Run `openai4s setup` (or `./setup.sh --with-kernel-envs`) to build "
            "the Python and R environments.",
            facts,
        )
    detail = f"{len(prebuilt)} prebuilt environment(s) available"
    if not r_path:
        return Check(
            "runtime",
            WARN,
            f"{detail}, but no Rscript could be resolved — R cells will not run",
            "Run `openai4s setup --only r` if you need the R channel.",
            facts,
        )
    return Check("runtime", OK, f"{detail}, Rscript at {r_path}", facts=facts)


def _isolation(cfg: Any) -> Check:
    """Is the kernel sandbox actually going to be applied?"""
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
    if probe.exists() and not os.access(probe, os.W_OK | os.X_OK):
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
            "Back up the data directory first (docs/upgrading.md, section 1), "
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


def _connectors(cfg: Any) -> Check:
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
            "Enable it in Customize -> Network, or set " "OPENAI4S_ALLOW_NETWORK=1.",
            facts,
        )
    detail = f"{len(facts['science_databases'])} science databases built in"
    if "configured_connectors" in facts:
        detail += f", {facts['configured_connectors']} connector(s) configured"
    if mode == "allowlist":
        granted = sorted(egress.granted_domains())
        facts["egress_allowlist_size"] = len(granted)
        detail += f"; egress allowlist enforced ({len(granted)} domain(s))"
    return Check("connectors", OK, detail, facts=facts)


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
)

#: Probes `report()` still runs for the CLI, and that a page-load GET must not.
#: `model` and `connectors` open the Store too, but only a database already at
#: this release's schema (`unopened_database`): no probe upgrades one.
#: `data` calls `ensure_dirs()` (a write). `isolation` builds a temp sandbox
#: and runs the kernel self-test (a subprocess). `remote` runs the BYOC
#: confinement self-test (another subprocess). `runtime` walks environment
#: roots. The web Diagnostics GET uses `passive_status()` instead of this set.
SIDE_EFFECT_CHECKS = frozenset({"data", "isolation", "remote", "runtime"})


def run_checks(cfg: Any) -> list[Check]:
    """Every probe, in order. A probe that raises becomes a failed check.

    A crash here would deny the report to the person who most needs it, so an
    unexpected exception is itself a finding rather than a traceback.
    """
    results: list[Check] = []
    for name, probe in _CHECKS:
        try:
            results.append(probe(cfg))
        except Exception as e:  # noqa: BLE001
            results.append(Check(name, FAIL, f"the {name} check itself failed: {e!r}"))
    return results


def report(cfg: Any) -> dict[str, Any]:
    """Full probe set, including checks that write or spawn.

    The CLI wants the real answer, so this still runs `SIDE_EFFECT_CHECKS`.
    The web Diagnostics GET must call `passive_status()` instead.
    """
    checks = run_checks(cfg)
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
    "OK",
    "SIDE_EFFECT_CHECKS",
    "WARN",
    "passive_status",
    "render",
    "report",
    "run_checks",
    "unopened_database",
]
