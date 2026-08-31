import { describe, expect, it } from "vitest";
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
  it("emptyLineage is the load-failure fallback, not a fabricated producer", () => {
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
