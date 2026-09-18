/**
 * The column resizers are created by bootChrome(), before the locale chunks
 * land. Their tooltip was `t("resizer.drag")` read once at creation, so every
 * load showed the bare key "resizer.drag" on hover and a language switch never
 * changed it. It is a static label now, repainted with the rest.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

type FakeElement = {
  className: string;
  textContent: string;
  title: string;
  getAttribute: (name: string) => string | null;
  hasAttribute: (name: string) => boolean;
  setAttribute: (name: string, value: string) => void;
  removeAttribute: (name: string) => void;
  addEventListener: () => void;
  classList: { add: () => void; remove: () => void };
};

function fakeElement(): FakeElement {
  const attrs = new Map<string, string>();
  return {
    className: "",
    textContent: "",
    title: "",
    getAttribute: (name) => (attrs.has(name) ? attrs.get(name)! : null),
    hasAttribute: (name) => attrs.has(name),
    setAttribute: (name, value) => {
      attrs.set(name, value);
    },
    removeAttribute: (name) => {
      attrs.delete(name);
    },
    addEventListener: () => undefined,
    classList: { add: () => undefined, remove: () => undefined },
  };
}

let release: () => void = () => undefined;

afterEach(() => {
  release();
  vi.doUnmock("../../i18n/en");
  vi.doUnmock("../../i18n/zh");
  vi.resetModules();
  vi.unstubAllGlobals();
});

describe("column resizer tooltip", () => {
  it("is never the bare key, and is repainted when the dictionaries land and on a language switch", async () => {
    const created: FakeElement[] = [];
    const main = {
      querySelector: () => null,
      appendChild: (child: FakeElement) => {
        created.push(child);
      },
    };
    vi.stubGlobal("document", {
      documentElement: { lang: "", style: { setProperty: () => undefined } },
      body: { classList: { contains: () => false } },
      createElement: () => fakeElement(),
      querySelector: (selector: string) => (selector === "#main" ? main : null),
      querySelectorAll: (selector: string) => {
        const attr = /^\[([\w-]+)\]$/.exec(selector)?.[1];
        return attr ? created.filter((node) => node.hasAttribute(attr)) : [];
      },
    });
    vi.stubGlobal("window", { addEventListener: () => undefined, innerWidth: 1440 });
    vi.stubGlobal("localStorage", {
      getItem: (key: string) => (key === "os-lang" ? "en" : null),
      setItem: () => undefined,
    });

    vi.resetModules();
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const realEn = (await vi.importActual<{ default: Record<string, string> }>("../../i18n/en")).default;
    const realZh = (await vi.importActual<{ default: Record<string, string> }>("../../i18n/zh")).default;
    vi.doMock("../../i18n/en", async () => {
      await gate;
      return { default: realEn };
    });
    vi.doMock("../../i18n/zh", async () => {
      await gate;
      return { default: realZh };
    });
    const runtime = await import("../../i18n/runtime");
    const { initColResizers } = await import("./resizer");

    initColResizers();
    const handle = created.find((node) => node.className.includes("col-resizer-side"));
    expect(handle).toBeDefined();
    expect(handle!.title).not.toBe("resizer.drag");

    release();
    await runtime.i18nReady();
    expect(handle!.title).toBe(realEn["resizer.drag"]);

    await runtime.setLang("zh");
    expect(handle!.title).toBe(realZh["resizer.drag"]);
  });
});
