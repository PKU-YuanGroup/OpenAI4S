/**
 * Skill / standard-profile readiness helpers. Port of app.js:11231-11240 and
 * 7795-7835 / 11653-11658.
 */
import { publicText } from "../scrub/scrub";
import { t } from "../../i18n";

export function publicList(
  value: unknown,
  limit = 24,
  textLimit = 160,
): string[] {
  return (Array.isArray(value) ? value : [])
    .slice(0, limit)
    .map((item) => publicText(item, textLimit))
    .filter(Boolean);
}

export function skillReadinessNoteText(s: unknown): string | null {
  const row = s && typeof s === "object" ? (s as Record<string, unknown>) : {};
  const rd =
    row.readiness && typeof row.readiness === "object"
      ? (row.readiness as Record<string, unknown>)
      : {};
  if (!rd.state || rd.state === "ready") return null;
  const named = publicList(
    rd.state === "needs_setup" ? rd.missing : rd.unverifiable,
    8,
  );
  const listed = (named.length ? named : publicList(row.requirements, 8)).join(", ");
  if (!listed) return null;
  return t(
    rd.state === "needs_setup" ? "skill.readiness.needsSetup" : "skill.readiness.unknown",
    listed,
  );
}

export type StandardReadiness = {
  schema_version: number;
  enabled: boolean;
  ready: boolean;
  state: string;
  reason: string;
  requirements_digest: string;
  missing_environments: string[];
  missing_packages: Record<string, string[]>;
  remediation: {
    requires_explicit_action: boolean;
    commands: Array<{ command: string; label: string }>;
  } | null;
};

export function sanitizeStandardProfileReadiness(
  value: unknown,
): StandardReadiness | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const allowed = new Set(["ready", "needs_setup", "needs_repair", "unavailable"]);
  const state = allowed.has(String(row.state || ""))
    ? String(row.state)
    : "unavailable";
  const missingEnvironments = (
    Array.isArray(row.missing_environments) ? row.missing_environments : []
  )
    .map((name) => publicText(name, 160))
    .filter(Boolean);
  const missingPackages: Record<string, string[]> = {};
  const sourcePackages =
    row.missing_packages && typeof row.missing_packages === "object"
      ? (row.missing_packages as Record<string, unknown>)
      : {};
  Object.keys(sourcePackages)
    .sort()
    .forEach((name) => {
      const environment = publicText(name, 160);
      if (!environment) return;
      missingPackages[environment] = (
        Array.isArray(sourcePackages[name]) ? sourcePackages[name] : []
      )
        .map((packageName) => publicText(packageName, 160))
        .filter(Boolean);
    });
  const sourceRemediation =
    row.remediation && typeof row.remediation === "object"
      ? (row.remediation as Record<string, unknown>)
      : null;
  const commands: Array<{ command: string; label: string }> = [];
  if (sourceRemediation && sourceRemediation.requires_explicit_action === true) {
    const candidates = Array.isArray(sourceRemediation.commands)
      ? sourceRemediation.commands
      : sourceRemediation.command
        ? [sourceRemediation]
        : [];
    candidates.forEach((candidate) => {
      if (!candidate || typeof candidate !== "object") return;
      const item = candidate as Record<string, unknown>;
      if (typeof item.command !== "string") return;
      const command = item.command;
      if (!command || command.length > 1000) return;
      commands.push({ command, label: publicText(item.label, 120) });
    });
  }
  return {
    schema_version: Number(row.schema_version) || 0,
    enabled: row.enabled === true,
    ready: row.ready === true && state === "ready",
    state,
    reason: publicText(row.reason, 120),
    requirements_digest: publicText(row.requirements_digest, 96),
    missing_environments: missingEnvironments,
    missing_packages: missingPackages,
    remediation: sourceRemediation
      ? {
          requires_explicit_action: sourceRemediation.requires_explicit_action === true,
          commands,
        }
      : null,
  };
}

export function standardReadinessStateText(readiness: StandardReadiness): string {
  if (readiness.ready) return t("environment.readiness.ready");
  if (readiness.state === "needs_setup") return t("environment.readiness.needsSetup");
  if (readiness.state === "needs_repair") return t("environment.readiness.needsRepair");
  return t("environment.readiness.unavailable");
}
