/**
 * Provenance tab. Port of app.js:10631-10833.
 *
 * F-17 Viewer calls `window.renderProvenanceInto` when `provMode` is set.
 * Lineage / env-honesty transforms live in `lineage.ts` (tested as pure data).
 */

import { _envSnapById, dockArtifact } from "../../stores/artifacts";
import { _lineageFor, _lineageReq, cells, lineage } from "../../stores/notebook";
import { currentId } from "../../stores/session";
import { provMode, provSub } from "../../stores/ui";
import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { artifactCacheKey } from "../artifacts/cache";
import { addOpenTab, setActiveTab } from "../artifacts/ui";
import type { ArtifactRow } from "../artifacts/types";
import { renderMd } from "../md/render";
import { iconEl, notebookExportLink } from "../notebook/chrome";
import { cellNode, scrollToCell } from "../notebook/Notebook";
import type { NotebookCell } from "../notebook/types";
import { publicText } from "../scrub/scrub";
import { codeBlock } from "../send/step";
import { ago } from "../sessions/dom";
import { fetchRecentMessages, type ChatMessage } from "../sessions/messages";
import { api, apiErrorText } from "./api";
import {
  captureInRootNotebook,
  emptyLineage,
  envPackageCount,
  envPythonChip,
  envSnapshotHonesty,
  lineageReviewModel,
} from "./lineage";
import type { EnvSnapshot, LineageCapture, LineagePayload } from "./types";

function el(tag: string, cls?: string | null, text?: string | null): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function asArtifact(a: unknown): ArtifactRow | null {
  if (!a || typeof a !== "object") return null;
  return a as ArtifactRow;
}

function rerenderViewer(): void {
  const fn = (globalThis as unknown as { renderViewer?: unknown }).renderViewer;
  if (isReady(fn)) (fn as () => void)();
}

export async function loadLineage(a: ArtifactRow | null | undefined): Promise<LineagePayload> {
  if (!a) return emptyLineage();
  try {
    return (await api(`/artifacts/${a.id}/lineage`)) as LineagePayload;
  } catch {
    return emptyLineage();
  }
}

function provRow(label: string, files: string[]): HTMLElement {
  const d = el("div", "prov-row");
  d.appendChild(el("span", "prov-lbl", label));
  const box = el("div", "prov-files");
  (files || []).forEach((f) => box.appendChild(el("span", "prov-pill", f)));
  d.appendChild(box);
  return d;
}

export function showProvenance(a: unknown): void {
  const art = asArtifact(a);
  if (!art) return;
  dockArtifact.value = art;
  provMode.value = true;
  if (!provSub.value) provSub.value = "code";
  addOpenTab(art);
  setActiveTab(art.id);
  const key = artifactCacheKey(art);
  if (!lineage.value || _lineageFor.value !== key) {
    const request = (_lineageReq.value = (_lineageReq.value || 0) + 1);
    void loadLineage(art).then((l) => {
      const docked = asArtifact(dockArtifact.value);
      if (
        request !== _lineageReq.value ||
        !provMode.value ||
        !docked ||
        docked.id !== art.id ||
        artifactCacheKey(docked) !== key
      )
        return;
      lineage.value = l;
      _lineageFor.value = key;
      rerenderViewer();
    });
  }
}

const SUBS: Array<[string, string]> = [
  ["code", "Code"],
  ["exec", "Execution Log"],
  ["messages", "Messages"],
  ["environment", "Environment"],
  ["review", "Review"],
];

export function renderProvenanceInto(v: HTMLElement, a: unknown): void {
  const art = asArtifact(a);
  if (!art) return;
  const tabs = el("div", "prov-subtabs");
  SUBS.forEach(([k, lab]) => {
    const b = el("button", "prov-subtab" + (provSub.value === k ? " active" : ""), lab);
    b.onclick = () => {
      provSub.value = k;
      rerenderViewer();
    };
    tabs.appendChild(b);
  });
  v.appendChild(tabs);
  const body = el("div", "prov-body");
  v.appendChild(body);
  const lin =
    _lineageFor.value === artifactCacheKey(art) ? (lineage.value as LineagePayload | null) : null;
  const model = lin ? lineageReviewModel(lin) : null;
  const cell = model && model.cell;
  if (provSub.value === "code") {
    if (cell && cell.source) {
      body.appendChild(
        codeBlock(String(cell.source), {
          lang: String(cell.language || "python"),
          langLabel: String(cell.language || "python"),
          env: cell.environment ? String(cell.environment) : undefined,
        }),
      );
    } else if (!lin) body.appendChild(el("div", "dock-empty", t("common.loading")));
    else body.appendChild(el("div", "dock-empty", "Generating reproduction code…"));
  } else if (provSub.value === "exec") {
    if (currentId.value) body.appendChild(notebookExportLink(currentId.value));
    const list = (cells.value || []) as NotebookCell[];
    if (!list.length) body.appendChild(el("div", "dock-empty", t("prov.exec.noRecords")));
    list.forEach((e) => body.appendChild(cellNode(e)));
  } else if (provSub.value === "environment") {
    void renderProvEnvironment(body, art);
  } else if (provSub.value === "messages") {
    void renderProvMessages(body);
  } else if (provSub.value === "review") {
    renderProvReview(body, art, lin);
  } else {
    body.appendChild(el("div", "dock-empty", "—"));
  }
}

