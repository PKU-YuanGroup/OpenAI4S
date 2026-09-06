import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type LayoutApi = typeof import("./layout");

describe("F-20 layout density", () => {
  let store: Map<string, string>;
  let classTokens: Set<string>;
  let api: LayoutApi;

  beforeEach(async () => {
    vi.resetModules();
    store = new Map();
    classTokens = new Set();
    const body = {
      classList: {
        remove(...names: string[]) {
          for (const n of names) classTokens.delete(n);
        },
        add(...names: string[]) {
          for (const n of names) classTokens.add(n);
        },
        contains(name: string) {
          return classTokens.has(name);
        },
      },
    };
    vi.stubGlobal("document", {
      body,
      documentElement: { style: { setProperty: vi.fn() } },
      querySelector: () => null,
    });
    vi.stubGlobal("window", {});
    vi.stubGlobal("localStorage", {
      getItem(key: string) {
        return store.has(key) ? (store.get(key) as string) : null;
      },
      setItem(key: string, value: string) {
        store.set(key, String(value));
      },
    });
    api = await import("./layout");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("applyLayout writes layout-compact / layout-wide and comfortable is the absence", () => {
    api.applyLayout("compact");
    expect(classTokens.has("layout-compact")).toBe(true);
    expect(classTokens.has("layout-wide")).toBe(false);
    api.applyLayout("wide");
    expect(classTokens.has("layout-compact")).toBe(false);
    expect(classTokens.has("layout-wide")).toBe(true);
    api.applyLayout("comfortable");
    expect(classTokens.has("layout-compact")).toBe(false);
    expect(classTokens.has("layout-wide")).toBe(false);
  });

  it("setLayout persists os-layout", () => {
    api.setLayout("compact");
    expect(store.get("os-layout")).toBe("compact");
    expect(classTokens.has("layout-compact")).toBe(true);
  });

  it("readStoredLayout defaults to comfortable", () => {
    expect(api.readStoredLayout()).toBe("comfortable");
    store.set("os-layout", "wide");
    expect(api.readStoredLayout()).toBe("wide");
    store.set("os-layout", "nope");
    expect(api.readStoredLayout()).toBe("comfortable");
  });
});

describe("F-20 column width restore", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("restoreColWidths clamps side 200–520 and dock ≥360", async () => {
    const props = new Map<string, string>();
    const store = new Map<string, string>([
      ["os-side-w", "240"],
      ["os-dock-w", "400"],
    ]);
    vi.stubGlobal("document", {
      documentElement: {
        style: {
          setProperty(name: string, value: string) {
            props.set(name, value);
          },
        },
      },
      querySelector: () => null,
    });
    vi.stubGlobal("window", { innerWidth: 1400 });
    vi.stubGlobal("localStorage", {
      getItem(key: string) {
        return store.get(key) ?? null;
      },
    });
    const { restoreColWidths } = await import("./resizer");
    restoreColWidths();
    expect(props.get("--side-w")).toBe("240px");
    expect(props.get("--dock-w")).toBe("400px");
  });

  it("ignores an out-of-range persisted side width", async () => {
    const props = new Map<string, string>();
    vi.stubGlobal("document", {
      documentElement: {
        style: {
          setProperty(name: string, value: string) {
            props.set(name, value);
          },
        },
      },
    });
    vi.stubGlobal("window", { innerWidth: 1400 });
    vi.stubGlobal("localStorage", {
      getItem(key: string) {
        if (key === "os-side-w") return "80";
        if (key === "os-dock-w") return "100";
        return null;
      },
    });
    const { restoreColWidths } = await import("./resizer");
    restoreColWidths();
    expect(props.has("--side-w")).toBe(false);
    expect(props.has("--dock-w")).toBe(false);
  });
});
