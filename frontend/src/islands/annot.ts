/**
 * Image annotator. Port of app.js:8965-8993 helpers and 9149-9429.
 *
 * Admission tracking (9016-9148) is F-11. This island owns pins, zoom/pan,
 * the draft popup, the composer chip, and annotation CRUD.
 */

import { artifacts as artifactsSignal, dockArtifact } from "../stores/artifacts";
import { _annotDraft, annotations, currentId } from "../stores/session";
import { api, apiErrorText } from "../features/artifacts/api";
import type { ArtifactRow } from "../features/artifacts/types";
import { openViewer } from "../features/artifacts/ui";
import { hint } from "../features/sessions/chrome";
import { $, el, icon, iconEl } from "./dom";
import { translate } from "./host";

export type Annotation = {
  id?: string;
  annotation_id?: string;
  artifact_id?: string;
  artifact_name?: string;
  number?: number | string;
  body?: string;
  status?: string;
  x?: number;
  y?: number;
};

type AnnotStage = HTMLElement & { _artId?: string; _panned?: boolean };

type AnnotDraft = {
  stage: AnnotStage;
  art: ArtifactRow;
  x: number;
  y: number;
  pin: HTMLElement;
  pop: HTMLElement;
};

function asAnnotations(value: unknown): Annotation[] {
  return Array.isArray(value) ? (value as Annotation[]) : [];
}

/** app.js:8965 */
export function annotationsFor(artifactId: string): Annotation[] {
  return asAnnotations(annotations.value).filter((x) => x.artifact_id === artifactId);
}

/** app.js:8966 */
export function openAnnotations(): Annotation[] {
  return asAnnotations(annotations.value).filter((x) => x.status === "open");
}

/** app.js:8967 */
export function annotationId(an: Annotation | null | undefined): string | undefined {
  const id = an && (an.id || an.annotation_id);
  return id == null ? undefined : String(id);
}

/* The one place that decides what a pin's status is for display. `reserved`
   and `pending` are in-flight: not open (the user cannot act on them) and not
   sent (they are not consumed yet). Anything unrecognised is `unknown` rather
   than silently `open`, because "I do not know" and "you may edit this" are
   different answers. */
export function annotationStatus(an: Annotation | null | undefined): string {
  const raw = String((an && an.status) || "open");
  if (raw === "sent" || raw === "resolved" || raw === "dismissed") return raw;
  if (raw === "reserved" || raw === "pending") return "pending";
  if (raw === "open") return "open";
  return "unknown";
}

export function annotationIsHeld(an: Annotation | null | undefined): boolean {
  const shown = annotationStatus(an);
  return shown === "pending" || shown === "unknown";
}

export function renderPins(stage: AnnotStage, a: ArtifactRow): void {
  const layer = stage.querySelector(".annot-layer");
  if (!layer) return;
  layer.querySelectorAll(".annot-pin:not(.draft)").forEach((n) => n.remove());
  annotationsFor(a.id).forEach((an) => {
    // A held pin is not an open one. `reserved` (a turn is quoting it) and
    // `pending` (accepted, consume unconfirmed) both used to render as `open`,
    // which invites the user to edit or delete a comment that is already on its
    // way -- and `data-annotation-status` exists so a test can assert that
    // without matching on CSS class soup or translated text.
    const shown = annotationStatus(an);
    const pin = el("div", "annot-pin " + shown);
    pin.dataset.annotationStatus = shown;
    pin.style.left = Number(an.x || 0) * 100 + "%";
    pin.style.top = Number(an.y || 0) * 100 + "%";
    pin.textContent = String(an.number ?? "");
    pin.title = an.body || "";
    pin.onclick = (e) => {
      e.stopPropagation();
      openPinPop(stage, a, an);
    };
    layer.appendChild(pin);
  });
}

export function closeAnnotDraft(): void {
  const d = _annotDraft.value as AnnotDraft | null;
  if (!d) return;
  try {
    if (d.pin) d.pin.remove();
    if (d.pop) d.pop.remove();
  } catch {
    /* already removed */
  }
  _annotDraft.value = null;
}

export function closeAnnotPop(): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".annot-pop.view").forEach((n) => n.remove());
}

