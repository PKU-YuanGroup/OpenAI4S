/**
 * Draggable sidebar / dock column widths. Port of app.js:13250-13319.
 * localStorage keys `os-side-w` / `os-dock-w` unchanged.
 */

import { tOptional } from "../../i18n/runtime";
import { $, el } from "./dom";

let _colClampBound = false;

/** app.js:13254-13259 */
export function restoreColWidths(): void {
  if (typeof document === "undefined") return;
  let side = "";
  let dock = "";
  try {
    side = localStorage.getItem("os-side-w") || "";
    dock = localStorage.getItem("os-dock-w") || "";
  } catch {
    // Blocked site data makes the storage getter itself throw SecurityError;
    // keep the stylesheet widths like layout, theme and i18n do.
    return;
  }
  const sw = parseInt(side, 10);
  if (sw && sw >= 200 && sw <= 520) {
    document.documentElement.style.setProperty("--side-w", sw + "px");
  }
  const dw = parseInt(dock, 10);
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
  // A static label, not t() read once: this runs from bootChrome() before the
  // locale chunks land, when t() answers with the key, and nothing wrote the
  // title again -- so the tooltip was "resizer.drag" on every load and never
  // followed a language switch. applyStaticI18n repaints it with the Shell.
  h.setAttribute("data-i18n-title", "resizer.drag");
  h.title = tOptional("resizer.drag") ?? "";
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
