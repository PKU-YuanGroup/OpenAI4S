/**
 * Volcengine SSO / key-poll helpers. Port of app.js:12152-12259.
 * Key polling is bound to a TimerLease so unmount leaves zero timers.
 */
import { publicText } from "../scrub/scrub";
import {
  isLeaseLive,
  scheduleTimeout,
  type TimerLease,
} from "./timers";

export const VOLC_KEY_POLL_FIRST_MS = 2500;
export const VOLC_KEY_POLL_EVERY_MS = 5000;
export const VOLC_KEY_POLL_MAX = 24;

export const VOLC_KEY_WAIT_STATES = new Set([
  "key_missing",
  "no_plan",
  "key_check_failed",
]);

export const VOLC_CHECK_FAILED_STATES = new Set([
  "check_failed",
  "key_check_failed",
  "endpoint_check_failed",
]);

export function volcPercent(period: unknown): number {
  const row = period && typeof period === "object" ? (period as Record<string, unknown>) : {};
  const direct = Number(row.percent);
  if (Number.isFinite(direct)) return Math.max(0, Math.min(100, direct));
  const used = Number(row.used);
  const total = Number(row.total);
  return Number.isFinite(used) && Number.isFinite(total) && total > 0
    ? Math.max(0, Math.min(100, (used * 100) / total))
    : 0;
}

export function volcQuotaValue(period: unknown): string {
  const row = period && typeof period === "object" ? (period as Record<string, unknown>) : {};
  const used = Number(row.used);
  const total = Number(row.total);
  if (Number.isFinite(used) && Number.isFinite(total)) {
    return `${used.toLocaleString()} / ${total.toLocaleString()}`;
  }
  return `${Math.round(volcPercent(period))}%`;
}

export function volcApiKeyUrl(state: unknown): string {
  const identity =
    state && typeof state === "object"
      ? ((state as Record<string, unknown>).identity as Record<string, unknown> | undefined)
      : undefined;
  const raw = String((identity && identity.region) || "cn-beijing").toLowerCase();
  const region = /^[a-z0-9-]{2,64}$/.test(raw) ? raw : "cn-beijing";
  return `https://console.volcengine.com/ark/region:ark+${region}/apiKey`;
}

export function accessStateOf(state: unknown): string {
  const row = state && typeof state === "object" ? (state as Record<string, unknown>) : {};
  const access =
    row.access && typeof row.access === "object"
      ? (row.access as Record<string, unknown>)
      : {};
  return String(access.state || "");
}

export type VolcKeyPollHooks = {
  refresh: () => Promise<unknown>;
  isAlive: () => boolean;
  onExhausted?: () => void;
};

/**
 * app.js:12206-12228. First tick at 2500ms, then every 5000ms, at most 24
 * attempts. Stops when access leaves the wait set, the lease dies, or the
 * host unmounts (`isAlive`).
 */
export function startVolcengineKeyPolling(
  lease: TimerLease,
  hooks: VolcKeyPollHooks,
): { stop: () => void; isPolling: () => boolean } {
  let attempts = 0;
  let polling = true;
  let current: ReturnType<typeof setTimeout> | 0 = 0;

  const stop = (): void => {
    polling = false;
    if (current) {
      clearTimeout(current);
      current = 0;
    }
  };

  const arm = (ms: number): void => {
    if (!polling || !isLeaseLive(lease) || !hooks.isAlive()) {
      polling = false;
      return;
    }
    current = scheduleTimeout(
      lease,
      () => {
        current = 0;
        void poll();
      },
      ms,
    );
  };

  const poll = async (): Promise<void> => {
    if (!polling || !isLeaseLive(lease) || !hooks.isAlive()) {
      polling = false;
      return;
    }
    attempts += 1;
    try {
      const next = await hooks.refresh();
      const accessState = accessStateOf(next);
      if (!VOLC_KEY_WAIT_STATES.has(accessState)) {
        polling = false;
        return;
      }
    } catch {
      /* Keep the explicit recheck action available. */
    }
    if (!polling || !isLeaseLive(lease) || !hooks.isAlive()) {
      polling = false;
      return;
    }
    if (attempts < VOLC_KEY_POLL_MAX) {
      arm(VOLC_KEY_POLL_EVERY_MS);
    } else {
      polling = false;
      hooks.onExhausted?.();
    }
  };

  arm(VOLC_KEY_POLL_FIRST_MS);
  return { stop, isPolling: () => polling && isLeaseLive(lease) };
}

export function openVolcengineAuthorization(
  url: string,
  popup: Window | null = null,
): Window | null {
  if (!url) return null;
  let target = popup;
  try {
    if (target && !target.closed) target.location.href = url;
    else target = window.open(url, "_blank", "noopener,noreferrer");
    if (target) {
      try {
        target.focus();
      } catch {
        /* popup blocked */
      }
    }
  } catch {
    /* The fallback button remains available if the browser blocks it. */
  }
  return target;
}

export function publicVolcError(error: unknown): string {
  return publicText(
    error && typeof error === "object" && "message" in error
      ? (error as { message: unknown }).message
      : error,
    240,
  );
}

export const VOLC_CONFIGURE_REFRESH_CODES = new Set([
  "ark_key_missing",
  "ark_key_choice_required",
  "ark_key_choice_invalid",
  "ark_endpoint_missing",
  "ark_endpoint_choice_required",
  "ark_endpoint_choice_invalid",
  "ark_profile_missing",
  "ark_profile_ambiguous",
  "plan_not_available",
]);
