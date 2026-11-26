/**
 * Owner decision 30: the platform's own progress lines are said in the UI
 * language at display time.
 *
 * The server writes deterministic status rows around the agent's actions
 * (`openai4s/server/completions.py`: `action_narration`, `outcome_narration`,
 * `_bookkeeping_narration`, `cancellation_marker`) in the language of the
 * turn's request, and stores them as assistant rows. Viewed later in the
 * other UI language they stayed in the first one: "我已经准备好一个 Python
 * 单元……" in an English workbench. A row (or live chunk) that is wholly one
 * of these templates is recognised in either language, its parameters kept
 * (a step count, R or Python, the error text in backticks, a line or
 * character total, a file or environment name), and said again from the
 * table below in the current language. A cell's output block is kept
 * verbatim; only the template sentences around it change. Anything else,
 * the model's own prose above all, is shown as written. The stored row is
 * unchanged: Copy, the rating key and the end-of-turn reconcile read it.
 *
 * Every template is written here exactly as the server writes it, `{0}`,
 * `{1}`, … for its parameters. `tests/test_progress_copy_parity.py` generates
 * the real sentences from `completions.py` and fails when one is not in this
 * file, so a server rewording cannot drift from this table unnoticed.
 */

import { LANG, languageRevision, type Lang } from "../../i18n/runtime";

/** One whole row: an action's line, a failure, a refusal, a stop marker. */
const LINE_KEYS = [
  "cell.python",
  "cell.r",
  "steps",
  "search.first",
  "search",
  "fetch",
  "save",
  "inspect.first",
  "inspect",
  "plan",
  "env",
  "next",
  "stop.budget",
  "stop.user",
  "failed",
  "failed.module",
  "failed.package",
  "failed.rPackage",
  "refused.timeout",
  "refused.denied",
  "refused.reason",
] as const;

/** A bookkeeping cell's sentences, one or more in a row (zh joined with "", en with " "). */
const BOOK_KEYS = [
  "book.artifact",
  "book.artifactAny",
  "book.planSteps",
  "book.plan",
  "book.progress",
  "book.read",
  "book.envs",
  "book.envsAny",
  "book.envUse",
  "book.envUseAny",
  "book.installed",
  "book.failed",
  "book.incomplete",
  "book.done",
] as const;

/** A finished cell's first line; the first two are followed by its output block. */
const FINISHED_HEADS = ["finished.output", "finished.head", "finished.plain", "finished.none"] as const;
/** The lines after it. */
const FINISHED_TAILS = ["finished.long", "finished.lines", "finished.stderr"] as const;

type ProgressKey =
  | (typeof LINE_KEYS)[number]
  | (typeof BOOK_KEYS)[number]
  | (typeof FINISHED_HEADS)[number]
  | (typeof FINISHED_TAILS)[number];

