import { afterEach, describe, expect, it, vi } from "vitest";
import { installSend } from "./index";
import { bindComposer } from "./send";

type KeyEvent = {
  key?: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
  target?: unknown;
  preventDefault: () => void;
};
type Listener = (e: KeyEvent) => void;

function fakeToggle() {
  return {
    dataset: {} as Record<string, string>,
    onclick: null as (() => void) | null,
    classList: { toggle: vi.fn(), remove: vi.fn() },
  };
}

function fakeComposer() {
  return { id: "composer", value: "" };
}

/** The document root the Enter handler is delegated on. */
function fakeRoot() {
  const listeners: Record<string, Listener[]> = {};
  return {
    dataset: {} as Record<string, string>,
    listeners,
    addEventListener: (type: string, fn: Listener) => {
      (listeners[type] ||= []).push(fn);
    },
  };
}

function stubDocument(nodes: Record<string, unknown>) {
  const root = fakeRoot();
  vi.stubGlobal("document", {
    documentElement: root,
    getElementById: (id: string) => nodes[id] ?? null,
  });
  return root;
}

const enter = (target: unknown, extra: Partial<KeyEvent> = {}): KeyEvent => ({
  key: "Enter",
  target,
  preventDefault: vi.fn(),
  ...extra,
});
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("bindComposer", () => {
  it("binds the Enter handler and the mode toggles once, however often it is called", () => {
    const plan = fakeToggle();
    const explore = fakeToggle();
    const root = stubDocument({ composer: fakeComposer(), "plan-toggle": plan, "explore-toggle": explore });

    bindComposer(vi.fn());
    bindComposer(vi.fn());

    expect(root.dataset.sendBound).toBe("1");
    expect(root.listeners.keydown).toHaveLength(1);
    expect(plan.dataset.sendBound).toBe("1");
    expect(typeof plan.onclick).toBe("function");
    expect(explore.dataset.sendBound).toBe("1");
    expect(typeof explore.onclick).toBe("function");
  });

  it("installSend() stays DOM-free; main.tsx binds after render(<App/>)", () => {
    // installSend() runs at module import, before Shell.tsx has rendered the
    // composer. An import-time bind found nothing and never tried again,
    // which is how Enter shipped dead. The bind belongs to the post-render
    // slot in main.tsx, next to bootChrome(), not to install.
    const root = stubDocument({ composer: fakeComposer() });

    installSend({});

    expect(root.dataset.sendBound).toBeUndefined();
    expect(root.listeners.keydown).toBeUndefined();
  });

  it("Enter dispatches the composer text; Shift+Enter, IME, an open autocomplete and other targets do not", () => {
    const composer = fakeComposer();
    const root = stubDocument({ composer });
    const dispatch = vi.fn(() => Promise.resolve());
    bindComposer(dispatch);
    const onKey = root.listeners.keydown![0]!;
    composer.value = "hello";

    for (const e of [
      enter(composer, { shiftKey: true }),
      enter(composer, { isComposing: true }),
      enter(composer, { keyCode: 229 }),
      enter(composer, { key: "a" }),
      enter({ id: "conv-title", value: "not the composer" }),
    ]) {
      onKey(e);
      expect(e.preventDefault).not.toHaveBeenCalled();
    }
    vi.stubGlobal("ac", { open: true });
    const withAutocomplete = enter(composer);
    onKey(withAutocomplete);
    expect(withAutocomplete.preventDefault).not.toHaveBeenCalled();
    vi.stubGlobal("ac", undefined);
    expect(dispatch).not.toHaveBeenCalled();

    const plain = enter(composer);
    onKey(plain);
    expect(plain.preventDefault).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledWith("hello");
  });

  it("keeps sending after #composer is re-created, with no rebind", () => {
    // The handler is delegated on the document root. A keyed or conditional
    // composer subtree, or a second render(), replaces the textarea node; a
    // node-bound listener would be gone with it and nothing would rebind.
    const first = fakeComposer();
    const root = stubDocument({ composer: first });
    const dispatch = vi.fn(() => Promise.resolve());
    bindComposer(dispatch);
    const onKey = root.listeners.keydown![0]!;

    const replacement = fakeComposer();
    replacement.value = "after remount";
    onKey(enter(replacement));

    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledWith("after remount");
  });

  it("drops a repeated Enter while the previous dispatch is still in flight", async () => {
    // send() clears the composer only after it has awaited POST /frames on a
    // fresh session (or the skills catalog for a /skill token). A held or
    // double Enter inside that window created a second session and sent the
    // same text twice.
    const composer = fakeComposer();
    const root = stubDocument({ composer });
    let settle!: () => void;
    const dispatch = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          settle = resolve;
        }),
    );
    bindComposer(dispatch);
    const onKey = root.listeners.keydown![0]!;
    composer.value = "hello";

    const repeat = enter(composer);
    onKey(enter(composer));
    onKey(repeat);
    onKey(enter(composer));
    expect(dispatch).toHaveBeenCalledTimes(1);
    // The key is still consumed: a swallowed Enter must not insert a newline.
    expect(repeat.preventDefault).toHaveBeenCalledTimes(1);

    settle();
    await tick();
    composer.value = "again";
    onKey(enter(composer));
    expect(dispatch).toHaveBeenCalledTimes(2);
    expect(dispatch).toHaveBeenLastCalledWith("again");
  });
});
