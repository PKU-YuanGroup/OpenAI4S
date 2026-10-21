/**
 * Backup web-search engines (`GET/PUT /search/config`): parsing the
 * projection, and the two rules the list enforces before the server does —
 * the order is a permutation, and at least one engine stays usable.
 */

import { copyLookup, type CopyTable } from "../../i18n/copy";

/** Feature-local copy: `i18n/en.ts` / `zh.ts` are generated extracts of app.js. */
const COPY: CopyTable = {
  zh: {
    "search.title": "搜索引擎顺序",
    "search.desc":
      "豆包已配置时总是最先使用；否则、或豆包配额用尽且你同意回退时，按下面的顺序依次尝试已开启的引擎。",
    "search.doubao": "豆包搜索（Agent Plan）",
    "search.doubao.on": "已配置 · 总是最先使用",
    "search.doubao.off": "未配置 · 在上方保存 Agent Plan Key 后最先使用",
    "search.engine.tavily": "Tavily",
    "search.engine.you": "You.com",
    "search.engine.builtin": "内置免 key 引擎",
    "search.desc.tavily.set": "已保存 API Key。",
    "search.desc.tavily.unset": "保存 API Key 后才会使用。",
    "search.desc.you":
      "免费额度每个 IP 每天 100 次；保存 API Key 后改用付费接口。查询会发送给 You.com，请求会表明来自 OpenAI4S。",
    "search.desc.you.keyed": "已保存 API Key，使用付费接口。查询会发送给 You.com，请求会表明来自 OpenAI4S。",
    "search.desc.builtin": "DuckDuckGo / Bing / Mojeek，无需 key，可能被限流。",
    "search.keyPh.tavily": "输入 Tavily API Key",
    "search.keyPh.you": "输入 You.com API Key（可选）",
    "search.saveKey": "保存",
    "search.keySaved": "API Key 已保存",
    "search.up": "上移",
    "search.down": "下移",
    "search.drag": "拖动调整顺序",
    "search.lastUsable": "至少要保留一个可用的搜索引擎",
    "search.saved": "搜索引擎顺序已保存",
    "search.setup.title": "网页搜索",
    "search.setup.desc": "选一种主要的搜索方式；之后可以在「设置 → 网络」里调整顺序。可跳过。",
    "search.setup.doubao": "豆包搜索（Agent Plan Key）",
    "search.setup.tavily": "Tavily（需要 API Key）",
    "search.setup.you": "You.com（免费，API Key 可选）",
    "search.setup.builtin": "只用内置免 key 引擎",
    "search.setup.keyPh.doubao": "输入 Agent Plan Key",
    "search.setup.current": "当前顺序：{0}",
    "search.setup.apply": "应用",
    "search.setup.applied": "搜索方式已更新",
    "search.setup.needKey": "这个选项需要填写 key",
  },
  en: {
    "search.title": "Search engine order",
    "search.desc":
      "Doubao always answers first when configured. Otherwise — or when its quota is spent and you agree to fall back — the switched-on engines below are tried in this order.",
    "search.doubao": "Doubao Search (Agent Plan)",
    "search.doubao.on": "Configured · always tried first",
    "search.doubao.off": "Not configured · tried first once an Agent Plan Key is saved above",
    "search.engine.tavily": "Tavily",
    "search.engine.you": "You.com",
    "search.engine.builtin": "Built-in keyless engines",
    "search.desc.tavily.set": "API key saved.",
    "search.desc.tavily.unset": "Used only once an API key is saved.",
    "search.desc.you":
      "Free tier: 100 searches a day per IP; save an API key for the paid endpoint. Queries go to You.com, and requests identify OpenAI4S.",
    "search.desc.you.keyed":
      "API key saved; uses the paid endpoint. Queries go to You.com, and requests identify OpenAI4S.",
    "search.desc.builtin": "DuckDuckGo / Bing / Mojeek. No key needed; may be rate-limited.",
    "search.keyPh.tavily": "Enter Tavily API key",
    "search.keyPh.you": "Enter You.com API key (optional)",
    "search.saveKey": "Save",
    "search.keySaved": "API key saved",
    "search.up": "Move up",
    "search.down": "Move down",
    "search.drag": "Drag to reorder",
    "search.lastUsable": "At least one usable search engine must stay on",
    "search.saved": "Search engine order saved",
    "search.setup.title": "Web search",
    "search.setup.desc":
      "Pick your main search option; the order can be changed later in Settings → Network. Optional.",
    "search.setup.doubao": "Doubao Search (Agent Plan Key)",
    "search.setup.tavily": "Tavily (API key required)",
    "search.setup.you": "You.com (free; API key optional)",
    "search.setup.builtin": "Built-in keyless engines only",
    "search.setup.keyPh.doubao": "Enter Agent Plan Key",
    "search.setup.current": "Current order: {0}",
    "search.setup.apply": "Apply",
    "search.setup.applied": "Search option updated",
    "search.setup.needKey": "This option needs a key",
  },
};

