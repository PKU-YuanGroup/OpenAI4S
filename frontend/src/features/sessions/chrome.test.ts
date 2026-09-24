import { afterEach, describe, expect, it, vi } from "vitest";
import { LANG } from "../../i18n";
import { errorPrefix, reportFailure } from "./chrome";
import { actionFailedCopy } from "./copy";

describe("hint error prefix", () => {
  it("uses the zh/en literals without a new i18n key", () => {
    expect(errorPrefix("zh")).toBe("错误：");
    expect(errorPrefix("en")).toBe("Error: ");
  });
});

describe("watchActivateKeys", () => {
  afterEach(() => vi.unstubAllGlobals());

  class FakeEl {
    tabIndex = -1;
    dataset: Record<string, string> = {};
    attrs: Record<string, string> = {};
    constructor(readonly tagName: string, readonly classes: string[] = [], readonly children: FakeEl[] = []) {}
    getAttribute(name: string) { return this.attrs[name] ?? null; }
    setAttribute(name: string, value: string) { this.attrs[name] = value; }
    addEventListener() {}
    matches(selector: string) {
      return selector.split(",").some((part) => this.classes.includes(part.trim().replace(/^\./, "")));
    }
    querySelectorAll(selector: string): FakeEl[] {
      return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    }
  }

  it("watches the hosts later lanes fill rather than the whole body, and keys their tiles", async () => {
    vi.resetModules();
    const observed: unknown[] = [];
    let deliver: (records: Array<{ addedNodes: FakeEl[] }>) => void = () => undefined;
    class FakeObserver {
      constructor(callback: (records: Array<{ addedNodes: FakeEl[] }>) => void) { deliver = callback; }
      observe(target: unknown) { observed.push(target); }
    }
    class FakeDocument {
      body = new FakeEl("BODY");
      hosts: Record<string, FakeEl> = {
        "#dock-tabs": new FakeEl("DIV"), "#dock-files": new FakeEl("DIV"), "#messages": new FakeEl("DIV"),
      };
      querySelector(selector: string) { return this.hosts[selector] ?? null; }
      querySelectorAll() { return []; }
    }
    vi.stubGlobal("MutationObserver", FakeObserver);
    vi.stubGlobal("HTMLElement", FakeEl);
    vi.stubGlobal("Document", FakeDocument);
    const doc = new FakeDocument();
    const { watchActivateKeys: watch } = await import("./chrome");
    watch(doc as unknown as ParentNode);
    const named = (target: unknown) =>
      target === doc.body ? "body" : Object.keys(doc.hosts).find((key) => doc.hosts[key] === target) ?? "other";
    expect(observed.map(named)).toEqual(["#dock-tabs", "#dock-files", "#messages"]);

    // An artifact strip lands in the transcript; its "more" row grows later.
    const tile = new FakeEl("DIV", ["tile"]);
    const row = new FakeEl("DIV", ["gen-tiles"], [tile]);
    deliver([{ addedNodes: [new FakeEl("DIV", ["generated"], [row])] }]);
    expect(tile.tabIndex).toBe(0);
    expect(tile.attrs.role).toBe("button");
    expect(observed.at(-1) === row).toBe(true);
  });
});

describe("reportFailure", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the failure, with the error's own text, as an error hint", () => {
    const shown: string[] = [];
    const hintHost = { setAttribute() {}, innerHTML: "", appendChild: (node: { textContent: string }) => shown.push(node.textContent) };
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#composer-hint" ? hintHost : null),
      createElement: () => ({ textContent: "", style: {} }),
    });
    reportFailure(new Error("module refused"));
    expect(shown).toEqual([errorPrefix(LANG) + actionFailedCopy("module refused")]);
  });
});
