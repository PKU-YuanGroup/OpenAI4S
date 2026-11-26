/**
 * M-73: a fork left one line under the composer and nothing in the sidebar.
 * The open session's row now lists its branches, marks the current one, and
 * switches through the same confirmed activation as the branch panel.
 *
 * Owner decision 24: a branch that is neither the current one nor the main
 * line can be deleted from that list, after the in-app confirm.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { branchState } from "../../stores/timeline";

const askConfirm = vi.fn(async (_message: string, _opts?: unknown) => false);
vi.mock("../chrome/dialog", () => ({
  askConfirm: (message: string, opts?: unknown) => askConfirm(message, opts),
  askText: vi.fn(),
  showNotice: vi.fn(),
}));

type FakeNode = {
  tag: string;
  className: string;
  textContent: string;
  innerHTML: string;
  title: string;
  type: string;
  disabled: boolean;
  dataset: Record<string, string>;
  attrs: Record<string, string>;
  children: FakeNode[];
  onclick: ((event: { stopPropagation(): void }) => void) | null;
  appendChild(child: FakeNode): FakeNode;
  setAttribute(name: string, value: string): void;
};

function fakeNode(tag: string): FakeNode {
  const node: FakeNode = {
    tag,
    className: "",
    textContent: "",
    innerHTML: "",
    title: "",
    type: "",
    disabled: false,
    dataset: {},
    attrs: {},
    children: [],
    onclick: null,
    appendChild(child) {
      node.children.push(child);
      return child;
    },
    setAttribute(name, value) {
      node.attrs[name] = value;
    },
  };
  return node;
}

const text = (node: FakeNode): string => node.children.map((kid) => kid.textContent).join(" ");
/** The switch button of one listed branch. */
const rowOf = (item: FakeNode): FakeNode => item.children.find((kid) => kid.className.startsWith("session-branch"))!;
/** Its delete button, if it has one. */
const deleteOf = (item: FakeNode): FakeNode | undefined =>
  item.children.find((kid) => kid.className === "session-branch-del");
const click = (node: FakeNode) => node.onclick!({ stopPropagation: () => undefined });

const activate = vi.fn();
const reload = vi.fn(async () => undefined);
const fetchCalls: Array<{ url: string; method: string }> = [];
let fetchReply: { status: number; body: unknown } = { status: 200, body: { ok: true } };

beforeEach(async () => {
  resetStoreFields();
  await setLang("en");
  vi.stubGlobal("document", { createElement: fakeNode, querySelector: () => null });
  vi.stubGlobal("activateSessionBranch", activate);
  vi.stubGlobal("loadWorkbenchState", reload);
  fetchCalls.length = 0;
  fetchReply = { status: 200, body: { ok: true } };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit = {}) => {
      fetchCalls.push({ url: String(url), method: String(init.method || "GET") });
      return {
        ok: fetchReply.status < 400,
        status: fetchReply.status,
        text: async () => JSON.stringify(fetchReply.body),
      };
    }),
  );
  activate.mockClear();
  reload.mockClear();
  askConfirm.mockReset();
  askConfirm.mockImplementation(async () => false);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const { deleteSessionBranch, sessionBranchList } = await import("./branches");

const view = (current: string) => ({
  root_frame_id: "f-1",
  branch_id: current,
  capabilities: { activate: true },
  branches: [
    { branch_id: "f-1", name: "", activatable: true },
    { branch_id: "br-2", name: "TURN-2: compute x2", activatable: true },
    { branch_id: "br-3", name: "no head yet", activatable: false },
  ],
});

