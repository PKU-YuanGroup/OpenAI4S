import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  checkDicts,
  emitDictTs,
  extractAppJsDicts,
  keyDiff,
} from "./extract-i18n.mjs";
import en from "./en";
import {
  I18N,
  LANG,
  applyStaticI18n,
  detectLang,
  i18nReady,
  loadLocale,
  onLanguageChange,
  planModePayload,
  setLang,
  t,
  tOptional,
} from "./runtime";
import zh from "./zh";

const HERE = dirname(fileURLToPath(import.meta.url));
const MIN_KEYS = 1207;

function symmetricKeyDiff(
  a: Record<string, string>,
  b: Record<string, string>,
): { onlyA: string[]; onlyB: string[] } {
  const aKeys = new Set(Object.keys(a));
  const bKeys = new Set(Object.keys(b));
  return {
    onlyA: [...aKeys].filter((k) => !bKeys.has(k)).sort(),
    onlyB: [...bKeys].filter((k) => !aKeys.has(k)).sort(),
  };
}

describe("F-07 extract vs app.js", () => {
  const extracted = extractAppJsDicts();

  it("evals 1207 aligned keys from the Object.assign blocks", () => {
    const zhN = Object.keys(extracted.zh).length;
    const enN = Object.keys(extracted.en).length;
    expect(zhN).toBeGreaterThanOrEqual(MIN_KEYS);
    expect(enN).toBeGreaterThanOrEqual(MIN_KEYS);
    expect(zhN).toBe(enN);
    const { onlyA, onlyB } = symmetricKeyDiff(extracted.zh, extracted.en);
    expect(onlyA).toEqual([]);
    expect(onlyB).toEqual([]);
  });

  it("emitted zh.ts/en.ts match the script output byte-for-byte", () => {
    expect(emitDictTs(extracted.zh)).toBe(readFileSync(join(HERE, "zh.ts"), "utf8"));
    expect(emitDictTs(extracted.en)).toBe(readFileSync(join(HERE, "en.ts"), "utf8"));
    expect(checkDicts(extracted)).toEqual([]);
  });

  it("imported modules key-by-key match the app.js eval (diff empty)", () => {
    expect(keyDiff(extracted.zh, zh)).toEqual([]);
    expect(keyDiff(extracted.en, en)).toEqual([]);
  });
});

describe("F-07 zh/en key sets", () => {
  it("symmetric difference is empty and count ≥ 1207", () => {
    const zhN = Object.keys(zh).length;
    const enN = Object.keys(en).length;
    expect(zhN).toBeGreaterThanOrEqual(MIN_KEYS);
    expect(enN).toBe(zhN);
    const { onlyA, onlyB } = symmetricKeyDiff(zh, en);
    expect(onlyA).toEqual([]);
    expect(onlyB).toEqual([]);
  });
});

