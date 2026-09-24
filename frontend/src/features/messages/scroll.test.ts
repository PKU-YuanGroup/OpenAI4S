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
