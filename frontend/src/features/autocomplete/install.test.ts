import { describe, expect, it } from "vitest";
import { isReady } from "../../compat/stub";
import { autocompleteReady, installAutocomplete } from "./index";

describe("F-12 window exports", () => {
  it("assigns ac (object) and edacTeardown (isReady, not typeof)", () => {
    const target: Record<string, unknown> = {};
    installAutocomplete(target);
    expect(target.ac).toBeTruthy();
    expect((target.ac as { open: boolean }).open).toBe(false);
    expect(isReady(target.edacTeardown)).toBe(true);
    expect(isReady(target.bindEditorAutocomplete)).toBe(true);
    expect(autocompleteReady(target)).toBe(true);
  });
});
