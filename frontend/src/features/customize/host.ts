/**
 * Optional later-lane capabilities. Guard with isReady — F-05 placeholders
 * are functions, so `typeof x === "function"` would pass and then throw.
 */
import { isReady } from "../../compat/stub";
import { currentId, project, projects, sessions } from "../../stores/session";
import { skillsCatalog } from "../../stores/customize";
import { _envSnapById } from "../../stores/artifacts";
import { t } from "../../i18n";

type HostWindow = Window & {
  hint?: (message: string, err?: boolean, spin?: boolean) => void;
  openViewer?: (artifact: unknown) => void;
  loadModels?: () => Promise<void> | void;
  refreshKeyBanner?: () => Promise<void> | void;
  grow?: () => void;
  openModalEl?: (modal: Element) => void;
  closeModalEl?: (modal: Element) => void;
};

export function hostWindow(): HostWindow {
  return window as HostWindow;
}

export function hint(message: string, err?: boolean, spin?: boolean): void {
  if (typeof window === "undefined") return;
  const fn = hostWindow().hint;
  if (isReady(fn)) fn(message, err, spin);
}

export function confirmAction(message: string): boolean {
  try {
    return window.confirm(message);
  } catch {
    return false;
  }
}

export function openViewer(artifact: unknown): void {
  const fn = hostWindow().openViewer;
  if (isReady(fn)) fn(artifact);
}

export async function loadModels(): Promise<void> {
  const fn = hostWindow().loadModels;
  if (isReady(fn)) await fn();
}

export async function refreshKeyBanner(): Promise<void> {
  const fn = hostWindow().refreshKeyBanner;
  if (isReady(fn)) await fn();
}

export function growComposer(): void {
  const fn = hostWindow().grow;
  if (isReady(fn)) fn();
}

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function asList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function asBool(value: unknown): boolean {
  return !!value;
}

export function effProject(): string | null {
  if (project.value) return project.value;
  const list = sessions.value as Array<{ id?: string; project_id?: string }>;
  const f = list.find((x) => x.id === currentId.value);
  return (f && f.project_id) || null;
}

export function projectName(pid: string | null | undefined): string {
  if (!pid) return "";
  const list = projects.value as Array<{
    project_id?: string;
    id?: string;
    name?: string;
  }>;
  const p = list.find((x) => (x.project_id || x.id) === pid);
  return (p && p.name) || pid;
}

export function dropSkillsCatalog(): void {
  skillsCatalog.value = null;
}

export function dropEnvSnapshots(): void {
  _envSnapById.value = {};
}

export function insertSkillMention(name: string): void {
  const workspace = document.getElementById("workspace");
  if (workspace && workspace.classList.contains("hidden")) {
    hint(t("skill.insertedToast", name));
    return;
  }
  const c = document.getElementById("composer") as HTMLTextAreaElement | null;
  if (!c) {
    hint(t("skill.insertedToast", name));
    return;
  }
  const cur = c.value || "";
  c.value = (cur && !/\s$/.test(cur) ? cur + " " : cur) + "/" + name + " ";
  growComposer();
  c.focus();
  c.setSelectionRange(c.value.length, c.value.length);
  hint(t("skill.insertedToast", name));
}

export function closeCustomizeDom(): void {
  if (typeof document === "undefined") return;
  const modal = document.getElementById("cust");
  if (!modal) return;
  const closer = hostWindow().closeModalEl;
  if (isReady(closer)) closer(modal);
  else modal.classList.add("hidden");
}

export function openCustomizeDom(): void {
  if (typeof document === "undefined") return;
  const modal = document.getElementById("cust");
  if (!modal) return;
  const opener = hostWindow().openModalEl;
  if (isReady(opener)) opener(modal);
  else modal.classList.remove("hidden");
}
