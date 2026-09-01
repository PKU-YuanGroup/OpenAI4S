import { useState } from "preact/hooks";
import { t } from "../../../i18n";
import { api, apiErrorText } from "../../../features/customize/api";
import { currentId } from "../../../stores/session";
import {
  DATAPRO_CONNECTOR_ID,
  dataproIndexComplete,
  dataproResponseCode,
  dataproResultText,
} from "../../../features/customize/vendors";
import {
  asString,
  dropSkillsCatalog,
  hint,
  openViewer,
} from "../../../features/customize/host";

export function DataProCard({
  config,
  configError,
}: {
  config: Record<string, unknown>;
  configError: unknown;
}) {
  const [keyConfigured, setKeyConfigured] = useState(!!config.key_configured);
  const [arkKeyReused, setArkKeyReused] = useState(!!config.ark_key_reused);
  const [connectorEnabled, setConnectorEnabled] = useState(!!config.connector_enabled);
  const [skillEnabled, setSkillEnabled] = useState(!!config.skill_enabled);
  const [skillBusy, setSkillBusy] = useState(false);
  const [key, setKey] = useState("");
  const [keyState, setKeyState] = useState(
    configError
      ? t("cust.datapro.requestFailed", apiErrorText(configError))
      : arkKeyReused
        ? t("cust.datapro.keyArkReused")
        : keyConfigured
          ? t("cust.datapro.keyConfigured")
          : t("cust.datapro.keyMissing"),
  );
  const [keyBad, setKeyBad] = useState(!!configError || (!keyConfigured && !arkKeyReused));
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [statusClass, setStatusClass] = useState("datapro-status");
  const [indexText, setIndexText] = useState("");
  const [indexClass, setIndexClass] = useState("datapro-index-status hidden");
  const [result, setResult] = useState(t("cust.datapro.noResult"));
  const [artifact, setArtifact] = useState<Record<string, unknown> | null>(null);
  const [searching, setSearching] = useState(false);
  const [savingKey, setSavingKey] = useState(false);

  const placeholder = arkKeyReused
    ? t("cust.datapro.keyPlaceholderArk")
    : keyConfigured
      ? t("cust.datapro.keyPlaceholderSet")
      : t("cust.datapro.keyPlaceholder");

  const saveKey = async () => {
    let secret = key.trim();
    setKey("");
    if (!secret) {
      hint(t("cust.datapro.keyRequired"), true);
      return;
    }
    setSavingKey(true);
    const request = api("/datapro/config", {
      method: "POST",
      body: JSON.stringify({ agent_plan_key: secret }),
    });
    secret = "";
    try {
      const saved = await request;
      setKeyConfigured(!!saved.key_configured);
      setArkKeyReused(!!saved.ark_key_reused);
      setKeyState(
        saved.ark_key_reused ? t("cust.datapro.keyArkReused") : t("cust.datapro.keyConfigured"),
      );
      setKeyBad(false);
      hint(t("cust.datapro.keySaved"));
    } catch (error) {
      setKeyState(t("cust.datapro.requestFailed", apiErrorText(error)));
      setKeyBad(true);
    } finally {
      setKey("");
      setSavingKey(false);
    }
  };

  const runSearch = async () => {
    const text = query.trim();
    if (!text) {
      hint(t("cust.datapro.queryRequired"), true);
      return;
    }
    setSearching(true);
    setStatus(t("cust.datapro.searching"));
    setStatusClass("datapro-status");
    setIndexText("");
    setIndexClass("datapro-index-status hidden");
    setResult(t("cust.datapro.noResult"));
    setArtifact(null);
    try {
      const body: Record<string, unknown> = { query: text };
      if (currentId.value) body.frame_id = currentId.value;
      const response = await api("/datapro/search", {
        method: "POST",
        body: JSON.stringify(body),
      });
      const code = dataproResponseCode(response);
      const indexed = code === 0 && dataproIndexComplete(response);
      setStatus(
        indexed
          ? t("cust.datapro.available")
          : code === 0
            ? t("cust.datapro.indexFailed")
            : code === 4011
              ? t("cust.datapro.auth4011")
              : asString(response.message) ||
                t("cust.datapro.unavailable", code == null ? "?" : code),
      );
      setStatusClass("datapro-status " + (indexed ? "ok" : "bad"));
      if (indexed) {
        const index = response.index as Record<string, unknown>;
        setIndexText(
          t("cust.datapro.indexed", index.entry_count, index.source_leaf_count),
        );
        setIndexClass("datapro-index-status ok");
      }
      setResult(dataproResultText(response) || t("cust.datapro.noResult"));
      const saved = response.artifact as Record<string, unknown> | undefined;
      setArtifact(saved && typeof saved === "object" ? saved : null);
    } catch (error) {
      setStatus(t("cust.datapro.requestFailed", apiErrorText(error)));
      setStatusClass("datapro-status bad");
      setResult(t("cust.datapro.noResult"));
      setArtifact(null);
    } finally {
      setSearching(false);
    }
  };

  return (
    <section class="datapro-card">
      <div class="datapro-head">
        <div>
          <div class="datapro-title">{t("cust.datapro.title")}</div>
          <div class="datapro-desc">{t("cust.datapro.desc")}</div>
        </div>
        <button
          type="button"
          class="outline-btn small"
          data-action="datapro-enable-skill"
          disabled={skillBusy || (skillEnabled && connectorEnabled)}
          onClick={async () => {
            setSkillBusy(true);
            try {
              if (!connectorEnabled) {
                await api(`/connectors/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`, {
                  method: "PUT",
                  body: JSON.stringify({ enabled: true }),
                });
              }
              await api(
                `/skills/catalog/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`,
                { method: "PUT", body: JSON.stringify({ enabled: true }) },
              );
              setConnectorEnabled(true);
              setSkillEnabled(true);
              dropSkillsCatalog();
              hint(t("cust.datapro.skillEnabledToast"));
            } catch (error) {
              hint(t("toast.failed", apiErrorText(error)), true);
              setSkillBusy(false);
              return;
            }
            setSkillBusy(false);
          }}
        >
          {skillBusy
            ? t("cust.datapro.enablingSkill")
            : skillEnabled
              ? t("cust.datapro.skillEnabled")
              : t("cust.datapro.enableSkill")}
        </button>
        <button
          type="button"
          class={"toggle" + (connectorEnabled ? " on" : "")}
          data-action="datapro-toggle-connector"
          title={t("cust.datapro.connectorToggle")}
          onClick={async () => {
            const on = !connectorEnabled;
            setConnectorEnabled(on);
            try {
              await api(`/connectors/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`, {
                method: "PUT",
                body: JSON.stringify({ enabled: on }),
              });
              hint(on ? t("cust.datapro.connectorOn") : t("cust.datapro.connectorOff"));
            } catch (error) {
              setConnectorEnabled(!on);
              hint(t("toast.failed", apiErrorText(error)), true);
            }
          }}
        />
      </div>
      <div class="datapro-field">
        <label class="skill-lbl">{t("cust.datapro.keyLabel")}</label>
        <div class="datapro-input-row">
          <input
            id="datapro-plan-key"
            class="cust-input"
            type="password"
            autocomplete="off"
            autocapitalize="off"
            spellcheck={false}
            placeholder={placeholder}
            value={key}
            onInput={(e) => setKey((e.target as HTMLInputElement).value)}
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
            data-action="datapro-save-key"
            disabled={savingKey}
            onClick={() => void saveKey()}
          >
            {t("cust.datapro.saveKey")}
          </button>
        </div>
        <div class={"datapro-credential-state" + (keyBad ? " bad" : "")}>{keyState}</div>
      </div>
      <div class="datapro-field">
        <label class="skill-lbl">{t("cust.datapro.queryLabel")}</label>
        <textarea
          id="datapro-query"
          class="datapro-query"
          rows={3}
          maxLength={10000}
          placeholder={t("cust.datapro.queryPlaceholder")}
          value={query}
          onInput={(e) => setQuery((e.target as HTMLTextAreaElement).value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
              e.preventDefault();
              void runSearch();
            }
          }}
        />
        <div class="datapro-query-actions">
          <div class={statusClass} data-datapro-status="" aria-live="polite">
            {status}
          </div>
          <button
            type="button"
            class="solid-btn small"
            data-action="datapro-search"
            disabled={searching}
            onClick={() => void runSearch()}
          >
            {searching ? t("cust.datapro.searching") : t("cust.datapro.search")}
          </button>
        </div>
      </div>
      <div class="datapro-output">
        <div class="skill-lbl">{t("cust.datapro.result")}</div>
        <div class={indexClass} data-datapro-index-status="" aria-live="polite">
          {indexText}
        </div>
        <pre class="datapro-result" data-datapro-result="">
          {result}
        </pre>
        {artifact ? (
          <button
            type="button"
            class="outline-btn small datapro-artifact"
            data-datapro-artifact=""
            disabled={!artifact.id}
            onClick={() => {
              if (artifact.id) openViewer(artifact);
            }}
          >
            {t(
              "cust.datapro.artifact",
              asString(artifact.filename || artifact.id || "artifact"),
            )}
          </button>
        ) : null}
      </div>
    </section>
  );
}
