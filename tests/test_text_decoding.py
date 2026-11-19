"""UP-4: GBK/GB18030 and UTF-16 Artifact text decodes as itself.

A GBK CSV from an instrument previewed as replacement characters (the
thumbnail's column names were U+FFFD soup) and the viewer gave up and offered
a download. The server half of the rule is pinned here; the client half
(`frontend/src/features/artifacts/text-encoding.ts`) is pinned by Vitest with
the same cases.
"""

from __future__ import annotations

import codecs

import pytest

from openai4s.server.text_decoding import decode_text, plausibly_chinese

GBK_TABLE = "患者编号,年龄,温度℃\n张三,42,36.5\n"


def test_utf8_stays_utf8_and_its_bom_is_dropped():
    assert decode_text(b"a,b\n1,2\n") == ("a,b\n1,2\n", "utf-8")
    assert decode_text(codecs.BOM_UTF8 + "患者".encode("utf-8")) == ("患者", "utf-8")


@pytest.mark.parametrize("codec", ["gbk", "gb2312", "gb18030"])
def test_chinese_legacy_encodings_decode_as_gb18030(codec):
    assert decode_text(GBK_TABLE.encode(codec)) == (GBK_TABLE, "gb18030")


def test_a_prefix_drops_the_half_character_at_the_cut():
    raw = GBK_TABLE.encode("gbk")
    text, encoding = decode_text(raw[:3], partial=True)
    assert (text, encoding) == ("患", "gb18030")


def test_utf16_with_a_bom_is_decoded():
    assert decode_text(codecs.BOM_UTF16_LE + "a\t中".encode("utf-16-le")) == (
        "a\t中",
        "utf-16le",
    )
    assert decode_text(codecs.BOM_UTF16_BE + "a".encode("utf-16-be")) == (
        "a",
        "utf-16be",
    )


def test_binary_and_other_code_pages_are_not_called_chinese():
    png = b"\x89PNG\r\n\x1a\n\x00\xff\x80"
    assert decode_text(png)[1] == "utf-8"
    assert decode_text("café crème".encode("cp1252"))[1] == "utf-8"
    assert not plausibly_chinese("plain ascii")
    assert not plausibly_chinese("°±")
    assert plausibly_chinese("温度 25°C")
    assert not plausibly_chinese("中")
