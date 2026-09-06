import { h, render } from "preact";
import { WizardHost } from "../../components/onboarding/Wizard";

export type WindowBag = Record<string, unknown>;

function mountOnboarding(): void {
  if (typeof document === "undefined") return;
  if (import.meta.env.MODE === "test") return;
  let host = document.getElementById("onboarding-root");
  if (!host) {
    host = document.createElement("div");
    host.id = "onboarding-root";
    document.body.appendChild(host);
  }
  render(h(WizardHost, {}), host);
}

/** M-01 boot: mount the first-run wizard. No window contract names. */
export function bootOnboarding(_target: WindowBag = globalThis as unknown as WindowBag): void {
  mountOnboarding();
}
