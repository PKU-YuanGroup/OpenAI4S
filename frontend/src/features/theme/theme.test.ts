import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(here, "../../..");
const repoRoot = join(frontendRoot, "..");

type ThemeApi = typeof import("./theme");

type Harness = {
  attrs: Map<string, string>;
  classTokens: Set<string>;
  store: Map<string, string>;
  style: { colorScheme: string };
  prefersDark: boolean;
  systemListeners: Array<() => void>;
  raf: Array<FrameRequestCallback>;
  buttons: Map<string, FakeButton>;
  mol: { setBackgroundColor: ReturnType<typeof vi.fn>; render: ReturnType<typeof vi.fn> };
};

type FakeButton = {
  dataset: Record<string, string>;
  title: string;
  attrs: Map<string, string>;
  setAttribute: (name: string, value: string) => void;
};

function installHarness(opts: {
  theme?: string | null;
  prefersDark?: boolean;
  extraStore?: Record<string, string>;
}): Harness {
  const store = new Map<string, string>();
  if (opts.theme !== undefined && opts.theme !== null) store.set("os-theme", opts.theme);
  if (opts.extraStore) {
    for (const [key, value] of Object.entries(opts.extraStore)) store.set(key, value);
  }
  const attrs = new Map<string, string>();
  const classTokens = new Set<string>();
  const style = { colorScheme: "" };
  const systemListeners: Array<() => void> = [];
  const raf: Array<FrameRequestCallback> = [];
  const buttons = new Map<string, FakeButton>();
  const mol = {
    setBackgroundColor: vi.fn(),
    render: vi.fn(),
  };

  const documentElement = {
    style,
    setAttribute(name: string, value: string) {
      attrs.set(name, value);
    },
    getAttribute(name: string) {
      return attrs.get(name) ?? null;
    },
    removeAttribute(name: string) {
      attrs.delete(name);
    },
    hasAttribute(name: string) {
      return attrs.has(name);
    },
  };
  const body = {
    classList: {
      toggle(name: string, force?: boolean) {
        if (force === true) classTokens.add(name);
        else if (force === false) classTokens.delete(name);
        else if (classTokens.has(name)) classTokens.delete(name);
        else classTokens.add(name);
      },
      contains(name: string) {
        return classTokens.has(name);
      },
    },
  };
  const localStorage = {
    getItem(key: string) {
      return store.has(key) ? (store.get(key) as string) : null;
    },
    setItem(key: string, value: string) {
      store.set(key, String(value));
    },
    removeItem(key: string) {
      store.delete(key);
    },
    clear() {
      store.clear();
    },
  };
  const matchMedia = (query: string) => ({
    matches: harness.prefersDark,
    media: query,
    addEventListener(_type: string, listener: () => void) {
      systemListeners.push(listener);
    },
    addListener(listener: () => void) {
      systemListeners.push(listener);
    },
    removeEventListener() {},
    removeListener() {},
  });

  const harness: Harness = {
    attrs,
    classTokens,
    store,
    style,
    prefersDark: opts.prefersDark ?? false,
    systemListeners,
    raf,
    buttons,
    mol,
  };

  const fakeDocument = {
    documentElement,
    body,
    querySelector(sel: string) {
      return buttons.get(sel) ?? null;
    },
  };

  vi.stubGlobal("localStorage", localStorage);
  vi.stubGlobal("document", fakeDocument);
  vi.stubGlobal("matchMedia", matchMedia);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    raf.push(cb);
    return raf.length;
  });
  vi.stubGlobal("window", {
    matchMedia,
    S: { _molViewer: mol },
  });

  return harness;
}

async function loadTheme(): Promise<ThemeApi> {
  vi.resetModules();
  return import("./theme");
}

