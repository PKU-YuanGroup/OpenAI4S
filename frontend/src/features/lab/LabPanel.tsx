import { useEffect, useRef, useState } from "preact/hooks";
import { LANG, languageRevision } from "../../i18n/runtime";
import { api, apiErrorText } from "../timeline/api";
import { categoryLabel, MarketPanel } from "./MarketPanel";
import type {
  ActivityItem,
  ArmPoint,
  DoseCurve,
  LabEndpoint,
  LabResults,
  LabStatus,
  LabVerify,
  ReactorRun,
} from "./types";

const POLL_MS = 2000;

const COPY: Record<"zh" | "en", Record<string, string>> = {
  zh: {
    title: "OpenMHS 实验室",
    sub: "OpenAI4S 通过 OpenMHS 标准接入的模拟实验设备：内置 3 台演示设备，另可从市场一键安装 746 台。端点强制安全，每条回执哈希链存证。",
    setupTitle: "先准备 OpenMHS",
    setupBody: "实验室需要 OpenMHS（SDK、参考主机和市场里的设备驱动）。点下面的按钮会下载固定版本（约 35 MB）到数据目录，之后一切都在本机运行。",
    setupBtn: "一键准备 OpenMHS",
    settingUp: "正在下载 OpenMHS…",
    manual: "也可以在终端运行：",
    start: "启动实验室",
    stop: "停止",
    starting: "启动中…",
    market: "设备市场",
    closeMarket: "收起市场",
    groupMhs: "MHS · 原生工具",
    groupMhsHint: "智能体直接调用 mhs_read / mhs_command，OpenAI4S 进程内的 MHSHost 走 HTTP JSON-RPC 访问端点，不经过 MCP。",
    groupMcp: "MCP · 连接器",
    groupMcpHint: "智能体用 call_mcp_tool 调用连接器 mhs-<设备>，每次调用都经过受治理的主机路由（日志、账本、证据）。",
    emptyGroup: "这一组还没有设备：在设备市场安装时选择该接入方式即可。",
    remove: "卸载",
    confirmRemove: "从实验室卸载这台设备？它的连接器和授权规则会一并删除，已有回执保留。",
    builtIn: "内置",
    offline: "离线",
    onDemand: "按需启动",
    stoppedHint: "实验室已停止。点「启动实验室」后智能体才能使用这些设备。",
    simulated: "模拟",
    online: "在线",
    verified: "证据链已验证",
    unverified: "证据链未通过",
    estopAll: "全部急停",
    estop: "急停",
    reset: "操作员复位",
    latched: "已锁存",
    ready: "就绪",
    devices: "设备与接入",
    results: "实验结果",
    activity: "实时回执",
    evidence: "证据与审计",
    lims: "样品登记 (LIMS)",
    noActivity: "还没有回执。让智能体运行一个实验后，这里会实时出现每条 MHS 回执。",
    doseTitle: "剂量-反应曲线（% 活性 vs 浓度）",
    reactorTitle: "流动反应条件优化（收率 vs 实验序号）",
    armTitle: "机械臂俯视轨迹（mm）",
    bestSoFar: "历史最优",
    experiments: "实验",
    receipts: "回执",
    completed: "完成",
    rejected: "拒绝",
    failed: "失败",
    chain: "端点证据链",
    hostLedger: "主机账本（治理路由）",
    records: "条",
    confirmReset: "确认现场安全后复位该设备的急停锁存？复位只释放锁存，不会自动恢复运动。",
    confirmEstop: "立即急停全部设备？",
    access: "接入",
    wire: "线路",
    provenance: "来源",
    ic50: "IC50≈",
    vial: "样品瓶",
    location: "位置",
    product: "产物",
    purity: "纯度",
    plate: "板",
    tips: "吸头",
    temp: "温度",
    pressure: "压力",
    flow: "流速",
    pose: "末端位置",
    moves: "次移动",
    refusedBy: "条被端点拒绝",
  },
  en: {
    title: "OpenMHS Lab",
    sub: "Simulated instruments reached through the OpenMHS standard: three built-in demo devices, plus 746 more from the market in one click. The endpoint enforces safety; every receipt is hash-chained.",
    setupTitle: "Set up OpenMHS first",
    setupBody: "The lab needs OpenMHS (the SDK, the reference host and the market's driver packages). The button downloads the pinned release (about 35 MB) into your data directory; everything then runs on this machine.",
    setupBtn: "Set up OpenMHS",
    settingUp: "Downloading OpenMHS…",
    manual: "Or in a terminal:",
    start: "Start lab",
    stop: "Stop",
    starting: "Starting…",
    market: "Device market",
    closeMarket: "Close market",
    groupMhs: "MHS · native tools",
    groupMhsHint: "The agent calls mhs_read / mhs_command directly; the MHSHost inside OpenAI4S reaches the endpoint over HTTP JSON-RPC, with no MCP in between.",
    groupMcp: "MCP · connectors",
    groupMcpHint: "The agent calls the connector mhs-<device> with call_mcp_tool; every call goes through the governed host route (journal, ledger, evidence).",
    emptyGroup: "No device in this group yet: pick this access when you install from the market.",
    remove: "Remove",
    confirmRemove: "Remove this device from the lab? Its connector and allow rules are deleted; existing receipts are kept.",
    builtIn: "built-in",
    offline: "offline",
    onDemand: "on demand",
    stoppedHint: "The lab is stopped. Press Start lab before asking the agent to use these devices.",
    simulated: "SIMULATED",
    online: "online",
    verified: "evidence verified",
    unverified: "evidence failed",
    estopAll: "E-STOP ALL",
    estop: "E-stop",
    reset: "Operator reset",
    latched: "latched",
    ready: "ready",
    devices: "Devices & access",
    results: "Results",
    activity: "Live receipts",
    evidence: "Evidence & audit",
    lims: "Samples (LIMS)",
    noActivity: "No receipts yet. Ask the agent to run an experiment and every MHS receipt appears here live.",
    doseTitle: "Dose-response (% activity vs concentration)",
    reactorTitle: "Flow-reaction optimisation (yield vs experiment)",
    armTitle: "Robot arm path, top view (mm)",
    bestSoFar: "best so far",
    experiments: "experiments",
    receipts: "receipts",
    completed: "completed",
    rejected: "rejected",
    failed: "failed",
    chain: "Endpoint evidence chains",
    hostLedger: "Host ledger (governed route)",
    records: "records",
    confirmReset: "Release this device's e-stop latch after checking the bench is safe? A reset releases the latch only; it does not resume motion.",
    confirmEstop: "E-stop every device now?",
    access: "access",
    wire: "wire",
    provenance: "source",
    ic50: "IC50≈",
    vial: "Vial",
    location: "Location",
    product: "Product",
    purity: "Purity",
    plate: "Plate",
    tips: "tips",
    temp: "temp",
    pressure: "pressure",
    flow: "flow",
    pose: "TCP",
    moves: "moves",
    refusedBy: "refused by the endpoint",
  },
};

