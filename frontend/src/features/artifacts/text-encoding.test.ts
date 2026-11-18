import { describe, expect, it } from "vitest";
import { decodeArtifactBytes, encodingLabel, plausiblyChinese } from "./text-encoding";

const bytes = (...values: number[]) => new Uint8Array(values);
const utf8 = (text: string) => new TextEncoder().encode(text);

describe("Artifact text encoding (UP-4)", () => {
  it("keeps UTF-8 (and strips its BOM) exactly as before", () => {
    expect(decodeArtifactBytes(utf8("a,b\n1,2\n"))).toEqual({ text: "a,b\n1,2\n", encoding: "utf-8" });
    expect(decodeArtifactBytes(new Uint8Array([0xef, 0xbb, 0xbf, ...utf8("患者")]))).toEqual({
      text: "患者",
      encoding: "utf-8",
    });
  });

  it("detects GB18030/GBK Chinese text", () => {
    // "温度℃,样本\n" in GBK: 温 CE C2, 度 B6 C8, ℃ A1 E6, 样 D1 F9, 本 B1 BE.
    const gbk = bytes(0xce, 0xc2, 0xb6, 0xc8, 0xa1, 0xe6, 0x2c, 0xd1, 0xf9, 0xb1, 0xbe, 0x0a);
    expect(decodeArtifactBytes(gbk)).toEqual({ text: "温度℃,样本\n", encoding: "gb18030" });
  });

  it("decodes UTF-16 with a BOM (Excel's Unicode text)", () => {
    expect(decodeArtifactBytes(bytes(0xff, 0xfe, 0x61, 0x00, 0x09, 0x00, 0x2d, 0x4e))).toEqual({
      text: "a\t中",
      encoding: "utf-16le",
    });
    expect(decodeArtifactBytes(bytes(0xfe, 0xff, 0x00, 0x61))).toEqual({ text: "a", encoding: "utf-16be" });
  });

  it("does not call binary or other code pages Chinese", () => {
    const binary = bytes(0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0xff, 0x80);
    expect(decodeArtifactBytes(binary).encoding).toBe("utf-8");
    // "café crème" in Windows-1252: é followed by ASCII is not valid GB18030.
    const latin = bytes(0x63, 0x61, 0x66, 0xe9, 0x20, 0x63, 0x72, 0xe8, 0x6d, 0x65);
    expect(decodeArtifactBytes(latin).encoding).toBe("utf-8");
    expect(plausiblyChinese("plain ascii")).toBe(false);
    expect(plausiblyChinese("°±")).toBe(false);
    expect(plausiblyChinese("温度 25°C")).toBe(true);
    expect(plausiblyChinese("中")).toBe(false);
  });

  it("names encodings for people", () => {
    expect(encodingLabel("gb18030")).toBe("GB18030");
    expect(encodingLabel("utf-16le")).toBe("UTF-16 LE");
  });
});
