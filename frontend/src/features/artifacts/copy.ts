import { LANG, tOptional } from "../../i18n/runtime";

/**
 * M-03 copy. Existing Files/viewer keys stay in the F-07 dictionaries.
 * New search/filter/pagination/deep-link strings live here so we do not
 * rewrite the generated `i18n/en.ts` / `zh.ts` extract.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "files.search.ph": "按文件名搜索",
    "files.filter.type": "类型",
    "files.filter.type.ph": "content type",
    "files.filter.origin": "来源",
    "files.filter.origin.all": "全部",
    "files.filter.origin.uploaded": "上传",
    "files.filter.origin.generated": "生成",
    "files.loadMore": "加载更多",
    "files.deeplink.copy": "复制深链",
    "files.deeplink.copied": "已复制",
    "files.version.stale": "找不到 version {0}（当前 latest 为 {1}）。不会改用 latest。",
    "files.version.notFound": "找不到该 Artifact 或指定 version。",
    "files.index.fallback": "索引路由不可用，已回退为本地过滤。",
  },
  en: {
    "files.search.ph": "Search by filename",
    "files.filter.type": "Type",
    "files.filter.type.ph": "content type",
    "files.filter.origin": "Source",
    "files.filter.origin.all": "All",
    "files.filter.origin.uploaded": "Uploaded",
    "files.filter.origin.generated": "Generated",
    "files.loadMore": "Load more",
    "files.deeplink.copy": "Copy deep link",
    "files.deeplink.copied": "Copied",
    "files.version.stale": "Version {0} was not found (latest is {1}). Latest was not substituted.",
    "files.version.notFound": "This artifact or version was not found.",
    "files.index.fallback": "Index route unavailable; using local filtering.",
  },
};

export function filesT(key: string, ...args: unknown[]): string {
  const fromDict = tOptional(key);
  let s = fromDict != null ? fromDict : COPY[LANG]?.[key] || COPY.en[key] || key;
  if (args.length) {
    s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
      args[+i] != null ? String(args[+i]) : m,
    );
  }
  return s;
}
