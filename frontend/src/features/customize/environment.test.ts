import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { skillReadinessNote } from "./environment";

// SCI-08: the Skills tab said "Not available on this machine: gpu" and
// "Cannot be verified on this machine: science" beside a switch that was on.

const row = (readiness: Record<string, unknown>, requirements: string[] = []) => ({
  name: "a-skill",
  requirements,
  readiness,
});

afterEach(async () => {
  await setLang("en");
});

describe("a Skill's readiness in words", () => {
  it("is silent for a ready Skill", async () => {
    await setLang("en");
    expect(skillReadinessNote(row({ state: "ready", missing: [], unverifiable: [], blocked_on: [] }))).toBeNull();
    expect(skillReadinessNote({ name: "no-readiness" })).toBeNull();
  });

  it("a missing GPU says what the agent will do and where to fix it", async () => {
    await setLang("en");
    const note = skillReadinessNote(
      row({ state: "needs_setup", missing: ["gpu"], unverifiable: [], blocked_on: [] }, ["gpu"]),
    )!;
    expect(note.text).toBe(
      "Needs an NVIDIA GPU, and this machine has none. With the switch on the agent can still load it, but its GPU steps fail here. Add a remote GPU in Compute to run them there.",
    );
    expect(note.compute).toBe(true);
    expect(note.text).not.toMatch(/: gpu$/);
  });

  it("a load the version refuses says so first, and promises nothing about the switch", async () => {
    await setLang("en");
    const note = skillReadinessNote(
      row({ state: "needs_setup", missing: ["gpu"], unverifiable: [], blocked_on: ["raw_network"] }, ["gpu"]),
    )!;
    expect(note.text.startsWith("The agent cannot load this Skill in this version")).toBe(true);
    expect(note.text).toContain("Needs an NVIDIA GPU");
    expect(note.text).not.toContain("can still load");
    expect(note.compute).toBe(false);
  });

  it("the science extra is named as packages, with the two ways to get them", async () => {
    await setLang("en");
    const note = skillReadinessNote(
      row({ state: "unknown", missing: [], unverifiable: ["science"], blocked_on: [] }, ["science"]),
    )!;
    expect(note.text).toContain("scientific Python packages");
    expect(note.text).toContain("install it in Compute or run `openai4s setup`");
    expect(note.text).not.toContain("verified on this machine: science");
    expect(note.compute).toBe(true);
  });

  it("sandbox blocks are one sentence naming the setting", async () => {
    await setLang("en");
    const note = skillReadinessNote(
      row({
        state: "needs_setup",
        missing: [],
        unverifiable: [],
        blocked_on: ["sandbox_not_enforced", "sandbox_unproven", "raw_network_allowed"],
      }),
    )!;
    expect(note.text.match(/kernel sandbox/g)).toHaveLength(1);
    expect(note.text).toContain("OPENAI4S_KERNEL_SANDBOX");
    expect(note.text).toContain("OPENAI4S_KERNEL_ALLOW_RAW_NETWORK");
  });

  it("an unknown requirement is still named, and an older row falls back to its requirements", async () => {
    await setLang("en");
    expect(skillReadinessNote(row({ state: "unknown", unverifiable: ["cuda"] }))!.text).toBe(
      "Needs cuda; this machine cannot check whether it is there.",
    );
    expect(skillReadinessNote(row({ state: "needs_setup" }, ["gpu"]))!.text).toContain("Needs an NVIDIA GPU");
  });

  it("speaks Chinese in the Chinese UI", async () => {
    await setLang("zh");
    const note = skillReadinessNote(
      row({ state: "needs_setup", missing: ["gpu"], unverifiable: [], blocked_on: [] }, ["gpu"]),
    )!;
    expect(note.text).toContain("需要 NVIDIA GPU，本机没有。");
    expect(note.text).toContain("「计算」");
    // Chinese sentences run on: no space after 。
    expect(note.text).not.toContain("。 ");
  });
});
