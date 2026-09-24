import { useState } from "preact/hooks";
import { t } from "../../../i18n";
import { api, apiErrorText } from "../../../features/customize/api";
import { hint } from "../../../features/customize/host";

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
  const placeholder = arkKeyReused
    ? t(`${prefix}.keyPlaceholderArk`)
    : keyConfigured
      ? t(`${prefix}.keyPlaceholderSet`)
      : t(`${prefix}.keyPlaceholder`);

  const saveKey = async () => {
    if (!config || savingKey) return;
    const secret = key.trim();
    setKey("");
    if (!secret) {
      hint(t(`${prefix}.keyRequired`), true);
      return;
    }
    setSavingKey(true);
    const request = api(path, {
      method: "POST",
      body: JSON.stringify({ agent_plan_key: secret }),
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

  return { loaded, key, setKey, savingKey, saveKey, keyState, keyBad, placeholder };
}
