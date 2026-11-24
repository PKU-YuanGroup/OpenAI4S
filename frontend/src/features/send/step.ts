/**
 * Semantic activity steps. Port of app.js:6043-6458.
 *
 * `searchResultHttpUrl` rebuilds the scheme from a literal so untrusted
 * result data can only reach the URL suffix. `_ocHighlight` is not ported:
 * code blocks use F-08 `mdHighlight` (documented F-08 visible change).
 */

import { t } from "../../i18n/runtime";
import { stepEls, stream as liveStream } from "../../stores/stream";
import { bytes, looksBinary } from "../artifacts/api";
import { artUrl } from "../artifacts/cache";
import { dockOpen, openViewer } from "../artifacts/ui";
import type { ArtifactRow } from "../artifacts/types";
import { mdHighlight } from "../md/highlight";
import { renderMd } from "../md/render";
import { declinedMeta, declinedReason, declinedStep } from "../messages/declined";
import { el } from "../messages/dom";
import { down } from "../messages/scroll";
import { adoptIntoLiveStream, ensure, placeInLiveStream, type LiveStream } from "../messages/stream";
import { shortRuntime } from "../notebook/kernel";
import { publicText } from "../scrub/scrub";
import { apiErrorText } from "../sessions/api";
import { hint } from "../sessions/chrome";
import { publicList } from "../timeline/sanitize";
import { appendSemanticSkillSearch } from "../judgment/chips";
import { sendCopy } from "./copy";
import { icon, iconEl } from "./icon";
import {
  blockedBody,
  blockedView,
  loadView,
  marksNetworkLimit,
  networkNoticeBody,
  noticeView,
  stepNetworkLabel,
  stepNetworkMeta,
} from "./skill-network";
import { reviewFailureText } from "./review-copy";
import { stepSummaryFromParts, stepSummaryText, stepTitleText } from "./step-title";

export type Step = {
  step_id?: string;
  kind?: string;
  title?: string;
  input?: Record<string, unknown>;
  output?: Record<string, unknown>;
  status?: string;
  summary?: string;
  created_at?: unknown;
  [key: string]: unknown;
};

export type StepHandle = {
  card: HTMLElement;
  body: HTMLElement;
  meta: HTMLElement;
  ic: HTMLElement;
  step: Step;
  /** The body does not show `step` yet: built when the card is next opened. */
  bodyStale?: boolean;
};

/** `lazyBody`: a collapsed card (its body is `display: none`) builds its body on first expand. */
export type StepPaint = { lazyBody?: boolean };

const STEP_ICON: Record<string, string> = {
  search: "search",
  fetch: "globe",
  plan: "list-check",
  env: "package",
  skill: "book",
  bash: "terminal",
  edit: "pencil",
  write: "file-text",
  read: "file-text",
  files: "files",
  artifact: "download",
  delegate: "users",
  review: "eye-context",
  mcp: "link",
  fold: "box",
  code: "terminal",
  network: "lock",
};

export function stepIcon(kind: string | undefined): string {
  return (kind && STEP_ICON[kind]) || "check";
}

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function openArt(meta: {
  artifact_id?: unknown;
  version_id?: unknown;
  filename?: unknown;
  content_type?: unknown;
  size_bytes?: unknown;
}): void {
  if (!meta || !meta.artifact_id) return;
  dockOpen();
  // ART-4: a step card shows the version that step produced; opening it must
  // show that version too, not whatever a later cell overwrote it with.
  const version = typeof meta.version_id === "string" && meta.version_id ? meta.version_id : "";
  // openViewer may resolve a version first; a failure there belongs to this
  // click, not to an unhandled rejection.
  void Promise.resolve(
    openViewer({
      id: String(meta.artifact_id),
      artifact_id: String(meta.artifact_id),
      filename: meta.filename != null ? String(meta.filename) : undefined,
      content_type: meta.content_type != null ? String(meta.content_type) : undefined,
      size_bytes: typeof meta.size_bytes === "number" ? meta.size_bytes : undefined,
      ...(version ? { version_id: version, _exactVersion: true } : {}),
    } as ArtifactRow),
  ).catch((error: unknown) => hint(t("toast.failed", apiErrorText(error)), true));
}

export function binElide(len: number): HTMLElement {
  const d = el("div", "bin-elide");
  d.appendChild(iconEl("file", 13));
  d.appendChild(el("span", null, t("output.binaryElided", bytes(len || 0))));
  return d;
}

export function clipPre(text: unknown, cls?: string): HTMLElement {
  const s = text == null ? "" : String(text);
  if (looksBinary(s)) return binElide(s.length);
  const p = el("pre", "s-pre" + (cls ? " " + cls : ""));
  p.textContent = s.slice(0, 14000);
  return p;
}

function diffView(oldS: string, newS: string): HTMLElement {
  const box = el("div", "s-diff");
  const add = (txt: string, cls: string): void => {
    if (!txt) return;
    const pre = el("pre", "s-pre " + cls);
    txt.split("\n").forEach((line) =>
      pre.appendChild(el("div", null, (cls === "d-del" ? "- " : "+ ") + line)),
    );
    box.appendChild(pre);
  };
  add(oldS, "d-del");
  add(newS, "d-add");
  return box;
}

const LANG_EXT: Record<string, string> = {
  py: "python",
  pyw: "python",
  r: "r",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  js: "javascript",
  mjs: "javascript",
  ts: "typescript",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  md: "markdown",
  txt: "text",
  csv: "csv",
  tsv: "tsv",
  tex: "latex",
  sql: "sql",
};

