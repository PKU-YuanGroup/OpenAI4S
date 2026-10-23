import { useRef, useState } from "preact/hooks";
import { copyLookup, type CopyTable } from "../../../i18n/copy";
import { api, apiErrorText } from "../../../features/customize/api";
import { useAlive } from "../use-timer-lease";

const COPY: CopyTable = {
  en: {
    title: "Ollama web tools",
    description: "Save one API key to use Ollama web search and page reading with any model. Your main model stays as configured.",
    key: "Ollama API key",
    placeholder: "Enter an Ollama API key",
    replace: "Enter a new key to replace the saved key",
    loading: "Loading…",
    configured: "Independent key saved · available to all models",
    missing: "No independent key saved",
    model: "Using the current Ollama cloud model’s key. Save an independent key to keep web tools available when switching models.",
    environment: "Using the Ollama key from the environment",
    save: "Save key",
    clear: "Remove saved key",
    saving: "Saving…",
    required: "Enter an API key first",
    failed: "Could not update Ollama web tools: {0}",
    readFailed: "Could not read Ollama web settings: {0}",
    keys: "Manage Ollama API keys",
  },
  zh: {
    title: "Ollama 网页工具",
    description: "填一次 API key，即可让任意模型使用 Ollama 网页搜索和网页读取，主模型保持原配置。",
    key: "Ollama API key",
    placeholder: "输入 Ollama API key",
    replace: "输入新密钥以替换已保存的密钥",
    loading: "正在加载…",
    configured: "独立密钥已保存 · 所有模型均可使用",
    missing: "尚未保存独立密钥",
    model: "正在使用当前 Ollama 云端模型的密钥。保存独立密钥后，切换模型仍可使用网页工具。",
    environment: "正在使用环境变量中的 Ollama 密钥",
    save: "保存密钥",
    clear: "移除已保存密钥",
    saving: "正在保存…",
    required: "请先输入 API key",
    failed: "未能更新 Ollama 网页工具：{0}",
    readFailed: "未能读取 Ollama 网页设置：{0}",
    keys: "管理 Ollama API 密钥",
  },
};
const ot = copyLookup(COPY);

export function OllamaWebCard({
  config,
  configError,
}: {
  config: Record<string, unknown> | null;
  configError: unknown;
}) {
  const alive = useAlive();
  const inFlight = useRef(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{
    source: Record<string, unknown>;
    config: Record<string, unknown>;
    error: string | null;
  } | null>(null);
  const own = result?.source === config ? result : null;
  const current = own?.config || config;
  const configured = !!current?.key_configured;
  const ready = config !== null && !configError;
  const failure = configError
    ? ot("readFailed", apiErrorText(configError))
    : own?.error;
  const status = !config
    ? ot("loading")
    : failure || (
      configured ? ot("configured")
        : current?.source === "model" ? ot("model")
          : current?.source === "environment" ? ot("environment")
            : ot("missing")
    );

  const save = async (clear = false) => {
    if (!ready || !config || inFlight.current) return;
    const secret = key.trim();
    setKey("");
    if (!clear && !secret) {
      setResult({ source: config, config: current || config, error: ot("required") });
      return;
    }
    inFlight.current = true;
    setBusy(true);
    try {
      const saved = await api("/ollama-web/config", {
        method: "PUT",
        body: JSON.stringify(clear ? { clear_api_key: true } : { api_key: secret }),
      });
      if (alive()) setResult({ source: config, config: saved, error: null });
    } catch (error) {
      if (alive()) setResult({
        source: config,
        config: current || config,
        error: ot("failed", apiErrorText(error)),
      });
    } finally {
      inFlight.current = false;
      if (alive()) setBusy(false);
    }
  };

  return (
    <section class="datapro-card ollama-web-card" aria-label={ot("title")}>
      <div class="datapro-title">{ot("title")}</div>
      <div class="datapro-desc">{ot("description")}</div>
      <div class="datapro-field">
        <label class="skill-lbl" for="ollama-web-api-key">{ot("key")}</label>
        <div class="datapro-input-row">
          <input
            id="ollama-web-api-key"
            class="cust-input"
            type="password"
            autocomplete="off"
            autocapitalize="off"
            spellcheck={false}
            disabled={!ready || busy}
            value={key}
            placeholder={ot(configured ? "replace" : "placeholder")}
            onInput={(event) => setKey((event.target as HTMLInputElement).value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void save();
              }
            }}
          />
          <button
            type="button"
            class="solid-btn small"
            data-action="ollama-web-save-key"
            disabled={!ready || busy}
            onClick={() => void save()}
          >{ot(busy ? "saving" : "save")}</button>
          <button
            type="button"
            class="outline-btn small"
            data-action="ollama-web-clear-key"
            disabled={!ready || busy || !configured}
            onClick={() => void save(true)}
          >{ot("clear")}</button>
        </div>
        <div class={"datapro-credential-state" + (failure ? " bad" : "")} aria-live="polite">
          {status}
        </div>
      </div>
      <a href="https://ollama.com/settings/keys" target="_blank" rel="noopener noreferrer">
        {ot("keys")}
      </a>
    </section>
  );
}
