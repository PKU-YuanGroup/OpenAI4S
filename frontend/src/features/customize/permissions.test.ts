import { describe, expect, it } from "vitest";
import { globalRulesSummary, toolDescription, type PermRule } from "./permissions";

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

describe("toolDescription", () => {
  it("knows the seeded tools and leaves an unknown name alone", () => {
    for (const r of seeded) expect(toolDescription(r.tool)).toBeTruthy();
    expect(toolDescription("grep")).toBe("Search inside files for text");
    expect(toolDescription("some_plugin_tool")).toBeNull();
  });
});
