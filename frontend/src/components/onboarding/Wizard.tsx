import { useEffect, useReducer, useRef, useState } from "preact/hooks";
import { LANG, setLang, t } from "../../i18n";
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
  discoverModelProfile,
  fetchOnboarding,
  mergeReadinessRefresh,
  probeModelProfile,
  saveModelProfile,
  updateModelProfile,
} from "../../features/onboarding/api";
import { ot } from "../../features/onboarding/copy";
import { keyPage, presetLabel } from "../../features/onboarding/presets";
import {
  INITIAL_WIZARD,
  REQUIRED_STEPS,
  formatWizardError,
  reduceWizard,
  wizardErrorFromUnknown,
  type PathChoice,
  type PathKind,
  type RequiredStep,
  type WizardState,
} from "../../features/onboarding/machine";
import type { OnboardingStatus } from "../../features/onboarding/status";
import { displayProjectName } from "../../features/sessions/copy";
import { createProject, openProject } from "../../features/sessions/projects";
import { loadProjects } from "../../features/sessions/load";
import { projects } from "../../stores/session";
import { CapabilityBadges } from "./CapabilityBadges";
import { ReadinessPanel } from "./ReadinessPanel";
import { SearchSetupCard, type SearchSetupHandle } from "./SearchSetupCard";
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

/**
 * The model the daemon already runs from its environment variables
 * (`OPENAI4S_LLM_PROVIDER` / `_MODEL` / `OPENAI4S_<P>_API_KEY`), when no saved
 * profile stands for it yet. GET /onboarding reports it (`has_api_key` and the
 * model), but the wizard listed saved profiles only, so a daemon configured
 * this way read "No profile yet" and Test asked for a profile.
 *
 * Choosing it saves a profile with an empty `base_url` and no key: the key
 * stays in the environment and the server's credential rule resolves the same
 * provider's environment key for that endpoint (never another provider's, and
 * never for search).
 */
export function environmentPath(status: OnboardingStatus | null): PathChoice | null {
  if (!status || !status.has_api_key) return null;
  const provider = status.provider.trim();
  const served = modelProtocolOptions(status.protocols).map((item) => item.value);
  if (!provider || !served.includes(provider)) return null;
  const model = status.model.trim();
  const bare = (value: unknown) => asString(value).trim();
  if (status.profiles.some((p) => bare(p.provider) === provider && bare(p.model) === model)) {
    return null;
  }
  return {
    kind: "existing",
    profileId: "",
    provider,
    model,
    baseUrl: "",
    name: ot("onboarding.path.envName", model || provider),
  };
}

/**
 * The configured model step 1 starts on: the active profile, else the only
 * configured choice (one saved profile, or only the environment's model).
 */
export function defaultPath(status: OnboardingStatus): PathChoice | null {
  const active = status.profiles.find((p) => asString(p.id) === status.active_id && status.active_id);
  if (active) return pathFromProfile(active as Profile);
  const env = environmentPath(status);
  const choices = status.profiles.length + (env ? 1 : 0);
  if (choices !== 1) return null;
  return env || pathFromProfile(status.profiles[0] as Profile);
}

