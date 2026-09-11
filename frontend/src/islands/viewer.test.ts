import { describe, expect, it, vi } from "vitest";
import { isTextEditable, renderViewer } from "./viewer";
import { hint } from "../features/sessions/chrome";
import { translate } from "../features/artifacts/api";
import { filesT } from "../features/artifacts/copy";
import { dockArtifact } from "../stores/artifacts";
import { resetStoreFields } from "../stores/signal-field";
const menu = vi.hoisted(() => ({ open: vi.fn() }));
vi.mock("../features/sessions/chrome", () => ({ openMenu: (...args: unknown[]) => menu.open(...args), hint: vi.fn() }));
vi.mock("../features/artifacts/renderers", () => ({ renderArtifactBody: vi.fn() }));
vi.mock("../features/execution/provenance", () => ({ renderProvenanceInto: vi.fn(), decorateViewerWithProvenance: vi.fn() }));
vi.mock("../features/autocomplete/editor", () => ({ edacTeardown: vi.fn(), bindEditorAutocomplete: vi.fn() }));
vi.mock("./mol", () => ({ molTeardown: vi.fn() }));

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


it.each([true, false])("Edit is offered only on the latest tab (exact=%s)", (exact) => {
  class Node {
    children: Node[] = [];
    innerHTML = ""; textContent = ""; className = ""; title = "";
    onclick?: () => void;
    appendChild(child: Node) { this.children.push(child); return child; }
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
