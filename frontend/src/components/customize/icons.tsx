import { ICON_PATHS } from "../../features/icons/paths";

/**
 * Lucide drawings used by Customize, from the workbench's shared table
 * (`features/icons/paths.ts`). The modal used to keep a copy of its own,
 * which can only drift; its four modal-only names now live in that table.
 */
function paths(name: string): string {
  return Object.prototype.hasOwnProperty.call(ICON_PATHS, name) ? ICON_PATHS[name] || "" : "";
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