function tr(key: string): string {
  const table = COPY[LANG] || COPY.en;
  return table[key] || COPY.en[key] || key;
}

const ACCESS_LABEL: Record<string, { zh: string; en: string }> = {
  "mcp-http": { zh: "MCP 连接器 · HTTP", en: "MCP connector · HTTP" },
  "native-http": { zh: "原生工具 · HTTP", en: "Native tools · HTTP" },
  "mcp-stdio": { zh: "MCP 连接器 · stdio", en: "MCP connector · stdio" },
};

function accessLabel(access: string): string {
  const entry = ACCESS_LABEL[access];
  return entry ? entry[LANG] || entry.en : access;
}

const SERIES = ["var(--lab-s1)", "var(--lab-s2)", "var(--lab-s3)", "var(--lab-s4)", "var(--lab-s5)"];

function fmt(v: unknown, digits = 3): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return v == null ? "—" : String(v);
  return Number(n.toPrecision(digits)).toString();
}

function shortTime(ts?: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function StatusChip({ status, code }: { status?: string | null; code?: string | null }) {
  const ok = status === "completed";
  const cls = ok ? "lab-chip ok" : status === "rejected" || status === "failed" ? "lab-chip bad" : "lab-chip";
  const glyph = ok ? "✓" : status === "rejected" ? "⛔" : status === "failed" ? "✕" : "•";
  return (
    <span class={cls} title={code || status || ""}>
      {glyph} {status ? tr(status) : "—"}
    </span>
  );
}

// ---------------------------------------------------------------- charts

/** The curves of the most recent read (the trailing group sharing its read and plate). */
export function latestRead(all: DoseCurve[]): DoseCurve[] {
  const last = all[all.length - 1];
  if (!last) return [];
  const out: DoseCurve[] = [];
  for (let i = all.length - 1; i >= 0; i--) {
    const c = all[i]!;
    if (c.read_id !== last.read_id || (c.plate_id ?? null) !== (last.plate_id ?? null)) break;
    out.unshift(c);
  }
  return out;
}

function DoseChart({ curves: source }: { curves: DoseCurve[] }) {
  const curves = latestRead(source);
  if (!curves.length) return null;
  const W = 460;
  const H = 230;
  const L = 44;
  const R = 12;
  const T = 12;
  const B = 34;
  const all = curves.flatMap((c) => c.concentrations_uM.filter((x) => x > 0));
  const lo = Math.floor(Math.log10(Math.min(...all)));
  const hi = Math.ceil(Math.log10(Math.max(...all)));
  const x = (c: number) => L + ((Math.log10(c) - lo) / Math.max(1, hi - lo)) * (W - L - R);
  const y = (a: number) => T + (1 - (Math.max(-10, Math.min(120, a)) + 10) / 130) * (H - T - B);
  const compounds: string[] = [];
  curves.forEach((c) => {
    if (!compounds.includes(c.compound_id)) compounds.push(c.compound_id);
  });
  const color = (cid: string) => SERIES[compounds.indexOf(cid) % SERIES.length];
  const decades = [];
  for (let d = lo; d <= hi; d++) decades.push(d);
  return (
    <figure class="lab-figure">
      <figcaption>{tr("doseTitle")}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr("doseTitle")}>
        {[0, 50, 100].map((a) => (
          <g key={a}>
            <line class="lab-grid" x1={L} x2={W - R} y1={y(a)} y2={y(a)} />
            <text class="lab-axis" x={L - 6} y={y(a) + 4} text-anchor="end">
              {a}%
            </text>
          </g>
        ))}
        {decades.map((d) => (
          <text key={d} class="lab-axis" x={x(10 ** d)} y={H - B + 16} text-anchor="middle">
            {d >= 0 ? `${10 ** d}` : `1e${d}`}
          </text>
        ))}
        <text class="lab-axis" x={(L + W - R) / 2} y={H - 4} text-anchor="middle">
          µM (log)
        </text>
        {curves.map((c) => {
          const pts = c.concentrations_uM
            .map((cx, i) => [cx, c.activity_pct[i]] as [number, number | undefined])
            .filter((p): p is [number, number] => p[0] > 0 && typeof p[1] === "number")
            .sort((a, b) => a[0] - b[0]);
          const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join(" ");
          return (
            <g key={`${c.read_id}-${c.row}`}>
              <path d={d} fill="none" stroke={color(c.compound_id)} stroke-width="2" />
              {pts.map((p, i) => (
                <circle key={i} class="lab-dot" cx={x(p[0])} cy={y(p[1])} r="4" fill={color(c.compound_id)}>
                  <title>{`${c.compound_id} · ${fmt(p[0])} µM → ${fmt(p[1])} %`}</title>
                </circle>
              ))}
            </g>
          );
        })}
      </svg>
      <div class="lab-legend">
        {compounds.map((cid) => {
          const est = curves.filter((c) => c.compound_id === cid).map((c) => c.ic50_estimate_uM).find((v) => v);
          return (
            <span key={cid}>
              <i style={{ background: color(cid) }} />
              {cid}
              {est ? ` · ${tr("ic50")}${fmt(est)} µM` : ""}
            </span>
          );
        })}
      </div>
    </figure>
  );
}

