import type { ComponentChildren } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import { t } from "../../../i18n";
import { publicText } from "../../../features/scrub/scrub";
import { api, apiErrorText, ApiError } from "../../../features/customize/api";
import { custTab } from "../../../features/customize/actions";
import {
  asList,
  asString,
  confirmAction,
  hint,
  loadModels,
  refreshKeyBanner,
} from "../../../features/customize/host";
import {
  openVolcengineAuthorization,
  startVolcengineKeyPolling,
  volcApiKeyUrl,
  volcPercent,
  volcQuotaValue,
  VOLC_CHECK_FAILED_STATES,
  VOLC_CONFIGURE_REFRESH_CODES,
} from "../../../features/customize/volcengine";
import { useAlive, useTimerLease } from "../use-timer-lease";
import { Icon } from "../icons";

type VolcState = Record<string, unknown>;

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function VolcBtn({
  label,
  icon,
  class: cls = "outline-btn small",
  disabled,
  spinning,
  onClick,
}: {
  label: string;
  icon: string;
  class?: string;
  disabled?: boolean;
  spinning?: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" class={cls} disabled={disabled} onClick={onClick}>
      <Icon name={icon} size={14} spin={spinning} />
      <span>{label}</span>
    </button>
  );
}

function VolcNotice({
  tone,
  title,
  body,
}: {
  tone?: string;
  title: string;
  body?: string;
}) {
  const icon =
    tone === "ok" ? "check" : tone === "warn" ? "alert-triangle" : "terminal";
  return (
    <div class={"volc-notice " + (tone || "")}>
      <Icon name={icon} size={17} />
      <div class="volc-notice-copy">
        <div class="volc-notice-title">{title}</div>
        {body ? <div class="volc-notice-body">{body}</div> : null}
      </div>
    </div>
  );
}

function VolcExternal({
  label,
  url,
  icon = "globe",
  onOpen,
}: {
  label: string;
  url: string;
  icon?: string;
  onOpen?: () => void;
}) {
  return (
    <VolcBtn
      label={label}
      icon={icon}
      onClick={() => {
        openVolcengineAuthorization(url);
        if (onOpen) onOpen();
      }}
    />
  );
}

