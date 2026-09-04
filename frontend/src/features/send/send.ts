/**
 * Composer send chain. Port of app.js:7908-8178.
 *
 * Plan-mode payload goes through F-07 `planModePayload` (dictionary), not the
 * drifted Chinese literal. Admission id is minted HERE and stored BEFORE the
 * request goes out.
 */

import { planModePayload, t } from "../../i18n/runtime";
import {
  _environmentStatusRefreshFailed,
  defaultModelName,
  skillsCatalog,
  standardProfileReadiness,
} from "../../stores/customize";
import {
  _openGen,
  annotations,
  currentId,
  lastAnnotationReservation,
  project,
} from "../../stores/session";
import {
  exploreMode,
  planMode,
  planPending,
  planReady,
  planStatus,
  running,
} from "../../stores/stream";
import { $, el } from "../messages/dom";
import { down } from "../messages/scroll";
import { runtimeSummary } from "../notebook/kernel";
import { api, apiErrorText } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { enableComposer, grow } from "../sessions/dom";
import { loadSessions } from "../sessions/load";
import { renderComposerRefChips } from "../sessions/transcript";
import { sub } from "../ws/connect";
import {
  admissionSettled,
  forgetAdmission,
  rememberAdmission,
} from "./admission";
import {
  isEnvironmentReadinessError,
  refreshEnvironmentStatus,
  renderEnvironmentReadinessBanner,
  unavailableReadinessSnapshot,
} from "./environment";
import { callLane, setCancelHidden } from "./host";
import { iconEl } from "./icon";
import {
  acceptTurnTicket,
  openTurnTicket,
  ownsTurnTicket,
  resumeWatch,
  retireTurnTicket,
} from "./ticket";
import { turnDone } from "./turn";

type Annotation = {
  id?: string;
  annotation_id?: string;
  number?: unknown;
  artifact_name?: string;
  body?: string;
  status?: string;
};

function annotationId(an: Annotation | null | undefined): string {
  return String((an && (an.id || an.annotation_id)) || "");
}

function openAnnotations(): Annotation[] {
  const fn = callLane("openAnnotations");
  if (Array.isArray(fn)) return fn as Annotation[];
  return ((annotations.value || []) as Annotation[]).filter((x) => x.status === "open");
}

function setLocalAnnotationStatus(ids: string[], status: string): void {
  const wanted = new Set((ids || []).filter(Boolean));
  if (!wanted.size) return;
  annotations.value = ((annotations.value || []) as Annotation[]).map((an) =>
    wanted.has(annotationId(an)) ? { ...an, status } : an,
  );
}

async function loadAnnotationsLocal(fid: string): Promise<boolean> {
  const via = callLane("loadAnnotations", fid);
  if (via && typeof (via as Promise<unknown>).then === "function") {
    return !!(await (via as Promise<unknown>));
  }
  let res: { annotations?: Annotation[] } | null = null;
  try {
    res = (await api(`/frames/${fid}/annotations`)) as { annotations?: Annotation[] };
  } catch {
    return false;
  }
  if (fid !== currentId.value) return true;
  annotations.value = (res && res.annotations) || [];
  callLane("updateAnnotBadge");
  return true;
}

async function loadSkillsCatalog(): Promise<Array<{ name?: unknown }>> {
  if (skillsCatalog.value) return skillsCatalog.value as Array<{ name?: unknown }>;
  try {
    const d = (await api("/skills/catalog")) as { skills?: Array<{ name?: unknown }> };
    skillsCatalog.value = (d && d.skills) || [];
  } catch {
    skillsCatalog.value = [];
  }
  return (skillsCatalog.value as Array<{ name?: unknown }>) || [];
}

export function annotAttachment(anns: Annotation[]): HTMLElement {
  const box = el("div", "annot-attach");
  box.appendChild(iconEl("message-square", 13));
  box.appendChild(el("span", "annot-attach-t", t("annot.attachCount", anns.length)));
  const list = el("div", "annot-attach-list");
  anns.forEach((an) => {
    const r = el("div", "annot-attach-row");
    r.appendChild(el("span", "annot-attach-pin", String(an.number)));
    r.appendChild(el("span", "annot-attach-file", an.artifact_name || "artifact"));
    r.appendChild(el("span", "annot-attach-body", "· " + (an.body || "")));
    list.appendChild(r);
  });
  box.appendChild(list);
  return box;
}

