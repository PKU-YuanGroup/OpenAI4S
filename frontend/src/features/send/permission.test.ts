/**
 * Permission gate cards. `POST /frames/{id}/decision` answers 202
 * `decision_resolving` when the decision was accepted but its durable commit
 * outlived the request thread's bounded wait (openai4s/permissions.py
 * `resolve_result`, gateway `_DECISION_REFUSAL_STATUS`). That is "ask
 * again", not "failed": the tool thread commits and emits permission_resolved.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang, t } from "../../i18n/runtime";
import { resetStoreFields } from "../../stores/signal-field";
import { renderPermissionCard, resolvePermissionCard } from "./permission";

class El {
  tagName: string;
  classes = new Set<string>();
  children: El[] = [];
  parent: El | null = null;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  value = "";
  type = "";
  placeholder = "";
  title = "";
  disabled = false;
  onclick: (() => unknown) | null = null;
  private text = "";
  constructor(tag: string) {
    this.tagName = tag.toUpperCase();
  }
  classList = {
    add: (...names: string[]) => names.forEach((n) => this.classes.add(n)),
    remove: (...names: string[]) => names.forEach((n) => this.classes.delete(n)),
    contains: (name: string) => this.classes.has(name),
    toggle: (name: string, on?: boolean) => {
      const next = on === undefined ? !this.classes.has(name) : on;
      if (next) this.classes.add(name);
      else this.classes.delete(name);
      return next;
    },
  };
  set className(value: string) {
    this.classes = new Set(String(value).split(/\s+/).filter(Boolean));
  }
  get className(): string {
    return [...this.classes].join(" ");
  }
  get textContent(): string {
    return this.children.length ? this.children.map((c) => c.textContent).join("") : this.text;
  }
  set textContent(value: string) {
    this.children = [];
    this.text = String(value ?? "");
  }
  set innerHTML(_value: string) {
    this.children = [];
    this.text = "";
  }
  get firstChild(): El | null {
    return this.children[0] ?? null;
  }
  get isConnected(): boolean {
    let node: El | null = this;
    while (node.parent) node = node.parent;
    return node.tagName === "BODY";
  }
  appendChild(child: El): El {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }
  setAttribute(): void {}
  querySelector(sel: string): El | null {
    return this.querySelectorAll(sel)[0] ?? null;
  }
  querySelectorAll(sel: string): El[] {
    const out: El[] = [];
    const want = sel.startsWith(".") ? sel.slice(1) : null;
    const walk = (node: El): void => {
      for (const child of node.children) {
        if (want ? child.classes.has(want) : child.tagName === sel.toUpperCase()) out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

let body: El;
let messages: El;
let hintBox: El;

beforeEach(async () => {
  await setLang("en");
  resetStoreFields();
  body = new El("body");
  messages = body.appendChild(new El("div"));
  hintBox = body.appendChild(new El("div"));
  vi.stubGlobal("requestAnimationFrame", () => 1);
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
  vi.stubGlobal("document", {
    createElement: (tag: string) => new El(tag),
    createTextNode: (text: string) => {
      const node = new El("#text");
      node.textContent = text;
      return node;
    },
    getElementById: (id: string) => (id === "messages" ? messages : id === "composer-hint" ? hintBox : null),
    querySelector: (sel: string) => (sel === "#messages" ? messages : sel === "#composer-hint" ? hintBox : null),
    querySelectorAll: () => [],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function card(): { card: El; allow: El; deny: El } {
  const node = messages.querySelector(".perm-card")!;
  return { card: node, allow: node.querySelector(".perm-allow")!, deny: node.querySelector(".perm-deny")! };
}

async function settle(): Promise<void> {
  for (let i = 0; i < 10; i++) await Promise.resolve();
}

describe("permission card decisions", () => {
  it("a 202 decision_resolving keeps the decision down until permission_resolved settles it", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({
        ok: true,
        status: 202,
        text: () =>
          Promise.resolve(
            JSON.stringify({
              ok: false,
              decision_id: "d-1",
              error: "the decision was accepted and is still being committed; re-read the request to see its final state",
              code: "decision_resolving",
            }),
          ),
      }),
    );
    renderPermissionCard({ decision_id: "d-1", frame_id: "f-1", tool: "bash", input: { command: "ls" } });
    const { card: node, allow, deny } = card();
    allow.onclick!();
    await settle();
    // Not offered again: a second submit would be refused as in flight.
    expect(allow.disabled).toBe(true);
    expect(deny.disabled).toBe(true);
    expect(hintBox.textContent).not.toContain(t("toast.submitFailed", ""));
    expect(node.querySelector(".perm-status")!.textContent).not.toBe("");
    expect(node.classes.has("resolved")).toBe(false);

    // The tool thread commits and says so.
    resolvePermissionCard({ decision_id: "d-1", allow: true, scope: "once" });
    expect(node.classes.has("resolved")).toBe(true);
    expect(node.classes.has("allowed")).toBe(true);
    expect(node.querySelector(".perm-status")!.textContent).toBe(t("perm.status.allowed"));
  });

  it("a real refusal still offers the buttons again", async () => {
    vi.stubGlobal("fetch", () =>
      Promise.resolve({
        ok: false,
        status: 410,
        text: () => Promise.resolve(JSON.stringify({ ok: false, error: "approval request expired", code: "decision_expired" })),
      }),
    );
    renderPermissionCard({ decision_id: "d-2", frame_id: "f-1", tool: "bash", input: { command: "ls" } });
    const { allow, deny } = card();
    allow.onclick!();
    await settle();
    expect(allow.disabled).toBe(false);
    expect(deny.disabled).toBe(false);
    expect(hintBox.textContent).toContain("approval request expired");
  });
});

describe("pick-one permission cards", () => {
  function captureDecision(): { bodies: Record<string, unknown>[] } {
    const seen = { bodies: [] as Record<string, unknown>[] };
    vi.stubGlobal("fetch", (_url: string, init: { body?: string }) => {
      seen.bodies.push(JSON.parse(String(init.body || "{}")));
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify({ ok: true, allow: true })),
      });
    });
    return seen;
  }

  const fallbackEvent = {
    decision_id: "d-3",
    frame_id: "f-1",
    tool: "web_search_fallback",
    input: { query: "crispr" },
    choices: [
      { id: "you", label: "You.com" },
      { id: "builtin", label: "DuckDuckGo / Bing / Mojeek" },
    ],
  };

  it("sends the chosen engine as the decision pattern", async () => {
    const seen = captureDecision();
    renderPermissionCard(fallbackEvent);
    const { card: node, allow } = card();
    const choices = node.querySelector(".perm-choices")!.querySelectorAll(".perm-seg");
    expect(choices.map((c) => c.textContent)).toEqual(["You.com", "DuckDuckGo / Bing / Mojeek"]);
    choices[1]!.onclick!();
    allow.onclick!();
    await settle();
    expect(seen.bodies[0]).toMatchObject({ decision_id: "d-3", allow: true, pattern: "builtin" });
  });

  it("carries the choice even when nothing is remembered", async () => {
    const seen = captureDecision();
    renderPermissionCard({ ...fallbackEvent, decision_id: "d-4" });
    const { card: node, allow } = card();
    const once = node
      .querySelectorAll(".perm-scope")
      .find((row) => !row.classes.has("perm-choices"))!
      .querySelectorAll(".perm-seg")
      .find((b) => b.textContent === t("perm.scope.once"))!;
    once.onclick!();
    allow.onclick!();
    await settle();
    expect(seen.bodies[0]).toMatchObject({ scope: "once", pattern: "you" });
  });
});

describe("what a card remembers", () => {
  function captureDecision(): { bodies: Record<string, unknown>[] } {
    const seen = { bodies: [] as Record<string, unknown>[] };
    vi.stubGlobal("fetch", (_url: string, init: { body?: string }) => {
      seen.bodies.push(JSON.parse(String(init.body || "{}")));
      return Promise.resolve({
        ok: true,
        status: 200,
        text: () => Promise.resolve(JSON.stringify({ ok: true, allow: true })),
      });
    });
    return seen;
  }

  function scopeButton(node: El, label: string): El {
    return node
      .querySelectorAll(".perm-scope")
      .find((row) => !row.classes.has("perm-choices"))!
      .querySelectorAll(".perm-seg")
      .find((b) => b.textContent === label)!;
  }

  const shellCard = {
    decision_id: "d-r1",
    frame_id: "f-1",
    tool: "bash",
    target: 'python3 -c "import numpy; print(numpy.__version__)"',
    input: { command: 'python3 -c "import numpy; print(numpy.__version__)"' },
    suggested_patterns: [
      'python3 -c "import numpy; print(numpy.__version__)"',
      "python3 -c *",
      "python3 *",
      "*",
    ],
  };

  it("starts at once, so allowing remembers nothing", async () => {
    const seen = captureDecision();
    renderPermissionCard(shellCard);
    const { card: node, allow } = card();
    expect(scopeButton(node, t("perm.scope.once")).classes.has("active")).toBe(true);
    expect(scopeButton(node, t("perm.scope.conversation")).classes.has("active")).toBe(false);
    expect(node.querySelector(".perm-pat")!.style.display).toBe("none");
    allow.onclick!();
    await settle();
    expect(seen.bodies[0]).toMatchObject({ scope: "once" });
    expect(seen.bodies[0]).not.toHaveProperty("pattern");
  });

  it("widened, it proposes the command's prefix, names each choice, and never preselects *", async () => {
    const seen = captureDecision();
    renderPermissionCard({ ...shellCard, decision_id: "d-r2" });
    const { card: node, allow } = card();
    scopeButton(node, t("perm.scope.conversation")).onclick!();
    expect(node.querySelector(".perm-pat")!.style.display).toBe("");
    const input = node.querySelector(".perm-in")!;
    expect(input.value).toBe("python3 -c *");
    const chips = node.querySelectorAll(".perm-chip");
    expect(chips.map((c) => c.textContent)).toEqual([
      "This exact command",
      "python3 -c *",
      "python3 *",
      "Everything (not recommended)",
    ]);
    expect(chips.find((c) => c.classes.has("active"))?.dataset.pattern).toBe("python3 -c *");
    expect(chips[3]!.classes.has("danger")).toBe(true);
    expect(node.querySelector(".perm-note")!.textContent).toContain("still asks");
    allow.onclick!();
    await settle();
    expect(seen.bodies[0]).toMatchObject({ scope: "conversation", pattern: "python3 -c *" });
  });

  it("falls back to the exact command when the server offers no prefix (a chained command)", async () => {
    const { defaultRememberPattern } = await import("./permission");
    expect(
      defaultRememberPattern({ tool: "bash", target: "cd data && ls", suggested_patterns: ["cd data && ls", "*"] }),
    ).toBe("cd data && ls");
    expect(defaultRememberPattern({ tool: "bash", target: "x", suggested_patterns: ["*"] })).toBe("x");
    expect(
      defaultRememberPattern({ tool: "write_file", target: "out/a.csv", suggested_patterns: ["out/a.csv", "out/*", "*.csv", "*"] }),
    ).toBe("out/a.csv");
  });
});

describe("what a card says before the user decides", () => {
  it("shows the whole code of a dynamic tool, not only its name", async () => {
    const { permContext, permActionLine } = await import("./permission");
    const card = {
      tool: "dynamic_tool_define",
      target: "noop",
      input: {
        name: "noop",
        description: "Echo the input back",
        implementation: "def execute(args):\n    return args\n",
        input_schema: { type: "object" },
      },
    };
    expect(permActionLine(card).text).toBe("noop");
    const ctx = permContext(card);
    expect(ctx.code[0]?.text).toBe("def execute(args):\n    return args\n");
    expect(ctx.code[1]?.text).toContain('"type": "object"');
    expect(ctx.lines.join("\n")).toContain("Echo the input back");
  });

  it("names the environment an install goes into and that existing versions are kept", async () => {
    const { permContext } = await import("./permission");
    setLang("zh");
    const ctx = permContext({
      tool: "env_setup",
      input: {
        packages: ["lifelines"],
        environment: "lab_env",
        interpreter: "/envs/lab_env/bin/python",
      },
    });
    expect(ctx.lines[0]).toBe("安装到：lab_env (/envs/lab_env/bin/python)");
    expect(ctx.lines[1]).toContain("保持原版本");
  });

  it("warns that a switch targets one of the user's own environments", async () => {
    const { permContext, permActionLine } = await import("./permission");
    setLang("en");
    const card = { tool: "env_use", target: "local:work", input: { name: "work", local: true } };
    expect(permActionLine(card).text).toBe("work");
    expect(permContext(card).lines[0]).toContain("your own conda environments");
    expect(permContext({ tool: "env_use", input: { name: "python", local: false } }).lines).toEqual([]);
  });

  it("titles download, install and environment requests in the UI language (PERM-1)", async () => {
    const { permTitle } = await import("./permission");
    const download = {
      tool: "web_download",
      title: "Downloading from files.example.org",
      target: "files.example.org",
      input: { url: "https://files.example.org/download/demo.cif.gz", path: "structures/demo.cif.gz" },
    };
    const install = {
      tool: "env_setup",
      title: "Installing statsmodels into base",
      input: { packages: ["statsmodels", "seaborn"], environment: "base" },
    };
    const local = { tool: "env_use", title: "Switching to the local work environment", input: { name: "work", local: true } };
    await setLang("zh");
    expect(permTitle(download)).toBe("从 files.example.org 下载 demo.cif.gz");
    expect(permTitle(install)).toBe("安装 statsmodels, seaborn 到 base");
    expect(permTitle(local)).toBe("切换到本机环境 work");
    expect(permTitle({ tool: "env_use", input: { name: "r", local: false } })).toBe("切换到 r 环境");
    // Any other kind: the server's title through the step templates, else as it came.
    expect(permTitle({ tool: "bash", title: "Running python3" })).toBe("运行 python3");
    expect(permTitle({ tool: "mcp_call", title: "Calling calc via example" })).toBe("Calling calc via example");
    await setLang("en");
    expect(permTitle(download)).toBe("Download demo.cif.gz from files.example.org");
    expect(permTitle(install)).toBe("Install statsmodels, seaborn into base");
    expect(permTitle(local)).toBe("Switch to your local environment work");
  });

  it("shows a download's whole address, where it is saved and the size limit (PERM-1)", async () => {
    const { permContext, permActionLine, workspaceTarget } = await import("./permission");
    await setLang("zh");
    const card = {
      tool: "web_download",
      target: "files.example.org",
      input: { url: "https://files.example.org/download/demo.cif.gz", path: "structures/demo.cif.gz", max_bytes: 67108864 },
    };
    expect(permActionLine(card).text).toBe("https://files.example.org/download/demo.cif.gz");
    expect(permContext(card).lines).toEqual([
      "保存到工作区：structures/demo.cif.gz",
      "下载前无法得知大小；超过 64.0 MB 会中止。",
    ]);
    expect(permContext({ ...card, input: { ...card.input, size_bytes: 2048 } }).lines[1]).toBe("大小：2.0 KB");
    // Never a local directory: an absolute or escaping path is shown by its file name.
    expect(workspaceTarget("/home/someone/work/demo.cif.gz")).toBe("demo.cif.gz");
    expect(workspaceTarget("C:\\data\\demo.cif.gz")).toBe("demo.cif.gz");
    expect(workspaceTarget("../outside/demo.cif.gz")).toBe("demo.cif.gz");
    expect(workspaceTarget("./structures/demo.cif.gz")).toBe("structures/demo.cif.gz");
  });

  it("says where a shell command runs and whether the sandbox holds", async () => {
    const { permContext } = await import("./permission");
    setLang("en");
    const enforced = permContext({
      tool: "bash",
      input: {
        command: "python3 -m pip install lifelines",
        cwd: "/ws/f-1",
        environment: "lab_env",
        python: "/envs/lab_env/bin/python",
        sandbox: { backend: "seatbelt", enforced: true, network: "blocked" },
      },
    }).lines;
    expect(enforced).toEqual([
      "Runs in the lab_env environment (python → /envs/lab_env/bin/python)",
      "Working directory: /ws/f-1",
      "Kernel sandbox: on (seatbelt); network blocked",
    ]);
    const open = permContext({
      tool: "bash",
      input: { command: "ls", sandbox: { backend: "none", enforced: false } },
    }).lines;
    expect(open[0]).toContain("not enforced");
  });
});
