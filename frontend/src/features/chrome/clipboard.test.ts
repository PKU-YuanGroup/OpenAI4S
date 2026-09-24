/**
 * copyText reports a copy only when one happened: the async API is called as
 * a method, a refusal falls back to a selection copy, and nothing available
 * resolves false instead of a success the user cannot see through.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { copyText } from "./clipboard";

class FakeArea {
  value = "";
  style: Record<string, string> = {};
  attrs: Record<string, string> = {};
  removed = false;
  selected = false;
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  select(): void {
    this.selected = true;
  }
  remove(): void {
    this.removed = true;
  }
}

function stubDocument(execResult: boolean | "throw") {
  const areas: FakeArea[] = [];
  const focus = vi.fn();
  const doc = {
    body: { appendChild: vi.fn() },
    activeElement: { focus },
    createElement: vi.fn(() => {
      const area = new FakeArea();
      areas.push(area);
      return area;
    }),
    execCommand: vi.fn((command: string) => {
      if (execResult === "throw") throw new Error("denied");
      return command === "copy" && execResult;
    }),
  };
  vi.stubGlobal("document", doc);
  return { doc, areas, focus };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("copyText", () => {
  it("resolves true after the async API confirms, called on the clipboard object", async () => {
    const clipboard = {
      written: "",
      async writeText(this: { written: string }, text: string) {
        // A detached call has no receiver: the browser throws Illegal invocation.
        if (this !== clipboard) throw new TypeError("Illegal invocation");
        this.written = text;
      },
    };
    vi.stubGlobal("navigator", { clipboard });
    const { doc } = stubDocument(true);
    await expect(copyText("abc")).resolves.toBe(true);
    expect(clipboard.written).toBe("abc");
    expect(doc.execCommand).not.toHaveBeenCalled();
  });

  it("falls back to a selection copy when the async API refuses", async () => {
    vi.stubGlobal("navigator", { clipboard: { writeText: () => Promise.reject(new Error("denied")) } });
    const { areas, focus } = stubDocument(true);
    await expect(copyText("fallback")).resolves.toBe(true);
    expect(areas).toHaveLength(1);
    expect(areas[0]!.value).toBe("fallback");
    expect(areas[0]!.selected).toBe(true);
    expect(areas[0]!.removed).toBe(true);
    expect(focus).toHaveBeenCalled();
  });

  it("uses the selection copy in an insecure context without navigator.clipboard", async () => {
    vi.stubGlobal("navigator", {});
    const { doc } = stubDocument(true);
    await expect(copyText("lan")).resolves.toBe(true);
    expect(doc.execCommand).toHaveBeenCalledWith("copy");
  });

  it("resolves false when neither path confirms a write", async () => {
    vi.stubGlobal("navigator", {});
    const refused = stubDocument(false);
    await expect(copyText("x")).resolves.toBe(false);
    expect(refused.areas[0]!.removed).toBe(true);

    stubDocument("throw");
    await expect(copyText("x")).resolves.toBe(false);

    vi.stubGlobal("document", undefined);
    await expect(copyText("x")).resolves.toBe(false);
  });
});