export function langOf(path: string): string {
  const m = (path || "").match(/\.([A-Za-z0-9]+)$/);
  if (!m) return "text";
  const ext = (m[1] || "").toLowerCase();
  return LANG_EXT[ext] || ext;
}

export function baseName(path: string): string {
  return (path || "").replace(/\\/g, "/").split("/").filter(Boolean).pop() || "";
}

export function codeBlock(
  source: string,
  opts: { lang?: string; langLabel?: string; term?: boolean; status?: string; env?: string } = {},
): HTMLElement {
  const lang = opts.lang || "python";
  const wrap = el("div", "os-code" + (opts.term ? " term" : ""));
  const head = el("div", "oc-head");
  const lg = el("span", "oc-lang");
  if (opts.term) lg.appendChild(iconEl("terminal", 12));
  lg.appendChild(el("span", null, opts.langLabel || lang));
  head.appendChild(lg);
  const right = el("div", "oc-right");
  if (opts.status) right.appendChild(el("span", "nbc-status " + opts.status, opts.status));
  if (opts.env) {
    const ev = el("span", "oc-env");
    ev.appendChild(el("span", "oc-env-k", "env"));
    ev.appendChild(el("span", "oc-env-v", opts.env));
    right.appendChild(ev);
  }
  if (right.children.length) head.appendChild(right);
  wrap.appendChild(head);
  const pre = el("pre", "oc-src");
  const code = el("code");
  code.innerHTML = mdHighlight(source, lang);
  pre.appendChild(code);
  wrap.appendChild(pre);
  return wrap;
}

export function outputBlock(
  box: HTMLElement,
  text: unknown,
  opts: { err?: boolean; mode?: string } = {},
): void {
  const raw = text == null ? "" : String(text);
  if (looksBinary(raw)) {
    box.appendChild(binElide(raw.length));
    return;
  }
  const out = el("pre", "oc-out" + (opts.err ? " err" : ""));
  out.textContent = raw.slice(0, 14000);
  if (opts.mode === "reveal") {
    const tgl = el("button", "oc-out-tgl");
    const label = el("span", null, t("step.artifact.showOutput"));
    tgl.appendChild(label);
    tgl.appendChild(iconEl("chevron-down", 13));
    out.style.display = "none";
    tgl.onclick = () => {
      const show = out.style.display === "none";
      out.style.display = show ? "block" : "none";
      tgl.classList.toggle("open", show);
      label.textContent = show ? t("step.artifact.hideOutput") : t("step.artifact.showOutput");
    };
    box.appendChild(tgl);
  }
  box.appendChild(out);
}

/**
 * Rebuild the scheme from a literal so untrusted result data can only reach
 * the URL suffix. Preserves mixed-case HTTP(S) inputs without allowing
 * javascript:, data:, or protocol-relative URLs to control the href scheme.
 */
export function searchResultHttpUrl(value: unknown): string {
  const raw = typeof value === "string" ? value.trim() : "";
  const lower = raw.toLowerCase();
  if (lower.startsWith("https://")) return "https://" + raw.slice(8);
  if (lower.startsWith("http://")) return "http://" + raw.slice(7);
  return "";
}

function delegateTaskChip(view: Record<string, unknown>): HTMLElement {
  const ts = view.task_status;
  let cls = "neutral";
  let key: string | null = null;
  if (ts === "completed") {
    cls = "completed";
    key = "step.delegate.status.completed";
  } else if (ts === "partial") {
    cls = "warning";
    key = "step.delegate.status.partial";
  } else if (ts === "blocked") {
    cls = "warning";
    key = "step.delegate.status.blocked";
  } else if (ts === "failed") {
    cls = "failed";
    key = "step.delegate.status.failed";
  } else if (["stopped", "cancelled"].includes(String(view.stop_reason || ""))) {
    cls = "warning";
    key = "step.delegate.status.stopped";
  } else if (["pending", "running"].includes(String(view.status || ""))) {
    key = "step.delegate.status.pending";
  }
  return el(
    "span",
    "dlg-chip " + cls,
    key ? t(key) : publicText(ts || view.status || "?", 32),
  );
}

function delegateResultRow(view: Record<string, unknown>, compact: boolean): HTMLElement {
  const row = el("div", "dlg-child" + (compact ? " compact" : ""));
  const head = el("div", "dlg-head");
  head.appendChild(delegateTaskChip(view));
  if (view.name || view.child_id) {
    head.appendChild(el("span", "dlg-name", publicText(view.name || view.child_id, 120)));
  }
  if (view.turns != null && view.max_turns) {
    head.appendChild(el("span", "dlg-pill", t("step.delegate.turns", view.turns, view.max_turns)));
  }
  const environment = rec(view.environment);
  const envName = environment.env_name || environment.python;
  if (envName) head.appendChild(el("span", "dlg-pill", publicText(envName, 80)));
  if (view.frame_id) {
    const ref = el("span", "dlg-pill dlg-frame-ref", shortRuntime(view.frame_id));
    ref.title = publicText(view.frame_id, 96);
    head.appendChild(ref);
  }
  row.appendChild(head);
  if (view.summary) row.appendChild(el("div", "dlg-summary", publicText(view.summary, 600)));
  if (view.error) row.appendChild(el("div", "dlg-error", publicText(view.error, 400)));
  if (Array.isArray(view.artifacts) && view.artifacts.length) {
    row.appendChild(
      el(
        "div",
        "dlg-meta",
        t("step.delegate.artifacts") + ": " + publicList(view.artifacts, 20, 120).join(", "),
      ),
    );
  }
  if (Array.isArray(view.missing_artifacts) && view.missing_artifacts.length) {
    row.appendChild(
      el(
        "div",
        "dlg-error",
        t("step.delegate.missingArtifacts") +
          ": " +
          publicList(view.missing_artifacts, 10, 120).join(", "),
      ),
    );
  }
  if (Array.isArray(view.limitations) && view.limitations.length) {
    const lim = el("div", "dlg-limits");
    lim.appendChild(el("div", "dlg-meta", t("step.delegate.limitations") + ":"));
    publicList(view.limitations, 8, 300).forEach((item) =>
      lim.appendChild(el("div", "dlg-limit", "· " + item)),
    );
    row.appendChild(lim);
  }
  return row;
}