describe("F-07 t()/tOptional semantics (app.js:144-159)", () => {
  afterEach(async () => {
    delete I18N.zh["test.only.zh"];
    delete I18N.en["test.empty"];
    delete I18N.zh["test.empty"];
    await setLang("zh");
  });

  it("loads the active locale before asserting", async () => {
    await i18nReady();
    expect(Object.keys(I18N.zh).length).toBeGreaterThanOrEqual(MIN_KEYS);
  });

  it("returns the zh string for the active language", async () => {
    await i18nReady();
    await setLang("zh");
    expect(t("theme.toggle")).toBe("切换主题");
    expect(t("theme.light")).toBe("浅色");
  });

  it("falls back to zh when en is missing the key", async () => {
    await i18nReady();
    I18N.zh["test.only.zh"] = "仅中文";
    await setLang("en");
    expect(t("test.only.zh")).toBe("仅中文");
    expect(tOptional("test.only.zh")).toBe("仅中文");
  });

  it("falls back to the key itself when neither locale has it", async () => {
    await i18nReady();
    expect(t("this.key.does.not.exist")).toBe("this.key.does.not.exist");
    expect(tOptional("this.key.does.not.exist")).toBeNull();
  });

  it("interpolates {0},{1}… positionally and leaves holes", async () => {
    await i18nReady();
    await setLang("zh");
    expect(t("toast.theme", t("theme.dark"))).toBe("主题：深色");
    expect(t("toast.theme")).toBe("主题：{0}");
    expect(t("viewer.msa.summary", "a", "b", "c")).toBe("a 条序列 · b 列 · c");
    expect(t("viewer.msa.summary", "a")).toBe("a 条序列 · {1} 列 · {2}");
  });

  it("replaces every {0} occurrence and treats 0 as a value", async () => {
    await i18nReady();
    await setLang("zh");
    expect(t("skill.invokeDirective", "fold")).toBe(
      '请使用技能「fold」：先调用 host.load_skill("fold") 载入其完整协议，然后严格按照该协议完成任务。',
    );
    I18N.zh["test.empty"] = "n={0}";
    expect(t("test.empty", 0)).toBe("n=0");
    expect(t("test.empty", "")).toBe("n=");
    expect(t("test.empty", undefined)).toBe("n={0}");
  });

  it("tOptional does not interpolate", async () => {
    await i18nReady();
    await setLang("zh");
    expect(tOptional("toast.theme")).toBe("主题：{0}");
  });

  it("treats empty-string translations as present", async () => {
    await i18nReady();
    I18N.zh["test.empty"] = "";
    I18N.en["test.empty"] = "";
    expect(t("test.empty")).toBe("");
    expect(tOptional("test.empty")).toBe("");
  });
});

describe("F-07 setLang / detectLang / applyStaticI18n", () => {
  afterEach(async () => {
    await setLang("zh");
  });

  it("setLang('en') persists os-lang and switches t()", async () => {
    await i18nReady();
    const store: Record<string, string> = {};
    // localStorage may be missing in node; stub so setLang can persist.
    const mem = {
      getItem: (k: string) => store[k] ?? null,
      setItem: (k: string, v: string) => {
        store[k] = v;
      },
      removeItem: (k: string) => {
        delete store[k];
      },
      clear: () => {
        for (const k of Object.keys(store)) delete store[k];
      },
      key: (i: number) => Object.keys(store)[i] ?? null,
      get length() {
        return Object.keys(store).length;
      },
    };
    (globalThis as { localStorage?: typeof mem }).localStorage = mem;

    await setLang("en");
    expect(LANG).toBe("en");
    expect(t("theme.toggle")).toBe("Toggle theme");
    expect(store["os-lang"]).toBe("en");
    await setLang("fr");
    expect(LANG).toBe("zh");
    expect(store["os-lang"]).toBe("zh");
  });

  it("detectLang reads os-lang then navigator.languages /^zh/i", () => {
    const store: Record<string, string> = { "os-lang": "en" };
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: { getItem: (k: string) => store[k] ?? null },
    });
    expect(detectLang()).toBe("en");
    store["os-lang"] = "zh";
    expect(detectLang()).toBe("zh");
    delete store["os-lang"];
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      value: { languages: ["en-US", "en"], language: "en-US" },
    });
    expect(detectLang()).toBe("en");
    Object.defineProperty(globalThis, "navigator", {
      configurable: true,
      value: { languages: ["zh-CN"], language: "zh-CN" },
    });
    expect(detectLang()).toBe("zh");
  });

  it("applyStaticI18n writes text/title/placeholder/value through t()", async () => {
    await i18nReady();
    await setLang("zh");
    type FakeEl = {
      textContent: string;
      title: string;
      placeholder: string;
      value: string;
      getAttribute: (name: string) => string | null;
    };
    const textEl: FakeEl = {
      textContent: "",
      title: "",
      placeholder: "",
      value: "",
      getAttribute: (name: string) => (name === "data-i18n" ? "theme.toggle" : null),
    };
    const titleEl: FakeEl = {
      textContent: "",
      title: "",
      placeholder: "",
      value: "",
      getAttribute: (name: string) =>
        name === "data-i18n-title" ? "theme.toggle" : null,
    };
    const phEl: FakeEl = {
      textContent: "",
      title: "",
      placeholder: "",
      value: "",
      getAttribute: (name: string) =>
        name === "data-i18n-ph" ? "annot.draft.placeholder" : null,
    };
    const valEl: FakeEl = {
      textContent: "",
      title: "",
      placeholder: "",
      value: "",
      getAttribute: (name: string) =>
        name === "data-i18n-val" ? "conv.title.default" : null,
    };
    const bySel: Record<string, FakeEl[]> = {
      "[data-i18n]": [textEl],
      "[data-i18n-title]": [titleEl],
      "[data-i18n-ph]": [phEl],
      "[data-i18n-val]": [valEl],
    };
    applyStaticI18n({
      querySelectorAll: (sel: string) => bySel[sel] ?? [],
    } as unknown as Parameters<typeof applyStaticI18n>[0]);
    expect(textEl.textContent).toBe("切换主题");
    expect(titleEl.title).toBe("切换主题");
    expect(phEl.placeholder).toBe(t("annot.draft.placeholder"));
    expect(valEl.value).toBe(t("conv.title.default"));
  });

  it("onLanguageChange runs after setLang", async () => {
    await i18nReady();
    const seen: string[] = [];
    const stop = onLanguageChange(() => {
      seen.push(LANG);
    });
    await setLang("en");
    expect(seen).toEqual(["en"]);
    stop();
    await setLang("zh");
    expect(seen).toEqual(["en"]);
  });
});

