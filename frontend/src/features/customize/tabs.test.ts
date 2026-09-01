import { afterEach, describe, expect, it } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { closeCust, custTab, openCust } from "./actions";
import { customizeGeneration, customizeOpen, customizeTab, nestedEditor } from "./state";
import {
  CUST_TABS,
  CUST_TAB_ALIASES,
  isCustTab,
  normalizeTab,
} from "./tabs";

describe("F-19 tab state machine", () => {
  afterEach(() => {
    closeCust();
    resetStoreFields();
  });

  it("names exactly the nine visible tabs", () => {
    expect([...CUST_TABS]).toEqual([
      "general",
      "skills",
      "specialists",
      "connectors",
      "compute",
      "permissions",
      "network",
      "memory",
      "models",
    ]);
  });

  it("openCust() with no argument lands on general", () => {
    openCust();
    expect(customizeOpen.value).toBe(true);
    expect(customizeTab.value).toBe("general");
  });

  it("openCust('models') selects models", () => {
    openCust("models");
    expect(customizeTab.value).toBe("models");
  });

  it("custTab('agents') is the specialists alias (app.js:11130)", () => {
    expect(CUST_TAB_ALIASES.agents).toBe("specialists");
    custTab("agents");
    expect(customizeTab.value).toBe("specialists");
  });

  it("unknown tab falls back to general rather than throwing", () => {
    expect(normalizeTab("not-a-tab")).toBe("general");
    expect(normalizeTab("")).toBe("general");
    expect(normalizeTab(null)).toBe("general");
    expect(isCustTab("memory")).toBe(true);
    expect(isCustTab("agents")).toBe(false);
  });

  it("custTab of the same tab still bumps generation so the pane remounts", () => {
    openCust("compute");
    const gen = customizeGeneration.value;
    custTab("compute");
    expect(customizeGeneration.value).toBe(gen + 1);
  });

  it("custTab clears a nested editor (skill/job overlay)", () => {
    nestedEditor.value = { kind: "job", id: "j1" };
    custTab("memory");
    expect(nestedEditor.value).toBeNull();
  });

  it("closeCust hides the modal and drops nested editors", () => {
    openCust("skills");
    nestedEditor.value = { kind: "skill", name: null };
    closeCust();
    expect(customizeOpen.value).toBe(false);
    expect(nestedEditor.value).toBeNull();
  });
});
