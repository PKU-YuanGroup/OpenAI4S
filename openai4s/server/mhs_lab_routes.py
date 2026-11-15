"""Lab panel routes: the simulated OpenMHS lab beside the Notebook.

The lab needs OpenMHS (the SDK ``mhs`` and the reference host
``openai4s_mhs``).  :mod:`openai4s.mhs_lab.openmhs` finds it in the
``third_party/OpenMHS`` submodule or a downloaded copy; until then ``status``
reports ``available: false`` and ``POST /mhs-lab/setup`` downloads the pinned
release.

* ``GET    /mhs-lab/status``        -- endpoints grouped by surface (MHS native / MCP), live state, latches, LIMS
* ``GET    /mhs-lab/activity``      -- every receipt of every endpoint, oldest first (``?limit=``)
* ``GET    /mhs-lab/results``       -- chart data derived from receipt outputs
* ``GET    /mhs-lab/verify``        -- offline verification of each evidence chain and the host ledger
* ``GET    /mhs-lab/card``          -- the device card given to the agent
* ``GET    /mhs-lab/market``        -- search the OpenMHS market (``?q=&track=&mode=&category=&limit=&offset=``)
* ``POST   /mhs-lab/devices``       -- install a market device ``{package, via: "mcp"|"mhs"}`` and start it
* ``DELETE /mhs-lab/devices/<id>``  -- uninstall a device the operator added
* ``POST   /mhs-lab/start|stop``    -- start or stop the lab's endpoints
* ``POST   /mhs-lab/setup``         -- ``{confirm: true}``: download the pinned OpenMHS release when no checkout exists
* ``POST   /mhs-lab/estop``         -- operator e-stop ``{endpoint: <id>|"all", reason?}``
* ``POST   /mhs-lab/reset``         -- operator reset of one latched endpoint ``{endpoint, operator?}``

Installing, starting, the e-stop and the reset are operator actions taken in
the UI by a person; they are not tools and the agent cannot reach them.  Team
mode refuses them to members.
"""

from __future__ import annotations

import threading
from typing import Any

from openai4s.server import contract, team_policy

_STATUS = contract.RouteSpec("mhs_lab.status", "GET", r"/mhs-lab/status", mutates=False)
_ACTIVITY = contract.RouteSpec(
    "mhs_lab.activity", "GET", r"/mhs-lab/activity", mutates=False
)
_RESULTS = contract.RouteSpec(
    "mhs_lab.results", "GET", r"/mhs-lab/results", mutates=False
)
_VERIFY = contract.RouteSpec("mhs_lab.verify", "GET", r"/mhs-lab/verify", mutates=False)
_CARD = contract.RouteSpec("mhs_lab.card", "GET", r"/mhs-lab/card", mutates=False)
_MARKET = contract.RouteSpec("mhs_lab.market", "GET", r"/mhs-lab/market", mutates=False)
_ADD = contract.RouteSpec(
    "mhs_lab.devices.add", "POST", r"/mhs-lab/devices", mutates=True
)
_REMOVE = contract.RouteSpec(
    "mhs_lab.devices.remove", "DELETE", r"/mhs-lab/devices/([^/]+)", mutates=True
)
_START = contract.RouteSpec("mhs_lab.start", "POST", r"/mhs-lab/start", mutates=True)
_STOP = contract.RouteSpec("mhs_lab.stop", "POST", r"/mhs-lab/stop", mutates=True)
_SETUP = contract.RouteSpec("mhs_lab.setup", "POST", r"/mhs-lab/setup", mutates=True)
_ESTOP = contract.RouteSpec("mhs_lab.estop", "POST", r"/mhs-lab/estop", mutates=True)
_RESET = contract.RouteSpec("mhs_lab.reset", "POST", r"/mhs-lab/reset", mutates=True)

ROUTES = contract.validate_routes(
    (
        _STATUS,
        _ACTIVITY,
        _RESULTS,
        _VERIFY,
        _CARD,
        _MARKET,
        _ADD,
        _REMOVE,
        _START,
        _STOP,
        _SETUP,
        _ESTOP,
        _RESET,
    )
)

_PREFIX = "/mhs-lab/"
_REVIVE_LOCK = threading.Lock()
_SETUP_LOCK = threading.Lock()


def _lab():
    try:
        from openai4s.mhs_lab.openmhs import bootstrap, disabled

        if (
            disabled()
        ):  # OPENAI4S_OPENMHS=off: the lab is unavailable, whatever is importable
            return None
        bootstrap()
        import mhs  # noqa: F401  # type: ignore

        from openai4s.mhs_lab import activity
    except ImportError:
        return None
    return activity


