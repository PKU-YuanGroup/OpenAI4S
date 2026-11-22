/**
 * The first-run wizard loads only when it has something to show: a setup
 * that is not complete, or a status it must report as unreadable.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { startOnboarding } from "./boot";
import { writeResumeMarker } from "./machine";
import type { OnboardingStatus } from "./status";

const status = (complete: boolean) => ({ complete }) as OnboardingStatus;

describe("startOnboarding", () => {
  it("does not load the wizard for a completed setup", async () => {
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => status(true), mount);
    expect(mount).not.toHaveBeenCalled();
  });

  it("loads the wizard for a setup still to do", async () => {
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => status(false), mount);
    expect(mount).toHaveBeenCalledOnce();
  });

  it("loads the wizard when the status cannot be read, so it can say so", async () => {
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => {
      throw new Error("daemon restarting");
    }, mount);
    expect(mount).toHaveBeenCalledOnce();
  });
});

describe("startOnboarding after a reload part-way through the wizard (NV-04)", () => {
  class TabStorage {
    data = new Map<string, string>();
    getItem(key: string) {
      return this.data.get(key) ?? null;
    }
    setItem(key: string, value: string) {
      this.data.set(key, value);
    }
    removeItem(key: string) {
      this.data.delete(key);
    }
  }
  const saved = (overrides: Record<string, unknown> = {}) =>
    ({
      complete: true,
      completed_explicitly: false,
      profiles: [{ id: "mp-saved", name: "Saved" }],
      active_id: "mp-saved",
      ...overrides,
    }) as unknown as OnboardingStatus;
  let storage: TabStorage;
  beforeEach(() => {
    storage = new TabStorage();
    vi.stubGlobal("sessionStorage", storage);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("loads the wizard when this tab saved the active profile and nobody finished", async () => {
    writeResumeMarker("mp-saved");
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => saved(), mount);
    expect(mount).toHaveBeenCalledOnce();
    expect(storage.data.size).toBe(1);
  });

  it("loads it too when the saved profile still needs activating", async () => {
    writeResumeMarker("mp-saved");
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => saved({ active_id: "" }), mount);
    expect(mount).toHaveBeenCalledOnce();
  });

  it("does not load it for a configured install this tab holds no marker for", async () => {
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => saved(), mount);
    expect(mount).not.toHaveBeenCalled();
  });

  it.each([
    ["finished or skipped", { completed_explicitly: true }],
    ["the profile is gone", { profiles: [] }],
    ["the daemon predates the field", { completed_explicitly: null }],
  ])("clears the marker and stays closed when %s", async (_name, overrides) => {
    writeResumeMarker("mp-saved");
    const mount = vi.fn(async () => {});
    await startOnboarding(async () => saved(overrides), mount);
    expect(mount).not.toHaveBeenCalled();
    expect(storage.data.size).toBe(0);
  });
});
