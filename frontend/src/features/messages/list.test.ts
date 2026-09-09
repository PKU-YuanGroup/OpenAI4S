import { afterEach, describe, expect, it, vi } from "vitest";
import { Shell } from "../../components/dashboard/Shell";
import * as messageComponents from "./components";
import { currentId, _openGen, historyLoad } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";

vi.mock("preact/hooks", async (original) => ({
  ...await original<typeof import("preact/hooks")>(),
  useEffect: vi.fn(),
}));
import {
  INITIAL_RENDER_BATCH,
  cancelFramedRender,
  nextBatchEnd,
  scheduleFramedRender,
} from "./list";

afterEach(() => {
  cancelFramedRender();
  vi.unstubAllGlobals();
});

describe("framed initial render batches", () => {
  it("uses 40 items per frame (inside the 30-50 window)", () => {
    expect(INITIAL_RENDER_BATCH).toBeGreaterThanOrEqual(30);
    expect(INITIAL_RENDER_BATCH).toBeLessThanOrEqual(50);
    expect(INITIAL_RENDER_BATCH).toBe(40);
  });

  it("splits a 640-row session into 16 frames", () => {
    const total = 640;
    const ends: number[] = [];
    let start = 0;
    while (start < total) {
      const end = nextBatchEnd(start, total);
      expect(end - start).toBeLessThanOrEqual(INITIAL_RENDER_BATCH);
      expect(end).toBeGreaterThan(start);
      ends.push(end);
      start = end;
    }
    expect(ends).toHaveLength(16);
    expect(ends[ends.length - 1]).toBe(640);
  });

  it("last batch may be shorter than the frame size", () => {
    expect(nextBatchEnd(280, 300)).toBe(300);
    expect(nextBatchEnd(0, 10)).toBe(10);
  });

  it("settles a framed render when a session switch cancels it", async () => {
    const frames = new Map<number, FrameRequestCallback>();
    let nextFrame = 1;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      const id = nextFrame++;
      frames.set(id, cb);
      return id;
    });
    vi.stubGlobal("cancelAnimationFrame", (id: number) => {
      frames.delete(id);
    });
    const onDone = vi.fn();
    const settled = new Promise<"cancelled">((resolve) => {
      scheduleFramedRender([], {
        host: { appendChild: (node: Node) => node } as unknown as ParentNode,
        onDone,
        onCancel: () => resolve("cancelled"),
      });
    });

    cancelFramedRender();

    await expect(settled).resolves.toBe("cancelled");
    expect(onDone).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
  });
});

describe("insertMessageByTime", () => {
  it("inserts before the first later timestamp and skips #msgs-earlier", async () => {
    const { insertMessageByTime } = await import("./list");
    const kids: Array<{ id: string; dataset: { ts?: string } }> = [];
    const host = {
      children: kids,
      insertBefore(node: (typeof kids)[0], ref: (typeof kids)[0]) {
        kids.splice(kids.indexOf(ref), 0, node);
        return node;
      },
      appendChild(node: (typeof kids)[0]) {
        kids.push(node);
        return node;
      },
    };
    const earlier = { id: "msgs-earlier", dataset: {} };
    const a = { id: "a", dataset: { ts: "100" } };
    const c = { id: "c", dataset: { ts: "300" } };
    kids.push(earlier, a, c);
    const b = { id: "b", dataset: { ts: "200" } };
    insertMessageByTime(
      b as unknown as HTMLElement,
      host as unknown as ParentNode,
    );
    expect(kids.map((k) => k.id)).toEqual(["msgs-earlier", "a", "b", "c"]);
  });
});


type MessageVNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
function messageVNodes(value: unknown): MessageVNode[] {
  if (Array.isArray(value)) return value.flatMap(messageVNodes);
  if (!value || typeof value !== "object") return [];
  const node = value as MessageVNode;
  return [node, ...messageVNodes(node.props?.children)];
}

it("mounts the history status in the real Shell outside its imperative transcript", () => {
  resetStoreFields();
  const root = Shell();
  const nodes = messageVNodes(root);
  const messages = nodes.filter((node) => node.props?.id === "messages");
  expect(messages).toHaveLength(1);
  expect(messages[0]?.props?.class).toBe("messages");
  expect(messages[0]?.props?.children).toBeUndefined();
  const status = nodes.find((node) => typeof node.type === "function" && node.type.name === "HistoryLoadStatus");
  expect(status).toBeDefined();
  expect(status?.type).toBe((messageComponents as unknown as Record<string, unknown>).HistoryLoadStatus);
  const column = nodes.find((node) => node.props?.id === "conv-view");
  expect(column?.props?.children).toEqual(expect.arrayContaining([status, messages[0]]));
  expect(nodes.filter((node) => node.props?.id === "jump-pill")).toHaveLength(1);
});

it("shows scoped read errors and a retry button, hiding settled or obsolete history state", () => {
  resetStoreFields(); currentId.value = "f"; _openGen.value = 3;
  historyLoad.value = {
    fid: "f", generation: 3, status: "partial", messagesLoaded: true,
    stepsLoaded: false, runStateLoaded: true, superseded: false,
    errors: { steps: "HTTP 503" }, deferred: false,
  };
  const Status = (messageComponents as unknown as Record<string, unknown>).HistoryLoadStatus as (() => unknown);
  expect(Status).toBeTypeOf("function");
  const visible = messageVNodes(Status());
  expect(visible[0]?.props).toMatchObject({ role: "status", "aria-live": "polite", "data-history-state": "partial" });
  expect(visible.filter((node) => node.type === "button")).toHaveLength(1);
  expect(visible.some((node) => JSON.stringify(node.props?.children).includes("HTTP 503"))).toBe(true);
  historyLoad.value = { ...historyLoad.value, status: "loaded" };
  expect(Status()).toBeNull();
  historyLoad.value = { ...historyLoad.value, status: "error", generation: 2 };
  expect(Status()).toBeNull();
  historyLoad.value = { ...historyLoad.value, fid: "g", generation: 3 };
  expect(Status()).toBeNull();
});
