import { signal } from "@preact/signals";
import { describe, expect, it } from "vitest";

describe("F-03 scaffold", () => {
  it("wires @preact/signals", () => {
    const ready = signal(false);
    ready.value = true;
    expect(ready.value).toBe(true);
  });
});
