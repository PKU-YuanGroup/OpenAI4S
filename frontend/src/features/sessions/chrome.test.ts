import { describe, expect, it } from "vitest";
import { errorPrefix } from "./chrome";

describe("hint error prefix", () => {
  it("uses the zh/en literals without a new i18n key", () => {
    expect(errorPrefix("zh")).toBe("错误：");
    expect(errorPrefix("en")).toBe("Error: ");
  });
});
