import { describe, expect, it } from "vitest";
import { csvFields, delimiterFor, parseDelimited, parseTable } from "./csv";

/**
 * Quoted-newline sample. The three consumers that used to disagree
 * (parseDelimited 9690, csvFields 12907, parseTable 12878) must now agree.
 */
const QUOTED_NEWLINE = ['id,note,count', '1,"hello', 'world",2', '3,"plain",4'].join(
  "\n",
);

const EXPECTED_ROWS = [
  ["id", "note", "count"],
  ["1", "hello\nworld", "2"],
  ["3", "plain", "4"],
];

describe("CSV quoted-newline: three paths agree", () => {
  it("parseDelimited keeps the newline inside the quoted field", () => {
    expect(parseDelimited(QUOTED_NEWLINE, ",")).toEqual(EXPECTED_ROWS);
  });

  it("delimiterFor + parseDelimited matches parseDelimited with a comma", () => {
    const firstLine = QUOTED_NEWLINE.split("\n")[0] || "";
    const sep = delimiterFor("table.csv", "", firstLine);
    expect(sep).toBe(",");
    expect(parseDelimited(QUOTED_NEWLINE, sep)).toEqual(EXPECTED_ROWS);
  });

  it("parseTable object rows match the same grid", () => {
    expect(parseTable(QUOTED_NEWLINE, { filename: "table.csv" })).toEqual([
      { id: "1", note: "hello\nworld", count: "2" },
      { id: "3", note: "plain", count: "4" },
    ]);
  });

  it("csvFields on a quoted-newline record is the same cell", () => {
    expect(csvFields('"hello\nworld"', ",")).toEqual(["hello\nworld"]);
    expect(csvFields(EXPECTED_ROWS[0]?.join(",") || "", ",")).toEqual(
      EXPECTED_ROWS[0],
    );
  });

  it("doubled quotes still unescape", () => {
    const src = "a,\"b\"\"c\"\n";
    expect(parseDelimited(src, ",")).toEqual([["a", "b\"c"]]);
    expect(csvFields("a,\"b\"\"c\"", ",")).toEqual(["a", "b\"c"]);
  });
});

describe("delimiterFor", () => {
  it("trusts .tsv / .csv before sniffing", () => {
    expect(delimiterFor("x.tsv", "", "a,b,c")).toBe("\t");
    expect(delimiterFor("x.csv", "", "a\tb\tc")).toBe(",");
  });

  it("sniffs the widest split when the name is not csv/tsv", () => {
    expect(delimiterFor("x.txt", "", "a\tb\tc")).toBe("\t");
    expect(delimiterFor("x.dat", "", "a;b;c")).toBe(";");
  });
});
