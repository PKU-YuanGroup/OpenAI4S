import { useEffect, useState } from "preact/hooks";
import { t, LANG, langPreference, setLang, type LangPreference } from "../../i18n";
import { getTheme, setTheme, type ThemeMode } from "../../features/theme/theme";
import { getLayout, setLayout, type LayoutName } from "../../features/customize/layout";
import { markCustomizeLoaded } from "../../features/customize/load";
import { NotebookReplSetting } from "./NotebookReplSetting";
import { CustRow, Hdr, Seg, Subhead, Toggle } from "./ui";
import { labCopy, labTabHidden, setLabTabHidden } from "../../features/lab";

/**
 * NAV-6: the Lab tab can be hidden from its own panel; this is where it comes
 * back. A per-browser choice, like the theme.
 */
export function LabTabSetting() {
  const [hidden, setHidden] = useState(labTabHidden);
  return (
    <CustRow name={labCopy("tabSetting")} desc={hidden ? labCopy("tabHidden") : labCopy("tabShown")}>
      <Toggle
        on={!hidden}
        onClick={() => {
          setLabTabHidden(!hidden);
          setHidden(!hidden);
        }}
      />
    </CustRow>
  );
}

/**
 * Feature-local on purpose: `i18n/en.ts` / `zh.ts` are generated extracts of
 * the legacy dictionary and are byte-checked.
 */
const COPY = {
  en: {
    langSystem: "System",
    serverHead: "This server",
    serverDesc: "Applies to every window and every user of this server, not only this browser.",
  },
  zh: {
    langSystem: "跟随系统",
    serverHead: "这台服务器",
    serverDesc: "对这台服务器上的所有窗口和所有用户生效，不只是这个浏览器。",
  },
} as const;

/**
 * Appearance and language (M-22). The model-key line, the update check, the
 * experimental judgment layer and the diagnostics used to follow here; the
 * model is on Models, the rest on Experimental and Diagnostics.
 */
export function GeneralTab() {
  // Shown from local state: a pick used to remount the whole tab (custTab),
  // refetching the key line, the judgment settings and diagnostics, just to
  // move the highlighted segment.
  const [theme, setThemeChoice] = useState<ThemeMode>(getTheme);
  const [layout, setLayoutChoice] = useState<LayoutName>(getLayout);
  // Read on every render, not cached at mount: the Shell repaints on each
  // language change, including one made in another tab. The tick only
  // repaints this tab at once after a pick here.
  const [, setLangTick] = useState(0);

  // Nothing to read: every row here is this browser's own setting.
  useEffect(() => {
    markCustomizeLoaded();
  }, []);

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
      <LabTabSetting />
      {/* SYNC-27: the switch below is the daemon's, under a page that says
          "saved in this browser". */}
      <div data-server-settings="">
        <Subhead>{(LANG === "zh" ? COPY.zh : COPY.en).serverHead}</Subhead>
        <div class="cust-sub">{(LANG === "zh" ? COPY.zh : COPY.en).serverDesc}</div>
        <NotebookReplSetting />
      </div>
    </div>
  );
}
