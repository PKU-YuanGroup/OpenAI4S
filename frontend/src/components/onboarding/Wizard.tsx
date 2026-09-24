import { useEffect, useReducer, useRef, useState } from "preact/hooks";
import { t } from "../../i18n";
import { publicText } from "../../features/scrub/scrub";
import { asString } from "../../features/customize/host";
import {
  loadModels,
  loopbackModelBase,
  modelProtocolOptions,
  readCapabilityReceipt,
} from "../../features/customize/models";
import {
  activateExistingModelProfile,
  activateModelProfile,
  completeOnboarding,
  fetchOnboarding,
  probeModelProfile,
  saveModelProfile,
  updateModelProfile,
} from "../../features/onboarding/api";
import { ot } from "../../features/onboarding/copy";
import {
  INITIAL_WIZARD,
  REQUIRED_STEPS,
  checklistItems,
  formatWizardError,
  reduceWizard,
  wizardErrorFromUnknown,
  type PathChoice,
  type PathKind,
  type RequiredStep,
  type WizardState,
} from "../../features/onboarding/machine";
import type { OnboardingStatus } from "../../features/onboarding/status";
import { createProject, openProject } from "../../features/sessions/projects";
import { loadProjects } from "../../features/sessions/load";
import { projects } from "../../stores/session";
import { CapabilityBadges } from "./CapabilityBadges";
import { ReadinessPanel } from "./ReadinessPanel";
import "./onboarding.css";

type Profile = Record<string, unknown>;

// One mapping from a saved profile row to the path it selects. The reducer keys
// receipt invalidation on these identity fields, so two hand-written copies that
// drift (one trims `base_url`, one does not) read as a model change.
function pathFromProfile(profile: Profile): PathChoice {
  const id = asString(profile.id);
  return {
    kind: "existing",
    profileId: id,
    provider: asString(profile.provider),
    model: asString(profile.model),
    baseUrl: asString(profile.base_url),
    name: asString(profile.name || id),
  };
}

/** A saved profile that already is this configuration (the server trims these fields). */
function sameProfileId(status: OnboardingStatus | null, body: Record<string, unknown>): string {
  const bare = (value: unknown) => asString(value).trim().replace(/\/+$/, "");
  const match = (status?.profiles || []).find(
    (p) =>
      bare(p.name) === bare(body.name) &&
      bare(p.provider) === bare(body.provider) &&
      bare(p.base_url) === bare(body.base_url) &&
      bare(p.model) === bare(body.model),
  );
  return match ? asString(match.id) : "";
}

function stepLabel(step: RequiredStep): string {
  return ot("onboarding.step." + step);
}

