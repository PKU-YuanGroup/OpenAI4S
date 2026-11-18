import { describe, expect, it } from "vitest";
import { searchSkills, skillNetworkDomains } from "./SkillsTab";

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
});
