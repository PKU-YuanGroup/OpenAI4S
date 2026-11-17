/**
 * The load-time readiness fetch.
 *
 * app.js fetched standard-profile readiness once during init. The port kept
 * `refreshEnvironmentStatus` and dropped that call, so the readiness banner had
 * nothing to show until a send was refused or the Compute tab was opened --
 * and the Shell did not render the banner at all, so even then it was not on
 * the page. The Shell half is covered in `components/dashboard/Shell.test.tsx`;
 * this is the fetch.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const i18n = vi.hoisted(() => {
  const state = {
    language: "en",
    hooks: [] as Array<(lang: string) => void>,
    release: (): void => undefined,
    fail: (): void => undefined,
    ready: vi.fn((): Promise<void> => Promise.resolve()),
    /** Hold `i18nReady()` open until `release()` or `fail()`. */
    arm(): void {
      const pending = new Promise<void>((resolve, reject) => {
        state.release = () => resolve();
        state.fail = () => reject(new Error("locale chunk failed"));
      });
      state.ready.mockImplementation(() => pending);
    },
  };
  return state;
});
const server = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("../../i18n/runtime", () => ({
  i18nReady: i18n.ready,
  onLanguageChange: (hook: (lang: string) => void) => {
    i18n.hooks.push(hook);
    return () => undefined;
  },
  t: (key: string, ...args: unknown[]) => [i18n.language, key, ...args].join("|"),
}));
vi.mock("../sessions/api", () => ({ api: server.api }));
vi.mock("../sessions/chrome", () => ({ hint: vi.fn() }));

type Banner = {
  hidden: boolean;
  title: { textContent: string };
  summary: { textContent: string };
  action: { textContent: string };
};

/** Two banners, the way the Shell renders them: dashboard and conversation. */
function mountBanners(): Banner[] {
  const banners: Banner[] = [0, 1].map(() => ({
    hidden: true,
    title: { textContent: "" },
    summary: { textContent: "" },
    action: { textContent: "" },
  }));
  const nodes = banners.map((banner) => ({
    classList: {
      toggle: (_name: string, hide: boolean) => {
        banner.hidden = hide;
      },
    },
    querySelector: (selector: string) =>
      selector.includes("title")
        ? banner.title
        : selector.includes("summary")
          ? banner.summary
          : banner.action,
  }));
  vi.stubGlobal("document", {
    querySelectorAll: (selector: string) =>
      selector === ".environment-readiness-banner" ? nodes : [],
  });
  return banners;
}

const NOT_READY = {
  standard_profile_readiness: {
    schema_version: 1,
    enabled: true,
    ready: false,
    state: "needs_setup",
    missing_environments: ["python", "r"],
    missing_packages: { python: ["numpy", "pandas"], r: ["r-base"] },
  },
};

const flush = async (): Promise<void> => {
  for (let i = 0; i < 6; i += 1) await Promise.resolve();
};

async function freshLane() {
  vi.resetModules();
  return import("./environment");
}

beforeEach(() => {
  i18n.language = "en";
  i18n.hooks.length = 0;
  i18n.ready.mockReset();
  i18n.ready.mockImplementation(() => Promise.resolve());
  server.api.mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the load-time readiness fetch", () => {
  it("waits for the dictionaries, then shows both banners", async () => {
    const banners = mountBanners();
    i18n.arm();
    server.api.mockResolvedValue(NOT_READY);
    const lane = await freshLane();

    lane.bootEnvironmentReadiness();
    await flush();
    // The banner is written through t(): fetching before the locale chunk
    // landed would have painted bare keys with nothing to repaint them.
    expect(server.api).not.toHaveBeenCalled();
    expect(banners.every((banner) => banner.hidden)).toBe(true);

    i18n.release();
    await flush();

    expect(server.api).toHaveBeenCalledTimes(1);
    expect(server.api).toHaveBeenCalledWith("/environments/status");
    for (const banner of banners) {
      expect(banner.hidden).toBe(false);
      expect(banner.title.textContent).toBe("en|environment.readiness.bannerTitle");
      // Two missing environments and three missing packages.
      expect(banner.summary.textContent).toBe("en|environment.readiness.bannerMissing|2|3");
      expect(banner.action.textContent).toBe("en|environment.readiness.openCompute");
    }
  });

  it("still fetches when the dictionaries fail to load", async () => {
    const banners = mountBanners();
    i18n.arm();
    server.api.mockResolvedValue(NOT_READY);
    const lane = await freshLane();

    lane.bootEnvironmentReadiness();
    i18n.fail();
    await flush();

    expect(server.api).toHaveBeenCalledTimes(1);
    expect(banners.every((banner) => !banner.hidden)).toBe(true);
  });

  it("repaints the banner when the language changes", async () => {
    const banners = mountBanners();
    server.api.mockResolvedValue(NOT_READY);
    const lane = await freshLane();
    lane.bootEnvironmentReadiness();
    await flush();
    expect(banners[0]!.title.textContent).toBe("en|environment.readiness.bannerTitle");

    i18n.language = "zh";
    i18n.hooks.forEach((hook) => hook("zh"));

    for (const banner of banners) {
      expect(banner.title.textContent).toBe("zh|environment.readiness.bannerTitle");
      expect(banner.action.textContent).toBe("zh|environment.readiness.openCompute");
    }
    // A repaint, not another request.
    expect(server.api).toHaveBeenCalledTimes(1);
  });

  it("keeps the banner hidden when the readiness gate is off", async () => {
    const banners = mountBanners();
    server.api.mockResolvedValue({
      standard_profile_readiness: { schema_version: 1, enabled: false, ready: false, state: "unavailable" },
    });
    const lane = await freshLane();

    lane.bootEnvironmentReadiness();
    await flush();

    expect(server.api).toHaveBeenCalledTimes(1);
    expect(banners.every((banner) => banner.hidden)).toBe(true);
  });

  it("keeps the banner hidden when the first fetch fails", async () => {
    const banners = mountBanners();
    server.api.mockRejectedValue(new Error("offline"));
    const lane = await freshLane();

    lane.bootEnvironmentReadiness();
    await flush();

    // Nothing is known yet, and "unknown" is not shown as "not ready" until a
    // send has actually been refused for it.
    expect(banners.every((banner) => banner.hidden)).toBe(true);
  });

  it("boots once, however often it is called", async () => {
    mountBanners();
    server.api.mockResolvedValue(NOT_READY);
    const lane = await freshLane();

    lane.bootEnvironmentReadiness();
    lane.bootEnvironmentReadiness();
    await flush();

    expect(i18n.hooks).toHaveLength(1);
    expect(server.api).toHaveBeenCalledTimes(1);
  });

  it("is started by main.tsx once the Shell has rendered the banners", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const main = readFileSync(join(here, "../../main.tsx"), "utf8");
    const rendered = main.indexOf("render(<App />, mount);");
    const booted = main.indexOf("bootEnvironmentReadiness();");
    expect(rendered).toBeGreaterThan(-1);
    expect(booted).toBeGreaterThan(rendered);
  });
});
