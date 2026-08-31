/**
 * 3Dmol structure viewer. Port of app.js:9610-9673.
 *
 * Lazy script-tag injection of the vendored copy only. A missing local
 * 3Dmol used to fall back to fetching https://3Dmol.org/build/3Dmol-min.js,
 * which executes third-party script in the page that holds the session
 * cookie -- and does it silently, on an app whose whole premise is that it
 * runs locally and makes no call the user did not ask for. The degraded
 * path below (render the coordinates as text) was already written; the CDN
 * hop only stood between the failure and it.
 *
 * Do not static-import 3Dmol. Do not add a CDN fallback.
 */

import { _molView, _molViewer } from "../stores/artifacts";
import { esc } from "../features/md/esc";
import { themeIsDark } from "../features/theme/theme";
import { el } from "./dom";
import { translate } from "./host";

export const MOL_VENDOR_SRC = "/static/vendor/3Dmol-min.js";

type MolModel = {
  selectedAtoms?: (sel: Record<string, never>) => Array<{ atom?: string; name?: string }>;
};

type MolViewer = {
  clear?: () => void;
  setBackgroundColor?: (color: string) => void;
  addModel: (data: string, fmt: string) => MolModel;
  removeAllSurfaces?: () => void;
  setStyle: (sel: Record<string, unknown>, style: Record<string, unknown>) => void;
  addSurface?: (typ: unknown, opts: Record<string, unknown>) => void;
  zoomTo: () => void;
  render: () => void;
};

type Mol3D = {
  createViewer: (el: HTMLElement, opts: { backgroundColor: string }) => MolViewer;
  SurfaceType?: { VDW?: unknown };
};

function molApi(): Mol3D | undefined {
  const g = globalThis as unknown as { $3Dmol?: Mol3D; window?: { $3Dmol?: Mol3D } };
  return g.$3Dmol || (g.window && g.window.$3Dmol);
}

function preFallback(view: HTMLElement, text: string): void {
  view.innerHTML = "<pre style='padding:16px'>" + esc(text.slice(0, 8000)) + "</pre>";
}

/* Free the previous 3Dmol WebGL context before creating a new one (browsers cap
   live contexts at ~16; leaking one per structure viewed eventually blanks them). */
export function molTeardown(): void {
  try {
    const viewer = _molViewer.value as MolViewer | null;
    if (viewer && viewer.clear) viewer.clear();
  } catch {
    /* already gone */
  }
  try {
    const host = _molView.value as HTMLElement | null;
    const cvs = host && host.querySelector && host.querySelector("canvas");
    if (cvs) {
      const gl =
        (cvs as HTMLCanvasElement).getContext("webgl") ||
        (cvs as HTMLCanvasElement).getContext("experimental-webgl");
      const ext = gl && (gl as WebGLRenderingContext).getExtension("WEBGL_lose_context");
      if (ext) ext.loseContext();
    }
  } catch {
    /* canvas gone */
  }
  _molViewer.value = null;
  _molView.value = null;
}

