import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { artifacts } from "../../stores/artifacts";
import { skillsCatalog } from "../../stores/customize";
import { currentId, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { filesT } from "../artifacts/copy";
import { bindComposer } from "../send/send";
import { loadSkillsCatalog } from "./catalog";
import {
  AC_DEBOUNCE_MS,
  AC_INDEX_LIMIT,
  AC_INDEX_TIMEOUT_MS,
  ac,
  acClose,
  acPending,
  acPick,
  acRender,
  acUpdate,
  bindComposerAutocomplete,
} from "./composer";
import { clearMentions, composerMentions } from "./mentions";

/** The version an `@` row pins. The text gets the name only (ART-16). */
function pinned(i = 0): string {
  return ac.items[i]?.mention?.version_id || "";
}

/** The versions the composer's text still carries, oldest pick first. */
function pins(): string[] {
  return composerMentions(composer.value).map((m) => m.version_id);
}

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
  remove(): void {
    const parent = this.parentNode;
    if (!parent) return;
    parent.children = parent.children.filter((child) => child !== this);
    this.parentNode = null;
  }
}

type FakeKey = {
  type: string;
  key?: string;
  shiftKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
  target?: unknown;
  stopped?: boolean;
  preventDefault: () => void;
  stopImmediatePropagation: () => void;
};

class Composer extends El {
  value = "";
  selectionStart = 0;
  scrollHeight = 40;
  private listeners = new Map<string, Array<(e: FakeKey) => void>>();
  setSelectionRange(start: number): void {
    this.selectionStart = start;
  }
  focus(): void {}
  addEventListener(type: string, fn: (e: FakeKey) => void): void {
    const list = this.listeners.get(type) || [];
    list.push(fn);
    this.listeners.set(type, list);
  }
  emit(e: FakeKey): void {
    for (const fn of this.listeners.get(e.type) || []) {
      fn(e);
      if (e.stopped) break;
    }
  }
}

let composer: Composer;
let popup: El | null;

function type(value: string, caret = value.length): void {
  composer.value = value;
  composer.selectionStart = caret;
}

type Pending = {
  url: string;
  resolve: (body: unknown) => void;
  reject: (err: unknown) => void;
};

let inflight: Pending[] = [];

/**
 * Each artifact-index request waits until the test answers it.
 * `respectAbort` rejects on the signal; the default stub ignores abort so a
 * late 200 still arrives after `acClose`.
 */
function pendingIndex(respectAbort = false): Pending[] {
  inflight = [];
  vi.stubGlobal("fetch", (input: unknown, init?: { signal?: AbortSignal }) => {
    return new Promise((resolve, reject) => {
      let settled = false;
      const pending: Pending = {
        url: String(input),
        resolve: (body) => {
          if (settled) return;
          settled = true;
          resolve({
            ok: true,
            status: 200,
            text: () => Promise.resolve(JSON.stringify(body)),
          });
        },
        reject: (err) => {
          if (settled) return;
          settled = true;
          reject(err);
        },
      };
      const signal = init?.signal;
      if (respectAbort && signal) {
        const onAbort = () => {
          pending.reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
        };
        if (signal.aborted) onAbort();
        else signal.addEventListener("abort", onAbort, { once: true });
      }
      inflight.push(pending);
    });
  });
  return inflight;
}

function page(rows: unknown[]): unknown {
  return { artifacts: rows, next_cursor: null, has_more: false };
}

function art(partial: {
  id: string;
  filename: string;
  version_id?: string;
  priority?: number;
  root_frame_id?: string;
}): Record<string, unknown> {
  return { priority: 0, version_id: "", artifact_id: partial.id, ...partial };
}

const FILES = [
  art({ id: "plot", filename: "plot.png" }),
  art({ id: "pca", filename: "pca.csv" }),
  art({ id: "plan", filename: "plan.md" }),
];
const SKILLS = [{ name: "plot" }, { name: "pca" }, { name: "plan" }];

function hintText(): string {
  const node = (popup?.children || []).find((child) => String(child.className).split(/\s+/).includes("ac-hint"));
  return node ? node.textContent : "";
}