/** The server's templates, verbatim. */
export const PROGRESS_COPY: Record<Lang, Record<ProgressKey, string>> = {
  zh: {
    "cell.python": "我已经准备好一个 Python 单元，正在执行；真实输出会持续记录到 Notebook。",
    "cell.r": "我已经准备好一个 R 单元，正在执行；真实输出会持续记录到 Notebook。",
    steps: "我正在执行 {0} 个相关步骤，并会根据返回结果继续分析。",
    "search.first": "我先检索相关来源，随后会说明检索结果如何影响下一步分析。",
    search: "我正在检索相关来源。",
    fetch: "我正在读取并核对这个来源中的关键证据。",
    save: "我正在保存阶段性结果，写出的文件会加入 Artifacts。",
    "inspect.first": "我先检查相关文件和数据，再根据实际内容继续分析。",
    inspect: "我正在查看相关文件和数据。",
    plan: "我正在更新计划。",
    env: "我正在准备适合这一步分析的运行环境。",
    next: "我正在执行下一步，并会把关键结果反馈在对话中。",
    "stop.budget": "_已停止：已达到自动模式预算。_",
    "stop.user": "_已由用户停止。_",
    failed: "这个单元运行出错：`{0}`。智能体会根据这条报错修正后重试。",
    "failed.module":
      "这个单元运行出错：`{0}`。`{1}` 由 `{2}` 包提供，当前环境没有安装。智能体安装前会先询问你；也可以在 设置 → 计算 里安装。",
    "failed.package":
      "这个单元运行出错：`{0}`。当前环境没有安装 `{1}` 包。智能体安装前会先询问你；也可以在 设置 → 计算 里安装。",
    "failed.rPackage":
      "这个单元运行出错：`{0}`。当前 R 环境没有 `{1}` 包。会话里不能安装 R 包：运行 `openai4s setup` 构建托管的 R 环境，或在本机 R 里安装后选用它。",
    "refused.timeout": "这个单元里需要批准的操作等待批准超时，没有执行。",
    "refused.denied": "这个单元里需要批准的操作被你拒绝了，没有执行。智能体会据此调整下一步。",
    "refused.reason": "这个单元里需要批准的操作未获批准，没有执行（原因：{0}）。智能体会据此调整下一步。",
    "book.artifact": "已登记产物 {0}。",
    "book.artifactAny": "已登记产物。",
    "book.planSteps": "更新了计划（{0} 步，完成 {1} 步）。",
    "book.plan": "更新了计划。",
    "book.progress": "更新了计划进度。",
    "book.read": "查看了当前计划。",
    "book.envs": "列出了 {0} 个环境。",
    "book.envsAny": "列出了可用环境。",
    "book.envUse": "已切换到 {0} 环境，下一个单元起生效。",
    "book.envUseAny": "已切换运行环境。",
    "book.installed": "已安装 {0}。",
    "book.failed": "未能安装 {0}。",
    "book.incomplete": "安装没有完成，详情见 Notebook。",
    "book.done": "安装步骤已结束。",
    "finished.output": "单元已运行结束，输出：",
    "finished.head": "单元已运行结束，输出的前几行：",
    "finished.plain": "单元已运行结束。",
    "finished.none": "单元已运行结束，没有输出。",
    "finished.long": "（输出很长，共 {0} 个字符，完整输出在 Notebook。）",
    "finished.lines": "（共 {0} 行，完整输出在 Notebook。）",
    "finished.stderr": "另有 stderr {0} 行，可能包含警告或错误，请在 Notebook 查看。",
  },
  en: {
    "cell.python": "I have prepared a Python cell and am running it now; its actual output will be recorded in the Notebook.",
    "cell.r": "I have prepared an R cell and am running it now; its actual output will be recorded in the Notebook.",
    steps: "I am running {0} related steps and will continue from their returned results.",
    "search.first":
      "I am searching relevant sources first, then I will explain how the results affect the next analysis step.",
    search: "I am searching relevant sources.",
    fetch: "I am reading this source and checking its key evidence.",
    save: "I am saving the current results; written files will be added to Artifacts.",
    "inspect.first": "I am inspecting the relevant files and data before continuing from their actual contents.",
    inspect: "I am looking at the relevant files and data.",
    plan: "I am updating the plan.",
    env: "I am preparing the appropriate runtime environment for this analysis step.",
    next: "I am running the next step and will report its key result in the conversation.",
    "stop.budget": "_Stopped: the Auto Mode budget was reached._",
    "stop.user": "_Stopped by user._",
    failed: "This cell failed: `{0}`. The agent will correct it from this error and try again.",
    "failed.module":
      "This cell failed: `{0}`. `{1}` is installed as the `{2}` package, which this environment does not have. The agent asks before installing it; you can also install it in Settings → Compute.",
    "failed.package":
      "This cell failed: `{0}`. This environment does not have the `{1}` package. The agent asks before installing it; you can also install it in Settings → Compute.",
    "failed.rPackage":
      "This cell failed: `{0}`. This R environment does not have the `{1}` package. R packages cannot be installed from a session: run `openai4s setup` to build the managed R environment, or install it in your own R and select that.",
    "refused.timeout": "An action in this cell that needed approval timed out waiting for it and did not run.",
    "refused.denied":
      "An action in this cell that needed approval was declined by you and did not run. The agent will adjust its next step.",
    "refused.reason":
      "An action in this cell that needed approval was not approved and did not run (reason: {0}). The agent will adjust its next step.",
    "book.artifact": "Registered the artifact {0}.",
    "book.artifactAny": "Registered an artifact.",
    "book.planSteps": "Updated the plan ({0} steps, {1} done).",
    "book.plan": "Updated the plan.",
    "book.progress": "Updated the plan's progress.",
    "book.read": "Checked the current plan.",
    "book.envs": "Listed {0} environments.",
    "book.envsAny": "Listed the available environments.",
    "book.envUse": "Switched to the {0} environment, from the next cell on.",
    "book.envUseAny": "Switched the runtime environment.",
    "book.installed": "Installed {0}.",
    "book.failed": "Could not install {0}.",
    "book.incomplete": "The install did not complete; see the Notebook for details.",
    "book.done": "The install step finished.",
    "finished.output": "The cell finished. Output:",
    "finished.head": "The cell finished. First lines of its output:",
    "finished.plain": "The cell finished.",
    "finished.none": "The cell finished with no output.",
    "finished.long": "(The output is long: {0} characters in total; the full output is in the Notebook.)",
    "finished.lines": "({0} lines in total; the full output is in the Notebook.)",
    "finished.stderr": "It also wrote {0} stderr line(s), which may contain warnings or errors; see the Notebook.",
  },
};

