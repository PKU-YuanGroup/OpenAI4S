"""Shared size contract for the kernel JSON-lines protocol.

The worker is the producer and a remote transport is the receiver, but a
frame is valid only if both agree on the same byte ceiling.  Keeping the
number in either endpoint lets the other drift into rejecting frames the
producer explicitly admitted.
"""

from __future__ import annotations

# Captured stdout, stderr, and error text are independently bounded in
# characters before a response is serialized.  The wire writer uses
# ``ensure_ascii=False``: a control character's six-byte ``\u0000`` escape is
# therefore the worst expansion of one valid code point (non-BMP UTF-8 is four
# bytes).  Derive the ceiling from all three fields, then reserve two million
# bytes for the remaining bounded response metadata.
MAX_OUTPUT_CHARS = 1_000_000
JSON_WORST_BYTES_PER_CHAR = 6
MAX_RESPONSE_TEXT_FIELDS = 3
MAX_FRAME_BYTES = (
    JSON_WORST_BYTES_PER_CHAR * MAX_RESPONSE_TEXT_FIELDS * MAX_OUTPUT_CHARS + 2_000_000
)

__all__ = [
    "JSON_WORST_BYTES_PER_CHAR",
    "MAX_FRAME_BYTES",
    "MAX_OUTPUT_CHARS",
    "MAX_RESPONSE_TEXT_FIELDS",
]