/** Literal copy. `filesT` returns the key itself when the entry is missing, so a length check cannot see that. */
const HINTS = {
  searching: ["正在搜索项目文件…", "Searching project files…"],
  failed: [
    "项目文件搜索失败，仅显示本会话文件",
    "Project file search failed. Showing only this session's files.",
  ],
  recent: [
    "显示最近的项目文件，输入文件名可搜索全部",
    "Showing the most recent project files. Type a filename to search all of them.",
  ],
} as const;

function expectHint(kind: keyof typeof HINTS, text = hintText()): void {
  expect(HINTS[kind]).toContain(text);
  expect(text.includes("ac.files.")).toBe(false);
}

function fakeKey(key: string): FakeKey {
  const e: FakeKey = {
    type: "keydown",
    key,
    target: composer,
    preventDefault: () => {},
    stopImmediatePropagation: () => {
      e.stopped = true;
    },
  };
  return e;
}

type RootListeners = Record<string, Array<(e: FakeKey) => void>>;

function bindKeys(): {
  dispatch: ReturnType<typeof vi.fn>;
  fire: (e: FakeKey) => void;
  listeners: RootListeners;
} {
  const dispatch = vi.fn(() => Promise.resolve());
  const listeners: RootListeners = {};
  const root = {
    dataset: {} as Record<string, string>,
    addEventListener(type: string, fn: (e: FakeKey) => void) {
      (listeners[type] ||= []).push(fn);
    },
  };
  Object.assign(document, { documentElement: root });
  bindComposerAutocomplete();
  bindComposer(dispatch);
  return {
    dispatch,
    listeners,
    fire(e: FakeKey) {
      composer.emit(e);
      if (!e.stopped) {
        for (const fn of listeners.keydown || []) fn(e);
      }
    },
  };
}

