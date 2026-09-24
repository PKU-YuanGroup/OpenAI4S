import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activeTab, openTabs } from "../../stores/ui";
import { artifactTabKey, syncArtifactVersion } from "./cache";
import { dockArtifact } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import { jsonResponse } from "./http-stub";
import { viewerVersionState } from "./state";
import type { ArtifactRow, ArtifactVersionRow } from "./types";
import {
  addOpenTab,
  closeTab,
  setActiveTab,
  applyArtifactDeepLink,
  consumeArtifactDeepLink,
  copyArtifactDeepLink,
  openViewer,
} from "./ui";

const versions: ArtifactVersionRow[] = [
  { version_id: "v-new", is_latest: true, ordinal: 2 },
  { version_id: "v-old", is_latest: false, ordinal: 1 },
];

describe("M-03 deep-link apply / openViewer", () => {
  beforeEach(() => {
    resetStoreFields();
    viewerVersionState.value = null;
    setArtifactsFetch(async () => jsonResponse({ versions }));
  });

  afterEach(() => {
    setArtifactsFetch(null);
  });

  it("omitted version_id opens latest without pinning _exactVersion", async () => {
    await applyArtifactDeepLink({ artifactId: "art-1", versionId: null });
    expect(viewerVersionState.value?.status).toBe("latest");
    const docked = dockArtifact.value as ArtifactRow;
    expect(docked._exactVersion).toBeUndefined();
    expect(docked.version_id).toBe("v-new");
  });

  it("provided version_id opens the exact snapshot", async () => {
    await applyArtifactDeepLink({ artifactId: "art-1", versionId: "v-old" });
    expect(viewerVersionState.value?.status).toBe("exact");
    const docked = dockArtifact.value as ArtifactRow;
    expect(docked._exactVersion).toBe(true);
    expect(docked.version_id).toBe("v-old");
  });

  it("stale exact version does not silently open latest", async () => {
    await applyArtifactDeepLink({ artifactId: "art-1", versionId: "v-missing" });
    expect(viewerVersionState.value?.status).toBe("stale");
    if (viewerVersionState.value?.status === "stale") {
      expect(viewerVersionState.value.versionId).toBe("v-missing");
      expect(viewerVersionState.value.latestVersionId).toBe("v-new");
    }
    const docked = dockArtifact.value as ArtifactRow;
    expect(docked.version_id).toBeUndefined();
    expect(docked._exactVersion).toBeUndefined();
  });

  it("not-found does not invent a latest artifact", async () => {
    setArtifactsFetch(async () => jsonResponse({ versions: [] }));
    await applyArtifactDeepLink({ artifactId: "nope", versionId: "v-old" });
    expect(viewerVersionState.value?.status).toBe("not-found");
    const docked = dockArtifact.value as ArtifactRow;
    expect(docked.id).toBe("nope");
    expect(docked.version_id).toBeUndefined();
  });

  it("a versions fetch failure is not-found, not a silent latest", async () => {
    setArtifactsFetch(async () => jsonResponse({ error: "boom" }, 500));
    await applyArtifactDeepLink({ artifactId: "art-1", versionId: "v-old" });
    expect(viewerVersionState.value?.status).toBe("not-found");
    expect((dockArtifact.value as ArtifactRow).version_id).toBeUndefined();
  });

  it("consumeArtifactDeepLink parses the query and pins exact version", async () => {
    await consumeArtifactDeepLink("?artifact=art-1&version_id=v-old");
    expect(viewerVersionState.value?.status).toBe("exact");
    expect((dockArtifact.value as ArtifactRow).version_id).toBe("v-old");
  });

  it("openViewer with version_id resolves exact and never falls back", async () => {
    await openViewer({ id: "art-1", version_id: "v-old" });
    expect(viewerVersionState.value?.status).toBe("exact");
    expect((dockArtifact.value as ArtifactRow).version_id).toBe("v-old");
    await openViewer({ id: "art-1", version_id: "v-missing" });
    expect(viewerVersionState.value?.status).toBe("stale");
    expect((dockArtifact.value as ArtifactRow).version_id).toBeUndefined();
  });

  it("keeps the selected row's stable owner while resolving an exact version", async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => { release = resolve; });
    setArtifactsFetch(async () => { await held; return jsonResponse({ versions }); });
    const row: ArtifactRow = { id: "art-1", version_id: "v-old", root_frame_id: "original-frame", project_id: "original-project", producing_cell_id: "head-cell" };
    const pending = openViewer(row);
    row.root_frame_id = "changed-frame"; row.project_id = "changed-project";
    release(); await pending;
    expect(dockArtifact.value).toMatchObject({ root_frame_id: "original-frame", project_id: "original-project", version_id: "v-old", _exactVersion: true });
    expect((dockArtifact.value as ArtifactRow).producing_cell_id).toBeUndefined();
  });

  it("does not borrow ownership from another artifact or guess it for a bare deep link", async () => {
    await applyArtifactDeepLink({ artifactId: "art-1", versionId: "v-old" }, { id: "other", root_frame_id: "wrong-frame" });
    expect((dockArtifact.value as ArtifactRow).root_frame_id).toBeUndefined();
  });

  it("enriches an already open unknown exact tab and retains ownership after switching tabs", async () => {
    await applyArtifactDeepLink({ artifactId: "art-1", versionId: "v-old" });
    expect((dockArtifact.value as ArtifactRow).root_frame_id).toBeUndefined();
    await openViewer({ id: "art-1", version_id: "v-old", root_frame_id: "real-frame", project_id: "real-project", producing_cell_id: "wrong-head-cell" });
    openViewer({ id: "other" });
    setActiveTab(artifactTabKey({ id: "art-1", version_id: "v-old", _exactVersion: true }));
    expect(dockArtifact.value).toMatchObject({ root_frame_id: "real-frame", project_id: "real-project", version_id: "v-old" });
    expect((dockArtifact.value as ArtifactRow).producing_cell_id).toBeUndefined();
  });

  it("copyable deep link omits version_id for latest and includes it for exact", async () => {
    const latest = await copyArtifactDeepLink({ id: "art-1", version_id: "v-new" });
    expect(latest).toContain("artifact=art-1");
    expect(latest).not.toContain("version_id=");
    const exact = await copyArtifactDeepLink({
      id: "art-1",
      version_id: "v-old",
      _exactVersion: true,
    });
    expect(exact).toContain("version_id=v-old");
  });
});