/**
 * Where what is shown differs from what the server wrote: the server's zh
 * line says "Artifacts", and the workbench's term is 产物 (decision 10). The
 * server's wording above is still what is recognised.
 */
export const PROGRESS_SHOWN: Partial<Record<Lang, Partial<Record<ProgressKey, string>>>> = {
  zh: { save: "我正在保存阶段性结果，写出的文件会加入产物。" },
};

/**
 * Rows stored by earlier versions, recognised as the template they became:
 * "a R cell" before SCI-35, "Cell" before the zh term was 单元.
 */
const LEGACY: ReadonlyArray<{ key: ProgressKey; lang: Lang; text: string }> = [
  { key: "cell.r", lang: "en", text: PROGRESS_COPY.en["cell.r"].replace("an R", "a R") },
  // Built from the current wording: the old zh term is recognised, never shown.
  { key: "cell.python", lang: "zh", text: PROGRESS_COPY.zh["cell.python"].replace("单元", "Cell") },
  { key: "cell.r", lang: "zh", text: PROGRESS_COPY.zh["cell.r"].replace("单元", "Cell") },
];

/**
 * What each parameter may be: what the server can put there (the same
 * character sets `_BOOKKEEPING_SENTENCE` recognises), so ordinary prose such
 * as "Installed packages are enough; …" is not taken for a template.
 */
const COUNT = "\\d+";
const QUOTED = "[^`\\n]+";
const PACKAGES = "[A-Za-z0-9_.+, -]+?";
const FILENAME = "[\\p{L}\\p{N}\\p{M}_() +-][\\p{L}\\p{N}\\p{M}_.() +-]*?";
const PARAMS: Partial<Record<ProgressKey, Record<number, string>>> = {
  steps: { 0: COUNT },
  failed: { 0: QUOTED },
  "failed.module": { 0: QUOTED, 1: QUOTED, 2: QUOTED },
  "failed.package": { 0: QUOTED, 1: QUOTED },
  "failed.rPackage": { 0: QUOTED, 1: QUOTED },
  "refused.reason": { 0: ".+?" },
  "book.artifact": { 0: FILENAME },
  "book.planSteps": { 0: COUNT, 1: COUNT },
  "book.envs": { 0: COUNT },
  "book.envUse": { 0: "\\S+?" },
  "book.installed": { 0: PACKAGES },
  "book.failed": { 0: PACKAGES },
  "finished.long": { 0: "\\d[\\d,]*" },
  "finished.lines": { 0: COUNT },
  "finished.stderr": { 0: COUNT },
};

type Compiled = { re: RegExp; order: number[] };

const escapeRe = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * A template as a regex: literal text escaped, each `{n}` a capture group.
 * `order` maps the groups, in the order they appear, back to `n`.
 */
function compile(key: ProgressKey, lang: Lang, template: string, mode: "whole" | "sticky"): Compiled {
  const order: number[] = [];
  // A bookkeeping sentence's free parameter stops at its own sentence end.
  const loose = lang === "zh" ? "[^。]+?" : ".+?";
  const source = template
    .split(/(\{\d+\})/)
    .map((part) => {
      const hole = /^\{(\d+)\}$/.exec(part);
      if (!hole) return escapeRe(part);
      const n = Number(hole[1]);
      order.push(n);
      return "(" + ((PARAMS[key] && PARAMS[key]![n]) || loose) + ")";
    })
    .join("");
  if (mode === "whole") return { re: new RegExp("^" + source + "$", "u"), order };
  // In a run of English sentences each one ends at a space or the end.
  return { re: new RegExp(source + (lang === "en" ? "(?= |$)" : ""), "yu"), order };
}

const LANGS: readonly Lang[] = ["zh", "en"];
let compiled: {
  whole: Array<{ key: ProgressKey; c: Compiled }>;
  book: Record<Lang, Array<{ key: ProgressKey; c: Compiled }>>;
  heads: Array<{ key: ProgressKey; c: Compiled }>;
  tails: Array<{ key: ProgressKey; c: Compiled }>;
} | null = null;

