/**
 * Where a session runs (M3b-6): the conversation-head badge, the lost-state
 * banner and the run-location dialog. Port of app.js:8375-8517.
 *
 * Three things a user cannot infer from a spinner: WHERE the kernel is, WHICH
 * of the four readiness conditions is still outstanding, and WHETHER the
 * kernel's memory was lost and quietly replaced. Results produced after a
 * recovery look exactly like results from the session that was lost, so
 * INV-11 makes saying so mandatory.
 */

import { copyLookup } from "../../i18n/copy";
import { _computeLostSeen } from "../../stores/artifacts";
import { _openGen, currentId } from "../../stores/session";
import { computeStatus } from "../../stores/timeline";
import { _modalMode } from "../../stores/ui";
import { api, apiErrorText } from "./api";
import { hint } from "./chrome";
import { $, closeModalEl, el, openModalEl } from "./dom";
import { iconEl } from "./icon";

export const computeT = copyLookup({
  en: {
    "compute.status.loading": "Checking run location…",
    "compute.status.unavailable": "Run location unavailable",
    "compute.status.stale": "last confirmed; may be out of date",
    "compute.status.retry": "Retry",
    "compute.status.invalid": "Invalid run-location response",
    "compute.profiles.unavailable": "Could not load cluster profiles",
    "compute.profiles.invalid": "Invalid cluster-profile response",
    "compute.profiles.empty": "No cluster profiles are configured on this daemon.",
    "compute.status.cluster": "cluster",
  },
  zh: {
    "compute.status.loading": "正在查询运行位置…",
    "compute.status.unavailable": "无法确认运行位置",
    "compute.status.stale": "上次确认；可能已过期",
    "compute.status.retry": "重试",
    "compute.status.invalid": "运行位置响应无效",
    "compute.profiles.unavailable": "无法加载集群配置",
    "compute.profiles.invalid": "集群配置响应无效",
    "compute.profiles.empty": "本 daemon 尚未配置集群运行配置。",
    "compute.status.cluster": "集群",
  },
});
const t = computeT;

export type ComputeStatus = {
  location: "local" | "cluster";
  readiness?: { ready?: boolean; blocked_on?: string };
  allocation?: { allocation_id?: string; phase?: string } | null;
  workload?: { profile?: string; phase?: string; reason?: string } | null;
  state_lost_epochs?: unknown[];
};

type ComputeProfile = {
  name: string;
  cpus?: number;
  gpus?: number;
  memory_mb?: number;
  walltime_s?: number;
};

const COMPUTE_BLOCKED_LABEL: Record<string, string> = {
  allocation: "compute.blocked.allocation",
  workspace: "compute.blocked.workspace",
  worker: "compute.blocked.worker",
  kernel: "compute.blocked.kernel",
};

export type ComputeReadState = {
  fid: string;
  generation: number;
  phase: "loading" | "available" | "error";
  status: ComputeStatus | null;
  error?: string;
};

type ComputeResult = { phase: "available"; status: ComputeStatus } | { phase: "error"; error: string };

// Validate the fields the UI reads. A 200 with an HTML body or an incomplete
// status is unknown, never evidence that the session is running locally.
function isRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function validStatus(value: unknown): value is ComputeStatus {
  if (!isRecord(value) || (value.location !== "local" && value.location !== "cluster")) return false;
  for (const [key, fields] of [
    ["readiness", { ready: "boolean", blocked_on: "string" }],
    ["allocation", { allocation_id: "string", phase: "string" }],
    ["workload", { profile: "string", phase: "string", reason: "string" }],
  ] as const) {
    const part = value[key];
    if (part == null) continue;
    if (!isRecord(part)) return false;
    for (const [field, type] of Object.entries(fields)) {
      if (part[field] != null && typeof part[field] !== type) return false;
    }
  }
  return value.state_lost_epochs === undefined || (Array.isArray(value.state_lost_epochs)
    && value.state_lost_epochs.every((epoch) => Number.isSafeInteger(epoch) && epoch >= 0));
}

export async function loadComputeStatus(fid: string): Promise<ComputeResult> {
  try {
    const status = await api(`/sessions/${encodeURIComponent(fid)}/compute`);
    if (!validStatus(status)) throw new Error(t("compute.status.invalid"));
    return { phase: "available", status };
  } catch (error) {
    return { phase: "error", error: apiErrorText(error) };
  }
}

/** Never confuse a failed read with Local, or let an older read replace a newer one. */
export async function refreshComputeStatus(fid: string | null | undefined): Promise<ComputeStatus | null> {
  if (!fid || fid !== currentId.value) return null;
  const result = await readComputeStatus(fid);
  return result.phase === "available" ? result.status : null;
}

async function readComputeStatus(fid: string): Promise<ComputeResult> {
  if (fid !== currentId.value) return loadComputeStatus(fid);
  const generation = _openGen.value;
  const previous = computeStatus.value;
  const loading: ComputeReadState = {
    fid, generation, phase: "loading",
    status: previous?.fid === fid ? previous.status : null,
  };
  computeStatus.value = loading;
  renderComputeBadge();
  renderComputeLostBanner();
  const result = await loadComputeStatus(fid);
  if (fid !== currentId.value || generation !== _openGen.value || computeStatus.value !== loading) return result;
  computeStatus.value = result.phase === "available"
    ? { fid, generation, ...result }
    : { ...loading, ...result };
  renderComputeBadge();
  renderComputeLostBanner();
  return result;
}

