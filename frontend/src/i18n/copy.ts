import { LANG, t, tOptional } from "./runtime";

/** A feature-local copy table, for strings the generated dictionaries do not carry. */
export type CopyTable = Record<"zh" | "en", Record<string, string>>;

function fill(text: string, args: readonly unknown[]): string {
  return args.length
    ? text.replace(/\{(\d+)\}/g, (hole, i: string) => (args[+i] != null ? String(args[+i]) : hole))
    : text;
}

/**
 * A feature's corrections of the generated dictionaries (I18N-1). For the keys
 * the table names in the active language its entry wins; every other key, and
 * the other language, is the dictionary's -- so it can stand in for `t()`.
 * The generated zh.ts carries English for some keys ("Busy", "Variable
 * Inspector", "Action Timeline") and is a byte-checked extract nobody edits.
 */
export function overrideLookup(
  table: Partial<CopyTable>,
): (key: string, ...args: unknown[]) => string {
  return (key, ...args) => {
    const own = table[LANG]?.[key];
    return own == null ? t(key, ...args) : fill(own, args);
  };
}

/**
 * The lookup behind a feature-local copy table (`ot`, `judgmentT`, `filesT`):
 * the loaded dictionary wins, then the active language's entry, then
 * English, then the key itself; `{0}`, `{1}` … are filled positionally and a
 * hole with no argument is left as written. It reads the language through
 * `tOptional`, so a render that calls it repaints on a language switch.
 */
export function copyLookup(table: CopyTable): (key: string, ...args: unknown[]) => string {
  return (key, ...args) => {
    const fromDict = tOptional(key);
    let s = fromDict != null ? fromDict : table[LANG]?.[key] || table.en[key] || key;
    if (args.length) {
      s = String(s).replace(/\{(\d+)\}/g, (m, i) =>
        args[+i] != null ? String(args[+i]) : m,
      );
    }
    return s;
  };
}