function mintAdmissionId(): string {
  const bytes = new Uint8Array(16);
  const cryptoObj =
    (globalThis as { crypto?: Crypto }).crypto ||
    (typeof window !== "undefined" ? window.crypto : undefined);
  if (!cryptoObj || !cryptoObj.getRandomValues) {
    return "resv-" + Date.now().toString(16).padStart(32, "0");
  }
  cryptoObj.getRandomValues(bytes);
  return "resv-" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function send(text?: string | null, opts?: { execute?: boolean }): Promise<void> {
  text = (text || "").trim();
  opts = opts || {};
  const queueing = running.value;
  const runtime = runtimeSummary();
  if (currentId.value && runtime.viewOnly && runtime.trustState === "quarantined") {
    hint(t("runtime.quarantineHint"), true);
    return;
  }
  const anns = openAnnotations();
  if (!text && !anns.length) return;
  const planNow = planMode.value && !opts.execute;
  const exploreNow = exploreMode.value && !planNow && !opts.execute;
  let skillDirective = "";
  const skillCandidates: string[] = [];
  if (!planNow) {
    text.replace(/(^|\s)\/([A-Za-z0-9][\w:-]*)/g, (m, _p, nm: string) => {
      if (!skillCandidates.includes(nm)) skillCandidates.push(nm);
      return m;
    });
  }
  if (skillCandidates.length) {
    try {
      const cat = await loadSkillsCatalog();
      const names = new Set((cat || []).map((s) => String(s.name).toLowerCase()));
      const hits = skillCandidates.filter((nm) => names.has(nm.toLowerCase()));
      if (hits.length) {
        skillDirective = "\n\n" + hits.map((n) => t("skill.invokeDirective", n)).join("\n");
      }
    } catch {
      /* catalog is advisory */
    }
  }
  if (!currentId.value) {
    const f = (await api("/frames", {
      method: "POST",
      body: JSON.stringify({
        project_id: project.value || undefined,
        model: defaultModelName.value,
      }),
    })) as { id: string };
    currentId.value = f.id;
    sub(f.id);
    await loadSessions();
  }
  const g = $(".generated");
  if (g) g.remove();
  const es = $(".empty-session");
  if (es) es.remove();
  const w = el("div", "msg user");
  const b = el("div", "bubble");
  b.textContent = text || t("send.imageAnnotationFallback");
  w.appendChild(b);
  if (anns.length) w.appendChild(annotAttachment(anns));
  if (queueing) w.classList.add("queued");
  const host = $("#messages");
  if (host) host.appendChild(w);
  down(true);
  let payload = text;
  if (planNow) {
    const oldCard = $("#plan-card-live");
    if (oldCard) oldCard.remove();
    planReady.value = null;
    planStatus.value = null;
    payload = planModePayload(text);
    planPending.value = true;
  }
  if (skillDirective) payload += skillDirective;
  const sawRunningAtDispatch = running.value;
  const turnTicketToken = sawRunningAtDispatch ? null : openTurnTicket();
  if (!turnTicketToken) hint(t("queue.accepted"));
  else {
    running.value = true;
    enableComposer(false);
    setCancelHidden(false);
    hint(t("toast.running"), false, true);
  }
  const composer = $("#composer") as HTMLTextAreaElement | null;
  if (composer) composer.value = "";
  grow();
  renderComposerRefChips();
  const annIds = anns.map((x) => annotationId(x)).filter(Boolean);
  let admissionId = "";
  if (annIds.length && currentId.value) {
    admissionId = mintAdmissionId();
    rememberAdmission(currentId.value, admissionId);
    setLocalAnnotationStatus(annIds, "pending");
    callLane("refreshAllStages");
    callLane("updateAnnotBadge");
  }
  if (currentId.value) sub(currentId.value);
  try {
    const accepted = (await api(`/frames/${currentId.value}/message`, {
      method: "POST",
      body: JSON.stringify({
        input_data: { request: payload },
        plan: planNow,
        explore: exploreNow,
        annotation_ids: annIds,
        annotation_reservation_id: admissionId || undefined,
        wait: false,
      }),
    })) as {
      execution_id?: unknown;
      request_id?: unknown;
      queue_position?: unknown;
      annotations?: unknown;
      annotation_reservation_id?: unknown;
    };
    if (accepted && accepted.execution_id) w.dataset.executionId = String(accepted.execution_id);
    if (!acceptTurnTicket(turnTicketToken, accepted)) retireTurnTicket(turnTicketToken);
    if (annIds.length) {
      const said = accepted && accepted.annotations;
      if (said === "none") setLocalAnnotationStatus(annIds, "open");
      else if (said === "sent") setLocalAnnotationStatus(annIds, "sent");
      if (accepted && accepted.annotation_reservation_id) {
        lastAnnotationReservation.value = accepted.annotation_reservation_id;
      }
      if (admissionId && currentId.value && admissionSettled(said)) {
        forgetAdmission(currentId.value, admissionId);
      }
      try {
        if (currentId.value) await loadAnnotationsLocal(currentId.value);
      } catch {
        /* reload is best-effort */
      }
      callLane("refreshAllStages");
      callLane("updateAnnotBadge");
    }
  } catch (e) {
    if (annIds.length && currentId.value) {
      const refused = !!(e && Number.isInteger((e as { status?: number }).status) && (e as { status: number }).status >= 400);
      if (admissionId && refused) forgetAdmission(currentId.value, admissionId);
      const reloaded = await loadAnnotationsLocal(currentId.value);
      if (!reloaded) setLocalAnnotationStatus(annIds, refused ? "open" : "pending");
      callLane("refreshAllStages");
      callLane("updateAnnotBadge");
    }
    if (isEnvironmentReadinessError(e)) {
      await refreshEnvironmentStatus();
      if (_environmentStatusRefreshFailed.value) {
        standardProfileReadiness.value = unavailableReadinessSnapshot();
        renderEnvironmentReadinessBanner();
      }
      const box = $("#composer") as HTMLTextAreaElement | null;
      if (box && !box.value.trim()) box.value = text;
      if (box) {
        grow();
        renderComposerRefChips();
      }
      w.remove();
      if (ownsTurnTicket(turnTicketToken)) turnDone("failed");
      callLane("openCust", "compute");
      hint(t("environment.readiness.sendBlocked"), true);
      void loadSessions();
      return;
    }
    const err = e as { code?: string };
    if (err && (err.code === "model_revision_unavailable" || err.code === "model_revision_ambiguous")) {
      const ask =
        typeof globalThis.confirm === "function" ? globalThis.confirm(t("model.rebind.confirm")) : false;
      if (ask) {
        try {
          await api(`/frames/${encodeURIComponent(String(currentId.value))}/model-binding`, {
            method: "POST",
          });
          hint(t("model.rebind.done"));
          if (ownsTurnTicket(turnTicketToken)) turnDone("failed");
          void loadSessions();
          return;
        } catch (rebindError) {
          hint(apiErrorText(rebindError), true);
        }
      }
    }
    hint(t("toast.sendFailed", apiErrorText(e)), true);
    if (ownsTurnTicket(turnTicketToken)) turnDone("failed");
    else w.classList.add("cancelled");
    void loadSessions();
    return;
  }
  if (currentId.value) resumeWatch(currentId.value, _openGen.value);
  void loadSessions();
}

type ComposerDispatch = (text: string) => unknown;

/**
 * Bind the composer keydown and the two mode toggles. `main.tsx` calls this
 * right after `render(<App/>)`, the same post-render slot as `bootChrome()`:
 * `installSend()` runs at module import, before Shell has rendered
 * `#composer`, so it stays DOM-free (the F-17 `bootArtifacts` /
 * `finishArtifactsBoot` split). Idempotent through the `data-send-bound`
 * markers; the Enter handler is delegated on the document root so a
 * re-created textarea needs no rebind. `dispatch` is a seam for tests;
 * production dispatches `send`.
 */
export function bindComposer(dispatch: ComposerDispatch = send): void {
  if (typeof document === "undefined") return;
  const planToggle = document.getElementById("plan-toggle");
  if (planToggle && !planToggle.dataset.sendBound) {
    planToggle.dataset.sendBound = "1";
    planToggle.onclick = () => {
      planMode.value = !planMode.value;
      if (planMode.value) {
        exploreMode.value = false;
        document.getElementById("explore-toggle")?.classList.remove("on");
      }
      planToggle.classList.toggle("on", planMode.value);
      hint(planMode.value ? t("plan.toggle.on") : "");
    };
  }
  const exploreToggle = document.getElementById("explore-toggle");
  if (exploreToggle && !exploreToggle.dataset.sendBound) {
    exploreToggle.dataset.sendBound = "1";
    exploreToggle.onclick = () => {
      exploreMode.value = !exploreMode.value;
      if (exploreMode.value) {
        planMode.value = false;
        document.getElementById("plan-toggle")?.classList.remove("on");
      }
      exploreToggle.classList.toggle("on", exploreMode.value);
      hint(exploreMode.value ? t("explore.toggle.on") : "");
    };
  }
  // Delegated on the document root, not on the node: a re-created #composer
  // (a keyed or conditional subtree, a second render()) keeps its Enter
  // handler with nothing to rebind. Bubble phase, so the autocomplete's
  // capture listener on the node still shields it with
  // stopImmediatePropagation while ac.open.
  const root = document.documentElement;
  if (root && !root.dataset.sendBound) {
    root.dataset.sendBound = "1";
    // One dispatch at a time. send() clears the composer only after its first
    // awaits (POST /frames on a fresh session, the skills catalog for a /skill
    // token), so a held or double Enter inside that window would create a
    // second session and send the same text twice.
    let inFlight: Promise<unknown> | null = null;
    root.addEventListener("keydown", (e) => {
      const c = e.target as HTMLTextAreaElement | null;
      if (!c || c.id !== "composer") return;
      if (e.isComposing || e.keyCode === 229) return;
      const ac = (globalThis as { ac?: { open?: boolean } }).ac;
      if (ac && ac.open) return;
      if (e.key !== "Enter" || e.shiftKey) return;
      e.preventDefault();
      if (inFlight) return;
      const pending = Promise.resolve(dispatch(c.value));
      inFlight = pending;
      void pending.finally(() => {
        if (inFlight === pending) inFlight = null;
      });
    });
  }
}
