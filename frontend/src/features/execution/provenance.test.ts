import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { dockArtifact, _envSnapById } from "../../stores/artifacts";
import { cells, lineage, _lineageFor } from "../../stores/notebook";
import { currentId, _openGen } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { provMode, provSub } from "../../stores/ui";
import { filesT } from "../artifacts/copy";
import type { ArtifactRow } from "../artifacts/types";
import { renderNotebook } from "../notebook/Notebook";
import { fetchRecentMessages } from "../sessions/messages";
import { setExecutionFetch } from "./api";
import { provenanceT } from "./copy";
import { renderProvenanceInto, renderProvReview, showProvenance } from "./provenance";
import { validateEnvironment, validateLineage } from "./validation";

vi.mock("../artifacts/ui", () => ({ addOpenTab: vi.fn(), setActiveTab: vi.fn() }));
vi.mock("../notebook/Notebook", () => ({ renderNotebook: vi.fn(), cellNode: vi.fn() }));
vi.mock("../sessions/messages", () => ({ fetchRecentMessages: vi.fn() }));

class Node {
  children: Node[] = [];
  className = "";
  textContent = "";
  onclick: (() => void) | null = null;
  attributes: Record<string, string> = {};
  parentElement: Node | null = null;
  tagName = "DIV";
  open = false;
  scrollIntoView = vi.fn();
  classList = { add: vi.fn(), remove: vi.fn() };
  set innerHTML(_html: string) { this.children = []; this.textContent = ""; }
  setAttribute(key: string, value: string) { this.attributes[key] = value; }
  getAttribute(key: string) { return this.attributes[key] ?? null; }
  appendChild(child: Node) { child.parentElement = this; this.children.push(child); return child; }
  querySelectorAll() { return this.children; }
}
function walk(node: Node): Node[] { return [node, ...node.children.flatMap(walk)]; }
function text(node: Node): string { return walk(node).map((n) => n.textContent).join(" "); }
function button(node: Node): Node { return walk(node).find((n) => n.className.includes("prov-retry"))!; }
function view(art = dockArtifact.value as ArtifactRow): Node {
  const root = new Node(); renderProvenanceInto(root as unknown as HTMLElement, art); return root;
}
function open(art: ArtifactRow, sub: string): void { dockArtifact.value = art; provMode.value = true; provSub.value = sub; }
function response(value: unknown, status = 200): Response { return new Response(JSON.stringify(value), { status }); }
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const empty = { interactions: [], dependency_mappings: { inputs: [] } };
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

let testNavigation = 0;
beforeEach(() => {
  resetStoreFields(); currentId.value = "test-" + ++testNavigation; vi.clearAllMocks();
  vi.stubGlobal("document", { createElement: () => new Node(), getElementById: () => null });
});
afterEach(() => { setExecutionFetch(null); vi.unstubAllGlobals(); });

