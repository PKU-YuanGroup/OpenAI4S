import { describe, expect, it } from "vitest";
import { csvFields, delimiterFor, parseDelimited, parseTable, tableShape } from "./csv";

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

describe("delimited tables keep every column (AUDIT A44)", () => {
  it("does not sniff a declared CSV/TSV as JSON when a header opens with a bracket", () => {
    expect(parseTable("[Na+],[Cl-],conc\n1,2,3\n", { filename: "ions.csv" })).toEqual([
      { "[Na+]": "1", "[Cl-]": "2", conc: "3" },
    ]);
    expect(parseTable("{a}\tb\n1\t2\n", { filename: "t", content_type: "text/tab-separated-values" })).toEqual([
      { "{a}": "1", b: "2" },
    ]);
    // Undeclared text that looks like JSON is still read as JSON.
    expect(parseTable('[{"a":1}]', { filename: "rows.txt" })).toEqual([{ a: 1 }]);
  });

  it("suffixes a repeated header instead of overwriting its twin", () => {
    expect(parseTable("mean,mean,sd,mean\n1,2,3,4\n", { filename: "t.csv" })).toEqual([
      { mean: "1", "mean.1": "2", sd: "3", "mean.2": "4" },
    ]);
  });

  it("keeps cells past the header under their position; trailing separators add nothing", () => {
    expect(parseTable("a,b\n1,2,3\n4,5\n", { filename: "t.csv" })).toEqual([
      { a: "1", b: "2", "2": "3" },
      { a: "4", b: "5", "2": "" },
    ]);
    expect(parseTable("a,b\n1,2,\n3,4,,\n", { filename: "t.csv" })).toEqual([
      { a: "1", b: "2" },
      { a: "3", b: "4" },
    ]);
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

describe("tableShape: parseTable's rows and columns without the rows (AUDIT P06)", () => {
  /** What the thumbnail reads off parseTable: the row count and the column set. */
  function fromParse(text: string, a: { filename?: string; content_type?: string }) {
    const rows = parseTable(text, a);
    return rows && rows.length ? { rows: rows.length, columns: Object.keys(rows[0] || {}).sort() } : null;
  }
  function shape(text: string, a: { filename?: string; content_type?: string }) {
    const found = tableShape(text, a);
    return found && { rows: found.rows, columns: [...found.columns].sort() };
  }

  it("agrees with parseTable on hand-picked tables", () => {
    const cases: Array<[string, { filename?: string; content_type?: string }]> = [
      [QUOTED_NEWLINE, { filename: "t.csv" }],
      ["gene\tlogFC\tpval\nTP53\t2.4\t0.001\n", { filename: "de.tsv" }],
      ["gene\ts1\ts2\ts3\nA\t1\t2\t3\n", { filename: "counts.txt" }],
      ["mean,mean,sd\r\n1,2,3\r\n\r\n4,5,6,7\r\n", { filename: "t.csv" }],
      ['a,"b\r\n""c"""\n1,2\n, \n', { filename: "t.csv" }],
      ["[Na+],[Cl-],conc\n1,2,3\n", { filename: "ions.csv" }],
      ['[{"a":1,"b":2},{"a":3}]', { filename: "rows.json" }],
      ["only a header\n", { filename: "t.csv" }],
      ["", { filename: "t.csv" }],
    ];
    for (const [text, a] of cases) expect(shape(text, a)).toEqual(fromParse(text, a));
    expect(tableShape("x,y,x\n1,2,3,4\n", { filename: "t.csv" })).toEqual({ rows: 1, columns: ["x", "y", "x.1", "3"] });
  });

  it("agrees with parseTable on generated delimited text", () => {
    const tokens = ["a", "b", "7", " ", " ", ",", "\t", ";", "|", '"', '""', "\n", "\r\n", "\r", "[", "{"];
    const names = ["t.csv", "t.tsv", "t.txt", "t"];
    let x = 3;
    const next = (n: number) => ((x = (Math.imul(x, 1103515245) + 12345) >>> 0), (x >>> 8) % n);
    let tables = 0;
    for (let run = 0; run < 3000; run++) {
      let text = "";
      for (let i = 0, n = 1 + next(40); i < n; i++) text += tokens[next(tokens.length)];
      const a = { filename: names[next(names.length)] };
      const expected = fromParse(text, a);
      if (expected) tables += 1;
      expect(shape(text, a), JSON.stringify([text, a])).toEqual(expected);
    }
    // Most generated texts must be real tables, or agreeing on `null` proves little.
    expect(tables).toBeGreaterThan(1000);
  });
});
