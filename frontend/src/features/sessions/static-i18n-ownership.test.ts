/**
 * A label code has written is no longer a static label.
 *
 * `#conv-title` and `#proj-current` start life as static markup
 * (`data-i18n-val="conv.title.default"`, `data-i18n="proj.fallbackName"`) and
 * are then owned by code: `setTitle()` writes the open session's name and
 * `renderProjMenu()` the current project's. Every static repaint -- the one
 * that runs when the locale chunks first land, and every language switch --
 * rewrote them back to "Session" / "Project". The title input commits on
 * blur, so focusing it and clicking away then renamed the session on the
 * server to "Session", and the real name was gone.
 *
 * These tests hold the locale modules behind a gate, open a named session
 * first, and only then let the dictionaries arrive.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

type FakeNode = {
  id: string;
  textContent: string;
  title: string;
  value: string;
  size: number;
  getAttribute: (name: string) => string | null;
  hasAttribute: (name: string) => boolean;
  removeAttribute: (name: string) => void;
  setAttribute: (name: string, value: string) => void;
};

function fakeNode(
  id: string,
  attrs: Record<string, string>,
  initial: Partial<Pick<FakeNode, "textContent" | "title" | "value">> = {},
): FakeNode {
  const map = new Map(Object.entries(attrs));
  return {
    id,
    textContent: initial.textContent ?? "",
    title: initial.title ?? "",
    value: initial.value ?? "",
    size: 0,
    getAttribute: (name) => (map.has(name) ? map.get(name)! : null),
    hasAttribute: (name) => map.has(name),
    removeAttribute: (name) => {
      map.delete(name);
    },
    setAttribute: (name, value) => {
      map.set(name, value);
    },
  };
}

type Chrome = { convTitle: FakeNode; projCurrent: FakeNode };

/** The Shell's markup for the two nodes, as rendered (components/dashboard/Shell.tsx). */
function installChrome(): Chrome {
  const convTitle = fakeNode(
    "conv-title",
    { "data-i18n-val": "conv.title.default", "data-i18n-title": "conv.title.rename" },
    { value: "会话", title: "重命名会话（回车保存）" },
  );
  const projCurrent = fakeNode("proj-current", { "data-i18n": "proj.fallbackName" }, { textContent: "项目" });
  const all = [convTitle, projCurrent];
  vi.stubGlobal("document", {
    documentElement: { lang: "" },
    activeElement: null,
    querySelector: (selector: string) => all.find((node) => `#${node.id}` === selector) ?? null,
    // Live, like the DOM: a node that drops its attribute drops out of the query.
    querySelectorAll: (selector: string) => {
      const attr = /^\[([\w-]+)\]$/.exec(selector)?.[1];
      return attr ? all.filter((node) => node.hasAttribute(attr)) : [];
    },
  });
  return { convTitle, projCurrent };
}

let release: () => void = () => undefined;

async function importWithGatedLocales() {
  vi.resetModules();
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const realEn = (await vi.importActual<{ default: Record<string, string> }>("../../i18n/en")).default;
  const realZh = (await vi.importActual<{ default: Record<string, string> }>("../../i18n/zh")).default;
  vi.doMock("../../i18n/en", async () => {
    await gate;
    return { default: realEn };
  });
  vi.doMock("../../i18n/zh", async () => {
    await gate;
    return { default: realZh };
  });
  const runtime = await import("../../i18n/runtime");
  const dom = await import("./dom");
  const projectsView = await import("./projects");
  const store = await import("../../stores/session");
  return { runtime, dom, projectsView, store, realEn };
}

beforeEach(() => {
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => (key === "os-lang" ? "en" : null),
    setItem: () => undefined,
  });
});

afterEach(() => {
  release();
  vi.doUnmock("../../i18n/en");
  vi.doUnmock("../../i18n/zh");
  vi.resetModules();
  vi.unstubAllGlobals();
});

describe("labels code owns survive static i18n repaints", () => {
  it("keeps the open session's name and project when the locale chunks land late", async () => {
    const chrome = installChrome();
    const { runtime, dom, projectsView, store, realEn } = await importWithGatedLocales();

    // A deep link opened the session while the dictionaries were in flight.
    store.project.value = "proj_1";
    store.projects.value = [{ project_id: "proj_1", name: "Browser smoke project" }];
    store._titleName.value = "My Real Session";
    dom.setTitle("My Real Session");
    projectsView.renderProjMenu();

    release();
    await runtime.i18nReady();

    expect(chrome.convTitle.value).toBe("My Real Session");
    expect(chrome.projCurrent.textContent).toBe("Browser smoke project");
    // The tooltip is still a static label, and is still translated.
    expect(chrome.convTitle.title).toBe(realEn["conv.title.rename"]);
  });

  it("keeps them across a language switch", async () => {
    const chrome = installChrome();
    const { runtime, dom, projectsView, store } = await importWithGatedLocales();
    release();
    await runtime.i18nReady();

    store.project.value = "proj_1";
    store.projects.value = [{ project_id: "proj_1", name: "Browser smoke project" }];
    store._titleName.value = "My Real Session";
    dom.setTitle("My Real Session");
    projectsView.renderProjMenu();

    await runtime.setLang("zh");
    expect(chrome.convTitle.value).toBe("My Real Session");
    expect(chrome.projCurrent.textContent).toBe("Browser smoke project");
    await runtime.setLang("en");
    expect(chrome.convTitle.value).toBe("My Real Session");
    expect(chrome.projCurrent.textContent).toBe("Browser smoke project");
  });

  it("still translates them while nothing has written them", async () => {
    const chrome = installChrome();
    const { runtime, realEn } = await importWithGatedLocales();
    release();
    await runtime.i18nReady();

    expect(chrome.convTitle.value).toBe(realEn["conv.title.default"]);
    expect(chrome.projCurrent.textContent).toBe(realEn["proj.fallbackName"]);
  });
});
