import { useEffect, useRef, useState } from "preact/hooks";
import { LANG } from "../../i18n/runtime";
import { api, apiErrorText } from "../timeline/api";
import type { MarketEntry, MarketPage } from "./types";

const PAGE = 24;

const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    title: "设备市场",
    lead: "OpenMHS 市场里每个设备都已在 fake 后端通过契约检查：点「安装」即可加入实验室并启动，不需要真实仪器。",
    search: "搜索设备，例如 显微镜、pump、UR5、mujoco…",
    all: "全部",
    install: "安装",
    installing: "安装中…",
    installed: "已安装",
    via: "接入方式",
    viaMcp: "MCP 连接器",
    viaMhs: "MHS 原生",
    more: "加载更多",
    none: "没有匹配的设备。",
    of: "共",
    upstream: "上游",
    hardware: "硬件驱动 · fake 后端",
    simulated: "仿真",
    website: "在网站上浏览全部设备",
  },
  en: {
    title: "Device market",
    lead: "Every OpenMHS market device passed its contract check on the fake backend: Install adds it to the lab and starts it. No instrument needed.",
    search: "Search devices, e.g. microscope, pump, UR5, mujoco…",
    all: "All",
    install: "Install",
    installing: "Installing…",
    installed: "Installed",
    via: "Access",
    viaMcp: "MCP connector",
    viaMhs: "MHS native",
    more: "Load more",
    none: "No device matches.",
    of: "of",
    upstream: "upstream",
    hardware: "hardware driver · fake backend",
    simulated: "simulation",
    website: "Browse every device on the website",
  },
};

function tr(key: string): string {
  const table = COPY[LANG] || COPY.en;
  return table[key] || COPY.en[key] || key;
}

const CATEGORY_ZH: Record<string, string> = {
  Simulation: "仿真环境",
  "Electronics and measurement": "电子与测量",
  "Sensors and cameras": "传感器与相机",
  "Motion control": "运动控制",
  "Robot arms and grippers": "机械臂与夹爪",
  "Liquid handling and fluidics": "液体处理与流体",
  "Optics and microscopy": "光学与显微",
  "Mobile robots": "移动机器人",
  "Thermal and process control": "温控与过程控制",
  Spectroscopy: "光谱",
  "Other devices": "其他设备",
};

/** The market category in the interface language (the market data is English). */
export function categoryLabel(name: string): string {
  return LANG === "zh" ? CATEGORY_ZH[name] || name : name;
}

