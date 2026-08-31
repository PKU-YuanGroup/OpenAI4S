import "./compat/window-exports";
import { render } from "preact";
import { App } from "./app";
import { bootArtifacts } from "./features/artifacts";
import { bootIslands } from "./islands";
import { bootCustomize } from "./features/customize";
import { bootOnboarding } from "./features/onboarding";
import { bootAttention } from "./features/attention";
import { bootChrome } from "./features/chrome";
import { installTheme } from "./features/theme/theme";
import { bootExecution } from "./features/execution";
import { installNotebook } from "./features/notebook";
import { bootWs } from "./features/ws";
import "./features/sessions";
import "./i18n";
import "./features/md";
import "./features/messages";
import "./features/send";
import "./features/timeline";
import "./features/autocomplete";
import "./features/table";

installTheme();
bootWs();
installNotebook();
bootArtifacts();
bootExecution();
bootIslands();
bootCustomize();
bootOnboarding();

const mount = document.getElementById("app");
if (mount === null) {
  throw new Error("frontend: missing #app mount node");
}
render(<App />, mount);
bootChrome();
bootAttention();