describe("F-09 theme", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  describe("storage and data-theme", () => {
    it("reads os-theme and writes only data-theme (never body.theme-dark)", async () => {
      const h = installHarness({ theme: "dark" });
      const api = await loadTheme();
      expect(api.THEME_STORAGE_KEY).toBe("os-theme");
      api.installTheme();
      expect(h.attrs.get("data-theme")).toBe("dark");
      expect(h.style.colorScheme).toBe("dark");
      expect(h.classTokens.has("theme-dark")).toBe(false);
      expect(h.store.has("os-lang")).toBe(false);
    });

    it("treats a missing or invalid stored value as system", async () => {
      const missing = installHarness({ theme: null, prefersDark: true });
      let api = await loadTheme();
      api.installTheme();
      expect(api.getTheme()).toBe("system");
      expect(missing.attrs.get("data-theme")).toBe("dark");

      vi.unstubAllGlobals();
      const invalid = installHarness({ theme: "nope", prefersDark: false });
      api = await loadTheme();
      api.installTheme();
      expect(api.getTheme()).toBe("system");
      expect(invalid.attrs.get("data-theme")).toBe("light");
    });

    it("setTheme persists os-theme and does not touch os-lang", async () => {
      const h = installHarness({ theme: "system", extraStore: { "os-lang": "zh" } });
      const api = await loadTheme();
      api.setTheme("dark");
      expect(h.store.get("os-theme")).toBe("dark");
      expect(h.store.get("os-lang")).toBe("zh");
      expect(h.attrs.get("data-theme")).toBe("dark");
      expect(h.classTokens.has("theme-dark")).toBe(false);
      api.setTheme("not-a-mode");
      expect(h.store.get("os-theme")).toBe("system");
    });

    it("applyTheme does not persist", async () => {
      const h = installHarness({ theme: "light" });
      const api = await loadTheme();
      api.applyTheme("dark");
      expect(api.getTheme()).toBe("dark");
      expect(h.store.get("os-theme")).toBe("light");
      expect(h.attrs.get("data-theme")).toBe("dark");
    });
  });

  describe("cycleTheme", () => {
    it("toggles light ↔ dark", async () => {
      const h = installHarness({ theme: "light" });
      const api = await loadTheme();
      api.cycleTheme();
      expect(h.store.get("os-theme")).toBe("dark");
      expect(h.attrs.get("data-theme")).toBe("dark");
      api.cycleTheme();
      expect(h.store.get("os-theme")).toBe("light");
      expect(h.attrs.get("data-theme")).toBe("light");
    });

    it("from system picks the opposite of the resolved value", async () => {
      const darkOs = installHarness({ theme: "system", prefersDark: true });
      let api = await loadTheme();
      api.cycleTheme();
      expect(darkOs.store.get("os-theme")).toBe("light");
      expect(darkOs.attrs.get("data-theme")).toBe("light");

      vi.unstubAllGlobals();
      const lightOs = installHarness({ theme: "system", prefersDark: false });
      api = await loadTheme();
      api.cycleTheme();
      expect(lightOs.store.get("os-theme")).toBe("dark");
      expect(lightOs.attrs.get("data-theme")).toBe("dark");
    });
  });

  describe("system preference", () => {
    it("follows prefers-color-scheme while the preference is system", async () => {
      const h = installHarness({ theme: "system", prefersDark: false });
      const api = await loadTheme();
      api.installTheme();
      expect(h.attrs.get("data-theme")).toBe("light");
      expect(h.systemListeners.length).toBeGreaterThan(0);
      h.prefersDark = true;
      const listener = h.systemListeners[0];
      if (listener === undefined) throw new Error("missing matchMedia listener");
      listener();
      expect(h.attrs.get("data-theme")).toBe("dark");
      expect(h.classTokens.has("theme-dark")).toBe(false);
    });

    it("ignores OS changes once the user forced light or dark", async () => {
      const h = installHarness({ theme: "light", prefersDark: false });
      const api = await loadTheme();
      api.installTheme();
      h.prefersDark = true;
      for (const listener of h.systemListeners) listener();
      expect(h.attrs.get("data-theme")).toBe("light");
    });
  });

  describe("instant + 3Dmol + toggle buttons", () => {
    it("sets data-theme-instant and clears it on the next animation frame", async () => {
      const h = installHarness({ theme: "dark" });
      const api = await loadTheme();
      api.applyTheme("dark", { instant: true });
      expect(h.attrs.has("data-theme-instant")).toBe(true);
      expect(h.raf.length).toBe(1);
      const frame = h.raf[0];
      if (frame === undefined) throw new Error("missing rAF");
      frame(0);
      expect(h.attrs.has("data-theme-instant")).toBe(false);
    });

    it("rethemes a live 3Dmol viewer with the verbatim colors", async () => {
      const h = installHarness({ theme: "light" });
      const api = await loadTheme();
      api.applyTheme("dark");
      expect(h.mol.setBackgroundColor).toHaveBeenCalledWith("#1c1c19");
      expect(h.mol.render).toHaveBeenCalled();
      api.applyTheme("light");
      expect(h.mol.setBackgroundColor).toHaveBeenLastCalledWith("white");
    });

    it("paints #dash-theme / #ws-theme data-icon without touching innerHTML", async () => {
      const h = installHarness({ theme: "dark" });
      const makeButton = (): FakeButton => {
        const attrs = new Map<string, string>();
        return {
          dataset: {},
          title: "",
          attrs,
          setAttribute(name: string, value: string) {
            attrs.set(name, value);
          },
        };
      };
      const dash = makeButton();
      const ws = makeButton();
      h.buttons.set("#dash-theme", dash);
      h.buttons.set("#ws-theme", ws);
      const api = await loadTheme();
      api.applyTheme("dark");
      expect(dash.dataset.icon).toBe("sun");
      expect(ws.dataset.icon).toBe("sun");
      api.applyTheme("light");
      expect(dash.dataset.icon).toBe("moon");
    });
  });

  describe("classic bootstrap + CSS single source", () => {
    it("loads theme-bootstrap.js as a classic blocking script in head", () => {
      const html = readFileSync(join(frontendRoot, "index.html"), "utf8");
      const headEnd = html.indexOf("</head>");
      expect(headEnd).toBeGreaterThan(0);
      const head = html.slice(0, headEnd);
      expect(head).toMatch(/<script src="\/static\/theme-bootstrap\.js"><\/script>/);
      expect(head).not.toMatch(/type=["']module["'][^>]*theme-bootstrap/);
      expect(head).not.toMatch(/theme-bootstrap\.js["'][^>]*type=["']module["']/);
      expect(html).toMatch(/<script type="module" src="\.\/src\/main\.tsx"><\/script>/);
    });

    it("committed dist/index.html keeps the same classic head script", () => {
      const html = readFileSync(
        join(repoRoot, "openai4s/server/webui/dist/index.html"),
        "utf8",
      );
      const headEnd = html.indexOf("</head>");
      expect(headEnd).toBeGreaterThan(0);
      const head = html.slice(0, headEnd);
      expect(head).toMatch(/<script src="\/static\/theme-bootstrap\.js"><\/script>/);
      expect(head).not.toMatch(/type=["']module["'][^>]*theme-bootstrap/);
      const bootstrapAt = head.indexOf("/static/theme-bootstrap.js");
      const moduleAt = head.indexOf('type="module"');
      expect(bootstrapAt).toBeGreaterThan(0);
      expect(moduleAt).toBeGreaterThan(bootstrapAt);
    });

    it("keeps theme-bootstrap.js as the os-theme IIFE that writes data-theme and html[lang]", () => {
      const src = readFileSync(
        join(repoRoot, "openai4s/server/webui/theme-bootstrap.js"),
        "utf8",
      );
      expect(src).toContain('localStorage.getItem("os-theme")');
      expect(src).toContain('setAttribute("data-theme"');
      expect(src).toContain('localStorage.getItem("os-lang")');
      expect(src).toContain("document.documentElement.lang");
      expect(src).not.toContain("theme-dark");
      expect(src).not.toContain("type=\"module\"");
      expect(src.startsWith("/*")).toBe(true);
    });

    it("converges style.css theme-toggle affordance onto html[data-theme=dark]", () => {
      const css = readFileSync(join(repoRoot, "openai4s/server/webui/style.css"), "utf8");
      expect(css).not.toMatch(/body\.theme-dark/);
      expect(css).toContain(
        'html[data-theme="dark"] #dash-theme,html[data-theme="dark"] #ws-theme{color:var(--accent-fill)}',
      );
    });

    it("theme.ts source never mentions body.theme-dark", () => {
      const src = readFileSync(join(here, "theme.ts"), "utf8");
      expect(src).not.toMatch(/theme-dark/);
      expect(src).not.toMatch(/classList/);
      expect(src).toContain('THEME_STORAGE_KEY = "os-theme"');
      expect(src).not.toContain("os-lang");
    });
  });
});
