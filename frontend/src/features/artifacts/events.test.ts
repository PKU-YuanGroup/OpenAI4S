import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { _artVer, dockArtifact } from "../../stores/artifacts";
import { _liveCell, cells, liveCells } from "../../stores/notebook";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { artifactCreatedSideEffects } from "./events";
import { resetFilesIndexState } from "./state";

describe("artifact_created side effects (app.js:5314-5346)", () => {
  beforeEach(() => {
    resetStoreFields();
    resetFilesIndexState();
  });

  afterEach(() => {
    delete (globalThis as { nbRender?: unknown }).nbRender;
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

  it("appends a live figure onto the producing cell while a turn is running", () => {
    running.value = true;
    const cell = { producing_cell_id: "c1", live: true, figures: [] as string[] };
    liveCells.value = [cell];
    _liveCell.value = cell;
    let painted = 0;
    (globalThis as { nbRender?: () => void }).nbRender = () => {
      painted += 1;
    };
    artifactCreatedSideEffects({
      type: "artifact_created",
      artifact: {
        id: "img1",
        filename: "fig.png",
        content_type: "image/png",
        producing_cell_id: "c1",
      },
    });
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
