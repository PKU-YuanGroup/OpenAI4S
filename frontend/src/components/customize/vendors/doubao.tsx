import { useState } from "preact/hooks";
import { t } from "../../../i18n";
import { api, apiErrorText } from "../../../features/customize/api";
import {
  doubaoSearchAvailable,
  doubaoSearchResultText,
} from "../../../features/customize/vendors";
import { hint } from "../../../features/customize/host";

export function DoubaoSearchCard({
  config,
  configError,
}: {
  config: Record<string, unknown>;
  configError: unknown;
}) {
  const [keyConfigured, setKeyConfigured] = useState(!!config.key_configured);
  const [arkKeyReused, setArkKeyReused] = useState(!!config.ark_key_reused);
  const [key, setKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [keyState, setKeyState] = useState(
    configError
      ? t("cust.doubao.requestFailed", apiErrorText(configError))
      : arkKeyReused
        ? t("cust.doubao.keyArkReused")
        : keyConfigured
          ? t("cust.doubao.keyConfigured")
          : t("cust.doubao.keyMissing"),
  );
  const [keyBad, setKeyBad] = useState(!!configError || (!keyConfigured && !arkKeyReused));
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [statusClass, setStatusClass] = useState("datapro-status");
  const [result, setResult] = useState(t("cust.doubao.noResult"));
  const [searching, setSearching] = useState(false);

  const placeholder = arkKeyReused
    ? t("cust.doubao.keyPlaceholderArk")
    : keyConfigured
      ? t("cust.doubao.keyPlaceholderSet")
      : t("cust.doubao.keyPlaceholder");

  const saveKey = async () => {
    let secret = key.trim();
    setKey("");
    if (!secret) {
      hint(t("cust.doubao.keyRequired"), true);
      return;
    }
    setSavingKey(true);
    const request = api("/doubao-search/config", {
      method: "POST",
      body: JSON.stringify({ agent_plan_key: secret }),
    });
    secret = "";
    try {
      const saved = await request;
      setKeyConfigured(!!saved.key_configured);
      setArkKeyReused(!!saved.ark_key_reused);
      setKeyState(
        saved.ark_key_reused ? t("cust.doubao.keyArkReused") : t("cust.doubao.keyConfigured"),
      );
      setKeyBad(false);
      hint(t("cust.doubao.keySaved"));
    } catch (error) {
      setKeyState(t("cust.doubao.requestFailed", apiErrorText(error)));
      setKeyBad(true);
    } finally {
      setKey("");
      setSavingKey(false);
    }
  };

  const runSearch = async () => {
    const text = query.trim();
    if (!text) {
      hint(t("cust.doubao.queryRequired"), true);
      return;
    }
    setSearching(true);
    setStatus(t("cust.doubao.searching"));
    setStatusClass("datapro-status");
    setResult(t("cust.doubao.noResult"));
    try {
      // Dedicated: the backend must not satisfy this with Tavily or a keyless fallback.
      const response = await api("/doubao-search/search", {
        method: "POST",
        body: JSON.stringify({ query: text }),
      });
      const available = doubaoSearchAvailable(response);
      setStatus(
        available
          ? t("cust.doubao.available")
          : String(response.message || "") || t("cust.doubao.empty"),
      );
      setStatusClass("datapro-status " + (available ? "ok" : "bad"));
      setResult(doubaoSearchResultText(response) || t("cust.doubao.noResult"));
    } catch (error) {
      setStatus(t("cust.doubao.requestFailed", apiErrorText(error)));
      setStatusClass("datapro-status bad");
      setResult(t("cust.doubao.noResult"));
    } finally {
      setSearching(false);
    }
  };

  return (
    <section class="datapro-card doubao-search-card">
      <div class="datapro-head">
        <div>
          <div class="datapro-title">
            {t("cust.doubao.title")} <span class="pill">{t("cust.doubao.primary")}</span>
          </div>
          <div class="datapro-desc">{t("cust.doubao.desc")}</div>
        </div>
      </div>
      <div class="datapro-field">
        <label class="skill-lbl">{t("cust.doubao.keyLabel")}</label>
        <div class="datapro-input-row">
          <input
            id="doubao-search-plan-key"
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
            data-action="doubao-search-save-key"
            disabled={savingKey}
            onClick={() => void saveKey()}
          >
            {t("cust.doubao.saveKey")}
          </button>
        </div>
        <div class={"datapro-credential-state" + (keyBad ? " bad" : "")}>{keyState}</div>
      </div>
      <div class="datapro-field">
        <label class="skill-lbl">{t("cust.doubao.queryLabel")}</label>
        <textarea
          id="doubao-search-query"
          class="datapro-query"
          rows={3}
          maxLength={100}
          placeholder={t("cust.doubao.queryPlaceholder")}
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
          <div class={statusClass} data-doubao-search-status="" aria-live="polite">
            {status}
          </div>
          <button
            type="button"
            class="solid-btn small"
            data-action="doubao-search-run"
            disabled={searching}
            onClick={() => void runSearch()}
          >
            {searching ? t("cust.doubao.searching") : t("cust.doubao.search")}
          </button>
        </div>
      </div>
      <div class="datapro-output">
        <div class="skill-lbl">{t("cust.doubao.result")}</div>
        <pre class="datapro-result" data-doubao-search-result="">
          {result}
        </pre>
      </div>
    </section>
  );
}
