import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

type ModalApi = typeof import("./modal");
type TeamApi = typeof import("./team");
type ChipNode = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };

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
  let identity: typeof import("./team-identity");
  let chip: typeof import("./team-chip");

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
    identity = await import("./team-identity");
    chip = await import("./team-chip");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("ensureTeamDom is idempotent and builds the modals only", () => {
    team.ensureTeamDom();
    team.ensureTeamDom();
    expect(doc.getElementById("team-admin-modal")).not.toBeNull();
    expect(doc.getElementById("team-admin-body")).not.toBeNull();
    expect(doc.getElementById("team-admin-close")).not.toBeNull();
    expect(doc.getElementById("team-files-modal")).not.toBeNull();
    // TEAM-21: the chip and the entry buttons are the Shell's (team-chip.tsx).
    // Appended to <body> they sat under the dashboard where nobody saw them.
    for (const id of ["team-user", "team-admin", "team-files-dash", "team-files-btn"]) {
      expect(doc.getElementById(id), id).toBeNull();
    }
    expect(doc.body.children.map((node) => node.id).sort()).toEqual([
      "team-admin-modal",
      "team-files-modal",
    ]);
  });

  it("401 on /auth/me redirects to /login", async () => {
    team.ensureTeamDom();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 401, ok: false }));
    await team.probeTeamAuth();
    expect(loc.replace).toHaveBeenCalledWith("/login");
  });

  it("401 on a deep link sends the page along as ?next= (TEAM-9)", async () => {
    vi.stubGlobal("location", { ...loc, pathname: "/projects/p1/frames/f-abc", search: "?x=1" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 401, ok: false }));
    await team.probeTeamAuth();
    expect(loc.replace).toHaveBeenCalledWith(
      "/login?next=" + encodeURIComponent("/projects/p1/frames/f-abc?x=1"),
    );
  });

  const signedIn = async (user: Record<string, unknown>): Promise<void> => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        json: async () => ({ team_mode: true, user }),
      }),
    );
    await team.probeTeamAuth();
  };
  /** Every element vnode under a `TeamChip` render, flattened. */
  const chipNodes = (placement: "dash" | "side"): ChipNode[] => {
    const out: ChipNode[] = [];
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) return node.forEach(walk);
      if (!node || typeof node !== "object") return;
      const vnode = node as ChipNode;
      if (typeof vnode.type === "string") out.push(vnode);
      walk(vnode.props?.children);
    };
    walk(chip.TeamChip({ placement }));
    return out;
  };
  const chipText = (node: unknown): string => {
    if (Array.isArray(node)) return node.map(chipText).join("");
    if (typeof node === "string" || typeof node === "number") return String(node);
    return node && typeof node === "object" ? chipText((node as ChipNode).props?.children) : "";
  };
  const menuLabels = (user: Record<string, unknown>): string[] =>
    team.teamMenuItems(user).flatMap((item) => (item.label ? [item.label] : []));

  it("single-user mode renders no chip at all", () => {
    expect(chip.TeamChip({ placement: "dash" })).toBeNull();
    expect(chip.TeamChip({ placement: "side" })).toBeNull();
  });

  it("an admin gets a named chip in both placements and a Team admin menu entry", async () => {
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
    await signedIn({ username: "erika", role: "admin", kind: "user" });
    expect(identity.teamUser.value?.username).toBe("erika");
    const dash = chipNodes("dash").find((node) => node.props?.id === "team-user");
    expect(dash, "dashboard chip").toBeTruthy();
    expect(String(dash?.props?.class)).not.toContain("hidden");
    expect(chipText(dash)).toContain("erika");
    expect(chipText(dash)).toContain("admin");
    const side = chipNodes("side").find((node) => node.props?.id === "team-user-side");
    expect(side, "sidebar chip").toBeTruthy();
    expect(String(side?.props?.["aria-label"])).toContain("erika");
    expect(menuLabels({ username: "erika", role: "admin" })).toEqual([
      "Team admin",
      "Change password",
      "My model keys",
      "Sign out",
    ]);
    await runtime.setLang("system");
  });

  it("a member gets the chip and Sign out, never the Team admin entry", async () => {
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
    await signedIn({ username: "mallory", role: "member", kind: "user" });
    expect(chipNodes("dash").some((node) => node.props?.id === "team-user")).toBe(true);
    // TEAM-22: a member's own password and model keys, beside Sign out.
    expect(menuLabels({ username: "mallory", role: "member" })).toEqual([
      "Change password",
      "My model keys",
      "Sign out",
    ]);
    // neither for a replay-only guest, nor for the CLI's service identity
    expect(menuLabels({ username: "visitor", role: "guest" })).toEqual(["Sign out"]);
    expect(menuLabels({ username: "cli", role: "admin", kind: "service" })).toEqual([
      "Team admin",
      "Sign out",
    ]);
    await runtime.setLang("system");
  });

  it("Sign out posts /auth/logout and lands on /login without a native confirm", async () => {
    const confirmSpy = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmSpy);
    const fetchSpy = vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchSpy);
    const signOutItem = team.teamMenuItems({ username: "mallory", role: "member" }).at(-1);
    signOutItem?.onClick?.();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(fetchSpy).toHaveBeenCalledWith("/api/v1/auth/logout", { method: "POST" });
    expect(loc.replace).toHaveBeenCalledWith("/login");
  });

  it("the Team files buttons follow the roots the server lists", () => {
    expect(chipNodes("dash")).toEqual([]);
    identity.teamFilesAvailable.value = true;
    expect(chipNodes("dash").some((node) => node.props?.id === "team-files-dash")).toBe(true);
    expect(chipNodes("side").some((node) => node.props?.id === "team-files-btn")).toBe(true);
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

  const settle = async (): Promise<void> => {
    for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
  };
  const submit = (form: MiniEl | null | undefined): void =>
    (form as unknown as { onsubmit: (e: { preventDefault: () => void }) => void }).onsubmit({
      preventDefault: () => undefined,
    });

  it("Change password sends the current and the new password, and says how it went (TEAM-22)", async () => {
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
    const account = await import("./team-account");
    const fetchSpy = vi.fn().mockResolvedValue({ status: 200, ok: true, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetchSpy);
    account.openChangePassword();
    const dialog = doc.getElementById("team-password-modal");
    expect(dialog?.classList.contains("hidden")).toBe(false);
    // a real close control, with an icon and a name
    const close = doc.getElementById("team-password-modal-close");
    expect(close?.getAttribute("aria-label")).toBe("Close");
    expect(close?.children.length).toBe(1);
    const set = (id: string, value: string) => {
      (doc.getElementById(id) as MiniEl).value = value;
    };
    const line = () => dialog?.querySelector(".team-form-status")?.textContent;

    set("team-password-current", "old-password-1");
    set("team-password-new", "new-password-2");
    set("team-password-again", "new-password-3");
    submit(dialog?.querySelector("form"));
    expect(line()).toBe("The two new passwords do not match.");
    expect(fetchSpy).not.toHaveBeenCalled();

    set("team-password-again", "new-password-2");
    submit(dialog?.querySelector("form"));
    await settle();
    expect(fetchSpy).toHaveBeenCalledWith("/api/v1/auth/me/password", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ current_password: "old-password-1", new_password: "new-password-2" }),
    });
    expect(line()).toBe("Password changed.");

    fetchSpy.mockResolvedValue({ status: 403, ok: false, json: async () => ({ code: "wrong_password" }) });
    set("team-password-current", "nope");
    set("team-password-new", "new-password-4");
    set("team-password-again", "new-password-4");
    submit(dialog?.querySelector("form"));
    await settle();
    expect(line()).toBe("The current password is not right.");
    expect(account.passwordFailure("weak_password", 400)).toBe("The new password needs at least 8 characters.");
    expect(account.passwordFailure("", 429)).toBe("Too many attempts. Wait a minute, then try again.");
    await runtime.setLang("system");
  });

  it("My model keys lists, saves and removes the user's own keys, never reading one back (TEAM-22)", async () => {
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
    const account = await import("./team-account");
    let keys = [{ provider: "deepseek", updated_at: 1700000000000 }];
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
        calls.push({ url, method: init?.method || "GET", body: init?.body });
        if (url === "/api/v1/models") {
          return { status: 200, ok: true, json: async () => ({ models: { default: [{ provider: "claude" }, { provider: "deepseek" }] } }) };
        }
        if (init?.method === "PUT") keys = [...keys, { provider: "claude", updated_at: 1700000000001 }];
        if (init?.method === "DELETE") keys = keys.filter((k) => k.provider !== "deepseek");
        return { status: 200, ok: true, json: async () => ({ keys }) };
      }),
    );
    account.openMyKeys();
    await settle();
    const dialog = doc.getElementById("team-keys-modal");
    const rows = () => (dialog?.querySelectorAll(".team-keys-provider") || []).map((n) => n.textContent);
    expect(rows()).toEqual(["deepseek"]);
    expect((doc.getElementById("team-keys-providers")?.children || []).map((o) => o.value)).toEqual(["claude", "deepseek"]);

    (doc.getElementById("team-keys-provider") as MiniEl).value = " Claude ";
    (doc.getElementById("team-keys-value") as MiniEl).value = "fake-key-not-real";
    submit(dialog?.querySelector("form"));
    await settle();
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.url).toBe("/api/v1/auth/me/llm-key");
    expect(JSON.parse(put?.body || "{}")).toEqual({ provider: "claude", api_key: "fake-key-not-real" });
    expect((doc.getElementById("team-keys-value") as MiniEl).value).toBe("");
    expect(rows()).toEqual(["deepseek", "claude"]);
    expect(dialog?.textContent).not.toContain("fake-key-not-real");

    const remove = dialog?.querySelectorAll("button").find((b) => b.textContent === "Remove");
    remove?.onclick?.();
    await settle();
    const del = calls.find((c) => c.method === "DELETE");
    expect(JSON.parse(del?.body || "{}")).toEqual({ provider: "deepseek" });
    expect(rows()).toEqual(["claude"]);
    await runtime.setLang("system");
  });

  const adminDaemon = () => {
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    const json = (body: unknown, status = 200) => ({ status, ok: status < 300, json: async () => body });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
        const u = String(url);
        calls.push({ url: u, method: init?.method || "GET", body: init?.body });
        if (init?.method === "POST" && u.endsWith("/reset-password")) return json({ ok: true, password: "fresh-generated-pw" });
        if (init?.method === "POST" && u.endsWith("/team/users")) return json({ user: {}, password: "initial-generated-pw" }, 201);
        if (init?.method === "POST" && u.includes("/u1/disable")) return json({ code: "last_admin", error: "x" }, 409);
        if (init?.method === "POST") return json({ ok: true });
        if (u.includes("/team/users")) {
          return json({
            users: [
              { id: "u1", username: "erika", display_name: "Erika E", role: "admin", disabled: false },
              { id: "u2", username: "mallory", role: "member", disabled: false },
              { id: "u3", username: "carol", role: "member", disabled: true },
            ],
          });
        }
        if (u.includes("/team/usage")) return json({ usage: [{ user_id: "u2", project_id: "proj_1", kind: "llm_output_tokens", total: 12, events: 1 }] });
        if (u.includes("/team/audit")) {
          return json({ audit: [{ ts: 1, actor: "erika", action: "user_disable", target: "u3" }, { ts: 2, actor: "x", action: "something_new", user_id: "u2" }] });
        }
        if (u.includes("/projects")) return json({ projects: [{ project_id: "proj_1", name: "Lab notebook" }] });
        return json({});
      }),
    );
    return calls;
  };
  const buttonsIn = (row: MiniEl | undefined) => (row?.querySelectorAll("button") || []).map((b) => b.textContent);
  const rowOf = (login: string) =>
    doc.getElementById("team-admin-body")?.querySelectorAll("tr").find((tr) => tr.children[0]?.textContent?.startsWith(login));

  it("the admin panel has a visible close icon and speaks the interface language (TEAM-25)", async () => {
    team.bootTeam();
    for (const id of ["team-admin-close", "team-files-close"]) {
      const close = doc.getElementById(id);
      expect(close?.children.length, id).toBe(1);
      expect(close?.getAttribute("aria-label")).toBeTruthy();
    }
    adminDaemon();
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("zh");
    team.openAdmin();
    await team.loadAdmin();
    expect(doc.getElementById("team-admin-title")?.textContent).toBe("团队管理");
    expect(doc.getElementById("team-admin-refresh")?.textContent).toBe("刷新");
    const text = doc.getElementById("team-admin-body")?.textContent || "";
    for (const word of ["显示名", "状态", "操作", "已停用", "正常", "停用用户", "停用", "启用", "重置密码"]) {
      expect(text, word).toContain(word);
    }
    await runtime.setLang("system");
  });

  it("the admin panel puts names beside ids (TEAM-25)", async () => {
    team.bootTeam();
    adminDaemon();
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
    await team.loadAdmin();
    const text = doc.getElementById("team-admin-body")?.textContent || "";
    expect(text).toContain("Lab notebook"); // usage: the project's name
    expect(text).not.toContain("proj_1");
    expect(text).toContain("Erika E"); // the display name is shown
    expect(text).toContain("user disabled"); // a known action, in words
    expect(text).toContain("something_new"); // an unknown one, as stored
    const audit = doc.getElementById("team-admin-body")?.querySelectorAll("tr").filter((tr) => tr.children[3]?.textContent === "carol");
    expect(audit?.length).toBe(1); // target u3 -> carol
    await runtime.setLang("system");
  });

  it("the users section disables, enables, resets and adds without the CLI (TEAM-25)", async () => {
    team.bootTeam();
    identity.teamUser.value = { id: "u1", username: "erika", role: "admin" };
    const calls = adminDaemon();
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
    team.openAdmin();
    await team.loadAdmin();
    // one's own row offers nothing that would sign oneself out
    expect(buttonsIn(rowOf("erika"))).toEqual([]);
    expect(buttonsIn(rowOf("mallory"))).toEqual(["Disable", "Reset password"]);
    expect(buttonsIn(rowOf("carol"))).toEqual(["Enable", "Reset password"]);

    // a destructive action asks once more, in place, then posts
    const disable = rowOf("mallory")?.querySelectorAll("button").find((b) => b.textContent === "Disable");
    disable?.onclick?.();
    expect(disable?.textContent).toBe("Confirm disable");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    disable?.onclick?.();
    await settle();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/api/v1/team/users/u2/disable"]);
    const status = () => doc.getElementById("team-admin-body")?.querySelector(".team-form-status")?.textContent || "";
    expect(status()).toContain("Disabled mallory");

    rowOf("carol")?.querySelectorAll("button").find((b) => b.textContent === "Enable")?.onclick?.();
    await settle();
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/v1/team/users/u3/enable")).toBe(true);
    expect(status()).toContain("Enabled carol");

    const reset = rowOf("mallory")?.querySelectorAll("button").find((b) => b.textContent === "Reset password");
    reset?.onclick?.();
    reset?.onclick?.();
    await settle();
    expect(status()).toBe("New password for mallory (shown once): fresh-generated-pw");

    (doc.getElementById("team-add-username") as MiniEl).value = " erin ";
    (doc.getElementById("team-add-display") as MiniEl).value = "Erin E";
    (doc.getElementById("team-add-role") as MiniEl).value = "guest";
    submit(doc.getElementById("team-admin-body")?.querySelector("form"));
    await settle();
    const add = calls.find((c) => c.method === "POST" && c.url === "/api/v1/team/users");
    expect(JSON.parse(add?.body || "{}")).toEqual({ username: "erin", display_name: "Erin E", role: "guest" });
    expect(status()).toBe("Created erin. Initial password (shown once): initial-generated-pw");
    identity.teamUser.value = null;
    await runtime.setLang("system");
  });

  it("loadAdmin renders the five governance sections and .team-admin-table", async () => {
    team.bootTeam();
    // These strings come from the active dictionary: on a non-English dev
    // box the OS default paints them in zh and the assertions below miss.
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("en");
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
    await runtime.setLang("system");
  });

  it("a directory read that answers last cannot repaint over the one opened after it", async () => {
    team.bootTeam();
    const answers = new Map<string, (body: unknown) => void>();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (url: string) =>
          new Promise((resolve) => {
            answers.set(String(url), (body) => resolve({ status: 200, ok: true, json: async () => body }));
          }),
      ),
    );
    const answer = async (path: string, body: unknown): Promise<void> => {
      const url = "/api/v1/files" + (path ? "?path=" + encodeURIComponent(path) : "");
      const respond = answers.get(url);
      expect(respond, url).toBeTruthy();
      respond!(body);
      await new Promise((resolve) => setTimeout(resolve, 0));
    };
    const rows = (): MiniEl[] => doc.getElementById("team-files-list")?.children || [];
    const open = (label: string): void => {
      const link = rows()
        .map((row) => row.children[0])
        .find((a) => a?.textContent === label);
      expect(link, label).toBeTruthy();
      link!.onclick?.({ preventDefault() {} } as unknown as Event);
    };

    team.openTeamFilesPanel();
    await answer("", { roots: [{ path: "/data" }] });
    open("📁 /data");
    await answer("/data", { path: "/data", entries: [{ name: "big", dir: true }, { name: "small", dir: true }] });
    open("📁 big");
    open("📁 small");
    await answer("/data/small", { path: "/data/small", entries: [{ name: "b.csv", size: 1 }] });
    await answer("/data/big", { path: "/data/big", entries: [{ name: "a.csv", size: 1 }] });
    const text = rows().map((row) => row.textContent).join("|");
    expect(text).toContain("b.csv");
    expect(text).not.toContain("a.csv");
    expect(team.teamFilesPath()).toBe("/data/small");
  });

  it("section headings follow the interface language", async () => {
    team.bootTeam();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async () => ({ status: 200, ok: true, json: async () => ({}) })),
    );
    const runtime = await import("../../i18n/runtime");
    await runtime.setLang("zh");
    await team.loadAdmin();
    const headings = (doc.getElementById("team-admin-body")?.children || [])
      .filter((node) => node.tagName === "H3")
      .map((node) => node.textContent);
    expect(headings).toEqual(["用户", "用量", "配额", "邀请", "审计（最近 50 条）"]);
    await runtime.setLang("en");
    await team.loadAdmin();
    expect(doc.getElementById("team-admin-body")?.textContent).toContain("Audit (latest 50)");
  });

  it("renders audit times from the epoch-millisecond ts the server stores", async () => {
    team.bootTeam();
    const ts = 1727136000000;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(async (url: string) => ({
        status: 200,
        ok: true,
        json: async () =>
          String(url).includes("/team/audit")
            ? { audit: [{ ts, actor: "erika", action: "login", target: "" }] }
            : {},
      })),
    );
    await team.loadAdmin();
    const text = doc.getElementById("team-admin-body")?.textContent || "";
    expect(text).not.toContain("Invalid Date");
    expect(text).toContain(new Date(ts).toLocaleString());
    expect(team.auditWhen(String(ts))).toBe(new Date(ts).toLocaleString());
    expect(team.auditWhen("2026-09-24T08:00:00Z")).toBe(new Date("2026-09-24T08:00:00Z").toLocaleString());
    expect(team.auditWhen(null)).toBe("");
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
