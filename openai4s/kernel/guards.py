"""Isolation guards — detect & contain cross-cell global-state leaks.

openai4s's guards/ package pins fragile global state so one cell can't silently
corrupt the next. We replicate the two load-bearing ones:

  isolation_pin......... snapshot a set of process-global registries before a
                         cell and diff/restore after, so leaked mutations are
                         surfaced (and optionally auto-reverted).
  matplotlib_global_state track pyplot's global figure manager: figures a cell
                         opens but never closes are a classic leak (memory +
                         "figure reused across cells"); we snapshot fignums
                         pre-cell and report the delta.

These are cheap best-effort probes: if a library is absent the guard no-ops.
"""

from __future__ import annotations

import os
import warnings
from typing import Any


def _off() -> bool:
    return os.environ.get("OPENAI4S_GUARDS_OFF") == "1"


class MatplotlibGlobalState:
    """Snapshot/diff pyplot's global figure registry across a cell."""

    def __init__(self) -> None:
        self._pre: tuple[int, ...] = ()
        self._active = False

    def _fignums(self) -> tuple[int, ...]:
        try:
            import matplotlib.pyplot as plt

            return tuple(plt.get_fignums())
        except Exception:  # noqa: BLE001 - matplotlib absent / headless
            return ()

    def snapshot(self) -> None:
        if _off():
            return
        self._pre = self._fignums()
        self._active = True

    def diff(self) -> dict:
        """Return {leaked:[fignum], closed:[fignum]} vs the snapshot."""
        if _off() or not self._active:
            return {"leaked": [], "closed": []}
        post = self._fignums()
        leaked = [n for n in post if n not in self._pre]
        closed = [n for n in self._pre if n not in post]
        return {"leaked": leaked, "closed": closed}

    def autoclose_leaked(self) -> list[int]:
        """Close figures this cell leaked (keeps the global registry clean)."""
        d = self.diff()
        try:
            import matplotlib.pyplot as plt

            for n in d["leaked"]:
                plt.close(n)
        except Exception:  # noqa: BLE001
            pass
        return d["leaked"]


class IsolationPin:
    """Pin a set of process-global registries; diff & restore after a cell.

    Tracks: os.environ, warnings filters, sys int/str conversion limit,
    the default random seed state, and the decimal context — the registries
    openai4s found most prone to silent cross-cell corruption.
    """

    def __init__(self) -> None:
        self._snap: dict[str, Any] = {}
        self._active = False

    def pin(self) -> None:
        if _off():
            return
        import copy
        import sys

        snap: dict[str, Any] = {
            "environ": dict(os.environ),
            "warnings_filters": list(warnings.filters),
        }
        try:
            snap["int_max_str_digits"] = sys.get_int_max_str_digits()
        except AttributeError:
            pass
        try:
            import random

            snap["random_state"] = random.getstate()
        except Exception:  # noqa: BLE001
            pass
        try:
            import decimal

            snap["decimal_ctx"] = copy.copy(decimal.getcontext())
        except Exception:  # noqa: BLE001
            pass
        self._snap = snap
        self._active = True

    def diff(self) -> dict:
        """Report which pinned registries a cell mutated (for surfacing)."""
        if _off() or not self._active:
            return {}
        changed: dict[str, Any] = {}
        cur_env = dict(os.environ)
        if cur_env != self._snap.get("environ"):
            added = {
                k: v for k, v in cur_env.items() if self._snap["environ"].get(k) != v
            }
            removed = [k for k in self._snap["environ"] if k not in cur_env]
            changed["environ"] = {"added_or_changed": added, "removed": removed}
        if list(warnings.filters) != self._snap.get("warnings_filters"):
            changed["warnings_filters"] = "mutated"
        return changed

    def restore(self) -> None:
        """Revert pinned registries to their pre-cell snapshot."""
        if _off() or not self._active:
            return
        import sys

        snap = self._snap
        if "environ" in snap:
            for k in list(os.environ.keys()):
                if k not in snap["environ"]:
                    os.environ.pop(k, None)
            for k, v in snap["environ"].items():
                if os.environ.get(k) != v:
                    os.environ[k] = v
        if "warnings_filters" in snap:
            warnings.filters[:] = snap["warnings_filters"]
        if "int_max_str_digits" in snap:
            try:
                sys.set_int_max_str_digits(snap["int_max_str_digits"])
            except (AttributeError, ValueError):
                pass
        if "random_state" in snap:
            try:
                import random

                random.setstate(snap["random_state"])
            except Exception:  # noqa: BLE001
                pass
        if "decimal_ctx" in snap:
            try:
                import decimal

                decimal.setcontext(snap["decimal_ctx"])
            except Exception:  # noqa: BLE001
                pass


class GuardBundle:
    """Bundles the guards a cell runs under. Snapshot pre-exec, report post."""

    def __init__(
        self, *, restore_isolation: bool = False, autoclose_figs: bool = False
    ) -> None:
        self.mpl = MatplotlibGlobalState()
        self.iso = IsolationPin()
        self._restore_isolation = restore_isolation
        self._autoclose_figs = autoclose_figs

    def before_cell(self) -> None:
        self.mpl.snapshot()
        self.iso.pin()

    def after_cell(self) -> dict:
        report: dict[str, Any] = {}
        mpl = self.mpl.diff()
        if mpl["leaked"] or mpl["closed"]:
            report["matplotlib"] = mpl
        if self._autoclose_figs:
            report.setdefault("matplotlib", {})[
                "autoclosed"
            ] = self.mpl.autoclose_leaked()
        iso = self.iso.diff()
        if iso:
            report["isolation"] = iso
        if self._restore_isolation:
            self.iso.restore()
        return report
