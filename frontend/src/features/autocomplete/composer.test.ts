import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { skillsCatalog } from "../../stores/customize";
import { project } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { loadSkillsCatalog } from "./catalog";
import { ac, acClose, acPick, acUpdate } from "./composer";

/** Just enough DOM for the composer popup (no jsdom here). */
class El {
  id = "";
  className = "";
  textContent = "";
  children: El[] = [];
  parentNode: El | null = null;
  style: Record<string, string> = {};
  onmousedown: unknown = null;
  classList = { add() {}, remove() {}, toggle() {}, contains: () => false };
  set innerHTML(_v: string) {
    this.children = [];
  }
  appendChild(child: El): El {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }
  insertBefore(child: El): El {
    return this.appendChild(child);
  }
}

class Composer extends El {
  value = "";
  selectionStart = 0;
  scrollHeight = 40;
  setSelectionRange(start: number): void {
    this.selectionStart = start;
  }
  focus(): void {}
}

let composer: Composer;
let popup: El | null;

function type(value: string, caret = value.length): void {
  composer.value = value;
  composer.selectionStart = caret;
}

type Answer = (rows: unknown) => void;

/** Each `/projects/<pid>/artifacts` request waits until the test answers it. */
function pendingFiles(): Answer[] {
  const answers: Answer[] = [];
  vi.stubGlobal("fetch", () =>
    new Promise((resolve) => {
      answers.push((rows) =>
        resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify(rows)) }),
      );
    }),
  );
  return answers;
}

const FILES = [{ filename: "plot.png" }, { filename: "pca.csv" }, { filename: "plan.md" }];
const SKILLS = [{ name: "plot" }, { name: "pca" }, { name: "plan" }];

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

beforeEach(() => {
  resetStoreFields();
  acClose();
  composer = new Composer();
  composer.id = "composer";
  const parent = new El();
  parent.appendChild(composer);
  popup = null;
  vi.stubGlobal("document", {
    querySelector: (sel: string) => (sel === "#composer" ? composer : sel === "#composer-ac" ? popup : null),
    getElementById: (id: string) => (id === "composer" ? composer : id === "composer-ac" ? popup : null),
    createElement: () => {
      const node = new El();
      if (!popup) popup = node;
      return node;
    },
    body: new El(),
  });
});

afterEach(() => {
  acClose();
  vi.unstubAllGlobals();
});

describe("composer autocomplete after an async load", () => {
  it("drops an update whose file list lands after a newer keystroke's", async () => {
    project.value = "proj-order";
    const answers = pendingFiles();
    type("@p");
    const older = acUpdate();
    type("@pl");
    const newer = acUpdate();
    await settle();
    expect(answers).toHaveLength(2);
    // The newer request answers first; the older one lands last.
    answers[1]!(FILES);
    await newer;
    answers[0]!(FILES);
    await older;
    expect(ac.open).toBe(true);
    expect(ac.items.map((it) => it.label)).toEqual(["plot.png", "plan.md"]);
  });

  it("anchors on the token at the caret now, not the one read before the load", async () => {
    project.value = "proj-caret";
    const answers = pendingFiles();
    type("see @pl");
    const update = acUpdate();
    await settle();
    // Typed while the list was loading: text before the token shifts it.
    type("please see @pl");
    answers[0]!(FILES);
    await update;
    expect(ac.open).toBe(true);
    expect(ac.start).toBe("please see ".length);
    acPick(0);
    expect(composer.value).toBe("please see @plot.png ");
  });

  it("`/` completions come back after a failed catalog read", async () => {
    let up = false;
    vi.stubGlobal("fetch", () =>
      Promise.resolve(
        up
          ? { ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ skills: SKILLS })) }
          : { ok: false, status: 503, text: () => Promise.resolve('{"error":"catalog unavailable"}') },
      ),
    );
    type("/pl");
    await acUpdate();
    expect(ac.open).toBe(false);
    up = true;
    type("/pl");
    await acUpdate();
    expect(ac.open).toBe(true);
    expect(ac.items.map((it) => it.insert)).toEqual(["plot", "plan"]);
  });

  it("closes when the caret has left the token", async () => {
    project.value = "proj-left";
    const answers = pendingFiles();
    type("@pl and more", 3);
    const update = acUpdate();
    await settle();
    composer.selectionStart = composer.value.length;
    answers[0]!(FILES);
    await update;
    expect(ac.open).toBe(false);
  });
});

describe("the shared skills catalog", () => {
  it("stores no failed read: the next caller asks again", async () => {
    let up = false;
    let requests = 0;
    vi.stubGlobal("fetch", () => {
      requests += 1;
      return Promise.resolve(
        up
          ? { ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ skills: SKILLS })) }
          : { ok: false, status: 503, text: () => Promise.resolve('{"error":"catalog unavailable"}') },
      );
    });
    await expect(loadSkillsCatalog()).rejects.toThrow(/catalog unavailable/);
    expect(skillsCatalog.value).toBeNull();
    up = true;
    await expect(loadSkillsCatalog()).resolves.toEqual(SKILLS);
    expect(requests).toBe(2);
    // A stored catalog, even an empty one, answers without a request.
    await loadSkillsCatalog();
    skillsCatalog.value = [];
    await expect(loadSkillsCatalog()).resolves.toEqual([]);
    expect(requests).toBe(2);
  });

  it("concurrent callers share the one request in flight", async () => {
    let answer: () => void = () => {};
    let requests = 0;
    vi.stubGlobal("fetch", () => {
      requests += 1;
      return new Promise((resolve) => {
        answer = () =>
          resolve({ ok: true, status: 200, text: () => Promise.resolve(JSON.stringify({ skills: SKILLS })) });
      });
    });
    const first = loadSkillsCatalog();
    const second = loadSkillsCatalog();
    expect(second).toBe(first);
    answer();
    await expect(first).resolves.toEqual(SKILLS);
    expect(requests).toBe(1);
  });
});