/** Strip the backticks of the market's introductions for plain display. */
export function plainIntro(text: string): string {
  return text.replace(/`/g, "");
}

function MarketCard({
  entry,
  busy,
  onInstall,
}: {
  entry: MarketEntry;
  busy: boolean;
  onInstall: (entry: MarketEntry, via: "mcp" | "mhs") => void;
}) {
  const [via, setVia] = useState<"mcp" | "mhs">("mcp");
  return (
    <article class="lab-market-card">
      <header>
        <h5 title={entry.id}>{entry.name}</h5>
        <div class="lab-meta">
          <span class="lab-tag">{categoryLabel(entry.category)}</span>
          {entry.topic ? <span class="lab-tag">{entry.topic}</span> : null}
          <span class="lab-tag">{entry.mode === "simulated" ? tr("simulated") : tr("hardware")}</span>
          <span class="lab-tag">{entry.risk}</span>
        </div>
      </header>
      <p class="lab-market-intro">{plainIntro(entry.intro)}</p>
      <div class="lab-market-foot">
        <a class="lab-market-src" href={entry.upstream.repo_url} target="_blank" rel="noopener noreferrer">
          {tr("upstream")} · {entry.upstream.license || "?"}
        </a>
        {entry.installed ? (
          <span class="lab-chip ok">
            ✓ {tr("installed")} · {entry.installed.via === "mhs" ? tr("viaMhs") : tr("viaMcp")}
          </span>
        ) : (
          <span class="lab-market-install">
            <select
              aria-label={tr("via")}
              value={via}
              disabled={busy}
              onChange={(e) => setVia((e.currentTarget as HTMLSelectElement).value === "mhs" ? "mhs" : "mcp")}
            >
              <option value="mcp">{tr("viaMcp")}</option>
              <option value="mhs">{tr("viaMhs")}</option>
            </select>
            <button class="lab-btn primary" type="button" disabled={busy} onClick={() => onInstall(entry, via)}>
              {busy ? tr("installing") : tr("install")}
            </button>
          </span>
        )}
      </div>
      <code class="lab-market-cmd" title="CLI">
        {entry.install.openai4s}
      </code>
    </article>
  );
}

/** Search the OpenMHS market and install a device with one click. */
export function MarketPanel({ onChanged }: { onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [page, setPage] = useState<MarketPage | null>(null);
  const [items, setItems] = useState<MarketEntry[]>([]);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const seq = useRef(0);

  const load = async (offset: number) => {
    const mine = ++seq.current;
    const params = new URLSearchParams({ q: query, limit: String(PAGE), offset: String(offset) });
    if (category) params.set("category", category);
    try {
      const res = (await api(`/mhs-lab/market?${params.toString()}`)) as MarketPage;
      if (mine !== seq.current) return;
      setPage(res);
      setItems((prev) => (offset ? [...prev, ...res.packages] : res.packages));
      setError("");
    } catch (e) {
      if (mine === seq.current) setError(apiErrorText(e));
    }
  };

  useEffect(() => {
    const timer = window.setTimeout(() => void load(0), 220);
    return () => window.clearTimeout(timer);
  }, [query, category]);

  const install = async (entry: MarketEntry, via: "mcp" | "mhs") => {
    setBusyId(entry.id);
    try {
      const res = (await api("/mhs-lab/devices", {
        method: "POST",
        body: JSON.stringify({ package: entry.id, via }),
      })) as { device: { id: string; via: "mcp" | "mhs" } };
      setItems((prev) =>
        prev.map((it) => (it.id === entry.id ? { ...it, installed: { id: res.device.id, via: res.device.via } } : it)),
      );
      onChanged();
    } catch (e) {
      setError(apiErrorText(e));
    } finally {
      setBusyId("");
    }
  };

  const categories = page ? Object.entries(page.categories) : [];
  return (
    <section class="lab-market" aria-label={tr("title")}>
      <p class="lab-market-lead">{tr("lead")}</p>
      <input
        class="lab-market-search"
        type="search"
        value={query}
        placeholder={tr("search")}
        aria-label={tr("search")}
        onInput={(e) => setQuery((e.currentTarget as HTMLInputElement).value)}
      />
      <div class="lab-market-cats" role="group">
        <button type="button" class={`lab-pill ${category ? "" : "on"}`} onClick={() => setCategory("")}>
          {tr("all")} {page ? `· ${page.count}` : ""}
        </button>
        {categories.map(([name, n]) => (
          <button
            key={name}
            type="button"
            class={`lab-pill ${category === name ? "on" : ""}`}
            onClick={() => setCategory(category === name ? "" : name)}
          >
            {categoryLabel(name)} · {n}
          </button>
        ))}
      </div>
      {error ? <p class="lab-error">{error}</p> : null}
      {page ? (
        <p class="lab-market-count">
          {items.length} {tr("of")} {page.total}
          {page.website ? (
            <>
              {" · "}
              <a href={`${page.website.replace(/\/$/, "")}/market/`} target="_blank" rel="noopener noreferrer">
                {tr("website")}
              </a>
            </>
          ) : null}
        </p>
      ) : null}
      <div class="lab-market-grid">
        {items.map((entry) => (
          <MarketCard key={entry.id} entry={entry} busy={busyId === entry.id} onInstall={install} />
        ))}
      </div>
      {page && !items.length ? <p class="lab-empty">{tr("none")}</p> : null}
      {page && items.length < page.total ? (
        <button class="lab-btn" type="button" onClick={() => void load(items.length)}>
          {tr("more")}
        </button>
      ) : null}
    </section>
  );
}
