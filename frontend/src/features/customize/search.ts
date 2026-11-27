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
      "已配置的 Ollama 网页工具优先使用。未配置 Ollama 时先使用豆包；豆包未配置、或配额用尽且你同意回退时，按下面的顺序尝试已开启的引擎。",
    "search.doubao": "豆包搜索",
    "search.doubao.on": "已配置 · 未配置 Ollama 网页工具时使用",
    "search.doubao.off": "未配置 · 保存 Agent Plan Key 后，在未配置 Ollama 网页工具时使用",
    "search.engine.tavily": "Tavily",
    "search.engine.you": "You.com",
    "search.engine.parallel": "Parallel",
    "search.engine.specstream": "SpecStream",
    "search.engine.builtin": "内置免 key 引擎",
    "search.desc.tavily.set": "已保存 API Key。",
    "search.desc.tavily.unset": "保存 API Key 后才会使用。",
    "search.desc.you":
      "免费额度每个 IP 每天 100 次；保存 API Key 后改用付费接口。查询会发送给 You.com，请求会表明来自 OpenAI4S。",
    "search.desc.you.keyed": "已保存 API Key，使用付费接口。查询会发送给 You.com，请求会表明来自 OpenAI4S。",
    "search.desc.parallel": "无需 key。查询会发送给 Parallel，请求会表明来自 OpenAI4S。匿名搜索默认使用 Fast 模式。",
    "search.desc.specstream.set": "已保存 API Key。查询会发送给 SpecStream。",
    "search.desc.specstream.unset": "保存 API Key 后才会使用。查询会发送给 SpecStream。",
    "search.desc.builtin": "DuckDuckGo / Bing / Mojeek，无需 key，可能被限流。",
    "search.keyPh.tavily": "输入 Tavily API Key",
    "search.keyPh.you": "输入 You.com API Key（可选）",
    "search.keyPh.specstream": "输入 SpecStream API Key",
    "search.saveKey": "保存",
    "search.keySaved": "API Key 已保存",
    "search.removeKey": "移除 Key",
    "search.keyRemoved": "API Key 已移除",
    "search.keyFromEnv": "这个 Key 来自服务器的环境变量，在这里移除不了，需要在服务器上修改。",
    "search.lastKey": "移除后就没有可用的搜索引擎了；请先开启另一个引擎",
    "search.up": "上移",
    "search.down": "下移",
    "search.drag": "拖动调整顺序",
    "search.lastUsable": "至少要保留一个可用的搜索引擎",
    "search.saved": "搜索引擎顺序已保存",
    "search.setup.title": "网页搜索",
    "search.setup.desc": "选择智能体联网搜索的方式，之后可在「设置 → 网络」里修改。可跳过。",
    "search.setup.doubao": "豆包搜索",
    "search.setup.doubao.sub": "需要 Agent Plan Key",
    "search.setup.tavily": "Tavily（需要 API Key）",
    "search.setup.you": "You.com（免费，API Key 可选）",
    "search.setup.builtin": "只用内置免 key 引擎",
    "search.setup.parallel": "Parallel（免 key，查询会发送给 Parallel）",
    "search.setup.keyPh.doubao": "输入 Agent Plan Key",
    "search.setup.current": "当前顺序：{0}",
    "search.setup.apply": "应用",
    "search.setup.applied": "搜索方式已更新",
    "search.setup.needKey": "这个选项需要填写 Key",
  },
  en: {
    "search.title": "Search engine order",
    "search.desc":
      "Configured Ollama web tools take priority. Without Ollama, Doubao is used first; when Doubao is unconfigured or its quota is spent and you approve fallback, the enabled engines below are tried in this order.",
    "search.doubao": "Doubao Search",
    "search.doubao.on": "Configured · used when Ollama web tools are unconfigured",
    "search.doubao.off": "Not configured · save an Agent Plan Key to use it when Ollama web tools are unconfigured",
    "search.engine.tavily": "Tavily",
    "search.engine.you": "You.com",
    "search.engine.parallel": "Parallel",
    "search.engine.specstream": "SpecStream",
    "search.engine.builtin": "Built-in keyless engines",
    "search.desc.tavily.set": "API key saved.",
    "search.desc.tavily.unset": "Used only once an API key is saved.",
    "search.desc.you":
      "Free tier: 100 searches a day per IP; save an API key for the paid endpoint. Queries go to You.com, and requests identify OpenAI4S.",
    "search.desc.you.keyed":
      "API key saved; uses the paid endpoint. Queries go to You.com, and requests identify OpenAI4S.",
    "search.desc.parallel": "No key needed. Queries go to Parallel, and requests identify OpenAI4S. Anonymous searches default to Fast mode.",
    "search.desc.specstream.set": "API key saved. Queries go to SpecStream.",
    "search.desc.specstream.unset": "Used only once an API key is saved. Queries go to SpecStream.",
    "search.desc.builtin": "DuckDuckGo / Bing / Mojeek. No key needed; may be rate-limited.",
    "search.keyPh.tavily": "Enter Tavily API key",
    "search.keyPh.you": "Enter You.com API key (optional)",
    "search.keyPh.specstream": "Enter SpecStream API key",
    "search.saveKey": "Save",
    "search.keySaved": "API key saved",
    "search.removeKey": "Remove key",
    "search.keyRemoved": "API key removed",
    "search.keyFromEnv":
      "This key comes from the server's environment, so it cannot be removed here; change it on the server.",
    "search.lastKey": "Removing it would leave no usable search engine; switch another engine on first",
    "search.up": "Move up",
    "search.down": "Move down",
    "search.drag": "Drag to reorder",
    "search.lastUsable": "At least one usable search engine must stay on",
    "search.saved": "Search engine order saved",
    "search.setup.title": "Web search",
    "search.setup.desc":
      "Choose how the agent searches the web; you can change it later in Settings → Network. Optional.",
    "search.setup.doubao": "Doubao Search",
    "search.setup.doubao.sub": "Needs an Agent Plan Key",
    "search.setup.tavily": "Tavily (API key required)",
    "search.setup.you": "You.com (free; API key optional)",
    "search.setup.builtin": "Built-in keyless engines only",
    "search.setup.parallel": "Parallel (keyless; queries go to Parallel)",
    "search.setup.keyPh.doubao": "Enter Agent Plan Key",
    "search.setup.current": "Current order: {0}",
    "search.setup.apply": "Apply",
    "search.setup.applied": "Search option updated",
    "search.setup.needKey": "This option needs a key",
  },
};

