import { describe, expect, it } from "vitest";
import {
  askUploadConflict,
  numberedUploadName,
  type ConflictAnswer,
  type UploadConflictBatch,
} from "./upload-conflict";

describe("same-name upload prompt (UP-3)", () => {
  it("numbers like the server, keeping a compression suffix last", () => {
    expect(numberedUploadName("qpcr_plate.csv", 2)).toBe("qpcr_plate-2.csv");
    expect(numberedUploadName("counts.csv.gz", 2)).toBe("counts-2.csv.gz");
    expect(numberedUploadName("README", 3)).toBe("README-3");
  });

  it("shows one dialog at a time, in order, whatever order the uploads ask in", async () => {
    const open: string[] = [];
    const log: string[] = [];
    const releases: Array<() => void> = [];
    const ask = (name: string): Promise<ConflictAnswer> =>
      new Promise((resolve) => {
        open.push(name);
        log.push("open " + name + " while " + open.length + " open");
        releases.push(() => {
          open.splice(open.indexOf(name), 1);
          resolve({ choice: "rename", remember: false });
        });
      });
    const batch: UploadConflictBatch = { remembered: null, size: 2 };
    const a = askUploadConflict("a.csv", batch, ask);
    const b = askUploadConflict("b.csv", batch, ask);
    await Promise.resolve();
    await Promise.resolve();
    expect(log).toEqual(["open a.csv while 1 open"]);
    releases.shift()?.();
    expect(await a).toBe("rename");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(log).toEqual(["open a.csv while 1 open", "open b.csv while 1 open"]);
    releases.shift()?.();
    expect(await b).toBe("rename");
  });

  it("answers the rest of a batch when asked to, and only that batch", async () => {
    let asked = 0;
    const ask = async (): Promise<ConflictAnswer> => {
      asked += 1;
      return { choice: "version", remember: true };
    };
    const batch: UploadConflictBatch = { remembered: null, size: 3 };
    expect(await askUploadConflict("a.csv", batch, ask)).toBe("version");
    expect(await askUploadConflict("b.csv", batch, ask)).toBe("version");
    expect(await askUploadConflict("c.csv", batch, ask)).toBe("version");
    expect(asked).toBe(1);
    const other: UploadConflictBatch = { remembered: null, size: 1 };
    expect(await askUploadConflict("a.csv", other, ask)).toBe("version");
    expect(asked).toBe(2);
  });

  it("a prompt that cannot be shown cancels instead of hanging or replacing", async () => {
    const batch: UploadConflictBatch = { remembered: null, size: 1 };
    const broken = async (): Promise<ConflictAnswer> => {
      throw new Error("no document");
    };
    expect(await askUploadConflict("a.csv", batch, broken)).toBe("cancel");
  });
});
