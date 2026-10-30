import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { activeTab } from "../../stores/ui";
import { computeRecordsT } from "./copy";
import { loadComputeTasks, renderComputeTasksPanel, resetComputeTasks } from "./island";
import { S } from "./s";
import type { ComputeTasksView } from "./types";

// C2 / PLAN §6: the remote-task panel is its own read of the C1 listing.
//
// These are behaviour tests over an HTTP stand-in: the read state lands in
// `S.computeTasks` exactly as appendix B.1 / §6 item 1 describe it, and the
// panel renders the states (loading, unknown, unavailable, successful empty,
// list) with the session/filter/paging rules. No source-string assertions.

class FakeEl {
  tag: string;
  className = "";
  text = "";
  id = "";
  title = "";
  type = "";
  disabled = false;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  attrs = new Map<string, string>();
  children: FakeEl[] = [];
  parentNode: FakeEl | null = null;
  onclick: (() => void) | null = null;
  classList = {
    contains: (name: string) => this.className.split(" ").includes(name),
    add: (...names: string[]) => {
      const list = this.className.split(" ").filter(Boolean);
      names.forEach((name) => {
        if (!list.includes(name)) list.push(name);
      });
      this.className = list.join(" ");
    },
    remove: (...names: string[]) => {
      this.className = this.className
        .split(" ")
        .filter((name) => name && !names.includes(name))
        .join(" ");
    },
    toggle: (name: string, force?: boolean) => {
      const on = force ?? !this.classList.contains(name);
      if (on) this.classList.add(name);
      else this.classList.remove(name);
      return on;
    },
  };
  constructor(tag: string) {
    this.tag = tag;
  }
  get firstChild(): FakeEl | null {
    return this.children[0] || null;
  }
  set innerHTML(_value: string) {
    this.children = [];
  }
  set textContent(value: string) {
    this.text = String(value);
  }
  get textContent(): string {
    return [this.text, ...this.children.map((child) => child.textContent)].join("");
  }
  appendChild(child: FakeEl): FakeEl {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child: FakeEl, before: FakeEl | null): FakeEl {
    child.parentNode = this;
    const at = before ? this.children.indexOf(before) : -1;
    if (at < 0) this.children.push(child);
    else this.children.splice(at, 0, child);
    return child;
  }
  remove(): void {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter((node) => node !== this);
    }
    this.parentNode = null;
  }
  setAttribute(name: string, value: string): void {
    this.attrs.set(name, String(value));
  }
  getAttribute(name: string): string | null {
    return this.attrs.has(name) ? this.attrs.get(name)! : null;
  }
  removeAttribute(name: string): void {
    this.attrs.delete(name);
  }
  contains(): boolean {
    return true;
  }
}

function installDom(): void {
  vi.stubGlobal("document", {
    createElement: (tag: string) => new FakeEl(tag),
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    querySelector: () => null,
    activeElement: null,
    body: new FakeEl("div"),
  });
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
}

function textOf(node: unknown): string {
  return node instanceof FakeEl ? node.textContent : "";
}

function findAction(node: FakeEl, action: string): FakeEl | null {
  if (node.dataset.action === action) return node;
  for (const child of node.children) {
    const found = findAction(child, action);
    if (found) return found;
  }
  return null;
}

type Reply = { status?: number; body: unknown };

function stubFetch(handler: (path: string) => Reply): string[] {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const path = String(url).replace(/^.*\/api\/v1/, "");
      calls.push(path);
      const reply = handler(path);
      const status = reply.status ?? 200;
      return {
        ok: status >= 200 && status < 300,
        status,
        text: async () => JSON.stringify(reply.body),
      };
    }),
  );
  return calls;
}

function deferredReply(): { promise: Promise<unknown>; release: (body: unknown) => void } {
  let release!: (body: unknown) => void;
  const promise = new Promise((resolve) => {
    release = (body) =>
      resolve({ ok: true, status: 200, text: async () => JSON.stringify(body) });
  });
  return { promise, release };
}

function task(jobId: string, overrides: Record<string, unknown> = {}) {
  return {
    job_id: jobId,
    provider: "byoc-test",
    status: "running",
    live: true,
    terminal: false,
    updated_at: Date.now() - 300_000,
    outputs: { file_count: 0, total_bytes: 0 },
    ...overrides,
  };
}

function page(tasks: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    tasks,
    live_count: tasks.filter((row) => (row as { live?: boolean }).live).length,
    polled: false,
    total_live_count: tasks.filter((row) => (row as { live?: boolean }).live).length,
    has_more: false,
    next_cursor: null,
    ...overrides,
  };
}

