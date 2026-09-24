import { describe, expect, it, vi } from "vitest";
import { isTextEditable, renderViewer } from "./viewer";
import { hint } from "../features/sessions/chrome";
import { translate } from "../features/artifacts/api";
import { filesT } from "../features/artifacts/copy";
import { copyFailedText } from "../features/chrome/clipboard";
import { _artBust, dockArtifact } from "../stores/artifacts";
import { resetStoreFields } from "../stores/signal-field";
const menu = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock("../features/sessions/chrome", () => ({ openMenu: (...args: unknown[]) => menu.open(...args), hint: vi.fn() }));
vi.mock("../features/artifacts/renderers", () => ({ renderArtifactBody: vi.fn() }));
vi.mock("../features/execution/provenance", () => ({ renderProvenanceInto: vi.fn(), decorateViewerWithProvenance: vi.fn() }));
vi.mock("../features/autocomplete/editor", () => ({ edacTeardown: vi.fn(), bindEditorAutocomplete: vi.fn() }));
vi.mock("./mol", () => ({ molTeardown: vi.fn() }));
vi.mock("../features/chrome/modal", () => ({ openModalEl: vi.fn() }));

describe("isTextEditable (app.js:9458-9461)", () => {
  it("rejects images, structures, and PDFs", () => {
    expect(isTextEditable({ id: "1", filename: "plot.png" })).toBe(false);
    expect(isTextEditable({ id: "1", filename: "struct.pdb" })).toBe(false);
    expect(isTextEditable({ id: "1", filename: "paper.pdf" })).toBe(false);
    expect(isTextEditable({ id: "1", filename: "x.mol" })).toBe(false);
    expect(isTextEditable({ id: "1", content_type: "image/png", filename: "x" })).toBe(false);
  });

  it("accepts text-like extensions and content types", () => {
    expect(isTextEditable({ id: "1", filename: "notes.md" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "table.csv" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "run.py" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "blob", content_type: "text/plain" })).toBe(true);
    expect(isTextEditable({ id: "1", filename: "blob", content_type: "application/json" })).toBe(true);
  });
});


it.each([true, false])("the Viewer menu copy respects exact=%s", (exact) => {
  class Node {
    children: Node[] = [];
    innerHTML = ""; textContent = ""; className = ""; title = "";
    onclick?: () => void;
    appendChild(child: Node) { this.children.push(child); return child; }
    append(...children: Node[]) { this.children.push(...children); }
    dataset: Record<string, string> = {};
    setAttribute() {}
  }
  resetStoreFields(); menu.open.mockReset();
  const root = new Node();
  const writeText = vi.fn();
  vi.stubGlobal("document", { getElementById: () => root, createElement: () => new Node() });
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal("location", { origin: "http://localhost", pathname: "/", search: "" });
  try {
    dockArtifact.value = { id: "a", filename: "plot.png", version_id: "v1", _exactVersion: exact };
    renderViewer();
    const walk = (node: Node): Node[] => [node, ...node.children.flatMap(walk)];
    walk(root).find((node) => node.innerHTML.includes('cx="12" cy="5"'))?.onclick?.();
    expect(menu.open).toHaveBeenCalledTimes(1);
    const items = menu.open.mock.calls[0]?.[1] as { icon?: string; onClick?: () => void }[];
    items.find((item) => item.icon === "link")?.onClick?.();
    expect(writeText).toHaveBeenCalledTimes(1);
    const copied = String(writeText.mock.calls[0]?.[0]);
    expect(copied).toContain("artifact=a");
    if (exact) expect(copied).toContain("version_id=v1");
    else expect(copied).not.toContain("version_id=");
  } finally { vi.unstubAllGlobals(); resetStoreFields(); }
});