function delegateStepBody(_inp: Record<string, unknown>, out: Record<string, unknown>): HTMLElement {
  const wrap = el("div", "dlg-card");
  if (Array.isArray(out.children)) {
    wrap.appendChild(el("div", "dlg-meta", t("step.delegate.children", out.children.length)));
    out.children.forEach((child) =>
      wrap.appendChild(delegateResultRow(rec(child), true)),
    );
  } else {
    wrap.appendChild(delegateResultRow(out, false));
  }
  const raw =
    typeof out.raw === "string" && out.raw ? out.raw : JSON.stringify(out, null, 2);
  const details = el("div", "s-out");
  const tgl = el("button", "s-out-tgl", t("step.delegate.showDetails"));
  const json = el("div", "s-json");
  json.textContent = raw;
  json.style.display = "none";
  tgl.onclick = () => {
    const show = json.style.display === "none";
    json.style.display = show ? "block" : "none";
    tgl.textContent = show
      ? t("step.delegate.hideDetails")
      : t("step.delegate.showDetails");
  };
  details.appendChild(tgl);
  details.appendChild(json);
  wrap.appendChild(details);
  return wrap;
}

export function stepBody(step: Step): HTMLElement {
  const k = step.kind;
  const inp = rec(step.input);
  const out = rec(step.output);
  const box = el("div", "s-inner");
  if (
    k === "delegate" &&
    out &&
    ("task_status" in out || Array.isArray(out.children))
  ) {
    if (inp.request) box.appendChild(clipPre(inp.request, "s-cmd"));
    box.appendChild(delegateStepBody(inp, out));
    return box;
  }
  const blocked = blockedView(step);
  if (blocked) {
    if (inp.command) {
      box.appendChild(codeBlock(String(inp.command), { term: true, lang: "bash", langLabel: "shell" }));
    } else if (inp.query) box.appendChild(el("div", "s-q", "“" + String(inp.query) + "”"));
    else if (inp.url) box.appendChild(el("div", "s-q", String(inp.url)));
    box.appendChild(blockedBody(blocked, out.error != null ? String(out.error) : ""));
    return box;
  }
  // Refused, not failed: what was asked for, and the reason in plain words
  // rather than the server's "Permission denied: …" in an error block.
  const declined = step.status === "error" ? declinedStep(out) : null;
  if (declined) {
    if (inp.command) {
      box.appendChild(codeBlock(String(inp.command), { term: true, lang: "bash", langLabel: "shell" }));
    } else if (inp.query) box.appendChild(el("div", "s-q", "“" + String(inp.query) + "”"));
    else if (inp.url) box.appendChild(el("div", "s-q", String(inp.url)));
    box.appendChild(el("div", "s-note", declinedMeta(declined)));
    const reason = declinedReason(declined);
    if (reason) box.appendChild(el("div", "s-note", reason));
    return box;
  }
  // M-75: a review that ended without a verdict says why and what to do,
  // in the UI language, above the provider's own words.
  if (k === "review" && out.verdict === "unavailable") {
    box.appendChild(el("div", "review-detail", reviewFailureText(out.reason)));
    if (out.error) box.appendChild(clipPre(out.error, "d-del"));
    return box;
  }
  if (out.error) {
    box.appendChild(clipPre(out.error, "d-del"));
    return box;
  }
  const notice = noticeView(step);
  if (notice) {
    box.appendChild(networkNoticeBody(notice));
    return box;
  }
  if (k === "review") {
    const issues = Array.isArray(out.issues) ? out.issues : [];
    if (out.verdict === "pass") return box;
    issues.forEach((raw) => {
      const issue = rec(raw);
      const row = el("div", "review-issue " + (issue.severity || "medium"));
      const head = el("div", "review-issue-head");
      head.appendChild(el("span", "review-severity", String(issue.severity || "medium")));
      head.appendChild(el("strong", null, String(issue.title || sendCopy("reviewFinding"))));
      row.appendChild(head);
      if (issue.detail) row.appendChild(el("div", "review-detail", String(issue.detail)));
      if (issue.evidence) {
        row.appendChild(el("div", "review-evidence", String(issue.evidence)));
      }
      box.appendChild(row);
    });
    return box;
  }
  if (k === "search") {
    if (inp.query) box.appendChild(el("div", "s-q", "“" + String(inp.query) + "”"));
    const results = Array.isArray(out.results) ? out.results : [];
    results.forEach((raw) => {
      const r = rec(raw);
      const row = el("div", "s-res");
      const safeUrl = searchResultHttpUrl(r.url);
      const a = el(safeUrl ? "a" : "div", "s-res-t") as HTMLAnchorElement;
      a.textContent = String(r.title || r.url || t("step.search.emptyResult"));
      if (safeUrl) {
        a.href = safeUrl;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
      }
      row.appendChild(a);
      if (r.url) row.appendChild(el("div", "s-res-u", String(r.url)));
      if (r.snippet) row.appendChild(el("div", "s-res-s", String(r.snippet)));
      box.appendChild(row);
    });
    if (out.note) box.appendChild(el("div", "s-note", String(out.note)));
    return box;
  }
  if (k === "fetch") {
    if (inp.url) box.appendChild(el("div", "s-q", String(inp.url)));
    if (out.content) box.appendChild(clipPre(out.content));
    return box;
  }
  if (k === "plan") {
    const todos = (Array.isArray(out.todos) ? out.todos : inp.todos) || [];
    const ul = el("div", "s-plan");
    (Array.isArray(todos) ? todos : []).forEach((raw) => {
      const item = rec(raw);
      const st = String(item.status || "pending");
      const row = el("div", "s-todo " + st);
      const b = el("span", "s-check");
      b.innerHTML = icon(
        st === "completed" ? "check" : st === "in_progress" ? "circle-dot" : "circle",
        13,
      );
      row.appendChild(b);
      row.appendChild(el("span", "s-todo-t", String(item.content || item.title || "")));
      ul.appendChild(row);
    });
    box.appendChild(ul);
    return box;
  }
  if (k === "env") {
    const envs = Array.isArray(out.environments) ? out.environments : [];
    envs.forEach((raw) => {
      const e = rec(raw);
      const row = el("div", "s-env");
      row.appendChild(
        el(
          "span",
          "s-env-n",
          String(e.name || "") + " " + String(e.python_version || e.r_version || ""),
        ),
      );
      const miss = Array.isArray(e.missing) ? e.missing : [];
      if (miss.length) {
        row.appendChild(el("span", "s-env-m", t("step.env.missing", miss.join(", "))));
      } else {
        row.appendChild(el("span", "s-env-ok", t("step.env.ready")));
      }
      box.appendChild(row);
    });
    const installed = Array.isArray(out.installed) ? out.installed : [];
    if (installed.length) {
      box.appendChild(el("div", "s-note", t("step.env.installed", installed.join(", "))));
    }
    if (out.note) box.appendChild(el("div", "s-note", String(out.note)));
    const packages = Array.isArray(inp.packages) ? inp.packages : [];
    if (!envs.length && !installed.length && packages.length) {
      box.appendChild(el("div", "s-note", packages.join(", ")));
    }
    return box;
  }
  if (k === "skill") {
    if (appendSemanticSkillSearch(box, out)) return box;
    const limited = loadView(step);
    if (limited) box.appendChild(networkNoticeBody(limited));
    if (out.content) {
      const md = el("div", "md s-skill");
      md.innerHTML = renderMd(String(out.content));
      box.appendChild(md);
    } else if (out.skills) {
      const list = Array.isArray(out.skills) ? out.skills : [];
      box.appendChild(el("div", "s-note", t("step.skill.list", list.join(", "))));
    } else if (inp.query) box.appendChild(el("div", "s-q", "“" + String(inp.query) + "”"));
    else if (inp.name) box.appendChild(el("div", "s-q", String(inp.name)));
    return box;
  }
  if (k === "bash") {
    if (inp.command) {
      box.appendChild(codeBlock(String(inp.command), { term: true, lang: "bash", langLabel: "shell" }));
    }
    const o = (String(out.stdout || "") + (out.stderr ? "\n" + String(out.stderr) : "")).trim();
    if (o) outputBlock(box, o, { err: !!out.stderr && !out.stdout });
    return box;
  }
  if (k === "edit") {
    box.appendChild(diffView(String(inp.old_string || ""), String(inp.new_string || "")));
    return box;
  }
  if (k === "code") {
    const src = String(inp.code || inp.source || inp.content || "");
    if (src) {
      box.appendChild(
        codeBlock(src, {
          lang: "python",
          env: inp.environment != null ? String(inp.environment) : undefined,
        }),
      );
    }
    const o = (
      String(out.stdout || out.result || "") + (out.stderr ? "\n" + String(out.stderr) : "")
    ).trim();
    if (o) outputBlock(box, o, { mode: "reveal", err: !!out.stderr && !out.stdout });
    return box;
  }
  if (k === "write") {
    if (inp.content != null && inp.content !== "") {
      box.appendChild(
        codeBlock(String(inp.content), {
          lang: langOf(String(inp.path || "")),
          langLabel: baseName(String(inp.path || "")) || undefined,
        }),
      );
    }
    return box;
  }
  if (k === "read") {
    if (out.content != null && out.content !== "") {
      box.appendChild(
        codeBlock(String(out.content), {
          lang: langOf(String(inp.path || "")),
          langLabel: baseName(String(inp.path || "")) || undefined,
        }),
      );
    }
    return box;
  }
  if (k === "files") {
    const rows = Array.isArray(out.matches) ? out.matches : [];
    const lines = rows.map((r) => {
      if (typeof r === "string") return r;
      const item = rec(r);
      if (item.file) {
        return String(item.file) + ":" + String(item.line || "") + "  " + String(item.text || "");
      }
      return String(item.name || JSON.stringify(r));
    });
    if (lines.length) box.appendChild(clipPre(lines.join("\n")));
    else if (inp.pattern) box.appendChild(el("div", "s-q", String(inp.pattern)));
    return box;
  }
  if (k === "artifact") {
    const artsRaw = Array.isArray(out.artifacts)
      ? out.artifacts
      : out.filename
        ? [{ filename: out.filename, version_id: out.version_id }]
        : [];
    const arts = artsRaw.map(rec);
    const imgArts = arts.filter((a) => {
      const nm = String(a.filename || "").toLowerCase();
      const ct = String(a.content_type || "");
      return (
        a.artifact_id &&
        (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm))
      );
    });
    // A figure is shown below with its name as the caption; listing it here
    // too put every file name on the card twice (three times with the title).
    const shown = new Set(imgArts.map((a) => a.filename));
    const files = (
      Array.isArray(inp.files) && inp.files.length
        ? inp.files
        : arts.map((a) => a.filename)
    ).filter((fn) => !!fn && !shown.has(fn)) as unknown[];
    const env = inp.environment || "python";
    // M-50: the files by name and size, each opening its viewer. The card
    // used to read as a data dump -- `files ["a", "b"]`, `environment python`
    // and the raw save receipt -- and the receipt named the file's absolute
    // path on this computer.
    const sizeOf = (meta: Record<string, unknown> | undefined): string =>
      meta && typeof meta.size_bytes === "number" ? " · " + bytes(meta.size_bytes) : "";
    if (files.length) {
      const fl = el("div", "s-files");
      files.forEach((fn) => {
        const meta = arts.find((a) => a.filename === fn);
        const line = el("div", "s-frow");
        const nm = el("span", "s-fn" + (meta && meta.artifact_id ? " clk" : ""), String(fn));
        if (meta && meta.artifact_id) {
          nm.title = t("step.artifact.openArtifact");
          nm.onclick = () => openArt(meta);
        }
        line.appendChild(nm);
        const size = sizeOf(meta);
        if (size) line.appendChild(el("span", "s-fsize", size));
        fl.appendChild(line);
      });
      box.appendChild(fl);
    }
    if (arts.length && (arts[0]?.artifact_id || arts[0]?.checksum)) {
      // The receipt for whoever needs it, behind its own toggle; never the
      // local storage path.
      const receipt = arts.map((a) => {
        const { storage_path: _local, ...rest } = a;
        return rest;
      });
      const wrap = el("div", "s-out");
      const tgl = el("button", "s-out-tgl", sendCopy("technicalDetails"));
      const json = el("div", "s-json");
      json.textContent = JSON.stringify({ environment: env, artifacts: receipt }, null, 2);
      json.style.display = "none";
      tgl.onclick = () => {
        const show = json.style.display === "none";
        json.style.display = show ? "block" : "none";
        tgl.textContent = sendCopy(show ? "hideTechnicalDetails" : "technicalDetails");
      };
      wrap.appendChild(tgl);
      wrap.appendChild(json);
      box.appendChild(wrap);
    }
    if (imgArts.length) {
      const figs = el("div", "s-figs");
      imgArts.forEach((a) => {
        const fig = el("figure", "s-fig");
        fig.title = t("step.artifact.openArtifact");
        const im = el("img") as HTMLImageElement;
        // ART-4: the head URL plus a cache-buster served the LATEST bytes, so
        // an old card silently changed once the figure was redrawn.
        im.src = artUrl(
          (typeof a.version_id === "string" && a.version_id
            ? { id: String(a.artifact_id), version_id: a.version_id, _exactVersion: true }
            : { id: String(a.artifact_id) }) as ArtifactRow,
        );
        im.alt = String(a.filename || t("step.fig.altFallback"));
        im.loading = "lazy";
        fig.appendChild(im);
        if (a.filename) fig.appendChild(el("figcaption", "s-fig-cap", String(a.filename)));
        if (typeof a.size_bytes === "number") fig.title += " · " + bytes(a.size_bytes);
        fig.onclick = () => openArt(a);
        figs.appendChild(fig);
      });
      box.appendChild(figs);
    }
    return box;
  }
  if (k === "delegate" || k === "mcp") {
    if (inp.request) box.appendChild(clipPre(inp.request, "s-cmd"));
    if (out.result != null) {
      box.appendChild(
        clipPre(typeof out.result === "string" ? out.result : JSON.stringify(out.result, null, 2)),
      );
    }
    return box;
  }
  // The structured summary (CHAT-6) is the head's, not the body's.
  const { summary_parts: _summaryParts, ...shown } = out;
  const dump = Object.keys(shown).length ? shown : inp;
  box.appendChild(clipPre(JSON.stringify(dump, null, 2)));
  return box;
}