describe("F-07 plan-mode payload (app.js:7955-7959 → t())", () => {
  afterEach(async () => {
    await setLang("zh");
  });

  it("builds the payload from plan.prompt.* through t(), not a Chinese literal", async () => {
    await i18nReady();
    await setLang("zh");
    const task = "fold BRCA1";
    const payload = planModePayload(task);
    expect(payload.startsWith(t("plan.prompt.intro"))).toBe(true);
    expect(payload.includes(t("plan.prompt.part1"))).toBe(true);
    expect(payload.includes(t("plan.prompt.part2"))).toBe(true);
    expect(payload.includes(t("plan.prompt.jsonSchema"))).toBe(true);
    expect(payload.endsWith(task)).toBe(true);
    expect(payload).toBe(
      t("plan.prompt.intro") +
        t("plan.prompt.part1") +
        t("plan.prompt.part2") +
        t("plan.prompt.jsonSchema") +
        t("plan.prompt.part3") +
        task,
    );
    // The send() literal used a shorter Chinese schema; the dictionary is
    // the i18n source of truth and must be what planModePayload emits.
    expect(payload.includes('["产出文件名.csv"]')).toBe(false);

    await setLang("en");
    const enPayload = planModePayload(task);
    expect(enPayload.startsWith("[Plan Mode]")).toBe(true);
    expect(enPayload.endsWith(task)).toBe(true);
    expect(enPayload.startsWith("[计划模式]")).toBe(false);
  });
});

describe("F-07 inactive locale is a dynamic import()", () => {
  it("runtime.ts import()s ./zh and ./en as separate specifiers", () => {
    const src = readFileSync(join(HERE, "runtime.ts"), "utf8");
    expect(src).toMatch(/import\(\s*["']\.\/en["']\s*\)/);
    expect(src).toMatch(/import\(\s*["']\.\/zh["']\s*\)/);
    expect(src).not.toMatch(/import\s+\w+\s+from\s+["']\.\/en["']/);
    expect(src).not.toMatch(/import\s+\w+\s+from\s+["']\.\/zh["']/);
  });

  it("loadLocale('en') fills I18N.en without a static import in runtime", async () => {
    await loadLocale("en");
    expect(I18N.en["theme.toggle"]).toBe("Toggle theme");
  });
});
