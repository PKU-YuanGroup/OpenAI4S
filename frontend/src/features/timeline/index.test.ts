import { describe, expect, it } from "vitest";
import { installTimeline } from "./index";

describe("timeline window lane", () => {
  it("publishes loadWorkbenchState for later-lane callWindow/callLane", () => {
    const target: Record<string, unknown> = {};
    installTimeline(target);
    expect(typeof target.loadWorkbenchState).toBe("function");
    expect(typeof target.renderActionTimeline).toBe("function");
  });
});
