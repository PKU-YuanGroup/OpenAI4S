import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hint = vi.fn();
vi.mock("./chrome", () => ({ hint: (...args: unknown[]) => hint(...args) }));

import { currentId, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { onEvent, resetWsHandlers } from "../ws/registry";
import { clearWsDebouncers, registerBuiltinHandlers, setLoadSessionsImpl } from "../ws/handlers";
import { expectOwnDeletion, forgetOwnDeletion, sessionDeletedElsewhereText } from "./deleted";

/** Let the handler's async leave run to its notice. */
async function settle(): Promise<void> {
  for (let i = 0; i < 5; i += 1) await Promise.resolve();
}

describe("SYNC-17: a session deleted in another tab", () => {
  let open: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetStoreFields();
    resetWsHandlers();
    registerBuiltinHandlers();
    setLoadSessionsImpl(vi.fn());
    hint.mockReset();
    open = vi.fn(async () => undefined);
    vi.stubGlobal("openConversation", open);
    project.value = "p";
    currentId.value = "f-open";
    sessions.value = [{ id: "f-open", project_id: "p" }, { id: "f-other", project_id: "p" }];
  });

  afterEach(() => {
    clearWsDebouncers();
    setLoadSessionsImpl(null);
    resetWsHandlers();
    vi.unstubAllGlobals();
    forgetOwnDeletion("f-open");
  });

  it("leaves the session on screen and says why", async () => {
    onEvent({ type: "session_status", frame_id: "f-open", project_id: "p", status: "deleted" });
    await settle();
    // Re-opened, so it lands on the not-found view (its read is a 404 now),
    // and out of the list first, so that open asks whether it exists.
    expect(open).toHaveBeenCalledWith("f-open", "p");
    expect((sessions.value as Array<{ id: string }>).map((row) => row.id)).toEqual(["f-other"]);
    expect(hint).toHaveBeenCalledWith(sessionDeletedElsewhereText(), true);
  });

  it("leaves a session this tab is not showing alone", async () => {
    onEvent({ type: "session_status", frame_id: "f-other", project_id: "p", status: "deleted" });
    await settle();
    expect(open).not.toHaveBeenCalled();
    expect(hint).not.toHaveBeenCalled();
  });

  it("does not call this tab's own deletion one made elsewhere", async () => {
    expectOwnDeletion("f-open");
    onEvent({ type: "session_status", frame_id: "f-open", project_id: "p", status: "deleted" });
    await settle();
    expect(open).not.toHaveBeenCalled();
    expect(hint).not.toHaveBeenCalled();
    // The mark is spent: a later deletion of the same id elsewhere is news.
    onEvent({ type: "session_status", frame_id: "f-open", project_id: "p", status: "deleted" });
    await settle();
    expect(open).toHaveBeenCalledTimes(1);
  });

  it("keeps the notice off once the user has moved on", async () => {
    open.mockImplementation(async () => {
      currentId.value = "f-other";
    });
    onEvent({ type: "session_status", frame_id: "f-open", project_id: "p", status: "deleted" });
    await settle();
    expect(hint).not.toHaveBeenCalled();
  });
});
