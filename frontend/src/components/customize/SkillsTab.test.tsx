import { describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { searchSkills, skillNetworkCopy, skillNetworkDomains } from "./SkillsTab";

// Customize → Skills lists 612 skills; 561 of them sit inside one collection.

const LIT = {
  name: "literature-review",
  description: "Find, verify, and synthesize scientific literature.",
  capabilities: {
    network: { mode: "host_only", domains: ["api.openalex.org", "eutils.ncbi.nlm.nih.gov"] },
  },
};
const FIGURE = {
  name: "figure-style",
  description: "Publication-grade figure rules.",
  capabilities: { network: { mode: "none", domains: [] } },
};
const MEMBER = {
  name: "bio-clinical-bayesian-trials",
  description: "Bayesian trials with meta-analytic-predictive priors.",
  collection: "bioskills",
  capabilities: { network: { mode: "unknown", domains: [] } },
};
const CATALOG = [FIGURE, LIT, MEMBER];

const names = (query: string) => searchSkills(CATALOG, query).map((s) => s.name);

describe("searching the skills tab", () => {
  it("finds a skill by name, description word, Chinese keyword or a domain it reaches", () => {
    expect(names("literature")).toEqual(["literature-review"]);
    expect(names("synthesize")).toEqual(["literature-review"]);
    expect(names("文献")[0]).toBe("literature-review");
    expect(names("eutils")).toEqual(["literature-review"]);
  });

  it("reaches members of a collection, which the tab otherwise folds away", () => {
    expect(names("bayesian")).toEqual(["bio-clinical-bayesian-trials"]);
  });

  it("finds nothing for a miss", () => {
    expect(names("zzzz-no-such-skill")).toEqual([]);
  });
});

describe("a skill's network limit", () => {
  it("is shown only for a host_only skill, with its domains", () => {
    expect(skillNetworkDomains(LIT)).toEqual(["api.openalex.org", "eutils.ncbi.nlm.nih.gov"]);
    expect(skillNetworkDomains(FIGURE)).toBeNull();
    expect(skillNetworkDomains(MEMBER)).toBeNull();
    expect(skillNetworkDomains({ name: "bare" })).toBeNull();
  });

  // SCI-07: "Limits network · 0 domains" opened onto a sentence ending in a
  // colon, and one domain read "1 domains".
  it("says that no declared domain turns the session's network off, and how to get it back", async () => {
    await setLang("en");
    const off = skillNetworkCopy([]);
    expect(off.summary).toBe("Turns network off");
    expect(off.detail).toContain("turns network access off for the rest of this session");
    expect(off.detail).toContain("Start a new session");
    expect(off.detail).not.toMatch(/:\s*$/);

    await setLang("zh");
    const offZh = skillNetworkCopy([]);
    expect(offZh.summary).toBe("加载后关闭联网");
    expect(offZh.detail).toContain("请新开一个会话");
  });

  it("names the domains it keeps, with the count in the right number", async () => {
    await setLang("en");
    expect(skillNetworkCopy(["integrate.api.nvidia.com"]).summary).toBe("Limits network · 1 domain");
    const two = skillNetworkCopy(["api.openalex.org", "doi.org"]);
    expect(two.summary).toBe("Limits network · 2 domains");
    expect(two.detail).toContain(
      "Loading this Skill limits network access for the rest of this session to: api.openalex.org, doi.org.",
    );

    await setLang("zh");
    expect(skillNetworkCopy(["api.openalex.org", "doi.org"]).summary).toBe("限定联网 · 2 个域名");
    await setLang("en");
  });
});
