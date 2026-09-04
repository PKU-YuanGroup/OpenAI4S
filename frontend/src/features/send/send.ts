/**
 * Composer send chain. Port of app.js:7954-8317.
 *
 * Plan-mode payload goes through F-07 `planModePayload` (dictionary), not the
 * drifted Chinese literal. Admission id is minted HERE and stored BEFORE the
 * request goes out.
 *
 * The dispatch discipline below is the shipped app.js one: one preparation
 * owner per (frameId, projectId, openGen); a draft that belongs to the
 * composer that started this call; the shared first-session creation promise
 * that Attach and Send both adopt; and an upload barrier taken as the LAST
 * await before the POST. After that barrier every use of the frame id is the
 * pinned dispatch id, never whatever `currentId` happens to hold.
 */

import { planModePayload, t } from "../../i18n/runtime";
import {
  _environmentStatusRefreshFailed,
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
import {
  UPLOAD_STATE,
  UPLOAD_WAIT_LIMIT_MS,
  createUploadSession,
  pendingUploadsFor,
  uploadFailureMatches,
  waitForPendingUploads,
  type UploadResult,
} from "../chrome/upload";
import { effProject } from "../customize/host";
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

/**
 * The single preparation owner. app.js keeps this on `S._sendPreparing`; here
 * it is module state because nothing outside this chain may read or clear it.
 */
type SendPreparation = {
  frameId: string | null;
  projectId: string | null;
  openGen: number;
};

let sendPreparing: SendPreparation | null = null;

/** Barrier answer: `waitForPendingUploads`, or the stall timer's refusal. */
type UploadBarrierResult = {
  ok: boolean;
  frameId: string | null;
  failures: UploadResult[];
  stalled?: boolean;
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
    // app.js throws here rather than mint a non-CSPRNG id: 128 bits from the
    // platform CSPRNG is the requirement, because this keys a claim on the
    // user's own unpublished comments and has to survive collision across
    // sessions and restarts. A timestamp is neither random nor unique -- two
    // tabs in the same millisecond produce the same one.
    throw new Error("admission id requires crypto.getRandomValues");
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
  const composerAtStart = $("#composer") as HTMLTextAreaElement | null;
  const composerDraft = composerAtStart ? composerAtStart.value : "";
  const sourceFrameId = currentId.value || null;
  const sourceProjectId = effProject() || project.value || null;
  const sourceOpenGen = _openGen.value || 0;
  // One preparation owner. Repeated Enter while FileReader is still working
  // must not wake two identical sends when the same upload promise settles.
  const activePreparation = sendPreparing;
  if (
    activePreparation &&
    activePreparation.frameId === sourceFrameId &&
    activePreparation.projectId === sourceProjectId &&
    activePreparation.openGen === sourceOpenGen
  ) {
    // Say why. Returning silently here made a slow (or stuck) upload look like
    // a dead composer: Enter did nothing and nothing explained it.
    hint(t("upload.pendingSend"), false, true);
    return;
  }
  const preparation: SendPreparation = {
    frameId: sourceFrameId,
    projectId: sourceProjectId,
    openGen: sourceOpenGen,
  };
  sendPreparing = preparation;
  const sendProjectId = sourceProjectId;
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
  // The full catalog includes lazy collection members, so fetching it can be
  // noticeable on a cold send. Ordinary prose has nothing to resolve here.
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
  let dispatchFrameId = sourceFrameId;
  let dispatchCreation: Promise<string> | null = null;
  let dispatchOpenGen = sourceOpenGen;
  try {
    // Catalog preflight can be cold. A draft and its pinned annotations belong
    // to the composer that started this function, not whichever session is
    // visible after that await (including an A→B→A same-id ABA switch, which
    // is the whole reason the generation is captured alongside the id).
    if (
      currentId.value !== sourceFrameId ||
      (_openGen.value || 0) !== sourceOpenGen ||
      (effProject() || project.value || null) !== sourceProjectId
    ) {
      return;
    }
    // Upload and send share the same first-session creation promise. Without
    // that single flight, selecting a file and pressing Enter can create two
    // frames and bind the bytes and message to different workspaces.
    if (!dispatchFrameId) {
      // A failed initial upload has no frame to bind to because creating that
      // frame may itself have failed. Keep it latched to the empty composer so
      // a second Enter cannot silently create a clean frame and ask the agent
      // to list files that never arrived. Selecting files again clears/replaces
      // this failure.
      const priorFailure = [...UPLOAD_STATE.failures].find((failure) =>
        uploadFailureMatches(failure, null, sendProjectId),
      );
      if (priorFailure) {
        const failed = priorFailure.results && priorFailure.results[0];
        hint(t("upload.failed", apiErrorText(failed && failed.error)), true);
        return;
      }
      dispatchCreation = createUploadSession(sendProjectId);
      dispatchFrameId = await dispatchCreation;
    }
    dispatchOpenGen = _openGen.value || 0;
    // This is the LAST await before the message POST. A FileReader/upload
    // batch that starts during any earlier preflight is therefore included;
    // after this barrier JavaScript runs synchronously through fetch().
    // Announce the wait before taking it. This barrier can span a real upload,
    // and an unexplained pause is indistinguishable from a broken Send.
    if (pendingUploadsFor(dispatchFrameId, sendProjectId, dispatchCreation).length) {
      hint(t("upload.pendingSend"), false, true);
    }
    let stallTimer: ReturnType<typeof setTimeout> | undefined;
    const uploadReady = await Promise.race<UploadBarrierResult>([
      waitForPendingUploads(dispatchFrameId, sendProjectId, dispatchCreation),
      // A hung /uploads must not pin the composer forever. Refusing rather
      // than proceeding keeps the barrier's guarantee -- bytes never silently
      // trail the turn that talks about them -- while `finally` below releases
      // the preparation latch so the next Enter is live again.
      new Promise<UploadBarrierResult>((resolve) => {
        stallTimer = setTimeout(
          () => resolve({ ok: false, stalled: true, frameId: dispatchFrameId, failures: [] }),
          UPLOAD_WAIT_LIMIT_MS,
        );
      }),
    ]);
    clearTimeout(stallTimer);
    if (uploadReady.stalled) {
      hint(t("upload.stalled"), true);
      return;
    }
    if (!uploadReady.ok) {
      const failure = uploadReady.failures[0];
      hint(t("upload.failed", apiErrorText(failure && failure.error)), true);
      // Reported once, then consumed. The refusal exists so this Enter cannot
      // ask the agent about bytes that never arrived -- it was never meant to
      // outlive the warning. Left latched, every later Enter in this
      // conversation returned here, so abandoning the attachment cost the user
      // their composer for the rest of the session.
      [...UPLOAD_STATE.failures].forEach((previous) => {
        if (uploadFailureMatches(previous, dispatchFrameId, sendProjectId)) {
          UPLOAD_STATE.failures.delete(previous);
        }
      });
      return;
    }
    // Navigation while preparation was in flight changes who owns the
    // composer. Never send the old draft into the newly opened conversation.
    if (
      !dispatchFrameId ||
      currentId.value !== dispatchFrameId ||
      (_openGen.value || 0) !== dispatchOpenGen
    ) {
      return;
    }
  } catch (error) {
    hint(t("toast.sendFailed", apiErrorText(error)), true);
    return;
  } finally {
    if (sendPreparing === preparation) sendPreparing = null;
  }
  // Unreachable: the guard closing the barrier above already returned when
  // there is no dispatch frame. It is here so the pinned id is a `string` for
  // every use below, where reading `currentId` again is the bug being fixed.
  if (!dispatchFrameId) return;
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
  // Read AFTER every await above and BEFORE this send touches `running`. The
  // snapshot at the top of `send` is only good enough to decide how the bubble
  // looks: the skills catalogue and, on a first message, creating the frame
  // all await, and another tab or a recovered turn can take ownership in that
  // window.
  const sawRunningAtDispatch = running.value;
  const turnTicketToken = sawRunningAtDispatch ? null : openTurnTicket();
  if (!turnTicketToken) hint(t("queue.accepted"));
  else {
    running.value = true;
    enableComposer(false);
    setCancelHidden(false);
    hint(t("toast.running"), false, true);
  }
  // The textarea remains editable while FileReader/upload is pending. Clear
  // only the draft that this invocation captured; text typed during that wait
  // belongs to the next message and must survive.
  const composer = $("#composer") as HTMLTextAreaElement | null;
  if (composer && composer.value === composerDraft) composer.value = "";
  grow();
  renderComposerRefChips();
  const annIds = anns.map((x) => annotationId(x)).filter(Boolean);
  let admissionId = "";
  if (annIds.length) {
    admissionId = mintAdmissionId();
    rememberAdmission(dispatchFrameId, admissionId);
    setLocalAnnotationStatus(annIds, "pending");
    callLane("refreshAllStages");
    callLane("updateAnnotBadge");
  }
  // Guarantee this client is subscribed BEFORE the POST spawns the turn
  // thread, on the pinned id the POST is about.
  sub(dispatchFrameId);
  try {
    const accepted = (await api(`/frames/${dispatchFrameId}/message`, {
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
      if (admissionId && admissionSettled(said)) {
        forgetAdmission(dispatchFrameId, admissionId);
      }
      try {
        await loadAnnotationsLocal(dispatchFrameId);
      } catch {
        /* reload is best-effort */
      }
      callLane("refreshAllStages");
      callLane("updateAnnotBadge");
    }
  } catch (e) {
    if (annIds.length) {
      const refused = !!(e && Number.isInteger((e as { status?: number }).status) && (e as { status: number }).status >= 400);
      if (admissionId && refused) forgetAdmission(dispatchFrameId, admissionId);
      const reloaded = await loadAnnotationsLocal(dispatchFrameId);
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
          await api(`/frames/${encodeURIComponent(dispatchFrameId)}/model-binding`, {
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
  // The async POST returns as soon as the job is accepted. Keep the composer
  // locked until the authoritative WebSocket frame_update arrives; the status
  // watchdog covers a missed terminal event after reconnects.
  if (currentId.value === dispatchFrameId && (_openGen.value || 0) === dispatchOpenGen) {
    resumeWatch(dispatchFrameId, dispatchOpenGen);
  }
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
      if (inFlight) {
        // Dropping the keystroke is right -- one dispatch at a time -- but
        // dropping it SILENTLY is the "dead composer" this branch's own
        // preparation latch exists to explain. `send()`'s hint can never fire
        // from here because the dispatch it guards never happens, so say the
        // same thing at the point that actually swallowed the Enter, and only
        // when a pending upload is the reason.
        if (pendingUploadsFor(currentId.value || null, effProject() || project.value || null, null).length) {
          hint(t("upload.pendingSend"), false, true);
        }
        return;
      }
      const pending = Promise.resolve(dispatch(c.value));
      inFlight = pending;
      void pending.finally(() => {
        if (inFlight === pending) inFlight = null;
      });
    });
  }
}
