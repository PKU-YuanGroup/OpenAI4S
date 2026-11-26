import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang, t } from "../../i18n/runtime";

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

describe("NV-05 the five codes that say which model failure it was", () => {
  const NEW_CODES = [
    "llm_authentication_failed",
    "llm_access_denied",
    "llm_model_unavailable",
    "llm_request_target_not_found",
    "llm_endpoint_unreachable",
  ];

  it("each names its cause and the fix in both languages", async () => {
    const english: string[] = [];
    for (const code of NEW_CODES) {
      expect(needsModelSettings(code)).toBe(true);
      await setLang("en");
      const en = failureCodeHint(code);
      await setLang("zh");
      const zh = failureCodeHint(code);
      expect(en).toContain("Settings → Models");
      expect(zh).toContain("设置 → 模型");
      expect(en).not.toBe(zh);
      english.push(en);
    }
    // Five different sentences: a 401 does not read like a 403 or a 404.
    expect(new Set(english).size).toBe(NEW_CODES.length);
    await setLang("en");
    expect(failureCodeHint("llm_authentication_failed")).toContain("401");
    expect(failureCodeHint("llm_access_denied")).toContain("403");
    expect(failureCodeHint("llm_request_target_not_found")).toContain("endpoint and the model id");
    expect(failureCodeHint("llm_endpoint_unreachable")).toContain("no request was sent");
  });

  it("offers the same 打开模型设置 / Open model settings button, and only that", async () => {
    for (const [lang, label] of [["zh", "打开模型设置"], ["en", "Open model settings"]] as const) {
      await setLang(lang);
      for (const code of NEW_CODES) {
        openCustomize.mockClear();
        const box = failureMeta({ code, request_id: "web-nv05" }) as unknown as FakeNode;
        expect(box.dataset.failureCode).toBe(code);
        expect(box.children).toHaveLength(1);
        const [button] = box.children;
        expect(button?.className).toContain("turn-open-models");
        expect(button?.textContent).toBe(label);
        button?.onclick?.();
        expect(openCustomize).toHaveBeenCalledWith("models");
        expect(openCustomize).toHaveBeenCalledTimes(1);
      }
    }
  });

  it("keeps the first version's codes and their own sentences for older rows", async () => {
    await setLang("en");
    expect(failureCodeHint("llm_auth_rejected")).toBe(
      "The model provider rejected the API key. Check it in Settings → Models.",
    );
    expect(failureCodeHint("llm_model_not_found")).toContain("no such model or route");
    expect(failureCodeHint("llm_network")).toContain("could not be reached");
  });

  it("says a committed failure was not retried, next to the button", async () => {
    await setLang("en");
    const box = failureMeta({
      code: "llm_authentication_failed",
      request_id: "web-nv05",
      output_committed: true,
    }) as unknown as FakeNode;
    expect(box.dataset.committed).toBe("1");
    expect(box.textContent).toContain(t("turn.failedCommitted"));
    expect(box.children.map((child) => child.className)).toEqual(["outline-btn small turn-open-models"]);
  });
});
