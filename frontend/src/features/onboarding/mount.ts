/**
 * The first-run wizard's UI: the one module that imports it, so the build
 * puts it in a chunk of its own. `boot.ts` loads it only when setup is not
 * complete.
 */
import { h, render } from "preact";
import { WizardHost } from "../../components/onboarding/Wizard";

export function mountOnboarding(): void {
  if (typeof document === "undefined") return;
  let host = document.getElementById("onboarding-root");
  if (!host) {
    host = document.createElement("div");
    host.id = "onboarding-root";
    document.body.appendChild(host);
  }
  render(h(WizardHost, {}), host);
}
