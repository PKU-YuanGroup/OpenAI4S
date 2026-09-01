/**
 * One name, one function. Two lanes ported `openConversation` and
 * `resumeWatch` independently and both copies stayed live -- not overwriting
 * each other, but split across two namespaces. `window.openConversation` was
 * F-10's framed paint while `binds.openConversation` was F-13's synchronous
 * forEach, and dashboard, sidebar, project-open and routing all call `binds`,
 * so the live path never got the framed paint or `cancelFramedRender`. Both
 * copies passed their own tests. Identity is what neither could assert alone.
 */

import { describe, expect, it } from "vitest";

import { openConversation as framedOpen } from "../messages/open";
import { resumeWatch as ticketResume } from "../send/ticket";
import { binds } from "./binds";
import { openConversation, resumeWatch } from "./conversation";

describe("conversation re-exports the owning lane's function", () => {
  it("openConversation is F-10's everywhere it can be reached", () => {
    expect(openConversation).toBe(framedOpen);
    expect(binds.openConversation).toBe(framedOpen);
  });

  it("resumeWatch is F-11's everywhere it can be reached", () => {
    expect(resumeWatch).toBe(ticketResume);
  });

  it("the window name agrees with the bind", () => {
    const target = globalThis as unknown as { openConversation?: unknown };
    if (target.openConversation !== undefined) {
      expect(target.openConversation).toBe(binds.openConversation);
    }
  });
});
