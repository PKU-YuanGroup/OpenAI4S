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
import { openShareDialog } from "./share";

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