function positionPop(pop: HTMLElement, layer: Element, x: number, y: number): void {
  const host = layer as HTMLElement;
  const lw = host.clientWidth,
    lh = host.clientHeight;
  pop.style.visibility = "hidden";
  pop.style.left = "0px";
  pop.style.top = "0px";
  requestAnimationFrame(() => {
    const pw = pop.offsetWidth || 260,
      ph = pop.offsetHeight || 120;
    let px = x * lw + 18,
      py = y * lh - 8;
    if (px + pw > lw - 8) px = x * lw - pw - 18;
    px = Math.max(8, Math.min(px, Math.max(8, lw - pw - 8)));
    py = Math.max(8, Math.min(py, Math.max(8, lh - ph - 8)));
    pop.style.left = px + "px";
    pop.style.top = py + "px";
    pop.style.visibility = "";
  });
}

function openAnnotDraft(stage: AnnotStage, a: ArtifactRow, x: number, y: number): void {
  closeAnnotDraft();
  closeAnnotPop();
  const layer = stage.querySelector(".annot-layer");
  if (!layer) return;
  const num = annotationsFor(a.id).length + 1;
  const pin = el("div", "annot-pin draft");
  pin.style.left = x * 100 + "%";
  pin.style.top = y * 100 + "%";
  pin.textContent = String(num);
  layer.appendChild(pin);
  const pop = el("div", "annot-pop edit");
  const ta = el("textarea", "annot-input");
  ta.placeholder = "Add annotation…";
  ta.rows = 2;
  const foot = el("div", "annot-foot");
  const spacer = el("div", "annot-foot-l");
  const cancel = el("button", "annot-btn ghost", translate("common.cancel"));
  const save = el("button", "annot-btn solid", translate("common.save"));
  save.disabled = true;
  ta.addEventListener("input", () => {
    save.disabled = !ta.value.trim();
  });
  ta.addEventListener("keydown", (e) => {
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeAnnotDraft();
    } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      if (!save.disabled) save.click();
    }
  });
  cancel.onclick = () => closeAnnotDraft();
  save.onclick = async () => {
    const text = ta.value.trim();
    if (!text) return;
    save.disabled = true;
    save.textContent = translate("common.saving");
    try {
      await saveAnnotation(a, x, y, text);
      closeAnnotDraft();
    } catch (err) {
      save.disabled = false;
      save.textContent = translate("common.save");
      const status = (err as { status?: number } | null) && (err as { status?: number }).status;
      hint(
        status === 404
          ? translate("annot.save.err404")
          : translate("annot.save.err", apiErrorText(err)),
        true,
      );
    }
  };
  foot.appendChild(spacer);
  foot.appendChild(cancel);
  foot.appendChild(save);
  pop.appendChild(ta);
  pop.appendChild(foot);
  layer.appendChild(pop);
  positionPop(pop, layer, x, y);
  _annotDraft.value = { stage, art: a, x, y, pin, pop };
  setTimeout(() => ta.focus(), 0);
}

async function saveAnnotation(a: ArtifactRow, x: number, y: number, text: string): Promise<void> {
  if (!currentId.value) {
    hint(translate("annot.noSession"), true);
    return;
  }
  const res = (await api(`/frames/${currentId.value}/annotations`, {
    method: "POST",
    body: JSON.stringify({
      artifact_id: a.id,
      artifact_name: a.filename || "",
      x,
      y,
      body: text,
    }),
  })) as { annotation?: Annotation } | null;
  const an = res && res.annotation;
  if (!an) return;
  annotations.value = asAnnotations(annotations.value).concat([an]);
  refreshAllStages();
  updateAnnotBadge();
  hint(translate("annot.added"));
}

/* Re-render pins on every visible image stage for this artifact (dock + modal). */
export function refreshAllStages(): void {
  if (typeof document === "undefined") return;
  document.querySelectorAll(".annot-stage").forEach((node) => {
    const stage = node as AnnotStage;
    const list = (artifactsSignal.value || []) as ArtifactRow[];
    const art =
      list.find((x) => x.id === stage._artId) ||
      (dockArtifact.value &&
      (dockArtifact.value as ArtifactRow).id === stage._artId
        ? (dockArtifact.value as ArtifactRow)
        : null);
    if (art) renderPins(stage, art);
  });
}