const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  resetStoreFields();
  installDom();
  resetComputeTasks();
  S.currentId = "f";
  activeTab.value = "timeline";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("compute task read states", () => {
  it("turns a 503 into 'records unavailable' with a retry, never 'no tasks'", async () => {
    stubFetch(() => ({
      status: 503,
      body: {
        error: "compute records are temporarily unavailable",
        code: "compute_record_unavailable",
      },
    }));
    await loadComputeTasks("f");

    const view = S.computeTasks as ComputeTasksView;
    expect(view.status).toBe("error");
    expect("stale" in view).toBe(false);
    const panel = renderComputeTasksPanel();
    const text = textOf(panel);
    expect(text).toContain(computeRecordsT("compute.recordsUnavailable"));
    expect(text).not.toContain(computeRecordsT("compute.recordsNone"));
    expect(findAction(panel as unknown as FakeEl, "compute-retry")).not.toBeNull();
  });

  it("keeps the last successful page as stale for the same session and filter", async () => {
    stubFetch(() => ({ body: page([task("job-a")], { total_live_count: 1 }) }));
    await loadComputeTasks("f");
    expect((S.computeTasks as ComputeTasksView).status).toBe("ok");

    stubFetch(() => ({ status: 503, body: { error: "unavailable" } }));
    await loadComputeTasks("f");
    const view = S.computeTasks as ComputeTasksView;
    expect(view.status).toBe("error");
    expect(view.status === "error" && view.stale?.data.tasks[0]?.job_id).toBe("job-a");

    const panel = renderComputeTasksPanel();
    const text = textOf(panel);
    expect(text).toContain("job-a");
    expect(text).toContain(computeRecordsT("compute.recordsStale", ""));
    expect(text).not.toContain(computeRecordsT("compute.recordsNone"));
    expect(findAction(panel as unknown as FakeEl, "compute-retry")).not.toBeNull();
  });

  it("does not show session A's tasks when session B's read fails", async () => {
    stubFetch(() => ({ body: page([task("job-a")], { total_live_count: 1 }) }));
    await loadComputeTasks("f");
    expect(textOf(renderComputeTasksPanel())).toContain("job-a");

    S.currentId = "g";
    stubFetch(() => ({ status: 503, body: { error: "unavailable" } }));
    await loadComputeTasks("g");

    const view = S.computeTasks as ComputeTasksView;
    expect(view.fid).toBe("g");
    const text = textOf(renderComputeTasksPanel());
    expect(text).not.toContain("job-a");
    expect(text).toContain(computeRecordsT("compute.recordsUnavailable"));
  });

  it("shows 'no remote tasks' only for a successful empty page", async () => {
    stubFetch(() => ({ body: page([], { total_live_count: 0 }) }));
    await loadComputeTasks("f");
    const text = textOf(renderComputeTasksPanel());
    expect(text).toContain(computeRecordsT("compute.recordsNone"));
    expect(text).not.toContain(computeRecordsT("compute.recordsUnavailable"));
  });

  it("says 'in flight on this page' when an old service omits total_live_count", async () => {
    stubFetch(() => ({
      body: {
        tasks: [task("job-a")],
        live_count: 1,
        polled: false,
        has_more: false,
        next_cursor: null,
      },
    }));
    await loadComputeTasks("f");
    const view = S.computeTasks as ComputeTasksView;
    expect(view.status === "ok" ? view.data.total_live_count : null).toBeUndefined();
    const text = textOf(renderComputeTasksPanel());
    expect(text).toContain(computeRecordsT("compute.recordsPageLive", 1));
    expect(text).not.toContain(computeRecordsT("compute.recordsTotalLive", 1));
  });

  it("appends the next page by cursor and de-duplicates by job_id", async () => {
    const calls = stubFetch((path) =>
      path.includes("cursor=c1")
        ? {
            body: page([task("job-b", { live: false }), task("job-c")], {
              total_live_count: 2,
              has_more: false,
              next_cursor: null,
            }),
          }
        : {
            body: page([task("job-a"), task("job-b")], {
              total_live_count: 2,
              has_more: true,
              next_cursor: "c1",
            }),
          },
    );
    await loadComputeTasks("f");
    const panel = renderComputeTasksPanel();
    expect(findAction(panel as unknown as FakeEl, "compute-load-more")).not.toBeNull();
    findAction(panel as unknown as FakeEl, "compute-load-more")!.onclick!();
    await flush();
    await flush();

    const view = S.computeTasks as ComputeTasksView;
    expect(view.status).toBe("ok");
    if (view.status === "ok") {
      expect(view.data.tasks.map((row) => row.job_id)).toEqual(["job-a", "job-b", "job-c"]);
      expect(view.nextCursor).toBeNull();
    }
    expect(calls.some((path) => path.includes("cursor=c1"))).toBe(true);
    // Paging is a read: no POST.
    expect(
      vi.mocked(fetch).mock.calls.every(([, init]) => !(init as RequestInit)?.method),
    ).toBe(true);
  });

  it("filters to in-flight tasks with state=live and starts from the first page", async () => {
    const calls = stubFetch((path) =>
      path.includes("state=live")
        ? { body: page([task("job-live")], { total_live_count: 1 }) }
        : {
            body: page([task("job-live"), task("job-done", { live: false })], {
              total_live_count: 1,
            }),
          },
    );
    await loadComputeTasks("f");
    const all = renderComputeTasksPanel();
    expect(textOf(all)).toContain("job-done");
    findAction(all as unknown as FakeEl, "compute-filter-live")!.onclick!();
    await flush();
    await flush();

    expect(calls.some((path) => path.includes("state=live"))).toBe(true);
    const view = S.computeTasks as ComputeTasksView;
    expect(view.state).toBe("live");
    const filtered = renderComputeTasksPanel();
    expect(textOf(filtered)).toContain("job-live");
    expect(textOf(filtered)).not.toContain("job-done");
  });

  it("shows loading before the first answer and unknown after a reset", async () => {
    const gate = deferredReply();
    vi.stubGlobal("fetch", vi.fn(() => gate.promise));
    const reading = loadComputeTasks("f");
    expect(textOf(renderComputeTasksPanel())).toContain(
      computeRecordsT("compute.recordsLoading"),
    );
    gate.release(page([], { total_live_count: 0 }));
    await reading;
    resetComputeTasks();
    expect(textOf(renderComputeTasksPanel())).toContain(
      computeRecordsT("compute.recordsUnknown"),
    );
  });

  it("drops a late answer after the session changed", async () => {
    const gate = deferredReply();
    vi.stubGlobal("fetch", vi.fn(() => gate.promise));
    const readingA = loadComputeTasks("f");
    S.currentId = "g";
    stubFetch(() => ({ body: page([task("job-b")], { total_live_count: 1 }) }));
    await loadComputeTasks("g");
    gate.release(page([task("job-a")], { total_live_count: 1 }));
    await readingA;

    const view = S.computeTasks as ComputeTasksView;
    expect(view.fid).toBe("g");
    const text = textOf(renderComputeTasksPanel());
    expect(text).toContain("job-b");
    expect(text).not.toContain("job-a");
  });

  it("labels a row with the record's own update time, not a fresh check", async () => {
    stubFetch(() => ({ body: page([task("job-a")], { total_live_count: 1 }) }));
    await loadComputeTasks("f");
    const text = textOf(renderComputeTasksPanel());
    expect(text).toContain(computeRecordsT("compute.recordsUpdated", "5m"));
    expect(text).not.toContain("just now");
  });

  it("keeps the last good page across repeated failures, stamped with its own read time", async () => {
    stubFetch(() => ({ body: page([task("job-a")], { total_live_count: 1 }) }));
    const before = Date.now();
    await loadComputeTasks("f");
    const readAt = (S.computeTasks as ComputeTasksView & { at?: number }).at!;
    expect(readAt).toBeGreaterThanOrEqual(before);

    stubFetch(() => ({ status: 503, body: { error: "unavailable" } }));
    await loadComputeTasks("f");
    await loadComputeTasks("f");
    const view = S.computeTasks as ComputeTasksView;
    expect(view.status).toBe("error");
    if (view.status === "error") {
      expect(view.stale?.data.tasks[0]?.job_id).toBe("job-a");
      expect(view.stale?.at).toBe(readAt);
    }
    expect(textOf(renderComputeTasksPanel())).toContain("job-a");
  });

  it("a workbench refresh keeps the rows a load-more already appended", async () => {
    let refreshed = false;
    stubFetch((path) =>
      path.includes("cursor=c1")
        ? { body: page([task("job-old")], { has_more: true, next_cursor: "c2" }) }
        : {
            body: page(refreshed ? [task("job-new"), task("job-a")] : [task("job-a")], {
              has_more: true,
              next_cursor: "c1",
            }),
          },
    );
    await loadComputeTasks("f");
    findAction(renderComputeTasksPanel() as unknown as FakeEl, "compute-load-more")!.onclick!();
    await flush();
    await flush();
    refreshed = true;
    await loadComputeTasks("f");

    const view = S.computeTasks as ComputeTasksView;
    expect(view.status).toBe("ok");
    if (view.status === "ok") {
      expect(view.data.tasks.map((row) => row.job_id)).toEqual(["job-new", "job-a", "job-old"]);
      // Continue after the deepest row shown, not from page two again.
      expect(view.data.next_cursor).toBe("c2");
    }
  });

  it("a failed load-more keeps the shown page as a successful read and offers it again", async () => {
    stubFetch((path) =>
      path.includes("cursor=c1")
        ? { status: 502, body: { error: "bad gateway" } }
        : { body: page([task("job-a")], { has_more: true, next_cursor: "c1" }) },
    );
    await loadComputeTasks("f");
    findAction(renderComputeTasksPanel() as unknown as FakeEl, "compute-load-more")!.onclick!();
    await flush();
    await flush();

    expect((S.computeTasks as ComputeTasksView).status).toBe("ok");
    const panel = renderComputeTasksPanel();
    const text = textOf(panel);
    expect(text).toContain("job-a");
    expect(text).toContain(computeRecordsT("compute.recordsLoadMoreFailed"));
    expect(text).not.toContain(computeRecordsT("compute.recordsUnavailable"));
    expect(findAction(panel as unknown as FakeEl, "compute-load-more")).not.toBeNull();
  });

  it("drops an in-flight answer after the session-reset hook", async () => {
    const gate = deferredReply();
    vi.stubGlobal("fetch", vi.fn(() => gate.promise));
    const reading = loadComputeTasks("f");
    resetComputeTasks();
    gate.release(page([task("job-a")], { total_live_count: 1 }));
    await reading;
    expect(S.computeTasks).toBeNull();
  });
});
