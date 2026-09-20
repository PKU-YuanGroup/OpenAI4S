/**
 * Provenance tab. Port of app.js:10631-10833.
 *
 * F-17 Viewer calls `window.renderProvenanceInto` when `provMode` is set.
 * Lineage / env-honesty transforms live in `lineage.ts` (tested as pure data).
 */

import { _envSnapById, dockArtifact } from "../../stores/artifacts";
import { _lineageFor, _lineageReq, cells, kernelFilter, lineage, liveCells } from "../../stores/notebook";
import { _openGen, currentId } from "../../stores/session";
import { provMode, provSub } from "../../stores/ui";
import { isReady } from "../../compat/stub";
import { t } from "../../i18n/runtime";
import { artifactMetadataCacheKey, artifactMetadataTarget, artifactMetadataUrl, artifactTabKey } from "../artifacts/cache";
import { filesT } from "../artifacts/copy";
import { addOpenTab, setActiveTab } from "../artifacts/ui";
import type { ArtifactRow } from "../artifacts/types";
import { renderMd } from "../md/render";
import { iconEl, notebookExportLink } from "../notebook/chrome";
import { cellNode, renderNotebook } from "../notebook/Notebook";
import type { NotebookCell } from "../notebook/types";
import { publicText } from "../scrub/scrub";
import { codeBlock } from "../send/step";
import { ago } from "../sessions/dom";
import { fetchRecentMessages, type ChatMessage } from "../sessions/messages";
import { api, apiErrorText, ApiError } from "./api";
import { provenanceT } from "./copy";
import { validateEnvironment, validateLineage } from "./validation";
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
  const target = artifactMetadataTarget(a);
  return validateLineage(await api(artifactMetadataUrl(target, "lineage")), target);
}

function provRow(label: string, files: string[]): HTMLElement {
  const d = el("div", "prov-row");
  d.appendChild(el("span", "prov-lbl", label));
  const box = el("div", "prov-files");
  (files || []).forEach((f) => box.appendChild(el("span", "prov-pill", f)));
  d.appendChild(box);
  return d;
}

type ReadOwner = { key: string; request: number; frameId: string | null };
type LineageRead = ReadOwner & ({ status: "loading" | "ready" } | { status: "error"; error: string });
let lineageRead: LineageRead | null = null;
let environmentRequest = 0;
let messagesRequest = 0;
type PendingRead<T> = ReadOwner & { promise: Promise<T> };
let environmentRead: PendingRead<EnvSnapshot> | null = null;
let messagesRead: PendingRead<{ messages: ChatMessage[] }> | null = null;
function sameRead(left: ReadOwner | null, right: ReadOwner): boolean {
  return !!left && left.key === right.key && left.request === right.request && left.frameId === right.frameId;
}

function readOwner(a: ArtifactRow): ReadOwner {
  return { key: artifactMetadataCacheKey(a), request: _lineageReq.value, frameId: currentId.value };
}
function ownsRead(owner: ReadOwner): boolean {
  return provMode.value && owner.request === _lineageReq.value && owner.frameId === currentId.value &&
    artifactMetadataCacheKey(asArtifact(dockArtifact.value)) === owner.key;
}
function readFailure(body: HTMLElement, error: unknown, retry: () => void): void {
  body.innerHTML = "";
  body.appendChild(el("div", "dock-empty", provenanceT("failed", apiErrorText(error))));
  const button = el("button", "outline-btn small prov-retry", provenanceT("retry"));
  button.onclick = retry;
  body.appendChild(button);
}
function startLineageRead(a: ArtifactRow): void {
  const target = artifactMetadataTarget(a);
  _lineageReq.value++;
  const owner = readOwner(target);
  lineageRead = { ...owner, status: "loading" };
  lineage.value = null;
  _lineageFor.value = owner.key;
  void loadLineage(target).then((value) => {
    if (!ownsRead(owner)) return;
    lineageRead = { ...owner, status: "ready" };
    lineage.value = value;
    rerenderViewer();
  }).catch((error: unknown) => {
    if (!ownsRead(owner)) return;
    lineageRead = { ...owner, status: "error", error: apiErrorText(error) };
    rerenderViewer();
  });
}

