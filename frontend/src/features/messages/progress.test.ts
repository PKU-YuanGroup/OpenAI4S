/**
 * Decision 30: the server's progress lines are said in the UI language at
 * display time; the model's prose never is. The server side of the same
 * table is pinned by tests/test_progress_copy_parity.py.
 */
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { LANG, i18nReady, setLang, type Lang } from "../../i18n/runtime";
import { PROGRESS_COPY, PROGRESS_SHOWN, resayProgress } from "./progress";

const zh = PROGRESS_COPY.zh;
const en = PROGRESS_COPY.en;
const fill = (text: string, ...args: string[]) => text.replace(/\{(\d+)\}/g, (_hole, n: string) => args[+n]!);
/** What the workbench shows for a template (its own term where the server's differs). */
const shownZh = (key: keyof typeof zh): string => PROGRESS_SHOWN.zh?.[key] ?? zh[key];

let original: Lang;
beforeAll(async () => {
  await i18nReady();
  original = LANG;
});
afterAll(async () => {
  await setLang(original);
});

describe("an action's progress line", () => {
  it("is said in the other language, both ways, for every template", () => {
    const keys = Object.keys(zh) as Array<keyof typeof zh>;
    const sample = ["3", "ValueError: x", "lab_env"];
    for (const key of keys) {
      const zhText = fill(zh[key], ...sample);
      const enText = fill(en[key], ...sample);
      // Bookkeeping and finished-cell lines are checked below with their own
      // shapes; every whole-line template round-trips here.
      if (key.startsWith("book.") || key.startsWith("finished.")) continue;
      const zhShown = fill(shownZh(key), ...sample);
      expect(resayProgress(zhText, "en")).toBe(enText);
      expect(resayProgress(enText, "zh")).toBe(zhShown);
      expect(resayProgress(zhText, "zh")).toBe(zhShown);
    }
  });

  it("uses the workbench's zh term where the server's line says Artifacts (decision 10)", () => {
    expect(zh.save).toContain("Artifacts");
    expect(resayProgress(en.save, "zh")).toBe("我正在保存阶段性结果，写出的文件会加入产物。");
    expect(resayProgress(zh.save, "zh")).toBe("我正在保存阶段性结果，写出的文件会加入产物。");
    expect(resayProgress(zh.save, "en")).toBe(en.save);
  });

  it("keeps its parameters: the step count, R or Python, the error text", () => {
    expect(resayProgress("我正在执行 7 个相关步骤，并会根据返回结果继续分析。", "en")).toBe(
      "I am running 7 related steps and will continue from their returned results.",
    );
    expect(resayProgress(en["cell.r"], "zh")).toBe(zh["cell.r"]);
    expect(resayProgress(en["cell.python"], "zh")).toBe(zh["cell.python"]);
    const error = "KeyError: 'treatment'";
    expect(resayProgress(fill(zh.failed, error), "en")).toBe(fill(en.failed, error));
    expect(resayProgress(fill(en["failed.module"], "ModuleNotFoundError: No module named 'Bio'", "Bio", "biopython"), "zh")).toBe(
      "这个单元运行出错：`ModuleNotFoundError: No module named 'Bio'`。`Bio` 由 `biopython` 包提供，当前环境没有安装。智能体安装前会先询问你；也可以在 设置 → 计算 里安装。",
    );
    expect(resayProgress(fill(zh["refused.reason"], "blocked by rule net-deny"), "en")).toBe(
      fill(en["refused.reason"], "blocked by rule net-deny"),
    );
  });

  it("reads rows stored before SCI-35 and the zh term change as the template they became", () => {
    expect(
      resayProgress(
        "I have prepared a R cell and am running it now; its actual output will be recorded in the Notebook.",
        "zh",
      ),
    ).toBe(zh["cell.r"]);
    expect(resayProgress("我已经准备好一个 Python Cell，正在执行；真实输出会持续记录到 Notebook。", "en")).toBe(en["cell.python"]);
    expect(resayProgress("我已经准备好一个 Python Cell，正在执行；真实输出会持续记录到 Notebook。", "zh")).toBe(zh["cell.python"]);
  });

  it("keeps the whitespace around a live chunk", () => {
    expect(resayProgress("\n\n" + en.plan + "\n\n", "zh")).toBe("\n\n" + zh.plan + "\n\n");
    expect(resayProgress(en["stop.user"] + "\n", "zh")).toBe(zh["stop.user"] + "\n");
  });

  it("follows the UI language when none is given", async () => {
    await setLang("en");
    expect(resayProgress(zh.search)).toBe(en.search);
    await setLang("zh");
    expect(resayProgress(en.search)).toBe(zh.search);
  });
});

