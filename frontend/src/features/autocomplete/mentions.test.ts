import { beforeEach, describe, expect, it } from "vitest";
import {
  MAX_MENTIONS,
  blankMentionTokens,
  clearMentions,
  composerMentions,
  mentionTokenCount,
  mentionsPayload,
  rememberMention,
  restoreMentions,
  stripRefVersions,
  type ComposerMention,
} from "./mentions";

function pin(filename: string, version_id: string, artifact_id = "a-" + filename): ComposerMention {
  return { filename, version_id, artifact_id, root_frame_id: "" };
}

function versions(text: string): string[] {
  return composerMentions(text).map((m) => m.version_id);
}

beforeEach(() => {
  clearMentions();
});

describe("which `@name` tokens a text holds", () => {
  it("counts a token at the start, after whitespace, and before punctuation or CJK", () => {
    expect(mentionTokenCount("@a.csv", "a.csv")).toBe(1);
    expect(mentionTokenCount("see @a.csv and\n@a.csv", "a.csv")).toBe(2);
    expect(mentionTokenCount("look at @a.csv, then @a.csv.", "a.csv")).toBe(2);
    expect(mentionTokenCount("@a.csv这个文件多少行", "a.csv")).toBe(1);
    expect(mentionTokenCount("@a.csv，多少行", "a.csv")).toBe(1);
  });

  it("does not count a longer name, the text form, or an address", () => {
    expect(mentionTokenCount("@a.csv.bak", "a.csv")).toBe(0);
    expect(mentionTokenCount("@a.csv-old", "a.csv")).toBe(0);
    expect(mentionTokenCount("@a.csv2", "a.csv")).toBe(0);
    expect(mentionTokenCount("@a.csv#v-abcdef123456", "a.csv")).toBe(0);
    expect(mentionTokenCount("me@a.csv", "a.csv")).toBe(0);
  });

  it("matches a filename literally, spaces and regex characters included", () => {
    expect(mentionTokenCount("open @notes with space.txt now", "notes with space.txt")).toBe(1);
    expect(mentionTokenCount("@cohort_两张表.xlsx 第二张", "cohort_两张表.xlsx")).toBe(1);
    expect(mentionTokenCount("@run (1).csv", "run (1).csv")).toBe(1);
    expect(mentionTokenCount("@runX1Y.csv", "run (1).csv")).toBe(0);
  });
});

describe("the pins a draft carries", () => {
  it("keeps a pin while its token is in the text, and only then", () => {
    rememberMention(pin("cohort.csv", "v-9f5c06efc35e"));
    expect(versions("@cohort.csv 多少行？")).toEqual(["v-9f5c06efc35e"]);
    expect(versions("多少行？")).toEqual([]);
  });

  it("deleting a token and picking the same name again sends the newer pick only", () => {
    rememberMention(pin("data.csv", "v-111111111111", "a-one"));
    rememberMention(pin("data.csv", "v-222222222222", "a-two"));
    expect(versions("compare @data.csv")).toEqual(["v-222222222222"]);
    // Two tokens: both picks, oldest first.
    expect(versions("compare @data.csv with @data.csv")).toEqual(["v-111111111111", "v-222222222222"]);
  });

  it("re-picking a version keeps one pin for it", () => {
    rememberMention(pin("a.csv", "v-aaaaaaaaaaaa"));
    rememberMention(pin("b.csv", "v-bbbbbbbbbbbb"));
    rememberMention(pin("a.csv", "v-aaaaaaaaaaaa"));
    expect(versions("@a.csv @b.csv @a.csv")).toEqual(["v-bbbbbbbbbbbb", "v-aaaaaaaaaaaa"]);
  });

  it("is bounded, and the request field carries only filename and version", () => {
    for (let i = 0; i < MAX_MENTIONS + 5; i++) rememberMention(pin(`f${i}.csv`, `v-${String(i).padStart(12, "0")}`));
    const text = Array.from({ length: MAX_MENTIONS + 5 }, (_, i) => `@f${i}.csv`).join(" ");
    const kept = composerMentions(text);
    expect(kept).toHaveLength(MAX_MENTIONS);
    expect(kept[0]!.filename).toBe("f5.csv");
    expect(mentionsPayload(kept.slice(0, 1))).toEqual([{ filename: "f5.csv", version_id: "v-000000000005" }]);
  });

  it("clear forgets every pin; restore puts a refused draft's pins back", () => {
    rememberMention(pin("a.csv", "v-aaaaaaaaaaaa"));
    const sent = composerMentions("@a.csv");
    clearMentions();
    expect(versions("@a.csv")).toEqual([]);
    restoreMentions(sent);
    expect(versions("@a.csv")).toEqual(["v-aaaaaaaaaaaa"]);
  });

  it("blanks the pinned tokens so a scan for other tokens cannot read them again", () => {
    const pins = [pin("notes with space.txt", "v-cccccccccccc")];
    const out = blankMentionTokens("a @notes with space.txt b @other.csv", pins);
    expect(out).toBe("a " + " ".repeat("@notes with space.txt".length) + " b @other.csv");
  });
});

describe("text-form pins are shown without their version", () => {
  it("drops `#v-<id>` from an `@name` token and nothing else", () => {
    expect(stripRefVersions("@cohort_clinical_markers.csv#v-9f5c06efc35e 这个文件一共多少行")).toBe(
      "@cohort_clinical_markers.csv 这个文件一共多少行",
    );
    expect(stripRefVersions("a @x.csv#v-abcdef12 and\n@数据.csv#v-0123456789ab.")).toBe("a @x.csv and\n@数据.csv.");
    expect(stripRefVersions("issue #v-123456 and me@x.csv#v-abcdef12")).toBe("issue #v-123456 and me@x.csv#v-abcdef12");
    expect(stripRefVersions("@plain.csv only")).toBe("@plain.csv only");
  });
});
