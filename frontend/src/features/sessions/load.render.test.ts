/**
 * A sessions read paints the sidebar when it starts (its loading state) and
 * once both lists have settled -- not a third time from the folder read it
 * drives, which rebuilt the whole list only to be rebuilt again at once.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String }));

import { t } from "../../i18n/runtime";
import { _sessionScope, project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { approvalCopy, approvalPendingFor } from "../messages/approval";
import { api } from "./api";
import { dashCopy } from "./copy";
import { loadSessions, sessionRow } from "./load";

class FakeNode {
  tagName = "DIV";
  children: FakeNode[] = [];
  className = "";
  textContent = "";
  tabIndex = -1;
  firstChild = null;
  style: Record<string, string> = {};
  dataset: Record<string, string> = {};
  set innerHTML(_value: string) { this.children = []; }
  appendChild(child: FakeNode) { this.children.push(child); return child; }
  setAttribute() {}
  getAttribute() { return null; }
  addEventListener() {}
  querySelectorAll() { return []; }
}

let rebuilds = 0;

/** `#session-list`: renderSessions starts every rebuild by clearing it. */
class SessionList extends FakeNode {
  override set innerHTML(_value: string) { rebuilds += 1; }
}

beforeEach(() => {
  resetStoreFields();
  rebuilds = 0;
  const list = new SessionList();
  vi.stubGlobal("document", {
    querySelector: (selector: string) => (selector === "#session-list" ? list : null),
    createElement: () => new FakeNode(),
    createDocumentFragment: () => new FakeNode(),
  });
  vi.mocked(api).mockReset().mockImplementation(async (path: string) =>
    path.includes("/folders") ? { folders: [{ folder_id: "d1", name: "Assays" }] } : { frames: [], has_more: false });
  project.value = "P";
  _sessionScope.value = "P";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a session row waiting for approval", () => {
  function texts(node: FakeNode): string[] {
    return [node.textContent, ...node.children.flatMap((child) => texts(child))].filter(Boolean);
  }

  it("says it waits for approval instead of running, from the server or from this tab", () => {
    const waiting = sessionRow({ id: "f-w", name: "Assay", running: true, awaiting_approval: true }) as unknown as FakeNode;
    expect(waiting.className).toContain("awaiting");
    expect(texts(waiting)).toContain(approvalCopy("badge"));
    expect(texts(waiting)).not.toContain(t("dash.badge.running"));

    const plain = sessionRow({ id: "f-r", name: "Fit", running: true }) as unknown as FakeNode;
    expect(texts(plain)).toContain(t("dash.badge.running"));

    // The open session's card is known here before the list is re-read.
    approvalPendingFor.value = "f-r";
    try {
      const local = sessionRow({ id: "f-r", name: "Fit", running: true }) as unknown as FakeNode;
      expect(texts(local)).toContain(approvalCopy("badge"));
    } finally {
      approvalPendingFor.value = null;
    }
  });
});

describe("SCI-17 / SYNC-28: a session row says how its last turn ended", () => {
  function texts(node: FakeNode): string[] {
    return [node.textContent, ...node.children.flatMap((child) => texts(child))].filter(Boolean);
  }
  function badge(node: FakeNode): FakeNode | undefined {
    return node.children.find((child) => child.className.includes("outcome"));
  }

  it("marks a failed, a stopped and a blocked turn in words, apart from the kernel dot", () => {
    const failed = sessionRow({ id: "f-1", name: "Fit", status: "failed", kernel_alive: true }) as unknown as FakeNode;
    expect(badge(failed)?.className).toBe("s-badge outcome failed");
    expect(texts(failed)).toContain(dashCopy("dash.status.failed"));
    // The kernel dot is still there, as its own badge.
    expect(failed.children.some((child) => child.className === "s-badge live")).toBe(true);

    const stopped = sessionRow({ id: "f-2", name: "Fit", status: "cancelled" }) as unknown as FakeNode;
    expect(texts(stopped)).toContain(dashCopy("dash.status.cancelled"));

    const blocked = sessionRow({ id: "f-3", name: "Fit", status: "blocked_by_guardian" }) as unknown as FakeNode;
    expect(texts(blocked)).toContain(dashCopy("dash.status.blocked_by_guardian"));
  });

  it("says nothing extra for a finished, running or new session", () => {
    for (const row of [
      { id: "f-4", name: "Fit", status: "done" },
      { id: "f-5", name: "Fit", status: "ready" },
      { id: "f-6", name: "Fit", status: "failed", running: true },
    ]) {
      expect(badge(sessionRow(row) as unknown as FakeNode)).toBeUndefined();
    }
  });
});

describe("the sidebar during a sessions read", () => {
  it("is rebuilt twice per read, not three times", async () => {
    expect(await loadSessions()).toMatchObject({ status: "loaded" });
    expect(rebuilds).toBe(2);
  });
});
