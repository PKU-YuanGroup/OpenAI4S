import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { custTab } from "./actions";
import {
  CUST_LOAD_TIMEOUT_MS,
  markCustomizeFailed,
  markCustomizeLoaded,
  markCustomizeTimedOut,
} from "./load";
import { customizeGeneration, customizeLoad } from "./state";

describe("bounded Settings load", () => {
  beforeEach(() => {
    vi.stubGlobal("document", { querySelectorAll: () => [] });
    customizeGeneration.value = 0;
    customizeLoad.value = { generation: 0, state: "ready", error: null };
  });
  afterEach(() => vi.unstubAllGlobals());

  it("every custTab() starts a pending load for the new generation", () => {
    custTab("skills");
    expect(customizeLoad.value).toEqual({
      generation: customizeGeneration.value,
      state: "loading",
      error: null,
    });
  });

  it("the tab's first answer, or its failure, settles the load once", () => {
    custTab("skills");
    markCustomizeLoaded();
    expect(customizeLoad.value.state).toBe("ready");
    markCustomizeFailed("late failure"); // a second report cannot reopen it
    expect(customizeLoad.value.state).toBe("ready");

    custTab("models");
    markCustomizeFailed("Load failed: boom");
    expect(customizeLoad.value).toMatchObject({ state: "failed", error: "Load failed: boom" });
  });

  it("the deadline fires only for the generation that is still pending", () => {
    custTab("skills");
    const stale = customizeGeneration.value;
    custTab("models"); // the user moved on before the first deadline
    markCustomizeTimedOut(stale);
    expect(customizeLoad.value.state).toBe("loading");
    markCustomizeTimedOut(customizeGeneration.value);
    expect(customizeLoad.value.state).toBe("timeout");
    expect(customizeLoad.value.error).toBeTruthy();
    // An answer that arrives after the deadline cannot reopen the load.
    markCustomizeLoaded();
    expect(customizeLoad.value.state).toBe("timeout");
  });

  it("keeps the app.js deadline", () => {
    expect(CUST_LOAD_TIMEOUT_MS).toBe(30000);
  });
});
