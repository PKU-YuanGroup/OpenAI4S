/**
 * The share dialog's Copy reports what happened: "copied" only for a write the
 * clipboard confirmed, and otherwise the failure, with the link left selected
 * for a manual copy.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./chrome", () => ({ hint: vi.fn() }));

import { i18nReady, t } from "../../i18n";
import { copyFailedText } from "../chrome/clipboard";
import { hint } from "./chrome";
import { countsText, openShareDialog, shareExpiry, snapshotText, tunnelDownText } from "./share";

class FakeNode {
  tagName: string;
  children: FakeNode[] = [];
  parent: FakeNode | null = null;
  className = "";
  id = "";
  type = "";
  title = "";
  htmlFor = "";
  value = "";
  readOnly = false;
  hidden = false;
  tabIndex = 0;
  textContent = "";
  innerHTML = "";
  dataset: Record<string, string> = {};
  attrs: Record<string, string> = {};
  onclick: ((event?: unknown) => unknown) | null = null;
  focused = 0;
  selected = 0;
  classList = { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false };
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  appendChild(child: FakeNode): FakeNode {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
    this.parent = null;
  }
  replaceWith(other: FakeNode): void {
    const parent = this.parent;
    if (!parent) return;
    parent.children = parent.children.map((node) => (node === this ? other : node));
    other.parent = parent;
    this.parent = null;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  addEventListener(): void {}
  focus(): void {
    this.focused += 1;
  }
  select(): void {
    this.selected += 1;
  }
  querySelector(): FakeNode | null {
    return this.find((node) => node.tagName === "BUTTON");
  }
  find(match: (node: FakeNode) => boolean): FakeNode | null {
    for (const child of this.children) {
      if (match(child)) return child;
      const deeper = child.find(match);
      if (deeper) return deeper;
    }
    return null;
  }
}

let body: FakeNode;

beforeAll(async () => {
  await i18nReady();
});

beforeEach(() => {
  vi.mocked(hint).mockClear();
  body = new FakeNode("body");
  vi.stubGlobal("HTMLElement", FakeNode);
  vi.stubGlobal("document", { body, createElement: (tag: string) => new FakeNode(tag) });
  vi.stubGlobal("fetch", async (input: unknown) => {
    const path = String(input);
    const reply = path.endsWith("/share/status")
      ? { state: "ready", configured: true }
      : { shares: [{ status: "ready", url: "https://share.example/s/abc", share_id: "s1" }] };
    return new Response(JSON.stringify(reply));
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function clickCopy(clipboard: { writeText: (text: string) => Promise<void> }): Promise<FakeNode> {
  vi.stubGlobal("navigator", { clipboard });
  await openShareDialog("f");
  const copy = body.find((node) => node.tagName === "BUTTON" && node.textContent === t("share.copy"));
  expect(copy).not.toBeNull();
  copy!.onclick!();
  await vi.waitFor(() => expect(hint).toHaveBeenCalled());
  return body.find((node) => node.id === "share-url")!;
}

describe("share dialog Copy", () => {
  it("says the link was copied only once the clipboard confirmed the write", async () => {
    const written: string[] = [];
    const clipboard = { writeText: async (text: string) => { written.push(text); } };
    await clickCopy(clipboard);
    expect(written).toEqual(["https://share.example/s/abc"]);
    expect(hint).toHaveBeenCalledWith(t("share.copied"));
  });

  it("reports a refused write and leaves the link selected for a manual copy", async () => {
    const clipboard = { writeText: () => Promise.reject(new Error("denied")) };
    const link = await clickCopy(clipboard);
    expect(hint).toHaveBeenCalledWith(copyFailedText(), true);
    expect(hint).not.toHaveBeenCalledWith(t("share.copied"));
    expect(link.selected).toBe(1);
  });
});

function allText(node: FakeNode): string[] {
  return [node.textContent, ...node.children.flatMap((child) => allText(child))].filter(Boolean);
}

describe("share dialog without a relay (EXP-2)", () => {
  function statusReply(status: Record<string, unknown>): void {
    vi.stubGlobal("fetch", async (input: unknown) => {
      const reply = String(input).endsWith("/share/status") ? status : { shares: [] };
      return new Response(JSON.stringify(reply));
    });
  }

  it.each([
    ["the default install (disabled, no relay)", { state: "disabled", configured: false }],
    ["sharing enabled but no relay", { state: "unconfigured", missing: ["relay_url", "auth_token"] }],
  ])("explains how to set sharing up for %s instead of a bare Close", async (_label, status) => {
    statusReply(status);
    await openShareDialog("f");
    const text = allText(body).join("\n");
    expect(text).not.toContain(t("share.disabled"));
    expect(text).toContain("OPENAI4S_SHARE_RELAY_URL");
    expect(text).toContain("openai4s share enable");
    expect(text).toContain("docs/webshare.md");
    const steps = body.find((node) => node.tagName === "OL");
    expect(steps?.children).toHaveLength(3);
  });

  it("keeps the Enable button when a relay is configured but sharing is off", async () => {
    statusReply({ state: "disabled", configured: true });
    await openShareDialog("f");
    const enable = body.find((node) => node.tagName === "BUTTON" && node.textContent === t("share.enable"));
    expect(enable).not.toBeNull();
    expect(body.find((node) => node.tagName === "OL")).toBeNull();
  });
});

describe("share dialog state (TEAM-15, TEAM-16, TEAM-17)", () => {
  const share = {
    status: "ready",
    url: "https://abc.share.example/",
    share_id: "s1",
    expires_at: 1795752280157,
    updated_at: 1795147480157,
    counts: { messages: 13, cells: 1, artifacts: 2, excluded: { secret_file: 1 } },
  };
  const calls: Array<{ method: string; path: string }> = [];
  function serve(status: Record<string, unknown>, shares: unknown[] = [share]): void {
    calls.length = 0;
    vi.stubGlobal("fetch", async (input: unknown, init?: { method?: string }) => {
      const path = String(input);
      calls.push({ method: init?.method || "GET", path });
      const reply = path.endsWith("/share/status")
        ? status
        : path.endsWith("/replay")
          ? { counts: { messages: 4, cells: 3, artifacts: 2 }, excluded: { secret_file: 1 } }
          : path.includes("/shares/")
            ? {}
            : { shares };
      return new Response(JSON.stringify(reply));
    });
  }
  const tunnel = () => body.find((node) => node.className === "share-tunnel");

  it("reads a millisecond expires_at as a date, not as a date string", () => {
    expect(shareExpiry(1795752280157)?.getTime()).toBe(1795752280157);
    expect(shareExpiry("1795752280157")?.getTime()).toBe(1795752280157);
    expect(shareExpiry(null)).toBeNull();
  });

  it("says the link does not open yet while the relay is not connected", async () => {
    serve({ state: "connecting", configured: true, connected: false, last_error: "ConnectionRefusedError: [Errno 61] Connection refused" });
    await openShareDialog("f");
    const note = tunnel();
    expect(note?.hidden).toBe(false);
    expect(note?.textContent).toBe(tunnelDownText("ConnectionRefusedError: [Errno 61] Connection refused"));
    expect(note?.textContent).toContain("Errno 61");
    expect(body.find((node) => node.className === "share-status offline")).not.toBeNull();
    const text = allText(body).join("\n");
    expect(text).not.toContain("Invalid Date");
  });

  it("shows no warning once the relay is connected", async () => {
    serve({ state: "connected", configured: true, connected: true });
    await openShareDialog("f");
    expect(tunnel()?.hidden).toBe(true);
    expect(body.find((node) => node.className === "share-status")).not.toBeNull();
  });

  it("warns before a link is created on a relay that is down", async () => {
    serve({ state: "connecting", configured: true, connected: false }, []);
    await openShareDialog("f");
    expect(tunnel()?.hidden).toBe(false);
  });

  it("says what a link would publish before it is created", async () => {
    serve({ state: "connected", connected: true }, []);
    await openShareDialog("f");
    const expected = countsText({ messages: 4, cells: 3, artifacts: 2, excluded: { secret_file: 1 } });
    await vi.waitFor(() => expect(allText(body).join("\n")).toContain(expected));
    expect(calls.some((call) => call.path.endsWith("/sessions/f/replay"))).toBe(true);
    expect(calls.some((call) => call.method === "POST")).toBe(false);
  });

  it("shows what the snapshot publishes", async () => {
    serve({ state: "connected", connected: true });
    await openShareDialog("f");
    expect(allText(body).join("\n")).toContain(snapshotText(share));
    expect(snapshotText(share)).toMatch(/13 .*1 .*2 .*1 /);
  });

  it("asks inside the dialog before revoking, and revokes only on that answer", async () => {
    const nativeConfirm = vi.fn(() => true);
    vi.stubGlobal("confirm", nativeConfirm);
    serve({ state: "connected", connected: true });
    await openShareDialog("f");
    const revoke = body.find((node) => node.tagName === "BUTTON" && node.textContent === t("share.revoke"));
    revoke!.onclick!();
    expect(nativeConfirm).not.toHaveBeenCalled();
    expect(calls.some((call) => call.method === "DELETE")).toBe(false);
    const ask = body.find((node) => node.className === "share-row share-confirm");
    expect(ask).not.toBeNull();
    const yes = ask!.find((node) => node.tagName === "BUTTON" && node.textContent === t("share.revoke"));
    await yes!.onclick!();
    expect(calls.filter((call) => call.method === "DELETE").map((call) => call.path)).toEqual([
      expect.stringContaining("/shares/s1"),
    ]);
  });
});
