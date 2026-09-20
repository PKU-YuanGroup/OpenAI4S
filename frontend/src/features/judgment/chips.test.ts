import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../i18n/runtime", () => ({
  LANG: "en",
  tOptional: () => null,
  t: (key: string, ...args: unknown[]) =>
    key === "step.skill.list" ? "skills: " + args[0] : key,
}));

import { appendSemanticSkillSearch, skillSearchView } from "./chips";

class FakeEl {
  tagName: string;
  className = "";
  title = "";
  dataset: Record<string, string> = {};
  children: FakeEl[] = [];
  textContent = "";
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  appendChild(child: FakeEl): FakeEl {
    this.children.push(child);
    return child;
  }
}

function textOf(node: FakeEl): string {
  const own = node.textContent || "";
  return own + node.children.map(textOf).join(" ");
}

let created: FakeEl[];

beforeEach(() => {
  created = [];
  vi.stubGlobal("document", {
    createElement: (tag: string) => {
      const node = new FakeEl(tag);
      created.push(node);
      return node;
    },
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const dict = {
  results: [{ name: "literature-review" }, { name: "plan-ml-experiment" }],
  semantic_status: "ok",
  semantic_suggestions: [
    {
      name: "literature-review",
      p_fit: 0.81,
      confidence: 0.64,
      template_version: "skills.suggest.1",
    },
  ],
};

describe("skillSearchView", () => {
  it("treats a raw list as the legacy shape", () => {
    const view = skillSearchView([{ name: "audit-dataset" }, { name: "evaluate-model" }]);
    expect(view.kind).toBe("list");
    expect(view.suggestions).toEqual([]);
    expect(view.status).toBeNull();
    expect(view.lexicalNames).toEqual(["audit-dataset", "evaluate-model"]);
  });

  it("treats the current {skills} projection as the legacy shape", () => {
    const view = skillSearchView({ skills: ["audit-dataset", "evaluate-model"] });
    expect(view.kind).toBe("list");
    expect(view.suggestions).toEqual([]);
    expect(view.lexicalNames).toEqual(["audit-dataset", "evaluate-model"]);
  });

  it("reads the W2-A dict shape", () => {
    const view = skillSearchView(dict);
    expect(view.kind).toBe("dict");
    expect(view.status).toBe("ok");
    expect(view.lexicalNames).toEqual(["literature-review", "plan-ml-experiment"]);
    expect(view.suggestions).toEqual([
      {
        name: "literature-review",
        p_fit: 0.81,
        confidence: 0.64,
        template_version: "skills.suggest.1",
      },
    ]);
  });
});

describe("appendSemanticSkillSearch", () => {
  it("does not intercept the list / {skills} shape", () => {
    const box = new FakeEl("div");
    expect(appendSemanticSkillSearch(box as unknown as HTMLElement, { skills: ["a"] })).toBe(false);
    expect(box.children).toEqual([]);
  });

  it("paints chips above lexical names for the dict shape", () => {
    const box = new FakeEl("div");
    expect(appendSemanticSkillSearch(box as unknown as HTMLElement, dict)).toBe(true);
    const rendered = textOf(box);
    expect(rendered).toContain("Experimental suggestions");
    expect(rendered).toContain("literature-review");
    expect(rendered).toContain("p_fit 0.81");
    expect(rendered).toContain("confidence 0.64");
    expect(rendered).toContain("skills: literature-review, plan-ml-experiment");
    const chip = box.children[0]?.children.find((child) => child.dataset.judgmentChip);
    expect(chip?.title).toBe("skills.suggest.1");
  });

  it("shows a note when semantic_status is unavailable or uncertain", () => {
    const box = new FakeEl("div");
    appendSemanticSkillSearch(box as unknown as HTMLElement, {
      results: [],
      semantic_status: "unavailable",
      semantic_suggestions: [],
    });
    expect(textOf(box)).toContain("Semantic suggestions are unavailable.");
    const box2 = new FakeEl("div");
    appendSemanticSkillSearch(box2 as unknown as HTMLElement, {
      results: [],
      semantic_status: "uncertain",
      semantic_suggestions: [],
    });
    expect(textOf(box2)).toContain("Semantic suggestions are uncertain");
  });
});