function labels(): string[] {
  return ac.items.map((item) => item.label);
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.spyOn(AbortSignal, "timeout").mockImplementation((ms: number) => {
    const ctrl = new AbortController();
    setTimeout(() => {
      ctrl.abort(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
    }, ms);
    return ctrl.signal;
  });
  resetStoreFields();
  acClose();
  clearMentions();
  inflight = [];
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
  for (const pending of inflight) {
    pending.reject(Object.assign(new Error("cancelled"), { name: "AbortError" }));
  }
  inflight = [];
  acClose();
  vi.clearAllTimers();
  vi.restoreAllMocks();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("composer @ mentions use one artifact-index page", () => {
  it("requests the encoded project index for the filename and never the full array", async () => {
    project.value = "a/b c";
    currentId.value = "sess";
    const pending = pendingIndex();
    type("@abc");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS - 1);
    expect(pending).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(pending).toHaveLength(1);
    expect(AC_INDEX_LIMIT).toBe(20);
    const url = pending[0]!.url;
    expect(url).toContain("/projects/a%2Fb%20c/artifact-index?q=abc&limit=20");
    expect(url).not.toMatch(/\/artifacts(?:\?|$)/);
    pending[0]!.resolve(page([]));
    await update;
  });

  it("shows at most 8 of a 20-row page", async () => {
    project.value = "proj";
    const rows = Array.from({ length: 20 }, (_, i) =>
      art({ id: `id-${i}`, filename: `hit-${String(i).padStart(2, "0")}.txt`, version_id: `v-${i}` }),
    );
    const pending = pendingIndex();
    type("@hit");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(1);
    expect(pending[0]!.url).toContain("limit=20");
    pending[0]!.resolve(page(rows));
    await update;
    expect(labels()).toEqual(rows.slice(0, 8).map((row) => String(row.filename)));
    expect(ac.items).toHaveLength(8);
  });

  it("drops a late page for an older query", async () => {
    project.value = "proj-order";
    currentId.value = "sess";
    const pending = pendingIndex();
    type("@ab");
    const older = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(1);
    type("@abc");
    const newer = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(2);
    pending[1]!.resolve(page([art({ id: "new", filename: "abc-new.txt", version_id: "v-new" })]));
    await newer;
    // `abc-old.txt` still matches the newer query, so only the generation guard can drop it.
    pending[0]!.resolve(page([art({ id: "old", filename: "abc-old.txt", version_id: "v-old" })]));
    await older;
    expect(ac.open).toBe(true);
    expect(labels()).toEqual(["abc-new.txt"]);
    expect(ac.items.map((item) => item.insert)).toEqual(["abc-new.txt"]);
    expect(pinned()).toBe("v-new");
  });

  it("does not show a page fetched for a project the user has left", async () => {
    project.value = "proj/1";
    currentId.value = "sess";
    artifacts.value = [art({ id: "local", filename: "ab-local.txt", version_id: "v-local" })];
    const pending = pendingIndex();
    type("@ab");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(1);
    expect(pending[0]!.url).toContain("/projects/proj%2F1/artifact-index?");
    expectHint("searching");
    expect(labels()).toEqual(["ab-local.txt"]);
    project.value = "proj/2";
    pending[0]!.resolve(page([art({ id: "old", filename: "ab-old.txt", version_id: "v-old" })]));
    await update;
    expect(labels()).toEqual(["ab-local.txt"]);
    expect(labels().join(" ")).not.toContain("ab-old");
    expect(hintText()).toBe("");
    expect(pending).toHaveLength(1);
  });

  it("does not show a page fetched for a session the user has left", async () => {
    project.value = "proj";
    currentId.value = "sess-a";
    artifacts.value = [art({ id: "local", filename: "ab-local.txt", version_id: "v-local" })];
    const pending = pendingIndex();
    type("@ab");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    currentId.value = "sess-b";
    artifacts.value = [art({ id: "other", filename: "ab-other.txt", version_id: "v-other" })];
    pending[0]!.resolve(page([art({ id: "old", filename: "ab-server.txt", version_id: "v-server" })]));
    await update;
    expect(labels()).toEqual(["ab-other.txt"]);
    expect(hintText()).toBe("");
  });

  it("keeps this session's files and shows the error when the index request fails", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [art({ id: "local", filename: "abc-local.txt", version_id: "v-local" })];
    const urls: string[] = [];
    vi.stubGlobal("fetch", (input: unknown) => {
      urls.push(String(input));
      return Promise.reject(new Error("offline"));
    });
    type("@abc");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    await expect(update).resolves.toBeUndefined();
    expect(urls).toHaveLength(1);
    expect(urls[0]).toContain("/projects/proj/artifact-index?q=abc&limit=20");
    expect(urls[0]).not.toMatch(/\/artifacts(?:\?|$)/);
    expect(labels()).toEqual(["abc-local.txt"]);
    expectHint("failed");
    expect(ac.open).toBe(true);
  });

  it("lists a shared artifact once, pinning the index page's version", async () => {
    project.value = "proj";
    artifacts.value = [art({ id: "a1", filename: "plot.png", version_id: "v-session" })];
    const pending = pendingIndex();
    type("@plot");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(page([art({ id: "a1", filename: "plot.png", version_id: "v-server" })]));
    await update;
    expect(ac.items).toHaveLength(1);
    expect(ac.items[0]?.insert).toBe("plot.png");
    expect(pinned()).toBe("v-server");
  });

  it("writes only the filename and pins the exact version id beside it", async () => {
    project.value = "proj";
    currentId.value = "sess";
    const pending = pendingIndex();
    type("@pl");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(
      page([art({ id: "plot", filename: "plot.png", version_id: "v-f85486107c9c", root_frame_id: "other" })]),
    );
    await update;
    expect(ac.items[0]?.insert).toBe("plot.png");
    acPick(0);
    // ART-16: the hash used to land in the textarea, the bubble and the title.
    expect(composer.value).toBe("@plot.png ");
    expect(composerMentions(composer.value)).toEqual([
      { filename: "plot.png", version_id: "v-f85486107c9c", artifact_id: "plot", root_frame_id: "other" },
    ]);
  });

  it("a pick whose token was deleted again carries no pin", async () => {
    project.value = "proj";
    const pending = pendingIndex();
    type("@pl");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v-f85486107c9c" })]));
    await update;
    acPick(0);
    expect(pins()).toEqual(["v-f85486107c9c"]);
    type("never mind");
    expect(pins()).toEqual([]);
  });

  it("omits q for an empty query and says the rows are only the recent page", async () => {
    project.value = "proj";
    const pending = pendingIndex();
    type("@");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(1);
    const params = new URL(pending[0]!.url, "https://fixture.invalid").searchParams;
    expect(params.has("q")).toBe(false);
    expect(params.get("limit")).toBe("20");
    pending[0]!.resolve(page([art({ id: "recent", filename: "recent.txt", version_id: "v-recent" })]));
    await update;
    expect(labels()).toEqual(["recent.txt"]);
    expectHint("recent");
  });

  it("sends only the last request of a 5-character burst", async () => {
    project.value = "proj";
    const pending = pendingIndex();
    const updates: Promise<void>[] = [];
    for (const value of ["@a", "@ab", "@abc", "@abcd", "@abcde"]) {
      type(value);
      updates.push(acUpdate());
    }
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS - 1);
    expect(pending).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(pending).toHaveLength(1);
    expect(pending[0]!.url).toContain("q=abcde&limit=20");
    pending[0]!.resolve(page([]));
    await Promise.all(updates);
  });

  it("leaves hidden artifacts out of the popup", async () => {
    project.value = "proj";
    artifacts.value = [
      art({ id: "hidden-session", filename: "abc-hidden.txt", version_id: "v-h", priority: -1 }),
      art({ id: "shown-session", filename: "abc-shown.txt", version_id: "v-s", priority: 1 }),
    ];
    const pending = pendingIndex();
    type("@abc");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(
      page([
        art({ id: "hidden-index", filename: "abc-secret.txt", version_id: "v-x", priority: -1 }),
        art({ id: "shown-index", filename: "abc-indexed.txt", version_id: "v-i", priority: 0 }),
      ]),
    );
    await update;
    expect(labels()).toEqual(["abc-indexed.txt", "abc-shown.txt"]);
  });

  it("shows the searching line with this session's files while the page is in flight", async () => {
    project.value = "proj";
    artifacts.value = [art({ id: "local", filename: "abc-local.txt", version_id: "v-local" })];
    const pending = pendingIndex();
    type("@abc");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expectHint("searching");
    expect(labels()).toEqual(["abc-local.txt"]);
    pending[0]!.resolve(page([art({ id: "remote", filename: "abc-remote.txt", version_id: "v-remote" })]));
    await update;
    expect(labels()).toEqual(["abc-remote.txt", "abc-local.txt"]);
    expect(hintText()).toBe("");
  });
});

