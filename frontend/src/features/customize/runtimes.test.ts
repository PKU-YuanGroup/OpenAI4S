import { describe, expect, it } from "vitest";
import {
  installOutcome,
  readRuntimes,
  rDefaultLabel,
  rPackagesNote,
  runtimeSummary,
  type RuntimeRow,
} from "./runtimes";

const python: RuntimeRow = { available: true, environment: "base", kind: "builtin", version: "3.13.1" };
const r: RuntimeRow = {
  available: true,
  selected: false,
  environment: "lab_env",
  kind: "local",
  version: "4.4.1",
  checked: true,
  packages: [
    { name: "ggplot2", available: false },
    { name: "dplyr", available: true },
    { name: "jsonlite", available: true },
  ],
};
const noR: RuntimeRow = { available: false, environment: "", kind: "", version: "", checked: false, packages: [] };

describe("runtime rows", () => {
  it("names the environment, where it comes from and the version, never a path", () => {
    expect(runtimeSummary("python", python)).toBe("base · built-in · Python 3.13.1");
    expect(runtimeSummary("r", r)).toBe("lab_env · local · R 4.4.1");
    // An R on PATH belongs to no environment.
    expect(runtimeSummary("r", { ...r, environment: "" })).toBe(
      "R installed on this computer · local · R 4.4.1",
    );
    expect(runtimeSummary("r", noR)).toContain("No R was found. Run `openai4s setup`");
  });

  it("says which key R packages load and where a missing one comes from", () => {
    const note = rPackagesNote(r);
    expect(note).toContain("ggplot2 is not available");
    expect(note).toContain("dplyr loads");
    expect(note).toContain("`openai4s setup`");
    const complete = { ...r, packages: r.packages!.map((p) => ({ ...p, available: true })) };
    expect(rPackagesNote(complete)).toBe("Key packages: ggplot2 loads · dplyr loads · jsonlite loads.");
    expect(rPackagesNote({ ...r, checked: false })).toBe("Could not check which R packages load.");
    expect(rPackagesNote(noR)).toBe("");
  });

  it("says what the R default resolves to", () => {
    expect(rDefaultLabel(r)).toBe("default → lab_env · local · R 4.4.1");
    expect(rDefaultLabel(noR)).toBe("default → no R found");
    expect(rDefaultLabel(null)).toBe("default");
  });

  it("reads `runtimes` from a listing and ignores one that has none", () => {
    expect(readRuntimes({ runtimes: { python, r } })).toEqual({
      python: { ...python, selected: false, checked: false, packages: [] },
      r,
    });
    expect(readRuntimes({ environments: [] })).toBeNull();
    expect(readRuntimes(null)).toBeNull();
  });
});

describe("installOutcome", () => {
  const context = { builtinEnv: "base", sessionOpen: false, sessionEnv: "", fallbackError: "see logs" };

  it("names the version installed and the environment it went into", () => {
    const outcome = installOutcome(
      {
        ok: true,
        installed: ["openpyxl", "pandas[excel]>=2"],
        installed_versions: [
          { name: "openpyxl", version: "3.1.5" },
          { name: "pandas", version: "2.2.3" },
        ],
      },
      context,
    );
    expect(outcome.ok).toBe(true);
    expect(outcome.lines[0]).toBe(
      "Installed into the built-in environment (base): openpyxl 3.1.5, pandas 2.2.3.",
    );
    expect(outcome.lines[1]).toContain("must be restarted to see them");
  });

  it("says whether the session's kernel was restarted", () => {
    const base = { ok: true, installed: ["openpyxl"], installed_versions: [{ name: "openpyxl", version: "3.1.5" }] };
    const open = { ...context, sessionOpen: true, sessionEnv: "base" };
    expect(installOutcome({ ...base, restarted: true }, open).lines[1]).toContain("kernel was restarted");
    expect(installOutcome({ ...base, restart_error: "x" }, open).lines[1]).toContain(
      "could not be restarted",
    );
    // A session running in another environment does not see the install.
    expect(installOutcome({ ...base, restarted: true }, { ...open, sessionEnv: "work" }).lines[1]).toBe(
      "This session runs in the work environment, which does not see packages installed here.",
    );
  });

  it("reports a failure with the installer's reason", () => {
    expect(installOutcome({ ok: false, failed: [{ error: "no matching distribution" }] }, context)).toEqual({
      ok: false,
      lines: ["Install failed: no matching distribution"],
    });
    expect(installOutcome({ ok: false }, context).lines).toEqual(["Install failed: see logs"]);
  });

  it("says so when no version came back", () => {
    expect(installOutcome({ ok: true, installed: ["./local.whl"] }, context).lines[0]).toBe(
      "Installed into the built-in environment (base): ./local.whl (version not reported).",
    );
  });
});
