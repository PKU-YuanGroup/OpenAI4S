import { useState } from "preact/hooks";
import { t } from "../../../i18n";
import { api, apiErrorText } from "../../../features/customize/api";
import {
  doubaoSearchAvailable,
  doubaoSearchResultText,
} from "../../../features/customize/vendors";
import { hint } from "../../../features/customize/host";
import { useVendorKey } from "./use-vendor-key";

export function DoubaoSearchCard({
  config,
  configError,
}: {
  /** `null` until `GET /doubao-search/config` has answered. */
  config: Record<string, unknown> | null;
  configError: unknown;
}) {
  const vendorKey = useVendorKey("cust.doubao", "/doubao-search/config", config, configError);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [statusClass, setStatusClass] = useState("datapro-status");
  const [result, setResult] = useState(t("cust.doubao.noResult"));
  const [searching, setSearching] = useState(false);

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
            data-action="doubao-search-save-key"
            disabled={vendorKey.savingKey || !vendorKey.loaded}
            onClick={() => void vendorKey.saveKey()}
          >
            {t("cust.doubao.saveKey")}
          </button>
        </div>
        <div class={"datapro-credential-state" + (vendorKey.keyBad ? " bad" : "")}>
          {vendorKey.keyState}
        </div>
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
