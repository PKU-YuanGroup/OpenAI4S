import { Shell } from "./components/dashboard/Shell";
import { languageRevision } from "./i18n/runtime";

export function App() {
  // Read so a language switch (and the dictionaries landing) repaints the
  // whole Shell tree, including views whose copy tables read `LANG` directly.
  void languageRevision.value;
  return <Shell />;
}
