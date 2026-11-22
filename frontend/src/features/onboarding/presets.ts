/**
 * Provider presets for the first-run wizard's cloud form (FR-2).
 *
 * The form offered a bare "compatible protocol" dropdown and an empty Base
 * URL box. A preset is what the backend's provider registry already knows
 * about a protocol — its own endpoint and default model, served as
 * `provider_presets` on `GET /onboarding` — plus a name a person recognises.
 * No URL is made up here: endpoints come from the registry, and a "get a
 * key" page is listed only where `docs/startup-guide.md` names one.
 */
import { publicText } from "../scrub/scrub";
import { ot } from "./copy";

export type ProviderPreset = { id: string; baseUrl: string; model: string };

/** Where `docs/startup-guide.md` ("Which protocol / key?") says a key comes from. */
const KEY_PAGES: Record<string, string> = {
  ark: "https://console.volcengine.com/ark",
  claude: "https://console.anthropic.com",
  gemini: "https://aistudio.google.com",
  ollama: "https://ollama.com",
};

export function readProviderPresets(raw: unknown): ProviderPreset[] {
  if (!Array.isArray(raw)) return [];
  const presets: ProviderPreset[] = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const row = item as Record<string, unknown>;
    const id = publicText(row.id, 64).trim();
    if (!id || presets.some((preset) => preset.id === id)) continue;
    presets.push({ id, baseUrl: publicText(row.base_url, 600), model: publicText(row.model, 512) });
  }
  return presets;
}

/** The provider's name for the dropdown; `fallback` (the protocol label) when unknown. */
export function presetLabel(id: string, fallback: string): string {
  const key = "onboarding.preset." + id;
  const label = ot(key);
  return label === key ? fallback : label;
}

/** The documented page to get a key from, or null (then only words are shown). */
export function keyPage(id: string): { url: string; label: string } | null {
  const url = KEY_PAGES[id];
  return url ? { url, label: url.replace(/^https:\/\//, "") } : null;
}