function paintStepBody(handle: StepHandle): void {
  handle.body.innerHTML = "";
  handle.body.appendChild(stepBody(handle.step));
  handle.bodyStale = false;
}

/**
 * The step's one-line summary. A Saving step stored before its summary was
 * (SYNC-14) gets the count the live card showed, from the files it saved.
 */
function stepSummary(step: Step): string {
  if (step.summary != null) return String(step.summary);
  const saved = rec(step.output).artifacts;
  if (step.kind !== "artifact" || !Array.isArray(saved) || !saved.length) return "";
  return saved.length + (saved.length === 1 ? " artifact" : " artifacts");
}

/**
 * The summary in the UI language: the structured parts the host sends beside
 * its English line (CHAT-6), else that line translated by pattern (a record
 * stored before the parts, or a step the gateway wrote itself).
 */
function stepSummaryWords(step: Step): string {
  const structured = stepSummaryFromParts(rec(step.output).summary_parts);
  if (structured) return structured;
  const legacy = stepSummary(step);
  return legacy ? stepSummaryText(legacy) : "";
}

/**
 * SCI-22: a delegation is titled by its task ("Sub-agent · GC content of
 * ATGC…"). Two parallel sub-agents of one kind were both "Delegating to
 * GENERAL", and only expanding them told them apart.
 */
