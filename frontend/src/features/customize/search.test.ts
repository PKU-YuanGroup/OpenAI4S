import { describe, expect, it } from "vitest";
import {
  canRemoveKey,
  canSwitchOff,
  engineConfigBody,
  keyRemovalNote,
  moved,
  readSearchConfig,
  searchT,
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

it("retains the optional Parallel engine in configuration edits", () => {
  const config = readSearchConfig({
    engines: [
      { id: "parallel", enabled: true, usable: true, needs_key: false, accepts_key: false },
      { id: "builtin", enabled: true, usable: true },
    ],
  });
  expect(config.engines.map((engine) => engine.id)).toEqual(["parallel", "builtin"]);
  expect(engineConfigBody(config, { toggle: "parallel" }).enabled.parallel).toBe(false);
});

describe("removing a saved key", () => {
  const keyed = (tavily: Record<string, unknown>, builtinOn: boolean) =>
    readSearchConfig({
      engines: [
        { id: "tavily", needs_key: true, accepts_key: true, api_key_configured: true, ...tavily },
        { id: "you", enabled: true, accepts_key: true, api_key_configured: true, usable: true },
        { id: "builtin", enabled: builtinOn, usable: builtinOn },
      ],
    });

  it("is refused, like its switch, while it keeps the last usable engine working", () => {
    const onlyTavily = readSearchConfig({
      engines: [
        { id: "tavily", enabled: true, needs_key: true, accepts_key: true, api_key_configured: true, usable: true },
        { id: "you", enabled: false, accepts_key: true, usable: false },
        { id: "builtin", enabled: false, usable: false },
      ],
    });
    expect(canRemoveKey(onlyTavily, "tavily")).toBe(false);
    expect(canRemoveKey(keyed({ enabled: true, usable: true }, true), "tavily")).toBe(true);
    // Switched off, Tavily answers nothing either way.
    expect(canRemoveKey(keyed({ enabled: false, usable: false }, false), "tavily")).toBe(true);
  });

  it("is allowed where the engine still answers without it, and needs a saved key", () => {
    const onlyYou = readSearchConfig({
      engines: [
        { id: "tavily", enabled: false, needs_key: true, accepts_key: true, usable: false },
        { id: "you", enabled: true, accepts_key: true, api_key_configured: true, usable: true },
        { id: "builtin", enabled: false, usable: false },
      ],
    });
    // You.com falls back to its keyless tier.
    expect(canRemoveKey(onlyYou, "you")).toBe(true);
    expect(canRemoveKey(onlyYou, "tavily")).toBe(false);
  });

  it("allows removing an environment-owned key even when it is the last usable engine", () => {
    const fromEnv = readSearchConfig({
      engines: [
        {
          id: "tavily",
          enabled: true,
          needs_key: true,
          accepts_key: true,
          api_key_configured: true,
          api_key_from_env: true,
          usable: true,
        },
        { id: "you", enabled: false, usable: false },
        { id: "builtin", enabled: false, usable: false },
      ],
    });
    expect(fromEnv.engines[0].keyFromEnv).toBe(true);
    expect(canRemoveKey(fromEnv, "tavily")).toBe(true);
  });

  it("says when the server's environment still supplies the key", () => {
    const gone = keyed({ enabled: false, usable: false, api_key_configured: false }, true);
    expect(keyRemovalNote(gone, "tavily")).toEqual({ text: searchT("search.keyRemoved"), warn: false });
    const kept = keyed({ enabled: false, usable: false }, true);
    expect(keyRemovalNote(kept, "tavily")).toEqual({ text: searchT("search.keyFromEnv"), warn: true });
  });
});