def ensure_native_tools() -> None:
    """Register the native ``mhs_*`` tools before a session's control plane is built.

    A lab started from the CLI (``openai4s lab add``/``up``) while this daemon
    was already running enabled the tools in a settings file no code in the
    daemon re-read: registration happened only at import and on a Lab panel
    action, so new sessions declared no ``mhs_*`` tools and the agent could
    not reach the reactor or any device added ``--via mhs``. Each session's
    tool catalog snapshots the registry when it is built, which is why this
    runs then. Idempotent and cheap: one small settings read.
    """
    _ensure_native_tools()


def _ensure_native_tools() -> None:
    """Register the native ``mhs_*`` tools in this daemon once the lab's settings enable them."""
    try:
        from openai4s.mhs_lab.openmhs import bootstrap

        bootstrap()
        import openai4s_mhs  # type: ignore

        openai4s_mhs.install()
    except (
        Exception
    ):  # noqa: BLE001 - the panel still works; the tools follow on restart
        pass


def _revive_if_wanted() -> None:
    """After a reboot, restart a lab the operator left running (once, in the background)."""
    from openai4s.mhs_lab import supervisor

    generation = supervisor.running_generation()
    if generation is None or not _REVIVE_LOCK.acquire(blocking=False):
        return

    def run() -> None:
        try:
            doc = supervisor.status()
            if any(ep.get("url") and not ep.get("running") for ep in doc["endpoints"]):
                supervisor.start(expected_generation=generation)
            _ensure_native_tools()
        except Exception:  # noqa: BLE001 - status shows what is down
            pass
        finally:
            _REVIVE_LOCK.release()

    threading.Thread(target=run, name="mhs-lab-revive", daemon=True).start()


def _refuse_member(handler: Any) -> bool:
    """True if a 403 was sent: in team mode only an admin is the lab operator."""
    if team_policy.may_change_instance_config(handler):
        return False
    handler._json({"error": "admin only", "code": "admin_only"}, 403)
    return True


def _int(q: dict, name: str, default: int, low: int, high: int) -> int | None:
    raw = (q.get(name) or [str(default)])[0]
    try:
        return max(low, min(int(raw), high))
    except (TypeError, ValueError):
        return None


def _market_page(q: dict) -> dict[str, Any] | str:
    from openai4s.mhs_lab import supervisor

    limit = _int(q, "limit", 40, 1, 200)
    offset = _int(q, "offset", 0, 0, 100000)
    if limit is None or offset is None:
        return "limit and offset must be integers"
    market = supervisor._market()
    document = market.load()
    track = (q.get("track") or [""])[0] or None
    mode = (q.get("mode") or [""])[0] or None
    category = (q.get("category") or [""])[0] or None
    hits = market.search(
        (q.get("q") or [""])[0][:200], track=track, mode=mode, document=document
    )
    if category:
        hits = [h for h in hits if h.get("category") == category]
    installed = {d["package"]: d for d in supervisor.installed_devices()}
    keys = (
        "id",
        "name",
        "category",
        "topic",
        "mode",
        "risk",
        "track",
        "intro",
        "tags",
        "upstream",
        "stars",
        "measures",
        "adjusts",
        "install",
    )
    rows = []
    for entry in hits[offset : offset + limit]:
        row = {k: entry.get(k) for k in keys}
        device = installed.get(entry["id"])
        row["installed"] = (
            {"id": device["id"], "via": device["via"]} if device else None
        )
        rows.append(row)
    categories: dict[str, int] = {}
    for entry in document["packages"]:
        categories[entry["category"]] = categories.get(entry["category"], 0) + 1
    return {
        "total": len(hits),
        "offset": offset,
        "limit": limit,
        "packages": rows,
        "count": document["count"],
        "categories": dict(sorted(categories.items(), key=lambda kv: -kv[1])),
        "website": document.get("website"),
    }


def _unavailable_status() -> dict[str, Any]:
    from openai4s.mhs_lab.openmhs import describe

    return {
        "available": False,
        "reason": "OpenMHS is not set up yet",
        "setup": "POST /api/v1/mhs-lab/setup",
        "openmhs": describe(),
    }


