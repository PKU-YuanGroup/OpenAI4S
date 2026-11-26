import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import { _messagesFollow } from "../../stores/ui";
import { bindMessageScroll, down, flushScrollNow, unbindMessageScroll, updateJumpPill } from "./scroll";

type Listener = () => void;

/** A scroll box: 1000px of content in a 500px viewport. */
function fakeHost(scrollTop: number) {
  const listeners: Record<string, Listener> = {};
  return {
    id: "messages",
    scrollHeight: 1000,
    clientHeight: 500,
    scrollTop,
    listeners,
    addEventListener(type: string, fn: Listener) {
      listeners[type] = fn;
    },
    removeEventListener(type: string, fn: Listener) {
      if (listeners[type] === fn) delete listeners[type];
    },
  };
}

function fakePill() {
  const listeners: Record<string, Listener> = {};
  let hidden = true;
  return {
    listeners,
    get hidden() {
      return hidden;
    },
    classList: {
      toggle(_name: string, on: boolean) {
        hidden = on;
      },
    },
    addEventListener(type: string, fn: Listener) {
      listeners[type] = fn;
    },
    removeEventListener(type: string, fn: Listener) {
      if (listeners[type] === fn) delete listeners[type];
    },
  };
}

let host: ReturnType<typeof fakeHost>;
let pill: ReturnType<typeof fakePill>;

beforeEach(() => {
  resetStoreFields();
  host = fakeHost(460);
  pill = fakePill();
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.stubGlobal("document", {
    querySelector: (sel: string) => (sel === "#messages" ? host : sel === "#jump-pill" ? pill : null),
  });
  bindMessageScroll(host as unknown as HTMLElement);
});

afterEach(() => {
  unbindMessageScroll();
  vi.unstubAllGlobals();
});

describe("follow-scroll", () => {
  it("a scroll event measures and paints the pill, and never scrolls", () => {
    // 40px above the bottom: inside the 80px "following" pad.
    host.listeners.scroll!();
    flushScrollNow();
    expect(host.scrollTop).toBe(460);
    expect(_messagesFollow.value).toBe(true);
    expect(pill.hidden).toBe(true);

    // Scrolled up past the pad: not following, and the pill appears.
    host.listeners.wheel!();
    host.scrollTop = 100;
    host.listeners.scroll!();
    flushScrollNow();
    expect(host.scrollTop).toBe(100);
    expect(_messagesFollow.value).toBe(false);
    expect(pill.hidden).toBe(false);

    updateJumpPill();
    flushScrollNow();
    expect(host.scrollTop).toBe(100);
  });

  it("down() follows only while following; down(true) and the pill always jump", () => {
    host.listeners.wheel!();
    host.scrollTop = 100;
    host.listeners.scroll!();
    flushScrollNow();
    down();
    flushScrollNow();
    expect(host.scrollTop).toBe(100);

    pill.listeners.click!();
    flushScrollNow();
    expect(host.scrollTop).toBe(1000);
    expect(_messagesFollow.value).toBe(true);

    host.scrollTop = 900;
    down();
    flushScrollNow();
    expect(host.scrollTop).toBe(1000);
  });

  it("a measure and a down() in one frame: the reader's position wins", () => {
    host.listeners.wheel!();
    host.scrollTop = 100;
    host.listeners.scroll!();
    down();
    flushScrollNow();
    expect(host.scrollTop).toBe(100);
    expect(_messagesFollow.value).toBe(false);
  });
});

