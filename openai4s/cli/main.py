"""openai4s CLI: serve / status / stop / url / run / init / setup.

  openai4s serve    start the daemon (foreground; use & or nohup to background)
  openai4s status   is the daemon up? (reads pidfile + /health)
  openai4s stop     stop the running daemon
  openai4s url      print the local web UI url
  openai4s run "<task>"   run one Code-as-Action task (in-process, no daemon)
  openai4s init     guided first-run model configuration
  openai4s setup    create/update conda envs from envs/*.yml
  openai4s jupyter  describe/export/install the optional Jupyter bridge
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from openai4s.config import get_config


def _write_state(cfg) -> None:
    cfg.pidfile.write_text(str(os.getpid()), "utf-8")
    cfg.statefile.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": cfg.host,
                "port": cfg.port,
                "started_at": int(time.time()),
            }
        ),
        "utf-8",
    )


def _clear_state(cfg) -> None:
    for p in (cfg.pidfile, cfg.statefile):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _read_pid(cfg) -> int | None:
    try:
        return int(cfg.pidfile.read_text("utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _url(cfg) -> str:
    return f"http://{cfg.host}:{cfg.port}/"


def cmd_serve(args) -> int:
    from openai4s.server import serve

    cfg = get_config()
    existing = _read_pid(cfg)
    if existing and _pid_alive(existing):
        print(f"daemon already running (pid {existing}) at {_url(cfg)}")
        return 1
    _write_state(cfg)
    print(f"openai4s listening at {_url(cfg)} (model={cfg.llm.model})")
    print("web UI ready. Ctrl-C to stop.")

    def _graceful(signum, frame):
        _clear_state(cfg)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _graceful)
    if not os.environ.get("OPENAI4S_NO_OPEN") and not getattr(args, "no_open", False):

        def _open():
            time.sleep(1.0)
            try:
                import webbrowser

                webbrowser.open(_url(cfg))
            except Exception:
                pass

        import threading

        threading.Thread(target=_open, daemon=True).start()
    try:
        serve(cfg, block=True)
    finally:
        _clear_state(cfg)
    return 0


def cmd_status(args) -> int:
    cfg = get_config()
    pid = _read_pid(cfg)
    if not pid or not _pid_alive(pid):
        print("daemon: not running")
        return 1
    # confirm via /health
    try:
        with urllib.request.urlopen(_url(cfg) + "health", timeout=3) as r:
            health = json.loads(r.read().decode("utf-8"))
        print(f"daemon: running (pid {pid}) at {_url(cfg)}")
        print(f"  model    : {health.get('model')}")
        # The loopback health response is intentionally a minimal public
        # projection.  The CLI already owns the local configuration, so it can
        # report the data directory without publishing an absolute host path
        # over HTTP.
        print(f"  data_dir : {cfg.data_dir}")
        return 0
    except urllib.error.URLError:
        print(f"daemon: pid {pid} alive but /health unreachable")
        return 2


def cmd_stop(args) -> int:
    cfg = get_config()
    pid = _read_pid(cfg)
    if not pid or not _pid_alive(pid):
        print("daemon: not running")
        _clear_state(cfg)
        return 1
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not _pid_alive(pid):
            break
        time.sleep(0.1)
    _clear_state(cfg)
    print(f"daemon stopped (pid {pid})")
    return 0


def cmd_url(args) -> int:
    print(_url(get_config()))
    return 0


def cmd_run(args) -> int:
    from openai4s.agent import Agent

    cfg = get_config()
    result = Agent(cfg=cfg, verbose=args.verbose).run(args.task)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("\n=== stop_reason:", result["stop_reason"], "===")
        if result.get("submitted_output"):
            print(
                "submitted_output:",
                json.dumps(result["submitted_output"], ensure_ascii=False, indent=2),
            )
        if result.get("final_message"):
            print("final:", result["final_message"])
    return 0


# --------------------------------------------------------------------------- #
#  init — guided first-run configuration without checkout-local files
# --------------------------------------------------------------------------- #


def _onboarding_service():
    from openai4s.llm import PROVIDERS
    from openai4s.onboarding import OnboardingService
    from openai4s.store import get_store

    cfg = get_config()
    cfg.ensure_dirs()
    store = get_store(cfg.db_path)
    return OnboardingService(cfg, store, PROVIDERS), store


def _prompt_value(label: str, default: str) -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{label}{suffix}: ").strip() or default


def cmd_init(args) -> int:
    service, store = _onboarding_service()
    try:
        defaults = service.defaults(args.provider)
        interactive = (
            not args.non_interactive and not args.api_key_stdin and sys.stdin.isatty()
        )
        provider = args.provider or defaults["provider"]
        model = args.model
        base_url = args.base_url
        api_key = None

        if interactive:
            known = ", ".join(sorted(service.providers))
            print("OpenAI4S first-run setup")
            print(f"Available providers: {known}")
            provider = _prompt_value("Provider", provider).lower()
            defaults = service.defaults(provider)
            model = model or _prompt_value("Model", defaults["model"])
            base_url = base_url or _prompt_value("Base URL", defaults["base_url"])
            if not args.clear_api_key:
                answer = input("Configure an API key now? [y/N]: ").strip().lower()
                if answer in {"y", "yes"}:
                    api_key = getpass.getpass("API key (input hidden): ")
        elif args.api_key_stdin:
            api_key = sys.stdin.readline().rstrip("\r\n")

        result = service.configure(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            clear_api_key=args.clear_api_key,
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()

    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Configured {result.provider} / {result.model}")
        print(f"Settings stored in {result.data_dir}")
        if not result.has_api_key:
            print("No API key stored; add one in Customize → Models after launch.")
        if not result.native_runtime_supported:
            print("Native Windows kernels are unsupported; run OpenAI4S under WSL2.")
        print("Next: openai4s serve")
    return 0


# --------------------------------------------------------------------------- #
#  optional Jupyter adapter — stdlib KernelSpec operations, lazy wire import
# --------------------------------------------------------------------------- #


def cmd_jupyter_describe(args) -> int:
    from openai4s.adapters.jupyter import adapter_status

    status = adapter_status()
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0
    bridge = "available" if status["bridge_available"] else "not installed"
    print(f"Jupyter bridge: {bridge}")
    print("  scope      : standalone (not a Web-session attachment)")
    print("  host RPC   : unavailable")
    print("  protocol   : Jupyter wire adapter -> hardened OpenAI4S JSON-line worker")
    for kernel in status["kernels"]:
        print(f"  kernelspec : {kernel['name']} ({kernel['language']})")
    if not status["bridge_available"]:
        print("  install    : python -m pip install 'ipykernel>=7,<8'")
    return 0


def _print_kernelspec_writes(written: list[dict], action: str) -> None:
    for item in written:
        print(f"{action} {item['name']}: {item['kernel_json']}")


def cmd_jupyter_export(args) -> int:
    from openai4s.adapters.jupyter import write_kernelspecs
    from openai4s.adapters.jupyter.kernelspec import KernelSpecError

    try:
        written = write_kernelspecs(
            args.output,
            languages=args.language,
            replace=args.replace,
        )
    except (KernelSpecError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_kernelspec_writes(written, "exported")
    return 0


def cmd_jupyter_install(args) -> int:
    from openai4s.adapters.jupyter import install_kernelspecs
    from openai4s.adapters.jupyter.kernelspec import KernelSpecError

    try:
        written = install_kernelspecs(
            prefix=args.prefix,
            languages=args.language,
            replace=args.replace,
        )
    except (KernelSpecError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_kernelspec_writes(written, "installed")
    return 0


# --------------------------------------------------------------------------- #
#  setup — create the four default conda environments from envs/*.yml
# --------------------------------------------------------------------------- #
# The four default envs, in the order we create them (python first: it's the
# default kernel env). Names must match the `name:` in each envs/<name>.yml.
_DEFAULT_ENVS = ["python", "phylo", "r", "struct"]

# Named setup profiles. The standard profile is the broad, everyday Python/R
# stack used by setup.sh; full preserves the historical four-env setup.
_ENV_PROFILES = {
    "standard": ["python", "r"],
    "full": list(_DEFAULT_ENVS),
}

# Conda-family tools we know how to drive, fastest first.
_CONDA_TOOLS = ["micromamba", "mamba", "conda"]


def _envs_dir() -> Path:
    """The repo's ``envs/`` directory (sibling of the ``openai4s`` package)."""
    return Path(__file__).resolve().parents[2] / "envs"


