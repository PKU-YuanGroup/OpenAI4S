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
  openViewer,
} from "../../../features/customize/host";
import { useOptimisticToggle } from "../hooks";
import { ActionStatus, type ActionNote } from "../ui";
import { useVendorKey, vendorKeyT } from "./use-vendor-key";
import { overrideLookup } from "../../../i18n/copy";

/**
 * M-6: the description promised "an existing Ark API Key is reused
 * automatically" right above "Agent Plan Key is not configured" when the model
 * ran on a key from the environment, which is not shared on its own.
 */
const dataproT = overrideLookup({
  zh: {
    "cust.datapro.desc":
      "保存 Agent Plan Key 后直接调用 dataPro_search。「模型」页里配置的方舟 Agent Plan 模型会自动共用它的 Key；只通过环境变量提供给模型的 Key 不会自动共用。",
  },
  en: {
    "cust.datapro.desc":
      "Save one Agent Plan Key and call dataPro_search directly. An Ark Agent Plan model set up under Models shares its key automatically; a model key given only through environment variables is not shared on its own.",
  },
});

const connectorPath = `/connectors/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`;

export function DataProCard({
  config,
  configError,
}: {
  /** `null` until `GET /datapro/config` has answered. */
  config: Record<string, unknown> | null;
  configError: unknown;
}) {
  const vendorKey = useVendorKey("cust.datapro", "/datapro/config", config, configError);
  // SCI-04: the connector switch and the Skill button answer in the card.
  const [cardNote, setCardNote] = useState<ActionNote>(null);
  const connector = useOptimisticToggle(
    config ? !!config.connector_enabled : null,
    (on) => api(connectorPath, { method: "PUT", body: JSON.stringify({ enabled: on }) }),
    {
      done: (on) =>
        setCardNote({ text: on ? t("cust.datapro.connectorOn") : t("cust.datapro.connectorOff") }),
      failed: (error) => setCardNote({ text: t("toast.failed", apiErrorText(error)), err: true }),
    },
  );
  // The config read the skill was enabled for; a newer read speaks for itself.
  const [skillEnabledFor, setSkillEnabledFor] = useState<Record<string, unknown> | null>(null);
  const skillEnabled = (config !== null && skillEnabledFor === config) || !!config?.skill_enabled;
  const [skillBusy, setSkillBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [statusClass, setStatusClass] = useState("datapro-status");
  const [indexText, setIndexText] = useState("");
  const [indexClass, setIndexClass] = useState("datapro-index-status hidden");
  const [result, setResult] = useState(t("cust.datapro.noResult"));
  const [artifact, setArtifact] = useState<Record<string, unknown> | null>(null);
  const [searching, setSearching] = useState(false);

  const runSearch = async () => {
    const text = query.trim();
    if (!text) {
      setStatus(t("cust.datapro.queryRequired"));
      setStatusClass("datapro-status bad");
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
          <div class="datapro-desc">{dataproT("cust.datapro.desc")}</div>
        </div>
        <button
          type="button"
          class="outline-btn small"
          data-action="datapro-enable-skill"
          disabled={skillBusy || !config || (skillEnabled && connector.on)}
          onClick={async () => {
            if (!config || skillBusy) return;
            setSkillBusy(true);
            try {
              if (!connector.on) {
                await api(connectorPath, {
                  method: "PUT",
                  body: JSON.stringify({ enabled: true }),
                });
              }
              await api(
                `/skills/catalog/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`,
                { method: "PUT", body: JSON.stringify({ enabled: true }) },
              );
              connector.confirm(true);
              setSkillEnabledFor(config);
              dropSkillsCatalog();
              setCardNote({ text: t("cust.datapro.skillEnabledToast") });
            } catch (error) {
              setCardNote({ text: t("toast.failed", apiErrorText(error)), err: true });
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
          class={"toggle" + (connector.on ? " on" : "")}
          data-action="datapro-toggle-connector"
          title={t("cust.datapro.connectorToggle")}
          disabled={!connector.ready}
          onClick={connector.toggle}
        />
      </div>
      <ActionStatus note={cardNote} />
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
            placeholder={vendorKey.placeholder}
            value={vendorKey.key}
            onInput={(e) => vendorKey.setKey((e.target as HTMLInputElement).value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void vendorKey.saveKey();
              }
            }}
          />
          <button
            type="button"
            class="solid-btn small"
            data-action="datapro-save-key"
            disabled={vendorKey.savingKey || !vendorKey.loaded}
            onClick={() => void vendorKey.saveKey()}
          >
            {t("cust.datapro.saveKey")}
          </button>
        </div>
        <div
          class={"datapro-credential-state" + (vendorKey.keyBad ? " bad" : "")}
          role="status"
          aria-live="polite"
        >
          {vendorKey.keyState}
        </div>
      </div>
      {vendorKey.missing && vendorKey.modelKeyAvailable ? (
        <div class="datapro-field" data-vendor-model-key="">
          <div class="ds">{vendorKeyT("vendor.modelKeyHint")}</div>
          <button
            type="button"
            class="outline-btn small"
            disabled={vendorKey.savingKey}
            onClick={() => vendorKey.useModelKey()}
          >
            {vendorKeyT("vendor.useModelKey")}
          </button>
        </div>
      ) : null}
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