async function renderProvEnvironment(body: HTMLElement, a: ArtifactRow): Promise<void> {
  body.appendChild(el("div", "dock-empty", t("prov.env.loadingSnapshot")));
  const key = artifactCacheKey(a);
  const snaps = _envSnapById.value || {};
  let env: EnvSnapshot;
  try {
    env = (snaps[key] ||
      (snaps[key] = await (a && a.id
        ? api(`/artifacts/${a.id}/environment`)
        : api("/kernel/environment")))) as EnvSnapshot;
    _envSnapById.value = snaps;
  } catch (e) {
    if (provMode.value && provSub.value === "environment") {
      body.innerHTML = "";
      body.appendChild(el("div", "dock-empty", t("prov.env.loadFailed", apiErrorText(e))));
    }
    return;
  }
  const docked = asArtifact(dockArtifact.value);
  if (!provMode.value || provSub.value !== "environment" || (a && artifactCacheKey(docked) !== key))
    return;
  body.innerHTML = "";
  const chip = (k: string, val: string) => {
    const c = el("span", "env-chip");
    c.appendChild(el("span", "env-chip-k", k));
    c.appendChild(el("span", "env-chip-v", val));
    return c;
  };
  const pkgs = env.packages || [];
  const chips = el("div", "env-chips");
  chips.appendChild(chip("Environment", env.kind || "python"));
  const py = envPythonChip(env);
  if (py) chips.appendChild(chip(py.label, py.value));
  if (env.environment_name) chips.appendChild(chip("Env", publicText(env.environment_name, 48)));
  chips.appendChild(chip("Packages", String(envPackageCount(env))));
  body.appendChild(chips);
  if (env.interpreter) body.appendChild(el("div", "env-plat", publicText(env.interpreter, 160)));
  if (env.platform) body.appendChild(el("div", "env-plat", env.platform));
  if (env.packages_unavailable) {
    body.appendChild(el("div", "env-src warn", publicText(env.packages_unavailable, 200)));
  }
  const honesty = envSnapshotHonesty(env);
  const note = el("div", "env-src " + honesty.noteClass);
  note.appendChild(iconEl(honesty.captured ? "package" : "clock", 13));
  note.appendChild(el("span", null, t(honesty.noteKey)));
  body.appendChild(note);
  if (honesty.showProvenanceWhy && env.provenance) {
    body.appendChild(el("div", "env-src warn", publicText(env.provenance, 200)));
  }
  const remote = env.remote || [];
  if (remote.length) {
    const rw = el("div", "env-remote");
    rw.appendChild(el("div", "env-remote-h", t("prov.env.remoteTitle")));
    remote.forEach((raw) => {
      const r = (raw && typeof raw === "object" ? raw : {}) as {
        env?: Record<string, unknown>;
        host?: string;
        engine?: string;
        service?: string;
      };
      const e = (r.env || {}) as Record<string, unknown>;
      const rows: Array<[string, string]> = [];
      const push = (k: string, v: unknown) => {
        if (v != null && v !== "") rows.push([k, String(v)]);
      };
      push(t("prov.env.remoteHost"), (r.host || "") + (e.hostname ? " · " + e.hostname : ""));
      push("GPU", e.gpu || "");
      push("Engine", r.engine || "");
      push(
        t("prov.env.remoteEnv"),
        (e.conda_env ? e.conda_env + " · " : "") + "Python " + (e.python || "?"),
      );
      if (e.packages && typeof e.packages === "object")
        push(
          t("prov.env.remotePkgs"),
          Object.entries(e.packages as Record<string, unknown>)
            .map(([k, v]) => k + " " + v)
            .join(" · "),
        );
      const code = e.code && typeof e.code === "object" ? (e.code as Record<string, unknown>) : null;
      if (code)
        push(
          t("prov.env.remoteCode"),
          (code.repo || "") +
            " @ " +
            (code.git_commit ? String(code.git_commit).slice(0, 10) : "?") +
            (code.git_dirty ? " (dirty)" : "") +
            (code.wrapper_sha256 ? " · wrapper " + String(code.wrapper_sha256).slice(0, 10) : ""),
        );
      const model = e.model && typeof e.model === "object" ? (e.model as Record<string, unknown>) : null;
      if (model)
        push(
          t("prov.env.remoteModel"),
          (model.name || "") +
            (model.weights_sha256 ? " · sha " + String(model.weights_sha256).slice(0, 12) : "") +
            (model.weights_bytes ? " · " + (Number(model.weights_bytes) / 1e9).toFixed(2) + " GB" : ""),
        );
      push(t("prov.env.remoteRun"), e.run_utc || "");
      const card = el("div", "env-remote-card");
      card.appendChild(el("div", "env-remote-svc", (r.service || "job") + " · " + (r.host || "")));
      const tbl = el("table", "env-table");
      const tb = el("tbody");
      rows.forEach(([k, val]) => {
        const tr = el("tr");
        tr.appendChild(el("td", "env-pk", k));
        tr.appendChild(el("td", "env-pv", val));
        tb.appendChild(tr);
      });
      tbl.appendChild(tb);
      card.appendChild(tbl);
      rw.appendChild(card);
    });
    body.appendChild(rw);
  }
  if (!pkgs.length) {
    body.appendChild(el("div", "dock-empty", t("prov.env.noPackages")));
    return;
  }
  const wrap = el("div", "env-tbl-wrap");
  const tbl = el("table", "env-table");
  const thead = el("thead");
  const htr = el("tr");
  htr.appendChild(el("th", null, "Package"));
  htr.appendChild(el("th", null, "Version"));
  thead.appendChild(htr);
  tbl.appendChild(thead);
  const tb = el("tbody");
  pkgs.forEach((p) => {
    const tr = el("tr");
    tr.appendChild(el("td", "env-pk", p.name || ""));
    tr.appendChild(el("td", "env-pv", p.version || "—"));
    tb.appendChild(tr);
  });
  tbl.appendChild(tb);
  wrap.appendChild(tbl);
  body.appendChild(wrap);
}