def _find_conda_tool() -> str | None:
    """First available of micromamba / mamba / conda on PATH, or None."""
    for tool in _CONDA_TOOLS:
        if shutil.which(tool):
            return tool
    return None


def _existing_envs() -> dict[str, Path]:
    """Existing conda envs, mapped name → prefix.

    Prefers the daemon's own discovery (:mod:`openai4s.kernel.environments`,
    which honours ``OPENAI4S_ENV_ROOTS`` and the reference-daemon envs dir);
    falls back to ``conda env list`` parsing if that import isn't available.

    The prefix matters: we decide create-vs-update from *these* roots, so an
    update has to name the very prefix we found. Passing only the spec file
    would make the conda tool re-resolve the yml's ``name:`` inside its own
    root prefix, which is a different namespace — see :func:`_update_cmd`."""
    try:
        from openai4s.kernel.environments import discover_environments

        return {e.name: e.root for e in discover_environments(force=True) if e.is_conda}
    except Exception:  # noqa: BLE001 — fall back to CLI probing
        pass
    tool = _find_conda_tool()
    if not tool:
        return {}
    try:
        out = subprocess.run(
            [tool, "env", "list"], capture_output=True, text=True, timeout=30
        )
    except Exception:  # noqa: BLE001
        return {}
    envs: dict[str, Path] = {}
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # rows look like:  "python   *  /path/to/envs/python"
        fields = line.split()
        path = fields[-1]
        if os.sep not in path:
            continue
        prefix = Path(path)
        envs.setdefault(prefix.name, prefix)
        first = fields[0]
        if first and first != "*":
            envs.setdefault(first, prefix)
    return envs


