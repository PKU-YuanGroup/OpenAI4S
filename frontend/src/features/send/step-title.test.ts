import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { stepSummaryText, stepTitleText } from "./step-title";

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
    expect(stepSummaryText("12 results")).toBe("12 条结果");
    expect(stepSummaryText("1 artifact")).toBe("1 个产物");
    expect(stepSummaryText("exit 0 · 3 file changes")).toBe("退出码 0 · 3 个文件变更");
    expect(stepSummaryText("failed: timeout")).toBe("失败：timeout");
  });

  it("leaves English, unknown titles and a cell's own title alone", async () => {
    expect(stepTitleText("Reading project file cohort.csv")).toBe("Reading project file cohort.csv");
    await setLang("zh");
    expect(stepTitleText("Fit the growth model")).toBe("Fit the growth model");
    expect(stepTitleText("Running analysis · cell 3")).toBe("Running analysis · cell 3");
    expect(stepSummaryText("loaded · network limited to 18 domains")).toBe("loaded · network limited to 18 domains");
  });
});
