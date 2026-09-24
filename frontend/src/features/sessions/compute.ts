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

import { t } from "../../i18n";
import { _computeLostSeen } from "../../stores/artifacts";
import { currentId } from "../../stores/session";
import { computeStatus } from "../../stores/timeline";
import { _modalMode } from "../../stores/ui";
import { api, apiErrorText } from "./api";
import { hint } from "./chrome";
import { $, closeModalEl, el, openModalEl } from "./dom";
import { iconEl } from "./icon";

export type ComputeStatus = {
  location?: string;
  readiness?: { ready?: boolean; blocked_on?: string };
  allocation?: { allocation_id?: string; phase?: string };
  workload?: { profile?: string; phase?: string; reason?: string };
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

export async function loadComputeStatus(fid: string | null | undefined): Promise<ComputeStatus | null> {
  if (!fid) return null;
  try {
    return (await api(`/sessions/${encodeURIComponent(fid)}/compute`)) as ComputeStatus;
  } catch {
    return null; // a daemon without the feature is not an error state
  }
}

/** Read the session's run location and repaint its badge and banner. Never rejects. */
export async function refreshComputeStatus(fid: string | null | undefined): Promise<ComputeStatus | null> {
  const status = await loadComputeStatus(fid);
  if (!fid || fid !== currentId.value) return status; // session switched mid-flight
  computeStatus.value = status;
  renderComputeBadge();
  renderComputeLostBanner();
  return status;
}

function renderComputeBadge(): void {
  const host = $(".conv-head-actions");
  if (!host) return;
  let badge = $("#compute-badge");
  const status = computeStatus.value as ComputeStatus | null;
  // A local session gets no badge at all: a chip reading "local" on every
  // session of an install with no cluster is pure noise.
  if (!status || status.location !== "cluster") {
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
  const status = (computeStatus.value || {}) as ComputeStatus;
  const epochs = status.state_lost_epochs || [];
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

/** The session menu's "Run location", and the badge's click. */
export async function openRunLocationDialog(fid: string | null | undefined): Promise<void> {
  const target = fid || currentId.value;
  if (!target) return;
  const mode = "run-location:" + target;
  _modalMode.value = mode;
  const [status, catalog] = await Promise.all([
    loadComputeStatus(target),
    api("/orchestration/profiles").catch(() => ({ profiles: [] })) as Promise<{ profiles?: ComputeProfile[] }>,
  ]);
  // Another modal opened while this one was loading owns the modal now.
  if (_modalMode.value !== mode) return;
  const wrap = el("div", "run-location");
  const current = (status && status.location) || "local";
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
  local.appendChild(el("div", "rl-name", t("compute.location.local")));
  local.appendChild(el("div", "rl-hint", t("compute.location.localHint")));
  local.onclick = () => {
    if (current === "local") closeModalEl($("#modal"));
    else void choose(null);
  };
  wrap.appendChild(local);

  const profiles = (catalog && catalog.profiles) || [];
  if (!profiles.length) wrap.appendChild(el("div", "rl-empty", t("compute.dialog.notConfigured")));
  profiles.forEach((profile) => {
    const chosen = current === "cluster" && status?.workload?.profile === profile.name;
    const option = el("button", "rl-option" + (chosen ? " current" : ""));
    option.type = "button";
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
  const title = $("#modal-title");
  if (title) title.textContent = t("compute.dialog.title");
  const download = $("#modal-download");
  if (download) download.style.display = "none";
  const body = $("#modal-body");
  if (!body) return;
  body.innerHTML = "";
  body.appendChild(wrap);
  openModalEl($("#modal"));
}