export const searchT = copyLookup(COPY);

export const SEARCH_ENGINE_IDS = ["tavily", "you", "builtin"] as const;
export type SearchEngineId = (typeof SEARCH_ENGINE_IDS)[number];

export type SearchEngine = {
  id: SearchEngineId;
  enabled: boolean;
  needsKey: boolean;
  acceptsKey: boolean;
  keyConfigured: boolean;
  usable: boolean;
};

export type SearchConfig = {
  doubaoConfigured: boolean;
  engines: SearchEngine[];
};

function isEngineId(value: unknown): value is SearchEngineId {
  return (SEARCH_ENGINE_IDS as readonly unknown[]).includes(value);
}

/** Parse the route's projection; unknown or repeated engines are dropped. */
export function readSearchConfig(raw: unknown): SearchConfig {
  const row = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const seen = new Set<string>();
  const engines: SearchEngine[] = [];
  for (const item of Array.isArray(row.engines) ? row.engines : []) {
    const e = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    if (!isEngineId(e.id) || seen.has(e.id)) continue;
    seen.add(e.id);
    engines.push({
      id: e.id,
      enabled: e.enabled === true,
      needsKey: e.needs_key === true,
      acceptsKey: e.accepts_key === true,
      keyConfigured: e.api_key_configured === true,
      usable: e.usable === true,
    });
  }
  return { doubaoConfigured: row.doubao_configured === true, engines };
}

/** Whether ``id`` may be switched off: not if it is the last usable engine. */
export function canSwitchOff(config: SearchConfig, id: SearchEngineId): boolean {
  return config.engines.some((e) => e.id !== id && e.usable);
}

/** The order with ``id`` moved by ``delta`` places (clamped to the ends). */
export function moved(order: readonly SearchEngineId[], id: SearchEngineId, delta: number): SearchEngineId[] {
  const next = [...order];
  const from = next.indexOf(id);
  if (from < 0) return next;
  const to = Math.max(0, Math.min(next.length - 1, from + delta));
  next.splice(from, 1);
  next.splice(to, 0, id);
  return next;
}

/** The PUT body for a new order and/or switch, from the current projection. */
export function engineConfigBody(
  config: SearchConfig,
  change: { order?: SearchEngineId[]; toggle?: SearchEngineId },
): { order: SearchEngineId[]; enabled: Record<SearchEngineId, boolean> } {
  const order = change.order || config.engines.map((e) => e.id);
  const enabled = {} as Record<SearchEngineId, boolean>;
  for (const e of config.engines) enabled[e.id] = e.id === change.toggle ? !e.enabled : e.enabled;
  return { order, enabled };
}

export function engineLabel(id: SearchEngineId): string {
  return searchT("search.engine." + id);
}

export function engineDescription(e: SearchEngine): string {
  if (e.id === "tavily") {
    return searchT(e.keyConfigured ? "search.desc.tavily.set" : "search.desc.tavily.unset");
  }
  if (e.id === "you") return searchT(e.keyConfigured ? "search.desc.you.keyed" : "search.desc.you");
  return searchT("search.desc.builtin");
}
