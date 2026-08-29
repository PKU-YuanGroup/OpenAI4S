/**
 * Local-model discovery sanitizer and protocol catalogue.
 * Port of app.js:12064-12150. Probe/readiness stay on the existing routes
 * (`GET /model-profiles` readiness is local-only; `POST .../probe` spends
 * quota). Capability-receipt badges read the B-04 additive field when present.
 */
import { publicText } from "../scrub/scrub";
import { t, tOptional } from "../../i18n";

export const LOCAL_MODEL_KINDS = new Set([
  "ollama",
  "lm_studio",
  "vllm",
  "llama_cpp",
]);

export function loopbackModelBase(value: unknown): string {
  const text = publicText(value, 600);
  try {
    const parsed = new URL(text);
    const host = parsed.hostname.toLowerCase();
    const safeHost = host === "127.0.0.1" || host === "::1" || host === "[::1]";
    return ["http:", "https:"].includes(parsed.protocol) &&
      safeHost &&
      !parsed.username &&
      !parsed.password &&
      !parsed.search &&
      !parsed.hash
      ? parsed.toString().replace(/\/$/, "")
      : "";
  } catch {
    return "";
  }
}

export type LocalEndpoint = {
  kind: string;
  label: string;
  provider: "chatgpt";
  base_url: string;
  models: string[];
  default_model: string;
  requires_api_key: false;
};

export type LocalDiscovery = {
  endpoints: LocalEndpoint[];
  probed: number;
  mutated_settings: false;
};

export function sanitizeLocalModelDiscovery(payload: unknown): LocalDiscovery {
  const source =
    payload && typeof payload === "object" ? (payload as Record<string, unknown>) : {};
  const endpoints: LocalEndpoint[] = [];
  const rawList = Array.isArray(source.endpoints) ? source.endpoints : [];
  rawList.slice(0, 20).forEach((raw) => {
    if (!raw || typeof raw !== "object") return;
    const row = raw as Record<string, unknown>;
    const kind = publicText(row.kind, 32);
    const baseUrl = loopbackModelBase(row.base_url);
    if (
      !LOCAL_MODEL_KINDS.has(kind) ||
      !baseUrl ||
      row.local !== true ||
      row.provider !== "chatgpt"
    ) {
      return;
    }
    const models: string[] = [];
    (Array.isArray(row.models) ? row.models : []).slice(0, 500).forEach((value) => {
      if (typeof value !== "string") return;
      const model = publicText(value, 512);
      if (model && !models.includes(model)) models.push(model);
    });
    endpoints.push({
      kind,
      label: publicText(row.label, 80) || kind,
      provider: "chatgpt",
      base_url: baseUrl,
      models,
      default_model: models.includes(String(row.default_model || ""))
        ? String(row.default_model)
        : models[0] || "",
      requires_api_key: false,
    });
  });
  return {
    endpoints,
    probed: Math.max(0, Math.min(20, Number(source.probed) || 0)),
    mutated_settings: false,
  };
}

const PROTOCOL_LABEL_KEYS: Record<string, string> = {
  chatgpt: "cust.models.protocol.openai",
  claude: "cust.models.protocol.anthropic",
  ark: "cust.models.protocol.ark",
  gemini: "cust.models.protocol.gemini",
  openai_responses: "cust.models.protocol.openaiResponses",
};

export type ProtocolOption = { value: string; label: string };

export function modelProtocolOptions(served: unknown): ProtocolOption[] {
  const ids: string[] = [];
  (Array.isArray(served) ? served : []).forEach((value) => {
    const id = typeof value === "string" ? value.trim().slice(0, 64) : "";
    if (id && !ids.includes(id)) ids.push(id);
  });
  const list = ids.length ? ids : ["chatgpt", "claude", "ark"];
  return list.map((id) => ({
    value: id,
    label: tOptional(PROTOCOL_LABEL_KEYS[id] || "") || id,
  }));
}

export type Evidence = "true" | "false" | "unknown";

export type CapabilityReceipt = {
  native_tool_call: Evidence;
  streaming: Evidence;
  stale: boolean;
  native_completion: boolean;
  reachable: boolean;
  detail?: string;
};

function asEvidence(value: unknown): Evidence {
  if (value === true || value === "true") return "true";
  if (value === false || value === "false") return "false";
  return "unknown";
}

export function readCapabilityReceipt(raw: unknown): CapabilityReceipt | null {
  if (!raw || typeof raw !== "object") return null;
  const row = raw as Record<string, unknown>;
  if (row.native_tool_call == null && row.streaming == null) return null;
  return {
    native_tool_call: asEvidence(row.native_tool_call),
    streaming: asEvidence(row.streaming),
    stale: row.stale === true,
    native_completion: row.native_completion === true,
    reachable: row.reachable === true,
  };
}

export function protocolLabelOf(
  protocols: ProtocolOption[],
  provider: unknown,
): string {
  const id = typeof provider === "string" ? provider : "";
  const match = protocols.find((item) => item.value === id);
  return match ? match.label : id;
}

export { t };
