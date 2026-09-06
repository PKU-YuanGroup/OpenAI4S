/**
 * M-01 first-run wizard state machine.
 * Four required decision steps; skip and checklist are first-class.
 * Provider requests stay 0 until the explicit Test action.
 */

export const REQUIRED_STEPS = ["path", "test", "readiness", "project"] as const;
export type RequiredStep = (typeof REQUIRED_STEPS)[number];

export type Surface = "hidden" | "wizard" | "checklist" | "done";

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
  | { type: "choosePath"; path: PathChoice }
  | { type: "goto"; step: RequiredStep }
  | { type: "next" }
  | { type: "showChecklist" }
  | { type: "showWizard" }
  | { type: "skip" }
  | { type: "startTest" }
  | {
      type: "testResult";
      receipt: WizardReceipt | null;
      detail: string;
      reachable: boolean;
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
  const err = e as { message?: unknown; requestId?: unknown } | null;
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
    case "choosePath":
      return {
        ...state,
        path: takePath(action.path),
        decided: withDecided(state, "path"),
        error: null,
      };
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
    case "showChecklist":
      return { ...state, surface: "checklist", error: null };
    case "showWizard":
      return { ...state, surface: "wizard", error: null };
    case "skip":
      return {
        ...state,
        surface: "done",
        skipped: true,
        complete: true,
        error: null,
      };
    case "startTest":
      return {
        ...state,
        testClicked: true,
        providerRequests: state.providerRequests + 1,
        decided: withDecided(state, "test"),
        error: null,
        probeDetail: "",
      };
    case "testResult":
      return {
        ...state,
        receipt: action.receipt,
        probeDetail: String(action.detail || ""),
        decided: withDecided(state, "test"),
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

export function checklistItems(
  state: WizardState,
): Array<{ step: RequiredStep; done: boolean }> {
  return REQUIRED_STEPS.map((step) => ({
    step,
    done: state.decided.includes(step),
  }));
}
