/**
 * M-01 first-run wizard state machine.
 * Four required decision steps; skip is first-class.
 * Provider requests stay 0 until Test or a model-list fetch; both are counted.
 */

import type { OnboardingStatus } from "./status";
import { ot } from "./copy";

export const REQUIRED_STEPS = ["path", "test", "readiness", "project"] as const;
export type RequiredStep = (typeof REQUIRED_STEPS)[number];

export type Surface = "hidden" | "wizard" | "done";

export type PathKind = "existing" | "cloud" | "local";

export type PathChoice = {
  kind: PathKind;
  profileId: string;
  provider: string;
  model: string;
  baseUrl: string;
  name: string;
};

export type WizardError = {
  message: string;
  requestId: string;
};

export type WizardEvidence = "true" | "false" | "unknown";

export type WizardReceipt = {
  native_tool_call: WizardEvidence;
  streaming: WizardEvidence;
  stale: boolean;
  native_completion: boolean;
  reachable: boolean;
};

export type WizardState = {
  surface: Surface;
  step: RequiredStep;
  decided: readonly RequiredStep[];
  providerRequests: number;
  testClicked: boolean;
  path: PathChoice | null;
  receipt: WizardReceipt | null;
  probeDetail: string;
  error: WizardError | null;
  skipped: boolean;
  complete: boolean;
};

export const INITIAL_WIZARD: WizardState = {
  surface: "hidden",
  step: "path",
  decided: [],
  providerRequests: 0,
  testClicked: false,
  path: null,
  receipt: null,
  probeDetail: "",
  error: null,
  skipped: false,
  complete: false,
};

export type WizardAction =
  | { type: "hydrate"; complete: boolean }
  /**
   * A reload in this tab after the wizard saved a profile (NV-04). The path
   * is the saved profile; nothing about the Test is carried over, because
   * a saved profile is not a verified connection.
   */
  | { type: "resume"; step: RequiredStep; path: PathChoice }
  /**
   * `saved`: the choice is already a saved, active profile, so step 1 is done.
   * A radio click is only a selection; step 1 is ticked once Continue saved it
   * (TEAM-10: a refused Continue left "Choose a model" ticked).
   */
  | { type: "choosePath"; path: PathChoice; saved?: boolean }
  | { type: "goto"; step: RequiredStep }
  | { type: "next" }
  | { type: "skip" }
  | { type: "startTest" }
  /** A model-list fetch: a provider request, but not the Test decision. */
  | { type: "providerRequest" }
  | {
      type: "testResult";
      receipt: WizardReceipt | null;
      detail: string;
      reachable: boolean;
      /** The profile the probe actually measured. */
      profileId?: string;
    }
  | { type: "fail"; message: string; requestId: string }
  | { type: "clearError" }
  | { type: "markReadinessSeen" }
  | { type: "markProjectOpened" }
  | { type: "finish" };

function takePath(path: PathChoice): PathChoice {
  const kind: PathKind =
    path.kind === "local" || path.kind === "cloud" || path.kind === "existing"
      ? path.kind
      : "cloud";
  return {
    kind,
    profileId: String(path.profileId || ""),
    provider: String(path.provider || ""),
    model: String(path.model || ""),
    baseUrl: String(path.baseUrl || ""),
    name: String(path.name || ""),
  };
}

function withDecided(state: WizardState, step: RequiredStep): readonly RequiredStep[] {
  if (state.decided.includes(step)) return state.decided;
  if (state.decided.length >= REQUIRED_STEPS.length) return state.decided;
  return [...state.decided, step];
}

function stepAfter(step: RequiredStep): RequiredStep {
  const i = REQUIRED_STEPS.indexOf(step);
  const next = REQUIRED_STEPS[i + 1];
  return next ?? step;
}

export function formatWizardError(error: WizardError | null): string {
  if (!error) return "";
  return error.requestId ? `${error.message} [${error.requestId}]` : error.message;
}

