import "./compat/window-exports";
import { render } from "preact";
import { App } from "./app";
import { bootArtifacts } from "./features/artifacts";
import { bootIslands } from "./islands";
import { bootCustomize } from "./features/customize";
import { bootChrome } from "./features/chrome";
import { installTheme } from "./features/theme/theme";
import { installNotebook } from "./features/notebook";
import { bootWs } from "./features/ws";
import "./features/sessions";
import "./i18n";
import "./features/md";
import "./features/messages";
import "./features/send";
import "./features/timeline";
import "./features/autocomplete";

installTheme();
bootWs();
installNotebook();
bootArtifacts();
bootIslands();
bootCustomize();

const mount = document.getElementById("app");
if (mount === null) {
  throw new Error("frontend: missing #app mount node");
}
render(<App />, mount);
bootChrome();
