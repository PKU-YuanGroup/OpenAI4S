import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { projectFolderOf } from "../../components/artifacts/ProjectFolderSection";
import { projects } from "../../stores/session";
import {
  connectorDescription,
  connectorIdBadge,
  settingsT,
  specialistBadge,
  specialistDescription,
  specialistName,
} from "./copy";
import { projectName } from "./host";
import { memScopeLabel } from "./memory";

const REPO = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..", "..");

afterEach(async () => {
  await setLang("en");
});

describe("Settings describes specialists and connectors in the UI language (I18N-1)", () => {
  const scientist = { name: "SCIENTIST", source: "bundled", description: "Primary research agent." };

  it("gives Chinese for the product's own entries and leaves a person's words alone", async () => {
    await setLang("zh");
    expect(specialistDescription(scientist)).toMatch(/^主研究智能体/);
    expect(specialistDescription({ name: "SCIENTIST", description: "my own scientist" })).toBe("my own scientist");
    expect(specialistBadge("subagent")).toBe("子智能体");
    expect(specialistBadge("custom")).toBe("自定义");
    const fetchText = "Fetch a URL and return its content (official; needs Node).";
    expect(connectorDescription("fetch", fetchText)).toMatch(/^抓取网址/);
    // A configured row is translated only while it still carries the catalog's text.
    expect(connectorDescription("fetch", fetchText, fetchText)).toMatch(/^抓取网址/);
    expect(connectorDescription("fetch", "our proxy fetcher", fetchText)).toBe("our proxy fetcher");
    expect(connectorDescription("lab-db", "Lab database")).toBe("Lab database");
    // SYNC-21: the configured example row is seeded with other words than the catalog's.
    const seeded = "Local demo MCP server: echo / now / calc / random_int.";
    expect(connectorDescription("example", seeded, "Local demo MCP server (always available)")).toMatch(/^本地演示/);
    await setLang("en");
    expect(connectorDescription("example", seeded, "Local demo MCP server (always available)")).toBe(seeded);
    expect(specialistDescription(scientist)).toBe("Primary research agent.");
    expect(connectorDescription("fetch", fetchText)).toBe(fetchText);
    expect(specialistBadge("subagent")).toBe("subagent");
  });

  it("names the read-only scout SCOUT, 侦察 in Chinese, and leaves a custom EXPLORE alone (decision 31)", async () => {
    const scout = { name: "SCOUT", source: "bundled", description: "Read-only scout." };
    await setLang("zh");
    expect(specialistName(scout)).toBe("侦察（SCOUT）");
    expect(specialistDescription(scout)).toMatch(/^只读侦察/);
    expect(specialistName({ name: "EXPLORE", source: "custom" })).toBe("EXPLORE");
    expect(specialistName({ name: "GENERAL", source: "bundled" })).toBe("GENERAL");
    await setLang("en");
    expect(specialistName(scout)).toBe("SCOUT");
  });

  it("has Chinese for every built-in specialist and catalog connector the daemon ships", async () => {
    const specialists = readFileSync(join(REPO, "openai4s", "specialists.py"), "utf8");
    const names = [...specialists.matchAll(/^\s+name="([A-Z_]+)",$/gm)].map((m) => m[1]!);
    const gateway = readFileSync(join(REPO, "openai4s", "server", "gateway.py"), "utf8");
    const catalog = gateway.slice(gateway.indexOf("_CONNECTOR_DIRECTORY = ["));
    const ids = [...catalog.slice(0, catalog.indexOf("\n]\n")).matchAll(/"id": "([^"]+)"/g)].map((m) => m[1]!);
    expect(names.length).toBeGreaterThan(3);
    expect(ids.length).toBeGreaterThan(3);
    await setLang("zh");
    const english = (text: string) => /^[\x20-\x7e]*$/.test(text);
    expect(names.filter((name) => english(specialistDescription({ name, source: "bundled", description: "x" })))).toEqual([]);
    expect(ids.filter((id) => english(connectorDescription(id, "x")))).toEqual([]);
  });
});

// I18N-2: the seeded example project showed its stored English name in the
// memory scope ("注入到 Example project") and the Files panel's project folder
// while the dashboard and sidebar said 示例项目.
describe("the seeded example project's name outside the dashboard", () => {
  afterEach(() => {
    projects.value = [];
  });

  it("is shown in the UI language until it is renamed", async () => {
    projects.value = [
      { project_id: "proj_example", name: "Example project", folder_path: "/data/example" },
      { project_id: "p2", name: "Example project", folder_path: "/data/p2" },
    ];
    await setLang("zh");
    expect(memScopeLabel("proj_example")).toBe("示例项目");
    expect(projectName("proj_example")).toBe("示例项目");
    expect(projectFolderOf("proj_example")?.name).toBe("示例项目");
    // Another project that merely has the same name keeps it.
    expect(memScopeLabel("p2")).toBe("Example project");
    projects.value = [{ project_id: "proj_example", name: "Assays", folder_path: "/data/example" }];
    expect(memScopeLabel("proj_example")).toBe("Assays");
  });
});

// SET-4: Settings explained two tabs in the agent's API ("call with
// host.delegate(task, name=…)", "the agent calls them with
// host.mcp.call(id, tool, args)"), and listed the example connector as
// "Example example" (its name, then its id).
describe("Settings speaks in the user's words, not the agent's API", () => {
  it("describes the specialists and connectors tabs without host.* calls", async () => {
    for (const lang of ["zh", "en"] as const) {
      await setLang(lang);
      for (const key of ["cust.specialists.desc", "cust.connectors.desc"]) {
        expect(settingsT(key)).not.toMatch(/host\.|\(task|\(id/);
      }
    }
    await setLang("zh");
    expect(settingsT("cust.specialists.desc")).toContain("可以把子任务交给这些专家");
    expect(settingsT("cust.connectors.desc")).toContain("智能体可以调用这些外部工具");
  });

  it("shows a connector's id beside its name only when it says something the name does not", () => {
    expect(connectorIdBadge("Example", "example")).toBe("");
    expect(connectorIdBadge("Sequential thinking", "sequential-thinking")).toBe("");
    expect(connectorIdBadge("Files", "c1")).toBe("c1");
  });
});