export function VolcenginePanel() {
  const alive = useAlive();
  const lease = useTimerLease();
  const [state, setState] = useState<VolcState>({});
  const [busy, setBusy] = useState(false);
  const [planKey, setPlanKey] = useState("");
  const [keyChoice, setKeyChoice] = useState("");
  const [endpointChoice, setEndpointChoice] = useState("");
  const [refreshMessage, setRefreshMessage] = useState("");
  const [polling, setPolling] = useState(false);
  const pollStop = useRef<(() => void) | null>(null);
  const configuring = useRef(false);

  const stopPoll = () => {
    pollStop.current?.();
    pollStop.current = null;
    setPolling(false);
  };

  useEffect(() => {
    return () => stopPoll();
  }, []);

  const applyState = (next: VolcState) => {
    if (!alive()) return;
    setState(next);
  };

  const refresh = async (opts?: {
    autoConfigure?: boolean;
    announce?: boolean;
  }): Promise<VolcState> => {
    const autoConfigure = opts?.autoConfigure !== false;
    const next = await api("/volcengine/refresh", { method: "POST" });
    const plans = asList(next.plans).filter(
      (plan) => plan && rec(plan).available !== false,
    );
    const accessState = asString(rec(next.access).state);
    const checkFailed = VOLC_CHECK_FAILED_STATES.has(accessState);
    if (alive()) {
      setRefreshMessage(opts?.announce && !checkFailed ? t("cust.volc.rechecked") : "");
      applyState(next);
    }
    if (
      autoConfigure &&
      !next.configured &&
      !next.linked &&
      plans.length === 1 &&
      accessState === "ready"
    ) {
      await configure(next, asString(rec(plans[0]).key));
    } else if (
      autoConfigure &&
      !next.configured &&
      !next.linked &&
      accessState === "platform_ready"
    ) {
      await configure(next, "platform", "", asString(rec(next.access).endpoint_choice));
    }
    return next;
  };

  const configure = async (
    current: VolcState,
    nextPlanKey: string,
    apiKeyChoice = "",
    nextEndpoint = "",
  ) => {
    if (configuring.current) return;
    configuring.current = true;
    setBusy(true);
    try {
      const result = await api("/volcengine/configure", {
        method: "POST",
        body: JSON.stringify({
          plan_key: nextPlanKey,
          api_key_choice: apiKeyChoice || undefined,
          endpoint_choice: nextEndpoint || undefined,
        }),
      });
      await loadModels();
      await refreshKeyBanner();
      applyState(rec(result.connection) || current);
      custTab("models");
    } catch (error) {
      setBusy(false);
      const code = error instanceof ApiError ? error.code : "";
      if (VOLC_CONFIGURE_REFRESH_CODES.has(code)) {
        try {
          await refresh({ autoConfigure: false });
          return;
        } catch {
          /* Fall through. */
        }
      }
      applyState({
        ...current,
        _error: t("cust.volc.configureFailed", apiErrorText(error)),
      });
    } finally {
      configuring.current = false;
    }
  };

  const startKeyPoll = () => {
    stopPoll();
    setPolling(true);
    const handle = startVolcengineKeyPolling(lease, {
      isAlive: alive,
      refresh: () => refresh({ autoConfigure: true }),
      onExhausted: () => {
        setPolling(false);
        applyState({ ...state });
      },
    });
    pollStop.current = () => {
      handle.stop();
      setPolling(false);
    };
  };

  const startLogin = async () => {
    let authWindow: Window | null = null;
    try {
      authWindow = window.open("about:blank", "_blank");
    } catch {
      /* Use the fallback button. */
    }
    try {
      if (authWindow) authWindow.opener = null;
    } catch {
      /* Best effort. */
    }
    try {
      const login = await api("/volcengine/login", {
        method: "POST",
        body: JSON.stringify({ mode: "device" }),
      });
      authWindow = openVolcengineAuthorization(
        asString(login.authorize_url),
        authWindow,
      );
      applyState({ ...state, login });
    } catch (error) {
      try {
        if (authWindow && !authWindow.closed) authWindow.close();
      } catch {
        /* Ignore blocked popups. */
      }
      applyState({ ...state, _error: apiErrorText(error) });
    }
  };

  useEffect(() => {
    void (async () => {
      try {
        const result = await api("/volcengine/connection");
        applyState(result);
      } catch (error) {
        applyState({ state: "error", _error: apiErrorText(error) });
      }
    })();
  }, [alive]);

  const login = rec(state.login);
  const identity = rec(state.identity);
  const access = rec(state.access);
  const plans = asList(state.plans).filter(
    (plan) => plan && rec(plan).available !== false,
  ) as Record<string, unknown>[];

  let statusText = t("cust.volc.disconnected");
  let statusClass = "";
  if (state.state === "connected") {
    statusText = t("cust.volc.connected");
    statusClass = " ok";
  } else if (state.state === "expired") statusText = t("cust.volc.expired");
  else if (state.state === "not_installed") statusText = t("cust.volc.notInstalled");

  const identityDetail = identity.name
    ? [identity.name, identity.project_name ? t("cust.volc.project", identity.project_name) : ""]
        .filter(Boolean)
        .join(" / ")
    : "";

  let selected = planKey || asString(state.configured_plan_key) || asString(access.plan_key);
  if (plans.length) {
    if (!plans.some((plan) => plan.key === selected)) selected = asString(plans[0]?.key);
  }
  const selectedPlan = plans.find((plan) => plan.key === selected) || null;
  const accessState =
    access.state === "plan_choice_required" && selectedPlan
      ? asString(selectedPlan.key_state)
      : asString(access.state) || (plans.length ? "key_check_failed" : "no_plan");
  const configuredForSelection = !!(
    state.configured && state.configured_plan_key === (selected || access.plan_key)
  );
  const resourceCheckFailed = VOLC_CHECK_FAILED_STATES.has(accessState);

  const body = (() => {
    if (state.state === "not_installed") {
      return (
        <div class="volc-actions">
          <VolcBtn
            label={t("cust.volc.getConnector")}
            icon="globe"
            class="solid-btn small"
            onClick={() => {
              window.open("https://github.com/volcengine/ark-cli", "_blank", "noopener");
            }}
          />
        </div>
      );
    }
    if (login.state === "connecting") {
      return (
        <>
          <VolcNotice tone="info" title={t("cust.volc.authTitle")} body={t("cust.volc.connecting")} />
          <div class="volc-project-hint">{t("cust.volc.projectHint")}</div>
          <div class="volc-actions">
            <VolcBtn
              label={t("cust.volc.cancel")}
              icon="x"
              onClick={async () => {
                try {
                  const next = await api("/volcengine/login/cancel", { method: "POST" });
                  applyState({ ...state, login: next });
                } catch (error) {
                  hint(apiErrorText(error), true);
                }
              }}
            />
          </div>
        </>
      );
    }
    if (login.state === "awaiting_code") {
      return (
        <AwaitingCode
          state={state}
          login={login}
          applyState={applyState}
          refresh={refresh}
        />
      );
    }
    if (login.state === "failed") {
      return (
        <FailedLogin
          state={state}
          login={login}
          startLogin={startLogin}
          refresh={refresh}
        />
      );
    }
    if (state.state !== "connected") {
      const prepKey = identity.project_name ? "cust.volc.reconnectPrep" : "cust.volc.loginPrep";
      return (
        <>
          {state._error ? (
            <div class="timeline-error">{publicText(state._error, 240)}</div>
          ) : null}
          <div class="volc-login-prep">{t(prepKey)}</div>
          <div class="volc-actions">
            <VolcBtn
              label={t("cust.volc.connect")}
              icon="link"
              class="solid-btn small"
              onClick={() => void startLogin()}
            />
          </div>
        </>
      );
    }

    const actions: ComponentChildren[] = [];
    const usageItems = asList(rec(state.usage).items).filter(
      (item) => !rec(item).product || rec(item).product === selected,
    );
    const periods = usageItems.flatMap((item) =>
      Array.isArray(rec(item).periods) ? rec(item).periods : [],
    );

    if (accessState === "no_plan") {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.connectedNoAccessTitle")}
          body={t("cust.volc.noPlanBody")}
        />,
        <VolcExternal
          key="plans"
          label={t("cust.volc.viewPlans")}
          url="https://www.volcengine.com/activity/agentplan"
        />,
        polling ? (
          <span key="wait" class="volc-key-wait">
            {t("cust.volc.keyWaiting")}
          </span>
        ) : (
          <VolcExternal
            key="key"
            label={t("cust.volc.createKey")}
            url={volcApiKeyUrl(state)}
            icon="lock"
            onOpen={startKeyPoll}
          />
        ),
      );
    } else if (accessState === "key_missing") {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.keyMissingTitle")}
          body={t("cust.volc.keyMissingBody")}
        />,
        polling ? (
          <span key="wait" class="volc-key-wait">
            {t("cust.volc.keyWaiting")}
          </span>
        ) : (
          <VolcExternal
            key="key"
            label={t("cust.volc.createKey")}
            url={volcApiKeyUrl(state)}
            icon="lock"
            onOpen={startKeyPoll}
          />
        ),
      );
    } else if (accessState === "key_choice_required" && configuredForSelection) {
      actions.push(
        <span key="ready" class="volc-ready">
          {t("cust.volc.ready")}
        </span>,
      );
    } else if (accessState === "key_choice_required") {
      const choices = asList(
        selectedPlan && selectedPlan.key_choices
          ? selectedPlan.key_choices
          : access.key_choices,
      ) as Record<string, unknown>[];
      let currentKey = keyChoice;
      if (!choices.some((c) => c.id === currentKey))
        currentKey = asString(choices[0]?.id);
      const endpointChoices = asList(access.endpoint_choices) as Record<string, unknown>[];
      let currentEp = endpointChoice;
      if (endpointChoices.length && !endpointChoices.some((c) => c.id === currentEp))
        currentEp = asString(endpointChoices[0]?.id);
      actions.push(
        <VolcNotice
          key="n"
          tone="info"
          title={t("cust.volc.keyChoiceTitle")}
          body={t("cust.volc.keyChoiceBody")}
        />,
        <div key="kc" class="volc-plan-row">
          <label class="skill-lbl">{t("cust.volc.apiKey")}</label>
          <select
            class="cust-input"
            value={currentKey}
            onChange={(e) => setKeyChoice((e.target as HTMLSelectElement).value)}
          >
            {choices.map((choice) => (
              <option value={asString(choice.id)} key={asString(choice.id)}>
                {choice.suffix
                  ? t("cust.volc.keyName", asString(choice.name) || t("cust.volc.apiKey"), choice.suffix)
                  : asString(choice.name) || t("cust.volc.apiKey")}
              </option>
            ))}
          </select>
        </div>,
        endpointChoices.length ? (
          <div key="ep" class="volc-plan-row">
            <label class="skill-lbl">{t("cust.volc.endpoint")}</label>
            <select
              class="cust-input"
              value={currentEp}
              onChange={(e) => setEndpointChoice((e.target as HTMLSelectElement).value)}
            >
              {endpointChoices.map((choice) => (
                <option value={asString(choice.id)} key={asString(choice.id)}>
                  {asString(choice.name || choice.suffix) || t("cust.volc.endpoint")}
                </option>
              ))}
            </select>
          </div>
        ) : null,
        <VolcBtn
          key="use"
          label={t("cust.volc.usePlan")}
          icon="check"
          class="solid-btn small"
          disabled={!currentKey}
          onClick={() =>
            void configure(
              state,
              selected || asString(access.plan_key),
              currentKey,
              endpointChoices.length ? currentEp : "",
            )
          }
        />,
      );
    } else if (["profile_missing", "profile_ambiguous"].includes(accessState)) {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.profileMissingTitle")}
          body={t("cust.volc.profileMissingBody")}
        />,
        <VolcBtn
          key="setup"
          label={t("cust.volc.retrySetup")}
          icon="refresh"
          class="solid-btn small"
          onClick={() => void startLogin()}
        />,
      );
    } else if (accessState === "plan_inactive") {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.planInactiveTitle")}
          body={t("cust.volc.planInactiveBody")}
        />,
        <VolcExternal
          key="plans"
          label={t("cust.volc.viewPlans")}
          url="https://www.volcengine.com/activity/agentplan"
        />,
      );
    } else if (accessState === "seat_required") {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.seatTitle")}
          body={t("cust.volc.seatBody")}
        />,
        <VolcExternal
          key="ark"
          label={t("cust.volc.viewPlans")}
          url="https://console.volcengine.com/ark"
        />,
      );
    } else if (accessState === "quota_exhausted") {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.quotaTitle")}
          body={t("cust.volc.quotaBody")}
        />,
        <VolcExternal
          key="plans"
          label={t("cust.volc.viewPlans")}
          url="https://www.volcengine.com/activity/agentplan"
        />,
      );
    } else if (configuredForSelection && resourceCheckFailed) {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.checkFailedTitle")}
          body={t("cust.volc.checkFailedBody")}
        />,
        <span key="ready" class="volc-ready">
          {t("cust.volc.ready")}
        </span>,
      );
    } else if (configuredForSelection) {
      actions.push(
        <span key="ready" class="volc-ready">
          {t("cust.volc.ready")}
        </span>,
      );
    } else if (accessState === "platform_ready") {
      actions.push(
        <VolcNotice
          key="n"
          tone="ok"
          title={t("cust.volc.platformReadyTitle")}
          body={t("cust.volc.platformReadyBody")}
        />,
        <VolcBtn
          key="use"
          label={t("cust.volc.useEndpoint")}
          icon="check"
          class="solid-btn small"
          onClick={() =>
            void configure(state, "platform", "", asString(access.endpoint_choice))
          }
        />,
      );
    } else if (accessState === "endpoint_choice_required") {
      const choices = asList(access.endpoint_choices) as Record<string, unknown>[];
      let currentEp = endpointChoice;
      if (!choices.some((c) => c.id === currentEp)) currentEp = asString(choices[0]?.id);
      actions.push(
        <VolcNotice
          key="n"
          tone="info"
          title={t("cust.volc.endpointChoiceTitle")}
          body={t("cust.volc.endpointChoiceBody")}
        />,
        <div key="ep" class="volc-plan-row">
          <label class="skill-lbl">{t("cust.volc.endpoint")}</label>
          <select
            class="cust-input"
            value={currentEp}
            onChange={(e) => setEndpointChoice((e.target as HTMLSelectElement).value)}
          >
            {choices.map((choice) => (
              <option value={asString(choice.id)} key={asString(choice.id)}>
                {asString(choice.name || choice.suffix) || t("cust.volc.endpoint")}
              </option>
            ))}
          </select>
        </div>,
        <VolcBtn
          key="use"
          label={t("cust.volc.useEndpoint")}
          icon="check"
          class="solid-btn small"
          disabled={!currentEp}
          onClick={() => void configure(state, "platform", "", currentEp)}
        />,
      );
    } else if (accessState === "platform_endpoint_required") {
      actions.push(
        <VolcNotice
          key="n"
          tone="info"
          title={t("cust.volc.platformTitle")}
          body={t("cust.volc.platformBody")}
        />,
        <VolcExternal
          key="ep"
          label={t("cust.volc.openEndpoints")}
          url="https://console.volcengine.com/ark"
        />,
      );
    } else if (resourceCheckFailed) {
      actions.push(
        <VolcNotice
          key="n"
          tone="warn"
          title={t("cust.volc.checkFailedTitle")}
          body={t("cust.volc.checkFailedBody")}
        />,
      );
    } else if (plans.length) {
      actions.push(
        <VolcBtn
          key="use"
          label={t("cust.volc.usePlan")}
          icon="check"
          class="solid-btn small"
          onClick={() => void configure(state, selected)}
        />,
      );
    }

    return (
      <>
        {state._error ? (
          <div class="timeline-error">{publicText(state._error, 240)}</div>
        ) : null}
        {plans.length > 1 ? (
          <>
            <VolcNotice
              tone="info"
              title={t("cust.volc.choiceTitle")}
              body={t("cust.volc.choiceBody")}
            />
            <div class="volc-plan-row">
              <label class="skill-lbl">{t("cust.volc.plan")}</label>
              <select
                class="cust-input"
                value={selected}
                onChange={(e) => setPlanKey((e.target as HTMLSelectElement).value)}
              >
                {plans.map((plan) => (
                  <option value={asString(plan.key)} key={asString(plan.key)}>
                    {[plan.name || plan.key, plan.tier, plan.scope].filter(Boolean).join(" / ")}
                  </option>
                ))}
              </select>
            </div>
          </>
        ) : null}
        {periods.length ? (
          <>
            <div class="cust-subhead volc-quota-title">{t("cust.volc.quota")}</div>
            <div class="volc-quotas">
              {periods.map((period, i) => {
                const p = rec(period);
                const parsed = p.reset_at ? new Date(String(p.reset_at)) : null;
                const reset =
                  parsed && !Number.isNaN(parsed.getTime())
                    ? parsed.toLocaleString()
                    : p.reset_at;
                return (
                  <div class="volc-quota" key={i}>
                    <div class="volc-quota-labels">
                      <span>{publicText(p.label, 24)}</span>
                      <span>{volcQuotaValue(period)}</span>
                    </div>
                    <div class="volc-progress">
                      <span style={{ width: `${volcPercent(period)}%` }} />
                    </div>
                    {reset ? (
                      <div class="volc-reset">{t("cust.volc.reset", publicText(reset, 80))}</div>
                    ) : null}
                  </div>
                );
              })}
            </div>
          </>
        ) : null}
        <div class="volc-actions">
          {actions}
          <RecheckButton
            refresh={refresh}
            applyState={applyState}
            state={state}
            refreshMessage={refreshMessage}
            setRefreshMessage={setRefreshMessage}
          />
          <VolcBtn
            label={t("cust.volc.switch")}
            icon="refresh"
            onClick={() => void startLogin()}
          />
          {state.linked ? (
            <VolcBtn
              label={t("cust.volc.disconnect")}
              icon="x"
              onClick={async () => {
                if (!confirmAction(t("cust.volc.disconnectConfirm"))) return;
                try {
                  await api("/volcengine/disconnect", {
                    method: "POST",
                    body: JSON.stringify({ confirm: true }),
                  });
                  await loadModels();
                  await refreshKeyBanner();
                  custTab("models");
                } catch (error) {
                  hint(apiErrorText(error), true);
                }
              }}
            />
          ) : null}
        </div>
      </>
    );
  })();

  return (
    <div class={"volc-panel" + (busy ? " busy" : "")}>
      <div class="volc-head">
        <div class="info">
          <div class="nm">{t("cust.volc.title")}</div>
          {identityDetail ? <div class="ds">{identityDetail}</div> : null}
        </div>
        <span class={"volc-status" + statusClass}>{statusText}</span>
      </div>
      {body}
    </div>
  );
}

