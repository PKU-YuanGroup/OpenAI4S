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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

OK = "ok"
WARN = "warn"
FAIL = "fail"

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


def _store_for(cfg: Any) -> Any:
    """The store, or None when it cannot be opened.

    Several checks need to see what the UI configured, and none of them may
    fail because the database is missing — a fresh install is exactly when this
    command gets run.
    """
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

    llm = resolve_llm_config(cfg.llm, _store_for(cfg))
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
    # The value is never reported, only whether one resolved.
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
            f"{base_url} (no credential required)",
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
    try:
        from openai4s.kernel.environments import discover_environments

        envs = discover_environments()
        facts["environments"] = sorted(getattr(e, "name", str(e)) for e in envs)
    except Exception as e:  # noqa: BLE001
        facts["environments_error"] = str(e)
        envs = []

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
    if not envs:
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
    detail = f"{len(envs)} environment(s) available"
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

    try:
        from openai4s.store import get_store

        store = get_store(cfg.db_path)
        # Names and configured-ness only. Values never leave the store.
        connectors = store.list_connectors()
        facts["configured_connectors"] = len(connectors)
    except Exception as e:  # noqa: BLE001
        facts["connector_store_error"] = str(e)

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
    ("model", _model),
    ("runtime", _runtime),
    ("isolation", _isolation),
    ("disk", _disk),
    ("connectors", _connectors),
    ("remote", _remote),
)


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
    checks = run_checks(cfg)
    worst = (
        FAIL
        if any(c.status == FAIL for c in checks)
        else (WARN if any(c.status == WARN for c in checks) else OK)
    )
    return {"status": worst, "checks": [c.public() for c in checks]}


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


__all__ = ["Check", "FAIL", "OK", "WARN", "render", "report", "run_checks"]
