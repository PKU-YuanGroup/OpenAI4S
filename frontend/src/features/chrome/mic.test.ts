import { afterEach, describe, expect, it } from "vitest";
import { setLang } from "../../i18n/runtime";
import { micErrorText } from "./mic";

describe("M-80: voice input errors read as what to do", () => {
  afterEach(async () => {
    await setLang("zh");
  });

  it("explains a blocked microphone instead of printing `not-allowed`", async () => {
    await setLang("en");
    const said = micErrorText("not-allowed") || "";
    expect(said).not.toContain("not-allowed");
    expect(said).toMatch(/microphone/i);
    expect(said).toMatch(/site settings/i);
    await setLang("zh");
    expect(micErrorText("not-allowed")).toContain("麦克风");
  });

  it("names the common failures in the UI language", async () => {
    await setLang("en");
    for (const code of ["no-speech", "audio-capture", "network", "service-not-allowed", "language-not-supported"]) {
      const said = micErrorText(code) || "";
      expect(said, code).not.toContain(code);
      expect(said.length, code).toBeGreaterThan(20);
    }
  });

  it("stays quiet for the user's own stop", () => {
    expect(micErrorText("aborted")).toBeNull();
  });
});
