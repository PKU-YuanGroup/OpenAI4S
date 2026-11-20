import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { _molView } from "../stores/artifacts";
import { resetStoreFields } from "../stores/signal-field";
import { filesT, readFailureText } from "../features/artifacts/copy";
import { abortPreviewReads, beginPreviewRead } from "../features/artifacts/api";
import { LANG, setLang } from "../i18n/runtime";
import { translate } from "./host";
import { MOL_FORCE_MAX_BYTES, MOL_VENDOR_SRC, molRetheme, molTeardown, molecule } from "./mol";
import { molT } from "./mol-copy";
import { B_HIGH, B_LOW, chainColor, hexColor, type MolAtom } from "./mol-facts";

const here = dirname(fileURLToPath(import.meta.url));
const artifactsDir = join(here, "../features/artifacts");

const CDN = "https://3Dmol.org/build/3Dmol-min.js";
const STATIC_IMPORT = /(?:from|import)\s*\(?\s*['"][^'"]*3dmol[^'"]*['"]/i;
const REQUIRE_3DMOL = /require\s*\(\s*['"][^'"]*3dmol[^'"]*['"]/i;

function codeLines(src: string): string[] {
  return src.split("\n").filter((line) => {
    const stripped = line.trim();
    return stripped !== "" && !stripped.startsWith("//") && !stripped.startsWith("*");
  });
}

function collectLaneSources(): Array<{ path: string; src: string }> {
  const files: Array<{ path: string; src: string }> = [];
  for (const name of readdirSync(here)) {
    if (!name.endsWith(".ts") || name.endsWith(".test.ts")) continue;
    files.push({ path: join(here, name), src: readFileSync(join(here, name), "utf8") });
  }
  files.push({
    path: join(artifactsDir, "renderers.ts"),
    src: readFileSync(join(artifactsDir, "renderers.ts"), "utf8"),
  });
  return files;
}

describe("3Dmol lazy injection (app.js:9665-9672)", () => {
  it("loads only the vendored script URL", () => {
    expect(MOL_VENDOR_SRC).toBe("/static/vendor/3Dmol-min.js");
  });

  it("keeps the deleted-CDN safety comment next to the script tag", () => {
    const src = readFileSync(join(here, "mol.ts"), "utf8");
    expect(src).toContain(CDN);
    expect(src).toContain('s.src = MOL_VENDOR_SRC');
    expect(src).toContain("Vendored copy only");
    const inject = src.slice(src.indexOf("Vendored copy only"));
    expect(inject).toContain("document.head.appendChild(s)");
    expect(inject).toContain("el(\"script\")");
  });

  it("has no static import of 3Dmol and no live CDN URL in lane sources", () => {
    const files = collectLaneSources();
    const offenders: string[] = [];
    for (const file of files) {
      if (STATIC_IMPORT.test(file.src) || REQUIRE_3DMOL.test(file.src)) {
        offenders.push(`${file.path}: static import/require of 3Dmol`);
      }
      for (const line of codeLines(file.src)) {
        if (line.includes(CDN) || /https?:\/\/3[Dd]mol\.org/.test(line)) {
          offenders.push(`${file.path}: live CDN URL: ${line.trim()}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

describe("3Dmol viewer lifetimes (AUDIT A37)", () => {
  class FakeEl {
    children: FakeEl[] = [];
    parent: FakeEl | null = null;
    page = false;
    className = "";
    textContent = "";
    src = "";
    style: Record<string, string> = {};
    classList = { add() {}, remove() {} };
    attrs: Record<string, string> = {};
    onclick: (() => void) | null = null;
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    constructor(readonly tagName = "div") {}
    get isConnected(): boolean {
      return this.page || (!!this.parent && this.parent.isConnected);
    }
    set innerHTML(_value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
    }
    appendChild(child: FakeEl): FakeEl {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(name: string, value: string): void {
      this.attrs[name] = value;
    }
    remove(): void {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
    contains(other: FakeEl): boolean {
      return this === other || this.children.some((child) => child.contains(other));
    }
    querySelector(): FakeEl | null {
      return this.children.find((node) => node.tagName === "canvas") ?? null;
    }
  }
  const walk = (node: FakeEl): FakeEl[] => [node, ...node.children.flatMap(walk)];
  const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

  function setup(sharedContext = false) {
    resetStoreFields();
    const head = new FakeEl("head");
    head.page = true;
    vi.stubGlobal("document", { head, createElement: (tag: string) => new FakeEl(tag) });
    const page = new FakeEl();
    page.page = true;
    const views: FakeEl[] = [];
    const sharedLoseContext = vi.fn();
    const sharedGl = { getExtension: () => ({ loseContext: sharedLoseContext }) };
    const runtime = {
      createViewer: vi.fn((view: FakeEl) => {
        views.push(view);
        const loseContext = sharedContext ? sharedLoseContext : vi.fn();
        const gl = sharedContext ? sharedGl : { getExtension: () => ({ loseContext }) };
        view.appendChild(Object.assign(new FakeEl("canvas"), {
          getContext: () => null, // 3Dmol may own an offscreen WebGL2 context.
        }));
        return {
          addModel: () => ({ selectedAtoms: () => [] }), setStyle() {}, zoomTo() {}, render: vi.fn(), clear: vi.fn(), loseContext,
          setBackgroundColor: vi.fn(),
          getRenderer: () => ({ getContext: () => gl }),
        };
      }),
    };
    let releaseA!: () => void;
    const heldA = new Promise<void>((resolve) => { releaseA = resolve; });
    const fetch = vi.fn(async (url: string) => {
      if (url === "/a.pdb") await heldA;
      return new Response("ATOM      1  CA  ALA A   1       0.000   0.000   0.000\n");
    });
    vi.stubGlobal("fetch", fetch);
    const host = (node: FakeEl) => node as unknown as HTMLElement;
    return { head, page, views, runtime, releaseA, fetch, host };
  }

  afterEach(() => {
    abortPreviewReads();
    molTeardown();
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("lets only the latest structure in a container create its viewer", async () => {
    const { page, views, runtime, releaseA, host } = setup();
    vi.stubGlobal("$3Dmol", runtime);
    const first = page.appendChild(new FakeEl());
    const second = first;
    beginPreviewRead(host(first));
    molecule(host(first), "/a.pdb", "a.pdb");
    molecule(host(second), "/b.pdb", "b.pdb");
    await vi.waitFor(() => expect(views).toHaveLength(1));
    releaseA();
    for (let i = 0; i < 3; i++) await settle();
    expect(views).toHaveLength(1);
    expect(walk(second)).toContain(views[0]);
    expect(_molView.value).toBe(views[0]);
  });

  it.each([false, true])("keeps dock and fullscreen alive with shared context = %s", async (shared) => {
    const { page, views, runtime, releaseA, host } = setup(shared);
    vi.stubGlobal("$3Dmol", runtime);
    const dock = page.appendChild(new FakeEl());
    const modal = page.appendChild(new FakeEl());
    const modalBody = modal.appendChild(new FakeEl());
    beginPreviewRead(host(modal));
    molecule(host(dock), "/a.pdb", "a.pdb");
    molecule(host(modalBody), "/b.pdb", "b.pdb");
    await vi.waitFor(() => expect(views).toHaveLength(1));
    releaseA();
    await vi.waitFor(() => expect(views).toHaveLength(2));
    const modalViewer = runtime.createViewer.mock.results[0]!.value;
    const dockViewer = runtime.createViewer.mock.results[1]!.value;
    // The dock completed last, but the modal was the last view opened.
    expect(_molView.value).toBe(views[0]);
    expect(modalViewer.clear).not.toHaveBeenCalled();
    expect(dockViewer.clear).not.toHaveBeenCalled();
    molRetheme(true);
    expect(modalViewer.setBackgroundColor).toHaveBeenLastCalledWith("#1c1c19");
    expect(dockViewer.setBackgroundColor).toHaveBeenLastCalledWith("#1c1c19");
    abortPreviewReads(host(modal));
    expect(modalViewer.clear).toHaveBeenCalledTimes(1);
    expect(modalViewer.loseContext).toHaveBeenCalledTimes(shared ? 0 : 1);
    expect(dockViewer.clear).not.toHaveBeenCalled();
    expect(dockViewer.loseContext).not.toHaveBeenCalled();
    expect(_molView.value).toBe(views[1]);
    molRetheme(false);
    expect(dockViewer.setBackgroundColor).toHaveBeenLastCalledWith("white");
    expect(modalViewer.setBackgroundColor).toHaveBeenCalledTimes(1);
    molTeardown(host(dock));
    expect(dockViewer.clear).toHaveBeenCalledTimes(1);
    expect(dockViewer.loseContext).toHaveBeenCalledTimes(1);
    expect(_molView.value).toBeNull();
  });

  it("creates no viewer for a structure whose view was replaced", async () => {
    const { page, views, runtime, releaseA, host } = setup();
    vi.stubGlobal("$3Dmol", runtime);
    const first = page.appendChild(new FakeEl());
    molecule(host(first), "/a.pdb", "a.pdb");
    first.remove();
    releaseA();
    for (let i = 0; i < 3; i++) await settle();
    expect(views).toHaveLength(0);
    expect(_molView.value).toBeNull();
  });

  it("aborts the structure read when the viewer is torn down", async () => {
    const { page, runtime, host } = setup();
    vi.stubGlobal("$3Dmol", runtime);
    let aborted = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async (_url: string, init?: RequestInit) =>
          new Response(
            new ReadableStream<Uint8Array>({
              start(controller) {
                init?.signal?.addEventListener("abort", () => {
                  aborted += 1;
                  controller.error(new DOMException("aborted", "AbortError"));
                });
              },
              pull() {
                /* waits for the abort */
              },
            }),
          ),
      ),
    );
    const first = page.appendChild(new FakeEl());
    molecule(host(first), "/a.pdb", "a.pdb");
    await vi.waitFor(() => expect(vi.mocked(globalThis.fetch)).toHaveBeenCalledTimes(1));
    molTeardown();
    await vi.waitFor(() => expect(aborted).toBe(1));
  });

  it("shows the read failure instead of feeding an error page to the viewer", async () => {
    const { page, runtime, host } = setup();
    const createViewer = vi.fn(runtime.createViewer);
    vi.stubGlobal("$3Dmol", { ...runtime, createViewer });
    let missing = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        missing
          ? new Response("missing", { status: 404 })
          : new Response("ATOM      1  CA  ALA A   1       1.000   2.000   3.000\n"),
      ),
    );
    const first = page.appendChild(new FakeEl());
    molecule(host(first), "/missing.pdb", "missing.pdb");
    await vi.waitFor(() =>
      expect(walk(first).some((node) => node.className === "renderer-fallback")).toBe(true),
    );
    // The error body is never parsed as structure data.
    expect(createViewer).not.toHaveBeenCalled();
    const card = walk(first).find((node) => node.className === "renderer-fallback")!;
    expect(walk(card).map((node) => node.textContent)).toContain(
      filesT("viewer.read.failedStatus", 404),
    );
    expect(readFailureText(new Error("x"))).toBe(filesT("viewer.read.failed"));
    const retry = walk(card).find(
      (node) => node.tagName === "button" && node.textContent === filesT("viewer.read.retry"),
    );
    expect(retry).toBeTruthy();
    missing = false;
    retry!.onclick?.();
    await vi.waitFor(() => expect(createViewer).toHaveBeenCalledTimes(1));
  });

  // ART-2: a 17 MB mmCIF got "over the 8.0 MB preview limit" with Retry and
  // Download. Retry cannot change a size; the reader may load it as lines.
  it("offers Load anyway, not Retry, for a structure over the preview limit", async () => {
    const { page, runtime, host } = setup();
    const createViewer = vi.fn(runtime.createViewer);
    vi.stubGlobal("$3Dmol", { ...runtime, createViewer });
    const seventeenMb = 17 * 1024 * 1024;
    const answer = (length: number) =>
      new Response("ATOM      1  CA  ALA A   1       1.000   2.000   3.000\n", {
        headers: { "content-length": String(length) },
      });
    let declared = seventeenMb;
    vi.stubGlobal("fetch", vi.fn(async () => answer(declared)));
    const first = page.appendChild(new FakeEl());
    molecule(host(first), "/5l3p.cif", "5l3p.cif");
    await vi.waitFor(() =>
      expect(walk(first).some((node) => node.className === "renderer-fallback")).toBe(true),
    );
    const buttons = () =>
      walk(first).filter((node) => node.tagName === "button").map((node) => node.textContent);
    expect(createViewer).not.toHaveBeenCalled();
    expect(buttons()).not.toContain(filesT("viewer.read.retry"));
    expect(buttons()).toContain(molT("mol.large.loadSize", "17.0 MB"));
    walk(first)
      .find((node) => node.textContent === molT("mol.large.loadSize", "17.0 MB"))!
      .onclick?.();
    await vi.waitFor(() => expect(createViewer).toHaveBeenCalledTimes(1));
    // Loaded as lines, and the surface style is off for a structure this size.
    const pill = (label: string) =>
      walk(first).find((node) => node.tagName === "button" && node.textContent === label)!;
    expect(pill(translate("mol.style.line")).className).toBe("mol-style on");
    expect(pill(translate("mol.style.cartoon")).className).toBe("mol-style");
    expect((pill(translate("mol.style.surface")) as unknown as { disabled?: boolean }).disabled).toBe(true);

    // Past the hard ceiling there is nothing to offer but the download.
    declared = MOL_FORCE_MAX_BYTES + 1;
    const second = page.appendChild(new FakeEl());
    molecule(host(second), "/huge.cif", "huge.cif");
    await vi.waitFor(() =>
      expect(walk(second).some((node) => node.className === "renderer-fallback")).toBe(true),
    );
    const offered = walk(second).filter((node) => node.tagName === "button").map((node) => node.textContent);
    expect(offered.some((text) => String(text).startsWith(molT("mol.large.load")))).toBe(false);
    expect(offered).not.toContain(filesT("viewer.read.retry"));
  });

  it("aborts the previous structure's read when its container opens a new structure", async () => {
    const { page, runtime, host } = setup();
    vi.stubGlobal("$3Dmol", runtime);
    let aborted = 0;
    const signals: AbortSignal[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            const signal = init?.signal;
            if (!signal) {
              reject(new Error("no abort signal"));
              return;
            }
            signals.push(signal);
            signal.addEventListener("abort", () => {
              aborted += 1;
              reject(new DOMException("aborted", "AbortError"));
            });
          }),
      ),
    );
    const first = page.appendChild(new FakeEl());
    const second = first;
    molecule(host(first), "/a.pdb", "a.pdb");
    await vi.waitFor(() => expect(signals.length).toBe(1));
    molecule(host(second), "/b.pdb", "b.pdb");
    await vi.waitFor(() => expect(signals.length).toBe(2));
    expect(aborted).toBe(1);
    expect(signals[0]!.aborted).toBe(true);
    expect(signals[1]!.aborted).toBe(false);
  });

  it("injects the vendored script once for structures opened before it loads", async () => {
    const { head, page, views, runtime, fetch, releaseA, host } = setup();
    const first = page.appendChild(new FakeEl());
    const second = page.appendChild(new FakeEl());
    molecule(host(first), "/a.pdb", "a.pdb");
    molecule(host(second), "/b.pdb", "b.pdb");
    const scripts = head.children.filter((node) => node.tagName === "script");
    expect(scripts).toHaveLength(1);
    expect(scripts[0]!.src).toBe(MOL_VENDOR_SRC);
    vi.stubGlobal("$3Dmol", runtime);
    scripts[0]!.onload?.();
    releaseA();
    await vi.waitFor(() => expect(views).toHaveLength(2));
    for (let i = 0; i < 3; i++) await settle();
    expect(views).toHaveLength(2);
    expect(views.filter((view) => walk(second).includes(view))).toHaveLength(1);
    expect(views.filter((view) => walk(first).includes(view))).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});

describe("3Dmol viewer controls (ART-6)", () => {
  /** An inert element that keeps the class list, state and children the controls touch. */
  class Node {
    children: Node[] = [];
    parent: Node | null = null;
    page = false;
    className = "";
    textContent = "";
    title = "";
    disabled = false;
    hidden = false;
    style: Record<string, string> = {};
    onclick: (() => void) | null = null;
    classList = {
      add: (name: string) => {
        if (!this.classes().includes(name)) this.className = `${this.className} ${name}`.trim();
      },
      remove: (name: string) => {
        this.className = this.classes().filter((c) => c !== name).join(" ");
      },
    };
    constructor(readonly tagName = "div") {}
    classes(): string[] {
      return this.className.split(/\s+/).filter(Boolean);
    }
    get isConnected(): boolean {
      return this.page || (!!this.parent && this.parent.isConnected);
    }
    set innerHTML(_value: string) {
      for (const child of this.children) child.parent = null;
      this.children = [];
    }
    appendChild(child: Node): Node {
      child.parent = this;
      this.children.push(child);
      return child;
    }
    setAttribute(): void {}
    contains(other: Node): boolean {
      return this === other || this.children.some((child) => child.contains(other));
    }
    querySelector(): Node | null {
      return null;
    }
  }
  const all = (node: Node): Node[] => [node, ...node.children.flatMap(all)];

  const PDB = [
    "HEADER    FLUORESCENT PROTEIN                     01-AUG-96   1EMA              ",
    "TITLE     GREEN FLUORESCENT PROTEIN FROM AEQUOREA VICTORIA                      ",
    "ATOM      1  N   GLY A  65       0.000   0.000   0.000  1.00 10.00           N",
  ].join("\n");
  const ATOMS: MolAtom[] = [
    { atom: "N", resn: "GLY", resi: 65, chain: "A", b: 10, x: 0, y: 0, z: 0 },
    { atom: "CA", resn: "GLY", resi: 65, chain: "A", b: 12, x: 1, y: 2, z: 3 },
    { atom: "CA", resn: "TYR", resi: 66, chain: "B", b: 50, x: 4, y: 5, z: 6 },
    { atom: "ZN", resn: "ZN", resi: 301, chain: "A", hetflag: true, b: 90, x: 7, y: 7, z: 7 },
  ];

  function mount(atoms: MolAtom[], name = "1ema.pdb", text = PDB) {
    resetStoreFields();
    const head = new Node("head");
    head.page = true;
    vi.stubGlobal("document", { head, createElement: (tag: string) => new Node(tag) });
    let onClick: ((atom: MolAtom) => void) | null = null;
    const viewer = {
      addModel: vi.fn(() => ({ selectedAtoms: () => atoms })),
      setStyle: vi.fn(),
      addSurface: vi.fn(),
      removeAllSurfaces: vi.fn(),
      zoomTo: vi.fn(),
      render: vi.fn(),
      clear: vi.fn(),
      setBackgroundColor: vi.fn(),
      getRenderer: () => ({ getContext: () => null }),
      setClickable: vi.fn((_sel: unknown, _on: boolean, callback: (atom: MolAtom) => void) => {
        onClick = callback;
      }),
      addLabel: vi.fn((label: string, options: Record<string, unknown>) => ({ label, options })),
      removeLabel: vi.fn(),
      removeAllLabels: vi.fn(),
      getView: vi.fn(() => [1, 2, 3, -40, 0, 0, 0, 1]),
      setView: vi.fn(),
    };
    vi.stubGlobal("$3Dmol", { createViewer: vi.fn(() => viewer), SurfaceType: { VDW: "vdw" } });
    const fetch = vi.fn(async () => new Response(text));
    vi.stubGlobal("fetch", fetch);
    const page = new Node();
    page.page = true;
    const container = page.appendChild(new Node());
    molecule(container as unknown as HTMLElement, "/a/" + name, name);
    const find = (cls: string): Node[] => all(container).filter((node) => node.classes().includes(cls));
    const [reset, clear] = find("mol-act") as [Node, Node];
    return {
      viewer,
      fetch,
      find,
      reset,
      clear,
      click: (atom: MolAtom) => onClick?.(atom),
      loaded: () => vi.waitFor(() => expect(reset.disabled).toBe(false)),
    };
  }
  /** The style last applied to every atom (the hetero override is a second call). */
  const mainStyle = (viewer: { setStyle: ReturnType<typeof vi.fn> }) =>
    viewer.setStyle.mock.calls.filter(([sel]) => Object.keys(sel as object).length === 0).at(-1)![1] as Record<
      string,
      { colorfunc?: (atom: MolAtom) => number }
    >;

  afterEach(() => {
    molTeardown();
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("shows the PDB ID, title, chains and atom count from the text it already read", async () => {
    const { fetch, find, loaded } = mount(ATOMS);
    await loaded();
    expect(find("mol-meta-id")[0]!.textContent).toBe("1EMA");
    expect(find("mol-meta-title")[0]!.textContent).toBe("GREEN FLUORESCENT PROTEIN FROM AEQUOREA VICTORIA");
    // The zinc is hetero: it neither adds a chain nor decides the B-factor range.
    expect(find("mol-meta-chains")[0]!.textContent).toBe(molT("mol.chains", 2, "A" + molT("mol.chains.sep") + "B"));
    expect(find("mol-meta-atoms")[0]!.textContent).toBe(molT("mol.atoms", (4).toLocaleString()));
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("resets to the view of the first fit, then refits", async () => {
    const { viewer, reset, loaded } = mount(ATOMS);
    expect(reset.disabled).toBe(true);
    reset.onclick?.();
    expect(viewer.setView).not.toHaveBeenCalled();
    await loaded();
    expect(viewer.getView).toHaveBeenCalledTimes(1);
    expect(viewer.getView.mock.invocationCallOrder[0]).toBeGreaterThan(viewer.zoomTo.mock.invocationCallOrder[0]!);
    reset.onclick?.();
    expect(viewer.setView).toHaveBeenCalledWith([1, 2, 3, -40, 0, 0, 0, 1]);
    expect(viewer.zoomTo).toHaveBeenCalledTimes(2);
    expect(viewer.setView.mock.invocationCallOrder[0]).toBeLessThan(viewer.zoomTo.mock.invocationCallOrder[1]!);
    expect(viewer.render.mock.invocationCallOrder.at(-1)).toBeGreaterThan(viewer.zoomTo.mock.invocationCallOrder[1]!);
  });

  it("labels a clicked residue at its CA, removes it on a second click, and clears all", async () => {
    const { viewer, clear, click, loaded } = mount(ATOMS);
    await loaded();
    expect(viewer.setClickable).toHaveBeenCalledWith({}, true, expect.any(Function));
    expect(clear.disabled).toBe(true);
    expect(clear.textContent).toBe(molT("mol.labels.clear"));
    click(ATOMS[0]!);
    expect(viewer.addLabel).toHaveBeenCalledWith(
      "GLY 65 · A",
      expect.objectContaining({ position: { x: 1, y: 2, z: 3 } }),
    );
    expect(clear.disabled).toBe(false);
    expect(clear.textContent).toBe(molT("mol.labels.clearCount", 1));
    // Another atom of the same residue toggles the same label off.
    click(ATOMS[1]!);
    expect(viewer.removeLabel).toHaveBeenCalledWith(viewer.addLabel.mock.results[0]!.value);
    expect(clear.disabled).toBe(true);
    click(ATOMS[0]!);
    click(ATOMS[2]!);
    expect(viewer.addLabel).toHaveBeenLastCalledWith("TYR 66 · B", expect.anything());
    expect(clear.textContent).toBe(molT("mol.labels.clearCount", 2));
    clear.onclick?.();
    expect(viewer.removeAllLabels).toHaveBeenCalledTimes(1);
    expect(clear.disabled).toBe(true);
    click(ATOMS[0]!);
    expect(viewer.addLabel).toHaveBeenCalledTimes(4);
  });

  it("colours by chain and by B-factor with a legend; default restores the original look", async () => {
    const { viewer, find, loaded } = mount(ATOMS);
    const pills = find("mol-style");
    const [def, byChain, byB] = pills.slice(5) as [Node, Node, Node];
    expect(byChain.disabled).toBe(true);
    await loaded();
    expect(mainStyle(viewer)).toEqual({ cartoon: { color: "spectrum" } });
    expect(byChain.disabled).toBe(false);
    expect(byB.disabled).toBe(false);
    const legend = find("mol-legend")[0]!;
    expect(legend.hidden).toBe(true);
    byChain.onclick?.();
    expect(mainStyle(viewer).cartoon!.colorfunc!(ATOMS[2]!)).toBe(chainColor(1));
    expect(viewer.setStyle).toHaveBeenLastCalledWith({ hetflag: true }, { stick: { colorscheme: "Jmol" } });
    expect(byChain.classes()).toContain("on");
    expect(def.classes()).not.toContain("on");
    expect(legend.hidden).toBe(false);
    expect(all(legend).filter((node) => node.className === "mol-swatch").map((node) => node.style.background)).toEqual([
      hexColor(chainColor(0)),
      hexColor(chainColor(1)),
    ]);
    byB.onclick?.();
    expect(mainStyle(viewer).cartoon!.colorfunc!(ATOMS[0]!)).toBe(B_LOW);
    expect(mainStyle(viewer).cartoon!.colorfunc!(ATOMS[2]!)).toBe(B_HIGH);
    expect(all(legend).map((node) => node.textContent)).toEqual(expect.arrayContaining(["10.0", "50.0"]));
    expect(legend.title).toBe(molT("mol.legend.bfactor"));
    pills[3]!.onclick?.();
    expect(viewer.addSurface).toHaveBeenLastCalledWith("vdw", { opacity: 0.85, colorfunc: expect.any(Function) });
    def.onclick?.();
    expect(mainStyle(viewer)).toEqual({ cartoon: { color: "spectrum" } });
    expect(viewer.addSurface).toHaveBeenLastCalledWith("vdw", { opacity: 0.85, color: "white" });
    expect(legend.hidden).toBe(true);
  });

  it("keeps chain and B-factor colouring off for a small molecule and says why", async () => {
    const methane: MolAtom[] = [
      { elem: "C", atom: "C", hetflag: true, index: 0, x: 0, y: 0, z: 0 },
      { elem: "H", atom: "H", hetflag: true, index: 1, x: 0.6, y: 0.6, z: 0.6 },
    ];
    const { viewer, find, click, loaded } = mount(methane, "methane.xyz", "HEADER    NOT A PDB FILE\n2\nmethane\n");
    await loaded();
    const [, byChain, byB] = find("mol-style").slice(5) as [Node, Node, Node];
    expect(byChain.disabled).toBe(true);
    expect(byChain.title).toBe(molT("mol.color.chain.none"));
    expect(byB.disabled).toBe(true);
    expect(byB.title).toBe(molT("mol.color.bfactor.none"));
    const applied = viewer.setStyle.mock.calls.length;
    byChain.onclick?.();
    expect(viewer.setStyle.mock.calls.length).toBe(applied);
    // Only PDB text is read for a header; the atom count still shows.
    expect(find("mol-meta-id")).toHaveLength(0);
    expect(find("mol-meta-title")).toHaveLength(0);
    expect(find("mol-meta-atoms")[0]!.textContent).toBe(molT("mol.atoms", (2).toLocaleString()));
    click(methane[0]!);
    expect(viewer.addLabel).toHaveBeenCalledWith("C 1", expect.objectContaining({ position: { x: 0, y: 0, z: 0 } }));
  });

  it("renders a structure without label or reset support in an older 3Dmol", async () => {
    const { viewer, clear, reset, click, loaded } = mount(ATOMS);
    Object.assign(viewer, { setClickable: undefined, getView: undefined });
    // The fields are read when the structure loads, after this point.
    await loaded();
    click(ATOMS[0]!);
    expect(viewer.addLabel).not.toHaveBeenCalled();
    reset.onclick?.();
    expect(viewer.zoomTo).toHaveBeenCalledTimes(2);
    expect(clear.disabled).toBe(true);
  });

  it.each([
    ["zh", "重置视角", "着色：", ["卡通", "棍状", "球状", "表面", "线条", "默认", "按链", "B 因子"]],
    ["en", "Reset view", "Color:", ["Cartoon", "Stick", "Sphere", "Surface", "Line", "Default", "By chain", "B-factor"]],
  ] as const)("labels every control in %s", async (lang, reset, color, pills) => {
    const previous = LANG;
    await setLang(lang);
    try {
      const { find } = mount(ATOMS);
      expect(find("mol-act")[0]!.textContent).toBe(reset);
      expect(find("mol-lbl")[1]!.textContent).toBe(color);
      expect(find("mol-style").map((node) => node.textContent)).toEqual(pills);
      expect(find("mol-tag")[0]!.textContent).toBe(lang === "zh" ? "使用 3Dmol.js 查看器" : "Using 3Dmol.js viewer");
      // The vendored 3Dmol zooms on Shift+drag and pans on Ctrl+drag.
      expect(find("mol-hint")[0]!.textContent).toContain("Ctrl+");
      expect(find("mol-hint")[0]!.textContent).not.toContain("Shift");
      // Every key has its own entry in this language (the lookup falls back
      // to English, so a missing Chinese entry would otherwise pass).
      const cjk = /[　-〿一-鿿＀-￯]/;
      for (const key of [
        "mol.color.label", "mol.color.default", "mol.color.chain", "mol.color.bfactor",
        "mol.color.chain.none", "mol.color.bfactor.none", "mol.legend.bfactor", "mol.reset",
        "mol.reset.title", "mol.labels.clear", "mol.labels.clearCount", "mol.atoms", "mol.atoms.one",
        "mol.chains", "mol.chains.one", "mol.chains.many", "mol.chains.sep", "mol.hint",
      ]) {
        expect(molT(key)).not.toBe(key);
        expect([key, cjk.test(molT(key))]).toEqual([key, lang === "zh"]);
      }
    } finally {
      molTeardown();
      vi.unstubAllGlobals();
      await setLang(previous);
    }
  });
});