export function openPinPop(stage: AnnotStage, a: ArtifactRow, an: Annotation): void {
  void a;
  closeAnnotDraft();
  closeAnnotPop();
  const layer = stage.querySelector(".annot-layer");
  if (!layer) return;
  const pop = el("div", "annot-pop view");
  const head = el("div", "annot-pop-head");
  head.appendChild(el("span", "annot-pop-num", "#" + an.number));
  const shown = annotationStatus(an);
  const label =
    (
      {
        sent: translate("annot.status.sent"),
        resolved: translate("annot.status.resolved"),
        dismissed: translate("annot.status.resolved"),
        pending: translate("annot.status.pending"),
        unknown: translate("annot.status.unknown"),
      } as Record<string, string>
    )[shown] || translate("annot.status.open");
  const st = el("span", "annot-pop-status " + shown, label);
  st.dataset.annotationStatus = shown;
  head.appendChild(st);
  const bodyEl = el("div", "annot-pop-body", an.body || "");
  const foot = el("div", "annot-foot");
  foot.appendChild(el("div", "annot-foot-l"));
  const del = el("button", "annot-btn ghost danger", translate("common.delete"));
  del.onclick = async () => {
    del.disabled = true;
    try {
      const id = annotationId(an);
      await deleteAnnotations(id ? [id] : []);
      closeAnnotPop();
      hint(translate("annot.deleted"));
    } catch (err) {
      del.disabled = false;
      hint(translate("toast.deleteFailed", apiErrorText(err)), true);
    }
  };
  // A held pin is not the user's to delete: a turn is quoting it, and the
  // server answers 409 for exactly this. Offering the button and then showing
  // an error is a worse version of not offering it.
  if (annotationIsHeld(an)) {
    del.disabled = true;
    del.title = translate("annot.status.pending");
    del.dataset.heldByTurn = "1";
  }
  const close = el("button", "annot-btn solid", translate("common.close"));
  close.onclick = () => closeAnnotPop();
  foot.appendChild(del);
  foot.appendChild(close);
  pop.appendChild(head);
  pop.appendChild(bodyEl);
  pop.appendChild(foot);
  layer.appendChild(pop);
  positionPop(pop, layer, Number(an.x || 0), Number(an.y || 0));
}

/* Composer chip: how many pinned comments will ride along with the next message. */
export function updateAnnotBadge(): void {
  const bar = $("#annot-bar");
  if (!bar) return;
  const open = openAnnotations();
  if (!open.length) {
    bar.classList.add("hidden");
    bar.innerHTML = "";
    return;
  }
  bar.classList.remove("hidden");
  bar.innerHTML = "";
  const chip = el("span", "annot-chip");
  const main = el("button", "annot-chip-main");
  main.appendChild(iconEl("message-square", 14));
  main.appendChild(
    el("span", null, " " + open.length + (open.length === 1 ? " comment" : " comments")),
  );
  main.title = translate("annot.chip.title");
  main.onclick = (e) => {
    e.stopPropagation();
    toggleAnnotList(chip);
  };
  const cancel = el("button", "annot-chip-x");
  cancel.innerHTML = icon("x", 13);
  cancel.title = translate("annot.discard.title");
  cancel.onclick = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    cancel.disabled = true;
    try {
      await deleteAnnotations(openAnnotations().map(annotationId).filter(Boolean) as string[]);
      closeAnnotPop();
      const p = $("#annot-list-pop");
      if (p) p.remove();
      hint(translate("annot.discarded"));
    } catch (err) {
      cancel.disabled = false;
      hint(translate("annot.remove.err", apiErrorText(err)), true);
    }
  };
  chip.appendChild(main);
  chip.appendChild(cancel);
  bar.appendChild(chip);
}

function toggleAnnotList(anchor: HTMLElement): void {
  const existing = $("#annot-list-pop");
  if (existing) {
    existing.remove();
    return;
  }
  const open = openAnnotations();
  const pop = el("div", "annot-list-pop");
  pop.id = "annot-list-pop";
  pop.appendChild(el("div", "annot-list-head", translate("annot.list.head", open.length)));
  open.forEach((an) => {
    const row = el("div", "annot-list-row");
    const trow = el("div", "annot-list-t");
    trow.appendChild(el("span", "annot-list-pin", String(an.number)));
    trow.appendChild(el("span", "annot-list-file", an.artifact_name || "artifact"));
    row.appendChild(trow);
    row.appendChild(el("div", "annot-list-body", an.body || ""));
    const acts = el("div", "annot-list-acts");
    const openBtn = el("button", "annot-mini", translate("common.view"));
    openBtn.onclick = () => {
      pop.remove();
      const art = ((artifactsSignal.value || []) as ArtifactRow[]).find(
        (x) => x.id === an.artifact_id,
      );
      if (art) openViewer(art);
    };
    const rm = el("button", "annot-mini danger", translate("btn.remove"));
    rm.onclick = async () => {
      try {
        const id = annotationId(an);
        await deleteAnnotations(id ? [id] : []);
        pop.remove();
        if (openAnnotations().length && anchor.parentElement) toggleAnnotList(anchor);
      } catch (err) {
        hint(translate("annot.remove.err", apiErrorText(err)), true);
      }
    };
    acts.appendChild(openBtn);
    acts.appendChild(rm);
    row.appendChild(acts);
    pop.appendChild(row);
  });
  if (anchor.parentElement) anchor.parentElement.appendChild(pop);
  setTimeout(() => {
    document.addEventListener("mousedown", function h(ev) {
      const target = ev.target as Node | null;
      if (!pop.contains(target) && !anchor.contains(target)) {
        pop.remove();
        document.removeEventListener("mousedown", h);
      }
    });
  }, 0);
}

