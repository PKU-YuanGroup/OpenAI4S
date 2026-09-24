import { afterEach, describe, expect, it, vi } from "vitest";

const i18n = vi.hoisted(() => ({ lang: "zh" as "zh" | "en", dict: {} as Record<string, string> }));
vi.mock("../../i18n/runtime", () => ({
  get LANG() {
    return i18n.lang;
  },
  tOptional: (key: string) => i18n.dict[key] ?? null,
}));

import { judgmentT } from "../judgment/copy";
import { copyLookup, ot } from "./copy";

afterEach(() => {
  i18n.lang = "zh";
  i18n.dict = {};
});

describe("feature-local copy lookup", () => {
  const t = copyLookup({
    zh: { "x.both": "两种 {0} / {1}", "x.zhOnly": "只有中文" },
    en: { "x.both": "both {0} / {1}", "x.enOnly": "English only" },
  });

  it("reads the active language, then English, then the key", () => {
    expect(t("x.both", "a", "b")).toBe("两种 a / b");
    expect(t("x.enOnly")).toBe("English only");
    expect(t("x.missing")).toBe("x.missing");
    i18n.lang = "en";
    expect(t("x.both", "a", "b")).toBe("both a / b");
    expect(t("x.zhOnly")).toBe("x.zhOnly");
  });

  it("lets the loaded dictionary win and leaves a hole with no argument as written", () => {
    i18n.dict = { "x.both": "dict {0} {1}" };
    expect(t("x.both", 0)).toBe("dict 0 {1}");
  });

  it("is what the onboarding and judgment copy use", () => {
    expect(ot("onboarding.readiness.egress", "off")).toBe("egress：off");
    expect(judgmentT("judgment.testLatency", 12)).toBe("延迟：12 ms");
    i18n.lang = "en";
    expect(ot("onboarding.readiness.egress", "off")).toBe("egress: off");
    expect(judgmentT("judgment.testLatency", 12)).toBe("latency: 12 ms");
    i18n.dict = { "judgment.testLatency": "{0}ms" };
    expect(judgmentT("judgment.testLatency", 12)).toBe("12ms");
  });
});
