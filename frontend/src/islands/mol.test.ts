import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";
import { _molView } from "../stores/artifacts";
import { resetStoreFields } from "../stores/signal-field";
import { MOL_VENDOR_SRC, molecule } from "./mol";

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

describe("one live 3Dmol viewer (AUDIT A37)", () => {
  class FakeEl {
    children: FakeEl[] = [];
    parent: FakeEl | null = null;
    page = false;
    className = "";
    textContent = "";
    src = "";
    style: Record<string, string> = {};
    classList = { add() {}, remove() {} };
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
    remove(): void {
      if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
      this.parent = null;
    }
    querySelector(): null {
      return null;
    }
  }
  const walk = (node: FakeEl): FakeEl[] => [node, ...node.children.flatMap(walk)];
  const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

  function setup() {
    resetStoreFields();
    const head = new FakeEl("head");
    head.page = true;
    vi.stubGlobal("document", { head, createElement: (tag: string) => new FakeEl(tag) });
    const page = new FakeEl();
    page.page = true;
    const views: FakeEl[] = [];
    const runtime = {
      createViewer: vi.fn((view: FakeEl) => {
        views.push(view);
        return { addModel: () => ({ selectedAtoms: () => [] }), setStyle() {}, zoomTo() {}, render() {}, clear() {} };
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
    vi.unstubAllGlobals();
    resetStoreFields();
  });

  it("lets only the latest structure create the viewer", async () => {
    const { page, views, runtime, releaseA, host } = setup();
    vi.stubGlobal("$3Dmol", runtime);
    const first = page.appendChild(new FakeEl());
    const second = page.appendChild(new FakeEl());
    molecule(host(first), "/a.pdb", "a.pdb");
    molecule(host(second), "/b.pdb", "b.pdb");
    await vi.waitFor(() => expect(views).toHaveLength(1));
    releaseA();
    for (let i = 0; i < 3; i++) await settle();
    expect(views).toHaveLength(1);
    expect(walk(second)).toContain(views[0]);
    expect(_molView.value).toBe(views[0]);
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
    await vi.waitFor(() => expect(views).toHaveLength(1));
    for (let i = 0; i < 3; i++) await settle();
    expect(views).toHaveLength(1);
    expect(walk(second)).toContain(views[0]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