describe("composer autocomplete after an async load", () => {
  it("anchors on the token at the caret now, not the one read before the load", async () => {
    project.value = "proj-caret";
    const pending = pendingIndex();
    type("see @pl");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(1);
    // Typed while the list was loading: text before the token shifts it.
    type("please see @pl");
    pending[0]!.resolve(page(FILES));
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
    const pending = pendingIndex();
    type("@pl and more", 3);
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    composer.selectionStart = composer.value.length;
    pending[0]!.resolve(page(FILES));
    await update;
    expect(ac.open).toBe(false);
  });

  it("does not debounce # completions or ask the artifact index", async () => {
    const urls: string[] = [];
    vi.stubGlobal("fetch", (input: unknown) => {
      urls.push(String(input));
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify({ skills: SKILLS })),
      });
    });
    sessions.value = [{ name: "alpha" }, { name: "alpine" }];
    type("#al");
    await acUpdate();
    expect(ac.open).toBe(true);
    expect(ac.items.map((item) => item.insert)).toEqual(["alpha", "alpine"]);
    expect(urls).toHaveLength(0);
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(urls).toHaveLength(0);
  });
});

describe("filename-search hints", () => {
  it("keeps the Chinese and English strings, and does not call the search full-text", () => {
    const src = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../artifacts/copy.ts"),
      "utf8",
    );
    expect(src).toContain("正在搜索项目文件…");
    expect(src).toContain("项目文件搜索失败，仅显示本会话文件");
    expect(src).toContain("显示最近的项目文件，输入文件名可搜索全部");
    expect(src).toContain("Searching project files…");
    expect(src).toContain("Project file search failed. Showing only this session's files.");
    expect(src).toContain(
      "Showing the most recent project files. Type a filename to search all of them.",
    );
    expect(src.toLowerCase()).not.toContain("full-text");
    expect(src.toLowerCase()).not.toContain("full text");
    expectHint("searching", filesT("ac.files.searching"));
    expectHint("failed", filesT("ac.files.failed"));
    expectHint("recent", filesT("ac.files.recent"));
    const css = readFileSync(
      join(dirname(fileURLToPath(import.meta.url)), "../../../../openai4s/server/webui/style.css"),
      "utf8",
    );
    const hintRule = css.slice(css.indexOf(".composer-ac .ac-hint{"), css.indexOf(".composer-ac .ac-hint{") + 240);
    expect(hintRule).toContain("position:sticky");
    expect(hintRule).toContain("bottom:0");
    expect(hintRule).toContain("var(--bg)");
    expect(css).toContain(".composer-ac .ac-list{overflow:auto");
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

describe("applied rows stay up while the next @ search is in flight", () => {
  it("refilters the page already applied instead of replacing it with this session", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [];
    const pending = pendingIndex();
    type("@pl");
    const first = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(
      page([
        art({ id: "plot", filename: "plot.png", version_id: "v-plot" }),
        art({ id: "extra", filename: "extra-plot.txt", version_id: "v-extra" }),
      ]),
    );
    await first;
    expect(labels()).toEqual(["plot.png", "extra-plot.txt"]);

    type("@plo");
    const second = acUpdate();
    expect(acPending()).toBe(true);
    expect(ac.open).toBe(true);
    expect(labels()).toEqual(["plot.png", "extra-plot.txt"]);
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[1]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v-plot" })]));
    await second;
    expect(labels()).toEqual(["plot.png"]);
  });

  it("keeps the highlighted insert across a notice redraw and a reorder", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [
      art({ id: "plot", filename: "plot.png", version_id: "v-plot" }),
      art({ id: "plan", filename: "plan.md", version_id: "v-plan" }),
    ];
    const pending = pendingIndex();
    type("@p");
    const update = acUpdate();
    expect(labels()).toEqual(["plot.png", "plan.md"]);
    ac.idx = 1;
    acRender();
    const list = popup?.children.find((child) => String(child.className).split(/\s+/).includes("ac-list"));
    const highlighted = list?.children.find((child) => String(child.className).split(/\s+/).includes("on"));
    expect(highlighted).toBeTruthy();

    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expectHint("searching");
    expect(ac.items[ac.idx]?.insert).toBe("plan.md");
    expect(pinned(ac.idx)).toBe("v-plan");
    expect(highlighted?.parentNode).toBe(list);
    const hint = popup?.children.find((child) => String(child.className).split(/\s+/).includes("ac-hint"));
    expect(hint?.parentNode).toBe(popup);
    expect(list?.children.some((child) => String(child.className).split(/\s+/).includes("ac-hint"))).toBe(false);

    pending[0]!.resolve(
      page([
        art({ id: "pca", filename: "pca.csv", version_id: "v-pca" }),
        art({ id: "plot", filename: "plot.png", version_id: "v-plot" }),
        art({ id: "plan", filename: "plan.md", version_id: "v-plan" }),
      ]),
    );
    await update;
    expect(ac.items[ac.idx]?.insert).toBe("plan.md");
    expect(pinned(ac.idx)).toBe("v-plan");
    expect(labels()[0]).toBe("pca.csv");

    type("@pl");
    const narrowed = acUpdate();
    expect(acPending()).toBe(true);
    expect(labels()).toEqual(["plot.png", "plan.md"]);
    expect(ac.items[ac.idx]?.insert).toBe("plan.md");
    expect(pinned(ac.idx)).toBe("v-plan");
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[1]!.resolve(page([art({ id: "plan", filename: "plan.md", version_id: "v-plan" })]));
    await narrowed;
  });
});

describe("a dismiss invalidates a late page", () => {
  async function startSearch(respectAbort: boolean): Promise<{ pending: Pending[]; update: Promise<void> }> {
    project.value = "proj-dismiss";
    currentId.value = "sess";
    artifacts.value = [art({ id: "local", filename: "abc-local.txt", version_id: "v-local" })];
    const pending = pendingIndex(respectAbort);
    type("@abc");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(pending).toHaveLength(1);
    expect(acPending()).toBe(true);
    expect(ac.open).toBe(true);
    return { pending, update };
  }

  function deliverLate(pending: Pending[]): void {
    pending[0]?.resolve(page([art({ id: "late", filename: "abc-late.txt", version_id: "v-late" })]));
  }

  it.each([true, false])(
    "Escape, blur, and pick ignore a late 200 (abort respected=%s)",
    async (respectAbort) => {
      const keys = bindKeys();
      const escape = await startSearch(respectAbort);
      keys.fire(fakeKey("Escape"));
      expect(ac.open).toBe(false);
      expect(acPending()).toBe(false);
      deliverLate(escape.pending);
      await escape.update;
      expect(ac.open).toBe(false);
      expect(labels()).not.toContain("abc-late.txt");
      expect(hintText()).toBe("");

      const blur = await startSearch(respectAbort);
      const shown = labels();
      composer.emit({ ...fakeKey("unused"), type: "blur" });
      expect(acPending()).toBe(false);
      deliverLate(blur.pending);
      await blur.update;
      expect(labels()).toEqual(shown);
      expect(labels()).not.toContain("abc-late.txt");
      await vi.advanceTimersByTimeAsync(120);
      expect(ac.open).toBe(false);

      const pick = await startSearch(respectAbort);
      acPick(0);
      expect(composer.value).toBe("@abc-local.txt ");
      expect(pins()).toEqual(["v-local"]);
      type("@abc");
      deliverLate(pick.pending);
      await pick.update;
      expect(ac.open).toBe(false);
      expect(labels()).not.toContain("abc-late.txt");
      expect(hintText()).toBe("");
    },
  );

  it.each([true, false])(
    "Escape during an empty debounce cancels the search (abort respected=%s)",
    async (respectAbort) => {
      project.value = "proj";
      artifacts.value = [];
      const pending = pendingIndex(respectAbort);
      type("@zzz");
      const update = acUpdate();
      expect(ac.open).toBe(false);
      expect(acPending()).toBe(true);
      bindKeys().fire(fakeKey("Escape"));
      expect(acPending()).toBe(false);
      await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
      await update;
      expect(pending).toHaveLength(0);
      expect(ac.open).toBe(false);
    },
  );
});

describe("Enter waits for the in-flight page", () => {
  it("does not send, then completes from the highlighted row once the page lands", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [art({ id: "plot", filename: "plot.png", version_id: "v-session" })];
    const pending = pendingIndex();
    const keys = bindKeys();
    type("@plot");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(acPending()).toBe(true);
    expect(ac.items[ac.idx]?.insert).toBe("plot.png");
    expect(pinned(ac.idx)).toBe("v-session");
    keys.fire(fakeKey("Enter"));
    expect(keys.dispatch).not.toHaveBeenCalled();
    expect(composer.value).toBe("@plot");
    pending[0]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v-server" })]));
    await update;
    expect(keys.dispatch).not.toHaveBeenCalled();
    expect(composer.value).toBe("@plot.png ");
    expect(pins()).toEqual(["v-server"]);
    expect(ac.open).toBe(false);
  });

  it("does nothing when the page that lands has no rows", async () => {
    project.value = "proj";
    artifacts.value = [];
    const pending = pendingIndex();
    const keys = bindKeys();
    type("@zzz");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(acPending()).toBe(true);
    keys.fire(fakeKey("Enter"));
    expect(keys.dispatch).not.toHaveBeenCalled();
    pending[0]!.resolve(page([]));
    await update;
    expect(composer.value).toBe("@zzz");
    expect(ac.open).toBe(false);
    expect(keys.dispatch).not.toHaveBeenCalled();
  });
});

