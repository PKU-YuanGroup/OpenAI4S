"use strict";
// OpenAI4S UI — aligned to Claude Science (dashboard + conversation), over /api + /api/ws.
const $ = (s) => document.querySelector(s);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
const esc = (s) => (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
/* ---------- line icons (lucide) ---------- */
const ICONS = {
  "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "chevron-up": '<path d="m18 15-6-6-6 6"/>',
  "arrow-left": '<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',
  "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
  "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  "check": '<path d="M20 6 9 17l-5-5"/>',
  "box": '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  "sliders": '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/><line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/><line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/><line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/><line x1="16" x2="16" y1="18" y2="22"/>',
  "files": '<path d="M20 7h-3a2 2 0 0 1-2-2V2"/><path d="M9 18a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h7l4 4v10a2 2 0 0 1-2 2Z"/><path d="M3 7.6v12.8A1.6 1.6 0 0 0 4.6 22h9.8"/>',
  "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
  "more-horizontal": '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  "more-vertical": '<circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/>',
  "panel-left": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/>',
  "panel-right": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M15 3v18"/>',
  "layout": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
  "grid": '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  "thumbs-up": '<path d="M7 10v12"/><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"/>',
  "thumbs-down": '<path d="M17 14V2"/><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"/>',
  "pencil": '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.375 2.625a1 1 0 0 1 3 3l-9.013 9.014a2 2 0 0 1-.853.505l-2.873.84a.5.5 0 0 1-.62-.62l.84-2.873a2 2 0 0 1 .506-.852z"/>',
  "copy": '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
  "trash-2": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/>',
  "maximize-2": '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" x2="14" y1="3" y2="10"/><line x1="3" x2="10" y1="21" y2="14"/>',
  "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>',
  "mic": '<path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/>',
  "notebook": '<path d="M2 6h4"/><path d="M2 10h4"/><path d="M2 14h4"/><path d="M2 18h4"/><rect width="16" height="20" x="4" y="2" rx="2"/><path d="M16 2v20"/>',
  "file": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  "table": '<path d="M12 3v18"/><rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="M3 15h18"/>',
  "type": '<polyline points="4 7 4 4 20 4 20 7"/><line x1="9" x2="15" y1="20" y2="20"/><line x1="12" x2="12" y1="4" y2="20"/>',
  "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  "stop": '<circle cx="12" cy="12" r="10"/><rect width="6" height="6" x="9" y="9" rx="1"/>',
  "alert-triangle": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  "eye": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  "eye-off": '<path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49"/><path d="M14.084 14.158a3 3 0 0 1-4.242-4.242"/><path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143"/><path d="m2 2 20 20"/>',
  "star": '<path d="M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755a2.122 2.122 0 0 0 1.597-1.16z"/>',
  "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
  "cloud-upload": '<path d="M12 13v8"/><path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/><path d="m8 17 4-4 4 4"/>',
  "eye-context": '<path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0"/><circle cx="12" cy="12" r="3"/>',
  "atom": '<circle cx="12" cy="12" r="1"/><path d="M20.2 20.2c2.04-2.03.02-7.36-4.5-11.9-4.54-4.52-9.87-6.54-11.9-4.5-2.04 2.03-.02 7.36 4.5 11.9 4.54 4.52 9.87 6.54 11.9 4.5Z"/><path d="M15.7 15.7c4.52-4.54 6.54-9.87 4.5-11.9-2.03-2.04-7.36-.02-11.9 4.5-4.52 4.54-6.54 9.87-4.5 11.9 2.03 2.04 7.36.02 11.9-4.5Z"/>',
  "lock": '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  "loader": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
  "provenance": '<line x1="6" x2="6" y1="3" y2="15"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
  "message-square": '<path d="M22 17a2 2 0 0 1-2 2H6l-4 4V4a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
  "book": '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  "globe": '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  "package": '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/><path d="M12 22V12"/><polyline points="3.29 7 12 12 20.71 7"/><path d="m7.5 4.27 9 5.15"/>',
  "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
  "list-check": '<path d="M11 18H3"/><path d="M11 12H3"/><path d="M11 6H3"/><path d="m15 18 2 2 4-4"/><path d="m15 6 2 2 4-4"/>',
  "circle": '<circle cx="12" cy="12" r="9"/>',
  "circle-dot": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3" fill="currentColor" stroke="none"/>',
  "minus": '<path d="M5 12h14"/>',
  "zoom-in": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M11 8v6"/><path d="M8 11h6"/>',
  "zoom-out": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/>',
};
const icon = (name, size, cls) => `<svg class="ic-svg${cls ? " " + cls : ""}" width="${size || 16}" height="${size || 16}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
const iconEl = (name, size, cls) => { const s = el("span", "ic"); s.innerHTML = icon(name, size, cls); return s.firstChild; };
function paintIcons(root) { (root || document).querySelectorAll("[data-icon]").forEach(e => { if (e._painted) return; e.innerHTML = icon(e.dataset.icon, +e.dataset.iconSize || 16); e._painted = true; }); }
function setTitle(name) { const ct = $("#conv-title"); if (!ct) return; ct.value = name || t("conv.title.default"); ct.size = Math.max(6, Math.min(40, (name || t("conv.title.default")).length + 1)); }
const api = async (p, o = {}) => {
  // `p` must be an internal, same-origin API path: a single leading slash and no
  // scheme/host. Rejecting "//host" (protocol-relative) and non-string input keeps
  // an untrusted id interpolated into `p` from redirecting the request off-origin.
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/") throw new Error("invalid api path");
  const r = await fetch("/api" + p, { headers: { "content-type": "application/json" }, ...o });
  const t = await r.text(); let j = null; try { j = t ? JSON.parse(t) : null; } catch { j = t; }
  if (!r.ok) throw new Error((j && j.detail) || ("HTTP " + r.status)); return j;
};
const S = { projects: [], sessions: [], project: null, currentId: null, ws: null, stream: null, running: false, models: [], defaultModel: null, sandboxOrigin: "", planMode: false, exploreMode: false, planPending: false, planReady: null, planStatus: null, artifacts: [], dock: { open: false, tab: "notebook" }, openTabs: [], activeTab: "notebook", provMode: false, provSub: "code", cells: [], kernels: [], liveCells: [], _liveCell: null, dockArtifact: null, kernelFilter: null, _titleName: "", skillsCatalog: null, _menu: null, annotations: [], _annotDraft: null, filesScope: "frame", projectArtifacts: [], _projArtFor: null };
const ac = { open: false, items: [], idx: 0, trigger: "", start: 0 };
const TOOL_LABELS = { run_python: "toolLabel.runPython", run_bash: "toolLabel.runBash", search_skills: "toolLabel.searchSkills", read_skill: "toolLabel.readSkill", write_file: "toolLabel.writeFile", read_file: "toolLabel.readFile", list_files: "toolLabel.listFiles", delegate: "toolLabel.delegate" };

/* ---------- i18n (中文 / English) ---------- */
// Single dictionary keyed by stable dot-keys; every UI string reads through t().
// I18N.zh / I18N.en are populated by the big Object.assign block just below.
const I18N = { zh: {}, en: {} };
let LANG = (() => {
  try { const s = localStorage.getItem("os-lang"); if (s === "zh" || s === "en") return s; } catch {}
  try { return (navigator.languages || [navigator.language || ""]).some(l => /^zh/i.test(l)) ? "zh" : "en"; } catch {}
  return "zh";
})();
// t("key", ...args) — current-language string with {0},{1}… positional interpolation; falls back to zh, then the key.
function t(key, ...args) {
  const d = I18N[LANG] || I18N.zh || {};
  let s = d[key]; if (s == null) { const z = (I18N.zh || {})[key]; s = z != null ? z : key; }
  if (args.length) s = String(s).replace(/\{(\d+)\}/g, (m, i) => (args[+i] != null ? args[+i] : m));
  return s;
}
// Apply translations to static HTML carrying data-i18n / data-i18n-title / data-i18n-ph / data-i18n-val.
function applyStaticI18n(root) {
  const r = root || document;
  r.querySelectorAll("[data-i18n]").forEach(e => { e.textContent = t(e.getAttribute("data-i18n")); });
  r.querySelectorAll("[data-i18n-title]").forEach(e => { e.title = t(e.getAttribute("data-i18n-title")); });
  r.querySelectorAll("[data-i18n-ph]").forEach(e => { e.placeholder = t(e.getAttribute("data-i18n-ph")); });
  r.querySelectorAll("[data-i18n-val]").forEach(e => { e.value = t(e.getAttribute("data-i18n-val")); });
}
function refreshLangToggle() { document.querySelectorAll(".lang-btn").forEach(b => b.classList.toggle("active", b.dataset.lang === LANG)); }
function setLang(lang) {
  LANG = lang === "en" ? "en" : "zh"; try { localStorage.setItem("os-lang", LANG); } catch {}
  document.documentElement.lang = LANG === "en" ? "en" : "zh";
  applyStaticI18n(document); refreshLangToggle(); rerenderI18n();
}
// Re-render the dynamic (JS-built) views currently on screen after a language switch.
function rerenderI18n() {
  try { if (!$("#dashboard").classList.contains("hidden")) loadDashboard(); } catch {}
  try { renderProjMenu(); } catch {}
  try { renderSessions(); } catch {}
  try { renderDockTabs(); } catch {}
  try { if (S._titleName) setTitle(S._titleName); } catch {}
  try { if (!$("#cust").classList.contains("hidden")) { const at = document.querySelector(".cust-tab.active"); custTab(at ? at.dataset.tab : "general"); } } catch {}
  try { const m = $("#messages"); if (m && m.children.length === 1 && m.firstChild && m.firstChild.classList && m.firstChild.classList.contains("empty-session")) { m.innerHTML = ""; renderEmptySession(); } } catch {}
}

Object.assign(I18N.zh, {
  "cust.general.language": "语言",
  "cust.general.languageDesc": "界面显示语言（保存在本机浏览器）",
  "annot.added": "已添加标注 · 发送消息时会一并提交给智能体",
  "annot.artifactFallback": "artifact",
  "annot.attachCount": " 附带 {0} 条图像标注",
  "annot.chip.title": "点击查看待发送的图像标注（发送消息时一并提交给智能体）",
  "annot.comment.plural": " 条评论",
  "annot.comment.singular": " 条评论",
  "annot.deleted": "已删除标注",
  "annot.discard.title": "取消待发送评论",
  "annot.discarded": "已取消待发送评论",
  "annot.draft.placeholder": "添加标注…",
  "annot.list.head": "待发送标注 · {0}",
  "annot.noSession": "请先打开一个会话",
  "annot.remove.err": "移除失败：{0}",
  "annot.save.err": "标注保存失败：{0}",
  "annot.save.err404": "保存失败：后端未加载标注接口，请重启服务（python3 -m openai4s serve）",
  "annot.status.open": "待发送",
  "annot.status.resolved": "已处理",
  "annot.status.sent": "已发送",
  "app.title": "OpenAI4S",
  "art.default.filename": "制品",
  "artifact.delete.confirm": "确定删除该文件？此操作不可撤销。",
  "artifact.deleted": "已删除：{0}",
  "artifact.hidden": "已隐藏",
  "artifact.linkCopied": "已复制链接",
  "artifact.metadataExported": "已导出元数据 JSON",
  "artifact.notEditable": "该文件类型不可编辑",
  "artifact.priority.err": "操作失败：{0}",
  "artifact.rename.prompt": "重命名文件",
  "artifact.renamed": "已重命名",
  "artifact.save.err": "保存失败：{0}",
  "artifact.saved": "已保存：{0}",
  "artifact.starred": "已收藏 ⭐",
  "artifact.unstarred": "已取消收藏",
  "btn.remove": "移除",
  "code.copied": "已复制",
  "code.copy.title": "复制代码",
  "code.lang.text": "text",
  "common.add": "添加",
  "common.cancel": "取消",
  "common.close": "关闭",
  "common.delete": "删除",
  "common.download": "下载",
  "common.edit": "编辑",
  "common.loading": "加载中…",
  "common.nameRequired": "请填写名称",
  "common.save": "保存",
  "common.saving": "保存中…",
  "common.settings": "设置",
  "common.view": "查看",
  "composer.attach": "上传文件",
  "composer.model": "模型",
  "composer.placeholder": "输入任何内容 — @ 引用制品，# 引用会话，/ 使用技能，⌘K 搜索…",
  "composer.planMode": "计划模式",
  "composer.exploreMode": "自主探索",
  "composer.voice": "语音输入",
  "confirm.deleteSession": "确定删除该会话？此操作不可撤销。",
  "conv.dockToggle": "侧栏面板",
  "conv.jumpLast": "跳到最后一条",
  "conv.jumpLastLabel": "最新",
  "output.binaryElided": "已省略二进制输出（{0}）",
  "skill.invokeDirective": "请使用技能「{0}」：先调用 host.load_skill(\"{0}\") 载入其完整协议，然后严格按照该协议完成任务。",
  "skill.useInChat": "在对话中使用",
  "skill.insertedToast": "已插入 /{0}，回车即可调用该技能",
  "resizer.drag": "拖动调整宽度",
  "zoom.in": "放大",
  "zoom.out": "缩小",
  "zoom.reset": "适应窗口（点击百分比重置）",
  "zoom.hint": "⌘/Ctrl+滚轮或双指捏合缩放 · 放大后拖动平移 · 点击图片添加批注",
  "conv.resuming.hint": "此会话仍在后台运行，正在恢复…",
  "conv.title.default": "会话",
  "conv.title.rename": "重命名会话（回车保存）",
  "cust.compute.desc": "本地内核环境、预装包与加速器",
  "cust.compute.gpuAvailable": "可用",
  "cust.compute.gpuName": "GPU",
  "cust.compute.gpuUnavailable": "不可用（本地无 GPU；重型模型以标注的 CPU 近似替代，或走 Modal/SSH 远程算力）",
  "cust.compute.host": "本机",
  "cust.compute.hostDetail": "Python {0} · {1} · {2} CPU · {3} GB 内存 · {4} GB 空闲",
  "cust.compute.installBtn": "安装",
  "cust.compute.installExtraName": "安装额外的包",
  "cust.compute.installPlaceholder": "如 scanpy anndata（空格分隔）",
  "cust.compute.installingBtn": "安装中…",
  "cust.compute.kernelInstalling": "预装中…",
  "cust.compute.kernelLabel": "{0} 内核 · {1}",
  "cust.compute.kernelReady": "就绪",
  "cust.compute.kernelRestarted": "（内核已重启）",
  "cust.compute.localName": "本机",
  "cust.compute.remoteName": "远程 GPU（结构预测）",
  "cust.compute.remoteDetail": "{0} · {1} · {2} · 由 host.fold() 实时调用",
  "cust.compute.remoteOnline": "在线",
  "cust.compute.remoteUnreachable": "已配置（当前不可达）",
  "cust.remote.title": "远程 GPU",
  "cust.remote.desc": "把 ~/.ssh/config 里的主机作为远端算力；服务按需在其上部署，并记入记忆以便复用。",
  "cust.remote.services": "服务：",
  "cust.remote.noservices": "尚未部署服务",
  "cust.remote.unreachable": "当前不可达",
  "cust.remote.addName": "添加远程 GPU（来自 ~/.ssh/config）",
  "cust.remote.pickAlias": "选择一个 SSH 主机…",
  "cust.remote.noAlias": "~/.ssh/config 里没有可用主机",
  "cust.remote.testing": "连接测试中…",
  "cust.remote.added": "已添加 {0}（{1}）",
  "cust.remote.addedUnreachable": "已添加 {0}，但当前不可达",
  "cust.remote.confirmRemove": "从远程算力中移除 {0}？",
  "common.remove": "移除",
  "cust.compute.preinstalledDetail": "已预装 {0} 个科学/联网包：{1}",
  "cust.compute.title": "计算",
  "cust.connectors.cmdPlaceholder": "启动命令，如 npx -y @modelcontextprotocol/server-filesystem .",
  "cust.connectors.customAddName": "添加自定义（命令行 MCP 服务器）",
  "cust.connectors.deleteConfirm": "删除连接器 {0}？",
  "cust.connectors.desc": "MCP 工具服务器：连接外部工具，智能体用 host.mcp.call(id, tool, args) 调用",
  "cust.connectors.fromDirectory": "从目录添加",
  "cust.connectors.namePlaceholder": "名称",
  "cust.connectors.test": "测试",
  "cust.connectors.testing": "测试中…",
  "cust.general.apiKeyConfigured": "✅ 已配置",
  "cust.general.apiKeyMissing": "⚠️ 尚未配置 API Key — 发送消息会失败",
  "cust.general.configureBtn": "配置 →",
  "cust.general.desc": "全局外观与偏好（保存在本机浏览器）",
  "cust.general.layout.comfortable": "舒适",
  "cust.general.layout.compact": "紧凑",
  "cust.general.layout.wide": "宽屏",
  "cust.general.layoutDesc": "调整界面的间距与内容宽度",
  "cust.general.layoutName": "布局密度",
  "cust.general.modelKeyName": "模型与 API Key",
  "cust.general.title": "通用",
  "cust.importing": "导入中…",
  "cust.jobs.cmdPlaceholder": "bash: 如 \"for i in 1 2 3; do echo $i; sleep 1; done\"；python: 一段脚本",
  "cust.jobs.desc": "把长命令/脚本作为后台任务运行，可查看输出、取消",
  "cust.jobs.empty": "还没有任务。",
  "cust.jobs.runBtn": "运行",
  "cust.jobs.submitName": "提交任务",
  "cust.jobs.title": "计算任务 Jobs",
  "cust.jobs.viewOutput": "输出",
  "cust.memory.addName": "添加记忆",
  "cust.memory.categories": "分类",
  "cust.memory.contentPlaceholder": "如 用户偏好 Python，专注嗜极菌系统发育…",
  "cust.memory.desc": "跨会话长期记忆（启用后自动注入后续会话的上下文）",
  "cust.memory.disabledDesc": "未启用",
  "cust.memory.empty": "还没有记忆。添加后会在启用时注入每次会话。",
  "cust.memory.enableName": "启用记忆",
  "cust.memory.enabledDesc": "已启用 — 保存的记忆会注入每次会话",
  "cust.memory.title": "记忆",
  "cust.models.activePill": "当前",
  "cust.models.addBtn": "新增",
  "cust.models.addHeading": "新增模型 / API",
  "cust.models.available": "可选模型",
  "cust.models.baseUrl.placeholder": "Base URL（留空用该 provider 默认）",
  "cust.models.baseUrlPlaceholder": "Base URL（留空用该 provider 默认）",
  "cust.models.cancelEdit": "取消编辑",
  "cust.models.configuredHeading": "已配置的模型 / API",
  "cust.models.editHeading": "编辑：{0}",
  "cust.models.empty2": "还没有模型配置。用上面的表单新增一个。",
  "cust.models.hasKey": "🔑 已配置 Key",
  "cust.models.key.configured": "✅ API Key 已配置",
  "cust.models.key.missing": "⚠️ 尚未配置 API Key — 发送消息会失败",
  "cust.models.key.placeholder.set": "API Key（已配置，留空则不改动）",
  "cust.models.key.placeholder.unset": "API Key（尚未配置，请填写）",
  "cust.models.keyPlaceholderSet": "API Key（已配置，留空则保留）",
  "cust.models.keyPlaceholderUnset": "API Key（未配置，可填写）",
  "cust.models.label.apiKey": "API Key",
  "cust.models.label.baseUrl": "Base URL",
  "cust.models.label.defaultModel": "默认模型",
  "cust.models.label.provider": "Provider",
  "cust.models.model.placeholder": "模型 id（留空用 provider 默认）",
  "cust.models.modelPlaceholder2": "模型 id（留空用该 provider 默认）",
  "cust.models.namePlaceholder": "名称（如 DeepSeek 生产 / 本地 vLLM）",
  "cust.models.noKey": "⚠️ 无 Key",
  "cust.models.provider.placeholder": "provider（如 ark / chatgpt / claude / gemini）",
  "cust.models.providerPlaceholder2": "provider（如 ark / chatgpt / claude / gemini）",
  "cust.search.name": "搜索 API Key（Tavily）",
  "cust.search.desc": "用于联网搜索的 Tavily 密钥；接入点固定为 api.tavily.com。",
  "cust.search.set": "已配置",
  "cust.search.unset": "未配置",
  "cust.search.ph": "输入 Tavily API Key",
  "cust.search.saved": "搜索 Key 已保存",
  "art.uploaded": "上传",
  "art.generated": "生成",
  "cust.models.save": "保存并生效",
  "cust.models.setActive": "设为当前",
  "cust.models.setDefault": "设为默认",
  "cust.models.subtitle": "配置 LLM 提供商、Base URL、模型与 API Key（保存后立即生效）",
  "cust.models.subtitle2": "配置多套 LLM API（provider / Base URL / 模型 / Key），随时新增、切换或删除，方便对接不同接口",
  "cust.models.updateBtn": "更新",
  "cust.network.allowName": "允许联网",
  "cust.network.desc": "联网访问（智能体的 web_search / web_fetch / bash 与代码请求）",
  "cust.network.disabledDesc": "已禁用 — 智能体仅用本地知识与已有文件",
  "cust.network.enabledDesc": "已启用 — 智能体可实时检索文献、抓取数据库、下载数据包",
  "cust.network.title": "网络",
  "cust.perm.decision.ask": "询问",
  "cust.perm.desc": "控制哪些工具需要你的批准。优先级：越具体越优先；同等具体时 本对话 > 本项目 > 全局。默认安全优先：读取放行，写入 / 命令 / 联网 / 装包 需批准，.env 读取被拒。",
  "cust.perm.noRules": "（无规则）",
  "cust.perm.noSessionNote": "打开一个会话后可管理该对话与项目的规则。下面仅显示全局默认。",
  "cust.perm.patternPlaceholder": "模式（git * / *.csv / *）",
  "cust.perm.resetBtn": "恢复默认",
  "cust.perm.resetConfirm": "恢复内置安全默认规则？",
  "cust.perm.resetDesc": "重新写入内置的全局默认规则（不会删除你已添加的规则）",
  "cust.perm.resetName": "恢复安全默认",
  "cust.perm.scope.conversation": "本对话",
  "cust.perm.scope.global": "全局（所有项目）",
  "cust.perm.scope.project": "本项目",
  "cust.perm.title": "权限",
  "cust.perm.toolPlaceholder": "工具（bash / write_file / *）",
  "cust.skills.deleteConfirm": "删除技能 {0}？",
  "cust.skills.desc": "{0} 个科研技能；开关控制智能体是否可用，也可新建/导入自己的技能",
  "cust.skills.importBtn": "导入 SKILL.md",
  "cust.skills.newBtn": "＋ 新建技能",
  "cust.skills.yourSkills": "你的技能",
  "cust.specialists.builtinRoles": "内置角色",
  "cust.specialists.deleteConfirm": "删除专家 {0}？",
  "cust.specialists.desc": "可委派的专家智能体：内置角色 + 你自定义的专家（用 host.delegate(task, name=…) 调用）",
  "cust.specialists.newBtn": "＋ 新建专家",
  "cust.specialists.yours": "你的专家",
  "cust.tab.connectors": "连接器",
  "cust.tab.models": "模型",
  "cust.tab.specialists": "专家",
  "dash.badge.running": "运行中",
  "dash.brand.beta": "Beta",
  "dash.col.projects": "项目",
  "dash.col.recentSessions": "最近会话",
  "dash.meta.session": "{0} 个会话",
  "dash.meta.sessions": "{0} 个会话",
  "dash.project.runningCount": "{0} 个会话运行中",
  "dash.project.untitled": "未命名项目",
  "dash.projects.empty": "还没有项目。点右上角 ＋New project 创建。",
  "dash.running.activeNow": "活跃中",
  "dash.running.count": "{0} 个运行中",
  "dash.sessions.empty": "还没有会话。",
  "dash.tag.example": "Example",
  "data.col.data": "数据",
  "data.column.plural": " 列",
  "data.column.singular": " 列",
  "data.rows.plural": " 行 · ",
  "data.rows.singular": " 行 · ",
  "date.bucket.older": "更早",
  "date.bucket.thisWeek": "本周",
  "date.bucket.today": "今天",
  "date.bucket.yesterday": "昨天",
  "dock.artifact.fallback": "工件",
  "dock.collapse": "收起",
  "dock.files.heading": "文件 · 制品",
  "dock.files.scope.frame": "本会话",
  "dock.files.scope.project": "本项目",
  "dock.notes.placeholder": "添加一条笔记…",
  "dock.tab.files": "文件",
  "dock.tab.notebook": "笔记本",
  "edac.keyword": "关键字",
  "editor.label": "编辑 {0}",
  "empty.sub": "描述你的科研任务，智能体会写 Python、联网检索、调用技能并产出图表/报告/结构文件。可试试：",
  "empty.title": "开始一个新分析",
  "export.artifactsHeading": "## 产物",
  "export.messageAssistant": "🤖 助手",
  "export.messageUser": "🧑 用户",
  "files.empty": "任务产出的文件、表格、图表会显示在这里。",
  "files.emptyProject": "本项目所有会话都还没产出文件。",
  "files.fromSession": "来自 {0}",
  "folder.assigned.in": "已移入文件夹",
  "folder.assigned.out": "已移出文件夹",
  "folder.create.failed": "创建失败：{0}",
  "folder.delete.confirm": "删除文件夹「{0}」？其中的会话会移出但不会被删除。",
  "folder.menu.delete": "删除文件夹",
  "folder.menu.rename": "重命名",
  "folder.move.failed": "移动失败：{0}",
  "folder.new.prompt": "文件夹名称",
  "folder.rename.prompt": "重命名文件夹",
  "gen.label": "已生成 · {0}",
  "gen.more": "+{0} 更多",
  "job.outputEmpty": "(无输出)",
  "job.outputLoadFailed": "加载失败",
  "job.outputTitle": "任务输出 — {0}",
  "kernel.envChanged": "已切换到 {0} 环境",
  "kernel.envChanged.default": "新",
  "kernel.restarted": "内核已重启（第 {0} 代）",
  "kernel.started": "内核已启动",
  "kernel.stopped": "内核已停止（会话保留，可随时启动以恢复）",
  "ketcher.modalTitle": "Ketcher — 化学结构编辑器",
  "key.banner.goConfigure": "去配置 →",
  "key.banner.notConfigured": " 尚未配置 API Key，发送消息会失败。",
  "label.apiKey": "API Key",
  "label.baseUrl": "Base URL",
  "label.model": "模型",
  "label.provider": "Provider",
  "menu.copyLink": "复制链接",
  "menu.exportMetadata": "导出元数据",
  "menu.hideFromList": "从列表隐藏",
  "menu.provenance": "溯源",
  "menu.star": "收藏",
  "menu.unstar": "取消收藏",
  "menu.versionHistory": "版本历史",
  "modal.title.preview": "预览",
  "model.delete.confirm": "删除模型配置「{0}」？",
  "models.none": "无模型",
  "mol.foot": "拖动旋转 • 滚动缩放 • Shift+拖动平移",
  "mol.style.cartoon": "卡通",
  "mol.style.line": "线条",
  "mol.style.sphere": "球状",
  "mol.style.stick": "棍状",
  "mol.style.surface": "表面",
  "mol.styleLabel": "样式：",
  "mol.tag": "使用 3Dmol.js 查看器",
  "moveFolder.newFolderAndMove": "＋ 新建文件夹并移入",
  "moveFolder.removeFromFolder": "（移出文件夹）",
  "msgAction.copy": "复制",
  "msgAction.thumbsDown": "踩",
  "msgAction.thumbsUp": "赞",
  "nb.badge.idle": "Idle",
  "nb.badge.live": "Live",
  "nb.cell.statusOk": "ok",
  "nb.cell.statusRunning": "running",
  "nb.chips.all": "全部",
  "nb.empty": "运行任务后，这里会显示 Notebook 代码单元与输出。",
  "nb.env.placeholder": "环境…",
  "nb.env.rSuffix": " · R",
  "nb.env.selectTitle": "选择运行环境（内置 conda 环境；切换会重启内核并清空变量，Notebook 与文件保留）",
  "nb.error.default": "执行出错",
  "nb.kernel.envSwitchFailed": "切换环境失败：{0}",
  "nb.kernel.envSwitched": "已切换到 {0} 环境（变量已清空，Notebook 与文件保留）",
  "nb.kernel.generation": " · 第{0}代",
  "nb.kernel.noSession": "无会话",
  "nb.kernel.opFailed": "内核操作失败：{0}",
  "nb.kernel.pendingSwitch": "（将切换到 {0}）",
  "nb.kernel.restartConfirm": "重启内核会清空所有变量与内存状态（Notebook 历史保留）。继续？",
  "nb.kernel.restartLabel": "重启",
  "nb.kernel.restartTitle": "重启内核（清空变量、载入新装的包；保留 Notebook 历史）",
  "nb.kernel.startLabel": "启动",
  "nb.kernel.startTitle": "启动/复活内核（保留对话，可继续跑）",
  "nb.kernel.stateActive": "活跃",
  "nb.kernel.stateLoading": "…",
  "nb.kernel.stateNone": "未启动",
  "nb.kernel.stateStopped": "已停止",
  "nb.kernel.stopConfirm": "停止内核会清空变量与内存状态（会话、Notebook 与文件保留，可再启动恢复）。继续？",
  "nb.kernel.stopLabel": "停止",
  "nb.kernel.stopTitle": "停止内核并释放资源（会话、Notebook 与文件保留，可再启动以恢复）",
  "nb.kernel.title": "kernel",
  "nb.repl.body": "已连接到与 Agent 共享的实时内核。上方下拉可切换内置运行环境（python / struct / phylo / r，免安装）；确需额外的包时 `pip install` 后点“重启内核”。",
  "nb.repl.execFailed": "执行失败：{0}",
  "nb.repl.inputPlaceholder": "在此内核中运行代码…",
  "nb.repl.interruptSent": "已发送中断",
  "nb.repl.interruptTitle": "中断执行",
  "nb.revive.startBtn": "▶ 启动内核",
  "nb.revive.text": "内核已停止 — 直接输入命令即可复活，或",
  "nb.status.ended": "{0} · 已结束 — 仅供查看；该内核的内存命名空间已不存在。",
  "nb.status.hint": "发送消息即可继续。你的下一条消息会在此环境中恢复运行 — 工作区文件保留；内存中的变量仅在内核存活时恢复。",
  "nb.status.live": "运行中 · {0}",
  "nb.table.rowsHidden": "… {0} 行未显示",
  "notes.empty": "还没有笔记。",
  "notes.emptyNoProject": "在某个项目下可添加笔记。",
  "palette.action.backHome": "返回主页",
  "palette.action.customize": "自定义",
  "palette.action.newProject": "新建项目",
  "palette.action.newSession": "新建会话",
  "palette.action.openNotebook": "打开 Notebook",
  "palette.empty": "没有匹配项",
  "palette.group.artifacts": "产物",
  "palette.group.commands": "命令",
  "palette.group.sessions": "会话",
  "palette.group.skills": "技能",
  "palette.searchPlaceholder": "搜索会话、产物、技能，或执行命令…",
  "perm.badge.subAgent": "子智能体",
  "perm.btn.allow": "允许",
  "perm.btn.deny": "拒绝",
  "perm.lbl.rememberRule": "记住规则（可用 * 通配）",
  "perm.lbl.rememberScope": "记住范围",
  "perm.placeholder.denyReason": "（可选）拒绝原因，会反馈给智能体",
  "perm.scope.conversation": "本对话",
  "perm.scope.global": "全局",
  "perm.scope.once": "仅此一次",
  "perm.scope.project": "本项目",
  "perm.status.allowed": "已允许",
  "perm.status.allowedScope": "已允许（{0}）",
  "perm.status.denied": "已拒绝",
  "perm.sub.approvalNeeded": "智能体请求执行下面的操作，需要你的批准。",
  "perm.title.run": "运行 {0}",
  "plan.approve": "批准并执行",
  "plan.approveFailed": "批准失败：{0}",
  "plan.autoExecuting": "按计划自动执行中…",
  "plan.confidenceSuffix": "{0} 置信度",
  "plan.discard": "放弃",
  "plan.eyebrow.completed": "计划已完成",
  "plan.eyebrow.default": "计划",
  "plan.eyebrow.draft": "计划已就绪，等待您审阅",
  "plan.eyebrow.executing": "正在执行计划",
  "plan.eyebrow.failed": "计划已中断",
  "plan.legacy.approvedPrompt": "已批准。请严格按上面的计划执行：运行代码、使用相应技能，并产出结果文件。",
  "plan.legacy.intro": "以上是执行计划。批准后将按计划运行并产出结果文件。",
  "plan.prompt.intro": "[计划模式] 请先不要执行、不要调用任何工具。为下面的任务制定一个结构化执行计划，并只输出两部分：\n",
  "plan.prompt.jsonSchema": "{\"title\":\"计划标题\",\"rationale\":\"一句话理由\",\"confidence\":\"high|medium|low\",\"steps\":[{\"id\":\"s1\",\"title\":\"步骤标题\",\"detail\":\"这一步做什么\",\"deliverables\":[\"中间结果.csv\",\"图.png\"]}]}\n",
  "plan.prompt.part1": "1) 一段简短的方案说明（散文，说明你选择的目标/思路与分析主线）；\n",
  "plan.prompt.part2": "2) 紧接着一个 ```json 代码块，严格使用如下结构：\n",
  "plan.prompt.part3": "每个步骤要有唯一 id、清晰标题、简要说明，以及该步预期产出的结果文件名列表；尽量让每一步都产出一个可查看的中间结果——一张表格（.csv）或一张图（.png）作为 deliverable，若该步确实不适合制表/画图则可省略。等待用户批准后再执行。\n\n任务：",
  "plan.revise.placeholder": "描述对计划的修改…（回车提交）",
  "plan.status.completed": "计划已执行完成（{0}/{1}）",
  "plan.status.executing": "正在按计划执行…（{0}/{1}）",
  "plan.status.failed": "执行中断（{0}/{1}）",
  "plan.step.default": "步骤",
  "plan.title.default": "执行计划",
  "plan.toggle.on": "计划模式：先出计划，批准后执行",
  "explore.toggle.on": "自主探索：AI 自动完成端到端研究",
  "proj.current.allSessions": "所有会话",
  "proj.delete.confirm": "确定删除该项目？此操作不可撤销。",
  "proj.fallbackName": "项目",
  "proj.menu.allProjects": "所有项目",
  "projModal.create": "创建",
  "projModal.ctx.label": "智能体上下文",
  "projModal.ctx.placeholder": "包含在此项目每个智能体的提示词中",
  "projModal.desc.placeholder": "显示在项目列表中",
  "projModal.name.placeholder": "项目名称",
  "projModal.title": "新建项目",
  "prov.code.generating": "正在生成复现代码…",
  "prov.env.chipEnvironment": "Environment",
  "prov.env.chipPackages": "Packages",
  "prov.env.chipPython": "Python",
  "prov.env.liveFallback": "实时快照 — 此产物未记录生产时环境（上传文件或早于该功能生成）",
  "prov.env.loadFailed": "无法加载环境：{0}",
  "prov.env.loadingSnapshot": "加载环境快照…",
  "prov.env.noPackages": "没有可报告的包。",
  "prov.env.recorded": "已记录于该产物生产时的内核环境",
  "prov.env.remoteTitle": "远程 GPU 计算（可复现）",
  "prov.env.remoteHost": "主机",
  "prov.env.remoteEnv": "环境",
  "prov.env.remotePkgs": "依赖",
  "prov.env.remoteCode": "代码版本",
  "prov.env.remoteModel": "模型/权重",
  "prov.env.remoteRun": "运行时间(UTC)",
  "prov.env.thPackage": "Package",
  "prov.env.thVersion": "Version",
  "prov.exec.downloadNotebook": "下载 Notebook",
  "prov.exec.noRecords": "暂无执行记录。",
  "prov.msg.loadFailed": "无法加载对话：{0}",
  "prov.msg.loading": "加载对话…",
  "prov.msg.noRecords": "暂无对话记录。",
  "prov.msg.roleAssistant": "Assistant",
  "prov.msg.roleSystem": "System",
  "prov.msg.roleUser": "User",
  "prov.review.noLineage": "暂无溯源信息（execution log 为空或未记录文件 I/O）。",
  "prov.review.producedBy": "由单元 {0} 生成",
  "prov.review.readsInputs": "读取 / 输入",
  "prov.review.saved": "已保存 · {0}",
  "prov.review.viewCode": "查看产生它的代码",
  "prov.review.wrote": "写入",
  "prov.sub.code": "Code",
  "prov.sub.environment": "Environment",
  "prov.sub.exec": "Execution Log",
  "prov.sub.messages": "Messages",
  "prov.sub.review": "Review",
  "send.imageAnnotationFallback": "（图像标注反馈）",
  "session.badge.liveTip": "内核活跃（可随时恢复）",
  "session.badge.runningTip": "任务仍在后台运行 — 点击恢复",
  "session.duplicateSuffix": "（副本）",
  "session.empty.label": "还没有会话",
  "session.menu.tip": "会话操作",
  "session.newFolder": "＋ 文件夹",
  "session.untitled": "未命名会话",
  "sessionMenu.duplicate": "复制会话",
  "sessionMenu.exportMarkdown": "导出为 Markdown",
  "sessionMenu.moveToFolder": "移动到文件夹",
  "skill.bodyPlaceholder": "SKILL.md 正文（Markdown 配方：步骤、代码、注意事项…）",
  "skill.descPlaceholder": "一句话描述（用于技能检索）",
  "skill.editTitle": "编辑技能 — {0}",
  "skill.importBtn": "导入",
  "skill.importLabel": "SKILL.md 内容",
  "skill.importPlaceholder": "粘贴完整的 SKILL.md（含 --- name / description --- 前置元数据）",
  "skill.importTitle": "导入技能（粘贴 SKILL.md）",
  "skill.label.body": "正文",
  "skill.label.desc": "描述",
  "skill.label.name": "名称",
  "skill.namePlaceholder": "技能名（英文短横线，如 my-analysis）",
  "skill.newTitle": "新建技能",
  "skill.saveBtn": "保存技能",
  "specialist.descPlaceholder": "一句话描述",
  "specialist.editTitle": "编辑专家 — {0}",
  "specialist.label.systemPrompt": "系统提示",
  "specialist.namePlaceholder": "专家名（如 Biostatistician）",
  "specialist.newTitle": "新建专家",
  "specialist.promptPlaceholder": "系统提示 / 人设：描述这个专家的专长、方法、风格与约束…",
  "specialist.saveBtn": "保存专家",
  "starter.dataAnalysis.prompt": "读取我上传的 CSV，做探索性数据分析：统计摘要、相关性热图、关键变量分布图，输出图表和结论。",
  "starter.dataAnalysis.title": "分析我上传的数据",
  "starter.litReview.prompt": "用 web_search 检索关于 CRISPR 碱基编辑脱靶效应的近三年进展，综合成一份带引用的 Markdown 简报。",
  "starter.litReview.title": "检索并综述文献",
  "starter.phylo.prompt": "对一组同源蛋白做多序列比对与进化树重建，输出比对总览图、树图和保守位点表。",
  "starter.phylo.title": "系统发育分析",
  "starter.proteinModel.prompt": "为一段蛋白序列构建一个可在 3D 查看器打开的结构模型（.pdb），并画出每残基置信度曲线。",
  "starter.proteinModel.title": "蛋白结构建模",
  "step.artifact.hideOutput": "隐藏输出",
  "step.artifact.openArtifact": "打开产物",
  "step.artifact.showOutput": "显示输出",
  "step.card.defaultTitle": "步骤",
  "step.env.installed": "已安装：{0}",
  "step.env.missing": "缺少：{0}",
  "step.env.ready": "就绪",
  "step.fig.altFallback": "图",
  "step.label.code": "代码",
  "step.search.emptyResult": "（结果）",
  "step.skill.list": "技能：{0}",
  "step.status.failed": "失败",
  "time.justNow": "刚刚",
  "toast.addFailed": "添加失败：{0}",
  "toast.compute.installFailed": "安装失败：{0}",
  "toast.compute.installSeeLogs": "见日志",
  "toast.compute.installedKernelRestart": "已安装：{0}",
  "toast.connectors.added": "已添加：{0}",
  "toast.connectors.probeOk": "连接成功，工具：{0}",
  "toast.connectors.testFailed": "测试失败：{0}",
  "toast.deleteFailed": "删除失败：{0}",
  "toast.deleted": "已删除",
  "toast.duplicateFailed": "复制失败：{0}",
  "toast.exportFailed": "导出失败：{0}",
  "toast.exportedMarkdown": "已导出会话 Markdown",
  "toast.failed": "失败：{0}",
  "toast.feedbackCancelled": "已取消反馈",
  "toast.feedbackDown": "已记录：踩 👎",
  "toast.feedbackUp": "已记录：赞 👍",
  "toast.importFailed": "导入失败：{0}",
  "toast.layout": "布局：{0}",
  "toast.llmSaved": "已保存 LLM 配置",
  "toast.llmSaved.noKey": "（仍缺 API Key）",
  "toast.memory.disabled": "记忆已关闭",
  "toast.memory.enabled": "记忆已启用",
  "toast.micError": "语音识别出错：{0}",
  "toast.micListening": "正在聆听…再次点击麦克风停止",
  "toast.micStartFailed": "无法启动语音识别",
  "toast.micUnsupported": "此浏览器不支持语音输入（请用 Chrome/Edge/Safari）",
  "toast.models.added": "已新增：{0}",
  "toast.models.switched": "已切换到：{0}",
  "toast.models.updated": "已更新：{0}",
  "toast.network.disabled": "联网已禁用",
  "toast.network.enabled": "联网已启用",
  "toast.perm.enterTool": "请填写工具名",
  "toast.perm.resetDone": "已恢复默认规则",
  "toast.perm.ruleUpdated": "已更新规则",
  "toast.perm.updateFailed": "更新失败：{0}",
  "toast.planDiscarded": "已放弃该计划",
  "toast.planRevising": "正在根据你的修改重拟计划…",
  "toast.renameFailed": "重命名失败：{0}",
  "toast.reviseFailed": "修改失败：{0}",
  "toast.running": "运行中…",
  "toast.sendFailed": "发送失败：{0}",
  "toast.skill.enterName": "请填写技能名",
  "toast.skill.imported": "已导入技能：{0}",
  "toast.skill.saved": "已保存技能：{0}",
  "toast.specialist.enterName": "请填写名称",
  "toast.specialist.saved": "已保存专家：{0}",
  "toast.submitFailed": "提交失败：{0}",
  "toast.switchFailed": "切换失败：{0}",
  "toolLabel.delegate": "委派给子智能体",
  "toolLabel.listFiles": "列出文件中",
  "toolLabel.readFile": "读取文件中",
  "toolLabel.readSkill": "读取技能中",
  "toolLabel.runBash": "运行命令中",
  "toolLabel.runPython": "运行代码中",
  "toolLabel.searchSkills": "搜索技能中",
  "toolLabel.writeFile": "写入文件中",
  "turn.failed": "这一轮失败了，请重试。",
  "upload.dropping": "正在上传拖入的文件…",
  "upload.failed": "上传失败：{0}",
  "upload.pasting": "正在上传粘贴的文件…",
  "upload.uploaded": "已上传：{0}",
  "versions.badge.current": "当前",
  "versions.empty": "暂无版本历史。",
  "versions.load.err": "加载失败：{0}",
  "versions.modal.title": "版本历史 — {0}",
  "versions.restore": "恢复此版本",
  "versions.restore.err": "恢复失败：{0}",
  "versions.restored": "已恢复到 v{0}",
  "versions.restoring": "恢复中…",
  "viewer.act.fullscreen": "全屏",
  "viewer.act.more": "更多",
  "viewer.empty": "在会话里点击一个文件以查看。",
  "ws.nav.files": "文件",
  "ws.nav.new": "新建",
  "ws.sidebar.collapse": "收起侧栏 (⌘B)",
  "ws.sidebar.expand": "展开侧栏 (⌘B)",
});
Object.assign(I18N.en, {
  "cust.general.language": "Language",
  "cust.general.languageDesc": "Interface display language (saved in this browser)",
  "annot.added": "Annotation added · will be submitted to the agent with your next message",
  "annot.artifactFallback": "artifact",
  "annot.attachCount": " {0} image annotations attached",
  "annot.chip.title": "Click to view pending image annotations (submitted to the agent with your next message)",
  "annot.comment.plural": " comments",
  "annot.comment.singular": " comment",
  "annot.deleted": "Annotation deleted",
  "annot.discard.title": "Cancel pending comments",
  "annot.discarded": "Pending comments cancelled",
  "annot.draft.placeholder": "Add annotation…",
  "annot.list.head": "Pending annotations · {0}",
  "annot.noSession": "Please open a session first",
  "annot.remove.err": "Remove failed: {0}",
  "annot.save.err": "Annotation save failed: {0}",
  "annot.save.err404": "Save failed: backend annotation API not loaded, please restart the service (python3 -m openai4s serve)",
  "annot.status.open": "Pending",
  "annot.status.resolved": "Resolved",
  "annot.status.sent": "Sent",
  "app.title": "OpenAI4S",
  "art.default.filename": "artifact",
  "artifact.delete.confirm": "Delete this file? This action cannot be undone.",
  "artifact.deleted": "Deleted: {0}",
  "artifact.hidden": "Hidden",
  "artifact.linkCopied": "Link copied",
  "artifact.metadataExported": "Metadata JSON exported",
  "artifact.notEditable": "This file type is not editable",
  "artifact.priority.err": "Operation failed: {0}",
  "artifact.rename.prompt": "Rename file",
  "artifact.renamed": "Renamed",
  "artifact.save.err": "Save failed: {0}",
  "artifact.saved": "Saved: {0}",
  "artifact.starred": "Starred ⭐",
  "artifact.unstarred": "Unstarred",
  "btn.remove": "Remove",
  "code.copied": "Copied",
  "code.copy.title": "Copy code",
  "code.lang.text": "text",
  "common.add": "Add",
  "common.cancel": "Cancel",
  "common.close": "Close",
  "common.delete": "Delete",
  "common.download": "Download",
  "common.edit": "Edit",
  "common.loading": "Loading…",
  "common.nameRequired": "Please enter a name",
  "common.save": "Save",
  "common.saving": "Saving…",
  "common.settings": "Settings",
  "common.view": "View",
  "composer.attach": "Upload file",
  "composer.model": "Model",
  "composer.placeholder": "Ask anything — @ for artifacts, # for sessions, / for skills, ⌘K to search…",
  "composer.planMode": "Plan mode",
  "composer.exploreMode": "Explore mode",
  "composer.voice": "Voice input",
  "confirm.deleteSession": "Delete this session? This action cannot be undone.",
  "conv.dockToggle": "Side panel",
  "conv.jumpLast": "Jump to latest",
  "conv.jumpLastLabel": "Latest",
  "output.binaryElided": "Binary output elided ({0})",
  "skill.invokeDirective": "Use the \"{0}\" skill: call host.load_skill(\"{0}\") to load its full protocol, then follow it exactly.",
  "skill.useInChat": "Use in chat",
  "skill.insertedToast": "Inserted /{0} — press Enter to invoke this skill",
  "resizer.drag": "Drag to resize",
  "zoom.in": "Zoom in",
  "zoom.out": "Zoom out",
  "zoom.reset": "Fit to window (click % to reset)",
  "zoom.hint": "⌘/Ctrl+scroll or pinch to zoom · drag to pan · click the image to annotate",
  "conv.resuming.hint": "This session is still running in the background, resuming…",
  "conv.title.default": "Session",
  "conv.title.rename": "Rename session (press Enter to save)",
  "cust.compute.desc": "Local kernel environments, preinstalled packages and accelerators",
  "cust.compute.gpuAvailable": "Available",
  "cust.compute.gpuName": "GPU",
  "cust.compute.gpuUnavailable": "Unavailable (no local GPU; heavy models fall back to annotated CPU approximations, or use Modal/SSH remote compute)",
  "cust.compute.host": "This machine",
  "cust.compute.hostDetail": "Python {0} · {1} · {2} CPU · {3} GB RAM · {4} GB free",
  "cust.compute.installBtn": "Install",
  "cust.compute.installExtraName": "Install additional packages",
  "cust.compute.installPlaceholder": "e.g. scanpy anndata (space-separated)",
  "cust.compute.installingBtn": "Installing…",
  "cust.compute.kernelInstalling": "Preinstalling…",
  "cust.compute.kernelLabel": "{0} kernel · {1}",
  "cust.compute.kernelReady": "Ready",
  "cust.compute.kernelRestarted": " (kernel restarted)",
  "cust.compute.localName": "Local machine",
  "cust.compute.remoteName": "Remote GPU (folding)",
  "cust.compute.remoteDetail": "{0} · {1} · {2} · live via host.fold()",
  "cust.compute.remoteOnline": "online",
  "cust.compute.remoteUnreachable": "configured (currently unreachable)",
  "cust.remote.title": "Remote GPU",
  "cust.remote.desc": "Use hosts from your ~/.ssh/config as remote compute; services are provisioned on demand and remembered for reuse.",
  "cust.remote.services": "Services:",
  "cust.remote.noservices": "no services provisioned yet",
  "cust.remote.unreachable": "currently unreachable",
  "cust.remote.addName": "Add remote GPU (from ~/.ssh/config)",
  "cust.remote.pickAlias": "Pick an SSH host…",
  "cust.remote.noAlias": "no hosts in ~/.ssh/config",
  "cust.remote.testing": "Testing…",
  "cust.remote.added": "Added {0} ({1})",
  "cust.remote.addedUnreachable": "Added {0}, but currently unreachable",
  "cust.remote.confirmRemove": "Remove {0} from remote compute?",
  "common.remove": "Remove",
  "cust.compute.preinstalledDetail": "{0} scientific/networking packages preinstalled: {1}",
  "cust.compute.title": "Compute",
  "cust.connectors.cmdPlaceholder": "Launch command, e.g. npx -y @modelcontextprotocol/server-filesystem .",
  "cust.connectors.customAddName": "Add custom (command-line MCP server)",
  "cust.connectors.deleteConfirm": "Delete connector {0}?",
  "cust.connectors.desc": "MCP tool servers: connect external tools, the agent calls them with host.mcp.call(id, tool, args)",
  "cust.connectors.fromDirectory": "Add from directory",
  "cust.connectors.namePlaceholder": "Name",
  "cust.connectors.test": "Test",
  "cust.connectors.testing": "Testing…",
  "cust.general.apiKeyConfigured": "✅ Configured",
  "cust.general.apiKeyMissing": "⚠️ API Key not configured — sending messages will fail",
  "cust.general.configureBtn": "Configure →",
  "cust.general.desc": "Global appearance and preferences (saved in this browser)",
  "cust.general.layout.comfortable": "Comfortable",
  "cust.general.layout.compact": "Compact",
  "cust.general.layout.wide": "Wide",
  "cust.general.layoutDesc": "Adjust the interface's spacing and content width",
  "cust.general.layoutName": "Layout density",
  "cust.general.modelKeyName": "Model and API Key",
  "cust.general.title": "General",
  "cust.importing": "Importing…",
  "cust.jobs.cmdPlaceholder": "bash: e.g. \"for i in 1 2 3; do echo $i; sleep 1; done\"; python: a script",
  "cust.jobs.desc": "Run long commands/scripts as background jobs; view output and cancel",
  "cust.jobs.empty": "No jobs yet.",
  "cust.jobs.runBtn": "Run",
  "cust.jobs.submitName": "Submit job",
  "cust.jobs.title": "Compute Jobs",
  "cust.jobs.viewOutput": "Output",
  "cust.memory.addName": "Add memory",
  "cust.memory.categories": "Categories",
  "cust.memory.contentPlaceholder": "e.g. User prefers Python, focuses on extremophile phylogenetics…",
  "cust.memory.desc": "Cross-session long-term memory (once enabled, automatically injected into future sessions' context)",
  "cust.memory.disabledDesc": "Not enabled",
  "cust.memory.empty": "No memories yet. Once added, they are injected into each session when enabled.",
  "cust.memory.enableName": "Enable memory",
  "cust.memory.enabledDesc": "Enabled — saved memories are injected into every session",
  "cust.memory.title": "Memory",
  "cust.models.activePill": "Active",
  "cust.models.addBtn": "Add",
  "cust.models.addHeading": "Add model / API",
  "cust.models.available": "Available models",
  "cust.models.baseUrl.placeholder": "Base URL (leave blank to use the provider default)",
  "cust.models.baseUrlPlaceholder": "Base URL (leave blank for the provider default)",
  "cust.models.cancelEdit": "Cancel edit",
  "cust.models.configuredHeading": "Configured models / APIs",
  "cust.models.editHeading": "Edit: {0}",
  "cust.models.empty2": "No models configured yet. Add one with the form above.",
  "cust.models.hasKey": "🔑 Key configured",
  "cust.models.key.configured": "✅ API Key configured",
  "cust.models.key.missing": "⚠️ API Key not configured yet — sending messages will fail",
  "cust.models.key.placeholder.set": "API Key (already configured, leave blank to keep unchanged)",
  "cust.models.key.placeholder.unset": "API Key (not configured yet, please fill in)",
  "cust.models.keyPlaceholderSet": "API Key (configured; leave blank to keep)",
  "cust.models.keyPlaceholderUnset": "API Key (not configured; enter one)",
  "cust.models.label.apiKey": "API Key",
  "cust.models.label.baseUrl": "Base URL",
  "cust.models.label.defaultModel": "Default model",
  "cust.models.label.provider": "Provider",
  "cust.models.model.placeholder": "Model id (leave blank to use the provider default)",
  "cust.models.modelPlaceholder2": "Model id (leave blank for the provider default)",
  "cust.models.namePlaceholder": "Name (e.g. DeepSeek Prod / Local vLLM)",
  "cust.models.noKey": "⚠️ No key",
  "cust.models.provider.placeholder": "provider (e.g. ark / chatgpt / claude / gemini)",
  "cust.models.providerPlaceholder2": "provider (e.g. ark / chatgpt / claude / gemini)",
  "cust.search.name": "Search API key (Tavily)",
  "cust.search.desc": "Tavily key for web search; the endpoint is fixed to api.tavily.com.",
  "cust.search.set": "Configured",
  "cust.search.unset": "Not configured",
  "cust.search.ph": "Enter Tavily API key",
  "cust.search.saved": "Search key saved",
  "art.uploaded": "UPLOADED",
  "art.generated": "GENERATED",
  "cust.models.save": "Save and apply",
  "cust.models.setActive": "Set active",
  "cust.models.setDefault": "Set as default",
  "cust.models.subtitle": "Configure the LLM provider, Base URL, model, and API Key (takes effect immediately after saving)",
  "cust.models.subtitle2": "Configure multiple LLM APIs (provider / Base URL / model / key); add, switch, or remove anytime to work with different endpoints",
  "cust.models.updateBtn": "Update",
  "cust.network.allowName": "Allow network access",
  "cust.network.desc": "Network access (the agent's web_search / web_fetch / bash and code requests)",
  "cust.network.disabledDesc": "Disabled — the agent uses only local knowledge and existing files",
  "cust.network.enabledDesc": "Enabled — the agent can search literature in real time, scrape databases, and download data packages",
  "cust.network.title": "Network",
  "cust.perm.decision.ask": "Ask",
  "cust.perm.desc": "Control which tools need your approval. Priority: the more specific, the higher; at equal specificity, This conversation > This project > Global. Safe by default: reads are allowed, writes / commands / network / package installs need approval, .env reads are denied.",
  "cust.perm.noRules": "(no rules)",
  "cust.perm.noSessionNote": "Open a session to manage its conversation and project rules. Only global defaults are shown below.",
  "cust.perm.patternPlaceholder": "Pattern (git * / *.csv / *)",
  "cust.perm.resetBtn": "Restore defaults",
  "cust.perm.resetConfirm": "Restore built-in safe default rules?",
  "cust.perm.resetDesc": "Rewrite the built-in global default rules (does not delete rules you've added)",
  "cust.perm.resetName": "Restore safe defaults",
  "cust.perm.scope.conversation": "This conversation",
  "cust.perm.scope.global": "Global (all projects)",
  "cust.perm.scope.project": "This project",
  "cust.perm.title": "Permissions",
  "cust.perm.toolPlaceholder": "Tool (bash / write_file / *)",
  "cust.skills.deleteConfirm": "Delete skill {0}?",
  "cust.skills.desc": "{0} research skills; toggles control whether the agent can use them, and you can create/import your own",
  "cust.skills.importBtn": "Import SKILL.md",
  "cust.skills.newBtn": "＋ New skill",
  "cust.skills.yourSkills": "Your skills",
  "cust.specialists.builtinRoles": "Built-in roles",
  "cust.specialists.deleteConfirm": "Delete specialist {0}?",
  "cust.specialists.desc": "Delegable specialist agents: built-in roles + your custom specialists (call with host.delegate(task, name=…))",
  "cust.specialists.newBtn": "＋ New specialist",
  "cust.specialists.yours": "Your specialists",
  "cust.tab.connectors": "Connectors",
  "cust.tab.models": "Models",
  "cust.tab.specialists": "Specialists",
  "dash.badge.running": "Running",
  "dash.brand.beta": "Beta",
  "dash.col.projects": "Projects",
  "dash.col.recentSessions": "Recent sessions",
  "dash.meta.session": "{0} session",
  "dash.meta.sessions": "{0} sessions",
  "dash.project.runningCount": "{0} sessions running",
  "dash.project.untitled": "Untitled project",
  "dash.projects.empty": "No projects yet. Click ＋New project at the top right to create one.",
  "dash.running.activeNow": "active now",
  "dash.running.count": "{0} running",
  "dash.sessions.empty": "No sessions yet.",
  "dash.tag.example": "Example",
  "data.col.data": "data",
  "data.column.plural": " columns",
  "data.column.singular": " column",
  "data.rows.plural": " rows · ",
  "data.rows.singular": " row · ",
  "date.bucket.older": "Older",
  "date.bucket.thisWeek": "This week",
  "date.bucket.today": "Today",
  "date.bucket.yesterday": "Yesterday",
  "dock.artifact.fallback": "artifact",
  "dock.collapse": "Collapse",
  "dock.files.heading": "Files · Artifacts",
  "dock.files.scope.frame": "This session",
  "dock.files.scope.project": "This project",
  "dock.notes.placeholder": "Add a note…",
  "dock.tab.files": "Files",
  "dock.tab.notebook": "Notebook",
  "edac.keyword": "Keywords",
  "editor.label": "Editing {0}",
  "empty.sub": "Describe your research task and the agent will write Python, search the web, invoke skills, and produce charts/reports/structure files. Try:",
  "empty.title": "Start a new analysis",
  "export.artifactsHeading": "## Artifacts",
  "export.messageAssistant": "🤖 Assistant",
  "export.messageUser": "🧑 User",
  "files.empty": "Files, tables, and charts produced by tasks will appear here.",
  "files.emptyProject": "No session in this project has produced files yet.",
  "files.fromSession": "From {0}",
  "folder.assigned.in": "Moved into folder",
  "folder.assigned.out": "Moved out of folder",
  "folder.create.failed": "Create failed: {0}",
  "folder.delete.confirm": "Delete folder \"{0}\"? Its sessions will be moved out but not deleted.",
  "folder.menu.delete": "Delete folder",
  "folder.menu.rename": "Rename",
  "folder.move.failed": "Move failed: {0}",
  "folder.new.prompt": "Folder name",
  "folder.rename.prompt": "Rename folder",
  "gen.label": "GENERATED · {0}",
  "gen.more": "+{0} more",
  "job.outputEmpty": "(no output)",
  "job.outputLoadFailed": "Load failed",
  "job.outputTitle": "Job output — {0}",
  "kernel.envChanged": "Switched to {0} environment",
  "kernel.envChanged.default": "new",
  "kernel.restarted": "Kernel restarted (generation {0})",
  "kernel.started": "Kernel started",
  "kernel.stopped": "Kernel stopped (session preserved; start anytime to resume)",
  "ketcher.modalTitle": "Ketcher — Chemical Structure Editor",
  "key.banner.goConfigure": "Configure →",
  "key.banner.notConfigured": " No API Key configured yet; sending messages will fail.",
  "label.apiKey": "API Key",
  "label.baseUrl": "Base URL",
  "label.model": "Model",
  "label.provider": "Provider",
  "menu.copyLink": "Copy link",
  "menu.exportMetadata": "Export metadata",
  "menu.hideFromList": "Hide from list",
  "menu.provenance": "Provenance",
  "menu.star": "Star",
  "menu.unstar": "Unstar",
  "menu.versionHistory": "Version history",
  "modal.title.preview": "Preview",
  "model.delete.confirm": "Delete model profile \"{0}\"?",
  "models.none": "No models",
  "mol.foot": "Drag to rotate • Scroll to zoom • Shift+drag to pan",
  "mol.style.cartoon": "Cartoon",
  "mol.style.line": "Line",
  "mol.style.sphere": "Sphere",
  "mol.style.stick": "Stick",
  "mol.style.surface": "Surface",
  "mol.styleLabel": "Style:",
  "mol.tag": "Using 3Dmol.js viewer",
  "moveFolder.newFolderAndMove": "+ New folder and move in",
  "moveFolder.removeFromFolder": "(Remove from folder)",
  "msgAction.copy": "Copy",
  "msgAction.thumbsDown": "Dislike",
  "msgAction.thumbsUp": "Like",
  "nb.badge.idle": "Idle",
  "nb.badge.live": "Live",
  "nb.cell.statusOk": "ok",
  "nb.cell.statusRunning": "running",
  "nb.chips.all": "All",
  "nb.empty": "After running a task, Notebook code cells and outputs will appear here.",
  "nb.env.placeholder": "Environment…",
  "nb.env.rSuffix": " · R",
  "nb.env.selectTitle": "Select runtime environment (built-in conda environments; switching restarts the kernel and clears variables, Notebook and files are preserved)",
  "nb.error.default": "Execution error",
  "nb.kernel.envSwitchFailed": "Failed to switch environment: {0}",
  "nb.kernel.envSwitched": "Switched to {0} environment (variables cleared, Notebook and files preserved)",
  "nb.kernel.generation": " · gen {0}",
  "nb.kernel.noSession": "No session",
  "nb.kernel.opFailed": "Kernel operation failed: {0}",
  "nb.kernel.pendingSwitch": " (switching to {0})",
  "nb.kernel.restartConfirm": "Restarting the kernel will clear all variables and memory state (Notebook history is preserved). Continue?",
  "nb.kernel.restartLabel": "Restart",
  "nb.kernel.restartTitle": "Restart the kernel (clears variables, loads newly installed packages; Notebook history is preserved)",
  "nb.kernel.startLabel": "Start",
  "nb.kernel.startTitle": "Start/revive the kernel (conversation preserved, can keep running)",
  "nb.kernel.stateActive": "Active",
  "nb.kernel.stateLoading": "…",
  "nb.kernel.stateNone": "Not started",
  "nb.kernel.stateStopped": "Stopped",
  "nb.kernel.stopConfirm": "Stopping the kernel will clear variables and memory state (session, Notebook and files are preserved, can restart to restore). Continue?",
  "nb.kernel.stopLabel": "Stop",
  "nb.kernel.stopTitle": "Stop the kernel and free resources (session, Notebook and files are preserved, can restart to restore)",
  "nb.kernel.title": "kernel",
  "nb.repl.body": "Connected to the live kernel shared with the Agent. Use the dropdown above to switch the built-in runtime environment (python / struct / phylo / r, no install needed); if you really need extra packages, `pip install` then click \"Restart kernel\".",
  "nb.repl.execFailed": "Execution failed: {0}",
  "nb.repl.inputPlaceholder": "run code in this kernel…",
  "nb.repl.interruptSent": "Interrupt sent",
  "nb.repl.interruptTitle": "Interrupt execution",
  "nb.revive.startBtn": "▶ Start kernel",
  "nb.revive.text": "Kernel stopped — just type a command to revive it, or",
  "nb.status.ended": "{0} · ended — view only; this kernel's in-memory namespace no longer exists.",
  "nb.status.hint": "Send a message to continue. Your next message resumes in this environment — workspace files remain; in-memory variables are restored only while the kernel is alive.",
  "nb.status.live": "Live · {0}",
  "nb.table.rowsHidden": "… {0} rows not shown",
  "notes.empty": "No notes yet.",
  "notes.emptyNoProject": "Notes can be added under a project.",
  "palette.action.backHome": "Back to home",
  "palette.action.customize": "Customize",
  "palette.action.newProject": "New project",
  "palette.action.newSession": "New session",
  "palette.action.openNotebook": "Open Notebook",
  "palette.empty": "No matches",
  "palette.group.artifacts": "Artifacts",
  "palette.group.commands": "Commands",
  "palette.group.sessions": "Sessions",
  "palette.group.skills": "Skills",
  "palette.searchPlaceholder": "Search sessions, artifacts, skills, or run a command…",
  "perm.badge.subAgent": "Subagent",
  "perm.btn.allow": "Allow",
  "perm.btn.deny": "Deny",
  "perm.lbl.rememberRule": "Remember rule (use * as wildcard)",
  "perm.lbl.rememberScope": "Remember scope",
  "perm.placeholder.denyReason": "(Optional) reason for denial, will be sent to the agent",
  "perm.scope.conversation": "This conversation",
  "perm.scope.global": "Global",
  "perm.scope.once": "Once",
  "perm.scope.project": "This project",
  "perm.status.allowed": "Allowed",
  "perm.status.allowedScope": "Allowed ({0})",
  "perm.status.denied": "Denied",
  "perm.sub.approvalNeeded": "The agent requests to perform the operation below and needs your approval.",
  "perm.title.run": "Run {0}",
  "plan.approve": "Approve and execute",
  "plan.approveFailed": "Approval failed: {0}",
  "plan.autoExecuting": "Auto-executing as planned…",
  "plan.confidenceSuffix": "{0} confidence",
  "plan.discard": "Discard",
  "plan.eyebrow.completed": "PLAN COMPLETE",
  "plan.eyebrow.default": "PLAN",
  "plan.eyebrow.draft": "PLAN READY FOR YOUR REVIEW",
  "plan.eyebrow.executing": "EXECUTING PLAN",
  "plan.eyebrow.failed": "PLAN INTERRUPTED",
  "plan.legacy.approvedPrompt": "Approved. Please strictly follow the plan above: run code, use the relevant skills, and produce result files.",
  "plan.legacy.intro": "The above is the execution plan. Once approved, it will run as planned and produce result files.",
  "plan.prompt.intro": "[Plan Mode] Do not execute or call any tools yet. Devise a structured execution plan for the task below, and output only two parts:\n",
  "plan.prompt.jsonSchema": "{\"title\":\"Plan title\",\"rationale\":\"One-sentence rationale\",\"confidence\":\"high|medium|low\",\"steps\":[{\"id\":\"s1\",\"title\":\"Step title\",\"detail\":\"What this step does\",\"deliverables\":[\"intermediate-table.csv\",\"figure.png\"]}]}\n",
  "plan.prompt.part1": "1) A brief description of the approach (prose, explaining your chosen goal/approach and the main analytical thread);\n",
  "plan.prompt.part2": "2) Immediately followed by a ```json code block, strictly using the following structure:\n",
  "plan.prompt.part3": "Each step must have a unique id, a clear title, a brief description, and a list of expected output filenames for that step; where reasonable, make each step yield a viewable intermediate result — a table (.csv) or a figure (.png) — as a deliverable, but omit it if that step genuinely isn't suited to a table/plot. Wait for user approval before executing.\n\nTask: ",
  "plan.revise.placeholder": "Describe changes to the plan… (press Enter to submit)",
  "plan.status.completed": "Plan execution complete ({0}/{1})",
  "plan.status.executing": "Executing as planned… ({0}/{1})",
  "plan.status.failed": "Execution interrupted ({0}/{1})",
  "plan.step.default": "Step",
  "plan.title.default": "Execution plan",
  "plan.toggle.on": "Plan mode: draft a plan first, then execute after approval",
  "explore.toggle.on": "Explore mode: AI autonomously runs an end-to-end investigation",
  "proj.current.allSessions": "All sessions",
  "proj.delete.confirm": "Delete this project? This action cannot be undone.",
  "proj.fallbackName": "Project",
  "proj.menu.allProjects": "All projects",
  "projModal.create": "Create",
  "projModal.ctx.label": "Agent Context",
  "projModal.ctx.placeholder": "Included in every agent's prompt for this project",
  "projModal.desc.placeholder": "Shown in the project list",
  "projModal.name.placeholder": "Project name",
  "projModal.title": "New Project",
  "prov.code.generating": "Generating reproduction code…",
  "prov.env.chipEnvironment": "Environment",
  "prov.env.chipPackages": "Packages",
  "prov.env.chipPython": "Python",
  "prov.env.liveFallback": "Live snapshot — this artifact has no recorded production environment (uploaded file, or generated before this feature)",
  "prov.env.loadFailed": "Failed to load environment: {0}",
  "prov.env.loadingSnapshot": "Loading environment snapshot…",
  "prov.env.noPackages": "No packages to report.",
  "prov.env.recorded": "Recorded from the kernel environment at the time this artifact was produced",
  "prov.env.remoteTitle": "Remote GPU compute (reproducible)",
  "prov.env.remoteHost": "Host",
  "prov.env.remoteEnv": "Env",
  "prov.env.remotePkgs": "Packages",
  "prov.env.remoteCode": "Code",
  "prov.env.remoteModel": "Model / weights",
  "prov.env.remoteRun": "Run (UTC)",
  "prov.env.thPackage": "Package",
  "prov.env.thVersion": "Version",
  "prov.exec.downloadNotebook": "Download notebook",
  "prov.exec.noRecords": "No execution records yet.",
  "prov.msg.loadFailed": "Failed to load conversation: {0}",
  "prov.msg.loading": "Loading conversation…",
  "prov.msg.noRecords": "No conversation records yet.",
  "prov.msg.roleAssistant": "Assistant",
  "prov.msg.roleSystem": "System",
  "prov.msg.roleUser": "User",
  "prov.review.noLineage": "No provenance information (execution log is empty or no file I/O was recorded).",
  "prov.review.producedBy": "produced by cell {0}",
  "prov.review.readsInputs": "reads / inputs",
  "prov.review.saved": "saved · {0}",
  "prov.review.viewCode": "View the code that produced it",
  "prov.review.wrote": "wrote",
  "prov.sub.code": "Code",
  "prov.sub.environment": "Environment",
  "prov.sub.exec": "Execution Log",
  "prov.sub.messages": "Messages",
  "prov.sub.review": "Review",
  "send.imageAnnotationFallback": "(Image annotation feedback)",
  "session.badge.liveTip": "Kernel alive (can resume anytime)",
  "session.badge.runningTip": "Task still running in the background — click to resume",
  "session.duplicateSuffix": "(Copy)",
  "session.empty.label": "No sessions yet",
  "session.menu.tip": "Session actions",
  "session.newFolder": "＋ Folder",
  "session.untitled": "Untitled session",
  "sessionMenu.duplicate": "Duplicate session",
  "sessionMenu.exportMarkdown": "Export as Markdown",
  "sessionMenu.moveToFolder": "Move to folder",
  "skill.bodyPlaceholder": "SKILL.md body (Markdown recipe: steps, code, caveats…)",
  "skill.descPlaceholder": "One-line description (used for skill retrieval)",
  "skill.editTitle": "Edit skill — {0}",
  "skill.importBtn": "Import",
  "skill.importLabel": "SKILL.md content",
  "skill.importPlaceholder": "Paste the full SKILL.md (including --- name / description --- front matter)",
  "skill.importTitle": "Import skill (paste SKILL.md)",
  "skill.label.body": "Body",
  "skill.label.desc": "Description",
  "skill.label.name": "Name",
  "skill.namePlaceholder": "Skill name (lowercase-hyphenated, e.g. my-analysis)",
  "skill.newTitle": "New skill",
  "skill.saveBtn": "Save skill",
  "specialist.descPlaceholder": "One-line description",
  "specialist.editTitle": "Edit specialist — {0}",
  "specialist.label.systemPrompt": "System prompt",
  "specialist.namePlaceholder": "Specialist name (e.g. Biostatistician)",
  "specialist.newTitle": "New specialist",
  "specialist.promptPlaceholder": "System prompt / persona: describe this specialist's expertise, methods, style and constraints…",
  "specialist.saveBtn": "Save specialist",
  "starter.dataAnalysis.prompt": "Read my uploaded CSV and do exploratory data analysis: summary statistics, a correlation heatmap, and distribution plots of key variables, then output the charts and conclusions.",
  "starter.dataAnalysis.title": "Analyze my uploaded data",
  "starter.litReview.prompt": "Use web_search to find advances in CRISPR base-editing off-target effects over the past three years, and synthesize a cited Markdown brief.",
  "starter.litReview.title": "Search and review literature",
  "starter.phylo.prompt": "Perform multiple sequence alignment and phylogenetic tree reconstruction on a set of homologous proteins, then output an alignment overview, the tree figure, and a conserved-sites table.",
  "starter.phylo.title": "Phylogenetic analysis",
  "starter.proteinModel.prompt": "Build a structure model (.pdb) for a protein sequence that can be opened in the 3D viewer, and plot the per-residue confidence curve.",
  "starter.proteinModel.title": "Protein structure modeling",
  "step.artifact.hideOutput": "Hide output",
  "step.artifact.openArtifact": "Open artifact",
  "step.artifact.showOutput": "Show output",
  "step.card.defaultTitle": "step",
  "step.env.installed": "installed: {0}",
  "step.env.missing": "missing: {0}",
  "step.env.ready": "ready",
  "step.fig.altFallback": "figure",
  "step.label.code": "Code",
  "step.search.emptyResult": "(result)",
  "step.skill.list": "skills: {0}",
  "step.status.failed": "Failed",
  "time.justNow": "just now",
  "toast.addFailed": "Failed to add: {0}",
  "toast.compute.installFailed": "Install failed: {0}",
  "toast.compute.installSeeLogs": "see logs",
  "toast.compute.installedKernelRestart": "Installed: {0}",
  "toast.connectors.added": "Added: {0}",
  "toast.connectors.probeOk": "Connected successfully, tools: {0}",
  "toast.connectors.testFailed": "Test failed: {0}",
  "toast.deleteFailed": "Delete failed: {0}",
  "toast.deleted": "Deleted",
  "toast.duplicateFailed": "Duplicate failed: {0}",
  "toast.exportFailed": "Export failed: {0}",
  "toast.exportedMarkdown": "Session exported as Markdown",
  "toast.failed": "Failed: {0}",
  "toast.feedbackCancelled": "Feedback cleared",
  "toast.feedbackDown": "Recorded: disliked 👎",
  "toast.feedbackUp": "Recorded: liked 👍",
  "toast.importFailed": "Import failed: {0}",
  "toast.layout": "Layout: {0}",
  "toast.llmSaved": "LLM configuration saved",
  "toast.llmSaved.noKey": " (API Key still missing)",
  "toast.memory.disabled": "Memory disabled",
  "toast.memory.enabled": "Memory enabled",
  "toast.micError": "Speech recognition error: {0}",
  "toast.micListening": "Listening… click the mic again to stop",
  "toast.micStartFailed": "Could not start speech recognition",
  "toast.micUnsupported": "This browser does not support voice input (please use Chrome/Edge/Safari)",
  "toast.models.added": "Added: {0}",
  "toast.models.switched": "Switched to: {0}",
  "toast.models.updated": "Updated: {0}",
  "toast.network.disabled": "Network access disabled",
  "toast.network.enabled": "Network access enabled",
  "toast.perm.enterTool": "Please enter a tool name",
  "toast.perm.resetDone": "Default rules restored",
  "toast.perm.ruleUpdated": "Rule updated",
  "toast.perm.updateFailed": "Update failed: {0}",
  "toast.planDiscarded": "Plan discarded",
  "toast.planRevising": "Revising the plan based on your changes…",
  "toast.renameFailed": "Rename failed: {0}",
  "toast.reviseFailed": "Revision failed: {0}",
  "toast.running": "Running…",
  "toast.sendFailed": "Send failed: {0}",
  "toast.skill.enterName": "Please enter a skill name",
  "toast.skill.imported": "Skill imported: {0}",
  "toast.skill.saved": "Skill saved: {0}",
  "toast.specialist.enterName": "Please enter a name",
  "toast.specialist.saved": "Specialist saved: {0}",
  "toast.submitFailed": "Submit failed: {0}",
  "toast.switchFailed": "Switch failed: {0}",
  "toolLabel.delegate": "Delegating to sub-agent",
  "toolLabel.listFiles": "Listing files",
  "toolLabel.readFile": "Reading file",
  "toolLabel.readSkill": "Reading skill",
  "toolLabel.runBash": "Running command",
  "toolLabel.runPython": "Running code",
  "toolLabel.searchSkills": "Searching skills",
  "toolLabel.writeFile": "Writing file",
  "turn.failed": "This turn failed. Please try again.",
  "upload.dropping": "Uploading dropped files…",
  "upload.failed": "Upload failed: {0}",
  "upload.pasting": "Uploading pasted files…",
  "upload.uploaded": "Uploaded: {0}",
  "versions.badge.current": "Current",
  "versions.empty": "No version history yet.",
  "versions.load.err": "Load failed: {0}",
  "versions.modal.title": "Version history — {0}",
  "versions.restore": "Restore this version",
  "versions.restore.err": "Restore failed: {0}",
  "versions.restored": "Restored to v{0}",
  "versions.restoring": "Restoring…",
  "viewer.act.fullscreen": "Fullscreen",
  "viewer.act.more": "More",
  "viewer.empty": "Click a file in the conversation to view it.",
  "ws.nav.files": "Files",
  "ws.nav.new": "New",
  "ws.sidebar.collapse": "Collapse sidebar (⌘B)",
  "ws.sidebar.expand": "Expand sidebar (⌘B)",
});


/* ---------- routing ---------- */
// The browser address bar is the source of truth for "where you are", so a view
// is a real, persistent, shareable location instead of everything living at the
// bare origin: the dashboard is "/", a conversation is
// "/projects/{pid}/frames/{fid}". Navigation pushes a history entry so reload,
// back/forward, bookmark and copy-link all restore the exact view
// (routeInitialView re-hydrates on load; the server already serves the SPA shell
// for any such deep-link path).
function framePath(fid, pid) { return `/projects/${encodeURIComponent(pid || "default")}/frames/${encodeURIComponent(fid)}`; }
function navURL(path, replace) {
  try {
    if (path === location.pathname) return;  // already there — don't stack a duplicate history entry
    history[replace ? "replaceState" : "pushState"]({ path }, "", path);
  } catch { /* history API unavailable (e.g. file://) — navigation still works, just not addressable */ }
}
function showDashboard() { navURL("/"); $("#workspace").classList.add("hidden"); $("#dashboard").classList.remove("hidden"); S.currentId = null; loadDashboard(); startDashPoll(); }
function showWorkspace() { stopDashPoll(); $("#dashboard").classList.add("hidden"); $("#workspace").classList.remove("hidden"); showConv(); syncMobileChrome(false); }
function showConv() { $("#conv-view").classList.remove("hidden"); }

/* ---------- responsive chrome (mobile sidebar drawer + scrim) ---------- */
const mqMobile = window.matchMedia("(max-width: 900px)");
function scrimEl() {
  let s = document.getElementById("mobile-scrim");
  if (!s) { s = el("div"); s.id = "mobile-scrim"; s.className = "mobile-scrim hidden"; s.onclick = () => setSidebar(true); document.body.appendChild(s); }
  return s;
}
// Single source of truth for sidebar state across desktop (grid column) and mobile (overlay drawer).
function setSidebar(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  const reopen = $("#sidebar-reopen"); if (reopen) reopen.classList.toggle("hidden", !collapsed);
  scrimEl().classList.toggle("hidden", collapsed || !mqMobile.matches);
}
// mobile → sidebar starts collapsed (drawer closed); desktop reset only when crossing the breakpoint.
function syncMobileChrome(resetDesktop) {
  if (mqMobile.matches) setSidebar(true);
  else if (resetDesktop) setSidebar(false);
}
mqMobile.addEventListener("change", () => syncMobileChrome(true));

/* ---------- right dock (dynamic artifact tabs + Notebook) ---------- */
function dockOpen() { S.dock.open = true; $("#rightdock").classList.remove("collapsed"); }
function dockClose() { S.dock.open = false; $("#rightdock").classList.add("collapsed"); }
function dockToggle() { if (S.dock.open) dockClose(); else { dockOpen(); setActiveTab(S.activeTab || "notebook"); } }
/* thin router kept for legacy call sites */
function dockTab(tab) {
  if (tab === "files") setActiveTab("files");
  else if (tab === "notebook") setActiveTab("notebook");
  else if (tab === "viewer") setActiveTab(S.dockArtifact ? S.dockArtifact.id : "notebook");
  else if (tab === "prov") { if (S.dockArtifact) showProvenance(S.dockArtifact); }
}
function artIcon(a) { const nm = (a.filename || "").toLowerCase(); const ct = a.content_type || ""; if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) return "file"; if (/\.(pdb|cif|mol|mol2|sdf|xyz)$/i.test(nm)) return "atom"; if (/csv|json|tsv/.test(ct) || /\.(csv|json|tsv)$/i.test(nm)) return "table"; return "file"; }
function tabBtn(cls, iconName, label) { const t = el(cls === "div" ? "div" : "button", "dock-tab"); const ic = el("span", "ic"); ic.innerHTML = icon(iconName, 14); t.appendChild(ic); t.appendChild(el("span", "t-name", label)); return t; }
function renderDockTabs() {
  const bar = $("#dock-tabs"); if (!bar) return; bar.innerHTML = "";
  (S.openTabs || []).forEach(a => {
    const tab = tabBtn("div", artIcon(a), a.filename || "artifact");
    if (S.activeTab === a.id) tab.classList.add("active");
    const cl = el("span", "t-close"); cl.innerHTML = icon("x", 14); cl.title = t("common.close"); cl.onclick = (e) => { e.stopPropagation(); closeTab(a.id); }; tab.appendChild(cl);
    tab.onclick = () => { S.dockArtifact = a; S.provMode = false; setActiveTab(a.id); };
    bar.appendChild(tab);
  });
  const nt = tabBtn("button", "notebook", "Notebook");
  if (S.activeTab === "notebook") nt.classList.add("active");
  nt.onclick = () => setActiveTab("notebook"); bar.appendChild(nt);
  if (S.activeTab === "files") { const ft = tabBtn("button", "files", "Files"); ft.classList.add("active"); bar.appendChild(ft); }
}
function addOpenTab(a) { if (!(S.openTabs || []).some(x => x.id === a.id)) (S.openTabs = S.openTabs || []).push(a); }
function closeTab(id) {
  S.openTabs = (S.openTabs || []).filter(x => x.id !== id);
  if (S.activeTab === id) { const last = S.openTabs[S.openTabs.length - 1]; if (last) { S.dockArtifact = last; S.provMode = false; setActiveTab(last.id); } else setActiveTab("notebook"); }
  else renderDockTabs();
}
function setActiveTab(t) {
  S.activeTab = t; dockOpen(); renderDockTabs();
  showDockPane(t === "notebook" ? "notebook" : (t === "files" ? "files" : "viewer"));
  if (t === "notebook") renderNotebook();
  else if (t === "files") { if (S.filesScope === "project") loadProjectArtifacts().then(renderFilesGrid); else renderFilesGrid(); }
  else renderViewer();
}
function showDockPane(pane) { ["viewer", "notebook", "files"].forEach(p => { const n = $("#dock-" + p); if (n) n.classList.toggle("hidden", p !== pane); }); }
function ghostIconBtn(name, title) { const b = el("button", "icon-ghost"); b.innerHTML = icon(name, 16); if (title) b.title = title; return b; }

/* ---------- WebSocket ---------- */
function connectWS() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + "/api/ws");
  S.ws = ws;
  ws.onopen = () => { conn(true); if (S.currentId) sub(S.currentId); };
  ws.onclose = () => { conn(false); setTimeout(connectWS, 1500); };
  ws.onmessage = (e) => { let m; try { m = JSON.parse(e.data); } catch { return; } onEvent(m); };
  clearInterval(connectWS._p); connectWS._p = setInterval(() => { try { ws.readyState === 1 && ws.send('{"type":"ping"}'); } catch {} }, 25000);
}
const sub = (f) => { try { S.ws && S.ws.readyState === 1 && S.ws.send(JSON.stringify({ type: "view_session", root_frame_id: f })); } catch {} };
const conn = (on) => { const d = $("#conn-dot"); if (d) d.className = "dot " + (on ? "on" : "off"); };
function onEvent(m) {
  const fid = m.root_frame_id || m.frame_id;
  if (m.type === "replay_begin") { if (mine(fid)) { if (S.stream && S.stream.wrap) S.stream.wrap.remove(); S.stream = null; S.liveCells = []; S._liveCell = null; } }
  else if (m.type === "replay_end") { if (mine(fid)) down(); }
  else if (m.type === "text_reset") { if (mine(fid)) startStream(); }
  else if (m.type === "text_chunk") { if (mine(fid)) feed(m.block_type || "text", m.chunk || "", m); }
  else if (m.type === "step") { if (mine(fid)) addLiveStep(m); }
  else if (m.type === "step_update") { if (mine(fid)) updateLiveStep(m); }
  else if (m.type === "plan_ready") { if (mine(fid)) renderPlanCard(m.plan, m.status); }
  else if (m.type === "plan_progress") { if (mine(fid)) updatePlanProgress(m); }
  else if (m.type === "await_permission") { if (mine(fid)) renderPermissionCard(m); }
  else if (m.type === "permission_resolved") { if (mine(fid)) resolvePermissionCard(m); }
  else if (m.type === "frame_update") {
    if (mine(m.frame_id) || mine(fid)) {
      if (m.status === "processing" && !S.running) { S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); resumeWatch(fid, S._openGen); }  // a turn observed on the WS (e.g. started from another tab) — watchdog covers a missed terminal event
      if (["completed","failed","cancelled","success","done"].includes(m.status)) turnDone(m.status);
    }
    loadSessions();
  }
  else if (m.type === "artifact_created") {
    // A produced file (possibly overwriting an existing artifact in place, e.g.
    // re-plotting after an annotation). Bust its cached URL by version so the
    // <img>/thumbnails reload the NEW bytes instead of the browser's stale copy,
    // and refresh the viewer if that very artifact is currently open.
    const art = m.artifact || {};
    const aid = art.id || art.artifact_id;
    if (aid) {
      (S._artBust = S._artBust || {})[aid] = art.version_id || String(Date.now());
      if (S.dockArtifact && S.dockArtifact.id === aid && !S.provMode && S.activeTab === aid) renderViewer();
    }
    const fn = art.filename || "";
    // An overwritten file may reuse the same cache key (fallback URL) — drop just
    // its cached inline table so a re-run cell re-reads the new bytes.
    if (S._tbl && fn) { const base = fn.split("/").pop(); for (const k in S._tbl) if (k.indexOf(base) !== -1) delete S._tbl[k]; }
    // Live-render a produced figure onto the current notebook cell, so images
    // show up as the agent makes them (not only after the whole turn ends).
    const isImg = /^image\//.test(art.content_type || "") || /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(fn);
    if (S.running && fn && isImg) {
      const cell = S._liveCell || (S.liveCells && S.liveCells[S.liveCells.length - 1]);
      if (cell && !(cell.figures || []).includes(fn)) { (cell.figures = cell.figures || []).push(fn); nbRender(); }
    }
    if (S.currentId) loadArtifacts(S.currentId);
    // Keep the project-wide Files view live too when it's the active scope.
    if (S.filesScope === "project") loadProjectArtifacts(true).then(() => { if (S.dock.open && S.activeTab === "files") renderFilesGrid(); });
  }
  else if (m.type === "kernel_status") { if (mine(m.frame_id)) {
    if (m.status === "restarted") hint(t("kernel.restarted", (m.generation || "?")));
    else if (m.status === "stopped") hint(t("kernel.stopped"));
    else if (m.status === "started") hint(t("kernel.started"));
    else if (m.status === "env_changed") hint(t("kernel.envChanged", ((m.env && m.env.name) || t("kernel.envChanged.default"))));
    invalidateKernelCache();  // kernel generation/env just changed — re-read state
    if (S.dock.open && S.activeTab === "notebook") renderNotebook();
  } }
}
const mine = (f) => f && S.currentId && f === S.currentId;

/* ---------- streaming ---------- */
// Batch markdown re-renders onto animation frames: a fast token stream would
// otherwise reparse the whole message on every chunk (janky, and it makes the
// caret strobe as the subtree is torn down each token).
function flushRender(st) {
  if (!st) return;
  if (st._raf) { cancelAnimationFrame(st._raf); st._raf = null; }
  if (st._dirty && st.md) { st._dirty = false; st.md.innerHTML = renderMd(st.text); }
}
function scheduleRender(st) {
  st._dirty = true;
  if (st._raf) return;
  st._raf = requestAnimationFrame(() => { st._raf = null; flushRender(st); down(); });
}
// Freeze the current text block: flush any pending render and drop its blinking
// caret. Called whenever the stream moves on to non-text content (a tool card, a
// step) so the caret never lingers on an already-finished paragraph.
function sealText(st) {
  if (!st || !st.md) return;
  flushRender(st);
  st.md.classList.remove("cursor");
}
function startStream() {
  const g = $(".generated"); if (g) g.remove();
  const es = $(".empty-session"); if (es) es.remove();  // clear starter card on any (resumed) stream
  const wrap = el("div", "msg assistant");
  const md = el("div", "md cursor"); wrap.appendChild(md);
  $("#messages").appendChild(wrap); S.stream = { wrap, md, text: "", full: "", toolPre: null, toolCard: null };
  S.stepEls = {};
  S.liveCells = []; S._liveCell = null; down();
}
const ensure = () => { if (!S.stream) startStream(); return S.stream; };
function feed(kind, chunk, event) {
  const st = ensure();
  if (kind === "tool") {
    const cellHeader = !!(event && event.cell_index != null);
    const subagentHeader = !cellHeader && chunk.startsWith("◆");
    const legacyCellHeader = !cellHeader && !st.toolPre && chunk.startsWith("⚙");
    if (cellHeader || subagentHeader || legacyCellHeader) {
      const suba = subagentHeader;
      const raw = chunk.replace(/[⚙◆\n]/g, "").trim();
      const tm = raw.match(/^([a-z_]+)/); const tool = tm ? tm[1] : "";
      const label = suba ? raw : (TOOL_LABELS[tool] ? t(TOOL_LABELS[tool]) : raw);
      const card = el("div", "activity" + (suba ? " subagent" : ""));
      const h = el("div", "a-head");
      const ic = el("span", "ic"); ic.innerHTML = icon("check", 16); h.appendChild(ic);
      h.appendChild(el("span", "lbl", label));
      const meta = el("span", "meta", ""); h.appendChild(meta);
      const chev = el("span", "chev-t"); chev.innerHTML = icon("chevron-down", 14); h.appendChild(chev);
      const pre = el("pre", null, raw + "\n"); card.appendChild(h); card.appendChild(pre);
      h.onclick = () => card.classList.toggle("open");
      sealText(st);
      st.wrap.appendChild(card); st.toolPre = pre; st.toolMeta = meta;
      if (!suba) { st.toolCard = card; card._demoted = false; }
      st.md = el("div", "md"); st.wrap.appendChild(st.md); st.text = "";
      if (!suba) nbLiveStart(tool, raw, event && event.kernel_id, event && event.cell_index, event && event.language);
    } else if (st.toolPre) {
      const add = chunk.replace(/^↳\s*/, "");
      st.toolPre.textContent += add;
      if (st.toolMeta) { const n = (st.toolPre.textContent.match(/\n/g) || []).length; st.toolMeta.textContent = n > 1 ? (n + (n === 1 ? " line" : " lines")) : "done"; }
      nbLiveAppend(add);
    }
  } else { st.text += chunk; st.full += chunk; st.md.classList.add("cursor"); scheduleRender(st); return; }
  down();
}
function turnDone(status) {
  S.running = false; enableComposer(true); $("#cancel-btn").classList.add("hidden");
  clearTimeout(S._resumeTimer); S._resumeTok = (S._resumeTok || 0) + 1;  // retire the resume-watchdog (incl. any in-flight tick) so it can't bleed into the next turn
  if (S.stream) { flushRender(S.stream); S.stream.md.classList.remove("cursor"); addMsgActions(S.stream.wrap, S.stream.full || S.stream.text); }
  // Belt-and-suspenders: a completed turn must leave nothing blinking, even on
  // text blocks orphaned earlier by a tool/step that started mid-stream.
  const mm = $("#messages"); if (mm) mm.querySelectorAll(".md.cursor").forEach(n => n.classList.remove("cursor"));
  hint(status === "failed" ? t("turn.failed") : "", status === "failed");
  invalidateKernelCache();  // the kernel just went turn_running → idle; re-read promptly
  if (S.currentId) { loadArtifacts(S.currentId); loadExecutionLog(S.currentId); }
  S.stream = null; S.liveCells = []; S._liveCell = null;
  // A plan that was still "executing …" must reach a terminal state when the turn
  // ends, or the card reads "finished but not finished". Flip the live card to
  // completed/failed to match the turn outcome.
  if (S.planReady && S.planStatus === "executing") renderPlanCard(S.planReady, status === "failed" ? "failed" : "completed");
  if (S.planPending && status !== "failed") { S.planPending = false; if (!S.planReady) showPlanApproval(); }
}
// Legacy fallback card (only shown if a plan-mode turn produced no structured
// plan_ready event, e.g. the model ignored the JSON schema). The rich card below
// is the normal path.
function showPlanApproval() {
  if ($("#plan-card-live")) return;  // structured plan_ready card already rendered → no legacy fallback
  const card = el("div", "plan-card");
  card.appendChild(el("div", null, t("plan.legacy.intro")));
  const pa = el("div", "pa"); const ok = el("button", "approve-btn"); ok.appendChild(iconEl("check", 15)); ok.appendChild(el("span", null, t("plan.approve")));
  ok.onclick = () => { card.remove(); send(t("plan.legacy.approvedPrompt"), { execute: true }); };
  const no = el("button", "outline-btn small", t("common.cancel")); no.onclick = () => card.remove();
  pa.appendChild(ok); pa.appendChild(no); card.appendChild(pa); $("#messages").appendChild(card); down();
}

/* ---------- structured plan: review card + live progress ---------- */
function planConfLevel(c) {
  if (c == null || c === "") return "medium";
  const s = String(c).toLowerCase();
  const n = parseFloat(s);
  if (s.includes("high") || s.includes("高") || (!isNaN(n) && n >= 0.75)) return "high";
  if (s.includes("low") || s.includes("低") || (!isNaN(n) && n > 0 && n < 0.4)) return "low";
  return "medium";
}
function planStepIcon(status) {
  if (status === "completed") return "check";
  if (status === "in_progress") return "circle-dot";
  if (status === "failed") return "x";
  if (status === "skipped") return "circle";
  return "circle";
}
function renderPlanCard(plan, status) {
  if (!plan || !mine(S.currentId)) return;
  status = status || plan.status || "draft";
  S.planReady = plan; S.planStatus = status;
  const old = $("#plan-card-live"); if (old) old.remove();
  document.querySelectorAll(".plan-card:not(.rich)").forEach(n => n.remove());  // drop any stray legacy fallback card
  if (status === "discarded") { S.planReady = null; return; }  // just clear on discard
  const card = el("div", "plan-card rich"); card.id = "plan-card-live";
  // header: title + confidence badge
  const head = el("div", "pc-head");
  const tt = el("div", "pc-title-wrap");
  tt.appendChild(el("div", "pc-eyebrow", status === "draft" ? t("plan.eyebrow.draft") : (status === "executing" ? t("plan.eyebrow.executing") : status === "completed" ? t("plan.eyebrow.completed") : status === "failed" ? t("plan.eyebrow.failed") : t("plan.eyebrow.default"))));
  tt.appendChild(el("div", "pc-title", plan.title || t("plan.title.default")));
  head.appendChild(tt);
  if (plan.confidence) {
    const lvl = planConfLevel(plan.confidence);
    const badge = el("span", "pc-conf " + lvl);
    badge.appendChild(el("span", null, t("plan.confidenceSuffix", (typeof plan.confidence === "string" && isNaN(parseFloat(plan.confidence)) ? plan.confidence : lvl))));
    head.appendChild(badge);
  }
  card.appendChild(head);
  // steps
  const steps = el("div", "pc-steps");
  (plan.steps || []).forEach((s, i) => {
    const sid = s.id || ("s" + (i + 1));
    const row = el("div", "pc-step " + (s.status || "pending")); row.dataset.stepId = sid;
    const chk = el("span", "pc-check"); chk.innerHTML = icon(planStepIcon(s.status), 15); row.appendChild(chk);
    const body = el("div", "pc-step-body");
    body.appendChild(el("div", "pc-step-t", (i + 1) + ". " + (s.title || s.content || t("plan.step.default"))));
    if (s.detail) body.appendChild(el("div", "pc-step-d", s.detail));
    if ((s.deliverables || []).length) {
      const chips = el("div", "pc-chips");
      s.deliverables.forEach(d => { const c = el("span", "pc-chip"); c.appendChild(iconEl("file-text", 11)); c.appendChild(el("span", null, d)); chips.appendChild(c); });
      body.appendChild(chips);
    }
    row.appendChild(body); steps.appendChild(row);
  });
  card.appendChild(steps);
  // footer
  if (status === "draft") {
    const rev = el("div", "pc-revise");
    const ta = el("textarea", "pc-revise-input"); ta.placeholder = t("plan.revise.placeholder"); ta.rows = 1;
    ta.onkeydown = (e) => { if (e.isComposing || e.keyCode === 229) return; if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); const v = ta.value.trim(); if (v) { ta.value = ""; revisePlan(v); } } };
    rev.appendChild(ta); card.appendChild(rev);
    const pa = el("div", "pa");
    const ok = el("button", "approve-btn"); ok.appendChild(iconEl("check", 15)); ok.appendChild(el("span", null, t("plan.approve"))); ok.onclick = approvePlan;
    const no = el("button", "outline-btn small", t("plan.discard")); no.onclick = discardPlan;
    pa.appendChild(ok); pa.appendChild(no); card.appendChild(pa);
  } else {
    const done = (plan.steps || []).filter(s => s.status === "completed").length;
    const total = (plan.steps || []).length;
    const st = el("div", "pc-status " + status);
    st.textContent = status === "executing" ? t("plan.status.executing", done, total)
      : status === "completed" ? t("plan.status.completed", done, total)
        : status === "failed" ? t("plan.status.failed", done, total) : "";
    card.appendChild(st);
  }
  $("#messages").appendChild(card); down();
}
function updatePlanProgress(m) {
  if (S.planReady) { const s = (S.planReady.steps || []).find(x => (x.id) === m.step_id); if (s) s.status = m.status; }
  const card = $("#plan-card-live"); if (!card) return;
  let row = null;
  card.querySelectorAll(".pc-step").forEach(r => { if (r.dataset.stepId === m.step_id) row = r; });
  if (row) {
    row.className = "pc-step " + (m.status || "pending");
    const chk = row.querySelector(".pc-check"); if (chk) chk.innerHTML = icon(planStepIcon(m.status), 15);
  }
  // refresh the "N/total" footer counter while executing
  const foot = card.querySelector(".pc-status.executing");
  if (foot && S.planReady) { const done = (S.planReady.steps || []).filter(s => s.status === "completed").length; const total = (S.planReady.steps || []).length; foot.textContent = t("plan.status.executing", done, total); }
  down();
}
async function approvePlan() {
  if (!S.currentId) return;
  try { await api(`/frames/${S.currentId}/plan/approve`, { method: "POST", body: JSON.stringify({ model: S.defaultModel }) }); }
  catch (e) { hint(t("plan.approveFailed", e.message), true); return; }
  S.planMode = false; const pt = $("#plan-toggle"); if (pt) pt.classList.remove("on");
  S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint(t("plan.autoExecuting"), false, true);
  resumeWatch(S.currentId, S._openGen);  // /plan/approve returns 202 immediately — only the WS unlocks us; watchdog covers a missed terminal event
}
async function discardPlan() {
  if (!S.currentId) return;
  try { await api(`/frames/${S.currentId}/plan/discard`, { method: "POST", body: "{}" }); } catch {}
  const card = $("#plan-card-live"); if (card) card.remove();
  S.planReady = null; S.planStatus = "discarded"; S.planPending = false; hint(t("toast.planDiscarded"));
}
async function revisePlan(changes) {
  if (!S.currentId) return;
  S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint(t("toast.planRevising"), false, true);
  try { await api(`/frames/${S.currentId}/plan/revise`, { method: "POST", body: JSON.stringify({ changes, model: S.defaultModel }) }); resumeWatch(S.currentId, S._openGen); }
  catch (e) { hint(t("toast.reviseFailed", e.message), true); if (S.running) turnDone("failed"); }
}

/* ---------- semantic activity steps (plan / search / env / skill / …) ---------- */
const STEP_ICON = { search: "search", fetch: "globe", plan: "list-check", env: "package", skill: "book", bash: "terminal", edit: "pencil", write: "file-text", read: "file-text", files: "files", artifact: "download", delegate: "users", mcp: "link", fold: "box", code: "terminal" };
function stepIcon(kind) { return STEP_ICON[kind] || "check"; }
function openArt(meta) {
  if (!meta || !meta.artifact_id) return;
  dockOpen();
  openViewer({ id: meta.artifact_id, artifact_id: meta.artifact_id, filename: meta.filename,
               content_type: meta.content_type, size_bytes: meta.size_bytes });
}
// Heuristic: is this string raw binary / a giant base64|hex blob that would be
// ugly dumped into the transcript? (e.g. `print(open(f,'rb').read())`, a base64
// image echoed to stdout). We elide those with a compact placeholder instead.
function looksBinary(s) {
  if (!s) return false;
  const sample = s.slice(0, 4096);
  let ctrl = 0;
  for (let i = 0; i < sample.length; i++) {
    const c = sample.charCodeAt(i);
    if (c === 9 || c === 10 || c === 13) continue;      // tab / LF / CR are fine
    if (c < 32 || c === 127 || c === 0xFFFD) ctrl++;    // control / replacement char
  }
  if (sample.length && ctrl / sample.length > 0.12) return true;
  // one unbroken 1200+ char run of base64/hex with no whitespace → a blob
  return /[A-Za-z0-9+/=]{1200,}/.test(s) || /(?:\\x[0-9a-fA-F]{2}){400,}/.test(s);
}
function binElide(len) {
  const d = el("div", "bin-elide"); d.appendChild(iconEl("file", 13));
  d.appendChild(el("span", null, t("output.binaryElided", bytes(len || 0))));
  return d;
}
function clipPre(text, cls) {
  const s = (text == null ? "" : String(text));
  if (looksBinary(s)) return binElide(s.length);
  const p = el("pre", "s-pre" + (cls ? " " + cls : "")); p.textContent = s.slice(0, 14000); return p;
}
function diffView(oldS, newS) {
  const box = el("div", "s-diff");
  const add = (txt, cls) => { if (!txt) return; const pre = el("pre", "s-pre " + cls); (txt.split("\n")).forEach(l => pre.appendChild(el("div", null, (cls === "d-del" ? "- " : "+ ") + l))); box.appendChild(pre); };
  add(oldS, "d-del"); add(newS, "d-add"); return box;
}
/* ---------- code / command block (Claude-Science styling) ----------
   A framed block with a header strip (language + optional environment /
   status) and syntax accents, plus an optional output reveal. Shared by
   step cards (bash / write / read / code), the notebook panel and
   provenance. Highlighting is self-contained (unique names, emits .tok-*
   spans matching .oc-src CSS) so it is independent of the markdown code
   renderer. */
const _LANG_EXT = { py: "python", pyw: "python", r: "r", sh: "bash", bash: "bash", zsh: "bash", js: "javascript", mjs: "javascript", ts: "typescript", json: "json", yaml: "yaml", yml: "yaml", toml: "toml", md: "markdown", txt: "text", csv: "csv", tsv: "tsv", tex: "latex", sql: "sql" };
function langOf(path) { const m = (path || "").match(/\.([A-Za-z0-9]+)$/); return m ? (_LANG_EXT[m[1].toLowerCase()] || m[1].toLowerCase()) : "text"; }
function baseName(path) { return (path || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() || ""; }
const _OC_KW = {
  python: new Set("False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case".split(" ")),
  bash: new Set("if then else elif fi for while until do done case esac function in select return set unset export local read source echo cd exit".split(" ")),
  r: new Set("if else for while repeat function return break next in TRUE FALSE NULL NA Inf NaN library require".split(" "))
};
const _ocSpan = (cls, s) => '<span class="tok-' + cls + '">' + esc(s) + '</span>';
function _ocLang(l) { l = (l || "").toLowerCase(); return ({ py: "python", python: "python", sh: "bash", bash: "bash", shell: "bash", zsh: "bash", console: "bash", r: "r", rlang: "r" })[l] || l; }
// Small, self-contained tokenizer → escaped HTML with .tok-* spans. Comments
// are '#' only (so '//' inside a URL is never mistaken for a comment).
function _ocHighlight(src, lang) {
  src = String(src == null ? "" : src);
  if (!src) return "";
  const c = _ocLang(lang), kw = _OC_KW[c] || _OC_KW.python;
  const re = /(#[^\n]*)|('''[\s\S]*?'''|"""[\s\S]*?"""|`(?:\\.|[^`\\])*`|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*")|(\b\d[\w.]*\b)|([A-Za-z_$@][\w$]*)/g;
  let out = "", last = 0, m;
  while ((m = re.exec(src))) {
    if (m.index > last) out += esc(src.slice(last, m.index));
    if (m[1]) out += _ocSpan("com", m[1]);
    else if (m[2]) out += _ocSpan("str", m[2]);
    else if (m[3]) out += _ocSpan("num", m[3]);
    else { const w = m[4]; out += (w[0] === "@") ? _ocSpan("fn", w) : (kw.has(w) ? _ocSpan("kw", w) : (src[re.lastIndex] === "(" ? _ocSpan("fn", w) : esc(w))); }
    last = re.lastIndex;
    if (re.lastIndex === m.index) re.lastIndex++;  // never loop on a zero-width match
  }
  return out + esc(src.slice(last));
}
function codeBlock(source, opts) {
  opts = opts || {};
  const lang = opts.lang || "python";
  const wrap = el("div", "os-code" + (opts.term ? " term" : ""));
  const head = el("div", "oc-head");
  const lg = el("span", "oc-lang");
  if (opts.term) lg.appendChild(iconEl("terminal", 12));
  lg.appendChild(el("span", null, opts.langLabel || lang));
  head.appendChild(lg);
  const right = el("div", "oc-right");
  if (opts.status) right.appendChild(el("span", "nbc-status " + opts.status, opts.status));
  if (opts.env) { const ev = el("span", "oc-env"); ev.appendChild(el("span", "oc-env-k", "env")); ev.appendChild(el("span", "oc-env-v", opts.env)); right.appendChild(ev); }
  if (right.children.length) head.appendChild(right);
  wrap.appendChild(head);
  const pre = el("pre", "oc-src"); const code = el("code"); code.innerHTML = _ocHighlight(source, lang); pre.appendChild(code); wrap.appendChild(pre);
  return wrap;
}
// Output block appended into `box`. mode:"reveal" hides it behind a
// "Show output" toggle; otherwise it renders directly beneath the code.
function outputBlock(box, text, opts) {
  opts = opts || {};
  const raw = (text == null ? "" : String(text));
  if (looksBinary(raw)) { box.appendChild(binElide(raw.length)); return; }
  const out = el("pre", "oc-out" + (opts.err ? " err" : "")); out.textContent = raw.slice(0, 14000);
  if (opts.mode === "reveal") {
    const tgl = el("button", "oc-out-tgl"); const t = el("span", null, "Show output"); tgl.appendChild(t); tgl.appendChild(iconEl("chevron-down", 13));
    out.style.display = "none";
    tgl.onclick = () => { const show = out.style.display === "none"; out.style.display = show ? "block" : "none"; tgl.classList.toggle("open", show); t.textContent = show ? "Hide output" : "Show output"; };
    box.appendChild(tgl);
  }
  box.appendChild(out);
}
function stepBody(step) {
  const k = step.kind, inp = step.input || {}, out = step.output || {};
  const box = el("div", "s-inner");
  if (out.error) { box.appendChild(clipPre(out.error, "d-del")); return box; }
  if (k === "search") {
    if (inp.query) box.appendChild(el("div", "s-q", "“" + inp.query + "”"));
    (out.results || []).forEach(r => {
      const row = el("div", "s-res");
      const u = typeof r.url === "string" ? r.url.trim() : "";
      // Only turn a result into a link when its scheme is safe to navigate to;
      // a javascript:/data: URL in an href would run on click (XSS). The scheme
      // test is inlined at the assignment so it acts as the guard on `u`.
      const a = el(/^https?:\/\//i.test(u) ? "a" : "div", "s-res-t");
      a.textContent = r.title || r.url || t("step.search.emptyResult");
      if (/^https?:\/\//i.test(u)) { a.href = u; a.target = "_blank"; a.rel = "noopener noreferrer"; }
      row.appendChild(a);
      if (r.url) row.appendChild(el("div", "s-res-u", r.url));
      if (r.snippet) row.appendChild(el("div", "s-res-s", r.snippet));
      box.appendChild(row);
    });
    if (out.note) box.appendChild(el("div", "s-note", out.note));
    return box;
  }
  if (k === "fetch") { if (inp.url) box.appendChild(el("div", "s-q", inp.url)); if (out.content) box.appendChild(clipPre(out.content)); return box; }
  if (k === "plan") {
    const todos = out.todos || inp.todos || [];
    const ul = el("div", "s-plan");
    todos.forEach(t => {
      const st = t.status || "pending";
      const row = el("div", "s-todo " + st);
      const b = el("span", "s-check"); b.innerHTML = icon(st === "completed" ? "check" : (st === "in_progress" ? "circle-dot" : "circle"), 13); row.appendChild(b);
      row.appendChild(el("span", "s-todo-t", t.content || t.title || "")); ul.appendChild(row);
    });
    box.appendChild(ul); return box;
  }
  if (k === "env") {
    const envs = out.environments || [];
    envs.forEach(e => {
      const row = el("div", "s-env");
      row.appendChild(el("span", "s-env-n", (e.name || "") + " " + (e.python_version || e.r_version || "")));
      const miss = e.missing || [];
      if (miss.length) row.appendChild(el("span", "s-env-m", t("step.env.missing", miss.join(", "))));
      else row.appendChild(el("span", "s-env-ok", t("step.env.ready")));
      box.appendChild(row);
    });
    if ((out.installed || []).length) box.appendChild(el("div", "s-note", t("step.env.installed", out.installed.join(", "))));
    if (out.note) box.appendChild(el("div", "s-note", out.note));
    if (!envs.length && !(out.installed || []).length && (inp.packages || []).length) box.appendChild(el("div", "s-note", (inp.packages || []).join(", ")));
    return box;
  }
  if (k === "skill") {
    if (out.content) { const md = el("div", "md s-skill"); md.innerHTML = renderMd(out.content); box.appendChild(md); }
    else if (out.skills) box.appendChild(el("div", "s-note", t("step.skill.list", (out.skills || []).join(", "))));
    else if (inp.query) box.appendChild(el("div", "s-q", "“" + inp.query + "”"));
    else if (inp.name) box.appendChild(el("div", "s-q", inp.name));
    return box;
  }
  if (k === "bash") {
    if (inp.command) box.appendChild(codeBlock(inp.command, { term: true, lang: "bash", langLabel: "shell" }));
    const o = ((out.stdout || "") + (out.stderr ? ("\n" + out.stderr) : "")).trim();
    if (o) outputBlock(box, o, { err: !!out.stderr && !out.stdout });
    return box;
  }
  if (k === "edit") { box.appendChild(diffView(inp.old_string || "", inp.new_string || "")); return box; }
  if (k === "code") {
    const src = inp.code || inp.source || inp.content || "";
    if (src) box.appendChild(codeBlock(src, { lang: "python", env: inp.environment }));
    const o = ((out.stdout || out.result || "") + (out.stderr ? ("\n" + out.stderr) : "")).toString().trim();
    if (o) outputBlock(box, o, { mode: "reveal", err: !!out.stderr && !out.stdout });
    return box;
  }
  if (k === "write") { if (inp.content != null && inp.content !== "") box.appendChild(codeBlock(inp.content, { lang: langOf(inp.path), langLabel: baseName(inp.path) || undefined })); return box; }
  if (k === "read") { if (out.content != null && out.content !== "") box.appendChild(codeBlock(out.content, { lang: langOf(inp.path), langLabel: baseName(inp.path) || undefined })); return box; }
  if (k === "files") {
    const rows = out.matches || [];
    const lines = rows.map(r => typeof r === "string" ? r : (r.file ? (r.file + ":" + (r.line || "") + "  " + (r.text || "")) : (r.name || JSON.stringify(r))));
    if (lines.length) box.appendChild(clipPre(lines.join("\n"))); else if (inp.pattern) box.appendChild(el("div", "s-q", inp.pattern));
    return box;
  }
  if (k === "artifact") {
    // Auto-captured deliverables → the reference "Saving …" card: a files list,
    // the environment, and a collapsible output JSON with the full metadata.
    const arts = out.artifacts || (out.filename ? [{ filename: out.filename, version_id: out.version_id }] : []);
    const files = (inp.files && inp.files.length ? inp.files : arts.map(a => a.filename)).filter(Boolean);
    const env = inp.environment || "python";
    // Value rendered as text only — el() assigns textContent, never HTML, so an
    // untrusted string (e.g. the environment label) can't inject markup. The
    // pre-built files list uses the node variant instead.
    const kvRow = (label, text) => {
      const r = el("div", "s-kv"); r.appendChild(el("span", "s-k", label));
      r.appendChild(el("div", "s-v", text == null ? "" : String(text)));
      return r;
    };
    const kvNodeRow = (label, node) => {
      const r = el("div", "s-kv"); r.appendChild(el("span", "s-k", label));
      const v = el("div", "s-v"); v.appendChild(node); r.appendChild(v); return r;
    };
    // files rendered as a clickable JSON-style array (each opens the artifact)
    const fl = el("div", "s-files"); fl.appendChild(el("span", "s-brk", "["));
    files.forEach((fn, i) => {
      const meta = arts.find(a => a.filename === fn);
      const line = el("div", "s-frow");
      const nm = el("span", "s-fn" + (meta && meta.artifact_id ? " clk" : ""), '"' + fn + '"');
      if (meta && meta.artifact_id) { nm.title = t("step.artifact.openArtifact"); nm.onclick = () => openArt(meta); }
      line.appendChild(nm);
      if (i < files.length - 1) line.appendChild(el("span", "s-comma", ","));
      fl.appendChild(line);
    });
    fl.appendChild(el("span", "s-brk", "]"));
    box.appendChild(kvNodeRow("files", fl));
    box.appendChild(kvRow("environment", env));
    if (arts.length && (arts[0].artifact_id || arts[0].checksum)) {
      const wrap = el("div", "s-out");
      const tgl = el("button", "s-out-tgl", t("step.artifact.showOutput"));
      const json = el("div", "s-json"); json.textContent = JSON.stringify({ artifacts: arts }, null, 2); json.style.display = "none";
      tgl.onclick = () => { const show = json.style.display === "none"; json.style.display = show ? "block" : "none"; tgl.textContent = show ? t("step.artifact.hideOutput") : t("step.artifact.showOutput"); };
      wrap.appendChild(tgl); wrap.appendChild(json); box.appendChild(wrap);
    }
    // Inline preview of produced images: keep every figure visible at the point in
    // the transcript where it was first produced (this "Saving …" step). The
    // end-of-turn GENERATED gallery then reads as a recap, not the only place the
    // figure ever appears. Persists on reopen since the step output is stored.
    const imgArts = arts.filter(a => {
      const nm = (a.filename || "").toLowerCase(); const ct = a.content_type || "";
      return a.artifact_id && (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm));
    });
    if (imgArts.length) {
      const figs = el("div", "s-figs");
      imgArts.forEach(a => {
        const fig = el("figure", "s-fig"); fig.title = t("step.artifact.openArtifact");
        const im = el("img"); im.src = artUrl({ id: a.artifact_id }); im.alt = a.filename || t("step.fig.altFallback"); im.loading = "lazy";
        fig.appendChild(im);
        if (a.filename) fig.appendChild(el("figcaption", "s-fig-cap", a.filename));
        fig.onclick = () => openArt(a);
        figs.appendChild(fig);
      });
      box.appendChild(figs);
    }
    return box;
  }
  if (k === "delegate" || k === "mcp") {
    if (inp.request) box.appendChild(clipPre(inp.request, "s-cmd"));
    if (out.result != null) box.appendChild(clipPre(typeof out.result === "string" ? out.result : JSON.stringify(out.result, null, 2)));
    return box;
  }
  const dump = (out && Object.keys(out).length) ? out : inp;
  box.appendChild(clipPre(JSON.stringify(dump, null, 2)));
  return box;
}
function buildStepCard(step) {
  const card = el("div", "step step-" + (step.kind || "code"));
  const h = el("div", "s-head");
  const ic = el("span", "s-ic"); h.appendChild(ic);
  h.appendChild(el("span", "s-lbl", step.title || step.kind || t("step.card.defaultTitle")));
  const meta = el("span", "s-meta", ""); h.appendChild(meta);
  const chev = el("span", "s-chev"); chev.innerHTML = icon("chevron-down", 13); h.appendChild(chev);
  const body = el("div", "s-body"); card.appendChild(h); card.appendChild(body);
  h.onclick = () => card.classList.toggle("open");
  const handle = { card, body, meta, ic, step };
  applyStepState(handle);
  return handle;
}
function applyStepState(handle) {
  const { card, body, meta, ic, step } = handle;
  const status = step.status || "running";
  card.classList.toggle("running", status === "running");
  card.classList.toggle("err", status === "error");
  if (status === "running") { ic.innerHTML = icon("loader", 14, "spin"); meta.textContent = ""; }
  else { ic.innerHTML = icon(status === "error" ? "x" : stepIcon(step.kind), 14); meta.textContent = step.summary || (step.output && step.output.error ? t("step.status.failed") : ""); }
  body.innerHTML = ""; body.appendChild(stepBody(step));
  if ((step.kind === "plan" || step.kind === "artifact") && status !== "running") card.classList.add("open");
}
function addLiveStep(m) {
  // Idempotent: if this step is already on screen (reconstructed on reopen, then
  // re-delivered by the WS replay of a still-running turn), patch it in place
  // instead of appending a duplicate card.
  const existing = (S.stepEls || {})[m.step_id];
  if (existing && existing.card && existing.card.isConnected) {
    existing.step.kind = m.kind || existing.step.kind;
    existing.step.title = m.title || existing.step.title;
    if (m.input != null) existing.step.input = m.input;
    if (m.status) existing.step.status = m.status;
    applyStepState(existing); down(); return;
  }
  const st = ensure();
  sealText(st);
  const handle = buildStepCard({ step_id: m.step_id, kind: m.kind, title: m.title, input: m.input, status: m.status || "running" });
  S.stepEls = S.stepEls || {}; S.stepEls[m.step_id] = handle;
  st.wrap.appendChild(handle.card);
  if (st.toolCard && !st.toolCard._demoted) { st.toolCard.classList.add("has-steps"); st.toolCard._demoted = true; const lbl = st.toolCard.querySelector(".lbl"); if (lbl) lbl.textContent = t("step.label.code"); }
  st.md = el("div", "md"); st.wrap.appendChild(st.md); st.text = "";
  down();
}
function updateLiveStep(m) {
  const h = (S.stepEls || {})[m.step_id]; if (!h) return;
  h.step.status = m.status; h.step.output = m.output; h.step.summary = m.summary;
  applyStepState(h); down();
}
function renderStoredStep(s) {
  const handle = buildStepCard(s);
  if (s.step_id) (S.stepEls = S.stepEls || {})[s.step_id] = handle;
  $("#messages").appendChild(handle.card);
}

/* ---------- permission gate (opencode-style tool-call approval) ---------- */
const PERM_SCOPE_KEYS = { once: "perm.scope.once", conversation: "perm.scope.conversation", project: "perm.scope.project", global: "perm.scope.global" };
function permScopeCn(s) { return PERM_SCOPE_KEYS[s] ? t(PERM_SCOPE_KEYS[s]) : s; }
function permActionLine(m) {
  const inp = m.input || {};
  const t = m.tool;
  if (t === "bash") return { mono: true, text: inp.command || m.target || "" };
  if (t === "write_file" || t === "edit_file" || t === "read_file" || t === "save_artifact")
    return { mono: true, text: inp.path || inp.filename || m.target || "" };
  if (t === "web_fetch") return { mono: true, text: inp.url || m.target || "" };
  if (t === "web_search") return { mono: false, text: "“" + (inp.query || m.target || "") + "”" };
  if (t === "env_setup") return { mono: true, text: (inp.packages && inp.packages.length) ? inp.packages.join(" ") : (inp.name || m.target || "") };
  if (t === "mcp_call") return { mono: true, text: (inp.server || "") + "/" + (inp.tool || "") };
  if (t === "delegate") return { mono: false, text: inp.specialist || m.target || "" };
  return { mono: true, text: m.target || "" };
}
function renderPermissionCard(m) {
  S.permCards = S.permCards || Object.create(null);  // null-proto: keys like __proto__ can't pollute
  const prev = S.permCards[m.decision_id];
  if (prev && prev.card && prev.card.isConnected) return;  // idempotent (reconnect re-emit)
  let host; try { host = ensure().wrap; } catch (e) { host = null; }
  if (!host) host = $("#messages");
  const card = el("div", "perm-card");
  const head = el("div", "perm-head");
  head.appendChild(iconEl("lock", 15, "perm-ic"));
  head.appendChild(el("span", "perm-title", m.title || t("perm.title.run", m.tool)));
  if (m.sub_agent) head.appendChild(el("span", "perm-badge", t("perm.badge.subAgent")));
  card.appendChild(head);
  card.appendChild(el("div", "perm-sub", t("perm.sub.approvalNeeded")));
  const act = permActionLine(m);
  if (act.text) card.appendChild(el("div", "perm-detail" + (act.mono ? " mono" : ""), act.text));

  let scope = "conversation";
  card.appendChild(el("div", "perm-lbl", t("perm.lbl.rememberScope")));
  const scRow = el("div", "perm-scope");
  const segs = {};
  const patWrap = el("div", "perm-pat");
  (m.scopes || ["once", "conversation", "project", "global"]).forEach(s => {
    const b = el("button", "perm-seg" + (s === scope ? " active" : ""), permScopeCn(s));
    b.onclick = () => { scope = s; Object.values(segs).forEach(x => x.classList.remove("active")); b.classList.add("active"); patWrap.style.display = (scope === "once") ? "none" : ""; };
    segs[s] = b; scRow.appendChild(b);
  });
  card.appendChild(scRow);

  patWrap.appendChild(el("div", "perm-lbl", t("perm.lbl.rememberRule")));
  const patIn = el("input", "perm-in"); patIn.type = "text";
  patIn.value = (m.suggested_patterns && m.suggested_patterns[0]) || m.target || "*";
  patWrap.appendChild(patIn);
  if (m.suggested_patterns && m.suggested_patterns.length > 1) {
    const chips = el("div", "perm-chips");
    m.suggested_patterns.forEach(p => { const c = el("button", "perm-chip", p); c.onclick = () => { patIn.value = p; }; chips.appendChild(c); });
    patWrap.appendChild(chips);
  }
  card.appendChild(patWrap);

  const fb = el("input", "perm-fb"); fb.type = "text"; fb.placeholder = t("perm.placeholder.denyReason");
  card.appendChild(fb);

  const btns = el("div", "perm-btns");
  const allow = el("button", "perm-allow", t("perm.btn.allow"));
  const deny = el("button", "perm-deny", t("perm.btn.deny"));
  const send = async (ok) => {
    allow.disabled = deny.disabled = true;
    const body = { decision_id: m.decision_id, allow: ok, scope };
    if (scope !== "once") body.pattern = patIn.value.trim() || "*";
    if (!ok && fb.value.trim()) body.message = fb.value.trim();
    try { await api(`/frames/${encodeURIComponent(m.frame_id)}/decision`, { method: "POST", body: JSON.stringify(body) }); }
    catch (e) { allow.disabled = deny.disabled = false; hint(t("toast.submitFailed", e.message), true); return; }
    markPermCard(m.decision_id, ok, scope);
  };
  allow.onclick = () => send(true);
  deny.onclick = () => send(false);
  btns.appendChild(allow); btns.appendChild(deny);
  card.appendChild(btns);

  host.appendChild(card);
  S.permCards[m.decision_id] = { card, allow, deny, resolved: false };
  down();
}
function markPermCard(id, allowed, scope) {
  const reg = S.permCards || {};
  if (!Object.prototype.hasOwnProperty.call(reg, id)) return;  // ignore __proto__/constructor keys
  const h = reg[id]; h.resolved = true;
  if (h.allow) h.allow.disabled = true; if (h.deny) h.deny.disabled = true;
  h.card.classList.add("resolved", allowed ? "allowed" : "denied");
  let st = h.card.querySelector(".perm-status");
  if (!st) { st = el("div", "perm-status"); h.card.appendChild(st); }
  st.textContent = allowed ? ((scope && scope !== "once") ? t("perm.status.allowedScope", permScopeCn(scope)) : t("perm.status.allowed")) : t("perm.status.denied");
}
function resolvePermissionCard(m) {
  const reg = S.permCards || {};
  if (!Object.prototype.hasOwnProperty.call(reg, m.decision_id)) return;  // ignore __proto__/constructor keys
  const h = reg[m.decision_id];
  if (!h.resolved) markPermCard(m.decision_id, !!m.allow, m.scope || null);
}

/* ---------- dashboard ---------- */
async function loadDashboard() {
  await loadProjects();
  let frames = [];
  try { frames = (await api("/frames?limit=50")).filter?.(f => !f.parent_frame_id) || []; } catch {}
  // annotate projects with a live running-session count (derived from frames)
  const rc = {};
  frames.forEach(f => { if (f.running) rc[f.project_id] = (rc[f.project_id] || 0) + 1; });
  S.projects.forEach(p => { p.running_count = rc[p.project_id || p.id] || 0; });
  renderDashProjects();
  renderDashRunning(frames);
  renderDashRecent(frames);
}
function renderDashProjects() {
  const pc = $("#dash-projects"); if (!pc) return; pc.innerHTML = "";
  if (!S.projects.length) pc.appendChild(el("div", "dash-empty", t("dash.projects.empty")));
  S.projects.forEach(p => {
    const row = el("div", "d-row"); const main = el("div", "d-main");
    main.appendChild(el("div", "d-name", p.name || t("dash.project.untitled")));
    if (/example/i.test(p.name || "")) main.appendChild(el("span", "d-tag", "Example"));
    if (p.running_count) { const b = el("span", "d-run"); b.appendChild(el("span", "d-run-dot")); b.appendChild(el("span", null, String(p.running_count))); b.title = t("dash.project.runningCount", p.running_count); main.appendChild(b); }
    row.appendChild(main);
    const n = p.conversation_count || 0;
    row.appendChild(el("div", "d-meta", t(n === 1 ? "dash.meta.session" : "dash.meta.sessions", n)));
    row.appendChild(el("div", "d-meta", ago(p.last_active_at || p.updated_at)));
    row.onclick = () => openProject(p.project_id || p.id);
    pc.appendChild(row);
  });
}
function renderDashRecent(frames) {
  const recent = frames.filter(f => (f.message_count || 0) > 0 || f.name || f.task_summary)
    .sort((a, b) => (new Date(b.updated_at) - new Date(a.updated_at))).slice(0, 10);
  const sc = $("#dash-sessions"); if (!sc) return; sc.innerHTML = "";
  if (!recent.length) sc.appendChild(el("div", "dash-empty", t("dash.sessions.empty")));
  recent.forEach(f => {
    const row = el("div", "d-row"); row.appendChild(el("div", f.running ? "d-dot live" : "d-dot"));
    const main = el("div", "d-main"); main.appendChild(el("div", "d-name", f.name || f.task_summary || t("session.untitled")));
    const pj = S.projects.find(p => (p.project_id || p.id) === f.project_id);
    if (pj) main.appendChild(el("div", "d-sub", pj.name || ""));
    row.appendChild(main);
    if (f.running) { const b = el("span", "d-run"); b.appendChild(el("span", "d-run-dot")); b.appendChild(el("span", null, t("dash.badge.running"))); row.appendChild(b); }
    else row.appendChild(el("div", "d-meta", ago(f.updated_at)));
    row.onclick = () => openConversation(f.id, f.project_id);
    sc.appendChild(row);
  });
}
// Prominent "Running" hero at the top of the home page — surfaces sessions
// still executing in the background so the user can jump back in / resume.
function renderDashRunning(frames) {
  const running = (frames || []).filter(f => f.running)
    .sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  const cnt = $("#dash-running-count");
  if (cnt) { if (running.length) { cnt.textContent = t("dash.running.count", running.length); cnt.classList.remove("hidden"); } else cnt.classList.add("hidden"); }
  const sec = $("#dash-running"); if (!sec) return;
  sec.innerHTML = "";
  if (!running.length) { sec.classList.add("hidden"); return; }
  sec.classList.remove("hidden");
  running.forEach(f => {
    const card = el("div", "run-card");
    const body = el("div", "run-body");
    body.appendChild(el("div", "run-title", f.name || f.task_summary || t("session.untitled")));
    const pj = S.projects.find(p => (p.project_id || p.id) === f.project_id);
    const sub = (f.task_summary && f.task_summary !== f.name) ? f.task_summary : (pj ? pj.name : "");
    if (sub) body.appendChild(el("div", "run-sub", sub));
    card.appendChild(body);
    const foot = el("div", "run-foot");
    const badge = el("span", "run-badge"); badge.appendChild(el("span", "run-dot")); badge.appendChild(el("span", null, t("dash.badge.running")));
    foot.appendChild(badge); foot.appendChild(el("span", "run-when", t("dash.running.activeNow")));
    card.appendChild(foot);
    card.title = t("session.badge.runningTip");
    card.onclick = () => openConversation(f.id, f.project_id);
    sec.appendChild(card);
  });
}
// Lightweight poll while the home page is open so a background turn that was
// started elsewhere (or before this page loaded) shows up live without a WS sub.
async function refreshDashRunning() {
  if ($("#dashboard").classList.contains("hidden")) { stopDashPoll(); return; }
  let frames = [];
  try { frames = (await api("/frames?limit=50")).filter?.(f => !f.parent_frame_id) || []; } catch { return; }
  if ($("#dashboard").classList.contains("hidden")) return;
  renderDashRunning(frames);
}
function startDashPoll() { stopDashPoll(); S._dashPoll = setInterval(refreshDashRunning, 4000); }
function stopDashPoll() { if (S._dashPoll) { clearInterval(S._dashPoll); S._dashPoll = null; } }

/* ---------- projects ---------- */
async function loadProjects() { try { const d = await api("/projects?limit=100&offset=0"); S.projects = (d && d.projects) || []; } catch { S.projects = []; } }
function renderProjMenu() {
  $("#proj-current").textContent = S.project ? projName(S.project) : t("proj.current.allSessions");
  const m = $("#proj-menu"); m.innerHTML = "";
  const home = el("div", "proj-item"); const hg = el("span"); hg.style.cssText = "display:flex;align-items:center;gap:6px"; hg.appendChild(iconEl("arrow-left", 16)); hg.appendChild(el("span", null, t("proj.menu.allProjects"))); home.appendChild(hg); home.onclick = () => { $("#proj-menu").classList.add("hidden"); showDashboard(); }; m.appendChild(home);
  S.projects.forEach(p => {
    const it = el("div", "proj-item"); it.appendChild(el("span", null, (p.name || t("proj.fallbackName")).slice(0, 26)));
    const del = el("span", "del"); del.appendChild(iconEl("trash-2", 15)); del.onclick = (e) => { e.stopPropagation(); if (confirm(t("proj.delete.confirm"))) deleteProject(p.project_id || p.id); };
    it.appendChild(del); it.onclick = () => selectProject(p.project_id || p.id); m.appendChild(it);
  });
}
const projName = (id) => { const p = S.projects.find(x => (x.project_id || x.id) === id); return p ? (p.name || t("proj.fallbackName")) : t("proj.fallbackName"); };
function selectProject(id) { S.project = id; $("#proj-menu").classList.add("hidden"); renderProjMenu(); loadSessions(); }
async function openProject(id) {
  await loadProjects(); S.project = id; showWorkspace(); await loadSessions(); renderProjMenu();
  const ss = S.sessions.filter(f => f.project_id === id);
  if (ss.length) openConversation(ss[0].id, id); else newSession();
}
async function createProject(name, description, context) {
  const p = await api("/projects", { method: "POST", body: JSON.stringify({ name, description, context }) });
  await loadProjects(); openProject(p.project_id || p.id);
}
async function deleteProject(id) { try { await api("/projects/" + id, { method: "DELETE" }); } catch {} await loadProjects(); if (S.project === id) showDashboard(); else renderProjMenu(); }

/* ---------- sessions ---------- */
async function loadSessions() {
  try { const f = await api("/frames?limit=100"); S.sessions = (Array.isArray(f) ? f : []).filter(x => !x.parent_frame_id); } catch { S.sessions = []; }
  await loadFolders();
  renderSessions(); syncCurrentTitle(); if (!$("#dashboard").classList.contains("hidden")) loadDashboard();
}
// Keep the open conversation's header in sync with the server title (e.g. the
// background-generated summary that replaces the first-message placeholder).
// Never stomp a title the user is actively editing.
function syncCurrentTitle() {
  if (!S.currentId) return;
  const f = S.sessions.find(x => x.id === S.currentId); if (!f) return;
  const ct = $("#conv-title"); if (ct && document.activeElement === ct) return;
  const name = f.name || f.task_summary || t("conv.title.default");
  if (name !== S._titleName) { S._titleName = name; setTitle(name); }
}
async function loadFolders() { const pid = S.project; if (!pid) { S.folders = []; S._foldersFor = null; return; } if (S._foldersFor === pid && S.folders) return; /* cache per-project: don't refetch on every frame_update */ try { const d = await api(`/projects/${pid}/folders`); S.folders = (d && d.folders) || []; S._foldersFor = pid; } catch { S.folders = []; } }
function invalidateFolders() { S._foldersFor = null; }
function dateBucket(iso) { const ts = new Date(iso).getTime(); if (isNaN(ts)) return t("date.bucket.older"); const d = (Date.now() - ts) / 86400000; if (d < 1) return t("date.bucket.today"); if (d < 2) return t("date.bucket.yesterday"); if (d < 7) return t("date.bucket.thisWeek"); return t("date.bucket.older"); }
function sessionRow(f) {
  const d = el("div", "session" + (f.id === S.currentId ? " active" : "") + (f.running ? " running" : "")); d.appendChild(el("div", "s-dot"));
  d.appendChild(el("div", "s-name", f.name || f.task_summary || t("session.untitled")));
  if (f.running) { const b = el("span", "s-badge run", t("dash.badge.running")); b.title = t("session.badge.runningTip"); d.appendChild(b); }
  else if (f.kernel_alive) { const b = el("span", "s-badge live"); b.title = t("session.badge.liveTip"); d.appendChild(b); }
  const menu = el("button", "s-menu"); menu.appendChild(iconEl("more-horizontal", 16)); menu.title = t("session.menu.tip"); menu.onclick = (e) => { e.stopPropagation(); sessionMenu(menu, f.id); }; d.appendChild(menu);
  d.onclick = () => openConversation(f.id, f.project_id); return d;
}
function renderSessions() {
  const list = $("#session-list"); if (!list) return; list.innerHTML = "";
  let ss = S.sessions; if (S.project) ss = ss.filter(f => f.project_id === S.project);
  ss = ss.slice().sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
  // folder toolbar
  if (S.project) { const tb = el("div", "folder-tools"); const nf = el("button", "side-mini", t("session.newFolder")); nf.onclick = newFolder; tb.appendChild(nf); list.appendChild(tb); }
  if (!ss.length && !(S.folders || []).length) { list.appendChild(el("div", "side-label", t("session.empty.label"))); return; }
  S._folderCollapsed = S._folderCollapsed || {};
  // folders first
  (S.folders || []).forEach(fold => {
    const inFold = ss.filter(f => f.folder_id === fold.folder_id);
    const head = el("div", "folder-head"); const collapsed = S._folderCollapsed[fold.folder_id];
    const chev = el("span", "folder-chev"); chev.innerHTML = icon(collapsed ? "chevron-right" : "chevron-down", 14); head.appendChild(chev);
    head.appendChild(iconEl("folder", 14)); head.appendChild(el("span", "folder-name", fold.name)); head.appendChild(el("span", "folder-count", String(inFold.length)));
    const menu = el("button", "s-menu"); menu.appendChild(iconEl("more-horizontal", 15)); menu.onclick = (e) => { e.stopPropagation(); folderMenu(menu, fold); }; head.appendChild(menu);
    head.onclick = () => { S._folderCollapsed[fold.folder_id] = !collapsed; renderSessions(); };
    list.appendChild(head);
    if (!collapsed) inFold.forEach(f => { const r = sessionRow(f); r.style.paddingLeft = "20px"; list.appendChild(r); });
  });
  // ungrouped, by date
  const ungrouped = ss.filter(f => !f.folder_id || !(S.folders || []).some(x => x.folder_id === f.folder_id));
  let lastBucket = null;
  ungrouped.forEach(f => { const b = dateBucket(f.updated_at); if (b !== lastBucket) { lastBucket = b; list.appendChild(el("div", "side-label", b)); } list.appendChild(sessionRow(f)); });
}
async function newFolder() {
  const name = prompt(t("folder.new.prompt")); if (!name || !S.project) return;
  try { await api(`/projects/${S.project}/folders`, { method: "POST", body: JSON.stringify({ name }) }); invalidateFolders(); await loadFolders(); await loadSessions(); } catch (e) { hint(t("folder.create.failed", e.message), true); }
}
function folderMenu(anchor, fold) {
  openMenu(anchor, [
    { label: t("folder.menu.rename"), icon: "pencil", onClick: async () => { const n = prompt(t("folder.rename.prompt"), fold.name); if (!n) return; try { await api(`/folders/${fold.folder_id}`, { method: "PATCH", body: JSON.stringify({ name: n }) }); invalidateFolders(); await loadFolders(); await loadSessions(); } catch {} } },
    { label: t("folder.menu.delete"), icon: "trash-2", danger: true, onClick: async () => { if (!confirm(t("folder.delete.confirm", fold.name))) return; try { await api(`/folders/${fold.folder_id}`, { method: "DELETE" }); invalidateFolders(); await loadFolders(); await loadSessions(); } catch {} } },
  ]);
}
async function assignFolder(fid, folder_id) { try { await api(`/frames/${fid}/folder`, { method: "POST", body: JSON.stringify({ folder_id }) }); await loadSessions(); hint(folder_id ? t("folder.assigned.in") : t("folder.assigned.out")); } catch (e) { hint(t("folder.move.failed", e.message), true); } }
async function newSession() {
  try { const f = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: S.project || undefined, model: S.defaultModel }) });
    await loadSessions(); openConversation(f.id, S.project); $("#composer").focus();
  } catch (e) { hint(t("folder.create.failed", e.message), true); }
}
// Safety net for the "recovering" (resume) state. We lock the composer while a
// turn keeps running server-side and normally unlock it when that turn's terminal
// frame_update arrives over the WS. But that signal can be missed for good: the WS
// subscribe can race the turn finishing (its buffered replay is gated on the turn
// still running), the socket may be mid-reconnect when the turn ends, or the server
// may have restarted and dropped the in-flight job. Without a fallback the composer
// stays disabled and "正在恢复…" spins forever. So while we still believe a turn is
// running, poll the authoritative status; the moment it is no longer running yet we
// haven't unlocked, re-open the conversation to load the final message and release
// the composer. is_running() never reports a live turn as done (jobs aren't pruned
// until finished), so this only fires once the turn has genuinely ended.
function resumeWatch(fid, gen) {
  clearTimeout(S._resumeTimer);
  // Token identifying THIS locked episode. turnDone (and a newly-armed watch) bump
  // it, so a tick already parked on its await when the turn ends can't wake up and
  // act on a DIFFERENT (next) turn's live state — it just sees a stale token and exits.
  const tok = S._resumeTok = (S._resumeTok || 0) + 1;
  const stale = () => tok !== S._resumeTok || gen !== S._openGen || S.currentId !== fid || !S.running;
  const tick = async () => {
    if (stale()) return;  // switched session/turn, or already unlocked by the WS
    let running = true;
    try { const stt = await api(`/frames/${fid}/status`); running = !!(stt && stt.running); }
    catch { running = true; }  // transient error — keep waiting and retry
    if (stale()) return;  // re-check AFTER the await: the turn may have ended (and a new one begun) while parked
    if (!running) { openConversation(fid, S.project); return; }  // turn ended but we missed its terminal event — resync
    S._resumeTimer = setTimeout(tick, 2000);
  };
  S._resumeTimer = setTimeout(tick, 2000);
}
async function openConversation(fid, pid) {
  if (pid && pid !== S.project) { S.project = pid; S._projArtFor = null; }  // new project → drop cached project-wide Files
  // reflect the open conversation in the address bar so it's a persistent,
  // shareable, reload-safe location (no-op when we're already at this path, e.g.
  // during deep-link hydration or a resume-resync re-open).
  navURL(framePath(fid, pid || S.project || (S.sessions.find(x => x.id === fid) || {}).project_id));
  showWorkspace(); showConv(); renderProjMenu();
  if (mqMobile.matches) setSidebar(true);  // collapse the mobile drawer so the conversation is visible
  S.currentId = fid; $("#messages").innerHTML = ""; S.stream = null;
  S.running = false; enableComposer(true); $("#cancel-btn").classList.add("hidden");
  clearTimeout(S._resumeTimer);  // stop any resume-watchdog from the previously open session
  const gen = S._openGen = (S._openGen || 0) + 1;  // guard async continuations against fast session-switching
  S.cells = []; S.kernels = []; S.liveCells = []; S._liveCell = null; S.dockArtifact = null; S.kernelFilter = null;
  S._tbl = {}; invalidateKernelCache();  // drop the prior session's table + kernel-state caches
  S.openTabs = []; S.activeTab = "notebook"; S.provMode = false; S.lineage = null; S._lineageFor = null;
  S.stepEls = {};  // fresh step registry so reopen-then-replay dedupes by step_id
  S.permCards = Object.create(null);  // fresh permission-card registry (null-proto; drop cards from the prior conversation)
  S.planReady = null; S.planStatus = null; S.planPending = false;  // fresh plan state per session
  S.annotations = []; closeAnnotDraft(); closeAnnotPop(); updateAnnotBadge();
  edacTeardown(); S._editing = null;  // stop any live editor autocomplete + clear edit state when switching sessions
  _molTeardown(); $("#dock-viewer").innerHTML = ""; renderDockTabs();
  if (!S.sessions.length) await loadSessions(); else renderSessions();
  const f = S.sessions.find(x => x.id === fid);
  S._titleName = (f && (f.name || f.task_summary)) || t("conv.title.default"); setTitle(S._titleName);
  try { const fb = await api(`/frames/${fid}/feedback`); S.feedback = (fb && fb.feedback) || {}; } catch { S.feedback = {}; }
  let msgCount = 0;
  try {
    const [d, sd] = await Promise.all([
      api(`/frames/${fid}/messages?from=0&limit=300`),
      api(`/frames/${fid}/steps`).catch(() => ({ steps: [] })),
    ]);
    if (gen !== S._openGen) return;
    const msgs = (d && d.messages) || []; msgCount = msgs.length;
    const steps = (sd && sd.steps) || [];
    // interleave stored messages + activity steps by timestamp (steps carry seq
    // for a stable tie-break) so a reopened session re-renders the full activity.
    const items = [];
    msgs.forEach(mm => items.push({ t: new Date(mm.created_at).getTime() || 0, seq: 1e15, kind: "msg", v: mm }));
    steps.forEach(s => items.push({ t: s.created_at || 0, seq: s.seq || 0, kind: "step", v: s }));
    items.sort((a, b) => (a.t - b.t) || (a.seq - b.seq));
    items.forEach(it => { if (it.kind === "msg") renderStored(it.v); else renderStoredStep(it.v); });
  } catch {}
  if (gen !== S._openGen) return;
  if (!msgCount) renderEmptySession();
  loadArtifacts(fid); loadExecutionLog(fid); loadAnnotations(fid); down(true); updateJumpPill();
  // Resume: subscribe AFTER history renders so a replayed in-flight turn streams
  // below it. If a turn is still running server-side (survived our last close),
  // lock the composer and let the WS replay rebuild the live stream + notebook.
  try {
    const stt = await api(`/frames/${fid}/status`);
    if (gen !== S._openGen) return;
    if (stt && stt.running) { S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint(t("conv.resuming.hint"), false, true); resumeWatch(fid, gen); }
  } catch {}
  // Resume a pending/executing/completed plan review card (drafts survive a reopen).
  try {
    const pj = await api(`/frames/${fid}/plan`);
    if (gen !== S._openGen) return;
    if (pj && pj.plan && pj.status && pj.status !== "discarded") renderPlanCard(pj.plan, pj.status);
  } catch {}
  sub(fid);
}
const STARTERS = [
  { t: t("starter.litReview.title"), p: t("starter.litReview.prompt") },
  { t: t("starter.dataAnalysis.title"), p: t("starter.dataAnalysis.prompt") },
  { t: t("starter.proteinModel.title"), p: t("starter.proteinModel.prompt") },
  { t: t("starter.phylo.title"), p: t("starter.phylo.prompt") },
];
function renderEmptySession() {
  const m = $("#messages"); const wrap = el("div", "empty-session");
  wrap.appendChild(el("div", "es-title", t("empty.title")));
  wrap.appendChild(el("div", "es-sub", t("empty.sub")));
  const chips = el("div", "es-chips");
  STARTERS.forEach(s => { const chip = el("button", "es-chip"); chip.appendChild(el("div", "es-chip-t", s.t)); chip.appendChild(el("div", "es-chip-p", s.p)); chip.onclick = () => { const c = $("#composer"); c.value = s.p; grow(); c.focus(); }; chips.appendChild(chip); });
  wrap.appendChild(chips); m.appendChild(wrap);
}
function renderStored(m) {
  const text = Array.isArray(m.content) ? m.content.map(b => (b && b.text) || "").join("") : (m.content || "");
  if (!text.trim()) return;
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  if (m.role === "user") { const b = el("div", "bubble"); b.textContent = text; w.appendChild(b); }
  else { const md = el("div", "md"); md.innerHTML = renderMd(text); w.appendChild(md); }
  $("#messages").appendChild(w);
  if (m.role !== "user") addMsgActions(w, text);
}

/* ---------- session title / actions ---------- */
async function commitTitle() {
  if (!S.currentId) return;
  const name = ($("#conv-title").value || "").trim();
  if (!name || name === S._titleName) { setTitle(S._titleName); return; }
  try { await api("/frames/" + S.currentId, { method: "PATCH", body: JSON.stringify({ name }) }); S._titleName = name; setTitle(name); loadSessions(); }
  catch (e) { setTitle(S._titleName); hint(t("toast.renameFailed", e.message), true); }
}
function sessionMenu(anchor, fid) {
  openMenu(anchor, [
    { label: t("folder.menu.rename"), icon: "pencil", onClick: () => renameFrame(fid) },
    { label: t("sessionMenu.duplicate"), icon: "copy", onClick: () => duplicateSession(fid) },
    { label: t("sessionMenu.moveToFolder"), icon: "folder", onClick: () => moveToFolderAt(anchor, fid) },
    { label: t("sessionMenu.exportMarkdown"), icon: "download", onClick: () => exportSession(fid) },
    { sep: true },
    { label: t("common.delete"), icon: "trash-2", danger: true, onClick: () => { if (confirm(t("confirm.deleteSession"))) deleteSession(fid); } },
  ]);
}
function moveToFolderAt(anchor, fid) {
  const folders = S.folders || [];
  const items = [{ label: t("moveFolder.removeFromFolder"), icon: "x", onClick: () => assignFolder(fid, null) }];
  folders.forEach(fo => items.push({ label: fo.name, icon: "folder", onClick: () => assignFolder(fid, fo.folder_id) }));
  items.push({ sep: true });
  items.push({ label: t("moveFolder.newFolderAndMove"), icon: "plus", onClick: async () => { const n = prompt(t("folder.new.prompt")); if (!n || !S.project) return; try { const r = await api(`/projects/${S.project}/folders`, { method: "POST", body: JSON.stringify({ name: n }) }); await assignFolder(fid, r.folder_id); } catch {} } });
  openMenu(anchor, items);
}
async function exportSession(fid) {
  try {
    const [d, arts] = await Promise.all([
      api(`/frames/${fid}/messages?from=0&limit=500`),
      api(`/frames/${fid}/artifacts`).catch(() => []),
    ]);
    const f = S.sessions.find(x => x.id === fid) || {};
    let md = "# " + (f.name || f.task_summary || t("conv.title.default")) + "\n\n";
    (d.messages || []).forEach(m => { const who = m.role === "user" ? "🧑 User" : "🤖 Assistant"; const txt = Array.isArray(m.content) ? m.content.map(b => b.text || "").join("") : (m.content || ""); md += `## ${who}\n\n${txt}\n\n`; });
    if ((arts || []).length) { md += "## 产物 Artifacts\n\n"; arts.forEach(a => md += `- ${a.filename} (${a.content_type || ""})\n`); }
    const blob = new Blob([md], { type: "text/markdown" }); const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = (f.name || f.task_summary || "session").replace(/[^\w一-龥-]+/g, "_") + ".md"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000); hint(t("toast.exportedMarkdown"));
  } catch (e) { hint(t("toast.exportFailed", e.message), true); }
}
async function renameFrame(fid) {
  const f = S.sessions.find(x => x.id === fid);
  if (fid !== S.currentId) await openConversation(fid, f && f.project_id);
  const ct = $("#conv-title"); ct.focus(); ct.select();
}
async function deleteSession(fid) {
  try { await api("/frames/" + fid, { method: "DELETE" }); } catch (e) { hint(t("toast.deleteFailed", e.message), true); return; }
  const wasCurrent = fid === S.currentId; await loadSessions();
  if (wasCurrent) { let ss = S.sessions; if (S.project) ss = ss.filter(f => f.project_id === S.project); if (ss.length) openConversation(ss[0].id, ss[0].project_id); else { S.currentId = null; $("#messages").innerHTML = ""; setTitle(t("conv.title.default")); S.artifacts = []; renderFilesGrid(); } }
}
async function duplicateSession(fid) {
  const f = S.sessions.find(x => x.id === fid) || {};
  try {
    const nf = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: f.project_id || S.project || undefined, model: S.defaultModel }) });
    const nm = (f.name || f.task_summary || t("conv.title.default")) + t("session.duplicateSuffix");
    try { await api("/frames/" + nf.id, { method: "PATCH", body: JSON.stringify({ name: nm }) }); } catch {}
    await loadSessions(); openConversation(nf.id, f.project_id);
  } catch (e) { hint(t("toast.duplicateFailed", e.message), true); }
}

/* ---------- context menu ---------- */
function openMenu(anchor, items) {
  closeMenu();
  const m = el("div", "ctx-menu");
  items.forEach(it => {
    if (it.sep) { m.appendChild(el("div", "ctx-sep")); return; }
    const b = el("button", "ctx-item" + (it.danger ? " danger" : ""));
    if (it.icon) { const ic = el("span", "ic"); ic.innerHTML = icon(it.icon, 16); b.appendChild(ic); }
    b.appendChild(el("span", null, it.label));
    b.onclick = (e) => { e.stopPropagation(); closeMenu(); it.onClick && it.onClick(); }; m.appendChild(b);
  });
  document.body.appendChild(m); S._menu = m;
  const r = anchor.getBoundingClientRect();
  m.style.top = (r.bottom + 4) + "px";
  m.style.left = Math.max(8, Math.min(r.left, window.innerWidth - m.offsetWidth - 8)) + "px";
  setTimeout(() => document.addEventListener("mousedown", menuOutside), 0);
}
function menuOutside(e) { if (S._menu && !S._menu.contains(e.target)) closeMenu(); }
function closeMenu() { if (S._menu) { S._menu.remove(); S._menu = null; document.removeEventListener("mousedown", menuOutside); } }

/* ---------- message actions (F7) ---------- */
function addMsgActions(wrap, text) {
  if (!wrap || wrap.querySelector(".msg-actions")) return;
  const row = el("div", "msg-actions");
  const copy = el("button", null); copy.title = t("msgAction.copy"); copy.innerHTML = icon("copy", 16);
  copy.onclick = () => { try { navigator.clipboard && navigator.clipboard.writeText(text || ""); } catch {} copy.innerHTML = icon("check", 16); setTimeout(() => copy.innerHTML = icon("copy", 16), 1200); };
  const key = fbKey(text);
  const cur = (S.feedback || {})[key] || null;
  const tup = el("button", cur === "up" ? "on" : null); tup.title = t("msgAction.thumbsUp"); tup.innerHTML = icon("thumbs-up", 16);
  const tdn = el("button", cur === "down" ? "on" : null); tdn.title = t("msgAction.thumbsDown"); tdn.innerHTML = icon("thumbs-down", 16);
  tup.onclick = () => { const on = !tup.classList.contains("on"); tup.classList.toggle("on", on); tdn.classList.remove("on"); sendFeedback(key, on ? "up" : null); };
  tdn.onclick = () => { const on = !tdn.classList.contains("on"); tdn.classList.toggle("on", on); tup.classList.remove("on"); sendFeedback(key, on ? "down" : null); };
  const edit = el("button", null); edit.title = t("common.edit"); edit.innerHTML = icon("pencil", 16); edit.onclick = () => { const c = $("#composer"); c.value = text || ""; grow(); c.focus(); };
  row.appendChild(copy); row.appendChild(tup); row.appendChild(tdn); row.appendChild(edit);
  wrap.appendChild(row);
}
function fbKey(text) { let h = 0; const s = (text || "").slice(0, 400); for (let i = 0; i < s.length; i++) { h = (h * 31 + s.charCodeAt(i)) | 0; } return "m" + (h >>> 0).toString(36); }
function sendFeedback(key, rating) {
  if (!S.currentId) return;
  S.feedback = S.feedback || {}; if (rating) S.feedback[key] = rating; else delete S.feedback[key];
  api("/frames/" + S.currentId + "/feedback", { method: "POST", body: JSON.stringify({ key, rating }) }).catch(() => {});
  hint(rating === "up" ? t("toast.feedbackUp") : rating === "down" ? t("toast.feedbackDown") : t("toast.feedbackCancelled"));
}
async function cancelTurn() { if (!S.currentId) return; try { await api("/frames/" + S.currentId + "/cancel", { method: "POST" }); } catch {} turnDone("cancelled"); }

/* ---------- send ---------- */
async function send(text, opts) {
  text = (text || "").trim(); opts = opts || {};
  if (S.running) return;
  const anns = openAnnotations();                 // pinned image comments to ride along
  if (!text && !anns.length) return;              // nothing to send
  const planNow = S.planMode && !opts.execute;
  const exploreNow = S.exploreMode && !planNow && !opts.execute;
  // Explicit skill invocation: a "/skillname" token (from the / autocomplete or
  // the Skills settings tab) is turned into a hard directive so the skill is
  // actually loaded — left as plain text the model routinely skips
  // host.load_skill and the skill never runs.
  let skillDirective = "";
  if (!planNow) {
    try {
      const cat = await loadSkillsCatalog();
      const names = new Set((cat || []).map(s => String(s.name).toLowerCase()));
      const hits = [];
      text.replace(/(^|\s)\/([A-Za-z0-9][\w:-]*)/g, (m, _p, nm) => { if (names.has(nm.toLowerCase()) && !hits.includes(nm)) hits.push(nm); return m; });
      if (hits.length) skillDirective = "\n\n" + hits.map(n => t("skill.invokeDirective", n)).join("\n");
    } catch {}
  }
  if (!S.currentId) { const f = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: S.project || undefined, model: S.defaultModel }) }); S.currentId = f.id; sub(f.id); await loadSessions(); }
  const g = $(".generated"); if (g) g.remove();
  const es = $(".empty-session"); if (es) es.remove();
  const w = el("div", "msg user"); const b = el("div", "bubble"); b.textContent = text || t("send.imageAnnotationFallback"); w.appendChild(b);
  if (anns.length) w.appendChild(annotAttachment(anns));
  $("#messages").appendChild(w); down(true);
  let payload = text;
  if (planNow) {
    const oldCard = $("#plan-card-live"); if (oldCard) oldCard.remove(); S.planReady = null; S.planStatus = null;
    payload = "[计划模式] 请先不要执行、不要调用任何工具。为下面的任务制定一个结构化执行计划，并只输出两部分：\n"
      + "1) 一段简短的方案说明（散文，说明你选择的目标/思路与分析主线）；\n"
      + "2) 紧接着一个 ```json 代码块，严格使用如下结构：\n"
      + '{"title":"计划标题","rationale":"一句话理由","confidence":"high|medium|low","steps":[{"id":"s1","title":"步骤标题","detail":"这一步做什么","deliverables":["产出文件名.csv"]}]}\n'
      + "每个步骤要有唯一 id、清晰标题、简要说明，以及该步预期产出的结果文件名列表。等待用户批准后再执行。\n\n任务：" + text;
    S.planPending = true;
  }
  if (skillDirective) payload += skillDirective;
  S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint(t("toast.running"), false, true);
  $("#composer").value = ""; grow();
  const annIds = anns.map(x => x.id);
  if (annIds.length) { setLocalAnnotationStatus(annIds, "sent"); refreshAllStages(); updateAnnotBadge(); }
  sub(S.currentId);  // guarantee this client is subscribed BEFORE the POST spawns the
                     // turn thread. On the FIRST turn opened via newSession(), S.currentId
                     // is already set so the block above is skipped and openConversation's
                     // late sub() may not have run yet — without this, run_message() emits
                     // text_reset/text_chunk before rid is in conn.subs and broadcast()
                     // drops them (server replay is gated on is_running, which is already
                     // false once the blocking POST returns). Idempotent set add.
  try {
    await api(`/frames/${S.currentId}/message`, { method: "POST", body: JSON.stringify({ input_data: { request: payload }, model: S.defaultModel, plan: planNow, explore: exploreNow, annotation_ids: annIds }) });
    // The optimistic status above clears the badge immediately; reload once the turn POST finishes to reconcile with the server.
    if (annIds.length) { try { await loadAnnotations(S.currentId); } catch {} refreshAllStages(); updateAnnotBadge(); }
  }
  catch (e) {
    if (annIds.length) {
      // POST failed → annotations were never consumed server-side. Reconcile with the server
      // if reachable; if that reload also fails, revert the optimistic "sent" flip locally so
      // the pending comments stay visible for a retry instead of vanishing from the composer.
      const reloaded = await loadAnnotations(S.currentId);
      if (!reloaded) setLocalAnnotationStatus(annIds, "open");
      refreshAllStages(); updateAnnotBadge();
    }
    hint(t("toast.sendFailed", e.message), true);
  }
  if (S.running) turnDone("completed");
  loadSessions();
}
/* compact "N annotations attached" block under a user message bubble */
function annotAttachment(anns) {
  const box = el("div", "annot-attach");
  box.appendChild(iconEl("message-square", 13));
  box.appendChild(el("span", "annot-attach-t", t("annot.attachCount", anns.length)));
  const list = el("div", "annot-attach-list");
  anns.forEach(an => { const r = el("div", "annot-attach-row"); r.appendChild(el("span", "annot-attach-pin", String(an.number))); r.appendChild(el("span", "annot-attach-file", (an.artifact_name || "artifact"))); r.appendChild(el("span", "annot-attach-body", "· " + (an.body || ""))); list.appendChild(r); });
  box.appendChild(list);
  return box;
}

/* ---------- api-key banner (C3) ---------- */
async function refreshKeyBanner() {
  let me = {}; try { me = await api("/me"); } catch {}
  let b = $("#key-banner");
  if (me && me.has_api_key === false) {
    if (!b) { b = el("div", "key-banner"); b.id = "key-banner"; document.body.appendChild(b); }
    b.innerHTML = ""; b.appendChild(iconEl("alert-triangle", 15));
    b.appendChild(el("span", null, t("key.banner.notConfigured")));
    const link = el("button", "kb-link", t("key.banner.goConfigure")); link.onclick = () => openCust("models"); b.appendChild(link);
    document.body.classList.add("has-key-banner");
  } else if (b) { b.remove(); document.body.classList.remove("has-key-banner"); }
}
/* ---------- models ---------- */
async function loadModels() {
  try { const m = await api("/models"); const groups = (m && m.models) || {}; S.models = Object.values(groups).flat(); S.defaultModel = m.default_model_id || (S.models[0] && S.models[0].id);
    const sel = $("#model-select"); sel.innerHTML = "";
    if (!S.models.length) { const o = el("option", null, t("models.none")); o.value = ""; sel.appendChild(o); }
    S.models.forEach(md => { const o = el("option", null, md.name || md.id); o.value = md.id; if (md.id === S.defaultModel) o.selected = true; sel.appendChild(o); });
    sel.onchange = async () => { S.defaultModel = sel.value; try { await api("/models/default", { method: "PUT", body: JSON.stringify({ model_id: sel.value }) }); } catch {} };
  } catch { $("#model-select").innerHTML = "<option>" + t("models.none") + "</option>"; }
}

/* ---------- artifacts (inline + files) ---------- */
async function loadArtifacts(id) {
  let a = []; try { a = await api(`/frames/${id}/artifacts`); } catch { a = []; }
  if (id !== S.currentId) return;
  a = Array.isArray(a) ? a : [];
  // Bust the URL cache of any artifact whose latest version changed since we last
  // saw it (covers overwrite-in-place edits even if the live event was missed).
  const seen = S._artVer || (S._artVer = {});
  a.forEach(x => { const v = x.version_id || x.checksum; if (v && seen[x.id] && seen[x.id] !== v) (S._artBust = S._artBust || {})[x.id] = v; if (v) seen[x.id] = v; });
  S.artifacts = a; renderConversationArtifacts();
  if (S.dock.open && S.activeTab === "files") {
    // In project scope, a conversation switch may have crossed into another
    // project — reload the aggregate (cache was invalidated on project change).
    if (S.filesScope === "project") loadProjectArtifacts().then(renderFilesGrid);
    else renderFilesGrid();
  }
}
function dataCol(iconName, label) { const c = el("div", "col"); const ic = el("span", "ic"); ic.innerHTML = icon(iconName, 12); c.appendChild(ic); c.appendChild(el("span", null, label)); return c; }
function fillDataPreview(d, a) {
  fetch(artUrl(a)).then(r => r.text()).then(txt => {
    let rows = null; try { rows = parseTable(txt, a); } catch {}
    d.innerHTML = "";
    if (!rows || !rows.length) { d.appendChild(dataCol("table", "data")); return; }
    const cols = Object.keys(rows[0]);
    d.appendChild(el("div", "rc", rows.length + (rows.length === 1 ? " row · " : " rows · ") + cols.length + (cols.length === 1 ? " column" : " columns")));
    cols.slice(0, 3).forEach(cn => d.appendChild(dataCol("type", cn)));
  }).catch(() => { d.innerHTML = ""; d.appendChild(dataCol("table", "data")); });
}
const TEXT_EXT = /\.(md|markdown|txt|text|rst|log|py|ipynb|r|jl|js|ts|sh|bash|zsh|yaml|yml|toml|ini|cfg|conf|env|tex|bib|xml|css|sql|c|cc|cpp|h|hpp|java|go|rs|rb|php|fasta|fa|fastq|nwk|nb)$/i;
const MOL_EXT = /\.(pdb|cif|mmcif|ent|xyz|mol|mol2|sdf|gro)$/i;
function tileThumb(a) {
  const t = el("div", "thumb"); const ct = a.content_type || ""; const nm = (a.filename || "").toLowerCase();
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) { const im = el("img"); im.src = artUrl(a); t.appendChild(im); }
  else if (/csv|tsv/.test(ct) || /\.(csv|tsv)$/i.test(nm)) { const d = el("div", "data"); d.appendChild(el("div", "rc", "…")); t.appendChild(d); fillDataPreview(d, a); }
  else if (MOL_EXT.test(nm)) { const d = el("div", "molmini"); t.appendChild(d); fillMolPreview(d, a); }
  else if (/\bjson\b/.test(ct) || /\.json$/i.test(nm)) { const d = el("div", "data"); d.appendChild(el("div", "rc", "…")); t.appendChild(d); fillDataPreview(d, a); }
  else if (TEXT_EXT.test(nm) || ct.startsWith("text/")) { const d = el("div", "txt"); t.appendChild(d); fillTextPreview(d, a); }
  else { const b = el("span", "big"); b.innerHTML = icon("file", 28); t.appendChild(b); }
  return t;
}
/* Cached fetch of an artifact's text, keyed by id+size so edits (which change size /
   bust the URL) refetch but frequent re-renders during a running turn don't hammer. */
function _thumbText(a) {
  S._thumbCache = S._thumbCache || {};
  const key = a.id + ":" + (a.size_bytes || 0);
  if (!S._thumbCache[key]) S._thumbCache[key] = fetch(artUrl(a)).then(r => r.text());
  return S._thumbCache[key];
}
/* Fallback: swap a preview container for a centered line icon. */
function thumbFallback(d, name) { d.className = "big"; d.innerHTML = icon(name || "file", 28); }
function fillTextPreview(d, a) {
  _thumbText(a).then(txt => {
    // Some "text" files are actually binary (a mislabelled blob) — a garbled
    // thumbnail is ugly, so fall back to a clean icon instead.
    if (looksBinary(txt)) return thumbFallback(d, "file");
    const snip = (txt || "").replace(/\r/g, "").split("\n").slice(0, 16).join("\n").slice(0, 900).replace(/\s+$/, "");
    if (!snip.trim()) return thumbFallback(d, "file-text");
    d.textContent = snip;
  }).catch(() => thumbFallback(d, "file-text"));
}
function fillMolPreview(d, a) {
  _thumbText(a).then(txt => {
    const pts = parseMolPoints(txt);
    if (pts.length < 3) return thumbFallback(d, "atom");
    d.innerHTML = molSvg(pts);
  }).catch(() => thumbFallback(d, "atom"));
}
/* Extract atom coordinates for a 2D structure thumbnail. Prefers CA backbone
   (PDB fixed columns); falls back to all atoms, then to whitespace-split xyz. */
function parseMolPoints(txt) {
  const lines = (txt || "").split("\n"); const all = [], ca = [];
  for (const ln of lines) {
    if (ln.startsWith("ATOM") || ln.startsWith("HETATM")) {
      const x = parseFloat(ln.slice(30, 38)), y = parseFloat(ln.slice(38, 46)), z = parseFloat(ln.slice(46, 54));
      if (!isFinite(x) || !isFinite(y)) continue;
      const p = { x, y, z: isFinite(z) ? z : 0 }; all.push(p);
      if (ln.slice(12, 16).trim() === "CA") ca.push(p);
    }
  }
  let pts = ca.length >= 3 ? ca : all;
  if (!pts.length) {
    for (const ln of lines) {
      const m = ln.trim().split(/\s+/);
      if (m.length >= 4) { const x = parseFloat(m[1]), y = parseFloat(m[2]), z = parseFloat(m[3]); if (isFinite(x) && isFinite(y) && isFinite(z)) pts.push({ x, y, z }); }
    }
  }
  if (pts.length > 500) { const step = Math.ceil(pts.length / 500); pts = pts.filter((_, i) => i % step === 0); }
  return pts;
}
/* Spectrum-colored point cloud of the XY projection (blue→red along the chain,
   like the 3Dmol viewer), with Z used as a depth cue for radius/opacity. */
function molSvg(pts) {
  const W = 180, H = 104, pad = 12;
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity, minz = Infinity, maxz = -Infinity;
  for (const p of pts) { minx = Math.min(minx, p.x); maxx = Math.max(maxx, p.x); miny = Math.min(miny, p.y); maxy = Math.max(maxy, p.y); minz = Math.min(minz, p.z); maxz = Math.max(maxz, p.z); }
  const sx = (maxx - minx) || 1, sy = (maxy - miny) || 1, zr = (maxz - minz) || 1;
  const scale = (Math.min(W, H) - 2 * pad) / Math.max(sx, sy);
  const ox = (W - sx * scale) / 2, oy = (H - sy * scale) / 2, last = pts.length - 1 || 1;
  let dots = "";
  pts.forEach((p, i) => {
    const cx = ox + (p.x - minx) * scale, cy = H - (oy + (p.y - miny) * scale);
    const hue = 240 - 240 * (i / last), depth = (p.z - minz) / zr;
    dots += `<circle cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${(1.4 + depth * 1.6).toFixed(1)}" fill="hsl(${hue.toFixed(0)} 65% 52%)" opacity="${(0.45 + depth * 0.5).toFixed(2)}"/>`;
  });
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet">${dots}</svg>`;
}
function visibleArtifacts() {
  // hide priority<0 (hidden); starred (priority>0) first, then newest.
  return (S.artifacts || []).filter(a => (a.priority || 0) >= 0)
    .slice().sort((x, y) => (y.priority || 0) - (x.priority || 0));
}
// The Files grid can scope to the current conversation (S.artifacts) or the
// whole project (S.projectArtifacts, aggregated across every frame).
function filesGridArtifacts() {
  const src = S.filesScope === "project" ? (S.projectArtifacts || []) : (S.artifacts || []);
  return src.filter(a => (a.priority || 0) >= 0)
    .slice().sort((x, y) => (y.priority || 0) - (x.priority || 0));
}
function sessionNameFor(frameId) {
  const f = (S.sessions || []).find(s => s.id === frameId);
  return (f && (f.name || f.task_summary)) || t("conv.title.default");
}
// Fetch all artifacts across the current project's conversations (cached per
// project; pass force=true to bust after a new artifact lands or a delete).
async function loadProjectArtifacts(force) {
  const pid = S.project;
  if (!pid) { S.projectArtifacts = []; S._projArtFor = null; return; }
  if (!force && S._projArtFor === pid) return;
  let a = []; try { a = await api(`/projects/${pid}/artifacts`); } catch { a = []; }
  if (S.project !== pid) return;  // project switched mid-fetch — drop stale result
  S.projectArtifacts = Array.isArray(a) ? a : []; S._projArtFor = pid;
}
async function setFilesScope(scope) {
  S.filesScope = (scope === "project") ? "project" : "frame";
  const seg = $("#files-scope");
  if (seg) seg.querySelectorAll(".seg-btn").forEach(b => b.classList.toggle("active", b.dataset.scope === S.filesScope));
  if (S.filesScope === "project") await loadProjectArtifacts();
  renderFilesGrid();
}
function renderConversationArtifacts() {
  document.querySelectorAll(".generated, .uploaded").forEach(n => n.remove());
  const arts = visibleArtifacts();
  if (!arts.length) return;
  const mkTile = a => { const tile = el("div", "tile"); tile.appendChild(tileThumb(a)); const fn = el("div", "tfn", a.filename || "artifact"); if ((a.priority || 0) > 0) fn.textContent = "⭐ " + fn.textContent; tile.appendChild(fn); tile.onclick = () => openViewer(a); return tile; };
  // Show at most 6 slots per section; when there are MORE than 6 files, collapse
  // the tail into a "+N more" tile (click to expand) so the gallery stays compact.
  const CAP = 6;
  const section = (cls, label, list) => {
    if (!list.length) return;
    const g = el("div", cls); g.appendChild(el("div", "gen-label", `${label} · ${list.length}`));
    const tiles = el("div", "gen-tiles");
    if (list.length <= CAP) {
      list.forEach(a => tiles.appendChild(mkTile(a)));
    } else {
      list.slice(0, CAP - 1).forEach(a => tiles.appendChild(mkTile(a)));
      const more = el("div", "tile tile-more");
      more.appendChild(el("div", "tile-more-n", t("gen.more", list.length - (CAP - 1))));
      more.onclick = () => { more.remove(); list.slice(CAP - 1).forEach(a => tiles.appendChild(mkTile(a))); };
      tiles.appendChild(more);
    }
    g.appendChild(tiles); $("#messages").appendChild(g);
  };
  // Separate user uploads from cell-generated outputs so an uploaded file (e.g. a
  // .fasta) is labelled "uploaded", not "generated".
  section("uploaded", t("art.uploaded"), arts.filter(a => a.is_user_upload));
  section("generated", t("art.generated"), arts.filter(a => !a.is_user_upload));
  down();
}
function renderFilesGrid() {
  const arts = filesGridArtifacts();
  const list = $("#results-list"); list.innerHTML = ""; $("#results-count").textContent = arts.length;
  if (!arts.length) {
    const msg = S.filesScope === "project"
      ? t("files.emptyProject")
      : t("files.empty");
    list.appendChild(el("div", "files-empty", msg)); return;
  }
  arts.forEach(a => {
    const card = el("div", "art"); card.appendChild(tileThumbBig(a));
    card.appendChild(el("div", "a-name", ((a.priority || 0) > 0 ? "⭐ " : "") + (a.filename || "artifact"))); card.appendChild(el("div", "a-meta", (a.content_type || "") + " · " + bytes(a.size_bytes)));
    // In project scope, tell the user which conversation each file came from.
    if (S.filesScope === "project") card.appendChild(el("div", "a-src", t("files.fromSession", sessionNameFor(a.root_frame_id))));
    card.onclick = () => openViewer(a); list.appendChild(card);
  });
}
function tileThumbBig(a) { const t = tileThumb(a); t.className = "a-thumb"; return t; }

/* Shared artifact body renderer (used by dock Viewer + fullscreen modal). */
function artUrl(a) { const b = (S._artBust || {})[a.id]; return `/api/artifacts/${a.id}` + (b ? `?_=${b}` : ""); }
function renderArtifactBody(body, a) {
  body.innerHTML = ""; const ct = a.content_type || ""; const nm = (a.filename || "").toLowerCase(); const url = artUrl(a);
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) { renderAnnotatableImage(body, a, url); }
  else if (ct === "application/pdf" || nm.endsWith(".pdf")) { const f = el("iframe"); f.src = url; body.appendChild(f); }
  else if (ct === "text/html" || nm.endsWith(".html") || nm.endsWith(".htm")) { const f = el("iframe"); f.setAttribute("sandbox", "allow-scripts allow-forms"); f.src = (S.sandboxOrigin || "") + `/preview/${a.id}`; body.appendChild(f); }
  else if (/\.(pdb|cif|mol|mol2|sdf|xyz)$/i.test(nm)) molecule(body, url, nm);
  else if (/\.(md|markdown)$/i.test(nm)) fetch(url).then(r => r.text()).then(t => { const d = el("div", "md"); d.style.padding = "18px"; d.innerHTML = renderMd(t); body.appendChild(d); }).catch(() => {});
  else if (/csv|json/.test(ct) || /\.(csv|json|tsv)$/i.test(nm)) fetch(url).then(r => r.text()).then(t => { const rows = parseTable(t, a); if (rows && rows.length) { const tbl = el("table", "sheet"); const hr = el("tr"); Object.keys(rows[0]).forEach(k => hr.appendChild(el("th", null, k))); tbl.appendChild(hr); rows.forEach(row => { const tr = el("tr"); Object.keys(rows[0]).forEach(k => tr.appendChild(el("td", null, String(row[k] ?? "")))); tbl.appendChild(tr); }); body.appendChild(tbl); } else { const p = el("pre"); p.textContent = t; body.appendChild(p); } }).catch(() => {});
  else fetch(url).then(r => r.text()).then(t => { const p = el("pre"); p.textContent = t.slice(0, 300000); body.appendChild(p); }).catch(() => {});
}

/* ---------- image annotations (figure review → message → remote edit) ---------- */
function annotationsFor(artifactId) { return (S.annotations || []).filter(x => x.artifact_id === artifactId); }
function openAnnotations() { return (S.annotations || []).filter(x => x.status === "open"); }
function annotationId(an) { return an && (an.id || an.annotation_id); }
function setLocalAnnotationStatus(ids, status) {
  const wanted = new Set((ids || []).filter(Boolean));
  if (!wanted.size) return;
  S.annotations = (S.annotations || []).map(an => wanted.has(annotationId(an)) ? { ...an, status } : an);
}
async function deleteAnnotations(ids) {
  const wanted = [...new Set((ids || []).filter(Boolean))];
  if (!wanted.length) return;
  const results = await Promise.allSettled(wanted.map(id => api(`/annotations/${id}`, { method: "DELETE" }).then(() => id)));
  const deleted = results.filter(r => r.status === "fulfilled").map(r => r.value);
  if (deleted.length) {
    const gone = new Set(deleted);
    S.annotations = (S.annotations || []).filter(an => !gone.has(annotationId(an)));
    refreshAllStages();
    updateAnnotBadge();
  }
  const failed = results.find(r => r.status === "rejected");
  if (failed) throw failed.reason || new Error("delete failed");
}
async function loadAnnotations(fid) {
  let res; try { res = await api(`/frames/${fid}/annotations`); } catch { return false; }
  if (fid !== S.currentId) return true;
  S.annotations = (res && res.annotations) || [];
  updateAnnotBadge();
  return true;
}
/* Render an image the user can pin comments onto, with zoom + pan. Used by the
   dock viewer AND the fullscreen modal. Zoom is WIDTH-BASED (the image element
   physically grows) rather than a CSS transform, so the pin layer scales with
   it and every annotation coordinate / popup stays pixel-correct — no transform
   math to reconcile. Panning is native overflow scroll. */
function renderAnnotatableImage(body, a, url) {
  closeAnnotDraft();
  const wrap = el("div", "annot-wrap");
  const zoom = el("div", "annot-zoom");
  const stage = el("div", "annot-stage"); stage._artId = a.id;
  const img = el("img", "annot-img"); img.src = url; img.draggable = false;
  const layer = el("div", "annot-layer");
  stage.appendChild(img); stage.appendChild(layer); zoom.appendChild(stage);
  wrap.appendChild(zoom); body.appendChild(wrap);

  const zs = { z: 1, fitW: 0, max: 8 };
  // At z=1 the image is fitted to the pane by CSS alone (.annot-img max-width:100%
  // inside a max-width:100% stage) — no JS sizing, so a wide figure never overflows
  // and resizing the pane reflows automatically. Only ONCE the user zooms do we
  // pin an explicit width (fitW * z) and let the stage grow past the pane.
  const applyZoom = (z) => {
    zs.z = Math.max(1, Math.min(zs.max, z));
    if (zs.z <= 1.001) { img.style.width = ""; img.style.maxWidth = ""; zoom.classList.remove("zoomed"); }
    else if (zs.fitW) { img.style.maxWidth = "none"; img.style.width = (zs.fitW * zs.z) + "px"; zoom.classList.add("zoomed"); }
    if (lvl) lvl.textContent = Math.round(zs.z * 100) + "%";
    if (bOut) bOut.disabled = zs.z <= 1.001;
    if (bIn) bIn.disabled = zs.z >= zs.max - 0.001;
  };
  // Zoom keeping the content point under (cx,cy) fixed on screen. The fit baseline
  // is captured from the CSS-fitted image while at z<=1 (pane is laid out by the
  // time the user interacts), so it's always correct regardless of load timing.
  const zoomAt = (cx, cy, nz) => {
    if (zs.z <= 1.001) { const w = img.getBoundingClientRect().width; if (w) zs.fitW = w; }
    if (nz > 1.001 && !zs.fitW) return;  // no fit baseline yet (image not laid out) — don't collapse it
    const sr = stage.getBoundingClientRect();
    if (!sr.width) return;
    const fx = (cx - sr.left) / sr.width, fy = sr.height ? (cy - sr.top) / sr.height : .5;
    applyZoom(nz);
    const sr2 = stage.getBoundingClientRect();
    zoom.scrollLeft += sr2.left - (cx - fx * sr2.width);
    zoom.scrollTop += sr2.top - (cy - fy * sr2.height);
  };
  const zoomCenter = (nz) => { const r = zoom.getBoundingClientRect(); zoomAt(r.left + r.width / 2, r.top + r.height / 2, nz); };

  // floating toolbar (−  %  +)
  const bar = el("div", "zoom-bar");
  const bOut = el("button"); bOut.title = t("zoom.out"); bOut.innerHTML = icon("minus", 16); bOut.onclick = () => zoomCenter(zs.z / 1.4);
  const lvl = el("div", "zoom-lvl", "100%"); lvl.title = t("zoom.reset"); lvl.onclick = () => { applyZoom(1); zoom.scrollTo(0, 0); };
  const bIn = el("button"); bIn.title = t("zoom.in"); bIn.innerHTML = icon("plus", 16); bIn.onclick = () => zoomCenter(zs.z * 1.4);
  bar.appendChild(bOut); bar.appendChild(lvl); bar.appendChild(bIn); wrap.appendChild(bar);
  wrap.appendChild(el("div", "zoom-hint", t("zoom.hint")));

  // Pins are %-positioned so they don't need the fit width; CSS fits the image at
  // z=1, so nothing here depends on pane-layout timing.
  const ready = () => renderPins(stage, a);
  if (img.complete) requestAnimationFrame(ready); else img.addEventListener("load", ready);

  // Ctrl/Cmd + wheel zooms toward the cursor (and trackpad pinch, which browsers
  // deliver as ctrl+wheel). A PLAIN wheel is left to scroll natively, so a tall
  // portrait image can still be scrolled/panned instead of being hijacked.
  zoom.addEventListener("wheel", (e) => {
    if (!(e.ctrlKey || e.metaKey)) return;
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, zs.z * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
  }, { passive: false });

  // drag-to-pan (only while zoomed). A drag that moves > threshold suppresses the
  // click-to-annotate that would otherwise fire on pointerup.
  zoom.addEventListener("pointerdown", (e) => {
    stage._panned = false;  // start every gesture clean, so a pan that ends off the layer can't swallow a later annotation click
    if (zs.z <= 1.001 || e.button !== 0) return;
    if (e.target.classList && e.target.classList.contains("annot-pin")) return;  // let pins handle their own click
    const sx = e.clientX, sy = e.clientY, sl = zoom.scrollLeft, st = zoom.scrollTop;
    let moved = false;
    const mv = (ev) => {
      const dx = ev.clientX - sx, dy = ev.clientY - sy;
      if (!moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) { moved = true; zoom.classList.add("grabbing"); }
      if (moved) { zoom.scrollLeft = sl - dx; zoom.scrollTop = st - dy; ev.preventDefault(); }
    };
    const up = () => {
      document.removeEventListener("pointermove", mv); document.removeEventListener("pointerup", up); document.removeEventListener("pointercancel", up);
      zoom.classList.remove("grabbing"); stage._panned = moved;
    };
    document.addEventListener("pointermove", mv); document.addEventListener("pointerup", up); document.addEventListener("pointercancel", up);
  });

  layer.addEventListener("click", (e) => {
    if (stage._panned) { stage._panned = false; return; }  // that "click" was the end of a pan
    if (e.target !== layer) return;                          // ignore clicks that land on a pin/popup
    const r = layer.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    openAnnotDraft(stage, a, x, y);
  });
}
function renderPins(stage, a) {
  const layer = stage.querySelector(".annot-layer"); if (!layer) return;
  layer.querySelectorAll(".annot-pin:not(.draft)").forEach(n => n.remove());
  annotationsFor(a.id).forEach(an => {
    const pin = el("div", "annot-pin" + (an.status === "sent" ? " sent" : (an.status === "resolved" ? " resolved" : "")));
    pin.style.left = (an.x * 100) + "%"; pin.style.top = (an.y * 100) + "%";
    pin.textContent = an.number; pin.title = an.body || "";
    pin.onclick = (e) => { e.stopPropagation(); openPinPop(stage, a, an); };
    layer.appendChild(pin);
  });
}
function closeAnnotDraft() {
  const d = S._annotDraft; if (!d) return;
  try { d.pin && d.pin.remove(); d.pop && d.pop.remove(); } catch {}
  S._annotDraft = null;
}
function closeAnnotPop() { document.querySelectorAll(".annot-pop.view").forEach(n => n.remove()); }
function positionPop(pop, layer, x, y) {
  const lw = layer.clientWidth, lh = layer.clientHeight;
  pop.style.visibility = "hidden"; pop.style.left = "0px"; pop.style.top = "0px";
  requestAnimationFrame(() => {
    const pw = pop.offsetWidth || 260, ph = pop.offsetHeight || 120;
    let px = x * lw + 18, py = y * lh - 8;
    if (px + pw > lw - 8) px = x * lw - pw - 18;
    px = Math.max(8, Math.min(px, Math.max(8, lw - pw - 8)));
    py = Math.max(8, Math.min(py, Math.max(8, lh - ph - 8)));
    pop.style.left = px + "px"; pop.style.top = py + "px"; pop.style.visibility = "";
  });
}
function openAnnotDraft(stage, a, x, y) {
  closeAnnotDraft(); closeAnnotPop();
  const layer = stage.querySelector(".annot-layer");
  const num = annotationsFor(a.id).length + 1;
  const pin = el("div", "annot-pin draft"); pin.style.left = (x * 100) + "%"; pin.style.top = (y * 100) + "%"; pin.textContent = num;
  layer.appendChild(pin);
  const pop = el("div", "annot-pop edit");
  const ta = el("textarea", "annot-input"); ta.placeholder = "Add annotation…"; ta.rows = 2;
  const foot = el("div", "annot-foot");
  const spacer = el("div", "annot-foot-l");
  const cancel = el("button", "annot-btn ghost", t("common.cancel"));
  const save = el("button", "annot-btn solid", t("common.save")); save.disabled = true;
  ta.addEventListener("input", () => { save.disabled = !ta.value.trim(); });
  ta.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") { e.preventDefault(); closeAnnotDraft(); }
    else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); if (!save.disabled) save.click(); }
  });
  cancel.onclick = () => closeAnnotDraft();
  save.onclick = async () => {
    const text = ta.value.trim(); if (!text) return;
    save.disabled = true; save.textContent = t("common.saving");
    try { await saveAnnotation(a, x, y, text); closeAnnotDraft(); }
    catch (e) { save.disabled = false; save.textContent = t("common.save"); hint(/404/.test(e.message) ? t("annot.save.err404") : (t("annot.save.err", e.message)), true); }
  };
  foot.appendChild(spacer); foot.appendChild(cancel); foot.appendChild(save);
  pop.appendChild(ta); pop.appendChild(foot);
  layer.appendChild(pop); positionPop(pop, layer, x, y);
  S._annotDraft = { stage, art: a, x, y, pin, pop };
  setTimeout(() => ta.focus(), 0);
}
async function saveAnnotation(a, x, y, text) {
  if (!S.currentId) { hint(t("annot.noSession"), true); return; }
  const res = await api(`/frames/${S.currentId}/annotations`, {
    method: "POST",
    body: JSON.stringify({ artifact_id: a.id, artifact_name: a.filename || "", x, y, body: text })
  });
  const an = res && res.annotation; if (!an) return;
  S.annotations = (S.annotations || []).concat([an]);
  refreshAllStages();
  updateAnnotBadge();
  hint(t("annot.added"));
}
/* Re-render pins on every visible image stage for this artifact (dock + modal). */
function refreshAllStages() {
  document.querySelectorAll(".annot-stage").forEach(stage => {
    const art = (S.artifacts || []).find(x => x.id === stage._artId) || (S.dockArtifact && S.dockArtifact.id === stage._artId ? S.dockArtifact : null);
    if (art) renderPins(stage, art);
  });
}
function openPinPop(stage, a, an) {
  closeAnnotDraft(); closeAnnotPop();
  const layer = stage.querySelector(".annot-layer");
  const pop = el("div", "annot-pop view");
  const head = el("div", "annot-pop-head");
  head.appendChild(el("span", "annot-pop-num", "#" + an.number));
  const st = el("span", "annot-pop-status " + (an.status || "open"), an.status === "sent" ? t("annot.status.sent") : (an.status === "resolved" ? t("annot.status.resolved") : t("annot.status.open")));
  head.appendChild(st);
  const bodyEl = el("div", "annot-pop-body", an.body || "");
  const foot = el("div", "annot-foot");
  foot.appendChild(el("div", "annot-foot-l"));
  const del = el("button", "annot-btn ghost danger", t("common.delete")); del.onclick = async () => {
    del.disabled = true;
    try { await deleteAnnotations([annotationId(an)]); closeAnnotPop(); hint(t("annot.deleted")); }
    catch (e) { del.disabled = false; hint(t("toast.deleteFailed", e.message), true); }
  };
  const close = el("button", "annot-btn solid", t("common.close")); close.onclick = () => closeAnnotPop();
  foot.appendChild(del); foot.appendChild(close);
  pop.appendChild(head); pop.appendChild(bodyEl); pop.appendChild(foot);
  layer.appendChild(pop); positionPop(pop, layer, an.x, an.y);
}
/* Composer chip: how many pinned comments will ride along with the next message. */
function updateAnnotBadge() {
  const bar = $("#annot-bar"); if (!bar) return;
  const open = openAnnotations();
  if (!open.length) { bar.classList.add("hidden"); bar.innerHTML = ""; return; }
  bar.classList.remove("hidden"); bar.innerHTML = "";
  const chip = el("span", "annot-chip");
  const main = el("button", "annot-chip-main"); main.appendChild(iconEl("message-square", 14));
  main.appendChild(el("span", null, " " + open.length + (open.length === 1 ? " comment" : " comments")));
  main.title = t("annot.chip.title");
  main.onclick = (e) => { e.stopPropagation(); toggleAnnotList(chip); };
  const cancel = el("button", "annot-chip-x"); cancel.innerHTML = icon("x", 13); cancel.title = t("annot.discard.title");
  cancel.onclick = async (e) => {
    e.preventDefault(); e.stopPropagation();
    cancel.disabled = true;
    try {
      await deleteAnnotations(openAnnotations().map(annotationId));
      closeAnnotPop();
      const p = $("#annot-list-pop"); if (p) p.remove();
      hint(t("annot.discarded"));
    } catch (err) {
      cancel.disabled = false;
      hint(t("annot.remove.err", err.message), true);
    }
  };
  chip.appendChild(main); chip.appendChild(cancel);
  bar.appendChild(chip);
}
function toggleAnnotList(anchor) {
  const existing = $("#annot-list-pop"); if (existing) { existing.remove(); return; }
  const open = openAnnotations();
  const pop = el("div", "annot-list-pop"); pop.id = "annot-list-pop";
  pop.appendChild(el("div", "annot-list-head", t("annot.list.head", open.length)));
  open.forEach(an => {
    const row = el("div", "annot-list-row");
    const trow = el("div", "annot-list-t");   // do NOT shadow the global i18n t() used below
    trow.appendChild(el("span", "annot-list-pin", String(an.number)));
    trow.appendChild(el("span", "annot-list-file", an.artifact_name || "artifact"));
    row.appendChild(trow);
    row.appendChild(el("div", "annot-list-body", an.body || ""));
    const acts = el("div", "annot-list-acts");
    const openBtn = el("button", "annot-mini", t("common.view")); openBtn.onclick = () => { pop.remove(); const art = (S.artifacts || []).find(x => x.id === an.artifact_id); if (art) openViewer(art); };
    const rm = el("button", "annot-mini danger", t("btn.remove")); rm.onclick = async () => { try { await deleteAnnotations([annotationId(an)]); pop.remove(); if (openAnnotations().length && anchor.parentElement) toggleAnnotList(anchor); } catch (e) { hint(t("annot.remove.err", e.message), true); } };
    acts.appendChild(openBtn); acts.appendChild(rm); row.appendChild(acts);
    pop.appendChild(row);
  });
  anchor.parentElement.appendChild(pop);
  setTimeout(() => document.addEventListener("mousedown", function h(ev) { if (!pop.contains(ev.target) && !anchor.contains(ev.target)) { pop.remove(); document.removeEventListener("mousedown", h); } }), 0);
}

/* Fullscreen: center modal. */
function openArtifact(a) {
  $("#modal-title").textContent = a.filename || t("modal.title.preview");
  const dl = $("#modal-download"); dl.style.display = ""; dl.href = `/api/artifacts/${a.id}`; dl.setAttribute("download", a.filename || "artifact");
  renderArtifactBody($("#modal-body"), a);
  $("#modal").classList.remove("hidden");
}
/* Artifact click → dock Viewer tab. */
function openViewer(a) { S.dockArtifact = a; S.provMode = false; addOpenTab(a); setActiveTab(a.id); }
function renderViewer() {
  const a = S.dockArtifact; const v = $("#dock-viewer"); if (!v) return;
  edacTeardown();  // tear down editor autocomplete before rebuild
  v.innerHTML = "";
  if (!a) { v.appendChild(el("div", "dock-empty", t("viewer.empty"))); return; }
  const head = el("div", "viewer-head");
  head.appendChild(el("div", "vh-name", a.filename || "artifact"));
  const acts = el("div", "vh-acts");
  const menuBtn = ghostIconBtn("more-vertical", t("viewer.act.more")); menuBtn.onclick = () => artifactMenu(menuBtn, a);
  if (!S.provMode && isTextEditable(a)) { const editBtn = ghostIconBtn("pencil", t("common.edit")); editBtn.onclick = () => editArtifact(a); acts.appendChild(editBtn); }
  const maxBtn = ghostIconBtn("maximize-2", t("viewer.act.fullscreen")); maxBtn.onclick = () => openArtifact(a);
  const dl = el("a", "icon-ghost"); dl.innerHTML = icon("download", 16); dl.href = `/api/artifacts/${a.id}`; dl.setAttribute("download", a.filename || "artifact"); dl.title = t("common.download");
  const closeBtn = ghostIconBtn("x", t("common.close")); closeBtn.onclick = () => { if (S.provMode) { S.provMode = false; renderViewer(); } else closeTab(a.id); };
  acts.insertBefore(menuBtn, acts.firstChild); acts.appendChild(maxBtn); acts.appendChild(dl); acts.appendChild(closeBtn);
  head.appendChild(acts); v.appendChild(head);
  if (S.provMode) { renderProvenanceInto(v, a); return; }
  _molTeardown();
  const body = el("div", "viewer-body"); v.appendChild(body);
  if (S._editing === a.id) renderArtifactEditor(body, a); else renderArtifactBody(body, a);
}
function isTextEditable(a) {
  const nm = (a.filename || "").toLowerCase(); const ct = a.content_type || "";
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg|pdb|cif|mol|mol2|sdf|xyz|pdf)$/i.test(nm)) return false;
  return /\.(md|markdown|txt|log|csv|tsv|json|py|js|ts|fasta|fa|nwk|treefile|xml|ya?ml|sh|r|tex|html?|css)$/i.test(nm) || ct.startsWith("text/") || /json|csv|xml|javascript/.test(ct);
}
function editArtifact(a) { S._editing = a.id; renderViewer(); }
async function renameArtifact(a) {
  const name = prompt(t("artifact.rename.prompt"), a.filename || ""); if (!name || name === a.filename) return;
  try { await api(`/artifacts/${a.id}/rename`, { method: "PATCH", body: JSON.stringify({ filename: name }) }); a.filename = name; if (S.currentId) loadArtifacts(S.currentId); renderViewer(); hint(t("artifact.renamed")); }
  catch (e) { hint(t("toast.renameFailed", e.message), true); }
}
async function deleteArtifact(a) {
  if (!confirm(t("artifact.delete.confirm"))) return;
  try { await api(`/artifacts/${a.id}`, { method: "DELETE" }); closeTab(a.id); if (S.currentId) loadArtifacts(S.currentId); hint(t("artifact.deleted", (a.filename || ""))); }
  catch (e) { hint(t("toast.deleteFailed", e.message), true); }
}
function renderArtifactEditor(body, a) {
  const bar = el("div", "edit-bar");
  bar.appendChild(el("span", "edit-label", t("editor.label", (a.filename || ""))));
  const save = el("button", "solid-btn small", t("common.save")); const cancel = el("button", "outline-btn small", t("common.cancel"));
  const acts = el("div", "edit-acts"); acts.appendChild(cancel); acts.appendChild(save); bar.appendChild(acts);
  body.appendChild(bar);
  const ta = el("textarea", "edit-area"); ta.spellcheck = false; ta.value = t("common.loading"); ta.disabled = true; body.appendChild(ta);
  // code autocomplete: per-editor controller + caret-anchored popup, torn down in renderViewer()
  const pop = el("div", "edit-ac hidden"); body.appendChild(pop);
  const ec = { open: false, items: [], idx: 0, start: 0, composing: false, dead: false, justPicked: false, a, ta, pop, deb: 0 };
  S._editorAC = ec;
  ta.addEventListener("input", () => { if (ec.composing || ec.justPicked) return; clearTimeout(ec.deb); ec.deb = setTimeout(() => { if (!ec.dead) edacUpdate(ec); }, 90); });
  ta.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229 || ec.composing) return;        // never touch keys during IME composition
    if (!ec.open) return;                                                  // popup closed → keys act natively (newline, tab-focus)
    if (e.key === "ArrowLeft" || e.key === "ArrowRight" || e.key === "Home" || e.key === "End" || e.key === "PageUp" || e.key === "PageDown") { edacClose(ec); return; }  // caret moved → dismiss (no preventDefault: caret moves natively)
    if (e.key === "ArrowDown") { e.preventDefault(); ec.idx = (ec.idx + 1) % ec.items.length; edacRender(ec); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); ec.idx = (ec.idx - 1 + ec.items.length) % ec.items.length; edacRender(ec); return; }
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); edacPick(ec, ec.idx); return; }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); edacClose(ec); return; }
  });
  ta.addEventListener("compositionstart", () => { ec.composing = true; edacClose(ec); });
  ta.addEventListener("compositionend", () => { ec.composing = false; requestAnimationFrame(() => { if (!ec.dead) edacUpdate(ec); }); });
  ta.addEventListener("blur", () => setTimeout(() => { if (!ec.dead) edacClose(ec); }, 120));  // grace for popup mousedown
  ta.addEventListener("scroll", () => edacClose(ec));
  ta.addEventListener("click", () => edacClose(ec));  // click repositions the caret → dismiss
  fetch(`/api/artifacts/${a.id}?_=${Date.now()}`).then(r => r.text()).then(t => { ta.value = t; ta.disabled = false; ta.focus(); }).catch(() => { ta.value = ""; ta.disabled = false; });
  cancel.onclick = () => { S._editing = null; renderViewer(); };
  save.onclick = async () => {
    save.disabled = true; save.textContent = t("common.saving");
    try {
      await api(`/artifacts/${a.id}/edit`, { method: "POST", body: JSON.stringify({ content: ta.value }) });
      S._editing = null; (S._artBust = S._artBust || {})[a.id] = Date.now(); hint(t("artifact.saved", (a.filename || "")));
      if (S.currentId) loadArtifacts(S.currentId);
      renderViewer();
    } catch (e) { save.disabled = false; save.textContent = t("common.save"); hint(t("artifact.save.err", e.message), true); }
  };
}
function artifactMenu(anchor, a) {
  const starred = (a.priority || 0) > 0;
  openMenu(anchor, [
    { label: t("menu.versionHistory"), icon: "clock", onClick: () => showVersions(a) },
    { label: t("menu.provenance"), icon: "provenance", onClick: () => showProvenance(a) },
    { sep: true },
    { label: starred ? t("menu.unstar") : t("menu.star"), icon: "star", onClick: () => setArtPriority(a, starred ? 0 : 1) },
    { label: t("menu.hideFromList"), icon: "eye-off", onClick: () => setArtPriority(a, -1, true) },
    { label: t("menu.copyLink"), icon: "link", onClick: () => { try { navigator.clipboard && navigator.clipboard.writeText(location.origin + "/api/artifacts/" + a.id); } catch {} hint(t("artifact.linkCopied")); } },
    { label: t("common.edit"), icon: "pencil", onClick: () => { if (isTextEditable(a)) editArtifact(a); else hint(t("artifact.notEditable")); } },
    { label: t("folder.menu.rename"), icon: "pencil", onClick: () => renameArtifact(a) },
    { label: t("menu.exportMetadata"), icon: "file-text", onClick: () => exportMetadata(a) },
    { sep: true },
    { label: t("common.delete"), icon: "trash-2", danger: true, onClick: () => deleteArtifact(a) },
  ]);
}
async function setArtPriority(a, p, closeAfter) {
  try { await api(`/artifacts/${a.id}/priority`, { method: "POST", body: JSON.stringify({ priority: p }) }); a.priority = p; hint(p > 0 ? t("artifact.starred") : p < 0 ? t("artifact.hidden") : t("artifact.unstarred")); if (S.currentId) loadArtifacts(S.currentId); if (closeAfter && S.dockArtifact === a) closeTab(a.id); }
  catch (e) { hint(t("artifact.priority.err", e.message), true); }
}
async function exportMetadata(a) {
  try {
    const [versions, lineage] = await Promise.all([
      api(`/artifacts/${a.id}/versions`).catch(() => ({ versions: [] })),
      api(`/artifacts/${a.id}/lineage`).catch(() => ({})),
    ]);
    const meta = { id: a.id, filename: a.filename, content_type: a.content_type, size_bytes: a.size_bytes, priority: a.priority || 0, versions: versions.versions || [], lineage };
    const blob = new Blob([JSON.stringify(meta, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = (a.filename || "artifact") + ".metadata.json"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000); hint(t("artifact.metadataExported"));
  } catch (e) { hint(t("toast.exportFailed", e.message), true); }
}
async function showVersions(a) {
  S._modalMode = "versions:" + a.id;
  $("#modal-title").textContent = t("versions.modal.title", (a.filename || ""));
  $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "<div class='dock-empty'>" + t("common.loading") + "</div>";
  $("#modal").classList.remove("hidden");
  const render = async () => {
    let d; try { d = await api(`/artifacts/${a.id}/versions`); } catch (e) { body.innerHTML = t("versions.load.err", e.message); return; }
    const vs = (d && d.versions) || []; body.innerHTML = "";
    const wrap = el("div", "ver-list");
    if (!vs.length) { wrap.appendChild(el("div", "dock-empty", t("versions.empty"))); }
    vs.forEach(v => {
      const row = el("div", "ver-row" + (v.is_latest ? " current" : ""));
      const info = el("div", "ver-info");
      const vt = el("div", "ver-title"); vt.appendChild(el("span", "ver-ord", "v" + v.ordinal)); if (v.is_latest) vt.appendChild(el("span", "ver-badge", t("cust.models.activePill"))); info.appendChild(vt);
      info.appendChild(el("div", "ver-meta", (bytes(v.size_bytes) || "") + " · " + ago(v.created_at)));
      row.appendChild(info);
      const acts = el("div", "ver-acts");
      const view = el("a", "outline-btn small", t("common.view")); view.href = `/api/artifacts/${v.version_id}`; view.target = "_blank"; acts.appendChild(view);
      if (!v.is_latest) { const rb = el("button", "solid-btn small", t("versions.restore")); rb.onclick = async () => { rb.disabled = true; rb.textContent = t("versions.restoring"); try { await api(`/artifacts/${a.id}/versions/${v.version_id}/restore`, { method: "POST" }); hint(t("versions.restored", v.ordinal)); (S._artBust = S._artBust || {})[a.id] = Date.now(); if (S.currentId) loadArtifacts(S.currentId); if (S.dockArtifact === a) renderViewer(); render(); } catch (e) { rb.disabled = false; rb.textContent = t("versions.restore"); hint(t("versions.restore.err", e.message), true); } }; acts.appendChild(rb); }
      row.appendChild(acts); wrap.appendChild(row);
    });
    body.appendChild(wrap);
  };
  render();
}
/* Free the previous 3Dmol WebGL context before creating a new one (browsers cap
   live contexts at ~16; leaking one per structure viewed eventually blanks them). */
function _molTeardown() {
  try { if (S._molViewer && S._molViewer.clear) S._molViewer.clear(); } catch {}
  try {
    const cvs = S._molView && S._molView.querySelector("canvas");
    if (cvs) { const gl = cvs.getContext("webgl") || cvs.getContext("experimental-webgl");
      const ext = gl && gl.getExtension("WEBGL_lose_context"); if (ext) ext.loseContext(); }
  } catch {}
  S._molViewer = null; S._molView = null;
}
/* 3Dmol structure viewer (F4) — container-agnostic, style selector, atom count, download, label. */
function molecule(container, url, nm) {
  _molTeardown();
  container.innerHTML = "";
  const wrap = el("div", "mol-wrap");
  wrap.appendChild(el("div", "mol-tag", "Using 3Dmol.js viewer"));
  const bar = el("div", "mol-bar");
  bar.appendChild(el("span", "mol-lbl", "Style:"));
  let cur = "cartoon"; const pills = {};
  [["cartoon", "Cartoon"], ["stick", "Stick"], ["sphere", "Sphere"], ["surface", "Surface"], ["line", "Line"]].forEach(([val, lab]) => {
    const b = el("button", "mol-style" + (val === cur ? " on" : ""), lab);
    b.onclick = () => { cur = val; Object.values(pills).forEach(x => x.classList.remove("on")); b.classList.add("on"); applyStyle(val); };
    pills[val] = b; bar.appendChild(b);
  });
  const cnt = el("span", "mol-count", ""); bar.appendChild(cnt);
  const view = el("div", "mol-view");
  const foot = el("div", "mol-foot", t("mol.foot"));
  wrap.appendChild(bar); wrap.appendChild(view); wrap.appendChild(foot); container.appendChild(wrap);
  const fmt = (nm.split(".").pop() || "pdb"); let viewer = null; let caOnly = false;
  // For coarse / CA-only models (e.g. synthetic backbones) plain cartoon renders
  // nothing, so we draw a trace tube + CA spheres so the structure is never blank.
  const spec = (style) => style === "cartoon" ? (caOnly ? { cartoon: { color: "spectrum", style: "trace" }, sphere: { colorscheme: "Jmol", radius: 0.5 } } : { cartoon: { color: "spectrum" } }) : style === "stick" ? { stick: { colorscheme: "Jmol" } } : style === "sphere" ? { sphere: { colorscheme: "Jmol" } } : { line: { colorscheme: "Jmol" } };
  const applyStyle = (style) => {
    if (!viewer) return;
    try { viewer.removeAllSurfaces && viewer.removeAllSurfaces(); } catch {}
    if (style === "surface") { viewer.setStyle({}, { cartoon: { color: "spectrum" } }); try { viewer.addSurface(window.$3Dmol.SurfaceType.VDW, { opacity: .85, color: "white" }); } catch {} }
    else viewer.setStyle({}, spec(style));
    viewer.setStyle({ hetflag: true }, { stick: { colorscheme: "Jmol" } });
    viewer.render();
  };
  const boot = () => fetch(url).then(r => r.text()).then(data => {
    try {
      viewer = window.$3Dmol.createViewer(view, { backgroundColor: "white" });
      S._molViewer = viewer; S._molView = view;
      const model = viewer.addModel(data, fmt);
      let atoms = []; try { atoms = model.selectedAtoms ? model.selectedAtoms({}) : []; } catch {}
      const n = atoms.length;
      // detect a CA-only backbone trace (no full sidechains/backbone)
      try { const ca = atoms.filter(a => a.atom === "CA" || a.name === "CA").length; caOnly = n > 0 && ca / n > 0.8; } catch {}
      cnt.textContent = n ? (n.toLocaleString() + " atoms") : "";
      applyStyle(cur); viewer.zoomTo(); viewer.render();
    } catch { view.innerHTML = "<pre style='padding:16px'>" + esc(data.slice(0, 8000)) + "</pre>"; }
  }).catch(() => {});
  const fb = () => fetch(url).then(r => r.text()).then(t => view.innerHTML = "<pre style='padding:16px'>" + esc(t.slice(0, 8000)) + "</pre>").catch(() => {});
  if (window.$3Dmol) return boot();
  const s = el("script"); s.src = "/static/vendor/3Dmol-min.js"; s.onload = boot; s.onerror = () => { const s2 = el("script"); s2.src = "https://3Dmol.org/build/3Dmol-min.js"; s2.onload = boot; s2.onerror = fb; document.head.appendChild(s2); }; document.head.appendChild(s);
}

/* ---------- Notebook tab (F2) ---------- */
function artUrlByName(fname) {
  if (!fname) return "";
  const base = String(fname).split("/").pop();
  const a = (S.artifacts || []).find(x => (x.filename || "") === fname || (x.filename || "").split("/").pop() === base);
  return a ? artUrl(a) : `/api/artifacts/${encodeURIComponent(fname)}`;  // artUrl adds the version cache-bust
}
// Same, but cache-busted by the artifact's current version so an overwritten
// table (re-run cell) refetches instead of serving the browser's stale copy.
function artUrlBust(fname) {
  const base = String(fname).split("/").pop();
  const a = (S.artifacts || []).find(x => (x.filename || "") === fname || (x.filename || "").split("/").pop() === base);
  return a ? artUrl(a) : `/api/artifacts/${encodeURIComponent(fname)}`;
}
// Minimal RFC-4180-ish parser: handles quoted fields, "" escapes and CRLF.
function parseDelimited(text, sep) {
  const rows = []; let row = [], field = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) {
      if (ch === '"') { if (text[i + 1] === '"') { field += '"'; i++; } else q = false; }
      else field += ch;
    } else if (ch === '"') q = true;
    else if (ch === sep) { row.push(field); field = ""; }
    else if (ch === "\n") { row.push(field); rows.push(row); row = []; field = ""; }
    else if (ch !== "\r") field += ch;
  }
  if (field.length || row.length) { row.push(field); rows.push(row); }
  return rows;
}
// Render a produced CSV/TSV as a real (capped) table so "表格" outputs show up
// inline like figures. Parsed rows are cached per busted-URL to avoid refetching
// on every notebook re-render during a live run.
function renderTableInto(holder, fname) {
  const url = artUrlBust(fname);
  const build = (rows) => {
    if (!rows || !rows.length) return;
    const view = rows.slice(0, 51);  // header + 50 body rows
    const tbl = el("table", "nbc-table");
    const thead = el("thead"), htr = el("tr");
    (view[0] || []).slice(0, 24).forEach(h => htr.appendChild(el("th", null, h)));
    thead.appendChild(htr); tbl.appendChild(thead);
    const tb = el("tbody");
    view.slice(1).forEach(r => { const tr = el("tr"); r.slice(0, 24).forEach(cell => tr.appendChild(el("td", null, cell))); tb.appendChild(tr); });
    tbl.appendChild(tb);
    const scroll = el("div", "nbc-table-scroll"); scroll.appendChild(tbl); holder.appendChild(scroll);
    if (rows.length > 51) holder.appendChild(el("div", "nbc-table-more", t("nb.table.rowsHidden", (rows.length - 51))));
  };
  S._tbl = S._tbl || {};
  if (S._tbl[url]) { build(S._tbl[url]); return; }
  fetch(url).then(r => r.ok ? r.text() : null).then(text => {
    if (text == null) return;
    const rows = parseDelimited(text, /\.tsv$/i.test(fname) ? "\t" : ",");
    S._tbl[url] = rows; build(rows);
  }).catch(() => {});
}
async function loadExecutionLog(id) {
  let d = null;
  try { d = await api(`/frames/${id}/execution-log`); } catch { d = null; }
  if (id !== S.currentId) return;  // a newer session was opened while this was in flight
  S.cells = (d && d.entries) || []; S.kernels = (d && d.kernels) || [];
  renderNotebook();
}
const _NB_DIV = "----- output -----";
function nbLiveStart(tool, raw, serverKernelId, serverCellIndex, serverLanguage) {
  const codeTools = /^(run_python|python|exec|run_bash|bash)/;
  const isCode = serverCellIndex != null || codeTools.test(tool || "") || !TOOL_LABELS[tool || ""];
  if (!isCode) { S._liveCell = null; return; }
  const idx = serverCellIndex || ((raw || "").match(/cell\s+(\d+)/) || [])[1];
  // The cell-start event carries the server's canonical runtime segment. The
  // status-cache fallback is retained only for older daemons/replayed events.
  const kernelId = serverKernelId || kernelIdFromEnv((_kc && _kc.st && _kc.st.env) || null);
  const cell = { cell_index: idx ? +idx : (S.liveCells ? S.liveCells.length : 0) + 1, kernel_id: kernelId, language: serverLanguage || "python", source: "", stdout: "", stderr: "", status: "running", figures: [], live: true, _out: false };
  (S.liveCells = S.liveCells || []).push(cell); S._liveCell = cell; nbRender();
}
function nbLiveAppend(txt) {
  const c = S._liveCell; if (!c) return;
  if (!c._out) {  // header emits code first, then the divider, then stdout
    const i = txt.indexOf(_NB_DIV);
    if (i === -1) { c.source += txt; }
    else { c.source += txt.slice(0, i); c._out = true; c.stdout += txt.slice(i + _NB_DIV.length).replace(/^\n/, ""); }
  } else { c.stdout += txt; }
  nbRender();
}
function nbRender() {
  if (!(S.dock.open && S.activeTab === "notebook")) return;
  // While a turn streams and the user has scrolled UP to read an earlier cell,
  // don't keep tearing the pane down under them (the "keeps updating while I
  // scroll up" jank). Mark it dirty and flush when they scroll back to the
  // bottom (see the scroll listener in renderNotebook) or when the turn ends.
  if (S.running && S._nbReading) { S._nbDirty = true; return; }
  if (S._nbSched) return; S._nbSched = true;
  requestAnimationFrame(() => { S._nbSched = false; renderNotebook(); });
}
// Kernel lifecycle control from the Notebook dock: stop / start / restart.
async function kernelCtl(action) {
  if (!S.currentId) return;
  if (action === "restart" && !confirm(t("nb.kernel.restartConfirm"))) return;
  if (action === "stop" && !confirm(t("nb.kernel.stopConfirm"))) return;
  try { await api(`/frames/${S.currentId}/kernel/${action}`, { method: "POST" }); }
  catch (e) { hint(t("nb.kernel.opFailed", e.message), true); }
  invalidateKernelCache();  // force a fresh read so the state chip reflects the action
  if (S.dock.open && S.activeTab === "notebook") renderNotebook();
}
// Cache for the Notebook header's kernel state + env list. renderNotebook rebuilds
// the whole pane on every streaming frame; without a cache the state chip and env
// <select> would refetch each frame and never settle (flickering "…" / t("nb.env.placeholder")).
// We paint the freshly-built nodes from cache immediately, then refresh the cache
// at most a few times a second.
const _kc = { id: null, st: null, stAt: 0, stBusy: false, envs: null, cur: null, envAt: 0, envBusy: false };
function invalidateKernelCache() { _kc.id = null; _kc.st = null; _kc.stAt = 0; _kc.envs = null; _kc.cur = null; _kc.envAt = 0; }
function _paintKernel(els, st) {
  const { state, bStop, bStart, title, revive, strip } = els || {};
  const label = st.turn_running ? t("dash.badge.running") : ({ running: t("nb.kernel.stateActive"), stopped: t("nb.kernel.stateStopped"), none: t("nb.kernel.stateNone") }[st.state] || st.state);
  if (state) {
    state.textContent = label + (st.generation ? t("nb.kernel.generation", st.generation) : "");
    state.className = "kstate " + (st.turn_running ? "run" : st.state);
  }
  const env = st.env || {};
  if (title) title.textContent = (env.name || "python")
    + (env.language ? " · " + env.language : "")
    + (env.python_version ? " " + env.python_version : "") + " kernel"
    + (env.pending ? t("nb.kernel.pendingSwitch", env.pending) : "");
  if (bStop) bStop.disabled = !st.alive;
  if (bStart) bStart.disabled = st.alive;
  // Revive banner: only when the kernel is stopped/absent and no turn is running.
  if (revive) revive.classList.toggle("hidden", st.alive || st.turn_running);
  if (strip) _paintStatusStrip(strip, st);
}
// Read-only Notebook status strip: a passive live/ended indicator + runtime
// label (no inputs, no kernel-control buttons). Repainted by refreshKernelState.
function _paintStatusStrip(strip, st) {
  if (!strip || !strip.line) return;
  const env = st.env || {};
  const rt = kernelLabel(kernelIdFromEnv(env)) + (env.python_version ? " " + env.python_version : "");
  const alive = !!(st.turn_running || st.alive);
  strip.line.textContent = alive ? t("nb.status.live", rt) : t("nb.status.ended", rt);
  strip.line.className = "nb-status-line " + (alive ? "live" : "ended");
}
async function refreshKernelState(els, _b, _c) {
  // Back-compat: old callers passed (stateEl, bStop, bStart).
  if (els && els.nodeType) els = { state: els, bStop: _b, bStart: _c };
  els = els || {};
  if (!S.currentId) { if (els.state) els.state.textContent = t("nb.kernel.noSession"); return; }
  if (_kc.id === S.currentId && _kc.st) _paintKernel(els, _kc.st);  // immediate, no flicker
  if (_kc.stBusy) return;
  if (_kc.id === S.currentId && _kc.st && (Date.now() - _kc.stAt) < 800) return;  // fresh enough
  const sid = S.currentId;
  _kc.stBusy = true;
  let st; try { st = await api(`/frames/${sid}/kernel`); } catch { _kc.stBusy = false; return; }
  _kc.stBusy = false;
  if (sid !== S.currentId) return;  // session switched during the fetch — drop the stale result
  if (_kc.id !== sid) { _kc.id = sid; _kc.envs = null; }
  _kc.st = st; _kc.stAt = Date.now();
  _paintKernel(els, st);  // els may be stale (a newer render replaced it); harmless — the next render repaints from cache
  // The first render happens before kernel status is known and therefore uses
  // the passive strip. If this daemon explicitly enables the developer REPL,
  // rebuild once now that `repl_enabled` is authoritative (and vice versa if a
  // runtime/config reload disabled it).
  const modeChanged = (!!st.repl_enabled && !!els.strip) || (!st.repl_enabled && !!els.state);
  if (modeChanged && S.dock.open && S.activeTab === "notebook") requestAnimationFrame(renderNotebook);
}

async function nbPopulateEnvSelect(envSel) {
  // Fill the notebook env dropdown from the prebuilt environments; mark the
  // current one selected and disable R (it cannot host a Python kernel).
  if (!S.currentId || !envSel) return;
  const fill = (envs, cur) => {
    envSel.innerHTML = "";
    (envs || []).forEach(e => {
      const notable = (e.notable && e.notable.length) ? " — " + e.notable.slice(0, 4).join("/") : "";
      const o = el("option", null, e.name + (e.runnable ? "" : " · R") + notable);
      o.value = e.name;
      if (!e.runnable) o.disabled = true;   // R env: use host.bash (Rscript) instead
      o.title = e.description || "";
      envSel.appendChild(o);
    });
    if (cur) envSel.value = cur;
  };
  if (_kc.id === S.currentId && _kc.envs) fill(_kc.envs, _kc.cur);  // immediate, no flicker
  if (_kc.envBusy) return;
  if (_kc.id === S.currentId && _kc.envs && (Date.now() - _kc.envAt) < 8000) return;  // env list rarely changes
  const sid = S.currentId;
  _kc.envBusy = true;
  let data; try { data = await api(`/frames/${sid}/environments`); } catch { _kc.envBusy = false; return; }
  _kc.envBusy = false;
  if (sid !== S.currentId) return;  // session switched during the fetch — drop the stale result
  if (_kc.id !== sid) { _kc.id = sid; _kc.st = null; }
  _kc.envs = data.environments || []; _kc.cur = data.current; _kc.envAt = Date.now();
  fill(_kc.envs, _kc.cur);
}

async function nbSwitchEnv(name, envSel) {
  // Switch this session's kernel into a prebuilt environment (restart into it).
  if (!S.currentId || !name) return;
  if (envSel) envSel.disabled = true;
  try {
    const r = await api(`/frames/${S.currentId}/kernel/env`, {
      method: "POST", body: JSON.stringify({ env: name }) });
    if (r.error) hint(t("nb.kernel.envSwitchFailed", r.error), true);
    else hint(t("nb.kernel.envSwitched", name));
  } catch (e) { hint(t("nb.kernel.envSwitchFailed", e.message), true); }
  if (envSel) envSel.disabled = false;
  invalidateKernelCache();  // env + generation changed — re-read state/env
  if (S.dock.open && S.activeTab === "notebook") renderNotebook();
}
// Display helpers for runtime-segment labels: the notebook groups cells by the
// raw kernel_id (the filter/grouping value) but shows a friendly label —
// "Python" or "Python — struct" — derived from it.
function kernelLabel(k) { k = k || "python"; return k.replace(/^python\b/i, "Python"); }
function kernelIdFromEnv(env) {  // stored kernel_id from a kernel-status env object
  // Prefer the server's canonical label so live cells group under the SAME chip
  // as reloaded ones (the server collapses the default env to "python" even when
  // OPENAI4S_DEFAULT_ENV names a non-base env — re-deriving from name would not).
  if (env && typeof env.kernel_id === "string" && env.kernel_id) return env.kernel_id;
  const n = (env && env.name || "").trim();
  if (!n || n === "python" || n === "base") return "python";
  return "python — " + n;
}
function renderNotebook() {
  const nb = $("#dock-notebook"); if (!nb) return;
  // Live-follow: if the user is already parked near the bottom, keep the newest
  // cell/output in view as the panel re-renders during a run (mirrors the chat's
  // auto-scroll). Measured BEFORE we tear the pane down. If they've scrolled up
  // to read, we leave their position alone.
  const body = nb.parentElement;  // .dock-body — the scroll container
  const follow = !body || (body.scrollHeight - body.scrollTop - body.clientHeight) < 120;
  // Bind a one-time scroll listener that tracks whether the user has scrolled up
  // to read (so live re-renders pause) and flushes any deferred render the moment
  // they return to the bottom.
  if (body && !body._nbScrollBound) {
    body._nbScrollBound = true;
    body.addEventListener("scroll", () => {
      const atBottom = (body.scrollHeight - body.scrollTop - body.clientHeight) < 120;
      S._nbReading = !atBottom;
      if (atBottom && S._nbDirty) { S._nbDirty = false; nbRender(); }
    }, { passive: true });
  }
  nb.innerHTML = "";
  let entries = (S.cells || []).slice();
  if (S.running && S.liveCells && S.liveCells.length) entries = entries.concat(S.liveCells);
  const kernels = []; entries.forEach(e => { const k = e.kernel_id || "python"; if (!kernels.includes(k)) kernels.push(k); });
  const chips = el("div", "kernel-chips");
  const mk = (k, label) => { const c = el("button", "kchip" + (((S.kernelFilter || null) === k) ? " on" : ""), label); c.onclick = () => { S.kernelFilter = k; renderNotebook(); }; return c; };
  chips.appendChild(mk(null, "All")); kernels.forEach(k => chips.appendChild(mk(k, kernelLabel(k))));
  const badge = el("div", "nb-live-badge" + (S.running ? " live" : " idle")); badge.appendChild(el("span", "ld")); badge.appendChild(el("span", null, S.running ? "Live" : "Idle")); badge.appendChild(iconEl("chevron-down", 14)); chips.appendChild(badge);
  nb.appendChild(chips);
  let shown = entries; if (S.kernelFilter) shown = entries.filter(e => (e.kernel_id || "python") === S.kernelFilter);
  if (!shown.length) nb.appendChild(el("div", "dock-empty", t("nb.empty")));
  else shown.forEach(e => nb.appendChild(cellNode(e)));
  // Read-only Notebook by default: the interactive REPL (input, env selector,
  // stop/start/restart/interrupt) is built ONLY when the server explicitly
  // enables it (developer flag repl_enabled). Otherwise render a passive,
  // non-interactive status strip. refreshKernelState runs in BOTH branches.
  const replEnabled = !!(_kc && _kc.st && _kc.st.repl_enabled);
  if (replEnabled) {
  const repl = el("div", "nb-repl");
  const rh = el("div", "nb-repl-head");
  const title = el("span", "nb-kernel-title", "kernel"); rh.appendChild(title);
  const rhr = el("div", "nb-repl-actions");
  // Prebuilt-environment selector: pick which built-in env the kernel runs in
  // (no per-task install). Switching restarts the kernel into it.
  const envSel = el("select", "nb-env-select");
  envSel.title = t("nb.env.selectTitle");
  envSel.disabled = !S.currentId;
  envSel.appendChild(el("option", null, t("nb.env.placeholder")));
  envSel.onchange = () => nbSwitchEnv(envSel.value, envSel);
  rhr.appendChild(envSel);
  const state = el("span", "kstate", "…"); rhr.appendChild(state);
  const mkBtn = (label, title, fn) => { const b = el("button", "kchip", label); b.title = title; b.disabled = !S.currentId; b.onclick = fn; rhr.appendChild(b); return b; };
  const bStop = mkBtn(t("nb.kernel.stopLabel"), t("nb.kernel.stopTitle"), () => kernelCtl("stop", bStop));
  const bStart = mkBtn(t("nb.kernel.startLabel"), t("nb.kernel.startTitle"), () => kernelCtl("start", bStart));
  const bRestart = mkBtn(t("nb.kernel.restartLabel"), t("nb.kernel.restartTitle"), () => kernelCtl("restart", bRestart));
  rh.appendChild(rhr); repl.appendChild(rh);
  // Revive banner — shown only when the kernel is stopped/absent.
  const revive = el("div", "nb-revive hidden");
  revive.appendChild(el("span", null, t("nb.revive.text")));
  const rbtn = el("button", "solid-btn small", t("nb.revive.startBtn")); rbtn.onclick = () => kernelCtl("start", bStart);
  revive.appendChild(rbtn);
  repl.appendChild(revive);
  refreshKernelState({ state, bStop, bStart, title, revive });
  nbPopulateEnvSelect(envSel);
  repl.appendChild(el("div", "nb-repl-body", t("nb.repl.body")));
  const pr = el("div", "nb-repl-prompt"); pr.appendChild(el("span", "pmt", ">>>")); const inp = el("input"); inp.placeholder = "run code in this kernel…"; inp.disabled = !S.currentId; inp.value = S._replDraft || "";
  const stop = el("button", "repl-stop hidden"); stop.title = t("nb.repl.interruptTitle"); stop.innerHTML = icon("stop", 15); stop.onclick = async () => { try { await api(`/frames/${S.currentId}/kernel/interrupt`, { method: "POST" }); hint(t("nb.repl.interruptSent")); } catch {} };
  inp.onkeydown = async (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault(); const code = inp.value.trim(); if (!code || !S.currentId) return;
    inp.disabled = true; stop.classList.remove("hidden"); S._replDraft = "";
    try { await api(`/frames/${S.currentId}/kernel/execute`, { method: "POST", body: JSON.stringify({ code }) }); }
    catch (err) { hint(t("nb.repl.execFailed", err.message), true); }
    stop.classList.add("hidden");
    invalidateKernelCache();  // running code may have started/advanced the kernel
    await loadExecutionLog(S.currentId); loadArtifacts(S.currentId);
    requestAnimationFrame(() => { const i2 = $("#dock-notebook input"); if (i2) { i2.disabled = false; i2.focus(); } });
  };
  inp.oninput = () => { S._replDraft = inp.value; };
  pr.appendChild(inp); pr.appendChild(stop); repl.appendChild(pr);
  nb.appendChild(repl);
  } else {
    // Passive status strip — no <input>, no <select>, no kernel-control buttons.
    // Shows the runtime label, a live/ended indicator and a one-line resume hint.
    // _paintStatusStrip (via refreshKernelState) keeps the indicator fresh.
    const strip = el("div", "nb-status");
    const sline = el("div", "nb-status-line", "…");
    strip.appendChild(sline);
    strip.appendChild(el("div", "nb-status-hint", t("nb.status.hint")));
    refreshKernelState({ strip: { line: sline } });
    nb.appendChild(strip);
  }
  // Keep following the live output as new code/figures stream in.
  if (S.running && follow && body) requestAnimationFrame(() => { body.scrollTop = body.scrollHeight; });
}
// Terminal tracebacks (and some libs) embed ANSI color escapes; strip them so
// the notebook shows clean text rather than "[0;31m…" noise.
const stripAnsi = (s) => String(s == null ? "" : s).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "");
// Format a cell error (a plain `traceback.format_exc()` string) as a labelled
// block: the last line — the `ExceptionType: message` — is surfaced as a
// one-line summary, with the full traceback below (collapsible when present).
function nbErrorBlock(raw) {
  const txt = stripAnsi(raw).replace(/\s+$/, "");
  const nonEmpty = txt.split("\n").filter(l => l.trim());
  const summary = nonEmpty.length ? nonEmpty[nonEmpty.length - 1].trim() : t("nb.error.default");
  const box = el("div", "nbc-error open");
  const head = el("div", "nbc-error-head");
  head.appendChild(iconEl("alert-triangle", 14));
  // Split "ExceptionType: message" → a red type pill + the message, so the error
  // reads at a glance instead of one long red line.
  const m = summary.match(/^([A-Za-z_][\w.]*(?:Error|Exception|Warning|Interrupt|Exit|Fault))\b:?\s*([\s\S]*)$/);
  if (m) {
    head.appendChild(el("span", "nbc-err-type", m[1]));
    if (m[2]) head.appendChild(el("span", "nbc-err-text", m[2]));
  } else {
    head.appendChild(el("span", "nbc-err-text", summary));
  }
  box.appendChild(head);
  if (nonEmpty.length > 1) {  // there's a traceback beyond the summary line
    const chev = iconEl("chevron-down", 13); chev.classList.add("nbc-error-chev"); head.appendChild(chev);
    head.classList.add("clickable");
    head.onclick = () => box.classList.toggle("open");
    const tb = el("pre", "nbc-error-tb"); tb.innerHTML = highlightTraceback(txt); box.appendChild(tb);
  }
  return box;
}
// Colour the two signal lines of a Python traceback: `File "...", line N, in fn`
// locations (blue) and the terminal `ExceptionType: message` (bold red).
function highlightTraceback(txt) {
  const lines = txt.split("\n");
  const lastIdx = (() => { for (let i = lines.length - 1; i >= 0; i--) if (lines[i].trim()) return i; return -1; })();
  return lines.map((ln, i) => {
    const e = esc(ln);
    if (/^\s*File ".*", line \d+/.test(ln)) return '<span class="tb-loc">' + e + '</span>';
    if (i === lastIdx && /^[A-Za-z_][\w.]*(Error|Exception|Warning|Interrupt|Exit|Fault)\b/.test(ln.trim())) return '<span class="tb-final">' + e + '</span>';
    return e;
  }).join("\n");
}
function cellNode(e) {
  const k = e.kernel_id || "python";
  const c = el("div", "notebook-cell" + (e.live ? " live" : ""));
  c.setAttribute("data-cell", e.cell_index != null ? e.cell_index : "");
  c.setAttribute("data-kernel", k);
  const st = e.status || (e.live ? "running" : "ok");
  const idx = e.cell_index != null ? e.cell_index : "…";
  c.appendChild(codeBlock(e.source || "", {
    lang: e.language || k,
    langLabel: (e.language || k) + " [" + idx + "]",
    status: st,
    env: e.environment || e.env || undefined
  }));
  if (e.stdout) { if (looksBinary(e.stdout)) c.appendChild(binElide(e.stdout.length)); else { const o = el("pre", "nbc-out"); o.textContent = e.stdout; c.appendChild(o); } }
  if (e.stderr) { if (looksBinary(e.stderr)) c.appendChild(binElide(e.stderr.length)); else { const er = el("pre", "nbc-err"); er.textContent = e.stderr; c.appendChild(er); } }
  if (e.error) c.appendChild(nbErrorBlock(e.error));
  (e.figures || []).forEach(f => { const im = el("img", "nbc-fig"); im.src = artUrlByName(f); im.onerror = () => im.remove(); c.appendChild(im); });
  // Inline-render tabular outputs (CSV/TSV the cell produced) as real tables, so
  // "表格" outputs show up rendered — not just as a filename pill.
  (e.files_written || []).filter(f => /\.(csv|tsv)$/i.test(f)).slice(0, 4).forEach(f => {
    const holder = el("div", "nbc-table-wrap"); holder.appendChild(el("div", "nbc-table-name", f.split("/").pop()));
    c.appendChild(holder); renderTableInto(holder, f);
  });
  const io = el("div", "nbc-io");
  (e.files_written || []).forEach(f => { const s = el("span", "io-w"); s.appendChild(iconEl("pencil", 12)); s.appendChild(el("span", null, f)); io.appendChild(s); });
  (e.files_read || []).forEach(f => { const s = el("span", "io-r"); s.appendChild(iconEl("arrow-down", 12)); s.appendChild(el("span", null, f)); io.appendChild(s); });
  if (io.children.length) c.appendChild(io);
  return c;
}
function scrollToCell(idx, kernel) {
  S.kernelFilter = kernel || null; renderNotebook();
  requestAnimationFrame(() => {
    const root = $("#dock-notebook");
    const node = (kernel && root.querySelector(`.notebook-cell[data-cell="${idx}"][data-kernel="${kernel}"]`)) || root.querySelector(`.notebook-cell[data-cell="${idx}"]`);
    if (node) { node.scrollIntoView({ behavior: "smooth", block: "center" }); node.classList.add("flash"); setTimeout(() => node.classList.remove("flash"), 1600); }
  });
}

/* ---------- Provenance tab (F5) ---------- */
async function loadLineage(a) {
  if (!a) return { interactions: [], dependency_mappings: { inputs: [] } };
  try { return await api(`/artifacts/${a.id}/lineage`); } catch { return { interactions: [], dependency_mappings: { inputs: [] } }; }
}
function provRow(label, files) {
  const d = el("div", "prov-row"); d.appendChild(el("span", "prov-lbl", label));
  const box = el("div", "prov-files"); (files || []).forEach(f => box.appendChild(el("span", "prov-pill", f))); d.appendChild(box); return d;
}
function showProvenance(a) {
  S.dockArtifact = a; S.provMode = true; S.provSub = S.provSub || "code";
  addOpenTab(a); setActiveTab(a.id);
  if (!S.lineage || S._lineageFor !== a.id) { loadLineage(a).then(l => { S.lineage = l; S._lineageFor = a.id; if (S.provMode && S.dockArtifact === a) renderViewer(); }); }
}
function renderProvenanceInto(v, a) {
  const tabs = el("div", "prov-subtabs");
  [["code", "Code"], ["exec", "Execution Log"], ["messages", "Messages"], ["environment", "Environment"], ["review", "Review"]].forEach(([k, lab]) => {
    const b = el("button", "prov-subtab" + (S.provSub === k ? " active" : ""), lab); b.onclick = () => { S.provSub = k; renderViewer(); }; tabs.appendChild(b);
  });
  v.appendChild(tabs);
  const body = el("div", "prov-body"); v.appendChild(body);
  const lin = (S._lineageFor === a.id) ? S.lineage : null;
  const cell = lin && (lin.interactions || []).find(i => i.kind === "cell");
  if (S.provSub === "code") {
    if (cell && cell.source) { body.appendChild(codeBlock(cell.source, { lang: cell.language || "python", langLabel: cell.language || "python", env: cell.environment })); }
    else if (!lin) body.appendChild(el("div", "dock-empty", t("common.loading")));
    else body.appendChild(el("div", "dock-empty", "Generating reproduction code…"));
  } else if (S.provSub === "exec") {
    const dl = el("a", "prov-dlbtn"); dl.appendChild(iconEl("download", 14)); dl.appendChild(el("span", null, "Download notebook")); dl.href = `/api/frames/${S.currentId}/execution-log`; dl.setAttribute("download", "notebook.json"); body.appendChild(dl);
    const cells = (S.cells || []); if (!cells.length) body.appendChild(el("div", "dock-empty", t("prov.exec.noRecords")));
    cells.forEach(e => body.appendChild(cellNode(e)));
  } else if (S.provSub === "environment") {
    renderProvEnvironment(body, a);
  } else if (S.provSub === "messages") {
    renderProvMessages(body);
  } else if (S.provSub === "review") {
    renderProvReview(body, a, lin);
  } else { body.appendChild(el("div", "dock-empty", "—")); }
}
// Provenance → Environment: the COMPLETE package manifest of the kernel that
// produced this artifact (Python version + every installed dist → version), like
// the reference daemon's Environment tab. Prefers the snapshot CAPTURED at the
// artifact's production run (bound per-version); falls back to a live freeze for
// uploads / pre-feature artifacts. Cached per artifact id so tab-flips don't refetch.
async function renderProvEnvironment(body, a) {
  body.appendChild(el("div", "dock-empty", t("prov.env.loadingSnapshot")));
  const key = (a && a.id) || "_live";
  S._envSnapById = S._envSnapById || {};
  let env;
  try {
    env = S._envSnapById[key] || (S._envSnapById[key] = await (
      a && a.id ? api(`/artifacts/${a.id}/environment`) : api("/kernel/environment")));
  }
  catch (e) { if (S.provMode && S.provSub === "environment") { body.innerHTML = ""; body.appendChild(el("div", "dock-empty", t("prov.env.loadFailed", e.message))); } return; }
  if (!S.provMode || S.provSub !== "environment") return;  // tab changed while loading
  body.innerHTML = "";
  const chip = (k, val) => { const c = el("span", "env-chip"); c.appendChild(el("span", "env-chip-k", k)); c.appendChild(el("span", "env-chip-v", val)); return c; };
  const pkgs = env.packages || [];
  const chips = el("div", "env-chips");
  chips.appendChild(chip("Environment", env.kind || "python"));
  chips.appendChild(chip(env.implementation || "Python", env.python_version || "?"));
  chips.appendChild(chip("Packages", String(env.package_count != null ? env.package_count : pkgs.length)));
  body.appendChild(chips);
  if (env.platform) body.appendChild(el("div", "env-plat", env.platform));
  // provenance honesty: say whether this is the recorded production env or a live fallback
  const captured = env.source !== "live";
  const note = el("div", "env-src" + (captured ? " ok" : " warn"));
  note.appendChild(iconEl(captured ? "package" : "clock", 13));
  note.appendChild(el("span", null, captured ? t("prov.env.recorded") : t("prov.env.liveFallback")));
  body.appendChild(note);
  const remote = env.remote || [];
  if (remote.length) {
    const rw = el("div", "env-remote"); rw.appendChild(el("div", "env-remote-h", t("prov.env.remoteTitle")));
    remote.forEach(r => {
      const e = r.env || {}; const rows = []; const push = (k, v) => { if (v != null && v !== "") rows.push([k, v]); };
      push(t("prov.env.remoteHost"), (r.host || "") + (e.hostname ? " · " + e.hostname : ""));
      push("GPU", e.gpu || ""); push("Engine", r.engine || "");
      push(t("prov.env.remoteEnv"), (e.conda_env ? e.conda_env + " · " : "") + "Python " + (e.python || "?"));
      if (e.packages) push(t("prov.env.remotePkgs"), Object.entries(e.packages).map(([k, v]) => k + " " + v).join(" · "));
      if (e.code) push(t("prov.env.remoteCode"), (e.code.repo || "") + " @ " + (e.code.git_commit ? String(e.code.git_commit).slice(0, 10) : "?") + (e.code.git_dirty ? " (dirty)" : "") + (e.code.wrapper_sha256 ? " · wrapper " + String(e.code.wrapper_sha256).slice(0, 10) : ""));
      if (e.model) push(t("prov.env.remoteModel"), (e.model.name || "") + (e.model.weights_sha256 ? " · sha " + String(e.model.weights_sha256).slice(0, 12) : "") + (e.model.weights_bytes ? " · " + (e.model.weights_bytes / 1e9).toFixed(2) + " GB" : ""));
      push(t("prov.env.remoteRun"), e.run_utc || "");
      const card = el("div", "env-remote-card"); card.appendChild(el("div", "env-remote-svc", (r.service || "job") + " · " + (r.host || "")));
      const tbl = el("table", "env-table"); const tb = el("tbody");
      rows.forEach(([k, v]) => { const tr = el("tr"); tr.appendChild(el("td", "env-pk", k)); tr.appendChild(el("td", "env-pv", v)); tb.appendChild(tr); });
      tbl.appendChild(tb); card.appendChild(tbl); rw.appendChild(card);
    });
    body.appendChild(rw);
  }
  if (!pkgs.length) { body.appendChild(el("div", "dock-empty", t("prov.env.noPackages"))); return; }
  const wrap = el("div", "env-tbl-wrap");
  const tbl = el("table", "env-table");
  const thead = el("thead"); const htr = el("tr"); htr.appendChild(el("th", null, "Package")); htr.appendChild(el("th", null, "Version")); thead.appendChild(htr); tbl.appendChild(thead);
  const tb = el("tbody");
  pkgs.forEach(p => { const tr = el("tr"); tr.appendChild(el("td", "env-pk", p.name || "")); tr.appendChild(el("td", "env-pv", p.version || "—")); tb.appendChild(tr); });
  tbl.appendChild(tb); wrap.appendChild(tbl); body.appendChild(wrap);
}
// Provenance → Messages: the conversation turns that led to this artifact (role +
// text), from the frame's stored message history.
async function renderProvMessages(body) {
  body.appendChild(el("div", "dock-empty", t("prov.msg.loading")));
  let msgs;
  try { const d = await api(`/frames/${S.currentId}/messages?from=0&limit=500`); msgs = (d && d.messages) || []; }
  catch (e) { if (S.provMode && S.provSub === "messages") { body.innerHTML = ""; body.appendChild(el("div", "dock-empty", t("prov.msg.loadFailed", e.message))); } return; }
  if (!S.provMode || S.provSub !== "messages") return;  // tab changed while loading
  body.innerHTML = "";
  if (!msgs.length) { body.appendChild(el("div", "dock-empty", t("prov.msg.noRecords"))); return; }
  msgs.forEach(m => {
    const role = m.role || "assistant";
    const row = el("div", "prov-msg " + role);
    const head = el("div", "prov-msg-h");
    head.appendChild(el("span", "prov-msg-role", role === "user" ? "User" : (role === "system" ? "System" : "Assistant")));
    if (m.created_at) head.appendChild(el("span", "prov-msg-t", ago(m.created_at)));
    row.appendChild(head);
    const txt = m.text || m.content || "";
    const md = el("div", "md prov-msg-b"); md.innerHTML = renderMd(txt); row.appendChild(md);
    body.appendChild(row);
  });
}
function renderProvReview(body, a, lin) {
  if (!lin) { body.appendChild(el("div", "dock-empty", t("common.loading"))); return; }
  const inter = lin.interactions || []; const cell = inter.find(i => i.kind === "cell");
  const inputs = (lin.dependency_mappings && lin.dependency_mappings.inputs) || (cell && cell.files_read) || [];
  if (!cell && !inputs.length) { body.appendChild(el("div", "dock-empty", t("prov.review.noLineage"))); return; }
  const card = el("div", "prov-card");
  if (cell) {
    card.appendChild(el("div", "prov-h", t("prov.review.producedBy", (cell.cell_index != null ? cell.cell_index : "?"))));
    card.appendChild(el("div", "prov-meta", (cell.language || "python") + " · " + (cell.exit_status || cell.status || "ok") + (cell.kernel_id ? (" · " + cell.kernel_id) : "")));
    if ((cell.files_written || []).length) card.appendChild(provRow("wrote", cell.files_written));
    const reads = (cell.files_read && cell.files_read.length) ? cell.files_read : inputs;
    if (reads.length) card.appendChild(provRow("reads / inputs", reads));
    const link = el("a", "prov-link"); link.appendChild(iconEl("arrow-left", 14)); link.appendChild(el("span", null, t("prov.review.viewCode"))); link.onclick = () => { S.provMode = false; setActiveTab("notebook"); scrollToCell(cell.cell_index, cell.kernel_id); }; card.appendChild(link);
  } else if (inputs.length) card.appendChild(provRow("reads / inputs", inputs));
  body.appendChild(card);
  const save = inter.find(i => i.kind === "save");
  if (save && save.at) body.appendChild(el("div", "prov-meta", t("prov.review.saved", ago(save.at))));
}
function openKetcher() { $("#modal-title").textContent = t("ketcher.modalTitle"); $("#modal-download").style.display = "none"; const body = $("#modal-body"); body.innerHTML = ""; const f = el("iframe"); f.src = (S.sandboxOrigin || "") + "/ketcher"; f.setAttribute("allow", "clipboard-read; clipboard-write"); body.appendChild(f); $("#modal").classList.remove("hidden"); }

/* ---------- upload ---------- */
function uploadFiles(files) {
  [...files].forEach(file => { const rd = new FileReader(); rd.onload = async () => { const b64 = (rd.result.split(",")[1]) || "";
    try { if (!S.currentId) { const f = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: S.project || undefined, model: S.defaultModel }) }); S.currentId = f.id; sub(f.id); await loadSessions(); await openConversation(f.id, S.project); }
      await api("/uploads", { method: "POST", body: JSON.stringify({ filename: file.name, content_base64: b64, project_id: S.project || undefined, frame_id: S.currentId }) });
      loadArtifacts(S.currentId); hint(t("upload.uploaded", file.name));
    } catch (e) { hint(t("upload.failed", e.message), true); } }; rd.readAsDataURL(file); });
}

/* ---------- notes ---------- */
function effProject() { if (S.project) return S.project; const f = S.sessions.find(x => x.id === S.currentId); return (f && f.project_id) || null; }
async function loadNotes() { const pid = effProject(); const list = $("#notes-list"); if (!pid) { list.innerHTML = '<div class="files-empty">' + t("notes.emptyNoProject") + '</div>'; return; }
  try { const d = await api(`/projects/${pid}/notes`); const notes = (d && d.notes) || (Array.isArray(d) ? d : []); renderNotes(notes); } catch { list.innerHTML = ""; } }
function renderNotes(notes) { const list = $("#notes-list"); list.innerHTML = ""; if (!notes.length) { list.appendChild(el("div", "files-empty", t("notes.empty"))); return; }
  notes.forEach(n => { const d = el("div", "note"); d.appendChild(el("div", null, n.content || n.text || "")); d.appendChild(el("div", "nt-time", ago(n.updated_at || n.created_at))); const del = el("span", "nt-del"); del.appendChild(iconEl("trash-2", 14)); del.onclick = async () => { try { await api(`/notes/${n.note_id || n.id}`, { method: "DELETE" }); } catch {} loadNotes(); }; d.appendChild(del); list.appendChild(d); }); }
async function addNote() { const pid = effProject(); const inp = $("#note-input"); const c = inp.value.trim(); if (!pid || !c) return; try { await api(`/projects/${pid}/notes`, { method: "POST", body: JSON.stringify({ content: c }) }); inp.value = ""; loadNotes(); } catch {} }

/* ---------- customize ---------- */
// Voice dictation via the browser SpeechRecognition API — appends the
// transcript to the composer. No backend needed; works in Chrome/Edge/Safari.
let _rec = null;
function micDictate() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const btn = $("#mic-btn");
  if (!SR) { hint(t("toast.micUnsupported"), true); return; }
  if (_rec) { try { _rec.stop(); } catch {} _rec = null; btn.classList.remove("on"); return; }
  const r = new SR(); _rec = r; r.lang = (navigator.language || "zh-CN"); r.interimResults = true; r.continuous = true;
  const comp = $("#composer"); const base = comp.value;
  btn.classList.add("on"); hint(t("toast.micListening"));
  r.onresult = (e) => { let txt = ""; for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript; comp.value = (base ? base + " " : "") + txt; grow(); };
  r.onerror = (e) => { hint(t("toast.micError", (e.error || "")), true); btn.classList.remove("on"); _rec = null; };
  r.onend = () => { btn.classList.remove("on"); _rec = null; };
  try { r.start(); } catch { hint(t("toast.micStartFailed"), true); btn.classList.remove("on"); _rec = null; }
}

// Layout / density switcher (the dashboard 布局 button). Cycles comfortable →
// compact → wide, persisted so it survives reloads.
function applyLayout(name) { document.body.classList.remove("layout-compact", "layout-wide"); if (name === "compact") document.body.classList.add("layout-compact"); else if (name === "wide") document.body.classList.add("layout-wide"); }
// setLayout (explicit choice, used by the 通用/General settings tab) is defined near custGeneral.

/* ---------- ⌘K command palette ---------- */
const PAL = { open: false, items: [], idx: 0, el: null };
function openPalette() {
  if (PAL.open) return;
  PAL.open = true;
  const ov = el("div", "palette-ov");
  const box = el("div", "palette");
  const inp = el("input", "palette-input"); inp.placeholder = t("palette.searchPlaceholder"); inp.spellcheck = false;
  const list = el("div", "palette-list");
  box.appendChild(inp); box.appendChild(list); ov.appendChild(box); document.body.appendChild(ov);
  PAL.el = ov; PAL.listEl = list;
  ov.onclick = (e) => { if (e.target === ov) closePalette(); };
  inp.addEventListener("input", () => palSearch(inp.value));
  inp.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") { e.preventDefault(); closePalette(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); PAL.idx = Math.min(PAL.idx + 1, PAL.items.length - 1); palRender(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); PAL.idx = Math.max(PAL.idx - 1, 0); palRender(); }
    else if (e.key === "Enter") { e.preventDefault(); palPick(PAL.idx); }
  });
  palSearch("");
  inp.focus();
}
function closePalette() { if (PAL.el) PAL.el.remove(); PAL.el = null; PAL.open = false; PAL.items = []; PAL.idx = 0; }
function palActions() {
  return [
    { group: t("palette.group.commands"), label: t("palette.action.newSession"), icon: "plus", run: () => newSession() },
    { group: t("palette.group.commands"), label: t("palette.action.newProject"), icon: "plus", run: () => $("#proj-modal").classList.remove("hidden") },
    { group: t("palette.group.commands"), label: t("palette.action.openNotebook"), icon: "notebook", run: () => setActiveTab("notebook") },
    { group: t("palette.group.commands"), label: t("palette.action.customize"), icon: "sliders", run: () => openCust() },
    { group: t("palette.group.commands"), label: t("palette.action.backHome"), icon: "arrow-left", run: () => showDashboard() },
  ];
}
async function palSearch(query) {
  const q = (query || "").trim().toLowerCase();
  const gen = (PAL.gen = (PAL.gen || 0) + 1);  // discard out-of-order responses
  const items = [];
  // actions (filtered)
  palActions().forEach(a => { if (!q || a.label.toLowerCase().includes(q)) items.push(a); });
  // skills (local)
  const sk = await loadSkillsCatalog();
  sk.filter(s => !q || (s.name || "").toLowerCase().includes(q) || (s.displayName || "").toLowerCase().includes(q))
    .slice(0, 6).forEach(s => items.push({ group: t("palette.group.skills"), label: s.displayName || s.name, sub: s.description || "", icon: "sparkles", run: () => { closePalette(); const c = $("#composer"); c.value = (c.value ? c.value + " " : "") + "/" + s.name + " "; c.focus(); grow(); } }));
  // sessions + artifacts (backend search)
  if (q) {
    try {
      const r = await api("/search?q=" + encodeURIComponent(q));
      (r.sessions || []).slice(0, 8).forEach(s => items.push({ group: t("conv.title.default"), label: s.name || s.task_summary || t("conv.title.default"), icon: "message-square", run: () => { closePalette(); openConversation(s.id, s.project_id); } }));
      (r.artifacts || []).slice(0, 8).forEach(a => items.push({ group: t("palette.group.artifacts"), label: a.filename, sub: a.content_type || "", icon: "file", run: () => { closePalette(); if (a.root_frame_id) openConversation(a.root_frame_id, a.project_id).then(() => dockTab("files")); } }));
    } catch {}
  }
  if (gen !== PAL.gen) return;  // a newer keystroke superseded this response
  PAL.items = items; PAL.idx = 0; palRender();
}
function palRender() {
  const list = PAL.listEl; if (!list) return; list.innerHTML = "";
  if (!PAL.items.length) { list.appendChild(el("div", "palette-empty", t("palette.empty"))); return; }
  let lastGroup = null;
  PAL.items.forEach((it, i) => {
    if (it.group !== lastGroup) { lastGroup = it.group; list.appendChild(el("div", "palette-group", it.group)); }
    const row = el("div", "palette-item" + (i === PAL.idx ? " on" : ""));
    if (it.icon) { const ic = el("span", "pi-ic"); ic.innerHTML = icon(it.icon, 15); row.appendChild(ic); }
    const t = el("div", "pi-txt"); t.appendChild(el("div", "pi-label", it.label)); if (it.sub) t.appendChild(el("div", "pi-sub", it.sub)); row.appendChild(t);
    row.onmouseenter = () => { PAL.idx = i; [...list.querySelectorAll(".palette-item")].forEach(x => x.classList.remove("on")); row.classList.add("on"); };
    row.onclick = () => palPick(i);
    list.appendChild(row);
  });
}
function palPick(i) { const it = PAL.items[i]; if (it && it.run) it.run(); }

function openCust(tab) { $("#cust").classList.remove("hidden"); custTab(tab || "general"); }
function custTab(tab) { document.querySelectorAll(".cust-tab").forEach(t => t.classList.toggle("active", t.dataset.tab === tab)); const c = $("#cust-content"); c.innerHTML = t("common.loading");
  ({ general: custGeneral, skills: custSkills, specialists: custSpecialists, connectors: custConnectors, agents: custSpecialists, permissions: custPermissions, compute: custCompute, network: custNetwork, memory: custMemory, models: custModels }[tab])(c); }
// Permissions — manage the opencode-style tool-call approval rules per scope.
async function custPermissions(c) {
  c.innerHTML = ""; c.appendChild(hdr(t("cust.perm.title"), t("cust.perm.desc")));
  if (!S.currentId) { c.appendChild(el("div", "cust-note", t("cust.perm.noSessionNote"))); c.appendChild(permResetRow()); return; }
  let data;
  try { data = await api(`/frames/${S.currentId}/permissions`); }
  catch (e) { c.appendChild(el("div", "cust-note", t("versions.load.err", e.message))); c.appendChild(permResetRow()); return; }
  const meta = {
    global: { scope: "global", scope_id: "", label: t("cust.perm.scope.global") },
    project: { scope: "project", scope_id: data.project_id, label: t("cust.perm.scope.project") },
    conversation: { scope: "conversation", scope_id: data.root_frame_id, label: t("cust.perm.scope.conversation") },
  };
  ["conversation", "project", "global"].forEach(k => {
    const g = meta[k]; const rules = (data.rules && data.rules[k]) || [];
    const sec = el("div", "perm-sec");
    sec.appendChild(el("div", "perm-sec-h", g.label));
    if (!rules.length) sec.appendChild(el("div", "cust-note", t("cust.perm.noRules")));
    rules.forEach(r => sec.appendChild(permRuleRow(r, g)));
    sec.appendChild(permAddRow(g));
    c.appendChild(sec);
  });
  c.appendChild(permResetRow());
}
function permDecSelect(val, onChange) {
  const sel = el("select", "perm-dec");
  [["allow", t("perm.btn.allow")], ["ask", t("cust.perm.decision.ask")], ["deny", t("perm.btn.deny")]].forEach(([v, t]) => { const o = el("option", null, t); o.value = v; if (v === val) o.selected = true; sel.appendChild(o); });
  sel.onchange = () => onChange(sel.value); return sel;
}
function permRuleRow(r, g) {
  const row = el("div", "perm-rule");
  row.appendChild(el("span", "perm-rtool", r.tool));
  row.appendChild(el("span", "perm-rpat mono", r.pattern));
  row.appendChild(permDecSelect(r.decision, async (v) => {
    try { await api("/permissions", { method: "POST", body: JSON.stringify({ scope: g.scope, scope_id: g.scope_id, tool: r.tool, pattern: r.pattern, decision: v }) }); hint(t("toast.perm.ruleUpdated")); }
    catch (e) { hint(t("toast.perm.updateFailed", e.message), true); }
  }));
  const del = el("button", "icon-ghost"); del.innerHTML = icon("trash-2", 15); del.title = t("common.delete");
  del.onclick = async () => { try { await api(`/permissions/${r.rule_id}`, { method: "DELETE" }); custTab("permissions"); } catch (e) { hint(t("toast.deleteFailed", e.message), true); } };
  row.appendChild(del); return row;
}
function permAddRow(g) {
  const row = el("div", "perm-rule perm-add");
  const tool = el("input", "perm-in"); tool.placeholder = t("cust.perm.toolPlaceholder");
  const pat = el("input", "perm-in"); pat.placeholder = t("cust.perm.patternPlaceholder"); pat.value = "*";
  let dec = "ask"; const sel = permDecSelect(dec, v => dec = v);
  const add = el("button", "outline-btn small", t("common.add"));
  add.onclick = async () => {
    if (!tool.value.trim()) { hint(t("toast.perm.enterTool"), true); return; }
    try { await api("/permissions", { method: "POST", body: JSON.stringify({ scope: g.scope, scope_id: g.scope_id, tool: tool.value.trim(), pattern: pat.value.trim() || "*", decision: dec }) }); custTab("permissions"); }
    catch (e) { hint(t("toast.addFailed", e.message), true); }
  };
  row.appendChild(tool); row.appendChild(pat); row.appendChild(sel); row.appendChild(add); return row;
}
function permResetRow() {
  const row = el("div", "cust-row");
  const info = el("div", "info"); info.appendChild(el("div", "nm", t("cust.perm.resetName"))); info.appendChild(el("div", "ds", t("cust.perm.resetDesc")));
  row.appendChild(info);
  const b = el("button", "outline-btn small", t("cust.perm.resetBtn"));
  b.onclick = async () => { if (!confirm(t("cust.perm.resetConfirm"))) return; try { await api("/permissions/reset", { method: "POST" }); custTab("permissions"); hint(t("toast.perm.resetDone")); } catch (e) { hint(t("toast.failed", e.message), true); } };
  row.appendChild(b); return row;
}
// General / global preferences — the dashboard 设置 (settings) entry lands here.
async function custGeneral(c) {
  c.innerHTML = ""; c.appendChild(hdr(t("cust.general.title"), t("cust.general.desc")));
  // Layout density (was the old 布局 cycler; now an explicit, labeled choice)
  const row = el("div", "cust-row"); const info = el("div", "info");
  info.appendChild(el("div", "nm", t("cust.general.layoutName"))); info.appendChild(el("div", "ds", t("cust.general.layoutDesc")));
  row.appendChild(info);
  const seg = el("div", "seg"); const cur = localStorage.getItem("os-layout") || "comfortable";
  [["comfortable", t("cust.general.layout.comfortable")], ["compact", t("cust.general.layout.compact")], ["wide", t("cust.general.layout.wide")]].forEach(([val, label]) => { const b = el("button", "seg-btn" + (val === cur ? " active" : ""), label); b.onclick = () => { setLayout(val); custTab("general"); }; seg.appendChild(b); });
  row.appendChild(seg); c.appendChild(row);
  // Interface language (中文 / English)
  const lrow = el("div", "cust-row"); const linfo = el("div", "info");
  linfo.appendChild(el("div", "nm", t("cust.general.language"))); linfo.appendChild(el("div", "ds", t("cust.general.languageDesc")));
  lrow.appendChild(linfo);
  const lseg = el("div", "seg");
  [["zh", "中文"], ["en", "English"]].forEach(([v, lbl]) => { const b = el("button", "seg-btn" + (LANG === v ? " active" : ""), lbl); b.onclick = () => setLang(v); lseg.appendChild(b); });
  lrow.appendChild(lseg); c.appendChild(lrow);
  // LLM / API key status with a shortcut to the Models tab
  let conf = {}; try { conf = await api("/config/llm"); } catch {}
  const kr = el("div", "cust-row"); const ki = el("div", "info"); ki.appendChild(el("div", "nm", t("cust.general.modelKeyName"))); ki.appendChild(el("div", "ds", conf.has_api_key ? (t("cust.general.apiKeyConfigured") + (conf.model ? "（" + conf.model + "）" : "")) : t("cust.models.key.missing"))); kr.appendChild(ki); const go = el("button", "outline-btn small", t("cust.general.configureBtn")); go.onclick = () => custTab("models"); kr.appendChild(go); c.appendChild(kr);
}
function setLayout(name) { localStorage.setItem("os-layout", name); applyLayout(name); hint(t("toast.layout", ({ comfortable: t("cust.general.layout.comfortable"), compact: t("cust.general.layout.compact"), wide: t("cust.general.layout.wide") }[name] || name))); }
async function custSkills(c) { try { const d = await api("/skills/catalog"); const skills = (d && d.skills) || []; c.innerHTML = ""; c.appendChild(hdr(t("palette.group.skills"), t("cust.skills.desc", skills.length)));
  const bar = el("div", "cust-row"); const bi = el("div", "info"); const acts = el("div", "cust-actrow");
  const nb = el("button", "outline-btn small", t("cust.skills.newBtn")); nb.onclick = () => skillEditor(null);
  const ib = el("button", "outline-btn small", t("cust.skills.importBtn")); ib.onclick = () => skillImport();
  acts.appendChild(nb); acts.appendChild(ib); bi.appendChild(el("div", "nm", t("cust.skills.yourSkills"))); bi.appendChild(acts); bar.appendChild(bi); c.appendChild(bar);
  skills.forEach(s => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, s.displayName || s.name)); if (s.origin === "user") { nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", "user")); } info.appendChild(nm); info.appendChild(el("div", "ds", s.description || "")); row.appendChild(info);
    // "Use in chat" — insert /skillname into the composer so the skill can be
    // invoked directly from Customize (previously there was no way to run one).
    const useBtn = el("button", "icon-ghost"); useBtn.title = t("skill.useInChat"); useBtn.innerHTML = icon("message-square", 15); useBtn.onclick = () => insertSkillMention(s.name); row.appendChild(useBtn);
    if (s.editable) { const eb = el("button", "icon-ghost"); eb.title = t("common.edit"); eb.innerHTML = icon("pencil", 15); eb.onclick = () => skillEditor(s.name); row.appendChild(eb); const db = el("button", "icon-ghost"); db.title = t("common.delete"); db.innerHTML = icon("trash-2", 15); db.onclick = async () => { if (!confirm(t("cust.skills.deleteConfirm", s.name))) return; try { await api(`/skills/${encodeURIComponent(s.name)}`, { method: "DELETE" }); S.skillsCatalog = null; custTab("skills"); } catch (e) { hint(t("toast.deleteFailed", e.message), true); } }; row.appendChild(db); }
    const tg = el("button", "toggle" + (s.enabled !== false ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api(`/skills/catalog/${encodeURIComponent(s.name)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) }); } catch {} }; row.appendChild(tg); c.appendChild(row); });
} catch (e) { c.innerHTML = t("versions.load.err", e.message); } }
// Insert a "/skillname" mention into the composer from the Skills settings tab,
// close settings, and focus the composer so the skill can be invoked directly.
function insertSkillMention(name) {
  $("#cust").classList.add("hidden");
  if ($("#workspace").classList.contains("hidden")) { hint(t("skill.insertedToast", name)); return; }
  const c = $("#composer"); if (!c) return;
  const cur = c.value || "";
  c.value = (cur && !/\s$/.test(cur) ? cur + " " : cur) + "/" + name + " ";
  grow(); c.focus(); c.setSelectionRange(c.value.length, c.value.length);
  hint(t("skill.insertedToast", name));
}
async function skillEditor(name) {
  S._modalMode = "skill";
  let cur = { name: "", description: "", body: "" };
  if (name) { try { cur = await api(`/skills/${encodeURIComponent(name)}`); } catch {} }
  $("#modal-title").textContent = name ? t("skill.editTitle", name) : t("skill.newTitle");
  $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "";
  const form = el("div", "skill-form");
  const nameIn = el("input", "cust-input"); nameIn.placeholder = t("skill.namePlaceholder"); nameIn.value = cur.name || name || ""; if (name) nameIn.disabled = true;
  const descIn = el("input", "cust-input"); descIn.placeholder = t("skill.descPlaceholder"); descIn.value = cur.description || "";
  const bodyIn = el("textarea", "skill-body"); bodyIn.placeholder = t("skill.bodyPlaceholder"); bodyIn.value = cur.body || "";
  form.appendChild(el("label", "skill-lbl", t("cust.connectors.namePlaceholder"))); form.appendChild(nameIn);
  form.appendChild(el("label", "skill-lbl", t("skill.label.desc"))); form.appendChild(descIn);
  form.appendChild(el("label", "skill-lbl", t("skill.label.body"))); form.appendChild(bodyIn);
  const save = el("button", "solid-btn", t("skill.saveBtn"));
  save.onclick = async () => { const nm = nameIn.value.trim(); if (!nm) { hint(t("toast.skill.enterName"), true); return; } save.disabled = true; save.textContent = t("common.saving"); try { if (name) await api(`/skills/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ description: descIn.value, body: bodyIn.value }) }); else await api("/skills", { method: "POST", body: JSON.stringify({ name: nm, description: descIn.value, body: bodyIn.value }) }); S.skillsCatalog = null; $("#modal").classList.add("hidden"); hint(t("toast.skill.saved", nm)); custTab("skills"); } catch (e) { save.disabled = false; save.textContent = t("skill.saveBtn"); hint(t("artifact.save.err", e.message), true); } };
  const fa = el("div", "form-actions"); fa.appendChild(save); form.appendChild(fa);
  body.appendChild(form); $("#modal").classList.remove("hidden");
}
async function skillImport() {
  S._modalMode = "skill-import";
  $("#modal-title").textContent = t("skill.importTitle");
  $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "";
  const form = el("div", "skill-form");
  const ta = el("textarea", "skill-body"); ta.placeholder = t("skill.importPlaceholder"); ta.style.minHeight = "260px";
  form.appendChild(el("label", "skill-lbl", t("skill.importLabel"))); form.appendChild(ta);
  const save = el("button", "solid-btn", t("skill.importBtn"));
  save.onclick = async () => { if (!ta.value.trim()) return; save.disabled = true; save.textContent = t("cust.importing"); try { const r = await api("/skills/import", { method: "POST", body: JSON.stringify({ content: ta.value }) }); if (r.error) throw new Error(r.error); S.skillsCatalog = null; $("#modal").classList.add("hidden"); hint(t("toast.skill.imported", (r.name || ""))); custTab("skills"); } catch (e) { save.disabled = false; save.textContent = t("skill.importBtn"); hint(t("toast.importFailed", e.message), true); } };
  const fa = el("div", "form-actions"); fa.appendChild(save); form.appendChild(fa);
  body.appendChild(form); $("#modal").classList.remove("hidden");
}
async function custSpecialists(c) { try {
  const d = await api("/specialists"); const builtin = (d && d.builtin) || []; const custom = (d && d.specialists) || [];
  c.innerHTML = ""; c.appendChild(hdr(t("cust.tab.specialists"), t("cust.specialists.desc")));
  const bar = el("div", "cust-row"); const bi = el("div", "info"); bi.appendChild(el("div", "nm", t("cust.specialists.yours"))); const acts = el("div", "cust-actrow"); const nb = el("button", "outline-btn small", t("cust.specialists.newBtn")); nb.onclick = () => specialistEditor(null); acts.appendChild(nb); bi.appendChild(acts); bar.appendChild(bi); c.appendChild(bar);
  custom.forEach(s => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, s.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", "custom")); info.appendChild(nm); info.appendChild(el("div", "ds", s.description || "")); row.appendChild(info); const eb = el("button", "icon-ghost"); eb.title = t("common.edit"); eb.innerHTML = icon("pencil", 15); eb.onclick = () => specialistEditor(s.name); row.appendChild(eb); const db = el("button", "icon-ghost"); db.title = t("common.delete"); db.innerHTML = icon("trash-2", 15); db.onclick = async () => { if (!confirm(t("cust.specialists.deleteConfirm", s.name))) return; try { await api(`/specialists/${encodeURIComponent(s.name)}`, { method: "DELETE" }); custTab("specialists"); } catch (e) { hint(t("toast.deleteFailed", e.message), true); } }; row.appendChild(db); c.appendChild(row); });
  c.appendChild(el("div", "cust-subhead", t("cust.specialists.builtinRoles")));
  builtin.forEach(ag => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, ag.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", ag.mode || "agent")); if (ag.supportsPlanMode) { nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", "plan")); } info.appendChild(nm); info.appendChild(el("div", "ds", ag.description || "")); row.appendChild(info); const tg = el("button", "toggle" + (ag.enabled !== false ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api(`/agents/${encodeURIComponent(ag.name)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) }); } catch {} }; row.appendChild(tg); c.appendChild(row); });
} catch (e) { c.innerHTML = t("versions.load.err", e.message); } }
async function specialistEditor(name) {
  S._modalMode = "specialist";
  let cur = { name: "", description: "", system_prompt: "" };
  if (name) { try { cur = await api(`/specialists/${encodeURIComponent(name)}`); } catch {} }
  $("#modal-title").textContent = name ? t("specialist.editTitle", name) : t("specialist.newTitle"); $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = ""; const form = el("div", "skill-form");
  const nameIn = el("input", "cust-input"); nameIn.placeholder = t("specialist.namePlaceholder"); nameIn.value = cur.name || name || ""; if (name) nameIn.disabled = true;
  const descIn = el("input", "cust-input"); descIn.placeholder = t("specialist.descPlaceholder"); descIn.value = cur.description || "";
  const spIn = el("textarea", "skill-body"); spIn.placeholder = t("specialist.promptPlaceholder"); spIn.value = cur.system_prompt || "";
  form.appendChild(el("label", "skill-lbl", t("cust.connectors.namePlaceholder"))); form.appendChild(nameIn); form.appendChild(el("label", "skill-lbl", t("skill.label.desc"))); form.appendChild(descIn); form.appendChild(el("label", "skill-lbl", t("specialist.label.systemPrompt"))); form.appendChild(spIn);
  const save = el("button", "solid-btn", t("specialist.saveBtn")); save.onclick = async () => { const nm = nameIn.value.trim(); if (!nm) { hint(t("toast.specialist.enterName"), true); return; } save.disabled = true; save.textContent = t("common.saving"); const b = { name: nm, description: descIn.value, system_prompt: spIn.value }; try { if (name) await api(`/specialists/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(b) }); else await api("/specialists", { method: "POST", body: JSON.stringify(b) }); $("#modal").classList.add("hidden"); hint(t("toast.specialist.saved", nm)); custTab("specialists"); } catch (e) { save.disabled = false; save.textContent = t("specialist.saveBtn"); hint(t("artifact.save.err", e.message), true); } };
  const fa = el("div", "form-actions"); fa.appendChild(save); form.appendChild(fa); body.appendChild(form); $("#modal").classList.remove("hidden");
}
async function custConnectors(c) { try {
  const d = await api("/connectors"); const conns = (d && d.connectors) || [];
  c.innerHTML = ""; c.appendChild(hdr(t("cust.tab.connectors"), t("cust.connectors.desc")));
  conns.forEach(k => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, k.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", k.connector_id)); info.appendChild(nm); info.appendChild(el("div", "ds", (k.description || "") + "  ·  " + (k.command_display || ""))); row.appendChild(info);
    const pb = el("button", "outline-btn small", t("cust.connectors.test")); pb.onclick = async () => { pb.disabled = true; pb.textContent = t("cust.connectors.testing"); try { const r = await api(`/connectors/${k.connector_id}/probe`, { method: "POST" }); hint(r.ok ? (t("toast.connectors.probeOk", (r.tools || []).map(t => t.name).join("、"))) : (t("toast.failed", (r.error || "")))); } catch (e) { hint(t("toast.connectors.testFailed", e.message), true); } pb.disabled = false; pb.textContent = t("cust.connectors.test"); }; row.appendChild(pb);
    const tg = el("button", "toggle" + (k.enabled ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api(`/connectors/${k.connector_id}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) }); } catch {} }; row.appendChild(tg);
    const db = el("button", "icon-ghost"); db.title = t("common.delete"); db.innerHTML = icon("trash-2", 15); db.onclick = async () => { if (!confirm(t("cust.connectors.deleteConfirm", k.name))) return; try { await api(`/connectors/${k.connector_id}`, { method: "DELETE" }); custTab("connectors"); } catch {} }; row.appendChild(db); c.appendChild(row); });
  // directory (one-click add)
  c.appendChild(el("div", "cust-subhead", t("cust.connectors.fromDirectory")));
  let dir = { directory: [] }; try { dir = await api("/connectors/directory"); } catch {}
  (dir.directory || []).forEach(item => { if (conns.some(k => k.connector_id === item.id)) return; const row = el("div", "cust-row"); const info = el("div", "info"); info.appendChild(el("div", "nm", item.name)); info.appendChild(el("div", "ds", item.description || "")); row.appendChild(info); const add = el("button", "outline-btn small", t("common.add")); add.onclick = async () => { try { await api("/connectors", { method: "POST", body: JSON.stringify({ connector_id: item.id, name: item.name, description: item.description, command: item.command }) }); hint(t("toast.connectors.added", item.name)); custTab("connectors"); } catch (e) { hint(t("toast.addFailed", e.message), true); } }; row.appendChild(add); c.appendChild(row); });
  // custom add
  const add = el("div", "cust-row"); const ai = el("div", "info"); ai.appendChild(el("div", "nm", t("cust.connectors.customAddName"))); const ad = el("div", "job-submit"); const nameIn = el("input", "cust-input"); nameIn.placeholder = t("cust.connectors.namePlaceholder"); nameIn.style.flex = "0 0 120px"; const cmdIn = el("input", "cust-input"); cmdIn.placeholder = t("cust.connectors.cmdPlaceholder"); const go = el("button", "solid-btn small", t("common.add")); go.onclick = async () => { const nm = nameIn.value.trim(); const cmd = cmdIn.value.trim(); if (!nm || !cmd) return; try { await api("/connectors", { method: "POST", body: JSON.stringify({ name: nm, command: cmd.split(/\s+/) }) }); nameIn.value = cmdIn.value = ""; custTab("connectors"); } catch (e) { hint(t("toast.addFailed", e.message), true); } }; ad.appendChild(nameIn); ad.appendChild(cmdIn); ad.appendChild(go); ai.appendChild(ad); add.appendChild(ai); c.appendChild(add);
} catch (e) { c.innerHTML = t("versions.load.err", e.message); } }
async function renderRemoteGPU(c) {
  let info; try { info = await api("/compute/remote"); } catch (e) { return; }
  const hd = el("div", "cust-row"); hd.innerHTML = `<div class="info"><div class="nm">${t("cust.remote.title")}</div><div class="ds">${t("cust.remote.desc")}</div></div>`; c.appendChild(hd);
  const hosts = (info && info.hosts) || [];
  hosts.forEach(h => {
    const row = el("div", "cust-row"); const dot = h.reachable ? "🟢" : "🔴";
    const gpus = h.gpus || (h.reachable ? "" : t("cust.remote.unreachable"));
    const caps = (h.capabilities || []).map(cp => `<span style="display:inline-block;padding:1px 7px;margin:3px 4px 0 0;border-radius:8px;background:rgba(127,127,127,.18);font-size:11px">${cp.name}${cp.engine ? " · " + cp.engine : ""}${cp.verified ? " ✓" : ""}</span>`).join("");
    const inf = el("div", "info");
    inf.innerHTML = `<div class="nm">${dot} ${h.label || h.alias} <span style="opacity:.55;font-weight:400">· ${h.provider}</span></div><div class="ds">${gpus}${caps ? "<br>" + t("cust.remote.services") + " " + caps : "<br><span style='opacity:.6'>" + t("cust.remote.noservices") + "</span>"}</div>`;
    const rm = el("button", "outline-btn small", t("common.remove"));
    rm.onclick = async () => { if (!confirm(t("cust.remote.confirmRemove", h.alias))) return; try { await api("/compute/remote/" + encodeURIComponent(h.alias), { method: "DELETE" }); custTab("compute"); } catch (e) { hint(e.message, true); } };
    row.appendChild(inf); row.appendChild(rm); c.appendChild(row);
  });
  const taken = new Set(hosts.map(h => h.alias));
  const avail = ((info && info.available_aliases) || []).filter(a => !taken.has(a));
  const addRow = el("div", "cust-row"); const ai = el("div", "info"); ai.appendChild(el("div", "nm", t("cust.remote.addName")));
  const ds = el("div", "ds job-submit"); const sel = el("select", "cust-input");
  const o0 = el("option"); o0.value = ""; o0.textContent = avail.length ? t("cust.remote.pickAlias") : t("cust.remote.noAlias"); sel.appendChild(o0);
  avail.forEach(a => { const o = el("option"); o.value = a; o.textContent = a; sel.appendChild(o); });
  const add = el("button", "solid-btn small", t("common.add"));
  add.onclick = async () => { const alias = sel.value; if (!alias) return; add.disabled = true; add.textContent = t("cust.remote.testing"); try { const r = await api("/compute/remote", { method: "POST", body: JSON.stringify({ alias }) }); hint(r.reachable ? t("cust.remote.added", alias, r.gpus || "") : t("cust.remote.addedUnreachable", alias)); custTab("compute"); } catch (e) { hint(e.message, true); add.disabled = false; add.textContent = t("common.add"); } };
  ds.appendChild(sel); ds.appendChild(add); ai.appendChild(ds); addRow.appendChild(ai); c.appendChild(addRow);
}
async function custCompute(c) { try { const gpu = await api("/compute/gpu"); const env = await api("/environments/status").catch(() => ({ environments: [] })); const host = await api("/compute/local/hostinfo").catch(() => ({})); c.innerHTML = ""; c.appendChild(hdr(t("cust.compute.title"), t("cust.compute.desc"))); const hostRow = el("div", "cust-row"); hostRow.innerHTML = `<div class="info"><div class="nm">${t("cust.compute.host")}</div><div class="ds">${t("cust.compute.hostDetail", host.python || "?", host.machine || "", host.cpu_count || "?", host.ram_gb || "?", host.disk_free_gb || "?")}</div></div>`; c.appendChild(hostRow); const g = el("div", "cust-row"); g.innerHTML = `<div class="info"><div class="nm">GPU</div><div class="ds">${gpu.available ? (gpu.gpu_name || t("cust.compute.gpuAvailable")) : t("cust.compute.gpuUnavailable")}</div></div>`; c.appendChild(g); await renderRemoteGPU(c); const envs = env.environments || []; envs.forEach(e => { const row = el("div", "cust-row"); const inst = (e.packages || []).filter(p => p.installed); row.innerHTML = `<div class="info"><div class="nm">${t("cust.compute.kernelLabel", e.language, e.status === "installing" ? t("cust.compute.kernelInstalling") : t("cust.compute.kernelReady"))}</div><div class="ds">${t("cust.compute.preinstalledDetail", e.package_count, inst.slice(0, 18).map(p => p.name).join("、") + (inst.length > 18 ? " …" : ""))}</div></div>`; c.appendChild(row); }); const ins = el("div", "cust-row"); const info = el("div", "info"); info.appendChild(el("div", "nm", t("cust.compute.installExtraName"))); const dsc = el("div", "ds"); const inp = el("input"); inp.placeholder = t("cust.compute.installPlaceholder"); inp.className = "cust-input"; const btn = el("button", "outline-btn small", t("cust.compute.installBtn")); btn.onclick = async () => { const pkgs = inp.value.trim().split(/\s+/).filter(Boolean); if (!pkgs.length) return; btn.disabled = true; btn.textContent = t("cust.compute.installingBtn"); try { const r = S.currentId ? await api(`/frames/${S.currentId}/kernel/install`, { method: "POST", body: JSON.stringify({ packages: pkgs, restart: true }) }) : await api(`/kernel/install`, { method: "POST", body: JSON.stringify({ packages: pkgs }) }); hint(r.ok ? (t("step.env.installed", (r.installed || []).join("、") + (r.restarted ? t("cust.compute.kernelRestarted") : ""))) : (t("toast.compute.installFailed", ((r.failed && r.failed[0] && r.failed[0].error) || t("toast.compute.installSeeLogs"))))); if (r.ok) S._envSnapById = {}; custTab("compute"); } catch (e) { hint(t("toast.compute.installFailed", e.message), true); } btn.disabled = false; btn.textContent = t("cust.compute.installBtn"); }; dsc.appendChild(inp); dsc.appendChild(btn); info.appendChild(dsc); ins.appendChild(info); c.appendChild(ins); await renderJobs(c); } catch (e) { c.innerHTML = t("versions.load.err", e.message); } }
async function renderJobs(c) {
  c.appendChild(hdr(t("cust.jobs.title"), t("cust.jobs.desc")));
  const sub = el("div", "cust-row"); const si = el("div", "info"); si.appendChild(el("div", "nm", t("cust.jobs.submitName")));
  const row = el("div", "job-submit");
  const sel = el("select", "cust-input"); sel.style.flex = "0 0 92px"; ["bash", "python"].forEach(k => { const o = el("option", null, k); o.value = k; sel.appendChild(o); });
  const cmd = el("input", "cust-input"); cmd.placeholder = t("cust.jobs.cmdPlaceholder");
  const go = el("button", "solid-btn small", t("cust.jobs.runBtn"));
  go.onclick = async () => { const command = cmd.value.trim(); if (!command) return; go.disabled = true; try { await api("/compute/jobs", { method: "POST", body: JSON.stringify({ command, kind: sel.value }) }); cmd.value = ""; await refreshJobList(list); } catch (e) { hint(t("toast.submitFailed", e.message), true); } go.disabled = false; };
  row.appendChild(sel); row.appendChild(cmd); row.appendChild(go); si.appendChild(row); sub.appendChild(si); c.appendChild(sub);
  const list = el("div", "job-list"); c.appendChild(list);
  await refreshJobList(list);
}
async function refreshJobList(list) {
  let d; try { d = await api("/compute/jobs"); } catch { d = { jobs: [] }; }
  const jobs = (d && d.jobs) || []; list.innerHTML = "";
  if (!jobs.length) { list.appendChild(el("div", "dock-empty", t("cust.jobs.empty"))); return; }
  let anyRunning = false;
  jobs.forEach(j => {
    if (j.status === "running" || j.status === "queued") anyRunning = true;
    const row = el("div", "cust-row"); const info = el("div", "info");
    const nm = el("div", "nm"); nm.appendChild(el("span", "job-badge " + j.status, j.status)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "job-cmd", (j.kind + "  " + j.command).slice(0, 80))); info.appendChild(nm);
    info.appendChild(el("div", "ds", (j.duration_s != null ? j.duration_s + "s" : "") + (j.exit_code != null ? " · exit " + j.exit_code : "")));
    row.appendChild(info);
    const view = el("button", "outline-btn small", t("cust.jobs.viewOutput")); view.onclick = () => showJobOutput(j.id); row.appendChild(view);
    if (j.status === "running" || j.status === "queued") { const cx = el("button", "outline-btn small", t("common.cancel")); cx.onclick = async () => { try { await api(`/compute/jobs/${j.id}/cancel`, { method: "POST" }); await refreshJobList(list); } catch {} }; row.appendChild(cx); }
    list.appendChild(row);
  });
  // reschedule only while the Customize panel is actually open on this list
  if (anyRunning) { clearTimeout(S._jobPoll); S._jobPoll = setTimeout(() => { if (!$("#cust").classList.contains("hidden") && document.querySelector(".job-list") === list) refreshJobList(list); }, 1500); }
}
async function showJobOutput(id) {
  const mode = "job:" + id; S._modalMode = mode;
  $("#modal-title").textContent = t("job.outputTitle", id); $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "<div class='dock-empty'>" + t("common.loading") + "</div>"; $("#modal").classList.remove("hidden");
  const load = async () => { if (S._modalMode !== mode || $("#modal").classList.contains("hidden")) return; let d; try { d = await api(`/compute/jobs/${id}`); } catch (e) { body.innerHTML = t("job.outputLoadFailed"); return; } if (S._modalMode !== mode) return; body.innerHTML = ""; const pre = el("pre", "job-output", d.output || t("job.outputEmpty")); body.appendChild(pre); if (d.status === "running" || d.status === "queued") setTimeout(load, 1200); };
  load();
}
// Global web-search API key (Tavily). The endpoint is fixed; only the key is
// user-editable. Persisted server-side and read by webtools at search time.
async function searchKeyRow(c) {
  let sc = {}; try { sc = await api("/search/config"); } catch {}
  const row = el("div", "cust-row"); const info = el("div", "info");
  info.appendChild(el("div", "nm", t("cust.search.name")));
  info.appendChild(el("div", "ds", (sc.api_key_configured ? t("cust.search.set") : t("cust.search.unset")) + " · " + (sc.endpoint || "https://api.tavily.com/search")));
  const kin = el("input", "cust-input"); kin.type = "password"; kin.placeholder = t("cust.search.ph"); kin.autocomplete = "off";
  const sv = el("button", "solid-btn small", t("common.save"));
  sv.onclick = async () => { const k = kin.value.trim(); if (!k) return; sv.disabled = true; try { await api("/search/config", { method: "POST", body: JSON.stringify({ api_key: k }) }); hint(t("cust.search.saved")); kin.value = ""; custTab("network"); } catch (e) { sv.disabled = false; hint(e.message, true); } };
  const sub = el("div", "job-submit"); sub.appendChild(kin); sub.appendChild(sv); info.appendChild(sub);
  row.appendChild(info); c.appendChild(row);
}
async function custNetwork(c) { try { const d = await api("/preferences/builtin-allowlist"); c.innerHTML = ""; c.appendChild(hdr(t("cust.network.title"), t("cust.network.desc"))); const master = el("div", "cust-row"); const mi = el("div", "info"); mi.appendChild(el("div", "nm", t("cust.network.allowName"))); mi.appendChild(el("div", "ds", d.enabled ? t("cust.network.enabledDesc") : t("cust.network.disabledDesc"))); master.appendChild(mi); const tg = el("button", "toggle" + (d.enabled ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { const r = await api("/network/status", { method: "PUT", body: JSON.stringify({ enabled: on }) }); hint(r.enabled ? t("toast.network.enabled") : t("toast.network.disabled")); } catch {} }; master.appendChild(tg); c.appendChild(master); await searchKeyRow(c); ((d && d.groups) || []).forEach(g => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, g.name || g.label)); info.appendChild(nm); const box = el("div", "ds"); (g.domains || []).slice(0, 12).forEach(dm => box.appendChild(el("span", "pill", dm))); info.appendChild(box); row.appendChild(info); c.appendChild(row); }); } catch (e) { c.innerHTML = t("versions.load.err", e.message); } }
async function custMemory(c) { try {
  const m = await api("/memory/enabled");
  const mem = await api("/memory?project_id=all").catch(() => ({ memories: [] }));
  const cats = await api("/memory/categories?project_id=all").catch(() => ({ categories: [] }));
  c.innerHTML = ""; c.appendChild(hdr(t("cust.memory.title"), t("cust.memory.desc")));
  const master = el("div", "cust-row"); const mi = el("div", "info"); mi.appendChild(el("div", "nm", t("cust.memory.enableName"))); mi.appendChild(el("div", "ds", m.enabled ? t("cust.memory.enabledDesc") : t("cust.memory.disabledDesc"))); master.appendChild(mi); const tg = el("button", "toggle" + (m.enabled ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api("/memory/enabled", { method: "PUT", body: JSON.stringify({ enabled: on }) }); hint(on ? t("toast.memory.enabled") : t("toast.memory.disabled")); } catch {} }; master.appendChild(tg); c.appendChild(master);
  // add with category
  const add = el("div", "cust-row"); const ai = el("div", "info"); ai.appendChild(el("div", "nm", t("cust.memory.addName"))); const ad = el("div", "job-submit");
  const catSel = el("select", "cust-input"); catSel.style.flex = "0 0 120px"; ["user", "project", "preference", "fact", "general"].forEach(k => { const o = el("option", null, k); o.value = k; catSel.appendChild(o); });
  const inp = el("input", "cust-input"); inp.placeholder = t("cust.memory.contentPlaceholder");
  const btn = el("button", "solid-btn small", t("common.save")); btn.onclick = async () => { const v = inp.value.trim(); if (!v) return; try { await api("/memory", { method: "POST", body: JSON.stringify({ content: v, block: catSel.value }) }); inp.value = ""; custTab("memory"); } catch (e) { hint(t("artifact.save.err", e.message), true); } };
  ad.appendChild(catSel); ad.appendChild(inp); ad.appendChild(btn); ai.appendChild(ad); add.appendChild(ai); c.appendChild(add);
  // category chips
  const catList = (cats.categories || []);
  if (catList.length) { const cr = el("div", "cust-row"); const ci = el("div", "info"); ci.appendChild(el("div", "nm", t("cust.memory.categories"))); const box = el("div", "ds"); catList.forEach(k => box.appendChild(el("span", "pill", (k.block || "general") + " · " + k.count))); ci.appendChild(box); cr.appendChild(ci); c.appendChild(cr); }
  // memories grouped by block
  const groups = {}; (mem.memories || []).forEach(x => { const b = x.block || "general"; (groups[b] = groups[b] || []).push(x); });
  Object.keys(groups).sort().forEach(block => {
    c.appendChild(el("div", "cust-subhead", block));
    groups[block].forEach(x => { const row = el("div", "cust-row"); const info = el("div", "info"); info.appendChild(el("div", "ds", x.content || "")); row.appendChild(info); const del = el("button", "icon-ghost"); del.appendChild(iconEl("trash-2", 14)); del.onclick = async () => { try { await api(`/memory/${x.memory_id}`, { method: "DELETE" }); custTab("memory"); } catch {} }; row.appendChild(del); c.appendChild(row); });
  });
  if (!(mem.memories || []).length) c.appendChild(el("div", "dock-empty", t("cust.memory.empty")));
} catch (e) { c.innerHTML = t("versions.load.err", e.message); } }
async function custModels(c) {
  c.innerHTML = ""; c.appendChild(hdr(t("cust.tab.models"), t("cust.models.subtitle2")));
  let data = { profiles: [], active_id: "", known_providers: [] };
  try { data = await api("/model-profiles"); } catch (e) { c.appendChild(el("div", "dock-empty", t("versions.load.err", e.message))); return; }
  let editing = null;  // set to a profile object when editing that row

  // --- add / edit form ---
  const head = el("div", "cust-subhead", t("cust.models.addHeading"));
  c.appendChild(head);
  const form = el("div", "skill-form");
  const nameIn = el("input", "cust-input"); nameIn.placeholder = t("cust.models.namePlaceholder");
  const provIn = el("input", "cust-input"); provIn.placeholder = t("cust.models.providerPlaceholder2"); provIn.setAttribute("list", "os-provs");
  const dl = el("datalist"); dl.id = "os-provs"; (data.known_providers || []).forEach(p => { const o = el("option"); o.value = p; dl.appendChild(o); });
  const baseIn = el("input", "cust-input"); baseIn.placeholder = t("cust.models.baseUrlPlaceholder");
  const modelIn = el("input", "cust-input"); modelIn.placeholder = t("cust.models.modelPlaceholder2");
  const keyIn = el("input", "cust-input"); keyIn.type = "password"; keyIn.placeholder = "API Key"; keyIn.autocomplete = "off";
  form.appendChild(dl);
  form.appendChild(el("label", "skill-lbl", t("cust.connectors.namePlaceholder"))); form.appendChild(nameIn);
  form.appendChild(el("label", "skill-lbl", t("label.provider"))); form.appendChild(provIn);
  form.appendChild(el("label", "skill-lbl", "Base URL")); form.appendChild(baseIn);
  form.appendChild(el("label", "skill-lbl", t("label.model"))); form.appendChild(modelIn);
  form.appendChild(el("label", "skill-lbl", "API Key")); form.appendChild(keyIn);
  const save = el("button", "solid-btn", t("cust.models.addBtn"));
  const cancel = el("button", "outline-btn small", t("cust.models.cancelEdit")); cancel.style.display = "none";
  const resetForm = () => { editing = null; nameIn.value = provIn.value = baseIn.value = modelIn.value = keyIn.value = ""; keyIn.placeholder = "API Key"; save.textContent = t("cust.models.addBtn"); head.textContent = t("cust.models.addHeading"); cancel.style.display = "none"; };
  const startEdit = (p) => { editing = p; nameIn.value = p.name || ""; provIn.value = p.provider || ""; baseIn.value = p.base_url || ""; modelIn.value = p.model || ""; keyIn.value = ""; keyIn.placeholder = p.has_api_key ? t("cust.models.keyPlaceholderSet") : t("cust.models.keyPlaceholderUnset"); save.textContent = t("cust.models.updateBtn"); head.textContent = t("cust.models.editHeading", (p.name || p.id)); cancel.style.display = ""; nameIn.focus(); c.scrollTop = 0; };
  cancel.onclick = resetForm;
  save.onclick = async () => {
    const nm = nameIn.value.trim(); if (!nm) { hint(t("toast.specialist.enterName"), true); nameIn.focus(); return; }
    save.disabled = true; const label = save.textContent; save.textContent = t("common.saving");
    const body = { name: nm, provider: provIn.value.trim(), base_url: baseIn.value.trim(), model: modelIn.value.trim() };
    if (keyIn.value) body.api_key = keyIn.value;
    try {
      if (editing) { await api(`/model-profiles/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) }); hint(t("toast.models.updated", nm)); }
      else { await api("/model-profiles", { method: "POST", body: JSON.stringify(body) }); hint(t("toast.models.added", nm)); }
      if (editing && editing.id === data.active_id) { refreshKeyBanner(); await loadModels(); }
      custTab("models");
    } catch (e) { save.disabled = false; save.textContent = label; hint(t("artifact.save.err", e.message), true); }
  };
  const fa = el("div", "form-actions"); fa.appendChild(save); fa.appendChild(cancel); form.appendChild(fa);
  c.appendChild(form);

  // --- configured profiles ---
  c.appendChild(el("div", "cust-subhead", t("cust.models.configuredHeading")));
  const profs = data.profiles || [];
  if (!profs.length) { c.appendChild(el("div", "dock-empty", t("cust.models.empty2"))); return; }
  profs.forEach(p => {
    const row = el("div", "cust-row prof-row"); const info = el("div", "info");
    const nm = el("div", "nm"); nm.appendChild(el("span", null, p.name || p.id));
    const isActive = p.id === data.active_id;
    if (isActive) { nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", t("cust.models.activePill"))); }
    info.appendChild(nm);
    const bits = []; if (p.provider) bits.push(p.provider); if (p.model) bits.push(p.model); bits.push(p.has_api_key ? t("cust.models.hasKey") : t("cust.models.noKey"));
    info.appendChild(el("div", "ds", bits.join(" · ") + (p.base_url ? "  ·  " + p.base_url : "")));
    row.appendChild(info);
    if (!isActive) { const use = el("button", "outline-btn small", t("cust.models.setActive")); use.onclick = async () => { use.disabled = true; try { await api(`/model-profiles/${p.id}/activate`, { method: "POST" }); hint(t("toast.models.switched", (p.name || p.id))); S.defaultModel = p.model || S.defaultModel; await loadModels(); refreshKeyBanner(); custTab("models"); } catch (e) { use.disabled = false; hint(t("toast.switchFailed", e.message), true); } }; row.appendChild(use); } else { row.appendChild(el("div", "col-spacer")); }
    const edit = el("button", "outline-btn small", t("common.edit")); edit.onclick = () => startEdit(p); row.appendChild(edit);
    const del = el("button", "icon-ghost"); del.title = t("common.delete"); del.appendChild(iconEl("trash-2", 14)); del.onclick = async () => { if (!confirm(t("model.delete.confirm", (p.name || p.id)))) return; try { await api(`/model-profiles/${p.id}`, { method: "DELETE" }); hint(t("toast.deleted")); if (isActive) { refreshKeyBanner(); await loadModels(); } custTab("models"); } catch (e) { hint(t("toast.deleteFailed", e.message), true); } }; row.appendChild(del);
    c.appendChild(row);
  });
}
const hdr = (h, s) => { const d = el("div"); d.appendChild(el("div", "cust-h", h)); d.appendChild(el("div", "cust-sub", s)); return d; };

/* ---------- helpers ---------- */
// A small self-contained markdown renderer. Two design goals matter here:
//  1) STREAMING SAFETY — a fenced code block whose closing ``` hasn't arrived
//     yet must still render as code, not fall through to the block parser (which
//     would turn `# comment` lines into <h1> and leak a literal ```lang line).
//  2) inline code spans are tokenized out first so their contents are never
//     touched by emphasis/link processing.
var MD_KEYWORDS = {
  python: "False None True and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case self print len range enumerate zip map filter open int float str list dict set tuple bool sum min max abs sorted reversed type isinstance super",
  javascript: "function return if else for while do const let var new class extends super import from export default async await yield try catch finally throw typeof instanceof in of this null undefined true false void delete switch case break continue static get set",
  bash: "if then else elif fi for while until do done case esac function in select time echo export local return set unset read source alias",
  r: "if else for while repeat function return break next in TRUE FALSE NULL NA Inf NaN library require",
  sql: "select from where group by order having join inner left right outer on as insert into values update set delete create table drop alter index and or not null distinct limit union all",
  _default: "if else for while return function class import from export const let var def new try catch finally throw switch case break continue true false null undefined and or not in is with as async await yield"
};
var MD_LINE_COMMENT = { python: "#", bash: "#", r: "#", yaml: "#", toml: "#", ruby: "#", javascript: "//", sql: "--" };
var MD_BLOCK_COMMENT = { javascript: ["/*", "*/"] };
function mdLang(l) {
  l = (l || "").toLowerCase();
  return ({ py: "python", python: "python", js: "javascript", javascript: "javascript", ts: "javascript", typescript: "javascript", jsx: "javascript", tsx: "javascript", node: "javascript", json: "javascript", sh: "bash", bash: "bash", shell: "bash", zsh: "bash", console: "bash", r: "r", rlang: "r", sql: "sql", yaml: "yaml", yml: "yaml", toml: "toml", ini: "toml", rb: "ruby", ruby: "ruby" })[l] || l;
}
var _mdKwCache = {};
function mdKw(lang) {
  var c = mdLang(lang);
  if (_mdKwCache[c]) return _mdKwCache[c];
  var s = new Set((MD_KEYWORDS[c] || MD_KEYWORDS._default).split(/\s+/));
  _mdKwCache[c] = s; return s;
}
// Lightweight, language-aware tokenizer for code blocks. Returns escaped HTML
// with <span class="tok-*"> wrappers; textContent stays byte-identical to the
// source so the copy button can read it back verbatim.
function mdHighlight(code, lang) {
  code = String(code == null ? "" : code);
  if (!code) return "";
  if (code.length > 24000) return esc(code); // don't tokenize huge blobs every frame
  var c = mdLang(lang), kw = mdKw(lang);
  var lc = MD_LINE_COMMENT[c] || null, bc = MD_BLOCK_COMMENT[c] || null, py = c === "python";
  var reIdent = /[A-Za-z_$@][\w$]*/y, reNum = /0[xX][0-9a-fA-F]+|\d[\d_]*\.?\d*(?:[eE][+-]?\d+)?[jJ]?/y;
  var i = 0, n = code.length, out = "";
  var sp = function (cls, s) { return '<span class="tok-' + cls + '">' + esc(s) + '</span>'; };
  while (i < n) {
    var ch = code[i];
    if (lc && code.startsWith(lc, i)) { var j = code.indexOf("\n", i); if (j < 0) j = n; out += sp("com", code.slice(i, j)); i = j; continue; }
    if (bc && code.startsWith(bc[0], i)) { var j = code.indexOf(bc[1], i); j = j < 0 ? n : j + bc[1].length; out += sp("com", code.slice(i, j)); i = j; continue; }
    if (py && (code.startsWith('"""', i) || code.startsWith("'''", i))) { var q = code.slice(i, i + 3), j = code.indexOf(q, i + 3); j = j < 0 ? n : j + 3; out += sp("str", code.slice(i, j)); i = j; continue; }
    if (ch === '"' || ch === "'" || ch === "`") { var j = i + 1; while (j < n && code[j] !== ch) { if (code[j] === "\\") j++; j++; } j = Math.min(n, j + 1); out += sp("str", code.slice(i, j)); i = j; continue; }
    if (ch >= "0" && ch <= "9") { reNum.lastIndex = i; var mn = reNum.exec(code); var tk = mn ? mn[0] : ch; out += sp("num", tk); i += tk.length; continue; }
    if (/[A-Za-z_$@]/.test(ch)) { reIdent.lastIndex = i; var mi = reIdent.exec(code); var w = mi ? mi[0] : ch; i += w.length; if (w[0] === "@") out += sp("fn", w); else if (kw.has(w)) out += sp("kw", w); else if (code[i] === "(") out += sp("fn", w); else out += esc(w); continue; }
    out += esc(ch); i++;
  }
  return out;
}
function mdCodeBlock(code, lang) {
  var label = (lang || "").trim();
  return '<div class="codeblock">'
    + '<div class="cb-head"><span class="cb-lang">' + esc(label || "text") + '</span>'
    + '<button class="cb-copy" type="button" title="' + t("code.copy.title") + '">' + icon("copy", 13) + '<span class="cb-copy-t">' + t("msgAction.copy") + '</span></button></div>'
    + '<pre><code>' + mdHighlight(code, label) + '</code></pre></div>';
}
var MDC0 = String.fromCharCode(0xE000), MDC1 = String.fromCharCode(0xE001);
var _mdCodeRestore = new RegExp(MDC0 + "(\\d+)" + MDC1, "g");
function mdInline(t) {
  t = String(t == null ? "" : t);
  var codes = [];
  t = t.replace(/`([^`]+)`/g, function (m, c) { codes.push(c); return MDC0 + (codes.length - 1) + MDC1; });
  t = esc(t);
  t = t.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, '<img alt="$1" src="$2">');
  t = t.replace(/\[([^\]]+)\]\(((?:https?:|mailto:|\/|#)[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  t = t.replace(/\*\*\*([^*]+?)\*\*\*/g, "<strong><em>$1</em></strong>");
  t = t.replace(/\*\*([^*]+?)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/(^|[^\w*])__([^_]+?)__(?!\w)/g, "$1<strong>$2</strong>");
  t = t.replace(/(^|[^*])\*([^*\n]+?)\*/g, "$1<em>$2</em>");
  t = t.replace(/(^|[^\w_])_([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>");
  t = t.replace(/~~([^~]+?)~~/g, "<del>$1</del>");
  t = t.replace(_mdCodeRestore, function (m, k) { return "<code>" + esc(codes[+k]) + "</code>"; });
  return t;
}
function mdBuildList(items, cur) {
  var ordered = items[cur.v].ordered, indent = items[cur.v].indent;
  var html = "<" + (ordered ? "ol" : "ul") + ">";
  while (cur.v < items.length && items[cur.v].indent >= indent) {
    if (items[cur.v].indent > indent) break;
    var text = items[cur.v].text; cur.v++;
    var nested = "";
    if (cur.v < items.length && items[cur.v].indent > indent) nested = mdBuildList(items, cur);
    html += "<li>" + mdInline(text) + nested + "</li>";
  }
  return html + "</" + (ordered ? "ol" : "ul") + ">";
}
function mdList(lines, start, n) {
  var itemRe = /^(\s*)([-*+]|\d+[.)])[ \t]+(.*)$/;
  var items = [], i = start;
  while (i < n) {
    var m = lines[i].match(itemRe);
    if (m) { items.push({ indent: m[1].replace(/\t/g, "  ").length, ordered: /\d/.test(m[2]), text: m[3] }); i++; }
    else if (!lines[i].trim()) {
      var k = i + 1; while (k < n && !lines[k].trim()) k++;
      if (k < n && itemRe.test(lines[k])) { i = k; continue; }
      break;
    } else if (/^\s+\S/.test(lines[i]) && items.length) { items[items.length - 1].text += " " + lines[i].trim(); i++; }
    else break;
  }
  return { html: mdBuildList(items, { v: 0 }), next: i };
}
function renderMd(src) {
  var lines = String(src == null ? "" : src).replace(/\r\n?/g, "\n").split("\n");
  var n = lines.length, i = 0, html = "";
  var fenceRe = /^(\s*)(`{3,}|~{3,})[ \t]*([\w+#.\-]*)[ \t]*$/;
  var listRe = /^(\s*)([-*+]|\d+[.)])[ \t]+/;
  var hrRe = /^\s*([-*_])[ \t]*(?:\1[ \t]*){2,}$/;
  // Table delimiter row, matched cell-by-cell so there is no nested-quantifier
  // regex to catastrophically backtrack (ReDoS-safe). A cell is `:?-+:?` padded.
  var cellDelimRe = /^[ \t]*:?-+:?[ \t]*$/;
  var isDelimRow = function (s) {
    var tr = s.trim();
    if (tr.indexOf("-") === -1) return false;
    if (tr.charAt(0) === "|") tr = tr.slice(1);
    if (tr.charAt(tr.length - 1) === "|") tr = tr.slice(0, -1);
    var parts = tr.split("|");
    for (var j = 0; j < parts.length; j++) if (!cellDelimRe.test(parts[j])) return false;
    return true;
  };
  var looksTable = function (idx) { return lines[idx].indexOf("|") !== -1 && idx + 1 < n && isDelimRow(lines[idx + 1]); };
  while (i < n) {
    var line = lines[i];
    var fm = line.match(fenceRe);
    if (fm) {
      var fchar = fm[2][0], flen = fm[2].length, lang = fm[3] || "";
      var code = []; i++;
      // Closing fence detected without a dynamically-built RegExp (regex-injection
      // safe): a line that trims to >= flen of the same fence char and nothing else.
      var isClose = function (s) {
        var tr = s.trim();
        if (tr.length < flen) return false;
        for (var j = 0; j < tr.length; j++) if (tr.charAt(j) !== fchar) return false;
        return true;
      };
      while (i < n && !isClose(lines[i])) { code.push(lines[i]); i++; }
      if (i < n) i++; // consume the closing fence when it exists (unclosed = stream still open)
      html += mdCodeBlock(code.join("\n"), lang);
      continue;
    }
    if (!line.trim()) { i++; continue; }
    var hm = line.match(/^(#{1,6})[ \t]+(.*?)[ \t]*#*$/);
    if (hm) { var lv = hm[1].length; html += "<h" + lv + ">" + mdInline(hm[2]) + "</h" + lv + ">"; i++; continue; }
    if (hrRe.test(line)) { html += "<hr>"; i++; continue; }
    if (/^\s*>\s?/.test(line)) {
      var q = []; while (i < n && /^\s*>\s?/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
      html += "<blockquote>" + renderMd(q.join("\n")) + "</blockquote>"; continue;
    }
    if (looksTable(i)) {
      var cells = function (r) { return r.trim().replace(/^\||\|$/g, "").split("|").map(function (x) { return x.trim(); }); };
      var head = cells(lines[i]); i += 2;
      var t = "<table><thead><tr>" + head.map(function (c) { return "<th>" + mdInline(c) + "</th>"; }).join("") + "</tr></thead><tbody>";
      while (i < n && lines[i].indexOf("|") !== -1 && lines[i].trim()) { var r = cells(lines[i]); t += "<tr>" + head.map(function (_, ci) { return "<td>" + mdInline(r[ci] || "") + "</td>"; }).join("") + "</tr>"; i++; }
      html += t + "</tbody></table>"; continue;
    }
    if (listRe.test(line)) { var lr = mdList(lines, i, n); html += lr.html; i = lr.next; continue; }
    var para = [line]; i++;
    while (i < n && lines[i].trim() && !listRe.test(lines[i]) && !fenceRe.test(lines[i]) && !hrRe.test(lines[i]) && !/^(#{1,6}[ \t]|\s*>)/.test(lines[i]) && !looksTable(i)) { para.push(lines[i]); i++; }
    html += "<p>" + mdInline(para.join(" ")) + "</p>";
  }
  return html;
}
function parseTable(text, a) { const nm = (a.filename || "").toLowerCase(); if (nm.endsWith(".json") || /^\s*[\[{]/.test(text)) { try { let j = JSON.parse(text); if (!Array.isArray(j)) j = j.rows || j.data || j.candidates || j.items || []; if (Array.isArray(j) && j.length && typeof j[0] === "object") return j; } catch {} return null; } const lines = text.replace(/\r/g, "").split("\n").filter(l => l.trim()); if (lines.length < 2) return null; const cols = csv(lines[0]); return lines.slice(1).map(l => { const v = csv(l); const o = {}; cols.forEach((c, i) => o[c] = v[i] ?? ""); return o; }); }
function csv(line) { const o = []; let cur = "", q = false; for (let i = 0; i < line.length; i++) { const c = line[i]; if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; } else { if (c === '"') q = true; else if (c === ",") { o.push(cur); cur = ""; } else cur += c; } } o.push(cur); return o.map(s => s.trim()); }
function ago(iso) { if (!iso) return ""; const t = new Date(iso).getTime(); if (isNaN(t)) return ""; const d = (Date.now() - t) / 1000; if (d < 60) return "just now"; if (d < 3600) return (d / 60 | 0) + "m"; if (d < 86400) return (d / 3600 | 0) + "h"; return (d / 86400 | 0) + "d"; }
function bytes(b) { b = b || 0; if (b < 1024) return b + " B"; if (b < 1048576) return (b / 1024).toFixed(1) + " KB"; return (b / 1048576).toFixed(1) + " MB"; }
function hint(t, err, spin) { const h = $("#composer-hint"); h.innerHTML = ""; if (!t) return; if (spin) { h.appendChild(iconEl("loader", 13, "spin")); h.appendChild(document.createTextNode(" ")); } const s = el("span", null, t); if (err) s.style.color = "var(--danger)"; h.appendChild(s); }
function enableComposer(on) { $("#composer").disabled = !on; }
function messagesAtBottom(m, pad) { return !m || (m.scrollHeight - m.scrollTop - m.clientHeight) < (pad || 80); }
function paintJumpPill() { const m = $("#messages"), pill = $("#jump-pill"); if (!m || !pill) return; pill.classList.toggle("hidden", messagesAtBottom(m, 60)); }
function down(force) {
  const m = $("#messages"); if (!m) return;
  if (force || S._messagesFollow !== false) { m.scrollTop = m.scrollHeight; S._messagesFollow = true; }
  paintJumpPill();
}
function grow() { const t = $("#composer"); t.style.height = "auto"; t.style.height = Math.min(220, t.scrollHeight) + "px"; }
function updateJumpPill() { const m = $("#messages"); if (!m) return; S._messagesFollow = messagesAtBottom(m); paintJumpPill(); }

/* ---------- composer autocomplete (F8) ---------- */
async function loadSkillsCatalog() { if (S.skillsCatalog) return S.skillsCatalog; try { const d = await api("/skills/catalog"); S.skillsCatalog = (d && d.skills) || []; } catch { S.skillsCatalog = []; } return S.skillsCatalog; }
function acDetect() {
  const c = $("#composer"); const pos = c.selectionStart; const before = (c.value || "").slice(0, pos);
  const m = before.match(/(^|\s)([@#/])([^\s@#/]*)$/);
  if (!m) return null;
  return { trigger: m[2], query: m[3], start: pos - m[3].length - 1 };
}
// @-mention suggestions = files in the CURRENT PROJECT (across all its
// conversations), not just this frame, so any uploaded/generated file is easy to
// reference. Cached briefly per project to avoid a fetch on every keystroke.
const _acFiles = { pid: null, at: 0, list: [] };
async function acProjectFiles() {
  const pid = (typeof effProject === "function" ? effProject() : S.project) || null;
  if (pid && (_acFiles.pid !== pid || (Date.now() - _acFiles.at) > 4000)) {
    try { const a = await api(`/projects/${pid}/artifacts`); _acFiles.list = Array.isArray(a) ? a : []; _acFiles.pid = pid; _acFiles.at = Date.now(); }
    catch (e) { /* keep last good list */ }
  }
  const seen = new Set(); const out = [];
  for (const a of [...(pid ? _acFiles.list : []), ...(S.artifacts || [])]) { const fn = a && a.filename; if (!fn || seen.has(fn)) continue; seen.add(fn); out.push(a); }
  return out;
}
async function acUpdate() {
  const d = acDetect(); if (!d) { acClose(); return; }
  let items = [];
  if (d.trigger === "@") items = (await acProjectFiles()).map(a => ({ label: a.filename || "artifact", insert: a.filename || "artifact", sub: a.content_type || "" }));
  else if (d.trigger === "#") items = (S.sessions || []).map(f => ({ label: f.name || f.task_summary || "session", insert: f.name || f.task_summary || "session", sub: "" }));
  else if (d.trigger === "/") { const sk = await loadSkillsCatalog(); items = sk.map(s => ({ label: s.displayName || s.name, insert: s.name, sub: s.description || "" })); }
  const q = (d.query || "").toLowerCase();
  if (q) items = items.filter(it => (it.label || "").toLowerCase().includes(q) || (it.insert || "").toLowerCase().includes(q));
  items = items.slice(0, 8);
  if (!items.length) { acClose(); return; }
  ac.open = true; ac.items = items; ac.idx = 0; ac.trigger = d.trigger; ac.start = d.start; acRender();
}
function acRender() {
  const box = $("#composer-ac"); box.innerHTML = "";
  ac.items.forEach((it, i) => {
    const row = el("div", "ac-item" + (i === ac.idx ? " on" : ""));
    row.appendChild(el("span", "ac-lbl", ac.trigger + (it.label || "")));
    if (it.sub) row.appendChild(el("span", "ac-sub", it.sub));
    row.onmousedown = (e) => { e.preventDefault(); acPick(i); };
    box.appendChild(row);
  });
  box.classList.remove("hidden");
}
function acPick(i) {
  const it = ac.items[i]; if (!it) return;
  const c = $("#composer"); const val = c.value; const pos = c.selectionStart;
  const token = ac.trigger + it.insert + " ";
  c.value = val.slice(0, ac.start) + token + val.slice(pos);
  const np = ac.start + token.length; c.setSelectionRange(np, np);
  acClose(); grow(); c.focus();
}
function acClose() { ac.open = false; const b = $("#composer-ac"); if (b) b.classList.add("hidden"); }

/* ---------- editor code autocomplete (right-dock artifact editor) ---------- */
/* Dependency-free, offline sibling of the composer autocomplete above. Lives on the
   artifact editor textarea and completes ASCII identifiers only, so it can never fire
   or hijack keys during CJK/IME composition (the trigger regex excludes Han). It merges
   a static per-extension keyword table with identifiers harvested from the buffer, and
   inserts via execCommand('insertText') so native undo/redo survives (a value= splice
   would nuke the whole undo stack — a data-loss bug in a code editor). The controller
   (ec) is per-editor, torn down in renderViewer() before the dock rebuilds, and never
   registers document/window listeners — so nothing leaks across re-renders. */
let _edMirror = null;
const EDKW = {
  py: ["def","class","return","import","from","as","if","elif","else","for","while","break","continue","pass","with","try","except","finally","raise","lambda","yield","global","nonlocal","assert","async","await","and","or","not","in","is","None","True","False","self","print","len","range","enumerate","zip","map","filter","list","dict","set","tuple","str","int","float","bool","open","super","isinstance","format"],
  js: ["const","let","var","function","return","if","else","for","while","do","break","continue","switch","case","default","try","catch","finally","throw","new","delete","typeof","instanceof","void","in","of","this","class","extends","super","import","export","from","as","async","await","yield","static","get","set","null","undefined","true","false","console","document","window","Object","Array","String","Number","Boolean","Promise","Math","JSON","Map","Set"],
  css: ["display","position","flex","grid","color","background","background-color","border","border-radius","margin","padding","width","height","max-width","min-width","font-size","font-weight","font-family","line-height","text-align","align-items","justify-content","gap","opacity","overflow","z-index","transition","transform","box-shadow","cursor","white-space","absolute","relative","fixed","sticky","inherit","none","auto","block","hidden","pointer","center"],
  html: ["div","span","class","href","src","style","input","button","script","link","section","header","footer","article","label","textarea","select","option","table","thead","tbody","title","width","height","placeholder","value","type","target","alt","aria-label","data-icon"],
  sh: ["echo","export","source","function","local","return","if","then","elif","else","fi","for","in","do","done","while","case","esac","read","cd","mkdir","grep","sed","awk","cat","chmod","exit","set","unset","true","false"],
  r: ["function","return","if","else","for","while","repeat","break","next","library","require","TRUE","FALSE","NULL","NA","c","list","vector","data.frame","matrix","print","cat","paste","paste0","length","names","nrow","ncol","sapply","lapply","ggplot","aes"],
  yaml: ["true","false","null","name","version","on","jobs","steps","run","uses","with","env","needs"],
  xml: ["version","encoding","xmlns","xsi"],
  json: ["true","false","null"],
};
EDKW.ts = EDKW.js.concat(["interface","type","enum","namespace","declare","readonly","public","private","protected","implements","abstract","keyof","never","unknown","any","string","number","boolean"]);
EDKW.mjs = EDKW.cjs = EDKW.jsx = EDKW.js; EDKW.tsx = EDKW.ts; EDKW.htm = EDKW.html; EDKW.bash = EDKW.zsh = EDKW.sh; EDKW.yml = EDKW.yaml;
function edacExt(a) { const m = (a && a.filename || "").toLowerCase().match(/\.([a-z0-9]+)$/); return m ? m[1] : ""; }
function edacDetect(ta) {
  if (ta.selectionStart !== ta.selectionEnd) return null;                  // no popup over a range selection
  const m = ta.value.slice(0, ta.selectionStart).match(/[A-Za-z_$][\w$]*$/);
  if (!m || m[0].length < 2) return null;
  return { query: m[0], start: ta.selectionStart - m[0].length };
}
function edacItems(a, ta, q) {
  const ql = q.toLowerCase(); const used = new Set(); const out = [];
  const push = (list, sub) => {
    for (const w of list) { const wl = w.toLowerCase(); if (used.has(w) || wl === ql || !wl.startsWith(ql)) continue; used.add(w); out.push({ label: w, sub }); if (out.length >= 8) return true; }
    return false;
  };
  if (push(EDKW[edacExt(a)] || [], t("edac.keyword"))) return out;                    // language keywords first
  if (ta.value.length <= 200000) {                                         // then buffer identifiers (cap scan on huge files)
    const seen = new Set(); const words = []; const mm = ta.value.match(/[A-Za-z_$][\w$]*/g) || [];
    for (const w of mm) { if (w.length >= 2 && !seen.has(w)) { seen.add(w); words.push(w); } }
    push(words, "");
  }
  return out;
}
/* Caret pixel coords via one reused offscreen mirror div (no library). .edit-area has
   border:0 + box-sizing:border-box, so a mirror at (0,0) with the same width, padding
   and font wraps identically; a marker span then gives the caret's rect. */
function edacCaretXY(ta) {
  if (!_edMirror) { _edMirror = el("div", "ed-mirror"); document.body.appendChild(_edMirror); }
  const m = _edMirror, cs = getComputedStyle(ta);
  ["fontFamily","fontSize","fontWeight","fontStyle","letterSpacing","lineHeight","textTransform","tabSize","paddingTop","paddingRight","paddingBottom","paddingLeft","borderTopWidth","borderRightWidth","borderBottomWidth","borderLeftWidth","boxSizing","whiteSpace","wordWrap","overflowWrap","direction"].forEach(p => { m.style[p] = cs[p]; });
  m.style.width = ta.clientWidth + "px";
  m.textContent = ta.value.slice(0, ta.selectionStart);
  const mark = el("span"); mark.textContent = "​"; m.appendChild(mark);
  const mr = m.getBoundingClientRect(), sr = mark.getBoundingClientRect(), tr = ta.getBoundingClientRect();
  const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.4;
  return { x: tr.left + (sr.left - mr.left) - ta.scrollLeft, y: tr.top + (sr.top - mr.top) - ta.scrollTop, lh };
}
function edacRender(ec) {
  const box = ec.pop; box.innerHTML = "";
  ec.items.forEach((it, i) => {
    const row = el("div", "ac-item" + (i === ec.idx ? " on" : ""));
    row.appendChild(el("span", "ac-lbl", it.label));
    if (it.sub) row.appendChild(el("span", "ac-sub", it.sub));
    row.onmousedown = (e) => { e.preventDefault(); edacPick(ec, i); };     // preventDefault keeps caret/focus for execCommand
    box.appendChild(row);
  });
  box.classList.remove("hidden");
  const on = box.querySelector(".ac-item.on"); if (on) on.scrollIntoView({ block: "nearest" });
}
function edacPosition(ec) {
  const c = edacCaretXY(ec.ta), pop = ec.pop;
  pop.style.left = "0px"; pop.style.top = "0px";                           // measure at origin first
  const pw = pop.offsetWidth || 200, ph = pop.offsetHeight || 120;
  let left = c.x, top = c.y + c.lh;
  if (left + pw > window.innerWidth - 8) left = Math.max(8, window.innerWidth - 8 - pw);
  if (top + ph > window.innerHeight - 8) top = Math.max(8, c.y - ph);      // flip above the caret near the bottom edge
  pop.style.left = Math.round(left) + "px"; pop.style.top = Math.round(top) + "px";
}
function edacUpdate(ec) {
  if (ec.dead || ec.composing || ec.ta.disabled) return;
  const d = edacDetect(ec.ta);
  if (!d) { edacClose(ec); return; }
  const items = edacItems(ec.a, ec.ta, d.query);
  if (!items.length) { edacClose(ec); return; }
  ec.open = true; ec.items = items; ec.idx = 0; ec.start = d.start;
  edacRender(ec); edacPosition(ec);
}
function edacPick(ec, i) {
  const it = ec.items[i]; if (!it) return;
  const ta = ec.ta;
  const d = edacDetect(ta);                                                // re-validate against the LIVE caret at pick time
  if (!d || d.start !== ec.start) { edacClose(ec); return; }               // caret moved / token changed → never overwrite the wrong span
  const v = ta.value; let end = ta.selectionStart;                         // extend over trailing identifier chars so mid-word completion replaces the whole token
  while (end < v.length && /[\w$]/.test(v[end])) end++;
  ta.focus(); ta.setSelectionRange(d.start, end);
  ec.justPicked = true; let ok = false;                                    // justPicked suppresses the re-open from execCommand's input event
  try { ok = document.execCommand("insertText", false, it.label); } catch { ok = false; }
  if (!ok) { ta.setRangeText(it.label, d.start, end, "end"); ta.dispatchEvent(new Event("input", { bubbles: true })); }
  ec.justPicked = false; edacClose(ec);
}
function edacClose(ec) { if (!ec) return; ec.open = false; ec.items = []; if (ec.pop) ec.pop.classList.add("hidden"); }
function edacTeardown() { const ec = S._editorAC; if (!ec) return; ec.dead = true; clearTimeout(ec.deb); ec.open = false; S._editorAC = null; }  // shared: renderViewer + openConversation

async function routeInitialView() {
  const path = location.pathname || "/";
  const fm = path.match(/^\/projects\/([^/]+)\/frames\/([^/]+)/);
  if (fm) {
    const pid = decodeURIComponent(fm[1]);
    const fid = decodeURIComponent(fm[2]);
    await loadProjects(); S.project = pid; showWorkspace(); await loadSessions(); renderProjMenu();
    await openConversation(fid, pid);
    return;
  }
  const pm = path.match(/^\/projects\/([^/]+)\/?$/);
  if (pm) {
    const pid = decodeURIComponent(pm[1]);
    await openProject(pid);
    return;
  }
  showDashboard();
}

/* ---------- draggable column resizers ---------- */
// Restore any persisted sidebar / dock widths onto :root BEFORE the workspace
// paints, so there's no width flash. Clamped defensively in case the viewport
// shrank since the width was saved.
function restoreColWidths() {
  const sw = parseInt(localStorage.getItem("os-side-w") || "", 10);
  if (sw && sw >= 200 && sw <= 520) document.documentElement.style.setProperty("--side-w", sw + "px");
  const dw = parseInt(localStorage.getItem("os-dock-w") || "", 10);
  if (dw && dw >= 360) document.documentElement.style.setProperty("--dock-w", Math.min(dw, Math.max(360, window.innerWidth - 360)) + "px");
}
function initColResizers() {
  const main = $("#main"), dock = $("#rightdock");
  if (main && !main.querySelector(".col-resizer-side")) makeColResizer(main, "side");
  if (dock && !dock.querySelector(".col-resizer-dock")) makeColResizer(dock, "dock");
  // Re-clamp on window resize so a wide persisted dock can't shrink #main to
  // nothing (or overflow) when the viewport gets smaller.
  if (!window._colClampBound) {
    window._colClampBound = true;
    window.addEventListener("resize", () => {
      const cs = getComputedStyle(document.documentElement);
      const dw = parseInt(cs.getPropertyValue("--dock-w"), 10);
      if (dw) document.documentElement.style.setProperty("--dock-w", Math.max(360, Math.min(dw, window.innerWidth - 360)) + "px");
      const sw = parseInt(cs.getPropertyValue("--side-w"), 10);
      if (sw) document.documentElement.style.setProperty("--side-w", Math.max(200, Math.min(sw, Math.max(200, window.innerWidth * 0.4))) + "px");
    });
  }
}
function makeColResizer(host, kind) {
  const h = el("div", "col-resizer col-resizer-" + kind);
  h.title = t("resizer.drag");
  host.appendChild(h);
  let startX = 0, curW = 0, curW0 = 0;
  const apply = (w) => {
    if (kind === "side") { curW = Math.max(200, Math.min(520, w)); document.documentElement.style.setProperty("--side-w", curW + "px"); }
    else {
      // fold the active CSS cap into the clamp: below 1180px the stylesheet caps
      // the dock at 60vw, so without this the drag "dies" past that width.
      const cap = Math.min(window.innerWidth - 360, window.innerWidth <= 1180 ? window.innerWidth * 0.6 : Infinity);
      curW = Math.max(360, Math.min(cap, w));
      document.documentElement.style.setProperty("--dock-w", curW + "px");
    }
  };
  const onMove = (e) => {
    const dx = e.clientX - startX;
    // sidebar edge grows to the RIGHT (+dx); dock's left edge grows the dock to the LEFT (−dx).
    apply(kind === "side" ? (curW0 + dx) : (curW0 - dx));
  };
  const onUp = () => {
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.removeEventListener("pointercancel", onUp);
    document.body.classList.remove("col-resizing");
    h.classList.remove("active");
    try { localStorage.setItem(kind === "side" ? "os-side-w" : "os-dock-w", String(Math.round(curW))); } catch {}
    // let iframes / the 3Dmol canvas relayout to the new pane width
    try { window.dispatchEvent(new Event("resize")); } catch {}
  };
  h.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;  // left-button drags only (ignore right/middle click)
    // never start a resize while the sidebar is collapsed (side handle) — it's hidden anyway
    if (kind === "side" && document.body.classList.contains("sidebar-collapsed")) return;
    e.preventDefault();
    startX = e.clientX;
    curW0 = curW = (kind === "side" ? $("#sidebar") : host).getBoundingClientRect().width;
    document.body.classList.add("col-resizing"); h.classList.add("active");
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  });
}

/* ---------- init ---------- */
async function init() {
  try { S.sandboxOrigin = (window.__OPERON__ || {}).sandboxOrigin || ""; } catch {}
  paintIcons();
  document.documentElement.lang = LANG === "en" ? "en" : "zh";
  applyStaticI18n(document); refreshLangToggle();
  document.querySelectorAll(".lang-btn").forEach(b => b.onclick = () => setLang(b.dataset.lang));
  applyLayout(localStorage.getItem("os-layout") || "comfortable");
  restoreColWidths(); initColResizers();
  connectWS(); await loadModels(); refreshKeyBanner();
  $("#dash-new-project").onclick = () => $("#proj-modal").classList.remove("hidden");
  $("#back-home").onclick = showDashboard;
  $("#new-session").onclick = newSession;
  $("#tab-new").onclick = newSession;
  $("#tab-close").onclick = (e) => { e.stopPropagation(); showDashboard(); };
  $("#sidebar-collapse").onclick = () => setSidebar(true);
  $("#sidebar-reopen").onclick = () => setSidebar(false);
  $("#mic-btn").onclick = micDictate;
  $("#dash-settings").onclick = () => openCust("general");
  $("#customize-btn").onclick = openCust;
  $("#files-btn").onclick = () => { loadNotes(); $("#notes-block").classList.remove("hidden"); dockTab("files"); };
  { const fscope = $("#files-scope"); if (fscope) fscope.querySelectorAll(".seg-btn").forEach(b => b.onclick = () => setFilesScope(b.dataset.scope)); }
  $("#proj-btn").onclick = () => $("#proj-menu").classList.toggle("hidden");
  const ct = $("#conv-title");
  ct.addEventListener("keydown", (e) => { if (e.isComposing || e.keyCode === 229) return; if (e.key === "Enter") { e.preventDefault(); ct.blur(); } else if (e.key === "Escape") { setTitle(S._titleName); ct.blur(); } });
  ct.addEventListener("blur", commitTitle);
  $("#session-menu-btn").onclick = (e) => { if (S.currentId) sessionMenu(e.currentTarget, S.currentId); };
  $("#dock-toggle").onclick = dockToggle;
  $("#dock-collapse").onclick = dockClose;
  // ⌘K / Ctrl-K global command palette (advertised in the composer placeholder)
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { if (PAL.open) { e.preventDefault(); closePalette(); return; } const modalOpen = ["#cust", "#modal", "#proj-modal"].some(s => { const m = $(s); return m && !m.classList.contains("hidden"); }); if (modalOpen) return; e.preventDefault(); openPalette(); }
    // ⌘/Ctrl+B toggles the sidebar — a reliable escape hatch so a collapsed
    // sidebar can always be brought back even if the expand icon is missed.
    if ((e.metaKey || e.ctrlKey) && (e.key === "b" || e.key === "B")) { e.preventDefault(); setSidebar(!document.body.classList.contains("sidebar-collapsed")); }
  });
  // Composer "Notebook" tray opens the live notebook panel (claude-science parity)
  const nbTray = $(".nb-tray");
  if (nbTray) nbTray.onclick = () => { if (S.dock.open && S.activeTab === "notebook") dockClose(); else setActiveTab("notebook"); };
  $("#jump-pill").onclick = () => down(true);
  $("#messages").addEventListener("scroll", updateJumpPill);
  $("#cancel-btn").onclick = cancelTurn;
  $("#settings-gear").onclick = openCust;
  $("#cust-close").onclick = () => $("#cust").classList.add("hidden");
  document.querySelectorAll(".cust-tab").forEach(t => t.onclick = () => custTab(t.dataset.tab));
  $("#modal-close").onclick = () => $("#modal").classList.add("hidden");
  $("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); };
  $("#attach-btn").onclick = () => $("#file-input").click();
  $("#file-input").onchange = (e) => uploadFiles(e.target.files);
  $("#plan-toggle").onclick = () => { S.planMode = !S.planMode; if (S.planMode) { S.exploreMode = false; $("#explore-toggle").classList.remove("on"); } $("#plan-toggle").classList.toggle("on", S.planMode); hint(S.planMode ? t("plan.toggle.on") : ""); };
  $("#explore-toggle").onclick = () => { S.exploreMode = !S.exploreMode; if (S.exploreMode) { S.planMode = false; $("#plan-toggle").classList.remove("on"); } $("#explore-toggle").classList.toggle("on", S.exploreMode); hint(S.exploreMode ? t("explore.toggle.on") : ""); };
  $("#note-save").onclick = addNote;
  $("#proj-modal-close").onclick = $("#pm-cancel").onclick = () => $("#proj-modal").classList.add("hidden");
  $("#pm-create").onclick = async () => { const n = $("#pm-name").value.trim() || t("palette.action.newProject"); await createProject(n, $("#pm-desc").value, $("#pm-ctx").value); $("#proj-modal").classList.add("hidden"); $("#pm-name").value = $("#pm-desc").value = $("#pm-ctx").value = ""; };
  const c = $("#composer");
  c.addEventListener("input", () => { grow(); acUpdate(); });
  c.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;  // IME composition: Enter commits the candidate, not the message
    if (ac.open) {
      if (e.key === "ArrowDown") { e.preventDefault(); ac.idx = (ac.idx + 1) % ac.items.length; acRender(); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); ac.idx = (ac.idx - 1 + ac.items.length) % ac.items.length; acRender(); return; }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); acPick(ac.idx); return; }
      if (e.key === "Escape") { e.preventDefault(); acClose(); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); acClose(); send(c.value); }
  });
  c.addEventListener("blur", () => setTimeout(acClose, 120));
  // paste images/files directly into the composer
  c.addEventListener("paste", (e) => {
    const items = (e.clipboardData || {}).items || []; const files = [];
    for (const it of items) { if (it.kind === "file") { const f = it.getAsFile(); if (f) files.push(f); } }
    if (files.length) { e.preventDefault(); uploadFiles(files); hint(t("upload.pasting")); }
  });
  // drag & drop files onto the composer/messages area
  const dz = $(".composer-wrap") || c;
  dz.addEventListener("dragover", (e) => { e.preventDefault(); dz.classList.add("dragover"); });
  dz.addEventListener("dragleave", (e) => { e.preventDefault(); dz.classList.remove("dragover"); });
  dz.addEventListener("drop", (e) => { e.preventDefault(); dz.classList.remove("dragover"); const files = e.dataTransfer && e.dataTransfer.files; if (files && files.length) { uploadFiles(files); hint(t("upload.dropping")); } });
  // Back/forward: the browser already restored location.pathname, so just
  // re-hydrate the view for it. routeInitialView's navURL calls are no-ops here
  // (the path already matches), so this never pushes a spurious history entry.
  window.addEventListener("popstate", () => { routeInitialView().catch(showDashboard); });
  routeInitialView().catch(showDashboard);
}
init();

// Delegated copy button for markdown code blocks emitted by renderMd().
document.addEventListener("click", function (e) {
  var btn = e.target && e.target.closest ? e.target.closest(".cb-copy") : null;
  if (!btn) return;
  var block = btn.closest(".codeblock");
  var codeEl = block && block.querySelector("pre code");
  var text = codeEl ? codeEl.textContent : "";
  try { if (navigator.clipboard) navigator.clipboard.writeText(text); } catch (_) {}
  var lbl = btn.querySelector(".cb-copy-t");
  btn.classList.add("copied");
  if (lbl) { if (!lbl.getAttribute("data-o")) lbl.setAttribute("data-o", lbl.textContent); lbl.textContent = t("code.copied"); }
  clearTimeout(btn._t);
  btn._t = setTimeout(function () { btn.classList.remove("copied"); if (lbl) lbl.textContent = lbl.getAttribute("data-o") || t("msgAction.copy"); }, 1400);
});