describe("Viewer Copy link (AUDIT A17, A19)", () => {
  class Node {
    children: Node[] = [];
    innerHTML = ""; textContent = ""; className = ""; title = "";
    onclick?: () => void;
    appendChild(child: Node) { this.children.push(child); return child; }
    append(...children: Node[]) { this.children.push(...children); }
    dataset: Record<string, string> = {};
    setAttribute() {}
  }
  const walk = (node: Node): Node[] => [node, ...node.children.flatMap(walk)];

  function mountExactViewer(clipboard: unknown): { root: Node; button: Node | undefined } {
    resetStoreFields(); vi.mocked(hint).mockClear();
    const root = new Node();
    // No `body`: the selection-copy fallback has nothing to select into.
    vi.stubGlobal("document", { getElementById: () => root, createElement: () => new Node() });
    vi.stubGlobal("navigator", { clipboard });
    vi.stubGlobal("location", { pathname: "/", search: "" });
    dockArtifact.value = { id: "a", filename: "plot.png", version_id: "v1", _exactVersion: true };
    renderViewer();
    return { root, button: walk(root).find((node) => node.textContent === filesT("files.deeplink.copy")) };
  }

  it("writes through the clipboard object and says Copied only once the write lands", async () => {
    const clipboard = {
      written: "",
      async writeText(this: { written: string }, text: string) {
        // A detached call has no receiver: browsers throw Illegal invocation.
        if (this !== clipboard) throw new TypeError("Illegal invocation");
        this.written = text;
      },
    };
    try {
      const { button } = mountExactViewer(clipboard);
      button?.onclick?.();
      await vi.waitFor(() => expect(button?.textContent).toBe(filesT("files.deeplink.copied")));
      expect(clipboard.written).toBe("/?artifact=a&version_id=v1");
      expect(hint).not.toHaveBeenCalled();
    } finally { vi.unstubAllGlobals(); resetStoreFields(); }
  });

  it("shows the link to copy by hand when the write is refused", async () => {
    try {
      const { button } = mountExactViewer({ writeText: () => Promise.reject(new Error("denied")) });
      button?.onclick?.();
      await vi.waitFor(() => expect(hint).toHaveBeenCalled());
      expect(hint).toHaveBeenCalledWith(copyFailedText() + " /?artifact=a&version_id=v1", true);
      expect(button?.textContent).toBe(filesT("files.deeplink.copy"));
    } finally { vi.unstubAllGlobals(); resetStoreFields(); }
  });

  function menuCopy(clipboard: unknown): void {
    menu.open.mockReset();
    const { root } = mountExactViewer(clipboard);
    walk(root).find((node) => node.innerHTML.includes('cx="12" cy="5"'))?.onclick?.();
    const items = menu.open.mock.calls[0]?.[1] as { icon?: string; onClick?: () => void }[];
    items.find((item) => item.icon === "link")?.onClick?.();
  }

  it("the menu reports Copied only after a confirmed write", async () => {
    const writeText = vi.fn(async () => undefined);
    try {
      menuCopy({ writeText });
      await vi.waitFor(() => expect(hint).toHaveBeenCalledWith(translate("artifact.linkCopied")));
      expect(writeText).toHaveBeenCalledWith("/?artifact=a&version_id=v1");
    } finally { vi.unstubAllGlobals(); resetStoreFields(); }
  });

  it.each([
    ["refused", { writeText: () => Promise.reject(new Error("denied")) }],
    ["insecure context", undefined],
  ])("the menu shows the link instead of Copied when the clipboard is %s", async (_label, clipboard) => {
    try {
      menuCopy(clipboard);
      await vi.waitFor(() => expect(hint).toHaveBeenCalled());
      expect(hint).toHaveBeenCalledWith(copyFailedText() + " /?artifact=a&version_id=v1", true);
      expect(hint).not.toHaveBeenCalledWith(translate("artifact.linkCopied"));
    } finally { vi.unstubAllGlobals(); resetStoreFields(); }
  });
});


it.each([true, false])("Edit is offered only on the latest tab (exact=%s)", (exact) => {
  class Node {
    children: Node[] = [];
    innerHTML = ""; textContent = ""; className = ""; title = "";
    onclick?: () => void;
    appendChild(child: Node) { this.children.push(child); return child; }
    append(...children: Node[]) { this.children.push(...children); }
    dataset: Record<string, string> = {};
    setAttribute() {}
  }
  resetStoreFields(); menu.open.mockReset();
  const root = new Node();
  vi.stubGlobal("document", { getElementById: () => root, createElement: () => new Node() });
  try {
    dockArtifact.value = { id: "a", filename: "notes.txt", content_type: "text/plain", version_id: "v1", _exactVersion: exact };
    renderViewer();
    const walk = (node: Node): Node[] => [node, ...node.children.flatMap(walk)];
    const editButtons = walk(root).filter((node) => node.className === "icon-ghost" && node.title === translate("common.edit"));
    expect(editButtons).toHaveLength(exact ? 0 : 1);
    walk(root).find((node) => node.innerHTML.includes('cx="12" cy="5"'))?.onclick?.();
    const items = menu.open.mock.calls[0]?.[1] as { label?: string; onClick?: () => void }[];
    vi.mocked(hint).mockClear();
    items.find((item) => item.label === translate("common.edit"))?.onClick?.();
    if (exact) expect(hint).toHaveBeenCalledWith(filesT("artifact.editPinned"));
    else expect(hint).not.toHaveBeenCalled();
  } finally { vi.unstubAllGlobals(); resetStoreFields(); }
});

import { setArtifactsFetch } from "../features/artifacts/api";
import type { ArtifactRow } from "../features/artifacts/types";

class MetadataNode {
  children: MetadataNode[] = [];
  innerHTML = ""; textContent = ""; className = ""; title = "";
  href = ""; download = ""; dataset: Record<string, string> = {};
  onclick?: () => void;
  appendChild(child: MetadataNode) { this.children.push(child); return child; }
  append(...children: MetadataNode[]) { this.children.push(...children); }
  setAttribute() {}
  click() {}
}

