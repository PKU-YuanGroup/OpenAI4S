import { t, tOptional } from "./runtime";

export {
  I18N,
  LANG,
  applyStaticI18n,
  detectLang,
  i18nReady,
  loadLocale,
  onLanguageChange,
  planModePayload,
  refreshLangToggle,
  setLang,
  t,
  tOptional,
} from "./runtime";
export type { I18nDict, Lang } from "./runtime";

// F-07 window export. `t` is in the E2E contract (10 references across three
// browser files), and F-05 reserves it with a stub that throws. The owning
// module assigns the real one, the same way F-06's bootWs() assigns onEvent --
// main.tsx imports this module after compat/window-exports, so this wins.
const hostWindow = (globalThis as unknown as { window?: Record<string, unknown> }).window;
if (hostWindow) {
  hostWindow.t = t;
  hostWindow.tOptional = tOptional;
}
