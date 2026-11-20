/**
 * TEAM-23: a refused save in Settings must say so where the user is looking,
 * and a refusal by role must say who can do it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../i18n", () => ({
  LANG: "en",
  t: (key: string) => key,
  tOptional: () => null,
  onLanguageChange: () => () => undefined,
}));

import { teamUser } from "../chrome/team-identity";
import { custTab } from "./actions";
import { ApiError, apiErrorText } from "./api";
import { hint } from "./host";
import { settingsReadOnly } from "./roles";
import { customizeNotice, customizeOpen } from "./state";

describe("a refusal by role", () => {
  it("reads as who can do it, without the bare server text or a request id", () => {
    const refusal = new ApiError({ error: "admin only", code: "admin_only", request_id: "req-1" }, 403);
    const text = apiErrorText(refusal);
    expect(text).toMatch(/Only an administrator/);
    expect(text).not.toContain("admin only");
    expect(text).not.toContain("req-1");
  });

  it("leaves any other failure as the server said it, with its request id", () => {
    const failure = new ApiError({ error: "disk full", request_id: "req-2" }, 500);
    expect(apiErrorText(failure)).toBe("disk full [req-2]");
  });
});

describe("the Settings status line", () => {
  const composer = vi.fn();
  beforeEach(() => {
    vi.stubGlobal("window", { hint: composer });
    composer.mockReset();
  });
  afterEach(() => {
    customizeOpen.value = false;
    customizeNotice.value = null;
    vi.unstubAllGlobals();
  });

  it("carries what an action reported while Settings is open", () => {
    customizeOpen.value = true;
    hint("Save failed: Only an administrator can change this setting.", true);
    expect(customizeNotice.value).toEqual({
      text: "Save failed: Only an administrator can change this setting.",
      error: true,
    });
    // The composer line still gets it, for when Settings closes.
    expect(composer).toHaveBeenCalledOnce();
    hint("Added: lab model");
    expect(customizeNotice.value).toEqual({ text: "Added: lab model", error: false });
  });

  it("is untouched while Settings is closed, and cleared by a tab change", () => {
    hint("from the workbench", true);
    expect(customizeNotice.value).toBeNull();
    customizeOpen.value = true;
    hint("refused", true);
    custTab("models");
    expect(customizeNotice.value).toBeNull();
  });
});

describe("settingsReadOnly", () => {
  afterEach(() => {
    teamUser.value = null;
  });

  it("is true for a team member only", () => {
    expect(settingsReadOnly()).toBe(false);
    teamUser.value = { username: "alice", role: "admin" };
    expect(settingsReadOnly()).toBe(false);
    teamUser.value = { username: "cli", kind: "service" };
    expect(settingsReadOnly()).toBe(false);
    teamUser.value = { username: "bob", role: "member" };
    expect(settingsReadOnly()).toBe(true);
  });
});
