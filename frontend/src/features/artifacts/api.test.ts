import { describe, expect, it } from "vitest";
import { BINARY_SCAN_LIMIT, looksBinary } from "./api";

/** Deterministic letters from `alphabet`, so a failure reproduces exactly. */
function residues(alphabet: string, length: number, seed = 7): string {
  let x = seed;
  let out = "";
  for (let i = 0; i < length; i++) {
    x = (Math.imul(x, 1103515245) + 12345) >>> 0;
    out += alphabet[(x >>> 16) % alphabet.length];
  }
  return out;
}

function base64(bytes: Uint8Array): string {
  let raw = "";
  for (const byte of bytes) raw += String.fromCharCode(byte);
  return btoa(raw);
}

function pseudoRandomBytes(length: number): Uint8Array {
  const out = new Uint8Array(length);
  let x = 11;
  for (let i = 0; i < length; i++) {
    x = (Math.imul(x, 1664525) + 1013904223) >>> 0;
    out[i] = x >>> 24;
  }
  return out;
}

const AMINO = "ACDEFGHIKLMNPQRSTVWY";
/** A spike-length (1273 aa) protein record on one line, as FASTA writers emit it. */
const SPIKE_FASTA = ">sp|P0DTC2|SPIKE_SARS2 Spike glycoprotein\n" + residues(AMINO, 1273) + "\n";

describe("looksBinary (app.js:6055-6066)", () => {
  it("keeps one-line biological sequences as text", () => {
    // The old `/[A-Za-z0-9+/=]{1200,}/` rule called every one of these binary.
    expect(looksBinary(SPIKE_FASTA)).toBe(false);
    expect(looksBinary(">chr1\n" + residues("ACGT", 5000) + "\n")).toBe(false);
    // Soft-masked DNA mixes both cases; a sequence still carries no digits.
    expect(looksBinary(residues("ACGTNacgtn", 5000))).toBe(false);
    // A strict-PHYLIP name runs straight into its sequence.
    expect(looksBinary("sample0001" + residues("ACGT", 2000))).toBe(false);
    // Digits alone are a number, not an encoding.
    expect(looksBinary(String(2n ** 5000n))).toBe(false);
  });

  it("still flags a base64 blob", () => {
    expect(looksBinary(base64(pseudoRandomBytes(3000)))).toBe(true);
    // Low-entropy payloads still carry digits, `+` and `/` well past 1%.
    const floats = new Float64Array(500).map((_, i) => i);
    expect(looksBinary("data = " + base64(new Uint8Array(floats.buffer)) + "\n")).toBe(true);
    // Zero bytes encode to "AAAA...", which is also a poly-A tract.
    expect(looksBinary(base64(new Uint8Array(3000)))).toBe(false);
  });

  it("keeps the control-character and escape-dump rules", () => {
    expect(looksBinary("\u0000\u0001\u0002 ".repeat(100))).toBe(true);
    expect(looksBinary("b'" + "\\x00\\x1f".repeat(250) + "'")).toBe(true);
    expect(looksBinary("b'" + "\\x00".repeat(399) + "'")).toBe(false);
    expect(looksBinary("The quick brown fox jumps over the lazy dog.\n".repeat(500))).toBe(false);
    expect(looksBinary("")).toBe(false);
    expect(looksBinary(null)).toBe(false);
  });
});

describe("looksBinary cost (AUDIT P05)", () => {
  it("stays linear on runs just short of a blob", () => {
    // Every run one character short of the minimum made the old regex retry
    // from each start inside it: about a second per MiB, per streamed chunk.
    const line = residues(AMINO, 1199) + "\n";
    const text = line.repeat(Math.ceil((4 * BINARY_SCAN_LIMIT) / line.length));
    const started = performance.now();
    expect(looksBinary(text)).toBe(false);
    expect(performance.now() - started).toBeLessThan(250);
  });

  it("judges by the first MiB, which holds a whole kernel stream", () => {
    const blob = base64(pseudoRandomBytes(3000));
    const logs = "step=1 loss=0.5 acc=0.99\n".repeat(Math.ceil(BINARY_SCAN_LIMIT / 25) + 1);
    expect(looksBinary(blob + "\n" + logs)).toBe(true);
    // A cell stream is at most 1,000,000 characters plus its truncation marker.
    expect(looksBinary(logs.slice(0, 1_000_000 - blob.length) + blob)).toBe(true);
    expect(looksBinary(logs + blob)).toBe(false);
  });
});
