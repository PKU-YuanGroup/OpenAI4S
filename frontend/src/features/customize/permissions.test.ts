import { describe, expect, it } from "vitest";
import {
  categoryLabel,
  decisionLabel,
  globalRulesSummary,
  groupDecision,
  groupRules,
  installRule,
  ruleCategory,
  ruleCount,
  toolDescription,
  type PermRule,
} from "./permissions";

const rule = (tool: string, pattern: string, decision: string): PermRule => ({
  rule_id: tool + pattern,
  tool,
  pattern,
  decision,
});

const each = (tools: string[], decision: string) => tools.map((tool) => rule(tool, "*", decision));
const WRITES = ["write_file", "edit_file", "save_artifact", "project_import_file"];

// The rules a fresh install seeds, as far as the summary's groups read them.
const seeded: PermRule[] = [
  rule("read_file", "*.env", "deny"),
  ...each(["read_file", "glob", "grep", "list_dir"], "allow"),
  ...each(["project_list_dir", "project_read_file", "project_glob", "project_grep"], "allow"),
  ...each(WRITES, "allow"),
  ...each(["web_fetch", "web_search", "science_search"], "allow"),
  ...each(["env_setup", "exec_background"], "ask"),
  ...each(["mcp_call", "mcp_resource_read", "mcp_prompt_get"], "ask"),
  ...each(["skills_edit", "skills_delete", "skills_publish", "credentials_set"], "ask"),
];

describe("globalRulesSummary", () => {
  it("describes the seeded rules as they are", () => {
    expect(globalRulesSummary(seeded)).toBe(
      "Under the current global rules: reading files, writing files, network access run without asking; " +
        "package installs, background commands, connectors, skills and credentials need your approval; " +
        "refused by a specific rule: read_file *.env.",
    );
  });

  it("follows a changed rule", () => {
    const one = seeded.map((r) => (r.tool === "write_file" ? { ...r, decision: "ask" } : r));
    expect(globalRulesSummary(one)).toContain("writing files partly need approval");
    const all = seeded.map((r) => (WRITES.includes(r.tool) ? { ...r, decision: "ask" } : r));
    expect(globalRulesSummary(all)).toContain("writing files, package installs,");
  });

  it("reads a tool with no catch-all rule as asking, which is what the resolver does", () => {
    expect(globalRulesSummary([rule("env_setup", "numpy", "allow")])).toContain(
      "package installs, background commands, connectors, skills and credentials need your approval",
    );
    expect(globalRulesSummary([])).toBe("There are no global rules: every tool asks first.");
  });
});

describe("package installs (PERM-1)", () => {
  it("never reads an install allow as running without asking: no rule answers for one", () => {
    const legacy = seeded.map((r) => (r.tool === "env_setup" ? { ...r, decision: "allow" } : r));
    expect(globalRulesSummary(legacy)).toMatch(/package installs[^;]* need your approval/);
    expect(globalRulesSummary([...seeded, rule("*", "*", "allow")])).toMatch(
      /package installs[^;]* need your approval/,
    );
    const denied = seeded.map((r) => (r.tool === "env_setup" ? { ...r, decision: "deny" } : r));
    expect(globalRulesSummary(denied)).toContain("package installs are refused");
  });

  it("recognises an install rule the way the server does", () => {
    for (const [tool, pattern] of [
      ["env_setup", "*"],
      ["env_setup", "numpy"],
      ["bash", "pip install *"],
      ["bash", "python3 -m pip install x"],
      ["bash", "conda env create -f env.yml"],
      ["bash", "Rscript -e 'install.packages(\"x\")'"],
    ] as const) {
      expect(installRule(tool, pattern), `${tool} ${pattern}`).toBe(true);
    }
    for (const [tool, pattern] of [
      ["bash", "*"],
      ["bash", "pip show *"],
      ["bash", "git add *"],
      ["web_fetch", "pip install"],
    ] as const) {
      expect(installRule(tool, pattern), `${tool} ${pattern}`).toBe(false);
    }
  });
});

describe("rule categories (PERM-3)", () => {
  it("puts every seeded tool in a named category, in a fixed order", () => {
    const groups = groupRules(seeded);
    expect(groups.map((group) => group.category)).toEqual([
      "read",
      "write",
      "network",
      "install",
      "command",
      "connector",
      "skills",
      "credentials",
    ]);
    expect(groups.find((group) => group.category === "read")!.rules.map((r) => r.tool)).toContain("project_grep");
    expect(ruleCategory("bash")).toBe("command");
    expect(ruleCategory("delegate")).toBe("delegate");
    expect(ruleCategory("env_use")).toBe("install");
    expect(ruleCategory("some_plugin_tool")).toBe("other");
    expect(ruleCategory("*")).toBe("other");
  });

  it("says what a category's rules amount to", () => {
    const byCategory = (category: string) => groupRules(seeded).find((group) => group.category === category)!.rules;
    // read_file *.env deny beside allows: mixed.
    expect(groupDecision(byCategory("read"))).toBe("mixed");
    expect(groupDecision(byCategory("write"))).toBe("allow");
    expect(groupDecision(byCategory("connector"))).toBe("ask");
    expect(groupDecision([rule("bash", "rm *", "deny")])).toBe("deny");
    // An install allow never answers (PERM-1), so it reads as asking.
    expect(groupDecision([rule("env_setup", "*", "allow")])).toBe("ask");
  });

  it("names each category and its standing in both languages", async () => {
    const { setLang } = await import("../../i18n/runtime");
    await setLang("zh");
    expect(categoryLabel("install")).toBe("装包与环境");
    expect(categoryLabel("delegate")).toBe("子智能体");
    expect(decisionLabel("allow")).toBe("全部允许");
    expect(decisionLabel("ask")).toBe("需要批准");
    expect(decisionLabel("mixed")).toBe("混合");
    expect(ruleCount(3)).toBe("3 条规则");
    await setLang("en");
    expect(categoryLabel("read")).toBe("Reading files");
    expect(decisionLabel("deny")).toBe("All refused");
    expect(ruleCount(1)).toBe("1 rule");
    expect(ruleCount(2)).toBe("2 rules");
  });
});

describe("toolDescription", () => {
  it("knows the seeded tools and leaves an unknown name alone", () => {
    for (const r of seeded) expect(toolDescription(r.tool)).toBeTruthy();
    expect(toolDescription("grep")).toBe("Search inside files for text");
    expect(toolDescription("some_plugin_tool")).toBeNull();
  });
});
