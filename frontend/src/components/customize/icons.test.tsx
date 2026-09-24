import { describe, expect, it } from "vitest";
import { ICON_PATHS } from "../../features/icons/paths";
import { Icon } from "./icons";

const drawn = (name: string) =>
  (Icon({ name }) as unknown as { props: { dangerouslySetInnerHTML: { __html: string } } }).props
    .dangerouslySetInnerHTML.__html;

const SHAPE = /<(path|circle|rect|line|polyline)\b/;

// Every name the modal, its rows and the Volcengine panel paint.
const USED = [
  "x", "pencil", "trash-2", "message-square", "clock", "check",
  "alert-triangle", "terminal", "globe", "link", "lock", "refresh",
];

describe("Customize icons", () => {
  it("draws every name the modal paints, and nothing for an unknown one", () => {
    for (const name of USED) expect(drawn(name), name).toMatch(SHAPE);
    expect(drawn("constructor")).toBe("");
    expect(drawn("no-such-icon")).toBe("");
  });

  it("draws a name the shared table carries from that table", () => {
    const table = ICON_PATHS as Record<string, string>;
    const original = table.x!;
    table.x = '<path d="M0 0h1"/>';
    try {
      expect(drawn("x")).toBe('<path d="M0 0h1"/>');
    } finally {
      table.x = original;
    }
  });
});
