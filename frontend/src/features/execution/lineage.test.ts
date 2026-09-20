import { setExecutionFetch } from "./api";
import { loadLineage, renderProvenanceInto } from "./provenance";
import { lineage, _lineageFor } from "../../stores/notebook";
import { _artVer, _envSnapById, dockArtifact } from "../../stores/artifacts";
import { provMode, provSub } from "../../stores/ui";
import { resetStoreFields } from "../../stores/signal-field";
import { syncArtifactVersion } from "../artifacts/cache";
import { filesT } from "../artifacts/copy";
import { t } from "../../i18n/runtime";
import { describe, expect, it, vi } from "vitest";
import {
  asLineage,
  captureInRootNotebook,
  emptyLineage,
  envPackageCount,
  envPythonChip,
  envSnapshotHonesty,
  lineageCaptures,
  lineageCell,
  lineageReviewModel,
} from "./lineage";

describe("provenance chain transforms (app.js:10631-10833)", () => {
  it("emptyLineage represents no recorded evidence, not a fabricated producer", () => {
    const empty = emptyLineage();
    expect(empty.interactions).toEqual([]);
    expect(empty.dependency_mappings).toEqual({ inputs: [] });
    expect(lineageReviewModel(empty).empty).toBe(true);
    expect(lineageReviewModel(null).empty).toBe(true);
  });

  it("asLineage drops non-objects rather than inventing a cell", () => {
    expect(asLineage("nope").interactions).toEqual([]);
    expect(lineageCell(asLineage({ interactions: [{ kind: "save" }] }))).toBeNull();
  });

  it("extracts the producing cell and mapped vs cell inputs separately", () => {
    const model = lineageReviewModel({
      interactions: [
        {
          kind: "cell",
          cell_index: 3,
          language: "python",
          files_read: ["in.csv"],
          files_written: ["out.png"],
          source: "df.to_csv()",
        },
        { kind: "save", at: "2026-01-01T00:00:00Z" },
      ],
      dependency_mappings: { inputs: ["mapped.parquet"] },
    });
    expect(model.cell?.cell_index).toBe(3);
    expect(model.cellInputs).toEqual(["in.csv"]);
    expect(model.mappedInputs).toEqual(["mapped.parquet"]);
    expect(model.saveAt).toBe("2026-01-01T00:00:00Z");
    expect(model.empty).toBe(false);
  });

  it("keeps head_checksum_reused captures even when a cell card exists", () => {
    const lin = asLineage({
      interactions: [{ kind: "cell", cell_index: 1 }],
      capture_observations: [
        { capture_kind: "head_checksum_reused", producing_cell_id: "c-same" },
        { capture_kind: "version_written", producing_cell_id: "c-other" },
      ],
    });
    const withCell = lineageCaptures(lin, true);
    expect(withCell).toHaveLength(1);
    expect(withCell[0]?.capture_kind).toBe("head_checksum_reused");
    const withoutCell = lineageCaptures(lin, false);
    expect(withoutCell).toHaveLength(2);
  });

  it("does not treat a delegate capture as a root-Notebook cell", () => {
    expect(
      captureInRootNotebook({
        cell_index: 2,
        frame_kind: "delegate",
        producing_cell_id: "child-cell",
      }),
    ).toBe(false);
    expect(
      captureInRootNotebook({
        cell_index: 2,
        frame_kind: "session",
        producing_cell_id: "root-cell",
      }),
    ).toBe(true);
    expect(captureInRootNotebook({ frame_kind: "session" })).toBe(false);
  });

  it("falls back to producer when there is no cell and no captures", () => {
    const model = lineageReviewModel({
      producer: {
        kind: "native_tool",
        frame_id: "f1",
        frame_kind: "session",
      },
    });
    expect(model.empty).toBe(false);
    expect(model.cell).toBeNull();
    expect(model.captures).toEqual([]);
    expect(model.producer?.kind).toBe("native_tool");
  });

  it("env honesty is three states: live / verified / unverified", () => {
    expect(envSnapshotHonesty({ source: "live" })).toEqual({
      captured: false,
      verified: false,
      noteKey: "prov.env.liveFallback",
      noteClass: "warn",
      showProvenanceWhy: false,
    });
    expect(
      envSnapshotHonesty({ source: "captured", generation_confidence: "verified" }),
    ).toEqual({
      captured: true,
      verified: true,
      noteKey: "prov.env.recorded",
      noteClass: "ok",
      showProvenanceWhy: false,
    });
    const unverified = envSnapshotHonesty({
      source: "captured",
      generation_confidence: "legacy_unverified",
      provenance: "assumed: no kernel generation on record",
    });
    expect(unverified.noteKey).toBe("prov.env.recordedUnverified");
    expect(unverified.noteClass).toBe("warn");
    expect(unverified.showProvenanceWhy).toBe(true);
  });

  it("does not claim a Python version on an R (or empty) snapshot", () => {
    expect(envPythonChip({ kind: "r", python_version: null })).toBeNull();
    expect(envPythonChip({ kind: "python" })).toBeNull();
    expect(envPythonChip({ python_version: "3.11.8", implementation: "CPython" })).toEqual({
      label: "CPython",
      value: "3.11.8",
    });
  });

  it("package count prefers the record, and 0 is a real empty list", () => {
    expect(envPackageCount({ packages: [{ name: "a" }, { name: "b" }] })).toBe(2);
    expect(envPackageCount({ package_count: 0, packages: [] })).toBe(0);
    expect(envPackageCount({ package_count: 4, packages: [] })).toBe(4);
  });
});


