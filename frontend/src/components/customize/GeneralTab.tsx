import { useEffect, useState } from "preact/hooks";
import { t, LANG, langPreference, setLang, type LangPreference } from "../../i18n";
import { getTheme, setTheme, type ThemeMode } from "../../features/theme/theme";
import { api } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { getLayout, setLayout, type LayoutName } from "../../features/customize/layout";
import { useAlive } from "./use-timer-lease";
import { markCustomizeLoaded } from "../../features/customize/load";
import { DiagnosticsTab } from "./DiagnosticsTab";
import { ExperimentsTab } from "./ExperimentsTab";
import { UpdateCheck } from "./UpdateCheck";
import { CustRow, Hdr, Seg } from "./ui";

/**
 * Feature-local on purpose: `i18n/en.ts` / `zh.ts` are generated extracts of
 * the legacy dictionary and are byte-checked.
 */
const COPY = {
  en: { langSystem: "System" },
  zh: { langSystem: "跟随系统" },
} as const;

export function GeneralTab() {
  const alive = useAlive();
  const [keyLine, setKeyLine] = useState(t("cust.models.key.missing"));
  // Shown from local state: a pick used to remount the whole tab (custTab),
  // refetching the key line, the judgment settings and diagnostics, just to
  // move the highlighted segment.
  const [theme, setThemeChoice] = useState<ThemeMode>(getTheme);
  const [layout, setLayoutChoice] = useState<LayoutName>(getLayout);
  // Read on every render, not cached at mount: the Shell repaints on each
  // language change, including one made in another tab. The tick only
  // repaints this tab at once after a pick here.
  const [, setLangTick] = useState(0);

  useEffect(() => {
    void (async () => {
      let conf: Record<string, unknown> = {};
      try {
        conf = await api("/config/llm");
      } catch {
        conf = {};
      }
      if (!alive()) return;
      markCustomizeLoaded();
      setKeyLine(
        conf.has_api_key
          ? t("cust.general.apiKeyConfigured") +
              (conf.model ? "（" + String(conf.model) + "）" : "")
          : t("cust.models.key.missing"),
      );
    })();
  }, [alive]);

  return (
    <div>
      <Hdr title={t("cust.general.title")} sub={t("cust.general.desc")} />
      <CustRow name={t("cust.general.themeName")} desc={t("cust.general.themeDesc")}>
        <Seg
          value={theme}
          options={[
            ["light", t("theme.light")],
            ["dark", t("theme.dark")],
            ["system", t("theme.system")],
          ]}
          onPick={(val) => {
            setTheme(val as ThemeMode);
            setThemeChoice(getTheme());
          }}
        />
      </CustRow>
      <CustRow name={t("cust.general.layoutName")} desc={t("cust.general.layoutDesc")}>
        <Seg
          value={layout}
          options={[
            ["comfortable", t("cust.general.layout.comfortable")],
            ["compact", t("cust.general.layout.compact")],
            ["wide", t("cust.general.layout.wide")],
          ]}
          onPick={(val) => {
            setLayout(val as LayoutName);
            setLayoutChoice(val as LayoutName);
          }}
        />
      </CustRow>
      <CustRow name={t("cust.general.language")} desc={t("cust.general.languageDesc")}>
        <Seg
          value={langPreference()}
          options={[
            ["zh", "中文"],
            ["en", "English"],
            ["system", (LANG === "zh" ? COPY.zh : COPY.en).langSystem],
          ]}
          onPick={(val) => {
            void setLang(val as LangPreference);
            setLangTick((tick) => tick + 1);
          }}
        />
      </CustRow>
      <CustRow name={t("cust.general.modelKeyName")} desc={keyLine}>
        <button
          type="button"
          class="outline-btn small"
          onClick={() => custTab("models")}
        >
          {t("cust.general.configureBtn")}
        </button>
      </CustRow>
      <UpdateCheck />
      <ExperimentsTab />
      <DiagnosticsTab />
    </div>
  );
}
