import { effect } from "@preact/signals";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { variableInspector } from "../../stores/notebook";
import { resetStoreFields } from "../../stores/signal-field";
import { currentId } from "../../stores/session";
import { setExecutionFetch } from "./api";
import { refreshVariableInspector } from "./inspector";

type Inspector = { loading: string | null; results: Record<string, { variables?: Array<{ name?: string }> }> };

beforeEach(() => {
  resetStoreFields();
  currentId.value = "frame-v";
});
afterEach(() => setExecutionFetch(null));

describe("variable inspector state", () => {
  it("publishes each step as a new object, so a subscriber sees loading and results", async () => {
    setExecutionFetch(async () =>
      new Response(
        JSON.stringify({
          root_frame_id: "frame-v",
          branch_id: "frame-v",
          language: "python",
          state: "active",
          available: true,
          state_revision: 3,
          variables: [{ name: "x", type: "int", preview: 1 }],
        }),
      ),
    );
    const seen: Inspector[] = [];
    const stop = effect(() => {
      seen.push(variableInspector.value as Inspector);
    });
    await refreshVariableInspector();
    stop();
    expect(seen.map((state) => state.loading)).toEqual([null, "python", "python", null]);
    expect(seen[seen.length - 1]!.results.python!.variables![0]!.name).toBe("x");
  });
});
