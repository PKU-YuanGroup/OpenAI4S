"""What a bounded channel has to say about the bound.

Seven buffers in this tree drop input to stay inside a budget, and until this
module each of them decided independently what -- if anything -- to say about
it. Four spellings existed, and two of the seven said nothing at all, which is
the failure this file is for: a bound that is enforced and unreported does not
produce missing information, it produces a confident wrong answer. A `host.bash`
that printed five million characters returned exactly thirty thousand and
recorded, in the durable audit, `chars: 30000` beside a `sha256` of the tail
presented as the command's stdout digest -- indistinguishable from a command
that printed thirty thousand characters. `len(stdout)` is not a derivation:
it is ambiguous for the command that printed exactly the budget.

Four numbers, because four is what a reader needs and no fewer:

``seen``       -- what arrived. Counted where every byte passes, never inferred.
``retained``   -- what was kept, which is what the consumer is holding.
``dropped``    -- the difference, stated so nobody has to compute it.
``truncated``  -- the fact, stated so nobody has to *decide* it. A consumer
                  asking "was this cut?" would otherwise have to know that
                  `dropped > 0` means yes, that a marker in the text means yes,
                  and that neither is a promise.

A function and not a dataclass, deliberately. A type would be one more declared
shape that nothing is obliged to fill in; this returns a plain dict that every
existing wire, audit row and frozen schema already accepts, and it landed with
its consumers rather than ahead of them.

Here rather than in `kernel/` for the reason `process_group.py` states about
itself: this is imported by the daemon *and* by `sdk/bash.py`, which runs
inside the worker, so it must stay pure stdlib and free of kernel imports.
"""

from __future__ import annotations


def channel_counters(
    *,
    seen: int,
    retained: int,
    prefix: str = "",
    unit: str = "bytes",
) -> dict:
    """The four numbers for one bounded channel, ready to merge into a record.

    ``prefix`` namespaces a channel inside a record that carries more than one
    (`stdout_`, `stderr_`); ``unit`` is `bytes` where bytes were counted and
    `chars` where characters were, because the two are not the same number and
    a record that says `bytes` while counting characters is the kind of claim
    this module exists to stop.

    ``dropped`` is clamped at zero rather than trusted: `seen` and `retained`
    are counted by different objects at different times, and a negative loss is
    a bug in the caller, not a fact to publish.
    """
    seen = int(seen)
    retained = int(retained)
    dropped = max(0, seen - retained)
    return {
        f"{prefix}seen_{unit}": seen,
        f"{prefix}retained_{unit}": retained,
        f"{prefix}dropped_{unit}": dropped,
        f"{prefix}truncated": dropped > 0,
    }
