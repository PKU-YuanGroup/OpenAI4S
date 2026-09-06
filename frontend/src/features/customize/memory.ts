/**
 * Memory scope helpers. Port of app.js:11993-12003.
 * Save always sends the chosen scope — never the literal "default".
 */
import { t } from "../../i18n";
import { effProject, projectName } from "./host";

export type MemScope = { id: string; label: string };

export function memScopeLabel(pid: string | null | undefined): string {
  if (!pid || pid === "global") return t("cust.memory.scope.global");
  return projectName(pid);
}

export function memScopes(): MemScope[] {
  const pid = effProject();
  const out: MemScope[] = [{ id: "global", label: t("cust.memory.scope.global") }];
  if (pid) out.push({ id: pid, label: memScopeLabel(pid) });
  return out;
}

export const MEMORY_BLOCKS = [
  "user",
  "project",
  "preference",
  "fact",
  "general",
] as const;
