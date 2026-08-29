import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { CONTRACT_GLOBAL_NAMES } from "../compat/window-exports";
import { IDENTITY_S_FIELDS, S_FIELD_META, sSignals } from "./registry";

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = join(here, "..", "..", "..");
const contractPath = join(repoRoot, "tests", "webui-contract.md");
const migrationPath = join(here, "MIGRATION.md");

function backtickCell(raw: string): string | null {
  const m = raw.trim().match(/^`([^`]+)`$/);
  return m ? m[1]! : null;
}

function tablesAfter(md: string, headingPrefix: string): string[][] {
  const lines = md.split("\n");
  const start = lines.findIndex((line) => line.startsWith(headingPrefix));
  if (start < 0) throw new Error(`missing heading ${headingPrefix}`);
  const rows: string[][] = [];
  let inTable = false;
  for (let i = start + 1; i < lines.length; i++) {
    const line = lines[i]!;
    if (line.startsWith("#") && !inTable) break;
    if (line.startsWith("#") && inTable) break;
    if (line.startsWith("| ---")) {
      inTable = true;
      continue;
    }
    if (!line.startsWith("|")) {
      if (inTable) break;
      continue;
    }
    if (!inTable) continue;
    const cells = line
      .split("|")
      .slice(1, -1)
      .map((cell) => cell.trim());
    rows.push(cells);
  }
  return rows;
}

function allMigrationRows(md: string): Array<{
  name: string;
  path: string;
  origin: string;
  line: number;
  identity: boolean;
}> {
  const rows: Array<{
    name: string;
    path: string;
    origin: string;
    line: number;
    identity: boolean;
  }> = [];
  for (const line of md.split("\n")) {
    if (!line.startsWith("| `")) continue;
    const cells = line
      .split("|")
      .slice(1, -1)
      .map((cell) => cell.trim());
    const name = backtickCell(cells[0] ?? "");
    const path = backtickCell(cells[1] ?? "");
    if (!name || !path) continue;
    rows.push({
      name,
      path,
      origin: cells[2] ?? "",
      line: Number(cells[3]),
      identity: cells[4] === "yes",
    });
  }
  return rows;
}

describe("S field migration table vs F-01 contract", () => {
  const contract = readFileSync(contractPath, "utf8");
  const migration = readFileSync(migrationPath, "utf8");

  it("diffs contract S fields into MIGRATION.md (zero missing, zero extra vs meta)", () => {
    const privateFields = tablesAfter(contract, "### 2a. Private").map((row) => backtickCell(row[0] ?? ""));
    const otherFields = tablesAfter(contract, "### 2b. Other").map((row) => backtickCell(row[0] ?? ""));
    const contractFields = [...privateFields, ...otherFields].filter((name): name is string => !!name);

    const migrationS = allMigrationRows(migration).filter((row) => row.path !== "notebook._kc");
    const migrationNames = new Set(migrationS.map((row) => row.name));
    const metaNames = S_FIELD_META.map((row) => row.name);

    const missingFromMigration = contractFields.filter((name) => !migrationNames.has(name));
    const missingFromMeta = contractFields.filter((name) => !metaNames.includes(name));
    const metaNotInMigration = metaNames.filter((name) => !migrationNames.has(name));
    const migrationNotInMeta = [...migrationNames].filter((name) => !metaNames.includes(name));

    expect({ missingFromMigration, missingFromMeta, metaNotInMigration, migrationNotInMeta }).toEqual({
      missingFromMigration: [],
      missingFromMeta: [],
      metaNotInMigration: [],
      migrationNotInMeta: [],
    });
    expect(new Set(metaNames).size).toBe(122);
    expect(migrationNames.size).toBe(122);
    expect(contractFields.length).toBeGreaterThan(0);
  });

  it("each MIGRATION row store path matches S_FIELD_META", () => {
    const byName = new Map(S_FIELD_META.map((row) => [row.name, row]));
    for (const row of allMigrationRows(migration)) {
      if (row.name === "_kc") {
        expect(row.path).toBe("notebook._kc");
        continue;
      }
      const meta = byName.get(row.name);
      expect(meta, row.name).toBeTruthy();
      expect(row.path).toBe(`${meta!.store}.${meta!.name}`);
      expect(row.origin, row.name).toBe(meta!.origin);
      expect(row.line, row.name).toBe(meta!.originLine);
      expect(row.identity, row.name).toBe(meta!.identity);
    }
  });

  it("every meta field has a live signal on sSignals", () => {
    for (const row of S_FIELD_META) {
      expect(sSignals[row.name], row.name).toBeTruthy();
    }
    expect(Object.keys(sSignals).sort()).toEqual(S_FIELD_META.map((row) => row.name).sort());
  });

  it("marks the three identity objects the plan names", () => {
    expect([...IDENTITY_S_FIELDS].sort()).toEqual(
      ["_timelineView", "actionTimeline", "executionQueue"].sort(),
    );
    for (const name of IDENTITY_S_FIELDS) {
      const meta = S_FIELD_META.find((row) => row.name === name);
      expect(meta?.identity, name).toBe(true);
    }
  });

  it("diffs CONTRACT_GLOBAL_NAMES against webui-contract.md §1", () => {
    const names = tablesAfter(contract, "## 1. Bare window globals")
      .map((row) => backtickCell(row[0] ?? ""))
      .filter((name): name is string => !!name);
    expect([...CONTRACT_GLOBAL_NAMES]).toEqual(names);
  });
});
