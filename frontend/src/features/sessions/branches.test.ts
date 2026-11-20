/**
 * M-73: a fork left one line under the composer and nothing in the sidebar.
 * The open session's row now lists its branches, marks the current one, and
 * switches through the same confirmed activation as the branch panel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { branchState } from "../../stores/timeline";

type FakeNode = {
  tag: string;
  className: string;
  textContent: string;
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

const activate = vi.fn();

beforeEach(async () => {
  resetStoreFields();
  await setLang("en");
  vi.stubGlobal("document", { createElement: fakeNode });
  vi.stubGlobal("activateSessionBranch", activate);
  activate.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const { sessionBranchList } = await import("./branches");

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
    const [main, fork, headless] = box.children as [FakeNode, FakeNode, FakeNode];
    expect(text(main)).toBe("↳ Main line");
    expect(text(fork)).toBe("↳ TURN-2: compute x2 current");
    expect(fork.className).toContain("current");
    expect(fork.attrs["aria-current"]).toBe("true");
    expect(fork.disabled).toBe(true);
    expect(headless.disabled).toBe(true);
    expect(headless.title).toContain("no checkpoint");

    expect(main.title).toBe("Switch to branch “Main line” (asks first)");
    main.onclick!({ stopPropagation: () => undefined });
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
