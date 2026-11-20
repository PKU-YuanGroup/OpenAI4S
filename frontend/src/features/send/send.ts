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

import { effect } from "@preact/signals";
import { LANG, onLanguageChange, planModePayload, t } from "../../i18n/runtime";
import {
  _environmentStatusRefreshFailed,
  standardProfileReadiness,
} from "../../stores/customize";
import {
  _openGen,
  beginHistorySubmission,
  annotations,
  currentId,
  lastAnnotationReservation,
  project,
} from "../../stores/session";
import {
  exploreMode,
  planMode,
  planPending,
  planPendingTurn,
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
  type UploadCreation,
  type UploadResult,
} from "../chrome/upload";
import { restoreAttachments, takeAttachments, type ComposerAttachment } from "../chrome/attachments";
import { clearDraft } from "../chrome/drafts";
import { askConfirm } from "../chrome/dialog";
import { loadSkillsCatalog } from "../autocomplete/catalog";
import { acHoldSend, acPending } from "../autocomplete/composer";
import {
  clearMentions,
  composerMentions,
  mentionsPayload,
  restoreMentions,
  stripRefVersions,
  type ComposerMention,
} from "../autocomplete/mentions";
import { effProject } from "../customize/host";
import { composerChoiceMark, composerModelChoicePending, modelT, noteAdmittedModelBinding, noteSessionModelBinding } from "../customize/models";
import { $, el, keepStripsLast } from "../messages/dom";
import { continueMarker, isContinuePrompt } from "../messages/failure";
import { down } from "../messages/scroll";
import { runtimeSummary } from "../notebook/kernel";
import { api, apiErrorText } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { enableComposer, grow } from "../sessions/dom";
import { loadSessions } from "../sessions/load";
import { renderProjectRefChips, stripProjectRefSyntax } from "../messages/list";
import { renderSentAttachments } from "../messages/attachments";
import { renderComposerRefChips, renderMessageRefChips } from "../sessions/transcript";
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
import { bindBackgroundCells } from "./background";
import { bindTurnProgress } from "./progress";
import { bindTurnStatus } from "../messages/turnStatus";
import {
  acceptTurnTicket,
  openTurnTicket,
  ownsTurnTicket,
  resumeWatch,
  retireTurnTicket,
} from "./ticket";
import { turnDone } from "./turn";
import { sendCopy } from "./copy";

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

/**
 * This send's correlation id. It only has to tell this tab's bubbles apart
 * (the server bounds and sanitises it), so a non-CSPRNG fallback is enough
 * where the platform has none -- unlike the admission id above.
 */
