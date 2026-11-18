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
    expect(observed).toEqual([row]);

    // Following at the bottom; a figure in the row loads and it grows.
    grown.scrollHeight = 1600;
    onResize!();
    flushScrollNow();
    expect(grown.scrollTop).toBe(1600);

    // Scrolled up to read: growth no longer moves the view.
    grown.scrollTop = 300;
    grown.listeners.scroll!();
    flushScrollNow();
    grown.scrollHeight = 2000;
    onResize!();
    flushScrollNow();
    expect(grown.scrollTop).toBe(300);
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
