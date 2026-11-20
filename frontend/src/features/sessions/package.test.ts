/**
 * TEAM-20: importing from the home page reported into the hidden workbench
 * composer, so a refused package looked like nothing happened. The import has
 * a dialog of its own. TEAM-13: exports say what they saved, or why not.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./chrome", () => ({ hint: vi.fn() }));
vi.mock("./conversation", () => ({ openConversation: vi.fn(async () => undefined) }));
vi.mock("./load", () => ({ loadProjects: vi.fn(async () => undefined) }));
vi.mock("./dom", () => ({ $: () => null }));

import { i18nReady } from "../../i18n";
import { hint } from "./chrome";
import { openConversation } from "./conversation";
import { exportSessionPackage, importSessionPackage, packageT } from "./package";

class FakeNode {
  tagName: string;
  children: FakeNode[] = [];
  parent: FakeNode | null = null;
  className = "";
  id = "";
  type = "";
  textContent = "";
  href = "";
  download = "";
  attrs: Record<string, string> = {};
  onclick: (() => unknown) | null = null;
  clicked = 0;
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  appendChild(child: FakeNode): FakeNode {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child: FakeNode, before: FakeNode): FakeNode {
    child.parent = this;
    const at = this.children.indexOf(before);
    this.children.splice(at < 0 ? this.children.length : at, 0, child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((node) => node !== this);
    this.parent = null;
  }
  setAttribute(name: string, value: string): void {
    this.attrs[name] = value;
  }
  addEventListener(): void {}
  focus(): void {}
  click(): void {
    this.clicked += 1;
  }
  text(): string {
    return [this.textContent, ...this.children.map((child) => child.text())].filter(Boolean).join("\n");
  }
}

let body: FakeNode;

beforeAll(async () => {
  await i18nReady();
});

beforeEach(() => {
  vi.mocked(hint).mockClear();
  vi.mocked(openConversation).mockClear();
  body = new FakeNode("body");
  vi.stubGlobal("document", { body, createElement: (tag: string) => new FakeNode(tag) });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function serve(routes: Record<string, () => Response>): string[] {
  const seen: string[] = [];
  vi.stubGlobal("fetch", async (input: unknown) => {
    const path = String(input);
    seen.push(path);
    const route = Object.keys(routes).find((key) => path.endsWith(key));
    return route ? routes[route]!() : new Response("{}", { status: 404 });
  });
  return seen;
}

const file = { name: "run.openai4s-session.zip", size: 2048 } as File;

describe("import dialog", () => {
  it("shows the importer's refusal in a dialog instead of the hidden composer", async () => {
    const seen = serve({
      "/sessions/verify": () => new Response(JSON.stringify({ ok: true, files_verified: ["a", "b"], archive_sha256: "fe4f2893e40ce8" })),
      "/sessions/import": () =>
        new Response(JSON.stringify({ error: "artifact version references an unknown identity" }), { status: 400 }),
    });
    await importSessionPackage(file);
    const dialog = body.children[0]!;
    expect(dialog.attrs.role).toBe("dialog");
    const text = dialog.text();
    expect(text).toContain("run.openai4s-session.zip");
    expect(text).toContain(packageT("checked", 2, "fe4f2893e40c"));
    expect(text).toContain("artifact version references an unknown identity");
    expect(text).toContain(packageT("next"));
    expect(seen.filter((path) => path.endsWith("/sessions/import"))).toHaveLength(1);
    expect(hint).not.toHaveBeenCalled();
    expect(openConversation).not.toHaveBeenCalled();
  });

  it("stops at a failed check and never imports", async () => {
    const seen = serve({
      "/sessions/verify": () =>
        new Response(JSON.stringify({ ok: false, problems: ["not a readable zip archive: File is not a zip file"] })),
    });
    await importSessionPackage(file);
    expect(body.children[0]!.text()).toContain("not a readable zip archive");
    expect(seen.some((path) => path.endsWith("/sessions/import"))).toBe(false);
  });

  it("opens the imported session and closes the dialog", async () => {
    serve({
      "/sessions/verify": () => new Response(JSON.stringify({ ok: true, files_verified: [] })),
      "/sessions/import": () => new Response(JSON.stringify({ root_frame_id: "f-new", project_id: "p-1" }), { status: 201 }),
    });
    await importSessionPackage(file);
    expect(openConversation).toHaveBeenCalledWith("f-new", "p-1");
    expect(body.children).toHaveLength(0);
  });
});

describe("session package export", () => {
  it("says what was saved", async () => {
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined });
    serve({ "/session/export": () => new Response(new Uint8Array(2048)) });
    await exportSessionPackage("f-1", "My run");
    expect(hint).toHaveBeenLastCalledWith(packageT("exportedPackage", "2.0 KB"));
  });

  it("reports a refused export instead of downloading the error", async () => {
    const created = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: created, revokeObjectURL: () => undefined });
    serve({
      "/session/export": () =>
        new Response(JSON.stringify({ error: "a revert is still awaiting recovery", code: "session_not_exportable" }), {
          status: 409,
        }),
    });
    await exportSessionPackage("f-1", "My run");
    expect(created).not.toHaveBeenCalled();
    expect(vi.mocked(hint).mock.calls.at(-1)?.[1]).toBe(true);
    expect(String(vi.mocked(hint).mock.calls.at(-1)?.[0])).toContain("awaiting recovery");
  });

  // E / TEAM-13: the Notebook's clean export is named by the server; a blob
  // link without a name opened the notebook as a page instead of saving it.
  it("saves under the server's name when given none", async () => {
    vi.stubGlobal("URL", { createObjectURL: () => "blob:x", revokeObjectURL: () => undefined });
    serve({
      "/notebook/export?variant=clean": () =>
        new Response("{}", {
          headers: { "Content-Disposition": "attachment; filename=\"x.ipynb\"; filename*=UTF-8''Growth%20assay.ipynb" },
        }),
    });
    const links: FakeNode[] = [];
    const make = (tag: string) => {
      const node = new FakeNode(tag);
      if (tag === "a") links.push(node);
      return node;
    };
    vi.stubGlobal("document", { body, createElement: make });
    const { saveExport } = await import("./package");
    expect(await saveExport("/api/v1/frames/f-1/notebook/export?variant=clean", "")).toBe(2);
    expect(links[0]?.download).toBe("Growth assay.ipynb");
    expect(links[0]?.clicked).toBe(1);
  });
});
