import { describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { searchSkills, skillDetailFacts, skillNetworkCopy, skillNetworkDomains } from "./SkillsTab";

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

// SCI-06: a row showed ~200 characters of the description and nothing opened.
describe("a skill's expanded row", () => {
  const ALPHAFOLD = {
    name: "alphafold2",
    description: "Predict protein structure with AlphaFold2. Reach for this sk...",
    requirements: ["gpu"],
    capabilities: {
      network: {
        mode: "raw_required",
        domains: [],
        ui: { label_en: "raw network required and currently blocked", label_zh: "需要 raw 网络且当前被阻止" },
      },
    },
  };

  it("shows the whole description once the document has answered, the catalog line until then", async () => {
    await setLang("en");
    expect(skillDetailFacts(ALPHAFOLD, null).description).toBe(ALPHAFOLD.description);
    const whole = "Predict protein structure with AlphaFold2. Reach for this skill when: (1) a monomer …";
    expect(
      skillDetailFacts(ALPHAFOLD, { description: ALPHAFOLD.description, full_description: whole, body: "# AF2" })
        .description,
    ).toBe(whole);
  });

  it("says what the skill needs and its network mode in words", async () => {
    await setLang("en");
    const facts = skillDetailFacts(ALPHAFOLD, null);
    expect(facts.needs).toBe("Needs: an NVIDIA GPU");
    expect(facts.network).toBe("Network: raw network required and currently blocked");
    expect(skillDetailFacts(FIGURE, null).needs).toBe("Needs: nothing declared beyond the kernel");
    expect(skillDetailFacts(FIGURE, null).network).toBeNull();

    await setLang("zh");
    expect(skillDetailFacts(ALPHAFOLD, null).needs).toBe("需要：NVIDIA GPU");
    expect(skillDetailFacts(ALPHAFOLD, null).network).toBe("联网：需要 raw 网络且当前被阻止");
    await setLang("en");
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
