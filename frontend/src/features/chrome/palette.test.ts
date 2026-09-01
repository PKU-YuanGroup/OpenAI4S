import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { contractStub } from "../../compat/stub";
import { skillsCatalog } from "../../stores/customize";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";

const here = dirname(fileURLToPath(import.meta.url));

describe("F-20 command palette", () => {
  beforeEach(() => {
    resetStoreFields();
    vi.unstubAllGlobals();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("parseArtifactQuery keeps exact version_id and treats a missing one as latest", async () => {
    const { parseArtifactQuery } = await import("./palette");
    expect(parseArtifactQuery("")).toBeNull();
    expect(parseArtifactQuery("q=foo")).toBeNull();
    expect(parseArtifactQuery("?artifact=art_1")).toEqual({
      artifactId: "art_1",
      versionId: null,
    });
    expect(parseArtifactQuery("artifact=art_1&version_id=ver_9")).toEqual({
      artifactId: "art_1",
      versionId: "ver_9",
    });
    expect(parseArtifactQuery("?artifact=art_1&version_id=")).toEqual({
      artifactId: "art_1",
      versionId: null,
    });
  });

  it("M-03: opens the owning session then openViewer, forwarding version_id", async () => {
    const openConversation = vi.fn().mockResolvedValue(undefined);
    const openViewer = vi.fn();
    vi.stubGlobal("window", { openConversation, openViewer, setActiveTab: vi.fn() });
    currentId.value = "other";
    const { openPaletteArtifact, PAL } = await import("./palette");
    PAL.open = true;
    openPaletteArtifact({
      id: "art_1",
      filename: "table.csv",
      root_frame_id: "frame_9",
      project_id: "proj_1",
      version_id: "ver_exact",
    });
    expect(PAL.open).toBe(false);
    expect(openConversation).toHaveBeenCalledWith("frame_9", "proj_1");
    await Promise.resolve();
    await Promise.resolve();
    expect(openViewer).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "art_1",
        version_id: "ver_exact",
        root_frame_id: "frame_9",
      }),
    );
    const view = openViewer.mock.calls[0]?.[0] as { version_id?: string };
    expect(view.version_id).toBe("ver_exact");
  });

  it("M-03: does not rewrite a missing version_id into latest", async () => {
    const openViewer = vi.fn();
    vi.stubGlobal("window", { openViewer });
    currentId.value = "frame_9";
    const { openPaletteArtifact } = await import("./palette");
    openPaletteArtifact({
      id: "art_1",
      root_frame_id: "frame_9",
    });
    expect(openViewer).toHaveBeenCalled();
    const view = openViewer.mock.calls[0]?.[0] as Record<string, unknown>;
    expect(view).not.toHaveProperty("version_id");
  });

  it("falls back to Files tab when openViewer is a F-05 stub (typeof-function would throw)", async () => {
    const setActiveTab = vi.fn();
    const stub = contractStub("openViewer");
    vi.stubGlobal("window", {
      openViewer: stub,
      setActiveTab,
    });
    currentId.value = "frame_9";
    const { openPaletteArtifact } = await import("./palette");
    expect(() =>
      openPaletteArtifact({ id: "art_1", root_frame_id: "frame_9" }),
    ).not.toThrow();
    expect(setActiveTab).toHaveBeenCalledWith("files");
  });

  it("same-session hit skips openConversation", async () => {
    const openConversation = vi.fn();
    const openViewer = vi.fn();
    vi.stubGlobal("window", { openConversation, openViewer });
    currentId.value = "frame_9";
    const { openPaletteArtifact } = await import("./palette");
    openPaletteArtifact({ id: "art_1", root_frame_id: "frame_9" });
    expect(openConversation).not.toHaveBeenCalled();
    expect(openViewer).toHaveBeenCalled();
  });

  it("discards an out-of-order palSearch response (PAL.gen)", async () => {
    resetStoreFields();
    skillsCatalog.value = [];
    const list = {
      innerHTML: "keep",
      appendChild: vi.fn(),
      querySelectorAll: () => [],
    };
    vi.stubGlobal("window", {});
    vi.stubGlobal(
      "document",
      {
        createElement: (tag: string) => {
          const node: Record<string, unknown> = {
            tagName: tag.toUpperCase(),
            className: "",
            textContent: "",
            innerHTML: "",
            style: {},
            appendChild: vi.fn(),
            setAttribute: vi.fn(),
            addEventListener: vi.fn(),
            querySelectorAll: () => [],
          };
          return node;
        },
        body: { appendChild: vi.fn() },
      },
    );
    let finishFirst: (v: unknown) => void = () => undefined;
    const first = new Promise((resolve) => {
      finishFirst = resolve;
    });
    const fetchMock = vi
      .fn()
      .mockImplementationOnce(() => first.then((body) => ({
        ok: true,
        text: async () => JSON.stringify(body),
      })))
      .mockImplementationOnce(async () => ({
        ok: true,
        text: async () =>
          JSON.stringify({ sessions: [], artifacts: [], datapro: [] }),
      }));
    vi.stubGlobal("fetch", fetchMock);

    const { PAL, palSearch } = await import("./palette");
    PAL.listEl = list as unknown as HTMLElement;
    const slow = palSearch("alpha");
    const fast = palSearch("beta");
    await fast;
    finishFirst({
      sessions: [],
      artifacts: [{ id: "stale", filename: "stale.csv" }],
      datapro: [],
    });
    await slow;
    expect(PAL.items.some((it) => it.label === "stale.csv")).toBe(false);
  });

  it("source gates later-lane names with isReady and never imports window-exports", () => {
    const src = readFileSync(join(here, "palette.ts"), "utf8");
    expect(src).toContain("isReady");
    expect(src).not.toContain("window-exports");
    expect(src).not.toMatch(/typeof\s+\w+\s*===\s*["']function["']/);
  });
});