describe("the open session's branches in the sidebar", () => {
  it("lists every branch, marks the current one and switches to another through the confirmed activation", () => {
    currentId.value = "f-1";
    const box = sessionBranchList("f-1", view("br-2")) as unknown as FakeNode;
    expect(box.className).toBe("session-branches");
    expect(box.attrs["aria-label"]).toBe("This session's branches");
    const [main, fork, headless] = box.children.map(rowOf) as [FakeNode, FakeNode, FakeNode];
    expect(text(main)).toBe("↳ Main line");
    expect(text(fork)).toBe("↳ TURN-2: compute x2 current");
    expect(fork.className).toContain("current");
    expect(fork.attrs["aria-current"]).toBe("true");
    expect(fork.disabled).toBe(true);
    expect(headless.disabled).toBe(true);
    expect(headless.title).toContain("no checkpoint");

    expect(main.title).toBe("Switch to branch “Main line” (asks first)");
    click(main);
    expect(activate).toHaveBeenCalledWith("f-1");
  });

  it("shows nothing for a session with only its main line, or for another session's projection", () => {
    currentId.value = "f-1";
    const single = { ...view("f-1"), branches: [{ branch_id: "f-1", activatable: true }] };
    expect(sessionBranchList("f-1", single)).toBeNull();
    expect(sessionBranchList("f-1", { ...view("f-1"), root_frame_id: "f-other" })).toBeNull();
    expect(sessionBranchList("f-1", null)).toBeNull();
    currentId.value = "f-other";
    expect(sessionBranchList("f-1", view("f-1"))).toBeNull();
    // The stored projection is what it reads by default.
    currentId.value = "f-1";
    branchState.value = view("f-1");
    expect(sessionBranchList("f-1")).not.toBeNull();
  });
});

describe("deleting a branch from the sidebar", () => {
  it("offers delete on every branch except the current one and the main line", () => {
    currentId.value = "f-1";
    const onFork = sessionBranchList("f-1", view("br-2")) as unknown as FakeNode;
    const [main, current, other] = onFork.children as [FakeNode, FakeNode, FakeNode];
    expect(deleteOf(main)).toBeUndefined();
    expect(deleteOf(current)).toBeUndefined();
    const remove = deleteOf(other)!;
    expect(remove.tag).toBe("button");
    expect(remove.dataset.branchId).toBe("br-3");
    expect(remove.attrs["aria-label"]).toBe("Delete branch “no head yet”");

    // On the main line, every fork can go and the main line still cannot.
    const onMain = sessionBranchList("f-1", view("f-1")) as unknown as FakeNode;
    expect(onMain.children.map((item) => deleteOf(item)?.dataset.branchId)).toEqual([
      undefined,
      "br-2",
      "br-3",
    ]);
  });

  it("sends nothing when the confirm is cancelled", async () => {
    currentId.value = "f-1";
    const box = sessionBranchList("f-1", view("f-1")) as unknown as FakeNode;
    click(deleteOf(box.children[1]!)!);
    await vi.waitFor(() => expect(askConfirm).toHaveBeenCalledTimes(1));
    const [message, opts] = askConfirm.mock.calls[0]!;
    expect(message).toContain("Delete branch “TURN-2: compute x2”?");
    expect(message).toContain("cannot be undone");
    expect(opts).toMatchObject({ danger: true, okLabel: "Delete branch" });
    await Promise.resolve();
    expect(fetchCalls).toEqual([]);
    expect(reload).not.toHaveBeenCalled();
    expect(activate).not.toHaveBeenCalled();
  });

  it("sends one DELETE after the confirm and re-reads the branches", async () => {
    currentId.value = "f-1";
    askConfirm.mockImplementation(async () => true);
    const deleted = await deleteSessionBranch("f-1", "br-2", "TURN-2: compute x2");
    expect(deleted).toBe(true);
    expect(fetchCalls).toEqual([{ url: "/api/v1/frames/f-1/branches/br-2", method: "DELETE" }]);
    expect(reload).toHaveBeenCalledWith("f-1", true, ["branches"]);
    expect(activate).not.toHaveBeenCalled();
  });

  it("re-reads a stale list after a 404 and keeps it after a 409", async () => {
    currentId.value = "f-1";
    askConfirm.mockImplementation(async () => true);
    fetchReply = { status: 409, body: { error: "current", code: "branch_active" } };
    expect(await deleteSessionBranch("f-1", "br-2", "x")).toBe(false);
    expect(reload).not.toHaveBeenCalled();

    fetchReply = { status: 404, body: { error: "branch not found", code: "branch_not_found" } };
    expect(await deleteSessionBranch("f-1", "br-2", "x")).toBe(false);
    expect(reload).toHaveBeenCalledWith("f-1", true, ["branches"]);
    expect(fetchCalls.map((call) => call.method)).toEqual(["DELETE", "DELETE"]);
  });

  it("never asks about, or sends, a deletion of the main line", async () => {
    askConfirm.mockImplementation(async () => true);
    expect(await deleteSessionBranch("f-1", "f-1", "Main line")).toBe(false);
    expect(askConfirm).not.toHaveBeenCalled();
    expect(fetchCalls).toEqual([]);
  });
});