describe("a notice with no rows", () => {
  it("closes the popup when the recent page or the failure has nothing to list", async () => {
    project.value = "proj";
    artifacts.value = [];
    const pending = pendingIndex();
    type("@");
    const recent = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(page([]));
    await recent;
    expect(ac.open).toBe(false);
    expect(hintText()).toBe("");

    vi.stubGlobal("fetch", () => Promise.reject(new Error("offline")));
    type("@zzz");
    const failed = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    await failed;
    expect(ac.open).toBe(false);
    expect(hintText()).toBe("");
  });
});

describe("an index request that times out", () => {
  it("shows the failure hint and does not treat a user abort as that failure", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [art({ id: "local", filename: "abc-local.txt", version_id: "v-local" })];
    vi.stubGlobal("fetch", (_input: unknown, init?: { signal?: AbortSignal }) => {
      return new Promise((_resolve, reject) => {
        const signal = init?.signal;
        const fail = () => {
          const reason = signal?.reason;
          reject(reason instanceof Error ? reason : Object.assign(new Error("aborted"), { name: "AbortError" }));
        };
        if (!signal) return;
        if (signal.aborted) fail();
        else signal.addEventListener("abort", fail, { once: true });
      });
    });
    type("@abc");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(AbortSignal.timeout).toHaveBeenCalledWith(AC_INDEX_TIMEOUT_MS);
    expectHint("searching");
    await vi.advanceTimersByTimeAsync(AC_INDEX_TIMEOUT_MS);
    await update;
    expect(labels()).toEqual(["abc-local.txt"]);
    expectHint("failed");
    expect(ac.open).toBe(true);

    const pending = pendingIndex(true);
    type("@abcd");
    const next = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    expect(acPending()).toBe(true);
    bindKeys().fire(fakeKey("Escape"));
    pending[0]?.resolve(page([art({ id: "late", filename: "abcd-late.txt", version_id: "v-late" })]));
    await next;
    expect(ac.open).toBe(false);
    expect(hintText()).toBe("");
  });
});

