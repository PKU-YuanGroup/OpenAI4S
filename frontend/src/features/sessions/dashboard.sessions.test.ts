/**
 * The dashboard's session lists (Running, Recent) and the example CTA shown
 * when there are no sessions yet.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type FakeEl = {
  tag: string;
  cls: string | null;
  text: string;
  textContent: string;
  children: FakeEl[];
  removed: boolean;
  disabled: boolean;
  onclick: (() => void) | null;
  classList: { contains: () => boolean; add: () => void; remove: () => void };
  appendChild: (child: FakeEl) => FakeEl;
  setAttribute: (name: string, value: string) => void;
  remove: () => void;
  innerHTML: string;
};

function fakeEl(tag = "div", cls: string | null = null, text = ""): FakeEl {
  const node = {
    tag,
    cls,
    text,
    textContent: text,
    children: [] as FakeEl[],
    removed: false,
    disabled: false,
    onclick: null,
    classList: { contains: () => false, add: () => {}, remove: () => {} },
    appendChild(child: FakeEl) {
      node.children.push(child);
      return child;
    },
    setAttribute() {},
    remove() {
      node.removed = true;
    },
    get innerHTML() {
      return "";
    },
    set innerHTML(_value: string) {
      node.children.length = 0;
    },
  } as FakeEl;
  return node;
}

const dom: Record<string, FakeEl | null> = {};

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: (e: unknown) => String(e) }));
vi.mock("./chrome", () => ({ ensureActivateKeys: () => {} }));
vi.mock("./dom", () => ({
  $: (sel: string) => (sel in dom ? dom[sel] : null),
  el: (tag: string, cls: string | null, text?: string) => fakeEl(tag, cls, text || ""),
  ago: () => "just now",
  navURL: () => {},
  syncMobileChrome: () => {},
}));

import { t } from "../../i18n";
import { api } from "./api";
import { sessionCopy } from "./copy";
import {
  loadDashboard,
  onDashPoll,
  renderDashRecent,
  renderDashRunning,
  resetExampleRun,
  runCardSubtitle,
  startDashPoll,
  stopDashPoll,
} from "./dashboard";
import { attentionCards } from "../attention/state";
import type { AttentionCardModel } from "../attention/types";
import { dashCopy, displayProjectName } from "./copy";
import { LANG, setLang } from "../../i18n/runtime";

/** Every text painted under `node`. */
function texts(node: FakeEl | null | undefined): string[] {
  const out: string[] = [];
  const walk = (n: FakeEl) => {
    if (n.text) out.push(n.text);
    n.children.forEach(walk);
  };
  if (node) walk(node);
  return out;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

/** Intervals started through `window.setInterval` and not yet cleared. */
const intervals = new Set<number>();

beforeEach(() => {
  vi.mocked(api).mockReset();
  for (const key of Object.keys(dom)) delete dom[key];
  dom["#dash-sessions"] = fakeEl();
  intervals.clear();
  let next = 1;
  vi.stubGlobal("window", {
    setInterval: () => {
      const id = next++;
      intervals.add(id);
      return id;
    },
  });
  vi.stubGlobal("clearInterval", (id: number) => {
    intervals.delete(id);
  });
});

afterEach(() => {
  stopDashPoll();
  resetExampleRun();
  attentionCards.value = [];
  vi.unstubAllGlobals();
});

describe("the example CTA's poll", () => {
  it("runs once however many repaints raced its first status read", async () => {
    const replies = [deferred<unknown>(), deferred<unknown>()];
    let call = 0;
    vi.mocked(api).mockImplementation(() => replies[call++]!.promise as never);
    renderDashRecent([]);
    renderDashRecent([]);
    for (const reply of replies) reply.resolve({ running: true });
    await vi.waitFor(() => expect(call).toBe(2));
    await Promise.resolve();
    await Promise.resolve();
    expect(intervals.size).toBe(1);
  });

  it("does not start after the dashboard was left", async () => {
    const reply = deferred<unknown>();
    vi.mocked(api).mockReturnValue(reply.promise as never);
    renderDashRecent([]);
    stopDashPoll(); // what showWorkspace does
    reply.resolve({ running: true });
    await Promise.resolve();
    await Promise.resolve();
    expect(intervals.size).toBe(0);
  });
});

describe("the dashboard poll", () => {
  it("drives the Running card and every rider from one interval", async () => {
    const ticks: Array<() => void> = [];
    vi.stubGlobal("setInterval", (tick: () => void) => {
      ticks.push(tick);
      return ticks.length;
    });
    vi.stubGlobal("document", { hidden: false, addEventListener: vi.fn() });
    dom["#dashboard"] = fakeEl();
    vi.mocked(api).mockResolvedValue({ frames: [] } as never);
    const rider = vi.fn();
    const leave = onDashPoll(rider);
    try {
      startDashPoll();
      expect(rider).toHaveBeenCalledTimes(1); // it reads now, not a tick from now
      expect(ticks).toHaveLength(1);
      ticks[0]!();
      expect(rider).toHaveBeenCalledTimes(2);
      await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/frames?limit=50"));
    } finally {
      leave();
    }
  });
});

describe("a /frames read that fails on the dashboard", () => {
  let framesFail = false;
  beforeEach(() => {
    framesFail = false;
    dom["#dash-projects"] = fakeEl();
    vi.stubGlobal("document", { hidden: false, createDocumentFragment: () => fakeEl("fragment") });
    vi.mocked(api).mockImplementation(async (path: string) => {
      if (path.startsWith("/frames")) {
        if (framesFail) throw new Error("daemon restarting");
        return { frames: [{ id: "f1", name: "Titration run", message_count: 2 }] };
      }
      if (path.startsWith("/projects")) return { projects: [] };
      throw new Error(`unexpected request: ${path}`);
    });
  });

  it("says the list could not be read instead of showing no sessions and the example", async () => {
    framesFail = true;
    await loadDashboard();
    const shown = texts(dom["#dash-sessions"]);
    expect(shown).toEqual([sessionCopy("sessionsError"), sessionCopy("retry")]);
    expect(shown).not.toContain(t("dash.sessions.empty"));
    expect(vi.mocked(api).mock.calls.map(([path]) => path)).not.toContain("/example/session");
  });

  it("keeps the sessions it last read, and Retry reads them again", async () => {
    await loadDashboard();
    expect(texts(dom["#dash-sessions"])).toContain("Titration run");

    framesFail = true;
    await loadDashboard();
    const shown = texts(dom["#dash-sessions"]);
    expect(shown).toContain("Titration run");
    expect(shown).toContain(sessionCopy("sessionsError"));

    framesFail = false;
    const retry = dom["#dash-sessions"]!.children.find((node) => node.text === sessionCopy("retry"));
    retry!.onclick!();
    await vi.waitFor(() => expect(texts(dom["#dash-sessions"])).not.toContain(sessionCopy("sessionsError")));
    expect(texts(dom["#dash-sessions"])).toContain("Titration run");
  });
});

// NAV-4: a session waiting for approval showed under "Needs attention" (twice,
// with its running row), again as a Running card, and again under Recent.
describe("one place per session on the home page", () => {
  const FRAMES = [
    { id: "f-run", name: "Running fit", running: true, message_count: 2, updated_at: "2026-11-18T08:00:00Z" },
    { id: "f-wait", name: "Waiting fit", running: true, message_count: 2, updated_at: "2026-11-18T08:01:00Z" },
    { id: "f-done", name: "Finished fit", running: false, message_count: 2, updated_at: "2026-11-18T07:00:00Z" },
  ];

  beforeEach(() => {
    dom["#dash-running"] = fakeEl();
    dom["#dash-running-count"] = fakeEl();
    attentionCards.value = [{ frameId: "f-wait", sourceKind: "approval" } as AttentionCardModel];
  });

  it("lists a waiting session only under Needs attention and a running one only under Running", () => {
    renderDashRunning(FRAMES);
    renderDashRecent(FRAMES);
    const running = texts(dom["#dash-running"]);
    const recent = texts(dom["#dash-sessions"]);
    expect(running).toContain("Running fit");
    expect(running).not.toContain("Waiting fit");
    expect(recent).toEqual(expect.arrayContaining(["Finished fit"]));
    expect(recent).not.toContain("Running fit");
    expect(recent).not.toContain("Waiting fit");
  });

  it("says the sessions are above instead of 'no sessions yet' when all of them are", () => {
    vi.mocked(api).mockResolvedValue({} as never);
    renderDashRecent(FRAMES.slice(0, 2));
    const recent = texts(dom["#dash-sessions"]);
    expect(recent).toEqual([dashCopy("dash.sessions.allAbove")]);
    expect(api).not.toHaveBeenCalled();
  });

  it("does not repeat the session's name under a Running card's title", () => {
    const project = { project_id: "p1", name: "qPCR project" };
    expect(runCardSubtitle({ name: "ΔΔCt analysis", task_summary: "ΔΔCt analysis of the plate" }, project)).toBe(
      "qPCR project",
    );
    expect(runCardSubtitle({ name: "ΔΔCt analysis", task_summary: "Fit a standard curve" }, project)).toBe(
      "qPCR project · Fit a standard curve",
    );
    expect(runCardSubtitle({ task_summary: "Fit a standard curve" }, null)).toBe("");
  });
});

// I18N-1: the seeded project read "Example project" with an "Example" tag in
// a Chinese UI. Shown in the UI language while it keeps the seeded name.
describe("the seeded example project's name", () => {
  it("is said in the UI language until the user renames it", async () => {
    const original = LANG;
    try {
      await setLang("zh");
      expect(displayProjectName({ project_id: "proj_example", name: "Example project" })).toBe("示例项目");
      expect(displayProjectName({ project_id: "proj_example", name: "我的示例" })).toBe("我的示例");
      expect(displayProjectName({ project_id: "p-2", name: "Example project" })).toBe("Example project");
      expect(dashCopy("dash.example.tag")).toBe("示例");
    } finally {
      await setLang(original);
    }
  });
});

// NAV-4: after "Run the example analysis" the card vanished as soon as the
// session existed, and nothing said when it finished or that cells failed.
describe("the example run's notice", () => {
  // CHAT-1: a fetch that failed inside a cell that carried on left "all 6
  // cells ran without an error" on the card.
  it.each([
    [{ cells: 6, failed: 2 }, () => dashCopy("dash.example.doneFailed", 2, 6)],
    [{ cells: 6, failed: 0, failed_steps: 1 }, () => dashCopy("dash.example.doneStepFailed", 6, 1)],
    [{ cells: 6, failed: 0, failed_steps: 0 }, () => dashCopy("dash.example.done", 6)],
  ])("follows the run and says how it ended: %j", async (result, expected) => {
    const ticks: Array<() => void> = [];
    vi.stubGlobal("window", {
      setInterval: (tick: () => void) => {
        ticks.push(tick);
        intervals.add(ticks.length);
        return ticks.length;
      },
    });
    vi.stubGlobal("document", { hidden: false, createDocumentFragment: () => fakeEl("fragment") });
    let status: Record<string, unknown> = { running: false, seeded: false, error: null };
    vi.mocked(api).mockImplementation(async (path: string, init?: { method?: string }) => {
      if (path === "/example/session" && init?.method === "POST") {
        status = { running: true, seeded: false, started: true, frame_id: null };
        return status;
      }
      if (path === "/example/session") return status;
      if (path.startsWith("/frames")) return { frames: [] };
      if (path.startsWith("/projects")) return { projects: [] };
      throw new Error(`unexpected request: ${path}`);
    });

    renderDashRecent([]);
    await vi.waitFor(() => expect(api).toHaveBeenCalledWith("/example/session"));
    const cta = dom["#dash-sessions"]!.children.find((node) => node.cls === "dash-example")!;
    const button = cta.children.find((node) => node.tag === "button")!;
    expect(button.cls).toBe("solid-btn");
    button.onclick!();
    await vi.waitFor(() =>
      expect(texts(dom["#dash-sessions"])).toContain(dashCopy("dash.example.runningNote")),
    );
    expect(ticks).toHaveLength(1);

    status = {
      running: false,
      seeded: true,
      frame_id: "f-example",
      project_id: "proj_example",
      error: null,
      result,
    };
    ticks[0]!();
    await vi.waitFor(() => expect(texts(dom["#dash-sessions"])).toContain(expected()));
    expect(texts(dom["#dash-sessions"])).toContain(dashCopy("dash.example.open"));
    expect(intervals.size).toBe(0);
  });
});
