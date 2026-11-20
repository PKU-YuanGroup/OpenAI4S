import { useState } from "preact/hooks";
import { t } from "../../../i18n";
import { copyLookup } from "../../../i18n/copy";
import { api, apiErrorText } from "../../../features/customize/api";
import { confirmAction, hint } from "../../../features/customize/host";

/** Feature-local: the generated `i18n/en.ts` / `zh.ts` are byte-checked extracts. */
export const vendorKeyT = copyLookup({
  zh: {
    "vendor.useModelKey": "使用当前模型的 Key",
    "vendor.useModelKeyConfirm":
      "把当前在用模型（火山方舟）的 API Key 保存为豆包搜索和 DataPro 共用的 Agent Plan Key？Key 本身不会显示出来。",
    "vendor.modelKeyHint": "当前在用的是火山方舟模型，可以直接共用它的 Key。",
  },
  en: {
    "vendor.useModelKey": "Use the current model's key",
    "vendor.useModelKeyConfirm":
      "Save the API key of the model in use (Volcengine Ark) as the Agent Plan Key Doubao Search and DataPro share? The key itself is not shown.",
    "vendor.modelKeyHint": "The model in use is a Volcengine Ark model; its key can be shared here.",
  },
});

type KeyResult = {
  /** The config read this result answered; a newer read replaces it. */
  source: Record<string, unknown>;
  configured: boolean;
  arkReused: boolean;
  error: string | null;
};

/**
 * The Agent Plan key half of a vendor card (DataPro on Connectors, Doubao on
 * Network).
 *
 * The cards used to copy their `config` prop into state on first render. The
 * tabs render them before the config request answers, with an empty config
 * and no key, so a card said "key missing" forever and its first action was
 * based on that. The key state is now derived from the `config` prop on every
 * render (`null` until it has been read, and nothing can be saved until then);
 * a save's answer is kept only for the config it answered.
 */
export function useVendorKey(
  prefix: "cust.datapro" | "cust.doubao",
  path: "/datapro/config" | "/doubao-search/config",
  config: Record<string, unknown> | null,
  configError: unknown,
) {
  const [key, setKey] = useState("");
  const [savingKey, setSavingKey] = useState(false);
  const [result, setResult] = useState<KeyResult | null>(null);
  const loaded = config !== null;
  const own = result && result.source === config ? result : null;
  const keyConfigured = own ? own.configured : !!config?.key_configured;
  const arkKeyReused = own ? own.arkReused : !!config?.ark_key_reused;
  const failure = own
    ? own.error
    : configError
      ? t(`${prefix}.requestFailed`, apiErrorText(configError))
      : null;

  const keyState = !loaded
    ? t("common.loading")
    : failure
      ? failure
      : arkKeyReused
        ? t(`${prefix}.keyArkReused`)
        : keyConfigured
          ? t(`${prefix}.keyConfigured`)
          : t(`${prefix}.keyMissing`);
  const keyBad = loaded && (!!failure || (!keyConfigured && !arkKeyReused));
  /** Read, no failure, and no key the outbound resolver would send. */
  const missing = loaded && !failure && !keyConfigured && !arkKeyReused;
  const placeholder = arkKeyReused
    ? t(`${prefix}.keyPlaceholderArk`)
    : keyConfigured
      ? t(`${prefix}.keyPlaceholderSet`)
      : t(`${prefix}.keyPlaceholder`);

  /**
   * M-6: the model in use is an Ark model on Volcengine with a key (from the
   * environment, say) -- share it after one confirmation instead of asking for
   * it to be typed again. The server reads and saves the key; it never comes
   * to the browser.
   */
  const modelKeyAvailable = loaded && !failure && !!config?.model_key_available;
  const useModelKey = () => {
    if (!config || savingKey || !modelKeyAvailable) return;
    if (!confirmAction(vendorKeyT("vendor.useModelKeyConfirm"))) return;
    void submit({ use_model_key: true });
  };

  const saveKey = async () => {
    if (!config || savingKey) return;
    const secret = key.trim();
    setKey("");
    if (!secret) {
      hint(t(`${prefix}.keyRequired`), true);
      return;
    }
    await submit({ agent_plan_key: secret });
  };

  const submit = async (body: Record<string, unknown>) => {
    if (!config) return;
    setSavingKey(true);
    const request = api(path, {
      method: "POST",
      body: JSON.stringify(body),
    });
    try {
      const saved = await request;
      setResult({
        source: config,
        configured: !!saved.key_configured,
        arkReused: !!saved.ark_key_reused,
        error: null,
      });
      hint(t(`${prefix}.keySaved`));
    } catch (error) {
      setResult({
        source: config,
        configured: keyConfigured,
        arkReused: arkKeyReused,
        error: t(`${prefix}.requestFailed`, apiErrorText(error)),
      });
    } finally {
      setKey("");
      setSavingKey(false);
    }
  };

  return {
    loaded,
    key,
    setKey,
    savingKey,
    saveKey,
    keyState,
    keyBad,
    missing,
    placeholder,
    modelKeyAvailable,
    useModelKey,
  };
}
