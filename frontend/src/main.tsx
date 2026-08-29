import "./compat/window-exports";
import { render } from "preact";
import { App } from "./app";
import { installTheme } from "./features/theme/theme";
import { bootWs } from "./features/ws";
import "./features/sessions";
import "./i18n";
import "./features/messages";

installTheme();
bootWs();

const mount = document.getElementById("app");
if (mount === null) {
  throw new Error("frontend: missing #app mount node");
}
render(<App />, mount);
