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
  platform: string;
  native_runtime_supported: boolean;
  outbound: number;
  contacted: boolean;
  network: OnboardingNetwork;
  profiles: Record<string, unknown>[];
  active_id: string;
  protocols: unknown[];
  local_model_catalog: LocalDiscovery;
  environment: StandardReadiness | null;
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
    local_model_catalog: sanitizeLocalModelDiscovery(row.local_model_catalog),
    environment: sanitizeStandardProfileReadiness(row.environment),
  };
}

export function onboardingStatusHasSecret(status: OnboardingStatus, secret: string): boolean {
  if (!secret) return false;
  return JSON.stringify(status).includes(secret);
}
