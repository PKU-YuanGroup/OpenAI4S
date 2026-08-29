import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { _artVer, dockArtifact } from "../../stores/artifacts";
import { _liveCell, liveCells } from "../../stores/notebook";
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
    const cell = { id: "c1", figures: [] as string[] };
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
    const cell = { id: "c1", figures: [] as string[] };
    _liveCell.value = cell;
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
        artifact: { id: "img1", filename: "fig.png", content_type: "image/png" },
      }),
    ).not.toThrow();
    expect(cell.figures).toEqual(["fig.png"]);
  });
});
