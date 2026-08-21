"use strict";
// OpenAI4S UI — aligned to Claude Science (dashboard + conversation), over /api/v1 + /api/v1/ws.
const $ = (s) => document.querySelector(s);
const el = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
const esc = (s) => (s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
/* ---------- line icons (lucide) ---------- */
const ICONS = {
  "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
  "share": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>',
  "chevron-down": '<path d="m6 9 6 6 6-6"/>',
  "chevron-up": '<path d="m18 15-6-6-6 6"/>',
  "chevron-right": '<path d="m9 18 6-6-6-6"/>',
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
  "server": '<rect width="20" height="8" x="2" y="2" rx="2"/><rect width="20" height="8" x="2" y="14" rx="2"/><path d="M6 6h.01"/><path d="M6 18h.01"/>',
  "notebook": '<path d="M2 6h4"/><path d="M2 10h4"/><path d="M2 14h4"/><path d="M2 18h4"/><rect width="16" height="20" x="4" y="2" rx="2"/><path d="M16 2v20"/>',
  "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
  "compass": '<circle cx="12" cy="12" r="10"/><path d="m16.2 7.8-2.1 6.3-6.3 2.1 2.1-6.3Z"/>',
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
  "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
  "chevron-right": '<path d="m9 18 6-6-6-6"/>',
  "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
  "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/><path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
  "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  "monitor": '<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/><line x1="12" x2="12" y1="17" y2="21"/>',
};
const icon = (name, size, cls) => `<svg class="ic-svg${cls ? " " + cls : ""}" width="${size || 16}" height="${size || 16}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name] || ""}</svg>`;
const iconEl = (name, size, cls) => { const s = el("span", "ic"); s.innerHTML = icon(name, size, cls); return s.firstChild; };
function paintIcons(root) { (root || document).querySelectorAll("[data-icon]").forEach(e => { if (e._painted) return; e.innerHTML = icon(e.dataset.icon, +e.dataset.iconSize || 16); e._painted = true; }); }
function setTitle(name) { const ct = $("#conv-title"); if (!ct) return; ct.value = name || t("conv.title.default"); ct.size = Math.max(6, Math.min(40, (name || t("conv.title.default")).length + 1)); }
// The versioned API root. Contract v1 is the frozen surface; a future version
// bump is this one line plus a gateway prefix, not a sweep through the file.
const API = "/api/v1";

// Every failure body is {error, code, status, request_id}. This used to throw a
// bare Error carrying only `error`, so the fields that make a failure
// actionable were parsed and then dropped on the floor: `code`, the stable
// machine-readable contract, left callers with nothing to branch on except the
// prose -- which the backend documents as explicitly *not* an interface -- and
// `request_id` never reached the user, so the one string that ties their
// report to a server log line existed on both ends and was shown at neither.
class ApiError extends Error {
  constructor(body, httpStatus) {
    super((body && (body.error || body.detail)) || ("HTTP " + httpStatus));
    this.name = "ApiError";
    this.code = (body && body.code) || "";
    this.status = (body && body.status) || httpStatus;
    this.requestId = (body && body.request_id) || "";
    this.body = body;
  }
}
// What a human should read. The id is appended rather than woven in so the
// sentence still reads as a sentence, and omitted when there is none rather
// than rendered as "(null)" -- an absent id means the failure never reached a
// request, which is a different thing from a request without one.
function apiErrorText(e) {
  const msg = (e && e.message) ? String(e.message) : String(e);
  return (e && e.requestId) ? `${msg} [${e.requestId}]` : msg;
}
const api = async (p, o = {}) => {
  // `p` must be an internal, same-origin API path: a single leading slash and no
  // scheme/host. Rejecting "//host" (protocol-relative) and non-string input keeps
  // an untrusted id interpolated into `p` from redirecting the request off-origin.
  if (typeof p !== "string" || p[0] !== "/" || p[1] === "/") throw new Error("invalid api path");
  const r = await fetch(API + p, { headers: { "content-type": "application/json" }, ...o });
  const t = await r.text(); let j = null; try { j = t ? JSON.parse(t) : null; } catch { j = t; }
  if (!r.ok) throw new ApiError(j, r.status); return j;
};
const S = { projects: [], sessions: [], project: null, currentId: null, ws: null, stream: null, running: false, models: [], defaultModel: null, sandboxOrigin: "", planMode: false, exploreMode: false, planPending: false, planReady: null, planStatus: null, artifacts: [], dock: { open: false, tab: "notebook" }, openTabs: [], activeTab: "notebook", provMode: false, provSub: "code", cells: [], kernels: [], liveCells: [], _liveCell: null, dockArtifact: null, kernelFilter: null, _titleName: "", skillsCatalog: null, _menu: null, annotations: [], _annotDraft: null, filesScope: "frame", projectArtifacts: [], _projArtFor: null,
  rendererCatalog: null, _rendererCatalogPromise: null, rendererDescriptors: {},
  // The workbench surfaces are projections only. They deliberately keep no
  // provider wire payloads or raw tool arguments in browser state.
  actionTimeline: null, executionQueue: null, executionIdentity: null, recoveryState: null,
  actionTimelineSelectedGroupId: null, actionTimelineSelectedBranchId: null,
  recoveryActions: null, branchState: null, branchUndo: null, contextState: null, securityState: null, computeTasks: null,
  delegationState: null,
  environmentStatus: null, standardProfileReadiness: null, _environmentStatusPromise: null, _environmentStatusRefreshFailed: false,
  workbenchErrors: {}, _workbenchReq: 0, _timelineHistoryReq: 0, _timelineHistoryLoading: null, _timelineView: null,
  _recoveryActionLoading: null, _branchActionLoading: null, _timelineRestoreFocusGroupId: null,
  variableInspector: { language: "python", results: {}, loading: null, error: "", request: 0 } };
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
// `t` falls back to the key itself, which is right for a missing translation
// (a developer sees the key) and wrong for an optional label (a user would see
// "context.omitted.images" rendered as text). This says "translate if you know
// it" and lets the caller supply something a person can read otherwise.
function tOptional(key) {
  const d = I18N[LANG] || {}, z = I18N.zh || {};
  const value = d[key] != null ? d[key] : z[key];
  return value != null ? String(value) : null;
}
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
  applyStaticI18n(document); refreshLangToggle(); refreshThemeToggle(); rerenderI18n();
}

/* ---------- theme (light / dark / system) ---------- */
let THEME = (() => {
  try { const s = localStorage.getItem("os-theme"); if (s === "dark" || s === "light" || s === "system") return s; } catch {}
  return "system";
})();
function themeIsDark() {
  if (THEME === "dark") return true;
  if (THEME === "light") return false;
  try { return !!(window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches); } catch { return false; }
}
function applyTheme(mode, opts) {
  if (mode === "dark" || mode === "light" || mode === "system") THEME = mode;
  const dark = themeIsDark();
  const root = document.documentElement;
  if (opts && opts.instant) root.setAttribute("data-theme-instant", "");
  root.setAttribute("data-theme", dark ? "dark" : "light");
  root.style.colorScheme = dark ? "dark" : "light";
  document.body.classList.toggle("theme-dark", dark);
  refreshThemeToggle();
  // Retheme live 3Dmol canvases if any
  try {
    if (S._molViewer && S._molViewer.setBackgroundColor) {
      S._molViewer.setBackgroundColor(dark ? "#1c1c19" : "white");
      S._molViewer.render && S._molViewer.render();
    }
  } catch {}
  if (opts && opts.instant) {
    requestAnimationFrame(() => { try { root.removeAttribute("data-theme-instant"); } catch {} });
  }
}
function setTheme(mode) {
  THEME = (mode === "dark" || mode === "light" || mode === "system") ? mode : "system";
  try { localStorage.setItem("os-theme", THEME); } catch {}
  applyTheme(THEME);
  hint(t("toast.theme", t("theme." + THEME)));
}
function cycleTheme() {
  // Quick toggle: light ↔ dark; from system, pick the opposite of the resolved value.
  if (THEME === "system") setTheme(themeIsDark() ? "light" : "dark");
  else setTheme(THEME === "dark" ? "light" : "dark");
}
function refreshThemeToggle() {
  const dark = themeIsDark();
  const name = dark ? "sun" : "moon";
  const title = t("theme.toggle");
  ["#dash-theme", "#ws-theme"].forEach(sel => {
    const b = $(sel); if (!b) return;
    b.dataset.icon = name; b._painted = false; b.title = title;
    b.setAttribute("aria-label", title);
    b.innerHTML = icon(name, +b.dataset.iconSize || 20);
    b._painted = true;
  });
}
// Follow OS theme changes while the preference is "system"
try {
  const mq = window.matchMedia && matchMedia("(prefers-color-scheme: dark)");
  if (mq) {
    const onChange = () => { if (THEME === "system") applyTheme("system", { instant: true }); };
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
} catch {}
// Re-render the dynamic (JS-built) views currently on screen after a language switch.
function rerenderI18n() {
  try { renderEnvironmentReadinessBanner(); } catch {}
  try { if (!$("#dashboard").classList.contains("hidden")) loadDashboard(); } catch {}
  try { renderProjMenu(); } catch {}
  try { renderSessions(); } catch {}
  try { renderDockTabs(); } catch {}
  try { if (S.activeTab === "notebook") renderNotebook(); else if (S.activeTab === "timeline") renderActionTimeline(); } catch {}
  try { if (S._titleName) setTitle(S._titleName); } catch {}
  try { if (!$("#cust").classList.contains("hidden")) { const at = document.querySelector(".cust-tab.active"); custTab(at ? at.dataset.tab : "general"); } } catch {}
  try { const m = $("#messages"); if (m && m.children.length === 1 && m.firstChild && m.firstChild.classList && m.firstChild.classList.contains("empty-session")) { m.innerHTML = ""; renderEmptySession(); } } catch {}
}

Object.assign(I18N.zh, {
  "cust.general.language": "语言",
  "cust.general.languageDesc": "界面显示语言（保存在本机浏览器）",
  "cust.general.themeName": "外观主题",
  "cust.general.themeDesc": "浅色 / 深色 / 跟随系统（保存在本机浏览器）",
  "theme.light": "浅色",
  "theme.dark": "深色",
  "theme.system": "系统",
  "theme.toggle": "切换主题",
  "toast.theme": "主题：{0}",
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
  "annot.status.pending": "发送中",
  "annot.status.unknown": "状态未知",
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
  "composer.addToMessage": "添加到消息",
  "composer.sessionOptions": "会话选项",
  "composer.menu.attachFiles": "附加文件",
  "composer.menu.contextUsage": "上下文用量",
  "composer.menu.requestReview": "请求审核",
  "composer.menu.saveAsSkill": "另存为技能",
  "composer.menu.yourFiles": "你的文件",
  "composer.option.autoReview": "自动审核",
  "composer.option.compute": "计算环境",
  "composer.option.delegation": "委派",
  "composer.option.memory": "记忆",
  "composer.option.reviewerModel": "Reviewer 模型",
  "composer.option.sameModel": "跟随主模型",
  "composer.option.specialist": "专家",
  "composer.model": "模型",
  "composer.placeholder": "输入任何内容 — @ 引用制品，# 引用会话，/ 使用技能，⌘K 搜索…",
  "composer.placeholderQueue": "当前任务运行中 — 现在发送将排入队列…",
  "queue.accepted": "已排入队列，将在当前任务之后运行",
  "queue.waiting": "{0} 条排队中",
  "queue.noPreview": "（无文本）",
  "queue.underProfile": "配置 {0} · 版本 {1}",
  "queue.onBranch": "分支 {0}",
  "queue.cancelOne": "取消这一条（不影响正在运行的任务）",
  "queue.cancelled": "已取消该排队消息",
  "queue.cancelFailed": "取消排队消息失败：{0}",
  "composer.planMode": "计划模式",
  "composer.exploreMode": "自主探索",
  "composer.voice": "语音输入",
  "confirm.deleteSession": "确定删除该会话？此操作不可撤销。",
  "conv.dockToggle": "侧栏面板",
  "conv.jumpLast": "跳到最后一条",
  "conv.jumpLastLabel": "最新",
  "conv.loadEarlier": "加载更早的消息",
  "conv.loadEarlierFailed": "加载更早的消息失败：{0}",
  "conv.exportTruncated": "（导出被长度上限截断：更早的消息未包含在内）",
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
  "environment.readiness.bannerTitle": "standard 科研环境尚未就绪",
  "environment.readiness.bannerMissing": "缺少 {0} 个环境、{1} 个软件包；首个科研 Cell 运行前需要处理。",
  "environment.readiness.bannerUnavailable": "当前无法确认科研环境是否就绪；未知状态不会被显示成就绪。",
  "environment.readiness.openCompute": "查看缺项与修复命令",
  "environment.readiness.cardTitle": "Standard profile readiness",
  "environment.readiness.ready": "就绪：Python 与 R 的 standard 依赖均已核对。",
  "environment.readiness.needsSetup": "需要创建缺失的 standard 环境。",
  "environment.readiness.needsRepair": "环境存在，但缺少 standard 清单中的软件包。",
  "environment.readiness.unavailable": "无法完成本地 readiness 检查；系统未将它猜测为就绪。",
  "environment.readiness.missingEnvironments": "缺少环境",
  "environment.readiness.missingPackages": "{0} 缺少的软件包",
  "environment.readiness.remediation": "受管修复命令",
  "environment.readiness.explicitOnly": "仅复制；必须由你显式运行，不会自动安装。",
  "environment.readiness.copy": "复制命令",
  "environment.readiness.copied": "修复命令已复制",
  "environment.readiness.refresh": "刷新 readiness",
  "environment.readiness.sendBlocked": "首个科研 Cell 已在启动前停止。请先完成 standard 环境配置。",
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
  "cust.datapro.title": "火山方舟专业数据集 DataPro",
  "cust.datapro.desc": "保存 Agent Plan Key 后直接调用 dataPro_search；已配置的 Ark API Key 会自动复用。",
  "cust.datapro.connectorToggle": "启用/停用 DataPro 连接器",
  "cust.datapro.connectorOn": "已启用 DataPro 连接器。",
  "cust.datapro.connectorOff": "已停用 DataPro 连接器，智能体不再访问该服务。",
  "cust.datapro.keyLabel": "Agent Plan Key",
  "cust.datapro.keyPlaceholder": "输入 Agent Plan Key",
  "cust.datapro.keyPlaceholderSet": "已保存；输入新 Key 可替换",
  "cust.datapro.keyPlaceholderArk": "已复用 Ark API Key；输入新 Key 可替换",
  "cust.datapro.keyConfigured": "凭证已保存",
  "cust.datapro.keyArkReused": "已复用 Ark API Key",
  "cust.datapro.keyMissing": "尚未配置 Agent Plan Key",
  "cust.datapro.saveKey": "保存凭证",
  "cust.datapro.keyRequired": "请输入 Agent Plan Key",
  "cust.datapro.keySaved": "Agent Plan Key 已安全保存",
  "cust.datapro.queryLabel": "查询专业数据集",
  "cust.datapro.queryPlaceholder": "输入要检索的问题",
  "cust.datapro.search": "调用专业数据集",
  "cust.datapro.searching": "查询中…",
  "cust.datapro.queryRequired": "请输入查询文本",
  "cust.datapro.available": "专业数据集可用",
  "cust.datapro.indexed": "已完整索引本次返回的 {0} 条记录（{1} 个内容叶节点）",
  "cust.datapro.indexFailed": "本次返回内容索引失败，专业数据集暂不可用",
  "cust.datapro.auth4011": "Key 无效、额度不足，或者专业数据集 Harness 未开启。",
  "cust.datapro.unavailable": "专业数据集不可用（code {0}）",
  "cust.datapro.requestFailed": "专业数据集查询失败：{0}",
  "cust.datapro.result": "查询结果",
  "cust.datapro.noResult": "尚未查询。",
  "cust.datapro.enableSkill": "导入/启用 volcengine-datapro Skill",
  "cust.datapro.enablingSkill": "启用中…",
  "cust.datapro.skillEnabled": "volcengine-datapro Skill 已启用",
  "cust.datapro.skillEnabledToast": "volcengine-datapro Skill 与 DataPro connector 已启用",
  "cust.datapro.artifact": "已保存：{0}",
  "cust.doubao.title": "豆包搜索 Custom 版",
  "cust.doubao.primary": "主选",
  "cust.doubao.desc": "联网搜索主选项；与 Ark 和 DataPro 共用同一个 Agent Plan Key。只有真实返回非空豆包结果才会标记可用。",
  "cust.doubao.keyLabel": "Agent Plan Key（一次授权）",
  "cust.doubao.keyPlaceholder": "输入 Agent Plan Key",
  "cust.doubao.keyPlaceholderSet": "已保存；输入新 Key 可替换",
  "cust.doubao.keyPlaceholderArk": "已复用 Ark API Key；输入新 Key 可替换",
  "cust.doubao.keyConfigured": "凭证已保存",
  "cust.doubao.keyArkReused": "已复用 Ark API Key",
  "cust.doubao.keyMissing": "尚未配置 Agent Plan Key",
  "cust.doubao.saveKey": "保存凭证",
  "cust.doubao.keyRequired": "请输入 Agent Plan Key",
  "cust.doubao.keySaved": "Agent Plan Key 已安全保存，可同时用于豆包搜索和 DataPro",
  "cust.doubao.queryLabel": "查询豆包搜索",
  "cust.doubao.queryPlaceholder": "输入要联网检索的问题（最多 100 个字符）",
  "cust.doubao.search": "调用豆包搜索",
  "cust.doubao.searching": "搜索中…",
  "cust.doubao.queryRequired": "请输入查询文本",
  "cust.doubao.available": "豆包搜索可用",
  "cust.doubao.empty": "豆包搜索未返回可用结果",
  "cust.doubao.requestFailed": "豆包搜索失败：{0}",
  "cust.doubao.result": "搜索结果",
  "cust.doubao.noResult": "尚未搜索。",
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
  "cust.jobs.dropped": "· 已丢弃 {0} 字节",
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
  "cust.memory.injectedCounts": "已注入 {0} 条 · 省略 {1} 条 · 继承自全局 {2} 条 · 被本项目同类记忆覆盖 {3} 条",
  "cust.memory.injectedInto": "注入到 {0}",
  "cust.memory.scope.global": "全局（所有项目）",
  "cust.memory.scopeName": "范围",
  "cust.memory.title": "记忆",
  "cust.models.activePill": "当前",
  "cust.models.addBtn": "新增",
  "cust.models.addHeading": "新增模型 / API",
  "cust.models.available": "可选模型",
  "cust.models.baseUrl.placeholder": "Base URL（留空用该协议默认）",
  "cust.models.baseUrlPlaceholder": "Base URL（留空用该协议默认）",
  "cust.models.cancelEdit": "取消编辑",
  "cust.models.configuredHeading": "已配置的模型 / API",
  "cust.models.editHeading": "编辑：{0}",
  "cust.models.empty2": "还没有模型配置。用上面的表单新增一个。",
  "cust.models.hasKey": "🔑 已配置 Key",
  "cust.models.reachable": "端点已响应一次最小请求",
  "cust.models.test": "测试连接",
  "cust.models.testing": "正在联系该端点…",
  "cust.models.unreachable": "未能联系上该端点",
  "cust.models.key.configured": "✅ API Key 已配置",
  "cust.models.key.missing": "⚠️ 尚未配置 API Key — 发送消息会失败",
  "cust.models.key.placeholder.set": "API Key（已配置，留空则不改动）",
  "cust.models.key.placeholder.unset": "API Key（尚未配置，请填写）",
  "cust.models.keyPlaceholderSet": "API Key（已配置，留空则保留）",
  "cust.models.keyPlaceholderUnset": "API Key（未配置，可填写）",
  "cust.models.label.apiKey": "API Key",
  "cust.models.label.baseUrl": "Base URL",
  "cust.models.label.defaultModel": "默认模型",
  "cust.models.label.provider": "兼容协议",
  "cust.models.label.protocol": "兼容协议",
  "cust.models.model.placeholder": "模型 id（留空用该协议默认）",
  "cust.models.modelPlaceholder2": "模型 id（留空用该协议默认）",
  "cust.models.namePlaceholder": "名称（如 DeepSeek 生产 / 本地 vLLM）",
  "cust.models.local.title": "本地推理服务",
  "cust.models.local.desc": "点击下方按钮扫描本机固定端口上的 Ollama、LM Studio、vLLM 与 llama.cpp；扫描不会修改当前模型，未知能力默认走保守的 Code-as-Action。",
  "cust.models.local.scan": "扫描本机",
  "cust.models.local.idle": "尚未扫描。点击上方按钮检测本机的 Ollama、LM Studio、vLLM 与 llama.cpp。",
  "cust.models.local.scanning": "正在扫描本机…",
  "cust.models.local.none": "没有发现可用的本地 OpenAI-compatible endpoint。",
  "cust.models.local.models": "{0} 个模型",
  "cust.models.local.add": "添加配置",
  "cust.models.local.configured": "已配置",
  "cust.models.local.added": "已添加本地模型：{0}",
  "cust.models.local.error": "本地模型扫描失败：{0}",
  "cust.models.local.keyless": "本机 · 无需 API Key",
  "cust.models.noKey": "⚠️ 无 Key",
  "cust.models.protocol.openai": "OpenAI 兼容协议",
  "cust.models.protocol.anthropic": "Anthropic 兼容协议",
  "cust.models.protocol.ark": "ark 兼容协议",
  "cust.models.protocol.gemini": "Gemini 兼容协议",
  "cust.models.protocol.openaiResponses": "OpenAI Responses 协议",
  "cust.search.name": "备用搜索 API Key（Tavily）",
  "cust.search.desc": "Tavily 是备用搜索选项；接入点固定为 api.tavily.com。豆包搜索的专用测试不会回退到这里。",
  "cust.search.set": "已配置",
  "cust.search.unset": "未配置",
  "cust.search.ph": "输入 Tavily API Key",
  "cust.search.saved": "搜索 Key 已保存",
  "art.uploaded": "上传",
  "art.generated": "生成",
  "cust.models.save": "保存并生效",
  "cust.models.setActive": "设为当前",
  "cust.models.setDefault": "设为默认",
  "cust.models.subtitle": "配置 LLM 兼容协议、Base URL、模型与 API Key（保存后立即生效）",
  "cust.models.subtitle2": "配置多套 LLM API（兼容协议 / Base URL / 模型 / Key），随时新增、切换或删除，方便对接不同接口",
  "cust.models.updateBtn": "更新",
  "cust.network.allowName": "允许联网",
  "cust.network.desc": "联网访问（智能体的 web_search / web_fetch / bash 与代码请求）",
  "cust.network.disabledDesc": "已禁用 — 智能体仅用本地知识与已有文件",
  "cust.network.enabledDesc": "已启用 — 智能体可实时检索文献、抓取数据库、下载数据包",
  "cust.network.title": "网络",
  "cust.telemetry.name": "匿名用量统计",
  "cust.telemetry.off": "已关闭 — 没有任何数据离开这台机器",
  "cust.telemetry.on": "已开启 — 仅发送匿名计数（版本、系统、成功/失败），绝不含提示词、文件名或数据",
  "cust.telemetry.envlock": "已被环境变量 OPENAI4S_TELEMETRY 关闭",
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
  "ac.fromOtherSession": "来自其他会话，发送时会复制进来",
  "refs.problemsTitle": "有 {0} 处引用没能解析（这一轮仍在继续）",
  "refs.unresolvedChip": "这个引用现在解析不到任何文件；发送后这一轮会照常继续。",
  "cust.memory.edited": "已修改",
  "cust.memory.editPrompt": "修改这条记忆的内容：",
  "versions.retrievalSource": "数据来源（只读）",
  "versions.retrievalTruncated": "以下字段过长已截断：{0}",
  "versions.retrievalWithheld": "另有 {0} 个字段未展示",
  "dash.example.cta": "运行示例分析",
  "dash.example.hint": "一次真实的 NIF3/DUF34 分析：调用 UniProt 与 RCSB PDB 接口、执行 6 个 Python Cell、产出图表与报告。启动时不会自动运行——只有你点它才跑。",
  "dash.example.running": "正在运行示例分析……",
  "dash.example.failed": "示例分析失败：",
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
  "dock.tab.timeline": "行动时间线",
  "edac.keyword": "关键字",
  "editor.label": "编辑 {0}",
  "empty.sub": "描述你的科研任务，智能体会写 Python、联网检索、调用技能并产出图表/报告/结构文件。可试试：",
  "empty.title": "开始一个新分析",
  "review.badge.candidate": "候选 · 未验证",
  "review.badge.verified": "已验证",
  "review.badge.completed_with_issues": "完成 · 未验证",
  "review.badge.review_unavailable": "审核不可用 · 未验证",
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
  "wb.table.filter": "筛选 col:值",
  "wb.table.prev": "上一页",
  "wb.table.next": "下一页",
  "wb.table.meta": "{0} 行 · 显示 {1}–{2}",
  "wb.ketcher.edit": "在 Ketcher 中编辑",
  "wb.locator.title": "按位置评论（进入下一轮）",
  "wb.locator.quote": "选中的原文",
  "wb.locator.selector": "CSS 选择器，如 #hit",
  "wb.locator.body": "评论…",
  "wb.locator.saved": "评论已保存",
  "wb.locator.err": "评论失败：{0}",
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
  "model.rebind.confirm": "该会话固定的模型配置已不存在。是否改绑到当前启用的配置以继续？",
  "model.rebind.done": "已改绑到当前启用的模型配置",
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
  "nb.badge.ready": "就绪",
  "nb.cell.statusOk": "ok",
  "nb.cell.statusRunning": "running",
  "nb.kernel.shared": "与 Agent 共享",
  "nb.owner.agent": "Agent",
  "nb.owner.user_repl": "用户 REPL",
  "nb.owner.repair": "Repair",
  "nb.owner.review_scratch": "Review scratch",
  "nb.owner.generation": "generation {0}",
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
  "nb.status.ready": "就绪 · {0}",
  "nb.revisions.summary": "共 {0} 次尝试 · 展开查看 {1} 个失败版本",
  "nb.table.rowsHidden": "… {0} 行未显示",
  "nb.table.colsHidden": "… {0} 列未显示",
  "nb.table.bothHidden": "… {0} 行、{1} 列未显示",
  "nb.action.copy": "复制",
  "nb.action.copied": "已复制代码",
  "nb.action.rerun": "作为新单元运行",
  "nb.action.fork": "从此前 Fork",
  "nb.action.promote": "提升为 Artifact",
  "nb.action.promoted": "已提升为制品 · {0}",
  "nb.action.unavailable": "当前服务尚未提供此操作；历史单元不会被修改。",
  "nb.action.failed": "Notebook 操作失败：{0}",
  "nb.interrupt.noOwner": "当前没有可精确中断的 execution owner。",
  "nb.action.queued": "已追加为新的 {0} 单元",
  "nb.cell.current": "Current",
  "nb.cell.drafting": "模型草稿 · 正在更新",
  "nb.cell.stale": "Stale",
  "nb.cell.nonReplayable": "Non-replayable",
  "nb.cell.historical": "历史版本 · 只读",
  "nb.repl.language": "语言",
  "nb.repl.run": "Shift+Enter 运行",
  "nb.repl.multilineHint": "多行 Python/R 输入只会追加新单元；已执行历史始终只读。",
  "nb.variables.title": "Variable Inspector",
  "nb.variables.language": "命名空间",
  "nb.variables.refresh": "刷新变量",
  "nb.variables.loading": "正在读取当前命名空间…",
  "nb.variables.notLoaded": "选择 Python 或 R，然后手动刷新。读取不会运行 Cell。",
  "nb.variables.empty": "当前命名空间没有可显示的用户变量。",
  "nb.variables.error": "变量读取失败：{0}",
  "nb.variables.generation": "Generation {0}",
  "nb.variables.revision": "State revision S{0}",
  "nb.variables.stale": "可能已过期 · 请刷新",
  "nb.variables.truncated": "只显示前 {0} 个变量",
  "nb.variables.length": "长度 {0}",
  "nb.variables.fingerprint": "指纹 {0}",
  "nb.variables.state.busy": "内核正在执行，Variable Inspector 暂不可用。",
  "nb.variables.state.ended": "该内核 generation 已结束；不会为检查变量而重启。",
  "nb.variables.state.not_started": "该语言内核从未启动；不会为检查变量而启动。",
  "nb.variables.state.restoring": "内核正在恢复，完成后再刷新。",
  "nb.variables.state.unsupported": "当前内核不支持安全变量检查。",
  "nb.variables.state.failed": "变量检查已安全失败；命名空间未被修改。",
  "runtime.branch": "Branch",
  "runtime.python": "Python",
  "runtime.r": "R",
  "runtime.revision": "Revision",
  "runtime.owner": "Owner",
  "runtime.queue": "Queue",
  "runtime.none": "—",
  "runtime.status.live": "Live",
  "runtime.status.busy": "Busy",
  "runtime.status.ended": "Ended · 仅供查看",
  "runtime.status.restoring": "Restoring",
  "runtime.status.partial": "Partial",
  "runtime.status.failed": "Failed",
  "runtime.trust.quarantined": "隔离导入",
  "runtime.trust": "信任",
  "runtime.quarantineHint": "这是未受信任的导入会话，当前仅供查看。请在恢复面板明确确认“全新重启”后再继续。",
  "timeline.title": "Action Timeline",
  "timeline.subtitle": "来自持久 Action Ledger 的安全投影；不显示原始参数或 wire state。",
  "timeline.refresh": "刷新",
  "timeline.loading": "正在读取行动记录…",
  "timeline.loadEarlier": "加载更早记录",
  "timeline.loadingEarlier": "正在加载更早记录…",
  "timeline.loadEarlierFailed": "无法加载更早记录：{0}",
  "timeline.empty": "还没有可显示的行动。Notebook 仅保留科研 cell，完整控制流程会出现在这里。",
  "timeline.owner": "Owner",
  "timeline.permission": "权限",
  "timeline.resources": "资源",
  "timeline.generation": "Generation",
  "timeline.replay": "Replay",
  "timeline.duration": "耗时",
  "timeline.artifacts": "产物",
  "timeline.tokens": "Tokens",
  "timeline.tokensValue": "{0} 输入 · {1} 输出",
  "timeline.cost": "成本",
  "timeline.column.ordinal": "#",
  "timeline.column.kind": "类型",
  "timeline.column.action": "行动",
  "timeline.turnBoundary": "Turn",
  "timeline.inspector": "行动详情",
  "timeline.inspector.close": "关闭详情",
  "timeline.row.open": "查看行动 #{0} 的详情：{1}",
  "timeline.search.label": "搜索已加载记录",
  "timeline.search.placeholder": "搜索标题、类型、资源和产物",
  "timeline.search.scope": "仅搜索当前已加载的 {0} 条记录；未加载的更早记录不在结果中。搜索时会暂时展开 Turn，清空后恢复折叠。",
  "timeline.search.loaded": "已加载 {0} 条记录",
  "timeline.search.matches": "{0} 条匹配（已加载 {1} 条）",
  "timeline.search.matchesInSelection": "当前选区显示 {0} 条（搜索命中 {1} / 已加载 {2} 条）",
  "timeline.search.clear": "清除搜索",
  "timeline.search.empty": "当前已加载范围内没有匹配记录",
  "timeline.search.emptySelection": "当前时间选区内没有搜索命中；已加载范围内共有 {0} 条命中。",
  "timeline.turn.collapse": "收起 Turn {0}，{1} 条当前可见记录，总耗时 {2}",
  "timeline.turn.expand": "展开 Turn {0}，{1} 条当前可见记录，总耗时 {2}",
  "timeline.turn.summary": "Turn · {0} 条当前可见记录",
  "timeline.ledger.keyboard": "键盘：在账本区域按 Enter、向下键或 Home 进入记录；用方向键、Page Up、Page Down、Home 和 End 浏览。",
  "timeline.overview": "时间线概览",
  "timeline.overview.help": "悬停查看精确时刻；拖选过滤；滚轮缩放；右键拖拽平移。",
  "timeline.overview.keyboard": "键盘：上下键浏览行动，Enter 打开，Shift+Enter 按该行动的已知活动区间筛选。",
  "timeline.overview.queue": "排队",
  "timeline.overview.ttft": "首响应",
  "timeline.overview.decode": "解码",
  "timeline.overview.allocated": "分配",
  "timeline.overview.started": "开始",
  "timeline.overview.response": "首响应",
  "timeline.overview.finished": "结束",
  "timeline.overview.running": "运行中 · 仅显示真实起点",
  "timeline.overview.omitted": "加载被省略的更早记录",
  "timeline.overview.omittedLoading": "正在加载被省略的更早记录",
  "timeline.overview.clear": "清除时间选区",
  "timeline.overview.zoomIn": "放大时间域",
  "timeline.overview.zoomOut": "缩小时间域",
  "timeline.overview.panEarlier": "向较早时间平移",
  "timeline.overview.panLater": "向较晚时间平移",
  "timeline.overview.selection": "已选 {0} — {1}",
  "timeline.overview.emptySelection": "选区内没有行动",
  "timeline.kind.native_tool": "Native Tool",
  "timeline.kind.python": "Python Cell",
  "timeline.kind.r": "R Cell",
  "timeline.kind.dynamic_tool": "Dynamic Tool",
  "timeline.kind.delegate": "Delegated Agent",
  "timeline.kind.background": "Background / Remote Job",
  "timeline.kind.permission": "Permission Pause",
  "timeline.kind.recovery": "Recovery Event",
  "timeline.kind.finalize": "FinalizeAction",
  "timeline.kind.action": "Action",
  "timeline.panel.branches": "Branch · Checkpoint",
  "timeline.panel.context": "Context composition",
  "timeline.panel.security": "Sandbox · Permission",
  "timeline.panel.delegation": "子代理树",
  "timeline.panel.compute": "远程计算任务",
  "attach.problemsTitle": "有 {0} 张图没有随本轮发送",
  "attach.tooLarge": "单张 {0}，超过上限 {1}；请缩小分辨率后重新钉图。",
  "attach.budget": "本轮图片总量已达上限 {0}；请减少图钉数量或分几轮发送。",
  "attach.tooMany": "本轮最多附带 {0} 张图。",
  "attach.versionChanged": "图钉之后该图被重新绘制覆盖，你标注的那一版已不存在——请在新图上重新钉图。",
  "attach.notFound": "该图文件已被删除或移动，无法随本轮发送。",
  "attach.unsupported": "该文件的实际内容不是位图（按文件头判断），无法作为图片发送。",
  "attach.decodeFailed": "该图无法解码（文件损坏，或服务端缺少 Pillow）。",
  "delegation.stop": "停止这个子代理及其下级",
  "delegation.steer": "在下一个回合边界给它一句话",
  "delegation.steerPrompt": "要在下一个回合边界告诉这个子代理什么？",
  "delegation.steerQueued": "已排队，将在该子代理的下一个回合边界送达。",
  "delegation.stopFailed": "停止失败",
  "delegation.steerFailed": "引导失败",
  "compute.none": "本会话还没有远程计算任务。",
  "compute.live": "进行中 {0}",
  "compute.fromRecord": "来自本地记录，未联网核对",
  "compute.checked": "刚刚向远端核对过",
  "compute.refresh": "向远端核对并回收产物",
  "compute.refreshFailed": "核对失败",
  "compute.outputs": "产物 {0} 个 · {1}",
  "compute.status.unknown": "未知（联系不上远端）",
  "timeline.noBranch": "尚无 branch/checkpoint 投影。",
  "timeline.noContext": "尚无 context composition 投影。",
  "timeline.noSecurity": "尚无 sandbox/permission 状态投影。",
  "timeline.noDelegation": "本会话尚未创建子代理。",
  "delegation.budget": "预算 {0}/{1}",
  "delegation.active": "活动 {0}",
  "delegation.turns": "边界 {0}/{1}",
  "delegation.steering": "消息：{0} 待投递 · {1} 已投递",
  "branch.current": "当前",
  "branch.viewOnly": "未激活 · 仅查看",
  "branch.currentSummary": "当前分支：{0}",
  "branch.head": "Head {0}",
  "branch.checkpoint": "创建 checkpoint",
  "branch.fork": "Fork",
  "branch.forkName": "为新分支命名（可以留空）",
  "branch.forkDefault": "Fork {0}",
  "branch.forked": "已从 checkpoint {0} 创建分支",
  "branch.activate": "激活",
  "branch.activating": "正在切换…",
  "branch.activated": "已切换到分支 {0}",
  "branch.activatedPartial": "已切换到分支 {0}，但部分状态需要修复；请查看 Recovery。",
  "branch.internalCheckpoints": "内部游标 checkpoint（{0}）",
  "branch.preview": "预览回滚",
  "branch.revert": "回滚并继续",
  "branch.undo": "撤销上次回滚",
  "branch.undone": "已撤销上次回滚",
  "branch.actionFailed": "Branch 操作失败：{0}",
  "branch.conflict": "检测到外部文件冲突，不能直接应用。",
  "branch.previewTitle": "Revert preview",
  "branch.diff": "消息 {0} · Notebook {1} · 文件写入 {2} / 删除 {3} · Artifact +{4}/-{5}",
  "recovery.title": "Kernel Recovery",
  "recovery.checkpoint": "Checkpoint {0}",
  "recovery.action.restore": "恢复 checkpoint",
  "recovery.action.retry": "重试恢复",
  "recovery.action.restart_fresh": "全新重启",
  "recovery.action.ready": "可以执行",
  "recovery.action.loading": "正在执行…",
  "recovery.action.unavailable": "当前服务未提供此 Recovery 操作。",
  "recovery.action.currentOnly": "Recovery 只允许用于当前会话已激活的分支。",
  "recovery.action.failed": "Recovery 操作失败：{0}",
  "recovery.action.done": "Recovery 操作已完成；状态与 journal 已刷新。",
  "recovery.freshConfirm": "全新重启会清空当前 Python/R 内存变量，不会把 checkpoint 当作已恢复的命名空间。对话、Notebook、工作区文件与 Artifact 会保留。确定继续吗？",
  "context.tokens": "{0} tokens",
  "context.outputReserve": "输出预留 {0}",
  "context.messages": "{0} 条消息",
  "context.compressed": "已压缩",
  "context.handoff": "Handoff",
  "context.history": "压缩历史（{0}）",
  "context.compaction": "Compaction",
  "context.savings": "{0} → {1} tokens",
  "context.omitted.memory": "记忆（未注入）",
  "context.omittedCount": "略去 {0} 条",
  "context.reason.too_long": "单条过长",
  "context.reason.too_many": "超出条数",
  "context.reason.budget_exhausted": "超出总量",
  "context.artifacts": "{0} 个 Artifact 引用",
  "security.sandbox": "Sandbox",
  "security.generation": "Generation",
  "security.generationEnded": "{0} 已结束（{1}）",
  "security.permission": "Permission",
  "security.selfTest": "Self-test",
  "security.network": "Network",
  "security.pending": "{0} 个待审批",
  "notes.empty": "还没有笔记。",
  "notes.emptyNoProject": "在某个项目下可添加笔记。",
  "palette.action.backHome": "返回主页",
  "palette.action.customize": "自定义",
  "palette.action.newProject": "新建项目",
  "palette.action.search": "搜索",
  "palette.action.newSession": "新建会话",
  "palette.action.openNotebook": "打开 Notebook",
  "palette.empty": "没有匹配项",
  "palette.group.artifacts": "产物",
  "palette.group.commands": "命令",
  "palette.group.datapro": "专业数据集",
  "palette.group.sessions": "会话",
  "palette.group.skills": "技能",
  "palette.datapro.result": "DataPro 查询结果",
  "palette.searchPlaceholder": "搜索会话、产物、专业数据集、技能，或执行命令…",
  "perm.badge.subAgent": "子智能体",
  "perm.badge.dangerous": "高风险",
  "perm.btn.allow": "允许",
  "perm.btn.continueReplan": "继续并重新规划",
  "perm.btn.deny": "拒绝",
  "perm.continuePrompt": "继续。刚才批准的是守护进程重启前被中断的操作；请先重新评估当前状态，只在仍有必要时发起新的操作，不要假设原操作已经执行。",
  "perm.lbl.rememberRule": "记住规则（可用 * 通配）",
  "perm.lbl.rememberScope": "记住范围",
  "perm.placeholder.denyReason": "（可选）拒绝原因，会反馈给智能体",
  "perm.scope.conversation": "本对话",
  "perm.scope.global": "全局",
  "perm.scope.once": "仅此一次",
  "perm.scope.project": "本项目",
  "perm.status.allowed": "已允许",
  "perm.status.allowedScope": "已允许（{0}）",
  "perm.status.afterRestartAllowed": "批准已记录；守护进程重启后，原操作未执行。",
  "perm.status.afterRestartDenied": "已拒绝；守护进程重启后，原操作未执行。",
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
  "plan.eyebrow.paused": "计划已暂停，还有步骤没跑完",
  "plan.status.paused": "已暂停：{0}/{1} 步完成，还剩 {2} 步",
  "plan.resume": "继续执行剩余步骤",
  "plan.resumeFailed": "无法继续执行：{0}",
  "plan.resuming": "正在继续执行剩余步骤…",
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
  "proj.menu.downloadArtifacts": "下载产物",
  "proj.menu.settings": "项目设置",
  "proj.menu.newProject": "新建项目",
  "projectResearch.menu": "项目研究图谱",
  "projectResearch.title": "{0} · 全局研究视图",
  "projectResearch.timeline": "Timeline",
  "projectResearch.lineage": "Lineage",
  "projectResearch.timelineSummary": "{0} 个会话 · {1} 个行动",
  "projectResearch.lineageSummary": "{0} 个 Artifact · {1} 个版本 · {2} 条边",
  "projectResearch.latest": "latest",
  "projectResearch.noLineage": "还没有项目级血缘数据。",
  "projectResearch.edges": "血缘边（{0}）",
  "share.menu": "分享（只读链接）",
  "share.title": "分享此会话",
  "share.scope": "将公开：对话、Notebook 代码与输出、产物文件、环境清单。任何持有链接的人均可查看，内容经你的 relay 明文中转。",
  "share.create": "创建分享链接",
  "share.copy": "复制",
  "share.copied": "已复制链接",
  "share.update": "更新快照",
  "share.updated": "快照已更新",
  "share.revoke": "撤销",
  "share.revokeConfirm": "确定撤销该分享链接？撤销后立即失效。",
  "share.revoked": "已撤销",
  "share.disabled": "分享功能未启用。",
  "share.enable": "启用分享",
  "share.expiry": "有效期：",
  "share.expiry.never": "永不过期",
  "share.expiry.1d": "1 天",
  "share.expiry.7d": "7 天",
  "share.expiry.30d": "30 天",
  "share.expiresAt": "过期于",
  "share.neverExpires": "永不过期",
  "share.unconfigured": "分享未配置（需设置 relay URL 与 token，见 docs/webshare.md）。",
  "share.close": "关闭",
  "sessionPackage.import": "导入会话包",
  "sessionPackage.export": "导出会话包",
  "sessionPackage.imported": "会话包已安全导入；Kernel 保持结束状态，需显式恢复",
  "sessionPackage.tooLarge": "会话包超过客户端 128 MiB 限制",
  "sessionPackage.verified": "校验通过：{0} 个文件与包内清单一致，正在导入",
  "sessionPackage.verifyFailed": "校验未通过，已拒绝导入：{0}",
  "projModal.create": "创建",
  "projModal.editTitle": "项目设置",
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
  "prov.env.recordedUnverified": "环境已记录，但无法确认它只属于这一次生产运行",
  "prov.env.remoteTitle": "远程 GPU 计算（可复现）",
  "prov.env.remoteHost": "主机",
  "prov.env.remoteEnv": "环境",
  "prov.env.remotePkgs": "依赖",
  "prov.env.remoteCode": "代码版本",
  "prov.env.remoteModel": "模型/权重",
  "prov.env.remoteRun": "运行时间(UTC)",
  "prov.env.thPackage": "Package",
  "prov.env.thVersion": "Version",
  "prov.exec.downloadNotebook": "下载 Notebook（打包）",
  "prov.exec.downloadPython": "只下载 Python Notebook (.ipynb)",
  "prov.exec.downloadR": "只下载 R Notebook (.ipynb)",
  "prov.exec.downloadMarkdown": "下载 Markdown 记录 (.md)",
  "prov.exec.downloadMore": "其他导出格式",
  "prov.exec.noRecords": "暂无执行记录。",
  "prov.msg.loadFailed": "无法加载对话：{0}",
  "prov.msg.loading": "加载对话…",
  "prov.msg.noRecords": "暂无对话记录。",
  "prov.msg.roleAssistant": "Assistant",
  "prov.msg.roleSystem": "System",
  "prov.msg.roleUser": "User",
  "prov.review.noLineage": "暂无溯源信息（execution log 为空或未记录文件 I/O）。",
  "prov.review.producedBy": "由单元 {0} 生成",
  "prov.review.producedByIdentity": "由 Cell {0} 生成",
  "prov.review.producerFrame": "{0} frame · {1}",
  "prov.review.nonCellProducer": "由非 Cell 动作生成",
  "prov.review.sameBytesCapture": "相同字节 · capture observation",
  "prov.review.versionCapture": "版本捕获 · capture observation",
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
  "session.loadMore": "加载更多会话",
  "session.loadMoreLimit": "已达列表上限，更早的会话请用搜索查找",
  "session.menu.tip": "会话操作",
  "session.newFolder": "＋ 文件夹",
  "session.untitled": "未命名会话",
  "sessionMenu.duplicate": "复制会话",
  "sessionMenu.cancel": "取消任务",
  "sessionMenu.downloadArtifacts": "下载产物",
  "sessionMenu.exportMarkdown": "导出为 Markdown",
  "sessionMenu.viewNotebook": "查看 Notebook",
  "compute.menu.runLocation": "运行位置",
  "compute.location.local": "本机（守护进程）",
  "compute.location.localHint": "内核跑在这台运行 daemon 的机器上。",
  "compute.dialog.title": "这个会话在哪里运行",
  "compute.dialog.notConfigured": "本 daemon 未开启 worker 监听，无法在集群上运行会话。",
  "compute.dialog.release": "释放集群资源",
  "compute.badge.local": "本机",
  "compute.blocked.allocation": "排队等待资源",
  "compute.blocked.workspace": "准备工作区",
  "compute.blocked.worker": "等待 worker 连回",
  "compute.blocked.kernel": "启动内核",
  "compute.badge.ready": "集群就绪",
  "compute.lost.title": "内核状态已丢失",
  "compute.lost.body": "这个会话的资源被回收，内核的内存随之丢失（变量、import、已加载的数据都不在了）。会话已在新的 epoch 上继续，但之前定义的东西需要重新执行。",
  "compute.lost.dismiss": "知道了",
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
  "skill.readiness.needsSetup": "本机缺少：{0}",
  "skill.readiness.unknown": "本机无法确认：{0}",
  "skill.saveBtn": "保存技能",
  "skill.historyBtn": "版本历史",
  "skill.historyTitle": "技能版本 — {0}",
  "skill.historyEmpty": "这个作用域还没有可回滚的版本。",
  "skill.scope.personal": "个人",
  "skill.scope.project": "项目",
  "skill.scope.bundled": "内置",
  "skill.versionActive": "当前版本",
  "skill.rollbackBtn": "回滚到此版本",
  "skill.rollbackConfirm": "将 {0} 回滚到版本 {1}？当前版本仍会保留。",
  "skill.rollbackDone": "已将 {0} 回滚到所选版本",
  "skill.versionSidecar": "Sidecar：{0}",
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
  "toast.telemetry.on": "匿名统计已开启",
  "toast.telemetry.off": "匿名统计已关闭，身份一并删除",
  "toast.telemetry.failed": "统计开关未能保存，已恢复原状态：{0}",
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
  "turn.failedCommitted": "这一轮失败了，但它已经产出了输出或执行过工具——直接重试会重复已经发生的操作。请先检查结果再决定。",
  "turn.failure.llmRequestBurst": "模型服务触发了突发流量保护。这不是 API Key 配置问题，请稍后在当前会话继续，或临时切换模型。",
  "turn.failure.llmRateLimited": "模型服务正在限流。请稍后在当前会话继续，或临时切换模型。",
  "turn.failure.llmUpstreamOverloaded": "模型服务当前过载。这不是 API Key 配置问题，请稍后在当前会话继续，或临时切换模型。",
  "turn.supportId": "支持 ID：{0}",
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
  "viewer.chem.fallback": "当前文件没有可安全绘制的二维坐标，下面保留原始化学表示。",
  "viewer.chem.source": "原始化学表示",
  "viewer.downloadOnly": "该二进制产物没有安全的内置预览器。",
  "viewer.empty": "在会话里点击一个文件以查看。",
  "viewer.genome.features": "{0} 个特征 · {1} 条染色体/序列",
  "viewer.genome.invalid": "忽略 {0} 条无效记录",
  "viewer.genome.list": "特征描述",
  "viewer.latex.preview": "安全预览",
  "viewer.latex.source": "LaTeX 源码",
  "viewer.loading": "正在选择安全渲染器…",
  "viewer.msa.summary": "{0} 条序列 · {1} 列 · {2}",
  "viewer.renderer.compat": "兼容模式",
  "viewer.renderer.error": "无法预览此产物，可继续下载原文件。",
  "viewer.renderer.matched": "匹配：{0}",
  "viewer.renderer.version": "版本 {0}",
  "viewer.sequence.omitted": "为保持界面流畅，其余 {0} 个残基未展开。",
  "viewer.sequence.summary": "{0} 条序列 · {1} 个残基 · {2}",
  "viewer.table.shape": "共 {0} 行 × {1} 列",
  "ws.nav.files": "文件",
  "ws.nav.new": "新建",
  "ws.sidebar.collapse": "收起侧栏 (⌘B)",
  "ws.sidebar.expand": "展开侧栏 (⌘B)",
});
Object.assign(I18N.en, {
  "cust.general.language": "Language",
  "cust.general.languageDesc": "Interface display language (saved in this browser)",
  "cust.general.themeName": "Appearance",
  "cust.general.themeDesc": "Light / dark / follow system (saved in this browser)",
  "theme.light": "Light",
  "theme.dark": "Dark",
  "theme.system": "System",
  "theme.toggle": "Toggle theme",
  "toast.theme": "Theme: {0}",
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
  "annot.status.pending": "Sending",
  "annot.status.unknown": "Unknown",
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
  "composer.addToMessage": "Add to message",
  "composer.sessionOptions": "Session options",
  "composer.menu.attachFiles": "Attach files",
  "composer.menu.contextUsage": "Context usage",
  "composer.menu.requestReview": "Request review",
  "composer.menu.saveAsSkill": "Save as skill",
  "composer.menu.yourFiles": "Your files",
  "composer.option.autoReview": "Auto-review",
  "composer.option.compute": "Compute",
  "composer.option.delegation": "Delegation",
  "composer.option.memory": "Memory",
  "composer.option.reviewerModel": "Reviewer model",
  "composer.option.sameModel": "Same as agent",
  "composer.option.specialist": "Specialist",
  "composer.model": "Model",
  "composer.placeholder": "Ask anything — @ for artifacts, # for sessions, / for skills, ⌘K to search…",
  "composer.placeholderQueue": "A turn is running — sending now queues this behind it…",
  "queue.accepted": "Queued — it will run after the current turn",
  "queue.waiting": "{0} queued",
  "queue.noPreview": "(no text)",
  "queue.underProfile": "profile {0} · rev {1}",
  "queue.onBranch": "branch {0}",
  "queue.cancelOne": "Drop this queued message (the running turn keeps going)",
  "queue.cancelled": "Queued message dropped",
  "queue.cancelFailed": "Could not drop the queued message: {0}",
  "composer.planMode": "Plan mode",
  "composer.exploreMode": "Explore mode",
  "composer.voice": "Voice input",
  "confirm.deleteSession": "Delete this session? This action cannot be undone.",
  "conv.dockToggle": "Side panel",
  "conv.jumpLast": "Jump to latest",
  "conv.jumpLastLabel": "Latest",
  "conv.loadEarlier": "Load earlier messages",
  "conv.loadEarlierFailed": "Could not load earlier messages: {0}",
  "conv.exportTruncated": "(Export stopped at the walk limit; earlier messages are not included.)",
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
  "environment.readiness.bannerTitle": "The standard science environment is not ready",
  "environment.readiness.bannerMissing": "Missing {0} environment(s) and {1} package(s); resolve these before the first science Cell runs.",
  "environment.readiness.bannerUnavailable": "Science-environment readiness cannot be confirmed; an unknown state is not shown as ready.",
  "environment.readiness.openCompute": "View gaps and repair command",
  "environment.readiness.cardTitle": "Standard profile readiness",
  "environment.readiness.ready": "Ready: the standard Python and R dependencies were checked.",
  "environment.readiness.needsSetup": "One or more standard environments must be created.",
  "environment.readiness.needsRepair": "The environments exist, but packages from the standard manifest are missing.",
  "environment.readiness.unavailable": "The local readiness check could not complete; the UI does not guess that it is ready.",
  "environment.readiness.missingEnvironments": "Missing environments",
  "environment.readiness.missingPackages": "Packages missing from {0}",
  "environment.readiness.remediation": "Managed repair command",
  "environment.readiness.explicitOnly": "Copy only; you must run it explicitly. Nothing is installed automatically.",
  "environment.readiness.copy": "Copy command",
  "environment.readiness.copied": "Repair command copied",
  "environment.readiness.refresh": "Refresh readiness",
  "environment.readiness.sendBlocked": "The first science Cell was stopped before startup. Complete the standard environment setup first.",
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
  "cust.datapro.title": "Volcengine Ark Professional Dataset DataPro",
  "cust.datapro.desc": "Save one Agent Plan Key and call dataPro_search directly; an existing Ark API Key is reused automatically.",
  "cust.datapro.connectorToggle": "Enable / disable the DataPro connector",
  "cust.datapro.connectorOn": "DataPro connector enabled.",
  "cust.datapro.connectorOff": "DataPro connector disabled; the agent can no longer reach it.",
  "cust.datapro.keyLabel": "Agent Plan Key",
  "cust.datapro.keyPlaceholder": "Enter Agent Plan Key",
  "cust.datapro.keyPlaceholderSet": "Saved; enter a new key to replace it",
  "cust.datapro.keyPlaceholderArk": "Using the Ark API Key; enter a new key to replace it",
  "cust.datapro.keyConfigured": "Credential saved",
  "cust.datapro.keyArkReused": "Using the Ark API Key",
  "cust.datapro.keyMissing": "Agent Plan Key is not configured",
  "cust.datapro.saveKey": "Save credential",
  "cust.datapro.keyRequired": "Enter an Agent Plan Key",
  "cust.datapro.keySaved": "Agent Plan Key saved securely",
  "cust.datapro.queryLabel": "Search the professional dataset",
  "cust.datapro.queryPlaceholder": "Enter a research question",
  "cust.datapro.search": "Call professional dataset",
  "cust.datapro.searching": "Searching…",
  "cust.datapro.queryRequired": "Enter query text",
  "cust.datapro.available": "Professional dataset available",
  "cust.datapro.indexed": "Fully indexed all {0} records returned by this query ({1} content leaf nodes)",
  "cust.datapro.indexFailed": "Indexing this returned content failed; the professional dataset is not yet available",
  "cust.datapro.auth4011": "The Key is invalid, quota is insufficient, or the professional dataset Harness is not enabled.",
  "cust.datapro.unavailable": "Professional dataset unavailable (code {0})",
  "cust.datapro.requestFailed": "Professional dataset query failed: {0}",
  "cust.datapro.result": "Query result",
  "cust.datapro.noResult": "No query has been run yet.",
  "cust.datapro.enableSkill": "Import/enable volcengine-datapro Skill",
  "cust.datapro.enablingSkill": "Enabling…",
  "cust.datapro.skillEnabled": "volcengine-datapro Skill enabled",
  "cust.datapro.skillEnabledToast": "volcengine-datapro Skill and DataPro connector enabled",
  "cust.datapro.artifact": "Saved: {0}",
  "cust.doubao.title": "Doubao Search Custom",
  "cust.doubao.primary": "Primary",
  "cust.doubao.desc": "The primary web-search option. It shares one Agent Plan Key with Ark and DataPro, and is marked available only after a real non-empty Doubao response.",
  "cust.doubao.keyLabel": "Agent Plan Key (one-time authorization)",
  "cust.doubao.keyPlaceholder": "Enter Agent Plan Key",
  "cust.doubao.keyPlaceholderSet": "Saved; enter a new key to replace it",
  "cust.doubao.keyPlaceholderArk": "Using the Ark API Key; enter a new key to replace it",
  "cust.doubao.keyConfigured": "Credential saved",
  "cust.doubao.keyArkReused": "Using the Ark API Key",
  "cust.doubao.keyMissing": "Agent Plan Key is not configured",
  "cust.doubao.saveKey": "Save credential",
  "cust.doubao.keyRequired": "Enter an Agent Plan Key",
  "cust.doubao.keySaved": "Agent Plan Key saved securely for both Doubao Search and DataPro",
  "cust.doubao.queryLabel": "Query Doubao Search",
  "cust.doubao.queryPlaceholder": "Enter a web-search query (up to 100 characters)",
  "cust.doubao.search": "Call Doubao Search",
  "cust.doubao.searching": "Searching…",
  "cust.doubao.queryRequired": "Enter query text",
  "cust.doubao.available": "Doubao Search available",
  "cust.doubao.empty": "Doubao Search returned no usable result",
  "cust.doubao.requestFailed": "Doubao Search failed: {0}",
  "cust.doubao.result": "Search results",
  "cust.doubao.noResult": "No search has been run yet.",
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
  "cust.jobs.dropped": "· {0} bytes dropped",
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
  "cust.memory.injectedCounts": "{0} injected · {1} omitted · {2} inherited from global · {3} hidden by this project's own blocks",
  "cust.memory.injectedInto": "Injected into {0}",
  "cust.memory.scope.global": "Global (all projects)",
  "cust.memory.scopeName": "Scope",
  "cust.memory.title": "Memory",
  "cust.models.activePill": "Active",
  "cust.models.addBtn": "Add",
  "cust.models.addHeading": "Add model / API",
  "cust.models.available": "Available models",
  "cust.models.baseUrl.placeholder": "Base URL (leave blank to use the protocol default)",
  "cust.models.baseUrlPlaceholder": "Base URL (leave blank for the protocol default)",
  "cust.models.cancelEdit": "Cancel edit",
  "cust.models.configuredHeading": "Configured models / APIs",
  "cust.models.editHeading": "Edit: {0}",
  "cust.models.empty2": "No models configured yet. Add one with the form above.",
  "cust.models.hasKey": "🔑 Key configured",
  "cust.models.reachable": "the endpoint answered a minimal request",
  "cust.models.test": "Test",
  "cust.models.testing": "contacting the endpoint…",
  "cust.models.unreachable": "could not reach the endpoint",
  "cust.models.key.configured": "✅ API Key configured",
  "cust.models.key.missing": "⚠️ API Key not configured yet — sending messages will fail",
  "cust.models.key.placeholder.set": "API Key (already configured, leave blank to keep unchanged)",
  "cust.models.key.placeholder.unset": "API Key (not configured yet, please fill in)",
  "cust.models.keyPlaceholderSet": "API Key (configured; leave blank to keep)",
  "cust.models.keyPlaceholderUnset": "API Key (not configured; enter one)",
  "cust.models.label.apiKey": "API Key",
  "cust.models.label.baseUrl": "Base URL",
  "cust.models.label.defaultModel": "Default model",
  "cust.models.label.provider": "Compatible protocol",
  "cust.models.label.protocol": "Compatible protocol",
  "cust.models.model.placeholder": "Model id (leave blank to use the protocol default)",
  "cust.models.modelPlaceholder2": "Model id (leave blank for the protocol default)",
  "cust.models.namePlaceholder": "Name (e.g. DeepSeek Prod / Local vLLM)",
  "cust.models.local.title": "Local inference servers",
  "cust.models.local.desc": "Press the button below to scan fixed loopback ports for Ollama, LM Studio, vLLM, and llama.cpp. Scanning never changes the active model; unknown capabilities default to conservative Code-as-Action.",
  "cust.models.local.scan": "Scan this machine",
  "cust.models.local.idle": "Not scanned yet. Press the button above to look for Ollama, LM Studio, vLLM, and llama.cpp on this machine.",
  "cust.models.local.scanning": "Scanning this machine…",
  "cust.models.local.none": "No local OpenAI-compatible endpoint was detected.",
  "cust.models.local.models": "{0} models",
  "cust.models.local.add": "Add profile",
  "cust.models.local.configured": "Configured",
  "cust.models.local.added": "Added local model: {0}",
  "cust.models.local.error": "Local model discovery failed: {0}",
  "cust.models.local.keyless": "local · no API key required",
  "cust.models.noKey": "⚠️ No key",
  "cust.models.protocol.openai": "OpenAI-compatible protocol",
  "cust.models.protocol.anthropic": "Anthropic-compatible protocol",
  "cust.models.protocol.ark": "Ark-compatible protocol",
  "cust.models.protocol.gemini": "Gemini-compatible protocol",
  "cust.models.protocol.openaiResponses": "OpenAI Responses protocol",
  "cust.search.name": "Backup search API key (Tavily)",
  "cust.search.desc": "Tavily is the backup search option at the fixed api.tavily.com endpoint. The dedicated Doubao test never falls back to it.",
  "cust.search.set": "Configured",
  "cust.search.unset": "Not configured",
  "cust.search.ph": "Enter Tavily API key",
  "cust.search.saved": "Search key saved",
  "art.uploaded": "UPLOADED",
  "art.generated": "GENERATED",
  "cust.models.save": "Save and apply",
  "cust.models.setActive": "Set active",
  "cust.models.setDefault": "Set as default",
  "cust.models.subtitle": "Configure the LLM-compatible protocol, Base URL, model, and API Key (takes effect immediately after saving)",
  "cust.models.subtitle2": "Configure multiple LLM APIs (compatible protocol / Base URL / model / key); add, switch, or remove anytime to work with different endpoints",
  "cust.models.updateBtn": "Update",
  "cust.network.allowName": "Allow network access",
  "cust.network.desc": "Network access (the agent's web_search / web_fetch / bash and code requests)",
  "cust.network.disabledDesc": "Disabled — the agent uses only local knowledge and existing files",
  "cust.network.enabledDesc": "Enabled — the agent can search literature in real time, scrape databases, and download data packages",
  "cust.network.title": "Network",
  "cust.telemetry.name": "Anonymous usage statistics",
  "cust.telemetry.off": "Off — nothing leaves this machine",
  "cust.telemetry.on": "On — anonymous counts only (version, OS, success/failure); never prompts, file names, or data",
  "cust.telemetry.envlock": "Disabled by the OPENAI4S_TELEMETRY environment variable",
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
  "ac.fromOtherSession": "from another session — copied in on send",
  "refs.problemsTitle": "{0} reference(s) did not resolve (the turn still ran)",
  "refs.unresolvedChip": "This reference resolves to nothing right now; the turn will still run.",
  "cust.memory.edited": "edited",
  "cust.memory.editPrompt": "Edit this memory:",
  "versions.retrievalSource": "Retrieved from (read-only)",
  "versions.retrievalTruncated": "clipped for length: {0}",
  "versions.retrievalWithheld": "{0} further field(s) not shown",
  "dash.example.cta": "Run the example analysis",
  "dash.example.hint": "A real NIF3/DUF34 analysis: calls the UniProt and RCSB PDB APIs, runs 6 Python cells, and produces figures and a report. It does not run on startup \u2014 only when you click.",
  "dash.example.running": "Running the example analysis\u2026",
  "dash.example.failed": "The example analysis failed: ",
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
  "dock.tab.timeline": "Action Timeline",
  "edac.keyword": "Keywords",
  "editor.label": "Editing {0}",
  "empty.sub": "Describe your research task and the agent will write Python, search the web, invoke skills, and produce charts/reports/structure files. Try:",
  "empty.title": "Start a new analysis",
  "review.badge.candidate": "Candidate · not verified",
  "review.badge.verified": "Verified",
  "review.badge.completed_with_issues": "Completed · unverified",
  "review.badge.review_unavailable": "Unavailable · not verified",
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
  "wb.table.filter": "Filter col:value",
  "wb.table.prev": "Previous",
  "wb.table.next": "Next",
  "wb.table.meta": "{0} rows · showing {1}–{2}",
  "wb.ketcher.edit": "Edit in Ketcher",
  "wb.locator.title": "Location comments (sent on the next turn)",
  "wb.locator.quote": "Selected source text",
  "wb.locator.selector": "CSS selector, e.g. #hit",
  "wb.locator.body": "Comment…",
  "wb.locator.saved": "Comment saved",
  "wb.locator.err": "Comment failed: {0}",
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
  "model.rebind.confirm": "The model configuration this session was pinned to no longer exists. Re-bind it to the active configuration and continue?",
  "model.rebind.done": "Re-bound to the active model configuration",
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
  "nb.badge.ready": "Ready",
  "nb.cell.statusOk": "ok",
  "nb.cell.statusRunning": "running",
  "nb.kernel.shared": "shared with the agent",
  "nb.owner.agent": "Agent",
  "nb.owner.user_repl": "User REPL",
  "nb.owner.repair": "Repair",
  "nb.owner.review_scratch": "Review scratch",
  "nb.owner.generation": "generation {0}",
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
  "nb.status.ready": "Ready · {0}",
  "nb.revisions.summary": "{0} attempts · expand {1} failed revisions",
  "nb.table.rowsHidden": "… {0} rows not shown",
  "nb.table.colsHidden": "… {0} columns not shown",
  "nb.table.bothHidden": "… {0} rows and {1} columns not shown",
  "nb.action.copy": "Copy",
  "nb.action.copied": "Code copied",
  "nb.action.rerun": "Rerun as new",
  "nb.action.fork": "Fork from before",
  "nb.action.promote": "Promote to Artifact",
  "nb.action.promoted": "Promoted to Artifact · {0}",
  "nb.action.unavailable": "This operation is not exposed by the current server; history will not be modified.",
  "nb.action.failed": "Notebook action failed: {0}",
  "nb.interrupt.noOwner": "There is no exact execution owner to interrupt.",
  "nb.action.queued": "Appended as a new {0} cell",
  "nb.cell.current": "Current",
  "nb.cell.drafting": "Model draft · updating",
  "nb.cell.stale": "Stale",
  "nb.cell.nonReplayable": "Non-replayable",
  "nb.cell.historical": "Historical revision · read only",
  "nb.repl.language": "Language",
  "nb.repl.run": "Shift+Enter to run",
  "nb.repl.multilineHint": "Multiline Python/R input only appends new cells; executed history is always read-only.",
  "nb.variables.title": "Variable Inspector",
  "nb.variables.language": "Namespace",
  "nb.variables.refresh": "Refresh variables",
  "nb.variables.loading": "Reading the current namespace…",
  "nb.variables.notLoaded": "Choose Python or R, then refresh manually. Inspection never runs a Cell.",
  "nb.variables.empty": "There are no displayable user variables in this namespace.",
  "nb.variables.error": "Variable inspection failed: {0}",
  "nb.variables.generation": "Generation {0}",
  "nb.variables.revision": "State revision S{0}",
  "nb.variables.stale": "Possibly stale · refresh",
  "nb.variables.truncated": "Showing only the first {0} variables",
  "nb.variables.length": "length {0}",
  "nb.variables.fingerprint": "fingerprint {0}",
  "nb.variables.state.busy": "The kernel is executing; Variable Inspector is temporarily unavailable.",
  "nb.variables.state.ended": "This kernel generation has ended; inspection will not restart it.",
  "nb.variables.state.not_started": "This language kernel has never started; inspection will not start it.",
  "nb.variables.state.restoring": "Kernel recovery is in progress; refresh when it finishes.",
  "nb.variables.state.unsupported": "This kernel does not support safe variable inspection.",
  "nb.variables.state.failed": "Variable inspection failed closed; the namespace was not changed.",
  "runtime.branch": "Branch",
  "runtime.python": "Python",
  "runtime.r": "R",
  "runtime.revision": "Revision",
  "runtime.owner": "Owner",
  "runtime.queue": "Queue",
  "runtime.none": "—",
  "runtime.status.live": "Live",
  "runtime.status.busy": "Busy",
  "runtime.status.ended": "Ended · view only",
  "runtime.status.restoring": "Restoring",
  "runtime.status.partial": "Partial",
  "runtime.status.failed": "Failed",
  "runtime.trust.quarantined": "Quarantined import",
  "runtime.trust": "Trust",
  "runtime.quarantineHint": "This imported Session is untrusted and view-only. Explicitly confirm Restart fresh in Recovery before continuing.",
  "timeline.title": "Action Timeline",
  "timeline.subtitle": "Safe projection of the durable Action Ledger; raw arguments and wire state are never shown.",
  "timeline.refresh": "Refresh",
  "timeline.loading": "Loading actions…",
  "timeline.loadEarlier": "Load earlier actions",
  "timeline.loadingEarlier": "Loading earlier actions…",
  "timeline.loadEarlierFailed": "Could not load earlier actions: {0}",
  "timeline.empty": "No actions to show yet. Notebook keeps scientific cells; the full control flow appears here.",
  "timeline.owner": "Owner",
  "timeline.permission": "Permission",
  "timeline.resources": "Resources",
  "timeline.generation": "Generation",
  "timeline.replay": "Replay",
  "timeline.duration": "Duration",
  "timeline.artifacts": "Artifacts",
  "timeline.tokens": "Tokens",
  "timeline.tokensValue": "{0} in · {1} out",
  "timeline.cost": "Cost",
  "timeline.column.ordinal": "#",
  "timeline.column.kind": "Kind",
  "timeline.column.action": "Action",
  "timeline.turnBoundary": "Turn",
  "timeline.inspector": "Action details",
  "timeline.inspector.close": "Close details",
  "timeline.row.open": "Open details for action #{0}: {1}",
  "timeline.search.label": "Search loaded actions",
  "timeline.search.placeholder": "Search title, kind, resources, and artifacts",
  "timeline.search.scope": "Search covers only currently loaded actions (loaded: {0}); unloaded earlier history is not included. Searching temporarily reveals collapsed Turns; clearing restores them.",
  "timeline.search.loaded": "Loaded actions: {0}",
  "timeline.search.matches": "Loaded-window matches: {0} · loaded actions: {1}",
  "timeline.search.matchesInSelection": "Matches in this time selection: {0} · loaded-window matches: {1} · loaded actions: {2}",
  "timeline.search.clear": "Clear search",
  "timeline.search.empty": "No matches in the currently loaded actions",
  "timeline.search.emptySelection": "No search matches in this time selection · loaded-window matches: {0}",
  "timeline.turn.collapse": "Collapse Turn {0} · visible actions: {1} · total duration: {2}",
  "timeline.turn.expand": "Expand Turn {0} · visible actions: {1} · total duration: {2}",
  "timeline.turn.summary": "Turn · visible actions: {0}",
  "timeline.ledger.keyboard": "Keyboard: press Enter, Down, or Home on the ledger to enter it; use arrows, Page Up, Page Down, Home, and End to browse.",
  "timeline.overview": "Timeline overview",
  "timeline.overview.help": "Hover for exact timing; drag to filter; wheel to zoom; right-drag to pan.",
  "timeline.overview.keyboard": "Keyboard: use Up and Down to inspect actions, Enter to open one, or Shift+Enter to filter by its known active interval.",
  "timeline.overview.queue": "Queue",
  "timeline.overview.ttft": "First response",
  "timeline.overview.decode": "Decode",
  "timeline.overview.allocated": "Allocated",
  "timeline.overview.started": "Started",
  "timeline.overview.response": "First response",
  "timeline.overview.finished": "Finished",
  "timeline.overview.running": "Running · real start only",
  "timeline.overview.omitted": "Load omitted earlier actions",
  "timeline.overview.omittedLoading": "Loading omitted earlier actions",
  "timeline.overview.clear": "Clear time selection",
  "timeline.overview.zoomIn": "Zoom in on time",
  "timeline.overview.zoomOut": "Zoom out on time",
  "timeline.overview.panEarlier": "Pan to earlier time",
  "timeline.overview.panLater": "Pan to later time",
  "timeline.overview.selection": "Selected {0} — {1}",
  "timeline.overview.emptySelection": "No actions in this range",
  "timeline.kind.native_tool": "Native Tool",
  "timeline.kind.python": "Python Cell",
  "timeline.kind.r": "R Cell",
  "timeline.kind.dynamic_tool": "Dynamic Tool",
  "timeline.kind.delegate": "Delegated Agent",
  "timeline.kind.background": "Background / Remote Job",
  "timeline.kind.permission": "Permission Pause",
  "timeline.kind.recovery": "Recovery Event",
  "timeline.kind.finalize": "FinalizeAction",
  "timeline.kind.action": "Action",
  "timeline.panel.branches": "Branch · Checkpoint",
  "timeline.panel.context": "Context composition",
  "timeline.panel.security": "Sandbox · Permission",
  "timeline.panel.delegation": "Sub-agent tree",
  "timeline.panel.compute": "Remote compute",
  "attach.problemsTitle": "{0} image(s) were not sent with this turn",
  "attach.tooLarge": "{0}, over the {1} per-image limit — downscale it and pin again.",
  "attach.budget": "this turn's {0} image budget is spent — pin fewer figures, or split across turns.",
  "attach.tooMany": "at most {0} images may be attached to one turn.",
  "attach.versionChanged": "the figure was re-plotted over after you pinned it — the version you annotated no longer exists; pin again on the new one.",
  "attach.notFound": "the file was deleted or moved, so it could not be sent.",
  "attach.unsupported": "the file's actual contents are not a raster image (judged by its header), so it cannot be sent as one.",
  "attach.decodeFailed": "the image could not be decoded (corrupt file, or Pillow missing on the server).",
  "delegation.stop": "Stop this sub-agent and everything under it",
  "delegation.steer": "Send it a message at its next turn boundary",
  "delegation.steerPrompt": "What should this sub-agent be told at its next turn boundary?",
  "delegation.steerQueued": "Queued — it will arrive at the sub-agent's next turn boundary.",
  "delegation.stopFailed": "Stop failed",
  "delegation.steerFailed": "Steering failed",
  "compute.none": "No remote compute tasks in this session.",
  "compute.live": "{0} in flight",
  "compute.fromRecord": "from the local record — not re-checked",
  "compute.checked": "just checked with the remote",
  "compute.refresh": "Check the remote and harvest outputs",
  "compute.refreshFailed": "Refresh failed",
  "compute.outputs": "{0} output file(s) · {1}",
  "compute.status.unknown": "unknown (the remote could not be reached)",
  "timeline.noBranch": "No branch/checkpoint projection is available yet.",
  "timeline.noContext": "No context composition projection is available yet.",
  "timeline.noSecurity": "No sandbox/permission projection is available yet.",
  "timeline.noDelegation": "No sub-agent has been created in this session.",
  "delegation.budget": "Budget {0}/{1}",
  "delegation.active": "Active {0}",
  "delegation.turns": "Boundary {0}/{1}",
  "delegation.steering": "Messages: {0} queued · {1} delivered",
  "branch.current": "current",
  "branch.viewOnly": "inactive · view only",
  "branch.currentSummary": "Current branch: {0}",
  "branch.head": "Head {0}",
  "branch.checkpoint": "Create checkpoint",
  "branch.fork": "Fork",
  "branch.forkName": "Name the new branch (optional)",
  "branch.forkDefault": "Fork {0}",
  "branch.forked": "Created a branch from checkpoint {0}",
  "branch.activate": "Activate",
  "branch.activating": "Switching…",
  "branch.activated": "Activated branch {0}",
  "branch.activatedPartial": "Activated branch {0}, but some state needs repair; inspect Recovery.",
  "branch.internalCheckpoints": "Internal cursor checkpoints ({0})",
  "branch.preview": "Preview revert",
  "branch.revert": "Revert and continue",
  "branch.undo": "Undo last revert",
  "branch.undone": "The last revert was undone",
  "branch.actionFailed": "Branch action failed: {0}",
  "branch.conflict": "External file conflicts prevent this revert from being applied.",
  "branch.previewTitle": "Revert preview",
  "branch.diff": "Messages {0} · Notebook {1} · files write {2} / delete {3} · Artifacts +{4}/-{5}",
  "recovery.title": "Kernel Recovery",
  "recovery.checkpoint": "Checkpoint {0}",
  "recovery.action.restore": "Restore checkpoint",
  "recovery.action.retry": "Retry recovery",
  "recovery.action.restart_fresh": "Restart fresh",
  "recovery.action.ready": "Ready",
  "recovery.action.loading": "Running…",
  "recovery.action.unavailable": "This Recovery action is not advertised by the current server.",
  "recovery.action.currentOnly": "Recovery is available only for the session's active branch.",
  "recovery.action.failed": "Recovery action failed: {0}",
  "recovery.action.done": "Recovery finished; status and journal were refreshed.",
  "recovery.freshConfirm": "A fresh restart clears the current Python/R in-memory variables and does not claim the checkpoint namespace was restored. Conversation, Notebook, workspace files, and Artifacts remain. Continue?",
  "context.tokens": "{0} tokens",
  "context.outputReserve": "output reserve {0}",
  "context.messages": "{0} messages",
  "context.compressed": "compressed",
  "context.handoff": "Handoff",
  "context.history": "Compaction history ({0})",
  "context.compaction": "Compaction",
  "context.savings": "{0} → {1} tokens",
  "context.omitted.memory": "Memory (not injected)",
  "context.omittedCount": "{0} omitted",
  "context.reason.too_long": "too long",
  "context.reason.too_many": "over the count",
  "context.reason.budget_exhausted": "over the total",
  "context.artifacts": "{0} Artifact refs",
  "security.sandbox": "Sandbox",
  "security.generation": "Generation",
  "security.generationEnded": "{0} ended ({1})",
  "security.permission": "Permission",
  "security.selfTest": "Self-test",
  "security.network": "Network",
  "security.pending": "{0} pending approvals",
  "notes.empty": "No notes yet.",
  "notes.emptyNoProject": "Notes can be added under a project.",
  "palette.action.backHome": "Back to home",
  "palette.action.customize": "Customize",
  "palette.action.newProject": "New project",
  "palette.action.search": "Search",
  "palette.action.newSession": "New session",
  "palette.action.openNotebook": "Open Notebook",
  "palette.empty": "No matches",
  "palette.group.artifacts": "Artifacts",
  "palette.group.commands": "Commands",
  "palette.group.datapro": "Professional datasets",
  "palette.group.sessions": "Sessions",
  "palette.group.skills": "Skills",
  "palette.datapro.result": "DataPro query result",
  "palette.searchPlaceholder": "Search sessions, artifacts, professional datasets, skills, or run a command…",
  "perm.badge.subAgent": "Subagent",
  "perm.badge.dangerous": "High risk",
  "perm.btn.allow": "Allow",
  "perm.btn.continueReplan": "Continue and replan",
  "perm.btn.deny": "Deny",
  "perm.continuePrompt": "Continue. The operation I just approved was interrupted before the daemon restarted. Re-evaluate the current state first, issue a fresh action only if it is still needed, and do not assume the original operation executed.",
  "perm.lbl.rememberRule": "Remember rule (use * as wildcard)",
  "perm.lbl.rememberScope": "Remember scope",
  "perm.placeholder.denyReason": "(Optional) reason for denial, will be sent to the agent",
  "perm.scope.conversation": "This conversation",
  "perm.scope.global": "Global",
  "perm.scope.once": "Once",
  "perm.scope.project": "This project",
  "perm.status.allowed": "Allowed",
  "perm.status.allowedScope": "Allowed ({0})",
  "perm.status.afterRestartAllowed": "Approval recorded; the original operation did not execute after the daemon restart.",
  "perm.status.afterRestartDenied": "Denied; the original operation did not execute after the daemon restart.",
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
  "plan.eyebrow.paused": "PLAN PAUSED \u2014 STEPS REMAIN",
  "plan.status.paused": "Paused: {0}/{1} steps done, {2} remaining",
  "plan.resume": "Run the remaining steps",
  "plan.resumeFailed": "Could not resume: {0}",
  "plan.resuming": "Running the remaining steps\u2026",
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
  "proj.menu.downloadArtifacts": "Download artifacts",
  "proj.menu.settings": "Project settings",
  "proj.menu.newProject": "New project",
  "projectResearch.menu": "Project research map",
  "projectResearch.title": "{0} · Global research view",
  "projectResearch.timeline": "Timeline",
  "projectResearch.lineage": "Lineage",
  "projectResearch.timelineSummary": "{0} sessions · {1} actions",
  "projectResearch.lineageSummary": "{0} Artifacts · {1} versions · {2} edges",
  "projectResearch.latest": "latest",
  "projectResearch.noLineage": "No project lineage data yet.",
  "projectResearch.edges": "Lineage edges ({0})",
  "share.menu": "Share (read-only link)",
  "share.title": "Share this session",
  "share.scope": "This publishes: the conversation, Notebook code and output, artifact files, and the environment list. Anyone with the link can view it, relayed in the clear through your relay.",
  "share.create": "Create share link",
  "share.copy": "Copy",
  "share.copied": "Link copied",
  "share.update": "Update snapshot",
  "share.updated": "Snapshot updated",
  "share.revoke": "Revoke",
  "share.revokeConfirm": "Revoke this share link? It stops working immediately.",
  "share.revoked": "Revoked",
  "share.disabled": "Sharing is off.",
  "share.enable": "Enable sharing",
  "share.expiry": "Expires:",
  "share.expiry.never": "Never",
  "share.expiry.1d": "1 day",
  "share.expiry.7d": "7 days",
  "share.expiry.30d": "30 days",
  "share.expiresAt": "Expires",
  "share.neverExpires": "Never expires",
  "share.unconfigured": "Sharing is not configured (relay URL and token required — see docs/webshare.md).",
  "share.close": "Close",
  "sessionPackage.import": "Import session",
  "sessionPackage.export": "Export session package",
  "sessionPackage.imported": "Session imported safely; its Kernel remains Ended until explicit recovery",
  "sessionPackage.tooLarge": "Session package exceeds the 128 MiB client limit",
  "sessionPackage.verified": "Verified: {0} file(s) match the package's own manifest — importing",
  "sessionPackage.verifyFailed": "Verification failed, import refused: {0}",
  "projModal.create": "Create",
  "projModal.editTitle": "Project settings",
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
  "prov.env.recordedUnverified": "Environment recorded, but not confirmed to belong to this production run alone",
  "prov.env.remoteTitle": "Remote GPU compute (reproducible)",
  "prov.env.remoteHost": "Host",
  "prov.env.remoteEnv": "Env",
  "prov.env.remotePkgs": "Packages",
  "prov.env.remoteCode": "Code",
  "prov.env.remoteModel": "Model / weights",
  "prov.env.remoteRun": "Run (UTC)",
  "prov.env.thPackage": "Package",
  "prov.env.thVersion": "Version",
  "prov.exec.downloadNotebook": "Download notebooks (zip)",
  "prov.exec.downloadPython": "Python notebook only (.ipynb)",
  "prov.exec.downloadR": "R notebook only (.ipynb)",
  "prov.exec.downloadMarkdown": "Markdown record (.md)",
  "prov.exec.downloadMore": "Other export formats",
  "prov.exec.noRecords": "No execution records yet.",
  "prov.msg.loadFailed": "Failed to load conversation: {0}",
  "prov.msg.loading": "Loading conversation…",
  "prov.msg.noRecords": "No conversation records yet.",
  "prov.msg.roleAssistant": "Assistant",
  "prov.msg.roleSystem": "System",
  "prov.msg.roleUser": "User",
  "prov.review.noLineage": "No provenance information (execution log is empty or no file I/O was recorded).",
  "prov.review.producedBy": "produced by cell {0}",
  "prov.review.producedByIdentity": "produced by Cell {0}",
  "prov.review.producerFrame": "{0} frame · {1}",
  "prov.review.nonCellProducer": "produced by a non-Cell action",
  "prov.review.sameBytesCapture": "same bytes · capture observation",
  "prov.review.versionCapture": "version capture · capture observation",
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
  "session.loadMore": "Load more sessions",
  "session.loadMoreLimit": "List limit reached — search for older sessions",
  "session.menu.tip": "Session actions",
  "session.newFolder": "＋ Folder",
  "session.untitled": "Untitled session",
  "sessionMenu.duplicate": "Duplicate session",
  "sessionMenu.cancel": "Cancel",
  "sessionMenu.downloadArtifacts": "Download artifacts",
  "sessionMenu.exportMarkdown": "Export as Markdown",
  "sessionMenu.viewNotebook": "View notebook",
  "compute.menu.runLocation": "Run location",
  "compute.location.local": "This machine (daemon)",
  "compute.location.localHint": "The kernel runs on the host running the daemon.",
  "compute.dialog.title": "Where this session runs",
  "compute.dialog.notConfigured": "This daemon has no worker listener, so sessions cannot run on a cluster.",
  "compute.dialog.release": "Release the cluster resource",
  "compute.badge.local": "local",
  "compute.blocked.allocation": "queued for a resource",
  "compute.blocked.workspace": "preparing the workspace",
  "compute.blocked.worker": "waiting for the worker to dial in",
  "compute.blocked.kernel": "starting the kernel",
  "compute.badge.ready": "cluster ready",
  "compute.lost.title": "Kernel state was lost",
  "compute.lost.body": "This session's resource went away and the kernel's memory went with it — variables, imports and loaded data are gone. The session continued on a new attempt, but anything defined earlier has to be run again.",
  "compute.lost.dismiss": "Got it",
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
  "skill.readiness.needsSetup": "Not available on this machine: {0}",
  "skill.readiness.unknown": "Cannot be verified on this machine: {0}",
  "skill.saveBtn": "Save skill",
  "skill.historyBtn": "Version history",
  "skill.historyTitle": "Skill versions — {0}",
  "skill.historyEmpty": "This scope has no rollbackable versions yet.",
  "skill.scope.personal": "personal",
  "skill.scope.project": "project",
  "skill.scope.bundled": "bundled",
  "skill.versionActive": "Active version",
  "skill.rollbackBtn": "Roll back to this version",
  "skill.rollbackConfirm": "Roll {0} back to version {1}? The current version will be retained.",
  "skill.rollbackDone": "Rolled {0} back to the selected version",
  "skill.versionSidecar": "Sidecar: {0}",
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
  "toast.telemetry.on": "Anonymous statistics on",
  "toast.telemetry.off": "Anonymous statistics off; the identity was deleted too",
  "toast.telemetry.failed": "Could not save the statistics setting; restored the previous state: {0}",
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
  "turn.failedCommitted": "This turn failed after it had already produced output or run a tool — retrying would repeat work that already happened. Check the result before deciding.",
  "turn.failure.llmRequestBurst": "The model provider's burst-traffic protection was triggered. This is not an API-key configuration problem. Continue this session later or temporarily switch models.",
  "turn.failure.llmRateLimited": "The model provider is rate-limiting requests. Continue this session later or temporarily switch models.",
  "turn.failure.llmUpstreamOverloaded": "The model provider is currently overloaded. This is not an API-key configuration problem. Continue this session later or temporarily switch models.",
  "turn.supportId": "Support ID: {0}",
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
  "viewer.chem.fallback": "No safe 2D coordinates were found; the original chemical representation is preserved below.",
  "viewer.chem.source": "Original chemical representation",
  "viewer.downloadOnly": "This binary artifact has no safe built-in preview.",
  "viewer.empty": "Click a file in the conversation to view it.",
  "viewer.genome.features": "{0} features · {1} chromosomes/sequences",
  "viewer.genome.invalid": "Ignored {0} invalid records",
  "viewer.genome.list": "Feature descriptors",
  "viewer.latex.preview": "Safe preview",
  "viewer.latex.source": "LaTeX source",
  "viewer.loading": "Selecting a safe renderer…",
  "viewer.msa.summary": "{0} sequences · {1} columns · {2}",
  "viewer.renderer.compat": "Compatibility mode",
  "viewer.renderer.error": "This artifact could not be previewed. You can still download the original file.",
  "viewer.renderer.matched": "Matched by {0}",
  "viewer.renderer.version": "Version {0}",
  "viewer.sequence.omitted": "{0} additional residues are collapsed to keep the viewer responsive.",
  "viewer.sequence.summary": "{0} sequences · {1} residues · {2}",
  "viewer.table.shape": "{0} rows × {1} columns",
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
  else if (tab === "timeline") setActiveTab("timeline");
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
  const tt = tabBtn("button", "clock", t("dock.tab.timeline"));
  if (S.activeTab === "timeline") tt.classList.add("active");
  tt.onclick = () => setActiveTab("timeline"); bar.appendChild(tt);
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
  showDockPane(t === "notebook" ? "notebook" : (t === "timeline" ? "timeline" : (t === "files" ? "files" : "viewer")));
  if (t === "notebook") renderNotebook();
  else if (t === "timeline") { loadWorkbenchState(S.currentId); renderActionTimeline(); }
  else if (t === "files") { if (S.filesScope === "project") loadProjectArtifacts().then(renderFilesGrid); else renderFilesGrid(); }
  else renderViewer();
}
function showDockPane(pane) { ["viewer", "notebook", "timeline", "files"].forEach(p => { const n = $("#dock-" + p); if (n) n.classList.toggle("hidden", p !== pane); }); }
function ghostIconBtn(name, title) { const b = el("button", "icon-ghost"); b.innerHTML = icon(name, 16); if (title) b.title = title; return b; }

/* ---------- Action Timeline + session workbench projections ---------- */
// These helpers are intentionally allowlist-based. The durable ledger may carry
// provider wire ids, canonical arguments and complete results for audit/replay;
// none of those belong in the researcher-facing DOM (or browser state).
function publicText(value, limit = 180) {
  let out = String(value == null ? "" : value);
  out = out
    .replace(/\bBearer\s+[^\s,;]+/gi, "Bearer [redacted]")
    .replace(/\b(?:sk|ark|api[_-]?key|access[_-]?token|refresh[_-]?token)[-_][A-Za-z0-9._-]{8,}\b/gi, "[redacted]")
    .replace(/([?&](?:key|token|api_key)=)[^&#\s]+/gi, "$1[redacted]");
  return out.length > limit ? out.slice(0, Math.max(0, limit - 1)) + "…" : out;
}
function publicList(value, limit = 24, textLimit = 160) {
  return (Array.isArray(value) ? value : []).slice(0, limit).map(item => publicText(item, textLimit)).filter(Boolean);
}
function publicArtifacts(result) {
  const found = [];
  const add = (value) => { const text = publicText(value, 160); if (text && !found.includes(text) && found.length < 16) found.push(text); };
  const walk = (value, depth) => {
    if (depth > 2 || value == null) return;
    if (Array.isArray(value)) { value.slice(0, 16).forEach(item => walk(item, depth + 1)); return; }
    if (typeof value !== "object") return;
    ["filename", "artifact_id", "version_id"].forEach(key => { if (value[key] != null) add(value[key]); });
    ["artifact", "artifacts", "files", "files_written"].forEach(key => { if (value[key] != null) walk(value[key], depth + 1); });
  };
  walk(result, 0); return found;
}
const ACTION_TIMELINE_PAGE_SIZE = 500;
const ACTION_TIMELINE_ROW_HEIGHT = 46;
const ACTION_TIMELINE_OVERSCAN = 8;
const ACTION_TIMELINE_TOP_THRESHOLD = ACTION_TIMELINE_ROW_HEIGHT * 2;
const ACTION_TIMELINE_BOTTOM_THRESHOLD = 2;
const ACTION_TIMELINE_OVERVIEW_WIDTH = 1000;
const ACTION_TIMELINE_OVERVIEW_HEIGHT = 112;
const ACTION_TIMELINE_OVERVIEW_HOVER_DELAY = 500;
function timelineOrdinal(value) {
  return value !== null && value !== "" && Number.isFinite(Number(value)) ? Number(value) : null;
}
function sanitizeActionTimeline(payload) {
  const source = payload && (payload.timeline || payload.payload || payload);
  const usage = value => {
    const source = value && typeof value === "object" ? value : {};
    const number = key => Number.isSafeInteger(source[key]) && source[key] >= 0 ? source[key] : 0;
    return { input_tokens: number("input_tokens"), output_tokens: number("output_tokens"), total_tokens: number("total_tokens") };
  };
  const permission = value => value && typeof value === "object"
    ? publicText([value.state, value.scope].filter(Boolean).join(" · "), 80)
    : publicText(value, 80);
  const groups = ((source && source.groups) || []).slice(-ACTION_TIMELINE_PAGE_SIZE).map(group => ({
    group_id: publicText(group.group_id, 96), branch_id: publicText(group.branch_id, 96),
    turn_id: publicText(group.turn_id, 96), ordinal: timelineOrdinal(group.ordinal),
    kind: publicText(group.kind, 48), language: publicText(group.language, 24), provider: publicText(group.provider, 48), model: publicText(group.model, 96),
    title: publicText(group.title, 260), status: publicText(group.status, 32), owner: publicText(group.owner || group.owner_kind, 80),
    permission: permission(group.permission || group.permission_state), replay_policy: publicText(group.replay_policy, 48),
    usage: usage(group.usage), cost: Number.isFinite(+group.cost) && +group.cost >= 0 ? +group.cost : null, created_at: group.created_at,
    session: group.session && typeof group.session === "object" ? {
      root_frame_id: publicText(group.session.root_frame_id, 96), name: publicText(group.session.name, 160)
    } : null,
    // The server projection has already removed arguments, wire state and raw
    // results. Keep every projected event and its complete bounded public
    // resource/artifact lists so loaded-window search cannot miss a safe item
    // merely because it appeared late in the group.
    events: (group.events || []).map(event => ({
      event_id: publicText(event.event_id, 96), sequence: event.sequence, type: publicText(event.type, 64),
      action_id: publicText(event.action_id, 96), name: publicText(event.name, 120),
      side_effect_class: publicText(event.side_effect_class, 64), resource_keys: publicList(event.resource_keys, 64, 160),
      artifacts: publicList(event.artifacts, 32, 200).concat(publicArtifacts(event.result)).slice(0, 32),
      outcome: publicText(event.outcome, 32), is_error: !!event.is_error, created_at: event.created_at
    })),
    // Inspector state is about the latest execution. Retain the bounded tail
    // so a long retry history cannot strand the UI on attempt 50 forever.
    attempts: ((group.attempts || []).slice(-50)).map(attempt => ({
      attempt_id: publicText(attempt.attempt_id, 96), producing_cell_id: publicText(attempt.producing_cell_id, 96),
      attempt_ordinal: attempt.attempt_ordinal, generation_id: publicText(attempt.generation_id, 96),
      allocated_at: attempt.allocated_at, started_at: attempt.started_at, response_at: attempt.response_at,
      capture_at: attempt.capture_at, finished_at: attempt.finished_at,
      terminal_state: publicText(attempt.terminal_state, 48), error: publicText(attempt.error, 240),
      replayed_from_cell_id: publicText(attempt.replayed_from_cell_id, 96)
    }))
  })).filter(group => !!group.group_id);
  const firstOrdinal = timelineOrdinal(source && source.first_ordinal);
  const lastOrdinal = timelineOrdinal(source && source.last_ordinal);
  const hasMoreBefore = !!(source && (source.has_more_before || source.has_earlier));
  const hasMoreAfter = !!(source && (source.has_more_after || source.has_more));
  return {
    project_id: publicText(source && source.project_id, 120),
    root_frame_id: publicText(source && source.root_frame_id, 96),
    branch_id: publicText(source && source.branch_id, 96), groups,
    session_count: Number.isFinite(+(source && source.session_count)) ? Math.max(0, +(source && source.session_count)) : null,
    count: Number.isFinite(+(source && source.count)) ? +(source && source.count) : groups.length,
    total_count: Number.isFinite(+(source && source.total_count)) ? +(source && source.total_count) : groups.length,
    truncated: !!(source && source.truncated),
    has_more_before: hasMoreBefore, has_more_after: hasMoreAfter,
    has_earlier: hasMoreBefore, has_more: hasMoreAfter,
    first_ordinal: firstOrdinal != null ? firstOrdinal : (groups[0] && groups[0].ordinal),
    last_ordinal: lastOrdinal != null ? lastOrdinal : (groups[groups.length - 1] && groups[groups.length - 1].ordinal),
    running: !!(source && source.running)
  };
}
function mergeActionTimelines(current, incoming, direction = "latest") {
  if (!current) return incoming;
  if (!incoming) return current;
  if ((current.root_frame_id && incoming.root_frame_id && current.root_frame_id !== incoming.root_frame_id) ||
      (current.branch_id && incoming.branch_id && current.branch_id !== incoming.branch_id)) return incoming;
  const deduped = new Map();
  const ordered = direction === "before" ? (incoming.groups || []).concat(current.groups || []) :
    (current.groups || []).concat(incoming.groups || []);
  ordered.forEach(group => { if (group && group.group_id) deduped.set(group.group_id, group); });
  const groups = Array.from(deduped.values()).sort((a, b) => {
    const left = timelineOrdinal(a.ordinal), right = timelineOrdinal(b.ordinal);
    if (left != null && right != null && left !== right) return left - right;
    return (+a.created_at || 0) - (+b.created_at || 0);
  });
  const currentFirst = timelineOrdinal(current.first_ordinal), incomingFirst = timelineOrdinal(incoming.first_ordinal);
  const beforeSource = direction === "before" ? incoming :
    (currentFirst != null && (incomingFirst == null || currentFirst <= incomingFirst) ? current : incoming);
  const afterSource = direction === "before" ? current : incoming;
  const hasMoreBefore = !!beforeSource.has_more_before;
  const hasMoreAfter = !!afterSource.has_more_after;
  return {
    ...afterSource,
    root_frame_id: incoming.root_frame_id || current.root_frame_id,
    branch_id: incoming.branch_id || current.branch_id,
    groups, count: groups.length,
    total_count: Math.max(+current.total_count || 0, +incoming.total_count || 0, groups.length),
    truncated: hasMoreBefore || hasMoreAfter,
    has_more_before: hasMoreBefore, has_more_after: hasMoreAfter,
    has_earlier: hasMoreBefore, has_more: hasMoreAfter,
    first_ordinal: groups.length ? groups[0].ordinal : null,
    last_ordinal: groups.length ? groups[groups.length - 1].ordinal : null,
    running: direction === "before" ? !!current.running : !!incoming.running
  };
}
function queueMetadata(raw) {
  const m = raw || {};
  const rev = +m.model_profile_revision;
  return {
    preview: publicText(m.preview, 160), model_profile_id: publicText(m.model_profile_id, 96),
    model_profile_revision: Number.isFinite(rev) && rev > 0 ? rev : null
  };
}
function sanitizeExecutionQueue(payload) {
  const source = payload && (payload.execution || payload.payload || payload) || {};
  const ticket = item => item ? {
    execution_id: publicText(item.execution_id, 96), status: publicText(item.status, 32),
    owner: { kind: publicText((item.owner || {}).kind || item.owner_kind, 48), id: publicText((item.owner || {}).id || item.owner_id, 96) },
    branch_id: publicText(item.branch_id, 96), language: publicText(item.language, 24),
    generation_id: publicText(item.generation_id, 96), resource_keys: publicList(item.resource_keys),
    queue_position: Number.isFinite(+item.queue_position) ? +item.queue_position : null,
    queued_at: item.queued_at, started_at: item.started_at, cancel_requested: !!item.cancel_requested,
    // The ticket's own frozen description of the work. A queued item has no
    // frame row and no message row yet, so this is the only thing that can say
    // what the item is — and it is frozen at admission, so it keeps saying the
    // same thing while the frame's model pin is rewritten underneath it.
    metadata: queueMetadata(item.metadata)
  } : null;
  return {
    owner: ticket(source.owner), queue: (source.queue || []).slice(0, 100).map(ticket).filter(Boolean),
    queued_count: Number.isFinite(+source.queued_count) ? +source.queued_count : ((source.queue || []).length),
    active_count: Number.isFinite(+source.active_count) ? +source.active_count : (source.owner ? 1 : 0),
    closed: !!source.closed, close_reason: publicText(source.close_reason, 160)
  };
}
function rememberExecutionQueue(payload) {
  S.executionQueue = sanitizeExecutionQueue(payload);
  const ticket = S.executionQueue.owner;
  S.executionIdentity = ticket && ticket.execution_id && ticket.owner && ticket.owner.kind && ticket.owner.id ? {
    execution_id: ticket.execution_id, owner: { kind: ticket.owner.kind, id: ticket.owner.id }
  } : null;
  renderQueueStrip();
  return S.executionQueue;
}
// ---- queued follow-ups -----------------------------------------------------
// The composer stays usable while a turn runs, so the FIFO queue is now
// something the user can see rather than an internal detail. Every row is drawn
// from the server projection alone: position, preview and the frozen
// profile/branch all come off the ticket, so a repaint after any queue change
// cannot disagree with what the server will actually run.
function queueRowLabel(item) {
  const meta = item.metadata || {};
  const bits = [];
  if (meta.model_profile_id) bits.push(t("queue.underProfile", meta.model_profile_id, meta.model_profile_revision == null ? "?" : meta.model_profile_revision));
  if (item.branch_id) bits.push(t("queue.onBranch", item.branch_id));
  bits.push(item.execution_id);
  return bits.join(" · ");
}
function renderQueueStrip() {
  const box = $("#queue-strip"); if (!box) return;
  const queue = ((S.executionQueue || {}).queue || []).filter(item => (item.owner || {}).kind === "agent");
  box.innerHTML = "";
  box.classList.toggle("hidden", !queue.length);
  if (!queue.length) return;
  box.appendChild(el("div", "queue-head", t("queue.waiting", queue.length)));
  queue.forEach(item => {
    const row = el("div", "queue-row");
    row.appendChild(el("span", "queue-pos", "#" + (item.queue_position == null ? "?" : item.queue_position)));
    row.appendChild(el("span", "queue-preview", item.metadata.preview || t("queue.noPreview")));
    const meta = el("span", "queue-meta", queueRowLabel(item));
    meta.title = queueRowLabel(item);
    row.appendChild(meta);
    const drop = el("button", "icon-ghost queue-cancel");
    drop.title = t("queue.cancelOne");
    drop.appendChild(iconEl("x", 13));
    // One item, by its own id AND its own owner. The server refuses a
    // half-matching pair, which is what keeps this from ever reaching the
    // running turn or a sibling that happens to sit at the same position.
    drop.onclick = () => cancelQueuedExecution(item);
    row.appendChild(drop);
    box.appendChild(row);
  });
}
async function cancelQueuedExecution(item) {
  const fid = S.currentId;
  if (!fid || !item || !item.execution_id || !(item.owner || {}).id) return;
  try {
    const r = await api(`/frames/${fid}/cancel`, { method: "POST", body: JSON.stringify({
      execution_id: item.execution_id, owner: { kind: item.owner.kind, id: item.owner.id }, reason: "queued follow-up dropped by user"
    }) });
    if (!r || r.ok !== true) { hint(t("queue.cancelFailed", (r && r.reason) || ""), true); return; }
    // Mark the optimistic bubble this item was sent as, rather than removing
    // it: a message the user typed and can still see is easier to re-send than
    // one that vanished, and the transcript should not silently lose a turn.
    const bubble = [...document.querySelectorAll(".msg.user")].find(n => n.dataset.executionId === item.execution_id);
    if (bubble) bubble.classList.add("cancelled");
    hint(t("queue.cancelled"));
  } catch (e) { hint(t("queue.cancelFailed", apiErrorText(e)), true); }
}
function rememberExecutionState(event) {
  const status = String(event && event.status || "").toLowerCase();
  const identity = event && event.execution_id && event.owner && event.owner.kind && event.owner.id ? {
    execution_id: publicText(event.execution_id, 96),
    owner: { kind: publicText(event.owner.kind, 48), id: publicText(event.owner.id, 96) }
  } : null;
  if (identity && ["running", "finalizing"].includes(status)) S.executionIdentity = identity;
  else if (identity && status === "queued" && !S.executionIdentity) S.executionIdentity = identity;
  if (S.executionIdentity && event && event.execution_id === S.executionIdentity.execution_id && ["completed", "failed", "cancelled"].includes(status)) S.executionIdentity = null;
  const pending = S.pendingReplIdentity;
  if (pending && event && event.execution_id === pending.execution_id && ["completed", "failed", "cancelled"].includes(status)) {
    const frameId = pending.frame_id;
    S.pendingReplIdentity = null;
    invalidateKernelCache();
    if (S.currentId === frameId) {
      loadExecutionLog(frameId).catch(() => {});
      loadArtifacts(frameId);
      scheduleWorkbenchRefresh();
      if (S.dock.open && S.activeTab === "notebook") scheduleNotebookRender();
    }
  }
}
function identityForOwner(queue, ownerKind) {
  const safe = queue || sanitizeExecutionQueue({}), candidates = [safe.owner].concat(safe.queue || []).filter(Boolean);
  const ticket = ownerKind ? candidates.find(item => item.owner && item.owner.kind === ownerKind) : safe.owner;
  return ticket && ticket.execution_id && ticket.owner && ticket.owner.kind && ticket.owner.id ? { execution_id: ticket.execution_id, owner: ticket.owner } : null;
}
async function exactExecutionIdentity(frameId, ownerKind) {
  const pending = ownerKind === "user_repl" && frameId === S.currentId && S.pendingReplIdentity && S.pendingReplIdentity.frame_id === frameId ? S.pendingReplIdentity : null;
  if (pending && pending.owner.kind === ownerKind) return pending;
  if (frameId === S.currentId) {
    const cached = identityForOwner(S.executionQueue, ownerKind);
    if (cached) return cached;
    if (!ownerKind && S.executionIdentity) return S.executionIdentity;
  }
  const snapshot = await optionalApi([`/frames/${frameId}/execution-queue`, `/frames/${frameId}/execution`]);
  if (!snapshot) return null;
  const safe = sanitizeExecutionQueue(snapshot);
  if (frameId === S.currentId) rememberExecutionQueue(snapshot);
  return identityForOwner(safe, ownerKind);
}
async function scopedExecutionRequest(frameId, endpoint, reason, ownerKind) {
  const identity = await exactExecutionIdentity(frameId, ownerKind);
  if (!identity) { hint(t("nb.interrupt.noOwner"), true); return { ok: false, reason: "no_exact_owner" }; }
  return api(`/frames/${frameId}/${endpoint}`, {
    method: "POST", body: JSON.stringify({ execution_id: identity.execution_id, owner: identity.owner, owner_id: identity.owner.id, reason })
  });
}
function sanitizeRecovery(payload) {
  const source = payload && (payload.recovery || payload.payload || payload) || {};
  const generations = source.generations || {}, current = source.current || {};
  const candidateJournal = source.log || source.events || current.events;
  const journal = Array.isArray(candidateJournal) ? candidateJournal : (/recovery_log/.test(String(source.type || "")) ? [source] : []);
  return {
    status: publicText(source.status || source.state, 48), progress: Number.isFinite(+source.progress) ? Math.max(0, Math.min(1, +source.progress)) : null,
    state_revision: source.state_revision, branch_id: publicText(source.branch_id, 96),
    view_only: source.view_only === true, trust_state: publicText(source.trust_state, 32), explicit_recovery_required: source.explicit_recovery_required === true,
    python_generation_id: publicText(source.python_generation_id || (generations.python || {}).generation_id || generations.python, 96),
    r_generation_id: publicText(source.r_generation_id || (generations.r || {}).generation_id || generations.r, 96),
    message: publicText(source.message || source.reason || source.error || current.phase, 240),
    log: journal.slice(-50).map(item => ({
      status: publicText(item.status || item.state || item.type, 48),
      message: publicText(item.message || item.reason || item.error || [item.phase, item.status].filter(Boolean).join(": "), 240),
      at: item.at || item.created_at
    }))
  };
}
const RECOVERY_ACTION_IDS = ["restore", "retry", "restart_fresh"];
function sanitizeRecoveryActions(payload) {
  const source = payload && (payload.recovery || payload.payload || payload) || {};
  const advertised = new Map((Array.isArray(source.actions) ? source.actions : []).map(item => [String(item && item.id || ""), item || {}]));
  return {
    root_frame_id: publicText(source.root_frame_id, 96), branch_id: publicText(source.branch_id, 96),
    checkpoint_id: publicText(source.checkpoint_id, 96), state: publicText(source.state, 48),
    view_only: source.view_only === true, trust_state: publicText(source.trust_state, 32), explicit_recovery_required: source.explicit_recovery_required === true,
    actions: RECOVERY_ACTION_IDS.map(id => {
      const item = advertised.get(id);
      return {
        id, enabled: !!(item && item.enabled === true),
        reason: publicText(item ? item.reason : t("recovery.action.unavailable"), 240),
        requires_confirmation: !!(item && item.requires_confirmation === true),
        requires_ticket: !!(item && item.requires_ticket === true)
      };
    })
  };
}
function sanitizeBranches(payload) {
  const source = payload && (payload.branch || payload.payload || payload) || {};
  const capabilities = source.capabilities || source.actions || {};
  const capabilityEnabled = value => value === true || !!(value && typeof value === "object" && value.enabled === true);
  const capabilityReason = value => publicText(value && typeof value === "object" ? value.reason : "", 200);
  const checkpoints = items => (Array.isArray(items) ? items : []).slice(0, 100).map(item => {
    const cp = item && typeof item === "object" ? item : {}, metadata = cp.metadata && typeof cp.metadata === "object" ? cp.metadata : {};
    return {
      checkpoint_id: publicText(cp.checkpoint_id || cp.id, 96), parent_checkpoint_id: publicText(cp.parent_checkpoint_id, 96),
      reason: publicText(cp.reason, 80), created_at: cp.created_at, message_cursor: cp.message_cursor,
      action_cursor: cp.action_cursor, cell_cursor: cp.cell_cursor,
      internal: cp.internal === true || cp.internal === 1,
      source_kind: publicText(cp.source_kind, 24), source_id: publicText(cp.source_id, 96),
      requires_kernel_recovery: !!(metadata.requires_kernel_recovery || cp.requires_kernel_recovery),
      undo_revert_checkpoint_id: publicText(metadata.undo_checkpoint_id ? (cp.checkpoint_id || cp.id) : "", 96)
    };
  });
  return {
    root_frame_id: publicText(source.root_frame_id, 96),
    branch_id: publicText(source.branch_id || source.current_branch_id, 96),
    capabilities: {
      checkpoint: capabilityEnabled(capabilities.checkpoint), fork: capabilityEnabled(capabilities.fork),
      fork_from_cell: capabilityEnabled(capabilities.fork && capabilities.fork.fork_from_cell),
      fork_from_message: capabilityEnabled(capabilities.fork && capabilities.fork.fork_from_message),
      revert_preview: capabilityEnabled(capabilities.revert_preview || capabilities.preview_revert),
      revert: capabilityEnabled(capabilities.revert), activate: capabilityEnabled(capabilities.activate),
      promote: capabilityEnabled(capabilities.promote || capabilities.promote_artifact)
    },
    capability_reasons: {
      checkpoint: capabilityReason(capabilities.checkpoint), fork: capabilityReason(capabilities.fork),
      fork_from_cell: publicText((capabilities.fork || {}).fork_from_cell_reason, 200),
      fork_from_message: publicText((capabilities.fork || {}).fork_from_message_reason, 200),
      revert_preview: capabilityReason(capabilities.revert_preview || capabilities.preview_revert),
      revert: capabilityReason(capabilities.revert), activate: capabilityReason(capabilities.activate)
    },
    branches: (Array.isArray(source.branches) ? source.branches : []).slice(0, 100).map(item => {
      const branch = item && typeof item === "object" ? item : {};
      return {
        branch_id: publicText(branch.branch_id || branch.id, 96), name: publicText(branch.name, 120),
        head_checkpoint_id: publicText(branch.head_checkpoint_id, 96), created_at: branch.created_at,
        active: branch.active === true, view_only: branch.view_only === true, activatable: branch.activatable === true,
        checkpoints: checkpoints(branch.checkpoints)
      };
    }),
    revert_preview: sanitizeRevertPreview(source.revert_preview)
  };
}
function branchUndoFromProjection(state) {
  if (!state || !state.branch_id || !state.capabilities || state.capabilities.revert !== true) return null;
  const branch = (state.branches || []).find(item => item.branch_id === state.branch_id);
  const checkpoint = branch && (branch.checkpoints || []).find(item => item.checkpoint_id === branch.head_checkpoint_id);
  return checkpoint && checkpoint.undo_revert_checkpoint_id ? {
    branch_id: state.branch_id, revert_checkpoint_id: checkpoint.undo_revert_checkpoint_id
  } : null;
}
function sanitizeRevertPreview(source) {
  if (!source || typeof source !== "object") return null;
  const workspace = source.workspace || {};
  const count = value => Array.isArray(value) ? Math.min(value.length, 1000000) : 0;
  const delta = value => Number.isFinite(Number(value)) ? Math.max(-1000000, Math.min(1000000, Number(value))) : 0;
  const setDelta = value => ({ added_count: count((value || {}).added), removed_count: count((value || {}).removed) });
  return {
    branch_id: publicText(source.branch_id, 96), current_checkpoint_id: publicText(source.current_checkpoint_id, 96),
    target_checkpoint_id: publicText(source.target_checkpoint_id, 96), can_apply: !!source.can_apply,
    messages: { delta: delta((source.messages || {}).delta) },
    notebook: { delta: delta((source.notebook || {}).delta) },
    actions: { delta: delta((source.actions || {}).delta) },
    workspace: { writes_count: count(workspace.writes), deletes_count: count(workspace.deletes), conflicts_count: count(workspace.conflicts) },
    artifacts: setDelta(source.artifacts), environment: source.environment ? { changed: true } : null,
    permissions: source.permissions ? { changed: true } : null
  };
}
function sanitizeRevertMutationResult(source) {
  const checkpoint = source && source.checkpoint || {};
  return {
    ok: !!(source && source.ok === true), branch_id: publicText(checkpoint.branch_id, 96),
    revert_checkpoint_id: publicText(checkpoint.checkpoint_id, 96),
    requires_kernel_recovery: !!(source && source.requires_kernel_recovery === true)
  };
}
function sanitizeVariableInspection(payload, frameId, language) {
  const source = payload && typeof payload === "object" ? payload : {};
  const allowedStates = ["active", "busy", "ended", "not_started", "restoring", "unsupported", "failed"];
  const state = allowedStates.includes(String(source.state || "")) ? String(source.state) : "failed";
  const activeBranch = publicText(S.branchState && S.branchState.branch_id, 96) || frameId;
  const exactScope = publicText(source.root_frame_id, 96) === frameId && publicText(source.branch_id, 96) === activeBranch && source.language === language;
  const primitive = value => {
    if (value === null || typeof value === "boolean") return value;
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") return publicText(value, 240);
    return undefined;
  };
  const variables = (Array.isArray(source.variables) ? source.variables : []).slice(0, 500).map(raw => {
    const item = raw && typeof raw === "object" ? raw : {};
    const safe = { name: publicText(item.name, 160), type: publicText(item.type, 160) };
    const kind = publicText(item.kind, 32); if (kind) safe.kind = kind;
    if (Number.isSafeInteger(item.length) && item.length >= 0) safe.length = Math.min(item.length, 1000000000000);
    const preview = primitive(item.preview); if (preview !== undefined) safe.preview = preview;
    const fingerprint = publicText(item.fingerprint, 128).toLowerCase(); if (/^[a-f0-9]{8,128}$/.test(fingerprint)) safe.fingerprint = fingerprint;
    return safe;
  }).filter(item => item.name && item.type);
  const available = !!(source.available === true && exactScope && state === "active");
  return {
    available,
    root_frame_id: exactScope ? frameId : "", branch_id: exactScope ? activeBranch : "", language,
    state: exactScope ? state : "failed", generation_id: publicText(source.generation_id, 96),
    state_revision: Number.isSafeInteger(source.state_revision) && source.state_revision >= 0 ? source.state_revision : 0,
    variables: available ? variables : [], truncated: available && !!source.truncated, reason: publicText(source.reason, 200)
  };
}
function sanitizeContext(payload) {
  const source = payload && (payload.context || payload.payload || payload) || {};
  const layers = source.layers || source.segments || source.composition || [];
  const history = Array.isArray(source.compaction_history) ? source.compaction_history : [];
  return {
    token_count: Number.isFinite(+source.token_count) ? +source.token_count : null,
    token_limit: Number.isFinite(+source.token_limit) ? +source.token_limit : null,
    output_reserve: Number.isFinite(+source.output_reserve) ? +source.output_reserve : null,
    message_count: Number.isFinite(+source.message_count) ? +source.message_count : null,
    compaction_count: Number.isFinite(+source.compaction_count) ? Math.max(0, +source.compaction_count) : history.length,
    handoff: !!(source.handoff || source.handoff_id), compressed: !!(source.compressed || source.compaction_count),
    layers: (Array.isArray(layers) ? layers : []).slice(0, 100).map(layer => ({
      name: publicText(layer.name || layer.kind || layer.type, 120), kind: publicText(layer.kind || layer.type, 64),
      token_count: Number.isFinite(+layer.token_count) ? +layer.token_count : null,
      status: publicText(layer.status, 48), compressed: !!layer.compressed
    })),
    // What this turn's budgets left out. Dropping it here would have made the
    // server-side omission report unreachable — the panel would keep reading
    // as a complete account of the context while quietly being a partial one.
    omitted: (Array.isArray(source.omitted) ? source.omitted : []).slice(0, 20).map(item => ({
      kind: publicText(item && item.kind, 48),
      count: Math.max(0, Number(item && item.count) || 0),
      reasons: (Array.isArray(item && item.reasons) ? item.reasons : []).slice(0, 8).map(r => ({
        reason: publicText(r && r.reason, 48), count: Math.max(0, Number(r && r.count) || 0)
      }))
    })),
    compaction_history: history.slice(0, 50).map(item => ({
      archive_id: publicText(item && item.archive_id, 120), branch_id: publicText(item && item.branch_id, 120),
      generation_id: publicText(item && item.generation_id, 120), created_at: Number(item && item.created_at) || 0,
      message_count: Number.isFinite(+(item && item.message_count)) ? Math.max(0, +(item && item.message_count)) : 0,
      tokens_before: Number.isFinite(+(item && item.tokens_before)) ? Math.max(0, +(item && item.tokens_before)) : 0,
      tokens_after: Number.isFinite(+(item && item.tokens_after)) ? Math.max(0, +(item && item.tokens_after)) : 0,
      artifact_count: Array.isArray(item && item.artifact_refs) ? Math.min(item.artifact_refs.length, 100) : 0
    }))
  };
}
function sanitizeSecurity(payload) {
  const source = payload && (payload.security || payload.payload || payload) || {};
  const sandbox = source.sandbox || source.kernel_sandbox || (/sandbox/.test(String(source.type || "")) ? source : {});
  const permission = source.permission || source.permissions || {};
  return {
    sandbox: {
      state: publicText(sandbox.state || sandbox.status, 48), mode: publicText(sandbox.mode, 32),
      backend: publicText(sandbox.backend, 64), enforced: !!sandbox.enforced,
      self_test_passed: sandbox.self_test_passed === true, network_policy: publicText(sandbox.network_policy, 64),
      detail: publicText(sandbox.detail || sandbox.warning, 500), generation_ended: !!sandbox.generation_ended,
      runtimes: (sandbox.runtimes || []).slice(0, 2).map(runtime => ({
        language: publicText(runtime.language, 16), source: publicText(runtime.source, 32),
        generation_state: publicText(runtime.generation_state, 48), generation_ended: !!runtime.generation_ended,
        generation_ended_reason: publicText(runtime.generation_ended_reason, 80)
      }))
    },
    permission: {
      mode: publicText(permission.mode || permission.policy, 48),
      pending_count: Number.isFinite(+permission.pending_count) ? +permission.pending_count : 0,
      unattended: publicText(permission.unattended, 48)
    }
  };
}
function sanitizeDelegations(payload) {
  const source = payload && (payload.delegation || payload.payload || payload) || {};
  const count = value => Number.isSafeInteger(+value) && +value >= 0 ? Math.min(+value, 1000000) : 0;
  const budgetSource = source.budget && typeof source.budget === "object" ? source.budget : null;
  const budget = budgetSource ? {
    limit: count(budgetSource.limit), spawned: count(budgetSource.spawned),
    active: count(budgetSource.active), remaining: count(budgetSource.remaining),
  } : null;
  const children = (Array.isArray(source.children) ? source.children : []).slice(0, 1000).map(raw => {
    const item = raw && typeof raw === "object" ? raw : {};
    const progress = item.progress && typeof item.progress === "object" ? item.progress : {};
    const steering = item.steering && typeof item.steering === "object" ? item.steering : {};
    const overrides = item.overrides && typeof item.overrides === "object" ? item.overrides : {};
    return {
      child_id: publicText(item.child_id, 96), parent_child_id: publicText(item.parent_child_id, 96),
      frame_id: publicText(item.frame_id, 96), name: publicText(item.name, 160),
      status: publicText(item.status, 32), depth: Math.min(count(item.depth), 16),
      stop_reason: publicText(item.stop_reason, 160), error: publicText(item.error, 240),
      created_at: item.created_at, started_at: item.started_at, finished_at: item.finished_at,
      progress: { turn_boundary: count(progress.turn_boundary), max_turns: count(progress.max_turns) || null },
      steering: { queued: count(steering.queued), delivered: count(steering.delivered), discarded: count(steering.discarded) },
      overrides: {
        model: publicText(overrides.model, 120), steps: count(overrides.steps) || null,
        permission_count: Array.isArray(overrides.permissions) ? Math.min(overrides.permissions.length, 100) : 0,
        capability_count: Array.isArray(overrides.capabilities) ? Math.min(overrides.capabilities.length, 100) : 0,
      },
    };
  }).filter(item => item.child_id);
  return {
    root_frame_id: publicText(source.root_frame_id, 96), initialized: source.initialized === true,
    budget, stats: source.stats && typeof source.stats === "object" ? {
      total: count(source.stats.total), pending: count(source.stats.pending), running: count(source.stats.running),
      done: count(source.stats.done), failed: count(source.stats.failed), stopped: count(source.stats.stopped),
    } : { total: children.length, pending: 0, running: 0, done: 0, failed: 0, stopped: 0 },
    children,
  };
}
async function optionalApi(paths) {
  for (const path of paths) { try { return await api(path); } catch {} }
  return null;
}
function actionTimelineBranchScope(timeline = S.actionTimeline, groups = null) {
  const items = groups || ((timeline && timeline.groups) || []);
  return publicText((timeline && timeline.branch_id) || (items[0] && items[0].branch_id) || S.currentId, 96);
}
function actionTimelineRootScope(timeline = S.actionTimeline) {
  return publicText((timeline && timeline.root_frame_id) || S.currentId, 96);
}
function actionTimelineHistoryIsLoading(timeline = S.actionTimeline) {
  const loading = S._timelineHistoryLoading;
  return !!loading && loading.frameId === S.currentId && loading.branchId === actionTimelineBranchScope(timeline);
}
async function loadEarlierActionTimeline() {
  const id = S.currentId, timeline = S.actionTimeline;
  const branchId = actionTimelineBranchScope(timeline);
  if (!id || !timeline || !branchId || !timeline.has_more_before || S._timelineHistoryLoading) return;
  const first = timelineOrdinal(timeline.first_ordinal);
  if (first == null || first < 0) return;
  const request = S._timelineHistoryReq = (S._timelineHistoryReq || 0) + 1;
  const loading = { frameId: id, branchId, firstOrdinal: first };
  S._timelineHistoryLoading = loading;
  delete S.workbenchErrors.timelineHistory;
  if (S.activeTab === "timeline") syncActionTimelineHistoryState();
  try {
    const page = await api(`/frames/${encodeURIComponent(id)}/action-timeline?before_ordinal=${first}&limit=${ACTION_TIMELINE_PAGE_SIZE}&branch_id=${encodeURIComponent(branchId)}`);
    const current = S.actionTimeline, incoming = sanitizeActionTimeline(page);
    if (request !== S._timelineHistoryReq || id !== S.currentId ||
        actionTimelineBranchScope(current) !== branchId || (incoming.branch_id && incoming.branch_id !== branchId)) return;
    // Take the anchor immediately before the synchronous merge. A WS append
    // that arrived while this request was in flight is therefore already part
    // of oldScrollHeight and cannot be mistaken for prepended history.
    const view = S._timelineView;
    const matchingView = view && view.scroll && view.rootFrameId === actionTimelineRootScope(current) && view.branchId === branchId;
    const prependSnapshot = S.activeTab === "timeline" && matchingView
      ? { node: view.scroll, scrollHeight: view.scroll.scrollHeight, scrollTop: view.scroll.scrollTop,
        followTail: actionTimelineBottomDistance(view) <= ACTION_TIMELINE_BOTTOM_THRESHOLD }
      : null;
    const pendingPrependRestore = S.activeTab !== "timeline" && matchingView ? actionTimelineFilterScrollSnapshot(view) : null;
    S.actionTimeline = mergeActionTimelines(current, incoming, "before");
    if (S.activeTab === "timeline") updateActionTimelineLedger({ direction: "before", prependSnapshot });
    else if (pendingPrependRestore) view.pendingPrependRestore = pendingPrependRestore;
  } catch (error) {
    if (request === S._timelineHistoryReq && id === S.currentId && actionTimelineBranchScope() === branchId) {
      S.workbenchErrors.timelineHistory = publicText(error && error.message, 240);
    }
  } finally {
    if (request === S._timelineHistoryReq && id === S.currentId && S._timelineHistoryLoading === loading) {
      S._timelineHistoryLoading = null;
      if (S.activeTab === "timeline") syncActionTimelineHistoryState();
    }
  }
}
async function loadWorkbenchState(id, force = false) {
  if (!id || id !== S.currentId) return;
  if (!force && S._workbenchLoading === id) return;
  const request = S._workbenchReq = (S._workbenchReq || 0) + 1;
  S._workbenchLoading = id;
  const base = `/frames/${id}`;
  const [timeline, execution, branches, context, security, delegation, recovery, recoveryActions, computeTasks] = await Promise.all([
    optionalApi([base + `/action-timeline?limit=${ACTION_TIMELINE_PAGE_SIZE}`]),
    optionalApi([base + "/execution-queue", base + "/execution"]),
    optionalApi([base + "/branches"]), optionalApi([base + "/context"]), optionalApi([base + "/security"]), optionalApi([base + "/delegations"]),
    optionalApi([base + "/recovery"]), optionalApi([base + "/recovery/actions"]),
    // Reading the durable record, not asking a provider. The server route has
    // no path to a ComputeManager, so opening the workbench cannot contact a
    // remote — which matters because in this system contacting the remote is
    // what harvests files and closes the job. The harvest stays behind the
    // per-task button below.
    optionalApi([base + "/compute/tasks"])
  ]);
  if (request !== S._workbenchReq || id !== S.currentId) return;
  S._workbenchLoading = null;
  if (timeline) S.actionTimeline = mergeActionTimelines(S.actionTimeline, sanitizeActionTimeline(timeline), "latest");
  if (execution) rememberExecutionQueue(execution);
  if (branches) { S.branchState = sanitizeBranches(branches); S.branchUndo = branchUndoFromProjection(S.branchState); }
  if (context) S.contextState = sanitizeContext(context);
  if (security) S.securityState = sanitizeSecurity(security);
  if (delegation) S.delegationState = sanitizeDelegations(delegation);
  if (recovery) S.recoveryState = sanitizeRecovery(recovery);
  if (recoveryActions) S.recoveryActions = sanitizeRecoveryActions(recoveryActions);
  if (computeTasks) S.computeTasks = sanitizeComputeTasks(computeTasks);
  if (S.activeTab === "timeline") renderActionTimeline();
  if (S.activeTab === "notebook") renderNotebook();
}
function scheduleWorkbenchRefresh(delay = 180) {
  clearTimeout(S._workbenchTimer);
  S._workbenchTimer = setTimeout(() => loadWorkbenchState(S.currentId, true), delay);
}
function scheduleConversationResync(fid, delay = 120) {
  clearTimeout(S._branchConversationTimer);
  S._branchConversationTimer = setTimeout(() => { if (S.currentId === fid) openConversation(fid, S.project); }, delay);
}
function scheduleBranchConversationResync(fid, delay = 120) { scheduleConversationResync(fid, delay); }
function latestCellForLanguage(language) {
  return (S.cells || []).concat(S.liveCells || []).filter(cell => String(cell.language || cell.kernel_id || "python").toLowerCase().startsWith(language)).slice(-1)[0] || null;
}
function runtimeSummary() {
  const queue = S.executionQueue || {};
  const ownerTicket = queue.owner || null;
  const owner = ownerTicket && ownerTicket.owner || {};
  const recovery = S.recoveryState || {};
  const recoveryStatus = String(recovery.status || "").toLowerCase();
  const trustState = publicText(recovery.trust_state || (S.recoveryActions || {}).trust_state || (_kc.st || {}).trust_state, 32);
  const explicitRecoveryRequired = recovery.explicit_recovery_required === true || (S.recoveryActions || {}).explicit_recovery_required === true || (_kc.st || {}).explicit_recovery_required === true;
  const viewOnly = explicitRecoveryRequired || recovery.view_only === true || (S.recoveryActions || {}).view_only === true || (_kc.st || {}).view_only === true;
  let status = "ended";
  if (/fail|error/.test(recoveryStatus)) status = "failed";
  else if (/partial/.test(recoveryStatus)) status = "partial";
  else if (/restor|recover|bootstrap|validat/.test(recoveryStatus)) status = "restoring";
  else if (ownerTicket || S.running || (_kc.st && _kc.st.turn_running)) status = "busy";
  else if (_kc.st && _kc.st.alive) status = "live";
  const pythonCell = latestCellForLanguage("python"), rCell = latestCellForLanguage("r");
  const branch = (S.branchState && S.branchState.branch_id) || (S.actionTimeline && S.actionTimeline.branch_id) || (recovery && recovery.branch_id) || S.currentId;
  const stateRevision = recovery.state_revision != null ? recovery.state_revision : Math.max(0, ...((S.cells || []).concat(S.liveCells || []).map(cell => Number(cell.state_revision) || 0)));
  const pyGeneration = recovery.python_generation_id || (_kc.st && (_kc.st.python_generation_id || _kc.st.generation_id)) || (pythonCell && pythonCell.generation_id);
  const rGeneration = recovery.r_generation_id || (rCell && rCell.generation_id);
  return {
    status, branch: publicText(branch, 96), python: publicText(pyGeneration, 96), r: publicText(rGeneration, 96),
    viewOnly, trustState,
    revision: stateRevision || null, owner: publicText(owner.kind || (ownerTicket && ownerTicket.owner_kind), 48),
    ownerId: publicText(owner.id || (ownerTicket && ownerTicket.owner_id), 96),
    queue: Number(queue.queued_count || (queue.queue || []).length || 0)
  };
}
function shortRuntime(value) { const text = publicText(value, 96); return text ? (text.length > 12 ? text.slice(0, 8) + "…" : text) : t("runtime.none"); }
function runtimeSummaryNode(compact = false) {
  const runtime = runtimeSummary();
  const root = el("div", "runtime-summary" + (compact ? " compact" : ""));
  const state = el("span", "runtime-state " + runtime.status, t("runtime.status." + runtime.status)); root.appendChild(state);
  const item = (key, value, title) => { const chip = el("span", "runtime-chip"); chip.appendChild(el("span", "runtime-key", t(key))); const val = el("span", "runtime-val", value); if (title) val.title = publicText(title, 160); chip.appendChild(val); root.appendChild(chip); };
  item("runtime.branch", shortRuntime(runtime.branch), runtime.branch);
  item("runtime.python", shortRuntime(runtime.python), runtime.python);
  item("runtime.r", shortRuntime(runtime.r), runtime.r);
  item("runtime.revision", runtime.revision == null ? t("runtime.none") : "S" + runtime.revision);
  item("runtime.owner", runtime.owner ? runtime.owner + (runtime.ownerId ? " · " + shortRuntime(runtime.ownerId) : "") : t("runtime.none"), runtime.ownerId);
  item("runtime.queue", String(runtime.queue));
  if (runtime.viewOnly && runtime.trustState === "quarantined") item("runtime.trust", t("runtime.trust.quarantined"));
  return root;
}
function latestActionTimelineAttempt(group) {
  return ((group && group.attempts) || []).slice(-1)[0] || null;
}
function timelineKind(group) {
  const kind = String(group && group.kind || "").toLowerCase();
  const eventKinds = (group.events || []).map(event => String(event.type || "").toLowerCase()).join(" ");
  const latestAttempt = latestActionTimelineAttempt(group), linkedCell = latestAttempt && nbFindCell(latestAttempt.producing_cell_id);
  const language = String(group.language || (linkedCell && linkedCell.language) || "").toLowerCase();
  if (/final/.test(kind + " " + eventKinds)) return "finalize";
  if (/permission|approval/.test(kind + " " + eventKinds)) return "permission";
  if (/recover|restore|bootstrap/.test(kind + " " + eventKinds)) return "recovery";
  if (/delegat|subagent/.test(kind + " " + eventKinds)) return "delegate";
  if (/background|remote|compute|job/.test(kind + " " + eventKinds)) return "background";
  if (/dynamic/.test(kind + " " + eventKinds)) return "dynamic_tool";
  if (language === "r" || /\br\b|r_cell|rcode/.test(kind)) return "r";
  if (/code|python|cell/.test(kind)) return "python";
  if (/tool/.test(kind + " " + eventKinds)) return "native_tool";
  return "action";
}
function timelineDurationMs(attempt) {
  if (!attempt) return "";
  // ActionTimelineService exposes INTEGER epoch milliseconds, and a small
  // numeric timestamp is never reinterpreted as seconds. Parsing goes through
  // timelineEpochMs so the ledger, the overview and the turn stats agree about
  // which timestamps exist — a field one of them can read and another cannot
  // produced a duration with no bar and a completed turn labelled "≥".
  const parse = timelineEpochMs;
  const start = parse(attempt.started_at != null ? attempt.started_at : attempt.allocated_at);
  const end = parse(attempt.finished_at != null ? attempt.finished_at : (attempt.capture_at != null ? attempt.capture_at : attempt.response_at));
  if (start == null || end == null || end < start) return "";
  return end - start;
}
function timelineDurationValue(ms) {
  return ms === "" || !Number.isFinite(+ms) || +ms < 0 ? "" : (+ms < 1000 ? Math.round(+ms) + " ms" : (+ms / 1000).toFixed(+ms < 10000 ? 1 : 0) + " s");
}
function timelineDuration(attempt) {
  return timelineDurationValue(timelineDurationMs(attempt));
}
function timelineCost(value) {
  if (value == null || !Number.isFinite(+value) || +value < 0) return "";
  const amount = +value;
  return "$" + (amount < 0.01 ? amount.toFixed(6) : amount.toFixed(4));
}
function timelineMeta(label, value) {
  if (value == null || value === "" || (Array.isArray(value) && !value.length)) return null;
  const row = el("div", "timeline-meta"); row.appendChild(el("span", "timeline-meta-key", label));
  const values = Array.isArray(value) ? value : [value]; const body = el("span", "timeline-meta-value");
  values.slice(0, 24).forEach(item => body.appendChild(el("span", "timeline-pill", publicText(item, 160)))); row.appendChild(body); return row;
}
function timelineKindIcon(kind) {
  if (kind === "delegate") return "users";
  if (kind === "permission") return "lock";
  if (kind === "recovery") return "refresh";
  if (kind === "finalize") return "check";
  if (kind === "native_tool" || kind === "dynamic_tool") return "sliders";
  return "terminal";
}
function actionTimelineDetails(group) {
  const latest = latestActionTimelineAttempt(group);
  const resources = [], artifacts = [];
  (group.events || []).forEach(event => {
    (event.resource_keys || []).forEach(value => { if (!resources.includes(value)) resources.push(value); });
    (event.artifacts || []).forEach(value => { if (!artifacts.includes(value)) artifacts.push(value); });
  });
  return {
    latest, resources, artifacts, owner: group.owner || "",
    permission: group.permission || (group.events || []).map(event => event.side_effect_class).filter(Boolean),
    replay: group.replay_policy || (latest && latest.replayed_from_cell_id ? "replayed" : "original"),
    duration: timelineDuration(latest),
    tokens: t("timeline.tokensValue", (group.usage || {}).input_tokens || 0, (group.usage || {}).output_tokens || 0),
    cost: timelineCost(group.cost)
  };
}
function appendActionTimelineDetails(container, group) {
  const details = actionTimelineDetails(group);
  [
    timelineMeta(t("timeline.owner"), details.owner),
    timelineMeta(t("timeline.permission"), details.permission),
    timelineMeta(t("timeline.resources"), details.resources),
    timelineMeta(t("timeline.artifacts"), details.artifacts),
    timelineMeta(t("timeline.generation"), details.latest && details.latest.generation_id),
    timelineMeta(t("timeline.replay"), details.replay),
    timelineMeta(t("timeline.duration"), details.duration),
    timelineMeta(t("timeline.tokens"), details.tokens),
    timelineMeta(t("timeline.cost"), details.cost)
  ].filter(Boolean).forEach(node => container.appendChild(node));
  if (details.latest && details.latest.error) container.appendChild(el("div", "timeline-error", details.latest.error));
  return details;
}
function actionTimelineCard(group) {
  const kind = timelineKind(group), status = String(group.status || "completed").toLowerCase();
  const card = el("article", "timeline-card kind-" + kind + " status-" + status); card.setAttribute("data-action-kind", kind);
  const head = el("div", "timeline-card-head");
  const kindLabel = el("span", "timeline-kind"); kindLabel.appendChild(iconEl(timelineKindIcon(kind), 14)); kindLabel.appendChild(el("span", null, t("timeline.kind." + kind))); head.appendChild(kindLabel);
  head.appendChild(el("span", "timeline-status " + status, publicText(status || "completed", 32))); card.appendChild(head);
  card.appendChild(el("div", "timeline-card-title", group.title || t("timeline.kind." + kind)));
  appendActionTimelineDetails(card, group);
  return card;
}
function actionTimelineInspector(group) {
  const kind = timelineKind(group), status = String(group.status || "completed").toLowerCase();
  const panel = el("section", "timeline-inspector kind-" + kind + " status-" + status);
  panel.id = "timeline-action-inspector"; panel.dataset.groupId = group.group_id; panel.setAttribute("role", "region"); panel.setAttribute("aria-labelledby", "timeline-inspector-label");
  const inspectorHead = el("div", "timeline-inspector-head");
  const inspectorLabel = el("div", "timeline-inspector-label", t("timeline.inspector")); inspectorLabel.id = "timeline-inspector-label"; inspectorHead.appendChild(inspectorLabel);
  const close = ghostIconBtn("x", t("timeline.inspector.close")); close.setAttribute("aria-label", t("timeline.inspector.close"));
  close.onclick = event => { event.stopPropagation(); S._timelineRestoreFocusGroupId = group.group_id; S.actionTimelineSelectedGroupId = null; S.actionTimelineSelectedBranchId = null; updateActionTimelineLedger(); };
  inspectorHead.appendChild(close); panel.appendChild(inspectorHead);
  const cardHead = el("div", "timeline-card-head");
  const kindLabel = el("span", "timeline-kind"); kindLabel.appendChild(iconEl(timelineKindIcon(kind), 14)); kindLabel.appendChild(el("span", null, t("timeline.kind." + kind))); cardHead.appendChild(kindLabel);
  cardHead.appendChild(el("span", "timeline-status " + status, publicText(status || "completed", 32))); panel.appendChild(cardHead);
  panel.appendChild(el("div", "timeline-card-title", group.title || t("timeline.kind." + kind)));
  appendActionTimelineDetails(panel, group); appendActionTimelineTimingDetails(panel, group); panel._timelineGroup = group; panel._timelineLanguage = LANG;
  return panel;
}
function timelineTokenTotal(usage) {
  const source = usage || {}, input = +source.input_tokens || 0, output = +source.output_tokens || 0, total = +source.total_tokens || 0;
  return total > 0 ? total : input + output;
}
// Every consumer of an attempt timestamp must agree on what is parseable, or a
// projection renders a duration the chart cannot plot and the turn stats read
// as still running. This is the single parser: it accepts what
// timelineDurationMs accepts, including the ISO string fallback kept for
// imported legacy projections, and refuses anything Date cannot represent
// (|t| > 8.64e15) so toISOString can never throw out of a render.
const TIMELINE_MAX_EPOCH_MS = 8.64e15;
function timelineEpochMs(value) {
  if (value == null || value === "") return null;
  const number = Number(value);
  const ms = Number.isFinite(number) ? Math.round(number) : Date.parse(value);
  return Number.isFinite(ms) && ms >= 0 && ms <= TIMELINE_MAX_EPOCH_MS ? ms : null;
}
function actionTimelineSpan(group, rank, laneCount) {
  const attempt = latestActionTimelineAttempt(group);
  if (!attempt || !group || !group.group_id) return null;
  const times = {
    allocated: timelineEpochMs(attempt.allocated_at),
    started: timelineEpochMs(attempt.started_at),
    response: timelineEpochMs(attempt.response_at),
    capture: timelineEpochMs(attempt.capture_at),
    finished: timelineEpochMs(attempt.finished_at)
  };
  if (times.allocated == null) return null;
  const segments = [];
  const addSegment = (phase, start, end) => {
    if (start != null && end != null && end >= start) segments.push({ phase, start, end });
  };
  const running = times.finished == null;
  if (!running && times.finished >= times.allocated) {
    addSegment("queue", times.allocated, times.started);
    addSegment("ttft", times.started, times.response);
    addSegment("decode", times.response, times.finished);
  }
  if (!running && times.finished < times.allocated) return null;
  // An unfinished attempt is drawn only as its allocated-at marker, but its
  // filter interval may extend through milestones we actually observed.  This
  // never guesses a finish time or extends the record to "now".
  const latestKnown = [times.allocated, times.started, times.response, times.capture]
    .filter(value => value != null && value >= times.allocated)
    .reduce((latest, value) => Math.max(latest, value), times.allocated);
  return {
    groupId: group.group_id, group, attempt, rank, laneCount, times, segments,
    start: times.allocated, end: running ? latestKnown : times.finished,
    markerAt: running ? times.allocated : null,
    pointAt: !running && !segments.some(segment => segment.end > segment.start) ? times.allocated : null,
    running
  };
}
function actionTimelineOverviewModel(groups, domainGroups = groups) {
  const drawableItems = groups.map(group => actionTimelineSpan(group, 0, 1)).filter(Boolean);
  const items = [], byId = new Map(), laneCount = Math.max(1, drawableItems.length);
  let dataStart = null, dataEnd = null;
  drawableItems.forEach((item, rank) => {
    item.rank = rank; item.laneCount = laneCount;
    items.push(item); byId.set(item.groupId, item);
  });
  // Searching changes which bars are painted, not the honest time domain of
  // the loaded window.  Keeping the full loaded domain also leaves the
  // omitted-prefix control anchored to the real earliest loaded timestamp.
  domainGroups.forEach(group => {
    const attempt = latestActionTimelineAttempt(group); if (!attempt) return;
    [attempt.allocated_at, attempt.started_at, attempt.response_at, attempt.capture_at, attempt.finished_at].forEach(raw => {
      const value = timelineEpochMs(raw);
      if (value == null) return;
      dataStart = dataStart == null ? value : Math.min(dataStart, value);
      dataEnd = dataEnd == null ? value : Math.max(dataEnd, value);
    });
  });
  return { items, byId, laneCount, dataStart, dataEnd };
}
function timelineOverviewTimeToX(overview, value) {
  const start = overview.viewStart, end = overview.viewEnd;
  if (start == null || end == null || value == null) return null;
  if (end === start) return ACTION_TIMELINE_OVERVIEW_WIDTH / 2;
  return (value - start) / (end - start) * ACTION_TIMELINE_OVERVIEW_WIDTH;
}
function timelineOverviewPathRect(x1, x2, y1, y2) {
  if (![x1, x2, y1, y2].every(Number.isFinite) || x2 <= x1 || y2 <= y1) return "";
  const n = value => Number(value.toFixed(3));
  return `M${n(x1)},${n(y1)}H${n(x2)}V${n(y2)}H${n(x1)}Z`;
}
function timelineOverviewItemPaths(overview, item) {
  const laneHeight = ACTION_TIMELINE_OVERVIEW_HEIGHT / item.laneCount;
  const padding = Math.min(.22, laneHeight * .12), y1 = item.rank * laneHeight + padding, y2 = (item.rank + 1) * laneHeight - padding;
  const paths = { queue: "", ttft: "", decode: "", marker: "", point: "", highlight: "" };
  item.segments.forEach(segment => {
    const rawX1 = timelineOverviewTimeToX(overview, segment.start), rawX2 = timelineOverviewTimeToX(overview, segment.end);
    if (rawX1 == null || rawX2 == null || rawX2 < 0 || rawX1 > ACTION_TIMELINE_OVERVIEW_WIDTH) return;
    const rect = timelineOverviewPathRect(Math.max(0, rawX1), Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, rawX2), y1, y2);
    paths[segment.phase] += rect; paths.highlight += rect;
  });
  const markerAt = item.markerAt != null ? item.markerAt : item.pointAt;
  if (markerAt != null) {
    const x = timelineOverviewTimeToX(overview, markerAt), center = (y1 + y2) / 2;
    if (x != null && x >= 0 && x <= ACTION_TIMELINE_OVERVIEW_WIDTH) {
      const n = value => Number(value.toFixed(3));
      const marker = `M${n(x)},${n(Math.max(0, center - .9))}V${n(Math.min(ACTION_TIMELINE_OVERVIEW_HEIGHT, center + .9))}`;
      paths[item.running ? "marker" : "point"] += marker; paths.highlight += marker;
    }
  }
  return paths;
}
function actionTimelineOverviewVisualExtent(item) {
  if (!item) return null;
  const values = [];
  item.segments.forEach(segment => { values.push(segment.start, segment.end); });
  if (item.markerAt != null) values.push(item.markerAt);
  if (item.pointAt != null) values.push(item.pointAt);
  const finite = values.filter(Number.isFinite);
  return finite.length ? { start: Math.min(...finite), end: Math.max(...finite) } : null;
}
function timelineOverviewExactTime(value) {
  const ms = timelineEpochMs(value);
  return ms == null ? "—" : new Date(ms).toISOString();
}
function timelineOverviewExactDuration(start, end) {
  const left = timelineEpochMs(start), right = timelineEpochMs(end);
  return left == null || right == null || right < left ? "—" : String(right - left) + " ms";
}
function appendActionTimelineTimingDetails(container, group) {
  const attempt = latestActionTimelineAttempt(group); if (!attempt || timelineEpochMs(attempt.allocated_at) == null) return;
  [
    timelineMeta(t("timeline.overview.allocated"), timelineOverviewExactTime(attempt.allocated_at)),
    timelineMeta(t("timeline.overview.started"), timelineOverviewExactTime(attempt.started_at)),
    timelineMeta(t("timeline.overview.response"), timelineOverviewExactTime(attempt.response_at)),
    timelineMeta(t("timeline.overview.finished"), timelineOverviewExactTime(attempt.finished_at)),
    timelineMeta(t("timeline.overview.queue"), timelineOverviewExactDuration(attempt.allocated_at, attempt.started_at)),
    timelineMeta(t("timeline.overview.ttft"), timelineOverviewExactDuration(attempt.started_at, attempt.response_at)),
    timelineMeta(t("timeline.overview.decode"), timelineOverviewExactDuration(attempt.response_at, attempt.finished_at))
  ].filter(Boolean).forEach(node => container.appendChild(node));
}
function clearActionTimelineOverviewHover(view) {
  const overview = view && view.overview; if (!overview) return;
  if (overview.hoverTimer) clearTimeout(overview.hoverTimer);
  if (overview.hoverLeaveTimer) clearTimeout(overview.hoverLeaveTimer);
  overview.hoverTimer = 0; overview.hoverLeaveTimer = 0; overview.tooltipHovered = false; overview.hoverCandidateId = null; overview.hoverGroupId = null;
  overview.tooltip.replaceChildren(); overview.tooltip.classList.add("hidden"); overview.tooltip.setAttribute("aria-hidden", "true");
  overview.hoverPath.setAttribute("d", "");
}
function cancelActionTimelineOverviewHoverClear(view) {
  const overview = view && view.overview; if (!overview || !overview.hoverLeaveTimer) return;
  clearTimeout(overview.hoverLeaveTimer); overview.hoverLeaveTimer = 0;
}
function scheduleActionTimelineOverviewHoverClear(view) {
  const overview = view && view.overview; if (!overview) return;
  cancelActionTimelineOverviewHoverClear(view);
  overview.hoverLeaveTimer = setTimeout(() => {
    overview.hoverLeaveTimer = 0;
    if (!overview.tooltipHovered) clearActionTimelineOverviewHover(view);
  }, 150);
}
function positionActionTimelineOverviewTooltip(overview, clientX, clientY) {
  const rect = overview.shell.getBoundingClientRect();
  const width = overview.tooltip.offsetWidth || 220, height = overview.tooltip.offsetHeight || 120;
  const pointerX = clientX - rect.left, pointerY = clientY - rect.top;
  const left = Math.max(8, Math.min(Math.max(8, rect.width - width - 8), pointerX - width / 2));
  let top = pointerY + 8;
  if (top + height > rect.height - 8) top = Math.max(8, pointerY - height - 8);
  overview.tooltip.style.left = left + "px"; overview.tooltip.style.top = top + "px";
}
function showActionTimelineOverviewTooltip(view, groupId, clientX, clientY) {
  const overview = view && view.overview, item = overview && overview.model && overview.model.byId.get(groupId);
  if (!overview || !item || overview.hoverCandidateId !== groupId) return;
  const ordinal = timelineOrdinal(item.group.ordinal), title = item.group.title || t("timeline.kind." + timelineKind(item.group));
  const head = el("div", "timeline-overview-tooltip-title", (ordinal == null ? "" : "#" + ordinal + " · ") + title);
  const body = el("div", "timeline-overview-tooltip-grid");
  const row = (label, value) => { body.appendChild(el("span", "timeline-overview-tooltip-key", label)); body.appendChild(el("code", null, value)); };
  row(t("timeline.overview.allocated"), timelineOverviewExactTime(item.times.allocated));
  row(t("timeline.overview.started"), timelineOverviewExactTime(item.times.started));
  row(t("timeline.overview.response"), timelineOverviewExactTime(item.times.response));
  row(t("timeline.overview.finished"), timelineOverviewExactTime(item.times.finished));
  row(t("timeline.overview.queue"), timelineOverviewExactDuration(item.times.allocated, item.times.started));
  row(t("timeline.overview.ttft"), timelineOverviewExactDuration(item.times.started, item.times.response));
  row(t("timeline.overview.decode"), timelineOverviewExactDuration(item.times.response, item.times.finished));
  if (item.running) body.appendChild(el("span", "timeline-overview-running", t("timeline.overview.running")));
  overview.tooltip.replaceChildren(head, body); overview.tooltip.classList.remove("hidden"); overview.tooltip.setAttribute("aria-hidden", "false");
  overview.hoverGroupId = groupId; positionActionTimelineOverviewTooltip(overview, clientX, clientY);
  overview.hoverPath.setAttribute("d", timelineOverviewItemPaths(overview, item).highlight);
}
function actionTimelineOverviewHit(view, event) {
  const overview = view && view.overview, model = overview && overview.model;
  if (!overview || !model || !model.items.length) return null;
  const rect = overview.svg.getBoundingClientRect(); if (!rect.width || !rect.height) return null;
  const x = Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, (event.clientX - rect.left) / rect.width * ACTION_TIMELINE_OVERVIEW_WIDTH));
  const y = Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_HEIGHT - .0001, (event.clientY - rect.top) / rect.height * ACTION_TIMELINE_OVERVIEW_HEIGHT));
  const rank = Math.floor(y / ACTION_TIMELINE_OVERVIEW_HEIGHT * model.laneCount);
  const radius = Math.min(256, Math.max(2, Math.ceil(5 / rect.height * model.laneCount)));
  const tolerance = 6 / rect.width * ACTION_TIMELINE_OVERVIEW_WIDTH;
  let best = null, bestDistance = Infinity;
  for (let candidateRank = Math.max(0, rank - radius); candidateRank <= Math.min(model.laneCount - 1, rank + radius); candidateRank += 1) {
    // A search compresses the SVG lanes to matching records.  Resolve the
    // lane from that painted model, never from the unfiltered loaded array.
    const item = model.items[candidateRank];
    if (!item) continue;
    const markerAt = item.markerAt != null ? item.markerAt : item.pointAt;
    const markerX = markerAt == null ? null : timelineOverviewTimeToX(overview, markerAt);
    const onMarker = markerX != null && Math.abs(markerX - x) <= tolerance;
    const onSegment = item.segments.some(segment => {
      const x1 = timelineOverviewTimeToX(overview, segment.start), x2 = timelineOverviewTimeToX(overview, segment.end);
      return x1 != null && x2 != null && x >= Math.min(x1, x2) - tolerance && x <= Math.max(x1, x2) + tolerance;
    });
    const distance = Math.abs(candidateRank - rank);
    if ((onMarker || onSegment) && distance < bestDistance) { best = item; bestDistance = distance; }
  }
  return best;
}
function actionTimelineOverviewPointerMove(view, event) {
  const overview = view && view.overview; if (!overview) return;
  cancelActionTimelineOverviewHoverClear(view);
  if (overview.gesture) { moveActionTimelineOverviewGesture(view, event); return; }
  const hit = actionTimelineOverviewHit(view, event), groupId = hit && hit.groupId;
  // Once visible, keep the tooltip at its original anchor so a magnification
  // user can actually catch it. Moving across compressed lanes on the way to
  // the tooltip gets a short grace period; stopping elsewhere dismisses it.
  if (overview.hoverGroupId) {
    if (groupId === overview.hoverGroupId) return;
    scheduleActionTimelineOverviewHoverClear(view); return;
  }
  if (!groupId) { clearActionTimelineOverviewHover(view); return; }
  if (overview.hoverCandidateId === groupId) {
    overview.hoverPoint = { clientX: event.clientX, clientY: event.clientY };
    return;
  }
  clearActionTimelineOverviewHover(view); overview.hoverCandidateId = groupId;
  overview.hoverPoint = { clientX: event.clientX, clientY: event.clientY };
  overview.hoverTimer = setTimeout(() => {
    overview.hoverTimer = 0;
    const point = overview.hoverPoint || { clientX: event.clientX, clientY: event.clientY };
    showActionTimelineOverviewTooltip(view, groupId, point.clientX, point.clientY);
  }, ACTION_TIMELINE_OVERVIEW_HOVER_DELAY);
}
function timelineOverviewEventX(overview, event) {
  const rect = overview.svg.getBoundingClientRect();
  if (!rect.width) return 0;
  return Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, (event.clientX - rect.left) / rect.width * ACTION_TIMELINE_OVERVIEW_WIDTH));
}
function timelineOverviewXToTime(overview, x) {
  if (overview.viewStart == null || overview.viewEnd == null) return null;
  if (overview.viewStart === overview.viewEnd) return overview.viewStart;
  const ratio = Math.max(0, Math.min(1, x / ACTION_TIMELINE_OVERVIEW_WIDTH));
  return overview.viewStart + ratio * (overview.viewEnd - overview.viewStart);
}
function timelineOverviewXToDomainTime(start, end, x) {
  if (start == null || end == null) return null;
  if (start === end) return start;
  const ratio = Math.max(0, Math.min(1, x / ACTION_TIMELINE_OVERVIEW_WIDTH));
  return start + ratio * (end - start);
}
// A time filter is a question about when an action happened, not about whether
// the chart could paint it. Permission decisions, native tool calls, delegates
// and finalize groups carry no execution attempt, so they have no span — they
// are still real actions with a real timestamp, and dropping them would hide
// the control plane from the surface whose whole job is to show it.
function actionTimelineSelectionOverlaps(item, selection, group = null) {
  if (!selection) return true;
  const left = Math.min(selection.start, selection.end), right = Math.max(selection.start, selection.end);
  if (item) return item.start <= right && item.end >= left;
  const createdAt = timelineEpochMs(group && group.created_at);
  return createdAt != null && createdAt >= left && createdAt <= right;
}
function normalizeActionTimelineSearch(value) {
  return String(value || "").trim().toLocaleLowerCase();
}
function actionTimelineSearchDocument(group) {
  // The placeholder promises the Kind column, so index the label the row
  // actually shows. group.kind is the raw server token ("code"); the displayed
  // kind is derived from it plus language and event types ("python" ->
  // "Python Cell"), so indexing the raw value alone left the visible text
  // unsearchable. Keep the raw token too — it is what the ledger stores.
  const kind = timelineKind(group || {});
  const fields = [group && group.title, group && group.kind, kind, t("timeline.kind." + kind)];
  ((group && group.events) || []).forEach(event => {
    (event.resource_keys || []).forEach(value => fields.push(value));
    (event.artifacts || []).forEach(value => fields.push(value));
  });
  return fields.filter(value => value != null && value !== "").map(value => String(value).toLocaleLowerCase()).join("\u0000");
}
function syncActionTimelineSearchIndex(view, groups) {
  const previous = view.searchIndex || new Map(), next = new Map();
  groups.forEach(group => {
    const cached = previous.get(group.group_id);
    // The document carries a localized kind label, so it is language-scoped.
    next.set(group.group_id, cached && cached.group === group && cached.lang === LANG
      ? cached : { group, lang: LANG, text: actionTimelineSearchDocument(group) });
  });
  view.searchIndex = next;
}
function searchActionTimelineGroups(view, groups) {
  syncActionTimelineSearchIndex(view, groups);
  if (!view.searchNeedle) return groups;
  return groups.filter(group => {
    const indexed = view.searchIndex.get(group.group_id);
    return !!indexed && indexed.text.includes(view.searchNeedle);
  });
}
function filteredActionTimelineGroups(view, groups) {
  const selection = view && view.overview && view.overview.selection;
  if (!selection) return groups;
  return groups.filter(group => actionTimelineSelectionOverlaps(view.overview.model.byId.get(group.group_id), selection, group));
}
function actionTimelineTurnStats(groups) {
  let totalMs = 0, hasDuration = false, hasRunning = false;
  groups.forEach(group => {
    const attempt = latestActionTimelineAttempt(group), duration = timelineDurationMs(attempt);
    if (duration !== "") { totalMs += duration; hasDuration = true; }
    if (attempt && timelineEpochMs(attempt.finished_at) == null) hasRunning = true;
  });
  const duration = hasDuration ? (hasRunning ? "≥ " : "") + timelineDurationValue(totalMs) : "—";
  return { count: groups.length, totalMs: hasDuration ? totalMs : null, hasRunning, duration };
}
function actionTimelineLedgerEntries(view, groups) {
  const turns = new Map();
  groups.forEach(group => {
    const turnId = publicText(group.turn_id, 96); if (!turnId) return;
    if (!turns.has(turnId)) turns.set(turnId, []);
    turns.get(turnId).push(group);
  });
  const stats = new Map(); turns.forEach((turnGroups, turnId) => stats.set(turnId, actionTimelineTurnStats(turnGroups)));
  const entries = [], emittedCollapsed = new Set(), searchActive = !!view.searchNeedle;
  groups.forEach((group, index) => {
    const turnId = publicText(group.turn_id, 96);
    const previousTurnId = index > 0 ? publicText(groups[index - 1].turn_id, 96) : "";
    const turnStart = index === 0 || previousTurnId !== turnId;
    const turnBoundary = index > 0 && previousTurnId !== turnId;
    // Search temporarily reveals every matching action so its announced match
    // count equals the ledger rows.  The Set is retained and takes effect
    // again as soon as the query is cleared.
    if (!searchActive && turnId && view.collapsedTurns.has(turnId)) {
      if (emittedCollapsed.has(turnId)) return;
      emittedCollapsed.add(turnId);
      entries.push({ type: "turn", turnId, groups: turns.get(turnId), stats: stats.get(turnId), turnBoundary });
      return;
    }
    entries.push({ type: "group", group, turnId, turnStart, turnBoundary, stats: turnId ? stats.get(turnId) : null, foldable: !!turnId && !searchActive });
  });
  return entries;
}
function actionTimelineEntryKey(entry) {
  return !entry ? "" : (entry.type === "turn" ? "turn:" + entry.turnId : "group:" + entry.group.group_id);
}
// `display:none` destroys the scrolling box, so a hidden ledger reports
// scrollTop 0 and offsetHeight 0. Snapshotting it live would anchor every
// off-tab restore to the first entry with a fabricated -30px offset, so read
// through the cached values whenever the pane has no layout box.
function actionTimelineLiveScrollTop(view) {
  return view.scroll.clientHeight > 0 ? view.scroll.scrollTop : view.scrollTop;
}
function actionTimelineHeaderHeight(view) {
  const measured = view.thead.offsetHeight;
  if (measured > 0) { view.headerHeight = measured; return measured; }
  return view.headerHeight || 30;
}
function actionTimelineFilterScrollSnapshot(view) {
  const headerHeight = actionTimelineHeaderHeight(view), scrollTop = actionTimelineLiveScrollTop(view);
  const entries = view.entries || [], index = Math.max(0, Math.min(entries.length - 1, Math.floor(Math.max(0, scrollTop - headerHeight) / ACTION_TIMELINE_ROW_HEIGHT)));
  const entry = entries[index];
  return {
    entryKey: actionTimelineEntryKey(entry),
    groupId: entry && entry.type === "group" ? entry.group.group_id : null,
    turnId: entry && (entry.turnId || (entry.group && entry.group.turn_id)),
    offset: scrollTop - (headerHeight + index * ACTION_TIMELINE_ROW_HEIGHT),
    followTail: view.followTail
  };
}
function syncActionTimelineOverviewDecorations(view) {
  const overview = view.overview, activeSelection = overview.draftSelection || overview.selection;
  const selected = overview.model.byId.get(S.actionTimelineSelectedGroupId);
  overview.selectedPath.setAttribute("d", selected ? timelineOverviewItemPaths(overview, selected).highlight : "");
  if (activeSelection && overview.viewStart != null && overview.viewEnd != null) {
    const x1 = timelineOverviewTimeToX(overview, Math.min(activeSelection.start, activeSelection.end));
    const x2 = timelineOverviewTimeToX(overview, Math.max(activeSelection.start, activeSelection.end));
    const left = Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, x1));
    const right = Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, x2));
    overview.selectionRect.setAttribute("x", String(Math.min(left, right)));
    overview.selectionRect.setAttribute("width", String(Math.abs(right - left)));
    overview.selectionRect.classList.remove("hidden");
  } else {
    overview.selectionRect.classList.add("hidden"); overview.selectionRect.setAttribute("width", "0");
  }
  if (overview.selection) {
    overview.selectionStatus.textContent = t("timeline.overview.selection", timelineOverviewExactTime(overview.selection.start), timelineOverviewExactTime(overview.selection.end));
    overview.clearButton.classList.remove("hidden"); overview.clearButton.disabled = false;
  } else {
    overview.selectionStatus.textContent = ""; overview.clearButton.classList.add("hidden"); overview.clearButton.disabled = true;
  }
}
function syncActionTimelineOverviewControls(view) {
  const overview = view && view.overview, timeline = S.actionTimeline || {};
  if (!overview) return;
  const span = overview.viewStart != null && overview.viewEnd != null ? Math.max(0, overview.viewEnd - overview.viewStart) : 0;
  const tolerance = span / ACTION_TIMELINE_OVERVIEW_WIDTH;
  const includesLoadedStart = overview.dataStart != null && overview.viewStart != null && overview.viewEnd != null &&
    overview.viewStart <= overview.dataStart + tolerance && overview.viewEnd >= overview.dataStart - tolerance;
  // A page made entirely of non-attempt groups has no honest time coordinate.
  // Keep the omission visible at the left edge without inventing a duration.
  const visible = !!timeline.has_more_before && (overview.dataStart == null || includesLoadedStart);
  const loading = actionTimelineHistoryIsLoading(timeline);
  const restoreFocus = !visible && document.activeElement === overview.prefixButton;
  const restoreAfterLoad = !!overview.restoreFocusAfterPrefix && !loading;
  overview.prefixButton.classList.toggle("hidden", !visible); overview.prefixButton.disabled = !visible || loading;
  overview.prefixButton.setAttribute("aria-busy", loading ? "true" : "false");
  overview.prefixButton.setAttribute("aria-label", t(loading ? "timeline.overview.omittedLoading" : "timeline.overview.omitted"));
  const x = overview.dataStart == null ? 0 : timelineOverviewTimeToX(overview, overview.dataStart);
  overview.prefixButton.style.left = Math.max(0, Math.min(100, (x == null ? 0 : x / ACTION_TIMELINE_OVERVIEW_WIDTH * 100))) + "%";
  if (restoreFocus || restoreAfterLoad) {
    overview.restoreFocusAfterPrefix = false;
    const target = visible ? overview.prefixButton : overview.shell;
    try { target.focus({ preventScroll: true }); } catch { target.focus(); }
  }
}
function clearActionTimelineOverviewSelection(view, options = {}) {
  const overview = view && view.overview; if (!overview || (!overview.selection && !overview.draftSelection)) return false;
  const restoreControlFocus = document.activeElement === overview.clearButton;
  overview.selection = null; overview.draftSelection = null;
  const restore = options.restore !== false && !view.searchNeedle ? view.preFilterScroll : null;
  if (!view.searchNeedle) view.preFilterScroll = null;
  view.autoLoadArmed = false; clearActionTimelineOverviewHover(view);
  if (options.update !== false) updateActionTimelineLedger({ direction: "filter", filterChanged: true, filterRestore: restore });
  else syncActionTimelineOverviewDecorations(view);
  if (restoreControlFocus) { try { overview.shell.focus({ preventScroll: true }); } catch { overview.shell.focus(); } }
  return true;
}
function commitActionTimelineOverviewSelection(view, start, end) {
  const overview = view && view.overview;
  if (!overview || start == null || end == null) return;
  if (!overview.selection && !view.searchNeedle) view.preFilterScroll = actionTimelineFilterScrollSnapshot(view);
  overview.selection = { start: Math.floor(Math.min(start, end)), end: Math.ceil(Math.max(start, end)) };
  overview.draftSelection = null; view.autoLoadArmed = false; clearActionTimelineOverviewHover(view);
  updateActionTimelineLedger({ direction: "filter", filterChanged: true });
}
function selectActionTimelineGroup(groupId, branchScope, fromOverview = false) {
  const view = S._timelineView;
  const targetGroup = view && view.allGroups.find(group => group.group_id === groupId);
  if (!view || !groupId || !targetGroup) return;
  let filterChanged = false, filterRestore = null;
  if (fromOverview && view.overview.selection && !view.groups.some(group => group.group_id === groupId)) {
    filterChanged = clearActionTimelineOverviewSelection(view, { update: false, restore: false });
  }
  if (fromOverview && targetGroup.turn_id && view.collapsedTurns.has(targetGroup.turn_id) && !view.searchNeedle) {
    filterRestore = actionTimelineFilterScrollSnapshot(view); view.collapsedTurns.delete(targetGroup.turn_id); filterChanged = true;
  }
  S._timelineRestoreFocusGroupId = groupId; S.actionTimelineSelectedGroupId = groupId; S.actionTimelineSelectedBranchId = branchScope;
  updateActionTimelineLedger({ direction: "selection", filterChanged, filterRestore });
  if (fromOverview) {
    const current = S._timelineView, index = current && current.groups.findIndex(group => group.group_id === groupId);
    if (current && index >= 0) focusActionTimelineGroup(current, index);
  } else {
    const current = S._timelineView; revealActionTimelineOverviewGroup(current, groupId);
    const close = current && current.inspectorHost.querySelector(".timeline-inspector button");
    if (close) { try { close.focus({ preventScroll: true }); } catch { close.focus(); } }
  }
}
function revealActionTimelineOverviewGroup(view, groupId) {
  const overview = view && view.overview, item = overview && overview.model.byId.get(groupId);
  if (!item || overview.viewStart == null || overview.viewEnd == null || overview.dataStart == null || overview.dataEnd == null) return;
  const extent = actionTimelineOverviewVisualExtent(item); if (!extent) return;
  const itemStart = extent.start, itemEnd = extent.end;
  if (itemEnd >= overview.viewStart && itemStart <= overview.viewEnd) return;
  const domainSpan = overview.dataEnd - overview.dataStart, currentSpan = overview.viewEnd - overview.viewStart;
  if (domainSpan <= 0 || currentSpan <= 0) return;
  const span = Math.min(domainSpan, Math.max(currentSpan, itemEnd - itemStart));
  const center = itemStart + (itemEnd - itemStart) / 2;
  const start = Math.max(overview.dataStart, Math.min(center - span / 2, overview.dataEnd - span));
  overview.viewStart = start; overview.viewEnd = start + span; renderActionTimelineOverviewPaths(view);
}
function beginActionTimelineOverviewGesture(view, event) {
  const overview = view && view.overview;
  const button = event.button === 2 || (event.button === 0 && event.ctrlKey) ? 2 : event.button;
  if (!overview || ![0, 2].includes(button) || overview.viewStart == null || overview.viewEnd == null) return;
  clearActionTimelineOverviewHover(view);
  const x = timelineOverviewEventX(overview, event), time = timelineOverviewXToTime(overview, x);
  const hit = button === 0 ? actionTimelineOverviewHit(view, event) : null;
  overview.gesture = { pointerId: event.pointerId, button, startClientX: event.clientX, startX: x, startTime: time, lastTime: time,
    startViewStart: overview.viewStart, startViewEnd: overview.viewEnd, dragging: false, hitGroupId: hit && hit.groupId };
  try { overview.svg.setPointerCapture(event.pointerId); } catch {}
  event.preventDefault();
}
function moveActionTimelineOverviewGesture(view, event) {
  const overview = view && view.overview, gesture = overview && overview.gesture;
  if (!gesture || gesture.pointerId !== event.pointerId) return;
  const x = timelineOverviewEventX(overview, event);
  const time = timelineOverviewXToDomainTime(gesture.startViewStart, gesture.startViewEnd, x);
  if (!gesture.dragging && Math.abs(event.clientX - gesture.startClientX) >= 4) gesture.dragging = true;
  gesture.lastTime = time;
  if (gesture.dragging && gesture.button === 0) {
    overview.draftSelection = { start: Math.floor(Math.min(gesture.startTime, time)), end: Math.ceil(Math.max(gesture.startTime, time)) };
    syncActionTimelineOverviewDecorations(view);
  } else if (gesture.dragging && gesture.button === 2) {
    const rect = overview.svg.getBoundingClientRect(), span = gesture.startViewEnd - gesture.startViewStart, domainSpan = overview.dataEnd - overview.dataStart;
    if (rect.width && span > 0 && span < domainSpan) {
      const shifted = gesture.startViewStart - (event.clientX - gesture.startClientX) / rect.width * span;
      overview.viewStart = Math.max(overview.dataStart, Math.min(shifted, overview.dataEnd - span)); overview.viewEnd = overview.viewStart + span;
      scheduleActionTimelineOverviewPaths(view);
    }
  }
  event.preventDefault();
}
function finishActionTimelineOverviewGesture(view, event) {
  const overview = view && view.overview, gesture = overview && overview.gesture;
  if (!gesture || gesture.pointerId !== event.pointerId) return;
  overview.gesture = null;
  try { overview.svg.releasePointerCapture(event.pointerId); } catch {}
  if (gesture.button === 0 && gesture.dragging) commitActionTimelineOverviewSelection(view, gesture.startTime, gesture.lastTime);
  else if (gesture.button === 0 && gesture.hitGroupId) selectActionTimelineGroup(gesture.hitGroupId, view.branchId, true);
  else if (gesture.button === 2 && !gesture.dragging) clearActionTimelineOverviewSelection(view);
  else { overview.draftSelection = null; syncActionTimelineOverviewDecorations(view); }
  event.preventDefault();
}
function cancelActionTimelineOverviewGesture(view, event) {
  const overview = view && view.overview, gesture = overview && overview.gesture;
  if (!gesture || (event && gesture.pointerId !== event.pointerId)) return;
  overview.gesture = null; overview.draftSelection = null; syncActionTimelineOverviewDecorations(view);
}
function renderActionTimelineOverviewPaths(view) {
  const overview = view.overview, model = overview.model;
  if (!overview || !model) return;
  const aggregate = { queue: "", ttft: "", decode: "", marker: "", point: "" };
  model.items.forEach(item => {
    const paths = timelineOverviewItemPaths(overview, item);
    Object.keys(aggregate).forEach(key => { aggregate[key] += paths[key]; });
  });
  overview.queuePath.setAttribute("d", aggregate.queue); overview.ttftPath.setAttribute("d", aggregate.ttft);
  overview.decodePath.setAttribute("d", aggregate.decode); overview.markerPath.setAttribute("d", aggregate.marker);
  overview.pointPath.setAttribute("d", aggregate.point); syncActionTimelineOverviewDecorations(view);
  if (overview.hoverGroupId && !model.byId.has(overview.hoverGroupId)) clearActionTimelineOverviewHover(view);
  else if (overview.hoverGroupId) {
    const point = overview.hoverPoint;
    if (point) showActionTimelineOverviewTooltip(view, overview.hoverGroupId, point.clientX, point.clientY);
    else overview.hoverPath.setAttribute("d", timelineOverviewItemPaths(overview, model.byId.get(overview.hoverGroupId)).highlight);
  }
  const axisTime = value => value == null ? "—" : new Date(Math.round(value)).toISOString();
  const startText = axisTime(overview.viewStart), endText = axisTime(overview.viewEnd);
  overview.axisStart.textContent = startText === "—" ? startText : startText.slice(11, 23); overview.axisStart.title = startText;
  overview.axisEnd.textContent = endText === "—" ? endText : endText.slice(11, 23); overview.axisEnd.title = endText;
  overview.shell.dataset.viewStart = overview.viewStart == null ? "" : String(Math.round(overview.viewStart));
  overview.shell.dataset.viewEnd = overview.viewEnd == null ? "" : String(Math.round(overview.viewEnd));
  syncActionTimelineOverviewControls(view);
}
function scheduleActionTimelineOverviewPaths(view) {
  const overview = view && view.overview;
  if (!overview || view !== S._timelineView || overview.raf) return;
  overview.raf = requestAnimationFrame(() => {
    overview.raf = 0;
    if (view === S._timelineView && overview.shell.isConnected) renderActionTimelineOverviewPaths(view);
  });
}
function actionTimelineOverviewZoomAt(view, factor, anchorRatio) {
  const overview = view && view.overview;
  if (!overview || overview.dataStart == null || overview.dataEnd == null || overview.viewStart == null || overview.viewEnd == null) return false;
  const domainSpan = overview.dataEnd - overview.dataStart, currentSpan = overview.viewEnd - overview.viewStart;
  if (domainSpan <= 0 || currentSpan <= 0 || !Number.isFinite(factor) || factor <= 0) return false;
  const minSpan = Math.max(1, domainSpan / 1000), nextSpan = Math.max(minSpan, Math.min(domainSpan, currentSpan * factor));
  if (Math.abs(nextSpan - currentSpan) < .001) return false;
  const ratio = Math.max(0, Math.min(1, anchorRatio)), anchor = overview.viewStart + currentSpan * ratio;
  let start = anchor - nextSpan * ratio;
  start = Math.max(overview.dataStart, Math.min(start, overview.dataEnd - nextSpan));
  overview.viewStart = start; overview.viewEnd = start + nextSpan; clearActionTimelineOverviewHover(view); scheduleActionTimelineOverviewPaths(view);
  return true;
}
function actionTimelineOverviewPanBy(view, delta) {
  const overview = view && view.overview;
  if (!overview || overview.dataStart == null || overview.dataEnd == null || overview.viewStart == null || overview.viewEnd == null) return false;
  const span = overview.viewEnd - overview.viewStart, domainSpan = overview.dataEnd - overview.dataStart;
  if (span <= 0 || span >= domainSpan || !Number.isFinite(delta)) return false;
  const start = Math.max(overview.dataStart, Math.min(overview.viewStart + delta, overview.dataEnd - span));
  if (Math.abs(start - overview.viewStart) < .001) return false;
  overview.viewStart = start; overview.viewEnd = start + span; clearActionTimelineOverviewHover(view); scheduleActionTimelineOverviewPaths(view);
  return true;
}
function actionTimelineOverviewWheel(view, event) {
  const overview = view && view.overview; if (!overview || !event.deltaY) return;
  const rect = overview.svg.getBoundingClientRect(); if (!rect.width) return;
  const unit = event.deltaMode === 1 ? 16 : (event.deltaMode === 2 ? rect.height : 1);
  const factor = Math.exp(event.deltaY * unit * .0015), ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
  if (actionTimelineOverviewZoomAt(view, factor, ratio)) event.preventDefault();
}
function showActionTimelineOverviewKeyboardItem(view, groupId) {
  const overview = view && view.overview, item = overview && overview.model.byId.get(groupId);
  if (!item) return false;
  revealActionTimelineOverviewGroup(view, groupId); clearActionTimelineOverviewHover(view);
  overview.keyboardGroupId = groupId; overview.hoverCandidateId = groupId;
  const rect = overview.svg.getBoundingClientRect();
  const extent = actionTimelineOverviewVisualExtent(item);
  const anchorTime = extent ? extent.start + (extent.end - extent.start) / 2 : item.start;
  const rawX = timelineOverviewTimeToX(overview, anchorTime);
  const point = {
    clientX: rect.left + Math.max(0, Math.min(ACTION_TIMELINE_OVERVIEW_WIDTH, rawX == null ? 0 : rawX)) / ACTION_TIMELINE_OVERVIEW_WIDTH * rect.width,
    clientY: rect.top + (item.rank + .5) / item.laneCount * rect.height
  };
  overview.hoverPoint = point; showActionTimelineOverviewTooltip(view, groupId, point.clientX, point.clientY);
  return true;
}
function actionTimelineOverviewKeydown(view, event) {
  const overview = view && view.overview; if (!overview) return;
  if (event.target !== overview.shell && event.target.closest && event.target.closest("button")) return;
  if (event.key === "Escape") {
    if (overview.selection) clearActionTimelineOverviewSelection(view); else clearActionTimelineOverviewHover(view);
    event.preventDefault(); return;
  }
  const items = overview.model.items;
  if (["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) && items.length) {
    const current = Math.max(0, items.findIndex(item => item.groupId === overview.keyboardGroupId));
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 :
      Math.max(0, Math.min(items.length - 1, current + (event.key === "ArrowUp" ? -1 : 1)));
    event.preventDefault(); showActionTimelineOverviewKeyboardItem(view, items[next].groupId); return;
  }
  if (event.key === "Enter" && overview.keyboardGroupId) {
    const item = overview.model.byId.get(overview.keyboardGroupId); if (!item) return;
    event.preventDefault();
    if (event.shiftKey) commitActionTimelineOverviewSelection(view, item.start, item.end);
    else {
      selectActionTimelineGroup(item.groupId, view.branchId, true);
      const close = view.inspectorHost.querySelector(".timeline-inspector button");
      if (close) { try { close.focus({ preventScroll: true }); } catch { close.focus(); } }
    }
    return;
  }
  if (event.key === "+" || event.key === "=") { if (actionTimelineOverviewZoomAt(view, .75, .5)) event.preventDefault(); return; }
  if (event.key === "-") { if (actionTimelineOverviewZoomAt(view, 4 / 3, .5)) event.preventDefault(); return; }
  const span = overview.viewEnd != null && overview.viewStart != null ? overview.viewEnd - overview.viewStart : 0;
  if (event.key === "ArrowLeft" && actionTimelineOverviewPanBy(view, -span * .1)) event.preventDefault();
  else if (event.key === "ArrowRight" && actionTimelineOverviewPanBy(view, span * .1)) event.preventDefault();
}
function drawActionTimelineOverview(view, groups, force = false, domainGroups = groups) {
  const overview = view.overview, model = actionTimelineOverviewModel(groups, domainGroups);
  const previousStart = overview.dataStart, previousEnd = overview.dataEnd;
  const wasFull = !overview.initialized || (overview.viewStart === previousStart && overview.viewEnd === previousEnd);
  overview.model = model; overview.dataStart = model.dataStart; overview.dataEnd = model.dataEnd;
  if (!overview.gesture && (!overview.initialized || wasFull)) {
    overview.viewStart = model.dataStart; overview.viewEnd = model.dataEnd; overview.initialized = model.dataStart != null;
  } else if (!overview.gesture && model.dataStart != null && model.dataEnd != null) {
    const span = Math.max(0, overview.viewEnd - overview.viewStart);
    overview.viewStart = Math.max(model.dataStart, Math.min(overview.viewStart, model.dataEnd - span));
    overview.viewEnd = Math.min(model.dataEnd, overview.viewStart + span);
  }
  renderActionTimelineOverviewPaths(view);
  overview.shell.dataset.itemCount = String(model.items.length);
  overview.label.textContent = t("timeline.overview"); overview.svg.setAttribute("aria-label", t("timeline.overview")); overview.help.textContent = t("timeline.overview.help"); overview.keyboardHelp.textContent = t("timeline.overview.keyboard");
  overview.clearButton.textContent = t("timeline.overview.clear"); overview.clearButton.setAttribute("aria-label", t("timeline.overview.clear"));
  [[overview.zoomInButton, "timeline.overview.zoomIn"], [overview.zoomOutButton, "timeline.overview.zoomOut"], [overview.panEarlierButton, "timeline.overview.panEarlier"], [overview.panLaterButton, "timeline.overview.panLater"]].forEach(([button, key]) => { button.title = t(key); button.setAttribute("aria-label", t(key)); });
  overview.legendQueue.lastChild.textContent = t("timeline.overview.queue"); overview.legendTtft.lastChild.textContent = t("timeline.overview.ttft"); overview.legendDecode.lastChild.textContent = t("timeline.overview.decode");
  overview.shell.classList.toggle("timeline-overview-empty", !model.items.length);
  if ((overview.hoverGroupId && !model.byId.has(overview.hoverGroupId)) ||
      (overview.hoverCandidateId && !model.byId.has(overview.hoverCandidateId))) clearActionTimelineOverviewHover(view);
  if (overview.keyboardGroupId && !model.byId.has(overview.keyboardGroupId)) overview.keyboardGroupId = null;
  if (force) clearActionTimelineOverviewHover(view);
}
function createActionTimelineOverview() {
  const shell = el("section", "timeline-overview"); shell.tabIndex = 0; shell.setAttribute("aria-labelledby", "timeline-overview-label"); shell.setAttribute("aria-describedby", "timeline-overview-help timeline-overview-keyboard-help timeline-overview-tooltip");
  const head = el("div", "timeline-overview-head"), label = el("div", "timeline-overview-label", t("timeline.overview")); label.id = "timeline-overview-label"; head.appendChild(label);
  const legend = el("div", "timeline-overview-legend");
  const legendItem = (className, text) => { const item = el("span", "timeline-overview-legend-item " + className); item.appendChild(el("i")); item.appendChild(el("span", null, text)); legend.appendChild(item); return item; };
  const legendQueue = legendItem("queue", t("timeline.overview.queue"));
  const legendTtft = legendItem("ttft", t("timeline.overview.ttft"));
  const legendDecode = legendItem("decode", t("timeline.overview.decode")); head.appendChild(legend);
  const clearButton = el("button", "timeline-overview-clear hidden", t("timeline.overview.clear")); clearButton.type = "button"; clearButton.disabled = true; clearButton.setAttribute("aria-label", t("timeline.overview.clear")); head.appendChild(clearButton); shell.appendChild(head);
  const help = el("div", "timeline-overview-help", t("timeline.overview.help")); help.id = "timeline-overview-help"; shell.appendChild(help);
  const keyboardHelp = el("div", "timeline-overview-keyboard-help", t("timeline.overview.keyboard")); keyboardHelp.id = "timeline-overview-keyboard-help"; shell.appendChild(keyboardHelp);
  const selectionStatus = el("div", "timeline-overview-selection-status"); selectionStatus.setAttribute("aria-live", "polite"); shell.appendChild(selectionStatus);
  const plot = el("div", "timeline-overview-plot");
  const svg = svgElement("svg", { viewBox: `0 0 ${ACTION_TIMELINE_OVERVIEW_WIDTH} ${ACTION_TIMELINE_OVERVIEW_HEIGHT}`, preserveAspectRatio: "none", role: "img", "aria-label": t("timeline.overview") });
  svg.appendChild(svgElement("rect", { class: "timeline-overview-background", x: 0, y: 0, width: ACTION_TIMELINE_OVERVIEW_WIDTH, height: ACTION_TIMELINE_OVERVIEW_HEIGHT }));
  const queuePath = svgElement("path", { class: "timeline-overview-phase queue" });
  const ttftPath = svgElement("path", { class: "timeline-overview-phase ttft" });
  const decodePath = svgElement("path", { class: "timeline-overview-phase decode" });
  const pointPath = svgElement("path", { class: "timeline-overview-point" });
  const markerPath = svgElement("path", { class: "timeline-overview-running" });
  const selectionRect = svgElement("rect", { class: "timeline-overview-selection hidden", x: 0, y: 0, width: 0, height: ACTION_TIMELINE_OVERVIEW_HEIGHT });
  const selectedPath = svgElement("path", { class: "timeline-overview-highlight selected" });
  const hoverPath = svgElement("path", { class: "timeline-overview-highlight hover" });
  [queuePath, ttftPath, decodePath, pointPath, markerPath, selectionRect, selectedPath, hoverPath].forEach(node => svg.appendChild(node));
  plot.appendChild(svg);
  const prefixButton = el("button", "timeline-overview-prefix hidden", "…"); prefixButton.type = "button"; prefixButton.setAttribute("data-action", "load-omitted-timeline"); prefixButton.setAttribute("aria-label", t("timeline.overview.omitted")); prefixButton.setAttribute("aria-busy", "false"); plot.appendChild(prefixButton);
  shell.appendChild(plot);
  const tooltip = el("div", "timeline-overview-tooltip hidden"); tooltip.id = "timeline-overview-tooltip"; tooltip.setAttribute("role", "tooltip"); tooltip.setAttribute("aria-live", "polite"); tooltip.setAttribute("aria-hidden", "true"); shell.appendChild(tooltip);
  const axis = el("div", "timeline-overview-axis"), axisStart = el("time"), axisEnd = el("time"), nav = el("div", "timeline-overview-nav");
  const navButton = (text, key) => { const button = el("button", "timeline-overview-nav-button", text); button.type = "button"; button.title = t(key); button.setAttribute("aria-label", t(key)); nav.appendChild(button); return button; };
  const panEarlierButton = navButton("←", "timeline.overview.panEarlier"), zoomOutButton = navButton("−", "timeline.overview.zoomOut");
  const zoomInButton = navButton("+", "timeline.overview.zoomIn"), panLaterButton = navButton("→", "timeline.overview.panLater");
  axis.appendChild(axisStart); axis.appendChild(nav); axis.appendChild(axisEnd); shell.appendChild(axis);
  return { shell, head, label, help, keyboardHelp, selectionStatus, clearButton, plot, svg, prefixButton, tooltip, axis, axisStart, axisEnd, nav, zoomInButton, zoomOutButton, panEarlierButton, panLaterButton, queuePath, ttftPath, decodePath, pointPath, markerPath, selectionRect, selectedPath, hoverPath,
    legendQueue, legendTtft, legendDecode, model: actionTimelineOverviewModel([]), dataStart: null, dataEnd: null,
    viewStart: null, viewEnd: null, initialized: false, selection: null, draftSelection: null, gesture: null,
    hoverTimer: 0, hoverLeaveTimer: 0, tooltipHovered: false, hoverCandidateId: null, hoverGroupId: null, hoverPoint: null, keyboardGroupId: null, restoreFocusAfterPrefix: false, raf: 0 };
}
function actionTimelineTurnToggle(turnId, stats, expanded, view) {
  const label = t(expanded ? "timeline.turn.collapse" : "timeline.turn.expand", shortRuntime(turnId), stats.count, stats.duration);
  const button = el("button", "timeline-turn-toggle"); button.type = "button"; button.dataset.turnId = turnId;
  button.setAttribute("aria-expanded", expanded ? "true" : "false"); button.setAttribute("aria-label", label); button.title = label;
  const chevron = el("span", "timeline-turn-chevron", expanded ? "▾" : "▸"); chevron.setAttribute("aria-hidden", "true"); button.appendChild(chevron);
  button.appendChild(el("span", "timeline-turn-marker", t("timeline.turnBoundary")));
  button.onclick = event => { event.stopPropagation(); toggleActionTimelineTurn(view, turnId); };
  return button;
}
function actionTimelineLedgerRow(group, reusableRow, turnBoundary, selected, branchScope, firstRow = false, options = {}) {
  const kind = timelineKind(group), status = String(group.status || "completed").toLowerCase();
  const statusClass = status.replace(/[^a-z0-9_-]/g, "-") || "completed";
  const row = reusableRow || el("tr", "timeline-ledger-row");
  row.className = "timeline-ledger-row kind-" + kind + " status-" + statusClass + (turnBoundary ? " turn-boundary" : "") + (selected ? " selected" : "") + (firstRow ? " timeline-first-row" : "") + (options.searchMatch ? " search-match" : "");
  row.setAttribute("role", "row");
  row.dataset.groupId = group.group_id; row.dataset.turnId = group.turn_id || ""; row.dataset.actionKind = kind;
  row.dataset.status = statusClass;
  const ordinal = timelineOrdinal(group.ordinal), ordinalText = ordinal == null ? "—" : String(ordinal);
  row.replaceChildren();
  const cell = className => { const node = el("td", className); node.setAttribute("role", "cell"); return node; };
  const ordinalCell = cell("timeline-ledger-ordinal");
  if (options.turnStart) {
    if (options.foldable && options.stats) ordinalCell.appendChild(actionTimelineTurnToggle(options.turnId, options.stats, true, S._timelineView));
    else ordinalCell.appendChild(el("span", "timeline-turn-marker", t("timeline.turnBoundary")));
  }
  ordinalCell.appendChild(el("span", "timeline-ordinal-value", "#" + ordinalText)); row.appendChild(ordinalCell);
  const kindCell = cell("timeline-ledger-kind"); const kindIcon = el("span", "timeline-kind-icon");
  kindIcon.title = t("timeline.kind." + kind); kindIcon.setAttribute("aria-hidden", "true"); kindIcon.appendChild(iconEl(timelineKindIcon(kind), 15)); kindCell.appendChild(kindIcon);
  kindCell.appendChild(el("span", "timeline-kind-label", t("timeline.kind." + kind))); row.appendChild(kindCell);
  const title = group.title || t("timeline.kind." + kind), titleCell = cell("timeline-ledger-title");
  // Status was color on a 15px glyph and nothing else, so cancelled, pending,
  // running and recorded rows were pixel-identical to completed and no
  // accessible name carried the state. Anything that is not a plain completion
  // gets text, in the row and in its accessible name.
  const statusText = publicText(status || "completed", 32);
  const statusNoteworthy = statusClass !== "completed" && statusClass !== "recorded";
  if (statusNoteworthy) titleCell.appendChild(el("span", "timeline-ledger-status timeline-status " + statusClass, statusText));
  const titleButton = el("button", "timeline-row-button", title); titleButton.type = "button";
  const latest = latestActionTimelineAttempt(group), rowError = latest && latest.error;
  titleButton.title = rowError ? title + " — " + rowError : title;
  titleButton.setAttribute("aria-label", statusNoteworthy
    ? t("timeline.row.open", ordinalText, title) + " · " + statusText + (rowError ? " · " + rowError : "")
    : t("timeline.row.open", ordinalText, title));
  titleButton.setAttribute("aria-expanded", selected ? "true" : "false");
  if (selected) titleButton.setAttribute("aria-controls", "timeline-action-inspector");
  titleCell.appendChild(titleButton); row.appendChild(titleCell);
  // Only the two scalars are needed here; actionTimelineDetails also walks
  // every event and de-dupes resources/artifacts with Array.includes, and the
  // row discarded all of it.
  const durationCell = cell("timeline-ledger-duration"); durationCell.textContent = timelineDuration(latest) || "—"; row.appendChild(durationCell);
  // An absent usage row means unknown, not zero. The server returns null for
  // every group that carried no model reply, and a fabricated 0 is
  // indistinguishable from a real one.
  const tokens = cell("timeline-ledger-tokens");
  tokens.textContent = group.usage ? String(timelineTokenTotal(group.usage)) : "—";
  if (group.usage) tokens.title = t("timeline.tokensValue", (group.usage || {}).input_tokens || 0, (group.usage || {}).output_tokens || 0);
  row.appendChild(tokens);
  const groupId = group.group_id;
  row.onclick = () => selectActionTimelineGroup(groupId, branchScope, false);
  row._timelineGroup = group; row._timelineTurnBoundary = turnBoundary; row._timelineSelected = selected;
  row._timelineBranchScope = branchScope; row._timelineLanguage = LANG; row._timelineFirstRow = firstRow;
  row._timelineTurnStart = !!options.turnStart; row._timelineTurnSignature = options.stats ? options.stats.count + ":" + options.stats.duration : "";
  row._timelineFoldable = !!options.foldable; row._timelineSearchMatch = !!options.searchMatch;
  return row;
}
function actionTimelineTurnSummaryRow(entry, reusableRow, branchScope, firstRow = false) {
  const row = reusableRow || el("tr", "timeline-ledger-row timeline-turn-summary");
  row.className = "timeline-ledger-row timeline-turn-summary" + (entry.turnBoundary ? " turn-boundary" : "") + (firstRow ? " timeline-first-row" : "");
  row.setAttribute("role", "row");
  delete row.dataset.groupId; delete row.dataset.actionKind; delete row.dataset.status; row.dataset.turnId = entry.turnId;
  row.replaceChildren();
  const cell = (className, text) => { const node = el("td", className, text); node.setAttribute("role", "cell"); return node; };
  const ordinalCell = cell("timeline-ledger-ordinal"); ordinalCell.appendChild(actionTimelineTurnToggle(entry.turnId, entry.stats, false, S._timelineView)); row.appendChild(ordinalCell);
  const kindCell = cell("timeline-ledger-kind"); const chevron = el("span", "timeline-turn-summary-icon", "↳"); chevron.setAttribute("aria-hidden", "true"); kindCell.appendChild(chevron); row.appendChild(kindCell);
  row.appendChild(cell("timeline-ledger-title", t("timeline.turn.summary", entry.stats.count)));
  row.appendChild(cell("timeline-ledger-duration", entry.stats.duration)); row.appendChild(cell("timeline-ledger-tokens", "—"));
  row.onclick = () => toggleActionTimelineTurn(S._timelineView, entry.turnId);
  row._timelineTurnId = entry.turnId; row._timelineTurnBoundary = entry.turnBoundary; row._timelineTurnSignature = entry.stats.count + ":" + entry.stats.duration;
  row._timelineBranchScope = branchScope; row._timelineLanguage = LANG; row._timelineFirstRow = firstRow;
  return row;
}
function actionTimelineEntryIndexForGroup(view, groupId) {
  return (view.entries || []).findIndex(entry => entry.type === "group" ? entry.group.group_id === groupId : entry.groups.some(group => group.group_id === groupId));
}
function focusActionTimelineEntry(view, index) {
  if (!view || !view.entries.length) return;
  const targetIndex = Math.max(0, Math.min(view.entries.length - 1, index)), entry = view.entries[targetIndex];
  const viewportTop = targetIndex * ACTION_TIMELINE_ROW_HEIGHT;
  const headerHeight = actionTimelineHeaderHeight(view);
  const viewportBottom = viewportTop + ACTION_TIMELINE_ROW_HEIGHT + headerHeight;
  if (viewportTop < view.scroll.scrollTop) view.scroll.scrollTop = viewportTop;
  else if (viewportBottom > view.scroll.scrollTop + view.scroll.clientHeight) view.scroll.scrollTop = viewportBottom - view.scroll.clientHeight;
  view.scrollTop = view.scroll.scrollTop;
  if (entry.type === "group") S._timelineRestoreFocusGroupId = entry.group.group_id;
  else view.restoreFocusTurnId = entry.turnId;
  reconcileActionTimelineWindow(view);
}
function focusActionTimelineGroup(view, index) {
  if (!view || !view.groups.length) return;
  const group = view.groups[Math.max(0, Math.min(view.groups.length - 1, index))];
  const entryIndex = group && actionTimelineEntryIndexForGroup(view, group.group_id);
  if (entryIndex >= 0) focusActionTimelineEntry(view, entryIndex);
}
function actionTimelineLedgerKeydown(view, event) {
  const row = event.target && event.target.closest ? event.target.closest(".timeline-ledger-row") : null;
  if (!row || !view || view !== S._timelineView) return;
  const index = view.entries.findIndex(entry => entry.type === "group" ? entry.group.group_id === row.dataset.groupId : entry.turnId === row.dataset.turnId);
  if (index < 0) return;
  const page = Math.max(1, Math.floor(view.scroll.clientHeight / ACTION_TIMELINE_ROW_HEIGHT) - 1);
  const targets = { ArrowUp: index - 1, ArrowDown: index + 1, PageUp: index - page, PageDown: index + page, Home: 0, End: view.entries.length - 1 };
  if (!(event.key in targets)) return;
  event.preventDefault(); focusActionTimelineEntry(view, targets[event.key]);
}
function sortedActionTimelineGroups(timeline = S.actionTimeline) {
  return ((timeline && timeline.groups) || []).filter(group => !!group.group_id).slice().sort((left, right) => {
    const leftOrdinal = timelineOrdinal(left.ordinal), rightOrdinal = timelineOrdinal(right.ordinal);
    if (leftOrdinal != null && rightOrdinal != null && leftOrdinal !== rightOrdinal) return leftOrdinal - rightOrdinal;
    if (leftOrdinal != null && rightOrdinal == null) return -1;
    if (leftOrdinal == null && rightOrdinal != null) return 1;
    const created = (+left.created_at || 0) - (+right.created_at || 0);
    return created || String(left.group_id).localeCompare(String(right.group_id));
  });
}
function destroyActionTimelineView(view = S._timelineView) {
  if (!view) return;
  if (view.raf) cancelAnimationFrame(view.raf);
  if (view.overview && view.overview.raf) cancelAnimationFrame(view.overview.raf);
  if (view.overview && view.overview.dismissKeydown) document.removeEventListener("keydown", view.overview.dismissKeydown);
  clearActionTimelineOverviewHover(view);
  if (view.resizeObserver) view.resizeObserver.disconnect();
  if (S._timelineView === view) S._timelineView = null;
}
function actionTimelineViewMatches(view, rootFrameId, branchId) {
  return !!view && view.rootFrameId === rootFrameId && view.branchId === branchId;
}
function actionTimelineBottomDistance(view) {
  return Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight - view.scroll.scrollTop);
}
function scheduleActionTimelineWindow(view) {
  if (!view || view !== S._timelineView || view.raf) return;
  view.raf = requestAnimationFrame(() => {
    view.raf = 0;
    if (view === S._timelineView && view.region.isConnected) reconcileActionTimelineWindow(view);
  });
}
function actionTimelineViewportScrolled(view) {
  if (!view || view !== S._timelineView) return;
  view.scrollTop = view.scroll.scrollTop; view.scrollLeft = view.scroll.scrollLeft;
  view.followTail = actionTimelineBottomDistance(view) <= ACTION_TIMELINE_BOTTOM_THRESHOLD;
  if (view.scroll.scrollTop > ACTION_TIMELINE_TOP_THRESHOLD) {
    view.autoLoadArmed = true; view.autoLoadCursor = null;
  }
  scheduleActionTimelineWindow(view);
  const timeline = S.actionTimeline || {}, first = timelineOrdinal(timeline.first_ordinal);
  if (!view.overview.selection && !view.searchNeedle && view.scroll.scrollTop <= ACTION_TIMELINE_TOP_THRESHOLD && timeline.has_more_before &&
      !S._timelineHistoryLoading && view.autoLoadArmed && first != null && view.autoLoadCursor !== first) {
    view.autoLoadArmed = false; view.autoLoadCursor = first; loadEarlierActionTimeline();
  }
}
function createActionTimelineToolbar() {
  const shell = el("form", "timeline-toolbar"); shell.setAttribute("role", "search"); shell.onsubmit = event => event.preventDefault();
  const field = el("div", "timeline-search-field");
  const label = el("label", "timeline-search-label", t("timeline.search.label")); label.htmlFor = "timeline-action-search"; field.appendChild(label);
  const controls = el("div", "timeline-search-controls");
  const input = el("input", "timeline-search-input"); input.id = "timeline-action-search"; input.type = "search"; input.maxLength = 256;
  input.autocomplete = "off"; input.spellcheck = false; input.placeholder = t("timeline.search.placeholder");
  input.setAttribute("aria-describedby", "timeline-search-scope timeline-search-status"); controls.appendChild(input);
  const clearButton = el("button", "timeline-search-clear hidden", t("timeline.search.clear")); clearButton.type = "button"; clearButton.disabled = true;
  clearButton.setAttribute("aria-label", t("timeline.search.clear")); controls.appendChild(clearButton); field.appendChild(controls); shell.appendChild(field);
  const meta = el("div", "timeline-search-meta");
  const status = el("span", "timeline-search-status", t("timeline.search.loaded", 0)); status.id = "timeline-search-status";
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite"); status.setAttribute("aria-atomic", "true"); meta.appendChild(status);
  const scope = el("span", "timeline-search-scope", t("timeline.search.scope", 0)); scope.id = "timeline-search-scope"; meta.appendChild(scope);
  shell.appendChild(meta); return { shell, label, input, clearButton, status, scope };
}
function syncActionTimelineSearchToolbar(view, loadedCount, matchCount, searchMatchCount = matchCount, force = false) {
  const search = view.search, active = !!view.searchNeedle;
  if (search.input.value !== view.searchQuery) search.input.value = view.searchQuery;
  search.shell.dataset.matchCount = String(matchCount); search.shell.dataset.searchMatchCount = String(searchMatchCount); search.shell.dataset.loadedCount = String(loadedCount);
  search.status.textContent = active && view.overview.selection
    ? t("timeline.search.matchesInSelection", matchCount, searchMatchCount, loadedCount)
    : t(active ? "timeline.search.matches" : "timeline.search.loaded", active ? matchCount : loadedCount, loadedCount);
  search.scope.textContent = t("timeline.search.scope", loadedCount);
  search.clearButton.classList.toggle("hidden", !active); search.clearButton.disabled = !active;
  if (force) {
    search.label.textContent = t("timeline.search.label"); search.input.placeholder = t("timeline.search.placeholder");
    search.clearButton.textContent = t("timeline.search.clear"); search.clearButton.setAttribute("aria-label", t("timeline.search.clear"));
  }
}
function changeActionTimelineSearch(view, rawQuery) {
  if (!view || view !== S._timelineView) return;
  const query = String(rawQuery || "").slice(0, 256), needle = normalizeActionTimelineSearch(query);
  if (query === view.searchQuery && needle === view.searchNeedle) return;
  const wasFiltered = !!view.searchNeedle || !!view.overview.selection;
  if (!wasFiltered && needle) view.preFilterScroll = actionTimelineFilterScrollSnapshot(view);
  view.searchQuery = query; view.searchNeedle = needle; view.autoLoadArmed = false;
  let restore = null;
  if (!needle && !view.overview.selection) { restore = view.preFilterScroll; view.preFilterScroll = null; }
  clearActionTimelineOverviewHover(view);
  updateActionTimelineLedger({ direction: "search", filterChanged: true, filterRestore: restore });
}
function toggleActionTimelineTurn(view, turnId) {
  if (!view || view !== S._timelineView || !turnId || view.searchNeedle) return;
  const snapshot = actionTimelineFilterScrollSnapshot(view), collapsing = !view.collapsedTurns.has(turnId);
  if (collapsing) view.collapsedTurns.add(turnId); else view.collapsedTurns.delete(turnId);
  if (collapsing) {
    const selected = view.groups.find(group => group.group_id === S.actionTimelineSelectedGroupId);
    if (selected && selected.turn_id === turnId) { S.actionTimelineSelectedGroupId = null; S.actionTimelineSelectedBranchId = null; }
  }
  view.restoreFocusTurnId = turnId; view.autoLoadArmed = false;
  updateActionTimelineLedger({ direction: "fold", filterChanged: true, filterRestore: snapshot });
}
function createActionTimelineView(rootFrameId, branchId) {
  const region = el("div", "timeline-ledger-region");
  region.dataset.rootFrameId = rootFrameId; region.dataset.branchId = branchId;
  region.style.setProperty("--timeline-row-height", ACTION_TIMELINE_ROW_HEIGHT + "px");
  const search = createActionTimelineToolbar(); region.appendChild(search.shell);
  const overview = createActionTimelineOverview(); region.appendChild(overview.shell);
  const inspectorHost = el("div", "timeline-inspector-host hidden"); region.appendChild(inspectorHost);
  const filterEmpty = el("div", "workbench-empty timeline-filter-empty hidden", t("timeline.overview.emptySelection")); region.appendChild(filterEmpty);
  const ledgerHelp = el("div", "timeline-ledger-keyboard-help", t("timeline.ledger.keyboard")); ledgerHelp.id = "timeline-ledger-keyboard-help"; region.appendChild(ledgerHelp);
  const scroll = el("div", "timeline-ledger-scroll");
  scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", t("timeline.title")); scroll.setAttribute("aria-describedby", ledgerHelp.id);
  // The stylesheet gives every table element an explicit display (block/grid/
  // flex) so the rows can be absolutely positioned, and that strips the
  // implicit table/rowgroup/row/cell roles. aria-colcount, aria-rowcount and
  // aria-rowindex are only honoured inside a table role context, so without
  // these the virtualized "row 412 of 8000" announcement is silently dropped.
  const table = el("table", "timeline-ledger"); table.setAttribute("role", "table"); table.setAttribute("aria-colcount", "5");
  const thead = el("thead"), header = el("tr");
  thead.setAttribute("role", "rowgroup"); header.setAttribute("role", "row"); header.setAttribute("aria-rowindex", "1");
  const headerColumns = [["timeline.column.ordinal", "timeline-ledger-ordinal"], ["timeline.column.kind", "timeline-ledger-kind"], ["timeline.column.action", "timeline-ledger-title"], ["timeline.duration", "timeline-ledger-duration"], ["timeline.tokens", "timeline-ledger-tokens"]];
  headerColumns.forEach(([key, className], column) => {
    const th = el("th", className, t(key)); th.scope = "col"; th.dataset.i18nKey = key;
    th.setAttribute("role", "columnheader"); th.setAttribute("aria-colindex", String(column + 1)); header.appendChild(th);
  });
  thead.appendChild(header); table.appendChild(thead);
  const tbody = el("tbody", "timeline-ledger-body"); tbody.setAttribute("role", "rowgroup"); table.appendChild(tbody); scroll.appendChild(table); region.appendChild(scroll);
  const view = { rootFrameId, branchId, region, search, overview, inspectorHost, filterEmpty, ledgerHelp, scroll, table, thead, tbody, allGroups: [], groups: [], entries: [],
    initialized: false, followTail: true, scrollTop: 0, scrollLeft: 0, headerHeight: 0, start: 0, end: 0,
    autoLoadArmed: true, autoLoadCursor: null, raf: 0, resizeObserver: null, language: LANG,
    searchQuery: "", searchNeedle: "", searchIndex: new Map(), collapsedTurns: new Set(), preFilterScroll: null,
    pendingPrependRestore: null, restoreFocusTurnId: null };
  search.input.oninput = () => changeActionTimelineSearch(view, search.input.value);
  search.input.onkeydown = event => { if (event.key === "Escape" && view.searchNeedle) { event.preventDefault(); changeActionTimelineSearch(view, ""); } };
  search.clearButton.onclick = event => { event.preventDefault(); search.input.focus(); changeActionTimelineSearch(view, ""); };
  overview.svg.addEventListener("pointermove", event => actionTimelineOverviewPointerMove(view, event));
  overview.svg.addEventListener("pointerdown", event => beginActionTimelineOverviewGesture(view, event));
  overview.svg.addEventListener("pointerup", event => finishActionTimelineOverviewGesture(view, event));
  overview.svg.addEventListener("pointercancel", event => cancelActionTimelineOverviewGesture(view, event));
  overview.svg.addEventListener("lostpointercapture", event => cancelActionTimelineOverviewGesture(view, event));
  overview.svg.addEventListener("pointerleave", event => {
    if (overview.gesture) return;
    if (event.relatedTarget === overview.tooltip || overview.tooltip.contains(event.relatedTarget)) return;
    scheduleActionTimelineOverviewHoverClear(view);
  });
  overview.tooltip.addEventListener("pointerenter", () => { overview.tooltipHovered = true; cancelActionTimelineOverviewHoverClear(view); });
  overview.tooltip.addEventListener("pointerleave", event => {
    overview.tooltipHovered = false;
    if (event.relatedTarget === overview.svg || overview.svg.contains(event.relatedTarget)) return;
    scheduleActionTimelineOverviewHoverClear(view);
  });
  overview.svg.addEventListener("wheel", event => actionTimelineOverviewWheel(view, event), { passive: false });
  overview.svg.addEventListener("contextmenu", event => event.preventDefault());
  overview.shell.addEventListener("keydown", event => actionTimelineOverviewKeydown(view, event));
  overview.shell.addEventListener("focus", () => {
    const groupId = overview.keyboardGroupId || (overview.model.byId.has(S.actionTimelineSelectedGroupId) ? S.actionTimelineSelectedGroupId : (overview.model.items[0] || {}).groupId);
    if (groupId) showActionTimelineOverviewKeyboardItem(view, groupId);
  });
  overview.shell.addEventListener("focusout", event => { if (!overview.shell.contains(event.relatedTarget)) clearActionTimelineOverviewHover(view); });
  overview.dismissKeydown = event => {
    if (event.key === "Escape" && view === S._timelineView && (overview.hoverTimer || overview.hoverGroupId)) {
      clearActionTimelineOverviewHover(view); event.preventDefault();
    }
  };
  document.addEventListener("keydown", overview.dismissKeydown);
  overview.clearButton.onclick = event => { event.stopPropagation(); clearActionTimelineOverviewSelection(view); };
  overview.zoomInButton.onclick = event => { event.stopPropagation(); actionTimelineOverviewZoomAt(view, .75, .5); };
  overview.zoomOutButton.onclick = event => { event.stopPropagation(); actionTimelineOverviewZoomAt(view, 4 / 3, .5); };
  overview.panEarlierButton.onclick = event => { event.stopPropagation(); const span = overview.viewEnd - overview.viewStart; actionTimelineOverviewPanBy(view, -span * .1); };
  overview.panLaterButton.onclick = event => { event.stopPropagation(); const span = overview.viewEnd - overview.viewStart; actionTimelineOverviewPanBy(view, span * .1); };
  overview.prefixButton.addEventListener("pointerdown", event => event.stopPropagation());
  // Arm the focus-restore latch only if the load actually took the lock.
  // loadEarlierActionTimeline sets it synchronously before its first await, so
  // this reads the real outcome; setting the latch unconditionally left it
  // armed across every early return, and the next repaint then stole focus.
  overview.prefixButton.onclick = event => {
    event.stopPropagation(); loadEarlierActionTimeline();
    overview.restoreFocusAfterPrefix = !!S._timelineHistoryLoading;
  };
  scroll.addEventListener("scroll", () => actionTimelineViewportScrolled(view), { passive: true });
  scroll.addEventListener("keydown", event => {
    if (event.target !== scroll || !view.entries.length || !["Enter", "ArrowDown", "Home"].includes(event.key)) return;
    const headerHeight = actionTimelineHeaderHeight(view);
    const firstVisible = Math.max(0, Math.min(view.entries.length - 1,
      Math.floor(Math.max(0, view.scroll.scrollTop - headerHeight) / ACTION_TIMELINE_ROW_HEIGHT)));
    event.preventDefault(); focusActionTimelineEntry(view, event.key === "Home" ? 0 : firstVisible);
  });
  table.addEventListener("keydown", event => actionTimelineLedgerKeydown(view, event));
  if (typeof ResizeObserver !== "undefined") {
    view.resizeObserver = new ResizeObserver(() => scheduleActionTimelineWindow(view));
    view.resizeObserver.observe(scroll);
  }
  S._timelineView = view; return view;
}
function actionTimelineLedger(groups, branchScope, rootFrameScope) {
  let view = S._timelineView;
  if (!actionTimelineViewMatches(view, rootFrameScope, branchScope)) {
    destroyActionTimelineView(view); view = createActionTimelineView(rootFrameScope, branchScope);
  }
  return view.region;
}
function syncActionTimelineInspector(view, force = false) {
  const selected = view.groups.find(group => group.group_id === S.actionTimelineSelectedGroupId) || null;
  const current = view.inspectorHost.firstElementChild;
  if (!selected) {
    view.inspectorHost.replaceChildren(); view.inspectorHost.classList.add("hidden"); return;
  }
  view.inspectorHost.classList.remove("hidden");
  if (force || !current || current._timelineGroup !== selected || current._timelineLanguage !== LANG) {
    const restoreCloseFocus = !!(current && current.contains(document.activeElement));
    const next = actionTimelineInspector(selected); view.inspectorHost.replaceChildren(next);
    if (restoreCloseFocus) {
      const close = next.querySelector("button");
      if (close) { try { close.focus({ preventScroll: true }); } catch { close.focus(); } }
    }
  }
}
function reconcileActionTimelineWindow(view, force = false) {
  if (!view || view !== S._timelineView) return;
  const entries = view.entries, scroll = view.scroll;
  const viewportHeight = scroll.clientHeight || ACTION_TIMELINE_ROW_HEIGHT * 12;
  const headerHeight = actionTimelineHeaderHeight(view);
  const viewportStart = Math.max(0, scroll.scrollTop - headerHeight);
  const viewportEnd = Math.max(0, scroll.scrollTop + viewportHeight - headerHeight);
  const start = Math.max(0, Math.floor(viewportStart / ACTION_TIMELINE_ROW_HEIGHT) - ACTION_TIMELINE_OVERSCAN);
  const end = Math.min(entries.length, Math.ceil(viewportEnd / ACTION_TIMELINE_ROW_HEIGHT) + ACTION_TIMELINE_OVERSCAN);
  const activeRow = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".timeline-ledger-row") : null;
  const activeGroupId = activeRow && activeRow.dataset.groupId, activeTurnId = activeRow && !activeGroupId && activeRow.dataset.turnId;
  const requestedFocusGroupId = S._timelineRestoreFocusGroupId; S._timelineRestoreFocusGroupId = null;
  const requestedFocusTurnId = view.restoreFocusTurnId; view.restoreFocusTurnId = null;
  const reusableRows = new Map(), reusableTurns = new Map();
  view.tbody.querySelectorAll(".timeline-ledger-row[data-group-id]").forEach(row => { if (row.dataset.groupId) reusableRows.set(row.dataset.groupId, row); });
  view.tbody.querySelectorAll(".timeline-turn-summary[data-turn-id]").forEach(row => { if (row.dataset.turnId) reusableTurns.set(row.dataset.turnId, row); });
  const fragment = document.createDocumentFragment();
  entries.slice(start, end).forEach((entry, offset) => {
    const index = start + offset, firstRow = index === 0;
    let row;
    if (entry.type === "turn") {
      row = reusableTurns.get(entry.turnId);
      const signature = entry.stats.count + ":" + entry.stats.duration;
      if (force || !row || row._timelineTurnBoundary !== entry.turnBoundary || row._timelineTurnSignature !== signature ||
          row._timelineBranchScope !== view.branchId || row._timelineLanguage !== LANG || row._timelineFirstRow !== firstRow) {
        row = actionTimelineTurnSummaryRow(entry, row, view.branchId, firstRow);
      }
    } else {
      const group = entry.group, selected = group.group_id === S.actionTimelineSelectedGroupId;
      row = reusableRows.get(group.group_id);
      const signature = entry.stats ? entry.stats.count + ":" + entry.stats.duration : "";
      if (force || !row || row._timelineGroup !== group || row._timelineTurnBoundary !== entry.turnBoundary ||
          row._timelineSelected !== selected || row._timelineBranchScope !== view.branchId || row._timelineLanguage !== LANG ||
          row._timelineFirstRow !== firstRow || row._timelineTurnStart !== entry.turnStart || row._timelineTurnSignature !== signature ||
          row._timelineFoldable !== entry.foldable || row._timelineSearchMatch !== !!view.searchNeedle) {
        row = actionTimelineLedgerRow(group, row, entry.turnBoundary, selected, view.branchId, firstRow, {
          turnStart: entry.turnStart, turnId: entry.turnId, stats: entry.stats, foldable: entry.foldable, searchMatch: !!view.searchNeedle
        });
      }
    }
    row.style.transform = `translateY(${index * ACTION_TIMELINE_ROW_HEIGHT}px)`;
    row.setAttribute("aria-rowindex", String(index + 2)); fragment.appendChild(row);
  });
  view.tbody.replaceChildren(fragment); view.start = start; view.end = end;
  const focusGroupId = requestedFocusGroupId || activeGroupId, focusTurnId = requestedFocusTurnId || activeTurnId;
  if (focusGroupId) {
    const focusRow = Array.from(view.tbody.querySelectorAll(".timeline-ledger-row[data-group-id]")).find(row => row.dataset.groupId === focusGroupId);
    const group = view.groups.find(item => item.group_id === focusGroupId);
    const summaryRow = !focusRow && group ? Array.from(view.tbody.querySelectorAll(".timeline-turn-summary[data-turn-id]")).find(row => row.dataset.turnId === group.turn_id) : null;
    const focusTarget = focusRow ? focusRow.querySelector(".timeline-row-button") : summaryRow && summaryRow.querySelector(".timeline-turn-toggle");
    if (focusTarget && document.activeElement !== focusTarget) {
      try { focusTarget.focus({ preventScroll: true }); } catch { focusTarget.focus(); }
    }
  } else if (focusTurnId) {
    const turnRow = Array.from(view.tbody.querySelectorAll(".timeline-ledger-row[data-turn-id]")).find(row => row.dataset.turnId === focusTurnId);
    const focusTarget = turnRow && (turnRow.querySelector(".timeline-turn-toggle") || turnRow.querySelector(".timeline-row-button"));
    if (focusTarget && document.activeElement !== focusTarget) {
      try { focusTarget.focus({ preventScroll: true }); } catch { focusTarget.focus(); }
    }
  }
}
function updateActionTimelineLedger(options = {}) {
  if (S.activeTab !== "timeline") return;
  const timeline = S.actionTimeline || {}, allGroups = sortedActionTimelineGroups(timeline);
  const rootFrameScope = actionTimelineRootScope(timeline), branchScope = actionTimelineBranchScope(timeline, allGroups);
  const view = S._timelineView;
  if (!allGroups.length || !actionTimelineViewMatches(view, rootFrameScope, branchScope) || !view.region.isConnected) {
    renderActionTimeline(); return;
  }
  const previousVisibleGroups = view.groups;
  const force = view.language !== LANG;
  view.allGroups = allGroups; view.language = LANG;
  const searchGroups = searchActionTimelineGroups(view, allGroups);
  drawActionTimelineOverview(view, searchGroups, force, allGroups);
  const groups = filteredActionTimelineGroups(view, searchGroups), entries = actionTimelineLedgerEntries(view, groups);
  const selectedHasActionRow = entries.some(entry => entry.type === "group" && entry.group.group_id === S.actionTimelineSelectedGroupId);
  if (S.actionTimelineSelectedBranchId !== branchScope || !groups.some(group => group.group_id === S.actionTimelineSelectedGroupId) || !selectedHasActionRow) {
    S.actionTimelineSelectedGroupId = null; S.actionTimelineSelectedBranchId = null;
  }
  const previousVisibleIds = new Set(previousVisibleGroups.map(group => group.group_id));
  // A running group can enter a loaded-window search when a streamed event
  // adds a matching resource or artifact, even though allGroups did not grow.
  // Treat that as a visible append for tail following without mistaking a
  // removal/non-match for newly visible data.
  const tailAdded = !options.filterChanged && groups.some(group => !previousVisibleIds.has(group.group_id));
  view.groups = groups; view.entries = entries; syncActionTimelineOverviewDecorations(view);
  syncActionTimelineSearchToolbar(view, allGroups.length, groups.length, searchGroups.length, force); view.search.shell.dataset.visibleCount = String(groups.length);
  view.table.setAttribute("aria-rowcount", String(entries.length + 1)); view.table.setAttribute("aria-label", t("timeline.title"));
  view.scroll.setAttribute("aria-label", t("timeline.title")); view.tbody.style.height = (entries.length * ACTION_TIMELINE_ROW_HEIGHT) + "px";
  const filterEmpty = (view.overview.selection || view.searchNeedle) && !groups.length;
  view.filterEmpty.classList.toggle("hidden", !filterEmpty);
  if (filterEmpty) view.filterEmpty.textContent = t(view.searchNeedle
    ? (view.overview.selection && searchGroups.length ? "timeline.search.emptySelection" : "timeline.search.empty")
    : "timeline.overview.emptySelection", searchGroups.length);
  view.overview.selectionStatus.dataset.matchCount = String(groups.length);
  if (force) {
    view.thead.querySelectorAll("th[data-i18n-key]").forEach(th => { th.textContent = t(th.dataset.i18nKey); });
    view.ledgerHelp.textContent = t("timeline.ledger.keyboard");
    // The clear button's own label is already re-localized unconditionally in
    // drawActionTimelineOverview, which ran above.
    if (!filterEmpty) view.filterEmpty.textContent = t("timeline.overview.emptySelection");
  }
  syncActionTimelineInspector(view, force);
  const snapshot = options.prependSnapshot, pendingPrependRestore = view.pendingPrependRestore; view.pendingPrependRestore = null;
  if (snapshot && snapshot.node === view.scroll) {
    const delta = view.scroll.scrollHeight - snapshot.scrollHeight;
    view.scroll.scrollTop = snapshot.scrollTop + delta;
    view.followTail = !!snapshot.followTail && actionTimelineBottomDistance(view) <= ACTION_TIMELINE_BOTTOM_THRESHOLD;
  } else if (options.filterChanged || pendingPrependRestore) {
    const restore = options.filterRestore || pendingPrependRestore;
    if (restore && restore.followTail) view.scroll.scrollTop = Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight);
    else if (restore && (restore.entryKey || restore.groupId || restore.turnId)) {
      let index = entries.findIndex(entry => actionTimelineEntryKey(entry) === restore.entryKey);
      if (index < 0 && restore.groupId) index = actionTimelineEntryIndexForGroup(view, restore.groupId);
      if (index < 0 && restore.turnId) index = entries.findIndex(entry => entry.turnId === restore.turnId);
      const headerHeight = actionTimelineHeaderHeight(view);
      view.scroll.scrollTop = index >= 0 ? Math.max(0, headerHeight + index * ACTION_TIMELINE_ROW_HEIGHT + restore.offset) : 0;
    } else view.scroll.scrollTop = 0;
    view.followTail = !!(restore && restore.followTail); view.initialized = true;
  } else if (!view.initialized && entries.length && view.scroll.clientHeight > 0) {
    view.scroll.scrollTop = Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight);
    view.initialized = true; view.followTail = true;
  } else if (view.initialized && view.followTail && tailAdded) {
    view.scroll.scrollTop = Math.max(0, view.scroll.scrollHeight - view.scroll.clientHeight);
  } else if (view.initialized) {
    view.scroll.scrollTop = view.scrollTop;
  }
  view.scroll.scrollLeft = view.scrollLeft;
  // Only trust a live reading. A hidden pane has no scrolling box and reports
  // 0, which would overwrite the cache that is the sole record of where the
  // reader was.
  if (view.scroll.clientHeight > 0) { view.scrollTop = view.scroll.scrollTop; view.scrollLeft = view.scroll.scrollLeft; }
  reconcileActionTimelineWindow(view, force); syncActionTimelineHistoryState();
  if (!view.initialized && entries.length) scheduleActionTimelineWindow(view);
}
function recoveryIsCurrentBranch(actions) {
  if (!actions || !S.currentId) return false;
  const projectedBranch = publicText(S.branchState && S.branchState.branch_id, 96) || S.currentId;
  return actions.root_frame_id === S.currentId && actions.branch_id === projectedBranch;
}
async function executeRecoveryAction(actionId) {
  const projection = S.recoveryActions;
  const action = projection && (projection.actions || []).find(item => item.id === actionId);
  if (!RECOVERY_ACTION_IDS.includes(actionId) || !action || !action.enabled || !recoveryIsCurrentBranch(projection) || S._recoveryActionLoading) return;
  if (actionId === "restart_fresh" && !confirm(t("recovery.freshConfirm"))) return;
  const frameId = S.currentId;
  S._recoveryActionLoading = actionId; delete S.workbenchErrors.recoveryAction; renderActionTimeline();
  try {
    await api(`/frames/${frameId}/recovery/actions/${actionId}`, {
      method: "POST", body: JSON.stringify({ branch_id: projection.branch_id, confirm: actionId === "restart_fresh" })
    });
    await Promise.all([loadWorkbenchState(frameId, true), loadExecutionLog(frameId)]);
    if (S.currentId === frameId) hint(t("recovery.action.done"));
  } catch (error) {
    if (S.currentId === frameId) S.workbenchErrors.recoveryAction = publicText(error && error.message, 240);
  } finally {
    if (S.currentId === frameId && S._recoveryActionLoading === actionId) {
      S._recoveryActionLoading = null; renderActionTimeline();
    }
  }
}
function recoveryTimelineCard(state, actionsState) {
  const hasActionsProjection = !!actionsState;
  state = state || {}; actionsState = actionsState || sanitizeRecoveryActions({});
  const status = publicText(state.status || actionsState.state || "none", 32);
  const statusClass = String(status).toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  const card = el("article", "timeline-card kind-recovery status-" + statusClass); card.setAttribute("data-action-kind", "recovery");
  const head = el("div", "timeline-card-head"), kind = el("span", "timeline-kind"); kind.appendChild(iconEl("refresh", 14)); kind.appendChild(el("span", null, t("recovery.title"))); head.appendChild(kind); head.appendChild(el("span", "timeline-status", status)); card.appendChild(head);
  if (state.message) card.appendChild(el("div", "timeline-card-title", state.message));
  if (actionsState.checkpoint_id) card.appendChild(el("div", "recovery-checkpoint", t("recovery.checkpoint", shortRuntime(actionsState.checkpoint_id))));
  if (state.progress != null) { const track = el("div", "recovery-progress"); const bar = el("span"); bar.style.width = Math.round(state.progress * 100) + "%"; track.appendChild(bar); card.appendChild(track); }
  const currentBranch = recoveryIsCurrentBranch(actionsState), list = el("div", "recovery-action-list");
  (actionsState.actions || []).forEach(action => {
    const row = el("div", "recovery-action-row");
    const loading = S._recoveryActionLoading === action.id;
    const reason = !hasActionsProjection ? t("recovery.action.unavailable") : (!currentBranch ? t("recovery.action.currentOnly") : (action.reason || t("recovery.action.ready")));
    row.appendChild(disabledWorkbenchButton(loading ? t("recovery.action.loading") : t("recovery.action." + action.id), !!(currentBranch && action.enabled && !S._recoveryActionLoading), () => executeRecoveryAction(action.id), reason));
    row.appendChild(el("span", "recovery-action-reason", reason)); list.appendChild(row);
  });
  card.appendChild(list);
  if (S.workbenchErrors.recoveryAction) card.appendChild(el("div", "timeline-error", t("recovery.action.failed", S.workbenchErrors.recoveryAction)));
  (state.log || []).slice(-12).forEach(entry => { const row = el("div", "recovery-log-row"); row.appendChild(el("span", "timeline-pill", entry.status || "event")); row.appendChild(el("span", "recovery-log-message", entry.message || "")); card.appendChild(row); });
  return card;
}
function panelShell(title, className) {
  const panel = el("section", "workbench-panel " + className); panel.appendChild(el("div", "workbench-panel-title", title)); return panel;
}
function branchCapability(name) { return !!(S.branchState && S.branchState.capabilities && S.branchState.capabilities[name]); }
function branchCapabilityReason(name) { return publicText(S.branchState && S.branchState.capability_reasons && S.branchState.capability_reasons[name], 200); }
function disabledWorkbenchButton(label, enabled, action, disabledReason) {
  const button = el("button", "outline-btn small", label); button.disabled = !enabled; button.title = enabled ? label : (disabledReason || t("nb.action.unavailable")); if (enabled) button.onclick = action; return button;
}
async function createSessionCheckpoint() {
  if (!S.currentId || !branchCapability("checkpoint") || S._branchActionLoading) return;
  const frameId = S.currentId; S._branchActionLoading = "checkpoint"; delete S.workbenchErrors.branchAction; renderActionTimeline();
  try { await api(`/frames/${frameId}/branches/checkpoints`, { method: "POST", body: JSON.stringify({ branch_id: (S.branchState || {}).branch_id }) }); await loadWorkbenchState(frameId, true); }
  catch (error) { if (S.currentId === frameId) S.workbenchErrors.branchAction = publicText(error && error.message, 240); }
  finally { if (S.currentId === frameId) { S._branchActionLoading = null; renderActionTimeline(); } }
}
async function forkSessionCheckpoint(checkpointId) {
  if (!S.currentId || !branchCapability("fork") || !checkpointId || S._branchActionLoading) return;
  checkpointId = publicText(checkpointId, 96);
  const requestedName = prompt(t("branch.forkName"), t("branch.forkDefault", shortRuntime(checkpointId)));
  if (requestedName === null) return;
  const name = publicText(String(requestedName).trim(), 120), frameId = S.currentId;
  S._branchActionLoading = "fork:" + checkpointId; delete S.workbenchErrors.branchAction; renderActionTimeline();
  try {
    const body = { from_checkpoint_id: checkpointId }; if (name) body.name = name;
    await api(`/frames/${frameId}/branches/fork`, { method: "POST", body: JSON.stringify(body) });
    await loadWorkbenchState(frameId, true); if (S.currentId === frameId) hint(t("branch.forked", shortRuntime(checkpointId)));
  } catch (error) { if (S.currentId === frameId) S.workbenchErrors.branchAction = publicText(error && error.message, 240); }
  finally { if (S.currentId === frameId) { S._branchActionLoading = null; renderActionTimeline(); } }
}
async function activateSessionBranch(branchId) {
  branchId = publicText(branchId, 96);
  if (!S.currentId || !branchId || !branchCapability("activate") || S._branchActionLoading) return;
  const frameId = S.currentId; S._branchActionLoading = "activate:" + branchId; delete S.workbenchErrors.branchAction; renderActionTimeline();
  try {
    const result = await api(`/frames/${encodeURIComponent(frameId)}/branches/${encodeURIComponent(branchId)}/activate`, { method: "POST", body: "{}" });
    invalidateKernelCache(); S.cells = []; S.liveCells = []; S._liveCell = null; S.pendingReplIdentity = null;
    await openConversation(frameId, S.project);
    if (S.currentId === frameId) {
      const partial = String(result && result.status || "").toLowerCase() !== "active";
      hint(t(partial ? "branch.activatedPartial" : "branch.activated", shortRuntime(branchId)), partial);
    }
  } catch (error) { if (S.currentId === frameId) S.workbenchErrors.branchAction = publicText(error && error.message, 240); }
  finally { if (S.currentId === frameId) { S._branchActionLoading = null; renderActionTimeline(); renderNotebook(); } }
}
async function previewSessionRevert(checkpointId) {
  if (!S.currentId || !branchCapability("revert_preview") || S._branchActionLoading) return;
  const frameId = S.currentId; S._branchActionLoading = "preview:" + checkpointId; delete S.workbenchErrors.branchAction; renderActionTimeline();
  try {
    const preview = await api(`/frames/${frameId}/branches/revert-preview`, { method: "POST", body: JSON.stringify({ branch_id: (S.branchState || {}).branch_id, target_checkpoint_id: checkpointId }) });
    if (S.currentId === frameId && S.branchState) S.branchState.revert_preview = sanitizeRevertPreview(preview.preview || preview);
  } catch (error) { if (S.currentId === frameId) S.workbenchErrors.branchAction = publicText(error && error.message, 240); }
  finally { if (S.currentId === frameId) { S._branchActionLoading = null; renderActionTimeline(); } }
}
async function applySessionRevert() {
  const preview = S.branchState && S.branchState.revert_preview;
  if (!preview || !preview.can_apply || !branchCapability("revert") || S._branchActionLoading) return;
  const frameId = S.currentId; S._branchActionLoading = "revert"; delete S.workbenchErrors.branchAction; renderActionTimeline();
  try {
    const response = await api(`/frames/${frameId}/branches/revert`, { method: "POST", body: JSON.stringify({ branch_id: preview.branch_id, target_checkpoint_id: preview.target_checkpoint_id }) });
    const safe = sanitizeRevertMutationResult(response);
    const undo = safe.ok && safe.revert_checkpoint_id ? { branch_id: safe.branch_id || preview.branch_id, revert_checkpoint_id: safe.revert_checkpoint_id } : null;
    await openConversation(frameId, S.project);
    if (S.currentId === frameId && undo && undo.branch_id === (S.branchState || {}).branch_id) { S.branchUndo = undo; renderActionTimeline(); }
  } catch (error) { if (S.currentId === frameId) S.workbenchErrors.branchAction = publicText(error && error.message, 240); }
  finally { if (S.currentId === frameId) { S._branchActionLoading = null; renderActionTimeline(); } }
}
async function undoSessionRevert() {
  const undo = S.branchUndo;
  if (!S.currentId || !undo || undo.branch_id !== (S.branchState || {}).branch_id || !undo.revert_checkpoint_id || S._branchActionLoading) return;
  const frameId = S.currentId; S._branchActionLoading = "undo"; delete S.workbenchErrors.branchAction; renderActionTimeline();
  try {
    await api(`/frames/${frameId}/revert/undo`, { method: "POST", body: JSON.stringify({ branch_id: undo.branch_id, revert_checkpoint_id: undo.revert_checkpoint_id }) });
    S.branchUndo = null; await openConversation(frameId, S.project);
    if (S.currentId === frameId) hint(t("branch.undone"));
  } catch (error) { if (S.currentId === frameId) S.workbenchErrors.branchAction = publicText(error && error.message, 240); }
  finally { if (S.currentId === frameId) { S._branchActionLoading = null; renderActionTimeline(); } }
}
function renderBranchPanel() {
  const panel = panelShell(t("timeline.panel.branches"), "branch-panel"), state = S.branchState;
  const busy = !!S._branchActionLoading, controls = el("div", "workbench-controls");
  controls.appendChild(disabledWorkbenchButton(S._branchActionLoading === "checkpoint" ? t("common.loading") : t("branch.checkpoint"), !!(branchCapability("checkpoint") && !busy), createSessionCheckpoint, branchCapabilityReason("checkpoint")));
  if (S.branchUndo && S.branchUndo.branch_id === (state || {}).branch_id) controls.appendChild(disabledWorkbenchButton(S._branchActionLoading === "undo" ? t("common.loading") : t("branch.undo"), !busy, undoSessionRevert));
  panel.appendChild(controls);
  if (state && state.branch_id) panel.appendChild(el("div", "branch-current-summary", t("branch.currentSummary", shortRuntime(state.branch_id))));
  if (S.workbenchErrors.branchAction) panel.appendChild(el("div", "timeline-error", t("branch.actionFailed", S.workbenchErrors.branchAction)));
  if (!state || !(state.branches || []).length) { panel.appendChild(el("div", "workbench-empty", t("timeline.noBranch"))); return panel; }
  (state.branches || []).forEach(branch => {
    const row = el("div", "branch-row" + (branch.branch_id === state.branch_id ? " current" : ""));
    const head = el("div", "branch-head"); head.appendChild(el("span", "branch-name", branch.name || shortRuntime(branch.branch_id)));
    if (branch.branch_id === state.branch_id) head.appendChild(el("span", "timeline-pill", t("branch.current")));
    else {
      head.appendChild(el("span", "timeline-pill", t("branch.viewOnly")));
      head.appendChild(disabledWorkbenchButton(S._branchActionLoading === "activate:" + branch.branch_id ? t("branch.activating") : t("branch.activate"), !!(branch.activatable && branchCapability("activate") && !busy), () => activateSessionBranch(branch.branch_id), branchCapabilityReason("activate")));
    }
    if (branch.head_checkpoint_id) head.appendChild(el("span", "branch-head-id", t("branch.head", shortRuntime(branch.head_checkpoint_id)))); row.appendChild(head);
    const cps = el("div", "checkpoint-list"), allCheckpoints = branch.checkpoints || [];
    const checkpointRow = cp => {
      const cpRow = el("div", "checkpoint-row"); cpRow.appendChild(el("span", "checkpoint-id", shortRuntime(cp.checkpoint_id)));
      cpRow.appendChild(el("span", "checkpoint-reason", cp.reason || "checkpoint"));
      const actions = el("span", "checkpoint-actions");
      actions.appendChild(disabledWorkbenchButton(S._branchActionLoading === "fork:" + cp.checkpoint_id ? t("common.loading") : t("branch.fork"), !!(branchCapability("fork") && !busy), () => forkSessionCheckpoint(cp.checkpoint_id), branchCapabilityReason("fork")));
      actions.appendChild(disabledWorkbenchButton(S._branchActionLoading === "preview:" + cp.checkpoint_id ? t("common.loading") : t("branch.preview"), !!(branchCapability("revert_preview") && !busy), () => previewSessionRevert(cp.checkpoint_id), branchCapabilityReason("revert_preview")));
      cpRow.appendChild(actions); return cpRow;
    };
    allCheckpoints.filter(cp => !cp.internal).slice(0, 8).forEach(cp => cps.appendChild(checkpointRow(cp)));
    const internalCheckpoints = allCheckpoints.filter(cp => cp.internal);
    if (internalCheckpoints.length) {
      const collapsed = el("details", "internal-checkpoints"); collapsed.appendChild(el("summary", null, t("branch.internalCheckpoints", internalCheckpoints.length)));
      const internalList = el("div", "checkpoint-list"); internalCheckpoints.slice(0, 20).forEach(cp => internalList.appendChild(checkpointRow(cp))); collapsed.appendChild(internalList); cps.appendChild(collapsed);
    }
    row.appendChild(cps); panel.appendChild(row);
  });
  const preview = state.revert_preview;
  if (preview) {
    const box = el("div", "revert-preview"); box.appendChild(el("div", "revert-preview-title", t("branch.previewTitle") + " · " + shortRuntime(preview.target_checkpoint_id)));
    const arts = preview.artifacts || {}, ws = preview.workspace || {};
    box.appendChild(el("div", "revert-diff", t("branch.diff", (preview.messages || {}).delta || 0, (preview.notebook || {}).delta || 0, ws.writes_count || 0, ws.deletes_count || 0, arts.added_count || 0, arts.removed_count || 0)));
    if (ws.conflicts_count) box.appendChild(el("div", "timeline-error", t("branch.conflict")));
    box.appendChild(disabledWorkbenchButton(S._branchActionLoading === "revert" ? t("common.loading") : t("branch.revert"), !!(preview.can_apply && branchCapability("revert") && !busy), applySessionRevert, branchCapabilityReason("revert"))); panel.appendChild(box);
  }
  return panel;
}
function renderContextPanel() {
  const panel = panelShell(t("timeline.panel.context"), "context-panel"), state = S.contextState;
  if (!state || !(state.layers || []).length) { panel.appendChild(el("div", "workbench-empty", t("timeline.noContext"))); return panel; }
  const summary = el("div", "context-summary");
  if (state.token_count != null) summary.appendChild(el("span", "timeline-pill", t("context.tokens", state.token_count) + (state.token_limit ? " / " + state.token_limit : "")));
  if (state.output_reserve) summary.appendChild(el("span", "timeline-pill", t("context.outputReserve", state.output_reserve)));
  if (state.message_count != null) summary.appendChild(el("span", "timeline-pill", t("context.messages", state.message_count)));
  if (state.compressed) summary.appendChild(el("span", "timeline-pill", t("context.compressed")));
  if (state.handoff) summary.appendChild(el("span", "timeline-pill", t("context.handoff"))); panel.appendChild(summary);
  state.layers.forEach(layer => { const row = el("div", "context-layer"); row.appendChild(el("span", "context-layer-name", layer.name || layer.kind || "context")); if (layer.token_count != null) row.appendChild(el("span", "context-layer-tokens", t("context.tokens", layer.token_count))); if (layer.status) row.appendChild(el("span", "timeline-pill", layer.status)); panel.appendChild(row); });
  (state.omitted || []).forEach(item => {
    const row = el("div", "context-layer context-omitted");
    row.appendChild(el("span", "context-layer-name", tOptional("context.omitted." + item.kind) || item.kind));
    row.appendChild(el("span", "context-layer-tokens", t("context.omittedCount", item.count)));
    (item.reasons || []).forEach(r => row.appendChild(el("span", "timeline-pill", (tOptional("context.reason." + r.reason) || r.reason) + " ×" + r.count)));
    panel.appendChild(row);
  });
  if ((state.compaction_history || []).length) {
    const history = el("details", "context-history"); history.appendChild(el("summary", null, t("context.history", state.compaction_count || state.compaction_history.length)));
    state.compaction_history.forEach(item => {
      const row = el("div", "context-history-row");
      row.appendChild(el("span", "context-history-id", shortRuntime(item.archive_id) || t("context.compaction")));
      row.appendChild(el("span", "context-layer-tokens", t("context.savings", item.tokens_before, item.tokens_after)));
      if (item.message_count) row.appendChild(el("span", "timeline-pill", t("context.messages", item.message_count)));
      if (item.artifact_count) row.appendChild(el("span", "timeline-pill", t("context.artifacts", item.artifact_count)));
      history.appendChild(row);
    });
    panel.appendChild(history);
  }
  return panel;
}
function renderSecurityPanel() {
  const panel = panelShell(t("timeline.panel.security"), "security-panel"), state = S.securityState;
  if (!state) { panel.appendChild(el("div", "workbench-empty", t("timeline.noSecurity"))); return panel; }
  const sandbox = state.sandbox || {}, permission = state.permission || {};
  const row = (label, values, stateClass) => { const line = el("div", "security-row " + (stateClass || "")); line.appendChild(el("span", "security-label", label)); values.filter(Boolean).forEach(value => line.appendChild(el("span", "timeline-pill", value))); panel.appendChild(line); };
  row(t("security.sandbox"), [sandbox.state || sandbox.mode || "unknown", sandbox.backend, sandbox.enforced ? "enforced" : "not enforced"], sandbox.enforced ? "ok" : "warn");
  row(t("security.selfTest"), [sandbox.self_test_passed ? "passed" : "not passed"], sandbox.self_test_passed ? "ok" : "warn");
  row(t("security.network"), [sandbox.network_policy || "unknown"]);
  (sandbox.runtimes || []).filter(runtime => runtime.generation_ended).forEach(runtime => {
    row(t("security.generation"), [t("security.generationEnded", runtime.language || "kernel", runtime.generation_ended_reason || runtime.generation_state || "ended")]);
  });
  row(t("security.permission"), [permission.mode || "unknown", permission.pending_count ? t("security.pending", permission.pending_count) : ""]);
  if (sandbox.detail) panel.appendChild(el("div", "security-detail", sandbox.detail)); return panel;
}
function sanitizeComputeTasks(payload) {
  const source = payload && (payload.tasks ? payload : payload.payload || payload) || {};
  const tasks = Array.isArray(source.tasks) ? source.tasks : [];
  return {
    // `polled` is the server saying whether it contacted a provider. Rendered,
    // not inferred: a panel that decided for itself would be guessing about
    // the one thing the user needs to be able to trust here.
    polled: !!source.polled,
    live_count: Math.max(0, Number(source.live_count) || 0),
    tasks: tasks.slice(0, 200).map(task => ({
      job_id: publicText(task && task.job_id, 120),
      provider: publicText(task && task.provider, 64),
      status: publicText(task && task.status, 32) || "unknown",
      reason: publicText(task && (task.reason || task.termination_reason), 500),
      live: !!(task && task.live), terminal: !!(task && task.terminal),
      updated_at: Number(task && task.updated_at) || 0,
      outputs: {
        file_count: Math.max(0, Number(task && task.outputs && task.outputs.file_count) || 0),
        total_bytes: Math.max(0, Number(task && task.outputs && task.outputs.total_bytes) || 0)
      }
    }))
  };
}
async function refreshComputeTask(jobId, button) {
  // The only action here that reaches a provider — and it harvests, which is
  // why it is a button a person presses rather than something on a timer.
  const id = S.currentId; if (!id || !jobId) return;
  button.disabled = true;
  try {
    await api(`/frames/${id}/compute/tasks/${encodeURIComponent(jobId)}/refresh`, { method: "POST", body: "{}" });
    await loadWorkbenchState(id, true);
  } catch (e) {
    hint(t("compute.refreshFailed") + " — " + apiErrorText(e), true);
  } finally { button.disabled = false; }
}
function renderComputeTasksPanel() {
  const panel = panelShell(t("timeline.panel.compute"), "compute-panel"), state = S.computeTasks;
  if (!state || !(state.tasks || []).length) { panel.appendChild(el("div", "workbench-empty", t("compute.none"))); return panel; }
  const summary = el("div", "compute-summary");
  summary.appendChild(el("span", "timeline-pill", t("compute.live", state.live_count)));
  // Said out loud. A list of states with no provenance reads as current, and
  // these rows are as old as the last time anyone actually checked.
  summary.appendChild(el("span", "timeline-pill", state.polled ? t("compute.checked") : t("compute.fromRecord")));
  panel.appendChild(summary);
  state.tasks.forEach(task => {
    const row = el("div", "compute-task status-" + String(task.status).toLowerCase());
    const head = el("div", "compute-task-head");
    head.appendChild(el("span", "compute-task-id", shortRuntime(task.job_id) || task.job_id));
    head.appendChild(el("span", "timeline-status " + String(task.status).toLowerCase(), tOptional("compute.status." + task.status) || task.status));
    if (task.provider) head.appendChild(el("span", "timeline-pill", task.provider));
    row.appendChild(head);
    if (task.outputs.file_count) row.appendChild(el("div", "compute-task-outputs", t("compute.outputs", task.outputs.file_count, bytes(task.outputs.total_bytes))));
    if (task.reason) row.appendChild(el("div", "compute-task-message", task.reason));
    // Only a job that might still be out there is worth contacting a provider
    // about. A finished one has nothing left to harvest, and offering the
    // button anyway would invite a paid round trip that cannot change anything.
    if (task.live) {
      const btn = ghostIconBtn("refresh", t("compute.refresh"));
      btn.onclick = () => refreshComputeTask(task.job_id, btn);
      row.appendChild(btn);
    }
    panel.appendChild(row);
  });
  return panel;
}
async function stopDelegationChild(childId, button) {
  const id = S.currentId; if (!id || !childId) return;
  button.disabled = true;
  try {
    await api(`/frames/${id}/delegations/${encodeURIComponent(childId)}/stop`, { method: "POST", body: "{}" });
    await loadWorkbenchState(id, true);
  } catch (e) {
    // A 409 here is the ordinary post-restart answer, not a fault: the record
    // survived and the run that owned it did not. Show what the server said
    // rather than a generic failure, then re-read so the row stops offering
    // an action that cannot work.
    hint(t("delegation.stopFailed") + " — " + apiErrorText(e), true);
    await loadWorkbenchState(id, true);
  } finally { button.disabled = false; }
}
async function steerDelegationChild(childId, button) {
  const id = S.currentId; if (!id || !childId) return;
  const message = prompt(t("delegation.steerPrompt"));
  if (!message || !message.trim()) return;
  button.disabled = true;
  try {
    await api(`/frames/${id}/delegations/${encodeURIComponent(childId)}/steer`, {
      method: "POST", body: JSON.stringify({ message })
    });
    hint(t("delegation.steerQueued"));
    await loadWorkbenchState(id, true);
  } catch (e) {
    hint(t("delegation.steerFailed") + " — " + apiErrorText(e), true);
    await loadWorkbenchState(id, true);
  } finally { button.disabled = false; }
}
function renderDelegationPanel() {
  const panel = panelShell(t("timeline.panel.delegation"), "delegation-panel"), state = S.delegationState;
  if (!state || !(state.children || []).length) {
    panel.appendChild(el("div", "workbench-empty", t("timeline.noDelegation")));
    return panel;
  }
  const summary = el("div", "delegation-summary"), budget = state.budget || {};
  if (state.budget) summary.appendChild(el("span", "timeline-pill", t("delegation.budget", budget.spawned || 0, budget.limit || 0)));
  summary.appendChild(el("span", "timeline-pill", t("delegation.active", budget.active || (state.stats || {}).running || 0)));
  panel.appendChild(summary);
  (state.children || []).forEach(child => {
    const row = el("div", "delegation-child status-" + String(child.status || "unknown").toLowerCase());
    row.style.setProperty("--delegation-indent", Math.min(child.depth || 0, 4) * 10 + "px");
    const head = el("div", "delegation-child-head");
    head.appendChild(el("span", "delegation-child-name", child.name || shortRuntime(child.child_id)));
    head.appendChild(el("span", "timeline-status " + String(child.status || "unknown").toLowerCase(), child.status || "unknown"));
    row.appendChild(head);
    const details = el("div", "delegation-child-details");
    if (child.progress && child.progress.max_turns) details.appendChild(el("span", "timeline-pill", t("delegation.turns", child.progress.turn_boundary || 0, child.progress.max_turns)));
    if (child.overrides && child.overrides.model) details.appendChild(el("span", "timeline-pill", child.overrides.model));
    if (child.overrides && child.overrides.steps) details.appendChild(el("span", "timeline-pill", "steps " + child.overrides.steps));
    if (child.steering && (child.steering.queued || child.steering.delivered)) details.appendChild(el("span", "timeline-pill", t("delegation.steering", child.steering.queued || 0, child.steering.delivered || 0)));
    row.appendChild(details);
    if (child.error || child.stop_reason) row.appendChild(el("div", "delegation-child-message", child.error || child.stop_reason));
    // Only a child that is actually going can be stopped or steered. Offering
    // the controls on a finished one invites a 409 the user cannot act on,
    // and after a daemon restart every child here is finished.
    if (["running", "pending"].includes(String(child.status || "").toLowerCase())) {
      const controls = el("div", "delegation-child-controls");
      const stop = ghostIconBtn("stop", t("delegation.stop"));
      stop.onclick = () => stopDelegationChild(child.child_id, stop);
      controls.appendChild(stop);
      const steer = ghostIconBtn("message-square", t("delegation.steer"));
      steer.onclick = () => steerDelegationChild(child.child_id, steer);
      controls.appendChild(steer);
      row.appendChild(controls);
    }
    panel.appendChild(row);
  });
  return panel;
}
function syncActionTimelineHistoryState(host = null) {
  if (S._timelineView) syncActionTimelineOverviewControls(S._timelineView);
  const root = $("#dock-timeline");
  const target = host || (root && root.querySelector(".timeline-history-state"));
  if (!target) return;
  // Measure with the previous reservation still applied. Clearing it first
  // reads 0 on an already-reserved slot, which collapses the band during the
  // same frame as a prepend and moves the compensated row -- the exact jump
  // the reservation below exists to prevent. It does not leak: renderActionTimeline
  // builds a fresh .timeline-history-state node, so the reservation cannot
  // outlive the next full render.
  const previousHeight = target.getBoundingClientRect().height;
  const timeline = S.actionTimeline || {}; target.replaceChildren(); target.style.minHeight = "";
  if (timeline.has_more_before) {
    const controls = el("div", "workbench-controls timeline-history-controls");
    const loading = actionTimelineHistoryIsLoading(timeline);
    const earlier = el("button", "outline-btn small", t(loading ? "timeline.loadingEarlier" : "timeline.loadEarlier"));
    earlier.disabled = loading; earlier.setAttribute("data-action", "load-earlier-timeline");
    earlier.setAttribute("aria-busy", loading ? "true" : "false"); earlier.onclick = () => loadEarlierActionTimeline();
    controls.appendChild(earlier); target.appendChild(controls);
  }
  if (S.workbenchErrors.timelineHistory) target.appendChild(el("div", "timeline-error", t("timeline.loadEarlierFailed", S.workbenchErrors.timelineHistory)));
  // When the final prepend proves there is no earlier page, removing the
  // fallback control would move the whole scroll viewport during the same
  // frame. Reserve its measured slot until the next full render so the
  // compensated row remains at the same screen coordinate.
  const preserveEmptyHeight = !target.children.length && previousHeight > 0 && !!S._timelineView;
  if (preserveEmptyHeight) target.style.minHeight = previousHeight + "px";
  target.classList.toggle("hidden", !target.children.length && !preserveEmptyHeight);
}
function renderActionTimeline() {
  const root = $("#dock-timeline"); if (!root) return;
  const timeline = S.actionTimeline || {};
  const groups = sortedActionTimelineGroups(timeline);
  const rootFrameScope = actionTimelineRootScope(timeline), branchScope = actionTimelineBranchScope(timeline, groups);
  const previousView = S._timelineView;
  const activeRow = document.activeElement && document.activeElement.closest ? document.activeElement.closest(".timeline-ledger-row") : null;
  const restoreInspectorFocus = !!(previousView && previousView.inspectorHost.contains(document.activeElement));
  const restoreSearchFocus = !!(previousView && previousView.search && previousView.search.shell.contains(document.activeElement));
  const searchSelection = restoreSearchFocus ? [previousView.search.input.selectionStart, previousView.search.input.selectionEnd] : null;
  if (activeRow && actionTimelineViewMatches(previousView, rootFrameScope, branchScope)) {
    if (activeRow.dataset.groupId) S._timelineRestoreFocusGroupId = activeRow.dataset.groupId;
    else previousView.restoreFocusTurnId = activeRow.dataset.turnId || null;
  }
  if (actionTimelineViewMatches(previousView, rootFrameScope, branchScope)) {
    // Returning to the tab re-creates the scrolling box at offset 0, so the
    // live reading is 0 and only the cache still knows where the reader was.
    if (previousView.scroll.clientHeight > 0) {
      previousView.scrollTop = previousView.scroll.scrollTop; previousView.scrollLeft = previousView.scroll.scrollLeft;
    }
  } else {
    destroyActionTimelineView(previousView); S._timelineRestoreFocusGroupId = null;
  }
  if (S.actionTimelineSelectedBranchId !== branchScope || !groups.some(group => group.group_id === S.actionTimelineSelectedGroupId)) {
    S.actionTimelineSelectedGroupId = null; S.actionTimelineSelectedBranchId = null;
  }
  root.replaceChildren(); root.dataset.timelineBranch = branchScope;
  const top = el("div", "timeline-top"); const heading = el("div"); heading.appendChild(el("div", "timeline-title", t("timeline.title"))); heading.appendChild(el("div", "timeline-subtitle", t("timeline.subtitle"))); top.appendChild(heading);
  const refresh = ghostIconBtn("refresh", t("timeline.refresh")); refresh.onclick = () => loadWorkbenchState(S.currentId, true); top.appendChild(refresh); root.appendChild(top);
  root.appendChild(runtimeSummaryNode(false));
  const layout = el("div", "workbench-layout"), side = el("div", "workbench-side"), actions = el("section", "timeline-actions");
  side.appendChild(renderBranchPanel()); side.appendChild(renderDelegationPanel()); side.appendChild(renderComputeTasksPanel()); side.appendChild(renderContextPanel()); side.appendChild(renderSecurityPanel()); layout.appendChild(side);
  const historyState = el("div", "timeline-history-state hidden"); actions.appendChild(historyState); syncActionTimelineHistoryState(historyState);
  const hasRecovery = !!(S.recoveryActions || (S.recoveryState && (S.recoveryState.status || (S.recoveryState.log || []).length)));
  if (hasRecovery) actions.appendChild(recoveryTimelineCard(S.recoveryState, S.recoveryActions));
  if (groups.length) actions.appendChild(actionTimelineLedger(groups, branchScope, rootFrameScope));
  else {
    // root.replaceChildren() above detached the region, so any path that does
    // not re-append it must destroy the view. Destroying only on the fully
    // empty path stranded a detached tree that still held a document-level
    // keydown listener and a live ResizeObserver, and that
    // syncActionTimelineHistoryState kept writing into.
    destroyActionTimelineView();
    if (!timeline.has_more_before && !S.workbenchErrors.timelineHistory && !hasRecovery) {
      actions.appendChild(el("div", "workbench-empty timeline-empty", S._workbenchLoading ? t("timeline.loading") : t("timeline.empty")));
    }
  }
  layout.appendChild(actions); root.appendChild(layout);
  if (groups.length) updateActionTimelineLedger({ direction: "render" });
  else S._timelineRestoreFocusGroupId = null;
  if (restoreInspectorFocus && S._timelineView) {
    const close = S._timelineView.inspectorHost.querySelector("button");
    if (close) { try { close.focus({ preventScroll: true }); } catch { close.focus(); } }
  } else if (restoreSearchFocus && S._timelineView) {
    const input = S._timelineView.search.input; try { input.focus({ preventScroll: true }); } catch { input.focus(); }
    if (searchSelection && searchSelection[0] != null) try { input.setSelectionRange(searchSelection[0], searchSelection[1]); } catch {}
  }
}

/* ---------- WebSocket ---------- */
function connectWS() {
  const ws = new WebSocket((location.protocol === "https:" ? "wss:" : "ws:") + "//" + location.host + API + "/ws");
  S.ws = ws;
  ws.onopen = () => { conn(true); if (S.currentId) sub(S.currentId); };
  ws.onclose = () => { conn(false); setTimeout(connectWS, 1500); };
  ws.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch { return; }
    // Record the cursor only AFTER onEvent has applied it: advancing first
    // would let a handler that throws leave the client claiming an event it
    // never rendered, and the resume would then skip it for good.
    onEvent(m);
    const rid = m && m.root_frame_id, sq = m && m.seq;
    if (rid && typeof sq === "number" && sq > (S._seqSeen[rid] || 0)) S._seqSeen[rid] = sq;
  };
  clearInterval(connectWS._p); connectWS._p = setInterval(() => { try { ws.readyState === 1 && ws.send('{"type":"ping"}'); } catch {} }, 25000);
}
// Highest event seq actually applied, per frame. Sent back on (re)subscribe so
// the server replays only what was missed instead of the whole turn — the
// client would otherwise have to de-duplicate a stream it cannot tell apart.
S._seqSeen = S._seqSeen || {};
// The daemon run that issued our cursors. A cursor only means anything within
// the process that produced it, so it travels with the epoch and the server
// tells us (gap) when it cannot honour it.
S._streamEpoch = S._streamEpoch || null;
const sub = (f) => { try { S.ws && S.ws.readyState === 1 && S.ws.send(JSON.stringify({ type: "view_session", root_frame_id: f, since_seq: S._seqSeen[f] || 0, epoch: S._streamEpoch || undefined })); } catch {} };
const unsub = (f) => { try { S.ws && S.ws.readyState === 1 && f && S.ws.send(JSON.stringify({ type: "unview_session", root_frame_id: f })); } catch {} };
const conn = (on) => { const d = $("#conn-dot"); if (d) d.className = "dot " + (on ? "on" : "off"); };
function onEvent(m) {
  const fid = m.root_frame_id || m.frame_id;
  if (m.type === "replay_begin") {
    // A restarted daemon issues a new epoch; every cursor we hold describes a
    // stream it never produced, so drop them all rather than resuming from a
    // position it cannot interpret.
    if (m.epoch && m.epoch !== S._streamEpoch) { S._streamEpoch = m.epoch; S._seqSeen = {}; }
    if (mine(fid)) {
      if (S.stream && S.stream.wrap) S.stream.wrap.remove();
      S.stream = null; S.liveCells = []; S._liveCell = null;
      // `gap` means the server could not serve our cursor — the buffer had
      // aged past it, or it belonged to a previous run. Replaying from a hole
      // we cannot see would leave the transcript quietly wrong, so reload it.
      if (m.gap) { S._seqSeen[fid] = 0; S._replayGap = fid; }
    }
  }
  else if (m.type === "replay_end") {
    if (mine(fid)) {
      if (S._replayGap === fid) { S._replayGap = null; openConversation(fid, S.project); }
      down();
    }
  }
  else if (m.type === "artifact_ref_problems") {
    // A reference that did not resolve is *shown*. The old resolver dropped
    // one in silence, so a user could ask a question about a file the model
    // never received and had no way to notice. Not an error state for the
    // turn -- it still runs, and referencing four files with one typo should
    // answer about the other three.
    if (mine(fid)) renderRefProblems(m.problems || []);
  }
  else if (m.type === "attachment_problems") {
    if (mine(fid)) renderAttachmentProblems(m.problems || []);
  }
  // A late failure's prose is addressed to the turn that produced it. Without
  // this it wipes the running turn's stream and prints its predecessor's error
  // into it.
  else if (m.type === "text_reset") { if (mine(fid) && !isStaleTurnEvent(m)) startStream(); }
  else if (m.type === "notebook_cell_draft") { if (mine(fid)) nbCellDraft(m); }
  else if (m.type === "notebook_cell_start") { if (mine(fid)) nbCellStart(m); }
  else if (m.type === "notebook_cell_chunk") { if (mine(fid)) nbCellChunk(m); }
  else if (m.type === "notebook_cell_finished") { if (mine(fid)) { nbCellFinished(m); scheduleWorkbenchRefresh(); } }
  else if (m.type === "action_timeline" || m.type === "action-timeline") { if (mine(fid)) {
    const incoming = sanitizeActionTimeline(m), currentBranch = actionTimelineBranchScope(S.actionTimeline);
    // Branch activation keeps the root frame id, so a delayed delta from the
    // previous branch must not replace the newly active branch projection.
    if (!S.actionTimeline || !incoming.branch_id || !currentBranch || incoming.branch_id === currentBranch) {
      S.actionTimeline = mergeActionTimelines(S.actionTimeline, incoming, "latest");
      if (S.activeTab === "timeline") updateActionTimelineLedger({ direction: "latest" });
    }
  } }
  else if (m.type === "execution_queue") { if (mine(fid)) { rememberExecutionQueue(m); if (S.activeTab === "timeline") renderActionTimeline(); if (S.activeTab === "notebook") renderNotebook(); } }
  else if (m.type === "execution_state" || m.type === "execution_owner") { if (mine(fid)) {
    // State/owner events are deltas. Paint the safe owner immediately, then
    // refresh the authoritative FIFO snapshot so queue positions never drift.
    if (m.type === "execution_owner") {
      const current = S.executionQueue || sanitizeExecutionQueue({});
      current.owner = m.owner ? sanitizeExecutionQueue({ owner: { ...m, owner: m.owner } }).owner : null;
      S.executionQueue = current;
      if (m.owner && m.execution_id) rememberExecutionState({ ...m, status: "running" }); else S.executionIdentity = null;
    }
    else rememberExecutionState(m);
    scheduleWorkbenchRefresh(60); if (S.activeTab === "timeline") renderActionTimeline(); if (S.activeTab === "notebook") renderNotebook();
  } }
  else if (["recovery", "recovery_state", "recovery_log"].includes(m.type)) { if (mine(fid)) {
    const next = sanitizeRecovery(m), previous = S.recoveryState;
    if (previous && m.type === "recovery_log") S.recoveryState = { ...previous, ...Object.fromEntries(Object.entries(next).filter(([, value]) => value != null && value !== "")), log: (previous.log || []).concat(next.log || []).slice(-50) };
    else S.recoveryState = next;
    if (m.type === "recovery_state" || ["completed", "failed", "partial", "cancelled"].includes(String(m.status || m.state || "").toLowerCase())) scheduleWorkbenchRefresh(120);
    if (S.activeTab === "timeline") renderActionTimeline(); if (S.activeTab === "notebook") renderNotebook();
  } }
  else if (["branch", "branch_state", "branch_activation_state", "branch_projection_restored", "checkpoint", "checkpoint_created", "branch_created", "branch_reverted", "branch_revert_conflict"].includes(m.type)) { if (mine(fid)) {
    if (m.type === "branch_projection_restored" || (m.type === "branch_activation_state" && m.branch_id)) { scheduleBranchConversationResync(fid); return; }
    if (m.type === "branch_reverted" && m.ok === true && publicText(m.branch_id, 96) === publicText(S.branchState && S.branchState.branch_id, 96) && publicText(m.checkpoint_id, 96)) S.branchUndo = { branch_id: publicText(m.branch_id, 96), revert_checkpoint_id: publicText(m.checkpoint_id, 96) };
    if (m.branches || (m.payload && m.payload.branches)) { S.branchState = sanitizeBranches(m); S.branchUndo = branchUndoFromProjection(S.branchState); }
    else scheduleWorkbenchRefresh(m.type === "branch_activation_state" ? 0 : 80);
    if (S.activeTab === "timeline") renderActionTimeline(); if (S.activeTab === "notebook") renderNotebook();
  } }
  else if (["delegation_child_event", "delegation_state", "delegation_progress", "delegation_steering"].includes(m.type)) { if (mine(fid)) {
    scheduleWorkbenchRefresh(60); if (S.activeTab === "timeline") renderActionTimeline();
  } }
  else if (["sandbox", "sandbox_status", "security_status"].includes(m.type)) { if (mine(fid)) { S.securityState = sanitizeSecurity(m); if (S.activeTab === "timeline") renderActionTimeline(); } }
  else if (m.type === "text_chunk") { if (mine(fid) && !isStaleTurnEvent(m)) feed(
    // A persist-first gated turn can be reopened while its Reviewer is still
    // running. REST has already rendered the canonical candidate in that case,
    // and replaying the same provisional bytes below it creates a second answer.
    // Shared turn/execution identity lets the durable row own those chunks.
    m.block_type || "text", m.chunk || "", m, storedCandidateOwnsChunk(m)
  ); }
  else if (m.type === "step") { if (mine(fid)) addLiveStep(m); }
  else if (m.type === "step_update") { if (mine(fid)) updateLiveStep(m); }
  else if (m.type === "plan_ready") { if (mine(fid)) renderPlanCard(m.plan, m.status); }
  else if (m.type === "plan_progress") { if (mine(fid)) updatePlanProgress(m); }
  else if (m.type === "await_permission") { if (mine(fid)) { renderPermissionCard(m); scheduleWorkbenchRefresh(); } }
  else if (m.type === "permission_resolved") { if (mine(fid)) { resolvePermissionCard(m); scheduleWorkbenchRefresh(); } }
  else if (m.type === "candidate_ready" && m.gates_completion) {
    if (mine(fid) || mine(m.root_frame_id)) markCandidateReady(m);
  }
  else if (m.type === "auto_run_terminal") {
    // This closes the durable audit run, not the answer delivery. Keep the
    // Timeline fresh, but wait for candidate_resolved (or the final frame
    // receipt) before changing the badge on user-visible prose.
    if (mine(fid) || mine(m.root_frame_id)) scheduleWorkbenchRefresh(60);
  }
  else if (m.type === "candidate_resolved") {
    if (mine(fid) || mine(m.root_frame_id)) applyCandidateResolution(m, fid);
  }
  else if (m.type === "frame_update") {
    if (mine(m.frame_id) || mine(fid)) {
      // Unconditional, and deliberately outside the `!S.running` guard below:
      // when a queued follow-up starts, `S.running` is already true from the
      // turn that just ended, and that is precisely the hand-off this exists
      // for.
      if (m.status === "processing") activateTurnTicket(m.request_id, m.execution_id);
      if (m.status === "processing" && !S.running) { S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); resumeWatch(fid, S._openGen); }  // a turn observed on the WS (e.g. started from another tab) — watchdog covers a missed terminal event
      if (["completed","failed","cancelled","success","done","ready"].includes(m.status)) {
        // A terminal event for a turn that is no longer on screen may not
        // close the one that is: no hint, no teardown, no ticket cleared. The
        // workbench still refreshes, because the artifacts and cells that turn
        // produced are real.
        if (isStaleTurnEvent(m)) scheduleWorkbenchRefresh();
        else { if (m.review_status) applyFinalReviewStatus(m, fid); handleEnvironmentReadinessTerminal(m); turnDone(m.status, m); scheduleWorkbenchRefresh(); }
      }
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
    if (aid) syncArtifactVersion(art, true);
    if (aid) {
      (S._artBust = S._artBust || {})[aid] = art.version_id || String(Date.now());
      if (S.dockArtifact && S.dockArtifact.id === aid && S.activeTab === aid) {
        // Capture emits before the execution log is persisted. Render the
        // invalidated/loading provenance state now; loadExecutionLog() fetches
        // the complete cell+lineage payload after the cell transaction.
        renderViewer();
      }
    }
    const fn = art.filename || "";
    // An overwritten file may reuse the same cache key (fallback URL) — drop just
    // its cached inline table so a re-run cell re-reads the new bytes.
    if (S._tbl && fn) { const base = fn.split("/").pop(); for (const k in S._tbl) if (k.indexOf(base) !== -1) delete S._tbl[k]; }
    // Live-render a produced figure onto the current notebook cell, so images
    // show up as the agent makes them (not only after the whole turn ends).
    const isImg = /^image\//.test(art.content_type || "") || /\.(png|jpe?g|gif|svg|webp|bmp)$/i.test(fn);
    if (S.running && fn && isImg) {
      const producer = art.producing_cell_id || m.producing_cell_id;
      const cell = (producer && nbFindCell(producer)) || S._liveCell || (S.liveCells && S.liveCells[S.liveCells.length - 1]);
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
    if (m.sandbox) S.securityState = sanitizeSecurity({ sandbox: m.sandbox });
    scheduleWorkbenchRefresh();
    if (S.dock.open && S.activeTab === "notebook") renderNotebook();
  } }
}
const mine = (f) => f && S.currentId && f === S.currentId;

/* ---------- streaming ---------- */
const LIVE_OUTPUT_CHAR_CAP = 1000000;
const LIVE_OUTPUT_TRUNCATION = "\n...(live output truncated)";
function appendLiveOutput(current, chunk) {
  const existing = String(current || ""), addition = String(chunk || "");
  if (existing.includes(LIVE_OUTPUT_TRUNCATION)) return existing;
  if (existing.length >= LIVE_OUTPUT_CHAR_CAP) return existing.slice(0, LIVE_OUTPUT_CHAR_CAP) + LIVE_OUTPUT_TRUNCATION;
  const remaining = LIVE_OUTPUT_CHAR_CAP - existing.length;
  return addition.length > remaining
    ? existing + addition.slice(0, remaining) + LIVE_OUTPUT_TRUNCATION
    : existing + addition;
}
// Batch markdown re-renders onto animation frames: a fast token stream would
// otherwise reparse the whole message on every chunk (janky, and it makes the
// caret strobe as the subtree is torn down each token).
// Streaming markdown: re-parse only the unstable tail once the sealed prefix
// is long enough. Full re-parse of multi-kB streams every frame is the main
// main-thread cost during long agent turns.
function _mdStableCut(text) {
  // Seal only top-level blank lines or completed fences. A blank line inside
  // code is not a stable Markdown boundary, and an opening fence must never be
  // mistaken for a closing one. Keep the final ~120 chars soft for streaming.
  const limit = Math.max(0, text.length - 120);
  if (limit < 80) return 0;
  const lines = text.split("\n");
  let offset = 0, stable = 0, openFence = null;
  for (let i = 0; i < lines.length - 1; i++) {
    const line = lines[i];
    const boundary = offset + line.length + 1;
    if (boundary > limit) break;
    if (openFence) {
      const trimmed = line.trim();
      const closes = trimmed.length >= openFence.length && [...trimmed].every(ch => ch === openFence.char);
      if (closes) { openFence = null; if (boundary >= 60) stable = boundary; }
    } else {
      const match = line.match(/^\s*(`{3,}|~{3,})[ \t]*[\w+#.\-]*[ \t]*$/);
      if (match) openFence = { char: match[1][0], length: match[1].length };
      else if (!line.trim() && boundary >= 60) stable = boundary;
    }
    offset = boundary;
  }
  return stable;
}
function flushRender(st, finalRender) {
  if (!st) return;
  if (st._raf) { cancelAnimationFrame(st._raf); st._raf = null; }
  if (!st.md || (!st._dirty && !finalRender)) return;
  st._dirty = false;
  const text = st.text || "";
  st._lastFlush = performance.now();
  if (finalRender) {
    st.md.innerHTML = renderMd(text);
    st._stableAt = 0; st._stableHtml = "";
    return;
  }
  // Grow the sealed prefix when a stable boundary appears further along.
  const cut = _mdStableCut(text);
  if (cut > (st._stableAt || 0) + 40) {
    st._stableAt = cut;
    st._stableHtml = renderMd(text.slice(0, cut));
  }
  if (st._stableAt && st._stableHtml && text.length > st._stableAt) {
    st.md.innerHTML = st._stableHtml + renderMd(text.slice(st._stableAt));
  } else {
    st.md.innerHTML = renderMd(text);
  }
}
function scheduleRender(st) {
  st._dirty = true;
  if (st._raf) return;
  st._raf = requestAnimationFrame(() => {
    st._raf = null;
    // Long streams: cap MD reparse to ~20/s (rAF still coalesces tokens; we only
    // skip the expensive innerHTML when the previous flush was very recent).
    const now = performance.now();
    if (st.text && st.text.length > 600 && st._lastFlush && (now - st._lastFlush) < 48) {
      st._raf = requestAnimationFrame(() => { st._raf = null; flushRender(st); down(); });
      return;
    }
    flushRender(st); down();
  });
}
// Freeze the current text block: flush any pending render and drop its blinking
// caret. Called whenever the stream moves on to non-text content (a tool card, a
// step) so the caret never lingers on an already-finished paragraph.
function sealText(st) {
  if (!st || !st.md) return;
  flushRender(st, true);
  st.md.classList.remove("cursor");
  // Next text block starts fresh (don't carry sealed prefix across tool cards).
  st._stableAt = 0; st._stableHtml = "";
}
function startStream() {
  const g = $(".generated"); if (g) g.remove();
  const es = $(".empty-session"); if (es) es.remove();  // clear starter card on any (resumed) stream
  const wrap = el("div", "msg assistant");
  const md = el("div", "md cursor"); wrap.appendChild(md);
  $("#messages").appendChild(wrap); S.stream = { wrap, md, text: "", full: "", toolPre: null, toolCard: null, _stableAt: 0, _stableHtml: "" };
  S.stepEls = {};
  S.liveCells = []; S._liveCell = null; down();
}
const ensure = () => { if (!S.stream) startStream(); return S.stream; };
function feed(kind, chunk, event, storedOwnsChunk = false) {
  if (storedOwnsChunk) return;
  const st = ensure();
  rememberCandidateIdentity(st.wrap, event);
  const structuredCellId = event && (event.producing_cell_id || event.cell_id);
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
      st._stableAt = 0; st._stableHtml = ""; st._lastFlush = 0;
      // Structured notebook_cell_* events own Notebook state on new daemons.
      // Keep sentinel parsing only as a compatibility fallback for old replays.
      if (!suba && !structuredCellId) nbLiveStart(tool, raw, event && event.kernel_id, event && event.cell_index, event && event.language);
    } else if (st.toolPre) {
      const add = chunk.replace(/^↳\s*/, "");
      st.toolPre.textContent = appendLiveOutput(st.toolPre.textContent, add);
      if (st.toolMeta) { const n = (st.toolPre.textContent.match(/\n/g) || []).length; st.toolMeta.textContent = n > 1 ? (n + (n === 1 ? " line" : " lines")) : "done"; }
      if (!structuredCellId) nbLiveAppend(add);
    }
  } else {
    st.text += chunk; st.full += chunk; st.md.classList.add("cursor");
    // Stage 4: this chunk is a candidate, not the answer. It is readable and
    // copyable for the whole reviewer round-trip, so it has to say so on the
    // block itself -- a badge that only appears after the verdict would leave
    // the most dangerous window, the one before it, unlabelled.
    if (event && (event.provisional || event.review_status === "candidate")) {
      setLiveReviewBadge("candidate");
    }
    scheduleRender(st); return;
  }
  down();
}
function candidateIdentityText(value) {
  return value == null ? "" : String(value).trim().slice(0, 192);
}
// The REST row and the WS receipt use the public top-level fields. The nested
// fallbacks keep this additive for an older captured response without exposing
// arbitrary message metadata to selectors.
function candidateIdentity(value) {
  const raw = value && typeof value === "object" ? value : {};
  const review = raw.review_status && typeof raw.review_status === "object" ? raw.review_status : {};
  const meta = raw.metadata && typeof raw.metadata === "object" ? raw.metadata : {};
  return {
    messageId: candidateIdentityText(raw.message_id || raw.candidate_message_id || raw.replacement_message_id || review.message_id || meta.message_id),
    turnId: candidateIdentityText(raw.turn_id || raw.candidate_turn_id || review.turn_id || meta.turn_id),
    executionId: candidateIdentityText(raw.execution_id || review.execution_id || meta.execution_id),
  };
}
function rememberCandidateIdentity(node, value) {
  const identity = candidateIdentity(value);
  if (!node || !node.dataset) return identity;
  const bind = (key, next) => { if (next && (!node.dataset[key] || node.dataset[key] === next)) node.dataset[key] = next; };
  bind("messageId", identity.messageId); bind("turnId", identity.turnId); bind("executionId", identity.executionId);
  return identity;
}
function candidateNodeMatches(node, identity) {
  if (!node || !node.dataset || !identity) return false;
  if (identity.messageId) return node.dataset.messageId === identity.messageId;
  if (identity.turnId) return node.dataset.turnId === identity.turnId && (!identity.executionId || !node.dataset.executionId || node.dataset.executionId === identity.executionId);
  return !!identity.executionId && node.dataset.executionId === identity.executionId;
}
// Once the server supplies a durable message id, only that exact row may be
// changed. Turn/execution identity exists solely for pre-promotion stream and
// replay de-duplication; it is never a replacement target fallback.
function candidateMessageNode(value) {
  const identity = candidateIdentity(value);
  const host = $("#messages");
  const nodes = host ? Array.from(host.querySelectorAll(".msg.assistant")) : [];
  if (identity.messageId) {
    return nodes.find(node => node.dataset && node.dataset.messageId === identity.messageId) || null;
  }
  return nodes.find(node => candidateNodeMatches(node, identity)) || null;
}
function reviewStatusFrom(value) {
  const review = value && value.review_status;
  return candidateIdentityText(review && typeof review === "object" ? review.status : review);
}
function reviewTruthFrom(value) {
  const review = value && value.review_status;
  return candidateIdentityText((value && value.user_truth) || (review && typeof review === "object" ? review.user_truth : ""));
}
function setMessageReviewBadge(node, status, userTruth) {
  if (!node || !status) return false;
  let badge = node.querySelector(":scope > .review-badge");
  if (!badge) {
    badge = el("div", "review-badge");
    const actions = node.querySelector(":scope > .msg-actions");
    if (actions) node.insertBefore(badge, actions); else node.appendChild(badge);
  }
  badge.className = "review-badge review-badge-" + String(status).replace(/[^a-z_]/g, "");
  badge.textContent = userTruth || t("review.badge." + status);
  if (node.dataset) node.dataset.reviewStatus = status;
  S.reviewGate = { status, user_truth: badge.textContent };
  return true;
}
// The live counterpart of the badge `renderStored` puts on a reopened message.
// One node, replaced in place, so candidate -> verified never stacks two.
function setLiveReviewBadge(status, userTruth) {
  const st = S.stream; return !!(st && st.wrap && setMessageReviewBadge(st.wrap, status, userTruth));
}
function markCandidateReady(value) {
  const target = candidateMessageNode(value);
  if (target) {
    rememberCandidateIdentity(target, value);
    // A stale replay must never demote a row REST already projected as final.
    if (!target.dataset.reviewStatus || target.dataset.reviewStatus === "candidate") setMessageReviewBadge(target, "candidate", value.user_truth);
    return true;
  }
  if (S.stream && S.stream.wrap) rememberCandidateIdentity(S.stream.wrap, value);
  return setLiveReviewBadge("candidate", value.user_truth);
}
function discardDuplicateLiveCandidate(stored, value) {
  const live = S.stream && S.stream.wrap;
  if (!stored || !live || live === stored) return;
  const identity = candidateIdentity(value);
  rememberCandidateIdentity(live, { turn_id: identity.turnId, execution_id: identity.executionId });
  if (!candidateNodeMatches(live, { ...identity, messageId: "" })) return;
  live.remove(); S.stream = null;
}
function storedCandidateOwnsChunk(value) {
  if (!value || (value.block_type || "text") !== "text" || !(value.provisional || reviewStatusFrom(value) === "candidate")) return false;
  const target = candidateMessageNode(value);
  if (!target || (S.stream && target === S.stream.wrap) || !target.dataset || !target.dataset.messageId) return false;
  discardDuplicateLiveCandidate(target, value);
  if (!target.dataset.reviewStatus || target.dataset.reviewStatus === "candidate") setMessageReviewBadge(target, "candidate", value.user_truth);
  return true;
}
function candidateReplacementText(value) {
  if (value && typeof value.text === "string") return value.text;
  return value && typeof value.final_answer === "string" ? value.final_answer : "";
}
function candidateReplacementCommitted(value) {
  const identity = candidateIdentity(value), text = candidateReplacementText(value);
  return !!(value && value.replaced === true && value.delivered === true && value.durable === true && identity.messageId && text && (value.persisted == null || value.persisted === true) && (value.promotion_committed == null || value.promotion_committed === true));
}
// Replace one identified answer, whether it is the current live wrapper or a
// canonical assistant row already rendered from REST. Message actions capture
// their text in closures, so refresh them as part of the replacement too.
function replaceMessageAnswer(node, text) {
  if (!node || !node.classList || !node.classList.contains("assistant") || !String(text || "").trim()) return false;
  const hadActions = !!node.querySelector(":scope > .msg-actions");
  node.querySelectorAll(":scope > .md").forEach(item => item.remove());
  const md = el("div", "md"); md.innerHTML = renderMd(text);
  if (S.stream && S.stream.wrap === node) {
    const badge = node.querySelector(":scope > .review-badge");
    if (badge) node.insertBefore(md, badge); else node.appendChild(md);
    S.stream.md = md; S.stream.text = text; S.stream.full = text;
    S.stream._stableAt = 0; S.stream._stableHtml = "";
  } else if (node.firstChild) node.insertBefore(md, node.firstChild);
  else node.appendChild(md);
  node._messageText = text;
  if (hadActions) { const actions = node.querySelector(":scope > .msg-actions"); if (actions) actions.remove(); addMsgActions(node, text); }
  return true;
}
// Compatibility seam for callers/tests that only have the current stream.
function replaceLiveAnswer(text) {
  const st = S.stream; return !!(st && st.wrap && replaceMessageAnswer(st.wrap, text));
}
function applyCandidateResolution(value, fid) {
  const target = candidateMessageNode(value), status = reviewStatusFrom(value), truth = reviewTruthFrom(value);
  const replacementWanted = !!(value && value.replaced === true);
  let replacementApplied = false;
  if (target) { discardDuplicateLiveCandidate(target, value); rememberCandidateIdentity(target, value); }
  if (replacementWanted && candidateReplacementCommitted(value) && target) {
    replacementApplied = replaceMessageAnswer(target, candidateReplacementText(value));
    if (replacementApplied && target.dataset) target.dataset.candidateResolved = "true";
  }
  if (replacementWanted && !replacementApplied) scheduleConversationResync(fid);
  // Never put Verified on bytes that an advertised replacement failed to reach,
  // or on any answer the receipt itself says was not delivered.
  const mayApplyBadge = status && (status !== "verified" || (value.delivered === true && value.durable === true && (!replacementWanted || replacementApplied)));
  if (mayApplyBadge && target) {
    setMessageReviewBadge(target, status, truth);
    if (value.delivered === true && value.durable === true && target.dataset) target.dataset.candidateResolved = "true";
  } else if (status && !target) scheduleConversationResync(fid);
  else if (status === "verified" && !mayApplyBadge) scheduleConversationResync(fid);
  return { targetFound: !!target, replacementApplied, badgeApplied: !!(mayApplyBadge && target) };
}
function applyFinalReviewStatus(value, fid) {
  const status = reviewStatusFrom(value); if (!status) return false;
  if (value && value.replaced === true) return applyCandidateResolution(value, fid).badgeApplied;
  const target = candidateMessageNode(value);
  // A previously applied candidate_resolved receipt is sufficient. Otherwise a
  // Verified terminal must itself say the durable answer was delivered.
  const mayVerify = status !== "verified" || (value.delivered === true && value.durable === true) || !!(target && target.dataset && target.dataset.candidateResolved === "true");
  if (target && mayVerify) return setMessageReviewBadge(target, status, reviewTruthFrom(value));
  scheduleConversationResync(fid); return false;
}
// A turn's request ticket, guarded by a generation.
//
// The job runs on its own thread and can fail before the handler has even
// returned the 202. So the terminal WS event -- and `turnDone` with it -- can
// arrive *first*, clear the ticket, and then `send`'s POST promise resolves
// and writes the finished turn's id back into the slot. The next turn then
// quotes a support id belonging to the previous one, which is worse than
// showing none: it sends an operator to the wrong request.
//
// `openTurnTicket` takes the generation before the POST; `closeTurnTicket`
// invalidates it (turn end, session switch); `commitTurnTicket` writes only if
// the generation is still the one it took AND the turn is still running.
function openTurnTicket() {
  S.turnTicket = (S.turnTicket || 0) + 1;
  return S.turnTicket;
}
function commitTurnTicket(token, accepted) {
  if (!accepted || !accepted.request_id) return false;
  if (token !== S.turnTicket) return false;   // a newer turn owns the slot
  if (!S.running) return false;               // this turn already ended
  S.pendingRequestId = String(accepted.request_id);
  // The 202 names the execution too, and it is the id the stale filter
  // actually compares -- a reused request id cannot tell two turns apart.
  if (accepted.execution_id) S.pendingExecutionId = String(accepted.execution_id);
  return true;
}
// A turn has started running server-side: it owns the slot from now on.
//
// This is the hand-off a queued follow-up depends on. Its 202 resolved while
// the previous turn still owned the screen, so it could not take the slot then
// -- and if it had, the earlier turn's own failure would have quoted the
// follow-up's id. The `processing` event is the first moment the id is
// current, and it carries it for exactly this reason.
//
// Bumping the generation here is what makes a late 202 from ANY earlier turn
// unable to write: it is stale by definition once another turn is running.
// Whether the server says this submission was QUEUED, whatever the client
// believed when it started.
//
// `queueing` is a snapshot taken at the top of `send`, and several awaits run
// before the POST -- the skills catalogue, sometimes creating the frame. In
// that window another tab, or a recovered turn, can take ownership, so a send
// that began idle can be answered with `queue_position: 1`. The 202 is the
// authoritative answer and the local snapshot is only a hint.
// Does THIS send still own the UI's current turn?
//
// `queueing`, read at the top of `send`, is a snapshot: several awaits follow
// before the POST, and another tab -- or a recovered turn -- can change who
// owns the session in that window. Deciding anything later from that snapshot
// is how a rejected follow-up tore down a turn that had started meanwhile.
// The provisional token is the honest question: it exists only for a send that
// began as the active turn, and it stops matching the moment any other turn is
// activated.
function ownsTurnTicket(token) {
  return token != null && token === S.turnTicket;
}
// Claim the slot for this submission, if the server says it is the one running.
//
// `queue_position === 0` is the ONLY proof of that. Greater than zero is
// queued behind someone else; absent means the snapshot could not be taken --
// typically a job that finished before it was read -- and an unknown is not a
// yes. Neither may write, or the running turn's failure quotes the wrong id.
function acceptTurnTicket(token, accepted) {
  // Ownership is `commitTurnTicket`'s question and it already asks it; asking
  // again here would be a branch no test can reach, which reads as care and is
  // decoration. What this adds is the server's answer.
  if (!accepted || !accepted.request_id) return false;
  if (accepted.queue_position !== 0) return false;
  return commitTurnTicket(token, accepted);
}
// This send is not the running turn after all: kill its provisional ticket so
// a later resolution cannot claim the slot with it. Deliberately leaves
// `pendingRequestId` alone -- if we no longer own the generation, whatever is
// in there belongs to somebody else's turn.
// Is this event the tail of a turn that is no longer the one on screen?
//
// The ordering is real and reproducible: `processing(A)`, `processing(B)`,
// then `failed(A)` -- A fails inside the turn, persists its row, and only
// finishes unwinding after B has been promoted out of the queue. Acting on
// A's terminal there closes B's turn, unlocks the composer under a running
// turn, and prints A's error into B's transcript.
//
// Filtered on the EXECUTION, not the request: a client may reuse
// `X-Request-Id`, so A and B can legitimately share one. Request id is the
// fallback for a daemon old enough not to send an execution id, and when
// neither side offers any identity at all the event is treated as current --
// the pre-identity behaviour, which is the only safe default for a client
// talking to an older server.
function isStaleTurnEvent(event) {
  const incomingExec = (event && event.execution_id) || "";
  if (incomingExec && S.pendingExecutionId) return incomingExec !== S.pendingExecutionId;
  if (incomingExec || S.pendingExecutionId) return false;   // one side is silent
  const incomingReq = (event && event.request_id) || "";
  if (incomingReq && S.pendingRequestId) return incomingReq !== S.pendingRequestId;
  return false;
}
function retireTurnTicket(token) {
  if (!ownsTurnTicket(token)) return false;
  S.turnTicket = (S.turnTicket || 0) + 1;
  return true;
}
function activateTurnTicket(requestId, executionId) {
  // The generation always advances: another turn is running now, so every
  // ticket in flight is stale whether or not this event named itself.
  S.turnTicket = (S.turnTicket || 0) + 1;
  // The identities are only *overwritten* by an event that carries them. An
  // older daemon sends `processing` with neither, and clearing on that would
  // throw away the ids the 202 had already given us -- leaving the running
  // turn's own failure with nothing to quote and nothing to filter on.
  if (requestId) S.pendingRequestId = String(requestId).slice(0, 96);
  if (executionId) S.pendingExecutionId = String(executionId).slice(0, 96);
  return S.turnTicket;
}
function closeTurnTicket() {
  S.turnTicket = (S.turnTicket || 0) + 1;
  S.pendingRequestId = null;
  S.pendingExecutionId = null;
}
// The sentence a failed turn shows, from whichever source has the facts.
//
// Two of them exist and they have to agree: the `frame_update` a live client
// receives, and the `failure` metadata `GET /frames/{id}/messages` projects
// when the same client reopens the session later. Before this, reopening lost
// both the support id and the retry veto -- the socket event was gone and the
// stored row was a sentence, so the user most likely to need them (the one who
// closed the tab on a failure) was the one who could not get them.
// The stored failure, shown on its own message. Text only -- these three
// fields are what the projector already published, and nothing here is derived
// from the exception.
// The failure on the LAST message, or null if the transcript does not end in
// one. Read from the DOM the render just produced rather than from a second
// fetch, so the two cannot disagree about what the newest message is.
function lastTerminalFailure() {
  const rows = [...document.querySelectorAll("#messages .msg")];
  const last = rows[rows.length - 1];
  if (!last) return null;
  const box = last.querySelector(".msg-failure-meta");
  return box ? { request_id: box.dataset.requestId || "", code: box.dataset.failureCode || "", output_committed: box.dataset.committed === "1" } : null;
}
function failureCodeHint(code) {
  const key = ({
    llm_request_burst: "turn.failure.llmRequestBurst",
    llm_rate_limited: "turn.failure.llmRateLimited",
    llm_upstream_overloaded: "turn.failure.llmUpstreamOverloaded",
  })[String(code || "")];
  return key ? t(key) : "";
}
function failureMeta(failure) {
  const box = el("div", "msg-failure-meta");
  const bits = [];
  const cause = failureCodeHint(failure.code);
  if (cause) bits.push(cause);
  if (failure.output_committed) bits.push(t("turn.failedCommitted"));
  if (failure.request_id) bits.push(t("turn.supportId", String(failure.request_id).slice(0, 96)));
  box.textContent = bits.join(" ");
  box.dataset.requestId = failure.request_id ? String(failure.request_id).slice(0, 96) : "";
  box.dataset.failureCode = failure.code ? String(failure.code).slice(0, 64) : "";
  if (failure.output_committed) box.dataset.committed = "1";
  return box;
}
function failureHint(detail) {
  const committed = !!(detail && detail.output_committed);
  const cause = failureCodeHint(detail && detail.code);
  const base = committed
    ? [t("turn.failedCommitted"), cause].filter(Boolean).join(" ")
    : (cause || t("turn.failed"));
  const raw = (detail && detail.request_id) || S.pendingRequestId || "";
  const id = raw ? String(raw).slice(0, 96) : "";
  return id ? base + " " + t("turn.supportId", id) : base;
}
function turnDone(status, detail) {
  S.running = false; enableComposer(true); $("#cancel-btn").classList.add("hidden");  clearTimeout(S._resumeTimer); S._resumeTok = (S._resumeTok || 0) + 1;  // retire the resume-watchdog (incl. any in-flight tick) so it can't bleed into the next turn
  if (S.stream) { flushRender(S.stream, true); S.stream.md.classList.remove("cursor"); addMsgActions(S.stream.wrap, S.stream.full || S.stream.text); }
  // Belt-and-suspenders: a completed turn must leave nothing blinking, even on
  // text blocks orphaned earlier by a tool/step that started mid-stream.
  const mm = $("#messages"); if (mm) mm.querySelectorAll(".md.cursor").forEach(n => n.classList.remove("cursor"));
  // "please retry" is the wrong advice once output has been committed. The
  // server sets `output_committed` when the failure happened after bytes were
  // streamed or a tool ran, and `llm/models.py` calls it the retry veto: a
  // transparent retry there duplicates visible output or re-fires a side
  // effect, however retryable the status looks. Saying "retry" anyway is how a
  // UI turns one failed turn into two executions of the same tool.
  hint(status === "failed" ? failureHint(detail) : "", status === "failed");
  // Retired with the turn it belonged to, and the generation moved on so a
  // 202 still in flight cannot write it back. Kept only as the fallback above,
  // for a terminal event that arrives without an id.
  closeTurnTicket();
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
// The same partition `PlanService._SETTLED_STEP_STATUSES` makes, in the one
// place the UI needs it. The paused footer counted "not completed and not
// failed" inline, which is a second copy of a rule that had already drifted
// once -- `skipped` was missing from both.
const PLAN_SETTLED_STEP_STATUSES = ["completed", "failed", "skipped"];
function planStepSettled(status) {
  return PLAN_SETTLED_STEP_STATUSES.includes(status);
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
  tt.appendChild(el("div", "pc-eyebrow", status === "draft" ? t("plan.eyebrow.draft") : (status === "executing" ? t("plan.eyebrow.executing") : status === "completed" ? t("plan.eyebrow.completed") : status === "failed" ? t("plan.eyebrow.failed") : status === "paused" ? t("plan.eyebrow.paused") : t("plan.eyebrow.default"))));
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
        : status === "failed" ? t("plan.status.failed", done, total)
          // A paused plan used to render an empty status line and no control:
          // the backend could hold `paused`, and the only way out of it was to
          // discard the plan and start over. It reports what is left rather
          // than what is done, because that is the number the button acts on.
          : status === "paused" ? t("plan.status.paused", done, total, (plan.steps || []).filter(x => !planStepSettled(x.status)).length) : "";
    card.appendChild(st);
    if (status === "paused") {
      const pa = el("div", "pa");
      const go = el("button", "approve-btn"); go.appendChild(iconEl("check", 15)); go.appendChild(el("span", null, t("plan.resume"))); go.onclick = resumePlan;
      const no = el("button", "outline-btn small", t("plan.discard")); no.onclick = discardPlan;
      pa.appendChild(go); pa.appendChild(no); card.appendChild(pa);
    }
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
// One generation-owned dispatch for every plan turn.
//
// Each of these used to lock the UI *after* awaiting its 202 (revise locked
// first, but took no ticket). A plan can fail before the POST is answered, so
// the terminal event arrives first, `turnDone` unlocks -- and then the await
// resolves and locks the composer again against a turn that has already ended.
// The session is stuck until reload. The mirror case is just as bad: while the
// await is outstanding another turn can start, and a rejected plan POST then
// tears *that* turn down.
//
// So: take the generation and lock before the POST; commit the 202's ids only
// if the generation still stands; and let only the owner tear anything down.
async function dispatchPlanTurn(path, body, runningHint, failedKey) {
  // Refuse outright while a turn is running -- including this one, if the
  // user double-clicks. Taking a second ticket makes the newer request the
  // owner: a 409 then tears down the turn that is actually running, and an
  // acceptance replaces the running turn's identity with a queued plan's, so
  // that turn's own terminal event is judged stale and never closes it.
  if (!S.currentId || S.running) return false;
  const token = openTurnTicket();
  S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden");
  hint(runningHint, false, true);
  try {
    const accepted = await api(`/frames/${S.currentId}${path}`, { method: "POST", body: JSON.stringify(body) });
    // A terminal event that beat the 202 has already closed this generation.
    // Re-locking, storing its ids or re-arming the watchdog here would revive
    // a turn that is over.
    if (!ownsTurnTicket(token)) return true;
    commitTurnTicket(token, accepted || {});
    resumeWatch(S.currentId, S._openGen);  // 202 returns at once; only the WS unlocks us
    return true;
  } catch (e) {
    hint(t(failedKey, apiErrorText(e)), true);
    // Only our own turn. If another one took over while we were awaiting, this
    // failure is ours to report and not theirs to end.
    if (ownsTurnTicket(token)) turnDone("failed");
    return false;
  }
}
async function approvePlan() {
  // The mode toggle follows acceptance, as it always has: an approved plan is
  // no longer being drafted.
  if (await dispatchPlanTurn("/plan/approve", { model: S.defaultModelName }, t("plan.autoExecuting"), "plan.approveFailed")) {
    S.planMode = false; const pt = $("#plan-toggle"); if (pt) pt.classList.remove("on");
  }
}
async function resumePlan() {
  await dispatchPlanTurn("/plan/resume", { model: S.defaultModelName }, t("plan.resuming"), "plan.resumeFailed");
}
async function discardPlan() {
  if (!S.currentId) return;
  try { await api(`/frames/${S.currentId}/plan/discard`, { method: "POST", body: "{}" }); } catch {}
  const card = $("#plan-card-live"); if (card) card.remove();
  S.planReady = null; S.planStatus = "discarded"; S.planPending = false; hint(t("toast.planDiscarded"));
}
async function revisePlan(changes) {
  await dispatchPlanTurn("/plan/revise", { changes, model: S.defaultModelName }, t("toast.planRevising"), "toast.reviseFailed");
}

/* ---------- semantic activity steps (plan / search / env / skill / …) ---------- */
const STEP_ICON = { search: "search", fetch: "globe", plan: "list-check", env: "package", skill: "book", bash: "terminal", edit: "pencil", write: "file-text", read: "file-text", files: "files", artifact: "download", delegate: "users", review: "eye-context", mcp: "link", fold: "box", code: "terminal" };
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
function searchResultHttpUrl(value) {
  const raw = typeof value === "string" ? value.trim() : "";
  const lower = raw.toLowerCase();
  // Rebuild the scheme from a literal so untrusted result data can only reach
  // the URL suffix. This preserves mixed-case HTTP(S) inputs without allowing
  // javascript:, data:, or protocol-relative URLs to control the href scheme.
  if (lower.startsWith("https://")) return "https://" + raw.slice(8);
  if (lower.startsWith("http://")) return "http://" + raw.slice(7);
  return "";
}
function stepBody(step) {
  const k = step.kind, inp = step.input || {}, out = step.output || {};
  const box = el("div", "s-inner");
  if (out.error) { box.appendChild(clipPre(out.error, "d-del")); return box; }
  if (k === "review") {
    const issues = Array.isArray(out.issues) ? out.issues : [];
    if (out.verdict === "pass") return box;
    issues.forEach(issue => {
      const row = el("div", "review-issue " + (issue.severity || "medium"));
      const head = el("div", "review-issue-head"); head.appendChild(el("span", "review-severity", issue.severity || "medium")); head.appendChild(el("strong", null, issue.title || "Review finding")); row.appendChild(head);
      if (issue.detail) row.appendChild(el("div", "review-detail", issue.detail));
      if (issue.evidence) row.appendChild(el("div", "review-evidence", issue.evidence));
      box.appendChild(row);
    });
    return box;
  }
  if (k === "search") {
    if (inp.query) box.appendChild(el("div", "s-q", "“" + inp.query + "”"));
    (out.results || []).forEach(r => {
      const row = el("div", "s-res");
      const safeUrl = searchResultHttpUrl(r.url);
      const a = el(safeUrl ? "a" : "div", "s-res-t");
      a.textContent = r.title || r.url || t("step.search.emptyResult");
      if (safeUrl) { a.href = safeUrl; a.target = "_blank"; a.rel = "noopener noreferrer"; }
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
  if (status === "running") { ic.innerHTML = icon("loader", 14, "spin"); meta.textContent = step.kind === "review" ? "Reviewing" : ""; }
  else { ic.innerHTML = icon(status === "error" ? "x" : stepIcon(step.kind), 14); meta.textContent = step.summary || (step.output && step.output.error ? t("step.status.failed") : ""); }
  body.innerHTML = ""; body.appendChild(stepBody(step));
  if ((step.kind === "plan" || step.kind === "artifact") && status !== "running") card.classList.add("open");
  if (step.kind === "review") {
    const hasIssues = step.output && step.output.verdict === "issues";
    card.classList.toggle("review-pass", status === "done" && !hasIssues);
    card.classList.toggle("review-issues", status === "done" && hasIssues);
    card.classList.toggle("open", !!hasIssues);
  }
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
  if (m.kind === "review") hint("Reviewing", false, true);
  down();
}
function updateLiveStep(m) {
  const h = (S.stepEls || {})[m.step_id]; if (!h) return;
  h.step.status = m.status; h.step.output = m.output; h.step.summary = m.summary;
  applyStepState(h); if (h.step.kind === "review" && m.status !== "running") hint(""); down();
}
function renderStoredStep(s) {
  const handle = buildStepCard(s);
  if (s.step_id) (S.stepEls = S.stepEls || {})[s.step_id] = handle;
  // Same stamp as a stored message: steps are fetched whole while messages are
  // paged, so a later page of older messages has to be able to sort against
  // the step cards already on screen.
  handle.card.dataset.ts = String(s.created_at || 0);
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
// How much the card offers to remember by default.
//
// It was "conversation" for everything. Combined with a pre-filled pattern and
// an Allow button, that made a single click on a `restore_artifact_version` or
// `compute_submit` prompt grant that capability for the rest of the session —
// and the card gave no sign the two were different, because nothing read the
// tool's `dangerous` declaration. Now the risky ones default to a grant that
// covers only this call. Every scope is still offered; the user picks a broader
// one deliberately rather than by not noticing the selector.
function defaultRememberScope(m) {
  return (m && m.dangerous) ? "once" : "conversation";
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
  if (m.dangerous) head.appendChild(el("span", "perm-badge danger", t("perm.badge.dangerous")));
  card.appendChild(head);
  card.appendChild(el("div", "perm-sub", t("perm.sub.approvalNeeded")));
  const act = permActionLine(m);
  if (act.text) card.appendChild(el("div", "perm-detail" + (act.mono ? " mono" : ""), act.text));

  let scope = defaultRememberScope(m);
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
  // The rule box is meaningless for a "once" grant, and was only ever hidden by
  // the click handler — so a card that starts at "once" would show an input
  // that does nothing.
  patWrap.style.display = (scope === "once") ? "none" : "";

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
    let resolution;
    try {
      resolution = await api(`/frames/${encodeURIComponent(m.frame_id)}/decision`, { method: "POST", body: JSON.stringify(body) });
      if (!resolution || resolution.ok !== true) throw new Error((resolution && resolution.error) || "permission decision was not accepted");
    }
    catch (e) {
      // Re-enabling is the right default: the decision was refused, so the user
      // may fix and resubmit. It is wrong for exactly one refusal --
      // `decision_continuation_failed`, where the approval WAS written and only
      // its continuation marker failed. Clicking Allow again there submits a
      // decision that already took effect, which is the dangerous retry P0-4's
      // `output_committed` exists to suppress. The turn-failure surface already
      // reads that field; this one did not.
      const committed = !!(e && e.body && e.body.output_committed);
      if (!committed) allow.disabled = deny.disabled = false;
      hint(t("toast.submitFailed", apiErrorText(e)), true);
      return;
    }
    markPermCard(m.decision_id, ok, scope, resolution);
  };
  allow.onclick = () => send(true);
  deny.onclick = () => send(false);
  btns.appendChild(allow); btns.appendChild(deny);
  card.appendChild(btns);

  host.appendChild(card);
  S.permCards[m.decision_id] = { card, allow, deny, resolved: false };
  down();
}
function markPermCard(id, allowed, scope, resolution) {
  const reg = S.permCards || {};
  if (!Object.prototype.hasOwnProperty.call(reg, id)) return;  // ignore __proto__/constructor keys
  const h = reg[id]; h.resolved = true; h.resolution = resolution || null;
  if (h.allow) h.allow.disabled = true; if (h.deny) h.deny.disabled = true;
  h.card.classList.add("resolved", allowed ? "allowed" : "denied");
  let st = h.card.querySelector(".perm-status");
  if (!st) { st = el("div", "perm-status"); h.card.appendChild(st); }
  const afterRestart = resolution && resolution.resolution_context === "after_restart";
  st.textContent = afterRestart
    ? (allowed ? t("perm.status.afterRestartAllowed") : t("perm.status.afterRestartDenied"))
    : (allowed ? ((scope && scope !== "once") ? t("perm.status.allowedScope", permScopeCn(scope)) : t("perm.status.allowed")) : t("perm.status.denied"));
  const oldContinue = h.card.querySelector(".perm-continue"); if (oldContinue) oldContinue.remove();
  if (allowed && resolution && resolution.requires_continue === true) {
    const cont = el("button", "perm-continue", t("perm.btn.continueReplan"));
    cont.onclick = async () => {
      if (S.running) { hint(t("toast.running"), false, true); return; }
      cont.disabled = true;
      try { await send(t("perm.continuePrompt")); }
      finally { if (cont.isConnected && !S.running) cont.disabled = false; }
    };
    h.card.appendChild(cont);
  }
}
function resolvePermissionCard(m) {
  const reg = S.permCards || {};
  if (!Object.prototype.hasOwnProperty.call(reg, m.decision_id)) return;  // ignore __proto__/constructor keys
  const h = reg[m.decision_id];
  if (!h.resolved) markPermCard(m.decision_id, !!m.allow, m.scope || null, m);
}

/* ---------- dashboard ---------- */
function paintDashSkeleton() {
  const skel = (n) => {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < n; i++) {
      const row = el("div", "d-row skeleton-row");
      const main = el("div", "d-main");
      main.appendChild(el("div", "d-name", "·")); main.appendChild(el("div", "d-sub", "·"));
      row.appendChild(main); row.appendChild(el("div", "d-meta", "·"));
      frag.appendChild(row);
    }
    return frag;
  };
  const pc = $("#dash-projects"); if (pc && !pc.childElementCount) pc.appendChild(skel(3));
  const sc = $("#dash-sessions"); if (sc && !sc.childElementCount) sc.appendChild(skel(4));
}
async function loadDashboard() {
  paintDashSkeleton();
  await loadProjects();
  let frames = [];
  try { frames = ((await api("/frames?limit=50")).frames || []).filter(f => !f.parent_frame_id); } catch {}
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
// The example analysis, offered rather than performed. It used to run itself
// on first boot -- six cells, live UniProt/RCSB calls, four artifacts, before
// the user had typed anything. The work is worth having; doing it unasked was
// the problem, so it became a button. The hint says what clicking will do,
// because "run the example" should not be the first time someone learns this
// app makes outbound calls.
function exampleSeedCta() {
  const box = el("div", "dash-example");
  const btn = el("button", "btn", t("dash.example.cta"));
  const note = el("div", "dash-example-hint", t("dash.example.hint"));
  box.appendChild(btn); box.appendChild(note);
  let timer = 0;
  const stop = () => { if (timer) { clearInterval(timer); timer = 0; } };
  const paint = (st) => {
    if (st.running) { btn.disabled = true; btn.textContent = t("dash.example.running"); }
    else { btn.disabled = false; btn.textContent = t("dash.example.cta"); }
    // Report a failed seed. Without this the only signal is that the example
    // never appears, which looks identical to a slow network.
    if (st.error) note.textContent = t("dash.example.failed") + st.error;
    if (st.seeded) { stop(); loadDashboard(); }
  };
  const poll = () => api("/example/session").then(paint).catch(stop);
  btn.onclick = () => {
    btn.disabled = true;
    // `confirm` is required by the route: it runs code and calls external
    // APIs, so intent has to be in the body rather than implied by the verb.
    api("/example/session", { method: "POST", body: JSON.stringify({ confirm: true }) }).then(st => {
      paint(st);
      stop(); timer = setInterval(poll, 1500);
    }).catch(e => { btn.disabled = false; note.textContent = t("dash.example.failed") + apiErrorText(e); });
  };
  // Hidden entirely once the example exists, and while a startup-opt-in seed
  // (OPENAI4S_SEED_DEMO=1) is already doing the same work.
  api("/example/session").then(st => { if (st.seeded) box.remove(); else paint(st); if (st.running) timer = setInterval(poll, 1500); }).catch(() => box.remove());
  return box;
}
function renderDashRecent(frames) {
  const recent = frames.filter(f => (f.message_count || 0) > 0 || f.name || f.task_summary)
    .sort((a, b) => (new Date(b.updated_at) - new Date(a.updated_at))).slice(0, 10);
  const sc = $("#dash-sessions"); if (!sc) return; sc.innerHTML = "";
  if (!recent.length) { sc.appendChild(el("div", "dash-empty", t("dash.sessions.empty"))); sc.appendChild(exampleSeedCta()); }
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
  // Skip work while the tab is backgrounded; the next visible tick will catch up.
  if (typeof document.hidden === "boolean" && document.hidden) return;
  let frames = [];
  try { frames = ((await api("/frames?limit=50")).frames || []).filter(f => !f.parent_frame_id); } catch { return; }
  if ($("#dashboard").classList.contains("hidden")) return;
  renderDashRunning(frames);
}
function startDashPoll() {
  stopDashPoll();
  S._dashPoll = setInterval(refreshDashRunning, 4000);
  if (!startDashPoll._visBound) {
    startDashPoll._visBound = true;
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && !$("#dashboard").classList.contains("hidden")) refreshDashRunning();
    });
  }
}
function stopDashPoll() { if (S._dashPoll) { clearInterval(S._dashPoll); S._dashPoll = null; } }

/* ---------- projects ---------- */
async function loadProjects() { try { const d = await api("/projects?limit=100&offset=0"); S.projects = (d && d.projects) || []; } catch { S.projects = []; } }
function sanitizeProjectLineage(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  const nodes = (Array.isArray(source.nodes) ? source.nodes : []).slice(0, 5000).map(item => ({
    id: publicText(item && item.id, 160), kind: publicText(item && item.kind, 48),
    artifact_id: publicText(item && item.artifact_id, 120), version_id: publicText(item && item.version_id, 120),
    filename: publicText(item && item.filename, 240), root_frame_id: publicText(item && item.root_frame_id, 120),
    cell_id: publicText(item && (item.cell_id || item.producing_cell_id), 120), created_at: item && item.created_at,
    latest: !!(item && item.latest)
  })).filter(item => item.id);
  const ids = new Set(nodes.map(item => item.id));
  const edges = (Array.isArray(source.edges) ? source.edges : []).slice(0, 10000).map(item => ({
    from: publicText(item && item.from, 160), to: publicText(item && item.to, 160), kind: publicText(item && item.kind, 48)
  })).filter(item => ids.has(item.from) && ids.has(item.to));
  return {
    project_id: publicText(source.project_id, 120), nodes, edges,
    artifact_count: Number.isFinite(+source.artifact_count) ? Math.max(0, +source.artifact_count) : 0,
    version_count: Number.isFinite(+source.version_count) ? Math.max(0, +source.version_count) : 0,
    truncated: !!source.truncated
  };
}
async function openProjectResearchView(initialTab = "timeline") {
  if (!S.project) return;
  const projectId = S.project, mode = "project-research:" + projectId;
  S._modalMode = mode; $("#modal-title").textContent = t("projectResearch.title", projName(projectId));
  $("#modal-download").style.display = "none"; const body = $("#modal-body"); body.innerHTML = ""; $("#modal").classList.remove("hidden");
  const tabs = el("div", "project-research-tabs"), content = el("div", "project-research-content"); body.appendChild(tabs); body.appendChild(content);
  const cache = { timeline: null, lineage: null };
  const renderTimeline = data => {
    content.innerHTML = "";
    const summary = el("div", "project-research-summary", t("projectResearch.timelineSummary", data.session_count || 0, data.total_count || data.count || 0)); content.appendChild(summary);
    if (!(data.groups || []).length) { content.appendChild(el("div", "dock-empty", t("timeline.empty"))); return; }
    (data.groups || []).forEach(group => {
      const wrapper = el("div", "project-timeline-entry");
      if (group.session) wrapper.appendChild(el("div", "project-session-label", group.session.name || shortRuntime(group.session.root_frame_id)));
      wrapper.appendChild(actionTimelineCard(group)); content.appendChild(wrapper);
    });
  };
  const renderLineage = data => {
    content.innerHTML = "";
    content.appendChild(el("div", "project-research-summary", t("projectResearch.lineageSummary", data.artifact_count, data.version_count, data.edges.length)));
    const byId = new Map((data.nodes || []).map(item => [item.id, item]));
    (data.nodes || []).filter(item => item.kind === "artifact_version").forEach(item => {
      const row = el("div", "project-lineage-node"); row.appendChild(el("span", "project-lineage-name", item.filename || shortRuntime(item.version_id)));
      if (item.latest) row.appendChild(el("span", "timeline-pill", t("projectResearch.latest")));
      if (item.cell_id) row.appendChild(el("span", "project-lineage-cell", shortRuntime(item.cell_id))); content.appendChild(row);
    });
    if (!(data.nodes || []).some(item => item.kind === "artifact_version")) content.appendChild(el("div", "dock-empty", t("projectResearch.noLineage")));
    if ((data.edges || []).length) {
      const edges = el("details", "project-lineage-edges"); edges.appendChild(el("summary", null, t("projectResearch.edges", data.edges.length)));
      data.edges.slice(0, 500).forEach(edge => {
        const from = byId.get(edge.from), to = byId.get(edge.to);
        edges.appendChild(el("div", "project-lineage-edge", (from && (from.filename || from.cell_id) || shortRuntime(edge.from)) + " → " + (to && (to.filename || to.cell_id) || shortRuntime(edge.to))));
      }); content.appendChild(edges);
    }
  };
  const select = async tab => {
    if (S._modalMode !== mode) return;
    Array.from(tabs.children).forEach(button => button.classList.toggle("active", button.dataset.tab === tab));
    content.innerHTML = ""; content.appendChild(el("div", "dock-empty", t("common.loading")));
    try {
      if (!cache[tab]) cache[tab] = tab === "timeline"
        ? sanitizeActionTimeline(await api(`/projects/${encodeURIComponent(projectId)}/action-timeline?limit=500`))
        : sanitizeProjectLineage(await api(`/projects/${encodeURIComponent(projectId)}/lineage?limit=2000`));
      if (S._modalMode !== mode) return;
      (tab === "timeline" ? renderTimeline : renderLineage)(cache[tab]);
    } catch (error) { if (S._modalMode === mode) { content.innerHTML = ""; content.appendChild(el("div", "timeline-error", publicText(error && error.message, 240))); } }
  };
  [["timeline", t("projectResearch.timeline")], ["lineage", t("projectResearch.lineage")]].forEach(([key, label]) => { const button = el("button", "seg-btn", label); button.dataset.tab = key; button.onclick = () => select(key); tabs.appendChild(button); });
  select(initialTab === "lineage" ? "lineage" : "timeline");
}
function renderProjMenu() {
  $("#proj-current").textContent = S.project ? projName(S.project) : t("proj.current.allSessions");
  const m = $("#proj-menu"); m.innerHTML = "";
  const item = (label, iconName, onClick) => {
    const it = el("div", "proj-item"); const group = el("span"); group.style.cssText = "display:flex;align-items:center;gap:6px";
    group.appendChild(iconEl(iconName, 16)); group.appendChild(el("span", null, label)); it.appendChild(group);
    it.onclick = () => { $("#proj-menu").classList.add("hidden"); onClick(); }; m.appendChild(it); return it;
  };
  if (S.project) {
    const current = S.projects.find(p => (p.project_id || p.id) === S.project);
    if (current) item(t("proj.menu.settings"), "settings", () => openProjectModal(current));
    item(t("projectResearch.menu"), "provenance", () => openProjectResearchView("timeline"));
    item(t("sessionPackage.import"), "cloud-upload", chooseSessionPackage);
    item(t("proj.menu.downloadArtifacts"), "download", () => downloadArtifactBundle(`${API}/projects/${encodeURIComponent(S.project)}/artifacts.zip`, `${projName(S.project)}-artifacts.zip`));
    m.appendChild(el("div", "ctx-sep"));
  }
  item(t("proj.menu.allProjects"), "arrow-left", showDashboard);
  S.projects.forEach(p => {
    if ((p.project_id || p.id) !== S.project) item((p.name || t("proj.fallbackName")).slice(0, 26), "box", () => selectProject(p.project_id || p.id));
  });
  m.appendChild(el("div", "ctx-sep"));
  item(t("proj.menu.newProject"), "plus", () => openProjectModal());
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
function closeProjectModal() {
  closeModalEl($("#proj-modal"));
  S.editingProject = null;
}
function openProjectModal(project) {
  const p = project || null;
  S.editingProject = p ? (p.project_id || p.id) : null;
  const title = $("#proj-modal .modal-head span");
  if (title) title.textContent = t(p ? "projModal.editTitle" : "projModal.title");
  $("#pm-name").value = p ? (p.name || "") : "";
  $("#pm-desc").value = p ? (p.description || "") : "";
  $("#pm-ctx").value = p ? (p.context || p.agent_context || "") : "";
  $("#pm-create").textContent = t(p ? "common.save" : "projModal.create");
  $("#pm-delete").classList.toggle("hidden", !p);
  openModalEl($("#proj-modal"));
  requestAnimationFrame(() => $("#pm-name").focus());
}
async function submitProjectModal() {
  const btn = $("#pm-create"); const name = $("#pm-name").value.trim() || t("palette.action.newProject");
  btn.disabled = true;
  try {
    if (S.editingProject) {
      await api(`/projects/${S.editingProject}`, { method: "PATCH", body: JSON.stringify({ name, description: $("#pm-desc").value, context: $("#pm-ctx").value }) });
      await loadProjects(); renderProjMenu();
      if (!$("#dashboard").classList.contains("hidden")) renderDashProjects();
      closeProjectModal();
    } else {
      await createProject(name, $("#pm-desc").value, $("#pm-ctx").value);
      closeProjectModal();
    }
  } catch (e) { hint(t("artifact.save.err", apiErrorText(e)), true); }
  finally { btn.disabled = false; }
}
async function deleteProject(id) {
  try {
    await api("/projects/" + id, { method: "DELETE" });
    closeProjectModal();
    await loadProjects();
    if (S.project === id) { S.project = null; showDashboard(); }
    else renderProjMenu();
  } catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); }
}

/* ---------- sessions ---------- */
// Newest-first paging, which the server has supported the whole time.
//
// Every message fetch here sent `?from=0&limit=N` — the OLDEST N — so opening a
// 640-message session showed messages 0 to 299 and the work you came back for
// was off the end. `newest_first` / `before_seq` / `next_before_seq` went in
// through the store, the repository and the route, and `app.js` contained
// neither string; a comment on the route even described "the client asks for
// the newest page", describing a client nobody had written.
//
// The rows come back descending, so they are sorted back into reading order
// here rather than at each call site.
const MESSAGE_PAGE_SIZE = 300;       // one page of history per request
const MESSAGE_WALK_MAX_PAGES = 200;  // 60k messages: a bound on a pathological session, not a feature cap
async function fetchRecentMessages(fid, limit) {
  const data = await api(`/frames/${encodeURIComponent(fid)}/messages?newest_first=1&limit=${limit}`);
  const rows = (data && data.messages) || [];
  rows.sort((a, b) => (a.seq || 0) - (b.seq || 0));
  return { ...data, messages: rows };
}
// One page OLDER than `beforeSeq`, sorted back into reading order.
//
// `before_seq` is a keyset bound on a monotonic `seq`, not an offset, so a
// message arriving while the reader walks back cannot shift the page under
// them — with an offset, every arrival would repeat or skip a row.
async function fetchOlderMessages(fid, beforeSeq, limit) {
  const data = await api(`/frames/${encodeURIComponent(fid)}/messages?limit=${limit}&before_seq=${encodeURIComponent(beforeSeq)}`);
  const rows = (data && data.messages) || [];
  rows.sort((a, b) => (a.seq || 0) - (b.seq || 0));
  return { ...data, messages: rows };
}
// The WHOLE conversation, walked newest page first and returned oldest-first.
//
// The Markdown export asked the newest-page helper for 500 messages and wrote
// the result under a heading naming the session: a 640-message session exported
// its last 500 messages and said nothing about the first 140. `complete` is
// reported so the caller can say so when the walk hits its bound, rather than
// producing a short export that looks whole.
async function fetchAllMessages(fid) {
  const first = await fetchRecentMessages(fid, MESSAGE_PAGE_SIZE);
  let rows = first.messages || [];
  let cursor = first.next_before_seq, earlier = !!first.has_earlier, pages = 1;
  while (earlier && cursor != null && pages < MESSAGE_WALK_MAX_PAGES) {
    const older = await fetchOlderMessages(fid, cursor, MESSAGE_PAGE_SIZE);
    rows = (older.messages || []).concat(rows);
    cursor = older.next_before_seq; earlier = !!older.has_earlier; pages += 1;
  }
  return { messages: rows, complete: !earlier };
}

const SESSION_PAGE_SIZE = 100;   // the route's own page cap is 200; this leaves headroom
const SESSION_MAX_PAGES = 50;    // 5000 sessions held in the sidebar at once
async function loadSessions() {
  // Scoped to the open project, and paged with the cursor the route has always
  // returned. This fetched the 100 most recent sessions across ALL projects and
  // filtered by project in the browser, so a project whose sessions sat outside
  // that global page appeared to have none — and `openProject` reads "none" as
  // a reason to call `newSession()`. Switching to a quiet project therefore
  // created a blank session instead of showing the work sitting in SQLite.
  //
  // It was also a hard stop at 100: `next_cursor` and `has_more` had no
  // consumer anywhere in this file, so session 101 of 260 was unreachable by
  // any control the UI offered.
  //
  // A refresh re-walks from the newest page instead of appending, because
  // `loadSessions()` runs on every `frame_update`. Re-walking is exact:
  // `(created_at, frame_id)` is a value bound, so a session created — or
  // deleted — between two page requests cannot make the walk repeat or skip a
  // row. That is the property an offset does not have, and it is why a live
  // arrival does not disturb a reader who has paged several pages deep.
  const scope = S.project ? `&project_id=${encodeURIComponent(S.project)}` : "";
  if (S._sessionScope !== (S.project || "")) { S._sessionScope = S.project || ""; S.sessionPages = 1; }
  const want = Math.min(SESSION_MAX_PAGES, Math.max(1, S.sessionPages || 1));
  const rows = []; const seen = new Set();
  let cursor = null, hasMore = false, walked = 0;
  try {
    while (walked < want) {
      const f = await api(`/frames?limit=${SESSION_PAGE_SIZE}${scope}` + (cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""));
      walked += 1;
      ((f && f.frames) || []).forEach(x => { if (!x.parent_frame_id && !seen.has(x.id)) { seen.add(x.id); rows.push(x); } });
      hasMore = !!(f && f.has_more); cursor = (f && f.next_cursor) || null;
      if (!hasMore || !cursor) break;
    }
    S.sessions = rows; S.sessionPages = Math.max(1, walked); S.sessionsHasMore = hasMore;
  } catch { S.sessions = []; S.sessionPages = 1; S.sessionsHasMore = false; }
  await loadFolders();
  renderSessions(); syncCurrentTitle(); if (!$("#dashboard").classList.contains("hidden")) loadDashboard();
}
// One more page, by re-walking one page deeper. Guarded because `loadSessions`
// is also fired by `frame_update`, and two overlapping walks would render the
// shorter one's result last.
async function loadMoreSessions() {
  if (S._sessionsLoadingMore || !S.sessionsHasMore) return;
  if ((S.sessionPages || 1) >= SESSION_MAX_PAGES) return;
  S._sessionsLoadingMore = true; S.sessionPages = (S.sessionPages || 1) + 1; renderSessions();
  try { await loadSessions(); } finally { S._sessionsLoadingMore = false; renderSessions(); }
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
  d.setAttribute("role", "button"); d.tabIndex = 0;
  d.setAttribute("aria-current", f.id === S.currentId ? "page" : "false");
  d.onkeydown = (e) => { if (e.target === d && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); openConversation(f.id, f.project_id); } };
  d.onclick = () => openConversation(f.id, f.project_id); return d;
}
function renderSessions() {
  const list = $("#session-list"); if (!list) return;
  list.innerHTML = "";
  const frag = document.createDocumentFragment();
  let ss = S.sessions; if (S.project) ss = ss.filter(f => f.project_id === S.project);
  ss = ss.slice().sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at));
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
    head.setAttribute("role", "button"); head.tabIndex = 0;
    head.onkeydown = (e) => { if (e.target === head && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); head.click(); } };
    frag.appendChild(head);
    if (!collapsed) inFold.forEach(f => { const r = sessionRow(f); r.style.paddingLeft = "20px"; frag.appendChild(r); });
  });
  // ungrouped, by date
  const ungrouped = ss.filter(f => !f.folder_id || !(S.folders || []).some(x => x.folder_id === f.folder_id));
  let lastBucket = null;
  ungrouped.forEach(f => { const b = dateBucket(f.updated_at); if (b !== lastBucket) { lastBucket = b; frag.appendChild(el("div", "side-label", b)); } frag.appendChild(sessionRow(f)); });
  // The control that makes the cursor reachable. Without it `has_more` is a
  // field nothing acts on, and the list simply ends at the first page with no
  // sign that it was cut rather than finished.
  if (S.sessionsHasMore && (S.sessionPages || 1) >= SESSION_MAX_PAGES) {
    // Say the walk stopped, rather than offer a button that cannot go deeper.
    // The bound exists because a refresh re-walks every held page, and a dead
    // control is worse than a sentence — it looks like the feature is broken.
    frag.appendChild(el("div", "side-label", t("session.loadMoreLimit")));
  } else if (S.sessionsHasMore) {
    const more = el("button", "outline-btn small", S._sessionsLoadingMore ? t("common.loading") : t("session.loadMore"));
    more.id = "session-more"; more.disabled = !!S._sessionsLoadingMore;
    more.style.margin = "10px 8px"; more.onclick = loadMoreSessions;
    frag.appendChild(more);
  }
  list.appendChild(frag);
}
async function newFolder() {
  const name = prompt(t("folder.new.prompt")); if (!name || !S.project) return;
  try { await api(`/projects/${S.project}/folders`, { method: "POST", body: JSON.stringify({ name }) }); invalidateFolders(); await loadFolders(); await loadSessions(); } catch (e) { hint(t("folder.create.failed", apiErrorText(e)), true); }
}
function folderMenu(anchor, fold) {
  openMenu(anchor, [
    { label: t("folder.menu.rename"), icon: "pencil", onClick: async () => { const n = prompt(t("folder.rename.prompt"), fold.name); if (!n) return; try { await api(`/folders/${fold.folder_id}`, { method: "PATCH", body: JSON.stringify({ name: n }) }); invalidateFolders(); await loadFolders(); await loadSessions(); } catch {} } },
    { label: t("folder.menu.delete"), icon: "trash-2", danger: true, onClick: async () => { if (!confirm(t("folder.delete.confirm", fold.name))) return; try { await api(`/folders/${fold.folder_id}`, { method: "DELETE" }); invalidateFolders(); await loadFolders(); await loadSessions(); } catch {} } },
  ]);
}
async function assignFolder(fid, folder_id) { try { await api(`/frames/${fid}/folder`, { method: "POST", body: JSON.stringify({ folder_id }) }); await loadSessions(); hint(folder_id ? t("folder.assigned.in") : t("folder.assigned.out")); } catch (e) { hint(t("folder.move.failed", apiErrorText(e)), true); } }
async function newSession() {
  try { const f = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: S.project || undefined, model: S.defaultModelName }) });
    await loadSessions(); openConversation(f.id, S.project); $("#composer").focus();
  } catch (e) { hint(t("folder.create.failed", apiErrorText(e)), true); }
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
  clearTimeout(S._branchConversationTimer);
  const previousFid = S.currentId;
  if (previousFid && previousFid !== fid) unsub(previousFid);
  if (pid && pid !== S.project) { S.project = pid; S._projArtFor = null; }  // new project → drop cached project-wide Files
  // reflect the open conversation in the address bar so it's a persistent,
  // shareable, reload-safe location (no-op when we're already at this path, e.g.
  // during deep-link hydration or a resume-resync re-open).
  navURL(framePath(fid, pid || S.project || (S.sessions.find(x => x.id === fid) || {}).project_id));
  showWorkspace(); showConv(); renderProjMenu();
  if (mqMobile.matches) setSidebar(true);  // collapse the mobile drawer so the conversation is visible
  S.currentId = fid; $("#messages").innerHTML = ""; S.stream = null;
  closeTurnTicket();  // a ticket belongs to the session that issued it
  S.msgCursor = null; S.msgHasEarlier = false; S._msgEarlierLoading = false;  // the history window restarts at the newest page
  S.running = false; enableComposer(true); $("#cancel-btn").classList.add("hidden");
  clearTimeout(S._resumeTimer);  // stop any resume-watchdog from the previously open session
  const gen = S._openGen = (S._openGen || 0) + 1;  // guard async continuations against fast session-switching
  S.cells = []; S.kernels = []; S.liveCells = []; S._liveCell = null; S.dockArtifact = null; S.kernelFilter = null;
  destroyActionTimelineView(); S.actionTimeline = null; S.actionTimelineSelectedGroupId = null; S.actionTimelineSelectedBranchId = null;
  S.executionQueue = null; S.executionIdentity = null; S.recoveryState = null; S.recoveryActions = null; S.delegationState = null;
  S.branchState = null; S.branchUndo = null; S.contextState = null; S.securityState = null;
  S.workbenchErrors = {}; S._timelineHistoryReq = (S._timelineHistoryReq || 0) + 1; S._timelineHistoryLoading = null;
  S._recoveryActionLoading = null; S._branchActionLoading = null; S._timelineRestoreFocusGroupId = null;
  S.variableInspector = { language: "python", results: {}, loading: null, error: "", request: 0 };
  clearTimeout(S._workbenchTimer); S._workbenchReq = (S._workbenchReq || 0) + 1; S._workbenchLoading = null;
  S._tbl = {}; invalidateKernelCache();  // drop the prior session's table + kernel-state caches
  S.openTabs = []; S.activeTab = "notebook"; S.provMode = false; S.lineage = null; S._lineageFor = null;
  showDockPane("notebook");
  S.stepEls = {};  // fresh step registry so reopen-then-replay dedupes by step_id
  S.permCards = Object.create(null);  // fresh permission-card registry (null-proto; drop cards from the prior conversation)
  S.planReady = null; S.planStatus = null; S.planPending = false;  // fresh plan state per session
  S.computeStatus = null;  // where the *previous* session ran says nothing about this one
  { const badge = $("#compute-badge"); if (badge) badge.remove(); }
  { const banner = $("#compute-lost"); if (banner) banner.remove(); }
  refreshComputeStatus(fid);  // deliberately not awaited: a session must open even if this route does not exist
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
      fetchRecentMessages(fid, MESSAGE_PAGE_SIZE),
      api(`/frames/${fid}/steps`).catch(() => ({ steps: [] })),
    ]);
    if (gen !== S._openGen) return;
    const msgs = (d && d.messages) || []; msgCount = msgs.length;
    S.msgCursor = (d && d.next_before_seq != null) ? d.next_before_seq : null;
    S.msgHasEarlier = !!(d && d.has_earlier);
    const steps = (sd && sd.steps) || [];
    // interleave stored messages + activity steps by timestamp (steps carry seq
    // for a stable tie-break) so a reopened session re-renders the full activity.
    const items = [];
    msgs.forEach(mm => items.push({ t: new Date(mm.created_at).getTime() || 0, seq: 1e15, kind: "msg", v: mm }));
    steps.forEach(s => items.push({ t: s.created_at || 0, seq: s.seq || 0, kind: "step", v: s }));
    items.sort((a, b) => (a.t - b.t) || (a.seq - b.seq));
    items.forEach(it => { if (it.kind === "msg") renderStored(it.v); else renderStoredStep(it.v); });
    paintEarlierControl();
  } catch {}
  if (gen !== S._openGen) return;
  if (!msgCount) renderEmptySession();
  loadArtifacts(fid); loadExecutionLog(fid); loadWorkbenchState(fid); down(true); updateJumpPill();
  // Sequenced, not raced. `reconcileLastAdmission` reads the server's view and
  // then reloads annotations; firing it alongside `loadAnnotations` meant the
  // two replies could land in either order, so a reload sometimes showed the
  // stale pre-turn state and sometimes the reconciled one.
  (async () => {
    await loadAnnotations(fid);
    await reconcileLastAdmission(fid);
  })();
  // Resume: subscribe AFTER history renders so a replayed in-flight turn streams
  // below it. If a turn is still running server-side (survived our last close),
  // lock the composer and let the WS replay rebuild the live stream + notebook.
  try {
    const stt = await api(`/frames/${fid}/status`);
    if (gen !== S._openGen) return;
    if (stt && stt.running) { S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint(t("conv.resuming.hint"), false, true); resumeWatch(fid, gen); }
    // The global hint is restored ONCE, and only when the session's current
    // state is a failure -- the frame says so, and the failure is the last
    // thing in the transcript. Any other reading (any stored failure anywhere,
    // as an earlier draft had it) would let a turn that has since been
    // succeeded by three good ones present itself as the state of the session.
    else if (stt && stt.status === "failed") {
      const last = lastTerminalFailure();
      if (last) hint(failureHint(last), true);
    }
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
function renderStored(m, target) {
  const text = Array.isArray(m.content) ? m.content.map(b => (b && b.text) || "").join("") : (m.content || "");
  if (!text.trim()) return null;
  const w = el("div", "msg " + (m.role === "user" ? "user" : "assistant"));
  rememberCandidateIdentity(w, m); w._messageText = text;
  if (m.role === "user") { const b = el("div", "bubble"); b.textContent = text; w.appendChild(b); renderMessageRefChips(w, m.artifact_refs); }
  else {
    const md = el("div", "md"); md.innerHTML = renderMd(text); w.appendChild(md);
    // Inline on the message it belongs to, never the global hint. A session
    // is rendered oldest-first and older pages are prepended later, so calling
    // hint() here would let any past failure -- including one several
    // successful turns ago, or one on a page the reader scrolled back to --
    // become the current state of the whole UI.
    if (m.failure && m.failure.request_id) w.appendChild(failureMeta(m.failure));
    const review = m.review_status || (m.metadata && m.metadata.review_status);
    const reviewStatus = review && (review.status || review);
    if (reviewStatus) { setMessageReviewBadge(w, reviewStatus, review && review.user_truth); if (reviewStatus !== "candidate" && w.dataset) w.dataset.candidateResolved = "true"; }
  }
  // Stamped with its own time so a page of OLDER messages can be put where it
  // belongs. Activity steps are fetched whole while messages are paged, so the
  // column already holds step cards older than the newest message page;
  // prepending an older page at the very top would put message 0 above a step
  // from the turn that produced it.
  w.dataset.ts = String(new Date(m.created_at).getTime() || 0);
  (target || $("#messages")).appendChild(w);
  if (m.role !== "user") addMsgActions(w, text);
  return w;
}
// Put one restored message in time order among what is already rendered.
function insertMessageByTime(node) {
  const host = $("#messages"); if (!host || !node) return;
  const ts = Number(node.dataset.ts || 0);
  const kids = host.children;
  for (let i = 0; i < kids.length; i++) {
    const kid = kids[i];
    if (kid.id === "msgs-earlier") continue;  // the control stays pinned to the top
    const kidTs = Number(kid.dataset && kid.dataset.ts);
    if (Number.isFinite(kidTs) && kidTs > ts) { host.insertBefore(node, kid); return; }
  }
  host.appendChild(node);
}
// Reaching the part of a long conversation that is not on screen.
//
// `fetchRecentMessages` asks for the NEWEST page and the route answers with
// `next_before_seq` / `has_earlier` beside it. Nothing in this file read
// either, so in a 640-message session messages 0-339 existed, were paged for,
// and could not be reached by scrolling or by any control. This is the half
// that was missing.
function paintEarlierControl() {
  const host = $("#messages"); if (!host) return;
  let bar = document.getElementById("msgs-earlier");
  if (!S.msgHasEarlier) { if (bar) bar.remove(); return; }
  if (!bar) {
    bar = el("div", "msgs-earlier"); bar.id = "msgs-earlier";
    bar.style.textAlign = "center"; bar.style.padding = "8px 0";
    const btn = el("button", "outline-btn small", t("conv.loadEarlier"));
    btn.onclick = loadEarlierMessages; bar.appendChild(btn);
  }
  const btn = bar.querySelector("button");
  if (btn) { btn.disabled = !!S._msgEarlierLoading; btn.textContent = S._msgEarlierLoading ? t("common.loading") : t("conv.loadEarlier"); }
  if (host.firstChild !== bar) host.insertBefore(bar, host.firstChild);
}
async function loadEarlierMessages() {
  if (!S.currentId || !S.msgHasEarlier || S.msgCursor == null || S._msgEarlierLoading) return;
  const host = $("#messages"); if (!host) return;
  const fid = S.currentId, gen = S._openGen;
  S._msgEarlierLoading = true; paintEarlierControl();
  try {
    const data = await fetchOlderMessages(fid, S.msgCursor, MESSAGE_PAGE_SIZE);
    if (gen !== S._openGen) return;  // the reader switched sessions mid-request
    // Hold the reading position. Leaving `scrollTop` alone moves the page by
    // exactly the height of what was inserted above it, and scrolling to the
    // top loses the message the reader was on; adding back the height the
    // prepend introduced keeps the same message under the same pixel.
    const beforeHeight = host.scrollHeight, beforeTop = host.scrollTop;
    const holder = document.createDocumentFragment();
    (data.messages || []).forEach(mm => insertMessageByTime(renderStored(mm, holder)));
    host.scrollTop = beforeTop + (host.scrollHeight - beforeHeight);
    S.msgCursor = data.next_before_seq != null ? data.next_before_seq : null;
    S.msgHasEarlier = !!data.has_earlier;
  } catch (e) { hint(t("conv.loadEarlierFailed", apiErrorText(e)), true); }
  finally { S._msgEarlierLoading = false; paintEarlierControl(); }
}

// The chips a restored message was sent with. Reopening a session used to show
// only the words the user typed: the `@name#v-id` token is inside that prose,
// so the reference was there to read but not to see, and after a cross-session
// copy the token names a version this session cannot resolve — the model read
// the local one. The server now stores the six fields and the conversation
// route returns them, so what is drawn here is the record, not a re-parse.
function renderMessageRefChips(host, refs) {
  if (!Array.isArray(refs) || !refs.length) return;
  const row = el("div", "msg-refs");
  refs.slice(0, 8).forEach(r => {
    const name = String((r && r.display_name) || "");
    if (!name) return;
    const chip = el("span", "msg-ref-chip");
    chip.appendChild(iconEl("file-text", 11));
    chip.appendChild(el("span", null, publicText(name, 60)));
    // Facts, not a sentence: the version actually sent, the head of its digest,
    // and — when the file was copied in — the session it came from. Deliberately
    // language-neutral, so this needs no translation key and cannot drift out of
    // step with one.
    const parts = [String(r.version_id || "")];
    if (r.sha256) parts.push("sha256:" + String(r.sha256).slice(0, 12));
    if (r.materialized_target) parts.push("↗ " + String(r.source_session || "").slice(0, 12));
    chip.title = parts.filter(Boolean).join(" · ");
    // Only opens what this client actually has. Synthesising an artifact
    // object out of the ref would hand the viewer a filename with no content
    // type and let it guess at the renderer.
    const full = (S.artifacts || []).find(x => (x.artifact_id || x.id) === r.artifact_id);
    if (full) { chip.classList.add("clickable"); chip.onclick = () => openViewer(full); }
    row.appendChild(chip);
  });
  if (row.children.length) host.appendChild(row);
}

// The same six facts as `renderMessageRefChips`, drawn *before* the send.
//
// A pinned reference was invisible until the turn came back. The autocomplete
// inserts `@name#v-...` and then it is prose in a textarea: nothing says which
// version was pinned, nothing says a file is coming from another conversation
// and will be copied in, and a token typed or pasted by hand -- or one whose
// artifact was since deleted -- looks exactly like one that resolves. The user
// found out by reading the answer.
//
// Resolved against the artifacts this client already holds, so this adds no
// route and no fetch. A token that resolves to nothing is drawn as unresolved
// rather than dropped: "this will not do what you think" is the whole reason to
// show it early, and hiding it would put the surprise back where it was.
function renderComposerRefChips() {
  const host = $("#composer-refs");
  if (!host) return;
  host.innerHTML = "";
  const text = (($("#composer") || {}).value) || "";
  // `@name` or `@name#version`, ended by whitespace. Same shape the server's
  // resolver accepts; deliberately not a second, cleverer grammar.
  const found = [];
  const seen = new Set();
  const re = /(?:^|\s)@([^\s@#]+)(?:#(v-[A-Za-z0-9_-]+))?/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const key = m[1] + "#" + (m[2] || "");
    if (seen.has(key)) continue;
    seen.add(key);
    found.push({ name: m[1], version: m[2] || "" });
    if (found.length >= 8) break;
  }
  if (!found.length) { host.classList.add("hidden"); return; }
  const pool = [...(S.artifacts || []), ...((_acFiles && _acFiles.list) || [])];
  found.forEach(ref => {
    const match = pool.find(a => a && a.filename === ref.name
      && (!ref.version || String(a.version_id || "") === ref.version));
    const chip = el("span", "msg-ref-chip" + (match ? "" : " unresolved"));
    chip.appendChild(iconEl(match ? "file-text" : "alert-triangle", 11));
    chip.appendChild(el("span", null, publicText(ref.name, 60)));
    if (!match) {
      chip.title = t("refs.unresolvedChip");
      host.appendChild(chip);
      return;
    }
    // Facts, not a sentence, and in the same order the message chip uses --
    // the two are read one above the other and must not disagree.
    const parts = [String(ref.version || match.version_id || "")];
    if (match.checksum) parts.push("sha256:" + String(match.checksum).slice(0, 12));
    const elsewhere = match.root_frame_id && S.currentId
      && match.root_frame_id !== S.currentId;
    if (elsewhere) parts.push("\u2197 " + String(match.root_frame_id).slice(0, 12));
    chip.title = parts.filter(Boolean).join(" \u00b7 ");
    if (elsewhere) chip.classList.add("elsewhere");
    chip.classList.add("clickable");
    chip.onclick = () => openViewer(match);
    host.appendChild(chip);
  });
  host.classList.remove("hidden");
}

/* ---------- session title / actions ---------- */
async function commitTitle() {
  if (!S.currentId) return;
  const name = ($("#conv-title").value || "").trim();
  if (!name || name === S._titleName) { setTitle(S._titleName); return; }
  try { await api("/frames/" + S.currentId, { method: "PATCH", body: JSON.stringify({ name }) }); S._titleName = name; setTitle(name); loadSessions(); }
  catch (e) { setTitle(S._titleName); hint(t("toast.renameFailed", apiErrorText(e)), true); }
}
function addToMessageMenu(anchor) {
  openMenu(anchor, [
    { label: t("composer.menu.attachFiles"), icon: "plus", onClick: () => $("#file-input").click() },
    { label: t("composer.menu.yourFiles"), icon: "files", onClick: () => setActiveTab("files") },
    { label: t("composer.menu.requestReview"), icon: "eye-context", onClick: requestReview },
    { label: t("composer.menu.saveAsSkill"), icon: "book", onClick: saveCurrentAsSkill },
    { sep: true },
    { label: t("composer.menu.contextUsage"), icon: "circle-dot", onClick: showContextUsage },
  ]);
}
async function showContextUsage() {
  if (!S.currentId) return;
  let frame, steps = []; try { const data = await Promise.all([api(`/frames/${S.currentId}`), api(`/frames/${S.currentId}/steps`).catch(() => ({ steps: [] }))]); frame = data[0]; steps = data[1].steps || []; } catch (e) { hint(e.message, true); return; }
  const input = Number(frame.input_tokens || 0); const output = Number(frame.output_tokens || 0);
  const reviewer = steps.filter(s => s.kind === "review").reduce((sum, s) => sum + Number(s.output && s.output.usage && ((s.output.usage.input_tokens || 0) + (s.output.usage.output_tokens || 0)) || 0), 0);
  $("#modal-title").textContent = t("composer.menu.contextUsage"); $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "";
  const card = el("div", "prov-card"); card.appendChild(el("div", "prov-h", `${(input + output).toLocaleString()} tokens`));
  card.appendChild(el("div", "prov-meta", `Input ${input.toLocaleString()} · Output ${output.toLocaleString()} · Reviewer ${reviewer.toLocaleString()}`)); body.appendChild(card);
  $("#modal").classList.remove("hidden");
}
async function saveCurrentAsSkill() {
  if (!S.currentId) { skillEditor(null); return; }
  let messages = []; try { const data = await fetchRecentMessages(S.currentId, 500); messages = data.messages || []; } catch {}
  const latestUser = [...messages].reverse().find(m => m.role === "user");
  const latestAssistant = [...messages].reverse().find(m => m.role === "assistant");
  const title = (S._titleName || "research-workflow").toLowerCase().replace(/[^a-z0-9一-龥]+/g, "-").replace(/^-|-$/g, "").slice(0, 48) || "research-workflow";
  const request = String(latestUser && latestUser.content || "").trim(); const result = String(latestAssistant && latestAssistant.content || "").trim();
  skillEditor(null, {
    name: title,
    description: request.replace(/\s+/g, " ").slice(0, 180),
    body: `# Purpose\n\n${request || "Describe when this workflow should be used."}\n\n# Procedure\n\n1. Reproduce the evidence-gathering and analysis workflow.\n2. Preserve data provenance, code, and generated artifacts.\n3. State uncertainty and do not overclaim beyond the evidence.\n\n# Example outcome\n\n${result.slice(0, 6000)}`,
  });
}
async function requestReview() {
  if (!S.currentId || S.running) return;
  S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint("Reviewing", false, true);
  try {
    await api(`/frames/${S.currentId}/review`, { method: "POST", body: "{}" });
    resumeWatch(S.currentId, S._openGen);
  } catch (e) {
    turnDone("failed"); hint(e.message, true);
  }
}
async function sessionOptionsMenu(anchor) {
  if (!S.currentId) return;
  let review = { auto_review: false, reviewer_model: "", delegation_enabled: true };
  try { review = await api(`/frames/${S.currentId}/review-settings`); } catch {}
  const checked = on => on ? "✓  " : "";
  openMenu(anchor, [
    { label: checked(review.delegation_enabled !== false) + t("composer.option.delegation"), icon: "users", onClick: async () => { const on = review.delegation_enabled === false; try { await api(`/frames/${S.currentId}/review-settings`, { method: "PATCH", body: JSON.stringify({ delegation_enabled: on }) }); hint(t("composer.option.delegation") + ` · ${on ? "On" : "Off"}`); } catch (e) { hint(e.message, true); } } },
    { label: checked(S.planMode) + t("composer.planMode"), icon: "grid", onClick: () => $("#plan-toggle").click() },
    { label: checked(S.exploreMode) + t("composer.exploreMode"), icon: "compass", onClick: () => $("#explore-toggle").click() },
    { sep: true },
    { label: checked(review.auto_review) + t("composer.option.autoReview"), icon: "eye-context", onClick: async () => { try { await api(`/frames/${S.currentId}/review-settings`, { method: "PATCH", body: JSON.stringify({ auto_review: !review.auto_review }) }); hint(t("composer.option.autoReview") + ` · ${!review.auto_review ? "On" : "Off"}`); } catch (e) { hint(e.message, true); } } },
    { label: t("composer.option.reviewerModel") + (review.reviewer_model ? ` · ${review.reviewer_model}` : ""), icon: "sliders", onClick: () => reviewerModelMenu(anchor, review.reviewer_model) },
    { label: t("composer.option.memory"), icon: "book", onClick: () => openCust("memory") },
    { label: t("composer.option.specialist"), icon: "users", onClick: () => openCust("specialists") },
    { label: t("composer.option.compute"), icon: "terminal", onClick: () => openCust("compute") },
  ]);
}
function reviewerModelMenu(anchor, current) {
  const choices = [{ id: "", name: t("composer.option.sameModel") }].concat((S.models || []).map(m => ({ id: m.id, name: m.name || m.id })));
  openMenu(anchor, choices.map(model => ({
    label: (model.id === (current || "") ? "✓  " : "") + model.name,
    icon: "circle-dot",
    onClick: async () => { try { await api(`/frames/${S.currentId}/review-settings`, { method: "PATCH", body: JSON.stringify({ reviewer_model: model.id }) }); hint(t("composer.option.reviewerModel") + ` · ${model.name}`); } catch (e) { hint(e.message, true); } },
  })));
}
function sessionMenu(anchor, fid) {
  const frame = S.sessions.find(x => x.id === fid) || {};
  const items = [{ label: t("folder.menu.rename"), icon: "pencil", onClick: () => renameFrame(fid) }];
  if (frame.running || (fid === S.currentId && S.running)) items.push({ label: t("sessionMenu.cancel"), icon: "stop", onClick: async () => {
    try { const result = await scopedExecutionRequest(fid, "cancel", "session menu cancel"); if (result && result.ok && fid === S.currentId) turnDone("cancelled"); }
    catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); }
    loadSessions();
  } });
  items.push(
    { label: t("sessionMenu.exportMarkdown"), icon: "download", onClick: () => exportSession(fid) },
    { label: t("share.menu"), icon: "share", onClick: () => openShareDialog(fid, frame) },
    { label: t("sessionPackage.export"), icon: "archive", onClick: () => exportSessionPackage(fid, frame) },
    { label: t("sessionMenu.downloadArtifacts"), icon: "files", onClick: () => downloadArtifactBundle(`${API}/frames/${encodeURIComponent(fid)}/artifacts.zip`, `${frame.name || frame.task_summary || "session"}-artifacts.zip`) },
    { label: t("sessionMenu.viewNotebook"), icon: "notebook", onClick: async () => { if (fid !== S.currentId) await openConversation(fid, frame.project_id); setActiveTab("notebook"); } },
    { label: t("compute.menu.runLocation"), icon: "server", onClick: () => openRunLocationDialog(fid) },
    { sep: true },
    { label: t("sessionMenu.duplicate"), icon: "copy", onClick: () => duplicateSession(fid) },
    { label: t("sessionMenu.moveToFolder"), icon: "folder", onClick: () => moveToFolderAt(anchor, fid) },
    { label: t("common.delete"), icon: "trash-2", danger: true, onClick: () => { if (confirm(t("confirm.deleteSession"))) deleteSession(fid); } },
  );
  openMenu(anchor, items);
}
function exportSessionPackage(fid, frame = {}) {
  const label = frame.name || frame.task_summary || "session";
  downloadArtifactBundle(
    `${API}/frames/${encodeURIComponent(fid)}/session/export`,
    label.replace(/[^\w一-龥-]+/g, "_") + ".openai4s-session.zip",
  );
}
async function openShareDialog(fid, frame = {}) {
  let status = {};
  let shares = { shares: [] };
  try {
    [status, shares] = await Promise.all([
      fetch(`${API}/share/status`).then(r => r.json()),
      fetch(`${API}/frames/${encodeURIComponent(fid)}/shares`).then(r => r.json()),
    ]);
  } catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); return; }

  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;z-index:1000";
  const box = document.createElement("div");
  box.style.cssText = "background:var(--panel,#fff);color:var(--ink,#111);max-width:520px;width:90%;border-radius:12px;padding:20px;box-shadow:0 10px 40px rgba(0,0,0,.3)";
  overlay.appendChild(box);
  const close = () => overlay.remove();
  overlay.onclick = e => { if (e.target === overlay) close(); };
  const h = document.createElement("h3"); h.textContent = t("share.title"); h.style.marginTop = "0";
  box.appendChild(h);

  const state = String(status.state || "");
  if (state === "unconfigured") {
    box.appendChild(Object.assign(document.createElement("p"),
      { textContent: t("share.unconfigured") }));
    box.appendChild(mkBtn(t("share.close"), close));
    document.body.appendChild(overlay);
    return;
  }
  if (state === "disabled") {
    box.appendChild(Object.assign(document.createElement("p"),
      { textContent: t("share.disabled") }));
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:16px";
    if (status.configured) {
      row.appendChild(mkBtn(t("share.enable"), async () => {
        await shareCall("PUT", `${API}/share/settings`, { enabled: true });
        close(); openShareDialog(fid, frame);
      }, false, true));
    }
    row.appendChild(mkBtn(t("share.close"), close));
    box.appendChild(row);
    document.body.appendChild(overlay);
    return;
  }

  const active = (shares.shares || []).find(s => s.status === "ready" || s.status === "publishing");
  const scope = document.createElement("p");
  scope.className = "muted";
  scope.style.fontSize = "13px";
  scope.textContent = t("share.scope");
  box.appendChild(scope);

  if (active) {
    const row = document.createElement("div");
    row.style.cssText = "display:flex;gap:8px;margin:12px 0";
    const inp = document.createElement("input");
    inp.readOnly = true; inp.value = active.url || "";
    inp.style.cssText = "flex:1;padding:8px;border:1px solid var(--line,#ccc);border-radius:8px";
    row.appendChild(inp);
    row.appendChild(mkBtn(t("share.copy"), () => {
      if (navigator.clipboard) navigator.clipboard.writeText(active.url || "");
      hint(t("share.copied"));
    }));
    box.appendChild(row);
    const exp = document.createElement("div");
    exp.className = "muted"; exp.style.fontSize = "12px"; exp.style.margin = "4px 0 8px";
    exp.textContent = active.expires_at
      ? t("share.expiresAt") + " " + new Date(active.expires_at).toLocaleString()
      : t("share.neverExpires");
    box.appendChild(exp);
    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:16px";
    actions.appendChild(mkBtn(t("share.update"), async () => {
      await shareCall("PUT", `${API}/shares/${encodeURIComponent(active.share_id)}`);
      hint(t("share.updated")); close();
    }));
    actions.appendChild(mkBtn(t("share.revoke"), async () => {
      if (!confirm(t("share.revokeConfirm"))) return;
      await shareCall("DELETE", `${API}/shares/${encodeURIComponent(active.share_id)}`);
      hint(t("share.revoked")); close();
    }, true));
    actions.appendChild(mkBtn(t("share.close"), close));
    box.appendChild(actions);
  } else {
    const expRow = document.createElement("div");
    expRow.style.cssText = "display:flex;align-items:center;gap:8px;margin:12px 0";
    const expLabel = document.createElement("span");
    expLabel.className = "muted"; expLabel.style.fontSize = "13px";
    expLabel.textContent = t("share.expiry");
    const sel = document.createElement("select");
    sel.style.cssText = "padding:6px;border:1px solid var(--line,#ccc);border-radius:8px";
    [[0, t("share.expiry.never")], [86400, t("share.expiry.1d")],
     [604800, t("share.expiry.7d")], [2592000, t("share.expiry.30d")]]
      .forEach(([secs, label]) => {
        const o = document.createElement("option"); o.value = String(secs); o.textContent = label;
        sel.appendChild(o);
      });
    sel.value = "604800";  // default 7 days
    expRow.appendChild(expLabel); expRow.appendChild(sel);
    box.appendChild(expRow);
    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end;margin-top:16px";
    actions.appendChild(mkBtn(t("share.create"), async () => {
      const body = {};
      const secs = parseInt(sel.value, 10);
      if (secs > 0) body.expires_in = secs;
      const rec = await shareCall("POST", `${API}/frames/${encodeURIComponent(fid)}/shares`, body);
      close();
      if (rec && rec.url) openShareDialog(fid, frame);
    }, false, true));
    actions.appendChild(mkBtn(t("share.close"), close));
    box.appendChild(actions);
  }
  document.body.appendChild(overlay);

  function mkBtn(label, onClick, danger, primary) {
    const b = document.createElement("button");
    b.textContent = label;
    b.className = danger ? "danger" : (primary ? "primary" : "");
    b.style.cssText = "padding:7px 14px;border-radius:8px;cursor:pointer;border:1px solid var(--line,#ccc)" +
      (primary ? ";background:var(--accent,#2b6cb0);color:#fff" : "");
    b.onclick = onClick;
    return b;
  }
  async function shareCall(method, path, body) {
    try {
      const r = await fetch(path, {
        method,
        headers: body ? { "Content-Type": "application/json" } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new ApiError(j, r.status);
      return j;
    } catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); return null; }
  }
}
function chooseSessionPackage() {
  const input = $("#session-package-input");
  if (input) input.click();
}
async function importSessionPackage(file) {
  if (!file) return;
  if (file.size > 128 * 1024 * 1024) { hint(t("sessionPackage.tooLarge"), true); return; }
  try {
    // Verify before importing, not as an optional extra afterwards. The
    // package arrived from somewhere else; checking it against its own
    // manifest costs one request and is the whole point of shipping hashes.
    // A tampered archive must never reach the database.
    const checked = await fetch(API + "/sessions/verify", {
      method: "POST",
      headers: { "Content-Type": "application/vnd.openai4s.session+zip" },
      body: file,
    });
    const verdict = await checked.json().catch(() => ({}));
    if (!checked.ok || !verdict.ok) {
      const first = (verdict.problems || [])[0] || verdict.error || "";
      hint(t("sessionPackage.verifyFailed", publicText(first, 160)), true);
      return;
    }
    hint(t("sessionPackage.verified", (verdict.files_verified || []).length));
    const response = await fetch(API + "/sessions/import", {
      method: "POST",
      headers: { "Content-Type": "application/vnd.openai4s.session+zip" },
      body: file,
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.root_frame_id || !result.project_id) {
      throw new ApiError(result, response.status);
    }
    await loadProjects();
    hint(t("sessionPackage.imported"));
    await openConversation(result.root_frame_id, result.project_id);
  } catch (error) {
    hint(t("toast.importFailed", apiErrorText(error)), true);
  }
}
function downloadArtifactBundle(url, filename) {
  const link = document.createElement("a"); link.href = url; link.download = filename || "artifacts.zip";
  document.body.appendChild(link); link.click(); link.remove();
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
      fetchAllMessages(fid),
      api(`/frames/${fid}/artifacts`).catch(() => []),
    ]);
    const f = S.sessions.find(x => x.id === fid) || {};
    let md = "# " + (f.name || f.task_summary || t("conv.title.default")) + "\n\n";
    if (d.complete === false) md += "> " + t("conv.exportTruncated") + "\n\n";
    (d.messages || []).forEach(m => { const who = m.role === "user" ? "🧑 User" : "🤖 Assistant"; const txt = Array.isArray(m.content) ? m.content.map(b => b.text || "").join("") : (m.content || ""); md += `## ${who}\n\n${txt}\n\n`; });
    if ((arts || []).length) { md += "## 产物 Artifacts\n\n"; arts.forEach(a => md += `- ${a.filename} (${a.content_type || ""})\n`); }
    const blob = new Blob([md], { type: "text/markdown" }); const url = URL.createObjectURL(blob); const link = document.createElement("a");
    link.href = url; link.download = (f.name || f.task_summary || "session").replace(/[^\w一-龥-]+/g, "_") + ".md"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000); hint(t("toast.exportedMarkdown"));
  } catch (e) { hint(t("toast.exportFailed", apiErrorText(e)), true); }
}
async function renameFrame(fid) {
  const f = S.sessions.find(x => x.id === fid);
  if (fid !== S.currentId) await openConversation(fid, f && f.project_id);
  const ct = $("#conv-title"); ct.focus(); ct.select();
}
async function deleteSession(fid) {
  try { await api("/frames/" + fid, { method: "DELETE" }); } catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); return; }
  const wasCurrent = fid === S.currentId; await loadSessions();
  if (wasCurrent) { let ss = S.sessions; if (S.project) ss = ss.filter(f => f.project_id === S.project); if (ss.length) openConversation(ss[0].id, ss[0].project_id); else { S.currentId = null; $("#messages").innerHTML = ""; setTitle(t("conv.title.default")); S.artifacts = []; renderFilesGrid(); } }
}
async function duplicateSession(fid) {
  const f = S.sessions.find(x => x.id === fid) || {};
  try {
    const nf = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: f.project_id || S.project || undefined, model: S.defaultModelName }) });
    const nm = (f.name || f.task_summary || t("conv.title.default")) + t("session.duplicateSuffix");
    try { await api("/frames/" + nf.id, { method: "PATCH", body: JSON.stringify({ name: nm }) }); } catch {}
    await loadSessions(); openConversation(nf.id, f.project_id);
  } catch (e) { hint(t("toast.duplicateFailed", apiErrorText(e)), true); }
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
  let top = r.bottom + 4;
  if (top + m.offsetHeight > window.innerHeight - 8) top = Math.max(8, r.top - m.offsetHeight - 4);
  m.style.top = top + "px";
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
async function cancelTurn() {
  if (!S.currentId) return;
  try { const result = await scopedExecutionRequest(S.currentId, "cancel", "composer cancel"); if (result && result.ok) turnDone("cancelled"); }
  catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); }
}

/* ---------- standard environment readiness ---------- */
function sanitizeStandardProfileReadiness(value) {
  if (!value || typeof value !== "object") return null;
  const allowedStates = new Set(["ready", "needs_setup", "needs_repair", "unavailable"]);
  const state = allowedStates.has(value.state) ? value.state : "unavailable";
  const missingEnvironments = (Array.isArray(value.missing_environments) ? value.missing_environments : [])
    .map(name => publicText(name, 160)).filter(Boolean);
  const missingPackages = {};
  const sourcePackages = value.missing_packages && typeof value.missing_packages === "object" ? value.missing_packages : {};
  Object.keys(sourcePackages).sort().forEach(name => {
    const environment = publicText(name, 160);
    if (!environment) return;
    missingPackages[environment] = (Array.isArray(sourcePackages[name]) ? sourcePackages[name] : [])
      .map(packageName => publicText(packageName, 160)).filter(Boolean);
  });
  const sourceRemediation = value.remediation && typeof value.remediation === "object" ? value.remediation : null;
  const commands = [];
  if (sourceRemediation && sourceRemediation.requires_explicit_action === true) {
    const candidates = Array.isArray(sourceRemediation.commands)
      ? sourceRemediation.commands
      : (sourceRemediation.command ? [sourceRemediation] : []);
    candidates.forEach(candidate => {
      if (!candidate || typeof candidate !== "object" || typeof candidate.command !== "string") return;
      const command = candidate.command;
      if (!command || command.length > 1000) return;
      commands.push({ command, label: publicText(candidate.label, 120) });
    });
  }
  return {
    schema_version: Number(value.schema_version) || 0,
    enabled: value.enabled === true,
    ready: value.ready === true && state === "ready",
    state,
    reason: publicText(value.reason, 120),
    requirements_digest: publicText(value.requirements_digest, 96),
    missing_environments: missingEnvironments,
    missing_packages: missingPackages,
    remediation: sourceRemediation ? {
      requires_explicit_action: sourceRemediation.requires_explicit_action === true,
      commands,
    } : null,
  };
}
function environmentReadinessSummary(readiness) {
  if (!readiness || readiness.state === "unavailable") return t("environment.readiness.bannerUnavailable");
  const packageCount = Object.values(readiness.missing_packages || {}).reduce((count, names) => count + names.length, 0);
  return t("environment.readiness.bannerMissing", readiness.missing_environments.length, packageCount);
}
function renderEnvironmentReadinessBanner() {
  const readiness = S.standardProfileReadiness;
  const visible = !!(readiness && readiness.enabled === true && readiness.ready !== true);
  document.querySelectorAll(".environment-readiness-banner").forEach(banner => {
    banner.classList.toggle("hidden", !visible);
    if (!visible) return;
    const title = banner.querySelector("[data-environment-readiness-title]");
    const summary = banner.querySelector("[data-environment-readiness-summary]");
    const action = banner.querySelector("[data-open-environment-readiness]");
    if (title) title.textContent = t("environment.readiness.bannerTitle");
    if (summary) summary.textContent = environmentReadinessSummary(readiness);
    if (action) action.textContent = t("environment.readiness.openCompute");
  });
}
async function refreshEnvironmentStatus() {
  if (S._environmentStatusPromise) return S._environmentStatusPromise;
  S._environmentStatusPromise = (async () => {
    try {
      const payload = await api("/environments/status");
      S._environmentStatusRefreshFailed = false;
      S.environmentStatus = payload && typeof payload === "object" ? payload : null;
      S.standardProfileReadiness = sanitizeStandardProfileReadiness(
        S.environmentStatus && S.environmentStatus.standard_profile_readiness
      );
    } catch (error) {
      S._environmentStatusRefreshFailed = true;
      // A server that predates the opt-in field behaves exactly as before. If
      // this browser already knows the feature is enabled, however, losing the
      // refresh cannot turn the last snapshot into a claim of readiness.
      if (S.standardProfileReadiness && S.standardProfileReadiness.enabled === true) {
        S.standardProfileReadiness = {
          ...S.standardProfileReadiness,
          ready: false,
          state: "unavailable",
          reason: "status_refresh_failed",
        };
      }
    }
    renderEnvironmentReadinessBanner();
    return S.environmentStatus;
  })();
  try { return await S._environmentStatusPromise; }
  finally { S._environmentStatusPromise = null; }
}
function isEnvironmentReadinessError(error) {
  return !!(error && (
    (error.status === 409 && error.code === "environment_not_ready")
    || (error.status === 503 && error.code === "environment_readiness_unavailable")
  ));
}
function handleEnvironmentReadinessTerminal(detail) {
  const code = detail && detail.code;
  if (!detail || detail.status !== "failed" || ![
    "environment_not_ready",
    "environment_readiness_unavailable",
  ].includes(code)) return false;
  // A control-only turn is allowed even while the scientific profile is
  // incomplete.  If routing selected a Code Cell, the server rejects it before
  // runtime side effects and this terminal event is the authoritative signal.
  void refreshEnvironmentStatus().finally(() => {
    openCust("compute");
    hint(t("environment.readiness.sendBlocked"), true);
  });
  return true;
}

/* ---------- send ---------- */
async function send(text, opts) {
  text = (text || "").trim(); opts = opts || {};
  // Sending mid-turn queues instead of being dropped on the floor. The old
  // `if (S.running) return;` silently discarded the message — no error, no
  // hint, just a composer that had already been cleared — even though the
  // server's FIFO admission has always accepted a follow-up here.
  const queueing = S.running;
  const runtime = runtimeSummary();
  if (S.currentId && runtime.viewOnly && runtime.trustState === "quarantined") {
    hint(t("runtime.quarantineHint"), true);
    return;
  }
  const anns = openAnnotations();                 // pinned image comments to ride along
  if (!text && !anns.length) return;              // nothing to send
  const planNow = S.planMode && !opts.execute;
  const exploreNow = S.exploreMode && !planNow && !opts.execute;
  // Readiness is visible before send, but admission belongs to the first Code
  // Cell. Keeping ordinary sends routable preserves native-tool and structured-
  // finalization turns that never need a kernel.
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
  if (!S.currentId) { const f = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: S.project || undefined, model: S.defaultModelName }) }); S.currentId = f.id; sub(f.id); await loadSessions(); }
  const g = $(".generated"); if (g) g.remove();
  const es = $(".empty-session"); if (es) es.remove();
  const w = el("div", "msg user"); const b = el("div", "bubble"); b.textContent = text || t("send.imageAnnotationFallback"); w.appendChild(b);
  if (anns.length) w.appendChild(annotAttachment(anns));
  if (queueing) w.classList.add("queued");
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
  // Queueing must not restate the turn banner: the running turn owns the hint
  // and the Stop button, and re-announcing "Running…" here would make the Stop
  // control read as belonging to the item just typed rather than to the one it
  // would actually interrupt.
  // Read AFTER every await above and BEFORE this send touches `S.running`.
  // The snapshot at the top of `send` is only good enough to decide how the
  // bubble looks: the skills catalogue and, on a first message, creating the
  // frame all await, and another tab or a recovered turn can take ownership in
  // that window. Reading it any later is worse than useless -- by then this
  // very send has set it to true and every observation says "queued".
  const sawRunningAtDispatch = S.running;
  const turnTicket = sawRunningAtDispatch ? null : openTurnTicket();
  // Declared out here, not inside the `try`: the catch needs it, and a
  // block-scoped `const` would have made that a ReferenceError.
  if (!turnTicket) hint(t("queue.accepted"));
  else { S.running = true; enableComposer(false); $("#cancel-btn").classList.remove("hidden"); hint(t("toast.running"), false, true); }
  $("#composer").value = ""; grow(); renderComposerRefChips();
  const annIds = anns.map(x => x.id);
  // The admission id is generated HERE and stored BEFORE the request goes out.
  //
  // That ordering is the whole mechanism. The case this exists for is the one
  // where the client never sees the response -- a dropped connection, a closed
  // tab, a reload mid-flight -- and a server-minted id is unknown to a browser
  // in exactly that case, so there is nothing left to ask about. Storing it
  // after the promise resolves covers only the case that needed no help.
  //
  // 128 bits from the platform CSPRNG: it keys a claim on the user's own
  // unpublished comments, and it has to survive collision across sessions and
  // restarts.
  let admissionId = "";
  if (annIds.length) {
    const bytes = new Uint8Array(16);
    (self.crypto || window.crypto).getRandomValues(bytes);
    admissionId = "resv-" + [...bytes].map(b => b.toString(16).padStart(2, "0")).join("");
    rememberAdmission(S.currentId, admissionId);
    // Optimistically "pending", never "sent". The server decides whether a pin
    // was consumed, and it can answer `pending` -- accepted, but the consume
    // did not confirm -- in which case the comment is neither gone nor
    // available and must not be shown as either.
    setLocalAnnotationStatus(annIds, "pending"); refreshAllStages(); updateAnnotBadge();
  }
  sub(S.currentId);  // guarantee this client is subscribed BEFORE the POST spawns the
                     // turn thread. On the FIRST turn opened via newSession(), S.currentId
                     // is already set so the block above is skipped and openConversation's
                     // late sub() may not have run yet — without this, run_message() emits
                     // text_reset/text_chunk before rid is in conn.subs and broadcast()
                     // drops them (server replay is gated on is_running, which is already
                     // false once the blocking POST returns). Idempotent set add.
  try {
    // No `model:` field. It carried the header selector's value on every single
    // message, and the server preferred it over the session's pinned revision --
    // so provider, endpoint and credential came from the pin while the model name
    // came from here, a configuration that exists in no profile. Changing model is
    // now activating a profile (PUT /models/default), which the session then binds.
    const accepted = await api(`/frames/${S.currentId}/message`, { method: "POST", body: JSON.stringify({ input_data: { request: payload }, plan: planNow, explore: exploreNow, annotation_ids: annIds, annotation_reservation_id: admissionId || undefined, wait: false }) });
    // Tie the optimistic bubble to the ticket the 202 named, so cancelling that
    // exact queued item can mark the message the user is looking at. Nothing
    // else in the transcript carries an execution id.
    if (accepted && accepted.execution_id) w.dataset.executionId = accepted.execution_id;
    // The id this turn will be blamed under. `wait:false` means the 202 is the
    // only synchronous thing the client gets, so this is where the correlation
    // starts -- the socket event and the job query name the same one. Written
    // through the generation guard, because this promise can resolve after the
    // turn it belongs to has already failed and been cleaned up.
    // Accepted, or retired. A 202 that is not `queue_position: 0` means this
    // send is not the running turn after all -- it was queued, or the snapshot
    // could not be read -- so its provisional ticket is dead and its id will
    // arrive on its own `processing` event instead. Retiring touches nothing
    // that belongs to another turn.
    if (!acceptTurnTicket(turnTicket, accepted)) retireTurnTicket(turnTicket);
    // The server's own answer, not a guess. `annotations` is `sent`, `pending`
    // or `none`, and `annotation_reservation_id` is what a reconcile asks
    // about after a reload. Only `sent` is a consumed pin; `none` means this
    // request claimed nothing (a concurrent turn won the race) and the comment
    // is still the user's to send.
    if (annIds.length) {
      const said = accepted && accepted.annotations;
      if (said === "none") setLocalAnnotationStatus(annIds, "open");
      else if (said === "sent") setLocalAnnotationStatus(annIds, "sent");
      if (accepted && accepted.annotation_reservation_id) {
        S.lastAnnotationReservation = accepted.annotation_reservation_id;
      }
      // The answer arrived, so there is nothing left to reconcile. Removing it
      // here rather than on the next reload keeps storage to what is genuinely
      // outstanding -- and only a decided answer removes anything.
      if (admissionId && admissionSettled(said)) forgetAdmission(S.currentId, admissionId);
      try { await loadAnnotations(S.currentId); } catch {}
      refreshAllStages(); updateAnnotBadge();
    }
  }
  catch (e) {
    if (annIds.length) {
      // A POST that failed *synchronously* released its reservation server-side,
      // so the pins are `open` again and this reload will say so. That is a
      // narrower claim than the one this comment used to make. It said "the
      // POST failed, therefore they were never consumed" -- which was false
      // twice over. The route used to burn them to 'sent' *before*
      // `submit_message`, where every refusal happens, so a 413/409/429
      // destroyed them; and a failure with no response at all (a dropped
      // connection after the server accepted) is not a refusal, so treating it
      // as one reopens pins the running turn is already carrying.
      //
      // Hence: reconcile from the server rather than deciding locally, and the
      // local revert below is only the fallback for when the server cannot be
      // reached at all -- where leaving the comments visible is the lesser
      // wrong, because a duplicate is recoverable and a silent loss is not.
      // Reconcile with the
      // server if reachable; if that reload also fails, revert the optimistic
      // "sent" flip locally so the pending comments stay visible for a retry
      // instead of vanishing from the composer.
      // Reopen only on an authoritative *synchronous* refusal -- a status the
      // server actually answered with, which means the reservation was
      // released and the pins are the user's again. A transport failure with
      // no status is not a refusal: the turn may be running and carrying them,
      // and reopening would offer the user a comment that is already on its
      // way. Ambiguity stays `pending` and is reconciled from the server.
      const refused = !!(e && Number.isInteger(e.status) && e.status >= 400);
      // An authoritative synchronous refusal is an answer: the server released
      // the reservation and said so, so there is nothing to ask it later. A
      // transport failure with no status is the ambiguous case this mechanism
      // exists for, and its id is kept.
      if (admissionId && refused) forgetAdmission(S.currentId, admissionId);
      const reloaded = await loadAnnotations(S.currentId);
      if (!reloaded) setLocalAnnotationStatus(annIds, refused ? "open" : "pending");
      refreshAllStages(); updateAnnotBadge();
    }
    // The preflight is advisory UX; the server remains authoritative. A
    // readiness transition between GET and POST is surfaced by these exact
    // contracts. Refresh the durable banner/card and give the rejected text
    // back instead of leaving an optimistic message that never ran.
    if (isEnvironmentReadinessError(e)) {
      await refreshEnvironmentStatus();
      // The POST itself proved that readiness admission is enabled. If the
      // requested refresh also failed, retain that authoritative fact as an
      // unavailable snapshot so the persistent banner cannot disappear and
      // imply readiness. A successful refresh (including an explicit flag-off
      // payload) remains authoritative and is not overwritten here.
      if (S._environmentStatusRefreshFailed) {
        S.standardProfileReadiness = {
          schema_version: 1,
          enabled: true,
          ready: false,
          state: "unavailable",
          reason: "status_refresh_failed",
          requirements_digest: null,
          missing_environments: [],
          missing_packages: {},
          remediation: null,
        };
        renderEnvironmentReadinessBanner();
      }
      const composer = $("#composer");
      if (composer && !composer.value.trim()) composer.value = text;
      if (composer) { grow(); renderComposerRefChips(); }
      w.remove();
      if (ownsTurnTicket(turnTicket)) turnDone("failed");
      openCust("compute");
      hint(t("environment.readiness.sendBlocked"), true);
      loadSessions();
      return;
    }
    // The two 409s a send can end on that the user cannot resolve from
    // anywhere else in the app. Both say "choose one to continue" and both are
    // answered by the same rebind: the binding is in no PATCH allowlist and
    // forking inherits it, so without this the session is unsendable for good.
    //
    // `model_revision_ambiguous` was left out. It is raised when a legacy
    // session's recorded model matches more than one profile -- the server
    // refuses to guess, which is right, and then the only remedy was
    // unreachable. Same predicament, same fix, one code away.
    if (e && (e.code === "model_revision_unavailable"
              || e.code === "model_revision_ambiguous")) {
      if (confirm(t("model.rebind.confirm"))) {
        try {
          await api(`/frames/${encodeURIComponent(S.currentId)}/model-binding`, { method: "POST" });
          hint(t("model.rebind.done"));
          // Ownership, not the dispatch snapshot: the rebind prompt is modal
          // and the user can sit on it for a long time, which is more than
          // enough for another turn to start and own the screen.
          if (ownsTurnTicket(turnTicket)) turnDone("failed");
          loadSessions();
          return;
        } catch (rebindError) { hint(apiErrorText(rebindError), true); }
      }
    }
    hint(t("toast.sendFailed", apiErrorText(e)), true);
    // A follow-up that was refused (413/409/429) says nothing about the turn
    // already running. Tearing the turn state down here would report the
    // running turn as failed because a *different*, never-admitted message was
    // rejected — and would re-enable Stop against a turn nobody stopped.
    // Ownership, not the stale snapshot. A send that began idle can be
    // rejected *after* another turn has started -- the `false -> true` case --
    // and tearing down there reports someone else's running turn as failed and
    // re-enables Stop against a turn nobody stopped. If this send no longer
    // owns the turn, its own bubble is the only thing that changes.
    if (ownsTurnTicket(turnTicket)) turnDone("failed");
    else w.classList.add("cancelled");
    loadSessions();
    return;
  }
  // The async POST returns as soon as the job is accepted. Keep the composer
  // locked until the authoritative WebSocket frame_update arrives; the status
  // watchdog covers a missed terminal event after reconnects.
  resumeWatch(S.currentId, S._openGen);
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

/* ---------- where a session runs (M3b-6) ----------
   Three things the user cannot infer from a spinner:
   WHERE the kernel is, WHICH of the four readiness conditions is still
   outstanding, and WHETHER the kernel's memory was lost and quietly
   replaced. The last one is not a nicety -- results produced after a
   recovery look exactly like results from the session that was lost, so
   INV-11 makes saying so mandatory. */
const COMPUTE_BLOCKED_LABEL = {
  allocation: "compute.blocked.allocation",
  workspace: "compute.blocked.workspace",
  worker: "compute.blocked.worker",
  kernel: "compute.blocked.kernel",
};
async function loadComputeStatus(fid) {
  if (!fid) return null;
  try { return await api(`/sessions/${encodeURIComponent(fid)}/compute`); }
  catch { return null; }   // a daemon without the feature is not an error state
}
async function refreshComputeStatus(fid) {
  const status = await loadComputeStatus(fid);
  if (!fid || fid !== S.currentId) return status;   // session switched mid-flight
  S.computeStatus = status;
  renderComputeBadge();
  renderComputeLostBanner();
  return status;
}
function renderComputeBadge() {
  const host = $(".conv-head-actions");
  if (!host) return;
  let badge = $("#compute-badge");
  const status = S.computeStatus;
  // A local session gets no badge at all. A chip reading "local" on every
  // session in an install that has no cluster is pure noise.
  if (!status || status.location !== "cluster") { if (badge) badge.remove(); return; }
  if (!badge) {
    badge = el("button", "compute-badge"); badge.id = "compute-badge";
    badge.onclick = () => openRunLocationDialog(S.currentId);
    host.insertBefore(badge, host.firstChild);
  }
  const readiness = status.readiness || {};
  const allocation = status.allocation || {};
  const workload = status.workload || {};
  badge.innerHTML = "";
  badge.appendChild(iconEl("server", 13));
  const ready = !!readiness.ready;
  const blockedKey = COMPUTE_BLOCKED_LABEL[readiness.blocked_on];
  const label = ready
    ? `${workload.profile || t("compute.badge.ready")}`
    : (blockedKey ? t(blockedKey) : (allocation.phase || workload.phase || "").toLowerCase());
  badge.appendChild(el("span", "cb-label", label));
  badge.className = "compute-badge" + (ready ? " ready" : " waiting");
  // The phase is the tooltip rather than the label: an allocation id and a
  // phase name are what a support conversation needs, and neither belongs
  // in a chip somebody reads fifty times a day.
  badge.title = [
    workload.profile ? `profile: ${workload.profile}` : "",
    allocation.allocation_id ? `allocation: ${allocation.allocation_id}` : "",
    allocation.phase ? `phase: ${allocation.phase}` : "",
    workload.reason ? `reason: ${workload.reason}` : "",
  ].filter(Boolean).join("\n");
}
function renderComputeLostBanner() {
  const status = S.computeStatus || {};
  const epochs = status.state_lost_epochs || [];
  const seen = S._computeLostSeen || (S._computeLostSeen = {});
  const key = S.currentId + ":" + epochs.join(",");
  let banner = $("#compute-lost");
  if (!epochs.length || seen[key]) { if (banner) banner.remove(); return; }
  if (!banner) {
    banner = el("div", "compute-lost"); banner.id = "compute-lost";
    const messages = $("#messages");
    if (!messages) return;
    messages.parentNode.insertBefore(banner, messages);
  }
  banner.innerHTML = "";
  banner.appendChild(iconEl("alert-triangle", 15));
  const text = el("div", "cl-text");
  text.appendChild(el("strong", null, t("compute.lost.title")));
  text.appendChild(el("div", "cl-body", t("compute.lost.body")));
  banner.appendChild(text);
  const dismiss = el("button", "cl-dismiss", t("compute.lost.dismiss"));
  // Dismissal is per (session, set of lost epochs): a *further* loss
  // raises it again rather than being swallowed by an earlier "got it".
  dismiss.onclick = () => { seen[key] = true; banner.remove(); };
  banner.appendChild(dismiss);
}
async function openRunLocationDialog(fid) {
  const target = fid || S.currentId;
  if (!target) return;
  let status = null, catalog = { profiles: [] };
  try {
    [status, catalog] = await Promise.all([
      loadComputeStatus(target),
      api("/orchestration/profiles").catch(() => ({ profiles: [] })),
    ]);
  } catch (error) { hint(apiErrorText(error), true); return; }

  const wrap = el("div", "run-location");
  const current = (status && status.location) || "local";
  const choose = async (profile) => {
    try {
      if (profile === null) await api(`/sessions/${encodeURIComponent(target)}/compute/release`, { method: "POST", body: "{}" });
      else await api(`/sessions/${encodeURIComponent(target)}/compute`, { method: "POST", body: JSON.stringify({ profile }) });
      closeModalEl($("#modal"));
      await refreshComputeStatus(target);
    } catch (error) {
      // The 409 that means "this daemon has no listener" is the one a user
      // will actually hit, and its body already explains itself.
      hint(apiErrorText(error), true);
    }
  };

  const local = el("button", "rl-option" + (current === "local" ? " current" : ""));
  local.appendChild(el("div", "rl-name", t("compute.location.local")));
  local.appendChild(el("div", "rl-hint", t("compute.location.localHint")));
  local.onclick = () => (current === "local" ? closeModalEl($("#modal")) : choose(null));
  wrap.appendChild(local);

  const profiles = (catalog && catalog.profiles) || [];
  if (!profiles.length) wrap.appendChild(el("div", "rl-empty", t("compute.dialog.notConfigured")));
  profiles.forEach(profile => {
    const option = el("button", "rl-option" + (current === "cluster" && status.workload && status.workload.profile === profile.name ? " current" : ""));
    option.appendChild(el("div", "rl-name", profile.name));
    const bits = [
      `${profile.cpus} CPU`,
      profile.gpus ? `${profile.gpus} GPU` : "",
      `${Math.round((profile.memory_mb || 0) / 1024)} GiB`,
      `${Math.round((profile.walltime_s || 0) / 3600)} h`,
    ].filter(Boolean).join(" · ");
    option.appendChild(el("div", "rl-hint", bits));
    option.onclick = () => choose(profile.name);
    wrap.appendChild(option);
  });

  if (current === "cluster") {
    const release = el("button", "rl-release", t("compute.dialog.release"));
    release.onclick = () => choose(null);
    wrap.appendChild(release);
  }
  $("#modal-title").textContent = t("compute.dialog.title");
  const download = $("#modal-download"); if (download) download.style.display = "none";
  const body = $("#modal-body"); body.innerHTML = ""; body.appendChild(wrap);
  openModalEl($("#modal"));
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
    // The option value is a profile_id now, so the model *name* has to be carried
    // separately for the display-only `model` field on session creation. Sending
    // the id there would store a profile id in `frames.model`.
    const nameFor = id => { const e = S.models.find(x => x.id === id); return (e && (e.model || e.name)) || id; };
    S.defaultModelName = nameFor(S.defaultModel);
    const sel = $("#model-select"); sel.innerHTML = "";
    if (!S.models.length) { const o = el("option", null, t("models.none")); o.value = ""; sel.appendChild(o); }
    S.models.forEach(md => { const o = el("option", null, md.name || md.id); o.value = md.id; if (md.id === S.defaultModel) o.selected = true; sel.appendChild(o); });
    sel.onchange = async () => { S.defaultModel = sel.value; S.defaultModelName = nameFor(sel.value); try { await api("/models/default", { method: "PUT", body: JSON.stringify({ model_id: sel.value }) }); } catch {} };
  } catch { $("#model-select").innerHTML = "<option>" + t("models.none") + "</option>"; }
}

/* ---------- artifacts (inline + files) ---------- */
function artifactCacheKey(a) {
  if (!a || !a.id) return "_live";
  const seen = S._artVer && S._artVer[a.id];
  const version = seen || a.version_id || a.latest_version_id || a.checksum || "unknown";
  return a.id + ":" + version;
}
function syncArtifactVersion(patch, force) {
  const aid = patch && (patch.id || patch.artifact_id);
  if (!aid) return false;
  const version = patch.version_id || patch.latest_version_id || patch.checksum;
  const seen = S._artVer || (S._artVer = {});
  const dockMatch = !!(S.dockArtifact && S.dockArtifact.id === aid);
  const previous = seen[aid] || (dockMatch && (S.dockArtifact.version_id || S.dockArtifact.latest_version_id || S.dockArtifact.checksum));
  const changed = !!(version && previous && previous !== version);
  if (version) seen[aid] = version;
  const update = Object.assign({}, patch, { id: aid });
  if (version) update.version_id = version;
  (S.openTabs || []).forEach(item => { if (item.id === aid) Object.assign(item, update); });
  if (dockMatch) Object.assign(S.dockArtifact, update);
  if (dockMatch && (changed || force)) {
    S.lineage = null; S._lineageFor = null;
    S._lineageReq = (S._lineageReq || 0) + 1;
    const key = artifactCacheKey(S.dockArtifact);
    if (S._envSnapById) delete S._envSnapById[key];
  }
  return changed || (dockMatch && !!force);
}
async function loadArtifacts(id) {
  const request = S._artifactLoadReq = (S._artifactLoadReq || 0) + 1;
  let a = []; try { a = await api(`/frames/${id}/artifacts`); } catch { a = []; }
  if (id !== S.currentId || request !== S._artifactLoadReq) return;
  a = Array.isArray(a) ? a : [];
  // Bust the URL cache of any artifact whose latest version changed since we last
  // saw it (covers overwrite-in-place edits even if the live event was missed).
  let refreshProv = false;
  a.forEach(x => {
    const v = x.version_id || x.latest_version_id || x.checksum;
    const changed = syncArtifactVersion(x, false);
    if (changed && v) (S._artBust = S._artBust || {})[x.id] = v;
    if (changed && S.provMode && S.dockArtifact && S.dockArtifact.id === x.id) refreshProv = true;
  });
  S.artifacts = a; renderConversationArtifacts();
  if (refreshProv && S.dockArtifact) showProvenance(S.dockArtifact);
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
    g.appendChild(tiles);
    const host = $("#messages"); let review = host.querySelector(".step-review");
    while (review && review.parentElement !== host) review = review.parentElement;
    host.insertBefore(g, review || null);
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
function artUrl(a) { const b = (S._artBust || {})[a.id]; return `${API}/artifacts/${a.id}` + (b ? `?_=${b}` : ""); }
function scientificRenderers() { return window.OpenAI4SScientificRenderers || null; }
function artifactRendererVersion(a) { return a && (a.version_id || a.latest_version_id) || ""; }
function loadRendererCatalog() {
  if (Array.isArray(S.rendererCatalog)) return Promise.resolve(S.rendererCatalog);
  if (S._rendererCatalogPromise) return S._rendererCatalogPromise;
  S._rendererCatalogPromise = api("/renderers").then(result => {
    const catalog = result && Array.isArray(result.renderers) ? result.renderers.filter(item => item && typeof item.renderer_id === "string") : [];
    S.rendererCatalog = catalog;
    return catalog;
  }).catch(() => {
    S.rendererCatalog = [];
    return [];
  });
  return S._rendererCatalogPromise;
}
function compatibilityRendererDescriptor(a) {
  const ct = String(a.content_type || "").toLowerCase().split(";", 1)[0];
  const nm = String(a.filename || "").toLowerCase();
  let id = "download";
  if (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm)) id = "image";
  else if (ct === "application/pdf" || nm.endsWith(".pdf")) id = "pdf";
  else if (ct === "text/html" || /\.html?$/i.test(nm)) id = "html-preview";
  else if (/\.(pdb|cif|mmcif|ent|xyz)$/i.test(nm)) id = "molecule-3d";
  else if (/\.(mol|mol2|sdf|smi|smiles)$/i.test(nm)) id = "chemistry-2d";
  else if (/\.(bed|bedgraph|gff3?|gtf|vcf)$/i.test(nm)) id = "genome-track";
  else if (/\.(aln|a2m|a3m|sto|stockholm)$/i.test(nm)) id = "msa";
  else if (/\.(fa|fasta|faa|fna|fastq|fq)$/i.test(nm)) id = "sequence";
  else if (/\.(md|markdown|rst)$/i.test(nm)) id = "markdown";
  else if (/\.tex$/i.test(nm)) id = "latex";
  else if (/csv|tab-separated/.test(ct) || /\.(csv|tsv)$/i.test(nm)) id = "table";
  else if (ct.startsWith("text/") || /json/.test(ct) || TEXT_EXT.test(nm)) id = "text";
  return { artifact_id: a.id, version_id: artifactRendererVersion(a), matched_by: "compatibility", renderer: { renderer_id: id, label: id }, trusted_html: false };
}
function artifactRendererDescriptor(a) {
  const version = artifactRendererVersion(a);
  const key = `${a.id}:${version || "latest"}`;
  S.rendererDescriptors = S.rendererDescriptors || {};
  if (S.rendererDescriptors[key]) return S.rendererDescriptors[key];
  const suffix = version ? `?version=${encodeURIComponent(version)}` : "";
  const request = Promise.all([
    loadRendererCatalog(),
    api(`/artifacts/${encodeURIComponent(a.id)}/renderer${suffix}`),
  ]).then(([catalog, descriptor]) => {
    if (!descriptor || descriptor.artifact_id !== a.id) throw new Error("renderer descriptor does not match artifact");
    if (version && descriptor.version_id && descriptor.version_id !== version) throw new Error("renderer descriptor does not match artifact version");
    const runtime = scientificRenderers();
    const rendererId = runtime ? runtime.rendererIdFromDescriptor(descriptor, catalog) : "download";
    const catalogRenderer = catalog.find(item => item.renderer_id === rendererId);
    return {
      ...descriptor,
      renderer: catalogRenderer || { renderer_id: rendererId, label: rendererId, capabilities: ["view"], sandboxed: true },
    };
  }).catch(error => {
    delete S.rendererDescriptors[key];
    throw error;
  });
  S.rendererDescriptors[key] = request;
  return request;
}
function renderArtifactBody(body, a) {
  const request = body._rendererRequest = (body._rendererRequest || 0) + 1;
  body.innerHTML = "";
  const loading = el("div", "renderer-loading"); loading.appendChild(iconEl("loader", 16, "spin")); loading.appendChild(el("span", null, t("viewer.loading"))); body.appendChild(loading);
  artifactRendererDescriptor(a).then(descriptor => {
    if (body._rendererRequest !== request) return;
    renderArtifactDescriptor(body, a, descriptor);
  }).catch(() => {
    if (body._rendererRequest !== request) return;
    renderArtifactDescriptor(body, a, compatibilityRendererDescriptor(a));
  });
}
function renderArtifactDescriptor(body, a, descriptor) {
  body.innerHTML = "";
  const renderer = descriptor.renderer || {};
  const rendererId = renderer.renderer_id || "download";
  const shell = el("div", "renderer-shell"); shell.dataset.rendererId = rendererId;
  const meta = el("div", "renderer-meta");
  meta.appendChild(el("span", "renderer-name", publicText(renderer.label || rendererId, 80)));
  const match = descriptor.matched_by === "compatibility" ? t("viewer.renderer.compat") : t("viewer.renderer.matched", publicText(descriptor.matched_by || "metadata", 30));
  meta.appendChild(el("span", "renderer-detail", match));
  if (descriptor.version_id) meta.appendChild(el("span", "renderer-version", t("viewer.renderer.version", publicText(String(descriptor.version_id).slice(0, 10), 12))));
  shell.appendChild(meta);
  const content = el("div", "renderer-content"); shell.appendChild(content); body.appendChild(shell);
  const url = artUrl(a); const nm = String(a.filename || "").toLowerCase();
  if (rendererId === "image") renderAnnotatableImage(content, a, url);
  else if (rendererId === "pdf") { const frame = el("iframe"); frame.src = url; content.appendChild(frame); if (artifactWorkbenchOn()) renderLocatorComments(content, a, "pdf"); }
  else if (rendererId === "html-preview") { const frame = el("iframe"); frame.setAttribute("sandbox", "allow-scripts allow-forms"); frame.src = (S.sandboxOrigin || "") + `/preview/${encodeURIComponent(a.id)}`; content.appendChild(frame); if (artifactWorkbenchOn()) renderLocatorComments(content, a, "html"); }
  else if (rendererId === "molecule-3d") molecule(content, url, nm);
  else if (rendererId === "chemistry-2d") renderChemistry2D(content, a, url);
  else if (rendererId === "genome-track") renderGenomeTrack(content, a, url);
  else if (rendererId === "sequence") renderSequenceArtifact(content, a, url);
  else if (rendererId === "msa") renderAlignmentArtifact(content, a, url);
  else if (rendererId === "latex") renderLatexArtifact(content, a, url);
  else if (rendererId === "markdown") renderMarkdownArtifact(content, url);
  else if (rendererId === "table") renderTableArtifact(content, a, url);
  else if (rendererId === "text") renderTextArtifact(content, a, url);
  else renderDownloadArtifact(content, a, url);
}
function fetchArtifactText(url) {
  return fetch(url).then(response => {
    if (!response.ok) throw new ApiError(null, response.status);
    return response.text();
  });
}
function rendererFailure(container, a, url) {
  container.innerHTML = "";
  const card = el("div", "renderer-fallback"); card.appendChild(iconEl("alert-triangle", 18));
  card.appendChild(el("div", "renderer-fallback-text", t("viewer.renderer.error")));
  const download = el("a", "outline-btn small", t("common.download")); download.href = url; download.setAttribute("download", a.filename || "artifact"); card.appendChild(download);
  container.appendChild(card);
}
function renderMarkdownArtifact(container, url) {
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    const markdown = el("div", "md renderer-markdown"); markdown.innerHTML = renderMd(text.slice(0, 1000000)); container.appendChild(markdown);
  }).catch(() => rendererFailure(container, { filename: "artifact" }, url));
}
function renderTextArtifact(container, a, url) {
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    if (looksBinary(text)) return renderDownloadArtifact(container, a, url);
    const ct = String(a.content_type || "").toLowerCase(); const nm = String(a.filename || "").toLowerCase();
    if (/json/.test(ct) || /\.json$/i.test(nm)) return renderStructuredText(container, a, text);
    const pre = el("pre", "renderer-source"); pre.textContent = text.slice(0, 300000); container.appendChild(pre);
  }).catch(() => rendererFailure(container, a, url));
}
function renderStructuredText(container, a, text) {
  const rows = parseTable(text, a);
  if (!rows || !rows.length) { const pre = el("pre", "renderer-source"); pre.textContent = text.slice(0, 300000); container.appendChild(pre); return; }
  renderSheet(container, rows);
}
function artifactWorkbenchOn() {
  return !!(S.artifactWorkbench || (_kc && _kc.st && _kc.st.artifact_workbench));
}
function renderTableArtifact(container, a, url) {
  if (artifactWorkbenchOn()) return renderWorkbenchTable(container, a);
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    if (looksBinary(text)) return renderDownloadArtifact(container, a, url);
    const rows = parseTable(text, a);
    if (rows && rows.length) renderSheet(container, rows);
    else { const pre = el("pre", "renderer-source"); pre.textContent = text.slice(0, 300000); container.appendChild(pre); }
  }).catch(() => rendererFailure(container, a, url));
}
function renderWorkbenchTable(container, a) {
  const state = { sort: "", dir: "asc", filters: {}, offset: 0, limit: 50 };
  const chrome = el("div", "wb-table");
  const controls = el("div", "wb-table-controls");
  const filter = el("input", "wb-filter"); filter.placeholder = t("wb.table.filter");
  const prev = el("button", "outline-btn small", t("wb.table.prev"));
  const next = el("button", "outline-btn small", t("wb.table.next"));
  const meta = el("div", "wb-table-meta");
  controls.appendChild(filter); controls.appendChild(prev); controls.appendChild(next); chrome.appendChild(controls); chrome.appendChild(meta);
  const hold = el("div", "wb-table-hold"); chrome.appendChild(hold); container.appendChild(chrome);
  const load = async () => {
    const query = new URLSearchParams({ sort: state.sort, dir: state.dir, offset: String(state.offset), limit: String(state.limit) });
    Object.entries(state.filters).forEach(([key, value]) => { if (value) query.set("q_" + key, value); });
    let payload;
    try { payload = await api(`/artifacts/${encodeURIComponent(a.id)}/table?${query}`); }
    catch (error) { hold.textContent = apiErrorText(error); return; }
    if (!container.isConnected) return;
    meta.textContent = t("wb.table.meta", payload.total_rows, payload.offset + 1, Math.min(payload.offset + payload.rows.length, payload.total_rows));
    hold.innerHTML = "";
    const table = el("table", "sheet"); const head = el("tr");
    (payload.columns || []).forEach(name => {
      const th = el("th", payload.sorted_by === name ? "wb-sorted" : "", name);
      th.onclick = () => { state.sort = name; state.dir = payload.sorted_by === name && state.dir === "asc" ? "desc" : "asc"; state.offset = 0; load(); };
      head.appendChild(th);
    });
    table.appendChild(head);
    (payload.rows || []).forEach(row => {
      const tr = el("tr"); (payload.columns || []).forEach((_, index) => tr.appendChild(el("td", null, String(row[index] ?? "")))); table.appendChild(tr);
    });
    hold.appendChild(table);
    prev.disabled = payload.offset <= 0;
    next.disabled = payload.offset + payload.rows.length >= payload.total_rows;
  };
  filter.onchange = () => { state.filters = payloadFilters(filter.value, a); state.offset = 0; load(); };
  prev.onclick = () => { state.offset = Math.max(0, state.offset - state.limit); load(); };
  next.onclick = () => { state.offset += state.limit; load(); };
  load();
}
function payloadFilters(text, a) {
  const value = String(text || "").trim();
  if (!value) return {};
  const named = value.match(/^([^:]+):(.*)$/);
  if (named) return { [named[1].trim()]: named[2].trim() };
  return { [((a && a.filename) || "col").replace(/\.[^.]+$/, "")]: value };
}
// The true shape of a parsed table: rows, and the union of every row's keys.
// Not `rows[0]`'s keys, which is what decides the drawn columns -- records
// parsed from JSON are ragged, so a field appearing only in later rows is
// invisible in the table and would be uncounted here too.
function sheetShape(rows) {
  const keys = new Set();
  for (const row of rows) { for (const key in row) keys.add(key); }
  return { rows: rows.length, columns: keys.size };
}
// Say the shape out loud, and name whatever the cap dropped.
//
// `renderSheet` caps at 5000x100 and used to say nothing at all, so a 5001x101
// matrix rendered as a table that looked complete: a reader counting 100
// columns had no way to learn a 101st existed. The Notebook's table path
// already carries this banner; the Viewer's did not, so the two disagreed
// about the same file. The `nb.table.*` sentences are reused deliberately
// rather than translated a second time -- two table paths wording the same
// fact differently is how they drifted apart to begin with.
function appendSheetShape(container, rows, shownRows, shownColumns) {
  const shape = sheetShape(rows);
  const note = el("div", "renderer-note", t("viewer.table.shape", shape.rows.toLocaleString(), shape.columns.toLocaleString()));
  const hiddenRows = Math.max(0, shape.rows - shownRows);
  const hiddenColumns = Math.max(0, shape.columns - shownColumns);
  let hidden = "";
  if (hiddenRows && hiddenColumns) hidden = t("nb.table.bothHidden", hiddenRows.toLocaleString(), hiddenColumns.toLocaleString());
  else if (hiddenRows) hidden = t("nb.table.rowsHidden", hiddenRows.toLocaleString());
  else if (hiddenColumns) hidden = t("nb.table.colsHidden", hiddenColumns.toLocaleString());
  if (hidden) note.appendChild(document.createTextNode(" " + hidden));
  container.appendChild(note);
}
function renderSheet(container, rows) {
  const safeRows = rows.slice(0, 5000); const columns = Object.keys(safeRows[0] || {}).slice(0, 100);
  appendSheetShape(container, rows, safeRows.length, columns.length);
  const table = el("table", "sheet"); const head = el("tr"); columns.forEach(key => head.appendChild(el("th", null, key))); table.appendChild(head);
  safeRows.forEach(row => { const tr = el("tr"); columns.forEach(key => tr.appendChild(el("td", null, String(row[key] ?? "")))); table.appendChild(tr); });
  container.appendChild(table);
}
function appendResidues(container, sequence, alphabet, limit) {
  const runtime = scientificRenderers(); const fragment = document.createDocumentFragment();
  const shown = String(sequence || "").slice(0, Math.max(0, limit));
  for (const residue of shown) {
    const span = el("span", "bio-residue " + (runtime ? runtime.residueClass(residue, alphabet) : "other"), residue);
    fragment.appendChild(span);
  }
  container.appendChild(fragment); return shown.length;
}
function renderSequenceArtifact(container, a, url) {
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    const runtime = scientificRenderers(); const parsed = runtime && runtime.parseSequence(text, a.filename);
    if (!parsed || !parsed.records.length) return renderTextArtifact(container, a, url);
    const summary = el("div", "bio-summary", t("viewer.sequence.summary", parsed.records.length, parsed.total_length.toLocaleString(), parsed.alphabet)); container.appendChild(summary);
    const list = el("div", "sequence-list"); let remaining = 30000; let shown = 0;
    parsed.records.slice(0, 100).forEach(record => {
      if (remaining <= 0) return;
      const card = el("section", "sequence-record"); const head = el("div", "sequence-head");
      head.appendChild(el("strong", null, record.name || "sequence"));
      head.appendChild(el("span", null, `${record.sequence.length.toLocaleString()} ${parsed.alphabet === "protein" ? "aa" : "nt"}`));
      card.appendChild(head); if (record.description) card.appendChild(el("div", "sequence-description", record.description));
      const sequence = el("div", "bio-sequence"); const used = appendResidues(sequence, record.sequence, parsed.alphabet, Math.min(remaining, 10000));
      remaining -= used; shown += used; card.appendChild(sequence); list.appendChild(card);
    });
    container.appendChild(list);
    if (shown < parsed.total_length) container.appendChild(el("div", "renderer-note", t("viewer.sequence.omitted", (parsed.total_length - shown).toLocaleString())));
  }).catch(() => rendererFailure(container, a, url));
}
function renderAlignmentArtifact(container, a, url) {
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    const runtime = scientificRenderers(); const parsed = runtime && runtime.parseAlignment(text, a.filename);
    if (!parsed || !parsed.records.length) return renderTextArtifact(container, a, url);
    container.appendChild(el("div", "bio-summary", t("viewer.msa.summary", parsed.records.length, parsed.columns.toLocaleString(), parsed.format)));
    const viewport = el("div", "msa-viewport");
    parsed.records.slice(0, 48).forEach(record => {
      const row = el("div", "msa-row"); const label = el("div", "msa-label", record.name || "sequence"); label.title = record.name || "sequence"; row.appendChild(label);
      const sequence = el("div", "msa-sequence"); appendResidues(sequence, record.sequence, parsed.alphabet || "protein", 1200); row.appendChild(sequence); viewport.appendChild(row);
    });
    container.appendChild(viewport);
    const omitted = parsed.records.reduce((sum, record, index) => sum + (index >= 48 ? record.sequence.length : Math.max(0, record.sequence.length - 1200)), 0);
    if (omitted) container.appendChild(el("div", "renderer-note", t("viewer.sequence.omitted", omitted.toLocaleString())));
  }).catch(() => rendererFailure(container, a, url));
}
function svgElement(name, attrs) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs || {}).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}
function renderGenomeTrack(container, a, url) {
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    const runtime = scientificRenderers(); const parsed = runtime && runtime.parseGenome(text, a.filename);
    if (!parsed || !parsed.features.length) return renderTextArtifact(container, a, url);
    container.appendChild(el("div", "bio-summary", `${parsed.format} · ${t("viewer.genome.features", parsed.features.length.toLocaleString(), parsed.chromosomes.length)}`));
    if (parsed.invalid) container.appendChild(el("div", "renderer-note", t("viewer.genome.invalid", parsed.invalid.toLocaleString())));
    const grouped = new Map(); parsed.features.forEach(feature => { if (!grouped.has(feature.chrom)) grouped.set(feature.chrom, []); grouped.get(feature.chrom).push(feature); });
    const tracks = el("div", "genome-tracks"); let budget = 500;
    parsed.chromosomes.slice(0, 24).forEach(chromosome => {
      const row = el("section", "genome-row"); const head = el("div", "genome-head");
      head.appendChild(el("strong", null, chromosome.chrom)); head.appendChild(el("span", null, `${chromosome.start.toLocaleString()}–${chromosome.end.toLocaleString()} · ${chromosome.count}`)); row.appendChild(head);
      const svg = svgElement("svg", { viewBox: "0 0 1000 58", role: "img", "aria-label": `${chromosome.chrom} genome track` });
      svg.appendChild(svgElement("line", { x1: 18, y1: 29, x2: 982, y2: 29, class: "genome-axis" }));
      const span = Math.max(1, chromosome.end - chromosome.start); const features = (grouped.get(chromosome.chrom) || []).slice(0, Math.max(0, budget)); budget -= features.length;
      features.forEach((feature, index) => {
        const x = 18 + 964 * ((feature.start - chromosome.start) / span); const width = Math.max(2, 964 * ((feature.end - feature.start) / span));
        const rect = svgElement("rect", { x: x.toFixed(2), y: 9 + (index % 5) * 8, width: Math.min(982 - x, width).toFixed(2), height: 6, rx: 2, class: `genome-feature genome-${String(feature.type || "feature").replace(/[^a-z0-9_-]/gi, "").toLowerCase()}` });
        const title = svgElement("title"); title.textContent = `${feature.label} · ${feature.chrom}:${feature.start + 1}-${feature.end} · ${feature.type}`; rect.appendChild(title); svg.appendChild(rect);
      });
      row.appendChild(svg); tracks.appendChild(row);
    });
    container.appendChild(tracks);
    const details = el("details", "genome-descriptors"); details.appendChild(el("summary", null, t("viewer.genome.list")));
    parsed.features.slice(0, 300).forEach(feature => {
      const row = el("div", "genome-descriptor"); row.appendChild(el("code", null, `${feature.chrom}:${feature.start + 1}-${feature.end}`)); row.appendChild(el("span", "genome-type", feature.type)); row.appendChild(el("span", "genome-label", feature.label)); details.appendChild(row);
    });
    container.appendChild(details);
  }).catch(() => rendererFailure(container, a, url));
}
function chemistryElementColor(element) {
  return ({ C: "#38434f", N: "#2563eb", O: "#dc2626", S: "#ca8a04", P: "#ea580c", F: "#16a34a", CL: "#16a34a", BR: "#9a3412", I: "#7e22ce", H: "#64748b" })[String(element || "").toUpperCase()] || "#475569";
}
function molecule2dSvg(model) {
  if (!model || !model.atoms.length) return null;
  const xs = model.atoms.map(atom => atom.x); const ys = model.atoms.map(atom => atom.y);
  const minX = Math.min(...xs); const maxX = Math.max(...xs); const minY = Math.min(...ys); const maxY = Math.max(...ys);
  if (model.atoms.length > 1 && Math.abs(maxX - minX) < 1e-8 && Math.abs(maxY - minY) < 1e-8) return null;
  const width = 900, height = 520, pad = 64; const sx = Math.max(1e-6, maxX - minX); const sy = Math.max(1e-6, maxY - minY);
  const scale = Math.min((width - pad * 2) / sx, (height - pad * 2) / sy); const usedW = sx * scale; const usedH = sy * scale;
  const point = atom => ({ x: (width - usedW) / 2 + (atom.x - minX) * scale, y: height - ((height - usedH) / 2 + (atom.y - minY) * scale) });
  const svg = svgElement("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": model.title || "2D molecule" });
  model.bonds.forEach(bond => {
    const p1 = point(model.atoms[bond.a]); const p2 = point(model.atoms[bond.b]); const dx = p2.x - p1.x; const dy = p2.y - p1.y; const length = Math.max(1, Math.hypot(dx, dy)); const nx = -dy / length * 4; const ny = dx / length * 4;
    const count = Math.max(1, Math.min(3, bond.order));
    for (let index = 0; index < count; index++) {
      const offset = (index - (count - 1) / 2); svg.appendChild(svgElement("line", { x1: p1.x + nx * offset, y1: p1.y + ny * offset, x2: p2.x + nx * offset, y2: p2.y + ny * offset, class: "chem-bond" }));
    }
  });
  model.atoms.forEach(atom => {
    const p = point(atom); svg.appendChild(svgElement("circle", { cx: p.x, cy: p.y, r: 13, class: "chem-atom-bg" }));
    const label = svgElement("text", { x: p.x, y: p.y + 5, class: "chem-atom", fill: chemistryElementColor(atom.element), "text-anchor": "middle" }); label.textContent = atom.element; svg.appendChild(label);
  });
  return svg;
}
function renderChemistry2D(container, a, url) {
  if (artifactWorkbenchOn()) {
    const bar = el("div", "wb-ketcher-bar");
    const open = el("button", "solid-btn small", t("wb.ketcher.edit"));
    open.onclick = () => {
      $("#modal-title").textContent = t("ketcher.modalTitle");
      $("#modal-download").style.display = "none";
      const body = $("#modal-body"); body.innerHTML = "";
      const frame = el("iframe");
      frame.src = (S.sandboxOrigin || "") + "/ketcher?artifact_id=" + encodeURIComponent(a.id);
      frame.setAttribute("allow", "clipboard-read; clipboard-write");
      body.appendChild(frame); openModalEl($("#modal"));
    };
    bar.appendChild(open); container.appendChild(bar);
  }
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    const runtime = scientificRenderers(); const model = runtime && runtime.parseMolfile(text); const drawing = molecule2dSvg(model);
    const wrap = el("div", "chemistry-view");
    if (drawing) {
      const head = el("div", "bio-summary", `${model.title} · ${model.atoms.length} atoms · ${model.bonds.length} bonds`); wrap.appendChild(head); wrap.appendChild(drawing);
    } else {
      wrap.appendChild(el("div", "renderer-note", t("viewer.chem.fallback")));
      const smiles = runtime ? runtime.smilesLines(text) : [];
      if (/\.(smi|smiles)$/i.test(String(a.filename || "")) && smiles.length) {
        const list = el("div", "smiles-list"); smiles.forEach(item => { const row = el("div", "smiles-row"); row.appendChild(el("span", "smiles-name", item.name)); row.appendChild(el("code", "smiles-code", item.smiles)); list.appendChild(row); }); wrap.appendChild(list);
      }
    }
    const details = el("details", "chem-source"); details.appendChild(el("summary", null, t("viewer.chem.source"))); const pre = el("pre"); pre.textContent = text.slice(0, 300000); details.appendChild(pre); wrap.appendChild(details); container.appendChild(wrap);
  }).catch(() => rendererFailure(container, a, url));
}
function renderLatexArtifact(container, a, url) {
  fetchArtifactText(url).then(text => {
    if (!container.isConnected) return;
    const runtime = scientificRenderers(); const blocks = runtime ? runtime.latexPreview(text) : [];
    const wrap = el("div", "latex-view"); const tabs = el("div", "latex-tabs"); const previewButton = el("button", "latex-tab active", t("viewer.latex.preview")); const sourceButton = el("button", "latex-tab", t("viewer.latex.source")); tabs.appendChild(previewButton); tabs.appendChild(sourceButton); wrap.appendChild(tabs);
    const preview = el("article", "latex-preview");
    blocks.forEach(block => {
      const node = block.kind === "heading" ? el(`h${Math.max(2, Math.min(4, (block.level || 1) + 1))}`) : el(block.kind === "math" ? "div" : "p", block.kind === "math" ? "latex-math" : null);
      node.textContent = block.text; preview.appendChild(node);
    });
    if (!blocks.length) preview.appendChild(el("div", "renderer-note", t("viewer.chem.fallback")));
    const source = el("pre", "renderer-source latex-source"); source.textContent = text.slice(0, 500000); source.classList.add("hidden"); wrap.appendChild(preview); wrap.appendChild(source);
    const show = mode => { const isPreview = mode === "preview"; preview.classList.toggle("hidden", !isPreview); source.classList.toggle("hidden", isPreview); previewButton.classList.toggle("active", isPreview); sourceButton.classList.toggle("active", !isPreview); };
    previewButton.onclick = () => show("preview"); sourceButton.onclick = () => show("source"); container.appendChild(wrap);
  }).catch(() => rendererFailure(container, a, url));
}
function renderDownloadArtifact(container, a, url) {
  container.innerHTML = "";
  const ct = String(a.content_type || "").toLowerCase(); const nm = String(a.filename || "").toLowerCase();
  if (ct.startsWith("text/") || /json|xml|javascript/.test(ct) || TEXT_EXT.test(nm)) return renderTextArtifact(container, a, url);
  const card = el("div", "download-artifact"); card.appendChild(iconEl("package", 28)); card.appendChild(el("strong", null, a.filename || "artifact")); card.appendChild(el("span", null, t("viewer.downloadOnly")));
  const link = el("a", "solid-btn small", t("common.download")); link.href = url; link.setAttribute("download", a.filename || "artifact"); card.appendChild(link); container.appendChild(card);
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
/* What happened to the pins this tab last sent, when it never saw the answer.
   A reload, a closed tab, a dropped connection: the 202 is gone and the client
   knows only that it sent something. Guessing is the one thing it must not do
   — "assume sent" silently loses the comments, "assume open" offers the user a
   comment a running turn is already carrying. So it asks. */
/* One localStorage key per outstanding admission, never a container.

   Two designs were wrong before this one. A single scalar
   (`openai4s.admission.<fid>`) meant a second send while the first was still
   outstanding — the ordinary queued follow-up, and exactly the case where a
   response goes missing — overwrote the only record of the first: not sent as
   far as the tab knew, not open as far as the server knew, and with no id left
   to ask about. Replacing it with a JSON list under the same key fixed the
   overwrite within one tab and kept it between two: read-modify-write is not
   atomic across tabs, so both read the same list and the later `setItem` drops
   the other's id. That list also carried a cap, which silently evicted the
   *oldest unresolved* id — and the queue accepts far more than the cap was set
   to, so the eviction was reachable by ordinary use.

   Independent keys have neither problem: a write touches one reservation, so
   concurrent tabs cannot clobber each other, and there is nothing to bound.
   Unresolved ids are removed when they are answered, never to make room. */
const ADMISSION_LEGACY_KEY = fid => "openai4s.admission." + fid;
const ADMISSION_PREFIX = fid => "openai4s.admission." + fid + ".";
/* The scalar and the list a tab may still be holding when it reloads into this
   build — which is precisely a client with something outstanding, so dropping
   them would lose the comments this whole mechanism exists to recover. */
function migrateAdmissions(fid) {
  let raw = null;
  try { raw = localStorage.getItem(ADMISSION_LEGACY_KEY(fid)); } catch { return; }
  if (!raw) return;
  let ids = [];
  if (raw[0] === "[") {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) ids = parsed.filter(x => typeof x === "string" && x);
    } catch {}
  } else ids = [raw];
  for (const id of ids) rememberAdmission(fid, id);
  try { localStorage.removeItem(ADMISSION_LEGACY_KEY(fid)); } catch {}
}
function outstandingAdmissions(fid) {
  migrateAdmissions(fid);
  const prefix = ADMISSION_PREFIX(fid);
  const found = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(prefix)) found.push([key.slice(prefix.length), localStorage.getItem(key)]);
    }
  } catch { return []; }
  // Oldest first, by the stamp written at mint time. Ties break on the id:
  // `Date.now()` has millisecond resolution and several sends can share one,
  // in which case sorting on the stamp alone falls back to storage iteration
  // order -- which is not defined, so "oldest first" would be a claim the code
  // does not keep.
  found.sort((a, b) => (Number(a[1]) || 0) - (Number(b[1]) || 0) || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return found.map(pair => pair[0]).filter(Boolean);
}
/* How long a just-minted admission is protected from another tab's 404.

   The value stored under each key is the mint time, and it doubles as a
   bounded dispatch lease. The race it closes: tab A writes its key and has not
   sent the POST yet; tab B opens the same session, asks about that id, and is
   told 404 because the server has genuinely never heard of it; B deletes the
   key; A's POST then succeeds and its response is lost, with nothing left to
   reconcile from. B's 404 was true and acting on it was still wrong -- "not
   yet" and "never" are the same answer from the server and different facts.

   Bounded, so a key for a request that really never left cannot accumulate
   forever. Past the grace a 404 is taken at face value. */
const ADMISSION_GRACE_MS = 60_000;
function admissionAge(fid, id) {
  let raw = null;
  try { raw = localStorage.getItem(ADMISSION_PREFIX(fid) + id); } catch { return null; }
  const minted = Number(raw);
  // A missing, unparseable or future stamp is not a lease. Trusting one would
  // make a corrupted value protect a key permanently.
  if (!raw || !Number.isFinite(minted) || minted <= 0 || minted > Date.now()) return null;
  return Date.now() - minted;
}
function admissionWithinGrace(fid, id) {
  const age = admissionAge(fid, id);
  return age !== null && age < ADMISSION_GRACE_MS;
}
function rememberAdmission(fid, id) {
  // A single independent write. No read, so nothing to lose a race with.
  try { localStorage.setItem(ADMISSION_PREFIX(fid) + id, String(Date.now())); } catch {}
}
function forgetAdmission(fid, id) {
  try { localStorage.removeItem(ADMISSION_PREFIX(fid) + id); } catch {}
}
/* Whether an answer settles an admission. `sent`, `released` and `none` are
   decided; `pending` is undecided by definition, and an unrecognised state is
   not evidence of anything — dropping either throws away the only handle the
   client has on those comments. */
function admissionSettled(state) {
  return state === "sent" || state === "released" || state === "none";
}
/* What happened to the pins this tab sent, when it never saw the answers.
   A reload, a closed tab, a dropped connection: the 202s are gone and the
   client knows only that it sent something. Guessing is the one thing it must
   not do — "assume sent" silently loses the comments, "assume open" offers the
   user a comment a running turn is already carrying. So it asks, about every
   one of them, and keeps the ones that are still undecided. */
/* One pending retry per session, so N unresolved ids schedule one sweep.

   Without the de-dupe every 404 inside the lease would arm its own timer and a
   tab with several outstanding sends would re-ask N times per round. */
const _admissionRetries = new Map();
function scheduleAdmissionRetry(fid) {
  if (_admissionRetries.has(fid)) return;
  _admissionRetries.set(fid, setTimeout(() => {
    _admissionRetries.delete(fid);
    // Only if the session is still the one on screen; reconciling a session
    // the user has left would fight whatever is now open.
    if (S.currentId === fid) reconcileLastAdmission(fid).catch(() => {});
  }, 3000));
}
async function reconcileLastAdmission(fid) {
  const outstanding = outstandingAdmissions(fid);
  if (!outstanding.length) return null;
  const records = [];
  for (const reservation of outstanding) {
    let record = null;
    try {
      record = await api(`/frames/${fid}/admissions/${encodeURIComponent(reservation)}`);
    } catch (e) {
      // 404 means this session has no such admission. That is true both for a
      // stale id and for one whose POST has not left another tab yet, and the
      // second must not be deleted — see ADMISSION_GRACE_MS. Within the lease
      // the key is kept and re-asked; past it, taken at face value. Anything
      // else (offline, 5xx) always leaves it for the next attempt rather than
      // dropping the only handle on the comments.
      if (e && e.status === 404) {
        if (admissionWithinGrace(fid, reservation)) scheduleAdmissionRetry(fid);
        else forgetAdmission(fid, reservation);
      }
      continue;
    }
    records.push(record);
    // Only a decided outcome is forgotten.
    if (admissionSettled(record && record.state)) forgetAdmission(fid, reservation);
  }
  await loadAnnotations(fid);
  refreshAllStages(); updateAnnotBadge();
  // The most recent decided record, for callers that want one answer; every
  // record was acted on above regardless.
  return records.length ? records[records.length - 1] : null;
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
/* The one place that decides what a pin's status is for display. `reserved`
   and `pending` are in-flight: not open (the user cannot act on them) and not
   sent (they are not consumed yet). Anything unrecognised is `unknown` rather
   than silently `open`, because "I do not know" and "you may edit this" are
   different answers. */
function annotationStatus(an) {
  const raw = String((an && an.status) || "open");
  if (raw === "sent" || raw === "resolved" || raw === "dismissed") return raw;
  if (raw === "reserved" || raw === "pending") return "pending";
  if (raw === "open") return "open";
  return "unknown";
}
function annotationIsHeld(an) {
  const shown = annotationStatus(an);
  return shown === "pending" || shown === "unknown";
}
function renderPins(stage, a) {
  const layer = stage.querySelector(".annot-layer"); if (!layer) return;
  layer.querySelectorAll(".annot-pin:not(.draft)").forEach(n => n.remove());
  annotationsFor(a.id).forEach(an => {
    // A held pin is not an open one. `reserved` (a turn is quoting it) and
    // `pending` (accepted, consume unconfirmed) both used to render as `open`,
    // which invites the user to edit or delete a comment that is already on its
    // way -- and `data-annotation-status` exists so a test can assert that
    // without matching on CSS class soup or translated text.
    const shown = annotationStatus(an);
    const pin = el("div", "annot-pin " + shown);
    pin.dataset.annotationStatus = shown;
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
    catch (e) { save.disabled = false; save.textContent = t("common.save"); hint(e.status === 404 ? t("annot.save.err404") : (t("annot.save.err", apiErrorText(e))), true); }
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
  const shown = annotationStatus(an);
  const label = {
    sent: t("annot.status.sent"),
    resolved: t("annot.status.resolved"),
    dismissed: t("annot.status.resolved"),
    pending: t("annot.status.pending"),
    unknown: t("annot.status.unknown"),
  }[shown] || t("annot.status.open");
  const st = el("span", "annot-pop-status " + shown, label);
  st.dataset.annotationStatus = shown;
  head.appendChild(st);
  const bodyEl = el("div", "annot-pop-body", an.body || "");
  const foot = el("div", "annot-foot");
  foot.appendChild(el("div", "annot-foot-l"));
  const del = el("button", "annot-btn ghost danger", t("common.delete")); del.onclick = async () => {
    del.disabled = true;
    try { await deleteAnnotations([annotationId(an)]); closeAnnotPop(); hint(t("annot.deleted")); }
    catch (e) { del.disabled = false; hint(t("toast.deleteFailed", apiErrorText(e)), true); }
  };
  // A held pin is not the user's to delete: a turn is quoting it, and the
  // server answers 409 for exactly this. Offering the button and then showing
  // an error is a worse version of not offering it.
  if (annotationIsHeld(an)) {
    del.disabled = true;
    del.title = t("annot.status.pending");
    del.dataset.heldByTurn = "1";
  }
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
      hint(t("annot.remove.err", apiErrorText(err)), true);
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
    const rm = el("button", "annot-mini danger", t("btn.remove")); rm.onclick = async () => { try { await deleteAnnotations([annotationId(an)]); pop.remove(); if (openAnnotations().length && anchor.parentElement) toggleAnnotList(anchor); } catch (e) { hint(t("annot.remove.err", apiErrorText(e)), true); } };
    acts.appendChild(openBtn); acts.appendChild(rm); row.appendChild(acts);
    pop.appendChild(row);
  });
  anchor.parentElement.appendChild(pop);
  setTimeout(() => document.addEventListener("mousedown", function h(ev) { if (!pop.contains(ev.target) && !anchor.contains(ev.target)) { pop.remove(); document.removeEventListener("mousedown", h); } }), 0);
}

/* Fullscreen: center modal. */
function openArtifact(a) {
  $("#modal-title").textContent = a.filename || t("modal.title.preview");
  const dl = $("#modal-download"); dl.style.display = ""; dl.href = `${API}/artifacts/${a.id}`; dl.setAttribute("download", a.filename || "artifact");
  renderArtifactBody($("#modal-body"), a);
  openModalEl($("#modal"));
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
  const dl = el("a", "icon-ghost"); dl.innerHTML = icon("download", 16); dl.href = `${API}/artifacts/${a.id}`; dl.setAttribute("download", a.filename || "artifact"); dl.title = t("common.download");
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
  catch (e) { hint(t("toast.renameFailed", apiErrorText(e)), true); }
}
async function deleteArtifact(a) {
  if (!confirm(t("artifact.delete.confirm"))) return;
  try { await api(`/artifacts/${a.id}`, { method: "DELETE" }); closeTab(a.id); if (S.currentId) loadArtifacts(S.currentId); hint(t("artifact.deleted", (a.filename || ""))); }
  catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); }
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
  fetch(`${API}/artifacts/${a.id}?_=${Date.now()}`).then(r => r.text()).then(t => { ta.value = t; ta.disabled = false; ta.focus(); }).catch(() => { ta.value = ""; ta.disabled = false; });
  cancel.onclick = () => { S._editing = null; renderViewer(); };
  save.onclick = async () => {
    save.disabled = true; save.textContent = t("common.saving");
    try {
      const edited = await api(`/artifacts/${a.id}/edit`, { method: "POST", body: JSON.stringify({ content: ta.value }) });
      syncArtifactVersion({ id: a.id, version_id: edited && edited.version_id }, true);
      S._editing = null; (S._artBust = S._artBust || {})[a.id] = Date.now(); hint(t("artifact.saved", (a.filename || "")));
      if (S.currentId) loadArtifacts(S.currentId);
      if (S.provMode) showProvenance(S.dockArtifact || a); else renderViewer();
    } catch (e) { save.disabled = false; save.textContent = t("common.save"); hint(t("artifact.save.err", apiErrorText(e)), true); }
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
    { label: t("menu.copyLink"), icon: "link", onClick: () => { try { navigator.clipboard && navigator.clipboard.writeText(location.origin + API + "/artifacts/" + a.id); } catch {} hint(t("artifact.linkCopied")); } },
    { label: t("common.edit"), icon: "pencil", onClick: () => { if (isTextEditable(a)) editArtifact(a); else hint(t("artifact.notEditable")); } },
    { label: t("folder.menu.rename"), icon: "pencil", onClick: () => renameArtifact(a) },
    { label: t("menu.exportMetadata"), icon: "file-text", onClick: () => exportMetadata(a) },
    { sep: true },
    { label: t("common.delete"), icon: "trash-2", danger: true, onClick: () => deleteArtifact(a) },
  ]);
}
async function setArtPriority(a, p, closeAfter) {
  try { await api(`/artifacts/${a.id}/priority`, { method: "POST", body: JSON.stringify({ priority: p }) }); a.priority = p; hint(p > 0 ? t("artifact.starred") : p < 0 ? t("artifact.hidden") : t("artifact.unstarred")); if (S.currentId) loadArtifacts(S.currentId); if (closeAfter && S.dockArtifact === a) closeTab(a.id); }
  catch (e) { hint(t("artifact.priority.err", apiErrorText(e)), true); }
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
  } catch (e) { hint(t("toast.exportFailed", apiErrorText(e)), true); }
}
async function showVersions(a) {
  S._modalMode = "versions:" + a.id;
  $("#modal-title").textContent = t("versions.modal.title", (a.filename || ""));
  $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "<div class='dock-empty'>" + t("common.loading") + "</div>";
  openModalEl($("#modal"));
  const render = async () => {
    let d; try { d = await api(`/artifacts/${a.id}/versions`); } catch (e) { body.textContent = t("versions.load.err", e.message); return; }
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
      const view = el("a", "outline-btn small", t("common.view")); view.href = `${API}/artifacts/${v.version_id}`; view.target = "_blank"; acts.appendChild(view);
      if (!v.is_latest) { const rb = el("button", "solid-btn small", t("versions.restore")); rb.onclick = async () => { rb.disabled = true; rb.textContent = t("versions.restoring"); try { const restored = await api(`/artifacts/${a.id}/versions/${v.version_id}/restore`, { method: "POST" }); syncArtifactVersion((restored && restored.artifact) || { id: a.id, version_id: v.version_id }, true); hint(t("versions.restored", v.ordinal)); (S._artBust = S._artBust || {})[a.id] = Date.now(); if (S.currentId) loadArtifacts(S.currentId); if (S.dockArtifact && S.dockArtifact.id === a.id) { if (S.provMode) showProvenance(S.dockArtifact); else renderViewer(); } render(); } catch (e) { rb.disabled = false; rb.textContent = t("versions.restore"); hint(t("versions.restore.err", apiErrorText(e)), true); } }; acts.appendChild(rb); }
      row.appendChild(acts); wrap.appendChild(row);
      // Where this version's data came from, when it came from anywhere. The
      // envelope has been recorded on every retrieved version since retrieval
      // provenance existed and read by nothing, so a figure built on a live
      // API fetch looked exactly like one computed from thin air. Read-only,
      // and already allowlisted, bounded and redacted by the server -- the
      // client renders what it is given and derives nothing.
      if (v.retrieval_source) wrap.appendChild(retrievalSourcePanel(v.retrieval_source));
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
      viewer = window.$3Dmol.createViewer(view, { backgroundColor: themeIsDark() ? "#1c1c19" : "white" });
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
  // Vendored copy only. A missing local 3Dmol used to fall back to fetching
  // https://3Dmol.org/build/3Dmol-min.js, which executes third-party script in
  // the page that holds the session cookie -- and does it silently, on an app
  // whose whole premise is that it runs locally and makes no call the user did
  // not ask for. The degraded path below (render the coordinates as text) was
  // already written; the CDN hop only stood between the failure and it.
  const s = el("script"); s.src = "/static/vendor/3Dmol-min.js"; s.onload = boot; s.onerror = fb; document.head.appendChild(s);
}

/* ---------- Notebook tab (F2) ---------- */
function artUrlByName(fname) {
  if (!fname) return "";
  const base = String(fname).split("/").pop();
  const a = (S.artifacts || []).find(x => (x.filename || "") === fname || (x.filename || "").split("/").pop() === base);
  return a ? artUrl(a) : `${API}/artifacts/${encodeURIComponent(fname)}`;  // artUrl adds the version cache-bust
}
// Same, but cache-busted by the artifact's current version so an overwritten
// table (re-run cell) refetches instead of serving the browser's stale copy.
function artUrlBust(fname) {
  const base = String(fname).split("/").pop();
  const a = (S.artifacts || []).find(x => (x.filename || "") === fname || (x.filename || "").split("/").pop() === base);
  return a ? artUrl(a) : `${API}/artifacts/${encodeURIComponent(fname)}`;
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
    // The widest row, not the header's width: a ragged file whose header is
    // short would otherwise under-report how much is being hidden.
    const width = rows.reduce((most, r) => Math.max(most, (r || []).length), 0);
    const tbl = el("table", "nbc-table");
    const thead = el("thead"), htr = el("tr");
    (view[0] || []).slice(0, 24).forEach(h => htr.appendChild(el("th", null, h)));
    thead.appendChild(htr); tbl.appendChild(thead);
    const tb = el("tbody");
    view.slice(1).forEach(r => { const tr = el("tr"); r.slice(0, 24).forEach(cell => tr.appendChild(el("td", null, cell))); tb.appendChild(tr); });
    tbl.appendChild(tb);
    const scroll = el("div", "nbc-table-scroll"); scroll.appendChild(tbl); holder.appendChild(scroll);
    // Both dimensions. Columns beyond 24 were dropped with no notice at all,
    // so a 101-column table showed 24 and looked complete -- the reader has no
    // way to tell a narrow table from a truncated view of a wide one.
    const hiddenRows = Math.max(0, rows.length - 51);
    const hiddenCols = Math.max(0, width - 24);
    if (hiddenRows && hiddenCols) {
      holder.appendChild(el("div", "nbc-table-more", t("nb.table.bothHidden", hiddenRows, hiddenCols)));
    } else if (hiddenRows) {
      holder.appendChild(el("div", "nbc-table-more", t("nb.table.rowsHidden", hiddenRows)));
    } else if (hiddenCols) {
      holder.appendChild(el("div", "nbc-table-more", t("nb.table.colsHidden", hiddenCols)));
    }
  };
  S._tbl = S._tbl || {};
  if (S._tbl[url]) { build(S._tbl[url]); return; }
  fetch(url).then(r => r.ok ? r.text() : null).then(text => {
    if (text == null) return;
    const firstLine = text.replace(/\r/g, "").split("\n", 1)[0] || "";
    const rows = parseDelimited(text, delimiterFor(fname, "", firstLine));
    S._tbl[url] = rows; build(rows);
  }).catch(() => {});
}
async function loadExecutionLog(id) {
  const request = S._executionLoadReq = (S._executionLoadReq || 0) + 1;
  let d = null;
  try { d = await api(`/frames/${id}/execution-log`); } catch { d = null; }
  // A slower response for the same session must not roll the Notebook back
  // after a newer REST response or a structured cell-finished event.
  if (id !== S.currentId || request !== S._executionLoadReq) return;
  const serverCells = (d && d.entries) || [];
  S.cells = mergeNotebookCells(serverCells, S.cells || []);
  S.kernels = (d && d.kernels) || [];
  S.cells.forEach(cell => { const k = cell.kernel_id || "python"; if (!S.kernels.includes(k)) S.kernels.push(k); });
  renderNotebook();
  if (S.provMode && S.dockArtifact) {
    S.lineage = null; S._lineageFor = null;
    S._lineageReq = (S._lineageReq || 0) + 1;
    showProvenance(S.dockArtifact);
  }
}

function nbEventCellId(event) { return event && (event.producing_cell_id || event.cell_id); }
function nbCellKey(cell) {
  if (cell && (cell.producing_cell_id || cell.cell_id)) return String(cell.producing_cell_id || cell.cell_id);
  return "legacy:" + String(cell && cell.kernel_id || "python") + ":" + String(cell && cell.cell_index != null ? cell.cell_index : "?");
}
function mergeNotebookCells(serverCells, localCells) {
  const merged = new Map();
  (localCells || []).forEach(cell => merged.set(nbCellKey(cell), cell));
  // A persisted execution record is authoritative for an identical Cell ID.
  (serverCells || []).forEach(cell => merged.set(nbCellKey(cell), cell));
  return Array.from(merged.values()).sort((a, b) => {
    const ai = Number(a.cell_index), bi = Number(b.cell_index);
    if (Number.isFinite(ai) && Number.isFinite(bi) && ai !== bi) return ai - bi;
    return String(nbCellKey(a)).localeCompare(String(nbCellKey(b)));
  });
}
function nbFindCell(producingCellId) {
  const key = String(producingCellId || "");
  return (S.liveCells || []).find(cell => nbCellKey(cell) === key)
    || (S.cells || []).find(cell => nbCellKey(cell) === key)
    || null;
}
function nbCellDraft(event) {
  const draftId = publicText(event && event.draft_id, 160);
  if (!draftId) return;
  const revision = Math.max(0, Number(event.revision) || 0);
  const previous = nbFindCell(draftId);
  if (previous && Number(previous._draftRevision || 0) > revision) return;
  if (event.status === "discarded") {
    S.liveCells = (S.liveCells || []).filter(cell => nbCellKey(cell) !== draftId);
    S._liveCell = (S.liveCells || [])[S.liveCells.length - 1] || null;
    nbRender(); return;
  }
  const language = String(event.language || "").toLowerCase() === "r" ? "r" : "python";
  const status = event.status === "ready" ? "ready" : "drafting";
  const cell = {
    producing_cell_id: draftId, cell_id: draftId, cell_index: null,
    kernel_id: language, language, origin: "agent",
    source: typeof event.source === "string" ? event.source.slice(0, 200000) : "",
    stdout: "", stderr: "", error: "", status,
    figures: [], files_written: [], files_read: [],
    complete: event.complete === true, draft: true, live: true,
    _draftRevision: revision
  };
  // One Agent writer owns the session. A new turn replaces any stale draft
  // left by a dropped terminal event instead of appending another partial Cell.
  S.liveCells = (S.liveCells || []).filter(candidate => !candidate.draft || nbCellKey(candidate) === draftId);
  S.liveCells = mergeNotebookCells([cell], S.liveCells);
  S._liveCell = cell; nbRender();
}
function nbCellStart(event) {
  const id = nbEventCellId(event);
  if (!id) return;
  // The transient model draft becomes this immutable server-identified Cell.
  S.liveCells = (S.liveCells || []).filter(candidate => !candidate.draft);
  const previous = nbFindCell(id) || {};
  // A persisted finished Cell may still be present when a live-turn replay
  // begins.  Never inherit its complete output and then append replay chunks a
  // second time; only an already-live in-memory Cell may continue its stream.
  const inheritLiveOutput = previous.live === true && previous.status === "running";
  const cell = {
    ...previous, producing_cell_id: String(id), cell_id: String(event.cell_id || previous.cell_id || id),
    cell_index: event.cell_index != null ? event.cell_index : previous.cell_index,
    kernel_id: event.kernel_id || previous.kernel_id || "python", language: event.language || previous.language || "python",
    origin: event.origin || previous.origin || null,
    source: event.source != null ? event.source : (previous.source || ""),
    stdout: inheritLiveOutput ? (previous.stdout || "") : "", stderr: inheritLiveOutput ? (previous.stderr || "") : "", error: "",
    status: "running", figures: previous.figures || [], files_written: previous.files_written || [], files_read: previous.files_read || [],
    generation_id: event.generation_id || previous.generation_id, state_revision: event.state_revision != null ? event.state_revision : previous.state_revision,
    attempt_group_id: event.attempt_group_id || previous.attempt_group_id, revision_of: event.revision_of || previous.revision_of,
    replay_policy: event.replay_policy || previous.replay_policy, visibility: event.visibility || previous.visibility,
    _seenChunks: inheritLiveOutput ? previous._seenChunks : undefined, live: true
  };
  // Replayed starts and reconnects upsert by the server identity; they never
  // create a duplicate temporary Cell.
  S.liveCells = mergeNotebookCells([cell], S.liveCells || []);
  S.cells = (S.cells || []).filter(saved => nbCellKey(saved) !== String(id));
  S._liveCell = nbFindCell(id);
  nbRender();
}
function nbCellChunk(event) {
  const producingCellId = event && (event.producing_cell_id || event.cell_id);
  const cell = event && nbFindCell(producingCellId);
  if (!cell) return;
  const stream = event.stream === "stderr" ? "stderr" : "stdout";
  const chunkId = event.chunk_id != null ? event.chunk_id : (event.sequence != null ? event.sequence : null);
  if (chunkId != null) {
    cell._seenChunks = cell._seenChunks || Object.create(null);
    const seenKey = stream + ":" + String(chunkId); if (cell._seenChunks[seenKey]) return; cell._seenChunks[seenKey] = true;
  }
  cell[stream] = appendLiveOutput(cell[stream], event.chunk || "");
  nbRender();
}
function nbCellFinished(event) {
  const id = event && (event.producing_cell_id || event.cell_id);
  if (!id) return;
  const active = nbFindCell(id) || {};
  const cell = {
    ...active, ...event, producing_cell_id: String(id), cell_id: String(event.cell_id || active.cell_id || id),
    source: event.source != null ? event.source : (active.source || ""),
    stdout: event.stdout != null ? appendLiveOutput("", event.stdout) : (active.stdout || ""),
    stderr: event.stderr != null ? appendLiveOutput("", event.stderr) : (active.stderr || ""),
    error: event.error || "", status: event.status || (event.error ? "error" : "ok"),
    figures: event.figures || active.figures || [],
    files_written: event.files_written || active.files_written || [],
    files_read: event.files_read || active.files_read || [],
    live: false
  };
  S.liveCells = (S.liveCells || []).filter(candidate => nbCellKey(candidate) !== String(id));
  S.cells = mergeNotebookCells([cell], S.cells || []);
  S._liveCell = (S.liveCells || [])[S.liveCells.length - 1] || null;
  nbRender();
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
  catch (e) { hint(t("nb.kernel.opFailed", apiErrorText(e)), true); }
  invalidateKernelCache();  // force a fresh read so the state chip reflects the action
  if (S.dock.open && S.activeTab === "notebook") renderNotebook();
}
async function executeNotebookCode(code, language, controls) {
  code = String(code || ""); language = String(language || "python").toLowerCase() === "r" ? "r" : "python";
  if (!code.trim() || !S.currentId) return false;
  const runButton = controls && controls.runButton, input = controls && controls.input, stop = controls && controls.stop;
  const randomId = (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") ? globalThis.crypto.randomUUID() : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
  const executionId = "repl-" + randomId, frameId = S.currentId;
  S.pendingReplIdentity = { frame_id: frameId, execution_id: executionId, owner: { kind: "user_repl", id: executionId } };
  if (runButton) runButton.disabled = true; if (input) input.disabled = true; if (stop) stop.classList.remove("hidden");
  let accepted = false;
  try {
    const response = await api(`/frames/${frameId}/kernel/execute`, { method: "POST", body: JSON.stringify({ code, language, execution_id: executionId }) });
    accepted = response && response.status === "accepted";
    if (accepted && S.pendingReplIdentity && S.pendingReplIdentity.execution_id === executionId) {
      S.pendingReplIdentity.owner = response.owner && response.owner.kind && response.owner.id ? response.owner : S.pendingReplIdentity.owner;
    }
    hint(t("nb.action.queued", language === "r" ? "R" : "Python"));
    if (!accepted && S.currentId === frameId) { invalidateKernelCache(); await loadExecutionLog(frameId); loadArtifacts(frameId); scheduleWorkbenchRefresh(); }
    else if (accepted && S.currentId === frameId) scheduleWorkbenchRefresh();
    return true;
  } catch (error) { hint(t("nb.repl.execFailed", apiErrorText(error)), true); return false; }
  finally {
    if (!accepted && S.pendingReplIdentity && S.pendingReplIdentity.execution_id === executionId) S.pendingReplIdentity = null;
    if (!accepted) { if (runButton) runButton.disabled = false; if (input) input.disabled = false; if (stop) stop.classList.add("hidden"); }
    // Execution-owner and live-cell events rebuild the Notebook while this
    // request is pending, so the controls captured above may be detached.
    // Repaint once more from authoritative queue/pending state after cleanup.
    if (S.currentId === frameId && S.dock.open && S.activeTab === "notebook") renderNotebook();
  }
}
// Cache for the Notebook header's kernel state + env list. renderNotebook rebuilds
// the whole pane on every streaming frame; without a cache the state chip and env
// <select> would refetch each frame and never settle (flickering "…" / t("nb.env.placeholder")).
// We paint the freshly-built nodes from cache immediately, then refresh the cache
// at most a few times a second.
const _kc = { id: null, st: null, stAt: 0, stBusy: false, envs: null, cur: null, envAt: 0, envBusy: false };
function invalidateKernelCache() { _kc.id = null; _kc.st = null; _kc.stAt = 0; _kc.envs = null; _kc.cur = null; _kc.envAt = 0; }
function _paintKernel(els, st) {
  const { state, bStop, bStart, title, revive, strip, badge } = els || {};
  const label = st.turn_running ? t("dash.badge.running") : ({ running: t("nb.kernel.stateActive"), stopped: t("nb.kernel.stateStopped"), none: t("nb.kernel.stateNone") }[st.state] || st.state);
  if (state) {
    state.textContent = label + (st.generation ? t("nb.kernel.generation", st.generation) : "");
    state.className = "kstate " + (st.turn_running ? "run" : st.state);
  }
  const env = st.env || {};
  if (title) title.textContent = kernelLabel(kernelIdFromEnv(env)) + " kernel · " + t("nb.kernel.shared")
    + (st.generation_id ? " · " + t("nb.owner.generation", shortRuntime(st.generation_id)) : "")
    + (env.pending ? t("nb.kernel.pendingSwitch", env.pending) : "");
  if (badge && badge.root && badge.label) {
    const mode = runtimeSummary().status;
    ["live", "busy", "ended", "restoring", "partial", "failed", "ready", "idle"].forEach(name => badge.root.classList.toggle(name, name === mode));
    badge.label.textContent = t("runtime.status." + mode);
  }
  const quarantined = st.view_only === true && st.trust_state === "quarantined";
  if (bStop) bStop.disabled = !st.alive;
  if (bStart) bStart.disabled = st.alive || quarantined;
  // Revive banner: only when the kernel is stopped/absent and no turn is running.
  if (revive) {
    revive.classList.toggle("hidden", st.alive || st.turn_running || quarantined);
    revive.title = quarantined ? t("runtime.quarantineHint") : "";
  }
  if (strip) _paintStatusStrip(strip, st);
}
// Read-only Notebook status strip: a passive live/ended indicator + runtime
// label (no inputs, no kernel-control buttons). Repainted by refreshKernelState.
function _paintStatusStrip(strip, st) {
  if (!strip || !strip.line) return;
  const env = st.env || {};
  const rt = kernelLabel(kernelIdFromEnv(env)) + (env.python_version ? " " + env.python_version : "");
  const live = !!st.turn_running;
  const ready = !live && !!st.alive;
  strip.line.textContent = live ? t("nb.status.live", rt) : (ready ? t("nb.status.ready", rt) : t("nb.status.ended", rt));
  strip.line.className = "nb-status-line " + (live ? "live" : (ready ? "ready" : "ended"));
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
  const previousRuntimeKey = _kc.st && [_kc.st.state, _kc.st.alive, _kc.st.turn_running, _kc.st.generation_id, _kc.st.generation, _kc.st.view_only, _kc.st.trust_state].join(":");
  if (_kc.id !== sid) { _kc.id = sid; _kc.envs = null; }
  _kc.st = st; _kc.stAt = Date.now();
  S.artifactWorkbench = !!st.artifact_workbench;
  _paintKernel(els, st);  // els may be stale (a newer render replaced it); harmless — the next render repaints from cache
  // The first render happens before kernel status is known and therefore uses
  // the passive strip. If this daemon explicitly enables the developer REPL,
  // rebuild once now that `repl_enabled` is authoritative (and vice versa if a
  // runtime/config reload disabled it).
  const modeChanged = (!!st.repl_enabled && !!els.strip) || (!st.repl_enabled && !!els.state);
  const runtimeKey = [st.state, st.alive, st.turn_running, st.generation_id, st.generation, st.view_only, st.trust_state].join(":");
  if ((modeChanged || runtimeKey !== previousRuntimeKey) && S.dock.open && S.activeTab === "notebook") requestAnimationFrame(renderNotebook);
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
  } catch (e) { hint(t("nb.kernel.envSwitchFailed", apiErrorText(e)), true); }
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
function projectNotebookCells(rawEntries) {
  const entries = (rawEntries || []).map(cell => ({ ...cell }));
  let previous = null;
  entries.forEach(cell => {
    const previousFailed = previous && ["error", "failed"].includes(previous.status);
    const agentRetry = previous && previous.origin === "agent" && cell.origin === "agent";
    const sameRuntime = previous && (previous.kernel_id || "python") === (cell.kernel_id || "python")
      && (previous.language || "python") === (cell.language || "python");
    if (!cell.attempt_group_id) {
      if (previousFailed && sameRuntime && agentRetry) {
        cell.attempt_group_id = previous.attempt_group_id || nbCellKey(previous);
        cell.revision_of = nbCellKey(previous);
        cell.attempt = (previous.attempt || 1) + 1;
      } else {
        cell.attempt_group_id = nbCellKey(cell);
        cell.revision_of = null;
        cell.attempt = 1;
      }
    }
    previous = cell;
  });
  const groups = new Map();
  entries.forEach(cell => {
    const group = String(cell.attempt_group_id || nbCellKey(cell));
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(cell);
  });
  return Array.from(groups.values()).map(attempts => {
    const latest = attempts[attempts.length - 1];
    return {
      ...latest,
      attempt: attempts.length,
      attempt_count: attempts.length,
      is_latest_attempt: true,
      _revisions: attempts.slice(0, -1)
    };
  });
}
// The export has always produced three things — a Python .ipynb, an R .ipynb,
// and a zip of both — and the UI could only ever ask for the zip, because the
// language was hardcoded. Two working formats were unreachable: a user wanting
// the Python notebook had to unzip a bundle to get at it, and nothing said the
// other options existed.
const NOTEBOOK_EXPORTS = [
  { language: "bundle", key: "prov.exec.downloadNotebook", suffix: "notebooks.zip" },
  { language: "python", key: "prov.exec.downloadPython", suffix: "python.ipynb" },
  { language: "r", key: "prov.exec.downloadR", suffix: "r.ipynb" },
  // The reading form. The three above are for re-running the work; this one is
  // for pasting it into an issue or a methods section, with both languages in
  // execution order because the interleaving is the record.
  { language: "markdown", key: "prov.exec.downloadMarkdown", suffix: "md" },
];
function notebookExportLink(frameId) {
  const wrap = el("div", "prov-dl");
  // The default action stays exactly what it was, so the common path is one
  // click and nobody has to learn a menu to get what they used to get.
  const primary = NOTEBOOK_EXPORTS[0];
  const dl = el("a", "prov-dlbtn");
  dl.appendChild(iconEl("download", 14));
  dl.appendChild(el("span", null, t(primary.key)));
  dl.href = `${API}/frames/${encodeURIComponent(frameId)}/notebook/export?language=${primary.language}`;
  dl.setAttribute("download", `${frameId}.${primary.suffix}`);
  wrap.appendChild(dl);

  const toggle = el("button", "prov-dlmore");
  toggle.setAttribute("aria-label", t("prov.exec.downloadMore"));
  toggle.setAttribute("aria-expanded", "false");
  toggle.appendChild(iconEl("chevron-down", 13));
  const menu = el("div", "prov-dlmenu hidden");
  NOTEBOOK_EXPORTS.slice(1).forEach(option => {
    const item = el("a", "prov-dlitem");
    item.appendChild(el("span", null, t(option.key)));
    item.href = `${API}/frames/${encodeURIComponent(frameId)}/notebook/export?language=${option.language}`;
    item.setAttribute("download", `${frameId}.${option.suffix}`);
    // A download navigates; the menu should not stay open behind it.
    item.onclick = () => { menu.classList.add("hidden"); toggle.setAttribute("aria-expanded", "false"); };
    menu.appendChild(item);
  });
  toggle.onclick = (e) => {
    e.preventDefault(); e.stopPropagation();
    const open = menu.classList.toggle("hidden") === false;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
  };
  wrap.appendChild(toggle); wrap.appendChild(menu);
  return wrap;
}
async function refreshVariableInspector() {
  const inspector = S.variableInspector, frameId = S.currentId;
  if (!inspector || !frameId || inspector.loading) return;
  const language = inspector.language === "r" ? "r" : "python";
  const request = inspector.request = (inspector.request || 0) + 1;
  inspector.loading = language; inspector.error = ""; renderNotebook();
  try {
    const payload = await api(`/frames/${encodeURIComponent(frameId)}/kernel/variables?language=${language}`);
    if (frameId !== S.currentId || request !== S.variableInspector.request) return;
    S.variableInspector.results[language] = sanitizeVariableInspection(payload, frameId, language);
  } catch (error) {
    if (frameId === S.currentId && request === S.variableInspector.request) S.variableInspector.error = publicText(error && error.message, 240);
  } finally {
    if (frameId === S.currentId && request === S.variableInspector.request) {
      S.variableInspector.loading = null; renderNotebook();
    }
  }
}
function variablePreviewText(value) {
  if (typeof value === "string") return JSON.stringify(value);
  if (value === null) return "null";
  if (typeof value === "boolean" || typeof value === "number") return String(value);
  return "";
}
function renderVariableInspector() {
  const inspector = S.variableInspector || { language: "python", results: {}, loading: null, error: "" };
  const language = inspector.language === "r" ? "r" : "python", data = (inspector.results || {})[language] || null;
  const panel = el("section", "nb-variables"); panel.setAttribute("data-variable-inspector", language);
  const head = el("div", "nb-variables-head"); head.appendChild(el("span", "nb-variables-title", t("nb.variables.title")));
  const controls = el("div", "nb-variables-controls"), label = el("label", "nb-variables-language", t("nb.variables.language"));
  const select = el("select", "nb-variables-select");
  [["python", "Python"], ["r", "R"]].forEach(([value, text]) => { const option = el("option", null, text); option.value = value; select.appendChild(option); });
  select.value = language; select.disabled = !!inspector.loading;
  select.onchange = () => { inspector.language = select.value === "r" ? "r" : "python"; inspector.error = ""; renderNotebook(); };
  label.appendChild(select); controls.appendChild(label);
  const refresh = el("button", "outline-btn small", inspector.loading ? t("nb.variables.loading") : t("nb.variables.refresh"));
  refresh.setAttribute("data-action", "refresh-variables"); refresh.disabled = !!inspector.loading || !S.currentId; refresh.onclick = refreshVariableInspector; controls.appendChild(refresh);
  head.appendChild(controls); panel.appendChild(head);
  if (inspector.loading === language) { panel.appendChild(el("div", "nb-variables-empty", t("nb.variables.loading"))); return panel; }
  if (inspector.error) { panel.appendChild(el("div", "timeline-error", t("nb.variables.error", inspector.error))); return panel; }
  if (!data) { panel.appendChild(el("div", "nb-variables-empty", t("nb.variables.notLoaded"))); return panel; }
  const meta = el("div", "nb-variables-meta");
  if (data.generation_id) meta.appendChild(el("span", "timeline-pill", t("nb.variables.generation", shortRuntime(data.generation_id))));
  meta.appendChild(el("span", "timeline-pill", t("nb.variables.revision", data.state_revision)));
  const runtime = runtimeSummary(), runtimeGeneration = language === "r" ? runtime.r : runtime.python;
  const stale = (Number(runtime.revision) > Number(data.state_revision)) || !!(runtimeGeneration && data.generation_id && runtimeGeneration !== data.generation_id);
  if (stale) meta.appendChild(el("span", "timeline-pill variable-stale", t("nb.variables.stale")));
  panel.appendChild(meta);
  if (!data.available) {
    const key = "nb.variables.state." + data.state;
    panel.appendChild(el("div", "nb-variables-empty", t(key) === key ? (data.reason || t("nb.variables.state.failed")) : t(key)));
    return panel;
  }
  if (!(data.variables || []).length) { panel.appendChild(el("div", "nb-variables-empty", t("nb.variables.empty"))); return panel; }
  const list = el("div", "nb-variable-list");
  data.variables.forEach(variable => {
    const row = el("div", "nb-variable-row"), identity = el("div", "nb-variable-identity");
    identity.appendChild(el("span", "nb-variable-name", variable.name)); identity.appendChild(el("span", "nb-variable-type", variable.type)); row.appendChild(identity);
    const details = el("div", "nb-variable-details"), preview = variablePreviewText(variable.preview);
    if (preview) details.appendChild(el("span", "nb-variable-preview", preview));
    if (variable.length != null) details.appendChild(el("span", "timeline-pill", t("nb.variables.length", variable.length)));
    if (variable.fingerprint) details.appendChild(el("span", "timeline-pill", t("nb.variables.fingerprint", shortRuntime(variable.fingerprint))));
    row.appendChild(details); list.appendChild(row);
  });
  panel.appendChild(list);
  if (data.truncated) panel.appendChild(el("div", "nb-variables-truncated", t("nb.variables.truncated", data.variables.length)));
  return panel;
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
  if (S.liveCells && S.liveCells.length) entries = entries.concat(S.liveCells);
  entries = projectNotebookCells(entries);
  const kernels = []; entries.forEach(e => { const k = e.kernel_id || "python"; if (!kernels.includes(k)) kernels.push(k); });
  const chips = el("div", "kernel-chips");
  chips.appendChild(runtimeSummaryNode(true));
  const mk = (k, label) => { const c = el("button", "kchip" + (((S.kernelFilter || null) === k) ? " on" : ""), label); c.onclick = () => { S.kernelFilter = k; renderNotebook(); }; return c; };
  chips.appendChild(mk(null, t("nb.chips.all"))); kernels.forEach(k => chips.appendChild(mk(k, kernelLabel(k))));
  const cachedRunning = !!(S.running || (_kc.id === S.currentId && _kc.st && _kc.st.turn_running));
  const cachedReady = !cachedRunning && !!(_kc.id === S.currentId && _kc.st && _kc.st.alive);
  const runtimeMode = runtimeSummary().status;
  const badgeMode = runtimeMode || (cachedRunning ? "busy" : (cachedReady ? "live" : "ended"));
  const badge = el("div", "nb-live-badge " + badgeMode); badge.appendChild(el("span", "ld"));
  const badgeLabel = el("span", null, t("runtime.status." + badgeMode)); badge.appendChild(badgeLabel); badge.appendChild(iconEl("chevron-down", 14)); chips.appendChild(badge);
  if (S.currentId) chips.appendChild(notebookExportLink(S.currentId));
  const badgeEls = { root: badge, label: badgeLabel };
  nb.appendChild(chips);
  let shown = entries; if (S.kernelFilter) shown = entries.filter(e => (e.kernel_id || "python") === S.kernelFilter);
  if (!shown.length) nb.appendChild(el("div", "dock-empty", t("nb.empty")));
  else shown.forEach(e => nb.appendChild(cellNode(e)));
  const owners = el("div", "nb-owners");
  ["agent", "user_repl", "repair", "review_scratch"].forEach(kind => {
    const chip = el("span", "nb-owner-chip");
    const active = identityForOwner(S.executionQueue, kind);
    if (active) chip.classList.add("active");
    chip.textContent = t("nb.owner." + kind);
    chip.title = active && active.execution_id ? active.execution_id : kind;
    owners.appendChild(chip);
  });
  nb.appendChild(owners);
  // Read-only Notebook by default: the interactive REPL (input, env selector,
  // stop/start/restart/interrupt) is built ONLY when the server explicitly
  // enables it (developer flag repl_enabled). Otherwise render a passive,
  // non-interactive status strip. refreshKernelState runs in BOTH branches.
  const replEnabled = !!(_kc && _kc.st && _kc.st.repl_enabled && !(_kc.st.view_only && _kc.st.trust_state === "quarantined"));
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
  refreshKernelState({ state, bStop, bStart, title, revive, badge: badgeEls });
  nbPopulateEnvSelect(envSel);
  repl.appendChild(el("div", "nb-repl-body", t("nb.repl.multilineHint")));
  S._replDrafts = S._replDrafts || { python: S._replDraft || "", r: "" };
  S._replLanguage = S._replLanguage === "r" ? "r" : "python";
  const editor = el("div", "nb-live-input");
  const editorBar = el("div", "nb-live-input-bar");
  const languageLabel = el("label", "nb-language-label", t("nb.repl.language"));
  const language = el("select", "nb-language-select");
  [["python", "Python"], ["r", "R"]].forEach(([value, label]) => { const option = el("option", null, label); option.value = value; language.appendChild(option); });
  const pendingRepl = S.pendingReplIdentity && S.pendingReplIdentity.frame_id === S.currentId ? S.pendingReplIdentity : null;
  const replIdentity = pendingRepl || identityForOwner(S.executionQueue, "user_repl");
  const replBusy = !!replIdentity;
  language.value = S._replLanguage; language.disabled = replBusy; languageLabel.appendChild(language); editorBar.appendChild(languageLabel);
  const editorActions = el("div", "nb-live-input-actions");
  const run = el("button", "solid-btn small", t("nb.repl.run"));
  run.disabled = replBusy || !S.currentId;
  const stop = el("button", "repl-stop" + (replBusy ? "" : " hidden")); stop.title = t("nb.repl.interruptTitle"); stop.innerHTML = icon("stop", 15); stop.onclick = async () => {
    try { const result = await scopedExecutionRequest(S.currentId, "kernel/interrupt", "notebook interrupt", "user_repl"); if (result && result.ok) hint(t("nb.repl.interruptSent")); }
    catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); }
  };
  editorActions.appendChild(run); editorActions.appendChild(stop); editorBar.appendChild(editorActions); editor.appendChild(editorBar);
  const inp = el("textarea", "nb-repl-input"); inp.rows = 7; inp.spellcheck = false; inp.placeholder = t("nb.repl.inputPlaceholder"); inp.disabled = !S.currentId || replBusy; inp.value = S._replDrafts[S._replLanguage] || ""; editor.appendChild(inp);
  const executeDraft = async () => {
    const currentLanguage = language.value === "r" ? "r" : "python", code = inp.value;
    if (await executeNotebookCode(code, currentLanguage, { runButton: run, input: inp, stop })) {
      S._replDrafts[currentLanguage] = ""; inp.value = "";
    }
    requestAnimationFrame(() => inp.focus());
  };
  language.onchange = () => {
    S._replDrafts[S._replLanguage] = inp.value; S._replLanguage = language.value === "r" ? "r" : "python";
    inp.value = S._replDrafts[S._replLanguage] || ""; inp.placeholder = S._replLanguage === "r" ? "# R" : t("nb.repl.inputPlaceholder"); inp.focus();
  };
  inp.oninput = () => { S._replDrafts[S._replLanguage] = inp.value; };
  inp.onkeydown = (event) => { if (event.isComposing || event.keyCode === 229) return; if (event.key === "Enter" && event.shiftKey) { event.preventDefault(); executeDraft(); } };
  run.onclick = executeDraft; repl.appendChild(editor);
  nb.appendChild(repl);
  } else {
    // Passive status strip — no <input>, no <select>, no kernel-control buttons.
    // Shows the runtime label, a live/ended indicator and a one-line resume hint.
    // _paintStatusStrip (via refreshKernelState) keeps the indicator fresh.
    const strip = el("div", "nb-status");
    const sline = el("div", "nb-status-line", "…");
    strip.appendChild(sline);
    strip.appendChild(el("div", "nb-status-hint", t("nb.status.hint")));
    refreshKernelState({ strip: { line: sline }, badge: badgeEls });
    nb.appendChild(strip);
  }
  nb.appendChild(renderVariableInspector());
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
function notebookOutputBlock(cell, raw, isError) {
  const text = String(raw == null ? "" : raw);
  if (!text) return;
  if (looksBinary(text)) { cell.appendChild(binElide(text.length)); return; }
  const details = el("details", "nbc-disclosure" + (isError ? " error" : ""));
  details.appendChild(el("summary", null, "output"));
  const pre = el("pre", isError ? "nbc-err" : "nbc-out"); pre.textContent = text; details.appendChild(pre);
  cell.appendChild(details);
}
function notebookCellState(cell) {
  if (cell.draft) return { key: "drafting", cls: "drafting" };
  if (String(cell.replay_policy || "").toLowerCase() === "never") return { key: "nonReplayable", cls: "non-replayable" };
  if (cell._historicalRevision) return { key: "historical", cls: "historical" };
  if (cell.stale === true) return { key: "stale", cls: "stale", reasons: Array.isArray(cell.stale_reasons) ? cell.stale_reasons : [] };
  return { key: "current", cls: "current" };
}
function notebookCellButton(label, iconName, enabled, action) {
  const button = el("button", "nbc-action"); button.appendChild(iconEl(iconName, 13)); button.appendChild(el("span", null, label));
  button.disabled = !enabled; button.title = enabled ? label : t("nb.action.unavailable"); if (enabled) button.onclick = action; return button;
}
async function copyNotebookCell(source) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(String(source || ""));
    else throw new Error("clipboard unavailable");
    hint(t("nb.action.copied"));
  } catch {
    // The fallback still never touches history: place a copy in the live-input
    // draft and let the user decide whether to run it.
    const language = S._replLanguage === "r" ? "r" : "python"; S._replDrafts = S._replDrafts || { python: "", r: "" }; S._replDrafts[language] = String(source || ""); hint(t("nb.action.copied"));
  }
}
async function forkNotebookCell(cell) {
  const checkpointId = publicText(cell && cell.fork_checkpoint_id, 96);
  if (!S.currentId || !branchCapability("fork_from_cell") || !checkpointId) return;
  try {
    await api(`/frames/${S.currentId}/branches/fork`, { method: "POST", body: JSON.stringify({ from_cell_id: nbCellKey(cell) }) });
    await loadWorkbenchState(S.currentId, true);
  } catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); }
}
async function promoteNotebookCell(cell) {
  if (!S.currentId || !branchCapability("promote")) return;
  try { const art = await api(`/frames/${S.currentId}/artifacts/promote`, { method: "POST", body: JSON.stringify({ cell_id: nbCellKey(cell) }) }); loadArtifacts(S.currentId); scheduleWorkbenchRefresh(); hint(t("nb.action.promoted", (art && art.filename) || "")); }
  catch (error) { hint(t("nb.action.failed", apiErrorText(error)), true); }
}
function cellNode(e) {
  const k = e.kernel_id || "python";
  const c = el("div", "notebook-cell" + (e.live ? " live" : "") + (e.draft ? " draft" : ""));
  c.setAttribute("data-cell", e.cell_index != null ? e.cell_index : "");
  c.setAttribute("data-kernel", k);
  c.setAttribute("data-producing-cell", e.producing_cell_id || "");
  const revisions = e._revisions || [];
  if (revisions.length) {
    const history = el("details", "nbc-revisions");
    history.appendChild(el("summary", null, t("nb.revisions.summary", revisions.length + 1, revisions.length)));
    const attempts = el("div", "nbc-revision-list");
    revisions.forEach(revision => attempts.appendChild(cellNode({ ...revision, _revisions: [], _historicalRevision: true })));
    history.appendChild(attempts); c.appendChild(history);
  }
  const st = e.status || (e.live ? "running" : "ok");
  const idx = e.cell_index != null ? e.cell_index : "…";
  const cellState = notebookCellState(e), cellMeta = el("div", "nbc-cell-meta");
  const statePill = el("span", "nbc-state " + cellState.cls, t("nb.cell." + cellState.key));
  if ((cellState.reasons || []).length) statePill.title = cellState.reasons.map(reason => publicText(reason, 240)).filter(Boolean).join("\n");
  cellMeta.appendChild(statePill);
  if (e.state_revision != null) cellMeta.appendChild(el("span", "nbc-revision", "S" + e.state_revision));
  if (e.generation_id) { const generation = el("span", "nbc-generation", shortRuntime(e.generation_id)); generation.title = publicText(e.generation_id, 160); cellMeta.appendChild(generation); }
  c.appendChild(cellMeta);
  c.appendChild(codeBlock(e.source || "", {
    lang: e.language || k,
    langLabel: (e.language || k) + " [" + idx + "]",
    status: st,
    env: e.environment || e.env || undefined
  }));
  notebookOutputBlock(c, e.stdout, false);
  notebookOutputBlock(c, e.stderr, true);
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
  if (!e.draft) {
    const actions = el("div", "nbc-actions");
    actions.appendChild(notebookCellButton(t("nb.action.copy"), "copy", true, () => copyNotebookCell(e.source || "")));
    const replEnabled = !!(_kc.st && _kc.st.repl_enabled), appendable = replEnabled && !e.live && !!String(e.source || "").trim();
    actions.appendChild(notebookCellButton(t("nb.action.rerun"), "refresh", appendable, () => executeNotebookCode(e.source || "", e.language || "python")));
    const canForkCell = !e.live && branchCapability("fork_from_cell") && !!publicText(e.fork_checkpoint_id, 96);
    if (canForkCell) actions.appendChild(notebookCellButton(t("nb.action.fork"), "provenance", true, () => forkNotebookCell(e)));
    actions.appendChild(notebookCellButton(t("nb.action.promote"), "star", branchCapability("promote"), () => promoteNotebookCell(e)));
    c.appendChild(actions);
  }
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
  const key = artifactCacheKey(a);
  if (!S.lineage || S._lineageFor !== key) {
    const request = S._lineageReq = (S._lineageReq || 0) + 1;
    loadLineage(a).then(l => {
      if (request !== S._lineageReq || !S.provMode || !S.dockArtifact || S.dockArtifact.id !== a.id || artifactCacheKey(S.dockArtifact) !== key) return;
      S.lineage = l; S._lineageFor = key; renderViewer();
    });
  }
}
function renderProvenanceInto(v, a) {
  const tabs = el("div", "prov-subtabs");
  [["code", "Code"], ["exec", "Execution Log"], ["messages", "Messages"], ["environment", "Environment"], ["review", "Review"]].forEach(([k, lab]) => {
    const b = el("button", "prov-subtab" + (S.provSub === k ? " active" : ""), lab); b.onclick = () => { S.provSub = k; renderViewer(); }; tabs.appendChild(b);
  });
  v.appendChild(tabs);
  const body = el("div", "prov-body"); v.appendChild(body);
  const lin = (S._lineageFor === artifactCacheKey(a)) ? S.lineage : null;
  const cell = lin && (lin.interactions || []).find(i => i.kind === "cell");
  if (S.provSub === "code") {
    if (cell && cell.source) { body.appendChild(codeBlock(cell.source, { lang: cell.language || "python", langLabel: cell.language || "python", env: cell.environment })); }
    else if (!lin) body.appendChild(el("div", "dock-empty", t("common.loading")));
    else body.appendChild(el("div", "dock-empty", "Generating reproduction code…"));
  } else if (S.provSub === "exec") {
    if (S.currentId) body.appendChild(notebookExportLink(S.currentId));
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
// uploads / pre-feature artifacts. Cached per artifact version so overwrites and
// restores cannot reuse a stale production environment.
async function renderProvEnvironment(body, a) {
  body.appendChild(el("div", "dock-empty", t("prov.env.loadingSnapshot")));
  const key = artifactCacheKey(a);
  S._envSnapById = S._envSnapById || {};
  let env;
  try {
    env = S._envSnapById[key] || (S._envSnapById[key] = await (
      a && a.id ? api(`/artifacts/${a.id}/environment`) : api("/kernel/environment")));
  }
  catch (e) { if (S.provMode && S.provSub === "environment") { body.innerHTML = ""; body.appendChild(el("div", "dock-empty", t("prov.env.loadFailed", e.message))); } return; }
  if (!S.provMode || S.provSub !== "environment" || (a && artifactCacheKey(S.dockArtifact) !== key)) return;  // tab or version changed while loading
  body.innerHTML = "";
  const chip = (k, val) => { const c = el("span", "env-chip"); c.appendChild(el("span", "env-chip-k", k)); c.appendChild(el("span", "env-chip-v", val)); return c; };
  const pkgs = env.packages || [];
  const chips = el("div", "env-chips");
  chips.appendChild(chip("Environment", env.kind || "python"));
  // Only claim a Python version when the record has one. An R kernel's
  // snapshot leaves it null, and "Python ?" would put back exactly the
  // misattribution the snapshot was fixed to stop telling.
  if (env.python_version) chips.appendChild(chip(env.implementation || "Python", env.python_version));
  if (env.environment_name) chips.appendChild(chip("Env", publicText(env.environment_name, 48)));
  chips.appendChild(chip("Packages", String(env.package_count != null ? env.package_count : pkgs.length)));
  body.appendChild(chips);
  if (env.interpreter) body.appendChild(el("div", "env-plat", publicText(env.interpreter, 160)));
  if (env.platform) body.appendChild(el("div", "env-plat", env.platform));
  // Why a package list is empty matters: "none installed" and "this runtime
  // has no Python distributions" look identical without it.
  if (env.packages_unavailable) {
    body.appendChild(el("div", "env-src warn", publicText(env.packages_unavailable, 200)));
  }
  // Provenance honesty, in three states rather than two. "Captured or live"
  // was the only distinction drawn, so a snapshot the STORE labels
  // `legacy_unverified` — the named generation produced this environment, but
  // it may not be the only one that did — rendered identically to one whose
  // address includes its generation and cannot have been shared. So did a row
  // carrying `provenance: "assumed: no kernel generation on record"`.
  //
  // The migration that wrote `generation_confidence` says in its own docstring
  // that "a reader that needs certainty filters on the label", and no reader
  // did: 0 occurrences in this file before now. The label was computed,
  // migrated, stored, shipped over the wire, and ignored — so the UI made the
  // strong claim on every snapshot regardless of what the data supported.
  const captured = env.source !== "live";
  const verified = String(env.generation_confidence || "") === "verified";
  const note = el("div", "env-src" + (captured && verified ? " ok" : " warn"));
  note.appendChild(iconEl(captured ? "package" : "clock", 13));
  note.appendChild(el("span", null,
    !captured ? t("prov.env.liveFallback")
      : verified ? t("prov.env.recorded")
      : t("prov.env.recordedUnverified")));
  body.appendChild(note);
  // The row's own words about why, when it has any. Rendered rather than
  // reworded: it is written next to the code that could not establish the
  // provenance, and that code knows why better than this does.
  if (captured && !verified && env.provenance) {
    body.appendChild(el("div", "env-src warn", publicText(env.provenance, 200)));
  }
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
  try { const d = await fetchRecentMessages(S.currentId, 500); msgs = (d && d.messages) || []; }
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
  const producer = lin.producer && typeof lin.producer === "object" ? lin.producer : null;
  const mapped = lin.dependency_mappings && lin.dependency_mappings.inputs;
  const inputs = Array.isArray(mapped) ? mapped : [];
  const cellInputs = (cell && Array.isArray(cell.files_read)) ? cell.files_read : [];
  const captures = Array.isArray(lin.capture_observations) ? lin.capture_observations : [];
  if (!cell && !inputs.length && !captures.length && !producer) { body.appendChild(el("div", "dock-empty", t("prov.review.noLineage"))); return; }
  const card = el("div", "prov-card");
  if (cell) {
    card.appendChild(el("div", "prov-h", t("prov.review.producedBy", (cell.cell_index != null ? cell.cell_index : "?"))));
    card.appendChild(el("div", "prov-meta", (cell.language || "python") + " · " + (cell.exit_status || cell.status || "ok") + (cell.kernel_id ? (" · " + cell.kernel_id) : "")));
    if ((cell.files_written || []).length) card.appendChild(provRow("wrote", cell.files_written));
    if (cellInputs.length) card.appendChild(provRow("reads / inputs", cellInputs));
    const link = el("a", "prov-link"); link.appendChild(iconEl("arrow-left", 14)); link.appendChild(el("span", null, t("prov.review.viewCode"))); link.onclick = () => { S.provMode = false; setActiveTab("notebook"); scrollToCell(cell.cell_index, cell.kernel_id); }; card.appendChild(link);
  } else if (inputs.length) card.appendChild(provRow("reads / inputs", inputs));
  if (cell || inputs.length) body.appendChild(card);
  captures.filter(capture => capture && (capture.capture_kind === "head_checksum_reused" || !cell)).forEach(capture => {
    const captureCard = el("div", "prov-card");
    const identity = publicText(capture.producing_cell_id || "unknown Cell", 96);
    captureCard.appendChild(el("div", "prov-h", capture.cell_index != null ? t("prov.review.producedBy", capture.cell_index) : t("prov.review.producedByIdentity", identity)));
    const captureKind = capture.capture_kind === "head_checksum_reused" ? t("prov.review.sameBytesCapture") : t("prov.review.versionCapture");
    const frameMeta = capture.frame_id ? (" · " + t("prov.review.producerFrame", publicText(capture.frame_kind || "unknown", 32), publicText(capture.frame_id, 96))) : "";
    captureCard.appendChild(el("div", "prov-meta", captureKind + " · " + identity + frameMeta));
    if (Array.isArray(capture.inputs) && capture.inputs.length) captureCard.appendChild(provRow("reads / inputs", capture.inputs));
    if (capture.cell_index != null) {
      const link = el("a", "prov-link"); link.appendChild(iconEl("arrow-left", 14)); link.appendChild(el("span", null, t("prov.review.viewCode"))); link.onclick = () => { S.provMode = false; setActiveTab("notebook"); scrollToCell(capture.cell_index, capture.kernel_id); }; captureCard.appendChild(link);
    }
    body.appendChild(captureCard);
  });
  if (!cell && !captures.length && producer) {
    const producerCard = el("div", "prov-card");
    const producerHeading = producer.kind === "cell" ? t("prov.review.producedByIdentity", publicText(producer.producing_cell_id || "unknown Cell", 96)) : t("prov.review.nonCellProducer");
    producerCard.appendChild(el("div", "prov-h", producerHeading));
    if (producer.frame_id) producerCard.appendChild(el("div", "prov-meta", t("prov.review.producerFrame", publicText(producer.frame_kind || "unknown", 32), publicText(producer.frame_id, 96))));
    body.appendChild(producerCard);
  }
  const save = inter.find(i => i.kind === "save");
  if (save && save.at) body.appendChild(el("div", "prov-meta", t("prov.review.saved", ago(save.at))));
}
function openKetcher() { $("#modal-title").textContent = t("ketcher.modalTitle"); $("#modal-download").style.display = "none"; const body = $("#modal-body"); body.innerHTML = ""; const f = el("iframe"); f.src = (S.sandboxOrigin || "") + "/ketcher"; f.setAttribute("allow", "clipboard-read; clipboard-write"); body.appendChild(f); openModalEl($("#modal")); }
function renderLocatorComments(container, a, kind) {
  const box = el("div", "wb-locator");
  box.appendChild(el("div", "wb-locator-title", t("wb.locator.title")));
  const quote = el("textarea", "wb-locator-quote"); quote.placeholder = t("wb.locator.quote");
  const selector = el("input", "wb-locator-selector"); selector.placeholder = t("wb.locator.selector");
  const comment = el("textarea", "wb-locator-body"); comment.placeholder = t("wb.locator.body");
  const save = el("button", "solid-btn small", t("common.save"));
  if (kind === "html") box.appendChild(selector);
  box.appendChild(quote); box.appendChild(comment); box.appendChild(save);
  const preview = el("pre", "wb-locator-preview"); box.appendChild(preview);
  container.appendChild(box);
  const endpoint = kind === "pdf" ? "pdf-text" : "html-outline";
  api(`/artifacts/${encodeURIComponent(a.id)}/${endpoint}`).then(payload => {
    preview.textContent = kind === "pdf"
      ? ((payload.pages || []).map(page => page.text).join("\n").slice(0, 4000))
      : (payload.elements || []).map(item => (item.selector || item.tag) + " " + (item.text || "")).join("\n").slice(0, 4000);
  }).catch(() => { preview.textContent = ""; });
  save.onclick = async () => {
    if (!S.currentId || !String(comment.value || "").trim()) return;
    save.disabled = true;
    try {
      await api(`/frames/${S.currentId}/annotations`, {
        method: "POST",
        body: JSON.stringify({
          artifact_id: a.id,
          artifact_name: a.filename,
          kind,
          body: comment.value,
          locator: kind === "pdf"
            ? { page: 1, quote: quote.value }
            : { selector: selector.value, quote: quote.value },
        }),
      });
      comment.value = ""; hint(t("wb.locator.saved"));
      if (S.currentId) loadAnnotations(S.currentId);
    } catch (error) { hint(t("wb.locator.err", apiErrorText(error)), true); }
    save.disabled = false;
  };
}

/* ---------- upload ---------- */
function uploadFiles(files) {
  [...files].forEach(file => { const rd = new FileReader(); rd.onload = async () => { const b64 = (rd.result.split(",")[1]) || "";
    try { if (!S.currentId) { const f = await api("/frames", { method: "POST", body: JSON.stringify({ project_id: S.project || undefined, model: S.defaultModelName }) }); S.currentId = f.id; sub(f.id); await loadSessions(); await openConversation(f.id, S.project); }
      await api("/uploads", { method: "POST", body: JSON.stringify({ filename: file.name, content_base64: b64, project_id: S.project || undefined, frame_id: S.currentId }) });
      loadArtifacts(S.currentId); hint(t("upload.uploaded", file.name));
    } catch (e) { hint(t("upload.failed", apiErrorText(e)), true); } }; rd.readAsDataURL(file); });
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
    { group: t("palette.group.commands"), label: t("palette.action.newProject"), icon: "plus", run: () => openProjectModal() },
    { group: t("palette.group.commands"), label: t("palette.action.openNotebook"), icon: "notebook", run: () => setActiveTab("notebook") },
    { group: t("palette.group.commands"), label: t("palette.action.customize"), icon: "sliders", run: () => openCust() },
    { group: t("palette.group.commands"), label: t("theme.toggle"), icon: themeIsDark() ? "sun" : "moon", run: () => cycleTheme() },
    { group: t("palette.group.commands"), label: t("palette.action.backHome"), icon: "arrow-left", run: () => showDashboard() },
  ];
}
function dataproPaletteSummary(hit) {
  const parts = [];
  if (hit && hit.dataset_type) parts.push(publicText(hit.dataset_type, 60));
  if (hit && hit.json_pointer) parts.push(publicText(hit.json_pointer, 80));
  if (hit && hit.content != null) {
    let content = hit.content;
    if (typeof content !== "string") {
      try { content = JSON.stringify(content); } catch { content = String(content); }
    }
    if (content) parts.push(publicText(content, 180));
  }
  return parts.join(" · ");
}
function openDataproSearchHit(hit) {
  closePalette();
  if (hit && hit.artifact_id) {
    const view = {
      id: String(hit.artifact_id),
      filename: t("palette.datapro.result") + ".json",
      content_type: "application/json",
      root_frame_id: hit.root_frame_id || null,
      project_id: hit.project_id || null,
    };
    // The dock lives inside #workspace, which the dashboard hides, and
    // openConversation resets S.openTabs/S.dockArtifact -- so the viewer has to
    // be opened *after* the owning session, exactly like the artifact hit does.
    // Opening it directly made a click from the dashboard render into a 0x0
    // node, and a cross-session hit render in the wrong session's dock.
    if (hit.root_frame_id && hit.root_frame_id !== S.currentId) {
      openConversation(hit.root_frame_id, hit.project_id).then(() => openViewer(view));
      return;
    }
    if (!hit.root_frame_id && !S.currentId) {
      // No session to open: the fullscreen modal renders over the dashboard.
      openArtifact(view);
      return;
    }
    openViewer(view);
    return;
  }
  openCust("connectors");
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
      (r.datapro || []).slice(0, 8).forEach(hit => items.push({
        group: t("palette.group.datapro"),
        label: publicText((hit && hit.query) || t("palette.datapro.result"), 140),
        sub: dataproPaletteSummary(hit),
        icon: "search",
        run: () => openDataproSearchHit(hit),
      }));
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

/* ---------- modal focus trap + Escape ---------- */
const _modalFocus = { stack: [] };
function _focusables(root) {
  if (!root) return [];
  return [...root.querySelectorAll('a[href],button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])')]
    .filter(n => !n.hasAttribute("disabled") && n.offsetParent !== null && !n.classList.contains("hidden"));
}
function openModalEl(modal) {
  if (!modal) return;
  const wasHidden = modal.classList.contains("hidden");
  modal.classList.remove("hidden");
  if (wasHidden) {
    _modalFocus.stack.push({ el: modal, prev: document.activeElement });
    // Defer focus until content paints (cust tabs fill async)
    requestAnimationFrame(() => {
      const box = modal.querySelector(".modal-box") || modal;
      if (box && !box.hasAttribute("tabindex")) box.setAttribute("tabindex", "-1");
      const list = _focusables(box);
      const prefer = modal.querySelector("[data-autofocus]") || list.find(n => !n.classList.contains("icon-ghost")) || list[0];
      try { (prefer || box).focus({ preventScroll: true }); } catch { try { (prefer || box).focus(); } catch {} }
    });
  }
}
function closeModalEl(modal) {
  if (!modal || modal.classList.contains("hidden")) return;
  modal.classList.add("hidden");
  // Pop matching stack entry (or top if this is the topmost)
  let entry = null;
  for (let i = _modalFocus.stack.length - 1; i >= 0; i--) {
    if (_modalFocus.stack[i].el === modal) { entry = _modalFocus.stack.splice(i, 1)[0]; break; }
  }
  const prev = entry && entry.prev;
  if (prev && typeof prev.focus === "function" && document.contains(prev)) {
    try { prev.focus({ preventScroll: true }); } catch { try { prev.focus(); } catch {} }
  }
}
function trapModalKeydown(e) {
  if (e.key !== "Tab" && e.key !== "Escape") return;
  // topmost open modal (stack) or first visible modal
  let modal = null;
  if (_modalFocus.stack.length) modal = _modalFocus.stack[_modalFocus.stack.length - 1].el;
  if (!modal || modal.classList.contains("hidden")) {
    modal = ["#cust", "#modal", "#proj-modal"].map(s => $(s)).find(m => m && !m.classList.contains("hidden")) || null;
  }
  if (!modal) return;
  if (e.key === "Escape") {
    // Don't steal Escape from nested popovers / composer autocomplete
    if (PAL.open) return;
    if (ac.open) return;
    e.preventDefault();
    closeModalEl(modal);
    return;
  }
  // Tab cycle within the modal
  const box = modal.querySelector(".modal-box") || modal;
  const list = _focusables(box);
  if (!list.length) return;
  const first = list[0], last = list[list.length - 1];
  if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  else if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
}

function openCust(tab) { openModalEl($("#cust")); custTab(tab || "general"); }
function custTab(tab) {
  document.querySelectorAll(".cust-tab").forEach(btn => {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle("active", on);
    btn.setAttribute("aria-selected", on ? "true" : "false");
  });
  const c = $("#cust-content"); c.innerHTML = t("common.loading");
  ({ general: custGeneral, skills: custSkills, specialists: custSpecialists, connectors: custConnectors, agents: custSpecialists, permissions: custPermissions, compute: custCompute, network: custNetwork, memory: custMemory, models: custModels }[tab])(c);
}
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
    catch (e) { hint(t("toast.perm.updateFailed", apiErrorText(e)), true); }
  }));
  const del = el("button", "icon-ghost"); del.innerHTML = icon("trash-2", 15); del.title = t("common.delete");
  del.onclick = async () => { try { await api(`/permissions/${r.rule_id}`, { method: "DELETE" }); custTab("permissions"); } catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); } };
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
    catch (e) { hint(t("toast.addFailed", apiErrorText(e)), true); }
  };
  row.appendChild(tool); row.appendChild(pat); row.appendChild(sel); row.appendChild(add); return row;
}
function permResetRow() {
  const row = el("div", "cust-row");
  const info = el("div", "info"); info.appendChild(el("div", "nm", t("cust.perm.resetName"))); info.appendChild(el("div", "ds", t("cust.perm.resetDesc")));
  row.appendChild(info);
  const b = el("button", "outline-btn small", t("cust.perm.resetBtn"));
  b.onclick = async () => { if (!confirm(t("cust.perm.resetConfirm"))) return; try { await api("/permissions/reset", { method: "POST" }); custTab("permissions"); hint(t("toast.perm.resetDone")); } catch (e) { hint(t("toast.failed", apiErrorText(e)), true); } };
  row.appendChild(b); return row;
}
// General / global preferences — the dashboard 设置 (settings) entry lands here.
async function custGeneral(c) {
  c.innerHTML = ""; c.appendChild(hdr(t("cust.general.title"), t("cust.general.desc")));
  // Appearance theme
  const trow = el("div", "cust-row"); const tinfo = el("div", "info");
  tinfo.appendChild(el("div", "nm", t("cust.general.themeName"))); tinfo.appendChild(el("div", "ds", t("cust.general.themeDesc")));
  trow.appendChild(tinfo);
  const tseg = el("div", "seg");
  [["light", t("theme.light")], ["dark", t("theme.dark")], ["system", t("theme.system")]].forEach(([val, label]) => {
    const b = el("button", "seg-btn" + (THEME === val ? " active" : ""), label);
    b.onclick = () => { setTheme(val); custTab("general"); };
    tseg.appendChild(b);
  });
  trow.appendChild(tseg); c.appendChild(trow);
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
// Readiness is computed server-side from local state alone (`nvidia-smi` is
// looked for on PATH, never run) and the catalogue row had no reader, so a
// GPU-only Skill rendered identically to one that runs anywhere and the user
// met the difference mid-task. `ready` adds nothing to a row, so only the two
// states that cost the user something are drawn.
function skillReadinessNote(s) {
  const rd = (s && s.readiness) || {};
  if (!rd.state || rd.state === "ready") return null;
  const named = publicList(rd.state === "needs_setup" ? rd.missing : rd.unverifiable, 8);
  // Falls back to the declared requirements rather than to an empty sentence:
  // "needs setup" that names nothing is not actionable.
  const listed = (named.length ? named : publicList(s.requirements, 8)).join(", ");
  if (!listed) return null;
  return el("div", "ds prof-warn", t(rd.state === "needs_setup" ? "skill.readiness.needsSetup" : "skill.readiness.unknown", listed));
}
async function custSkills(c) {
  try {
    const pid = (typeof effProject === "function" ? effProject() : S.project) || null;
    const personalRequest = api("/skills/catalog");
    const projectRequest = pid ? api(`/projects/${encodeURIComponent(pid)}/skills/catalog`).catch(() => ({ skills: [] })) : Promise.resolve({ skills: [] });
    const [personalData, projectData] = await Promise.all([personalRequest, projectRequest]);
    const personalSkills = Array.isArray(personalData && personalData.skills) ? personalData.skills : [];
    const projectSkills = Array.isArray(projectData && projectData.skills) ? projectData.skills : [];
    const skills = [...personalSkills, ...projectSkills];
    c.innerHTML = "";
    c.appendChild(hdr(t("palette.group.skills"), t("cust.skills.desc", skills.length)));
    const bar = el("div", "cust-row"); const bi = el("div", "info"); const acts = el("div", "cust-actrow");
    const nb = el("button", "outline-btn small", t("cust.skills.newBtn")); nb.onclick = () => skillEditor(null);
    const ib = el("button", "outline-btn small", t("cust.skills.importBtn")); ib.onclick = () => skillImport();
    acts.appendChild(nb); acts.appendChild(ib); bi.appendChild(el("div", "nm", t("cust.skills.yourSkills"))); bi.appendChild(acts); bar.appendChild(bi); c.appendChild(bar);
    skills.forEach(s => {
      const scope = s.scope === "project" ? "project" : (s.scope === "bundled" ? "bundled" : "personal");
      const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm");
      nm.appendChild(el("span", null, s.displayName || s.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", t(`skill.scope.${scope}`)));
      info.appendChild(nm); info.appendChild(el("div", "ds", s.description || "")); const rn = skillReadinessNote(s); if (rn) info.appendChild(rn); row.appendChild(info);
      const useBtn = el("button", "icon-ghost"); useBtn.title = t("skill.useInChat"); useBtn.innerHTML = icon("message-square", 15); useBtn.onclick = () => insertSkillMention(s.name); row.appendChild(useBtn);
      if (s.versioned) { const vb = el("button", "icon-ghost"); vb.title = t("skill.historyBtn"); vb.innerHTML = icon("clock", 15); vb.onclick = () => skillVersionHistory(s.name, scope, scope === "project" ? pid : null); row.appendChild(vb); }
      if (s.editable && scope === "personal") { const eb = el("button", "icon-ghost"); eb.title = t("common.edit"); eb.innerHTML = icon("pencil", 15); eb.onclick = () => skillEditor(s.name); row.appendChild(eb); const db = el("button", "icon-ghost"); db.title = t("common.delete"); db.innerHTML = icon("trash-2", 15); db.onclick = async () => { if (!confirm(t("cust.skills.deleteConfirm", s.name))) return; try { await api(`/skills/${encodeURIComponent(s.name)}`, { method: "DELETE" }); S.skillsCatalog = null; custTab("skills"); } catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); } }; row.appendChild(db); }
      if (scope !== "project") { const tg = el("button", "toggle" + (s.enabled !== false ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api(`/skills/catalog/${encodeURIComponent(s.name)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) }); } catch {} }; row.appendChild(tg); }
      c.appendChild(row);
    });
  } catch (e) { c.textContent = t("versions.load.err", e.message); }
}
function skillVersionPath(name, scope, projectId) {
  const encodedName = encodeURIComponent(name);
  if (scope === "project") {
    if (!projectId) throw new Error("project scope is unavailable");
    return `/projects/${encodeURIComponent(projectId)}/skills/${encodedName}`;
  }
  return `/skills/${encodedName}`;
}
async function skillVersionHistory(name, scope, projectId) {
  S._modalMode = "skill-history";
  $("#modal-title").textContent = t("skill.historyTitle", name);
  $("#modal-download").style.display = "none";
  const body = $("#modal-body"); body.innerHTML = "";
  body.appendChild(el("div", "subtle", t(`skill.scope.${scope}`)));
  $("#modal").classList.remove("hidden");
  let data;
  try { data = await api(skillVersionPath(name, scope, projectId) + "/versions?limit=100"); }
  catch (e) { body.appendChild(el("div", "empty", e.message)); return; }
  const versions = Array.isArray(data && data.versions) ? data.versions : [];
  if (!versions.length) { body.appendChild(el("div", "empty", t("skill.historyEmpty"))); return; }
  const list = el("div", "skill-version-list");
  versions.forEach(version => {
    const versionId = String(version.version_id || ""); const manifest = version.manifest && typeof version.manifest === "object" ? version.manifest : {};
    const card = el("div", "skill-version-card"), head = el("div", "skill-version-head"), meta = el("div", "info");
    const title = el("div", "nm", versionId.slice(0, 20) + (versionId.length > 20 ? "…" : "")); title.title = versionId; meta.appendChild(title);
    const when = Number(version.created_at || 0); meta.appendChild(el("div", "ds", when ? new Date(when).toLocaleString() : ""));
    const sidecar = manifest.sidecar && manifest.sidecar.present ? String(manifest.sidecar.sha256 || "").slice(0, 12) : "—"; meta.appendChild(el("div", "ds", t("skill.versionSidecar", sidecar)));
    head.appendChild(meta);
    if (version.active) head.appendChild(el("span", "pill", t("skill.versionActive")));
    else if (!(data.status && data.status.read_only)) { const rollback = el("button", "outline-btn small", t("skill.rollbackBtn")); rollback.onclick = async () => { if (!confirm(t("skill.rollbackConfirm", name, versionId.slice(0, 18)))) return; rollback.disabled = true; try { await api(skillVersionPath(name, scope, projectId) + "/rollback", { method: "POST", body: JSON.stringify({ version_id: versionId }) }); hint(t("skill.rollbackDone", name)); await skillVersionHistory(name, scope, projectId); custTab("skills"); } catch (e) { rollback.disabled = false; hint(t("toast.failed", apiErrorText(e)), true); } }; head.appendChild(rollback); }
    card.appendChild(head); list.appendChild(card);
  });
  body.appendChild(list);
}
// Insert a "/skillname" mention into the composer from the Skills settings tab,
// close settings, and focus the composer so the skill can be invoked directly.
function insertSkillMention(name) {
  closeModalEl($("#cust"));
  if ($("#workspace").classList.contains("hidden")) { hint(t("skill.insertedToast", name)); return; }
  const c = $("#composer"); if (!c) return;
  const cur = c.value || "";
  c.value = (cur && !/\s$/.test(cur) ? cur + " " : cur) + "/" + name + " ";
  grow(); c.focus(); c.setSelectionRange(c.value.length, c.value.length);
  hint(t("skill.insertedToast", name));
}
async function skillEditor(name, seed) {
  S._modalMode = "skill";
  let cur = seed || { name: "", description: "", body: "" };
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
  save.onclick = async () => { const nm = nameIn.value.trim(); if (!nm) { hint(t("toast.skill.enterName"), true); return; } save.disabled = true; save.textContent = t("common.saving"); try { if (name) await api(`/skills/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ description: descIn.value, body: bodyIn.value }) }); else await api("/skills", { method: "POST", body: JSON.stringify({ name: nm, description: descIn.value, body: bodyIn.value }) }); S.skillsCatalog = null; closeModalEl($("#modal")); hint(t("toast.skill.saved", nm)); custTab("skills"); } catch (e) { save.disabled = false; save.textContent = t("skill.saveBtn"); hint(t("artifact.save.err", apiErrorText(e)), true); } };
  const fa = el("div", "form-actions"); fa.appendChild(save); form.appendChild(fa);
  body.appendChild(form); openModalEl($("#modal"));
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
  save.onclick = async () => { if (!ta.value.trim()) return; save.disabled = true; save.textContent = t("cust.importing"); try { const r = await api("/skills/import", { method: "POST", body: JSON.stringify({ content: ta.value }) }); S.skillsCatalog = null; closeModalEl($("#modal")); hint(t("toast.skill.imported", (r.name || ""))); custTab("skills"); } catch (e) { save.disabled = false; save.textContent = t("skill.importBtn"); hint(t("toast.importFailed", apiErrorText(e)), true); } };
  const fa = el("div", "form-actions"); fa.appendChild(save); form.appendChild(fa);
  body.appendChild(form); openModalEl($("#modal"));
}
async function custSpecialists(c) { try {
  const d = await api("/specialists"); const builtin = (d && d.builtin) || []; const custom = (d && d.specialists) || [];
  c.innerHTML = ""; c.appendChild(hdr(t("cust.tab.specialists"), t("cust.specialists.desc")));
  const bar = el("div", "cust-row"); const bi = el("div", "info"); bi.appendChild(el("div", "nm", t("cust.specialists.yours"))); const acts = el("div", "cust-actrow"); const nb = el("button", "outline-btn small", t("cust.specialists.newBtn")); nb.onclick = () => specialistEditor(null); acts.appendChild(nb); bi.appendChild(acts); bar.appendChild(bi); c.appendChild(bar);
  custom.forEach(s => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, s.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", "custom")); info.appendChild(nm); info.appendChild(el("div", "ds", s.description || "")); row.appendChild(info); const eb = el("button", "icon-ghost"); eb.title = t("common.edit"); eb.innerHTML = icon("pencil", 15); eb.onclick = () => specialistEditor(s.name); row.appendChild(eb); const db = el("button", "icon-ghost"); db.title = t("common.delete"); db.innerHTML = icon("trash-2", 15); db.onclick = async () => { if (!confirm(t("cust.specialists.deleteConfirm", s.name))) return; try { await api(`/specialists/${encodeURIComponent(s.name)}`, { method: "DELETE" }); custTab("specialists"); } catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); } }; row.appendChild(db); c.appendChild(row); });
  c.appendChild(el("div", "cust-subhead", t("cust.specialists.builtinRoles")));
  builtin.forEach(ag => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, ag.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", ag.mode || "agent")); if (ag.supportsPlanMode) { nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", "plan")); } info.appendChild(nm); info.appendChild(el("div", "ds", ag.description || "")); row.appendChild(info); const tg = el("button", "toggle" + (ag.enabled !== false ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api(`/agents/${encodeURIComponent(ag.name)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) }); } catch {} }; row.appendChild(tg); c.appendChild(row); });
} catch (e) { c.textContent = t("versions.load.err", e.message); } }
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
  const save = el("button", "solid-btn", t("specialist.saveBtn")); save.onclick = async () => { const nm = nameIn.value.trim(); if (!nm) { hint(t("toast.specialist.enterName"), true); return; } save.disabled = true; save.textContent = t("common.saving"); const b = { name: nm, description: descIn.value, system_prompt: spIn.value }; try { if (name) await api(`/specialists/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify(b) }); else await api("/specialists", { method: "POST", body: JSON.stringify(b) }); closeModalEl($("#modal")); hint(t("toast.specialist.saved", nm)); custTab("specialists"); } catch (e) { save.disabled = false; save.textContent = t("specialist.saveBtn"); hint(t("artifact.save.err", apiErrorText(e)), true); } };
  const fa = el("div", "form-actions"); fa.appendChild(save); form.appendChild(fa); body.appendChild(form); openModalEl($("#modal"));
}

const DATAPRO_CONNECTOR_ID = "volcengine-datapro";
function dataproResultText(response) {
  if (!response || typeof response !== "object") return String(response || "");
  const result = response.structuredContent != null ? response.structuredContent : response.content;
  if (typeof result === "string") return result;
  if (result == null) return "";
  try { return JSON.stringify(result, null, 2); } catch { return String(result); }
}
function dataproResponseCode(response) {
  const structured = response && response.structuredContent;
  if (structured && typeof structured.code === "number") return structured.code;
  return null;
}
function dataproIndexComplete(response) {
  const index = response && response.index;
  return !!(index && index.complete === true
    && Number.isInteger(index.entry_count) && index.entry_count >= 0
    && Number.isInteger(index.source_leaf_count) && index.source_leaf_count >= 0
    && Number.isInteger(index.indexed_leaf_count) && index.indexed_leaf_count >= 0
    && index.source_leaf_count === index.indexed_leaf_count
    && typeof index.source_digest === "string" && index.source_digest.length > 0
    && index.source_digest === index.indexed_digest);
}
function dataproCard(config, configError) {
  const state = {
    keyConfigured: !!(config && config.key_configured),
    arkKeyReused: !!(config && config.ark_key_reused),
    connectorEnabled: !!(config && config.connector_enabled),
    skillEnabled: !!(config && config.skill_enabled),
  };
  const card = el("section", "datapro-card");
  const heading = el("div", "datapro-head");
  const headingText = el("div");
  headingText.appendChild(el("div", "datapro-title", t("cust.datapro.title")));
  headingText.appendChild(el("div", "datapro-desc", t("cust.datapro.desc")));
  heading.appendChild(headingText);
  const skill = el("button", "outline-btn small", state.skillEnabled ? t("cust.datapro.skillEnabled") : t("cust.datapro.enableSkill"));
  skill.dataset.action = "datapro-enable-skill";
  skill.disabled = state.skillEnabled && state.connectorEnabled;
  skill.onclick = async () => {
    skill.disabled = true; skill.textContent = t("cust.datapro.enablingSkill");
    try {
      if (!state.connectorEnabled) {
        await api(`/connectors/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: true }) });
      }
      await api(`/skills/catalog/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: true }) });
      state.connectorEnabled = true; state.skillEnabled = true; S.skillsCatalog = null;
      skill.textContent = t("cust.datapro.skillEnabled");
      hint(t("cust.datapro.skillEnabledToast"));
    } catch (error) {
      skill.disabled = false; skill.textContent = t("cust.datapro.enableSkill");
      hint(t("toast.failed", apiErrorText(error)), true);
    }
  };
  heading.appendChild(skill);
  // The managed row is filtered out of the connector list below, and DELETE is
  // refused with 403, so without this toggle a connector seeded enabled on every
  // boot could not be turned off anywhere in the UI.
  const power = el("button", "toggle" + (state.connectorEnabled ? " on" : ""));
  power.dataset.action = "datapro-toggle-connector";
  power.title = t("cust.datapro.connectorToggle");
  power.onclick = async () => {
    const on = power.classList.toggle("on");
    try {
      await api(`/connectors/${encodeURIComponent(DATAPRO_CONNECTOR_ID)}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) });
      state.connectorEnabled = on;
      skill.disabled = state.skillEnabled && on;
      hint(on ? t("cust.datapro.connectorOn") : t("cust.datapro.connectorOff"));
    } catch (error) {
      power.classList.toggle("on", !on);
      hint(t("toast.failed", apiErrorText(error)), true);
    }
  };
  heading.appendChild(power);
  card.appendChild(heading);

  const credentials = el("div", "datapro-field");
  credentials.appendChild(el("label", "skill-lbl", t("cust.datapro.keyLabel")));
  const credentialRow = el("div", "datapro-input-row");
  const keyInput = el("input", "cust-input");
  keyInput.id = "datapro-plan-key"; keyInput.type = "password"; keyInput.autocomplete = "off";
  keyInput.autocapitalize = "off"; keyInput.spellcheck = false;
  keyInput.placeholder = state.arkKeyReused ? t("cust.datapro.keyPlaceholderArk") : (state.keyConfigured ? t("cust.datapro.keyPlaceholderSet") : t("cust.datapro.keyPlaceholder"));
  const saveKey = el("button", "solid-btn small", t("cust.datapro.saveKey"));
  saveKey.dataset.action = "datapro-save-key";
  const keyState = el("div", "datapro-credential-state", configError ? t("cust.datapro.requestFailed", apiErrorText(configError)) : (state.arkKeyReused ? t("cust.datapro.keyArkReused") : (state.keyConfigured ? t("cust.datapro.keyConfigured") : t("cust.datapro.keyMissing"))));
  keyState.classList.toggle("bad", !!configError || (!state.keyConfigured && !state.arkKeyReused));
  saveKey.onclick = async () => {
    let secret = keyInput.value.trim();
    keyInput.value = "";
    if (!secret) { hint(t("cust.datapro.keyRequired"), true); return; }
    saveKey.disabled = true; const request = api("/datapro/config", { method: "POST", body: JSON.stringify({ agent_plan_key: secret }) });
    secret = "";
    try {
      const saved = await request;
      state.keyConfigured = !!(saved && saved.key_configured); state.arkKeyReused = !!(saved && saved.ark_key_reused);
      keyInput.placeholder = state.arkKeyReused ? t("cust.datapro.keyPlaceholderArk") : t("cust.datapro.keyPlaceholderSet");
      keyState.textContent = state.arkKeyReused ? t("cust.datapro.keyArkReused") : t("cust.datapro.keyConfigured");
      keyState.classList.remove("bad"); hint(t("cust.datapro.keySaved"));
    } catch (error) {
      keyState.textContent = t("cust.datapro.requestFailed", apiErrorText(error)); keyState.classList.add("bad");
    } finally {
      keyInput.value = ""; saveKey.disabled = false;
    }
  };
  keyInput.onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); saveKey.click(); } };
  credentialRow.appendChild(keyInput); credentialRow.appendChild(saveKey);
  credentials.appendChild(credentialRow); credentials.appendChild(keyState); card.appendChild(credentials);

  const queryField = el("div", "datapro-field");
  queryField.appendChild(el("label", "skill-lbl", t("cust.datapro.queryLabel")));
  const query = el("textarea", "datapro-query"); query.id = "datapro-query"; query.rows = 3; query.maxLength = 10000; query.placeholder = t("cust.datapro.queryPlaceholder");
  const queryActions = el("div", "datapro-query-actions");
  const status = el("div", "datapro-status"); status.dataset.dataproStatus = ""; status.setAttribute("aria-live", "polite");
  const search = el("button", "solid-btn small", t("cust.datapro.search")); search.dataset.action = "datapro-search";
  queryActions.appendChild(status); queryActions.appendChild(search); queryField.appendChild(query); queryField.appendChild(queryActions); card.appendChild(queryField);

  const output = el("div", "datapro-output"); output.appendChild(el("div", "skill-lbl", t("cust.datapro.result")));
  const indexStatus = el("div", "datapro-index-status hidden"); indexStatus.dataset.dataproIndexStatus = ""; indexStatus.setAttribute("aria-live", "polite"); output.appendChild(indexStatus);
  const result = el("pre", "datapro-result", t("cust.datapro.noResult")); result.dataset.dataproResult = ""; output.appendChild(result);
  const artifact = el("button", "outline-btn small datapro-artifact hidden"); artifact.dataset.dataproArtifact = ""; output.appendChild(artifact); card.appendChild(output);
  search.onclick = async () => {
    const text = query.value.trim();
    if (!text) { hint(t("cust.datapro.queryRequired"), true); return; }
    search.disabled = true; search.textContent = t("cust.datapro.searching"); status.textContent = t("cust.datapro.searching"); status.className = "datapro-status"; indexStatus.textContent = ""; indexStatus.className = "datapro-index-status hidden"; result.textContent = t("cust.datapro.noResult"); artifact.classList.add("hidden"); artifact.onclick = null;
    try {
      const body = { query: text }; if (S.currentId) body.frame_id = S.currentId;
      const response = await api("/datapro/search", { method: "POST", body: JSON.stringify(body) });
      const code = dataproResponseCode(response);
      const indexed = code === 0 && dataproIndexComplete(response);
      status.textContent = indexed ? t("cust.datapro.available") : (code === 0 ? t("cust.datapro.indexFailed") : (code === 4011 ? t("cust.datapro.auth4011") : (response.message || t("cust.datapro.unavailable", code == null ? "?" : code))));
      status.className = "datapro-status " + (indexed ? "ok" : "bad");
      if (indexed) {
        indexStatus.textContent = t("cust.datapro.indexed", response.index.entry_count, response.index.source_leaf_count);
        indexStatus.className = "datapro-index-status ok";
      }
      result.textContent = dataproResultText(response) || t("cust.datapro.noResult");
      const savedArtifact = response && response.artifact;
      if (savedArtifact) {
        artifact.classList.remove("hidden"); artifact.disabled = !savedArtifact.id;
        artifact.textContent = t("cust.datapro.artifact", savedArtifact.filename || savedArtifact.id || "artifact");
        artifact.onclick = savedArtifact.id ? () => openViewer(savedArtifact) : null;
      } else {
        artifact.classList.add("hidden"); artifact.onclick = null;
      }
    } catch (error) {
      status.textContent = t("cust.datapro.requestFailed", apiErrorText(error)); status.className = "datapro-status bad"; result.textContent = t("cust.datapro.noResult"); artifact.classList.add("hidden"); artifact.onclick = null;
    } finally {
      search.disabled = false; search.textContent = t("cust.datapro.search");
    }
  };
  query.onkeydown = event => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); search.click(); } };
  return card;
}
async function custConnectors(c) { try {
  const [d, datapro] = await Promise.all([api("/connectors"), api("/datapro/config").then(config => ({ config })).catch(error => ({ config: {}, error }))]); const conns = (d && d.connectors) || [];
  c.innerHTML = ""; c.appendChild(hdr(t("cust.tab.connectors"), t("cust.connectors.desc")));
  c.appendChild(dataproCard(datapro.config, datapro.error));
  conns.filter(k => k.connector_id !== DATAPRO_CONNECTOR_ID).forEach(k => { const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm"); nm.appendChild(el("span", null, k.name)); nm.appendChild(document.createTextNode(" ")); nm.appendChild(el("span", "pill", k.connector_id)); info.appendChild(nm); info.appendChild(el("div", "ds", (k.description || "") + "  ·  " + (k.command_display || ""))); row.appendChild(info);
    const pb = el("button", "outline-btn small", t("cust.connectors.test")); pb.onclick = async () => { pb.disabled = true; pb.textContent = t("cust.connectors.testing"); try { const r = await api(`/connectors/${k.connector_id}/probe`, { method: "POST" }); hint(r.ok ? (t("toast.connectors.probeOk", (r.tools || []).map(t => t.name).join("、"))) : (t("toast.failed", (r.error || "")))); } catch (e) { hint(t("toast.connectors.testFailed", apiErrorText(e)), true); } pb.disabled = false; pb.textContent = t("cust.connectors.test"); }; row.appendChild(pb);
    const tg = el("button", "toggle" + (k.enabled ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api(`/connectors/${k.connector_id}/enabled`, { method: "PUT", body: JSON.stringify({ enabled: on }) }); } catch {} }; row.appendChild(tg);
    const db = el("button", "icon-ghost"); db.title = t("common.delete"); db.innerHTML = icon("trash-2", 15); db.onclick = async () => { if (!confirm(t("cust.connectors.deleteConfirm", k.name))) return; try { await api(`/connectors/${k.connector_id}`, { method: "DELETE" }); custTab("connectors"); } catch {} }; row.appendChild(db); c.appendChild(row); });
  // directory (one-click add)
  c.appendChild(el("div", "cust-subhead", t("cust.connectors.fromDirectory")));
  let dir = { directory: [] }; try { dir = await api("/connectors/directory"); } catch {}
  (dir.directory || []).forEach(item => { if (item.id === DATAPRO_CONNECTOR_ID || conns.some(k => k.connector_id === item.id)) return; const row = el("div", "cust-row"); const info = el("div", "info"); info.appendChild(el("div", "nm", item.name)); info.appendChild(el("div", "ds", item.description || "")); row.appendChild(info); const add = el("button", "outline-btn small", t("common.add")); add.onclick = async () => { try { await api("/connectors", { method: "POST", body: JSON.stringify({ connector_id: item.id, name: item.name, description: item.description, command: item.command }) }); hint(t("toast.connectors.added", item.name)); custTab("connectors"); } catch (e) { hint(t("toast.addFailed", apiErrorText(e)), true); } }; row.appendChild(add); c.appendChild(row); });
  // custom add
  const add = el("div", "cust-row"); const ai = el("div", "info"); ai.appendChild(el("div", "nm", t("cust.connectors.customAddName"))); const ad = el("div", "job-submit"); const nameIn = el("input", "cust-input"); nameIn.placeholder = t("cust.connectors.namePlaceholder"); nameIn.style.flex = "0 0 120px"; const cmdIn = el("input", "cust-input"); cmdIn.placeholder = t("cust.connectors.cmdPlaceholder"); const go = el("button", "solid-btn small", t("common.add")); go.onclick = async () => { const nm = nameIn.value.trim(); const cmd = cmdIn.value.trim(); if (!nm || !cmd) return; try { await api("/connectors", { method: "POST", body: JSON.stringify({ name: nm, command: cmd.split(/\s+/) }) }); nameIn.value = cmdIn.value = ""; custTab("connectors"); } catch (e) { hint(t("toast.addFailed", apiErrorText(e)), true); } }; ad.appendChild(nameIn); ad.appendChild(cmdIn); ad.appendChild(go); ai.appendChild(ad); add.appendChild(ai); c.appendChild(add);
} catch (e) { c.textContent = t("versions.load.err", e.message); } }
async function renderRemoteGPU(c) {
  let info; try { info = await api("/compute/remote"); } catch (e) { return; }
  const hd = el("div", "cust-row"); hd.innerHTML = `<div class="info"><div class="nm">${t("cust.remote.title")}</div><div class="ds">${t("cust.remote.desc")}</div></div>`; c.appendChild(hd);
  const hosts = (info && info.hosts) || [];
  hosts.forEach(h => {
    // Built with DOM nodes, not innerHTML. Every string here comes off a
    // machine we do not control: the label/alias is whatever the user's
    // ~/.ssh/config says, and gpus and capability names are literally the
    // stdout of `nvidia-smi` and a service probe on the remote host. Through
    // innerHTML a hostile or merely odd GPU name was markup.
    const row = el("div", "cust-row");
    const inf = el("div", "info");

    const nm = el("div", "nm", (h.reachable ? "🟢 " : "🔴 ") + (h.label || h.alias || ""));
    const prov = el("span", null, " · " + (h.provider || ""));
    prov.style.opacity = ".55";
    prov.style.fontWeight = "400";
    nm.appendChild(prov);
    inf.appendChild(nm);

    const ds = el("div", "ds", h.gpus || (h.reachable ? "" : t("cust.remote.unreachable")));
    const caps = h.capabilities || [];
    ds.appendChild(el("br"));
    if (caps.length) {
      ds.appendChild(document.createTextNode(t("cust.remote.services") + " "));
      caps.forEach(cp => {
        const chip = el("span", null, (cp.name || "") + (cp.engine ? " · " + cp.engine : "") + (cp.verified ? " ✓" : ""));
        chip.style.cssText = "display:inline-block;padding:1px 7px;margin:3px 4px 0 0;border-radius:8px;background:rgba(127,127,127,.18);font-size:11px";
        ds.appendChild(chip);
      });
    } else {
      const none = el("span", null, t("cust.remote.noservices"));
      none.style.opacity = ".6";
      ds.appendChild(none);
    }
    inf.appendChild(ds);
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
// An info row built from DOM nodes. `t()` substitutes without escaping, so its
// result is safe as textContent and unsafe as innerHTML — several compute rows
// interpolate a GPU name straight out of `nvidia-smi`, a machine string, and
// package names, none of which this process authored.
const infoRow = (name, detail) => {
  const row = el("div", "cust-row");
  const info = el("div", "info");
  info.appendChild(el("div", "nm", name));
  info.appendChild(el("div", "ds", detail));
  row.appendChild(info);
  return row;
};

function standardReadinessStateText(readiness) {
  if (readiness.ready) return t("environment.readiness.ready");
  if (readiness.state === "needs_setup") return t("environment.readiness.needsSetup");
  if (readiness.state === "needs_repair") return t("environment.readiness.needsRepair");
  return t("environment.readiness.unavailable");
}
function renderStandardProfileReadiness(readiness) {
  const card = el("section", "standard-readiness-card state-" + readiness.state);
  const head = el("div", "standard-readiness-head");
  const title = el("div");
  title.appendChild(el("div", "standard-readiness-title", t("environment.readiness.cardTitle")));
  title.appendChild(el("div", "standard-readiness-summary", standardReadinessStateText(readiness)));
  head.appendChild(title);
  const refresh = el("button", "outline-btn small", t("environment.readiness.refresh"));
  refresh.onclick = () => custTab("compute");
  head.appendChild(refresh); card.appendChild(head);

  if (readiness.missing_environments.length) {
    const section = el("div", "standard-readiness-gap");
    section.appendChild(el("div", "standard-readiness-label", t("environment.readiness.missingEnvironments")));
    const list = el("ul");
    readiness.missing_environments.forEach(name => list.appendChild(el("li", null, name)));
    section.appendChild(list); card.appendChild(section);
  }
  Object.entries(readiness.missing_packages).forEach(([environment, packages]) => {
    if (!packages.length) return;
    const section = el("div", "standard-readiness-gap");
    section.appendChild(el("div", "standard-readiness-label", t("environment.readiness.missingPackages", environment)));
    const list = el("ul", "standard-readiness-packages");
    packages.forEach(packageName => list.appendChild(el("li", null, packageName)));
    section.appendChild(list); card.appendChild(section);
  });

  const remediation = readiness.remediation;
  if (remediation && remediation.requires_explicit_action && remediation.commands.length) {
    const section = el("div", "standard-readiness-remediation");
    section.appendChild(el("div", "standard-readiness-label", t("environment.readiness.remediation")));
    section.appendChild(el("div", "standard-readiness-explicit", t("environment.readiness.explicitOnly")));
    remediation.commands.forEach(item => {
      const row = el("div", "standard-readiness-command");
      const command = el("code", null, item.command);
      if (item.label) command.setAttribute("aria-label", item.label);
      const copy = el("button", "outline-btn small", t("environment.readiness.copy"));
      copy.onclick = async () => {
        try {
          if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("clipboard unavailable");
          await navigator.clipboard.writeText(item.command);
          copy.textContent = t("code.copied");
          hint(t("environment.readiness.copied"));
          setTimeout(() => { copy.textContent = t("environment.readiness.copy"); }, 1200);
        } catch { hint(t("nb.action.failed"), true); }
      };
      row.appendChild(command); row.appendChild(copy); section.appendChild(row);
    });
    card.appendChild(section);
  }
  return card;
}

async function custCompute(c) {
  try {
    const [gpu, env, host] = await Promise.all([
      api("/compute/gpu").catch(() => ({ available: false })),
      refreshEnvironmentStatus().then(status => status || { environments: [] }),
      api("/compute/local/hostinfo").catch(() => ({})),
    ]);
    c.innerHTML = "";
    c.appendChild(hdr(t("cust.compute.title"), t("cust.compute.desc")));
    const readiness = S.standardProfileReadiness;
    if (readiness && readiness.enabled) c.appendChild(renderStandardProfileReadiness(readiness));
    c.appendChild(infoRow(t("cust.compute.host"), t("cust.compute.hostDetail", host.python || "?", host.machine || "", host.cpu_count || "?", host.ram_gb || "?", host.disk_free_gb || "?")));
    c.appendChild(infoRow("GPU", gpu.available ? (gpu.gpu_name || t("cust.compute.gpuAvailable")) : t("cust.compute.gpuUnavailable")));
    await renderRemoteGPU(c);
    const envs = env.environments || [];
    envs.forEach(e => {
      const inst = (e.packages || []).filter(p => p.installed);
      c.appendChild(infoRow(
        t("cust.compute.kernelLabel", e.language, e.status === "installing" ? t("cust.compute.kernelInstalling") : t("cust.compute.kernelReady")),
        t("cust.compute.preinstalledDetail", e.package_count, inst.slice(0, 18).map(p => p.name).join("、") + (inst.length > 18 ? " …" : ""))
      ));
    });
    const ins = el("div", "cust-row"); const info = el("div", "info");
    info.appendChild(el("div", "nm", t("cust.compute.installExtraName")));
    const dsc = el("div", "ds"); const inp = el("input");
    inp.placeholder = t("cust.compute.installPlaceholder"); inp.className = "cust-input";
    const btn = el("button", "outline-btn small", t("cust.compute.installBtn"));
    btn.onclick = async () => {
      const pkgs = inp.value.trim().split(/\s+/).filter(Boolean); if (!pkgs.length) return;
      btn.disabled = true; btn.textContent = t("cust.compute.installingBtn");
      try {
        const r = S.currentId
          ? await api(`/frames/${S.currentId}/kernel/install`, { method: "POST", body: JSON.stringify({ packages: pkgs, restart: true }) })
          : await api(`/kernel/install`, { method: "POST", body: JSON.stringify({ packages: pkgs }) });
        hint(r.ok
          ? t("step.env.installed", (r.installed || []).join("、") + (r.restarted ? t("cust.compute.kernelRestarted") : ""))
          : t("toast.compute.installFailed", ((r.failed && r.failed[0] && r.failed[0].error) || t("toast.compute.installSeeLogs"))));
        if (r.ok) S._envSnapById = {};
        custTab("compute");
      } catch (e) { hint(t("toast.compute.installFailed", apiErrorText(e)), true); }
      btn.disabled = false; btn.textContent = t("cust.compute.installBtn");
    };
    dsc.appendChild(inp); dsc.appendChild(btn); info.appendChild(dsc); ins.appendChild(info); c.appendChild(ins);
    await renderJobs(c);
  } catch (e) { c.textContent = t("versions.load.err", e.message); }
}
async function renderJobs(c) {
  c.appendChild(hdr(t("cust.jobs.title"), t("cust.jobs.desc")));
  const sub = el("div", "cust-row"); const si = el("div", "info"); si.appendChild(el("div", "nm", t("cust.jobs.submitName")));
  const row = el("div", "job-submit");
  const sel = el("select", "cust-input"); sel.style.flex = "0 0 92px"; ["bash", "python"].forEach(k => { const o = el("option", null, k); o.value = k; sel.appendChild(o); });
  const cmd = el("input", "cust-input"); cmd.placeholder = t("cust.jobs.cmdPlaceholder");
  const go = el("button", "solid-btn small", t("cust.jobs.runBtn"));
  go.onclick = async () => { const command = cmd.value.trim(); if (!command) return; go.disabled = true; try { await api("/compute/jobs", { method: "POST", body: JSON.stringify({ command, kind: sel.value }) }); cmd.value = ""; await refreshJobList(list); } catch (e) { hint(t("toast.submitFailed", apiErrorText(e)), true); } go.disabled = false; };
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
    // The drop, on the row. The job record has carried these counters all
    // along and the only trace of a cut anywhere in the UI was a notice
    // prepended inside the output modal, which a reader has to open to see.
    info.appendChild(el("div", "ds", (j.duration_s != null ? j.duration_s + "s" : "") + (j.exit_code != null ? " · exit " + j.exit_code : "") + (j.truncated ? " " + t("cust.jobs.dropped", (j.dropped_bytes || 0).toLocaleString()) : "")));
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
  const body = $("#modal-body"); body.innerHTML = "<div class='dock-empty'>" + t("common.loading") + "</div>"; openModalEl($("#modal"));
  const load = async () => { if (S._modalMode !== mode || $("#modal").classList.contains("hidden")) return; let d; try { d = await api(`/compute/jobs/${id}`); } catch (e) { body.innerHTML = t("job.outputLoadFailed"); return; } if (S._modalMode !== mode) return; body.innerHTML = ""; const pre = el("pre", "job-output", d.output || t("job.outputEmpty")); body.appendChild(pre); if (d.status === "running" || d.status === "queued") setTimeout(load, 1200); };
  load();
}
function doubaoSearchResultText(response) {
  const results = response && Array.isArray(response.results) ? response.results : [];
  return results.map((item, index) => {
    const title = item && typeof item.title === "string" ? item.title.trim() : "";
    const url = item && typeof item.url === "string" ? item.url.trim() : "";
    const snippet = item && typeof item.snippet === "string" ? item.snippet.trim() : "";
    return [`${index + 1}. ${title || url}`, url, snippet].filter(Boolean).join("\n");
  }).filter(Boolean).join("\n\n");
}
function doubaoSearchCard(config, configError) {
  const state = {
    keyConfigured: !!(config && config.key_configured),
    arkKeyReused: !!(config && config.ark_key_reused),
  };
  const card = el("section", "datapro-card doubao-search-card");
  const heading = el("div", "datapro-head");
  const headingText = el("div");
  const title = el("div", "datapro-title");
  title.appendChild(document.createTextNode(t("cust.doubao.title") + " "));
  title.appendChild(el("span", "pill", t("cust.doubao.primary")));
  headingText.appendChild(title);
  headingText.appendChild(el("div", "datapro-desc", t("cust.doubao.desc")));
  heading.appendChild(headingText); card.appendChild(heading);

  const credentials = el("div", "datapro-field");
  credentials.appendChild(el("label", "skill-lbl", t("cust.doubao.keyLabel")));
  const credentialRow = el("div", "datapro-input-row");
  const keyInput = el("input", "cust-input");
  keyInput.id = "doubao-search-plan-key"; keyInput.type = "password"; keyInput.autocomplete = "off";
  keyInput.autocapitalize = "off"; keyInput.spellcheck = false;
  keyInput.placeholder = state.arkKeyReused ? t("cust.doubao.keyPlaceholderArk") : (state.keyConfigured ? t("cust.doubao.keyPlaceholderSet") : t("cust.doubao.keyPlaceholder"));
  const saveKey = el("button", "solid-btn small", t("cust.doubao.saveKey"));
  saveKey.dataset.action = "doubao-search-save-key";
  const keyState = el("div", "datapro-credential-state", configError ? t("cust.doubao.requestFailed", apiErrorText(configError)) : (state.arkKeyReused ? t("cust.doubao.keyArkReused") : (state.keyConfigured ? t("cust.doubao.keyConfigured") : t("cust.doubao.keyMissing"))));
  keyState.classList.toggle("bad", !!configError || (!state.keyConfigured && !state.arkKeyReused));
  saveKey.onclick = async () => {
    let secret = keyInput.value.trim();
    keyInput.value = "";
    if (!secret) { hint(t("cust.doubao.keyRequired"), true); return; }
    saveKey.disabled = true;
    const request = api("/doubao-search/config", { method: "POST", body: JSON.stringify({ agent_plan_key: secret }) });
    secret = "";
    try {
      const saved = await request;
      state.keyConfigured = !!(saved && saved.key_configured); state.arkKeyReused = !!(saved && saved.ark_key_reused);
      keyInput.placeholder = state.arkKeyReused ? t("cust.doubao.keyPlaceholderArk") : t("cust.doubao.keyPlaceholderSet");
      keyState.textContent = state.arkKeyReused ? t("cust.doubao.keyArkReused") : t("cust.doubao.keyConfigured");
      keyState.classList.remove("bad"); hint(t("cust.doubao.keySaved"));
    } catch (error) {
      keyState.textContent = t("cust.doubao.requestFailed", apiErrorText(error)); keyState.classList.add("bad");
    } finally {
      keyInput.value = ""; saveKey.disabled = false;
    }
  };
  keyInput.onkeydown = event => { if (event.key === "Enter") { event.preventDefault(); saveKey.click(); } };
  credentialRow.appendChild(keyInput); credentialRow.appendChild(saveKey);
  credentials.appendChild(credentialRow); credentials.appendChild(keyState); card.appendChild(credentials);

  const queryField = el("div", "datapro-field");
  queryField.appendChild(el("label", "skill-lbl", t("cust.doubao.queryLabel")));
  const query = el("textarea", "datapro-query"); query.id = "doubao-search-query"; query.rows = 3; query.maxLength = 100; query.placeholder = t("cust.doubao.queryPlaceholder");
  const queryActions = el("div", "datapro-query-actions");
  const status = el("div", "datapro-status"); status.dataset.doubaoSearchStatus = ""; status.setAttribute("aria-live", "polite");
  const search = el("button", "solid-btn small", t("cust.doubao.search")); search.dataset.action = "doubao-search-run";
  queryActions.appendChild(status); queryActions.appendChild(search); queryField.appendChild(query); queryField.appendChild(queryActions); card.appendChild(queryField);

  const output = el("div", "datapro-output"); output.appendChild(el("div", "skill-lbl", t("cust.doubao.result")));
  const result = el("pre", "datapro-result", t("cust.doubao.noResult")); result.dataset.doubaoSearchResult = ""; output.appendChild(result); card.appendChild(output);
  search.onclick = async () => {
    const text = query.value.trim();
    if (!text) { hint(t("cust.doubao.queryRequired"), true); return; }
    search.disabled = true; search.textContent = t("cust.doubao.searching"); status.textContent = t("cust.doubao.searching"); status.className = "datapro-status"; result.textContent = t("cust.doubao.noResult");
    try {
      // This product check is intentionally dedicated: the backend must not
      // satisfy it with Tavily or any keyless fallback engine.
      const response = await api("/doubao-search/search", { method: "POST", body: JSON.stringify({ query: text }) });
      const results = response && Array.isArray(response.results) ? response.results : [];
      const available = !!(response && response.available === true && response.source === "doubao" && Number.isInteger(response.count) && response.count === results.length && results.length > 0);
      status.textContent = available ? t("cust.doubao.available") : (response.message || t("cust.doubao.empty"));
      status.className = "datapro-status " + (available ? "ok" : "bad");
      result.textContent = doubaoSearchResultText(response) || t("cust.doubao.noResult");
    } catch (error) {
      status.textContent = t("cust.doubao.requestFailed", apiErrorText(error)); status.className = "datapro-status bad"; result.textContent = t("cust.doubao.noResult");
    } finally {
      search.disabled = false; search.textContent = t("cust.doubao.search");
    }
  };
  query.onkeydown = event => { if ((event.metaKey || event.ctrlKey) && event.key === "Enter") { event.preventDefault(); search.click(); } };
  return card;
}

// Backup web-search API key (Tavily). The endpoint is fixed; only the key is
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
async function custNetwork(c) { try {
  const [d, doubao] = await Promise.all([
    api("/preferences/builtin-allowlist"),
    api("/doubao-search/config").then(config => ({ config })).catch(error => ({ config: {}, error })),
  ]);
  c.innerHTML = ""; c.appendChild(hdr(t("cust.network.title"), t("cust.network.desc")));
  c.appendChild(doubaoSearchCard(doubao.config, doubao.error));
  const master = el("div", "cust-row"); const mi = el("div", "info");
  mi.appendChild(el("div", "nm", t("cust.network.allowName")));
  mi.appendChild(el("div", "ds", d.enabled ? t("cust.network.enabledDesc") : t("cust.network.disabledDesc")));
  master.appendChild(mi);
  const tg = el("button", "toggle" + (d.enabled ? " on" : ""));
  tg.onclick = async () => { const on = tg.classList.toggle("on"); try { const r = await api("/network/status", { method: "PUT", body: JSON.stringify({ enabled: on }) }); hint(r.enabled ? t("toast.network.enabled") : t("toast.network.disabled")); } catch {} };
  master.appendChild(tg); c.appendChild(master);
  await searchKeyRow(c);
  ((d && d.groups) || []).forEach(g => {
    const row = el("div", "cust-row"); const info = el("div", "info"); const nm = el("div", "nm");
    nm.appendChild(el("span", null, g.name || g.label)); info.appendChild(nm);
    const box = el("div", "ds"); (g.domains || []).slice(0, 12).forEach(dm => box.appendChild(el("span", "pill", dm)));
    info.appendChild(box); row.appendChild(info); c.appendChild(row);
  });
  await telemetryRow(c);
} catch (e) { c.textContent = t("versions.load.err", e.message); } }

async function telemetryRow(c) {
  let d; try { d = await api("/telemetry/consent"); } catch { return; }
  const row = el("div", "cust-row"); const info = el("div", "info");
  info.appendChild(el("div", "nm", t("cust.telemetry.name")));
  info.appendChild(el("div", "ds", d.env_locked ? t("cust.telemetry.envlock") : (d.enabled ? t("cust.telemetry.on") : t("cust.telemetry.off"))));
  row.appendChild(info);
  const tg = el("button", "toggle" + (d.enabled ? " on" : "") + (d.env_locked ? " off" : ""));
  if (d.env_locked) { tg.disabled = true; }
  // A privacy toggle must never end up showing a state the server does not
  // hold. The old handler flipped optimistically, swallowed failures without
  // restoring, and stayed clickable while a PUT was in flight — so two rapid
  // clicks raced and whichever response arrived *last* won, regardless of
  // which click the user made last.
  //
  // Two variables instead of reading the DOM: `desired` is what the user has
  // asked for, `confirmed` is what the server last told us it holds. Clicks
  // only move `desired`; one drain loop reconciles the difference, never with
  // two requests in flight. Extra clicks during a round trip are therefore
  // coalesced rather than dropped — the last one the user made is the one that
  // ends up applied — and the button stays live instead of going dead mid-
  // request.
  else {
    let running = false;
    let desired = !!d.enabled;
    let confirmed = !!d.enabled;
    const paint = (on) => {
      tg.classList.toggle("on", !!on);
      info.lastChild.textContent = on ? t("cust.telemetry.on") : t("cust.telemetry.off");
    };
    const drain = async () => {
      if (running) return;
      running = true;
      try {
        while (desired !== confirmed) {
          const want = desired;
          try {
            const r = await api("/telemetry/consent", { method: "PUT", body: JSON.stringify({ enabled: want }) });
            confirmed = !!r.enabled;
            // The server is authoritative: a grant vetoed by the environment
            // comes back disabled, and retrying it forever would be a spin.
            // Only overwrite the ask if the user has not since changed it.
            if (confirmed !== want && desired === want) desired = confirmed;
            hint(confirmed ? t("toast.telemetry.on") : t("toast.telemetry.off"));
          } catch (e) {
            // The server still holds `confirmed`; abandon the ask rather than
            // leave the control showing a state that was never applied.
            desired = confirmed;
            hint(t("toast.telemetry.failed", (e && e.message) || ""));
          }
          // Only repaint once the ask and the truth agree. Painting every
          // round trip would flash the now-stale server state at a user who
          // has already clicked again, mid-drain.
          if (desired === confirmed) paint(confirmed);
        }
      } finally { running = false; }
    };
    tg.onclick = () => { desired = !desired; paint(desired); drain(); };
  }
  row.appendChild(tg); c.appendChild(row);
}
// Memory has two tiers: "global", which every project inherits, and one
// project. The Save button used to send neither, so the server stored the
// literal "default" — a project nothing here creates — while injection reads
// the session's real project id. Saves listed fine and reached no prompt ever.
// So the scope is chosen here and always sent, and a row says which tier it is
// in. Unknown ids (rows left by that older write) render as the raw id rather
// than as the generic project fallback, so an orphan looks like one.
function memScopes() {
  const pid = effProject();
  const out = [{ id: "global", label: t("cust.memory.scope.global") }];
  if (pid) out.push({ id: pid, label: memScopeLabel(pid) });
  return out;
}
function memScopeLabel(pid) {
  if (!pid || pid === "global") return t("cust.memory.scope.global");
  const p = S.projects.find(x => (x.project_id || x.id) === pid);
  return (p && p.name) || pid;
}
async function custMemory(c) { try {
  const m = await api("/memory/enabled");
  const mem = await api("/memory?project_id=all").catch(() => ({ memories: [] }));
  const cats = await api("/memory/categories?project_id=all").catch(() => ({ categories: [] }));
  // What the active scope would actually inject, not merely what is stored:
  // this pane listed every scope's rows and said nothing about which of them
  // reach a prompt, which is how a write to a dead scope looked healthy.
  const scopes = memScopes(); const active = scopes[scopes.length - 1].id;
  const ctx = await api(`/memory/context?project_id=${encodeURIComponent(active)}`).catch(() => null);
  c.innerHTML = ""; c.appendChild(hdr(t("cust.memory.title"), t("cust.memory.desc")));
  const master = el("div", "cust-row"); const mi = el("div", "info"); mi.appendChild(el("div", "nm", t("cust.memory.enableName"))); mi.appendChild(el("div", "ds", m.enabled ? t("cust.memory.enabledDesc") : t("cust.memory.disabledDesc"))); master.appendChild(mi); const tg = el("button", "toggle" + (m.enabled ? " on" : "")); tg.onclick = async () => { const on = tg.classList.toggle("on"); try { await api("/memory/enabled", { method: "PUT", body: JSON.stringify({ enabled: on }) }); hint(on ? t("toast.memory.enabled") : t("toast.memory.disabled")); } catch {} }; master.appendChild(tg); c.appendChild(master);
  // add with category
  const add = el("div", "cust-row"); const ai = el("div", "info"); ai.appendChild(el("div", "nm", t("cust.memory.addName"))); const ad = el("div", "job-submit");
  const catSel = el("select", "cust-input"); catSel.style.flex = "0 0 120px"; ["user", "project", "preference", "fact", "general"].forEach(k => { const o = el("option", null, k); o.value = k; catSel.appendChild(o); });
  const scopeSel = el("select", "cust-input"); scopeSel.style.flex = "0 0 150px"; scopeSel.title = t("cust.memory.scopeName"); scopes.forEach(s => { const o = el("option", null, s.label); o.value = s.id; scopeSel.appendChild(o); }); scopeSel.value = active;
  const inp = el("input", "cust-input"); inp.placeholder = t("cust.memory.contentPlaceholder");
  const btn = el("button", "solid-btn small", t("common.save")); btn.onclick = async () => { const v = inp.value.trim(); if (!v) return; try { await api("/memory", { method: "POST", body: JSON.stringify({ content: v, block: catSel.value, project_id: scopeSel.value }) }); inp.value = ""; custTab("memory"); } catch (e) { hint(t("artifact.save.err", apiErrorText(e)), true); } };
  ad.appendChild(scopeSel); ad.appendChild(catSel); ad.appendChild(inp); ad.appendChild(btn); ai.appendChild(ad); add.appendChild(ai); c.appendChild(add);
  // What this scope injects, said out loud. The budgets already report what
  // they withheld; nothing showed it to the person who saved the item.
  if (ctx) { const sr = el("div", "cust-row"); const si = el("div", "info"); si.appendChild(el("div", "nm", t("cust.memory.injectedInto", memScopeLabel(active)))); si.appendChild(el("div", "ds", t("cust.memory.injectedCounts", String(ctx.included_count || 0), String((ctx.omitted || []).length), String(ctx.inherited_count || 0), String(ctx.overridden_count || 0)))); sr.appendChild(si); c.appendChild(sr); }
  // category chips
  const catList = (cats.categories || []);
  if (catList.length) { const cr = el("div", "cust-row"); const ci = el("div", "info"); ci.appendChild(el("div", "nm", t("cust.memory.categories"))); const box = el("div", "ds"); catList.forEach(k => box.appendChild(el("span", "pill", (k.block || "general") + " · " + k.count))); ci.appendChild(box); cr.appendChild(ci); c.appendChild(cr); }
  // memories grouped by block
  const groups = {}; (mem.memories || []).forEach(x => { const b = x.block || "general"; (groups[b] = groups[b] || []).push(x); });
  Object.keys(groups).sort().forEach(block => {
    c.appendChild(el("div", "cust-subhead", block));
    groups[block].forEach(x => {
      const row = el("div", "cust-row"); const info = el("div", "info");
      info.appendChild(el("div", "ds", x.content || ""));
      const sc = el("div", "ds"); sc.appendChild(el("span", "pill", memScopeLabel(x.project_id)));
      // Said out loud, because it is what retention measures. A memory nobody
      // has touched in a year stops being injected, and until there was an
      // edit the only "touch" a row could have was the day it was written.
      if (x.updated_at) sc.appendChild(el("span", "pill", t("cust.memory.edited")));
      info.appendChild(sc); row.appendChild(info);
      // Edit in place. Correcting standing context used to mean delete and
      // rewrite: two round trips through a scope that may be at its cap, so
      // the second can fail and leave the user with neither version.
      const edit = el("button", "icon-ghost"); edit.title = t("common.edit");
      edit.appendChild(iconEl("pencil", 14));
      edit.onclick = async () => {
        const next = prompt(t("cust.memory.editPrompt"), x.content || "");
        if (next === null) return;
        const value = String(next).trim();
        if (!value || value === (x.content || "")) return;
        try {
          await api(`/memory/${x.memory_id}?project_id=${encodeURIComponent(x.project_id || "global")}`, { method: "PATCH", body: JSON.stringify({ content: value }) });
          custTab("memory");
        } catch (e) { hint(apiErrorText(e), true); }
      };
      row.appendChild(edit);
      const del = el("button", "icon-ghost"); del.appendChild(iconEl("trash-2", 14));
      del.onclick = async () => { try { await api(`/memory/${x.memory_id}?project_id=${encodeURIComponent(x.project_id || "global")}`, { method: "DELETE" }); custTab("memory"); } catch (e) { hint(apiErrorText(e), true); } };
      row.appendChild(del); c.appendChild(row);
    });
  });
  if (!(mem.memories || []).length) c.appendChild(el("div", "dock-empty", t("cust.memory.empty")));
} catch (e) { c.textContent = t("versions.load.err", e.message); } }
const LOCAL_MODEL_KINDS = new Set(["ollama", "lm_studio", "vllm", "llama_cpp"]);
function loopbackModelBase(value) {
  const text = publicText(value, 600);
  try {
    const parsed = new URL(text);
    const host = parsed.hostname.toLowerCase();
    const safeHost = host === "127.0.0.1" || host === "::1" || host === "[::1]";
    return ["http:", "https:"].includes(parsed.protocol) && safeHost && !parsed.username && !parsed.password && !parsed.search && !parsed.hash ? parsed.toString().replace(/\/$/, "") : "";
  } catch { return ""; }
}
function sanitizeLocalModelDiscovery(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  const endpoints = [];
  (Array.isArray(source.endpoints) ? source.endpoints : []).slice(0, 20).forEach(raw => {
    if (!raw || typeof raw !== "object") return;
    const kind = publicText(raw.kind, 32), baseUrl = loopbackModelBase(raw.base_url);
    if (!LOCAL_MODEL_KINDS.has(kind) || !baseUrl || raw.local !== true || raw.provider !== "chatgpt") return;
    const models = [];
    (Array.isArray(raw.models) ? raw.models : []).slice(0, 500).forEach(value => {
      if (typeof value !== "string") return;
      const model = publicText(value, 512); if (model && !models.includes(model)) models.push(model);
    });
    endpoints.push({
      kind, label: publicText(raw.label, 80) || kind, provider: "chatgpt", base_url: baseUrl,
      models, default_model: models.includes(raw.default_model) ? raw.default_model : (models[0] || ""),
      requires_api_key: false
    });
  });
  return { endpoints, probed: Math.max(0, Math.min(20, Number(source.probed) || 0)), mutated_settings: false };
}
function renderLocalModelEndpoints(root, discovery, profiles) {
  root.innerHTML = "";
  const endpoints = discovery && discovery.endpoints || [];
  if (!endpoints.length) { root.appendChild(el("div", "dock-empty", t("cust.models.local.none"))); return; }
  endpoints.forEach(endpoint => {
    const row = el("div", "cust-row local-model-row"), info = el("div", "info");
    info.appendChild(el("div", "nm", endpoint.label));
    info.appendChild(el("div", "ds", endpoint.base_url + " · " + t("cust.models.local.models", endpoint.models.length)));
    row.appendChild(info);
    const modelSelect = el("select", "cust-input local-model-select");
    if (!endpoint.models.length) { const option = el("option", null, t("models.none")); option.value = ""; modelSelect.appendChild(option); }
    endpoint.models.forEach(model => { const option = el("option", null, model); option.value = model; option.selected = model === endpoint.default_model; modelSelect.appendChild(option); });
    row.appendChild(modelSelect);
    const configured = () => (profiles || []).some(profile => loopbackModelBase(profile.base_url) === endpoint.base_url && profile.model === modelSelect.value);
    const add = el("button", "outline-btn small", configured() ? t("cust.models.local.configured") : t("cust.models.local.add"));
    add.disabled = configured() || !modelSelect.value;
    modelSelect.onchange = () => { add.disabled = configured() || !modelSelect.value; add.textContent = configured() ? t("cust.models.local.configured") : t("cust.models.local.add"); };
    add.onclick = async () => {
      const model = publicText(modelSelect.value, 512); if (!model || configured()) return;
      add.disabled = true;
      try {
        await api("/model-profiles", { method: "POST", body: JSON.stringify({
          name: endpoint.label + " · " + model, provider: endpoint.provider,
          base_url: endpoint.base_url, model
        }) });
        hint(t("cust.models.local.added", model)); custTab("models");
      } catch (error) { add.disabled = false; hint(t("artifact.save.err", publicText(error && error.message, 240)), true); }
    };
    row.appendChild(add); root.appendChild(row);
  });
}
// Which protocols a user may pick is the daemon's answer, not a copy of it kept
// here. `gemini` and `openai_responses` are both in PROFILE_PROTOCOLS and both
// accepted by POST/PATCH, while this pane offered a fixed three -- so a user
// holding a Gemini key had no way to say so. The list has to be *generated* from
// the served catalogue or the next protocol added server-side is unreachable the
// same way. Only the label stays client-side: a protocol nobody has translated
// yet shows its id, which is a usable option rather than a missing one.
function modelProtocolOptions(served) {
  const labelKeys = {
    chatgpt: "cust.models.protocol.openai",
    claude: "cust.models.protocol.anthropic",
    ark: "cust.models.protocol.ark",
    gemini: "cust.models.protocol.gemini",
    openai_responses: "cust.models.protocol.openaiResponses",
  };
  const ids = [];
  (Array.isArray(served) ? served : []).forEach(value => {
    const id = typeof value === "string" ? value.trim().slice(0, 64) : "";
    if (id && !ids.includes(id)) ids.push(id);
  });
  // A daemon too old to serve the catalogue still has to leave the form usable;
  // these three are what shipped before `protocols` was part of the payload.
  const list = ids.length ? ids : ["chatgpt", "claude", "ark"];
  // tOptional, not t: a missing translation must not put a dot-key in a menu.
  return list.map(id => ({ value: id, label: tOptional(labelKeys[id] || "") || id }));
}
async function custModels(c) {
  c.innerHTML = ""; c.appendChild(hdr(t("cust.tab.models"), t("cust.models.subtitle2")));
  let data = { profiles: [], active_id: "", protocols: [] };
  try { data = await api("/model-profiles"); } catch (e) { c.appendChild(el("div", "dock-empty", t("versions.load.err", e.message))); return; }
  let editing = null;  // set to a profile object when editing that row
  const protocols = modelProtocolOptions(data.protocols);
  const protocolIds = new Set(protocols.map(item => item.value));
  const protocolLabel = provider => {
    const match = protocols.find(item => item.value === provider);
    return match ? match.label : provider;
  };

  // Local discovery is a read-only, fixed-loopback scan. The endpoint must be
  // explicitly added before it can affect model settings.
  c.appendChild(el("div", "cust-subhead", t("cust.models.local.title")));
  const localInfo = el("div", "cust-sub", t("cust.models.local.desc")), localActions = el("div", "form-actions");
  const localResults = el("div", "local-model-results"), scanLocal = el("button", "outline-btn small", t("cust.models.local.scan"));
  const runLocalScan = async force => {
    scanLocal.disabled = true; scanLocal.textContent = t("cust.models.local.scanning");
    localResults.innerHTML = ""; localResults.appendChild(el("div", "dock-empty", t("cust.models.local.scanning")));
    try {
      const result = sanitizeLocalModelDiscovery(await api("/model-endpoints/discover" + (force ? "?force=1" : "")));
      renderLocalModelEndpoints(localResults, result, data.profiles || []);
    } catch (error) {
      localResults.innerHTML = ""; localResults.appendChild(el("div", "timeline-error", t("cust.models.local.error", publicText(error && error.message, 240))));
    } finally { scanLocal.disabled = false; scanLocal.textContent = t("cust.models.local.scan"); }
  };
  scanLocal.onclick = () => runLocalScan(true); localActions.appendChild(scanLocal);
  c.appendChild(localInfo); c.appendChild(localActions); c.appendChild(localResults);
  // Opening this pane used to run the scan itself, so every visit -- including
  // the re-render after every save, activate and delete -- probed four loopback
  // ports nobody asked it to. Readiness is answered from local state precisely
  // so that opening Customize costs nothing; the one control here that touches a
  // socket waits for the button, like the per-profile probe beside it.
  localResults.appendChild(el("div", "dock-empty", t("cust.models.local.idle")));

  // --- add / edit form ---
  const head = el("div", "cust-subhead", t("cust.models.addHeading"));
  c.appendChild(head);
  const form = el("div", "skill-form");
  const nameIn = el("input", "cust-input"); nameIn.placeholder = t("cust.models.namePlaceholder");
  const provIn = el("select", "cust-input");
  protocols.forEach(({ value, label }) => { const option = el("option"); option.value = value; option.textContent = label; provIn.appendChild(option); });
  const baseIn = el("input", "cust-input"); baseIn.placeholder = t("cust.models.baseUrlPlaceholder");
  const modelIn = el("input", "cust-input"); modelIn.placeholder = t("cust.models.modelPlaceholder2");
  const keyIn = el("input", "cust-input"); keyIn.type = "password"; keyIn.placeholder = "API Key"; keyIn.autocomplete = "off";
  form.appendChild(el("label", "skill-lbl", t("cust.connectors.namePlaceholder"))); form.appendChild(nameIn);
  form.appendChild(el("label", "skill-lbl", t("cust.models.label.protocol"))); form.appendChild(provIn);
  form.appendChild(el("label", "skill-lbl", "Base URL")); form.appendChild(baseIn);
  form.appendChild(el("label", "skill-lbl", t("label.model"))); form.appendChild(modelIn);
  form.appendChild(el("label", "skill-lbl", "API Key")); form.appendChild(keyIn);
  const save = el("button", "solid-btn", t("cust.models.addBtn"));
  const cancel = el("button", "outline-btn small", t("cust.models.cancelEdit")); cancel.style.display = "none";
  const clearLegacyProtocol = () => provIn.querySelectorAll("option[data-legacy]").forEach(option => option.remove());
  const resetForm = () => { editing = null; clearLegacyProtocol(); nameIn.value = baseIn.value = modelIn.value = keyIn.value = ""; provIn.value = "chatgpt"; keyIn.placeholder = "API Key"; save.textContent = t("cust.models.addBtn"); head.textContent = t("cust.models.addHeading"); cancel.style.display = "none"; };
  const startEdit = (p) => { editing = p; clearLegacyProtocol(); nameIn.value = p.name || ""; if (protocolIds.has(p.provider)) { provIn.value = p.provider; } else { const legacy = el("option"); legacy.value = p.provider || ""; legacy.textContent = p.provider || "—"; legacy.disabled = true; legacy.dataset.legacy = "true"; provIn.appendChild(legacy); provIn.value = legacy.value; } baseIn.value = p.base_url || ""; modelIn.value = p.model || ""; keyIn.value = ""; keyIn.placeholder = p.has_api_key ? t("cust.models.keyPlaceholderSet") : t("cust.models.keyPlaceholderUnset"); save.textContent = t("cust.models.updateBtn"); head.textContent = t("cust.models.editHeading", (p.name || p.id)); cancel.style.display = ""; nameIn.focus(); c.scrollTop = 0; };
  cancel.onclick = resetForm;
  save.onclick = async () => {
    const nm = nameIn.value.trim(); if (!nm) { hint(t("toast.specialist.enterName"), true); nameIn.focus(); return; }
    save.disabled = true; const label = save.textContent; save.textContent = t("common.saving");
    const body = { name: nm, base_url: baseIn.value.trim(), model: modelIn.value.trim() };
    if (protocolIds.has(provIn.value)) body.provider = provIn.value;
    if (keyIn.value) body.api_key = keyIn.value;
    try {
      if (editing) { await api(`/model-profiles/${editing.id}`, { method: "PATCH", body: JSON.stringify(body) }); hint(t("toast.models.updated", nm)); }
      else { await api("/model-profiles", { method: "POST", body: JSON.stringify(body) }); hint(t("toast.models.added", nm)); }
      if (editing && editing.id === data.active_id) { refreshKeyBanner(); await loadModels(); }
      custTab("models");
    } catch (e) { save.disabled = false; save.textContent = label; hint(t("artifact.save.err", apiErrorText(e)), true); }
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
    const bits = []; if (p.provider) bits.push(protocolLabel(p.provider)); if (p.model) bits.push(p.model); bits.push(p.has_api_key ? t("cust.models.hasKey") : (loopbackModelBase(p.base_url) ? t("cust.models.local.keyless") : t("cust.models.noKey")));
    info.appendChild(el("div", "ds", bits.join(" · ") + (p.base_url ? "  ·  " + p.base_url : "")));
    // `readiness` is computed server-side from local state alone — no network,
    // deliberately — and had no reader: the row derived its own worse version
    // from `has_api_key`, so "no model named and this protocol has no default"
    // and "this build cannot dispatch that protocol" both displayed as a
    // configured profile that simply failed later.
    const rd = p.readiness || {};
    if (rd.state && rd.state !== "ready") {
      info.appendChild(el("div", "ds prof-warn", publicText(rd.detail || rd.state, 200)));
    }
    // Where a probe's answer lands. Created empty so the result appears in the
    // row that was tested rather than as a toast that outlives its context.
    const probeOut = el("div", "ds prof-probe"); probeOut.style.display = "none";
    info.appendChild(probeOut);
    row.appendChild(info);
    if (!isActive) { const use = el("button", "outline-btn small", t("cust.models.setActive")); use.onclick = async () => { use.disabled = true; try { await api(`/model-profiles/${p.id}/activate`, { method: "POST" }); hint(t("toast.models.switched", (p.name || p.id))); S.defaultModel = p.model || S.defaultModel; await loadModels(); refreshKeyBanner(); custTab("models"); } catch (e) { use.disabled = false; hint(t("toast.switchFailed", apiErrorText(e)), true); } }; row.appendChild(use); } else { row.appendChild(el("div", "col-spacer")); }
    // The only thing here that spends a request against the user's provider
    // quota, so it is a button and never a render-time call.
    const test = el("button", "outline-btn small", t("cust.models.test"));
    test.onclick = async () => {
      test.disabled = true;
      probeOut.style.display = ""; probeOut.className = "ds prof-probe";
      probeOut.textContent = t("cust.models.testing");
      try {
        const r = await api(`/model-profiles/${encodeURIComponent(p.id)}/probe`, { method: "POST" });
        // "reachable" is not "verified" — the server is careful about that
        // difference and the wording here keeps it.
        probeOut.className = "ds prof-probe " + (r.reachable ? "ok" : "bad");
        probeOut.textContent = (r.reachable ? t("cust.models.reachable") : t("cust.models.unreachable"))
          + (r.detail ? " — " + publicText(r.detail, 240) : "");
      } catch (e) {
        probeOut.className = "ds prof-probe bad";
        probeOut.textContent = apiErrorText(e);
      } finally { test.disabled = false; }
    };
    row.appendChild(test);
    const edit = el("button", "outline-btn small", t("common.edit")); edit.onclick = () => startEdit(p); row.appendChild(edit);
    const del = el("button", "icon-ghost"); del.title = t("common.delete"); del.appendChild(iconEl("trash-2", 14)); del.onclick = async () => { if (!confirm(t("model.delete.confirm", (p.name || p.id)))) return; try { await api(`/model-profiles/${p.id}`, { method: "DELETE" }); hint(t("toast.deleted")); if (isActive) { refreshKeyBanner(); await loadModels(); } custTab("models"); } catch (e) { hint(t("toast.deleteFailed", apiErrorText(e)), true); } }; row.appendChild(del);
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
  // esc() escapes &<> but not quotes, so any capture group interpolated into a
  // double-quoted HTML attribute must additionally neutralize " to prevent an
  // alt/href/src value from closing the attribute and injecting new ones.
  var escQuote = function (s) { return String(s).replace(/"/g, "&quot;"); };
  t = t.replace(/!\[([^\]]*)\]\((data:image\/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+)\)/g, function (m, alt, src) { return '<img alt="' + escQuote(alt) + '" src="' + src + '">'; });
  t = t.replace(/!\[([^\]]*)\]\((https?:\/\/[^\s)]+)\)/g, function (m, alt, src) { return '<img alt="' + escQuote(alt) + '" src="' + escQuote(src) + '">'; });
  t = t.replace(/\[([^\]]+)\]\(((?:https?:|mailto:|\/|#)[^\s)]+)\)/g, function (m, text, href) { return '<a href="' + escQuote(href) + '" target="_blank" rel="noopener">' + text + '</a>'; });
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
function parseTable(text, a) { const nm = (a.filename || "").toLowerCase(); if (nm.endsWith(".json") || /^\s*[\[{]/.test(text)) { try { let j = JSON.parse(text); if (!Array.isArray(j)) j = j.rows || j.data || j.candidates || j.items || []; if (Array.isArray(j) && j.length && typeof j[0] === "object") return j; } catch {} return null; } const lines = text.replace(/\r/g, "").split("\n").filter(l => l.trim()); if (lines.length < 2) return null; const sep = delimiterFor(nm, a && a.content_type, lines[0]); const cols = csv(lines[0], sep); return lines.slice(1).map(l => { const v = csv(l, sep); const o = {}; cols.forEach((c, i) => o[c] = v[i] ?? ""); return o; }); }
// The delimiter a tabular artifact actually uses.
//
// This was decided by `csv()` hardcoding a comma, so every `.tsv` parsed as a
// single column: the tile for a differential-expression table with three
// columns reported "1 column", and the column's *name* was the entire header
// line. Wrong numbers about scientific output, shown with the same confidence
// as right ones.
//
// The extension is checked first because it is a declaration. When there is
// none to trust -- science writes tab-separated `.txt` and `.dat` constantly --
// the header is sniffed, and the winner is whichever candidate splits it into
// the most fields. A file with no delimiter at all yields one field for every
// candidate, so the comma default is reached only when nothing distinguishes.
function delimiterFor(filename, contentType, headerLine) {
  const name = String(filename || "").toLowerCase();
  const type = String(contentType || "").toLowerCase();
  if (/\.tsv$/.test(name) || /tab-separated/.test(type)) return "\t";
  if (/\.csv$/.test(name) || /\bcsv\b/.test(type)) return ",";
  const header = String(headerLine || "");
  let best = ",", width = 1;
  for (const candidate of ["\t", ",", ";", "|"]) {
    const fields = csvFields(header, candidate).length;
    if (fields > width) { best = candidate; width = fields; }
  }
  return best;
}
// One field splitter, parameterised. `sep` defaults to a comma only so that
// callers predating the parameter keep their behaviour.
function csvFields(line, sep) {
  sep = sep || ",";
  const o = []; let cur = "", q = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
    else { if (c === '"') q = true; else if (c === sep) { o.push(cur); cur = ""; } else cur += c; }
  }
  o.push(cur); return o.map(s => s.trim());
}
function csv(line, sep) { return csvFields(line, sep); }
function ago(iso) { if (!iso) return ""; const t = new Date(iso).getTime(); if (isNaN(t)) return ""; const d = (Date.now() - t) / 1000; if (d < 60) return "just now"; if (d < 3600) return (d / 60 | 0) + "m"; if (d < 86400) return (d / 3600 | 0) + "h"; return (d / 86400 | 0) + "d"; }
function bytes(b) { b = b || 0; if (b < 1024) return b + " B"; if (b < 1048576) return (b / 1024).toFixed(1) + " KB"; return (b / 1048576).toFixed(1) + " MB"; }
function hint(t, err, spin) { const h = $("#composer-hint"); h.innerHTML = ""; if (!t) return; if (spin) { h.appendChild(iconEl("loader", 13, "spin")); h.appendChild(document.createTextNode(" ")); } const s = el("span", null, t); if (err) s.style.color = "var(--danger)"; h.appendChild(s); }
// `on` no longer means "typable" — it means "the next Enter starts a turn
// rather than queueing one". The textarea itself is never disabled: the server
// admits a follow-up sent mid-turn into its FIFO queue and always did, so
// disabling the box withheld a capability the backend already had. A disabled
// textarea also drops focus and throws away a half-typed @-mention, which made
// "wait for the turn to finish" cost the user their draft as well.
function enableComposer(on) {
  const c = $("#composer"); if (!c) return;
  c.disabled = false;
  c.classList.toggle("queueing", !on);
  c.placeholder = t(on ? "composer.placeholder" : "composer.placeholderQueue");
  renderQueueStrip();
}
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
  // Deduped by artifact identity, not by filename. Keying on the name was
  // right for the overlap this loop exists for (a project artifact is also a
  // session artifact) and wrong for everything else: two DIFFERENT artifacts
  // that happen to share a name -- `results.csv` here and `results.csv` in a
  // sibling conversation -- collapsed into one row, so the second was
  // unpickable and there was no way to reference it at all. `artifact_id` is
  // what the overlap actually is.
  const seen = new Set(); const out = [];
  for (const a of [...(pid ? _acFiles.list : []), ...(S.artifacts || [])]) {
    if (!a || !a.filename) continue;
    const key = a.artifact_id || a.id || a.filename;
    if (seen.has(key)) continue;
    seen.add(key); out.push(a);
  }
  return out;
}
async function acUpdate() {
  const d = acDetect(); if (!d) { acClose(); return; }
  let items = [];
  if (d.trigger === "@") items = (await acProjectFiles()).map(a => {
    const name = a.filename || "artifact";
    // Pin the version. Inserting the bare filename is what this menu used to
    // do, and it had a trap in it: the menu lists artifacts from across the
    // *project*, while the resolver only ever looked inside the current
    // session -- so picking a file from another conversation inserted a
    // reference that silently resolved to nothing. Naming the version makes it
    // resolvable (it is materialised at send) and makes it mean one thing
    // forever, rather than whatever a later cell leaves in that filename.
    const version = a.version_id || "";
    const elsewhere = a.root_frame_id && S.currentId && a.root_frame_id !== S.currentId;
    return {
      label: name,
      insert: version ? `${name}#${version}` : name,
      // The provenance matters at pick time: "this comes from another session
      // and will be copied in" is the one thing a user cannot see from a
      // filename, and it is what makes the copy unsurprising afterwards.
      // Two rows may now carry the same label (same name, different artifact),
      // so the short version id is the only thing that tells them apart.
      sub: (elsewhere ? t("ac.fromOtherSession") + " · " : "")
        + (version ? version.slice(2, 8) + " · " : "") + (a.content_type || ""),
    };
  });
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
  // Assigning `value` fires no `input` event, so the chip row has to be told.
  // Without this the one path that always inserts a *correct* token was the
  // one path that never drew a chip for it.
  acClose(); grow(); renderComposerRefChips(); c.focus();
}
// Unresolved @references, shown inline above the composer.
function renderAttachmentProblems(problems) {
  // The server emits `attachment_problems` to tell the user which pinned
  // figures were left out of a turn, and nothing here listened for it. The
  // model is told separately, in a system note, so the assistant usually
  // mentions it — but only usually, and never with the reason, the limit, or
  // what to do instead. A pin the user placed and the model never received is
  // the kind of gap that reads as "the model is broken".
  //
  // Not reusable as `renderRefProblems`: these carry {name, reason, limit,
  // bytes}, not {ref, code, message}. The server sends facts here and the
  // wording lives in the client, which is the opposite of the ref-problem
  // card and deliberate — these reasons are a closed set the client can
  // translate, where a ref problem's message names arbitrary files.
  if (!Array.isArray(problems) || !problems.length) return;
  // The refusals that carry no number, so they need no format arguments. They
  // exist because a pin can fail for reasons that are not a budget: the pinned
  // figure was re-plotted over, deleted, or is not actually an image. Falling
  // through to the bare reason code printed `version_changed` at the user,
  // which is a log line rather than a sentence.
  const ATTACH_REASONS = {
    version_changed: "attach.versionChanged",
    not_found: "attach.notFound",
    unsupported_type: "attach.unsupported",
    decode_failed: "attach.decodeFailed",
  };
  const messages = $("#messages"); if (!messages) return;
  const card = el("div", "ref-problems");
  card.appendChild(el("div", "ref-problems-head", t("attach.problemsTitle", problems.length)));
  problems.slice(0, 8).forEach(p => {
    const row = el("div", "ref-problem");
    row.appendChild(el("code", "ref-problem-ref", publicText((p && p.name) || "", 80)));
    const reason = String((p && p.reason) || "");
    const limit = Number(p && p.limit) || 0;
    const detail = reason === "too_large"
      ? t("attach.tooLarge", bytes(Number(p.bytes) || 0), bytes(limit))
      : reason === "budget_exhausted"
        ? t("attach.budget", bytes(limit))
        : reason === "too_many"
          ? t("attach.tooMany", limit)
          : ATTACH_REASONS[reason] ? t(ATTACH_REASONS[reason]) : reason;
    row.appendChild(el("span", "ref-problem-msg", detail));
    card.appendChild(row);
  });
  messages.appendChild(card);
  down();
}
function renderRefProblems(problems) {
  if (!Array.isArray(problems) || !problems.length) return;
  const messages = $("#messages"); if (!messages) return;
  const card = el("div", "ref-problems");
  card.appendChild(el("div", "ref-problems-head", t("refs.problemsTitle", problems.length)));
  problems.slice(0, 8).forEach(p => {
    const row = el("div", "ref-problem");
    row.appendChild(el("code", "ref-problem-ref", "@" + String((p && p.ref) || "")));
    // The server's message, not a code lookup: it already names the file and
    // says what to do, and re-deriving it here is how the two drift apart.
    row.appendChild(el("span", "ref-problem-msg", String((p && p.message) || "")));
    card.appendChild(row);
  });
  messages.appendChild(card);
  down();
}
// Read-only retrieval provenance. Deliberately dumb: every value here has
// already been through the server's allowlist, length cap and redaction, and
// re-deriving or re-formatting any of it in the client is how the two ends
// start disagreeing about what was sent.
const RETRIEVAL_FIELD_ORDER = [
  "database", "source", "retrieved_at", "request_url", "query",
  "normalization_version", "response_sha256", "record_count",
];
function retrievalSourcePanel(src) {
  const box = el("div", "ver-src");
  box.appendChild(el("div", "ver-src-head", t("versions.retrievalSource")));
  RETRIEVAL_FIELD_ORDER.forEach(field => {
    if (src[field] === undefined || src[field] === null || src[field] === "") return;
    const row = el("div", "ver-src-row");
    row.appendChild(el("span", "ver-src-k", field));
    row.appendChild(el("span", "ver-src-v", String(src[field])));
    box.appendChild(row);
  });
  // Both notes matter: a clipped value shown plain reads as the whole value,
  // and fields withheld without a count read as fields that never existed.
  if (Array.isArray(src.truncated_fields) && src.truncated_fields.length) {
    box.appendChild(el("div", "ver-src-note", t("versions.retrievalTruncated", src.truncated_fields.join(", "))));
  }
  if (src.undisclosed_field_count) {
    box.appendChild(el("div", "ver-src-note", t("versions.retrievalWithheld", src.undisclosed_field_count)));
  }
  return box;
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
  applyTheme(THEME, { instant: true });  // re-sync body class + theme icons after paintIcons
  document.querySelectorAll(".lang-btn").forEach(b => b.onclick = () => setLang(b.dataset.lang));
  applyLayout(localStorage.getItem("os-layout") || "comfortable");
  restoreColWidths(); initColResizers();
  connectWS(); await loadModels(); await refreshEnvironmentStatus(); refreshKeyBanner();
  document.querySelectorAll("[data-open-environment-readiness]").forEach(button => {
    button.onclick = () => openCust("compute");
  });
  $("#dash-new-project").onclick = () => openProjectModal();
  $("#dash-import-session").onclick = chooseSessionPackage;
  $("#session-package-input").onchange = async (event) => {
    const input = event.currentTarget, file = input.files && input.files[0];
    input.value = "";
    await importSessionPackage(file);
  };
  $("#pm-delete").onclick = async () => {
    const id = S.editingProject;
    if (!id || !confirm(t("proj.delete.confirm"))) return;
    await deleteProject(id);
  };
  $("#back-home").onclick = showDashboard;
  $("#search-btn").onclick = openPalette;
  $("#new-session").onclick = newSession;
  $("#tab-new").onclick = newSession;
  $("#tab-close").onclick = (e) => { e.stopPropagation(); showDashboard(); };
  $("#sidebar-collapse").onclick = () => setSidebar(true);
  $("#sidebar-reopen").onclick = () => setSidebar(false);
  $("#mic-btn").onclick = micDictate;
  const themeClick = () => cycleTheme();
  const dt = $("#dash-theme"); if (dt) dt.onclick = themeClick;
  const wt = $("#ws-theme"); if (wt) wt.onclick = themeClick;
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
    // Modal Escape / Tab trap first (so it wins over other shortcuts)
    if (e.key === "Escape" || e.key === "Tab") trapModalKeydown(e);
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) { if (PAL.open) { e.preventDefault(); closePalette(); return; } const modalOpen = ["#cust", "#modal", "#proj-modal"].some(s => { const m = $(s); return m && !m.classList.contains("hidden"); }); if (modalOpen) return; e.preventDefault(); openPalette(); }
    // ⌘/Ctrl+B toggles the sidebar — a reliable escape hatch so a collapsed
    // sidebar can always be brought back even if the expand icon is missed.
    if ((e.metaKey || e.ctrlKey) && (e.key === "b" || e.key === "B")) { e.preventDefault(); setSidebar(!document.body.classList.contains("sidebar-collapsed")); }
    // ⌘/Ctrl+Shift+L cycles light/dark (common editor convention)
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && (e.key === "l" || e.key === "L")) { e.preventDefault(); cycleTheme(); }
  });
  // Composer "Notebook" tray opens the live notebook panel
  const nbTray = $(".nb-tray");
  if (nbTray) nbTray.onclick = () => { if (S.dock.open && S.activeTab === "notebook") dockClose(); else setActiveTab("notebook"); };
  $("#jump-pill").onclick = () => down(true);
  $("#messages").addEventListener("scroll", updateJumpPill);
  $("#cancel-btn").onclick = cancelTurn;
  $("#settings-gear").onclick = openCust;
  $("#cust-close").onclick = () => closeModalEl($("#cust"));
  $("#cust").onclick = (e) => { if (e.target.id === "cust") closeModalEl($("#cust")); };
  document.querySelectorAll(".cust-tab").forEach(t => t.onclick = () => custTab(t.dataset.tab));
  $("#modal-close").onclick = () => closeModalEl($("#modal"));
  $("#modal").onclick = (e) => { if (e.target.id === "modal") closeModalEl($("#modal")); };
  $("#attach-btn").onclick = (e) => addToMessageMenu(e.currentTarget);
  $("#session-options-btn").onclick = (e) => sessionOptionsMenu(e.currentTarget);
  $("#file-input").onchange = (e) => uploadFiles(e.target.files);
  $("#plan-toggle").onclick = () => { S.planMode = !S.planMode; if (S.planMode) { S.exploreMode = false; $("#explore-toggle").classList.remove("on"); } $("#plan-toggle").classList.toggle("on", S.planMode); hint(S.planMode ? t("plan.toggle.on") : ""); };
  $("#explore-toggle").onclick = () => { S.exploreMode = !S.exploreMode; if (S.exploreMode) { S.planMode = false; $("#plan-toggle").classList.remove("on"); } $("#explore-toggle").classList.toggle("on", S.exploreMode); hint(S.exploreMode ? t("explore.toggle.on") : ""); };
  $("#note-save").onclick = addNote;
  $("#proj-modal-close").onclick = $("#pm-cancel").onclick = closeProjectModal;
  $("#proj-modal").onclick = (e) => { if (e.target.id === "proj-modal") closeProjectModal(); };
  $("#pm-create").onclick = submitProjectModal;
  const c = $("#composer");
  c.addEventListener("input", () => { grow(); acUpdate(); renderComposerRefChips(); });
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
  c.addEventListener("blur", () => setTimeout(acClose, 120));  // paste images/files directly into the composer
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

/* ---- Team mode (docs/team-server-plan.md M1-9) -------------------------
 * Self-contained: with team mode off and no data roots, every element
 * stays hidden and nothing below changes the single-user UI. */
(function teamBootstrap() {
  "use strict";
  function el(id) { return document.getElementById(id); }

  // Identity: redirect to /login when the session died; show the user chip
  // and a sign-out action when team mode is on.
  fetch(API + "/auth/me")
    .then(function (r) {
      if (r.status === 401) { location.replace("/login"); return null; }
      return r.ok ? r.json() : null;
    })
    .then(function (me) {
      if (!me || me.team_mode !== true || !me.user) return;
      var chip = el("team-user");
      if (chip) {
        chip.textContent = me.user.username + (me.user.role === "admin" ? " (admin)" : "");
        chip.classList.remove("hidden");
        chip.onclick = function () {
          if (!confirm("Sign out?")) return;
          fetch(API + "/auth/logout", { method: "POST" })
            .then(function () { location.replace("/login"); })
            .catch(function () { location.replace("/login"); });
        };
      }
    })
    .catch(function () {});

  // The team file area: probe once; the buttons appear only when roots exist.
  var tfState = { path: "" };
  function probe() {
    fetch(API + "/files")
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d || !d.roots || !d.roots.length) return;
        ["team-files-btn", "team-files-dash"].forEach(function (id) {
          var b = el(id);
          if (b) { b.classList.remove("hidden"); b.onclick = function () { openPanel(); }; }
        });
      })
      .catch(function () {});
  }
  function openPanel() {
    el("team-files-modal").classList.remove("hidden");
    load(tfState.path);
  }
  function fmtSize(n) {
    if (n >= 1073741824) return (n / 1073741824).toFixed(1) + " GB";
    if (n >= 1048576) return (n / 1048576).toFixed(1) + " MB";
    if (n >= 1024) return (n / 1024).toFixed(1) + " KB";
    return n + " B";
  }
  function load(path) {
    tfState.path = path || "";
    var url = API + "/files" + (tfState.path ? "?path=" + encodeURIComponent(tfState.path) : "");
    fetch(url)
      .then(function (r) { return r.json().then(function (b) { return { ok: r.ok, body: b }; }); })
      .then(function (res) { render(res); })
      .catch(function () {});
  }
  function render(res) {
    var crumbs = el("team-files-crumbs");
    var list = el("team-files-list");
    if (!crumbs || !list) return;
    crumbs.textContent = "";
    list.textContent = "";
    if (!res.ok) {
      list.textContent = (res.body && res.body.error) || "unavailable";
      return;
    }
    var home = document.createElement("a");
    home.href = "#"; home.textContent = "roots";
    home.onclick = function (e) { e.preventDefault(); load(""); };
    crumbs.appendChild(home);
    if (tfState.path) {
      crumbs.appendChild(document.createTextNode("  ›  " + tfState.path));
    }
    var upBtn = el("team-files-upload");
    if (upBtn) upBtn.style.display = tfState.path ? "" : "none";
    if (res.body.roots) {
      res.body.roots.forEach(function (root) {
        var row = document.createElement("div");
        row.className = "team-files-row";
        var a = document.createElement("a");
        a.href = "#"; a.textContent = "📁 " + root.path;
        a.onclick = function (e) { e.preventDefault(); load(root.path); };
        row.appendChild(a);
        list.appendChild(row);
      });
      return;
    }
    (res.body.entries || []).forEach(function (entry) {
      var row = document.createElement("div");
      row.className = "team-files-row";
      var full = res.body.path + "/" + entry.name;
      if (entry.dir) {
        var a = document.createElement("a");
        a.href = "#"; a.textContent = "📁 " + entry.name;
        a.onclick = function (e) { e.preventDefault(); load(full); };
        row.appendChild(a);
      } else {
        var link = document.createElement("a");
        link.href = API + "/files/download?path=" + encodeURIComponent(full);
        link.textContent = "📄 " + entry.name;
        row.appendChild(link);
        var size = document.createElement("span");
        size.className = "team-files-size";
        size.textContent = fmtSize(entry.size);
        row.appendChild(size);
      }
      list.appendChild(row);
    });
    if (!(res.body.entries || []).length) {
      var empty = document.createElement("div");
      empty.className = "team-files-row";
      empty.textContent = "(empty)";
      list.appendChild(empty);
    }
  }
  var closeBtn = el("team-files-close");
  if (closeBtn) closeBtn.onclick = function () { el("team-files-modal").classList.add("hidden"); };
  var uploadBtn = el("team-files-upload");
  var uploadInput = el("team-files-input");
  if (uploadBtn && uploadInput) {
    uploadBtn.onclick = function () { if (tfState.path) uploadInput.click(); };
    uploadInput.onchange = function () {
      var file = uploadInput.files && uploadInput.files[0];
      uploadInput.value = "";
      if (!file || !tfState.path) return;
      var url = API + "/files/upload?dir=" + encodeURIComponent(tfState.path) +
        "&name=" + encodeURIComponent(file.name) + "&overwrite=1";
      fetch(url, { method: "POST", body: file })
        .then(function (r) {
          if (!r.ok) return r.json().then(function (b) { alert((b && b.error) || ("upload failed (" + r.status + ")")); });
          load(tfState.path);
        })
        .catch(function () { alert("upload failed"); });
    };
  }
  probe();
})();

/* ---- Team governance (M2-7): guest redirect + minimal admin panel ------ */
(function teamGovernance() {
  "use strict";
  function el(id) { return document.getElementById(id); }

  fetch(API + "/auth/me")
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) {
      if (!me || me.team_mode !== true || !me.user) return;
      if (me.user.role === "guest") {
        // A guest's whole surface is the replay viewer (D3).
        if (location.pathname === "/") location.replace("/replay");
        return;
      }
      if (me.user.role === "admin" || me.user.kind === "service") {
        var btn = el("team-admin");
        if (btn) { btn.classList.remove("hidden"); btn.onclick = openAdmin; }
      }
    })
    .catch(function () {});

  function openAdmin() {
    el("team-admin-modal").classList.remove("hidden");
    loadAdmin();
  }
  var closeBtn = el("team-admin-close");
  if (closeBtn) closeBtn.onclick = function () { el("team-admin-modal").classList.add("hidden"); };
  var refreshBtn = el("team-admin-refresh");
  if (refreshBtn) refreshBtn.onclick = function () { loadAdmin(); };

  function section(parent, title) {
    var h = document.createElement("h3");
    h.className = "team-admin-h";
    h.textContent = title;
    parent.appendChild(h);
    var box = document.createElement("div");
    box.className = "team-admin-sec";
    parent.appendChild(box);
    return box;
  }
  function table(box, headers, rows) {
    var t = document.createElement("table");
    t.className = "team-admin-table";
    var tr = document.createElement("tr");
    headers.forEach(function (h) {
      var th = document.createElement("th"); th.textContent = h; tr.appendChild(th);
    });
    t.appendChild(tr);
    rows.forEach(function (cells) {
      var r = document.createElement("tr");
      cells.forEach(function (c) {
        var td = document.createElement("td"); td.textContent = c == null ? "" : String(c); r.appendChild(td);
      });
      t.appendChild(r);
    });
    box.appendChild(t);
    if (!rows.length) {
      var d = document.createElement("div"); d.className = "team-admin-empty"; d.textContent = "(none)"; box.appendChild(d);
    }
  }
  function jget(path) {
    return fetch(API + path).then(function (r) { return r.ok ? r.json() : null; });
  }

  function loadAdmin() {
    var body = el("team-admin-body");
    body.textContent = "loading…";
    Promise.all([
      jget("/team/users"), jget("/team/usage"), jget("/team/audit?limit=50"),
      jget("/team/invites"), jget("/team/quotas"),
    ]).then(function (res) {
      var users = (res[0] || {}).users || [];
      var usage = (res[1] || {}).usage || [];
      var audit = (res[2] || {}).audit || [];
      var invites = (res[3] || {}).invites || [];
      var quotas = (res[4] || {}).quotas || [];
      body.textContent = "";
      var idName = {};
      users.forEach(function (u) { idName[u.id] = u.username; });
      table(section(body, "Users"), ["user", "role", "state", "id"],
        users.map(function (u) { return [u.username, u.role, u.disabled ? "disabled" : "active", u.id]; }));
      table(section(body, "Usage"), ["user", "project", "kind", "total", "events"],
        usage.map(function (r) { return [idName[r.user_id] || r.user_id, r.project_id, r.kind, Math.round(r.total * 100) / 100, r.events]; }));
      table(section(body, "Quotas"), ["scope", "scope id", "kind", "limit", "window"],
        quotas.map(function (r) { return [r.scope, r.scope_id, r.kind, r.limit_amount, r.window]; }));
      table(section(body, "Invites"), ["prefix", "project", "by", "state"],
        invites.map(function (r) { return [r.token_prefix, r.project_id, r.created_by, r.live ? "live" : (r.used_at ? "used/revoked" : "expired")]; }));
      table(section(body, "Audit (latest 50)"), ["when", "actor", "action", "target"],
        audit.map(function (r) { return [new Date(r.ts).toLocaleString(), r.actor, r.action, r.target || r.user_id || ""]; }));
    }).catch(function () { el("team-admin-body").textContent = "failed to load"; });
  }
})();
