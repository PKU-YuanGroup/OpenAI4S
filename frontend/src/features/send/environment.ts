/**
 * Standard-profile readiness used by send() / turnDone. Port of
 * app.js:7842-7906. Sanitize lives in F-19 `customize/environment.ts`.
 */

import {
  _environmentStatusPromise,
  _environmentStatusRefreshFailed,
  environmentStatus,
  standardProfileReadiness,
} from "../../stores/customize";
import { t } from "../../i18n/runtime";
import {
  sanitizeStandardProfileReadiness,
  type StandardReadiness,
} from "../customize/environment";
import { api } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { callLane } from "./host";

export function isEnvironmentReadinessError(error: unknown): boolean {
  const err = error as { status?: number; code?: string } | null;
  return !!(
    err &&
    ((err.status === 409 && err.code === "environment_not_ready") ||
      (err.status === 503 && err.code === "environment_readiness_unavailable"))
  );
}

function environmentReadinessSummary(readiness: StandardReadiness | null): string {
  if (!readiness || readiness.state === "unavailable") {
    return t("environment.readiness.bannerUnavailable");
  }
  const packageCount = Object.values(readiness.missing_packages || {}).reduce(
    (count, names) => count + names.length,
    0,
  );
  return t(
    "environment.readiness.bannerMissing",
    readiness.missing_environments.length,
    packageCount,
  );
}

export function renderEnvironmentReadinessBanner(): void {
  if (typeof document === "undefined") return;
  const readiness = standardProfileReadiness.value as StandardReadiness | null;
  const visible = !!(readiness && readiness.enabled === true && readiness.ready !== true);
  document.querySelectorAll(".environment-readiness-banner").forEach((banner) => {
    banner.classList.toggle("hidden", !visible);
    if (!visible) return;
    const title = banner.querySelector("[data-environment-readiness-title]");
    const summary = banner.querySelector("[data-environment-readiness-summary]");
    const action = banner.querySelector("[data-open-environment-readiness]");
    if (title) title.textContent = t("environment.readiness.bannerTitle");
    if (summary) summary.textContent = environmentReadinessSummary(readiness);
    if (action) action.textContent = t("environment.readiness.openCompute");
  });
}

export async function refreshEnvironmentStatus(): Promise<unknown> {
  const existing = _environmentStatusPromise.value as Promise<unknown> | null;
  if (existing) return existing;
  const pending = (async () => {
    try {
      const payload = await api("/environments/status");
      _environmentStatusRefreshFailed.value = false;
      environmentStatus.value = payload && typeof payload === "object" ? payload : null;
      const env = environmentStatus.value as { standard_profile_readiness?: unknown } | null;
      standardProfileReadiness.value = sanitizeStandardProfileReadiness(
        env && env.standard_profile_readiness,
      );
    } catch {
      _environmentStatusRefreshFailed.value = true;
      const prev = standardProfileReadiness.value as StandardReadiness | null;
      if (prev && prev.enabled === true) {
        standardProfileReadiness.value = {
          ...prev,
          ready: false,
          state: "unavailable",
          reason: "status_refresh_failed",
        };
      }
    }
    renderEnvironmentReadinessBanner();
    return environmentStatus.value;
  })();
  _environmentStatusPromise.value = pending;
  try {
    return await pending;
  } finally {
    _environmentStatusPromise.value = null;
  }
}

export function handleEnvironmentReadinessTerminal(detail: unknown): boolean {
  const rec = detail && typeof detail === "object" ? (detail as Record<string, unknown>) : null;
  const code = rec && rec.code;
  if (
    !rec ||
    rec.status !== "failed" ||
    !["environment_not_ready", "environment_readiness_unavailable"].includes(String(code || ""))
  ) {
    return false;
  }
  void refreshEnvironmentStatus().finally(() => {
    callLane("openCust", "compute");
    hint(t("environment.readiness.sendBlocked"), true);
  });
  return true;
}

export function unavailableReadinessSnapshot(): StandardReadiness {
  return {
    schema_version: 1,
    enabled: true,
    ready: false,
    state: "unavailable",
    reason: "status_refresh_failed",
    requirements_digest: "",
    missing_environments: [],
    missing_packages: {},
    remediation: null,
  };
}
