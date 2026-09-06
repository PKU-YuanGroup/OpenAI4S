import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { dockArtifact } from "../../stores/artifacts";
import { resetStoreFields } from "../../stores/signal-field";
import { setArtifactsFetch } from "./api";
import { jsonResponse } from "./http-stub";
import { viewerVersionState } from "./state";
import type { ArtifactRow, ArtifactVersionRow } from "./types";
import {
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
