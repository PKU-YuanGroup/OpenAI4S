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
import { el } from "../messages/dom";
import { down } from "../messages/scroll";
import { ensure, sealText, type LiveStream } from "../messages/stream";
import { shortRuntime } from "../notebook/kernel";
import { publicText } from "../scrub/scrub";
import { hint } from "../sessions/chrome";
import { publicList } from "../timeline/sanitize";
import { icon, iconEl } from "./icon";

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
};

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
};

export function stepIcon(kind: string | undefined): string {
  return (kind && STEP_ICON[kind]) || "check";
}

function rec(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function openArt(meta: { artifact_id?: unknown; filename?: unknown; content_type?: unknown; size_bytes?: unknown }): void {
  if (!meta || !meta.artifact_id) return;
  dockOpen();
  openViewer({
    id: String(meta.artifact_id),
    artifact_id: String(meta.artifact_id),
    filename: meta.filename != null ? String(meta.filename) : undefined,
    content_type: meta.content_type != null ? String(meta.content_type) : undefined,
    size_bytes: typeof meta.size_bytes === "number" ? meta.size_bytes : undefined,
  } as ArtifactRow);
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
    const label = el("span", null, "Show output");
    tgl.appendChild(label);
    tgl.appendChild(iconEl("chevron-down", 13));
    out.style.display = "none";
    tgl.onclick = () => {
      const show = out.style.display === "none";
      out.style.display = show ? "block" : "none";
      tgl.classList.toggle("open", show);
      label.textContent = show ? "Hide output" : "Show output";
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
  if (out.error) {
    box.appendChild(clipPre(out.error, "d-del"));
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
      head.appendChild(el("strong", null, String(issue.title || "Review finding")));
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
    const files = (
      Array.isArray(inp.files) && inp.files.length
        ? inp.files
        : arts.map((a) => a.filename)
    ).filter(Boolean) as unknown[];
    const env = inp.environment || "python";
    const kvRow = (label: string, text: unknown): HTMLElement => {
      const r = el("div", "s-kv");
      r.appendChild(el("span", "s-k", label));
      r.appendChild(el("div", "s-v", text == null ? "" : String(text)));
      return r;
    };
    const kvNodeRow = (label: string, node: HTMLElement): HTMLElement => {
      const r = el("div", "s-kv");
      r.appendChild(el("span", "s-k", label));
      const v = el("div", "s-v");
      v.appendChild(node);
      r.appendChild(v);
      return r;
    };
    const fl = el("div", "s-files");
    fl.appendChild(el("span", "s-brk", "["));
    files.forEach((fn, i) => {
      const meta = arts.find((a) => a.filename === fn);
      const line = el("div", "s-frow");
      const nm = el(
        "span",
        "s-fn" + (meta && meta.artifact_id ? " clk" : ""),
        '"' + String(fn) + '"',
      );
      if (meta && meta.artifact_id) {
        nm.title = t("step.artifact.openArtifact");
        nm.onclick = () => openArt(meta);
      }
      line.appendChild(nm);
      if (i < files.length - 1) line.appendChild(el("span", "s-comma", ","));
      fl.appendChild(line);
    });
    fl.appendChild(el("span", "s-brk", "]"));
    box.appendChild(kvNodeRow("files", fl));
    box.appendChild(kvRow("environment", env));
    if (arts.length && (arts[0]?.artifact_id || arts[0]?.checksum)) {
      const wrap = el("div", "s-out");
      const tgl = el("button", "s-out-tgl", t("step.artifact.showOutput"));
      const json = el("div", "s-json");
      json.textContent = JSON.stringify({ artifacts: arts }, null, 2);
      json.style.display = "none";
      tgl.onclick = () => {
        const show = json.style.display === "none";
        json.style.display = show ? "block" : "none";
        tgl.textContent = show
          ? t("step.artifact.hideOutput")
          : t("step.artifact.showOutput");
      };
      wrap.appendChild(tgl);
      wrap.appendChild(json);
      box.appendChild(wrap);
    }
    const imgArts = arts.filter((a) => {
      const nm = String(a.filename || "").toLowerCase();
      const ct = String(a.content_type || "");
      return (
        a.artifact_id &&
        (ct.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(nm))
      );
    });
    if (imgArts.length) {
      const figs = el("div", "s-figs");
      imgArts.forEach((a) => {
        const fig = el("figure", "s-fig");
        fig.title = t("step.artifact.openArtifact");
        const im = el("img") as HTMLImageElement;
        im.src = artUrl({ id: String(a.artifact_id) } as ArtifactRow);
        im.alt = String(a.filename || t("step.fig.altFallback"));
        im.loading = "lazy";
        fig.appendChild(im);
        if (a.filename) fig.appendChild(el("figcaption", "s-fig-cap", String(a.filename)));
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
  const dump = out && Object.keys(out).length ? out : inp;
  box.appendChild(clipPre(JSON.stringify(dump, null, 2)));
  return box;
}

export function applyStepState(handle: StepHandle): void {
  const { card, body, meta, ic, step } = handle;
  const status = step.status || "running";
  card.classList.toggle("running", status === "running");
  card.classList.toggle("err", status === "error");
  card.classList.toggle("warn", status === "warning");
  if (status === "running") {
    ic.innerHTML = icon("loader", 14, "spin");
    meta.textContent = step.kind === "review" ? "Reviewing" : "";
  } else {
    ic.innerHTML = icon(
      status === "error" ? "x" : status === "warning" ? "alert-triangle" : stepIcon(step.kind),
      14,
    );
    meta.textContent =
      (step.summary != null ? String(step.summary) : "") ||
      (step.output && rec(step.output).error ? t("step.status.failed") : "");
  }
  body.innerHTML = "";
  body.appendChild(stepBody(step));
  if ((step.kind === "plan" || step.kind === "artifact") && status !== "running") {
    card.classList.add("open");
  }
  if (step.kind === "review") {
    const output = rec(step.output);
    const hasIssues = output.verdict === "issues";
    card.classList.toggle("review-pass", status === "done" && !hasIssues);
    card.classList.toggle("review-issues", status === "done" && hasIssues);
    card.classList.toggle("open", !!hasIssues);
  }
}

export function buildStepCard(step: Step): StepHandle {
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
    h.appendChild(
      el(
        "span",
        "s-child-tag",
        publicText(dlg.child_name || shortRuntime(dlg.delegation_child_id), 60),
      ),
    );
  }
  h.appendChild(el("span", "s-lbl", String(step.title || step.kind || t("step.card.defaultTitle"))));
  const meta = el("span", "s-meta", "");
  h.appendChild(meta);
  const chev = el("span", "s-chev");
  chev.innerHTML = icon("chevron-down", 13);
  h.appendChild(chev);
  const body = el("div", "s-body");
  card.appendChild(h);
  card.appendChild(body);
  h.onclick = () => card.classList.toggle("open");
  const handle: StepHandle = { card, body, meta, ic, step };
  applyStepState(handle);
  return handle;
}

function stepRegistry(): Record<string, StepHandle> {
  let els = stepEls.value as Record<string, StepHandle> | null;
  if (!els) {
    els = Object.create(null) as Record<string, StepHandle>;
    stepEls.value = els;
  }
  return els;
}

export function addLiveStep(m: Record<string, unknown>): void {
  const existing = stepRegistry()[String(m.step_id || "")];
  if (existing && existing.card && existing.card.isConnected) {
    existing.step.kind = (m.kind as string) || existing.step.kind;
    existing.step.title = (m.title as string) || existing.step.title;
    if (m.input != null) existing.step.input = rec(m.input);
    if (m.status) existing.step.status = String(m.status);
    applyStepState(existing);
    down();
    return;
  }
  const st = ensure() as (LiveStream & { toolCard?: HTMLElement & { _demoted?: boolean } }) | null;
  if (!st) return;
  sealText(st);
  const handle = buildStepCard({
    step_id: m.step_id != null ? String(m.step_id) : undefined,
    kind: m.kind != null ? String(m.kind) : undefined,
    title: m.title != null ? String(m.title) : undefined,
    input: rec(m.input),
    status: m.status != null ? String(m.status) : "running",
  });
  if (m.step_id) stepRegistry()[String(m.step_id)] = handle;
  st.wrap.appendChild(handle.card);
  if (st.toolCard && !st.toolCard._demoted) {
    st.toolCard.classList.add("has-steps");
    st.toolCard._demoted = true;
    const lbl = st.toolCard.querySelector(".lbl");
    if (lbl) lbl.textContent = t("step.label.code");
  }
  st.md = el("div", "md");
  st.wrap.appendChild(st.md);
  st.text = "";
  liveStream.value = st;
  if (m.kind === "review") hint("Reviewing", false, true);
  down();
}

export function updateLiveStep(m: Record<string, unknown>): void {
  const h = stepRegistry()[String(m.step_id || "")];
  if (!h) return;
  h.step.status = m.status != null ? String(m.status) : h.step.status;
  h.step.output = rec(m.output);
  if (m.summary != null) h.step.summary = String(m.summary);
  applyStepState(h);
  if (h.step.kind === "review" && m.status !== "running") hint("");
  down();
}

export function renderStoredStep(s: Step, target?: ParentNode | null): HTMLElement {
  const handle = buildStepCard(s);
  if (s.step_id) stepRegistry()[String(s.step_id)] = handle;
  handle.card.dataset.ts = String(s.created_at || 0);
  (target || (typeof document !== "undefined" ? document.getElementById("messages") : null))?.appendChild(
    handle.card,
  );
  return handle.card;
}
