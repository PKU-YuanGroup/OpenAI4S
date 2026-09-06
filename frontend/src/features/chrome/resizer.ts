/**
 * Draggable sidebar / dock column widths. Port of app.js:13250-13319.
 * localStorage keys `os-side-w` / `os-dock-w` unchanged.
 */

import { t } from "../../i18n/runtime";
import { $, el } from "./dom";

let _colClampBound = false;

/** app.js:13254-13259 */
export function restoreColWidths(): void {
  if (typeof document === "undefined") return;
  const sw = parseInt(localStorage.getItem("os-side-w") || "", 10);
  if (sw && sw >= 200 && sw <= 520) {
    document.documentElement.style.setProperty("--side-w", sw + "px");
  }
  const dw = parseInt(localStorage.getItem("os-dock-w") || "", 10);
  if (dw && dw >= 360) {
    const inner = typeof window !== "undefined" ? window.innerWidth : 1200;
    document.documentElement.style.setProperty(
      "--dock-w",
      Math.min(dw, Math.max(360, inner - 360)) + "px",
    );
  }
}

/** app.js:13260-13276 */
export function initColResizers(): void {
  if (typeof document === "undefined") return;
  const main = $("#main");
  const dock = $("#rightdock");
  if (main && !main.querySelector(".col-resizer-side")) makeColResizer(main, "side");
  if (dock && !dock.querySelector(".col-resizer-dock")) makeColResizer(dock, "dock");
  if (!_colClampBound) {
    _colClampBound = true;
    window.addEventListener("resize", () => {
      const cs = getComputedStyle(document.documentElement);
      const dw = parseInt(cs.getPropertyValue("--dock-w"), 10);
      if (dw) {
        document.documentElement.style.setProperty(
          "--dock-w",
          Math.max(360, Math.min(dw, window.innerWidth - 360)) + "px",
        );
      }
      const sw = parseInt(cs.getPropertyValue("--side-w"), 10);
      if (sw) {
        document.documentElement.style.setProperty(
          "--side-w",
          Math.max(200, Math.min(sw, Math.max(200, window.innerWidth * 0.4))) + "px",
        );
      }
    });
  }
}

/** app.js:13277-13319 */
function makeColResizer(host: HTMLElement, kind: "side" | "dock"): void {
  const h = el("div", "col-resizer col-resizer-" + kind);
  h.title = t("resizer.drag");
  host.appendChild(h);
  let startX = 0;
  let curW = 0;
  let curW0 = 0;
  const apply = (w: number) => {
    if (kind === "side") {
      curW = Math.max(200, Math.min(520, w));
      document.documentElement.style.setProperty("--side-w", curW + "px");
    } else {
      const cap = Math.min(
        window.innerWidth - 360,
        window.innerWidth <= 1180 ? window.innerWidth * 0.6 : Infinity,
      );
      curW = Math.max(360, Math.min(cap, w));
      document.documentElement.style.setProperty("--dock-w", curW + "px");
    }
  };
  const onMove = (e: PointerEvent) => {
    const dx = e.clientX - startX;
    apply(kind === "side" ? curW0 + dx : curW0 - dx);
  };
  const onUp = () => {
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.removeEventListener("pointercancel", onUp);
    document.body.classList.remove("col-resizing");
    h.classList.remove("active");
    try {
      localStorage.setItem(kind === "side" ? "os-side-w" : "os-dock-w", String(Math.round(curW)));
    } catch {
      /* private-mode */
    }
    try {
      window.dispatchEvent(new Event("resize"));
    } catch {
      /* ignore */
    }
  };
  h.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (kind === "side" && document.body.classList.contains("sidebar-collapsed")) return;
    e.preventDefault();
    startX = e.clientX;
    const measure = kind === "side" ? $("#sidebar") : host;
    curW0 = curW = measure ? measure.getBoundingClientRect().width : 0;
    document.body.classList.add("col-resizing");
    h.classList.add("active");
    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onUp);
  });
}

export function resetColClampBound(): void {
  _colClampBound = false;
}
