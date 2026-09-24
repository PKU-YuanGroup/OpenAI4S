import { describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n/runtime";
import {
  LIVE_OUTPUT_CHAR_CAP,
  LIVE_OUTPUT_TRUNCATION,
  appendLiveOutput,
} from "../stream/cap";
import {
  bindStreamingPre,
  countNewlines,
  liveOutputDelta,
  toolMetaLabel,
} from "./delta";

describe("liveOutputDelta / truncation", () => {
  it("concatenates under the cap and reports the increment", () => {
    const { next, added, addedNewlines } = liveOutputDelta("ab\n", "cd\n");
    expect(next).toBe("ab\ncd\n");
    expect(added).toBe("cd\n");
    expect(addedNewlines).toBe(1);
    expect(appendLiveOutput("ab\n", "cd\n")).toBe(next);
  });

  it("truncates at 1MB; further appends are no-ops (added is empty)", () => {
    const big = "x".repeat(LIVE_OUTPUT_CHAR_CAP);
    const once = liveOutputDelta("", big + "more");
    expect(once.next.length).toBe(
      LIVE_OUTPUT_CHAR_CAP + LIVE_OUTPUT_TRUNCATION.length,
    );
    expect(once.next.endsWith(LIVE_OUTPUT_TRUNCATION)).toBe(true);
    expect(once.next.slice(0, LIVE_OUTPUT_CHAR_CAP)).toBe(big);
    expect(once.added.endsWith(LIVE_OUTPUT_TRUNCATION)).toBe(true);

    const twice = liveOutputDelta(once.next, "again");
    expect(twice.next).toBe(once.next);
    expect(twice.added).toBe("");
    expect(twice.addedNewlines).toBe(0);

    const fromCap = liveOutputDelta(big, "y");
    expect(fromCap.next.endsWith(LIVE_OUTPUT_TRUNCATION)).toBe(true);
    const after = liveOutputDelta(fromCap.next, "z");
    expect(after.next).toBe(fromCap.next);
    expect(after.added).toBe("");
  });

  it("counts newlines only in the increment, not by rescanning the whole buffer", () => {
    const handleText = { data: "", appendData(s: string) { this.data += s; } };
    const handle = bindStreamingPre(handleText, "");
    handle.append("one\n");
    expect(handle.newlines).toBe(1);
    expect(countNewlines(handle.text)).toBe(1);
    handle.append("two\nthree\n");
    expect(handle.newlines).toBe(3);
    expect(handle.text).toBe(handleText.data);
    expect(handle.newlines).toBe(countNewlines(handleText.data));
  });

  it("appendData only receives the delta, never a rewrite of the prefix", () => {
    const pushes: string[] = [];
    const node = { appendData(s: string) { pushes.push(s); } };
    const handle = bindStreamingPre(node, "head\n");
    handle.append("tail\n");
    expect(pushes).toEqual(["tail\n"]);
    expect(handle.text).toBe("head\ntail\n");
    handle.append("");
    expect(pushes).toEqual(["tail\n"]);
  });

  it("examines only the chunk: no search or slice of the output it already holds", () => {
    const node = { data: "", appendData(s: string) { this.data += s; } };
    const handle = bindStreamingPre(node, "");
    const includes = vi.spyOn(String.prototype, "includes");
    const slice = vi.spyOn(String.prototype, "slice");
    try {
      for (let i = 0; i < 500; i++) handle.append("row " + i + "\n");
      expect(includes).not.toHaveBeenCalled();
      expect(slice).not.toHaveBeenCalled();
    } finally {
      includes.mockRestore();
      slice.mockRestore();
    }
    expect(handle.newlines).toBe(500);
    expect(handle.truncated).toBe(false);
    expect(node.data).toBe(handle.text);
  });

  it("does not appendData once truncated", () => {
    const pushes: string[] = [];
    const node = { appendData(s: string) { pushes.push(s); } };
    const handle = bindStreamingPre(node, "");
    handle.append("x".repeat(LIVE_OUTPUT_CHAR_CAP + 10));
    expect(handle.truncated).toBe(true);
    expect(pushes.length).toBe(1);
    handle.append("more");
    expect(pushes.length).toBe(1);
    expect(handle.text).toBe(pushes[0]);
  });

  it("toolMetaLabel keeps the original n>1 / done wording, in the UI language", async () => {
    await setLang("en");
    expect(toolMetaLabel(0)).toBe("done");
    expect(toolMetaLabel(1)).toBe("done");
    expect(toolMetaLabel(2)).toBe("2 lines");
    expect(toolMetaLabel(5)).toBe("5 lines");
    await setLang("zh");
    expect(toolMetaLabel(1)).toBe("完成");
    expect(toolMetaLabel(5)).toBe("5 行");
    await setLang("en");
  });
});