describe("provenance reads keep failure, absence, and ownership distinct", () => {
  it.each([null, {}, { interactions: [], dependency_mappings: { inputs: [42] } }, "network", 404, 500])("lineage failure %j exposes only a read retry", async (fault) => {
    const urls: string[] = [];
    let retry = false;
    setExecutionFetch(async (url, init) => {
      urls.push(url); expect(init?.method || "GET").toBe("GET");
      if (retry) return response({ ...empty, artifact_id: "a", version_id: "v1" });
      if (fault === "network") throw new Error("offline");
      return response(typeof fault === "number" ? { error: "read failed" } : fault, typeof fault === "number" ? fault : 200);
    });
    open({ id: "a", version_id: "v1" }, "review");
    showProvenance(dockArtifact.value);
    expect(text(view())).not.toContain(provenanceT("noLineage"));
    await settle();
    const failed = view();
    expect(button(failed)).toBeTruthy();
    expect(text(failed)).not.toContain(provenanceT("noLineage"));
    expect(lineage.value).toBeNull();
    retry = true; button(failed).onclick!(); await settle();
    expect(text(view())).toContain(provenanceT("noLineage"));
    expect(text(view())).toContain(provenanceT("noInputs"));
    expect(urls).toEqual(Array(2).fill("/api/v1/artifacts/a/lineage?version=v1"));
  });

  it.each(["success", "failure"])("A → B → A ignores old lineage %s after the newer A", async (old) => {
    const pending = [deferred<Response>(), deferred<Response>(), deferred<Response>()];
    let count = 0; setExecutionFetch(() => pending[count++]!.promise);
    open({ id: "a", version_id: "v1" }, "review"); showProvenance(dockArtifact.value);
    showProvenance({ id: "b", version_id: "v1" });
    showProvenance({ id: "a", version_id: "v1" });
    pending[2]!.resolve(response({ ...empty, artifact_id: "a", version_id: "v1", evidence: "new" })); await settle();
    pending[0]!.resolve(response(old === "success" ? { ...empty, version_id: "v1", evidence: "old" } : { error: "old error" }, old === "success" ? 200 : 500));
    pending[1]!.resolve(response({ ...empty, artifact_id: "b", version_id: "v1" })); await settle();
    expect(lineage.value).toMatchObject({ artifact_id: "a", evidence: "new" });
    expect(_lineageFor.value).toBe("a:v1");
    expect(button(view())).toBeUndefined();
  });

  it("a sidebar-only navigation preserves the retained frame's in-flight read and retry", async () => {
    const pending = deferred<Response>(); let calls = 0;
    setExecutionFetch(async () => ++calls === 1 ? pending.promise : response(empty));
    open({ id: "a" }, "review"); showProvenance(dockArtifact.value);
    _openGen.value++;
    pending.resolve(response({ error: "visible failure" }, 500)); await settle();
    const failed = view(); expect(text(failed)).toContain("visible failure");
    _openGen.value++;
    button(failed).onclick!(); await settle();
    expect(text(view())).toContain(provenanceT("noLineage")); expect(calls).toBe(2);
  });

  it("a recorded delegated Cell keeps its identity in Code without claiming the source was not recorded", () => {
    open({ id: "a" }, "code");
    lineage.value = { ...empty, producer: { kind: "cell", cell_recorded: true, producing_cell_id: "child-cell", frame_id: "child-frame", frame_kind: "delegate" } };
    _lineageFor.value = 'artifact-metadata-latest:["a","unknown"]';
    const root = view();
    expect(text(root)).toContain(provenanceT("codeElsewhere"));
    expect(text(root)).toContain("child-cell"); expect(text(root)).toContain("child-frame");
    expect(text(root)).not.toContain(provenanceT("noCode"));
    expect(walk(root).filter((n) => n.className === "prov-link")).toHaveLength(0);
  });

  it("a late lineage success cannot replace the new visit's error", async () => {
    const old = deferred<Response>(); let count = 0;
    setExecutionFetch(() => count++ === 0 ? old.promise : Promise.resolve(response({ error: "current failure" }, 500)));
    open({ id: "a" }, "review"); showProvenance(dockArtifact.value);
    _openGen.value++; showProvenance({ id: "a" }); await settle();
    old.resolve(response(empty)); await settle();
    expect(text(view())).toContain("current failure"); expect(button(view())).toBeTruthy();
    expect(lineage.value).toBeNull();
  });

  it.each([null, {}, { packages: [null] }, { source: "captured", packages: [], remote: [null] }, { source: "captured", packages: [], remote: {} }, 404, 500])("environment %j is a failed read, not an empty snapshot", async (fault) => {
    let calls = 0;
    setExecutionFetch(async (_url, init) => {
      expect(init?.method || "GET").toBe("GET"); calls++;
      return calls === 1 ? response(typeof fault === "number" ? { error: "failure", code: "environment_snapshot_unavailable" } : fault, typeof fault === "number" ? fault : 200)
        : response({ source: "captured", kind: "r", python_version: null, packages: [], package_count: 0 });
    });
    open({ id: "a", version_id: "v1" }, "environment");
    const root = view(); await settle();
    if (fault === 404) { expect(button(root)).toBeUndefined(); expect(_envSnapById.value["a:v1"]).toBeUndefined(); return; }
    expect(button(root)).toBeTruthy(); expect(_envSnapById.value["a:v1"]).toBeUndefined();
    button(root).onclick!(); await settle();
    expect(button(root)).toBeUndefined();
    expect(_envSnapById.value["a:v1"]).toMatchObject({ kind: "r" });
    expect(text(root)).not.toContain("Python"); expect(calls).toBe(2);
  });

  it("an unavailable historical package count is not reported as a measured zero", async () => {
    setExecutionFetch(async () => response({ source: "captured", kind: null, packages: [], package_count: null }));
    open({ id: "a" }, "environment"); const root = view(); await settle();
    const chips = walk(root).filter((node) => node.className === "env-chip");
    expect(text(chips.find((node) => text(node).includes("Packages"))!)).toContain(filesT("prov.env.packagesUnknown"));
    expect(text(root)).not.toContain("Python");
  });

  it.each(["success", "failure"])("an old environment %s cannot overwrite a newer same-key result or its cache", async (old) => {
    const pending = [deferred<Response>(), deferred<Response>(), deferred<Response>()]; let count = 0;
    setExecutionFetch(() => pending[count++]!.promise);
    open({ id: "a", version_id: "v1" }, "environment"); const oldRoot = view();
    open({ id: "b", version_id: "v1" }, "environment"); view();
    open({ id: "a", version_id: "v1" }, "environment"); const latest = view();
    pending[2]!.resolve(response({ source: "captured", packages: [], environment_name: "new A" })); await settle();
    pending[0]!.resolve(response(old === "success" ? { source: "captured", packages: [], environment_name: "old A" } : { error: "old failure" }, old === "success" ? 200 : 500));
    pending[1]!.resolve(response({ source: "captured", packages: [], environment_name: "B" })); await settle();
    expect(_envSnapById.value["a:v1"]).toMatchObject({ environment_name: "new A" });
    expect(_envSnapById.value["b:v1"]).toBeUndefined();
    expect(text(latest)).toContain("new A"); expect(text(latest)).not.toContain("old");
    expect(text(oldRoot)).not.toContain("old A");
  });

  it("reads messages from the artifact's owner and ignores stale replies", async () => {
    const first = deferred<{ messages: []; complete: boolean }>();
    vi.mocked(fetchRecentMessages).mockReturnValueOnce(first.promise).mockResolvedValueOnce({ messages: [] });
    currentId.value = "current-A";
    open({ id: "a", root_frame_id: "owner-B" }, "messages"); const firstView = view();
    expect(fetchRecentMessages).toHaveBeenCalledWith("owner-B", 500);
    open({ id: "b", root_frame_id: "owner-C" }, "messages"); const latest = view(); await settle();
    first.reject(new Error("stale B failure")); await settle();
    expect(button(firstView)).toBeUndefined(); expect(text(latest)).not.toContain("stale B failure");
    expect(fetchRecentMessages).toHaveBeenLastCalledWith("owner-C", 500);
    provSub.value = "exec";
    expect(text(view())).toContain(provenanceT("otherNotebook"));
  });
});