function renderComputeBadge(): void {
  const host = $(".conv-head-actions");
  if (!host) return;
  let badge = $("#compute-badge");
  const state = computeStatus.value;
  const status = state?.status;
  // A local session gets no badge at all: a chip reading "local" on every
  // session of an install with no cluster is pure noise.
  if (!state || (state.phase === "available" && status?.location === "local")) {
    badge?.remove();
    return;
  }
  if (!badge) {
    badge = el("button", "compute-badge");
    badge.id = "compute-badge";
    badge.onclick = () => {
      void openRunLocationDialog(currentId.value);
    };
    host.insertBefore(badge, host.firstChild);
  }
  if (state.phase !== "available") {
    badge.innerHTML = "";
    badge.appendChild(iconEl("server", 13));
    const previous = status?.location === "cluster"
      ? status.workload?.profile || t("compute.status.cluster")
      : status ? t("compute.location.local") : "";
    const label = t(state.phase === "loading" ? "compute.status.loading" : "compute.status.unavailable");
    // Lead with uncertainty: long profile names must not truncate the warning.
    const text = previous ? `${label} · ${previous} (${t("compute.status.stale")})` : label;
    badge.appendChild(el("span", "cb-label", text));
    badge.className = "compute-badge waiting";
    badge.title = [text, state.error].filter(Boolean).join("\n");
    return;
  }
  if (!status) return;
  const readiness = status.readiness || {};
  const allocation = status.allocation || {};
  const workload = status.workload || {};
  badge.innerHTML = "";
  badge.appendChild(iconEl("server", 13));
  const ready = !!readiness.ready;
  const blockedKey = COMPUTE_BLOCKED_LABEL[readiness.blocked_on || ""];
  const label = ready
    ? `${workload.profile || t("compute.badge.ready")}`
    : blockedKey ? t(blockedKey) : (allocation.phase || workload.phase || "").toLowerCase();
  badge.appendChild(el("span", "cb-label", label));
  badge.className = "compute-badge" + (ready ? " ready" : " waiting");
  // The phase is the tooltip rather than the label: an allocation id and a
  // phase name are what a support conversation needs.
  badge.title = [
    workload.profile ? `profile: ${workload.profile}` : "",
    allocation.allocation_id ? `allocation: ${allocation.allocation_id}` : "",
    allocation.phase ? `phase: ${allocation.phase}` : "",
    workload.reason ? `reason: ${workload.reason}` : "",
  ].filter(Boolean).join("\n");
}

function renderComputeLostBanner(): void {
  const status = computeStatus.value?.status;
  const epochs = status?.state_lost_epochs || [];
  const key = currentId.value + ":" + epochs.join(",");
  let banner = $("#compute-lost");
  if (!epochs.length || (_computeLostSeen.value as Record<string, unknown>)[key]) {
    banner?.remove();
    return;
  }
  if (!banner) {
    const messages = $("#messages");
    if (!messages || !messages.parentNode) return;
    banner = el("div", "compute-lost");
    banner.id = "compute-lost";
    messages.parentNode.insertBefore(banner, messages);
  }
  const shown = banner;
  shown.innerHTML = "";
  shown.appendChild(iconEl("alert-triangle", 15));
  const text = el("div", "cl-text");
  text.appendChild(el("strong", null, t("compute.lost.title")));
  text.appendChild(el("div", "cl-body", t("compute.lost.body")));
  shown.appendChild(text);
  const dismiss = el("button", "cl-dismiss", t("compute.lost.dismiss"));
  // Dismissal is per (session, set of lost epochs): a further loss raises it
  // again rather than being swallowed by an earlier "got it".
  dismiss.onclick = () => {
    _computeLostSeen.value = { ...(_computeLostSeen.value as Record<string, unknown>), [key]: true };
    shown.remove();
  };
  shown.appendChild(dismiss);
}

let dialogRead = 0;