export function wizardErrorFromUnknown(e: unknown): WizardError {
  const err = e as { message?: unknown; requestId?: unknown; code?: unknown } | null;
  // A refusal by role is not a fault to report with a request id: say who can
  // do it instead of the bare server text (TEAM-10).
  if (err && err.code === "admin_only") return { message: ot("onboarding.adminOnly"), requestId: "" };
  return {
    message: err && err.message != null ? String(err.message) : String(e),
    requestId: err && err.requestId != null ? String(err.requestId) : "",
  };
}

export function reduceWizard(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "hydrate":
      if (action.complete) {
        return {
          ...INITIAL_WIZARD,
          surface: "hidden",
          complete: true,
        };
      }
      return {
        ...INITIAL_WIZARD,
        surface: "wizard",
        complete: false,
      };
    case "resume":
      // A fresh wizard (no receipt, no request counted) placed back where the
      // reload interrupted it. Choosing the path was decided only when the
      // saved profile is already the active one; otherwise the user still
      // has to confirm it on the path step.
      return {
        ...INITIAL_WIZARD,
        surface: "wizard",
        step: action.step,
        path: takePath(action.path),
        decided: action.step === "path" ? [] : ["path"],
        complete: false,
      };
    case "choosePath": {
      const path = takePath(action.path);
      // A receipt belongs to this exact model selection, not its display name
      // or whether a saved cloud profile is now selected as an existing one.
      const changed = !state.path ||
        state.path.profileId !== path.profileId ||
        state.path.provider !== path.provider ||
        state.path.model !== path.model ||
        state.path.baseUrl !== path.baseUrl;
      return {
        ...state,
        path,
        decided: (action.saved ? withDecided(state, "path") : state.decided).filter(
          (step) => !changed || step !== "test",
        ),
        receipt: changed ? null : state.receipt,
        probeDetail: changed ? "" : state.probeDetail,
        testClicked: changed ? false : state.testClicked,
        error: null,
      };
    }
    case "goto":
      return {
        ...state,
        surface: "wizard",
        step: action.step,
        error: null,
      };
    case "next": {
      if (state.step === "path" && !state.path) {
        return {
          ...state,
          error: { message: "choose a model path", requestId: "" },
        };
      }
      const decided =
        state.step === "path"
          ? withDecided(state, "path")
          : state.step === "test"
            ? withDecided(state, "test")
            : state.step === "readiness"
              ? withDecided(state, "readiness")
              : state.decided;
      return {
        ...state,
        decided,
        step: stepAfter(state.step),
        error: null,
      };
    }
    case "skip":
      return {
        ...state,
        surface: "done",
        skipped: true,
        complete: true,
        error: null,
      };
    case "startTest":
      // Not decided yet: the rail's tick waits for a reachable result (or for
      // Continue). It used to tick here, while "Contacting the endpoint..."
      // was still on screen.
      return {
        ...state,
        testClicked: true,
        providerRequests: state.providerRequests + 1,
        error: null,
        probeDetail: "",
      };
    case "providerRequest":
      return { ...state, providerRequests: state.providerRequests + 1 };
    case "testResult":
      // The receipt is filed under `state.path`, so it has to have been
      // measured for it. Clearing on an identity change covers what is already
      // stored; this covers what is still arriving, whoever sent it.
      if (action.profileId !== undefined && action.profileId !== (state.path?.profileId ?? "")) {
        return state;
      }
      // A failed probe is not a passed test: only a reachable endpoint ticks
      // the step. Continue still records a user who moves on without one.
      return {
        ...state,
        receipt: action.receipt,
        probeDetail: String(action.detail || ""),
        decided: action.reachable
          ? withDecided(state, "test")
          : state.decided.filter((step) => step !== "test"),
      };
    case "fail":
      return {
        ...state,
        error: { message: action.message, requestId: action.requestId },
      };
    case "clearError":
      return { ...state, error: null };
    case "markReadinessSeen":
      return {
        ...state,
        decided: withDecided(state, "readiness"),
      };
    case "markProjectOpened":
      return {
        ...state,
        decided: withDecided(state, "project"),
        error: null,
      };
    case "finish":
      return {
        ...state,
        surface: "done",
        complete: true,
        error: null,
      };
    default:
      return state;
  }
}

