/**
 * Text encoding of an Artifact's bytes (UP-4).
 *
 * Instrument exports and older Chinese systems write CSV/TXT in GBK/GB18030,
 * and Excel's "Unicode text" is UTF-16 with a BOM. Decoding everything as
 * UTF-8 turned those into U+FFFD soup: a mangled thumbnail, and a viewer that
 * judged the file binary and offered only a download. The order is the one
 * the server's `text_decoding.py` uses:
 *
 *   1. a UTF-16 BOM decides UTF-16; a UTF-8 BOM is UTF-8;
 *   2. bytes that are valid UTF-8 are UTF-8 (ASCII included);
 *   3. bytes that are valid GB18030 *and* read as Chinese-script text are
 *      GB18030 (a superset of GBK and GB2312);
 *   4. anything else stays UTF-8 with replacement characters, as before.
 *
 * Step 3's plausibility test keeps arbitrary binary and other legacy code
 * pages from being "detected" as Chinese: no private-use characters, at least
 * one CJK ideograph or CJK punctuation mark, and nearly all non-ASCII
 * characters inside the repertoire GBK actually covers.
 */

export type DecodedText = { text: string; encoding: string };

const LABELS: Record<string, string> = {
  "utf-8": "UTF-8",
  "utf-16le": "UTF-16 LE",
  "utf-16be": "UTF-16 BE",
  gb18030: "GB18030",
};

/** The name a person reads ("GB18030"), not the WHATWG label. */
export function encodingLabel(encoding: string): string {
  return LABELS[encoding] || encoding.toUpperCase();
}

function inRanges(code: number, ranges: ReadonlyArray<readonly [number, number]>): boolean {
  for (const [lo, hi] of ranges) if (code >= lo && code <= hi) return true;
  return false;
}

/** CJK ideographs and CJK punctuation: the evidence that this is Chinese text. */
const CJK_CORE: ReadonlyArray<readonly [number, number]> = [
  [0x3000, 0x303f],
  [0x3400, 0x4dbf],
  [0x4e00, 0x9fff],
  [0xf900, 0xfaff],
  [0xff00, 0xffef],
];

/** What GBK text can contain beyond ASCII, roughly: CJK plus the symbol rows. */
const GBK_REPERTOIRE: ReadonlyArray<readonly [number, number]> = [
  ...CJK_CORE,
  [0x00a0, 0x00ff],
  [0x0391, 0x03c9],
  [0x0401, 0x0451],
  [0x2010, 0x203b],
  [0x2100, 0x2199],
  [0x2200, 0x22ff],
  [0x2312, 0x2312],
  [0x2460, 0x249b],
  [0x2500, 0x257f],
  [0x2580, 0x25ff],
  [0x2605, 0x2642],
  [0x3040, 0x30ff],
  [0x3100, 0x312f],
  [0x3220, 0x32a3],
  [0xfe30, 0xfe6b],
];

/** True when GB18030-decoded text reads as Chinese-script text. */
export function plausiblyChinese(text: string): boolean {
  let nonAscii = 0;
  let core = 0;
  let known = 0;
  for (const ch of text) {
    const code = ch.codePointAt(0) || 0;
    if (code < 0x80) continue;
    if ((code >= 0xe000 && code <= 0xf8ff) || code > 0xffff) return false;
    nonAscii += 1;
    if (inRanges(code, CJK_CORE)) core += 1;
    if (inRanges(code, GBK_REPERTOIRE)) known += 1;
  }
  return nonAscii > 0 && core > 0 && known / nonAscii >= 0.95;
}

function tryDecode(label: string, bytes: Uint8Array, partial: boolean): string | null {
  try {
    // `stream` on a prefix: an incomplete trailing sequence is held back and
    // dropped instead of becoming an error or U+FFFD.
    return new TextDecoder(label, { fatal: true }).decode(bytes, { stream: partial });
  } catch {
    return null;
  }
}

/**
 * Decode one complete file, or a prefix of one (`partial`), choosing the
 * encoding as described above.
 */
export function decodeArtifactBytes(bytes: Uint8Array, partial = false): DecodedText {
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    const text = tryDecode("utf-16le", bytes, partial);
    if (text !== null) return { text, encoding: "utf-16le" };
  }
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    const text = tryDecode("utf-16be", bytes, partial);
    if (text !== null) return { text, encoding: "utf-16be" };
  }
  const utf8 = tryDecode("utf-8", bytes, partial);
  if (utf8 !== null) return { text: utf8, encoding: "utf-8" };
  const gb = tryDecode("gb18030", bytes, partial);
  if (gb !== null && plausiblyChinese(gb)) return { text: gb, encoding: "gb18030" };
  return { text: new TextDecoder("utf-8").decode(bytes, { stream: partial }), encoding: "utf-8" };
}