describe("version-specific tabs", () => {
  beforeEach(() => resetStoreFields());
  it("keeps exact and latest side by side and closes only the selected identity", () => {
    const exact: ArtifactRow = { id: "a", version_id: "v1", _exactVersion: true };
    const latest: ArtifactRow = { id: "a", version_id: "v1" };
    addOpenTab(exact);
    addOpenTab(latest);
    expect(openTabs.value).toHaveLength(2);
    setActiveTab(artifactTabKey(exact));
    syncArtifactVersion({ id: "a", version_id: "v2" }, true);
    expect(dockArtifact.value).toBe(exact);
    expect(exact.version_id).toBe("v1");
    setActiveTab(artifactTabKey(latest));
    expect(viewerVersionState.value?.status).toBe("latest");
    expect((dockArtifact.value as ArtifactRow).version_id).toBe("v2");
    setActiveTab(artifactTabKey(exact));
    closeTab(artifactTabKey(exact));
    expect(openTabs.value).toEqual([latest]);
    expect(activeTab.value).toBe("a");
  });
  it("a missing pinned version cannot select an already open latest tab", async () => {
    const latest: ArtifactRow = { id: "a", version_id: "v-new" };
    addOpenTab(latest);
    setArtifactsFetch(async () => jsonResponse({ versions }));
    await applyArtifactDeepLink({ artifactId: "a", versionId: "missing" });
    expect(viewerVersionState.value?.status).toBe("stale");
    expect(dockArtifact.value).not.toBe(latest);
    setArtifactsFetch(null);
  });
  it("closing a background tab takes it off the tab bar (AUDIT A59)", () => {
    class Node {
      children: Node[] = []; className = ""; textContent = ""; title = "";
      classList = { add: (name: string) => { this.className += " " + name; } };
      onclick: ((event: { stopPropagation(): void }) => void) | null = null;
      set innerHTML(_value: string) { this.children = []; }
      appendChild(child: Node) { this.children.push(child); return child; }
    }
    const bar = new Node();
    vi.stubGlobal("document", { getElementById: (id: string) => (id === "dock-tabs" ? bar : null), createElement: () => new Node() });
    const names = () => bar.children.map((tab) => tab.children.find((node) => node.className === "t-name")?.textContent);
    try {
      addOpenTab({ id: "a", filename: "a.txt" });
      addOpenTab({ id: "b", filename: "b.txt" });
      setActiveTab("a");
      expect(names()).toEqual(["a.txt", "b.txt", "Notebook", expect.any(String)]);
      closeTab("b");
      expect(activeTab.value).toBe("a");
      expect(names()).toEqual(["a.txt", "Notebook", expect.any(String)]);
    } finally { vi.unstubAllGlobals(); }
  });
});


