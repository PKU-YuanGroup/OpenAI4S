import { ICON_PATHS } from "../../features/icons/paths";

/**
 * Lucide drawings used by Customize. Names in the workbench's shared table
 * (`features/icons/paths.ts`) are drawn from it; the modal used to keep a copy
 * of its own, which can only drift. These four are not in the shared table.
 */
const MODAL_ONLY: Readonly<Record<string, string>> = {
  globe:
    '<circle cx="12" cy="12" r="10"/><path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20"/><path d="M2 12h20"/>',
  lock: '<rect width="18" height="11" x="3" y="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  refresh:
    '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
};

function paths(name: string): string {
  const own = Object.prototype.hasOwnProperty;
  if (own.call(ICON_PATHS, name)) return ICON_PATHS[name] || "";
  return own.call(MODAL_ONLY, name) ? MODAL_ONLY[name] || "" : "";
}

export function Icon({
  name,
  size = 16,
  spin,
}: {
  name: string;
  size?: number;
  spin?: boolean;
}) {
  return (
    <svg
      class={"ic-svg" + (spin ? " spin" : "")}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
      dangerouslySetInnerHTML={{ __html: paths(name) }}
    />
  );
}