async function renderProvMessages(body: HTMLElement): Promise<void> {
  body.appendChild(el("div", "dock-empty", t("prov.msg.loading")));
  let msgs: ChatMessage[] = [];
  try {
    if (!currentId.value) throw new Error("no session");
    const d = await fetchRecentMessages(currentId.value, 500);
    msgs = (d && d.messages) || [];
  } catch (e) {
    if (provMode.value && provSub.value === "messages") {
      body.innerHTML = "";
      body.appendChild(el("div", "dock-empty", t("prov.msg.loadFailed", apiErrorText(e))));
    }
    return;
  }
  if (!provMode.value || provSub.value !== "messages") return;
  body.innerHTML = "";
  if (!msgs.length) {
    body.appendChild(el("div", "dock-empty", t("prov.msg.noRecords")));
    return;
  }
  msgs.forEach((m) => {
    const role = m.role || "assistant";
    const row = el("div", "prov-msg " + role);
    const head = el("div", "prov-msg-h");
    head.appendChild(
      el(
        "span",
        "prov-msg-role",
        role === "user" ? "User" : role === "system" ? "System" : "Assistant",
      ),
    );
    if (m.created_at) head.appendChild(el("span", "prov-msg-t", ago(m.created_at)));
    row.appendChild(head);
    const rec = m as ChatMessage & { text?: string };
    const txt = rec.text || rec.content || "";
    const md = el("div", "md prov-msg-b");
    md.innerHTML = renderMd(String(txt));
    row.appendChild(md);
    body.appendChild(row);
  });
}

function viewCodeLink(onClick: () => void): HTMLElement {
  const link = el("a", "prov-link");
  link.appendChild(iconEl("arrow-left", 14));
  link.appendChild(el("span", null, t("prov.review.viewCode")));
  link.onclick = onClick;
  return link;
}

