import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

type ModalApi = typeof import("./modal");
type TeamApi = typeof import("./team");

class MiniClassList {
  tokens = new Set<string>();
  constructor(
    private readonly onChange?: () => void,
    initial?: string,
  ) {
    if (initial) for (const t of initial.split(/\s+/).filter(Boolean)) this.tokens.add(t);
  }
  add(...n: string[]): void {
    for (const x of n) this.tokens.add(x);
    this.onChange?.();
  }
  remove(...n: string[]): void {
    for (const x of n) this.tokens.delete(x);
    this.onChange?.();
  }
  contains(n: string): boolean {
    return this.tokens.has(n);
  }
  toggle(n: string, force?: boolean): void {
    if (force === true) this.add(n);
    else if (force === false) this.remove(n);
    else if (this.tokens.has(n)) this.remove(n);
    else this.add(n);
  }
}

type MiniEl = {
  id: string;
  tagName: string;
  className: string;
  classList: MiniClassList;
  children: MiniEl[];
  parent: MiniEl | null;
  attrs: Map<string, string>;
  textContent: string;
  innerHTML: string;
  style: Record<string, string>;
  onclick: ((e?: Event) => void) | null;
  offsetParent: MiniEl | null;
  href?: string;
  type?: string;
  files?: File[];
  value?: string;
  appendChild: (c: MiniEl) => MiniEl;
  querySelector: (sel: string) => MiniEl | null;
  querySelectorAll: (sel: string) => MiniEl[];
  hasAttribute: (n: string) => boolean;
  setAttribute: (n: string, v: string) => void;
  getAttribute: (n: string) => string | null;
  focus: () => void;
  addEventListener: (type: string, fn: () => void) => void;
  contains: (n: MiniEl | null) => boolean;
  remove: () => void;
};

function makeEl(doc: MiniDoc, tag: string): MiniEl {
  let text = "";
  const node: MiniEl = {
    id: "",
    tagName: tag.toUpperCase(),
    className: "",
    classList: new MiniClassList(),
    children: [],
    parent: null,
    attrs: new Map(),
    textContent: "",
    innerHTML: "",
    style: {},
    onclick: null,
    offsetParent: null,
    href: "",
    type: "",
    files: undefined,
    value: "",
    appendChild(c: MiniEl) {
      c.parent = node;
      node.children.push(c);
      if (c.id) doc.ids.set(c.id, c);
      return c;
    },
    querySelector(sel: string) {
      return node.querySelectorAll(sel)[0] || null;
    },
    querySelectorAll(sel: string) {
      const out: MiniEl[] = [];
      const walk = (n: MiniEl) => {
        for (const c of n.children) {
          if (match(c, sel)) out.push(c);
          walk(c);
        }
      };
      walk(node);
      return out;
    },
    hasAttribute(n: string) {
      return node.attrs.has(n);
    },
    setAttribute(n: string, v: string) {
      node.attrs.set(n, v);
      if (n === "id") {
        node.id = v;
        doc.ids.set(v, node);
      }
    },
    getAttribute(n: string) {
      return node.attrs.get(n) ?? null;
    },
    focus() {
      doc.activeElement = node;
    },
    addEventListener() {},
    contains(n: MiniEl | null) {
      if (!n) return false;
      if (n === node) return true;
      return node.children.some((c) => c.contains(n));
    },
    remove() {
      if (!node.parent) return;
      node.parent.children = node.parent.children.filter((c) => c !== node);
    },
  };
  const syncHidden = (): void => {
    node.offsetParent = node.classList.contains("hidden") ? null : node;
  };
  node.classList = new MiniClassList(syncHidden);
  Object.defineProperty(node, "className", {
    configurable: true,
    get() {
      return [...node.classList.tokens].join(" ");
    },
    set(value: string) {
      node.classList = new MiniClassList(syncHidden, String(value || ""));
      syncHidden();
    },
  });
  Object.defineProperty(node, "textContent", {
    configurable: true,
    get() {
      if (node.children.length) return node.children.map((c) => c.textContent).join("");
      return text;
    },
    set(value: string) {
      text = value == null ? "" : String(value);
      node.children = [];
    },
  });
  node.offsetParent = node;
  return node;
}

