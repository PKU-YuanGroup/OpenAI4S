"""Kernel exception types, in a module that imports nothing.

`KernelInterruptUnavailable` has to be catchable by `KernelSupervisor`, and
the supervisor is reachable from `manager` through the watchdog -- so
defining it in `manager` and importing it from `supervisor` closes a cycle
that only fails on the import orders where `manager` happens to be
initialised first. A leaf module is the fix that does not depend on which
module some future caller reaches first.
"""

from __future__ import annotations

__all__ = ["KernelBusyError", "KernelInterruptUnavailable"]


class KernelBusyError(RuntimeError):
    """The worker protocol is owned by an in-flight cell transaction."""


class KernelInterruptUnavailable(RuntimeError):
    """This kernel cannot be interrupted at all — not "the attempt failed".

    A local kernel is interrupted with a signal to a pid we hold. A remote
    one has no pid here, so delivery depends on the allocation having been
    given a signal path; when it was not, there is nothing to try and no
    later attempt that would work. Its own type because every caller of
    `interrupt()` wraps it in `except Exception: pass` -- correctly, for the
    transient errors interruption really is best-effort about -- so a bare
    RuntimeError was swallowed by all of them, and the cancel API answered
    `interrupted: true` for a cell still running on the cluster.
    """