it("loadLineage requests the pinned version and propagates its read failure without latest", async () => {
  const requests: string[] = [];
  setExecutionFetch(async (url) => {
    requests.push(url);
    return new Response(JSON.stringify({ error: "version unavailable" }), { status: 404 });
  });
  try {
    await expect(loadLineage({ id: "a", version_id: "v1", _exactVersion: true })).rejects.toThrow("version unavailable");
    expect(requests).toEqual(["/api/v1/artifacts/a/lineage?version=v1"]);
  } finally { setExecutionFetch(null); }
});


class ProvenanceNode {
  children: ProvenanceNode[] = [];
  className = "";
  textContent = "";
  set innerHTML(_value: string) { this.children = []; }
  setAttribute() {}
  appendChild(child: ProvenanceNode) { this.children.push(child); return child; }
}

it("a delayed latest environment response cannot poison the pinned version cache", async () => {
  resetStoreFields();
  vi.stubGlobal("document", { createElement: () => new ProvenanceNode() });
  const latest = { id: "a", version_id: "v1" };
  dockArtifact.value = latest; _artVer.value.a = "v1";
  provMode.value = true; provSub.value = "environment";
  let release!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const requests: string[] = [];
  let serverHead = "v1";
  setExecutionFetch(async (url) => {
    requests.push(url);
    await delayed;
    const selected = new URL(url, "http://localhost").searchParams.get("version") || serverHead;
    return new Response(JSON.stringify({ source: "captured", kind: "python", environment_name: selected, packages: [] }));
  });
  try {
    renderProvenanceInto(new ProvenanceNode() as unknown as HTMLElement, latest);
    serverHead = "v2";
    syncArtifactVersion({ id: "a", version_id: "v2" }, true);
    release();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(_envSnapById.value["a:v1"]).toBeUndefined();
    expect(_envSnapById.value["a:v2"]).toBeUndefined();
    const pinned = { id: "a", version_id: "v1", _exactVersion: true };
    dockArtifact.value = pinned;
    renderProvenanceInto(new ProvenanceNode() as unknown as HTMLElement, pinned);
    await vi.waitFor(() => expect(_envSnapById.value["a:v1"]).toMatchObject({ environment_name: "v1" }));
    expect(requests).toEqual(["/api/v1/artifacts/a/environment?version=v1", "/api/v1/artifacts/a/environment?version=v1"]);
  } finally { setExecutionFetch(null); vi.unstubAllGlobals(); resetStoreFields(); }
});

it("latest lineage reads capture their known version rather than a moving head", async () => {
  const requests: string[] = [];
  setExecutionFetch(async (url) => {
    requests.push(url);
    return new Response(JSON.stringify({ version_id: new URL(url, "http://localhost").searchParams.get("version") || "v2", interactions: [], dependency_mappings: { inputs: [] } }));
  });
  try {
    const report = await loadLineage({ id: "a", version_id: "v1" });
    expect(report.version_id).toBe("v1");
    expect(requests).toEqual(["/api/v1/artifacts/a/lineage?version=v1"]);
  } finally { setExecutionFetch(null); }
});


