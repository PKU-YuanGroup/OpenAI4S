import { effect, signal } from "@preact/signals";
import { describe, expect, it, vi } from "vitest";

vi.mock("./components/dashboard/Shell", () => ({ Shell: () => null }));

import { App } from "./app";
import { languageRevision } from "./i18n/runtime";

describe("F-03 scaffold", () => {
  it("wires @preact/signals", () => {
    const ready = signal(false);
    ready.value = true;
    expect(ready.value).toBe(true);
  });
});

describe("the app root", () => {
  it("repaints when the language on screen changes", () => {
    let renders = 0;
    // A component render is tracked the same way an effect is.
    const stop = effect(() => {
      App();
      renders += 1;
    });
    languageRevision.value += 1;
    stop();
    expect(renders).toBe(2);
  });
});