def _create_cmd(tool: str, name: str, yml: Path) -> list[str]:
    """The env-creation argv for ``tool`` from spec file ``yml``.

    micromamba/mamba/conda all accept ``env create -f <file>``; conda derives
    the env name from the file's ``name:`` field."""
    return [tool, "env", "create", "-f", str(yml)]


def _update_cmd(tool: str, prefix: Path, yml: Path) -> list[str]:
    """Non-destructively update the env at ``prefix`` from ``yml``.

    ``-p`` is not optional. Without it, micromamba/mamba/conda resolve the
    yml's ``name:`` against *their own* root prefix — but we chose "update"
    because :func:`_existing_envs` found the env somewhere else (a second conda
    root, ``OPENAI4S_ENV_ROOTS``, …). conda would then happily build a brand-new
    env under its own root and report success while the env the agent actually
    runs in stays untouched; micromamba would abort with "Prefix does not exist".

    ``--prune`` is deliberately omitted so setup never removes packages the user
    installed after the initial environment creation.
    """
    return [tool, "env", "update", "-p", str(prefix), "-f", str(yml)]


def cmd_setup(args) -> int:
    tool = _find_conda_tool()
    if not tool:
        print("error: no conda/mamba/micromamba found on PATH.", file=sys.stderr)
        print(
            "       install one (e.g. micromamba) and re-run `openai4s setup`.",
            file=sys.stderr,
        )
        return 1

    envs_dir = _envs_dir()
    if not envs_dir.is_dir():
        print(f"error: envs directory not found: {envs_dir}", file=sys.stderr)
        return 1

    if args.only:
        if args.only not in _DEFAULT_ENVS:
            print(
                f"error: unknown env '{args.only}' "
                f"(choices: {', '.join(_DEFAULT_ENVS)})",
                file=sys.stderr,
            )
            return 1
        wanted = [args.only]
    elif getattr(args, "profile", None):
        wanted = list(_ENV_PROFILES[args.profile])
    else:
        wanted = list(_DEFAULT_ENVS)

    existing = _existing_envs()
    update_existing = bool(getattr(args, "update", False))

    print(
        f"using '{tool}' to manage envs from {envs_dir}"
        + (" (dry-run)" if args.dry_run else "")
    )
    created = 0
    updated = 0
    skipped = 0
    failed = 0
    for name in wanted:
        yml = envs_dir / f"{name}.yml"
        if not yml.is_file():
            print(f"  [{name}] skip: spec file missing ({yml})")
            failed += 1
            continue
        prefix = existing.get(name)
        if prefix is not None and not update_existing:
            print(f"  [{name}] already exists — skipping (use --update to sync)")
            skipped += 1
            continue
        cmd = (
            _update_cmd(tool, prefix, yml)
            if prefix is not None
            else _create_cmd(tool, name, yml)
        )
        action = "update" if prefix is not None else "create"
        if args.dry_run:
            print(f"  [{name}] would {action}: {' '.join(cmd)}")
            continue
        print(f"  [{name}] {action}… ({' '.join(cmd)})")
        try:
            rc = subprocess.run(cmd).returncode
        except Exception as exc:  # noqa: BLE001
            print(f"  [{name}] error: {exc}", file=sys.stderr)
            failed += 1
            continue
        if rc == 0:
            print(f"  [{name}] {action}d")
            if prefix is not None:
                updated += 1
            else:
                created += 1
        else:
            print(f"  [{name}] FAILED (exit {rc})", file=sys.stderr)
            failed += 1

    if args.dry_run:
        return 0
    print(
        f"done: {created} created, {updated} updated, "
        f"{skipped} skipped, {failed} failed"
    )
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="openai4s", description="openai4s CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("serve", help="start the daemon (foreground)")
    ps.add_argument("--no-open", action="store_true", help="don't open a browser")
    ps.set_defaults(fn=cmd_serve)
    sub.add_parser("status", help="check daemon status").set_defaults(fn=cmd_status)
    sub.add_parser("stop", help="stop the daemon").set_defaults(fn=cmd_stop)
    sub.add_parser("url", help="print the web UI url").set_defaults(fn=cmd_url)

    pr = sub.add_parser("run", help="run one Code-as-Action task in-process")
    pr.add_argument("task", help="the task description")
    pr.add_argument("--json", action="store_true", help="emit full JSON result")
    pr.add_argument("-v", "--verbose", action="store_true", help="stream turns")
    pr.set_defaults(fn=cmd_run)

    pi = sub.add_parser("init", help="guided first-run model configuration")
    pi.add_argument("--provider", help="provider id (default: current provider)")
    pi.add_argument("--model", help="model id (default: provider default)")
    pi.add_argument("--base-url", help="provider API base URL")
    pi.add_argument(
        "--api-key-stdin",
        action="store_true",
        help="read one API-key line from stdin (never from command arguments)",
    )
    pi.add_argument(
        "--clear-api-key",
        action="store_true",
        help="remove the stored API key for the selected profile",
    )
    pi.add_argument(
        "--non-interactive",
        action="store_true",
        help="accept supplied options and provider defaults without prompting",
    )
    pi.add_argument("--json", action="store_true", help="emit secret-free JSON")
    pi.set_defaults(fn=cmd_init)

    pu = sub.add_parser("setup", help="create or update conda envs from envs/*.yml")
    setup_selection = pu.add_mutually_exclusive_group()
    setup_selection.add_argument(
        "--only",
        metavar="NAME",
        choices=_DEFAULT_ENVS,
        help="create just one env (%(choices)s)",
    )
    setup_selection.add_argument(
        "--profile",
        choices=tuple(_ENV_PROFILES),
        help="environment profile: standard=python+r, full=all four",
    )
    pu.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands that would run, without executing",
    )
    pu.add_argument(
        "--update",
        action="store_true",
        help="update existing envs without pruning user-installed packages",
    )
    pu.set_defaults(fn=cmd_setup)

    pj = sub.add_parser(
        "jupyter",
        help="describe/export/install the optional Jupyter adapter",
    )
    jsub = pj.add_subparsers(dest="jupyter_action", required=True)
    jd = jsub.add_parser("describe", help="show adapter capabilities and limits")
    jd.add_argument("--json", action="store_true", help="emit JSON")
    jd.set_defaults(fn=cmd_jupyter_describe)
    je = jsub.add_parser("export", help="export standard KernelSpec directories")
    je.add_argument("output", type=Path, help="destination kernels directory")
    je.add_argument(
        "--language",
        choices=("all", "python", "r"),
        default="all",
    )
    je.add_argument(
        "--replace",
        action="store_true",
        help="replace kernel.json in an existing spec directory",
    )
    je.set_defaults(fn=cmd_jupyter_export)
    ji = jsub.add_parser("install", help="install KernelSpecs for Jupyter clients")
    ji.add_argument(
        "--prefix",
        type=Path,
        help="install below PREFIX/share/jupyter/kernels (default: user data dir)",
    )
    ji.add_argument(
        "--language",
        choices=("all", "python", "r"),
        default="all",
    )
    ji.add_argument(
        "--replace",
        action="store_true",
        help="replace kernel.json in an existing spec directory",
    )
    ji.set_defaults(fn=cmd_jupyter_install)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
