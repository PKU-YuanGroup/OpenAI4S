import { describe, expect, it } from "vitest";
import {
  canSwitchOff,
  engineConfigBody,
  moved,
  readSearchConfig,
} from "./search";

const raw = {
  doubao_configured: true,
  order: ["tavily", "you", "builtin"],
  engines: [
    { id: "tavily", enabled: true, needs_key: true, accepts_key: true, api_key_configured: false, usable: false },
    { id: "you", enabled: false, accepts_key: true, usable: false },
    { id: "builtin", enabled: true, usable: true },
    { id: "builtin", enabled: false, usable: false },
    { id: "bing", enabled: true, usable: true },
  ],
};

describe("search engine list", () => {
  it("parses the projection and drops unknown or repeated engines", () => {
    const config = readSearchConfig(raw);
    expect(config.doubaoConfigured).toBe(true);
    expect(config.engines.map((e) => e.id)).toEqual(["tavily", "you", "builtin"]);
    expect(config.engines[0]).toMatchObject({ needsKey: true, keyConfigured: false, usable: false });
    expect(readSearchConfig(null)).toEqual({ doubaoConfigured: false, engines: [] });
  });

  it("refuses to switch off the last usable engine", () => {
    const config = readSearchConfig(raw);
    expect(canSwitchOff(config, "builtin")).toBe(false);
    // Keyless Tavily is not usable, so switching it off never strands search.
    expect(canSwitchOff(config, "tavily")).toBe(true);
  });

  it("moves an engine within the bounds of the list", () => {
    expect(moved(["tavily", "you", "builtin"], "builtin", -1)).toEqual(["tavily", "builtin", "you"]);
    expect(moved(["tavily", "you", "builtin"], "tavily", -1)).toEqual(["tavily", "you", "builtin"]);
    expect(moved(["tavily", "you", "builtin"], "tavily", 5)).toEqual(["you", "builtin", "tavily"]);
  });

  it("builds the save body from the current state", () => {
    const config = readSearchConfig(raw);
    expect(engineConfigBody(config, { toggle: "you" })).toEqual({
      order: ["tavily", "you", "builtin"],
      enabled: { tavily: true, you: true, builtin: true },
    });
    expect(engineConfigBody(config, { order: ["builtin", "tavily", "you"] }).order).toEqual([
      "builtin",
      "tavily",
      "you",
    ]);
  });
});