def handle(self: Any, method: str, sub: str, q: dict) -> bool:
    """Answer a Lab route, or report that this group does not own it."""
    path = sub.split("?")[0]
    if not path.startswith(_PREFIX):
        return False
    lab = _lab()
    if _STATUS.match(method, path):
        if lab is None:
            self._json(_unavailable_status())
            return True
        if team_policy.may_change_instance_config(self):
            _revive_if_wanted()
        self._json({"available": True, **lab.status()})
        return True
    if _SETUP.match(method, path):
        if _refuse_member(self):
            return True
        from openai4s.mhs_lab.openmhs import disabled

        if disabled():
            self._json(
                {
                    "error": "OpenMHS discovery is turned off (OPENAI4S_OPENMHS=off)",
                    "code": "mhs_lab_disabled",
                },
                409,
            )
            return True
        if self._body().get("confirm") is not True:
            # A download is an explicit operator choice: the UI sends {"confirm": true}.
            self._json(
                {
                    "error": 'send {"confirm": true} to download the pinned OpenMHS release',
                    "code": "confirm_required",
                },
                400,
            )
            return True
        with _SETUP_LOCK:
            try:
                from openai4s.mhs_lab.openmhs import describe, ensure

                ensure(download=True, quiet=True)
                self._json({"ok": True, "openmhs": describe()})
            except (
                Exception
            ) as exc:  # noqa: BLE001 - shown to the operator with the fix
                self._json(
                    {
                        "error": f"{type(exc).__name__}: {exc}",
                        "code": "mhs_lab_setup_failed",
                    },
                    502,
                )
        return True
    if lab is None:
        self._json(
            {"error": "OpenMHS is not set up yet", "code": "mhs_lab_unavailable"}, 404
        )
        return True
    if _ACTIVITY.match(method, path):
        limit = _int(q, "limit", 300, 1, 2000)
        if limit is None:
            self._json(
                {"error": "limit must be an integer", "code": "invalid_limit"}, 400
            )
            return True
        self._json(lab.activity(limit))
        return True
    if _RESULTS.match(method, path):
        self._json(lab.results())
        return True
    if _VERIFY.match(method, path):
        self._json(lab.verify())
        return True
    if _CARD.match(method, path):
        self._json({"card": lab.lab_card()})
        return True
    if _MARKET.match(method, path):
        page = _market_page(q)
        if isinstance(page, str):
            self._json({"error": page, "code": "invalid_limit"}, 400)
        else:
            self._json(page)
        return True
    if not (
        _ADD.match(method, path)
        or _REMOVE.match(method, path)
        or _START.match(method, path)
        or _STOP.match(method, path)
        or _ESTOP.match(method, path)
        or _RESET.match(method, path)
    ):
        return False
    if _refuse_member(self):
        return True
    from openai4s.mhs_lab import supervisor

    if _ADD.match(method, path):
        body = self._body()
        package = str(body.get("package") or "").strip()
        via = str(body.get("via") or "mcp")
        if not package:
            self._json({"error": "package is required", "code": "invalid_package"}, 400)
            return True
        if via not in supervisor.VIAS:
            self._json({"error": "via must be mcp or mhs", "code": "invalid_via"}, 400)
            return True
        try:
            result = supervisor.add_device(package, via=via)
        except LookupError as exc:
            self._json({"error": str(exc), "code": "unknown_package"}, 404)
            return True
        except supervisor.LabStartSuperseded as exc:
            self._json({"error": str(exc), "code": "lab_start_superseded"}, 409)
            return True
        _ensure_native_tools()
        self._json(result)
        return True
    if _REMOVE.match(method, path):
        device_id = path.rsplit("/", 1)[-1]
        try:
            self._json(supervisor.remove_device(device_id))
        except ValueError as exc:
            self._json({"error": str(exc), "code": "builtin_device"}, 409)
        except LookupError as exc:
            self._json({"error": str(exc), "code": "unknown_device"}, 404)
        return True
    if _START.match(method, path):
        try:
            result = supervisor.start()
        except supervisor.LabStartSuperseded as exc:
            self._json({"error": str(exc), "code": "lab_start_superseded"}, 409)
            return True
        _ensure_native_tools()
        self._json(result)
        return True
    if _STOP.match(method, path):
        self._json(supervisor.stop())
        return True
    body = self._body()
    endpoint = str(body.get("endpoint") or "")
    known = {ep["id"] for ep in supervisor.lab_document()["endpoints"]}
    if endpoint not in known and endpoint != "all":
        self._json(
            {
                "error": "endpoint must be a lab device id or all",
                "code": "invalid_endpoint",
            },
            400,
        )
        return True
    action = "estop" if _ESTOP.match(method, path) else "reset"
    if action == "reset" and endpoint == "all":
        self._json(
            {"error": "reset one endpoint at a time", "code": "invalid_endpoint"}, 400
        )
        return True
    operator = str(body.get("operator") or "human:operator (Lab panel)")[:120]
    reason = str(body.get("reason") or "")[:300]
    self._json(lab.operator_action(action, endpoint, operator=operator, reason=reason))
    return True