export async function deleteAnnotations(ids: Array<string | undefined | null>): Promise<void> {
  const wanted = [...new Set((ids || []).filter(Boolean))] as string[];
  if (!wanted.length) return;
  const results = await Promise.allSettled(
    wanted.map((id) => api(`/annotations/${id}`, { method: "DELETE" }).then(() => id)),
  );
  const deleted = results
    .filter((r): r is PromiseFulfilledResult<string> => r.status === "fulfilled")
    .map((r) => r.value);
  if (deleted.length) {
    const gone = new Set(deleted);
    annotations.value = asAnnotations(annotations.value).filter(
      (an) => !gone.has(String(annotationId(an) || "")),
    );
    refreshAllStages();
    updateAnnotBadge();
  }
  const failed = results.find((r) => r.status === "rejected");
  if (failed && failed.status === "rejected") throw failed.reason || new Error("delete failed");
}

export async function loadAnnotations(fid: string): Promise<boolean> {
  let res: { annotations?: Annotation[] } | null = null;
  try {
    res = (await api(`/frames/${fid}/annotations`)) as { annotations?: Annotation[] };
  } catch {
    return false;
  }
  if (fid !== currentId.value) return true;
  annotations.value = (res && res.annotations) || [];
  updateAnnotBadge();
  return true;
}

/* Render an image the user can pin comments onto, with zoom + pan. Used by the
   dock viewer AND the fullscreen modal. Zoom is WIDTH-BASED (the image element
   physically grows) rather than a CSS transform, so the pin layer scales with
   it and every annotation coordinate / popup stays pixel-correct — no transform
   math to reconcile. Panning is native overflow scroll. */
