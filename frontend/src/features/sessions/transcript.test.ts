/**
 * The chips under the composer (ART-16). The `@` menu writes `@name` and keeps
 * the version beside the text, so a pinned file must still show as resolved,
 * with its version, without the version in the text.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { artifacts } from "../../stores/artifacts";
import { currentId } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { clearMentions, rememberMention } from "../autocomplete/mentions";
import { renderComposerRefChips } from "./transcript";

type Node = {
  className: string;
  classes: Set<string>;
  classList: { add: (c: string) => void; remove: (c: string) => void; contains: (c: string) => boolean };
  children: Node[];
  textContent: string;
  title: string;
  value: string;
  innerHTML: string;
  onclick: unknown;
  appendChild: (child: Node) => Node;
  firstChild?: Node;
};

function node(): Node {
  const classes = new Set<string>();
  const n: Node = {
    get className() {
      return [...classes].join(" ");
    },
    set className(v: string) {
      classes.clear();
      v.split(/\s+/).filter(Boolean).forEach((c) => classes.add(c));
    },
    classes,
    classList: {
      add: (c) => void classes.add(c),
      remove: (c) => void classes.delete(c),
      contains: (c) => classes.has(c),
    },
    children: [],
    textContent: "",
    title: "",
    value: "",
    onclick: null,
    get innerHTML() {
      return "";
    },
    set innerHTML(_v: string) {
      n.children = [];
    },
    appendChild(child) {
      n.children.push(child);
      return child;
    },
  };
  return n;
}

function text(n: Node): string {
  return n.textContent + n.children.map(text).join("");
}

describe("composer @-ref chips", () => {
  let host: Node;
  let composer: Node;

  beforeEach(() => {
    resetStoreFields();
    clearMentions();
    host = node();
    composer = node();
    vi.stubGlobal("document", {
      querySelector: (sel: string) => (sel === "#composer-refs" ? host : sel === "#composer" ? composer : null),
      createElement: () => node(),
    });
    currentId.value = "f-mine";
    artifacts.value = [];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    clearMentions();
  });

  it("a pin from the @ menu is a resolved chip naming its version, from another session too", () => {
    rememberMention({ filename: "cohort.csv", version_id: "v-9f5c06efc35e", artifact_id: "a-c", root_frame_id: "f-other" });
    composer.value = "@cohort.csv 一共多少行？";
    renderComposerRefChips();
    expect(host.children).toHaveLength(1);
    const chip = host.children[0]!;
    expect(chip.classList.contains("unresolved")).toBe(false);
    expect(chip.classList.contains("elsewhere")).toBe(true);
    expect(text(chip)).toContain("cohort.csv");
    expect(chip.title).toBe("v-9f5c06efc35e · ↗ f-other");
    expect(host.classList.contains("hidden")).toBe(false);
  });

  it("a pinned name with a space in it is one chip, not a shorter unresolved one as well", () => {
    rememberMention({ filename: "notes with space.txt", version_id: "v-aaaaaaaaaaaa", artifact_id: "a-n", root_frame_id: "f-mine" });
    composer.value = "read @notes with space.txt please";
    renderComposerRefChips();
    expect(host.children).toHaveLength(1);
    expect(host.children[0]!.classList.contains("unresolved")).toBe(false);
    expect(host.children[0]!.title).toBe("v-aaaaaaaaaaaa");
  });

  it("a pin opens the viewer on this session's row and names that row's checksum only for that version", () => {
    artifacts.value = [{ artifact_id: "a-p", filename: "plain.csv", version_id: "v-new", checksum: "0123456789abcdef" }];
    rememberMention({ filename: "plain.csv", version_id: "v-old", artifact_id: "a-p", root_frame_id: "f-mine" });
    composer.value = "@plain.csv";
    renderComposerRefChips();
    const chip = host.children[0]!;
    expect(chip.classList.contains("clickable")).toBe(true);
    expect(chip.title).toBe("v-old");
  });

  it("an unpinned @name still resolves against this session's files, or says it does not", () => {
    artifacts.value = [{ artifact_id: "a-p", filename: "plain.csv", version_id: "v-1", checksum: "0123456789abcdef" }];
    composer.value = "@plain.csv and @nothing.csv";
    renderComposerRefChips();
    expect(host.children.map((c) => c.classList.contains("unresolved"))).toEqual([false, true]);
    expect(host.children[0]!.title).toBe("v-1 · sha256:0123456789ab");
  });

  it("a pin whose token was deleted shows nothing", () => {
    rememberMention({ filename: "cohort.csv", version_id: "v-9f5c06efc35e", artifact_id: "a-c", root_frame_id: "f-mine" });
    composer.value = "never mind";
    renderComposerRefChips();
    expect(host.children).toHaveLength(0);
    expect(host.classList.contains("hidden")).toBe(true);
  });
});