/** app.js:9622-9673 — container-agnostic, style selector, atom count, download, label. */
export function molecule(container: HTMLElement, url: string, nm: string): void {
  molTeardown();
  container.innerHTML = "";
  const wrap = el("div", "mol-wrap");
  wrap.appendChild(el("div", "mol-tag", "Using 3Dmol.js viewer"));
  const bar = el("div", "mol-bar");
  bar.appendChild(el("span", "mol-lbl", "Style:"));
  let cur = "cartoon";
  const pills: Record<string, HTMLButtonElement> = {};
  (
    [
      ["cartoon", "Cartoon"],
      ["stick", "Stick"],
      ["sphere", "Sphere"],
      ["surface", "Surface"],
      ["line", "Line"],
    ] as Array<[string, string]>
  ).forEach(([val, lab]) => {
    const b = el("button", "mol-style" + (val === cur ? " on" : ""), lab);
    b.onclick = () => {
      cur = val;
      Object.values(pills).forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      applyStyle(val);
    };
    pills[val] = b;
    bar.appendChild(b);
  });
  const cnt = el("span", "mol-count", "");
  bar.appendChild(cnt);
  const view = el("div", "mol-view");
  const foot = el("div", "mol-foot", translate("mol.foot"));
  wrap.appendChild(bar);
  wrap.appendChild(view);
  wrap.appendChild(foot);
  container.appendChild(wrap);
  const fmt = nm.split(".").pop() || "pdb";
  let viewer: MolViewer | null = null;
  let caOnly = false;
  // For coarse / CA-only models (e.g. synthetic backbones) plain cartoon renders
  // nothing, so we draw a trace tube + CA spheres so the structure is never blank.
  const spec = (style: string): Record<string, unknown> =>
    style === "cartoon"
      ? caOnly
        ? { cartoon: { color: "spectrum", style: "trace" }, sphere: { colorscheme: "Jmol", radius: 0.5 } }
        : { cartoon: { color: "spectrum" } }
      : style === "stick"
        ? { stick: { colorscheme: "Jmol" } }
        : style === "sphere"
          ? { sphere: { colorscheme: "Jmol" } }
          : { line: { colorscheme: "Jmol" } };
  const applyStyle = (style: string): void => {
    if (!viewer) return;
    try {
      if (viewer.removeAllSurfaces) viewer.removeAllSurfaces();
    } catch {
      /* surface API optional */
    }
    const runtime = molApi();
    if (style === "surface") {
      viewer.setStyle({}, { cartoon: { color: "spectrum" } });
      try {
        if (viewer.addSurface && runtime && runtime.SurfaceType)
          viewer.addSurface(runtime.SurfaceType.VDW, { opacity: 0.85, color: "white" });
      } catch {
        /* surface optional */
      }
    } else viewer.setStyle({}, spec(style));
    viewer.setStyle({ hetflag: true }, { stick: { colorscheme: "Jmol" } });
    viewer.render();
  };
  const boot = (): Promise<void> =>
    fetch(url)
      .then((r) => r.text())
      .then((data) => {
        try {
          const runtime = molApi();
          if (!runtime) {
            preFallback(view, data);
            return;
          }
          viewer = runtime.createViewer(view, {
            backgroundColor: themeIsDark() ? "#1c1c19" : "white",
          });
          _molViewer.value = viewer;
          _molView.value = view;
          const model = viewer.addModel(data, fmt);
          let atoms: Array<{ atom?: string; name?: string }> = [];
          try {
            atoms = model.selectedAtoms ? model.selectedAtoms({}) : [];
          } catch {
            atoms = [];
          }
          const n = atoms.length;
          // detect a CA-only backbone trace (no full sidechains/backbone)
          try {
            const ca = atoms.filter((a) => a.atom === "CA" || a.name === "CA").length;
            caOnly = n > 0 && ca / n > 0.8;
          } catch {
            caOnly = false;
          }
          cnt.textContent = n ? n.toLocaleString() + " atoms" : "";
          applyStyle(cur);
          viewer.zoomTo();
          viewer.render();
        } catch {
          preFallback(view, data);
        }
      })
      .catch(() => {
        /* fetch failed; chrome still offers download */
      });
  const fb = (): Promise<void> =>
    fetch(url)
      .then((r) => r.text())
      .then((text) => {
        preFallback(view, text);
      })
      .catch(() => {
        /* nothing to show */
      });
  if (molApi()) {
    void boot();
    return;
  }
  // Vendored copy only. A missing local 3Dmol used to fall back to fetching
  // https://3Dmol.org/build/3Dmol-min.js, which executes third-party script in
  // the page that holds the session cookie -- and does it silently, on an app
  // whose whole premise is that it runs locally and makes no call the user did
  // not ask for. The degraded path below (render the coordinates as text) was
  // already written; the CDN hop only stood between the failure and it.
  if (typeof document === "undefined" || !document.head) {
    void fb();
    return;
  }
  const s = el("script");
  s.src = MOL_VENDOR_SRC;
  s.onload = () => {
    void boot();
  };
  s.onerror = () => {
    void fb();
  };
  document.head.appendChild(s);
}