describe("a pending @ search across entry points and engines", () => {
  const sendTarget = { closest: (selector: string) => (selector === "#send-btn" ? {} : null) };

  function pointer(listeners: RootListeners, type: string, onPrevent = () => {}): void {
    for (const fn of listeners[type] || []) {
      fn({
        type,
        target: sendTarget,
        preventDefault: onPrevent,
        stopImmediatePropagation: () => {},
      });
    }
  }

  /** Chrome 111-115, Firefox 121-123 and Safari 16.2-17.3 have no AbortSignal.any. */
  function withoutAbortSignalAny(run: () => Promise<void>): Promise<void> {
    const box = AbortSignal as unknown as { any?: unknown };
    const original = box.any;
    box.any = undefined;
    return run().finally(() => {
      box.any = original;
    });
  }

  it("without AbortSignal.any the request still goes out and Enter waits for it", () =>
    withoutAbortSignalAny(async () => {
      project.value = "proj";
      currentId.value = "sess";
      artifacts.value = [];
      const pending = pendingIndex(true);
      const { dispatch, fire } = bindKeys();
      type("@plot");
      const update = acUpdate();
      await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
      expect(pending).toHaveLength(1);
      expect(acPending()).toBe(true);
      fire(fakeKey("Enter"));
      expect(dispatch).not.toHaveBeenCalled();
      pending[0]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v1" })]));
      await update;
      expect(composer.value).toBe("@plot.png ");
      expect(pins()).toEqual(["v1"]);
    }));

  it("without AbortSignal.any a timeout still shows the failure hint", () =>
    withoutAbortSignalAny(async () => {
      project.value = "proj";
      currentId.value = "sess";
      artifacts.value = [art({ id: "local", filename: "plot-local.txt", version_id: "v-local" })];
      pendingIndex(true);
      type("@plot");
      const update = acUpdate();
      await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
      expectHint("searching");
      await vi.advanceTimersByTimeAsync(AC_INDEX_TIMEOUT_MS);
      await update;
      expectHint("failed");
    }));

  it("the send button keeps composer focus, waits for the search, then sends the completed text", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [];
    const pending = pendingIndex();
    const { dispatch, listeners } = bindKeys();
    type("@plot");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    let prevented = false;
    pointer(listeners, "mousedown", () => {
      prevented = true;
    });
    // mousedown -> blur -> click: a blur would cancel the search first.
    expect(prevented).toBe(true);
    pointer(listeners, "click");
    expect(dispatch).not.toHaveBeenCalled();
    pending[0]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v1" })]));
    await update;
    expect(composer.value).toBe("@plot.png ");
    expect(pins()).toEqual(["v1"]);
    pointer(listeners, "click");
    expect(dispatch).toHaveBeenCalledWith("@plot.png ");
  });

  it("an armed Enter only completes the @ token it was pressed on", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [];
    const pending = pendingIndex();
    const { fire } = bindKeys();
    type("a @res b @res");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    fire(fakeKey("Enter"));
    // A click moves the caret to the first token without an input event.
    composer.selectionStart = 6;
    pending[0]!.resolve(page([art({ id: "r", filename: "results.csv", version_id: "v1" })]));
    await update;
    expect(composer.value).toBe("a @res b @res");
  });

  it("Shift+Enter with no rows keeps its newline and arms nothing", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [];
    const pending = pendingIndex();
    const { dispatch, fire } = bindKeys();
    type("@plot");
    const update = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    let prevented = false;
    const shiftEnter = { ...fakeKey("Enter"), shiftKey: true };
    shiftEnter.preventDefault = () => {
      prevented = true;
    };
    fire(shiftEnter);
    expect(prevented).toBe(false);
    pending[0]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v1" })]));
    await update;
    expect(composer.value).toBe("@plot");
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("Enter in the debounce window, before any request, is held and completes", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [];
    const pending = pendingIndex();
    const { dispatch, fire } = bindKeys();
    type("@plot");
    const update = acUpdate();
    expect(pending).toHaveLength(0);
    fire(fakeKey("Enter"));
    expect(dispatch).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[0]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v1" })]));
    await update;
    expect(composer.value).toBe("@plot.png ");
    expect(pins()).toEqual(["v1"]);
  });

  it("the highlighted file stays highlighted when the index returns a newer version", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [
      art({ id: "plot", filename: "plot.png", version_id: "v-plot" }),
      art({ id: "plan", filename: "plan.md", version_id: "v-plan-old" }),
    ];
    const pending = pendingIndex();
    const { fire } = bindKeys();
    type("@p");
    const update = acUpdate();
    fire(fakeKey("ArrowDown"));
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    fire(fakeKey("Enter"));
    pending[0]!.resolve(
      page([
        art({ id: "plot", filename: "plot.png", version_id: "v-plot" }),
        art({ id: "plan", filename: "plan.md", version_id: "v-plan-new" }),
      ]),
    );
    await update;
    expect(composer.value).toBe("@plan.md ");
    expect(pins()).toEqual(["v-plan-new"]);
  });

  it("typing after an armed Enter disarms it", async () => {
    project.value = "proj";
    currentId.value = "sess";
    artifacts.value = [];
    const pending = pendingIndex();
    const { fire } = bindKeys();
    type("@plo");
    const first = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    fire(fakeKey("Enter"));
    type("@plot");
    const second = acUpdate();
    await vi.advanceTimersByTimeAsync(AC_DEBOUNCE_MS);
    pending[1]!.resolve(page([art({ id: "plot", filename: "plot.png", version_id: "v1" })]));
    await second;
    pending[0]!.resolve(page([art({ id: "plo", filename: "plot.png", version_id: "v0" })]));
    await first;
    expect(composer.value).toBe("@plot");
    expect(ac.open).toBe(true);
  });
});
