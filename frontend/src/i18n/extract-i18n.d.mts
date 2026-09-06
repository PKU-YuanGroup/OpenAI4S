/** Typings for the Node extractor. Not imported by the workbench bundle. */
export function endOfAssign(src: string, assignPos: number): number;
export function extractAppJsDicts(source?: string): {
  zh: Record<string, string>;
  en: Record<string, string>;
};
export function emitDictTs(dict: Record<string, string>): string;
export function keyDiff(
  a: Record<string, string>,
  b: Record<string, string>,
): string[];
export function writeDicts(dicts: {
  zh: Record<string, string>;
  en: Record<string, string>;
}): void;
export function checkDicts(dicts: {
  zh: Record<string, string>;
  en: Record<string, string>;
}): string[];