describe("record validation keeps legitimate historical evidence", () => {
  it("accepts save-only, nullable producer, unknown historic kinds and extension fields", () => {
    const value = { ...empty, interactions: [{ kind: "save", at: null }], producer: { kind: "non_cell", producing_cell_id: null, cell_recorded: false }, extra: { retained: true } };
    expect(validateLineage(value, { id: "a" })).toBe(value);
    expect(validateLineage({ ...empty, interactions: [{ kind: "historic-extension" }] }, { id: "a" }).interactions).toHaveLength(1);
  });
  it("accepts missing runtime fields, null remote environments, and both package formats without guessing Python", () => {
    const value = { source: "captured", packages: [], kind: null, package_count: null, implementation: null, generation_id: null, remote: [
      { host: "lab", service: "fold", engine: null, env: null },
      { host: "lab", service: "fold", env: { packages: ["torch==2.4.0"] } },
      { host: "lab", service: "fold", env: { packages: { torch: "2.4.0" } } },
    ] };
    expect(validateEnvironment(value)).toBe(value);
  });
  it.each([undefined, null, "bogus"])("rejects unverified environment source %j", (source) => {
    expect(() => validateEnvironment({ source, packages: [] })).toThrow();
  });
  it("never accepts a live daemon environment for an exact artifact version", () => {
    expect(() => validateEnvironment({ source: "live", packages: [] }, { id: "a", version_id: "v1", _exactVersion: true })).toThrow();
    expect(validateEnvironment({ source: "live", packages: [] }, { id: "a" }).source).toBe("live");
  });
  it.each([{ ...empty, producer: {} }, { ...empty, capture_observations: [{}] }, { ...empty, artifact_id: "other" }])("rejects malformed or wrong-owner evidence %j", (value) => {
    expect(() => validateLineage(value, { id: "a" })).toThrow();
  });
});