function PathStep({
  status,
  state,
  busy,
  onChoose,
  onSaveNew,
}: {
  status: OnboardingStatus;
  state: WizardState;
  busy: boolean;
  onChoose: (path: PathChoice) => void;
  onSaveNew: (path: PathChoice, apiKey: string) => void;
}) {
  const keyRef = useRef<HTMLInputElement>(null);
  const protocols = modelProtocolOptions(status.protocols);
  const [kind, setKind] = useState<PathChoice["kind"]>(state.path?.kind || "existing");
  const [provider, setProvider] = useState(state.path?.provider || protocols[0]?.value || "chatgpt");
  const [name, setName] = useState(state.path?.name || "");
  const [model, setModel] = useState(state.path?.model || "");
  const [baseUrl, setBaseUrl] = useState(state.path?.baseUrl || "");
  const locals = status.local_model_catalog.endpoints;

  const submitCloud = () => {
    const apiKey = keyRef.current?.value || "";
    if (keyRef.current) keyRef.current.value = "";
    onSaveNew(
      {
        kind: "cloud",
        profileId: "",
        provider,
        model: model.trim(),
        baseUrl: baseUrl.trim(),
        name: name.trim(),
      },
      apiKey,
    );
  };

  return (
    <div class="onb-path">
      <div class="onb-steps" role="tablist">
        {(["existing", "cloud", "local"] as const).map((item) => (
          <button
            key={item}
            type="button"
            class={"onb-step" + (kind === item ? " active" : "")}
            onClick={() => setKind(item)}
          >
            {ot("onboarding.path." + item)}
          </button>
        ))}
      </div>
      {kind === "existing" ? (
        status.profiles.length ? (
          status.profiles.map((raw) => {
            const p = raw as Profile;
            const id = asString(p.id);
            const selected = state.path?.profileId === id;
            return (
              <label class="onb-choice" key={id}>
                <input
                  type="radio"
                  name="onb-profile"
                  checked={selected}
                  disabled={busy}
                  onChange={() => onChoose(pathFromProfile(p))}
                />
                <span>
                  {asString(p.name || p.id)}
                  {p.has_api_key ? " · " + t("cust.models.hasKey") : ""}
                  {loopbackModelBase(p.base_url) ? " · " + t("cust.models.local.keyless") : ""}
                </span>
              </label>
            );
          })
        ) : (
          <div class="ds">{ot("onboarding.path.empty")}</div>
        )
      ) : null}
      {kind === "cloud" ? (
        <div class="skill-form">
          <label class="skill-lbl">{t("cust.connectors.namePlaceholder")}</label>
          <input
            class="cust-input"
            value={name}
            onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)}
            placeholder={t("cust.models.namePlaceholder")}
          />
          <label class="skill-lbl">{t("cust.models.label.protocol")}</label>
          <select
            class="cust-input"
            value={provider}
            onChange={(e) => setProvider((e.currentTarget as HTMLSelectElement).value)}
          >
            {protocols.map((item) => (
              <option value={item.value} key={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <label class="skill-lbl">{t("cust.models.label.baseUrl")}</label>
          <input
            class="cust-input"
            value={baseUrl}
            onInput={(e) => setBaseUrl((e.currentTarget as HTMLInputElement).value)}
            placeholder={t("cust.models.baseUrlPlaceholder")}
          />
          <label class="skill-lbl">{t("label.model")}</label>
          <input
            class="cust-input"
            value={model}
            onInput={(e) => setModel((e.currentTarget as HTMLInputElement).value)}
            placeholder={t("cust.models.modelPlaceholder2")}
          />
          <label class="skill-lbl">{t("cust.models.label.apiKey")}</label>
          <input
            ref={keyRef}
            class="cust-input"
            type="password"
            autocomplete="off"
            placeholder={t("cust.models.keyPlaceholderUnset")}
          />
          <button type="button" class="solid-btn" disabled={busy} onClick={submitCloud}>
            {ot("onboarding.next")}
          </button>
        </div>
      ) : null}
      {kind === "local" ? (
        locals.length ? (
          locals.map((endpoint) => (
            <label class="onb-choice" key={endpoint.base_url}>
              <input
                type="radio"
                name="onb-local"
                checked={state.path?.baseUrl === endpoint.base_url}
                disabled={busy}
                onChange={() =>
                  onChoose({
                    kind: "local",
                    profileId: "",
                    provider: "chatgpt",
                    model: endpoint.default_model,
                    baseUrl: endpoint.base_url,
                    name: endpoint.label,
                  })
                }
              />
              <span>
                {endpoint.label} · {endpoint.base_url}
              </span>
            </label>
          ))
        ) : (
          <div class="ds">{ot("onboarding.path.empty")}</div>
        )
      ) : null}
      {kind === "local" && state.path?.kind === "local" ? (
        <div class="skill-form">
          <label class="skill-lbl">{ot("onboarding.path.localModel")}</label>
          <input
            class="cust-input"
            value={state.path.model}
            onInput={(e) =>
              // An edited model is no longer the profile that was saved: Next
              // saves a new one. Keeping the saved id let Test (reached through
              // the checklist) probe the *old* model and file its receipt under
              // the new name.
              onChoose({
                ...state.path!,
                profileId: "",
                model: (e.currentTarget as HTMLInputElement).value,
              })
            }
          />
          <button
            type="button"
            class="solid-btn"
            disabled={busy || !state.path.model.trim()}
            onClick={() => onSaveNew(state.path!, "")}
          >
            {ot("onboarding.next")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function ProjectStep({
  busy,
  onOpen,
  onCreate,
}: {
  busy: boolean;
  onOpen: (id: string) => void;
  onCreate: (name: string) => void;
}) {
  const [name, setName] = useState("");
  const [selected, setSelected] = useState("");
  const list = projects.value as Array<{ project_id?: string; id?: string; name?: string }>;
  return (
    <div class="onb-list">
      {list.length ? (
        list.map((row) => {
          const id = row.project_id || row.id || "";
          return (
            <label class="onb-choice" key={id}>
              <input
                type="radio"
                name="onb-project"
                checked={selected === id}
                disabled={busy}
                onChange={() => setSelected(id)}
              />
              <span>{row.name || id}</span>
            </label>
          );
        })
      ) : (
        <div class="ds">{ot("onboarding.project.empty")}</div>
      )}
      {selected ? (
        <button type="button" class="solid-btn" disabled={busy} onClick={() => onOpen(selected)}>
          {ot("onboarding.project.open")}
        </button>
      ) : null}
      <label class="skill-lbl">{t("projModal.name.placeholder")}</label>
      <input
        class="cust-input"
        value={name}
        onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)}
        placeholder={t("projModal.name.placeholder")}
      />
      <button
        type="button"
        class="solid-btn"
        disabled={busy}
        onClick={() => onCreate(name.trim() || t("palette.action.newProject"))}
      >
        {t("projModal.create")}
      </button>
    </div>
  );
}

export function WizardHost() {
  const [state, dispatch] = useReducer(reduceWizard, INITIAL_WIZARD);
  const [status, setStatus] = useState<OnboardingStatus | null>(null);
  const [busy, setBusy] = useState(false);
  // A probe is not `busy`: it can wait out a connect timeout and its retries
  // for minutes, and `busy` disables Skip. The run counter lets the wizard
  // walk away from a probe that is still waiting; a late answer is dropped.
  const [testing, setTesting] = useState(false);
  const probeRun = useRef(0);
  const alive = useRef(true);
  // The profile this wizard saved for each path. Continue on a path already
  // saved updates that profile: it used to POST a new one every time, so
  // coming back to the step left duplicates, and the one activated last could
  // be a copy without the key (the key field starts empty on a second visit).
  const savedProfiles = useRef<Partial<Record<PathKind, string>>>({});

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = async () => {
    setBusy(true);
    try {
      const next = await fetchOnboarding();
      if (!alive.current) return;
      setStatus(next);
      dispatch({ type: "hydrate", complete: next.complete });
    } catch (error) {
      if (!alive.current) return;
      const fail = wizardErrorFromUnknown(error);
      dispatch({
        type: "fail",
        message: ot("onboarding.load.err", fail.message),
        requestId: fail.requestId,
      });
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (state.step === "project" && state.surface === "wizard") void loadProjects();
  }, [state.step, state.surface]);

  const fail = (error: unknown) => {
    const next = wizardErrorFromUnknown(error);
    dispatch({ type: "fail", message: next.message, requestId: next.requestId });
  };

  const leaveTest = () => {
    probeRun.current += 1;
    setTesting(false);
  };

  const choosePath = (path: PathChoice) => {
    leaveTest();
    dispatch({ type: "choosePath", path });
  };

  const onSkip = async () => {
    leaveTest();
    setBusy(true);
    try {
      await completeOnboarding({ skip: true });
      if (alive.current) dispatch({ type: "skip" });
    } catch (error) {
      if (alive.current) fail(error);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const onFinish = async () => {
    leaveTest();
    setBusy(true);
    try {
      await completeOnboarding({ skip: true });
      if (alive.current) dispatch({ type: "finish" });
    } catch (error) {
      if (alive.current) fail(error);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const onSaveNew = async (path: PathChoice, apiKey: string) => {
    const nm = path.name.trim();
    if (!nm) {
      dispatch({ type: "fail", message: t("toast.specialist.enterName"), requestId: "" });
      return;
    }
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        name: nm,
        provider: path.provider,
        base_url: path.baseUrl,
        model: path.model,
      };
      if (apiKey.trim()) body.api_key = apiKey.trim();
      let id = "";
      const known = savedProfiles.current[path.kind] || sameProfileId(status, body);
      if (known) {
        try {
          await updateModelProfile(known, body);
          id = known;
        } catch (error) {
          // Deleted meanwhile (Settings -> Models): save it afresh below.
          if ((error as { status?: unknown } | null)?.status !== 404) throw error;
        }
      }
      if (!id) id = asString((await saveModelProfile(body)).id);
      if (id) {
        savedProfiles.current[path.kind] = id;
        await activateModelProfile(id);
      }
      // The composer's `#model-select` lists saved profiles and marks the active one.
      void loadModels();
      if (!alive.current) return;
      const chosen = { ...path, profileId: id };
      choosePath(chosen);
      const refreshed = await fetchOnboarding();
      if (!alive.current) return;
      setStatus(refreshed);
      dispatch({ type: "next" });
    } catch (error) {
      if (alive.current) fail(error);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const onTest = async () => {
    // The receipt is filed under `state.path`, so that is the profile to probe.
    // Falling back to the active profile whenever the path had no id measured
    // some *other* model -- an unsaved local choice reached through the
    // checklist showed a cloud profile's capabilities as its own, and spent a
    // provider request on a profile nobody picked on this screen. With no path
    // at all, the active profile becomes the path first, then is probed.
    let id = state.path?.profileId || "";
    if (!state.path && status?.active_id) {
      const active = status.profiles.find((profile) => asString(profile.id) === status.active_id);
      if (active) {
        choosePath(pathFromProfile(active));
        id = status.active_id;
      }
    }
    if (!id) {
      dispatch({ type: "fail", message: ot("onboarding.test.needProfile"), requestId: "" });
      return;
    }
    const run = (probeRun.current += 1);
    const current = () => alive.current && probeRun.current === run;
    dispatch({ type: "startTest" });
    setTesting(true);
    try {
      const result = await probeModelProfile(id);
      if (!current()) return;
      const detail = publicText(result.detail, 240);
      dispatch({
        type: "testResult",
        receipt: readCapabilityReceipt(result.capability_receipt),
        detail: result.reachable === true ? "" : detail,
        reachable: result.reachable === true,
        profileId: id,
      });
      if (result.reachable !== true) {
        dispatch({
          type: "fail",
          message: detail || t("cust.models.unreachable"),
          requestId: asString(result.request_id),
        });
      }
    } catch (error) {
      if (!current()) return;
      const next = wizardErrorFromUnknown(error);
      dispatch({ type: "fail", message: next.message, requestId: next.requestId });
      dispatch({
        type: "testResult",
        receipt: state.receipt,
        detail: next.message,
        reachable: false,
        profileId: id,
      });
    } finally {
      if (current()) setTesting(false);
    }
  };

  const onUseExisting = async () => {
    const id = state.path?.kind === "existing" ? state.path.profileId : "";
    if (!id) {
      dispatch({ type: "fail", message: ot("onboarding.test.needProfile"), requestId: "" });
      return;
    }
    setBusy(true);
    try {
      const refreshed = await activateExistingModelProfile(id);
      void loadModels();
      if (!alive.current) return;
      // The saved profile may have been edited since the wizard listed it.
      // Refresh the identity too, so the old model's receipt cannot survive.
      const selected = refreshed.profiles.find((profile) => asString(profile.id) === id);
      if (selected) choosePath(pathFromProfile(selected));
      setStatus(refreshed);
      dispatch({ type: "next" });
    } catch (error) {
      if (alive.current) fail(error);
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  const onOpenProject = async (id: string) => {
    setBusy(true);
    try {
      await openProject(id);
      dispatch({ type: "markProjectOpened" });
      await onFinish();
    } catch (error) {
      if (alive.current) fail(error);
      if (alive.current) setBusy(false);
    }
  };

  const onCreateProject = async (name: string) => {
    if (!name) {
      dispatch({ type: "fail", message: ot("onboarding.project.needName"), requestId: "" });
      return;
    }
    setBusy(true);
    try {
      await createProject(name, "", "");
      dispatch({ type: "markProjectOpened" });
      await onFinish();
    } catch (error) {
      if (alive.current) fail(error);
      if (alive.current) setBusy(false);
    }
  };

  if (state.surface === "done") return null;
  const errorText = formatWizardError(state.error);
  if (state.surface === "hidden" && !errorText) return null;

  return (
    <div
      id="onboarding"
      class="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="onboarding-title"
      data-onboarding-step={state.step}
      data-onboarding-surface={state.surface}
      data-provider-requests={String(state.providerRequests)}
    >
      <div class="onb-box">
        <div id="onboarding-title" class="onb-h">
          {state.surface === "checklist" ? ot("onboarding.checklist.title") : ot("onboarding.title")}
        </div>
        <div class="onb-sub">
          {state.surface === "checklist" ? ot("onboarding.checklist.hint") : ot("onboarding.subtitle")}
        </div>
        {errorText ? (
          <div class="onb-error" role="alert">
            {errorText}
          </div>
        ) : null}
        {state.surface === "checklist" ? (
          <div class="onb-list">
            {checklistItems(state).map((item) => (
              <button
                key={item.step}
                type="button"
                class={"onb-step" + (item.done ? " done" : "")}
                onClick={() => {
                  if (item.step === "readiness") dispatch({ type: "markReadinessSeen" });
                  dispatch({ type: "goto", step: item.step });
                }}
              >
                {item.done ? "✓ " : ""}
                {stepLabel(item.step)}
              </button>
            ))}
          </div>
        ) : state.surface === "wizard" ? (
          <>
            <div class="onb-steps">
              {REQUIRED_STEPS.map((step, index) => (
                <span
                  key={step}
                  class={
                    "onb-step" +
                    (state.step === step ? " active" : "") +
                    (state.decided.includes(step) ? " done" : "")
                  }
                >
                  {index + 1}/{REQUIRED_STEPS.length} {stepLabel(step)}
                </span>
              ))}
            </div>
            {state.step === "path" && status ? (
              <PathStep
                status={status}
                state={state}
                busy={busy}
                onChoose={choosePath}
                onSaveNew={onSaveNew}
              />
            ) : null}
            {state.step === "test" ? (
              <div>
                <div class="onb-warn">{ot("onboarding.test.warning")}</div>
                {state.providerRequests === 0 ? (
                  <div class="ds">{ot("onboarding.test.idle")}</div>
                ) : null}
                <div class="onb-actions">
                  <button
                    type="button"
                    class="solid-btn"
                    disabled={busy || testing}
                    onClick={() => void onTest()}
                  >
                    {testing ? t("cust.models.testing") : t("cust.models.test")}
                  </button>
                </div>
                <CapabilityBadges receipt={state.receipt} unknownReason={state.probeDetail} />
              </div>
            ) : null}
            {state.step === "readiness" && status ? <ReadinessPanel status={status} /> : null}
            {state.step === "project" ? (
              <ProjectStep
                busy={busy}
                onOpen={(id) => void onOpenProject(id)}
                onCreate={(name) => void onCreateProject(name)}
              />
            ) : null}
          </>
        ) : null}
        <div class="onb-actions">
          <button type="button" class="outline-btn" disabled={busy} onClick={() => void onSkip()}>
            {ot("onboarding.skip")}
          </button>
          {state.surface === "wizard" ? (
            <button
              type="button"
              class="outline-btn"
              disabled={busy}
              onClick={() => {
                leaveTest();
                dispatch({ type: "showChecklist" });
              }}
            >
              {ot("onboarding.checklist")}
            </button>
          ) : (
            <button
              type="button"
              class="outline-btn"
              disabled={busy}
              onClick={() => dispatch({ type: "showWizard" })}
            >
              {ot("onboarding.title")}
            </button>
          )}
          {state.surface === "wizard" && state.step !== "path" && state.step !== "project" ? (
            <button
              type="button"
              class="solid-btn"
              disabled={busy}
              onClick={() => {
                if (state.step === "test") leaveTest();
                if (state.step === "readiness") dispatch({ type: "markReadinessSeen" });
                dispatch({ type: "next" });
              }}
            >
              {ot("onboarding.next")}
            </button>
          ) : null}
          {state.surface === "wizard" && state.step === "path" && state.path?.kind === "existing" ? (
            <button
              type="button"
              class="solid-btn"
              disabled={busy || !state.path}
              onClick={() => void onUseExisting()}
            >
              {ot("onboarding.next")}
            </button>
          ) : null}
          {!status && state.error ? (
            <button type="button" class="outline-btn" disabled={busy} onClick={() => void load()}>
              {ot("onboarding.retry")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
