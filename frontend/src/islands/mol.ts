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
import { fetchArtifactText, isArtifactReadAborted, previewReadSignal } from "../features/artifacts/api";
import { filesT, readFailureText } from "../features/artifacts/copy";
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
  getRenderer?: () => { getContext?: () => WebGLRenderingContext | WebGL2RenderingContext | null };
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

type MolSession = {
  view: HTMLElement;
  controller: AbortController;
  viewer: MolViewer | null;
  detach: () => void;
};
/** Dock and fullscreen own separate reads and scenes. */
const molSessions = new Map<HTMLElement, MolSession>();

function syncMolBridge(): void {
  // Opening order, not asynchronous completion order, selects the bridge.
  const latest = [...molSessions.values()].filter((session) => session.viewer).at(-1);
  _molViewer.value = latest?.viewer ?? null;
  _molView.value = latest?.view ?? null;
}

export function molRetheme(dark: boolean): void {
  for (const session of molSessions.values()) {
    if (!session.view.isConnected || session.controller.signal.aborted) continue;
    try {
      session.viewer?.setBackgroundColor?.(dark ? "#1c1c19" : "white");
      session.viewer?.render();
    } catch {
      /* one disposed renderer must not prevent the other view updating */
    }
  }
}
/** The one in-flight load of the vendored script, shared by every caller. */
let molScript: Promise<boolean> | null = null;

function loadMolScript(): Promise<boolean> {
  if (!molScript) {
    molScript = new Promise<boolean>((resolve) => {
      // Vendored copy only. A missing local 3Dmol used to fall back to fetching
      // https://3Dmol.org/build/3Dmol-min.js, which executes third-party script in
      // the page that holds the session cookie -- and does it silently, on an app
      // whose whole premise is that it runs locally and makes no call the user did
      // not ask for. The degraded path (render the coordinates as text) was
      // already written; the CDN hop only stood between the failure and it.
      const s = el("script");
      s.src = MOL_VENDOR_SRC;
      s.onload = () => resolve(true);
      s.onerror = () => {
        // A later structure may try again; this one falls back to text.
        molScript = null;
        resolve(false);
      };
      document.head.appendChild(s);
    });
  }
  return molScript;
}

/* Release only the closing view's WebGL context. An omitted root closes all
   viewers for the legacy session-switch hook; null means no matching view. */
export function molTeardown(root?: Element | null): void {
  if (root === null) return;
  for (const [container, session] of molSessions) {
    if (root && container.isConnected && root !== container && !root.contains(container)) continue;
    molSessions.delete(container);
    session.detach();
    session.controller.abort();
    try {
      session.viewer?.clear?.();
    } catch {
      /* already gone */
    }
    try {
      const cvs = session.view.querySelector("canvas");
      // Current 3Dmol can render through an OffscreenCanvas; its visible
      // canvas then only has a bitmaprenderer, not the owning GL context.
      const gl = session.viewer?.getRenderer?.().getContext?.() ||
        cvs?.getContext("webgl2") || cvs?.getContext("webgl") || cvs?.getContext("experimental-webgl");
      const ext = gl && (gl as WebGLRenderingContext).getExtension("WEBGL_lose_context");
      // 3Dmol shares its offscreen context across renderers. Its remaining
      // viewers still need that context even after this scene was cleared.
      const shared = gl && [...molSessions.values()].some(
        (other) => other.viewer?.getRenderer?.().getContext?.() === gl,
      );
      if (ext && !shared) ext.loseContext();
    } catch {
      /* canvas gone */
    }
  }
  // Keep the compatibility bridge pointed at a surviving viewer.
  syncMolBridge();
}

/** app.js:9622-9673 — container-agnostic, style selector, atom count, download, label. */
export function molecule(container: HTMLElement, url: string, nm: string): void {
  molTeardown(container);
  const controller = new AbortController();
  const signal = controller.signal;
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
  const parentSignal = previewReadSignal(container);
  const onClose = () => molTeardown(container);
  const session: MolSession = {
    view, controller, viewer: null,
    detach: () => parentSignal?.removeEventListener("abort", onClose),
  };
  molSessions.set(container, session);
  parentSignal?.addEventListener("abort", onClose, { once: true });
  if (parentSignal?.aborted) {
    onClose();
    return;
  }
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
  // Superseded by a later structure, or its view was replaced meanwhile.
  const stale = (): boolean => signal.aborted || !view.isConnected;
  /** The bounded read failed (over limit / timeout / HTTP): say so, keep download. */
  const showFailure = (error: unknown): void => {
    if (stale() || isArtifactReadAborted(error)) return;
    view.innerHTML = "";
    const card = el("div", "renderer-fallback");
    const text = el("div", "renderer-fallback-text", readFailureText(error));
    text.setAttribute("role", "alert");
    card.appendChild(text);
    const again = el("button", "outline-btn small", filesT("viewer.read.retry"));
    again.onclick = () => {
      molecule(container, url, nm);
    };
    card.appendChild(again);
    const download = el("a", "outline-btn small", translate("common.download"));
    download.href = url;
    download.setAttribute("download", nm || "artifact");
    card.appendChild(download);
    view.appendChild(card);
  };
  const boot = (): Promise<void> => {
    if (stale()) return Promise.resolve();
    return fetchArtifactText(url, { signal })
      .then((data) => {
        if (stale()) return;
        try {
          const runtime = molApi();
          if (!runtime) {
            preFallback(view, data);
            return;
          }
          viewer = runtime.createViewer(view, {
            backgroundColor: themeIsDark() ? "#1c1c19" : "white",
          });
          session.viewer = viewer;
          syncMolBridge();
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
      .catch(showFailure);
  };
  const fb = (): Promise<void> => {
    if (stale()) return Promise.resolve();
    return fetchArtifactText(url, { signal })
      .then((text) => {
        if (stale()) return;
        preFallback(view, text);
      })
      .catch(showFailure);
  };
  if (molApi()) {
    void boot();
    return;
  }
  if (typeof document === "undefined" || !document.head) {
    void fb();
    return;
  }
  // Every structure opened before the script arrives waits on the same tag.
  void loadMolScript().then((loaded) => (loaded ? boot() : fb()));
}
