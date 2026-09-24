/**
 * The Settings modal's UI: the one module that imports the Customize
 * component tree, so the build puts it in a chunk of its own. `index.ts`
 * loads it the first time Settings opens; the model stores behind the
 * composer's model list stay in the main bundle.
 */
import { h, render } from "preact";
import { Customize } from "../../components/customize/Customize";

export function mountCustomize(): void {
  if (typeof document === "undefined") return;
  if (import.meta.env.MODE === "test") return;
  let host = document.getElementById("cust-root");
  if (!host) {
    host = document.createElement("div");
    host.id = "cust-root";
    document.body.appendChild(host);
  }
  render(h(Customize, {}), host);
}
