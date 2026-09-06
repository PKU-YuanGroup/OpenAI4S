import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setArtifactsFetch, translate } from "./api";
import { forgetGrants, renderHtmlPreview } from "./preview";
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
const grant = { origin, path, expires_in: 3600 };
const noscript = translate("viewer.renderer.noscript");
const interactive = translate("viewer.renderer.interactive");

describe("production HTML artifact preview", () => {
  beforeEach(() => {
    vi.stubGlobal("location", app);
    vi.stubGlobal("document", { createElement: (tag: string) => new FakeNode(tag) });
    forgetGrants();
  });

  afterEach(() => {
    setArtifactsFetch(null);
    forgetGrants();
    vi.unstubAllGlobals();
  });

  it("waits for its authenticated grant and navigates the frame exactly once", async () => {
    let release!: (value: Response) => void;
    const request = vi.fn(() => new Promise<Response>((done) => { release = done; }));
    setArtifactsFetch(request);
    const body = new FakeNode();
    renderArtifactDescriptor(host(body), { id: "report" }, {
      artifact_id: "report", renderer: { renderer_id: "html-preview" },
    });
    const content = body.children[0]!.children[1]!;
    const frame = content.children[0]!;
    const note = content.children[1]!;
    // No first paint of the inert document: on a loopback page the grant is
    // the expected outcome, and loading the report twice is pure waste.
    expect(frame.src).toBe("");
    expect(frame.getAttribute("sandbox")).toBe("");
    expect(note.textContent).toBe(noscript);
    expect(request).toHaveBeenCalledWith("/api/v1/artifacts/report/sandbox-grant", expect.objectContaining({ method: "POST" }));

    release(response(grant));
    await flush();
    expect(frame.src).toBe(origin + path);
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin");
    // The caption is retargeted, not removed: a cross-origin frame cannot
    // report a refused navigation, so a blank canvas keeps an explanation.
    expect(content.children).toHaveLength(2);
    expect(note.textContent).toBe(interactive);
  });

  it("reuses a live grant for the same artifact version instead of minting again", async () => {
    const request = vi.fn(async () => response(grant));
    setArtifactsFetch(request);
    const first = new FakeNode();
    renderHtmlPreview(host(first), { id: "report" });
    await flush();
    expect(first.children[0]!.src).toBe(origin + path);

    const second = new FakeNode();
    renderHtmlPreview(host(second), { id: "report" });
    // Synchronous: the remembered URL needs no round trip.
    expect(second.children[0]!.src).toBe(origin + path);
    expect(second.children[0]!.getAttribute("sandbox")).toBe("allow-scripts allow-same-origin");
    expect(request).toHaveBeenCalledTimes(1);

    // A different version is a different grant.
    renderHtmlPreview(host(new FakeNode()), { id: "report", version_id: "version-1", _exactVersion: true });
    await flush();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("re-mints after a new version rather than replaying the superseded grant", async () => {
    const request = vi.fn(async () => response(grant));
    setArtifactsFetch(request);
    const row = { id: "report", version_id: "version-1" };
    renderHtmlPreview(host(new FakeNode()), row);
    await flush();
    expect(request).toHaveBeenCalledTimes(1);
    // What syncArtifactVersion writes when a new capture lands: a new
    // version_id on the same row, with no _exactVersion flag. The cached URL
    // still points at the version the server pinned at mint, so it must miss.
    renderHtmlPreview(host(new FakeNode()), { ...row, version_id: "version-2" });
    await flush();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("does not remember a grant that would expire before it could be reused", async () => {
    const request = vi.fn(async () => response({ ...grant, expires_in: 30 }));
    setArtifactsFetch(request);
    renderHtmlPreview(host(new FakeNode()), { id: "report" });
    await flush();
    renderHtmlPreview(host(new FakeNode()), { id: "report" });
    await flush();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("pins both the inert fallback and the scoped grant to an exact version", async () => {
    const request = vi.fn(async () => response({ error: "unavailable" }, 409));
    setArtifactsFetch(request);
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report", version_id: "version-1", _exactVersion: true });
    await flush();
    expect(request).toHaveBeenCalledWith("/api/v1/artifacts/report/sandbox-grant?version_id=version-1", expect.objectContaining({ method: "POST" }));
    // The app-origin preview of the version id: pinned bytes, forced HTML,
    // and relative siblings resolve next to it. Never the raw API byte route.
    expect(content.children[0]!.src).toBe("/preview/version-1");
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
    expect(content.children[1]!.textContent).toBe(noscript);
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
  ])("falls back to the inert document for an invalid grant %j", async (reply) => {
    setArtifactsFetch(async () => response(reply));
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    await flush();
    expect(content.children[0]!.src).toBe("/preview/report");
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
    expect(content.children[1]!.textContent).toBe(noscript);
  });

  it("stays inert without a request when the page is not a loopback HTTP origin", () => {
    vi.stubGlobal("location", { ...app, hostname: "remote.example" });
    const request = vi.fn(async () => response(grant));
    setArtifactsFetch(request);
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    expect(request).not.toHaveBeenCalled();
    expect(content.children[0]!.src).toBe("/preview/report");
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
  });

  it("does not reactivate a detached preview after the viewer switches artifacts", async () => {
    let release!: (value: Response) => void;
    setArtifactsFetch(() => new Promise<Response>((done) => { release = done; }));
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    const frame = content.children[0]!;
    frame.remove();
    release(response(grant));
    await flush();
    expect(frame.src).toBe("");
    expect(frame.getAttribute("sandbox")).toBe("");
  });

  it("falls back to the inert document on a network failure", async () => {
    setArtifactsFetch(async () => { throw new TypeError("connection lost"); });
    const content = new FakeNode();
    renderHtmlPreview(host(content), { id: "report" });
    await flush();
    expect(content.children[0]!.src).toBe("/preview/report");
    expect(content.children[0]!.getAttribute("sandbox")).toBe("");
    expect(content.children).toHaveLength(2);
  });
});
