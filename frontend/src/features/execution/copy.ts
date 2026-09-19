import { LANG } from "../../i18n/runtime";

const COPY = {
  en: {
    invalid: "The response does not contain a valid provenance record.",
    failed: "Could not read this record: {0}",
    retry: "Retry reading",
    noCode: "No producing code is included in this record.",
    codeElsewhere: "The producing Cell is recorded, but its source is not included in this view.",
    inputs: "Recorded inputs",
    noInputs: "No inputs were recorded. This does not establish that there were no dependencies.",
    noLineage: "No lineage was recorded for this version.",
    otherNotebook: "This artifact belongs to another session. Its recorded source is available in Code and Review.",
    unknownSession: "The owning session is not available in this view.",
    unknownEnvironment: "Not recorded",
    packages: "Recorded packages",
  },
  zh: {
    invalid: "响应未包含有效的溯源记录。",
    failed: "无法读取此记录：{0}",
    retry: "重试读取",
    noCode: "此记录未包含生产代码。",
    codeElsewhere: "已记录生产此版本的 Cell，但此面板未包含其源码。",
    inputs: "已记录输入",
    noInputs: "未记录输入。这不能证明没有依赖。",
    noLineage: "此版本未记录溯源信息。",
    otherNotebook: "此产物属于其他会话。可在代码和审查中查看已记录来源。",
    unknownSession: "此视图未包含所属会话信息。",
    unknownEnvironment: "未记录",
    packages: "已记录的包",
  },
};

export function provenanceT(key: keyof typeof COPY.en, ...args: unknown[]): string {
  return COPY[LANG === "zh" ? "zh" : "en"][key].replace(/\{(\d+)\}/g, (_, n: string) => String(args[Number(n)] ?? ""));
}
