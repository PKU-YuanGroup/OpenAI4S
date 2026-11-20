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
import { ArtifactReadError, bytes, fetchArtifactText, isArtifactReadAborted, previewReadSignal } from "../features/artifacts/api";
import { filesT, readFailureText, readRetryHelps } from "../features/artifacts/copy";
import { esc } from "../features/md/esc";
import { themeIsDark } from "../features/theme/theme";
import { el } from "./dom";
import { translate } from "./host";
import { molT } from "./mol-copy";
import {
  B_GRADIENT_CSS,
  MOL_SCHEMES,
  PDB_FORMATS,
  chainColor,
  hexColor,
  labelAnchor,
  molStyleSpec,
  parsePdbHeader,
  residueKey,
  residueLabel,
  schemeAvailable,
  schemeColorFunc,
  structureFacts,
  type MolAtom,
  type MolScheme,
  type StructureFacts,
  type StructureHeader,
} from "./mol-facts";

export const MOL_VENDOR_SRC = "/static/vendor/3Dmol-min.js";

type MolModel = {
  selectedAtoms?: (sel: Record<string, never>) => MolAtom[];
};

/** A 3Dmol label, only ever handed back to `removeLabel`. */
type MolLabel = object;

type MolViewer = {
  clear?: () => void;
  getRenderer?: () => { getContext?: () => WebGLRenderingContext | WebGL2RenderingContext | null };
  setBackgroundColor?: (color: string) => void;
  addModel: (data: string, fmt: string) => MolModel;
  removeAllSurfaces?: () => void;
  setStyle: (sel: Record<string, unknown>, style: Record<string, unknown>) => void;
  addSurface?: (typ: unknown, opts: Record<string, unknown>) => void;
  setClickable?: (sel: Record<string, unknown>, clickable: boolean, callback: (atom: MolAtom) => void) => void;
  addLabel?: (text: string, options: Record<string, unknown>) => MolLabel;
  removeLabel?: (label: MolLabel) => void;
  removeAllLabels?: () => void;
  getView?: () => number[];
  setView?: (view: number[]) => void;
  zoomTo: () => void;
  render: () => void;
};

/** A dark chip with light text reads on the light and the dark background alike. */
const LABEL_STYLE = {
  fontSize: 13,
  fontColor: "white",
  backgroundColor: "#1c1c19",
  backgroundOpacity: 0.8,
  borderThickness: 0,
  inFront: true,
  alignment: "bottomCenter",
};
/** Chains listed by ID in the facts line and the legend; more are counted. */
const CHAIN_LIST_MAX = 8;

function chainSummary(chains: readonly string[]): string {
  const n = chains.length;
  if (!n) return "";
  if (n > CHAIN_LIST_MAX) return molT("mol.chains.many", n);
  const list = chains.join(molT("mol.chains.sep"));
  return n === 1 ? molT("mol.chains.one", list) : molT("mol.chains", n, list);
}

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

/**
 * app.js:9622-9673 — container-agnostic, style and colour selectors, reset
 * view, click-to-label residues, the PDB ID/title/chains line, atom count.
 */
/**
 * ART-2: the most a structure the reader asked to load anyway may weigh. The
 * automatic preview stops at the shared 8 MB budget; a 17 MB ribosome is still
 * a file a browser can draw as lines.
 */
export const MOL_FORCE_MAX_BYTES = 64 * 1024 * 1024;

