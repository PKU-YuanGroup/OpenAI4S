import { useEffect, useState } from "preact/hooks";
import { onLanguageChange } from "../../i18n/runtime";
import { dashT } from "./copy";

/**
 * The dashboard's headline and one-line pitch. The strings come from the
 * local copy table, not `data-i18n`, so a language switch repaints them here.
 */
export function DashHeroText() {
  const [, repaint] = useState(0);
  useEffect(() => onLanguageChange(() => repaint((n) => n + 1)), []);
  return (
    <>
      <h1 class="dash-hero-title">{dashT("dash.hero.title")}</h1>
      <p class="dash-hero-sub">{dashT("dash.hero.sub")}</p>
    </>
  );
}
