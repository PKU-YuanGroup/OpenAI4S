import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { _artVer, dockArtifact, filesScope } from "../../stores/artifacts";
import { _liveCell, cells, liveCells } from "../../stores/notebook";
import { setNotebookRenderImpl } from "../notebook/scroll";
import { project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { activeTab, dock, openTabs } from "../../stores/ui";
import { setArtifactsFetch } from "./api";
import { artifactCreatedSideEffects, PROJECT_REFRESH_DELAY_MS } from "./events";
import { jsonResponse } from "./http-stub";
import { loadProjectArtifacts } from "./load";
import { resetFilesIndexState } from "./state";
import { filesT } from "./copy";

describe("artifact_created side effects (app.js:5314-5346)", () => {
  beforeEach(() => {
    resetStoreFields();
    resetFilesIndexState();
  });

  afterEach(() => {
    delete (globalThis as { nbRender?: unknown }).nbRender;
    setNotebookRenderImpl(null);
    vi.useRealTimers();
  });

  it("syncs the version cache when a produced file overwrites in place", () => {
    const dock = { id: "art1", version_id: "v1" };
    dockArtifact.value = dock;
    _artVer.value.art1 = "v1";
    artifactCreatedSideEffects({
      type: "artifact_created",
      artifact: { id: "art1", version_id: "v2", filename: "plot.png" },
    });
    expect(_artVer.value.art1).toBe("v2");
    expect(dock.version_id).toBe("v2");
  });

  // VIEW-3: a tab pinned to pca.png's first version kept saying nothing while
  // newer versions arrived.
  it("relabels a pinned tab and offers the latest in its viewer when a new version lands", () => {
    class Node {
      children: Node[] = []; className = ""; textContent = ""; title = "";
      dataset: Record<string, string> = {};
      classList = { add: (name: string) => { this.className += " " + name; } };
      onclick: unknown = null;
      set innerHTML(_value: string) { this.children = []; }
      appendChild(child: Node) { this.children.push(child); return child; }
      setAttribute() {}
      querySelectorAll(selector: string): Node[] {
        const name = selector.replace(/^\./, "");
        const out: Node[] = [];
        const walk = (n: Node) => n.children.forEach((kid) => {
          if (kid.className.split(/\s+/).includes(name)) out.push(kid);
          walk(kid);
        });
        walk(this);
        return out;
      }
    }
    const bar = new Node();
    const viewer = new Node();
    const slot = new Node();
    slot.className = "renderer-newer";
    slot.dataset.artifactId = "a-pca";
    slot.dataset.versionId = "v-1";
    viewer.appendChild(slot);
    vi.stubGlobal("document", {
      getElementById: (id: string) => (id === "dock-tabs" ? bar : null),
      createElement: () => new Node(),
      querySelectorAll: (selector: string) => viewer.querySelectorAll(selector),
    });
    try {
      const pinned = { id: "a-pca", filename: "pca.png", version_id: "v-1", _exactVersion: true };
      openTabs.value = [pinned];
      dockArtifact.value = pinned;
      activeTab.value = "artifact-version:" + JSON.stringify(["a-pca", "v-1"]);
      _artVer.value["a-pca"] = "v-1";
      artifactCreatedSideEffects({
        type: "artifact_created",
        artifact: { id: "a-pca", version_id: "v-2", filename: "pca.png" },
      });
      const label = bar.children
        .map((tab) => tab.children.find((node) => node.className === "t-name")?.textContent)
        .find((text) => String(text).startsWith("pca.png"));
      expect(label).toBe("pca.png · " + filesT("viewer.tab.older"));
      expect(slot.children.map((node) => node.textContent)).toEqual([
        filesT("viewer.version.newer"),
        filesT("viewer.version.openLatest"),
      ]);
      expect(pinned.version_id).toBe("v-1");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("appends a live figure onto the producing cell while a turn is running", () => {
    vi.useFakeTimers();
    running.value = true;
    dock.value = { open: true, tab: "notebook" };
    activeTab.value = "notebook";
    const cell = { producing_cell_id: "c1", live: true, figures: [] as string[] };
    liveCells.value = [cell];
    _liveCell.value = cell;
    // The Notebook's own render entry, not a window name nobody assigns.
    let painted = 0;
    setNotebookRenderImpl(() => {
      painted += 1;
    });
    artifactCreatedSideEffects({
      type: "artifact_created",
      artifact: {
        id: "img1",
        filename: "fig.png",
        content_type: "image/png",
        producing_cell_id: "c1",
      },
    });
    vi.runAllTimers();
    expect(cell.figures).toEqual(["fig.png"]);
    expect(painted).toBe(1);
    artifactCreatedSideEffects({
      type: "artifact_created",
      artifact: {
        id: "img1",
        filename: "fig.png",
        content_type: "image/png",
        producing_cell_id: "c1",
      },
    });
    vi.runAllTimers();
    expect(cell.figures).toEqual(["fig.png"]);
    expect(painted).toBe(1);
  });

  it("does not treat an F-05 stub as a live nbRender", () => {
    running.value = true;
    const cell = { producing_cell_id: "c1", live: true, figures: [] as string[] };
    _liveCell.value = cell;
    liveCells.value = [cell];
    const stub = Object.assign(
      () => {
        throw new Error("F-05 stub: window.nbRender is reserved");
      },
      { __openai4sContractStub: true },
    );
    (globalThis as { nbRender?: unknown }).nbRender = stub;
    expect(() =>
      artifactCreatedSideEffects({
        type: "artifact_created",
        artifact: { id: "img1", filename: "fig.png", content_type: "image/png", producing_cell_id: "c1" },
      }),
    ).not.toThrow();
    expect(cell.figures).toEqual(["fig.png"]);
  });
});


describe("project-scope artifact_created refresh (AUDIT P07)", () => {
  beforeEach(() => {
    resetStoreFields();
    resetFilesIndexState();
  });
  afterEach(() => {
    vi.useRealTimers();
    setArtifactsFetch(null);
  });

  async function projectListing(): Promise<string[]> {
    filesScope.value = "project";
    project.value = "p";
    const reads: string[] = [];
    setArtifactsFetch(async (url) => {
      reads.push(url);
      return jsonResponse({ artifacts: [], next_cursor: null, has_more: false });
    });
    await loadProjectArtifacts(true);
    reads.length = 0;
    return reads;
  }
  // Another session in the project produced a file.
  const created = (id: string) =>
    artifactCreatedSideEffects({ type: "artifact_created", root_frame_id: "other", artifact: { id, filename: `${id}.csv` } });

  it("reads nothing while Files is hidden, then refreshes once when it is shown", async () => {
    const reads = await projectListing();
    vi.useFakeTimers();
    for (const id of ["a", "b", "c"]) created(id);
    await vi.advanceTimersByTimeAsync(10 * PROJECT_REFRESH_DELAY_MS);
    expect(reads).toEqual([]);
    vi.useRealTimers();
    // What opening the Files tab calls.
    await loadProjectArtifacts();
    expect(reads).toHaveLength(1);
    await loadProjectArtifacts();
    expect(reads).toHaveLength(1);
  });

  it("refreshes a visible Files tab once per burst", async () => {
    const reads = await projectListing();
    dock.value = { open: true, tab: "files" };
    activeTab.value = "files";
    vi.useFakeTimers();
    for (const id of ["a", "b", "c"]) created(id);
    expect(reads).toEqual([]);
    await vi.advanceTimersByTimeAsync(PROJECT_REFRESH_DELAY_MS);
    expect(reads).toHaveLength(1);
    expect(reads[0]).toContain("/projects/p/artifact-index?");
  });
});


describe("confirmed capture attribution", () => {
  beforeEach(() => resetStoreFields());
  it("does not assign an unknown or absent producer to the active Cell", () => {
    const cell = { producing_cell_id: "c1", live: true, figures: [] as string[] };
    liveCells.value = [cell];
    _liveCell.value = cell;
    running.value = true;
    for (const producer of [undefined, "other"]) {
      artifactCreatedSideEffects({ artifact: { id: "a", version_id: "v1", filename: "plot.png", producing_cell_id: producer } });
    }
    expect(cell.figures).toEqual([]);
  });
  it("attaches a late same-byte observation to its completed Cell without changing others", () => {
    running.value = false;
    cells.value = [{ producing_cell_id: "c1", figures: ["plot.png"] }, { producing_cell_id: "c2", figures: ["plot.png"] }];
    const emit = (producer: string) => artifactCreatedSideEffects({ artifact: {
      id: "a", version_id: "v1", filename: "plot.png", producing_cell_id: producer,
    } });
    emit("c1");
    emit("c2");
    emit("c2");
    for (const cell of cells.value as { output_artifacts: unknown[] }[]) {
      expect(cell.output_artifacts).toEqual([{ filename: "plot.png", artifact_id: "a", version_id: "v1", url: "/api/v1/artifacts/versions/v1" }]);
    }
  });
});
