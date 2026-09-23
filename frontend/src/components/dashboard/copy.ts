import { LANG, tOptional } from "../../i18n/runtime";

/**
 * Dashboard hero copy. New strings live here so the generated F-07
 * `i18n/en.ts` / `zh.ts` extract stays byte-identical to `app.js`.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "dash.hero.title": "从哪里继续？",
    "dash.hero.sub":
      "回到正在运行的分析，或新建一个项目。智能体会编写并运行 Python 与 R 代码、联网检索、调用技能，并把图表、报告和结构文件留在项目里。",
  },
  en: {
    "dash.hero.title": "Where to next?",
    "dash.hero.sub":
      "Return to a running analysis or start a new project. The agent writes and runs Python and R, searches the web, calls skills, and keeps the figures, reports and structure files in the project.",
  },
};

export function dashT(key: string): string {
  const fromDict = tOptional(key);
  if (fromDict != null) return fromDict;
  const table = COPY[LANG] || COPY.en;
  return table[key] || COPY.en[key] || key;
}
