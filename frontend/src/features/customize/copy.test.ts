import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { connectorDescription, specialistBadge, specialistDescription, specialistName } from "./copy";

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
