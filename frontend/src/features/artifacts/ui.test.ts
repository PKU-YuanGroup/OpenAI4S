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