/** The session menu's "Run location", and the badge's click. */
export async function openRunLocationDialog(fid: string | null | undefined): Promise<void> {
  const target = fid || currentId.value;
  if (!target) return;
  const mode = "run-location:" + target;
  _modalMode.value = mode;
  const request = ++dialogRead;
  const generation = _openGen.value;
  const activeSession = currentId.value;
  const ownsDialog = () => _modalMode.value === mode && request === dialogRead
    && generation === _openGen.value && activeSession === currentId.value;
  const body = $("#modal-body");
  if (!body) return;
  const title = $("#modal-title");
  if (title) title.textContent = t("compute.dialog.title");
  const download = $("#modal-download");
  if (download) download.style.display = "none";
  body.innerHTML = "";
  body.appendChild(el("div", "rl-hint", t("compute.status.loading")));
  openModalEl($("#modal"));

  const [loaded, catalog] = await Promise.all([
    readComputeStatus(target),
    loadProfiles(),
  ]);
  // Read ownership also distinguishes two openings of the same session.
  if (!ownsDialog()) return;
  // The catalog may finish after a newer status read. Project the newest
  // active-session evidence rather than reviving this dialog's earlier read.
  const latest = target === currentId.value ? computeStatus.value : null;
  let result: ComputeResult | { phase: "loading" } = loaded;
  if (latest?.fid === target && latest.generation === generation) {
    result = latest.phase === "available" && latest.status
      ? { phase: "available", status: latest.status }
      : latest.phase === "error"
        ? { phase: "error", error: latest.error || t("compute.status.unavailable") }
        : { phase: "loading" };
  }
  const wrap = el("div", "run-location");
  const status = result.phase === "available" ? result.status : null;
  const current = status?.location;
  if (result.phase !== "available") {
    wrap.appendChild(el("div", "rl-error", t(result.phase === "loading"
      ? "compute.status.loading" : "compute.status.unavailable")));
    if (result.phase === "error") wrap.appendChild(el("div", "rl-hint", result.error));
    const previous = computeStatus.value;
    if (previous?.fid === target && previous.generation === generation && previous.status) {
      const last = previous.status.location === "cluster"
        ? previous.status.workload?.profile || t("compute.status.cluster") : t("compute.location.local");
      wrap.appendChild(el("div", "rl-hint", `${last} · ${t("compute.status.stale")}`));
    }
  }
  if (result.phase !== "available" || catalog.phase === "error") {
    const retry = el("button", "rl-retry", t("compute.status.retry"));
    retry.type = "button";
    retry.onclick = () => { void openRunLocationDialog(target); };
    wrap.appendChild(retry);
  }
  const choose = async (profile: string | null) => {
    try {
      if (profile === null) {
        await api(`/sessions/${encodeURIComponent(target)}/compute/release`, { method: "POST", body: "{}" });
      } else {
        await api(`/sessions/${encodeURIComponent(target)}/compute`, {
          method: "POST",
          body: JSON.stringify({ profile }),
        });
      }
      closeModalEl($("#modal"));
      await refreshComputeStatus(target);
    } catch (error) {
      // The 409 that means "this daemon has no listener" is the one a user
      // will actually hit, and its body already explains itself.
      hint(apiErrorText(error), true);
    }
  };

  const local = el("button", "rl-option" + (current === "local" ? " current" : ""));
  local.type = "button";
  local.disabled = result.phase !== "available";
  local.appendChild(el("div", "rl-name", t("compute.location.local")));
  local.appendChild(el("div", "rl-hint", t("compute.location.localHint")));
  local.onclick = () => {
    if (current === "local") closeModalEl($("#modal"));
    else void choose(null);
  };
  wrap.appendChild(local);

  const profiles = catalog.phase === "available" ? catalog.profiles : [];
  if (catalog.phase === "error") {
    wrap.appendChild(el("div", "rl-error", t("compute.profiles.unavailable")));
    wrap.appendChild(el("div", "rl-hint", catalog.error));
  }
  if (catalog.phase === "available" && !profiles.length) wrap.appendChild(el("div", "rl-empty", t("compute.profiles.empty")));
  profiles.forEach((profile) => {
    const chosen = current === "cluster" && status?.workload?.profile === profile.name;
    const option = el("button", "rl-option" + (chosen ? " current" : ""));
    option.type = "button";
    option.disabled = result.phase !== "available";
    option.appendChild(el("div", "rl-name", profile.name));
    const bits = [
      `${profile.cpus} CPU`,
      profile.gpus ? `${profile.gpus} GPU` : "",
      `${Math.round((profile.memory_mb || 0) / 1024)} GiB`,
      `${Math.round((profile.walltime_s || 0) / 3600)} h`,
    ].filter(Boolean).join(" · ");
    option.appendChild(el("div", "rl-hint", bits));
    option.onclick = () => {
      void choose(profile.name);
    };
    wrap.appendChild(option);
  });

  if (current === "cluster") {
    const release = el("button", "rl-release", t("compute.dialog.release"));
    release.type = "button";
    release.onclick = () => {
      void choose(null);
    };
    wrap.appendChild(release);
  }
  body.innerHTML = "";
  body.appendChild(wrap);
}

async function loadProfiles(): Promise<
  { phase: "available"; profiles: ComputeProfile[] } | { phase: "error"; error: string }
> {
  try {
    const catalog = await api("/orchestration/profiles");
    if (!isRecord(catalog) || !Array.isArray(catalog.profiles) || !catalog.profiles.every((profile) =>
      isRecord(profile) && typeof profile.name === "string" && profile.name.length > 0
      && ["cpus", "gpus", "memory_mb", "walltime_s"].every((key) =>
        profile[key] === undefined || (typeof profile[key] === "number" && Number.isFinite(profile[key]))))) {
      throw new Error(t("compute.profiles.invalid"));
    }
    return { phase: "available", profiles: catalog.profiles as ComputeProfile[] };
  } catch (error) {
    return { phase: "error", error: apiErrorText(error) };
  }
}
