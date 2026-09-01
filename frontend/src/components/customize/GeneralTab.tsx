import { useEffect, useState } from "preact/hooks";
import { t, LANG, setLang } from "../../i18n";
import { getTheme, setTheme, type ThemeMode } from "../../features/theme/theme";
import { api } from "../../features/customize/api";
import { custTab } from "../../features/customize/actions";
import { getLayout, setLayout, type LayoutName } from "../../features/customize/layout";
import { useAlive } from "./use-timer-lease";
import { CustRow, Hdr, Seg } from "./ui";

export function GeneralTab() {
  const alive = useAlive();
  const [keyLine, setKeyLine] = useState(t("cust.models.key.missing"));
  const theme = getTheme();
  const layout = getLayout();

  useEffect(() => {
    void (async () => {
      let conf: Record<string, unknown> = {};
      try {
        conf = await api("/config/llm");
      } catch {
        conf = {};
      }
      if (!alive()) return;
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
            custTab("general");
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
            custTab("general");
          }}
        />
      </CustRow>
      <CustRow name={t("cust.general.language")} desc={t("cust.general.languageDesc")}>
        <Seg
          value={LANG}
          options={[
            ["zh", "中文"],
            ["en", "English"],
          ]}
          onPick={(val) => {
            void setLang(val);
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
    </div>
  );
}