it("known latest and unbound legacy metadata read failures both propagate", async () => {
  const requests: string[] = [];
  setExecutionFetch(async (url) => {
    requests.push(url);
    return new Response(JSON.stringify({ error: "missing snapshot" }), { status: 404 });
  });
  try {
    await expect(loadLineage({ id: "a", version_id: "v1" })).rejects.toThrow("missing snapshot");
    await expect(loadLineage({ id: "a" })).rejects.toThrow("missing snapshot");
    expect(requests).toEqual(["/api/v1/artifacts/a/lineage?version=v1", "/api/v1/artifacts/a/lineage"]);
  } finally { setExecutionFetch(null); }
});


it("an exact version without a recorded environment is an expected state, not a failed load", async () => {
  resetStoreFields();
  vi.stubGlobal("document", { createElement: () => new ProvenanceNode() });
  const pinned = { id: "a", version_id: "v1", _exactVersion: true };
  dockArtifact.value = pinned; provMode.value = true; provSub.value = "environment";
  setExecutionFetch(async () => new Response(
    JSON.stringify({ error: "no environment snapshot was recorded for this version", code: "environment_snapshot_unavailable", request_id: "req-9" }),
    { status: 404 },
  ));
  try {
    const view = new ProvenanceNode();
    renderProvenanceInto(view as unknown as HTMLElement, pinned);
    const walk = (node: ProvenanceNode): ProvenanceNode[] => [node, ...node.children.flatMap(walk)];
    await vi.waitFor(() => expect(walk(view).some((node) => node.textContent === filesT("prov.env.noSnapshot"))).toBe(true));
    expect(walk(view).some((node) => node.textContent.includes("req-9"))).toBe(false);
  } finally { setExecutionFetch(null); vi.unstubAllGlobals(); resetStoreFields(); }
});

it.each([
  null, [], {},
  { interactions: "bad", dependency_mappings: { inputs: [] } },
  { interactions: [null], dependency_mappings: { inputs: [] } },
  { interactions: [], dependency_mappings: { inputs: "bad" } },
  { interactions: [], dependency_mappings: { inputs: [null] } },
  { interactions: [], dependency_mappings: { inputs: [] }, capture_observations: {} },
  { interactions: [], dependency_mappings: { inputs: [] }, capture_observations: [null] },
  { interactions: [], dependency_mappings: { inputs: [] }, producer: [] },
])("rejects malformed successful lineage reads instead of normalizing them to empty: %j", async (body) => {
  setExecutionFetch(async () => new Response(JSON.stringify(body)));
  try { await expect(loadLineage({ id: "a" })).rejects.toThrow(); }
  finally { setExecutionFetch(null); }
});

it.each([404, 500, "network"])("an unbound legacy lineage propagates %s", async (fault) => {
  setExecutionFetch(async () => {
    if (fault === "network") throw new Error("network failed");
    return new Response(JSON.stringify({ error: "read failed" }), { status: Number(fault) });
  });
  try { await expect(loadLineage({ id: "a" })).rejects.toThrow(); }
  finally { setExecutionFetch(null); }
});

it("rejects an exact lineage response naming another version", async () => {
  setExecutionFetch(async () => new Response(JSON.stringify({ artifact_id: "a", version_id: "v2", interactions: [], dependency_mappings: { inputs: [] } })));
  try { await expect(loadLineage({ id: "a", version_id: "v1" })).rejects.toThrow(); }
  finally { setExecutionFetch(null); }
});

it("no recorded producing cell is a completed empty state, not reproduction generation", () => {
  resetStoreFields();
  vi.stubGlobal("document", { createElement: () => new ProvenanceNode() });
  const art = { id: "a", version_id: "v1", is_user_upload: true };
  dockArtifact.value = art; provMode.value = true; provSub.value = "code";
  // The public stores represent a successful empty server record here.
  lineage.value = { artifact_id: "a", version_id: "v1", interactions: [], dependency_mappings: { inputs: [] } };
  _lineageFor.value = "a:v1";
  try {
    const view = new ProvenanceNode();
    renderProvenanceInto(view as unknown as HTMLElement, art);
    const walk = (node: ProvenanceNode): string => [node.textContent, ...node.children.map(walk)].join(" ");
    expect(walk(view)).not.toContain("Generating reproduction code");
    expect(walk(view)).toMatch(/No .*record|未记录|没有.*记录/i);
  } finally { vi.unstubAllGlobals(); resetStoreFields(); }
});

