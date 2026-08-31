/**
 * PDF / HTML locator comments. Port of app.js:10835-10897.
 */

import { currentId } from "../stores/session";
import { api, apiErrorText } from "../features/artifacts/api";
import { artUrl } from "../features/artifacts/cache";
import type { ArtifactRow } from "../features/artifacts/types";
import { hint } from "../features/sessions/chrome";
import { el } from "./dom";
import { translate } from "./host";
import { loadAnnotations } from "./annot";

type PdfPage = { page?: number; text?: string };

export function renderLocatorComments(
  container: HTMLElement,
  a: ArtifactRow,
  kind: string,
  viewer?: HTMLIFrameElement | null,
): void {
  const box = el("div", "wb-locator");
  box.appendChild(el("div", "wb-locator-title", translate("wb.locator.title")));
  const quote = el("textarea", "wb-locator-quote");
  quote.placeholder = translate("wb.locator.quote");
  const selector = el("input", "wb-locator-selector");
  selector.placeholder = translate("wb.locator.selector");
  const comment = el("textarea", "wb-locator-body");
  comment.placeholder = translate("wb.locator.body");
  const save = el("button", "solid-btn small", translate("common.save"));
  const preview = el("pre", "wb-locator-preview");
  let pdfPage: HTMLInputElement | null = null;
  let pdfPages: PdfPage[] = [];
  const selectPdfPage = (value: unknown): number => {
    if (!pdfPage) return 1;
    const page = Math.max(1, Math.floor(Number(value) || 1));
    pdfPage.value = String(page);
    if (viewer && viewer.dataset.currentPage !== String(page)) {
      const base = String(viewer.src || artUrl(a)).split("#", 1)[0];
      viewer.dataset.currentPage = String(page);
      viewer.src = base + "#page=" + encodeURIComponent(String(page));
    }
    const extracted = pdfPages.find((item) => Number(item && item.page) === page);
    preview.textContent = extracted ? String(extracted.text || "").slice(0, 4000) : "";
    return page;
  };
  if (kind === "pdf") {
    const pageControls = el("div", "wb-pdf-page-controls");
    const label = el("label", "wb-pdf-page-label", translate("wb.locator.pdfPage"));
    pdfPage = el("input", "wb-pdf-page");
    pdfPage.type = "number";
    pdfPage.min = "1";
    pdfPage.step = "1";
    pdfPage.value = "1";
    label.appendChild(pdfPage);
    pageControls.appendChild(label);
    const previous = el("button", "outline-btn small", translate("wb.locator.pdfPrev"));
    previous.type = "button";
    const next = el("button", "outline-btn small", translate("wb.locator.pdfNext"));
    next.type = "button";
    previous.onclick = () => selectPdfPage(Number(pdfPage && pdfPage.value) - 1);
    next.onclick = () => selectPdfPage(Number(pdfPage && pdfPage.value) + 1);
    pdfPage.onchange = () => selectPdfPage(pdfPage && pdfPage.value);
    pageControls.appendChild(previous);
    pageControls.appendChild(next);
    box.appendChild(pageControls);
  }
  if (kind === "html") box.appendChild(selector);
  box.appendChild(quote);
  box.appendChild(comment);
  box.appendChild(save);
  box.appendChild(preview);
  container.appendChild(box);
  const endpoint = kind === "pdf" ? "pdf-text" : "html-outline";
  api(`/artifacts/${encodeURIComponent(a.id)}/${endpoint}`)
    .then((payload) => {
      const rec = payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
      if (kind === "pdf") {
        pdfPages = Array.isArray(rec.pages) ? (rec.pages as PdfPage[]) : [];
        selectPdfPage(pdfPage && pdfPage.value);
      } else {
        const elements = Array.isArray(rec.elements) ? (rec.elements as Array<Record<string, unknown>>) : [];
        preview.textContent = elements
          .map((item) => (item.selector || item.tag) + " " + (item.text || ""))
          .join("\n")
          .slice(0, 4000);
      }
    })
    .catch(() => {
      preview.textContent = "";
    });
  save.onclick = async () => {
    if (!currentId.value || !String(comment.value || "").trim()) return;
    save.disabled = true;
    try {
      await api(`/frames/${currentId.value}/annotations`, {
        method: "POST",
        body: JSON.stringify({
          artifact_id: a.id,
          artifact_name: a.filename,
          kind,
          body: comment.value,
          locator:
            kind === "pdf"
              ? { page: selectPdfPage(pdfPage && pdfPage.value), quote: quote.value }
              : { selector: selector.value, quote: quote.value },
        }),
      });
      comment.value = "";
      hint(translate("wb.locator.saved"));
      if (currentId.value) void loadAnnotations(currentId.value);
    } catch (error) {
      hint(translate("wb.locator.err", apiErrorText(error)), true);
    }
    save.disabled = false;
  };
}
