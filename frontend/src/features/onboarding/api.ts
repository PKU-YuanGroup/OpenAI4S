import { api } from "../customize/api";
import { sanitizeOnboardingStatus, type OnboardingStatus } from "./status";

export async function fetchOnboarding(): Promise<OnboardingStatus> {
  return sanitizeOnboardingStatus(await api("/onboarding"));
}

export async function completeOnboarding(
  body: Record<string, unknown>,
): Promise<OnboardingStatus> {
  const payload: Record<string, unknown> = { ...body };
  for (const key of Object.keys(payload)) {
    if (/api[_-]?key|authorization|secret|password/i.test(key) && !payload[key]) {
      delete payload[key];
    }
  }
  return sanitizeOnboardingStatus(
    await api("/onboarding/complete", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  );
}

export async function saveModelProfile(
  body: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return api("/model-profiles", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function activateModelProfile(id: string): Promise<Record<string, unknown>> {
  return api(`/model-profiles/${encodeURIComponent(id)}/activate`, {
    method: "POST",
  });
}

/** Activate a selected existing profile, then return the authoritative status. */
export async function activateExistingModelProfile(id: string): Promise<OnboardingStatus> {
  const profileId = String(id || "").trim();
  if (!profileId) throw new Error("model profile id is required");
  await activateModelProfile(profileId);
  return fetchOnboarding();
}

export async function probeModelProfile(id: string): Promise<Record<string, unknown>> {
  return api(`/model-profiles/${encodeURIComponent(id)}/probe`, {
    method: "POST",
  });
}
