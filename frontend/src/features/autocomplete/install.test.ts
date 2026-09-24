import { afterEach, describe, expect, it, vi } from "vitest";
import { isReady } from "../../compat/stub";
import { autocompleteReady, installAutocomplete, watchEditAreas } from "./index";

afterEach(() => {
  vi.unstubAllGlobals();
});

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

  it("does not watch every mutation of the document for editor textareas", () => {
    // artifacts/editor-view.ts binds the one it creates; a document-wide
    // observer re-queried the whole page on every streamed chunk.
    const observer = vi.fn(() => ({ observe: vi.fn(), disconnect: vi.fn() }));
    const scans = vi.fn(() => []);
    vi.stubGlobal("MutationObserver", observer);
    vi.stubGlobal("document", { querySelectorAll: scans, documentElement: {}, body: {} });
    watchEditAreas();
    expect(scans).toHaveBeenCalledTimes(1);
    expect(observer).not.toHaveBeenCalled();
  });
});
