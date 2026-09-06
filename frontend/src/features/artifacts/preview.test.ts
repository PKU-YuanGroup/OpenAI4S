import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sandboxOrigin } from "../../stores/session";
import { setArtifactsFetch } from "./api";
import { bootArtifacts } from "./boot";
import { renderHtmlPreview } from "./preview";
import { renderArtifactDescriptor } from "./renderers";

class FakeNode {
  src = "";
  className = "";
  innerHTML = "";
  textContent = "";
  dataset: Record<string, string> = {};
  attrs = new Map<string, string>();
  children: FakeNode[] = [];
  parentNode: FakeNode | null = null;
  isConnected = true;
  constructor(readonly tagName = "div") {}
  setAttribute(name: string, value: string): void { this.attrs.set(name, value); }
  getAttribute(name: string): string | null { return this.attrs.get(name) ?? null; }
  appendChild(node: FakeNode): FakeNode {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }
  remove(): void {
    if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((n) => n !== this);
    this.parentNode = null;
    this.isConnected = false;
  }
}

const origin = "http://localhost:8760";
const path = "/sandbox/frame.999.signature/preview/report";
const app = { protocol: "http:", hostname: "127.0.0.1", port: "8760" };
const response = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status });
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
const host = (node: FakeNode) => node as unknown as HTMLElement;

describe("production HTML artifact preview", () => {
  beforeEach(() => {
    vi.stubGlobal("location", app);
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
    sandboxOrigin.value = origin;
  });

  afterEach(() => {
    setArtifactsFetch(null);
    sandboxOrigin.value = "";
    vi.unstubAllGlobals();
  });

  it("initializes the verified origin in workbench boot and refuses an unsafe override", () => {
    sandboxOrigin.value = "";
    bootArtifacts({});
    expect(sandboxOrigin.value).toBe(origin);
    bootArtifacts({ __OPERON__: { sandboxOrigin: "http://127.0.0.1:8760" } });
    expect(sandboxOrigin.value).toBe("");
  });

  it("the renderer starts inert and upgrades only after its authenticated grant resolves", async () => {
    let release!: (value: Response) => void;
    const request = vi.fn(() => new Promise<Response>((done) => { release = done; }));
    setArtifactsFetch(request);
    const body = new FakeNode();
    renderArtifactDescriptor(host(body), { id: "report" }, {
      artifact_id: "report", renderer: { renderer_id: "html-preview" },
    });
    const content = body.children[0]!.children[1]!;
    const frame = content.children[0]!;
    expect(frame.src).toBe("/preview/report");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
    expect(request).toHaveBeenCalledWith("/api/v1/artifacts/report/sandbox-grant", expect.objectContaining({ method: "POST" }));

    release(response({ origin, path }));
    await flush();
    expect(frame.src).toBe(origin + path);
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin");
    expect(content.children).toHaveLength(1);
  });

  it("pins both the inert bytes and the scoped grant to an exact version", async () => {
    const request = vi.fn(async () => response({ error: "unavailable" }, 409));
    setArtifactsFetch(request);
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report", version_id: "version-1", _exactVersion: true });
    await flush();
    expect(request).toHaveBeenCalledWith("/api/v1/artifacts/report/sandbox-grant?version_id=version-1", expect.objectContaining({ method: "POST" }));
    expect(content.children[0]!.src).toBe("/api/v1/artifacts/versions/version-1");
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
  });

  it.each([
    { origin, path: "//external.example/report" },
    { origin, path: "/\\external.example/report" },
    { origin, path: "/api/v1/settings" },
    { origin, path: "/sandbox/token/preview/other" },
    { origin, path: "/sandbox/token/preview/report/../other" },
    { origin, path: "/sandbox/token/preview/report?version_id=other" },
    { origin: "http://127.0.0.1:8760", path },
    { origin: "https://external.example", path },
    { path },
    null,
  ])("keeps the document inert for an invalid grant %j", async (grant) => {
    setArtifactsFetch(async () => response(grant));
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    await flush();
    expect(content.children[0]!.src).toBe("/preview/report");
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
  });

  it("does not grant from an unsafe runtime origin override", async () => {
    sandboxOrigin.value = "http://127.0.0.1:8760";
    const request = vi.fn(async () => response({ origin, path }));
    setArtifactsFetch(request);
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    await flush();
    expect(request).not.toHaveBeenCalled();
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
  });

  it("does not reactivate a detached preview after the viewer switches artifacts", async () => {
    let release!: (value: Response) => void;
    setArtifactsFetch(() => new Promise<Response>((done) => { release = done; }));
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    const frame = content.children[0]!;
    frame.remove();
    release(response({ origin, path }));
    await flush();
    expect(frame.src).toBe("/preview/report");
    expect(frame.getAttribute("sandbox")).toBe("");
  });

  it("keeps the inert document on a network failure", async () => {
    setArtifactsFetch(async () => { throw new TypeError("connection lost"); });
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    await flush();
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
  });
});