function ReactorChart({ runs }: { runs: ReactorRun[] }) {
  if (!runs.length) return null;
  const W = 460;
  const H = 210;
  const L = 40;
  const R = 12;
  const T = 12;
  const B = 30;
  const n = runs.length;
  const ys = runs.map((r) => Number(r.yield_pct) || 0);
  const ymin = Math.max(0, Math.floor(Math.min(...ys) / 10) * 10 - 10);
  const x = (i: number) => L + (n <= 1 ? 0.5 : i / (n - 1)) * (W - L - R);
  const y = (v: number) => T + (1 - (v - ymin) / (100 - ymin)) * (H - T - B);
  const reagents: string[] = [];
  runs.forEach((r) => {
    const k = String(r.reagent_b);
    if (!reagents.includes(k)) reagents.push(k);
  });
  const color = (k: string) => SERIES[Math.max(0, ["B1", "B2", "B3"].indexOf(k)) % SERIES.length];
  let best = -1;
  const bestPath = ys
    .map((v, i) => {
      best = Math.max(best, v);
      return `${i ? "L" : "M"}${x(i).toFixed(1)},${y(best).toFixed(1)}`;
    })
    .join(" ");
  const ticks = [ymin, (ymin + 100) / 2, 100].map((v) => Math.round(v));
  return (
    <figure class="lab-figure">
      <figcaption>{tr("reactorTitle")}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr("reactorTitle")}>
        {ticks.map((v) => (
          <g key={v}>
            <line class="lab-grid" x1={L} x2={W - R} y1={y(v)} y2={y(v)} />
            <text class="lab-axis" x={L - 6} y={y(v) + 4} text-anchor="end">
              {v}%
            </text>
          </g>
        ))}
        <path d={bestPath} fill="none" class="lab-best" stroke-width="2" />
        {runs.map((r, i) => (
          <circle key={i} class="lab-dot" cx={x(i)} cy={y(ys[i] ?? 0)} r="4.5" fill={color(String(r.reagent_b))}>
            <title>{`${r.experiment_id} · ${r.reagent_b} · ${fmt(r.temperature_C)} °C · ${fmt(r.residence_time_min)} min · ${fmt(r.equiv_b)} eq → ${fmt(r.yield_pct, 4)} %`}</title>
          </circle>
        ))}
        <text class="lab-axis" x={(L + W - R) / 2} y={H - 6} text-anchor="middle">
          {tr("experiments")} 1…{n}
        </text>
      </svg>
      <div class="lab-legend">
        {reagents.map((k) => (
          <span key={k}>
            <i style={{ background: color(k) }} />
            {k}
          </span>
        ))}
        <span>
          <i class="lab-best-key" />
          {tr("bestSoFar")} {fmt(Math.max(...ys), 4)}%
        </span>
      </div>
    </figure>
  );
}