describe("following through content that grows by itself", () => {
  it("content that grew below the view does not stop following; a reader moving up does", () => {
    // At the bottom (inside the pad): following.
    host.listeners.scroll!();
    flushScrollNow();
    expect(_messagesFollow.value).toBe(true);

    // A gallery's images arrive: 400px more below, nobody scrolled. The
    // browser may still fire a scroll event (anchoring); it is not a move up.
    host.scrollHeight = 1400;
    host.listeners.scroll!();
    flushScrollNow();
    expect(_messagesFollow.value).toBe(true);
    down();
    flushScrollNow();
    expect(host.scrollTop).toBe(1400);

    // The reader moves up: following stops, and new content does not pull.
    host.listeners.wheel!();
    host.scrollTop = 300;
    host.listeners.scroll!();
    flushScrollNow();
    expect(_messagesFollow.value).toBe(false);
    down();
    flushScrollNow();
    expect(host.scrollTop).toBe(300);
  });

  it("a row that grows keeps a following view at the bottom, and leaves a reader who moved up alone", () => {
    unbindMessageScroll();
    const observed: unknown[] = [];
    let onResize: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(cb: () => void) {
        onResize = cb;
      }
      observe(node: unknown) {
        observed.push(node);
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const row = { nodeType: 1 };
    const grown = Object.assign(fakeHost(500), { children: [row] });
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#messages" ? grown : sel === "#jump-pill" ? pill : null),
    });
    bindMessageScroll(grown as unknown as HTMLElement);
    expect(observed).toEqual([grown, row]);

    // Following at the bottom; a figure in the row loads and it grows.
    grown.scrollHeight = 1600;
    onResize!();
    flushScrollNow();
    expect(grown.scrollTop).toBe(1600);

    // Scrolled up to read: growth no longer moves the view.
    grown.listeners.touchmove!();
    grown.scrollTop = 300;
    grown.listeners.scroll!();
    flushScrollNow();
    grown.scrollHeight = 2000;
    onResize!();
    flushScrollNow();
    expect(grown.scrollTop).toBe(300);
  });

  it("a transcript box that gets shorter keeps a following view at the bottom (M-12)", () => {
    unbindMessageScroll();
    const observed: unknown[] = [];
    let onResize: (() => void) | null = null;
    class FakeResizeObserver {
      constructor(cb: () => void) {
        onResize = cb;
      }
      observe(node: unknown) {
        observed.push(node);
      }
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal("ResizeObserver", FakeResizeObserver);
    const box = Object.assign(fakeHost(500), { children: [] });
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#messages" ? box : sel === "#jump-pill" ? pill : null),
    });
    bindMessageScroll(box as unknown as HTMLElement);
    expect(observed).toContain(box);
    // The composer grows by three lines: the view is 60px shorter, the
    // content is not, and no scroll event fires.
    box.clientHeight = 440;
    onResize!();
    flushScrollNow();
    expect(box.scrollHeight - box.scrollTop - box.clientHeight).toBeLessThanOrEqual(0);
    expect(pill.hidden).toBe(true);
  });
});

describe("only the reader's own input is a move up (CHAT-2)", () => {
  it("the browser lowering scrollTop while the turn grows below keeps following", () => {
    // A reopened running session has jumped to its end.
    down(true);
    flushScrollNow();
    expect(host.scrollTop).toBe(1000);
    // Next frame: a card above the view shrank, so scroll anchoring lowered
    // scrollTop by 56px; the turn grew 1200px below; the row's resize asked
    // to follow. Measured in the browser: this dropped follow for good.
    host.scrollTop = 944;
    host.scrollHeight = 2200;
    host.listeners.scroll!();
    down();
    flushScrollNow();
    expect(_messagesFollow.value).toBe(true);
    expect(host.scrollTop).toBe(2200);
    expect(pill.hidden).toBe(true);
  });

  it("a wheel, a touch, a scrolling key or a held scrollbar is the reader; a second later it is not", () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(10_000);
    const keydown: Array<(event: { key: string }) => void> = [];
    const pointerup: Array<() => void> = [];
    unbindMessageScroll();
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#messages" ? host : sel === "#jump-pill" ? pill : null),
      addEventListener: (type: string, fn: never) => {
        if (type === "keydown") keydown.push(fn);
        if (type === "pointerup") pointerup.push(fn);
      },
      removeEventListener: () => undefined,
    });
    bindMessageScroll(host as unknown as HTMLElement);
    const movedUpBy = (by: number): boolean => {
      // At the bottom of 2000px in a 500px view, then `by` higher.
      host.scrollHeight = 2000;
      host.scrollTop = 1500;
      _messagesFollow.value = true;
      updateJumpPill();
      flushScrollNow();
      host.scrollTop = 1500 - by;
      host.listeners.scroll!();
      flushScrollNow();
      return (_messagesFollow.value as boolean) === false;
    };

    keydown.forEach((fn) => fn({ key: "PageUp" }));
    expect(movedUpBy(300)).toBe(true);
    // Typing is not scrolling.
    now.mockReturnValue(20_000);
    keydown.forEach((fn) => fn({ key: "a" }));
    expect(movedUpBy(300)).toBe(false);

    // Dragging the scrollbar: held for longer than the input window.
    host.listeners.pointerdown!();
    now.mockReturnValue(30_000);
    expect(movedUpBy(300)).toBe(true);
    pointerup.forEach((fn) => fn());
    now.mockReturnValue(30_500);
    expect(movedUpBy(300)).toBe(true);
    now.mockReturnValue(31_500);
    expect(movedUpBy(300)).toBe(false);
    now.mockRestore();
  });
});

describe("one follow-scroll implementation", () => {
  it("is what sessions/dom exports, so the jump pill and every caller share it", async () => {
    const sessionsDom = await import("../sessions/dom");
    const scroll = await import("./scroll");
    expect(sessionsDom.down).toBe(scroll.down);
    expect(sessionsDom.updateJumpPill).toBe(scroll.updateJumpPill);
    expect(sessionsDom.paintJumpPill).toBe(scroll.paintJumpPill);
    expect(sessionsDom.messagesAtBottom).toBe(scroll.messagesAtBottom);
  });
});
