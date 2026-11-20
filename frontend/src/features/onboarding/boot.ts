import { fetchOnboarding } from "./api";
import type { OnboardingStatus } from "./status";

export type WindowBag = Record<string, unknown>;

function mountWizard(): Promise<void> {
  return import("./mount").then((ui) => ui.mountOnboarding());
}

/**
 * Whether the wizard has anything for this reader: setup is not complete and
 * the reader may save it. A team member may not (TEAM-10) -- the model setup
 * is an admin's, and both Continue and Skip answered 403 -- so a member never
 * gets the wizard, whatever state the admin left it in.
 */
export function wizardWanted(status: OnboardingStatus): boolean {
  return !status.complete && status.can_configure;
}

/**
 * Load the first-run wizard only when it has something to show. A complete
 * setup, the usual case, hides it for the whole session (`hydrate` with
 * `complete`), so it used to ship in every first load for nothing. A status
 * that cannot be read still mounts it: the wizard reports that failure.
 */
export async function startOnboarding(
  read: () => Promise<OnboardingStatus> = fetchOnboarding,
  mount: () => Promise<void> = mountWizard,
): Promise<void> {
  let status: OnboardingStatus;
  try {
    status = await read();
  } catch {
    await mount();
    return;
  }
  if (wizardWanted(status)) await mount();
}

/** M-01 boot. No window contract names. */
export function bootOnboarding(_target: WindowBag = globalThis as unknown as WindowBag): void {
  if (typeof document === "undefined" || import.meta.env.MODE === "test") return;
  void startOnboarding().catch(() => {
    /* the wizard chunk did not load; the workbench works without it */
  });
}
