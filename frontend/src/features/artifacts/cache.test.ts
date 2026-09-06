import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  _artBust,
  _artVer,
  _envSnapById,
  artifacts,
  dockArtifact,
} from "../../stores/artifacts";
import { _lineageFor, _lineageReq, lineage } from "../../stores/notebook";
import { resetStoreFields } from "../../stores/signal-field";
import { currentId } from "../../stores/session";
import { openTabs } from "../../stores/ui";
import { ApiError, setArtifactsFetch } from "./api";
import { artifactCacheKey, artUrl, syncArtifactVersion } from "./cache";
import { jsonResponse } from "./http-stub";
import { loadArtifacts } from "./load";
import type { ArtifactRow } from "./types";

describe("artifact version cache (app.js:8353-8401)", () => {
  beforeEach(() => {
    resetStoreFields();
    setArtifactsFetch(null);
  });

  afterEach(() => {
    setArtifactsFetch(null);
  });

  it("artifactCacheKey prefers the seen version over the row", () => {
    expect(artifactCacheKey(null)).toBe("_live");
    expect(artifactCacheKey({ id: "" })).toBe("_live");
    const a: ArtifactRow = { id: "a1", version_id: "v1", checksum: "c1" };
    expect(artifactCacheKey(a)).toBe("a1:v1");
    _artVer.value.a1 = "v-seen";
    expect(artifactCacheKey(a)).toBe("a1:v-seen");
  });

  it("falls through version_id → latest_version_id → checksum → unknown", () => {
    expect(artifactCacheKey({ id: "x", latest_version_id: "L" })).toBe("x:L");
    expect(artifactCacheKey({ id: "x", checksum: "sum" })).toBe("x:sum");
    expect(artifactCacheKey({ id: "x" })).toBe("x:unknown");
  });

  it("syncArtifactVersion writes _artVer and mutates dock/openTabs in place", () => {
    const dock: ArtifactRow = { id: "a1", version_id: "v1" };
    dockArtifact.value = dock;
    const tab: ArtifactRow = { id: "a1", version_id: "v1" };
    const tabs = [tab, { id: "other", version_id: "z" }];
    openTabs.value = tabs;

    const changed = syncArtifactVersion({ id: "a1", version_id: "v2" }, false);
    expect(changed).toBe(true);
    expect(_artVer.value.a1).toBe("v2");
    expect(dockArtifact.value).toBe(dock);
    expect(dock.version_id).toBe("v2");
    expect(openTabs.value).toBe(tabs);
    expect(tab.version_id).toBe("v2");
    expect((tabs[1] as ArtifactRow).version_id).toBe("z");
  });

  it("does not report a change on the first sighting", () => {
    expect(syncArtifactVersion({ artifact_id: "a1", version_id: "v1" }, false)).toBe(false);
    expect(_artVer.value.a1).toBe("v1");
    expect(syncArtifactVersion({ id: "a1", version_id: "v1" }, false)).toBe(false);
  });

  it("force-refresh on the open dock busts lineage and env snapshot", () => {
    const dock: ArtifactRow = { id: "a1", version_id: "v1" };
    dockArtifact.value = dock;
    _artVer.value.a1 = "v1";
    lineage.value = { ok: true };
    _lineageFor.value = "a1";
    const key = artifactCacheKey(dock);
    _envSnapById.value[key] = { env: "py" };

    const changed = syncArtifactVersion({ id: "a1", version_id: "v1" }, true);
    expect(changed).toBe(true);
    expect(lineage.value).toBeNull();
    expect(_lineageFor.value).toBeNull();
    expect(_lineageReq.value).toBe(1);
    expect(_envSnapById.value[key]).toBeUndefined();
  });

  it("artUrl busts by _artBust and pins exact versions on the versions path", () => {
    const a: ArtifactRow = { id: "a1", version_id: "v9" };
    expect(artUrl(a)).toBe("/api/v1/artifacts/a1");
    _artBust.value.a1 = "v9";
    expect(artUrl(a)).toBe("/api/v1/artifacts/a1?_=v9");
    const pinned: ArtifactRow = { id: "a1", version_id: "v3", _exactVersion: true };
    expect(artUrl(pinned)).toBe("/api/v1/artifacts/versions/v3");
  });

  it("loadArtifacts drops a stale response after the session switches", async () => {
    currentId.value = "f1";
    let resolveFirst: ((body: string) => void) | undefined;
    const first = new Promise<string>((resolve) => {
      resolveFirst = resolve;
    });
    let calls = 0;
    setArtifactsFetch(async () => {
      calls += 1;
      if (calls === 1) {
        const text = await first;
        return jsonResponse(text);
      }
      return jsonResponse([{ artifact_id: "fresh", filename: "fresh.csv" }]);
    });

    const p1 = loadArtifacts("f1");
    currentId.value = "f2";
    const p2 = loadArtifacts("f2");
    resolveFirst!(JSON.stringify([{ artifact_id: "stale", filename: "old.csv" }]));
    await p1;
    await p2;
    const rows = artifacts.value as ArtifactRow[];
    expect(rows).toHaveLength(1);
    expect(rows[0]?.id).toBe("fresh");
  });

  it("ApiError carries code and request id", () => {
    const err = new ApiError({ error: "nope", code: "invalid_cursor", request_id: "r1" }, 400);
    expect(err.status).toBe(400);
    expect(err.code).toBe("invalid_cursor");
    expect(err.requestId).toBe("r1");
  });
});
