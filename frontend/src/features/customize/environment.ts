/**
 * Skill / standard-profile readiness helpers. Port of app.js:11231-11240 and
 * 7795-7835 / 11653-11658.
 */
import { publicText } from "../scrub/scrub";
import { LANG, t } from "../../i18n";
import { copyLookup } from "../../i18n/copy";

/**
 * SCI-08: why a Skill cannot run here, and what to do about it. The rows
 * said "Not available on this machine: gpu" and "Cannot be verified on this
 * machine: science" -- requirement tokens and an extras name -- beside a
 * switch that was still on.
 */
const readyT = copyLookup({
  zh: {
    "ready.gpu": "需要 NVIDIA GPU，本机没有。",
    "ready.gpuStill": "开关打开时代理仍可加载它，但其中需要 GPU 的步骤在本机会失败。",
    "ready.gpuRemote": "可以在「计算」里添加远程 GPU，让这些步骤在那里运行。",
    "ready.science":
      "需要科学计算用的 Python 包（numpy、pandas、matplotlib 等），这里无法检查是否已安装。某一步因缺包失败时，可在「计算」里安装，或运行 `openai4s setup`。",
    "ready.missing": "需要 {0}，本机没有。",
    "ready.unknown": "需要 {0}，本机无法检查是否具备。",
    "ready.raw": "这个版本里代理无法加载这个技能：它需要内核直接联网，而这一点被禁止。",
    "ready.sandbox":
      "代理在本机无法加载这个技能：它的联网限制要靠内核沙箱执行，而本机没有启用沙箱（OPENAI4S_KERNEL_SANDBOX）。",
    "ready.rawAllowed":
      "设置了 OPENAI4S_KERNEL_ALLOW_RAW_NETWORK 时代理无法加载这个技能：内核可以绕过它的联网限制。",
    "ready.blocked": "代理在本机无法加载这个技能（{0}）。",
    "ready.openCompute": "打开「计算」",
  },
  en: {
    "ready.gpu": "Needs an NVIDIA GPU, and this machine has none.",
    "ready.gpuStill": "With the switch on the agent can still load it, but its GPU steps fail here.",
    "ready.gpuRemote": "Add a remote GPU in Compute to run them there.",
    "ready.science":
      "Needs scientific Python packages (numpy, pandas, matplotlib and the like), which this list does not check. If a step fails on a missing package, install it in Compute or run `openai4s setup`.",
    "ready.missing": "Needs {0}, which this machine does not have.",
    "ready.unknown": "Needs {0}; this machine cannot check whether it is there.",
    "ready.raw":
      "The agent cannot load this Skill in this version: it needs the kernel to reach the network directly, which is blocked.",
    "ready.sandbox":
      "The agent cannot load this Skill on this machine: its network limit is enforced by the kernel sandbox, which is not on here (OPENAI4S_KERNEL_SANDBOX).",
    "ready.rawAllowed":
      "The agent cannot load this Skill while OPENAI4S_KERNEL_ALLOW_RAW_NETWORK is set: the kernel could bypass its network limit.",
    "ready.blocked": "The agent cannot load this Skill on this machine ({0}).",
    "ready.openCompute": "Open Compute",
  },
});

export const skillReadinessT = readyT;

export type SkillReadinessNote = {
  /** One or more sentences: what is missing, what the agent will do, what to do. */
  text: string;
  /** Compute is where this is fixed (a remote GPU, a package install). */
  compute: boolean;
};

const SANDBOX_BLOCKS = new Set(["sandbox_not_enforced", "sandbox_unproven", "network_policy"]);

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

/**
 * A Skill's readiness in words (SCI-08), or null when it is ready. Reads the
 * catalog row's `readiness` block: `missing` (a requirement checked and
 * absent), `unverifiable` (one nobody can check here) and `blocked_on` (why
 * the agent's load of it is refused). A block comes first: when the load is
 * refused, "the agent can still load it" would be false.
 */
export function skillReadinessNote(s: unknown): SkillReadinessNote | null {
  const row = s && typeof s === "object" ? (s as Record<string, unknown>) : {};
  const rd =
    row.readiness && typeof row.readiness === "object"
      ? (row.readiness as Record<string, unknown>)
      : {};
  if (!rd.state || rd.state === "ready") return null;
  const lower = (items: string[]) => items.map((item) => item.toLowerCase());
  let missing = lower(publicList(rd.missing, 8));
  let unknown = lower(publicList(rd.unverifiable, 8));
  const blocked = lower(publicList(rd.blocked_on, 8));
  if (!missing.length && !unknown.length && !blocked.length) {
    // An older catalog row with a state but no lists: its requirements.
    const declared = lower(publicList(row.requirements, 8));
    if (rd.state === "needs_setup") missing = declared;
    else unknown = declared;
  }
  const lines: string[] = [];
  let compute = false;
  let sandbox = false;
  for (const reason of blocked) {
    if (reason === "raw_network") lines.push(readyT("ready.raw"));
    else if (reason === "raw_network_allowed") lines.push(readyT("ready.rawAllowed"));
    else if (SANDBOX_BLOCKS.has(reason)) {
      if (!sandbox) lines.push(readyT("ready.sandbox"));
      sandbox = true;
    } else lines.push(readyT("ready.blocked", reason));
  }
  for (const need of missing) {
    if (need === "gpu") {
      lines.push(readyT("ready.gpu"));
      if (!blocked.length) {
        lines.push(readyT("ready.gpuStill"), readyT("ready.gpuRemote"));
        compute = true;
      }
    } else lines.push(readyT("ready.missing", need));
  }
  for (const need of unknown) {
    if (need === "science") {
      lines.push(readyT("ready.science"));
      compute = compute || !blocked.length;
    } else lines.push(readyT("ready.unknown", need));
  }
  if (!lines.length) return null;
  // Chinese sentences run on without a space.
  return { text: lines.join(LANG === "en" ? " " : ""), compute };
}

export function skillReadinessNoteText(s: unknown): string | null {
  const note = skillReadinessNote(s);
  return note ? note.text : null;
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