export function newRequestId(): string {
  const bytes = new Uint8Array(12);
  const cryptoObj = (globalThis as { crypto?: Crypto }).crypto;
  if (cryptoObj && cryptoObj.getRandomValues) cryptoObj.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
  return "web-" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

/** How long an unanswered POST /message keeps its optimistic bubble protected. */
export const SUBMISSION_GRACE_MS = 5000;

export async function send(text?: string | null, opts?: { execute?: boolean }): Promise<void> {
  text = (text || "").trim();
  opts = opts || {};
  if (composerModelChoicePending()) {
    hint(modelT("model.session.pending"), false, true);
    return;
  }
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
  // The `@` pins this draft carries, read with the draft (ART-16). Only the
  // composer's own text takes them: a programmatic send has its own words.
  const sentMentions = composerDraft.trim() === text ? composerMentions(text) : [];
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
  let dispatchCreation: UploadCreation | null = null;
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
      // frame may itself have failed. Refuse THIS Enter so it cannot silently
      // create a clean frame and ask the agent to list files that never
      // arrived -- then consume the failure, the same policy as the bound path
      // below. Left latched, every later Enter in this project's empty
      // composer (plain text included) was refused with the stale upload
      // error until the user re-attached.
      const priorFailure = [...UPLOAD_STATE.failures].find((failure) =>
        uploadFailureMatches(failure, null, sendProjectId),
      );
      if (priorFailure) {
        const failed = priorFailure.results && priorFailure.results[0];
        hint(t("upload.failed", apiErrorText(failed && failed.error)), true);
        [...UPLOAD_STATE.failures].forEach((previous) => {
          if (uploadFailureMatches(previous, null, sendProjectId)) {
            UPLOAD_STATE.failures.delete(previous);
          }
        });
        return;
      }
      dispatchCreation = createUploadSession(sendProjectId);
      dispatchFrameId = await dispatchCreation;
      // The shared creation adopts the frame it created and opens it
      // (loadSessions + openConversation) AFTER publishing the id. Dispatching
      // before that finished let openConversation's reset -- closeTurnTicket,
      // enableComposer(true), a hidden Stop, a wiped #messages and a bumped
      // _openGen -- land in the middle of the turn this send had just
      // started; with an attachment in flight the bump made the guard below
      // drop the message silently. Wait for the opening, so the generation
      // captured next is the one the conversation will keep.
      await dispatchCreation.opened;
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
  // A choice may have started during the upload/catalog/session preflight.
  // Keep the draft and annotations until that configuration is saved.
  if (composerModelChoicePending()) {
    hint(modelT("model.session.pending"), false, true);
    return;
  }
  // Mint the admission id before this send changes any state: minting can
  // refuse (no platform CSPRNG), and refusing after the draft was cleared and
  // the turn locked would strand the composer behind a turn never posted.
  const annIds = anns.map((x) => annotationId(x)).filter(Boolean);
  let admissionId = "";
  if (annIds.length) {
    try {
      admissionId = mintAdmissionId();
    } catch (error) {
      hint(t("toast.sendFailed", apiErrorText(error)), true);
      return;
    }
  }
  const es = $(".empty-session");
  if (es) es.remove();
  const confirmHistorySubmission = beginHistorySubmission();
  // Sent as `X-Request-Id`: the turn's `user_message` event carries it back,
  // so this tab recognises its own bubble instead of showing the question twice.
  const requestId = newRequestId();
  const w = el("div", "msg user");
  w.dataset.requestId = requestId;
  const b = el("div", "bubble");
  // As a reload shows it: a typed `@name#v-<id>` reads as `@name`.
  b.textContent = stripProjectRefSyntax(stripRefVersions(text)) || t("send.imageAnnotationFallback");
  w.appendChild(b);
  if (isContinuePrompt(text)) {
    // Sent by the Continue button: the platform's line, not the user's words.
    w.classList.add("continue-seed");
    b.remove();
    w.appendChild(continueMarker());
  }
  // The same chips a reload draws from the stored `artifact_refs`.
  if (sentMentions.length) {
    renderMessageRefChips(
      w,
      sentMentions.map((m) => ({ display_name: m.filename, version_id: m.version_id, artifact_id: m.artifact_id })),
    );
  }
  // A project-folder file the `@` menu named, as the reload shows it.
  renderProjectRefChips(w, text);
  if (anns.length) w.appendChild(annotAttachment(anns));
  if (queueing) {
    w.classList.add("queued");
    // Says so in words: a slightly faded bubble read as delivered. Shown only
    // while the row is `.queued`, which its turn's `user_message` removes.
    w.appendChild(el("span", "msg-queued", sendCopy("queuedBadge")));
  }
  const host = $("#messages");
  if (host) host.appendChild(w);
  // The session's file strips stay last, under the new question.
  keepStripsLast(host);
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
  // Which turn the plan flag waits on: queued behind a running turn, the end
  // of that turn is not this one's (turn.ts `endedAnotherTurn`).
  const planTurn = planNow ? { queued: sawRunningAtDispatch, executionId: null as string | null } : null;
  if (planTurn) planPendingTurn.value = planTurn;
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
  // belongs to the next message and must survive. And only when that draft is
  // what this call sends: a programmatic send (a permission's Continue, a plan
  // approval) carries its own text, and the user's unrelated draft is theirs.
  const composer = $("#composer") as HTMLTextAreaElement | null;
  // The files the composer showed as going with this message leave its tray
  // with it (M-10), and come back with the text if the message is refused.
  let sentAttachments: ComposerAttachment[] = [];
  if (composer && composer.value === composerDraft && composerDraft.trim() === text) {
    composer.value = "";
    clearMentions();
    sentAttachments = takeAttachments(dispatchFrameId);
    clearDraft(dispatchFrameId);
  }
  // ...and stay under its bubble, as a reload shows them (the stored row's
  // `attachments`).
  const sentFiles = sentAttachments
    .filter((a) => a.state === "ready" && a.artifactId)
    .map((a) => ({ artifact_id: a.artifactId, name: a.name }));
  renderSentAttachments(w, sentFiles);
  grow();
  renderComposerRefChips();
  if (annIds.length) {
    rememberAdmission(dispatchFrameId, admissionId);
    setLocalAnnotationStatus(annIds, "pending");
    callLane("refreshAllStages");
    callLane("updateAnnotBadge");
  }
  // Guarantee this client is subscribed BEFORE the POST spawns the turn
  // thread, on the pinned id the POST is about.
  sub(dispatchFrameId);
  // Taken before the POST: a model chosen while it is out is newer than its 202.
  const choiceMark = composerChoiceMark();
  try {
    const accepted = (await api(`/frames/${dispatchFrameId}/message`, {
      method: "POST",
      headers: { "content-type": "application/json", "X-Request-Id": requestId },
      body: JSON.stringify({
        input_data: { request: payload },
        plan: planNow,
        explore: exploreNow,
        annotation_ids: annIds,
        annotation_reservation_id: admissionId || undefined,
        // `{filename, version_id}` per `@` pin; the text carries the names only.
        mentions: sentMentions.length ? mentionsPayload(sentMentions) : undefined,
        attachments: sentFiles.length ? sentFiles : undefined,
        wait: false,
      }),
    })) as {
      execution_id?: unknown;
      request_id?: unknown;
      queue_position?: unknown;
      annotations?: unknown;
      annotation_reservation_id?: unknown;
      model_binding?: unknown;
    };
    if (accepted?.request_id) confirmHistorySubmission();
    // Admission is what pins a fresh session (its first send binds the
    // default), and the 202 names the pair: the composer shows it rather than
    // keep calling the session unpinned -- i.e. whatever the default is later.
    if (accepted?.model_binding) noteAdmittedModelBinding(dispatchFrameId, accepted.model_binding, choiceMark);
    if (accepted && accepted.execution_id) w.dataset.executionId = String(accepted.execution_id);
    if (planTurn && planPendingTurn.value === planTurn && accepted && accepted.execution_id) {
      planPendingTurn.value = { ...planTurn, executionId: String(accepted.execution_id) };
    }
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
    const refused = !!(e && Number.isInteger((e as { status?: number }).status) && (e as { status: number }).status >= 400);
    if (refused && (e as { status: number }).status < 500) restoreAttachments(sentAttachments);
    // The refusal belongs to the session this message was sent from, and the
    // composer, the hint line and Customize are shared by every session. Once
    // the user has opened another one, putting this draft back or asking about
    // this refusal would land it there (a rebind would even re-bind the
    // session no longer on screen): the refused text goes with its bubble.
    const onDispatchFrame = (): boolean => currentId.value === dispatchFrameId;
    // A plan send the server refused never runs, so no end will come for
    // the plan flag to wait on; a queued one would otherwise sit there.
    if (planTurn && planPendingTurn.value === planTurn && refused && Number((e as { status?: number }).status) < 500) {
      planPending.value = false;
      planPendingTurn.value = null;
    }
    // A refusal is a definite answer. A transport failure is indeterminate:
    // the server may still commit the admission, so hold the optimistic
    // bubble for one bounded grace rather than for the rest of the visit —
    // an unreleased token defers every later history read and refuses a
    // stopped run state (locked composer) until the user navigates away.
    if (refused) confirmHistorySubmission();
    else {
      setTimeout(() => {
        confirmHistorySubmission();
        if (currentId.value === dispatchFrameId && (_openGen.value || 0) === dispatchOpenGen) {
          callLane("alignHistoryAfterTurn", dispatchFrameId, dispatchOpenGen);
        }
      }, SUBMISSION_GRACE_MS);
    }
    if (annIds.length) {
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
      if (onDispatchFrame()) settleRefusedBubble(w, text, sentMentions);
      if (ownsTurnTicket(turnTicketToken)) turnDone("failed");
      if (onDispatchFrame()) {
        callLane("openCust", "compute");
        hint(t("environment.readiness.sendBlocked"), true);
      }
      void loadSessions();
      return;
    }
    const err = e as { code?: string; status?: number };
    // A 4xx is the server refusing this message before admission -- a model
    // profile with no key, a pin that cannot be honoured, nothing active to
    // rebind to. No user row and no job exist for it, so a reload would not
    // show the text: it goes back into the composer and the optimistic bubble
    // goes -- unless the composer already holds something new, in which case
    // the bubble is the only copy left and stays, marked not sent. A 5xx may
    // still have been admitted, and keeps both as before.
    const notAdmitted = refused && Number(err.status) < 500;
    if (notAdmitted && onDispatchFrame()) settleRefusedBubble(w, text, sentMentions);
    let lasting = t("toast.sendFailed", apiErrorText(e));
    let settingsCode = err?.code;
    if (
      onDispatchFrame() &&
      err &&
      (err.code === "model_revision_unavailable" || err.code === "model_revision_ambiguous")
    ) {
      const ask = await askConfirm(rebindConfirmText(err));
      if (ask) {
        try {
          const rebound = await api(`/frames/${encodeURIComponent(dispatchFrameId)}/model-binding`, {
            method: "POST",
          });
          noteSessionModelBinding(dispatchFrameId, rebound);
          if (ownsTurnTicket(turnTicketToken)) turnDone("failed");
          if (onDispatchFrame()) hint(rebindDoneText(rebound));
          void loadSessions();
          return;
        } catch (rebindError) {
          lasting = apiErrorText(rebindError);
          settingsCode = (rebindError as { code?: string } | null)?.code;
        }
      }
    }
    // turnDone paints its generic "This turn failed" hint in the same tick, so
    // it goes first: the server's reason is the hint that has to stay.
    if (ownsTurnTicket(turnTicketToken)) turnDone("failed");
    else if (!notAdmitted) w.classList.add("cancelled");
    if (onDispatchFrame()) {
      hint(lasting, true);
      if (settingsCode === "model_profile_needs_key" || settingsCode === "model_profile_needs_active") {
        callLane("openCust", "models");
      }
    }
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

/** Feature-local copy: `i18n/en.ts` / `zh.ts` are generated extracts of app.js. */
const REBIND_COPY: Record<"en" | "zh", Record<"unbound" | "backfilled", string>> = {
  en: {
    unbound: "No model profile is active: this session now runs on the daemon's global configuration",
    backfilled: "Re-bound to the saved model profile this session's model matches",
  },
  zh: {
    unbound: "当前没有启用的模型配置：该会话改为使用守护进程的全局配置运行",
    backfilled: "已改绑到与该会话模型匹配的已保存模型配置",
  },
};

type RebindReason = "moved" | "keyless" | "unreadable" | "ambiguous" | "unusable";

/** Feature-local copy: every refusal a re-bind answers, in its own words. */
const REBIND_CONFIRM_COPY: Record<"en" | "zh", Record<RebindReason, string>> = {
  en: {
    moved:
      "The model profile this session was pinned to now names a different provider or endpoint, so its key is not sent to the earlier configuration. Re-bind the session to the active configuration and continue?",
    keyless:
      "The model profile this session was pinned to has no usable API key. Re-bind the session to the active configuration and continue? (Cancel to add the key in Customize → Models instead.)",
    unreadable:
      "The model configuration this session was pinned to could not be read. Re-bind it to the active configuration and continue?",
    ambiguous:
      "More than one saved model profile matches the model this session used, so which one it ran under is unknown. Re-bind it to the active configuration and continue?",
    unusable:
      "The model configuration this session was pinned to is no longer usable. Re-bind it to the active configuration and continue?",
  },
  zh: {
    moved:
      "该会话固定的模型配置现在指向不同的提供商或端点，因此其密钥不会发往原先的配置。是否将该会话改绑到当前启用的配置以继续？",
    keyless:
      "该会话固定的模型配置没有可用的 API 密钥。是否将该会话改绑到当前启用的配置以继续？（取消后可在 自定义 → 模型 中添加密钥。）",
    unreadable: "无法读取该会话固定的模型配置。是否改绑到当前启用的配置以继续？",
    ambiguous:
      "有多个已保存的模型配置与该会话使用的模型匹配，无法确定它当时使用的是哪一个。是否改绑到当前启用的配置以继续？",
    unusable: "该会话固定的模型配置已不可用。是否改绑到当前启用的配置以继续？",
  },
};

/**
 * The re-bind prompt for a `model_revision_unavailable` /
 * `model_revision_ambiguous` refusal. One sentence -- "no longer exists" --
 * was shown for all of them, including a profile that still exists and only
 * moved to another provider or endpoint (the SEC-2 scope mismatch). The codes
 * are shared, so the server's message picks the reason; "no longer exists"
 * is kept for the refusal that says exactly that.
 */
export function rebindConfirmText(error: unknown): string {
  const rec = error && typeof error === "object" ? (error as { code?: unknown; message?: unknown }) : null;
  const code = String((rec && rec.code) || "");
  const message = String((rec && rec.message) || "").toLowerCase();
  const copy = REBIND_CONFIRM_COPY[LANG === "zh" ? "zh" : "en"];
  if (code === "model_revision_ambiguous") return copy.ambiguous;
  if (message.includes("different provider or endpoint")) return copy.moved;
  if (message.includes("credential is not available")) return copy.keyless;
  if (message.includes("could not be read")) return copy.unreadable;
  if (message.includes("no longer exists")) return t("model.rebind.confirm");
  return copy.unusable;
}

/**
 * What `POST /frames/{id}/model-binding` actually did. "Re-bound to the active
 * model configuration" was shown for every 200, including `{bound: false}`
 * from a daemon with no active profile -- a re-bind that bound nothing.
 */
export function rebindDoneText(answer: unknown): string {
  const raw = answer && typeof answer === "object" ? (answer as { binding?: unknown }).binding : null;
  const binding = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : null;
  const copy = REBIND_COPY[LANG === "zh" ? "zh" : "en"];
  if (binding?.bound === false) return copy.unbound;
  if (binding?.backfilled === true) return copy.backfilled;
  return t("model.rebind.done");
}

/** Feature-local copy for a refused message the composer could not take back. */
const NOT_SENT_COPY: Record<"en" | "zh", string> = {
  en: "Not sent: the server refused this message and the composer already held new text, so it stays here to copy",
  zh: "未发送：服务器拒绝了这条消息，而输入框里已有新内容，因此原文保留在这里以便复制",
};

/**
 * Put a refused message's text back, unless something new was typed since.
 * True when nothing of the refused text is lost: it went back into an empty
 * composer, or there was no text to keep (an annotation-only send). Its `@`
 * pins go back with the text, so a retry still sends the versions picked.
 */
function restoreDraft(text: string, mentions: readonly ComposerMention[] = []): boolean {
  if (!text) return true;
  const box = $("#composer") as HTMLTextAreaElement | null;
  if (!box) return false;
  const restored = !box.value.trim();
  if (restored) {
    box.value = text;
    restoreMentions(mentions);
  }
  grow();
  renderComposerRefChips();
  return restored;
}

/**
 * A refusal stores nothing, so the refused text must survive somewhere the
 * user can reach. Only a text that went back into the composer lets the
 * optimistic bubble go; otherwise the bubble is the last copy and stays,
 * marked not sent (the pre-existing `.msg.user.cancelled` style).
 */
function settleRefusedBubble(w: HTMLElement, text: string, mentions: readonly ComposerMention[] = []): void {
  if (restoreDraft(text, mentions)) {
    w.remove();
    return;
  }
  w.classList.add("cancelled");
  w.dataset.sendState = "not-sent";
  w.title = NOT_SENT_COPY[LANG === "zh" ? "zh" : "en"];
}

type ComposerDispatch = (text: string) => unknown;

/**
 * Plan mode and explore mode are switched on from the session options menu,
 * and their composer buttons used to be hidden for good: once on, nothing on
 * screen said a mode was shaping the next message. While a mode is on its
 * chip shows beside the composer tools, with what it does in its title, and a
 * click turns it off. Repainted on every change of the mode and of language.
 */
function bindModeChip(
  chip: HTMLElement,
  isOn: () => boolean,
  nameKey: string,
  descKey: "planDesc" | "exploreDesc",
): void {
  const paint = () => {
    const on = isOn();
    chip.classList.toggle("hidden", !on);
    chip.classList.toggle("on", on);
    chip.title = sendCopy("modeChipTitle", t(nameKey), sendCopy(descKey));
  };
  effect(paint);
  onLanguageChange(paint);
}

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
    bindModeChip(planToggle, () => !!planMode.value, "composer.planMode", "planDesc");
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
    bindModeChip(exploreToggle, () => !!exploreMode.value, "composer.exploreMode", "exploreDesc");
  }
  const sendBtn = document.getElementById("send-btn");
  if (sendBtn && !sendBtn.dataset.sendBound) {
    sendBtn.dataset.sendBound = "1";
    // Send stays on screen while a turn runs, beside Stop, and queues the
    // draft; its label says so rather than promising an immediate send.
    const label = () => {
      const text = sendCopy(running.value ? "queue" : "send");
      sendBtn.title = text;
      sendBtn.setAttribute("aria-label", text);
    };
    effect(label);
    onLanguageChange(label);
  }
  // The turn clock beside Stop and the session's token chip.
  bindTurnProgress();
  // What the running turn is doing, in the conversation itself.
  bindTurnStatus();
  // Background cells still running, above the composer, each stoppable.
  bindBackgroundCells();
  // Delegated on the document root, not on the node: a re-created #composer
  // (a keyed or conditional subtree, a second render()) keeps its Enter
  // handler with nothing to rebind. Bubble phase, so the autocomplete's
  // capture listener on the node still shields it with
  // stopImmediatePropagation while ac.open or a file search is in flight.
  const root = document.documentElement;
  if (root && !root.dataset.sendBound) {
    root.dataset.sendBound = "1";
    // One dispatch at a time, for Enter and the send button alike. send()
    // clears the composer only after its first awaits (POST /frames on a
    // fresh session, the skills catalog for a /skill token), so a held or
    // double Enter inside that window would create a second session and send
    // the same text twice.
    let inFlight: Promise<unknown> | null = null;
    const dispatchComposer = (text: string): void => {
      if (inFlight) {
        // Dropping the request is right -- one dispatch at a time -- but
        // dropping it SILENTLY is the "dead composer" this branch's own
        // preparation latch exists to explain. `send()`'s hint can never fire
        // from here because the dispatch it guards never happens, so say the
        // same thing at the point that actually swallowed it, and only when a
        // pending upload is the reason.
        if (pendingUploadsFor(currentId.value || null, effProject() || project.value || null, null).length) {
          hint(t("upload.pendingSend"), false, true);
        }
        return;
      }
      const pending = Promise.resolve(dispatch(text));
      inFlight = pending;
      void pending.finally(() => {
        if (inFlight === pending) inFlight = null;
      });
    };
    root.addEventListener("keydown", (e) => {
      const c = e.target as HTMLTextAreaElement | null;
      if (!c || c.id !== "composer") return;
      if (e.isComposing || e.keyCode === 229) return;
      const ac = (globalThis as { ac?: { open?: boolean } }).ac;
      if ((ac && ac.open) || acPending()) return;
      if (e.key !== "Enter" || e.shiftKey) return;
      e.preventDefault();
      dispatchComposer(c.value);
    });
    // Keep focus in the composer on a send-button press. Its blur cancels an
    // in-flight @ search, and blur runs before click (mousedown -> blur -> click).
    root.addEventListener("mousedown", (e) => {
      const target = e.target as { closest?: (selector: string) => unknown } | null;
      if (target && typeof target.closest === "function" && target.closest("#send-btn")) e.preventDefault();
    });
    // The round send button beside the model picker (Shell.tsx #send-btn).
    root.addEventListener("click", (e) => {
      const target = e.target as { closest?: (selector: string) => unknown } | null;
      if (!target || typeof target.closest !== "function" || !target.closest("#send-btn")) return;
      const c = document.getElementById("composer") as HTMLTextAreaElement | null;
      if (!c) return;
      e.preventDefault();
      if (acHoldSend()) return;
      dispatchComposer(c.value);
      if (typeof c.focus === "function") c.focus();
    });
  }
}
