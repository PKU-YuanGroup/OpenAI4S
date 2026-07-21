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


def _model(cfg: Any) -> Check:
    """Can we reach a model at all? Configuration only — no network call."""
    llm = cfg.llm
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
    # The value is never reported, only whether one resolved.
    if not llm.api_key:
        return Check(
            "model",
            FAIL,
            f"provider {llm.provider!r} resolves to model {model!r}, but no API "
            f"key is configured",
            f"Set OPENAI4S_{llm.provider.upper()}_API_KEY (or the generic "
            f"OPENAI4S_LLM_API_KEY), or add it in the UI under "
            f"Customize -> Models.",
            {"provider": llm.provider, "model": model, "api_key_configured": False},
        )
    return Check(
        "model",
        OK,
        f"provider {llm.provider!r}, model {model!r}, credential configured",
        facts={"provider": llm.provider, "model": model, "api_key_configured": True},
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

    r_path = shutil.which("Rscript")
    facts["rscript"] = bool(r_path)
    if not envs:
        return Check(
            "runtime",
            WARN,
            "no prebuilt environment found; Python cells will run in the "
            "daemon's own interpreter and R cells will not run",
            "Run `openai4s setup` (or `./setup.sh --with-kernel-envs`) to build "
            "the Python and R environments.",
            facts,
        )
    detail = f"{len(envs)} environment(s) available"
    if not r_path:
        return Check(
            "runtime",
            WARN,
            f"{detail}, but Rscript is not on PATH — R cells will not run",
            "Run `openai4s setup --only r` if you need the R channel.",
            facts,
        )
    return Check("runtime", OK, f"{detail}, Rscript present", facts=facts)


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
    try:
        import platform as _platform

        from openai4s.security.sandbox import _detect_backend

        # Detection only. Constructing a real sandbox would allocate a private
        # temp directory and run the self-test, which is a side effect a
        # diagnostic has no business causing.
        backend, _executable, reason = _detect_backend(
            platform_name=_platform.system().lower(), which=shutil.which
        )
        facts["available"] = backend is not None
        facts["backend"] = backend
        reason = reason or ""
    except Exception as e:  # noqa: BLE001
        return Check(
            "isolation",
            WARN,
            f"the sandbox could not be probed: {e}",
            "Run with OPENAI4S_KERNEL_SANDBOX=enforce to make this fatal "
            "rather than a degradation.",
            facts,
        )
    if facts["available"]:
        return Check(
            "isolation",
            OK,
            f"kernel sandbox active via {facts['backend']} (mode {mode})",
            facts=facts,
        )
    if mode == "enforce":
        return Check(
            "isolation",
            FAIL,
            f"OPENAI4S_KERNEL_SANDBOX=enforce but no backend is usable: "
            f"{reason or 'unavailable'}",
            "Install bubblewrap (Linux) or run on macOS with Seatbelt "
            "available; enforce deliberately refuses to run unconfined.",
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

    offline = (os.environ.get("OPENAI4S_EGRESS") or "").strip().lower() in {
        "off",
        "deny",
        "block",
    }
    facts["egress_blocked"] = offline
    if offline:
        return Check(
            "connectors",
            WARN,
            f"{len(facts['science_databases'])} science databases are built in, "
            f"but application egress is blocked, so none can be reached",
            "Unset OPENAI4S_EGRESS to allow the allowlisted public APIs.",
            facts,
        )
    return Check(
        "connectors",
        OK,
        f"{len(facts['science_databases'])} science databases built in"
        + (
            f", {facts['configured_connectors']} connector(s) configured"
            if "configured_connectors" in facts
            else ""
        ),
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
    confinement = (
        (os.environ.get("OPENAI4S_COMPUTE_CONFINEMENT") or "auto").strip().lower()
    )
    facts["confinement_mode"] = confinement

    if not providers and not ssh_available:
        return Check(
            "remote",
            OK,
            "no remote compute configured; everything runs locally",
            facts=facts,
        )
    if providers and confinement == "enforce":
        # Stated plainly because it is a refusal by construction, not a bug.
        return Check(
            "remote",
            FAIL,
            f"{len(providers)} BYOC provider(s) present but "
            f"OPENAI4S_COMPUTE_CONFINEMENT=enforce, which this host cannot "
            f"satisfy: no OS boundary is applied to the provider helper",
            "Set it to `auto` to accept unconfined remote execution, or keep "
            "`enforce` and use the ssh family instead.",
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
