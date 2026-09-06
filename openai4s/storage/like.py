"""The substring ``LIKE`` pattern the project and artifact searches share.

Three repositories carried a byte-identical private copy of this function
(frames, artifacts, and the DataPro index). A copy is where a fix lands once:
the DataPro copy is paired with a NUL escape because SQLite's ``LIKE`` stops at
an embedded NUL, and the other two were not -- so ``lab\\x00zzz`` matched
every project and every filename containing ``lab``, a filter silently
widened rather than applied.

The DataPro index keeps its own copy on purpose: its escape is reversible and
applied to the *stored* text as well as the needle, which this helper does not
do. Frames and artifacts search raw columns, so they share this one.

The pattern is meant for ``LIKE ? ESCAPE '\\'``.
"""

from __future__ import annotations

#: A private-use code point standing in for NUL inside a pattern. SQLite reads
#: the pattern as a C string, so the NUL itself would end it; a code point no
#: stored text is expected to carry keeps the rest of the needle in force and
#: makes the pattern match only text that really contains that code point.
_NUL_STAND_IN = "\ue001"


def like_contains(value: str) -> str:
    """A substring pattern that treats ``\\``, ``%`` and ``_`` as literals."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace("\x00", _NUL_STAND_IN)
    )
    return f"%{escaped}%"


__all__ = ["like_contains"]
