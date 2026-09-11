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
