/**
 * Menu entries reach real implementations. "Save as skill" and "Run location"
 * called window names nothing defines, so clicking them did nothing at all.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./chrome", () => ({ hint: vi.fn(), openMenu: vi.fn(), ensureActivateKeys: vi.fn() }));
vi.mock("./conversation", () => ({ openConversation: vi.fn(), resumeWatch: vi.fn() }));
vi.mock("./compute", () => ({ openRunLocationDialog: vi.fn() }));
const messages = vi.hoisted(() => ({ fetchRecentMessages: vi.fn(), fetchAllMessages: vi.fn() }));
vi.mock("./messages", () => messages);
// The read-only Auto Mode block builds its own DOM; here it is one known node.
const autoModeNode = vi.hoisted(() => ({ autoMode: true }));
vi.mock("../automode", () => ({
  autoModeMenuItem: () => ({ node: autoModeNode }),
  beginAutoModeStatusRead: vi.fn(),
}));

import { LANG, setLang, t } from "../../i18n";
import { customizeOpen, customizeTab, nestedEditor } from "../customize/state";
import { _titleName, currentId, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { nbT } from "../notebook/copy";
import { addToMessageMenu, saveCurrentAsSkill, sessionMenu, sessionOptionsMenu, showContextUsage } from "./actions";
import { openMenu, type MenuItem } from "./chrome";
import { openRunLocationDialog } from "./compute";
import { optionsMenuCopy, shareCopy } from "./copy";
import { primeShareStatus, resetShareStatus } from "./share";
import { openProjectResearchView, projectFiles, sanitizeProjectLineage } from "./projects";
import { actionTimelineCard } from "../timeline/island";

class FakeNode {
  className = "";
  textContent = "";
  firstChild = null;
  type = "";
  onclick: (() => void) | null = null;
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  attrs: Record<string, string> = {};
  children: FakeNode[] = [];
  classList = { add: () => {}, remove: () => {}, toggle: () => {} };
  set innerHTML(_value: string) { this.children = []; }
  setAttribute(name: string, value: string) { this.attrs[name] = value; }
  appendChild(child: FakeNode) { this.children.push(child); return child; }
  texts(): string[] {
    return [this.textContent, ...this.children.flatMap((child) => child.texts())].filter(Boolean);
  }
}

beforeEach(() => {
  resetStoreFields();
  customizeOpen.value = false;
  nestedEditor.value = null;
  vi.mocked(openMenu).mockReset();
  vi.mocked(openRunLocationDialog).mockReset();
  messages.fetchRecentMessages.mockReset();
});

describe("Save as skill", () => {
  it("opens the Customize skill editor seeded from the conversation", async () => {
    currentId.value = "f";
    _titleName.value = "Buffer Titration";
    messages.fetchRecentMessages.mockResolvedValue({
      messages: [
        { role: "user", content: "Titrate the phosphate buffer" },
        { role: "assistant", content: "The endpoint is at pH 7.2." },
      ],
    });
    await saveCurrentAsSkill();
    expect(customizeOpen.value).toBe(true);
    expect(customizeTab.value).toBe("skills");
    expect(nestedEditor.value).toMatchObject({
      kind: "skill",
      name: null,
      seed: { name: "buffer-titration", description: "Titrate the phosphate buffer" },
    });
    expect((nestedEditor.value as unknown as { seed: { body: string } }).seed.body).toContain("The endpoint is at pH 7.2.");
  });

  it("builds the Procedure from the cells the session ran (M-84)", async () => {
    currentId.value = "f";
    _titleName.value = "Buffer Titration";
    messages.fetchRecentMessages.mockResolvedValue({
      messages: [
        { role: "user", content: "Titrate the phosphate buffer" },
        { role: "assistant", content: "The endpoint is at pH 7.2." },
      ],
    });
    const entries = [
      { producing_cell_id: "c1", language: "python", status: "ok", is_latest_attempt: true, source: "# Load the titration data\nimport pandas as pd\ndf = pd.read_csv('titration.csv')" },
      { producing_cell_id: "c2", language: "python", status: "error", is_latest_attempt: false, source: "broken(" },
      { producing_cell_id: "c3", language: "python", status: "ok", is_latest_attempt: true, files_written: ["endpoint.png"], source: "def endpoint(df):\n    return df.ph.diff().idxmax()\n\nplot(endpoint(df))" },
    ];
    const urls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(String(url));
      return new Response(JSON.stringify(String(url).endsWith("/frames/f/execution-log") ? { entries } : {}));
    }));
    try {
      await saveCurrentAsSkill();
    } finally {
      vi.unstubAllGlobals();
    }
    expect(urls.some((url) => url.endsWith("/frames/f/execution-log"))).toBe(true);
    const seed = (nestedEditor.value as unknown as { seed: { body: string; draft: { steps: Array<{ summary: string }> } } }).seed;
    // The failed attempt is not a step; each kept step has a line and its code.
    expect(seed.draft.steps.map((step) => step.summary)).toEqual(["Load the titration data", "Writes endpoint.png"]);
    expect(seed.body).toContain("1. Load the titration data\n\n```python\n# Load the titration data\nimport pandas as pd");
    expect(seed.body).toContain("2. Writes endpoint.png");
    expect(seed.body).not.toContain("broken(");
    expect(seed.body).toContain("# kernel.py (draft)");
    expect(seed.body).toContain("def endpoint(df):\n    return df.ph.diff().idxmax()\n```");
    expect(seed.body).not.toContain("Reproduce the evidence-gathering");
  });

  it("opens an empty skill editor when no session is open", async () => {
    await saveCurrentAsSkill();
    expect(customizeOpen.value).toBe(true);
    expect(nestedEditor.value).toEqual({ kind: "skill", name: null });
    expect(messages.fetchRecentMessages).not.toHaveBeenCalled();
  });
});

// EXP-3: sharing needed a relay somebody hosts; the menu also offers the
// same read-only view as one file.
describe("Export read-only web page", () => {
  it("is one menu item that downloads the session as a single .html in the UI language", async () => {
    const previous = LANG;
    const links: Array<{ href: string; download: string; click: () => void; remove: () => void }> = [];
    try {
      await setLang("zh");
      vi.stubGlobal("document", {
        createElement: () => {
          const link = { href: "", download: "", click: vi.fn(), remove: vi.fn() };
          links.push(link);
          return link;
        },
        body: { appendChild: vi.fn() },
      });
      sessions.value = [{ id: "f 1", name: "生长曲线 QC" }];
      sessionMenu(new FakeNode() as unknown as Element, "f 1");
      const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
      const mine = items.filter((item) => item.label === "导出只读网页");
      expect(mine).toHaveLength(1);
      mine[0]!.onClick!();
      expect(links).toHaveLength(1);
      expect(links[0]!.href).toMatch(/\/frames\/f%201\/session\/export-html\?lang=zh$/);
      expect(links[0]!.download).toBe("生长曲线_QC.html");
      expect(links[0]!.click).toHaveBeenCalled();
      vi.unstubAllGlobals();
      await setLang("en");
      vi.mocked(openMenu).mockReset();
      sessionMenu(new FakeNode() as unknown as Element, "f 1");
      const english = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
      expect(english.filter((item) => item.label === "Export read-only web page")).toHaveLength(1);
    } finally {
      vi.unstubAllGlobals();
      await setLang(previous);
    }
  });
});

describe("Context usage", () => {
  it("labels the card in the language on screen", async () => {
    const previous = LANG;
    await setLang("zh");
    const body = new FakeNode();
    const nodes: Record<string, FakeNode> = { "#modal-body": body, "#modal-title": new FakeNode(), "#modal-download": new FakeNode(), "#modal": new FakeNode() };
    vi.stubGlobal("document", { createElement: () => new FakeNode(), querySelector: (sel: string) => nodes[sel] ?? null });
    vi.stubGlobal("fetch", async (input: unknown) => new Response(JSON.stringify(String(input).endsWith("/steps")
      ? { steps: [{ kind: "review", output: { usage: { input_tokens: 40, output_tokens: 2 } } }] }
      : { input_tokens: 120, output_tokens: 30 })));
    try {
      currentId.value = "f";
      await showContextUsage();
      expect(body.texts()).toEqual([t("context.tokens", "150"), "输入 120 · 输出 30 · 审阅 42"]);
    } finally {
      vi.unstubAllGlobals();
      await setLang(previous);
    }
  });
});

describe("the project research Timeline", () => {
  it("lists each session's action groups as cards under the session's name", async () => {
    const body = new FakeNode();
    const nodes: Record<string, FakeNode> = { "#modal-body": body, "#modal-title": new FakeNode(), "#modal-download": new FakeNode(), "#modal": new FakeNode() };
    vi.stubGlobal("document", { createElement: () => new FakeNode(), querySelector: (sel: string) => nodes[sel] ?? null });
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({
      session_count: 1, total_count: 1,
      groups: [{ kind: "code", status: "completed", title: "Fit the dose-response curve", session: { name: "Assay 3" }, events: [], attempts: [] }],
    })));
    try {
      project.value = "P";
      await openProjectResearchView("timeline");
      await vi.waitFor(() => expect(body.texts()).toContain("Fit the dose-response curve"));
      expect(body.texts()).toContain("Assay 3");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("M-48: is called the project's activity, with tabs named in the language on screen", async () => {
    const previous = LANG;
    await setLang("zh");
    const body = new FakeNode();
    const title = new FakeNode();
    const nodes: Record<string, FakeNode> = { "#modal-body": body, "#modal-title": title, "#modal-download": new FakeNode(), "#modal": new FakeNode() };
    vi.stubGlobal("document", { createElement: () => new FakeNode(), querySelector: (sel: string) => nodes[sel] ?? null });
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ session_count: 0, total_count: 0, groups: [] })));
    try {
      project.value = "P";
      await openProjectResearchView("timeline");
      expect(String(title.textContent)).toContain("项目活动记录");
      await vi.waitFor(() => expect(body.texts()).toContain("0 个会话 · 0 个操作"));
      expect(body.texts()).toEqual(expect.arrayContaining(["活动", "文件"]));
      expect(body.texts()).not.toContain("Timeline");
      expect(body.texts()).not.toContain("Lineage");
    } finally {
      vi.unstubAllGlobals();
      await setLang(previous);
    }
  });

  it("M-48: lists one row per file, with its version count, not one row per version", () => {
    const data = sanitizeProjectLineage({
      nodes: [
        { id: "v1", kind: "artifact_version", artifact_id: "a", version_id: "v1", filename: "fit.csv", latest: false },
        { id: "v2", kind: "artifact_version", artifact_id: "a", version_id: "v2", filename: "fit.csv", latest: true, cell_id: "cell-9" },
        { id: "v3", kind: "artifact_version", artifact_id: "b", version_id: "v3", filename: "plot.png", latest: true },
      ],
      edges: [],
    });
    expect(projectFiles(data.nodes)).toEqual([
      { id: "a", name: "fit.csv", versions: 2, cell: "cell-9" },
      { id: "b", name: "plot.png", versions: 1, cell: "" },
    ]);
  });

  it("renders each action group as a card with its details", () => {
    vi.stubGlobal("document", { createElement: () => new FakeNode() });
    try {
      const card = actionTimelineCard({
        kind: "code", status: "failed", title: "Fit the dose-response curve", owner: "agent",
        events: [{ resource_keys: ["gpu:0"], artifacts: ["fit.png"], side_effect_class: "compute" }],
        attempts: [{ generation_id: "gen-7", started_at: 1000, finished_at: 3500, error: "did not converge" }],
        usage: { input_tokens: 10, output_tokens: 5 }, cost: 0.5,
      } as never) as unknown as FakeNode;
      expect(card.className).toBe("timeline-card kind-python status-failed");
      expect(card.texts()).toEqual(expect.arrayContaining([
        t("timeline.kind.python"), "failed", "Fit the dose-response curve", "agent", "compute",
        "gpu:0", "fit.png", "gen-7", "2.5 s", "$0.5000", "did not converge",
      ]));
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("M-9: the + menu only adds to the message", () => {
  it("offers attaching and picking files, nothing that acts on the session", () => {
    vi.mocked(openMenu).mockClear();
    addToMessageMenu({} as Element);
    const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
    expect(items.map((item) => item.label)).toEqual([t("composer.menu.attachFiles"), t("composer.menu.yourFiles")]);
  });

  it("puts review, save-as-skill and context usage in the open session's menu only", () => {
    sessions.value = [{ id: "f", project_id: "P" }, { id: "g", project_id: "P" }];
    currentId.value = "f";
    const labels = (fid: string) => {
      vi.mocked(openMenu).mockClear();
      sessionMenu({} as Element, fid);
      return (vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[]).map((item) => item.label);
    };
    const moved = [t("composer.menu.requestReview"), t("composer.menu.saveAsSkill"), t("composer.menu.contextUsage")];
    expect(labels("f")).toEqual(expect.arrayContaining(moved));
    expect(labels("g").filter((label) => moved.includes(String(label)))).toEqual([]);
  });
});

describe("Run location", () => {
  it("opens the run-location dialog for that session", () => {
    sessions.value = [{ id: "f", project_id: "P" }];
    sessionMenu({} as Element, "f");
    const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
    items.find((item) => item.label === t("compute.menu.runLocation"))!.onClick!();
    expect(openRunLocationDialog).toHaveBeenCalledExactlyOnceWith("f");
  });
});

// UI-1: the composer's options menu ran modes, review switches, Settings
// shortcuts and a status block together, with "Compute" next to "Runtime
// environment (Python / R)" and nothing to tell them apart.
describe("Session options menu", () => {
  it("groups its entries under headings and tells Compute and Runtime environment apart", async () => {
    vi.stubGlobal("document", { createElement: () => new FakeNode(), querySelector: () => null });
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify({ auto_review: false, delegation_enabled: true })));
    try {
      currentId.value = "f";
      await sessionOptionsMenu({} as Element);
      const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
      const headings = items
        .map((item) => item.node as unknown as FakeNode | undefined)
        .filter((node) => node && node.className === "ctx-heading")
        .map((node) => node!.textContent);
      expect(headings).toEqual(
        (["groupModes", "groupReview", "groupSession", "groupSettings"] as const).map(optionsMenuCopy),
      );
      const compute = items.find((item) => item.label === t("composer.option.compute"))!;
      const runtime = items.find((item) => item.label === nbT("nb.env.menuItem"))!;
      expect(compute.desc).toBe(optionsMenuCopy("computeDesc"));
      expect(runtime.desc).toBe(optionsMenuCopy("runtimeDesc"));
      // The status block closes the menu, after a separator; it titles itself.
      expect(items.at(-1)!.node).toBe(autoModeNode);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

// EXP-3: in a default install Share opens a setup guide; the item says so.
describe("Share (read-only link)", () => {
  async function shareItem(status: Record<string, unknown>): Promise<MenuItem> {
    resetShareStatus();
    vi.stubGlobal("fetch", async () => new Response(JSON.stringify(status)));
    await primeShareStatus();
    vi.unstubAllGlobals();
    vi.mocked(openMenu).mockReset();
    sessions.value = [{ id: "f", project_id: "P" }];
    sessionMenu({} as Element, "f");
    const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
    return items.find((item) => item.label === t("share.menu"))!;
  }

  it("carries a needs-setup line while no relay is configured", async () => {
    expect((await shareItem({ state: "disabled", configured: false })).desc).toBe(
      shareCopy("menuNeedsSetup"),
    );
    expect((await shareItem({ state: "unconfigured" })).desc).toBe(shareCopy("menuNeedsSetup"));
  });

  it("carries none once a relay is configured", async () => {
    expect((await shareItem({ state: "ready", configured: true })).desc).toBeUndefined();
    expect((await shareItem({ state: "disabled", configured: true })).desc).toBeUndefined();
  });
});