const POSES: Array<{ name: string; x: number; y: number }> = [
  { name: "home", x: 300, y: 0 },
  { name: "reactor outlet", x: 420, y: -180 },
  { name: "assay deck", x: 380, y: 220 },
];

function ArmChart({ path }: { path: ArmPoint[] }) {
  if (!path.length) return null;
  const W = 460;
  const H = 220;
  const pad = 26;
  const xs = [...path.filter((p) => p.status === "completed").map((p) => Number(p.x)), ...POSES.map((p) => p.x)];
  const yvals = [...path.filter((p) => p.status === "completed").map((p) => Number(p.y)), ...POSES.map((p) => p.y)];
  const minX = Math.min(...xs) - 60;
  const maxX = Math.max(...xs) + 60;
  const minY = Math.min(...yvals) - 60;
  const maxY = Math.max(...yvals) + 60;
  const sx = (v: number) => pad + ((v - minX) / (maxX - minX)) * (W - 2 * pad);
  const sy = (v: number) => H - pad - ((v - minY) / (maxY - minY)) * (H - 2 * pad);
  const done = path.filter((p) => p.status === "completed");
  const d = done.map((p, i) => `${i ? "L" : "M"}${sx(Number(p.x)).toFixed(1)},${sy(Number(p.y)).toFixed(1)}`).join(" ");
  return (
    <figure class="lab-figure">
      <figcaption>{tr("armTitle")}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={tr("armTitle")}>
        {POSES.map((p) => (
          <g key={p.name}>
            <rect class="lab-pose" x={sx(p.x) - 7} y={sy(p.y) - 7} width="14" height="14" rx="3" />
            <text class="lab-axis" x={sx(p.x) + 11} y={sy(p.y) + 4}>
              {p.name}
            </text>
          </g>
        ))}
        <path d={d} fill="none" stroke="var(--lab-s1)" stroke-width="2" />
        {done.map((p, i) => (
          <circle key={i} class="lab-dot" cx={sx(Number(p.x))} cy={sy(Number(p.y))} r="3.5" fill="var(--lab-s1)">
            <title>{`(${fmt(p.x, 4)}, ${fmt(p.y, 4)}, ${fmt(p.z, 4)}) mm`}</title>
          </circle>
        ))}
      </svg>
      <div class="lab-legend">
        <span>
          <i style={{ background: "var(--lab-s1)" }} />
          {done.length} {tr("moves")}
        </span>
        {path.some((p) => p.status !== "completed") ? (
          <span class="lab-refused">⛔ {path.filter((p) => p.status !== "completed").length} {tr("refusedBy")}</span>
        ) : null}
      </div>
    </figure>
  );
}

