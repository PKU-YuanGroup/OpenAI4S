import { describe, expect, it } from "vitest";
import {
  _mdStableCut,
  emptyMdCutState,
  mdStableCut,
  shouldAdvanceSealed,
  type MdCutState,
} from "./cut";

/** Byte-faithful copy of app.js:5378-5402, used as the spec for the incremental scan. */
function originalMdStableCut(text: string): number {
  const limit = Math.max(0, text.length - 120);
  if (limit < 80) return 0;
  const lines = text.split("\n");
  let offset = 0,
    stable = 0,
    openFence: { char: string; length: number } | null = null;
  for (let i = 0; i < lines.length - 1; i++) {
    const line = lines[i]!;
    const boundary = offset + line.length + 1;
    if (boundary > limit) break;
    if (openFence) {
      const trimmed = line.trim();
      const closes =
        trimmed.length >= openFence.length &&
        [...trimmed].every((ch) => ch === openFence!.char);
      if (closes) {
        openFence = null;
        if (boundary >= 60) stable = boundary;
      }
    } else {
      const match = line.match(/^\s*(`{3,}|~{3,})[ \t]*[\w+#.\-]*[ \t]*$/);
      if (match && match[1]) {
        openFence = { char: match[1][0]!, length: match[1].length };
      } else if (!line.trim() && boundary >= 60) {
        stable = boundary;
      }
    }
    offset = boundary;
  }
  return stable;
}

function growIncremental(text: string, step = 1): { cut: number; state: MdCutState } {
  let state = emptyMdCutState();
  for (let i = 0; i <= text.length; i += step) {
    const end = Math.min(i, text.length);
    state = mdStableCut(text.slice(0, end), state);
  }
  if (state.textLen !== text.length) state = mdStableCut(text, state);
  return { cut: state.stable, state };
}

function pad(body: string, min = 240): string {
  if (body.length >= min) return body;
  return body + "x".repeat(min - body.length);
}

describe("_mdStableCut original semantics", () => {
  it("returns 0 while the sealed window is too small (len < 200)", () => {
    expect(_mdStableCut("hello\n\nworld")).toBe(0);
    expect(_mdStableCut("a".repeat(199))).toBe(0);
    expect(originalMdStableCut("a".repeat(199))).toBe(0);
  });

  it("keeps the final ~120 chars soft", () => {
    const head = "para one.\n\n";
    const text = pad(head + "para two is the unstable tail", 240);
    const cut = _mdStableCut(text);
    expect(cut).toBe(originalMdStableCut(text));
    expect(text.length - cut).toBeGreaterThanOrEqual(120);
  });

  it("seals on a top-level blank line once past offset 60", () => {
    const text = pad("word ".repeat(20) + "\n\n" + "more ".repeat(40), 260);
    const cut = originalMdStableCut(text);
    expect(cut).toBeGreaterThanOrEqual(60);
    expect(_mdStableCut(text)).toBe(cut);
  });

  it("does not treat a blank line inside a fence as a stable boundary", () => {
    const text = pad("```python\nprint(1)\n\nprint(2)\nstill open", 260);
    expect(originalMdStableCut(text)).toBe(0);
    expect(_mdStableCut(text)).toBe(0);
  });

  it("does not mistake an opening fence for a closing one", () => {
    const text = pad("```\nstill in the fence\n```notclose\nmore", 260);
    // ` ```notclose ` has letters, so it is not a closer; cut stays 0 or a
    // later blank, never the opening fence itself.
    const cut = originalMdStableCut(text);
    expect(cut).toBe(_mdStableCut(text));
    expect(text.slice(0, cut)).not.toMatch(/^```\n$/);
  });

  it("seals at a completed fence once the closer sits past offset 60", () => {
    // The original only records a seal when `boundary >= 60`. A tiny fenced
    // block at the start of the string therefore does not stabilize — the
    // intro has to push the closer past 60, and the tail has to leave 120
    // chars soft so the closer is inside `limit`.
    const intro = "Intro paragraph that pushes the fence closer past sixty chars.\n";
    const text = pad(
      intro + "```js\nconst x = 1;\n```\n\nprose after the fence. ",
      400,
    );
    const cut = originalMdStableCut(text);
    expect(cut).toBeGreaterThanOrEqual(60);
    expect(_mdStableCut(text)).toBe(cut);
    expect(text.slice(0, cut)).toContain("```js");
    expect(text.slice(0, cut)).toContain("```\n");
  });

  it("accepts tildes as fence characters", () => {
    const text = pad("~~~\ncode\n~~~\n\nafter", 260);
    expect(_mdStableCut(text)).toBe(originalMdStableCut(text));
  });
});

describe("_mdStableCut incremental scan", () => {
  it("matches a from-scratch cut after growing one character at a time", () => {
    const text = pad(
      "Intro paragraph.\n\n```python\nprint(1)\n\nprint(2)\n```\n\nClosing prose.\n",
      400,
    );
    const grown = growIncremental(text, 1);
    expect(grown.cut).toBe(originalMdStableCut(text));
    expect(_mdStableCut(text)).toBe(grown.cut);
  });

  it("matches at every prefix of a long append-only stream", () => {
    const chunks = [
      "Start of the answer.\n",
      "More tokens here, still one paragraph.\n\n",
      "```ts\nfunction f() {\n  return 1;\n",
      "}\n```\n\n",
      "And a trailing paragraph that stays in the soft tail.",
    ];
    let acc = "";
    let state = emptyMdCutState();
    for (const chunk of chunks) {
      for (const ch of chunk) {
        acc += ch;
        state = mdStableCut(acc, state);
        if (acc.length >= 200 && acc.endsWith("\n")) {
          expect(state.stable, acc.slice(-40)).toBe(originalMdStableCut(acc));
        }
      }
    }
    expect(state.stable).toBe(originalMdStableCut(acc));
  });

  it("resumes from offset: fence state survives blank lines in the open block", () => {
    let acc = "```\n" + "line\n".repeat(20) + "hold\n".repeat(20);
    let state = mdStableCut(acc);
    expect(state.openFence).not.toBeNull();
    expect(state.stable).toBe(originalMdStableCut(acc));
    acc += "\n\n";
    state = mdStableCut(acc, state);
    expect(state.openFence).not.toBeNull();
    expect(state.stable).toBe(originalMdStableCut(acc));
    acc += "```\n\nprose\n" + "t".repeat(160);
    state = mdStableCut(acc, state);
    expect(state.openFence).toBeNull();
    expect(state.stable).toBe(originalMdStableCut(acc));
    expect(state.stable).toBeGreaterThanOrEqual(60);
  });

  it("resets when the string shrinks (new md block)", () => {
    const long = pad("word ".repeat(20) + "\n\n" + "more ".repeat(40), 260);
    const prev = mdStableCut(long);
    expect(prev.stable).toBeGreaterThanOrEqual(60);
    const next = mdStableCut("short", prev);
    expect(next.stable).toBe(0);
    expect(next.offset).toBe(0);
  });

  it("offset is monotonic on an append-only stream once past the short-circuit", () => {
    const text = pad("a\n\nb\n\nc\n", 300);
    let state = emptyMdCutState();
    let lastOffset = 0;
    for (let i = 200; i <= text.length; i++) {
      state = mdStableCut(text.slice(0, i), state);
      expect(state.offset).toBeGreaterThanOrEqual(lastOffset);
      lastOffset = state.offset;
    }
  });

  it("sealed-prefix hysteresis is +40 (flush, not the cut itself)", () => {
    expect(shouldAdvanceSealed(0, 0)).toBe(false);
    expect(shouldAdvanceSealed(40, 0)).toBe(false);
    expect(shouldAdvanceSealed(41, 0)).toBe(true);
    expect(shouldAdvanceSealed(100, 50)).toBe(true);
    expect(shouldAdvanceSealed(90, 50)).toBe(false);
  });
});