describe("a bookkeeping cell's line", () => {
  it("says each sentence again, with its numbers and names", () => {
    const enRow =
      "Updated the plan (3 steps, 1 done). Listed 2 environments. Switched to the lab_env environment, from the next cell on.";
    const zhRow = "更新了计划（3 步，完成 1 步）。列出了 2 个环境。已切换到 lab_env 环境，下一个单元起生效。";
    expect(resayProgress(enRow, "zh")).toBe(zhRow);
    expect(resayProgress(zhRow, "en")).toBe(enRow);
  });

  it("keeps package versions and file names intact", () => {
    const enRow = "Installed statsmodels 0.15.0, patsy 1.0.1. Could not install nope.";
    const zhRow = "已安装 statsmodels 0.15.0, patsy 1.0.1。未能安装 nope。";
    expect(resayProgress(enRow, "zh")).toBe(zhRow);
    expect(resayProgress(zhRow, "en")).toBe(enRow);
    expect(resayProgress("Registered the artifact volcano_plot.v2.png.", "zh")).toBe("已登记产物 volcano_plot.v2.png。");
    expect(resayProgress("已登记产物 火山图.png。", "en")).toBe("Registered the artifact 火山图.png.");
    expect(resayProgress("The install did not complete; see the Notebook for details.", "zh")).toBe(zh["book.incomplete"]);
  });
});

describe("a finished cell's line", () => {
  it("says the header and notes again and keeps the output block verbatim", () => {
    const block = "```text\nslope 2.1\n\nIntercept 0.3 (p < 0.05)\n```";
    const enRow = [en["finished.head"], block, fill(en["finished.lines"], "8"), fill(en["finished.stderr"], "2")].join("\n");
    const zhRow = [zh["finished.head"], block, fill(zh["finished.lines"], "8"), fill(zh["finished.stderr"], "2")].join("\n");
    expect(resayProgress(enRow, "zh")).toBe(zhRow);
    expect(resayProgress(zhRow, "en")).toBe(enRow);
    const long = [zh["finished.head"], "```text\nrow 1\n```", fill(zh["finished.long"], "12,345")].join("\n");
    expect(resayProgress(long, "en")).toBe(
      [en["finished.head"], "```text\nrow 1\n```", fill(en["finished.long"], "12,345")].join("\n"),
    );
    expect(resayProgress([en["finished.output"], "```text\n42\n```"].join("\n"), "zh")).toBe(
      [zh["finished.output"], "```text\n42\n```"].join("\n"),
    );
    expect(resayProgress(en["finished.none"], "zh")).toBe(zh["finished.none"]);
    expect(resayProgress([zh["finished.plain"], fill(zh["finished.stderr"], "1")].join("\n"), "en")).toBe(
      [en["finished.plain"], fill(en["finished.stderr"], "1")].join("\n"),
    );
  });
});

describe("the model's own prose", () => {
  it("is shown as written", () => {
    for (const prose of [
      "Ages are right-skewed (median 61).",
      "I am running a sensitivity analysis next because the effect is small.",
      "我先看一下缺失值的分布。",
      "更新了计划：先做 QC，再拟合生长模型。",
      "Installed packages are enough; the fit converged for every well.",
      // A template with words of the model's own around it is the model's.
      en["cell.python"] + " Then I will fit the growth model.",
      "Note: " + en.plan,
      // A finished-cell header with prose after the block, not a note.
      [en["finished.output"], "```text\n1\n```", "So the mean is 1."].join("\n"),
      // Sentences from both languages in one row.
      "Updated the plan. 列出了 2 个环境。",
    ]) {
      expect(resayProgress(prose, "zh")).toBe(prose);
      expect(resayProgress(prose, "en")).toBe(prose);
    }
  });
});
