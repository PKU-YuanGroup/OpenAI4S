"""``openai4s lab``: the simulated OpenMHS lab from the command line.

::

    openai4s lab add labs/tiagocoutinho__julabo   # install a market device (and start the lab)
    openai4s lab search microscope                # find devices in the OpenMHS market
    openai4s lab status                           # what is installed, how the agent reaches it
    openai4s lab remove julabo
    openai4s lab up | down | reset | setup | card

Everything runs on simulators or fake backends; nothing here drives hardware.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

_GROUP_TITLE = {
    "mhs": "MHS · native mhs_* tools",
    "mcp": "MCP · connector call_mcp_tool",
}


def _emit(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def _ensure(args: argparse.Namespace) -> None:
    from openai4s.mhs_lab.openmhs import ensure

    ensure(download=True, quiet=bool(getattr(args, "json", False)))


def _print_status(doc: dict[str, Any]) -> None:
    for group in ("mhs", "mcp"):
        rows = [ep for ep in doc["endpoints"] if ep.get("group") == group]
        print(f"\n{_GROUP_TITLE[group]}  ({len(rows)})")
        for ep in rows:
            if ep.get("url"):
                state = "running" if ep.get("running") else "stopped"
            else:
                state = "on demand"
            latch = "  E-STOP LATCHED" if ep.get("estop_latched") else ""
            tag = "built-in" if ep.get("builtin") else (ep.get("package") or "")
            where = ep.get("connector") or ep.get("url") or ""
            print(
                f"  {ep['id']:<16} {state:<10} {where:<28} {ep['title'][:48]}  [{tag}]{latch}"
            )


def _next_steps() -> None:
    print(
        '\nNext: start the workbench with `openai4s serve` (or ./start.sh), open the project "OpenMHS Lab"'
    )
    print(
        "      (its agent context lists every lab device), start a conversation and open the Lab tab."
    )
    print('      Try: "Read every lab device and summarise what it can do."')


def cmd_setup(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab.openmhs import describe

    _ensure(args)
    info = describe()
    if args.json:
        _emit(info)
    else:
        print(
            f"OpenMHS ready: {info['checkout']} ({info['source']}, SDK {info.get('sdk_version')})"
        )
        print(
            "Next: `openai4s lab up` starts the lab; `openai4s lab search <words>` finds devices."
        )
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    _ensure(args)
    result = supervisor.start()
    if args.json:
        _emit(result)
        return 0
    print(
        f"Lab started: {', '.join(result.get('started') or []) or 'no HTTP endpoints'}"
    )
    for key in ("not_ready", "connector_error", "permission_error"):
        if result.get(key):
            print(f"  ! {key}: {result[key]}")
    _print_status(supervisor.status())
    _next_steps()
    return 0 if not result.get("not_ready") else 1


def cmd_down(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    result = supervisor.stop()
    (
        _emit(result)
        if args.json
        else print(
            f"Lab stopped: {', '.join(result['stopped']) or 'nothing was running'}"
        )
    )
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    result = supervisor.reset()
    (
        _emit(result)
        if args.json
        else print(
            "Lab reset: evidence, latches and LIMS erased; installed devices kept. "
            "Run `openai4s lab up` to start again."
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor
    from openai4s.mhs_lab.openmhs import describe

    info = describe()
    if not info["importable"]:
        if args.json:
            _emit({"available": False, **info})
        else:
            print(
                "OpenMHS is not set up yet. Run `openai4s lab setup` (or `git submodule update --init`)."
            )
        return 1
    doc = supervisor.status()
    if args.json:
        _emit({"available": True, "openmhs": info, **doc})
        return 0
    print(f"OpenMHS {info.get('sdk_version')} from {info['checkout']}")
    print(f"Lab directory: {doc['lab_dir']}")
    _print_status(doc)
    return 0


def _market():
    from openai4s.mhs_lab.supervisor import _market

    return _market()


def cmd_search(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    _ensure(args)
    market = _market()
    query = " ".join(args.words)
    hits = market.search(query, track=args.track, mode=args.mode)
    installed = {d["package"]: d["id"] for d in supervisor.installed_devices()}
    shown = hits[: args.limit] if args.limit else hits
    if args.json:
        _emit(
            {
                "total": len(hits),
                "packages": [
                    dict(h, installed_as=installed.get(h["id"])) for h in shown
                ],
            }
        )
        return 0
    for entry in shown:
        mark = (
            f"  (installed as {installed[entry['id']]})"
            if entry["id"] in installed
            else ""
        )
        print(f"{entry['id']:<56} {entry['mode']:<9} {entry['name'][:60]}{mark}")
    print(
        f"\n{len(shown)} of {len(hits)} shown. Install one: openai4s lab add <id>   Details: openai4s lab show <id>"
    )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    _ensure(args)
    market = _market()
    entry = market.find(args.package)
    if entry is None:
        hits = market.search(args.package, limit=5)
        print(
            f"No single market package named {args.package!r}."
            + (" Did you mean: " + ", ".join(h["id"] for h in hits) if hits else ""),
            file=sys.stderr,
        )
        return 1
    if args.json:
        _emit(entry)
        return 0
    print(
        f"{entry['name']}\n{entry['id']} · {entry['category']} · {entry['mode']} · risk {entry['risk']} · "
        f"licence {entry['upstream']['license']}\n"
    )
    print(entry["intro"].replace("`", ""))
    print(f"\nInstall: openai4s lab add {entry['id']}")
    print(f"Upstream: {entry['upstream']['repo_url']}")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    _ensure(args)
    try:
        result = supervisor.add_device(
            args.package, via=args.via, start_now=not args.no_start
        )
    except (LookupError, ValueError) as exc:
        print(f"openai4s lab add: {exc}", file=sys.stderr)
        return 1
    if args.json:
        _emit(result)
        return 0
    device = result["device"]
    surface = (
        f"native mhs_* tools at http://127.0.0.1:{device['port']}"
        if device["via"] == "mhs"
        else f"MCP connector mhs-{device['id']}"
    )
    verb = "Installed" if result["added"] else "Already installed"
    print(f"✓ {verb} {device['name']} as `{device['id']}` ({surface}, fake backend).")
    for key in ("not_ready", "connector_error", "permission_error"):
        if result.get(key):
            print(f"  ! {key}: {result[key]}")
    _next_steps()
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    try:
        result = supervisor.remove_device(args.device)
    except (LookupError, ValueError) as exc:
        print(f"openai4s lab remove: {exc}", file=sys.stderr)
        return 1
    _emit(result) if args.json else print(f"✓ Removed {args.device}.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import supervisor

    devices = supervisor.installed_devices()
    if args.json:
        _emit({"devices": devices})
        return 0
    if not devices:
        print(
            "No market devices installed yet (the three built-in devices are always there)."
        )
        print("Find one: openai4s lab search <words>    Install: openai4s lab add <id>")
        return 0
    for device in devices:
        print(
            f"{device['id']:<16} {device['via'].upper():<4} {device['package']:<56} {device.get('name', '')[:40]}"
        )
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    from openai4s.mhs_lab import lab_dir

    path = lab_dir() / "LAB-CARD.md"
    if not path.exists():
        print("No lab card yet: run `openai4s lab up` first.", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openai4s lab",
        description="The simulated OpenMHS lab: install devices from the OpenMHS market "
        "(https://mhs.openai4s.org/market/), start and stop the lab. Simulation only.",
    )
    sub = parser.add_subparsers(dest="verb", metavar="<command>")

    def command(
        name: str, fn, help_text: str, aliases: tuple[str, ...] = ()
    ) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, aliases=list(aliases))
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.set_defaults(fn=fn)
        return p

    p = command(
        "add",
        cmd_add,
        "install a market device into the lab and start it",
        ("install",),
    )
    p.add_argument(
        "package",
        help="market id, e.g. labs/tiagocoutinho__julabo (or a unique name such as julabo)",
    )
    p.add_argument(
        "--via",
        choices=("mcp", "mhs"),
        default="mcp",
        help="agent surface: mcp = MCP connector (default), mhs = native mhs_* tools over HTTP",
    )
    p.add_argument(
        "--no-start",
        action="store_true",
        help="only record it; start later with `openai4s lab up`",
    )
    p = command(
        "remove", cmd_remove, "uninstall a device you added", ("rm", "uninstall")
    )
    p.add_argument("device", help="the device id shown by `openai4s lab list`")
    p = command("search", cmd_search, "search the OpenMHS market", ("market", "find"))
    p.add_argument("words", nargs="*", help="search words (none: list everything)")
    p.add_argument("--track", choices=("labs", "robots"), default=None)
    p.add_argument("--mode", choices=("hardware", "simulated"), default=None)
    p.add_argument(
        "--limit", type=int, default=30, help="at most this many rows (0: all)"
    )
    p = command("show", cmd_show, "introduce one market device")
    p.add_argument("package")
    command("list", cmd_list, "the market devices you installed", ("ls",))
    command("status", cmd_status, "every lab device, its access path and state")
    command("up", cmd_up, "start the lab (built-in and installed devices)", ("start",))
    command("down", cmd_down, "stop the lab's endpoints", ("stop",))
    command(
        "reset",
        cmd_reset,
        "stop and erase evidence, latches and LIMS (keeps installed devices)",
    )
    command(
        "setup",
        cmd_setup,
        "make OpenMHS available (submodule, or download the pinned release)",
    )
    command("card", cmd_card, "print the device card the agent reads")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    try:
        return int(args.fn(args))
    except (
        RuntimeError
    ) as exc:  # OpenMHS missing or a download failure: one line with the fix
        print(f"openai4s lab: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
