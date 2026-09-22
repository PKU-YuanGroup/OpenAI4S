import { useEffect, useState } from "preact/hooks";
import { LANG } from "../../i18n";
import {
  apiErrorText,
  getJudgmentStatus,
  testJudgmentConnection,
  updateJudgmentSettings,
  type JudgmentProbe,
  type JudgmentStatus,
  type JudgmentUpdate,
} from "../../features/customize/api";
import { hint } from "../../features/customize/host";
import {
  judgmentSourceLabel,
  judgmentT as t,
} from "../../features/judgment/copy";
import "../../features/judgment/judgment.css";
import { useAlive } from "./use-timer-lease";
import { CustRow, Toggle } from "./ui";

const CAP_ORDER = [
  "skill_suggest",
  "literature_check",
  "text_features",
  "safety_shadow",
  "task_mode_shadow",
];

function capabilityNames(status: JudgmentStatus): string[] {
  const present = Object.keys(status.effective).filter((name) => name !== "master");
  const known = CAP_ORDER.filter((name) => present.includes(name));
  const extra = present.filter((name) => !CAP_ORDER.includes(name));
  return [...known, ...extra];
}

function disclosureNames(status: JudgmentStatus): string[] {
  const names = Object.keys(status.disclosure.capabilities);
  const known = CAP_ORDER.filter((name) => names.includes(name));
  const extra = names.filter((name) => !CAP_ORDER.includes(name));
  return [...known, ...extra];
}

function capLabel(name: string): string {
  const key = "judgment.cap." + name;
  const labelled = t(key);
  return labelled === key ? name : labelled;
}

function disclosureText(status: JudgmentStatus, name: string): string {
  const cap = status.disclosure.capabilities[name];
  if (!cap) return "";
  return (LANG === "zh" ? cap.zh : cap.en) || cap.en || cap.zh || "";
}

function factsText(status: JudgmentStatus): string {
  const facts = status.disclosure.facts;
  return (LANG === "zh" ? facts.zh : facts.en) || facts.en || facts.zh || "";
}

function sourceLine(source: string): string {
  return t("judgment.source", judgmentSourceLabel(source));
}