function delegateTitle(step: Step): string {
  const request = rec(step.input).request;
  if (step.kind !== "delegate" || typeof request !== "string" || !request.trim()) return "";
  const flat = request.replace(/\s+/g, " ").trim();
  const end = flat.search(/[.!?。！？](?:\s|$)/);
  return sendCopy("subagentTask", publicText(end > 0 ? flat.slice(0, end + 1) : flat, 80));
}

function stepLabel(step: Step): string {
  return delegateTitle(step) || stepTitleText(String(step.title || step.kind || t("step.card.defaultTitle")));
}

function stepOutcome(step: Step) {
  const status = step.status || "running";
  // Refused by a loaded Skill's network limit: not a crash, and not "0 results".
  const blocked = status !== "running" && !!blockedView(step);
  // Denied by the user, refused by a rule, or cancelled by a Stop while it
  // waited for approval: a refusal, shown neutrally, never as a failure.
  const declined = status === "error" && !blocked ? declinedStep(step.output) : null;
  return { status, blocked, declined };
}

/**
 * The card head's words: its title and status line. The host words both in
 * English and they are translated here (I18N-1), so a language switch has to
 * say them again (`relabelStepCards`); the static repaint cannot reach them.
 */
function paintStepWords(handle: StepHandle): void {
  const { card, meta, step } = handle;
  const { status, declined } = stepOutcome(step);
  meta.textContent =
    status === "running"
      ? step.kind === "review"
        ? sendCopy("reviewing")
        : ""
      : stepNetworkMeta(step) ||
        (declined ? declinedMeta(declined) : "") ||
        stepSummaryWords(step) ||
        (step.output && rec(step.output).error ? t("step.status.failed") : "");
  const label = card.querySelector(".s-lbl");
  // A loaded Skill's network limit: the server words these in English.
  if (label) label.textContent = stepNetworkLabel(step) || stepLabel(step);
}

