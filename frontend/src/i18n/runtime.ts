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
  // Do not keep a rejected load: a chunk request cancelled by navigation
  // would otherwise poison every later attempt with the same rejection,
  // and switching language would fail forever after one bad moment.
  localeLoads[lang] = assigned;
  void assigned.catch(() => {
    if (localeLoads[lang] === assigned) delete localeLoads[lang];
  });
  return assigned;
}

let boot: Promise<void> | undefined;
// Declared before the import-time i18nReady() below, whose repaint reads it.
const languageHooks: Array<() => void> = [];

function applyDocumentLang(lang: Lang): void {
  if (typeof document === "undefined" || !document.documentElement) return;
  document.documentElement.lang = lang === "en" ? "en" : "zh";
}

/**
 * Load what `t()` reads for `lang`: that dictionary and, for en, the zh one
 * it falls back to.
 *
 * Both requests start together. Awaiting them one after the other made an
 * English reader wait for two chunk round trips, and the first view waits for
 * this -- so a deep link showed the wrong screen for twice the chunk latency.
 *
 * Only the active dictionary decides the outcome. A zh fallback that fails to
 * load leaves `t()` answering from the active language (a missing entry shows
 * its key, as before), and is retried by the next load; letting it reject
 * skipped the repaint, so an English reader kept the markup's Chinese
 * tooltips even though the English dictionary had arrived.
 */
async function loadDictionaries(lang: Lang): Promise<void> {
  const active = loadLocale(lang);
  if (lang !== "zh") {
    const fallback = loadLocale("zh").then(
      () => undefined,
      () => undefined,
    );
    await Promise.all([active, fallback]);
    return;
  }
  await active;
}

export function i18nReady(): Promise<void> {
  if (!boot) {
    boot = (async () => {
      await loadDictionaries(LANG);
      const inactive: Lang = LANG === "zh" ? "en" : "zh";
      // Prefetch only, and deliberately not awaited: the page is already
      // usable in the active language. Left unhandled, a chunk request the
      // browser cancels on navigation surfaces as an uncaught page error --
      // which is what firefox and webkit reported while chromium finished
      // the fetch in time and said nothing. The cost of losing it is one
      // fetch at the next language switch.
      void loadLocale(inactive).catch(() => undefined);
      // The Shell renders synchronously and bindWorkbench() applies the
      // static labels on its first effect -- usually before these chunks
      // arrive, when there is nothing to translate with. Repaint once they
      // land, the same way a language switch does; otherwise every static
      // label keeps its pre-load text until the user changes language.
      // The dictionaries are loaded either way: a repaint that throws must
      // not turn i18nReady() into a rejection for the life of the page.
      try {
        repaintLanguage();
      } catch {
        /* partial or torn-down document */
      }
    })();
  }
  return boot.then(() => {
    applyDocumentLang(LANG);
  });
}

applyDocumentLang(LANG);
// The callers that depend on the outcome ask for it themselves; this one only
// starts the load, and must not report a failed chunk as an uncaught rejection.
void i18nReady().catch(() => undefined);

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
// A node with no translation (yet) keeps what it has: the markup carries a
// readable fallback, and t()'s key fallback would replace "Projects" with
// "dash.col.projects" whenever this runs before the dictionaries load.
export function applyStaticI18n(root?: QueryRoot): void {
  const r: QueryRoot | undefined =
    root ?? (typeof document !== "undefined" ? document : undefined);
  if (r === undefined) return;
  const each = (attr: string, write: (e: Element, value: string) => void) => {
    Array.from(r.querySelectorAll(`[${attr}]`)).forEach((e) => {
      const value = tOptional(String(e.getAttribute(attr)));
      if (value != null) write(e, value);
    });
  };
  each("data-i18n", (e, value) => {
    e.textContent = value;
  });
  each("data-i18n-title", (e, value) => {
    (e as HTMLElement).title = value;
  });
  each("data-i18n-ph", (e, value) => {
    (e as HTMLInputElement).placeholder = value;
  });
  each("data-i18n-val", (e, value) => {
    (e as HTMLInputElement).value = value;
  });
}

export function refreshLangToggle(): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".lang-btn").forEach((b) => {
    b.classList.toggle("active", (b as HTMLElement).dataset.lang === LANG);
  });
}

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
  await loadDictionaries(LANG);
  repaintLanguage();
}

/** Everything that shows the active language: static labels, toggle, hooks. */
function repaintLanguage(): void {
  if (typeof document !== "undefined") {
    applyDocumentLang(LANG);
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
