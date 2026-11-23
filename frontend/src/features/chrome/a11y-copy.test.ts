import { afterEach, describe, expect, it, vi } from "vitest";
import { skipToContent } from "./a11y-copy";

// SYNC-30: Enter on "Skip to content" left focus on <body>; the next Tab
// started from the top of the page again.
describe("skip link", () => {
  afterEach(() => vi.unstubAllGlobals());

  function view(visible: "messages" | "dashboard") {
    const node = (id: string) => {
      const attrs: Record<string, string> = {};
      return {
        id,
        focus: vi.fn(),
        attrs,
        hasAttribute: (name: string) => name in attrs,
        setAttribute: (name: string, value: string) => void (attrs[name] = value),
        getClientRects: () => (id === visible ? [{}] : []),
      };
    };
    const nodes = { messages: node("messages"), dashboard: node("dashboard") };
    vi.stubGlobal("document", { getElementById: (id: "messages" | "dashboard") => nodes[id] ?? null });
    return nodes;
  }

  it.each(["messages", "dashboard"] as const)("focuses the %s view when it is the one on screen", (visible) => {
    const nodes = view(visible);
    const event = { preventDefault: vi.fn() };
    skipToContent(event as unknown as Event);
    expect(event.preventDefault).toHaveBeenCalled();
    expect(nodes[visible].focus).toHaveBeenCalledTimes(1);
    expect(nodes[visible].attrs.tabindex).toBe("-1");
    const other = visible === "messages" ? "dashboard" : "messages";
    expect(nodes[other].focus).not.toHaveBeenCalled();
  });
});