/** Every step card on screen, in the language just applied. */
export function relabelStepCards(): void {
  const registry = stepEls.value as Record<string, StepHandle> | null;
  if (!registry) return;
  for (const handle of Object.values(registry)) {
    if (!handle.card.isConnected) continue;
    paintStepWords(handle);
    if (handle.step.kind === "delegate") paintChildCount(handle.card);
    // An open body is redrawn now; a closed one when it is next opened.
    if (handle.card.classList.contains("open")) paintStepBody(handle);
    else handle.bodyStale = true;
  }
}

export function applyStepState(handle: StepHandle, paint: StepPaint = {}): void {
  const { card, ic, step } = handle;
  const { status, blocked, declined } = stepOutcome(step);
  // SCI-22: a sub-agent's own failed attempt (a probe of a path that is not
  // there) is amber, not the red of a failed turn: the sub-agent carried on,
  // and its delegation card says how it ended.
  const childSlip = status === "error" && !blocked && !declined && card.classList.contains("step-child");
  card.classList.toggle("running", status === "running");
  card.classList.toggle("err", status === "error" && !blocked && !declined && !childSlip);
  card.classList.toggle("warn", status === "warning" || blocked || childSlip);
  card.classList.toggle("declined", !!declined);
  if (status === "running") {
    ic.innerHTML = icon("loader", 14, "spin");
  } else {
    ic.innerHTML = icon(
      blocked
        ? "lock"
        : declined
          ? declined.kind === "stopped"
            ? "stop"
            : "lock"
          : status === "error"
            ? childSlip
              ? "alert-triangle"
              : "x"
            : status === "warning"
              ? "alert-triangle"
              : stepIcon(step.kind),
      14,
    );
  }
  // I18N-1: the host words titles in English; an update can retitle a card.
  paintStepWords(handle);
  card.classList.toggle("s-net-limited", marksNetworkLimit(step));
  // A Saving step stays collapsed (M-50): its title and "N artifacts" say what
  // happened; the files, sizes and figures are one click away.
  if (step.kind === "plan" && status !== "running") {
    card.classList.add("open");
  }
  if (step.kind === "review") {
    const output = rec(step.output);
    const hasIssues = output.verdict === "issues";
    card.classList.toggle("review-pass", status === "done" && !hasIssues);
    card.classList.toggle("review-issues", status === "done" && hasIssues);
    card.classList.toggle("open", !!hasIssues);
  }
  // A reopened session's history and every live update used to build the
  // full body -- code highlighting, output, search results -- for cards
  // nobody had expanded.
  if (!paint.lazyBody || card.classList.contains("open")) paintStepBody(handle);
  else handle.bodyStale = true;
}