// ---------------------------------------------------------------- device cards

function PlateMap({ rows }: { rows: Record<string, { state: string; compound: string | null; points: number }> }) {
  return (
    <div class="lab-plate" aria-label="96-well plate">
      {Object.entries(rows).map(([row, info]) => (
        <div class="lab-plate-row" key={row} title={`${row}: ${info.state}${info.compound ? " · " + info.compound : ""}`}>
          <span class="lab-plate-label">{row}</span>
          {Array.from({ length: 12 }, (_, i) => {
            const col = i + 1;
            const filled = info.state !== "empty" && (col <= info.points || col >= 11);
            return <span key={col} class={`lab-well ${filled ? "s-" + info.state : ""}`} />;
          })}
          <span class="lab-plate-cpd">{info.compound || ""}</span>
        </div>
      ))}
    </div>
  );
}

function DeviceCard({
  ep,
  onAction,
  onRemove,
  busy,
}: {
  ep: LabEndpoint;
  onAction: (a: string, id: string) => void;
  onRemove: (id: string) => void;
  busy: boolean;
}) {
  const latched = Boolean(ep.latch && ep.latch.latched);
  const state = (ep.state || {}) as Record<string, unknown>;
  const plate = state.plate as { plate_id: string; rows: Record<string, { state: string; compound: string | null; points: number }> } | undefined;
  return (
    <article class={`lab-device ${latched ? "is-latched" : ""}`}>
      <header>
        <div>
          <h4>{ep.title}</h4>
          <div class="lab-meta">
            <span class="lab-access">{accessLabel(ep.access)}</span>
            <span class="lab-tag">{tr("simulated")}</span>
            {ep.builtin ? <span class="lab-tag">{tr("builtIn")}</span> : null}
            {!ep.builtin && ep.category ? <span class="lab-tag">{categoryLabel(ep.category)}</span> : null}
            {ep.connector ? <span class="lab-tag lab-mono">{ep.connector}</span> : null}
            {ep.running === false ? <span class="lab-tag off">{tr("offline")}</span> : null}
            {ep.running == null && !ep.url ? <span class="lab-tag">{tr("onDemand")}</span> : null}
          </div>
        </div>
        <div class="lab-device-actions">
          {latched ? (
            <button class="lab-btn warn" type="button" disabled={busy} onClick={() => onAction("reset", ep.id)}>
              {tr("reset")}
            </button>
          ) : null}
          <button class="lab-btn danger" type="button" disabled={busy} onClick={() => onAction("estop", ep.id)}>
            {tr("estop")}
          </button>
          {!ep.builtin ? (
            <button class="lab-btn" type="button" disabled={busy} onClick={() => onRemove(ep.id)}>
              {tr("remove")}
            </button>
          ) : null}
        </div>
      </header>
      <div class={`lab-latch ${latched ? "on" : ""}`}>
        {latched ? `⛔ ${tr("latched")} · ${ep.latch?.reason || ""}` : `✓ ${tr("ready")}`}
      </div>
      {ep.id === "assay" && plate ? (
        <>
          <div class="lab-kv">
            <span>
              {tr("plate")} {plate.plate_id}
            </span>
            <span>
              {String(state.tips_remaining ?? "—")} {tr("tips")}
            </span>
            <span>
              {tr("temp")} {fmt(state.temperature)} °C
            </span>
          </div>
          <PlateMap rows={plate.rows} />
        </>
      ) : null}
      {ep.id === "reactor" && ep.state ? (
        <div class="lab-kv">
          <span>
            {tr("temp")} {fmt(state.temperature)} °C
          </span>
          <span>
            {tr("pressure")} {fmt(state.pressure)} bar
          </span>
          <span>
            {tr("flow")} {fmt(state.flow)} mL/min
          </span>
          <span>
            {String(state.experiments ?? 0)} {tr("experiments")}
          </span>
        </div>
      ) : null}
      {ep.id === "xarm" ? (
        <div class="lab-kv">
          <span>
            {tr("pose")}{" "}
            {state.last_pose_mm
              ? (() => {
                  const p = state.last_pose_mm as { x: number; y: number; z: number };
                  return `(${fmt(p.x, 4)}, ${fmt(p.y, 4)}, ${fmt(p.z, 4)}) mm`;
                })()
              : "—"}
          </span>
          <span>
            {String(state.receipts ?? 0)} {tr("receipts")}
          </span>
        </div>
      ) : null}
      <div class="lab-prov">
        {tr("wire")}: {ep.wire} · {tr("provenance")}: {ep.provenance}
      </div>
    </article>
  );
}

