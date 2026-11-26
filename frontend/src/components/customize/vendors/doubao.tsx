import { useState } from "preact/hooks";
import { t } from "../../../i18n";
import { copyLookup, overrideLookup } from "../../../i18n/copy";
import { api, apiErrorText } from "../../../features/customize/api";
import {
  doubaoSearchAvailable,
  doubaoSearchResultText,
} from "../../../features/customize/vendors";
import { hint } from "../../../features/customize/host";
import { useVendorKey, vendorKeyT } from "./use-vendor-key";

const dt = copyLookup({
  en: {
    "doubao.web.description": "Used for search when Ollama web tools are not configured. It needs an Agent Plan Key, which it can share with an Ark Agent Plan model set up under Models and with DataPro; a model key set only through OPENAI4S_ARK_API_KEY or OPENAI4S_LLM_API_KEY is not shared automatically, but while the model in use is a Volcengine Ark model you can press \"Use the current model's key\" to share it. Availability is confirmed only by a successful search with results.",
    "doubao.web.keyMissing": "No Agent Plan Key saved in this app. A model key set only through OPENAI4S_ARK_API_KEY or OPENAI4S_LLM_API_KEY is not shared automatically; save the key here to use Doubao Search.",
    "doubao.web.keyMissingShare": "No Agent Plan Key saved in this app. A model key set only through OPENAI4S_ARK_API_KEY or OPENAI4S_LLM_API_KEY is not shared automatically; press \"Use the current model's key\" below to share it.",
  },
  zh: {
    "doubao.web.description": "未配置 Ollama 网页工具时使用的搜索服务，需要 Agent Plan Key；可与「模型」页里配置的方舟 Agent Plan 模型以及 DataPro 共用同一个 Key。只通过 OPENAI4S_ARK_API_KEY 或 OPENAI4S_LLM_API_KEY 环境变量提供给模型的 Key 不会自动共用，当前模型是火山方舟模型时，可点「使用当前模型的 Key」共用。只有真实返回非空搜索结果才会标记可用。",
    "doubao.web.keyMissing": "本应用里还没有保存 Agent Plan Key。只通过 OPENAI4S_ARK_API_KEY 或 OPENAI4S_LLM_API_KEY 环境变量提供给模型的 Key 不会自动共用；要用豆包搜索，请在这里保存 Key。",
    "doubao.web.keyMissingShare": "本应用里还没有保存 Agent Plan Key。只通过 OPENAI4S_ARK_API_KEY 或 OPENAI4S_LLM_API_KEY 环境变量提供给模型的 Key 不会自动共用；可点下面的「使用当前模型的 Key」共用。",
  },
});

/** ONB-2: one name for it everywhere (the generated title was "豆包搜索 Custom 版"). */
const titleT = overrideLookup({
  zh: { "cust.doubao.title": "豆包搜索" },
  en: { "cust.doubao.title": "Doubao Search" },
});

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
            {titleT("cust.doubao.title")}
          </div>
          <div class="datapro-desc">{dt("doubao.web.description")}</div>
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
          {/* "Not configured" read as wrong while the model ran on an Ark key
              from the environment: that key is never sent to search. */}
          {vendorKey.missing
            ? dt(vendorKey.modelKeyAvailable ? "doubao.web.keyMissingShare" : "doubao.web.keyMissing")
            : vendorKey.keyState}
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
