import "./compat/window-exports";
import { render } from "preact";
import { App } from "./app";
import { bootCustomize } from "./features/customize";
import { installTheme } from "./features/theme/theme";
import { bootWs } from "./features/ws";
import "./i18n";

installTheme();
bootWs();
bootCustomize();

const mount = document.getElementById("app");
if (mount === null) {
  throw new Error("frontend: missing #app mount node");
}
render(<App />, mount);
