/**
 * The first-run wizard loads only when it has something to show: a setup
 * that is not complete, or a status it must report as unreadable.
 */
import { describe, expect, it, vi } from "vitest";

import { startOnboarding } from "./boot";
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
