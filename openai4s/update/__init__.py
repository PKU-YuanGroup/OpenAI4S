"""`openai4s update` — the online updater.

Nothing is imported at module scope, on purpose, and the whole public surface
is served through PEP 562's module-level ``__getattr__``. Two reasons:

* This package ships its final public names before the modules behind them
  exist. The read-only half (channel detection, discovery, verification) lands
  first; the apply transaction lands after it. A caller writes
  ``from openai4s import update`` once, and the name it reaches for resolves
  when the module that owns it exists.
* ``import openai4s.update`` must stay cheap and side-effect free. Two
  properties depend on it: nothing under this package may be reachable from a
  ``Tool`` subclass, a ``host.*`` capability or a Skill, and a fresh-data-dir
  boot must open no socket and start no subprocess. A package whose import
  pulls in the installer is a package that has to argue those properties
  instead of demonstrating them.

The exception types are defined here rather than re-exported because every
module in the package raises them and a lazily-resolved exception class cannot
be caught. They are plain classes; defining one imports nothing.

**Name shadowing, stated because it is a trap.** ``openai4s.update.apply`` is
both a module (the transaction) and a re-exported callable. Python binds the
submodule onto its parent package the moment it is imported, and a real
attribute always wins over ``__getattr__`` — so after anything imports
``openai4s.update.apply``, that name is the *module*. Call it through the
collision-free alias ``apply_update``, or import it from its own module. The
same hazard does not exist for any other name here.
"""

from __future__ import annotations

__all__ = (
    "Channel",
    "UpdateError",
    "UpdateRefusal",
    "apply",
    "apply_update",
    "check",
    "detect",
    "plan",
    "prune",
    "recover",
    "rollback",
)


class UpdateError(RuntimeError):
    """Anything the updater refuses or cannot complete.

    Carries no channel, no path and no URL by itself: the caller that raises
    puts the one user-facing line into the message, because a message assembled
    two layers up is a message nobody proof-read.
    """


class UpdateRefusal(UpdateError):
    """A refusal with a stable machine-readable ``code``.

    The code is the contract — the CLI maps it to an exit status and the HTTP
    routes map it to a response body, and neither may parse the prose. Every
    code a caller is allowed to see is enumerated in
    ``openai4s.update.verify.REFUSAL_CODES``; a code outside that tuple is a
    bug in this package, not an input the caller has to handle.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)

    def __str__(self) -> str:
        return f"{self.code}: {super().__str__()}"


#: name -> (module, attribute). Resolved on first access, never at import.
_LAZY: dict[str, tuple[str, str]] = {
    "Channel": ("openai4s.update.channel", "Channel"),
    "detect": ("openai4s.update.channel", "detect"),
    "check": ("openai4s.update.discovery", "check"),
    # Provided by the apply transaction. Declared here before those modules
    # exist so the public surface is one list rather than a growing one.
    "plan": ("openai4s.update.apply", "plan"),
    "apply": ("openai4s.update.apply", "apply_update"),
    "apply_update": ("openai4s.update.apply", "apply_update"),
    "rollback": ("openai4s.update.apply", "rollback"),
    "recover": ("openai4s.update.apply", "recover"),
    "prune": ("openai4s.update.store", "prune"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, attribute = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    from importlib import import_module

    return getattr(import_module(module_name), attribute)


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
