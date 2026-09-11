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

describe("heterogeneous JSON tables", () => {
  const wrap = (key: string | null, rows: unknown[]) => JSON.stringify(key ? { [key]: rows } : rows);
  it("keeps sparse object rows unchanged through every supported wrapper", () => {
    for (const key of [null, "rows", "data", "candidates", "items"]) {
      expect(parseTable(wrap(key, [{}, { late: 0, valid: false }]), { filename: "data.json" })).toEqual(
        [{}, { late: 0, valid: false }],
      );
    }
  });
  it("refuses a non-object at any row instead of filtering it from the source", () => {
    for (const key of [null, "rows", "data", "candidates", "items"]) {
      for (const invalid of [null, [], 0, false, "text"]) {
        for (const rows of [
          [invalid, { a: 1 }], [{ a: 1 }, invalid], [{ a: 1 }, invalid, { b: 2 }],
        ]) {
          expect(parseTable(wrap(key, rows), { filename: "data.json" })).toBeNull();
        }
      }
    }
  });
});

describe("array-of-array JSON tables", () => {
  it("renders a homogeneous array of arrays as positional columns", () => {
    expect(parseTable("[[1,2],[3,4]]", { filename: "values.json" })).toEqual([
      { "0": 1, "1": 2 },
      { "0": 3, "1": 4 },
    ]);
    expect(parseTable(JSON.stringify({ rows: [["a"], ["b", "c"]] }), { filename: "values.json" })).toEqual([
      { "0": "a" },
      { "0": "b", "1": "c" },
    ]);
  });
  it("still refuses a mix of array and object rows", () => {
    expect(parseTable("[[1,2],{\"a\":1}]", { filename: "values.json" })).toBeNull();
  });
});