async function metadataExportFixture(art: ArtifactRow, read: (url: string) => Promise<Response>, check: (blobs: Blob[], urls: string[]) => Promise<void>) {
  resetStoreFields(); menu.open.mockReset(); vi.mocked(hint).mockClear();
  const root = new MetadataNode(); const blobs: Blob[] = []; const urls: string[] = [];
  vi.stubGlobal("document", { getElementById: () => root, createElement: () => new MetadataNode() });
  vi.spyOn(URL, "createObjectURL").mockImplementation((blob) => { blobs.push(blob as Blob); return "blob:metadata"; });
  vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
  setArtifactsFetch(async (url) => { urls.push(url); return read(url); });
  try {
    dockArtifact.value = art; renderViewer();
    const walk = (node: MetadataNode): MetadataNode[] => [node, ...node.children.flatMap(walk)];
    walk(root).find((node) => node.innerHTML.includes('cx="12" cy="5"'))?.onclick?.();
    const items = menu.open.mock.calls[0]?.[1] as { label?: string; onClick?: () => void }[];
    items.find((item) => item.label === translate("menu.exportMetadata"))?.onClick?.();
    await check(blobs, urls);
  } finally { setArtifactsFetch(null); vi.restoreAllMocks(); vi.unstubAllGlobals(); resetStoreFields(); }
}

it.each([
  ["versions", "http"], ["versions", {}], ["versions", { versions: [null] }],
  ["versions", { versions: [{ version_id: "" }] }],
  ["lineage", "http"], ["lineage", null], ["lineage", {}],
])("metadata export refuses required %s read failure or malformed 200: %j", async (surface, fault) => {
  await metadataExportFixture({ id: "a", filename: "plot.png" }, async (url) => {
    if (url.endsWith(`/${surface}`)) return new Response(JSON.stringify(fault === "http" ? { error: "read failed" } : fault), { status: fault === "http" ? 500 : 200 });
    return new Response(JSON.stringify(url.endsWith("/versions") ? { versions: [] } : { artifact_id: "a", interactions: [], dependency_mappings: { inputs: [] } }));
  }, async (blobs) => {
    await vi.waitFor(() => expect(hint).toHaveBeenCalled());
    expect(blobs).toHaveLength(0);
    expect(hint).toHaveBeenLastCalledWith(expect.any(String), true);
  });
});

it("metadata export freezes a known latest version and matching output identity before reads", async () => {
  const art = { id: "a", filename: "original.png", version_id: "v1", size_bytes: 10 };
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  await metadataExportFixture(art, async (url) => {
    await gate;
    return new Response(JSON.stringify(url.endsWith("/versions") ? { versions: [] } : { artifact_id: "a", version_id: "v1", interactions: [], dependency_mappings: { inputs: [] } }));
  }, async (blobs, urls) => {
    art.version_id = "v2"; art.filename = "changed.png"; art.size_bytes = 20;
    release();
    await vi.waitFor(() => expect(blobs).toHaveLength(1));
    const exported = JSON.parse(await blobs[0]!.text());
    expect(exported).toMatchObject({ version_id: "v1", filename: "original.png", size_bytes: 10, lineage: { version_id: "v1" } });
    expect(urls).toContain("/api/v1/artifacts/a/lineage?version=v1");
  });
});


it("a successful unbound legacy export stays unknown despite an available versions head", async () => {
  await metadataExportFixture({ id: "a", filename: "legacy.png" }, async (url) => new Response(JSON.stringify(
    url.endsWith("/versions") ? { versions: [{ version_id: "head", is_latest: true }] } : { artifact_id: "a", interactions: [], dependency_mappings: { inputs: [] } },
  )), async (blobs) => {
    await vi.waitFor(() => expect(blobs).toHaveLength(1));
    const exported = JSON.parse(await blobs[0]!.text());
    expect(exported.version_id).toBeNull(); expect(exported.lineage.version_id).toBeUndefined();
    expect(hint).toHaveBeenLastCalledWith(translate("artifact.metadataExported"));
  });
});