/**
 * The window contract (`buildStepCard`, read by the browser gates) builds the
 * body at once; the history and live paths below pass `lazyBody`.
 */
export function buildStepCard(step: Step, paint: StepPaint = {}): StepHandle {
  const card = el("div", "step step-" + (step.kind || "code"));
  const dlg = rec(rec(step.input).delegation);
  const hasDlg = step.input && rec(step.input).delegation && typeof rec(step.input).delegation === "object";
  if (hasDlg && Object.keys(dlg).length) {
    card.classList.add("step-child");
    card.style.setProperty(
      "--step-child-indent",
      Math.min(Number(dlg.depth) || 1, 4) * 12 + "px",
    );
  }
  const h = el("div", "s-head");
  const ic = el("span", "s-ic");
  h.appendChild(ic);
  if (hasDlg && Object.keys(dlg).length) {
    // Which sub-agent: two of one kind ran side by side under one name.
    const id = dlg.delegation_child_id ? shortRuntime(dlg.delegation_child_id) : "";
    const name = dlg.child_name ? publicText(dlg.child_name, 60) : "";
    h.appendChild(el("span", "s-child-tag", name && id ? name + " · " + id : name || id));
  }
  // `applyStepState` below writes its words (`paintStepWords`).
  h.appendChild(el("span", "s-lbl", ""));
  const meta = el("span", "s-meta", "");
  h.appendChild(meta);
  const chev = el("span", "s-chev");
  chev.innerHTML = icon("chevron-down", 13);
  h.appendChild(chev);
  const body = el("div", "s-body");
  card.appendChild(h);
  card.appendChild(body);
  const handle: StepHandle = { card, body, meta, ic, step };
  h.onclick = () => {
    const open = card.classList.toggle("open");
    if (open && handle.bodyStale) paintStepBody(handle);
  };
  applyStepState(handle, paint);
  return handle;
}

const LAZY_BODY: StepPaint = { lazyBody: true };

/**
 * SCI-22: the delegation card a sub-agent's step belongs under. Its steps
 * (each `Reading x failed …` probe included) used to land in the main
 * conversation between the parent's prose, two sub-agents' interleaved. The
 * server names the card on every relayed step (`input.delegation
 * .parent_step_id`, live and in the stored row), so the stream and a reload
 * nest the same way. Null when that card is not on screen: the step stays flat.
 */
function delegationParent(step: Step): StepHandle | null {
  const parentId = rec(rec(step.input).delegation).parent_step_id;
  if (typeof parentId !== "string" || !parentId || parentId === step.step_id) return null;
  const parent = stepRegistry()[parentId];
  return parent && parent.step.kind === "delegate" ? parent : null;
}

/** Put a sub-agent's step inside its delegation card (shown when the card is open). */
function nestUnderDelegation(parent: StepHandle, card: HTMLElement): void {
  nestInStep(parent, card);
  paintChildCount(parent.card);
}

function nestInStep(parent: StepHandle, card: HTMLElement): void {
  let box = ownChild(parent.card, "s-children");
  if (!box) {
    box = el("div", "s-children");
    parent.card.appendChild(box);
  }
  box.appendChild(card);
}

/**
 * SCI-11: one download left three rows -- the download, its approval ("✓
 * Allowed") and "Saving 1crn.pdb · 1 artifact". The file it saved is shown
 * inside the download's card when that download is the step just before the
 * Saving step and wrote that file; anything else is left as it was.
 */
function downloadThatSaved(step: Step): StepHandle | null {
  if (step.kind !== "artifact") return null;
  const files = rec(step.input).files;
  if (!Array.isArray(files) || !files.length) return null;
  const previous = Object.values(stepRegistry()).pop();
  if (!previous || previous.step.kind !== "fetch") return null;
  const saved = baseName(String(rec(previous.step.input).path || ""));
  return saved && files.map(String).includes(saved) ? previous : null;
}

/**
 * SCI-11: an allowed download's approval card goes inside the card of the
 * download it allowed (same address), so the decision stays one click away
 * and the conversation shows the download once. False when no such card is
 * on screen: the approval stays where it is.
 */
export function foldDownloadDecision(card: HTMLElement, request: { tool?: unknown; input?: unknown }): boolean {
  if (request.tool !== "web_download") return false;
  const url = rec(request.input).url;
  if (typeof url !== "string" || !url) return false;
  const download = Object.values(stepRegistry())
    .reverse()
    .find((handle) => handle.step.kind === "fetch" && rec(handle.step.input).url === url);
  if (!download) return false;
  nestInStep(download, card);
  // The decision came before what the download saved, whichever event this
  // page saw first: a reload paints them in that order too.
  const box = card.parentElement;
  if (box) for (const kid of Array.from(box.children) as HTMLElement[]) if (kid !== card) box.appendChild(kid);
  return true;
}

function ownChild(node: HTMLElement, cls: string): HTMLElement | undefined {
  return (Array.from(node.children) as HTMLElement[]).find((kid) => kid.classList.contains(cls));
}

/** "3 steps inside" on a delegation card's head, beside its chevron. */
function paintChildCount(card: HTMLElement): void {
  const box = ownChild(card, "s-children");
  const head = ownChild(card, "s-head");
  if (!box || !head) return;
  const count = (Array.from(box.children) as HTMLElement[]).filter((kid) => kid.classList.contains("step")).length;
  let badge = ownChild(head, "s-child-count");
  if (!badge) {
    badge = el("span", "s-child-count");
    head.appendChild(badge);
    // The chevron stays last.
    const chev = ownChild(head, "s-chev");
    if (chev) head.appendChild(chev);
  }
  badge.textContent = count === 1 ? sendCopy("subagentStep") : sendCopy("subagentSteps", String(count));
}