/** The env model is the path: chosen but not saved yet (its profile id is still empty). */
function isEnvironmentPath(path: PathChoice | null, status: OnboardingStatus | null): boolean {
  const env = environmentPath(status);
  return !!(
    env &&
    path &&
    path.kind === "existing" &&
    !path.profileId &&
    path.provider === env.provider &&
    path.model === env.model
  );
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
  onProviderRequest,
}: {
  status: OnboardingStatus;
  state: WizardState;
  busy: boolean;
  onChoose: (path: PathChoice) => void;
  onSaveNew: (path: PathChoice, apiKey: string) => void;
  /** A model-list fetch is about to contact the provider. */
  onProviderRequest: () => void;
}) {
  const keyRef = useRef<HTMLInputElement>(null);
  const protocols = modelProtocolOptions(status.protocols);
  const env = environmentPath(status);
  // With nothing configured the "configured models" tab is an empty page:
  // open on the cloud form instead.
  const [kind, setKind] = useState<PathChoice["kind"]>(
    state.path?.kind || (status.profiles.length || env ? "existing" : "cloud"),
  );
  const [provider, setProvider] = useState(state.path?.provider || protocols[0]?.value || "chatgpt");
  const [name, setName] = useState(state.path?.name || "");
  // Name edits do not invalidate a fetch, so Continue reads the latest value
  // after awaiting one rather than the name from the render that started it.
  const latestName = useRef(name);
  latestName.current = name;
  const [model, setModel] = useState(state.path?.model || "");
  const [baseUrl, setBaseUrl] = useState(state.path?.baseUrl || "");
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");
  const discoveryRun = useRef(0);
  const discoveryPending = useRef(false);
  const autoModel = useRef("");
  // Last, so the hooks above keep their order.
  const [nameError, setNameError] = useState("");
  const locals = status.local_model_catalog.endpoints;
  const preset = (status.provider_presets || []).find((item) => item.id === provider) || null;
  const keySource = keyPage(provider);
  // The environment's key applies to a new cloud profile only for the same
  // provider at its own endpoint (the server's credential rule): say so
  // instead of "not configured; enter one".
  const envKeyApplies =
    status.has_api_key && provider === status.provider.trim() && !baseUrl.trim();

  useEffect(() => () => { discoveryRun.current += 1; }, []);

  // Invalidate synchronously on edits, not after a render: a late response
  // must never fill (or save) a model from the previous endpoint/key/protocol.
  const invalidateDiscovery = (clearOptions = true) => {
    discoveryRun.current += 1;
    discoveryPending.current = false;
    setDiscovering(false);
    setDiscoveryError("");
    if (clearOptions) {
      setModelOptions([]);
      if (autoModel.current) setModel("");
      autoModel.current = "";
    }
  };

  const fetchModels = async (): Promise<string> => {
    if (busy || discoveryPending.current) return "";
    const run = ++discoveryRun.current;
    discoveryPending.current = true;
    setDiscovering(true);
    setDiscoveryError("");
    onProviderRequest();
    try {
      const { models, defaultModel } = await discoverModelProfile({
        provider,
        base_url: baseUrl.trim(),
        api_key: keyRef.current?.value || "",
      });
      if (run !== discoveryRun.current) return "";
      setModelOptions(models);
      if (!models.length) {
        setDiscoveryError(ot("onboarding.path.noModels"));
        return "";
      }
      const selected = model.trim() || defaultModel;
      if (!model.trim() && selected) {
        autoModel.current = selected;
        setModel(selected);
      }
      return selected;
    } catch (error) {
      if (run === discoveryRun.current) {
        setModelOptions([]);
        const failure = wizardErrorFromUnknown(error);
        setDiscoveryError(formatWizardError(failure));
      }
      return "";
    } finally {
      if (run === discoveryRun.current) {
        discoveryPending.current = false;
        setDiscovering(false);
      }
    }
  };

  const submitCloud = async () => {
    if (busy || discoveryPending.current) return;
    if (!name.trim()) {
      // Shown under the name field. It used to appear beside "Fetch model
      // list", three fields away from the one it is about.
      setNameError(t("toast.specialist.enterName"));
      return;
    }
    const apiKey = keyRef.current?.value || "";
    const selectedModel = model.trim() || await fetchModels();
    // Discovery is optional when a model was typed, but an empty/failed list
    // must not silently fall through to the protocol's unrelated default.
    if (!selectedModel) return;
    if (keyRef.current) keyRef.current.value = "";
    onSaveNew(
      {
        kind: "cloud",
        profileId: "",
        provider,
        model: selectedModel,
        baseUrl: baseUrl.trim(),
        name: latestName.current.trim(),
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
            role="tab"
            aria-selected={kind === item}
            class={"onb-step" + (kind === item ? " active" : "")}
            onClick={() => { invalidateDiscovery(); setKind(item); }}
          >
            {ot("onboarding.path." + item)}
          </button>
        ))}
      </div>
      {kind === "existing" && env ? (
        <label class="onb-choice onb-choice-env" key="environment" data-onboarding-env="">
          <input
            type="radio"
            name="onb-profile"
            checked={isEnvironmentPath(state.path, status)}
            disabled={busy}
            onChange={() => onChoose(env)}
          />
          <span>
            <span class="onb-choice-name">
              {ot(
                "onboarding.path.envLabel",
                env.model || ot("onboarding.path.envDefaultModel"),
                modelProtocolOptions([env.provider])[0]?.label || env.provider,
              )}
            </span>
            <span class="onb-choice-sub">{ot("onboarding.path.envHint")}</span>
          </span>
        </label>
      ) : null}
      {kind === "existing" ? (
        status.profiles.length || env ? (
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
          {/* Order: provider, name, endpoint, key, then the model. The key
              used to come last, so "Fetch model list" above it failed with
              "API key required" until the form was filled bottom-up. */}
          <label class="skill-lbl">{ot("onboarding.path.provider")}</label>
          <select
            class="cust-input"
            data-onboarding-provider=""
            value={provider}
            onChange={(e) => {
              setProvider((e.currentTarget as HTMLSelectElement).value);
              invalidateDiscovery();
            }}
          >
            {protocols.map((item) => (
              <option value={item.value} key={item.value}>
                {presetLabel(item.value, item.label)}
              </option>
            ))}
          </select>
          {preset ? (
            <div class="ds onb-field-note" data-onboarding-preset="">
              {ot(
                "onboarding.path.presetInfo",
                protocols.find((item) => item.value === provider)?.label || provider,
                preset.model,
              )}
            </div>
          ) : null}
          <label class="skill-lbl">{t("cust.connectors.namePlaceholder")}</label>
          <input
            class="cust-input"
            data-onboarding-name=""
            value={name}
            aria-invalid={nameError ? "true" : undefined}
            onInput={(e) => {
              setName((e.currentTarget as HTMLInputElement).value);
              setNameError("");
            }}
            placeholder={t("cust.models.namePlaceholder")}
          />
          {nameError ? (
            <div class="onb-field-error" role="alert" data-onboarding-name-error="">
              {nameError}
            </div>
          ) : null}
          <label class="skill-lbl">{t("cust.models.label.baseUrl")}</label>
          <input
            class="cust-input"
            value={baseUrl}
            onInput={(e) => {
              setBaseUrl((e.currentTarget as HTMLInputElement).value);
              invalidateDiscovery();
            }}
            placeholder={
              preset && preset.baseUrl
                ? ot("onboarding.path.baseUrlDefault", preset.baseUrl)
                : t("cust.models.baseUrlPlaceholder")
            }
          />
          <label class="skill-lbl">{t("cust.models.label.apiKey")}</label>
          <input
            ref={keyRef}
            class="cust-input"
            type="password"
            autocomplete="off"
            onInput={() => invalidateDiscovery()}
            placeholder={
              envKeyApplies
                ? ot("onboarding.path.keyFromEnv")
                : t("cust.models.keyPlaceholderUnset")
            }
          />
          <div class="ds onb-field-note" data-onboarding-key-source="">
            {keySource ? (
              <a href={keySource.url} target="_blank" rel="noopener noreferrer">
                {ot("onboarding.path.getKey", keySource.label)}
              </a>
            ) : (
              ot("onboarding.path.getKeyVendor")
            )}
          </div>
          <label class="skill-lbl">{t("label.model")}</label>
          <input
            class="cust-input"
            list="onboarding-model-options"
            value={model}
            onInput={(e) => {
              const value = (e.currentTarget as HTMLInputElement).value;
              if (value !== autoModel.current) autoModel.current = "";
              invalidateDiscovery(false);
              setModel(value);
            }}
            placeholder={ot("onboarding.path.modelPlaceholder")}
          />
          {modelOptions.length ? (
            <datalist id="onboarding-model-options">
              {modelOptions.map((item) => <option value={item} key={item} />)}
            </datalist>
          ) : null}
          <div class="onb-model-discovery">
            <button
              type="button"
              class="outline-btn small"
              disabled={busy || discovering}
              onClick={() => void fetchModels()}
            >
              {discovering ? ot("onboarding.path.fetchingModels") : ot("onboarding.path.fetchModels")}
            </button>
            {discoveryError ? <span class="onb-model-discovery-error">{discoveryError}</span> : null}
          </div>
          <button type="button" class="solid-btn" disabled={busy || discovering} onClick={submitCloud}>
            {ot("onboarding.next")}
          </button>
        </div>
      ) : null}
      {kind === "local" && locals.length ? (
        <div class="ds">{ot("onboarding.path.localHint")}</div>
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
                    provider: endpoint.provider,
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
          <div class="ds">{ot("onboarding.path.localEmpty")}</div>
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
  const create = (typed: string) => onCreate(typed.trim() || t("palette.action.newProject"));
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
              <span>{displayProjectName(row) || id}</span>
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
        data-onboarding-project-name=""
        value={name}
        onInput={(e) => setName((e.currentTarget as HTMLInputElement).value)}
        onKeyDown={(e) => {
          // Enter creates, as in the dashboard's project dialog. Not while an
          // IME is composing: that Enter confirms a candidate (229 is what
          // Safari reports for it, with isComposing already false).
          if (e.key !== "Enter" || e.isComposing || e.keyCode === 229) return;
          e.preventDefault();
          if (busy) return;
          create((e.currentTarget as HTMLInputElement).value);
        }}
        placeholder={t("projModal.name.placeholder")}
      />
      <button
        type="button"
        class="solid-btn"
        disabled={busy}
        onClick={() => create(name)}
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
  // Readiness re-check: its own pending/error/time state (not the wizard's
  // `busy`, which disables Skip), its own request sequence, and the visit it
  // was started in.
  const [refreshing, setRefreshing] = useState(false);
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  const [refreshError, setRefreshError] = useState("");
  const refreshRun = useRef(0);
  const readinessEpoch = useRef(0);
  const wasReadinessShown = useRef(false);
  const readinessShown = state.surface === "wizard" && state.step === "readiness";
  if (wasReadinessShown.current && !readinessShown) readinessEpoch.current += 1;
  wasReadinessShown.current = readinessShown;
  // ONB-2: the search card's pending choice, which Continue applies.
  const searchPending: SearchSetupHandle = useRef<SearchSetupHandle["current"]>(null);

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
      // Mounted after a failed first read: a member still never gets it.
      dispatch({ type: "hydrate", complete: next.complete || !next.can_configure });
      // One obvious choice is made for the user (M-5): the model in use, or
      // the only configured one. Continue then shows at once; it used to stay
      // hidden until the single radio was clicked.
      const preset = defaultPath(next);
      if (preset) dispatch({ type: "choosePath", path: preset });
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

  /**
   * Re-read the local onboarding snapshot without re-initialising the wizard.
   *
   * `load()` calls `hydrate`, which resets the step and evidence; a refresh
   * touches only `environment`, `network`, `platform` and
   * `native_runtime_supported`. The answer is dropped after unmount, after the
   * readiness step was left (the visit changed), or when a newer request has
   * taken over — a double click may issue two local reads and only the newest
   * one may shape the snapshot. A failed read keeps the old snapshot on screen
   * and reports "not refreshed: <reason>"; the action stays available, so it
   * is the retry.
   */
  const refreshReadiness = async () => {
    const run = (refreshRun.current += 1);
    const epoch = readinessEpoch.current;
    setRefreshing(true);
    setRefreshError("");
    try {
      const next = await fetchOnboarding();
      if (!alive.current) return;
      if (refreshRun.current !== run) return; // a newer refresh owns the state
      if (readinessEpoch.current !== epoch) {
        setRefreshing(false);
        return;
      }
      setStatus((prev) => (prev ? mergeReadinessRefresh(prev, next) : next));
      setRefreshedAt(Date.now());
    } catch (error) {
      if (!alive.current) return;
      if (refreshRun.current !== run) return;
      if (readinessEpoch.current !== epoch) {
        setRefreshing(false);
        return;
      }
      const fail = wizardErrorFromUnknown(error);
      setRefreshError(
        formatWizardError({ message: fail.message, requestId: fail.requestId }),
      );
    } finally {
      // Only the newest request settles the pending state; an older answer
      // (or one dropped because the user left) must not clear the spinner of
      // a newer request.
      if (alive.current && refreshRun.current === run) setRefreshing(false);
    }
  };

  const choosePath = (path: PathChoice, saved = false) => {
    leaveTest();
    dispatch({ type: "choosePath", path, saved });
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

  /** Save (or update) the profile for `path`, activate it, and return its id. */
  const saveProfile = async (path: PathChoice, apiKey: string): Promise<string> => {
    const body: Record<string, unknown> = {
      name: path.name.trim(),
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
    return id;
  };

  /**
   * Test with the environment's model selected (or nothing selected and no
   * active profile): save it as a keyless profile first, so the probe has a
   * profile to measure. Returns the saved id, or "" after reporting a failure.
   */
  const saveEnvironmentProfile = async (env: PathChoice): Promise<string> => {
    setBusy(true);
    try {
      const id = await saveProfile(env, "");
      if (!alive.current || !id) return "";
      // Not `choosePath`: that retires the probe this save is about to start.
      dispatch({ type: "choosePath", path: { ...env, profileId: id }, saved: true });
      const refreshed = await fetchOnboarding();
      if (alive.current) setStatus(refreshed);
      return id;
    } catch (error) {
      if (alive.current) fail(error);
      return "";
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
      const id = await saveProfile({ ...path, name: nm }, apiKey);
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
        choosePath(pathFromProfile(active), true);
        id = status.active_id;
      }
    }
    if (!id) {
      // The model the environment configures: chosen, or the only one there is.
      const env = isEnvironmentPath(state.path, status)
        ? state.path
        : !state.path
          ? environmentPath(status)
          : null;
      if (env) {
        id = await saveEnvironmentProfile(env);
        if (!id) return;
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
    // The environment's model has no profile yet: Continue saves it (no key;
    // the key stays in the environment) and activates it.
    if (state.path && isEnvironmentPath(state.path, status)) {
      await onSaveNew(state.path, "");
      return;
    }
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

  /** Continue on the Test and readiness steps. */
  const onContinue = async () => {
    if (state.step === "test") leaveTest();
    if (state.step === "readiness") {
      // ONB-2: a search choice made but not applied used to be dropped here
      // without a word. Apply it; a refusal (a missing key, a failed save)
      // is said in the search card and keeps the wizard on this step.
      const applyPending = searchPending.current;
      if (applyPending) {
        setBusy(true);
        let applied = false;
        try {
          applied = await applyPending();
        } finally {
          if (alive.current) setBusy(false);
        }
        if (!applied || !alive.current) return;
      }
      dispatch({ type: "markReadinessSeen" });
    }
    dispatch({ type: "next" });
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
        <div class="onb-top">
          <div id="onboarding-title" class="onb-h">
            {ot("onboarding.title")}
          </div>
          {/* The wizard is modal and covers the workbench's language switch. */}
          <button
            type="button"
            class="outline-btn small onb-lang"
            data-onboarding-lang=""
            title={ot("onboarding.lang.title")}
            onClick={() => void setLang(LANG === "zh" ? "en" : "zh")}
          >
            {ot("onboarding.lang.switch")}
          </button>
        </div>
        {/* Once, on the first step: it took a third of the box on every step (M-5). */}
        {state.surface === "wizard" && state.step !== "path" ? null : (
          <div class="onb-sub">{ot("onboarding.subtitle")}</div>
        )}
        {errorText ? (
          <div class="onb-error" role="alert">
            {errorText}
          </div>
        ) : null}
        {state.surface === "wizard" ? (
          <>
            {/* The rail moves between steps and marks the finished ones; the
                separate "Checklist" view listed the same four steps again. */}
            <nav class="onb-steps onb-rail" aria-label={ot("onboarding.checklist.title")}>
              {REQUIRED_STEPS.map((step, index) => {
                const done = state.decided.includes(step);
                const label = stepLabel(step);
                return (
                  <button
                    key={step}
                    type="button"
                    class={
                      "onb-step" +
                      (state.step === step ? " active" : "") +
                      (done ? " done" : "")
                    }
                    aria-current={state.step === step ? "step" : undefined}
                    title={ot("onboarding.step.goto", index + 1, label)}
                    aria-label={done ? ot("onboarding.step.done", label) : label}
                    disabled={busy}
                    onClick={() => {
                      if (step === state.step) return;
                      if (state.step === "test") leaveTest();
                      if (step === "readiness") dispatch({ type: "markReadinessSeen" });
                      dispatch({ type: "goto", step });
                    }}
                  >
                    <span class="onb-step-n" aria-hidden="true">
                      {done ? "✓" : index + 1}
                    </span>
                    {label}
                  </button>
                );
              })}
            </nav>
            {state.step === "path" && status ? (
              <PathStep
                status={status}
                state={state}
                busy={busy}
                onChoose={choosePath}
                onSaveNew={onSaveNew}
                onProviderRequest={() => dispatch({ type: "providerRequest" })}
              />
            ) : null}
            {state.step === "test" ? (
              <div>
                <div class="onb-warn">{ot("onboarding.test.warning")}</div>
                {state.providerRequests === 0 ? (
                  <div class="ds">{ot("onboarding.test.idle")}</div>
                ) : null}
                {state.testClicked && !testing && state.receipt?.reachable === true ? (
                  <div class="onb-ok" role="status" data-onboarding-test-ok="">
                    {ot("onboarding.test.ok")}
                  </div>
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
            {state.step === "readiness" && status ? (
              <>
                <ReadinessPanel
                  status={status}
                  onRefresh={() => { void refreshReadiness(); }}
                  refreshing={refreshing}
                  refreshedAt={refreshedAt}
                  refreshError={refreshError}
                />
                <SearchSetupCard pendingRef={searchPending} />
              </>
            ) : null}
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
          <button
            type="button"
            class="outline-btn"
            data-onboarding-skip=""
            disabled={busy}
            onClick={() => void onSkip()}
          >
            {ot("onboarding.skip")}
          </button>
          {state.surface === "wizard" && state.step !== "path" ? (
            <button
              type="button"
              class="outline-btn"
              data-onboarding-back=""
              disabled={busy}
              onClick={() => {
                if (state.step === "test") leaveTest();
                const index = REQUIRED_STEPS.indexOf(state.step);
                dispatch({ type: "goto", step: REQUIRED_STEPS[Math.max(0, index - 1)]! });
              }}
            >
              {ot("onboarding.back")}
            </button>
          ) : null}
          {state.surface === "wizard" && state.step !== "path" && state.step !== "project" ? (
            <button
              type="button"
              class="solid-btn"
              disabled={busy}
              onClick={() => void onContinue()}
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