function RecheckButton({
  refresh,
  applyState,
  state,
  refreshMessage,
  setRefreshMessage,
}: {
  refresh: (opts?: { announce?: boolean }) => Promise<VolcState>;
  applyState: (s: VolcState) => void;
  state: VolcState;
  refreshMessage: string;
  setRefreshMessage: (s: string) => void;
}) {
  const [spin, setSpin] = useState(false);
  return (
    <>
      <VolcBtn
        label={spin ? t("cust.volc.rechecking") : t("cust.volc.recheck")}
        icon="refresh"
        disabled={spin}
        spinning={spin}
        onClick={async () => {
          setSpin(true);
          setRefreshMessage("");
          try {
            await refresh({ announce: true });
          } catch (error) {
            applyState({
              ...state,
              _error: t("cust.volc.refreshFailed", apiErrorText(error)),
            });
          } finally {
            setSpin(false);
          }
        }}
      />
      {refreshMessage ? <span class="volc-key-wait">{refreshMessage}</span> : null}
    </>
  );
}

function AwaitingCode({
  state,
  login,
  applyState,
  refresh,
}: {
  state: VolcState;
  login: Record<string, unknown>;
  applyState: (s: VolcState) => void;
  refresh: () => Promise<VolcState>;
}) {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <>
      <VolcNotice tone="info" title={t("cust.volc.authTitle")} body={t("cust.volc.authBody")} />
      {state._error ? (
        <div class="timeline-error">{publicText(state._error, 240)}</div>
      ) : null}
      <div class="volc-project-hint">{t("cust.volc.projectHint")}</div>
      <div class="volc-actions">
        <VolcBtn
          label={t("cust.volc.openAuth")}
          icon="globe"
          class="solid-btn small"
          onClick={() => openVolcengineAuthorization(asString(login.authorize_url))}
        />
        <input
          class="cust-input volc-code"
          placeholder={t("cust.volc.codePlaceholder")}
          autocomplete="off"
          value={code}
          onInput={(e) => setCode((e.target as HTMLInputElement).value)}
        />
        <VolcBtn
          label={t("cust.volc.complete")}
          icon="link"
          disabled={busy}
          onClick={async () => {
            const value = code.trim();
            if (!value) return;
            setBusy(true);
            try {
              await api("/volcengine/login/complete", {
                method: "POST",
                body: JSON.stringify({ code: value }),
              });
              setCode("");
              await refresh();
            } catch (error) {
              setBusy(false);
              try {
                const next = await api("/volcengine/connection");
                applyState({ ...next, _error: apiErrorText(error) });
              } catch {
                hint(apiErrorText(error), true);
              }
            }
          }}
        />
        <VolcBtn
          label={t("cust.volc.cancel")}
          icon="x"
          onClick={async () => {
            try {
              const next = await api("/volcengine/login/cancel", { method: "POST" });
              applyState({ ...state, login: next });
            } catch (error) {
              hint(apiErrorText(error), true);
            }
          }}
        />
      </div>
    </>
  );
}

