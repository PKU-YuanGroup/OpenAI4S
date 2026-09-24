/**
 * The first data-driven view waits for the dictionaries.
 *
 * The Shell mounts synchronously and `bindWorkbench()` runs from its first
 * effect, while the locale chunks are still an `import()` in flight. Routing
 * straight away rendered the dashboard lists, the session sidebar and an
 * opened session through `t()` with nothing to translate with: "1 session"
 * became "dash.meta.session", an empty dashboard showed "dash.sessions.empty"
 * and a "dash.example.cta" button, and nothing re-rendered them once the
 * chunks arrived. It also let the late static repaint land on a session that
 * was already open. The handlers are still bound at once -- only the first
 * render waits.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const gate = vi.hoisted(() => {
  const state = {
    release: (): void => undefined,
    fail: (_error: unknown): void => undefined,
    ready: vi.fn((): Promise<void> => Promise.resolve()),
    arm(): void {
      const pending = new Promise<void>((resolve, reject) => {
        state.release = () => resolve();
        state.fail = (error) => reject(error);
      });
      state.ready.mockImplementation(() => pending);
    },
  };
  return state;
});
const route = vi.hoisted(() => ({
  routeInitialView: vi.fn(async () => {}),
  newSession: vi.fn(async () => {}),
}));
const dashboard = vi.hoisted(() => ({ showDashboard: vi.fn(), loadDashboard: vi.fn(), repaintDashboard: vi.fn() }));
const lists = vi.hoisted(() => ({ renderSessions: vi.fn(), renderEmptySession: vi.fn() }));

vi.mock("../../i18n", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../i18n")>()),
  i18nReady: gate.ready,
}));
vi.mock("./conversation", () => route);
vi.mock("./dashboard", () => dashboard);
vi.mock("./load", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./load")>()),
  renderSessions: lists.renderSessions,
}));
vi.mock("../messages/list", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../messages/list")>()),
  renderEmptySession: lists.renderEmptySession,
}));
vi.mock("./chrome", () => ({
  hint: vi.fn(),
  watchActivateKeys: vi.fn(),
  watchDisconnect: vi.fn(),
}));
vi.mock("./icon", () => ({ paintIcons: vi.fn(), icon: () => "", iconEl: vi.fn() }));
vi.mock("../theme/theme", () => ({ cycleTheme: vi.fn(), refreshThemeToggle: vi.fn() }));

const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

let clicks: Record<string, Record<string, unknown>>;

beforeEach(() => {
  vi.resetModules();
  gate.arm();
  route.routeInitialView.mockClear();
  dashboard.showDashboard.mockClear();
  dashboard.loadDashboard.mockClear();
  dashboard.repaintDashboard.mockClear();
  lists.renderSessions.mockClear();
  lists.renderEmptySession.mockClear();
  clicks = { "#new-session": {}, "#dash-new-project": {} };
  vi.stubGlobal("document", {
    documentElement: { lang: "" },
    body: { classList: { toggle: vi.fn(), contains: () => false } },
    activeElement: null,
    querySelector: (selector: string) => clicks[selector] ?? null,
    querySelectorAll: () => [],
    getElementById: () => null,
  });
  vi.stubGlobal("window", { addEventListener: vi.fn() });
  vi.stubGlobal("localStorage", { getItem: () => "en", setItem: () => undefined });
});

afterEach(() => {
  gate.release();
  vi.unstubAllGlobals();
});

describe("bindWorkbench and the locale chunks", () => {
  it("binds the Shell at once but routes only once the dictionaries are ready", async () => {
    const { bindWorkbench } = await import("./boot");
    const ready = bindWorkbench();
    await flush();

    expect(typeof clicks["#new-session"]?.onclick).toBe("function");
    expect(route.routeInitialView).not.toHaveBeenCalled();
    expect(dashboard.showDashboard).not.toHaveBeenCalled();

    gate.release();
    await ready;
    expect(route.routeInitialView).toHaveBeenCalledTimes(1);
  });

  it("still routes when a locale chunk fails to load", async () => {
    const { bindWorkbench } = await import("./boot");
    const ready = bindWorkbench();
    await flush();
    expect(route.routeInitialView).not.toHaveBeenCalled();

    gate.fail(new Error("Failed to fetch dynamically imported module"));
    await expect(ready).resolves.toBeUndefined();
    expect(route.routeInitialView).toHaveBeenCalledTimes(1);
  });

  it("repaints the data-driven lists when the dictionaries arrive after the wait", async () => {
    // Routing gave up on the chunk, so the lists rendered through t() with
    // nothing to translate with ("dash.meta.sessions", "date.bucket.today",
    // "empty.title"...), and the boot repaint only reaches static labels.
    const emptySession = { remove: vi.fn() };
    const messages = {
      querySelector: (selector: string) =>
        selector === ":scope > .empty-session" ? emptySession : null,
      addEventListener: vi.fn(),
    };
    clicks["#dashboard"] = { classList: { contains: () => false } };
    clicks["#messages"] = messages;
    vi.useFakeTimers();
    try {
      const { bindWorkbench, I18N_ROUTE_WAIT_MS } = await import("./boot");
      const ready = bindWorkbench();
      await vi.advanceTimersByTimeAsync(I18N_ROUTE_WAIT_MS);
      await ready;
      expect(route.routeInitialView).toHaveBeenCalledTimes(1);
      expect(lists.renderSessions).not.toHaveBeenCalled();

      gate.release();
      await vi.advanceTimersByTimeAsync(0);

      expect(dashboard.loadDashboard).toHaveBeenCalledTimes(1);
      expect(lists.renderSessions).toHaveBeenCalledTimes(1);
      expect(emptySession.remove).toHaveBeenCalledTimes(1);
      expect(lists.renderEmptySession).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not repaint the lists when the dictionaries arrived in time", async () => {
    clicks["#dashboard"] = { classList: { contains: () => false } };
    const { bindWorkbench } = await import("./boot");
    const ready = bindWorkbench();
    gate.release();
    await ready;
    await flush();
    expect(route.routeInitialView).toHaveBeenCalledTimes(1);
    expect(dashboard.loadDashboard).not.toHaveBeenCalled();
    expect(lists.renderSessions).not.toHaveBeenCalled();
    expect(lists.renderEmptySession).not.toHaveBeenCalled();
  });

  it("a language switch repaints the lists from what they hold, and only a switch does", async () => {
    clicks["#dashboard"] = { classList: { contains: () => false } };
    const { bindWorkbench } = await import("./boot");
    const ready = bindWorkbench();
    gate.release();
    await ready;
    await flush();
    lists.renderSessions.mockClear();
    const i18n = await import("../../i18n");

    await i18n.setLang("en"); // the language on screen (localStorage says en): nothing to repaint
    expect(dashboard.repaintDashboard).not.toHaveBeenCalled();
    expect(lists.renderSessions).not.toHaveBeenCalled();

    // (The i18n mock outlives resetModules, so every earlier test's workbench
    // hook is registered too: the counts are "at least once", not "once".)
    await i18n.setLang("zh");
    expect(dashboard.repaintDashboard).toHaveBeenCalled();
    expect(lists.renderSessions).toHaveBeenCalled();
    expect(dashboard.loadDashboard).not.toHaveBeenCalled(); // no new reads
  });

  it("does not wait forever on a stalled locale chunk", async () => {
    vi.useFakeTimers();
    try {
      const { bindWorkbench, I18N_ROUTE_WAIT_MS } = await import("./boot");
      const ready = bindWorkbench();
      await vi.advanceTimersByTimeAsync(I18N_ROUTE_WAIT_MS - 1);
      expect(route.routeInitialView).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);
      await ready;
      expect(route.routeInitialView).toHaveBeenCalledTimes(1);
    } finally {
      vi.useRealTimers();
    }
  });
});