export function ExperimentsTab() {
  const alive = useAlive();
  const [status, setStatus] = useState<JudgmentStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [keyDraft, setKeyDraft] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [testing, setTesting] = useState(false);
  const [probe, setProbe] = useState<JudgmentProbe | null>(null);
  const [disclosureOpen, setDisclosureOpen] = useState(false);
  const [pendingCapability, setPendingCapability] = useState<string | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);

  const apply = (next: JudgmentStatus) => {
    setStatus(next);
    setErr(null);
    setProbe(null);
  };

  useEffect(() => {
    void (async () => {
      try {
        const next = await getJudgmentStatus();
        if (alive()) apply(next);
      } catch (error) {
        if (alive()) setErr(t("judgment.loadFailed", apiErrorText(error)));
      }
    })();
  }, [alive]);

  const save = async (body: JudgmentUpdate): Promise<JudgmentStatus | null> => {
    setBusy(true);
    try {
      const next = await updateJudgmentSettings(body);
      if (!alive()) return null;
      apply(next);
      return next;
    } catch (error) {
      if (alive()) {
        const message = t("judgment.saveFailed", apiErrorText(error));
        setErr(message);
        hint(message, true);
      }
      return null;
    } finally {
      if (alive()) setBusy(false);
    }
  };

  const master = status?.effective.master;
  const masterOn = !!master?.enabled;
  const masterForcedOff = master?.source === "env_off";
  const masterForced = masterForcedOff || master?.source === "env_on";
  const updating = busy || savingKey || testing;
  const needsAck = !!status && !status.disclosure.acked;
  const listedDisclosure = status ? disclosureNames(status) : [];
  const allChecked =
    listedDisclosure.length > 0 && listedDisclosure.every((name) => checked[name]);

  const openDisclosure = (capability: string | null = null) => {
    if (!status) return;
    const initial: Record<string, boolean> = {};
    for (const name of disclosureNames(status)) initial[name] = false;
    setChecked(initial);
    setPendingCapability(capability);
    setDisclosureOpen(true);
  };

  const onMaster = () => {
    if (!status || updating || masterForced) return;
    if (masterOn) {
      void save({ enabled: false });
      return;
    }
    if (needsAck) {
      openDisclosure();
      return;
    }
    void save({ enabled: true });
  };

  const confirmDisclosure = async () => {
    if (!status || !allChecked || updating) return;
    const next = await save({
      ...(pendingCapability
        ? { capabilities: { [pendingCapability]: true } }
        : { enabled: true }),
      acknowledge: {
        version: status.disclosure.version,
        provider: status.provider,
        capabilities: listedDisclosure.filter((name) => checked[name]),
      },
    });
    if (next) setDisclosureOpen(false);
  };

  const onCap = (name: string) => {
    if (!status || updating || !masterOn) return;
    const flag = status.effective[name];
    if (!flag || flag.source === "env_off" || flag.source === "env_on") return;
    if (!flag.enabled && (flag.source === "no_disclosure"
      || !status.disclosure.acknowledged_capabilities.includes(name))) {
      openDisclosure(name);
      return;
    }
    void save({ capabilities: { [name]: !flag.enabled } });
  };

  const saveKey = async () => {
    if (updating) return;
    const secret = keyDraft.trim();
    setKeyDraft("");
    if (!secret) {
      hint(t("judgment.keyRequired"), true);
      return;
    }
    setSavingKey(true);
    try {
      const next = await updateJudgmentSettings({ api_key: secret });
      if (!alive()) return;
      apply(next);
      hint(t("judgment.keySaved"));
    } catch (error) {
      if (alive()) {
        const message = t("judgment.saveFailed", apiErrorText(error));
        setErr(message);
        hint(message, true);
      }
    } finally {
      if (alive()) {
        setKeyDraft("");
        setSavingKey(false);
      }
    }
  };

  const clearKey = async () => {
    if (updating) return;
    setSavingKey(true);
    setKeyDraft("");
    try {
      const next = await updateJudgmentSettings({ clear_api_key: true });
      if (!alive()) return;
      apply(next);
      hint(t("judgment.keyCleared"));
    } catch (error) {
      if (alive()) {
        const message = t("judgment.saveFailed", apiErrorText(error));
        setErr(message);
        hint(message, true);
      }
    } finally {
      if (alive()) setSavingKey(false);
    }
  };

  const runTest = async () => {
    if (updating) return;
    setTesting(true);
    try {
      const result = await testJudgmentConnection();
      if (alive()) setProbe(result);
    } catch (error) {
      if (alive()) {
        setProbe({
          status: "unavailable",
          error_code: apiErrorText(error),
          latency_ms: 0,
          model: null,
        });
      }
    } finally {
      if (alive()) setTesting(false);
    }
  };

  if (!status && !err) return null;

  const probeClass =
    probe && (probe.status === "ok" || probe.status === "uncertain")
      ? "judgment-test-result ok"
      : probe
        ? "judgment-test-result bad"
        : "judgment-test-result";

  return (
    <div class="judgment-block" data-judgment="1">
      <div>
        <div class="cust-h judgment-title-row">
          <span>{t("judgment.title")}</span>
          <span class="judgment-badge pill">{t("judgment.badge")}</span>
        </div>
        <div class="cust-sub">{t("judgment.intro." + (status?.provider || "typesafe"))}</div>
      </div>
      {err ? (
        <div class="cust-note" role="alert" data-judgment-error="1">
          {err}
        </div>
      ) : null}
      {status ? (
        <>
          <CustRow
            name={t("judgment.master")}
            desc={
              <>
                <div>{t("judgment.masterDesc")}</div>
                {master ? (
                  <div class="judgment-source">{sourceLine(master.source)}</div>
                ) : null}
                {masterForced ? (
                  <div class="judgment-env-off">{t(masterForcedOff ? "judgment.envOff" : "judgment.envOn")}</div>
                ) : null}
              </>
            }
          >
            <span data-judgment-master={masterOn ? "on" : "off"}>
              <Toggle
                on={masterOn}
                disabled={updating || masterForced}
                title={masterForced ? t(masterForcedOff ? "judgment.envOff" : "judgment.envOn") : undefined}
                onClick={onMaster}
              />
            </span>
          </CustRow>
          {capabilityNames(status).map((name) => {
            const flag = status.effective[name];
            if (!flag) return null;
            const forced = flag.source === "env_off" || flag.source === "env_on";
            const forcedLabel = flag.source === "env_off" ? "judgment.envOff" : "judgment.envOn";
            const safety = name === "safety_shadow";
            return (
              <CustRow
                key={name}
                name={capLabel(name)}
                desc={
                  <>
                    <div class="judgment-source">{sourceLine(flag.source)}</div>
                    {forced ? (
                      <div class="judgment-env-off">{t(forcedLabel)}</div>
                    ) : null}
                    {safety ? (
                      <div class="judgment-safety">{t("judgment.safetyWarn")}</div>
                    ) : null}
                  </>
                }
              >
                <span data-judgment-cap={name} data-judgment-cap-on={flag.enabled ? "on" : "off"}>
                  <Toggle
                    on={flag.enabled}
                    disabled={updating || forced || !masterOn}
                    title={forced ? t(forcedLabel) : !masterOn ? t("judgment.source.master_off") : undefined}
                    onClick={() => onCap(name)}
                  />
                </span>
              </CustRow>
            );
          })}
          {status.provider === "typesafe" ? <CustRow
            name={t("judgment.key")}
            desc={
              <>
                <div class="job-submit">
                  <input
                    class="cust-input"
                    type="password"
                    autocomplete="off"
                    autocapitalize="off"
                    spellcheck={false}
                    placeholder={t("judgment.keyPh")}
                    data-judgment-key="1"
                    value={keyDraft}
                    disabled={updating}
                    onInput={(e) => setKeyDraft((e.target as HTMLInputElement).value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void saveKey();
                      }
                    }}
                  />
                  <button
                    type="button"
                    class="solid-btn small"
                    data-judgment-save-key="1"
                    disabled={updating}
                    onClick={() => void saveKey()}
                  >
                    {t("judgment.keySave")}
                  </button>
                  <button
                    type="button"
                    class="outline-btn small"
                    data-judgment-clear-key="1"
                    disabled={updating}
                    onClick={() => void clearKey()}
                  >
                    {t("judgment.keyClear")}
                  </button>
                </div>
                <div
                  class="judgment-key-state"
                  data-judgment-key-state={status.key_configured ? "configured" : "missing"}
                >
                  {status.key_configured
                    ? t("judgment.keyConfigured")
                    : t("judgment.keyMissing")}
                </div>
              </>
            }
          /> : status.provider === "llm" ? (
            <CustRow name={t("judgment.llmKey")} desc={t("judgment.llmKeyDesc")}>
              <span data-judgment-key-state={status.key_configured ? "configured" : "missing"}>
                {t(status.key_configured ? "judgment.keyConfigured" : "judgment.keyMissing")}
              </span>
            </CustRow>
          ) : null}
          <CustRow name={t("judgment.test")}>
            <button
              type="button"
              class="outline-btn small"
              data-judgment-test="1"
              disabled={updating}
              onClick={() => void runTest()}
            >
              {t("judgment.test")}
            </button>
          </CustRow>
          <div
            class={probeClass}
            data-judgment-test-result={probe ? probe.status : "idle"}
            aria-live="polite"
          >
            {probe
              ? [
                  t("judgment.testStatus", probe.status),
                  probe.error_code
                    ? t("judgment.testError", probe.error_code)
                    : null,
                  t("judgment.testLatency", probe.latency_ms),
                ]
                  .filter(Boolean)
                  .join(" · ")
              : t("judgment.testIdle")}
          </div>
          <CustRow
            name={t("judgment.status")}
            desc={
              <>
                <div data-judgment-provider="1">{t("judgment.provider", status.provider)}</div>
                <div data-judgment-model="1">{t("judgment.model", status.model)}</div>
                <div data-judgment-egress="1">
                  {t("judgment.egress", status.egress.mode)}
                  {" · "}
                  {!status.egress.host
                    ? t("judgment.egressNone")
                    : status.egress.domain_allowed
                      ? t("judgment.egressAllowed", status.egress.host)
                      : t("judgment.egressNotListed", status.egress.host)}
                </div>
                {status.egress.remediation ? (
                  <div class="judgment-remediation" data-judgment-remediation="1">
                    {status.egress.remediation}
                  </div>
                ) : null}
              </>
            }
          />
        </>
      ) : null}
      {disclosureOpen && status ? (
        <div class="judgment-disclosure" data-judgment-disclosure="1" role="dialog" aria-modal="true">
          <div class="judgment-disclosure-box">
            <div class="cust-h">{t("judgment.disclosure.title")}</div>
            <div class="cust-sub">{t("judgment.disclosure.intro", status.egress.host)}</div>
            <div class="cust-sub">{t("judgment.ackVersion", status.disclosure.version)}</div>
            {factsText(status) ? (
              <div class="judgment-disclosure-facts">{factsText(status)}</div>
            ) : null}
            {listedDisclosure.map((name) => (
              <div
                key={name}
                class={"judgment-disclosure-item" + (name === "safety_shadow" ? " safety" : "")}
              >
                <input
                  id={"judgment-ack-" + name}
                  type="checkbox"
                  data-judgment-ack-cap={name}
                  checked={!!checked[name]}
                  onChange={(e) => {
                    const on = (e.target as HTMLInputElement).checked;
                    setChecked((prev) => ({ ...prev, [name]: on }));
                  }}
                />
                <label for={"judgment-ack-" + name}>
                  <div class="nm">{capLabel(name)}</div>
                  <div class="ds">{disclosureText(status, name)}</div>
                  {name === "safety_shadow" ? (
                    <div class="judgment-safety">{t("judgment.safetyWarn")}</div>
                  ) : null}
                </label>
              </div>
            ))}
            {!allChecked ? (
              <div class="cust-sub">{t("judgment.disclosure.needAll")}</div>
            ) : null}
            <div class="form-actions">
              <button
                type="button"
                class="solid-btn small"
                data-judgment-ack="1"
                disabled={!allChecked || updating}
                onClick={() => void confirmDisclosure()}
              >
                {t("judgment.disclosure.confirm")}
              </button>
              <button
                type="button"
                class="outline-btn small"
                data-judgment-disclosure-cancel="1"
                disabled={updating}
                onClick={() => setDisclosureOpen(false)}
              >
                {t("judgment.disclosure.cancel")}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
