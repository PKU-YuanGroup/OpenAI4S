/**
 * Redacted GET /onboarding projection. Drops credential-shaped keys even if
 * a buggy payload included them — the Web route is already secret-free.
 */
import {
  sanitizeStandardProfileReadiness,
  type StandardReadiness,
} from "../customize/environment";
import { sanitizeLocalModelDiscovery, type LocalDiscovery } from "../customize/models";
import { publicText } from "../scrub/scrub";
import { readProviderPresets, type ProviderPreset } from "./presets";

function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function isSecretKey(key: string): boolean {
  const k = key.toLowerCase();
  if (k === "has_api_key") return false;
  return (
    k === "api_key" ||
    k === "apikey" ||
    k === "authorization" ||
    k === "secret" ||
    k === "password" ||
    k === "token" ||
    k.endsWith("_api_key")
  );
}

export type OnboardingNetwork = {
  allow_network: boolean;
  egress: string;
  contacted: boolean;
};

export type OnboardingStatus = {
  provider: string;
  model: string;
  base_url: string;
  has_api_key: boolean;
  complete: boolean;
  /**
   * Whether the user finished or skipped setup (the stored flag alone), as
   * opposed to `complete`, which also reads "already configured". `null`
   * when the daemon predates the field: the wizard then keeps its original
   * flow and never resumes after a reload.
   */
  completed_explicitly: boolean | null;
  platform: string;
  native_runtime_supported: boolean;
  outbound: number;
  contacted: boolean;
  network: OnboardingNetwork;
  profiles: Record<string, unknown>[];
  active_id: string;
  protocols: unknown[];
  /** The registry's endpoint and default model for each offered protocol. */
  provider_presets: ProviderPreset[];
  local_model_catalog: LocalDiscovery;
  environment: StandardReadiness | null;
  /**
   * May this reader save the model setup? `false` for a team member: the
   * wizard is an admin's and is not shown to them (TEAM-10). A daemon that
   * does not send the field is a single-user one, so absent reads as `true`.
   */
  can_configure: boolean;
};

function dropSecretKeys(row: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(row)) {
    if (isSecretKey(key)) continue;
    out[key] = value;
  }
  return out;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function sanitizeOnboardingStatus(raw: unknown): OnboardingStatus {
  const row = dropSecretKeys(asRecord(raw));
  const network = dropSecretKeys(asRecord(row.network));
  const profiles = asList(row.profiles).map((item) => dropSecretKeys(asRecord(item)));
  return {
    provider: publicText(row.provider, 64),
    model: publicText(row.model, 512),
    base_url: publicText(row.base_url, 600),
    has_api_key: row.has_api_key === true,
    complete: row.complete === true,
    completed_explicitly:
      typeof row.completed_explicitly === "boolean" ? row.completed_explicitly : null,
    platform: publicText(row.platform, 80),
    native_runtime_supported: row.native_runtime_supported === true,
    outbound: Math.max(0, Number(row.outbound) || 0),
    contacted: row.contacted === true,
    network: {
      allow_network: network.allow_network === true,
      egress: publicText(network.egress, 32) || "off",
      contacted: network.contacted === true,
    },
    profiles,
    active_id: asString(row.active_id),
    protocols: asList(row.protocols),
    provider_presets: readProviderPresets(row.provider_presets),
    local_model_catalog: sanitizeLocalModelDiscovery(row.local_model_catalog),
    environment: sanitizeStandardProfileReadiness(row.environment),
    can_configure: row.can_configure !== false,
  };
}