function FailedLogin({
  state,
  login,
  startLogin,
  refresh,
}: {
  state: VolcState;
  login: Record<string, unknown>;
  startLogin: () => Promise<void>;
  refresh: () => Promise<VolcState>;
}) {
  const code = asString(login.error_code);
  const detail = asString(login.error_detail || state._error || code);
  let notice: ComponentChildren;
  if (code === "project_selection_required") {
    notice = (
      <VolcNotice
        tone="warn"
        title={t("cust.volc.projectRequiredTitle")}
        body={`${t("cust.volc.projectRequiredBody")} ${detail && detail !== code ? detail : ""}`.trim()}
      />
    );
  } else if (code === "interactive_terminal_unavailable") {
    notice = (
      <VolcNotice
        tone="warn"
        title={t("cust.volc.cliSetupTitle")}
        body={`${t("cust.volc.cliSetupBody")} ${detail && detail !== code ? detail : ""}`.trim()}
      />
    );
  } else {
    notice = <VolcNotice tone="warn" title={t("cust.volc.failed")} body={detail} />;
  }
  return (
    <>
      {notice}
      <div class="volc-actions">
        <VolcBtn
          label={t("cust.volc.retrySetup")}
          icon="refresh"
          class="solid-btn small"
          onClick={() => void startLogin()}
        />
        {state.state !== "connected" ? (
          <VolcBtn
            label={t("cust.volc.recheck")}
            icon="refresh"
            onClick={async () => {
              try {
                await refresh();
              } catch (error) {
                hint(t("cust.volc.refreshFailed", apiErrorText(error)), true);
              }
            }}
          />
        ) : null}
      </div>
    </>
  );
}
