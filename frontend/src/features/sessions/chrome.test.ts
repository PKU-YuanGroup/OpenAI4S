import { afterEach, describe, expect, it, vi } from "vitest";
import { LANG } from "../../i18n";
import { errorPrefix, reportFailure } from "./chrome";
import { actionFailedCopy } from "./copy";

describe("hint error prefix", () => {
  it("uses the zh/en literals without a new i18n key", () => {
    expect(errorPrefix("zh")).toBe("错误：");
    expect(errorPrefix("en")).toBe("Error: ");
  });
});

describe("reportFailure", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("shows the failure, with the error's own text, as an error hint", () => {
    const shown: string[] = [];
    const hintHost = { setAttribute() {}, innerHTML: "", appendChild: (node: { textContent: string }) => shown.push(node.textContent) };
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#composer-hint" ? hintHost : null),
      createElement: () => ({ textContent: "", style: {} }),
    });
    reportFailure(new Error("module refused"));
    expect(shown).toEqual([errorPrefix(LANG) + actionFailedCopy("module refused")]);
  });
});
