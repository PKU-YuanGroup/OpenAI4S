import { describe, expect, it } from "vitest";
import { isContractStub, isReady } from "../../compat/stub";
import { installMessages, messagesReady } from "./index";

describe("F-10 window exports", () => {
  it("assigns real implementations; isReady passes and typeof is not the test", () => {
    const target: Record<string, unknown> = {};
    installMessages(target);
    expect(isReady(target.openConversation)).toBe(true);
    expect(isReady(target.down)).toBe(true);
    // The fetch trio belongs to F-13, which also drives the earlier-messages
    // store and its hint. Both lanes ported it; main.tsx imports sessions
    // before messages, so claiming it here would overwrite that copy in
    // silence rather than conflict. Asserted as absent so the split holds.
    expect(target.fetchRecentMessages).toBeUndefined();
    expect(target.fetchOlderMessages).toBeUndefined();
    expect(target.fetchAllMessages).toBeUndefined();
    expect(isReady(target._mdStableCut)).toBe(true);
    expect(isContractStub(target.openConversation)).toBe(false);
    expect(messagesReady(target)).toBe(true);
    // F-05 stubs are functions too — a typeof check would lie. The assigned
    // openConversation is a real async function, not the throwing placeholder.
    expect(typeof target.openConversation).toBe("function");
    expect(isContractStub(target.down)).toBe(false);
  });
});
