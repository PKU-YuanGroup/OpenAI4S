import { LANG, tOptional } from "../../i18n/runtime";

/**
 * M-03 copy. Existing Files/viewer keys stay in the F-07 dictionaries.
 * New search/filter/pagination/deep-link strings live here so we do not
 * rewrite the generated `i18n/en.ts` / `zh.ts` extract.
 */
const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    "viewer.text.truncated": "预览已截断。可展开已读取的完整原文，或下载文件。",
    "viewer.text.expand": "展开完整原文",
    "viewer.text.complete": "已展示完整原文。",
    "nb.artifact.saving": "正在保存输出…",
    "nb.artifact.unconfirmed": "历史版本无法确认",
    "nb.artifact.failed": "已确认版本读取失败",
    "nb.artifact.retry": "重试读取",
    "nb.artifact.latest": "打开最新版本",
    "nb.artifact.download": "下载此版本",
    "nb.artifact.open": "查看此版本",
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
    "files.index.unavailable": "无法加载文件索引。",
    "artifact.editPinned": "当前标签页显示的是固定版本；请打开最新版本后再编辑",
    "prov.env.noSnapshot": "此版本未记录生产时环境（上传文件，或早于环境捕获功能生成）；不会用守护进程的实时环境替代。",
  },
  en: {
    "viewer.text.truncated": "Preview truncated. Expand the complete text already loaded, or download the file.",
    "viewer.text.expand": "Show complete text",
    "viewer.text.complete": "Showing complete text.",
    "nb.artifact.saving": "Saving output…",
    "nb.artifact.unconfirmed": "Historical version could not be confirmed",
    "nb.artifact.failed": "Could not read the confirmed version",
    "nb.artifact.retry": "Retry reading",
    "nb.artifact.latest": "Open latest version",
    "nb.artifact.download": "Download this version",
    "nb.artifact.open": "Open this version",
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
    "files.index.unavailable": "Could not load the file index.",
    "artifact.editPinned": "This tab shows a fixed version; open the latest version to edit the file",
    "prov.env.noSnapshot": "No environment was recorded for this version (uploaded file, or produced before environment capture existed); the live daemon environment is not substituted.",
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