export const searchT = copyLookup(COPY);

export const SEARCH_ENGINE_IDS = ["tavily", "you", "builtin", "parallel", "specstream"] as const;
export type SearchEngineId = (typeof SEARCH_ENGINE_IDS)[number];

export type SearchEngine = {
  id: SearchEngineId;
  enabled: boolean;
  needsKey: boolean;
  acceptsKey: boolean;
  keyConfigured: boolean;
  /** A clear would leave this key; it comes from the server's environment. */
  keyFromEnv: boolean;
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
      keyFromEnv: e.api_key_from_env === true,
      usable: e.usable === true,
    });
  }
  return { doubaoConfigured: row.doubao_configured === true, engines };
}

/** Whether ``id`` may be switched off: not if it is the last usable engine. */
export function canSwitchOff(config: SearchConfig, id: SearchEngineId): boolean {
  return config.engines.some((e) => e.id !== id && e.usable);
}

/**
 * Whether ``id``'s saved key may be removed. The server holds a removal to the
 * rule switching off obeys: not while the engine needs its key and nothing
 * else is usable. A key the server's environment still holds survives the
 * clear, so that removal is allowed — it changes nothing, and the notice says
 * the key has to be changed on the server.
 */
export function canRemoveKey(config: SearchConfig, id: SearchEngineId): boolean {
  const engine = config.engines.find((e) => e.id === id);
  if (!engine?.keyConfigured) return false;
  if (engine.keyFromEnv) return true;
  return !(engine.needsKey && engine.usable) || canSwitchOff(config, id);
}

/**
 * What to say once a removal is saved. A key set in the server's environment
 * outlives it, and the server then still reports the key configured.
 */
export function keyRemovalNote(config: SearchConfig, id: SearchEngineId): { text: string; warn: boolean } {
  return config.engines.some((e) => e.id === id && e.keyConfigured)
    ? { text: searchT("search.keyFromEnv"), warn: true }
    : { text: searchT("search.keyRemoved"), warn: false };
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
  if (e.id === "parallel") return searchT("search.desc.parallel");
  if (e.id === "specstream") {
    return searchT(e.keyConfigured ? "search.desc.specstream.set" : "search.desc.specstream.unset");
  }
  if (e.id === "you") return searchT(e.keyConfigured ? "search.desc.you.keyed" : "search.desc.you");
  return searchT("search.desc.builtin");
}