describe("recorded Cell links preserve identity", () => {
  const a = { id: "a", root_frame_id: "f" };
  const producer = { kind: "cell", producing_cell_id: "c-real", frame_id: "f", frame_kind: "session", cell_recorded: true };
  function review(p = producer): Node {
    const root = new Node(); renderProvReview(root as unknown as HTMLElement, a, { ...empty, producer: p, interactions: [{ kind: "cell", cell_index: 1, kernel_id: "r" }] }); return root;
  }
  function links(root: Node) { return walk(root).filter((n) => n.className === "prov-link"); }
  it("requires the owning session and an actual recorded identity; click rechecks navigation", () => {
    currentId.value = "other"; cells.value = [{ producing_cell_id: "c-real", cell_index: 1 }]; expect(links(review())).toHaveLength(0);
    currentId.value = "f"; cells.value = [{ producing_cell_id: "wrong", cell_index: 1 }]; expect(links(review())).toHaveLength(0);
    cells.value = [{ producing_cell_id: "c-real", cell_index: 1 }]; const root = review(); expect(links(root)).toHaveLength(1);
    expect(links(review({ ...producer, frame_kind: "delegate" }))).toHaveLength(0);
    currentId.value = "other"; links(root)[0]!.onclick!(); expect(renderNotebook).not.toHaveBeenCalled();
  });
  it("scrolls only to the producing identity, including a folded revision", () => {
    currentId.value = "f"; cells.value = [{ producing_cell_id: "new", _revisions: [{ producing_cell_id: "c-real", cell_index: 1, kernel_id: "r" }] }];
    const notebook = new Node(); const wrong = new Node(); wrong.setAttribute("data-producing-cell", "wrong");
    const selected = new Node(); selected.setAttribute("data-producing-cell", "c-real");
    const details = new Node(); details.tagName = "DETAILS"; details.appendChild(selected); details.parentElement = notebook;
    notebook.querySelectorAll = () => [wrong, selected];
    vi.stubGlobal("document", { createElement: () => new Node(), getElementById: () => notebook });
    vi.useFakeTimers();
    let frame!: () => void; vi.stubGlobal("requestAnimationFrame", (callback: () => void) => { frame = callback; });
    try { links(review())[0]!.onclick!(); frame(); expect(selected.scrollIntoView).toHaveBeenCalledOnce(); expect(wrong.scrollIntoView).not.toHaveBeenCalled(); expect(details.open).toBe(true); }
    finally { vi.runOnlyPendingTimers(); vi.useRealTimers(); }
  });
});
