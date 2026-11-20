import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";

const openCustomize = vi.hoisted(() => vi.fn(() => Promise.resolve()));
vi.mock("../customize", () => ({ openCustomize }));

import { failureCodeHint, failureMeta, needsModelSettings } from "./failure";

/** Just enough of an element for `failureMeta` (no jsdom here). */
type FakeNode = {
  tag: string;
  className: string;
  textContent: string;
  dataset: Record<string, string>;
  children: FakeNode[];
  type?: string;
  onclick?: () => unknown;
  appendChild(child: FakeNode): FakeNode;
};

function fakeNode(tag: string): FakeNode {
  const node: FakeNode = {
    tag,
    className: "",
    textContent: "",
    dataset: {},
    children: [],
    appendChild(child) {
      node.children.push(child);
      return child;
    },
  };
  return node;
}

const SETUP_CODES = [
  "llm_no_credential",
  "llm_auth_rejected",
  "llm_quota_exhausted",
  "llm_model_not_found",
  "llm_network",
];

beforeEach(() => {
  openCustomize.mockClear();
  vi.stubGlobal("document", { createElement: fakeNode, querySelectorAll: () => [] });
});
afterEach(async () => {
  vi.unstubAllGlobals();
  await setLang("zh");
});

describe("a failure fixed in Settings", () => {
  it("says what to do, in both languages, for every setup code", async () => {
    for (const code of SETUP_CODES) {
      expect(needsModelSettings(code)).toBe(true);
      await setLang("en");
      const english = failureCodeHint(code);
      await setLang("zh");
      const chinese = failureCodeHint(code);
      expect(english).toMatch(/Settings → Models|provider/);
      expect(chinese).toContain("设置 → 模型");
      expect(english).not.toBe(chinese);
    }
  });

  it("offers a button that opens Settings → Models, and does not repeat the cause", async () => {
    await setLang("en");
    const box = failureMeta({ code: "llm_no_credential", request_id: "web-1" }) as unknown as FakeNode;

    // The bubble above already carries the sentence; this row adds the id.
    expect(box.textContent).toBe("Support ID: web-1 Details: logs/support.log in the data directory.");
    expect(box.dataset.failureCode).toBe("llm_no_credential");
    const [button] = box.children;
    expect(box.children).toHaveLength(1);
    expect(button?.className).toContain("turn-open-models");
    expect(button?.textContent).toBe("Open model settings");
    expect(button?.type).toBe("button");

    button?.onclick?.();
    expect(openCustomize).toHaveBeenCalledWith("models");
  });

  it("leaves other failures without the button", () => {
    for (const code of ["internal_error", "llm_rate_limited", "", undefined]) {
      expect(needsModelSettings(code)).toBe(false);
      const box = failureMeta({ code, request_id: "web-2" }) as unknown as FakeNode;
      expect(box.children).toHaveLength(0);
      // Any failure with an id says where that id can be looked up.
      expect(box.textContent).toContain("logs/support.log");
    }
    expect(openCustomize).not.toHaveBeenCalled();
  });
});