it("a failed session read shows Retry instead of an empty grid (AUDIT A26)", async () => {
  const { currentId } = await import("../../stores/session");
  const { renderFilesGrid } = await import("./ui");
  const { loadArtifacts } = await import("./load");
  const { filesReadFailed } = await import("./files-index");
  const { resetFilesIndexState } = await import("./state");
  const { filesT } = await import("./copy");
  const { translate } = await import("./api");
  class Node {
    children: Node[] = []; className = ""; textContent = ""; title = ""; src = "";
    dataset: Record<string, string> = {};
    onclick?: () => unknown;
    set innerHTML(_value: string) { this.children = []; }
    appendChild(child: Node) { this.children.push(child); return child; }
    setAttribute() {}
  }
  const walk = (node: Node): Node[] => [node, ...node.children.flatMap(walk)];
  const list = new Node();
  vi.stubGlobal("document", {
    getElementById: (id: string) => (id === "results-list" ? list : id === "results-count" ? new Node() : null),
    createElement: () => new Node(),
  });
  resetStoreFields();
  resetFilesIndexState();
  let reads = 0;
  setArtifactsFetch(async () => {
    reads += 1;
    return reads === 1
      ? jsonResponse({ error: "daemon restarting" }, 503)
      : jsonResponse([{ id: "x", filename: "x.bin", content_type: "application/octet-stream" }]);
  });
  try {
    currentId.value = "s";
    await loadArtifacts("s");
    renderFilesGrid();
    const texts = walk(list).map((node) => node.textContent);
    expect(texts).toContain(filesT("files.read.failed"));
    expect(texts).not.toContain(translate("files.empty"));
    walk(list).find((node) => node.textContent === translate("common.retry"))?.onclick?.();
    await vi.waitFor(() => expect(filesReadFailed()).toBe(false));
    expect(reads).toBe(2);
    renderFilesGrid();
    expect(walk(list).some((node) => node.className === "art")).toBe(true);
    expect(walk(list).some((node) => node.className.includes("files-read-error"))).toBe(false);
  } finally {
    vi.unstubAllGlobals();
    setArtifactsFetch(null);
    resetStoreFields();
  }
});


