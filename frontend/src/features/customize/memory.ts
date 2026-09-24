/**
 * Memory scope helpers. Port of app.js:11993-12003.
 * Save always sends the chosen scope — never the literal "default".
 */
import { t } from "../../i18n";
import { projectName } from "./host";

export function memScopeLabel(pid: string | null | undefined): string {
  if (!pid || pid === "global") return t("cust.memory.scope.global");
  return projectName(pid);
}

/** Where a memory can be saved: global, then the current project when there is one. */
export function memScopeIds(pid: string | null): string[] {
  return pid ? ["global", pid] : ["global"];
}

export const MEMORY_BLOCKS = [
  "user",
  "project",
  "preference",
  "fact",
  "general",
] as const;