it("an exact legacy version's unknown metadata is exported without borrowing current head values", async () => {
  const { resolveArtifactVersion } = await import("../features/artifacts/deeplink");
  const result = await resolveArtifactVersion({ artifactId: "a", versionId: "v1" },
    async () => [{ version_id: "v1", filename: null, size_bytes: null, content_type: null, checksum: null, producing_cell_id: null }],
    async () => ({ id: "a", version_id: "v2", filename: "head.png", size_bytes: 100, content_type: "image/png", checksum: "head-checksum", producing_cell_id: "head-cell" }));
  expect(result.status).toBe("exact"); if (result.status !== "exact") throw new Error("expected exact version");
  expect(result.artifact).toMatchObject({ version_id: "v1", filename: null, size_bytes: null, content_type: null, checksum: null, producing_cell_id: null });
  await metadataExportFixture(result.artifact, async (url) => new Response(JSON.stringify(
    url.endsWith("/versions") ? { versions: [{ version_id: "v1", size_bytes: null }] } : { artifact_id: "a", version_id: "v1", interactions: [], dependency_mappings: { inputs: [] } },
  )), async (blobs) => {
    await vi.waitFor(() => expect(blobs).toHaveLength(1));
    expect(JSON.parse(await blobs[0]!.text())).toMatchObject({ version_id: "v1", filename: null, size_bytes: null, content_type: null });
  });
});


describe("versions modal ownership (AUDIT A29)", () => {
  class ModalNode {
    children: ModalNode[] = [];
    parent: ModalNode | null = null;
    root = false;
    className = ""; title = ""; href = ""; target = ""; disabled = false;
    dataset: Record<string, string> = {};
    style: Record<string, string> = {};
    onclick?: () => unknown;
    private text = "";
    get isConnected(): boolean {
      let node: ModalNode | null = this;
      while (node && !node.root) node = node.parent;
      return !!node;
    }
    get textContent(): string { return this.text + this.children.map((c) => c.textContent).join(""); }
    set textContent(value: string) { this.clear(); this.text = value; }
    set innerHTML(_value: string) { this.clear(); }
    appendChild(child: ModalNode) { child.parent = this; this.children.push(child); return child; }
    setAttribute() {}
    private clear() {
      for (const child of this.children) child.parent = null;
      this.children = []; this.text = "";
    }
  }
  const walk = (n: ModalNode): ModalNode[] => [n, ...n.children.flatMap(walk)];
  const listFor = (id: string, count: number) => ({
    versions: Array.from({ length: count }, (_, i) => ({ version_id: `${id}-v${i + 1}`, ordinal: i + 1, is_latest: i === count - 1 })),
  });

  function mountModal() {
    resetStoreFields();
    const modal = new ModalNode(); modal.root = true;
    const body = modal.appendChild(new ModalNode());
    const nodes: Record<string, ModalNode> = {
      "#modal": modal, "#modal-body": body,
      "#modal-title": modal.appendChild(new ModalNode()), "#modal-download": modal.appendChild(new ModalNode()),
    };
    vi.stubGlobal("document", { querySelector: (sel: string) => nodes[sel] ?? null, createElement: () => new ModalNode() });
    return { body, rows: () => walk(body).filter((n) => n.className.startsWith("ver-row")) };
  }

  it("drops a late list for the artifact whose modal was replaced", async () => {
    const { body, rows } = mountModal();
    let releaseA!: () => void;
    const heldA = new Promise<void>((resolve) => { releaseA = resolve; });
    setArtifactsFetch(async (url) => {
      if (url.includes("/artifacts/A/versions")) { await heldA; return new Response(JSON.stringify(listFor("A", 3))); }
      return new Response(JSON.stringify(listFor("B", 1)));
    });
    try {
      const { showVersions } = await import("./viewer");
      void showVersions({ id: "A", filename: "a.png" });
      void showVersions({ id: "B", filename: "b.png" });
      await vi.waitFor(() => expect(rows()).toHaveLength(1));
      releaseA();
      for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
      expect(rows()).toHaveLength(1);
      expect(body.textContent).toContain("v1");
      expect(body.textContent).not.toContain("v3");
    } finally { setArtifactsFetch(null); vi.unstubAllGlobals(); resetStoreFields(); }
  });

  it("a restore publishes a new cache-bust map (AUDIT S04)", async () => {
    const { body, rows } = mountModal();
    let listReads = 0;
    setArtifactsFetch(async (url) => {
      if (url.endsWith("/restore")) return new Response(JSON.stringify({ artifact: { id: "A", version_id: "A-v1" } }));
      listReads += 1;
      return new Response(JSON.stringify(listFor("A", 2)));
    });
    try {
      const { showVersions } = await import("./viewer");
      void showVersions({ id: "A", filename: "a.png" });
      await vi.waitFor(() => expect(rows()).toHaveLength(2));
      const before = _artBust.value;
      await walk(body).find((n) => n.textContent === translate("versions.restore"))?.onclick?.();
      expect(_artBust.value).not.toBe(before);
      expect(_artBust.value).toHaveProperty("A");
      // The restore repaints the list; let it finish before the fixture goes.
      await vi.waitFor(() => expect(listReads).toBe(2));
      for (let i = 0; i < 3; i++) await new Promise((resolve) => setTimeout(resolve, 0));
    } finally { setArtifactsFetch(null); vi.unstubAllGlobals(); resetStoreFields(); }
  });
});