it("the Files grid says Loading, not empty, while the project index read is in flight (AUDIT A63)", async () => {
  const { filesScope } = await import("../../stores/artifacts");
  const { project } = await import("../../stores/session");
  const { renderFilesGrid } = await import("./ui");
  const { browseFiles } = await import("./files-index");
  const { resetFilesIndexState } = await import("./state");
  const { translate } = await import("./api");
  class Node {
    children: Node[] = []; className = ""; textContent = "";
    set innerHTML(_value: string) { this.children = []; }
    appendChild(child: Node) { this.children.push(child); return child; }
    setAttribute() {}
  }
  const list = new Node();
  const count = new Node();
  vi.stubGlobal("document", {
    getElementById: (id: string) => (id === "results-list" ? list : id === "results-count" ? count : null),
    createElement: () => new Node(),
  });
  resetStoreFields();
  resetFilesIndexState();
  let release!: () => void;
  const held = new Promise<void>((resolve) => { release = resolve; });
  setArtifactsFetch(async () => { await held; return jsonResponse({ artifacts: [], next_cursor: null, has_more: false }); });
  try {
    filesScope.value = "project";
    project.value = "p";
    const reading = browseFiles({ reset: true });
    renderFilesGrid();
    expect(list.children.map((node) => node.textContent)).toEqual([translate("common.loading")]);
    expect(count.textContent).toBe("…");
    release();
    await reading;
    renderFilesGrid();
    expect(list.children.map((node) => node.textContent)).toEqual([translate("files.emptyProject")]);
    expect(count.textContent).toBe("0");
  } finally {
    vi.unstubAllGlobals();
    setArtifactsFetch(null);
    resetStoreFields();
  }
});


it("fullscreen download keeps the selected immutable version", async () => {
  const { openArtifact } = await import("../../islands/viewer");
  const download = { style: { display: "" }, href: "", setAttribute: vi.fn() };
  vi.stubGlobal("document", { querySelector: (selector: string) => selector === "#modal-download" ? download : null });
  try {
    openArtifact({ id: "a", filename: "table.csv", version_id: "v1", _exactVersion: true });
    expect(download.href).toBe("/api/v1/artifacts/versions/v1");
    expect(download.setAttribute).toHaveBeenCalledWith("download", "table.csv");
  } finally { vi.unstubAllGlobals(); }
});


it("conversation strip tiles open the latest tab rather than pinning the head at click time", async () => {
  const { artifacts } = await import("../../stores/artifacts");
  const { renderConversationArtifacts } = await import("./ui");
  class Node {
    children: Node[] = []; textContent = ""; className = ""; id = ""; src = "";
    onclick?: () => unknown;
    appendChild(child: Node) { this.children.push(child); return child; }
    insertBefore(child: Node) { this.children.push(child); return child; }
    querySelector() { return null; }
    remove() {}
  }
  resetStoreFields();
  viewerVersionState.value = null;
  const requests: string[] = [];
  const fetcher = vi.fn(async (url: string) => { requests.push(url); return jsonResponse({ versions }); });
  setArtifactsFetch(fetcher);
  // The list serializer stamps every row with the head's version_id.
  artifacts.value = [{ id: "art-1", filename: "notes.txt", content_type: "text/plain", version_id: "v-new", latest_version_id: "v-new" }];
  const host = new Node();
  vi.stubGlobal("document", {
    querySelectorAll: () => [],
    querySelector: () => null,
    getElementById: (id: string) => id === "messages" ? host : null,
    createElement: () => new Node(),
  });
  try {
    renderConversationArtifacts();
    const walk = (node: Node): Node[] => [node, ...node.children.flatMap(walk)];
    const tile = walk(host).find((node) => node.className === "tile");
    expect(tile).toBeDefined();
    await tile?.onclick?.();
    const state = viewerVersionState.value as { status?: string } | null;
    expect(state?.status).toBe("latest");
    const tabs = openTabs.value as ArtifactRow[];
    expect(tabs.map((row) => [row.id, row._exactVersion])).toEqual([["art-1", undefined]]);
    expect(activeTab.value).toBe("art-1");
    // openViewer would have resolved the pin through /versions; presentViewer
    // only lets the viewer read the latest bytes.
    expect(requests.some((url) => url.includes("/versions"))).toBe(false);
    for (let i = 0; i < 4; i++) await Promise.resolve();
  } finally {
    vi.unstubAllGlobals();
    setArtifactsFetch(null);
    resetStoreFields();
  }
});