function match(n: MiniEl, sel: string): boolean {
  if (sel.startsWith("#")) return n.id === sel.slice(1);
  if (sel.startsWith(".")) return n.classList.contains(sel.slice(1));
  if (sel === "[data-autofocus]") return n.hasAttribute("data-autofocus");
  if (sel.includes(",")) return sel.split(",").some((p) => match(n, p.trim()));
  return n.tagName === sel.toUpperCase();
}

class MiniDoc {
  ids = new Map<string, MiniEl>();
  body: MiniEl;
  documentElement: MiniEl;
  activeElement: MiniEl | null = null;
  constructor() {
    this.documentElement = makeEl(this, "html");
    this.body = makeEl(this, "body");
    this.documentElement.appendChild(this.body);
  }
  getElementById(id: string): MiniEl | null {
    return this.ids.get(id) || null;
  }
  createElement(tag: string): MiniEl {
    return makeEl(this, tag);
  }
  createTextNode(data: string): MiniEl {
    const n = makeEl(this, "#text");
    n.textContent = data;
    return n;
  }
  querySelector(sel: string): MiniEl | null {
    if (sel.startsWith("#")) return this.getElementById(sel.slice(1));
    return this.documentElement.querySelector(sel);
  }
  querySelectorAll(sel: string): MiniEl[] {
    return this.documentElement.querySelectorAll(sel);
  }
  contains(n: MiniEl | null): boolean {
    return this.documentElement.contains(n);
  }
}

