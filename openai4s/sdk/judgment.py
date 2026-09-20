"""Worker-side ``host.judge`` signature.

The kernel may only name a registered template. A raw questions list is
rejected here so cell code cannot push arbitrary text to the vendor.
Templates that need synthesized questions declare ``allow_custom`` at
registration; their ``build_questions`` builder stays host-side.
"""

from __future__ import annotations

from typing import Any, Callable


def judge(
    host_call: Callable[[str, list], Any],
    template: str,
    state: object,
    **params: Any,
) -> Any:
    """Call the host judgment service. Four statuses are normal return values."""

    if not isinstance(template, str) or not template.strip():
        raise ValueError("host.judge requires a template id")
    if "questions" in params:
        raise ValueError(
            "host.judge does not accept a questions list; use a registered template"
        )
    spec: dict[str, Any] = {"template": template, "state": state}
    if params:
        spec["params"] = params
    return host_call("judge", [spec])


__all__ = ["judge"]
