import { describe, expect, it, vi } from "vitest";

// C2: the Timeline's remote-task panel owns its own read.
//
// It is not one more entry in the workbench's `optionalApi` fan-out: a 503 has
// to stay a visible "records unavailable", not vanish into `undefined` (which
// the panel used to render as "no tasks", and a stale page used to render as
// the next session's tasks). The controller below is the whole ownership
// rule: a session, a filter, and a monotonic request token.
import { ApiError } from "./api";
import {
  computeRecordsPath,
  createComputeRecordsController,
} from "./compute-records";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((done, fail) => {
    resolve = done;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function page(tasks: unknown[], extras: Record<string, unknown> = {}) {
  return { tasks, live_count: tasks.length, ...extras };
}

describe("compute records read ownership", () => {
  it("keeps the last successful page and marks it stale when a reread fails", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(page([{ job_id: "a", live: true }], { total_live_count: 1 }))
      .mockRejectedValueOnce(new Error("HTTP 503"));
    const controller = createComputeRecordsController({ read, now: () => 42 });

    await controller.read("f");
    expect(controller.state?.phase).toBe("ready");
    expect(controller.state?.stale).toBe(false);
    expect(controller.state?.updatedAt).toBe(42);
    expect(controller.state?.page?.tasks).toHaveLength(1);

    await controller.read("f");
    expect(controller.state?.phase).toBe("error");
    expect(controller.state?.stale).toBe(true);
    expect(controller.state?.page?.tasks).toHaveLength(1);
    expect(controller.state?.error).toContain("503");
  });

  it("clears the old session's rows the moment another session is read", async () => {
    const late = deferred<unknown>();
    const read = vi
      .fn()
      .mockReturnValueOnce(late.promise)
      .mockResolvedValueOnce(page([]));
    const controller = createComputeRecordsController({ read });
    const first = controller.read("f");
    const second = controller.read("g");
    // The new session starts from unknown, not from f's records.
    expect(controller.state?.fid).toBe("g");
    expect(controller.state?.page).toBeNull();
    late.resolve(page([{ job_id: "f-task", live: true }]));
    await Promise.all([first, second]);
    expect(controller.state?.fid).toBe("g");
    expect(controller.state?.page?.tasks).toHaveLength(0);
  });

  it("drops a late answer for a filter the user has left", async () => {
    const late = deferred<unknown>();
    const read = vi
      .fn()
      .mockReturnValueOnce(late.promise)
      .mockResolvedValueOnce(page([], { total_live_count: 0 }));
    const controller = createComputeRecordsController({ read });

    const all = controller.read("f");
    const live = controller.read("f", { filter: "live" });
    expect(controller.state?.filter).toBe("live");
    expect(controller.state?.page).toBeNull();

    late.resolve(page([{ job_id: "old", live: true }], { total_live_count: 1 }));
    await Promise.all([all, live]);
    expect(controller.state?.filter).toBe("live");
    expect(controller.state?.page?.tasks).toHaveLength(0);
    expect(read).toHaveBeenLastCalledWith("f", "live", null);
  });

  it("starts each filter from unknown instead of reusing another filter's rows", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(page([{ job_id: "a", live: true }], { total_live_count: 1 }))
      .mockResolvedValueOnce(page([], { total_live_count: 1 }));
    const controller = createComputeRecordsController({ read });
    await controller.read("f");
    const pending = controller.read("f", { filter: "live" });
    expect(controller.state?.filter).toBe("live");
    expect(controller.state?.page).toBeNull();
    await pending;
    expect(controller.state?.page?.tasks).toHaveLength(0);
  });

  it("loads the next page from the cursor and appends without duplicates", async () => {
    const read = vi
      .fn()
      .mockResolvedValueOnce(
        page([{ job_id: "a", live: true }], {
          total_live_count: 2,
          has_more: true,
          next_cursor: "c1",
        }),
      )
      .mockResolvedValueOnce(
        page([{ job_id: "b", live: true }], {
          total_live_count: 2,
          has_more: false,
          next_cursor: null,
        }),
      );
    const controller = createComputeRecordsController({ read });
    await controller.read("f");
    await controller.read("f", { append: true });

    expect(read).toHaveBeenLastCalledWith("f", "all", "c1");
    expect(controller.state?.page?.tasks.map((task) => task.job_id)).toEqual(["a", "b"]);
    expect(controller.state?.page?.total_live_count).toBe(2);
    expect(controller.state?.loadingMore).toBe(false);
    expect(controller.state?.phase).toBe("ready");
  });

  it("does not load more when the page has no cursor", async () => {
    const read = vi.fn().mockResolvedValueOnce(
      page([], { has_more: false, next_cursor: null }),
    );
    const controller = createComputeRecordsController({ read });
    await controller.read("f");
    await controller.read("f", { append: true });
    expect(read).toHaveBeenCalledTimes(1);
  });

  it("exposes an in-flight load-more state without clearing the loaded page", async () => {
    const gate = deferred<unknown>();
    const read = vi
      .fn()
      .mockResolvedValueOnce(
        page([{ job_id: "a", live: true }], { has_more: true, next_cursor: "c1" }),
      )
      .mockReturnValueOnce(gate.promise);
    const controller = createComputeRecordsController({ read });
    await controller.read("f");
    const more = controller.read("f", { append: true });
    expect(controller.state?.loadingMore).toBe(true);
    expect(controller.state?.page?.tasks).toHaveLength(1);
    gate.resolve(page([{ job_id: "b", live: true }], { has_more: false }));
    await more;
    expect(controller.state?.loadingMore).toBe(false);
    expect(controller.state?.page?.tasks).toHaveLength(2);
  });

  it("builds the documented query parameters, encoding the session id", () => {
    expect(computeRecordsPath("f", "all", null)).toBe("/frames/f/compute/tasks");
    expect(computeRecordsPath("f/1", "live", "c 2")).toBe(
      "/frames/f%2F1/compute/tasks?state=live&cursor=c+2",
    );
  });
});

describe("C2 contract: the real 503 envelope", () => {
  it("turns the route's 503 into a visible failure instead of an empty page", async () => {
    // Verbatim shape from the merged route:
    // GatewayError(503, "compute records are unavailable right now",
    //              "compute_record_unavailable").
    const read = vi.fn().mockRejectedValue(
      new ApiError(
        {
          error: "compute records are unavailable right now",
          code: "compute_record_unavailable",
          status: 503,
        },
        503,
      ),
    );
    const controller = createComputeRecordsController({ read });
    await controller.read("f");
    expect(controller.state?.phase).toBe("error");
    expect(controller.state?.page).toBeNull();
    expect(controller.state?.stale).toBe(false);
    expect(controller.state?.error).toContain(
      "compute records are unavailable right now",
    );
  });
});
