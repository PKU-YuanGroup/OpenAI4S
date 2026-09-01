import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { setArtifactsFetch } from "./api";
import {
  artifactDeepLinkSearch,
  parseArtifactDeepLink,
  resolveArtifactVersion,
} from "./deeplink";
import { jsonResponse } from "./http-stub";
import type { ArtifactRow, ArtifactVersionRow } from "./types";

const versions: ArtifactVersionRow[] = [
  { version_id: "v-new", is_latest: true, ordinal: 2 },
  { version_id: "v-old", is_latest: false, ordinal: 1 },
];

const artifact: ArtifactRow = {
  id: "art-1",
  artifact_id: "art-1",
  filename: "plot.png",
  version_id: "v-new",
  latest_version_id: "v-new",
};

describe("M-03 artifact deep-link parse", () => {
  it("reads artifact id and omits version_id as latest", () => {
    expect(parseArtifactDeepLink("?artifact=art-1")).toEqual({
      artifactId: "art-1",
      versionId: null,
    });
    expect(parseArtifactDeepLink("artifact=art-1")).toEqual({
      artifactId: "art-1",
      versionId: null,
    });
  });

  it("reads an explicit version_id without coercing empty to latest at parse time", () => {
    expect(parseArtifactDeepLink("?artifact=art-1&version_id=v-old")).toEqual({
      artifactId: "art-1",
      versionId: "v-old",
    });
    expect(parseArtifactDeepLink("?artifact=art-1&version_id=")).toEqual({
      artifactId: "art-1",
      versionId: null,
    });
  });

  it("returns null when artifact is missing", () => {
    expect(parseArtifactDeepLink("")).toBeNull();
    expect(parseArtifactDeepLink("?version_id=v-old")).toBeNull();
    expect(parseArtifactDeepLink("?artifact=")).toBeNull();
  });

  it("serializes a copyable query; omits version_id for latest", () => {
    expect(artifactDeepLinkSearch("art-1")).toBe("?artifact=art-1");
    expect(artifactDeepLinkSearch("art-1", null)).toBe("?artifact=art-1");
    expect(artifactDeepLinkSearch("art-1", "v-old")).toBe("?artifact=art-1&version_id=v-old");
  });
});

describe("M-03 exact version resolve", () => {
  const fetchVersions = async () => versions;
  const fetchArtifact = async (id: string) => (id === artifact.id ? artifact : null);

  it("omitted version_id resolves to latest", async () => {
    const result = await resolveArtifactVersion(
      { artifactId: "art-1", versionId: null },
      fetchVersions,
      fetchArtifact,
    );
    expect(result.status).toBe("latest");
    if (result.status === "latest") {
      expect(result.versionId).toBe("v-new");
      expect(result.artifact._exactVersion).toBeUndefined();
    }
  });

  it("provided version_id hits the exact immutable snapshot", async () => {
    const result = await resolveArtifactVersion(
      { artifactId: "art-1", versionId: "v-old" },
      fetchVersions,
      fetchArtifact,
    );
    expect(result.status).toBe("exact");
    if (result.status === "exact") {
      expect(result.versionId).toBe("v-old");
      expect(result.artifact.version_id).toBe("v-old");
      expect(result.artifact._exactVersion).toBe(true);
    }
  });

  it("never silently substitutes latest when the exact version is gone", async () => {
    const result = await resolveArtifactVersion(
      { artifactId: "art-1", versionId: "v-missing" },
      fetchVersions,
      fetchArtifact,
    );
    expect(result.status).toBe("stale");
    if (result.status === "stale") {
      expect(result.versionId).toBe("v-missing");
      expect(result.latestVersionId).toBe("v-new");
    }
  });

  it("reports not-found when the artifact itself is gone", async () => {
    const result = await resolveArtifactVersion(
      { artifactId: "nope", versionId: "v-old" },
      fetchVersions,
      fetchArtifact,
    );
    expect(result).toEqual({
      status: "not-found",
      artifactId: "nope",
      versionId: "v-old",
    });
  });
});

describe("M-03 default version fetch (no silent latest)", () => {
  beforeEach(() => {
    setArtifactsFetch(null);
  });
  afterEach(() => {
    setArtifactsFetch(null);
  });

  it("treats an empty versions list as not-found, not a ghost latest", async () => {
    setArtifactsFetch(async () => jsonResponse({ versions: [] }));
    const result = await resolveArtifactVersion({ artifactId: "gone", versionId: "v-old" });
    expect(result.status).toBe("not-found");
    if (result.status === "not-found") {
      expect(result.artifactId).toBe("gone");
      expect(result.versionId).toBe("v-old");
    }
  });

  it("pins the exact version from GET /versions and never rewrites it to latest", async () => {
    setArtifactsFetch(async (url) => {
      expect(url).toContain("/artifacts/art-1/versions");
      return jsonResponse({ versions });
    });
    const result = await resolveArtifactVersion({ artifactId: "art-1", versionId: "v-old" });
    expect(result.status).toBe("exact");
    if (result.status === "exact") {
      expect(result.versionId).toBe("v-old");
      expect(result.artifact._exactVersion).toBe(true);
      expect(result.artifact.version_id).toBe("v-old");
    }
  });
});