export function molecule(container: HTMLElement, url: string, nm: string, force = false): void {
  molTeardown(container);
  const controller = new AbortController();
  const signal = controller.signal;
  container.innerHTML = "";
  const wrap = el("div", "mol-wrap");
  // What the structure is (PDB ID, title, chains, atoms) fills in once it is read.
  const meta = el("div", "mol-meta");
  const info = el("span", "mol-meta-info");
  meta.appendChild(info);
  meta.appendChild(el("span", "mol-tag", translate("mol.tag")));
  wrap.appendChild(meta);
  const bar = el("div", "mol-bar");
  bar.appendChild(el("span", "mol-lbl", translate("mol.styleLabel")));
  // A structure loaded past the preview budget opens as lines: the cheapest style.
  let cur = force ? "line" : "cartoon";
  let scheme: MolScheme = "default";
  const pills: Record<string, HTMLButtonElement> = {};
  ["cartoon", "stick", "sphere", "surface", "line"].forEach((val) => {
    const b = el("button", "mol-style" + (val === cur ? " on" : ""), translate("mol.style." + val));
    b.onclick = () => {
      cur = val;
      Object.values(pills).forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      applyStyle();
    };
    // A molecular surface over this many atoms can hang the tab.
    if (force && val === "surface") {
      b.disabled = true;
      b.title = molT("mol.large.noSurface");
    }
    pills[val] = b;
    bar.appendChild(b);
  });
  bar.appendChild(el("span", "mol-lbl mol-lbl-gap", molT("mol.color.label")));
  const schemePills = {} as Record<MolScheme, HTMLButtonElement>;
  MOL_SCHEMES.forEach((val) => {
    const b = el("button", "mol-style" + (val === scheme ? " on" : ""), molT("mol.color." + val));
    // Chain and B-factor colouring wait for the structure: a file may carry neither.
    b.disabled = val !== "default";
    b.onclick = () => {
      if (b.disabled) return;
      scheme = val;
      Object.values(schemePills).forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
      applyStyle();
      renderLegend();
    };
    schemePills[val] = b;
    bar.appendChild(b);
  });
  const legend = el("span", "mol-legend");
  legend.hidden = true;
  bar.appendChild(legend);
  const view = el("div", "mol-view");
  const foot = el("div", "mol-foot mol-foot-row");
  foot.appendChild(el("span", "mol-hint", molT("mol.hint")));
  const reset = el("button", "outline-btn small mol-act", molT("mol.reset"));
  reset.title = molT("mol.reset.title");
  reset.disabled = true;
  reset.onclick = () => resetView();
  foot.appendChild(reset);
  // Present (disabled) before the first label, so labelling never reflows the canvas.
  const clear = el("button", "outline-btn small mol-act", molT("mol.labels.clear"));
  clear.disabled = true;
  clear.onclick = () => clearLabels();
  foot.appendChild(clear);
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
  let atoms: MolAtom[] = [];
  let facts: StructureFacts = { chains: [], bRange: null };
  /** The view right after the first fit: what reset view returns to. */
  let home: number[] | null = null;
  const labels = new Map<string, MolLabel>();
  const applyStyle = (): void => {
    if (!viewer) return;
    try {
      if (viewer.removeAllSurfaces) viewer.removeAllSurfaces();
    } catch {
      /* surface API optional */
    }
    const runtime = molApi();
    const spec = molStyleSpec(cur, caOnly, schemeColorFunc(scheme, facts));
    viewer.setStyle({}, spec.style);
    if (spec.surface) {
      try {
        if (viewer.addSurface && runtime && runtime.SurfaceType)
          viewer.addSurface(runtime.SurfaceType.VDW, spec.surface);
      } catch {
        /* surface optional */
      }
    }
    // Ligands, ions and water keep element colours in every scheme.
    viewer.setStyle({ hetflag: true }, { stick: { colorscheme: "Jmol" } });
    viewer.render();
  };
  const renderLegend = (): void => {
    legend.innerHTML = "";
    legend.hidden = scheme === "default";
    legend.title = scheme === "bfactor" ? molT("mol.legend.bfactor") : "";
    if (scheme === "chain") {
      facts.chains.slice(0, CHAIN_LIST_MAX).forEach((chain, index) => {
        const key = el("span", "mol-key");
        const swatch = el("span", "mol-swatch");
        swatch.style.background = hexColor(chainColor(index));
        key.appendChild(swatch);
        key.appendChild(el("span", null, chain));
        legend.appendChild(key);
      });
      const more = facts.chains.length - CHAIN_LIST_MAX;
      if (more > 0) legend.appendChild(el("span", "mol-key", "+" + more));
    } else if (scheme === "bfactor" && facts.bRange) {
      const [lo, hi] = facts.bRange;
      legend.appendChild(el("span", null, lo.toFixed(1)));
      const gradient = el("span", "mol-grad");
      gradient.style.background = B_GRADIENT_CSS;
      legend.appendChild(gradient);
      legend.appendChild(el("span", null, hi.toFixed(1)));
    }
  };
  const showFacts = (header: StructureHeader | null, atomCount: number): void => {
    info.innerHTML = "";
    if (header && header.idCode) info.appendChild(el("span", "mol-meta-id", header.idCode));
    const title = header ? header.title || header.classification : "";
    if (title) info.appendChild(el("span", "mol-meta-title", title));
    const chains = chainSummary(facts.chains);
    if (chains) info.appendChild(el("span", "mol-meta-chains", chains));
    if (atomCount) {
      const count = molT(atomCount === 1 ? "mol.atoms.one" : "mol.atoms", atomCount.toLocaleString());
      info.appendChild(el("span", "mol-meta-atoms", count));
    }
    (["chain", "bfactor"] as const).forEach((val) => {
      const available = schemeAvailable(val, facts);
      schemePills[val].disabled = !available;
      schemePills[val].title = available ? "" : molT("mol.color." + val + ".none");
    });
  };
  const resetView = (): void => {
    if (!viewer || stale()) return;
    // The first fit's view brings back the orientation and pan; zoomTo()
    // then refits and restores the clipping slab.
    if (home && viewer.setView) viewer.setView([...home]);
    viewer.zoomTo();
    viewer.render();
  };
  const syncLabels = (): void => {
    clear.disabled = labels.size === 0;
    clear.textContent = labels.size
      ? molT("mol.labels.clearCount", labels.size)
      : molT("mol.labels.clear");
  };
  /** Click a residue to label it; click it again to remove the label. */
  const toggleLabel = (atom: MolAtom): void => {
    if (!viewer || stale() || !viewer.addLabel || !viewer.removeLabel) return;
    const key = residueKey(atom);
    const shown = labels.get(key);
    if (shown) {
      labels.delete(key);
      viewer.removeLabel(shown);
    } else {
      const position = labelAnchor(atoms, atom);
      if (!position) return;
      labels.set(key, viewer.addLabel(residueLabel(atom), { ...LABEL_STYLE, position }));
    }
    syncLabels();
  };
  const clearLabels = (): void => {
    if (!viewer) return;
    if (viewer.removeAllLabels) viewer.removeAllLabels();
    else labels.forEach((label) => viewer?.removeLabel?.(label));
    labels.clear();
    syncLabels();
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
    if (readRetryHelps(error)) {
      const again = el("button", "outline-btn small", filesT("viewer.read.retry"));
      again.onclick = () => {
        molecule(container, url, nm, force);
      };
      card.appendChild(again);
    } else if (!force && error instanceof ArtifactReadError && (error.declared === null || error.declared <= MOL_FORCE_MAX_BYTES)) {
      // Over the automatic budget, under the ceiling: the reader may choose.
      const anyway = el(
        "button",
        "outline-btn small",
        error.declared === null ? molT("mol.large.load") : molT("mol.large.loadSize", bytes(error.declared)),
      );
      anyway.title = molT("mol.large.title");
      anyway.onclick = () => {
        molecule(container, url, nm, true);
      };
      card.appendChild(anyway);
    }
    const download = el("a", "outline-btn small", translate("common.download"));
    download.href = url;
    download.setAttribute("download", nm || "artifact");
    card.appendChild(download);
    view.appendChild(card);
  };
  const boot = (): Promise<void> => {
    if (stale()) return Promise.resolve();
    return fetchArtifactText(url, force ? { signal, maxBytes: MOL_FORCE_MAX_BYTES } : { signal })
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
          facts = structureFacts(atoms);
          // The header is parsed from the text already read, not fetched again.
          showFacts(PDB_FORMATS.has(fmt) ? parsePdbHeader(data) : null, n);
          applyStyle();
          try {
            if (viewer.setClickable && viewer.addLabel && viewer.removeLabel)
              viewer.setClickable({}, true, toggleLabel);
          } catch {
            /* labels are optional; the structure still renders */
          }
          viewer.zoomTo();
          try {
            home = viewer.getView ? viewer.getView() : null;
          } catch {
            home = null;
          }
          reset.disabled = false;
          viewer.render();
        } catch {
          preFallback(view, data);
        }
      })
      .catch(showFailure);
  };
  const fb = (): Promise<void> => {
    if (stale()) return Promise.resolve();
    return fetchArtifactText(url, force ? { signal, maxBytes: MOL_FORCE_MAX_BYTES } : { signal })
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