// ---------------------------------------------------------------- panel

export function LabPanel() {
  void languageRevision.value;
  const [status, setStatus] = useState<LabStatus | null>(null);
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [counts, setCounts] = useState<Record<string, Record<string, number>>>({});
  const [results, setResults] = useState<LabResults | null>(null);
  const [verify, setVerify] = useState<LabVerify | null>(null);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [showMarket, setShowMarket] = useState(false);
  const [working, setWorking] = useState("");
  const root = useRef<HTMLDivElement>(null);

  const refresh = async (withVerify = false) => {
    try {
      const st = (await api("/mhs-lab/status")) as LabStatus;
      setStatus(st);
      if (!st.available) return;
      const [act, res] = await Promise.all([api("/mhs-lab/activity?limit=200"), api("/mhs-lab/results")]);
      const a = act as { items: ActivityItem[]; counts: Record<string, Record<string, number>> };
      setItems(a.items || []);
      setCounts(a.counts || {});
      setResults(res as LabResults);
      if (withVerify) setVerify((await api("/mhs-lab/verify")) as LabVerify);
      setError("");
    } catch (e) {
      setError(apiErrorText(e));
    }
  };

  useEffect(() => {
    let tick = 0;
    void refresh(true);
    const timer = window.setInterval(() => {
      const pane = root.current?.closest(".dock-pane");
      if (pane && pane.classList.contains("hidden")) return;
      tick += 1;
      void refresh(tick % 3 === 0);
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const act = async (action: string, endpoint: string) => {
    if (action === "reset" && !window.confirm(tr("confirmReset"))) return;
    if (action === "estop" && endpoint === "all" && !window.confirm(tr("confirmEstop"))) return;
    setBusy(true);
    try {
      await api(`/mhs-lab/${action}`, {
        method: "POST",
        body: JSON.stringify({ endpoint, operator: "human:operator (Lab panel)" }),
      });
    } catch (e) {
      setError(apiErrorText(e));
    } finally {
      setBusy(false);
      void refresh(true);
    }
  };

  const operate = async (label: string, path: string, init: RequestInit) => {
    setWorking(label);
    try {
      await api(path, init);
      setError("");
    } catch (e) {
      setError(apiErrorText(e));
    } finally {
      setWorking("");
      void refresh(true);
    }
  };

  const remove = (id: string) => {
    if (!window.confirm(tr("confirmRemove"))) return;
    void operate("remove", `/mhs-lab/devices/${encodeURIComponent(id)}`, { method: "DELETE" });
  };

  if (status && !status.available) {
    return (
      <div class="lab-root" ref={root}>
        <section class="lab-setup">
          <h3>{tr("setupTitle")}</h3>
          <p>{tr("setupBody")}</p>
          <button
            class="lab-btn primary big"
            type="button"
            disabled={Boolean(working)}
            onClick={() => void operate("setup", "/mhs-lab/setup", { method: "POST", body: JSON.stringify({ confirm: true }) })}
          >
            {working === "setup" ? tr("settingUp") : tr("setupBtn")}
          </button>
          <p class="lab-muted">
            {tr("manual")} <code>git submodule update --init third_party/OpenMHS</code> · <code>openai4s lab setup</code>
          </p>
          {error ? <p class="lab-error">{error}</p> : null}
        </section>
      </div>
    );
  }
  const eps = status?.endpoints || [];
  const labRunning = eps.some((e) => Boolean(e.url) && e.running === true);
  const groups: Array<["mhs" | "mcp", LabEndpoint[]]> = [
    ["mhs", eps.filter((e) => e.group === "mhs")],
    ["mcp", eps.filter((e) => e.group !== "mhs")],
  ];
  const online = eps.filter((e) => e.running !== false).length;
  const chains = verify ? Object.values(verify.chains) : [];
  const allValid =
    verify != null &&
    chains.every((c) => c.valid !== false) &&
    (verify.host_ledger.valid === undefined || verify.host_ledger.valid !== false);
  const recent = [...items].reverse().slice(0, 60);
  return (
    <div class="lab-root" ref={root}>
      <section class="lab-head">
        <div>
          <h3>
            {tr("title")} <span class="lab-tag strong">{tr("simulated")}</span>
          </h3>
          <p>{tr("sub")}</p>
          <div class="lab-meta">
            <span class="lab-chip">
              ● {online}/{eps.length} {tr("online")}
            </span>
            {verify ? (
              <span class={`lab-chip ${allValid ? "ok" : "bad"}`}>
                {allValid ? "✓" : "✕"} {allValid ? tr("verified") : tr("unverified")}
              </span>
            ) : null}
            <span class="lab-chip">
              {items.length} {tr("receipts")}
            </span>
          </div>
        </div>
        <div class="lab-head-actions">
          <button class="lab-btn primary" type="button" onClick={() => setShowMarket(!showMarket)}>
            {showMarket ? tr("closeMarket") : `＋ ${tr("market")}`}
          </button>
          {labRunning ? (
            <button
              class="lab-btn"
              type="button"
              disabled={Boolean(working)}
              onClick={() => void operate("stop", "/mhs-lab/stop", { method: "POST", body: "{}" })}
            >
              {tr("stop")}
            </button>
          ) : (
            <button
              class="lab-btn primary"
              type="button"
              disabled={Boolean(working)}
              onClick={() => void operate("start", "/mhs-lab/start", { method: "POST", body: "{}" })}
            >
              {working === "start" ? tr("starting") : `▶ ${tr("start")}`}
            </button>
          )}
          <button class="lab-btn danger big" type="button" disabled={busy} onClick={() => act("estop", "all")}>
            ⏹ {tr("estopAll")}
          </button>
        </div>
      </section>
      {error ? <p class="lab-error">{error}</p> : null}
      {status && !labRunning ? <p class="lab-notice">{tr("stoppedHint")}</p> : null}

      {showMarket ? (
        <>
          <h5 class="lab-section">{tr("market")}</h5>
          <MarketPanel onChanged={() => void refresh(true)} />
        </>
      ) : null}

      <h5 class="lab-section">{tr("devices")}</h5>
      {groups.map(([group, list]) => (
        <section class="lab-group" key={group}>
          <h6 class="lab-group-title">
            {group === "mhs" ? tr("groupMhs") : tr("groupMcp")} <span class="lab-chip">{list.length}</span>
          </h6>
          <p class="lab-muted">{group === "mhs" ? tr("groupMhsHint") : tr("groupMcpHint")}</p>
          {list.length ? (
            <div class="lab-devices">
              {list.map((ep) => (
                <DeviceCard key={ep.id} ep={ep} onAction={act} onRemove={remove} busy={busy || Boolean(working)} />
              ))}
            </div>
          ) : (
            <p class="lab-empty">{tr("emptyGroup")}</p>
          )}
        </section>
      ))}

      {results &&
      (results.dose_response.length || results.reactor_runs.length || results.arm_path.length) ? (
        <>
          <h5 class="lab-section">{tr("results")}</h5>
          <DoseChart curves={results.dose_response} />
          <ReactorChart runs={results.reactor_runs} />
          <ArmChart path={results.arm_path} />
        </>
      ) : null}

      <h5 class="lab-section">{tr("activity")}</h5>
      {recent.length ? (
        <ol class="lab-activity">
          {recent.map((it) => (
            <li key={`${it.endpoint}-${it.seq}`} class={it.status === "completed" ? "" : "is-refused"}>
              <span class="lab-time">{shortTime(it.ts)}</span>
              <span class="lab-access small">{accessLabel(it.access)}</span>
              <span class="lab-cap">
                {it.endpoint} · {it.capability}
              </span>
              <StatusChip status={it.status} code={it.code} />
              <span class="lab-summary">{it.status === "completed" ? it.summary : `${it.code || ""} ${it.message || ""}`}</span>
              <span class="lab-seq" title={it.digest || ""}>
                #{it.seq}
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p class="lab-empty">{tr("noActivity")}</p>
      )}

      <h5 class="lab-section">{tr("evidence")}</h5>
      <table class="lab-table">
        <thead>
          <tr>
            <th>{tr("chain")}</th>
            <th>{tr("receipts")}</th>
            <th>✓/⛔/✕</th>
            <th>head</th>
          </tr>
        </thead>
        <tbody>
          {eps.map((ep) => {
            const c = verify?.chains[ep.id];
            const k = counts[ep.id] || {};
            return (
              <tr key={ep.id}>
                <td>
                  {c ? (c.valid === false ? "✕ " : "✓ ") : ""}
                  {ep.id} <span class="lab-access small">{accessLabel(ep.access)}</span>
                </td>
                <td>{c ? c.checked : "—"}</td>
                <td>
                  {k.completed || 0}/{k.rejected || 0}/{k.failed || 0}
                </td>
                <td class="lab-mono">{c && c.head_digest ? c.head_digest.slice(7, 19) + "…" : "—"}</td>
              </tr>
            );
          })}
          {verify ? (
            <tr>
              <td>
                {verify.host_ledger.valid === false ? "✕ " : "✓ "}
                {tr("hostLedger")}
              </td>
              <td>{verify.host_ledger.checked ?? verify.host_ledger.records ?? 0}</td>
              <td />
              <td class="lab-mono">{verify.host_ledger.head_digest ? String(verify.host_ledger.head_digest).slice(7, 19) + "…" : "—"}</td>
            </tr>
          ) : null}
        </tbody>
      </table>

      {status && status.lims && status.lims.length ? (
        <>
          <h5 class="lab-section">{tr("lims")}</h5>
          <table class="lab-table">
            <thead>
              <tr>
                <th>{tr("vial")}</th>
                <th>{tr("product")}</th>
                <th>mM</th>
                <th>{tr("purity")}</th>
                <th>{tr("location")}</th>
              </tr>
            </thead>
            <tbody>
              {status.lims.map((v) => (
                <tr key={v.vial_id}>
                  <td>{v.vial_id}</td>
                  <td>{v.compound_id}</td>
                  <td>{fmt(v.concentration_mM)}</td>
                  <td>{fmt(Number(v.purity) * 100)}%</td>
                  <td>{v.location}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}
    </div>
  );
}