export function showProvenance(a: unknown): void {
  const art = asArtifact(a);
  if (!art) return;
  // A new visit invalidates even an earlier request for the same artifact.
  _lineageReq.value++;
  lineageRead = null;
  dockArtifact.value = art;
  provMode.value = true;
  if (!provSub.value) provSub.value = "code";
  if ((provSub.value === "code" || provSub.value === "review") && (!lineage.value || _lineageFor.value !== artifactMetadataCacheKey(art))) startLineageRead(art);
  addOpenTab(art);
  // setActiveTab synchronously renders the Viewer. Its read state must already
  // exist so this single click cannot launch another identical request.
  setActiveTab(artifactTabKey(art));
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
    _lineageFor.value === artifactMetadataCacheKey(art) ? (lineage.value as LineagePayload | null) : null;
  if ((provSub.value === "code" || provSub.value === "review") && !lin) {
    if (!lineageRead || !ownsRead(lineageRead)) startLineageRead(art);
    const read = lineageRead;
    if (read?.status === "error") {
      const target = artifactMetadataTarget(art);
      readFailure(body, read.error, () => {
        if (!ownsRead(read)) return;
        startLineageRead(target);
        rerenderViewer();
      });
      return;
    }
  }
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
    else {
      const recorded = model?.producer?.cell_recorded === true || model?.captures.some((capture) => capture.cell_recorded === true);
      body.appendChild(el("div", "dock-empty", provenanceT(recorded ? "codeElsewhere" : "noCode")));
      if (recorded) renderProvReview(body, art, lin);
    }
  } else if (provSub.value === "exec") {
    if (!art.root_frame_id || art.root_frame_id !== currentId.value) {
      body.appendChild(el("div", "dock-empty", provenanceT(art.root_frame_id ? "otherNotebook" : "unknownSession")));
      return;
    }
    body.appendChild(notebookExportLink(art.root_frame_id));
    const list = (cells.value || []) as NotebookCell[];
    if (!list.length) body.appendChild(el("div", "dock-empty", t("prov.exec.noRecords")));
    list.forEach((e) => body.appendChild(cellNode(e)));
  } else if (provSub.value === "environment") {
    void renderProvEnvironment(body, art);
  } else if (provSub.value === "messages") {
    void renderProvMessages(body, art);
  } else if (provSub.value === "review") {
    renderProvReview(body, art, lin);
  } else {
    body.appendChild(el("div", "dock-empty", "—"));
  }
}