export function renderAnnotatableImage(body: HTMLElement, a: ArtifactRow, url: string): void {
  closeAnnotDraft();
  const wrap = el("div", "annot-wrap");
  const zoom = el("div", "annot-zoom");
  const stage = el("div", "annot-stage") as AnnotStage;
  stage._artId = a.id;
  const img = el("img", "annot-img");
  img.src = url;
  img.draggable = false;
  const layer = el("div", "annot-layer");
  stage.appendChild(img);
  stage.appendChild(layer);
  zoom.appendChild(stage);
  wrap.appendChild(zoom);
  body.appendChild(wrap);

  const zs = { z: 1, fitW: 0, max: 8 };
  // At z=1 the image is fitted to the pane by CSS alone (.annot-img max-width:100%
  // inside a max-width:100% stage) — no JS sizing, so a wide figure never overflows
  // and resizing the pane reflows automatically. Only ONCE the user zooms do we
  // pin an explicit width (fitW * z) and let the stage grow past the pane.
  let bOut: HTMLButtonElement;
  let bIn: HTMLButtonElement;
  let lvl: HTMLElement;
  const applyZoom = (z: number): void => {
    zs.z = Math.max(1, Math.min(zs.max, z));
    if (zs.z <= 1.001) {
      img.style.width = "";
      img.style.maxWidth = "";
      zoom.classList.remove("zoomed");
    } else if (zs.fitW) {
      img.style.maxWidth = "none";
      img.style.width = zs.fitW * zs.z + "px";
      zoom.classList.add("zoomed");
    }
    if (lvl) lvl.textContent = Math.round(zs.z * 100) + "%";
    if (bOut) bOut.disabled = zs.z <= 1.001;
    if (bIn) bIn.disabled = zs.z >= zs.max - 0.001;
  };
  // Zoom keeping the content point under (cx,cy) fixed on screen. The fit baseline
  // is captured from the CSS-fitted image while at z<=1 (pane is laid out by the
  // time the user interacts), so it's always correct regardless of load timing.
  const zoomAt = (cx: number, cy: number, nz: number): void => {
    if (zs.z <= 1.001) {
      const w = img.getBoundingClientRect().width;
      if (w) zs.fitW = w;
    }
    if (nz > 1.001 && !zs.fitW) return; // no fit baseline yet (image not laid out) — don't collapse it
    const sr = stage.getBoundingClientRect();
    if (!sr.width) return;
    const fx = (cx - sr.left) / sr.width,
      fy = sr.height ? (cy - sr.top) / sr.height : 0.5;
    applyZoom(nz);
    const sr2 = stage.getBoundingClientRect();
    zoom.scrollLeft += sr2.left - (cx - fx * sr2.width);
    zoom.scrollTop += sr2.top - (cy - fy * sr2.height);
  };
  const zoomCenter = (nz: number): void => {
    const r = zoom.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, nz);
  };

  const bar = el("div", "zoom-bar");
  bOut = el("button");
  bOut.title = translate("zoom.out");
  bOut.innerHTML = icon("minus", 16);
  bOut.onclick = () => zoomCenter(zs.z / 1.4);
  lvl = el("div", "zoom-lvl", "100%");
  lvl.title = translate("zoom.reset");
  lvl.onclick = () => {
    applyZoom(1);
    zoom.scrollTo(0, 0);
  };
  bIn = el("button");
  bIn.title = translate("zoom.in");
  bIn.innerHTML = icon("plus", 16);
  bIn.onclick = () => zoomCenter(zs.z * 1.4);
  bar.appendChild(bOut);
  bar.appendChild(lvl);
  bar.appendChild(bIn);
  wrap.appendChild(bar);
  wrap.appendChild(el("div", "zoom-hint", translate("zoom.hint")));

  // Pins are %-positioned so they don't need the fit width; CSS fits the image at
  // z=1, so nothing here depends on pane-layout timing.
  const ready = (): void => renderPins(stage, a);
  if (img.complete) requestAnimationFrame(ready);
  else img.addEventListener("load", ready);

  // Ctrl/Cmd + wheel zooms toward the cursor (and trackpad pinch, which browsers
  // deliver as ctrl+wheel). A PLAIN wheel is left to scroll natively, so a tall
  // portrait image can still be scrolled/panned instead of being hijacked.
  zoom.addEventListener(
    "wheel",
    (e) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      zoomAt(e.clientX, e.clientY, zs.z * (e.deltaY < 0 ? 1.12 : 1 / 1.12));
    },
    { passive: false },
  );

  // drag-to-pan (only while zoomed). A drag that moves > threshold suppresses the
  // click-to-annotate that would otherwise fire on pointerup.
  zoom.addEventListener("pointerdown", (e) => {
    stage._panned = false;
    if (zs.z <= 1.001 || e.button !== 0) return;
    const target = e.target as HTMLElement | null;
    if (target && target.classList && target.classList.contains("annot-pin")) return;
    const sx = e.clientX,
      sy = e.clientY,
      sl = zoom.scrollLeft,
      st = zoom.scrollTop;
    let moved = false;
    const mv = (ev: PointerEvent): void => {
      const dx = ev.clientX - sx,
        dy = ev.clientY - sy;
      if (!moved && (Math.abs(dx) > 4 || Math.abs(dy) > 4)) {
        moved = true;
        zoom.classList.add("grabbing");
      }
      if (moved) {
        zoom.scrollLeft = sl - dx;
        zoom.scrollTop = st - dy;
        ev.preventDefault();
      }
    };
    const up = (): void => {
      document.removeEventListener("pointermove", mv);
      document.removeEventListener("pointerup", up);
      document.removeEventListener("pointercancel", up);
      zoom.classList.remove("grabbing");
      stage._panned = moved;
    };
    document.addEventListener("pointermove", mv);
    document.addEventListener("pointerup", up);
    document.addEventListener("pointercancel", up);
  });

  layer.addEventListener("click", (e) => {
    if (stage._panned) {
      stage._panned = false;
      return;
    }
    if (e.target !== layer) return;
    const r = layer.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width,
      y = (e.clientY - r.top) / r.height;
    if (x < 0 || x > 1 || y < 0 || y > 1) return;
    openAnnotDraft(stage, a, x, y);
  });
}