/**
 * UPG5-02. A snapshot whose packages were never read -- a 0.2.x Python kernel
 * recorded by its `repl` mode, an interpreter the daemon could not read, a
 * remote worker -- carries `packages: []`, `package_count: 0` and a
 * `packages_unavailable` reason. The panel rendered that as "Packages 0" and
 * "No packages to report." right beside "the package list is unknown".
 */
const LEGACY_REPL_ENV = {
  kind: "python",
  environment_name: "base",
  package_count: 0,
  packages: [],
  packages_unavailable:
    "openai4s 0.2.x recorded this Python kernel by its 'repl' mode and did not read its packages; the package list is unknown",
  source: "captured",
  generation_confidence: "verified",
};

let environmentPanelSerial = 0;

async function renderEnvironmentPanel(env: Record<string, unknown>): Promise<{ chips: string[]; empty: string[]; notes: string[] }> {
  resetStoreFields();
  vi.stubGlobal("document", { createElement: () => new ProvenanceNode() });
  // A distinct artifact per call. `renderProvEnvironment` de-duplicates an
  // in-flight read by (cache key, request, frame), and `resetStoreFields`
  // returns `_lineageReq` to 0 -- so reusing one id made the second call await
  // the first call's promise and assert against the first env.
  const pinned = { id: `env-${++environmentPanelSerial}`, version_id: "v1", _exactVersion: true };
  dockArtifact.value = pinned; provMode.value = true; provSub.value = "environment";
  setExecutionFetch(async () => new Response(JSON.stringify(env)));
  try {
    const view = new ProvenanceNode();
    renderProvenanceInto(view as unknown as HTMLElement, pinned);
    const walk = (node: ProvenanceNode): ProvenanceNode[] => [node, ...node.children.flatMap(walk)];
    await vi.waitFor(() => expect(walk(view).some((node) => node.className === "env-chips")).toBe(true));
    const nodes = walk(view);
    return {
      chips: nodes.filter((n) => n.className === "env-chip").map((n) => n.children.map((c) => c.textContent).join(" ")),
      empty: nodes.filter((n) => n.className === "dock-empty").map((n) => n.textContent),
      notes: nodes.filter((n) => n.className.startsWith("env-src")).map((n) => n.textContent),
    };
  } finally { setExecutionFetch(null); vi.unstubAllGlobals(); resetStoreFields(); }
}

describe("a package list that was never read is unknown, not empty", () => {
  it("envPackageCount has no count to give when the record says the list is unknown", () => {
    expect(envPackageCount(LEGACY_REPL_ENV)).toBeNull();
    expect(envPackageCount({ package_count: 0, packages: [], packages_unavailable: "could not read distributions from '/x/python'" })).toBeNull();
  });

  it("the legacy 0.2.x panel shows neither 'Packages 0' nor 'No packages to report.'", async () => {
    const out = await renderEnvironmentPanel(LEGACY_REPL_ENV);
    expect(out.chips).not.toContain("Packages 0");
    expect(out.chips).toContain("Packages " + filesT("prov.env.packagesUnknown"));
    expect(out.empty).not.toContain(t("prov.env.noPackages"));
    // The reason is still on the panel.
    expect(out.notes.some((n) => n.includes("the package list is unknown"))).toBe(true);
  });

  it("a Python snapshot whose list really is empty still says so", async () => {
    const out = await renderEnvironmentPanel({ kind: "python", package_count: 0, packages: [], source: "captured" });
    expect(out.chips).toContain("Packages 0");
    expect(out.empty).toContain(t("prov.env.noPackages"));
  });

  it("an R kernel's Python package count does not apply rather than being zero", async () => {
    const out = await renderEnvironmentPanel({
      kind: "r",
      package_count: 0,
      packages: [],
      packages_unavailable: "r kernel: Python distribution metadata does not apply",
      source: "captured",
    });
    expect(out.chips).not.toContain("Packages 0");
    expect(out.chips).toContain("Packages " + filesT("prov.env.packagesNotApplicable"));
    expect(out.empty).not.toContain(t("prov.env.noPackages"));
    expect(out.notes.some((n) => n.includes("does not apply"))).toBe(true);
  });
});
