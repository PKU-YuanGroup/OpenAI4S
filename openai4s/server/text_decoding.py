"""Text encoding of Artifact bytes (UP-4).

Instrument exports and older Chinese systems write CSV/TXT in GBK/GB18030,
and Excel's "Unicode text" is UTF-16 with a BOM. Decoding them as UTF-8 with
replacement turned a table into U+FFFD soup. The rule matches the workbench
client's ``frontend/src/features/artifacts/text-encoding.ts``:

1. a UTF-16 BOM decides UTF-16; a UTF-8 BOM is UTF-8 (and is dropped);
2. bytes that are valid UTF-8 are UTF-8 (ASCII included);
3. bytes that are valid GB18030 *and* read as Chinese-script text are GB18030
   (a superset of GBK and GB2312);
4. anything else stays UTF-8 with replacement characters, as before.

Step 3's plausibility test keeps binary and other legacy code pages from being
"detected" as Chinese: no private-use or astral characters, at least one CJK
ideograph or CJK punctuation mark, and nearly all non-ASCII characters inside
the repertoire GBK covers. Stdlib only.
"""

from __future__ import annotations

import codecs

#: CJK ideographs and CJK punctuation: the evidence that text is Chinese.
_CJK_CORE: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xFF00, 0xFFEF),
)
#: What GBK text can contain beyond ASCII, roughly: CJK plus its symbol rows.
_GBK_REPERTOIRE: tuple[tuple[int, int], ...] = _CJK_CORE + (
    (0x00A0, 0x00FF),
    (0x0391, 0x03C9),
    (0x0401, 0x0451),
    (0x2010, 0x203B),
    (0x2100, 0x2199),
    (0x2200, 0x22FF),
    (0x2312, 0x2312),
    (0x2460, 0x249B),
    (0x2500, 0x257F),
    (0x2580, 0x25FF),
    (0x2605, 0x2642),
    (0x3040, 0x30FF),
    (0x3100, 0x312F),
    (0x3220, 0x32A3),
    (0xFE30, 0xFE6B),
)

#: The labels the client uses, so both halves name an encoding the same way.
UTF8 = "utf-8"
GB18030 = "gb18030"
UTF16LE = "utf-16le"
UTF16BE = "utf-16be"


def _inside(code: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= code <= high for low, high in ranges)


def plausibly_chinese(text: str) -> bool:
    """True when GB18030-decoded text reads as Chinese-script text."""
    non_ascii = core = known = 0
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            continue
        if 0xE000 <= code <= 0xF8FF or code > 0xFFFF:
            return False
        non_ascii += 1
        if _inside(code, _CJK_CORE):
            core += 1
        if _inside(code, _GBK_REPERTOIRE):
            known += 1
    return non_ascii > 0 and core > 0 and known / non_ascii >= 0.95


def _strict(codec: str, raw: bytes, partial: bool) -> str | None:
    """Decode strictly; a prefix keeps back an incomplete trailing sequence."""
    try:
        return codecs.getincrementaldecoder(codec)("strict").decode(
            raw, final=not partial
        )
    except UnicodeDecodeError:
        return None


def decode_text(raw: bytes, *, partial: bool = False) -> tuple[str, str]:
    """``(text, encoding)`` for one complete file, or a prefix of one."""
    if raw.startswith(codecs.BOM_UTF16_LE):
        text = _strict("utf-16-le", raw[2:], partial)
        if text is not None:
            return text, UTF16LE
    if raw.startswith(codecs.BOM_UTF16_BE):
        text = _strict("utf-16-be", raw[2:], partial)
        if text is not None:
            return text, UTF16BE
    text = _strict("utf-8-sig", raw, partial)
    if text is not None:
        return text, UTF8
    text = _strict("gb18030", raw, partial)
    if text is not None and plausibly_chinese(text):
        return text, GB18030
    lossy = codecs.getincrementaldecoder("utf-8-sig")("replace").decode(
        raw, final=not partial
    )
    return lossy, UTF8


__all__ = [
    "GB18030",
    "UTF16BE",
    "UTF16LE",
    "UTF8",
    "decode_text",
    "plausibly_chinese",
]