export function requiredStepCount(): number {
  return REQUIRED_STEPS.length;
}

// --- NV-04: resume after a reload in the same tab ---------------------------
//
// The wizard saves (and activates) a profile before the connection test, the
// environment check and the project step. A saved profile makes the install
// read `complete`, so a reload used to hide those steps as if they were done.
// The tab now remembers, in sessionStorage, only the step to come back to and
// the saved profile's id -- never the key, the model, the endpoint or a probe
// result. On reload the id is checked against the authenticated profile list
// before anything is shown, and the probe, the activation, the environment
// setup and the project stay behind the user's own clicks.

export const RESUME_STORAGE_KEY = "openai4s.onboarding.resume";

/** The one step a reload returns to: a saved profile is not a tested one. */
export const RESUME_STEP: RequiredStep = "test";

export type ResumeMarker = { step: RequiredStep; profile_id: string };

type MarkerStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** This tab's sessionStorage, or null where the browser refuses it. */
function tabStorage(): MarkerStorage | null {
  try {
    const storage = (globalThis as { sessionStorage?: MarkerStorage }).sessionStorage;
    return storage && typeof storage.getItem === "function" ? storage : null;
  } catch {
    // Blocked site data throws on the property read itself.
    return null;
  }
}

/** Forget the marker. Storage that refuses the call holds nothing to forget. */
export function clearResumeMarker(storage: MarkerStorage | null = tabStorage()): void {
  try {
    storage?.removeItem(RESUME_STORAGE_KEY);
  } catch {
    /* storage disabled: nothing was written either */
  }
}

/** Remember that this tab saved `profileId`; false when storage refused it. */
export function writeResumeMarker(
  profileId: string,
  storage: MarkerStorage | null = tabStorage(),
): boolean {
  const id = String(profileId || "").trim();
  if (!id || !storage) return false;
  const marker: ResumeMarker = { step: RESUME_STEP, profile_id: id };
  try {
    storage.setItem(RESUME_STORAGE_KEY, JSON.stringify(marker));
    return true;
  } catch {
    return false;
  }
}

/** The marker this tab wrote, or null. A value of any other shape is dropped. */
export function readResumeMarker(storage: MarkerStorage | null = tabStorage()): ResumeMarker | null {
  if (!storage) return null;
  let raw: string | null;
  try {
    raw = storage.getItem(RESUME_STORAGE_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  const row =
    parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null;
  const id = row && typeof row.profile_id === "string" ? row.profile_id.trim() : "";
  if (!row || row.step !== RESUME_STEP || !id || Object.keys(row).length !== 2) {
    clearResumeMarker(storage);
    return null;
  }
  return { step: RESUME_STEP, profile_id: id };
}

export type ResumeDecision =
  /** No marker: the original flow. */
  | { kind: "none" }
  /** A marker that no longer applies: forget it, then the original flow. */
  | { kind: "clear" }
  /** The saved profile is the active one: back to the connection test. */
  | { kind: "test"; profile: Record<string, unknown> }
  /** Saved but not active: the existing-profile choice, for the user to confirm. */
  | { kind: "choose"; profile: Record<string, unknown> };

/**
 * What a reload does with this tab's marker, decided from the daemon's answer.
 *
 * The marker is a hint, the status is the authority: a daemon that does not
 * report `completed_explicitly` (older than the field), a setup someone
 * finished or skipped (in another tab too), and a profile the authenticated
 * list does not show (deleted, or another account's) each clear it.
 */
export function resolveResume(
  marker: ResumeMarker | null,
  status: Pick<OnboardingStatus, "completed_explicitly" | "profiles" | "active_id"> | null,
): ResumeDecision {
  if (!marker) return { kind: "none" };
  if (!status || status.completed_explicitly !== false) return { kind: "clear" };
  const profile = (status.profiles || []).find(
    (row) => String(row?.id ?? "").trim() === marker.profile_id,
  );
  if (!profile) return { kind: "clear" };
  return String(status.active_id || "").trim() === marker.profile_id
    ? { kind: "test", profile }
    : { kind: "choose", profile };
}