describe("F-20 team surface", () => {
  let doc: MiniDoc;
  let loc: { pathname: string; replace: ReturnType<typeof vi.fn> };
  let raf: FrameRequestCallback[];
  let team: TeamApi;
  let modal: ModalApi;

  beforeEach(async () => {
    vi.resetModules();
    doc = new MiniDoc();
    loc = { pathname: "/", replace: vi.fn() };
    raf = [];
    vi.stubGlobal("document", doc);
    vi.stubGlobal("window", { document: doc, ac: { open: false } });
    vi.stubGlobal("location", loc);
    vi.stubGlobal("confirm", () => true);
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      raf.push(cb);
      return raf.length;
    });
    modal = await import("./modal");
    team = await import("./team");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("ensureTeamDom is idempotent and creates contract ids", () => {
    team.ensureTeamDom();
    team.ensureTeamDom();
    expect(doc.getElementById("team-user")).not.toBeNull();
    expect(doc.getElementById("team-admin")).not.toBeNull();
    expect(doc.getElementById("team-admin-modal")).not.toBeNull();
    expect(doc.getElementById("team-admin-body")).not.toBeNull();
    expect(doc.getElementById("team-admin-close")).not.toBeNull();
    expect(doc.getElementById("team-files-modal")).not.toBeNull();
    expect(doc.getElementById("team-user")?.classList.contains("hidden")).toBe(true);
    expect(doc.getElementById("team-admin")?.classList.contains("hidden")).toBe(true);
  });

  it("401 on /auth/me redirects to /login", async () => {
    team.ensureTeamDom();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 401, ok: false }));
    await team.probeTeamAuth();
    expect(loc.replace).toHaveBeenCalledWith("/login");
  });

  it("admin identity unhides the chip and the Team admin button", async () => {
    team.ensureTeamDom();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({
          team_mode: true,
          user: { username: "erika", role: "admin" },
        }),
      }),
    );
    await team.probeTeamAuth();
    const chip = doc.getElementById("team-user");
    expect(chip?.classList.contains("hidden")).toBe(false);
    expect(chip?.textContent).toContain("erika");
    expect(chip?.textContent).toContain("admin");
    expect(doc.getElementById("team-admin")?.classList.contains("hidden")).toBe(false);
  });

  it("a member does not see the Team admin button", async () => {
    team.ensureTeamDom();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({
          team_mode: true,
          user: { username: "mallory", role: "member" },
        }),
      }),
    );
    await team.probeTeamAuth();
    expect(doc.getElementById("team-user")?.classList.contains("hidden")).toBe(false);
    expect(doc.getElementById("team-admin")?.classList.contains("hidden")).toBe(true);
  });

  it("a guest landing on / is sent to /replay", async () => {
    team.ensureTeamDom();
    loc.pathname = "/";
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({
          team_mode: true,
          user: { username: "visitor", role: "guest" },
        }),
      }),
    );
    await team.probeTeamAuth();
    expect(loc.replace).toHaveBeenCalledWith("/replay");
  });

  it("openAdmin goes through openModalEl (stack push), close through closeModalEl", () => {
    team.bootTeam();
    const trigger = doc.getElementById("outside") || doc.createElement("button");
    if (!trigger.id) {
      trigger.id = "outside";
      doc.body.appendChild(trigger);
    }
    doc.activeElement = trigger;
    expect(modal._modalFocus.stack.length).toBe(0);
    team.openAdmin();
    const adminModal = doc.getElementById("team-admin-modal");
    expect(adminModal?.classList.contains("hidden")).toBe(false);
    expect(modal._modalFocus.stack.length).toBe(1);
    expect(modal._modalFocus.stack[0]?.el as unknown).toBe(adminModal);
    const close = doc.getElementById("team-admin-close");
    close?.onclick?.();
    expect(adminModal?.classList.contains("hidden")).toBe(true);
    expect(modal._modalFocus.stack.length).toBe(0);
  });

  it("openTeamFilesPanel uses the same trap", () => {
    team.bootTeam();
    team.openTeamFilesPanel();
    const files = doc.getElementById("team-files-modal");
    expect(files?.classList.contains("hidden")).toBe(false);
    expect(modal._modalFocus.stack.some((e) => (e.el as unknown) === files)).toBe(true);
    doc.getElementById("team-files-close")?.onclick?.();
    expect(files?.classList.contains("hidden")).toBe(true);
  });

  it("loadAdmin renders the five governance sections and .team-admin-table", async () => {
    team.bootTeam();
    const json = (body: unknown) => ({
      status: 200,
      ok: true,
      json: async () => body,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => {
        if (String(url).includes("/team/users")) {
          return json({
            users: [
              { id: "u1", username: "erika", role: "admin", disabled: false },
              { id: "u2", username: "mallory", role: "member", disabled: false },
            ],
          });
        }
        if (String(url).includes("/team/usage")) return json({ usage: [] });
        if (String(url).includes("/team/audit")) return json({ audit: [] });
        if (String(url).includes("/team/invites")) return json({ invites: [] });
        if (String(url).includes("/team/quotas")) return json({ quotas: [] });
        return json({});
      }),
    );
    await team.loadAdmin();
    const body = doc.getElementById("team-admin-body");
    const text = body?.textContent || "";
    expect(text).toContain("Users");
    expect(text).toContain("Usage");
    expect(text).toContain("Quotas");
    expect(text).toContain("Invites");
    expect(text).toContain("Audit");
    expect(text).toContain("erika");
    expect(text).toContain("mallory");
    expect(body?.querySelectorAll(".team-admin-table").length).toBeGreaterThan(0);
  });

  it("fmtSize matches app.js:13495-13500", () => {
    expect(team.fmtSize(512)).toBe("512 B");
    expect(team.fmtSize(2048)).toBe("2.0 KB");
    expect(team.fmtSize(1048576)).toBe("1.0 MB");
    expect(team.fmtSize(1073741824)).toBe("1.0 GB");
  });

  it("source does not import window-exports", () => {
    const src = readFileSync(join(here, "team.ts"), "utf8");
    expect(src).not.toContain("window-exports");
    expect(src).toContain("openModalEl");
    expect(src).toContain("closeModalEl");
  });
});