function tables(): NonNullable<typeof compiled> {
  if (compiled) return compiled;
  const whole = (keys: readonly ProgressKey[]) =>
    keys.flatMap((key) => LANGS.map((lang) => ({ key, c: compile(key, lang, PROGRESS_COPY[lang][key], "whole") })));
  compiled = {
    whole: [
      ...whole(LINE_KEYS),
      ...LEGACY.map(({ key, lang, text }) => ({ key, c: compile(key, lang, text, "whole") })),
    ],
    book: {
      zh: BOOK_KEYS.map((key) => ({ key, c: compile(key, "zh", PROGRESS_COPY.zh[key], "sticky") })),
      en: BOOK_KEYS.map((key) => ({ key, c: compile(key, "en", PROGRESS_COPY.en[key], "sticky") })),
    },
    heads: whole(FINISHED_HEADS),
    tails: whole(FINISHED_TAILS),
  };
  return compiled;
}

function argsOf(match: RegExpExecArray, order: number[]): string[] {
  const args: string[] = [];
  order.forEach((n, i) => {
    args[n] = match[i + 1] || "";
  });
  return args;
}

function say(key: ProgressKey, lang: Lang, args: readonly string[] = []): string {
  const text = (PROGRESS_SHOWN[lang] && PROGRESS_SHOWN[lang]![key]) || PROGRESS_COPY[lang][key];
  return text.replace(/\{(\d+)\}/g, (hole, n: string) => (args[+n] != null ? args[+n]! : hole));
}

function matchWhole(
  line: string,
  list: ReadonlyArray<{ key: ProgressKey; c: Compiled }>,
): { key: ProgressKey; args: string[] } | null {
  for (const { key, c } of list) {
    const m = c.re.exec(line);
    if (m) return { key, args: argsOf(m, c.order) };
  }
  return null;
}

/** A bookkeeping row: every sentence one of the templates, in one language. */
function resayBookkeeping(body: string, target: Lang): string | null {
  for (const lang of LANGS) {
    const said: string[] = [];
    let pos = 0;
    while (pos < body.length) {
      if (said.length && lang === "en") {
        if (body[pos] !== " ") break;
        pos += 1;
      }
      let hit = false;
      for (const { key, c } of tables().book[lang]) {
        c.re.lastIndex = pos;
        const m = c.re.exec(body);
        if (!m) continue;
        said.push(say(key, target, argsOf(m, c.order)));
        pos = c.re.lastIndex;
        hit = true;
        break;
      }
      if (!hit) break;
    }
    if (said.length && pos === body.length) return said.join(target === "zh" ? "" : " ");
  }
  return null;
}

/** A finished cell: its header, its output block kept verbatim, then its notes. */
function resayFinished(body: string, target: Lang): string | null {
  const lines = body.split("\n");
  const head = matchWhole(lines[0] || "", tables().heads);
  if (!head) return null;
  const out = [say(head.key, target)];
  let next = 1;
  if (head.key === "finished.output" || head.key === "finished.head") {
    if (lines[1] !== "```text") return null;
    const close = lines.indexOf("```", 2);
    if (close < 0) return null;
    out.push(...lines.slice(1, close + 1));
    next = close + 1;
  }
  for (const line of lines.slice(next)) {
    const tail = matchWhole(line, tables().tails);
    if (!tail) return null;
    out.push(say(tail.key, target, tail.args));
  }
  return out.join("\n");
}

/** True when `text` is wholly one of the server's progress templates, in either language. */
export function isProgressLine(text: string): boolean {
  const body = String(text ?? "").trim();
  if (!body) return false;
  if (!body.includes("\n") && matchWhole(body, tables().whole)) return true;
  return resayBookkeeping(body, LANG) != null || resayFinished(body, LANG) != null;
}

/**
 * `text` in `lang` (the UI language) when it is wholly one of the server's
 * progress templates, in either language; otherwise `text` unchanged.
 * Whitespace around it is kept, so a live chunk's paragraph break stays.
 */
export function resayProgress(text: string, lang?: Lang): string {
  void languageRevision.value; // a render reading this repaints on a switch
  const target: Lang = lang || LANG;
  const raw = String(text ?? "");
  const body = raw.trim();
  if (!body) return raw;
  const lead = raw.slice(0, raw.indexOf(body));
  const trail = raw.slice(lead.length + body.length);
  const line = body.includes("\n") ? null : matchWhole(body, tables().whole);
  const said = line ? say(line.key, target, line.args) : resayBookkeeping(body, target) ?? resayFinished(body, target);
  return said == null ? raw : lead + said + trail;
}
