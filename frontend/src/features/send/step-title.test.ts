import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { stepSummaryFromParts, stepSummaryText, stepTitleText } from "./step-title";

afterEach(async () => {
  await setLang("en");
});

describe("step titles and summaries in the UI language (I18N-1)", () => {
  it("translates the host's English templates in the Chinese UI", async () => {
    await setLang("zh");
    expect(stepTitleText("Reading project file cohort.csv")).toBe("读取项目文件 cohort.csv");
    expect(stepTitleText("Reading api.crossref.org")).toBe("读取 api.crossref.org");
    expect(stepTitleText("Saving 2 artifacts")).toBe("保存 2 个产物");
    expect(stepTitleText("Saving bar_plot.png")).toBe("保存 bar_plot.png");
    expect(stepTitleText("Switching to the local r-mini environment")).toBe("切换到本机环境 r-mini");
    expect(stepTitleText("Installing lifelines into base")).toBe("安装 lifelines 到 base");
    expect(stepTitleText("Running which")).toBe("运行 which");
    expect(stepTitleText("Calling calc via example")).toBe("通过 example 调用 calc");
    expect(stepTitleText("Calling now via example")).toBe("通过 example 调用 now");
    expect(stepSummaryText("12 results")).toBe("12 条结果");
    expect(stepSummaryText("1 artifact")).toBe("1 个产物");
    expect(stepSummaryText("exit 0 · 3 file changes")).toBe("退出码 0 · 3 个文件变更");
    expect(stepSummaryText("failed: timeout")).toBe("失败：timeout");
    // M-75: the Reviewer card was English in the Chinese UI.
    expect(stepTitleText("Reviewer")).toBe("审阅");
    expect(stepSummaryText("Review unavailable")).toBe("审阅未完成");
    expect(stepSummaryText("No issues found")).toBe("未发现问题");
    expect(stepSummaryText("Review cancelled")).toBe("审阅已取消");
  });

  it("says which lines a paged read returned, and names a spill file plainly (TURN-3)", async () => {
    await setLang("zh");
    expect(stepTitleText("Reading an earlier step's full output")).toBe("读取先前步骤的完整输出");
    expect(stepSummaryText("lines 121–270 of 336")).toBe("第 121–270 行，共 336 行");
    expect(stepSummaryText("past the end (336 lines)")).toBe("已超出文件末尾（共 336 行）");
    expect(stepSummaryText("336 lines")).toBe("336 行");
  });

  it("reads a cell's banner comment as its words, not its rule characters (CHAT-8)", async () => {
    expect(stepTitleText("---------- QC decisions ----------")).toBe("QC decisions");
    expect(stepTitleText("=== Fit the curves ===")).toBe("Fit the curves");
    expect(stepTitleText("step 2 -- fit")).toBe("step 2 -- fit");
    // Only rule characters: nothing better to show than what came.
    expect(stepTitleText("----------")).toBe("----------");
    await setLang("zh");
    expect(stepTitleText("---------- QC 决策 ----------")).toBe("QC 决策");
  });

  it("leaves English, unknown titles and a cell's own title alone", async () => {
    expect(stepTitleText("Reading project file cohort.csv")).toBe("Reading project file cohort.csv");
    await setLang("zh");
    expect(stepTitleText("Fit the growth model")).toBe("Fit the growth model");
    expect(stepTitleText("Running analysis · cell 3")).toBe("Running analysis · cell 3");
    expect(stepSummaryText("120 aa · mean pLDDT 87.5")).toBe("120 aa · mean pLDDT 87.5");
  });

  it("translates the composite lines of steps stored before structured summaries (CHAT-6)", async () => {
    await setLang("zh");
    expect(stepSummaryText("4 envs, 3 missing")).toBe("4 个运行环境 · 缺少 3 个软件包");
    expect(stepSummaryText("6 results · you.com")).toBe("6 条结果 · you.com");
    expect(stepSummaryText("loaded")).toBe("已加载");
    expect(stepSummaryText("1 change")).toBe("1 处修改");
    expect(stepSummaryText("loaded · network limited to 18 domains")).toBe("已加载 · 网络限于 18 个域名");
    expect(stepSummaryText("3 envs · use bio")).toBe("3 个运行环境 · 建议使用 bio");
    expect(stepSummaryText("120 chars read")).toBe("已读取 120 个字符");
  });

  it("words a structured summary in the UI language (CHAT-6)", async () => {
    const envs = [
      { key: "count", unit: "env", n: 4 },
      { key: "missing", n: 3 },
    ];
    const search = [
      { key: "count", unit: "result", n: 6 },
      { key: "text", text: "you.com" },
    ];
    await setLang("zh");
    expect(stepSummaryFromParts(envs)).toBe("4 个运行环境 · 缺少 3 个软件包");
    expect(stepSummaryFromParts(search)).toBe("6 条结果 · you.com");
    expect(stepSummaryFromParts([{ key: "loaded" }, { key: "networkLimited", n: 2 }])).toBe(
      "已加载 · 网络限于 2 个域名",
    );
    expect(stepSummaryFromParts([{ key: "count", unit: "byte", n: 5312345 }])).toBe("5,312,345 字节");
    expect(stepSummaryFromParts([{ key: "failed", reason: "no such file" }])).toBe("失败：no such file");
    expect(stepSummaryFromParts([{ key: "delegate", status: "partial" }])).toBe("部分完成");
    expect(stepSummaryFromParts([{ key: "lines", from: 11, to: 12, total: 40 }])).toBe("第 11–12 行，共 40 行");
    await setLang("en");
    expect(stepSummaryFromParts(envs)).toBe("4 envs · 3 missing");
    expect(stepSummaryFromParts([{ key: "count", unit: "change", n: 1 }])).toBe("1 change");
    expect(stepSummaryFromParts([{ key: "count", unit: "child", n: 2 }])).toBe("2 children");
  });

  it("falls back to the English line when a part is not one this build knows", async () => {
    await setLang("zh");
    expect(stepSummaryFromParts([{ key: "count", unit: "result", n: 2 }, { key: "fromTheFuture" }])).toBeNull();
    expect(stepSummaryFromParts([{ key: "count", unit: "parsec", n: 2 }])).toBeNull();
    expect(stepSummaryFromParts(undefined)).toBeNull();
    expect(stepSummaryFromParts([])).toBeNull();
  });
});
