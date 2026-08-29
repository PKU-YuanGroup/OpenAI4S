/**
 * i18n runtime ported from openai4s/server/webui/app.js:135-249.
 *
 * t / tOptional / LANG / setLang / applyStaticI18n / refreshLangToggle keep
 * the original semantics. Locale dictionaries are separate async chunks:
 * the active language (and zh, when it is the fallback) load via import(),
 * and the inactive language is prefetched after first paint.
 */

export type Lang = "zh" | "en";
export type I18nDict = Record<string, string>;

// Single dictionary keyed by stable dot-keys; every UI string reads through t().
// I18N.zh / I18N.en are populated by loadLocale (dynamic import of zh.ts / en.ts).
export const I18N: { zh: I18nDict; en: I18nDict } = { zh: {}, en: {} };

export function detectLang(): Lang {
  try {
    const s = localStorage.getItem("os-lang");
    if (s === "zh" || s === "en") return s;
  } catch {
    /* localStorage missing or blocked */
  }
  try {
    return (navigator.languages || [navigator.language || ""]).some((l) =>
      /^zh/i.test(l),
    )
      ? "zh"
      : "en";
  } catch {
    /* navigator missing */
  }
  return "zh";
}

export let LANG: Lang = detectLang();

const localeLoads: { zh?: Promise<I18nDict>; en?: Promise<I18nDict> } = {};

/**
 * Load one locale module. The two import() specifiers are statically
 * visible so Vite/Rollup emit separate async chunks (inactive language
 * is not in the main bundle).
 */
export function loadLocale(lang: Lang): Promise<I18nDict> {
  const cached = localeLoads[lang];
  if (cached) return cached;
  const pending =
    lang === "en"
      ? import("./en").then((m) => m.default)
      : import("./zh").then((m) => m.default);
  const assigned = pending.then((dict) => {
    const copy: I18nDict = { ...dict };
    I18N[lang] = copy;
    return copy;
  });
  localeLoads[lang] = assigned;
  return assigned;
}

let boot: Promise<void> | undefined;

export function i18nReady(): Promise<void> {
  if (!boot) {
    boot = (async () => {
      await loadLocale(LANG);
      // t() falls back to zh when the active (en) entry is missing.
      if (LANG !== "zh") await loadLocale("zh");
      const inactive: Lang = LANG === "zh" ? "en" : "zh";
      void loadLocale(inactive);
    })();
  }
  return boot;
}

void i18nReady();

// t("key", ...args) — current-language string with {0},{1}… positional interpolation; falls back to zh, then the key.
// `t` falls back to the key itself, which is right for a missing translation
// (a developer sees the key) and wrong for an optional label (a user would see
// "context.omitted.images" rendered as text). This says "translate if you know
// it" and lets the caller supply something a person can read otherwise.
export function tOptional(key: string): string | null {
  const d = I18N[LANG] || {},
    z = I18N.zh || {};
  const value = d[key] != null ? d[key] : z[key];
  return value != null ? String(value) : null;
}

export function t(key: string, ...args: readonly unknown[]): string {
  const d = I18N[LANG] || I18N.zh || {};
  let s: unknown = d[key];
  if (s == null) {
    const z = (I18N.zh || {})[key];
    s = z != null ? z : key;
  }
  if (args.length)
    s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
      args[+i] != null ? (args[+i] as string) : m,
    );
  return s as string;
}

type QueryRoot = {
  querySelectorAll: (selector: string) => ArrayLike<Element> | NodeListOf<Element>;
};

// Apply translations to static HTML carrying data-i18n / data-i18n-title / data-i18n-ph / data-i18n-val.
export function applyStaticI18n(root?: QueryRoot): void {
  const r: QueryRoot | undefined =
    root ?? (typeof document !== "undefined" ? document : undefined);
  if (r === undefined) return;
  Array.from(r.querySelectorAll("[data-i18n]")).forEach((e) => {
    e.textContent = t(String(e.getAttribute("data-i18n")));
  });
  Array.from(r.querySelectorAll("[data-i18n-title]")).forEach((e) => {
    (e as HTMLElement).title = t(String(e.getAttribute("data-i18n-title")));
  });
  Array.from(r.querySelectorAll("[data-i18n-ph]")).forEach((e) => {
    (e as HTMLInputElement).placeholder = t(String(e.getAttribute("data-i18n-ph")));
  });
  Array.from(r.querySelectorAll("[data-i18n-val]")).forEach((e) => {
    (e as HTMLInputElement).value = t(String(e.getAttribute("data-i18n-val")));
  });
}

export function refreshLangToggle(): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".lang-btn").forEach((b) => {
    b.classList.toggle("active", (b as HTMLElement).dataset.lang === LANG);
  });
}

const languageHooks: Array<() => void> = [];

/**
 * Later lanes (theme toggle titles, dashboard/session rerenders) register
 * here. app.js:172 called refreshThemeToggle() then rerenderI18n(); those
 * views are not in this work item, so the calls are a hook list instead of
 * a hard dependency on unported functions.
 */
export function onLanguageChange(hook: () => void): () => void {
  languageHooks.push(hook);
  return () => {
    const i = languageHooks.indexOf(hook);
    if (i >= 0) languageHooks.splice(i, 1);
  };
}

export async function setLang(lang: string): Promise<void> {
  LANG = lang === "en" ? "en" : "zh";
  try {
    localStorage.setItem("os-lang", LANG);
  } catch {
    /* ignore quota / missing storage */
  }
  await loadLocale(LANG);
  if (LANG !== "zh") await loadLocale("zh");
  if (typeof document !== "undefined") {
    document.documentElement.lang = LANG === "en" ? "en" : "zh";
    applyStaticI18n(document);
    refreshLangToggle();
  }
  for (const hook of languageHooks) {
    try {
      hook();
    } catch {
      /* same isolation as app.js rerenderI18n per-view try/catch */
    }
  }
}

/**
 * app.js:7955-7959 sent a hardcoded Chinese plan-mode payload even though
 * plan.prompt.* already existed in both dictionaries (and had drifted from
 * that literal). Concatenate the dictionary entries through t() so F-11
 * send() can drop the Chinese string.
 *
 * Order matches send(): intro, part1, part2, jsonSchema, part3, task text.
 */
const PLAN_PROMPT_KEYS = [
  "plan.prompt.intro",
  "plan.prompt.part1",
  "plan.prompt.part2",
  "plan.prompt.jsonSchema",
  "plan.prompt.part3",
] as const;

export function planModePayload(taskText: string): string {
  return PLAN_PROMPT_KEYS.map((key) => t(key)).join("") + taskText;
}
