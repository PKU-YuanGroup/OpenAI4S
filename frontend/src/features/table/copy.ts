import { LANG, tOptional } from "../../i18n/runtime";

/**
 * M-04 copy. Existing `wb.table.*` keys stay in the F-07 dictionaries.
 * New Schema/Distribution/Export strings live here so we do not rewrite
 * the generated `i18n/en.ts` / `zh.ts` extract.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "wb.table.schema": "结构",
    "wb.table.distribution": "分布",
    "wb.table.export": "导出当前筛选",
    "wb.table.export.csv": "下载 CSV",
    "wb.table.export.note": "由服务端流式导出，本页不会把全量数据读进浏览器。",
    "wb.table.approximate": "近似",
    "wb.table.approximate.hint": "唯一值可能是下限；类别直方图最多 50 箱。不冒充精确统计。",
    "wb.table.unique.approx": "≈ {0}（近似）",
    "wb.table.col.type": "类型",
    "wb.table.col.missing": "缺失",
    "wb.table.col.unique": "唯一值",
    "wb.table.col.min": "最小",
    "wb.table.col.max": "最大",
    "wb.table.col.mean": "均值",
    "wb.table.profile.needVersion": "profile/export 需要 version_id，不会改用 latest。",
    "wb.table.profile.error": "无法加载列统计",
    "wb.table.parquet.on": "可查看 Parquet",
    "wb.table.filteredRows": "筛选后 {0} 行",
    "wb.table.hist.bins": "{0} 箱",
    "wb.table.hist.empty": "无分布",
  },
  en: {
    "wb.table.schema": "Schema",
    "wb.table.distribution": "Distribution",
    "wb.table.export": "Export current filter",
    "wb.table.export.csv": "Download CSV",
    "wb.table.export.note": "Server-streamed. This page does not load the full file into the browser.",
    "wb.table.approximate": "Approximate",
    "wb.table.approximate.hint": "Unique counts may be lower bounds; category histograms show at most 50 bins. Not exact.",
    "wb.table.unique.approx": "≈ {0} (approx.)",
    "wb.table.col.type": "Type",
    "wb.table.col.missing": "Missing",
    "wb.table.col.unique": "Unique",
    "wb.table.col.min": "Min",
    "wb.table.col.max": "Max",
    "wb.table.col.mean": "Mean",
    "wb.table.profile.needVersion": "profile/export require version_id; latest is not substituted.",
    "wb.table.profile.error": "Could not load column statistics",
    "wb.table.parquet.on": "Parquet viewer available",
    "wb.table.filteredRows": "{0} filtered rows",
    "wb.table.hist.bins": "{0} bins",
    "wb.table.hist.empty": "No distribution",
  },
};

export function tableT(key: string, ...args: unknown[]): string {
  const fromDict = tOptional(key);
  let s = fromDict != null ? fromDict : COPY[LANG]?.[key] || COPY.en[key] || key;
  if (args.length) {
    s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
      args[+i] != null ? String(args[+i]) : m,
    );
  }
  return s;
}