export function renderProvReview(
  body: HTMLElement,
  _a: ArtifactRow,
  lin: LineagePayload | null,
): void {
  if (!lin) {
    body.appendChild(el("div", "dock-empty", t("common.loading")));
    return;
  }
  const model = lineageReviewModel(lin);
  const cell = model.cell;
  const inputs = model.mappedInputs;
  const cellInputs = model.cellInputs;
  const captures = model.captures;
  const producer = model.producer;
  if (model.empty) {
    body.appendChild(el("div", "dock-empty", t("prov.review.noLineage")));
    return;
  }
  const card = el("div", "prov-card");
  if (cell) {
    card.appendChild(
      el("div", "prov-h", t("prov.review.producedBy", cell.cell_index != null ? cell.cell_index : "?")),
    );
    card.appendChild(
      el(
        "div",
        "prov-meta",
        (cell.language || "python") +
          " · " +
          (cell.exit_status || cell.status || "ok") +
          (cell.kernel_id ? " · " + cell.kernel_id : ""),
      ),
    );
    const wrote = Array.isArray(cell.files_written) ? cell.files_written.map((f) => publicText(f, 160)).filter(Boolean) : [];
    if (wrote.length) card.appendChild(provRow("wrote", wrote));
    if (cellInputs.length) card.appendChild(provRow("reads / inputs", cellInputs));
    card.appendChild(
      viewCodeLink(() => {
        provMode.value = false;
        setActiveTab("notebook");
        scrollToCell(cell.cell_index as number | string, cell.kernel_id);
      }),
    );
  } else if (inputs.length) card.appendChild(provRow("reads / inputs", inputs));
  if (cell || inputs.length) body.appendChild(card);
  captures.forEach((capture: LineageCapture) => {
    const captureCard = el("div", "prov-card");
    const identity = publicText(capture.producing_cell_id || "unknown Cell", 96);
    const inRoot = captureInRootNotebook(capture);
    captureCard.appendChild(
      el(
        "div",
        "prov-h",
        inRoot
          ? t("prov.review.producedBy", capture.cell_index)
          : t("prov.review.producedByIdentity", identity),
      ),
    );
    const captureKind =
      capture.capture_kind === "head_checksum_reused"
        ? t("prov.review.sameBytesCapture")
        : t("prov.review.versionCapture");
    const frameMeta = capture.frame_id
      ? " · " +
        t(
          "prov.review.producerFrame",
          publicText(capture.frame_kind || "unknown", 32),
          publicText(capture.frame_id, 96),
        )
      : "";
    captureCard.appendChild(el("div", "prov-meta", captureKind + " · " + identity + frameMeta));
    if (Array.isArray(capture.inputs) && capture.inputs.length)
      captureCard.appendChild(
        provRow(
          "reads / inputs",
          capture.inputs.map((item) => publicText(item, 160)).filter(Boolean),
        ),
      );
    if (inRoot) {
      captureCard.appendChild(
        viewCodeLink(() => {
          provMode.value = false;
          setActiveTab("notebook");
          scrollToCell(capture.cell_index as number | string, capture.kernel_id);
        }),
      );
    }
    body.appendChild(captureCard);
  });
  if (!cell && !captures.length && producer) {
    const producerCard = el("div", "prov-card");
    const producerHeading =
      producer.kind === "cell"
        ? t("prov.review.producedByIdentity", publicText(producer.producing_cell_id || "unknown Cell", 96))
        : t("prov.review.nonCellProducer");
    producerCard.appendChild(el("div", "prov-h", producerHeading));
    if (producer.frame_id)
      producerCard.appendChild(
        el(
          "div",
          "prov-meta",
          t(
            "prov.review.producerFrame",
            publicText(producer.frame_kind || "unknown", 32),
            publicText(producer.frame_id, 96),
          ),
        ),
      );
    body.appendChild(producerCard);
  }
  if (model.saveAt) body.appendChild(el("div", "prov-meta", t("prov.review.saved", ago(String(model.saveAt)))));
}

export function decorateViewerWithProvenance(): void {
  if (typeof document === "undefined") return;
  const v = document.getElementById("dock-viewer");
  if (!v) return;
  const acts = v.querySelector(".vh-acts");
  if (!acts) return;
  if (acts.querySelector("[data-f16-provenance]")) return;
  const art = asArtifact(dockArtifact.value);
  if (!art) return;
  if (provMode.value) {
    const back = el("button", "outline-btn small", t("common.close")) as HTMLButtonElement;
    back.setAttribute("data-f16-provenance", "back");
    back.onclick = () => {
      provMode.value = false;
      rerenderViewer();
    };
    acts.insertBefore(back, acts.firstChild);
    return;
  }
  const btn = el("button", "outline-btn small", t("menu.provenance")) as HTMLButtonElement;
  btn.setAttribute("data-f16-provenance", "1");
  btn.onclick = () => showProvenance(art);
  acts.insertBefore(btn, acts.firstChild);
}