function isNested(card: HTMLElement): boolean {
  return !!card.parentElement && card.parentElement.classList.contains("s-children");
}

function stepRegistry(): Record<string, StepHandle> {
  let els = stepEls.value as Record<string, StepHandle> | null;
  if (!els) {
    els = Object.create(null) as Record<string, StepHandle>;
    stepEls.value = els;
  }
  return els;
}

type DemotableStream = LiveStream & { toolCard?: HTMLElement & { _demoted?: boolean } };

/**
 * A Cell's activity card that steps follow becomes their quieter group head.
 * It keeps its title: relabelled "Code", a cell the model had titled, or
 * "Analysis · cell 24", read as a card called "Code".
 */
function demoteToolCard(st: DemotableStream): void {
  if (st.toolCard && !st.toolCard._demoted) {
    st.toolCard.classList.add("has-steps");
    st.toolCard._demoted = true;
  }
}

/**
 * One plan card per turn. Every plan update is a step of its own, and each
 * used to add another complete "Planning · 6 steps" card to the conversation.
 * A later plan step of the same turn repaints the turn's card where it is and
 * answers to its id from then on. Null when `turn` has no plan card yet.
 */
function mergePlanStep(turn: Element | null | undefined, step: Step): StepHandle | null {
  if (!turn || step.kind !== "plan") return null;
  const registry = stepRegistry();
  const prior = Object.values(registry).find(
    (handle) => handle.step.kind === "plan" && handle.card.parentElement === turn,
  );
  if (!prior) return null;
  prior.step = step;
  if (step.step_id) registry[String(step.step_id)] = prior;
  applyStepState(prior, LAZY_BODY);
  return prior;
}

export function addLiveStep(m: Record<string, unknown>): void {
  const existing = stepRegistry()[String(m.step_id || "")];
  if (existing && existing.card && existing.card.isConnected) {
    existing.step.kind = (m.kind as string) || existing.step.kind;
    existing.step.title = (m.title as string) || existing.step.title;
    if (m.input != null) existing.step.input = rec(m.input);
    if (m.status) existing.step.status = String(m.status);
    applyStepState(existing, LAZY_BODY);
    // A reopened running session rendered this stored step from REST before
    // the turn's replay reached its `step` event: it belongs here, between
    // the prose before and after it, as it was shown live. A sub-agent's step
    // stays in its delegation card, which is adopted with it.
    if (!isNested(existing.card) && adoptIntoLiveStream(existing.card)) {
      demoteToolCard(liveStream.value as DemotableStream);
    }
    down();
    return;
  }
  const st = ensure() as DemotableStream | null;
  if (!st) return;
  const step: Step = {
    step_id: m.step_id != null ? String(m.step_id) : undefined,
    kind: m.kind != null ? String(m.kind) : undefined,
    title: m.title != null ? String(m.title) : undefined,
    input: rec(m.input),
    status: m.status != null ? String(m.status) : "running",
  };
  if (mergePlanStep(st.wrap, step)) {
    down();
    return;
  }
  // Read before this step is registered: "the step just before" is not itself.
  const delegation = delegationParent(step);
  const download = delegation ? null : downloadThatSaved(step);
  const handle = buildStepCard(step, LAZY_BODY);
  if (m.step_id) stepRegistry()[String(m.step_id)] = handle;
  if (delegation || download) {
    if (delegation) nestUnderDelegation(delegation, handle.card);
    else if (download) nestInStep(download, handle.card);
    liveStream.value = st;
    down();
    return;
  }
  placeInLiveStream(handle.card, st);
  demoteToolCard(st);
  liveStream.value = st;
  if (m.kind === "review") hint(sendCopy("reviewing"), false, true);
  down();
}

export function updateLiveStep(m: Record<string, unknown>): void {
  const h = stepRegistry()[String(m.step_id || "")];
  if (!h) return;
  h.step.status = m.status != null ? String(m.status) : h.step.status;
  h.step.output = rec(m.output);
  if (m.summary != null) h.step.summary = String(m.summary);
  applyStepState(h, LAZY_BODY);
  if (h.step.kind === "review" && m.status !== "running") hint("");
  down();
}

export function renderStoredStep(s: Step, target?: ParentNode | null): HTMLElement {
  // The history paint names the turn its card joins (`messages/list.ts`).
  const merged = mergePlanStep((target as { turn?: Element | null } | null | undefined)?.turn, s);
  if (merged) return merged.card;
  const delegation = delegationParent(s);
  const download = delegation ? null : downloadThatSaved(s);
  const handle = buildStepCard(s, LAZY_BODY);
  if (s.step_id) stepRegistry()[String(s.step_id)] = handle;
  handle.card.dataset.ts = String(s.created_at || 0);
  // As the stream nested it (SCI-22, SCI-11).
  if (delegation) {
    nestUnderDelegation(delegation, handle.card);
    return handle.card;
  }
  if (download) {
    nestInStep(download, handle.card);
    return handle.card;
  }
  (target || (typeof document !== "undefined" ? document.getElementById("messages") : null))?.appendChild(
    handle.card,
  );
  return handle.card;
}
