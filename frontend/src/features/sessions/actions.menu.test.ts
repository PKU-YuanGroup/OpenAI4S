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

import { t } from "../../i18n";
import { customizeOpen, customizeTab, nestedEditor } from "../customize/state";
import { _titleName, currentId, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { saveCurrentAsSkill, sessionMenu } from "./actions";
import { openMenu, type MenuItem } from "./chrome";
import { openRunLocationDialog } from "./compute";
import { openProjectResearchView, projectTimelineCard } from "./projects";

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

  it("opens an empty skill editor when no session is open", async () => {
    await saveCurrentAsSkill();
    expect(customizeOpen.value).toBe(true);
    expect(nestedEditor.value).toEqual({ kind: "skill", name: null });
    expect(messages.fetchRecentMessages).not.toHaveBeenCalled();
  });
});

describe("the project research Timeline", () => {
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

  it("renders each action group as a card with its details", () => {
    vi.stubGlobal("document", { createElement: () => new FakeNode() });
    try {
      const card = projectTimelineCard({
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

describe("Run location", () => {
  it("opens the run-location dialog for that session", () => {
    sessions.value = [{ id: "f", project_id: "P" }];
    sessionMenu({} as Element, "f");
    const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
    items.find((item) => item.label === t("compute.menu.runLocation"))!.onClick!();
    expect(openRunLocationDialog).toHaveBeenCalledExactlyOnceWith("f");
  });
});