async function renderProvEnvironment(body: HTMLElement, a: ArtifactRow): Promise<void> {
  body.appendChild(el("div", "dock-empty", t("prov.env.loadingSnapshot")));
  const target = artifactMetadataTarget(a);
  const key = artifactMetadataCacheKey(target);
  const owner = readOwner(target);
  const request = ++environmentRequest;
  const current = () => request === environmentRequest && ownsRead(owner) && provSub.value === "environment";
  let env: EnvSnapshot;
  try {
    const cached = _envSnapById.value[key];
    if (!cached && !sameRead(environmentRead, owner)) {
      environmentRead = { ...owner, promise: api(artifactMetadataUrl(target, "environment")).then((value) => validateEnvironment(value, target)) };
    }
    env = cached ? validateEnvironment(cached, target) : await environmentRead!.promise;
    // An immutable version may finish after its head advances, but an older
    // visit/retry must never overwrite the newer visit's cache.
    if (!cached && request === environmentRequest && owner.request === _lineageReq.value && owner.frameId === currentId.value && provMode.value) {
      _envSnapById.value = { ..._envSnapById.value, [key]: env };
    }
  } catch (e) {
    if (!current()) return;
    body.innerHTML = "";
    if (e instanceof ApiError && e.status === 404 && e.code === "environment_snapshot_unavailable") {
      body.appendChild(el("div", "dock-empty", filesT("prov.env.noSnapshot")));
    } else readFailure(body, e, () => {
      if (current()) { environmentRead = null; body.innerHTML = ""; void renderProvEnvironment(body, target); }
    });
    return;
  }
  if (!current()) return;
  body.innerHTML = "";
  const chip = (k: string, val: string) => {
    const c = el("span", "env-chip");
    c.appendChild(el("span", "env-chip-k", k));
    c.appendChild(el("span", "env-chip-v", val));
    return c;
  };
  const pkgs = env.packages || [];
  const chips = el("div", "env-chips");
  chips.appendChild(chip("Environment", env.kind || provenanceT("unknownEnvironment")));
  const py = envPythonChip(env);
  if (py) chips.appendChild(chip(py.label, py.value));
  if (env.environment_name) chips.appendChild(chip("Env", publicText(env.environment_name, 48)));
  // An explicit `package_count: null` is a record saying the count is unknown,
  // which `envPackageCount` would otherwise answer with the length of an empty
  // `packages` list -- a measured zero the row never claimed.
  const packageCount = env.package_count === null ? null : envPackageCount(env);
  const pythonKind = String(env.kind || "python").toLowerCase() === "python";
  chips.appendChild(
    chip(
      "Packages",
      packageCount != null
        ? String(packageCount)
        : filesT(pythonKind ? "prov.env.packagesUnknown" : "prov.env.packagesNotApplicable"),
    ),
  );
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
        [e.conda_env, e.python ? "Python " + e.python : ""].filter(Boolean).join(" · "),
      );
      if (e.packages && typeof e.packages === "object")
        push(
          t("prov.env.remotePkgs"),
          Array.isArray(e.packages) ? e.packages.join(" · ") : Object.entries(e.packages as Record<string, unknown>)
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
    // An unread list is not an empty one; the warn note above says why.
    if (packageCount != null) body.appendChild(el("div", "dock-empty", t("prov.env.noPackages")));
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

async function renderProvMessages(body: HTMLElement, art: ArtifactRow): Promise<void> {
  const target = artifactMetadataTarget(art);
  const owner = readOwner(target);
  const request = ++messagesRequest;
  const current = () => request === messagesRequest && ownsRead(owner) && provSub.value === "messages";
  const frameId = target.root_frame_id;
  if (!frameId) { body.appendChild(el("div", "dock-empty", provenanceT("unknownSession"))); return; }
  body.appendChild(el("div", "dock-empty", t("prov.msg.loading")));
  let msgs: ChatMessage[] = [];
  try {
    if (!sameRead(messagesRead, owner)) messagesRead = { ...owner, promise: fetchRecentMessages(frameId, 500) };
    const d = await messagesRead!.promise;
    msgs = (d && d.messages) || [];
  } catch (e) {
    if (current()) readFailure(body, e, () => {
      if (current()) { messagesRead = null; body.innerHTML = ""; void renderProvMessages(body, target); }
    });
    return;
  }
  if (!current()) return;
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

function recordedCodeLink(a: ArtifactRow, producer: Record<string, unknown> | null | undefined): HTMLElement | null {
  const frameId = a.root_frame_id;
  const id = producer?.producing_cell_id;
  const entries = () => ([...cells.value, ...liveCells.value] as NotebookCell[]).flatMap((cell) => [cell, ...(cell._revisions || [])]);
  const valid = () => !!frameId && frameId === currentId.value &&
    producer?.frame_id === frameId && producer.frame_kind !== "delegate" && producer.cell_recorded !== false &&
    typeof id === "string" && !!id && entries().some((cell) => (cell.producing_cell_id || cell.cell_id) === id);
  if (!valid()) return null;
  return viewCodeLink(() => {
    if (!valid()) return;
    const navigation = _openGen.value;
    provMode.value = false;
    kernelFilter.value = null;
    setActiveTab("notebook");
    renderNotebook();
    requestAnimationFrame(() => {
      if (!valid() || navigation !== _openGen.value) return;
      const root = document.getElementById("dock-notebook");
      const node = Array.from(root?.querySelectorAll<HTMLElement>(".notebook-cell[data-producing-cell]") || [])
        .find((cell) => cell.getAttribute("data-producing-cell") === id);
      if (!node) return;
      let parent = node.parentElement;
      while (parent && parent !== root) {
        if (parent.tagName === "DETAILS") (parent as HTMLDetailsElement).open = true;
        parent = parent.parentElement;
      }
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      node.classList.add("flash");
      setTimeout(() => node.classList.remove("flash"), 1600);
    });
  });
}

export function renderProvReview(
  body: HTMLElement,
  a: ArtifactRow,
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
    body.appendChild(el("div", "dock-empty", provenanceT("noLineage")));
    body.appendChild(el("div", "prov-meta", provenanceT("noInputs")));
    if (model.saveAt) body.appendChild(el("div", "prov-meta", t("prov.review.saved", ago(String(model.saveAt)))));
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
    if (cellInputs.length) card.appendChild(provRow(provenanceT("inputs"), cellInputs));
    const link = recordedCodeLink(a, producer);
    if (link) card.appendChild(link);
    if (producer?.frame_id) card.appendChild(el("div", "prov-meta", t("prov.review.producerFrame", producer.frame_kind || "unknown", producer.frame_id)));

  } else if (inputs.length) card.appendChild(provRow(provenanceT("inputs"), inputs));
  if (cell || inputs.length) body.appendChild(card);
  captures.forEach((capture: LineageCapture) => {
    const captureCard = el("div", "prov-card");
    const identity = publicText(capture.producing_cell_id || "unknown Cell", 96);
    const link = recordedCodeLink(a, capture);
    const inRoot = captureInRootNotebook(capture) && !!link;
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
          provenanceT("inputs"),
          capture.inputs.map((item) => publicText(item, 160)).filter(Boolean),
        ),
      );
    if (link) captureCard.appendChild(link);
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
  if (!inputs.length && !cellInputs.length && !captures.some((c) => Array.isArray(c.inputs) && c.inputs.length))
    body.appendChild(el("div", "prov-meta", provenanceT("noInputs")));
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
