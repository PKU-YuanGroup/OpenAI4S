import { render } from "preact";
import { App } from "./app";
import { installTheme } from "./features/theme/theme";

installTheme();

const mount = document.getElementById("app");
if (mount === null) {
  throw new Error("frontend: missing #app mount node");
}
render(<App />, mount);
