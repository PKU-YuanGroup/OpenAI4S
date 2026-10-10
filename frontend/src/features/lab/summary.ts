import { labT } from "./copy";

const rec = (v: unknown): Record<string, unknown> => v && typeof v === "object" && !Array.isArray(v) ? v as Record<string, unknown> : {};
const scalar = (v: unknown): string => typeof v === "string" ? v : typeof v === "number" && Number.isFinite(v) ? String(v) : labT("unknown");
/** A capability without a source or target declares null (an omitted key means the same),
 * which is "none" rather than unknown. */
const endpoint = (v: unknown): string => v == null ? "—" : scalar(v);
const parametersByOperation: Record<string, string[]> = {
  transfer_liquid: ["volume"], drain_layers: ["pixels"], mix_model: ["duration"], settle_model: ["duration"], end_experiment: [],
};
const units = new Set(["mL", "L", "layer_px", "model_time", "dimensionless"]);

/** Only semantic request fields are rendered. Never stringify an input or raw receipt. */
export function requestSummary(input: unknown): string {
  const r = rec(input), op = typeof r.operation === "string" ? r.operation : "";
  const parameters = rec(r.parameters);
  const quantities = (parametersByOperation[op] || []).flatMap((name) => {
    const q = rec(parameters[name]);
    return typeof q.value === "number" && Number.isFinite(q.value) && typeof q.unit === "string" && units.has(q.unit)
      ? [labT("parameter", name, q.value, q.unit)] : [];
  });
  return [scalar(r.run_id), scalar(r.operation), `${endpoint(r.source)} → ${endpoint(r.target)}`,
    ...quantities, labT("expectedRevision", scalar(r.expected_revision))].join(" · ");
}

export function permissionSummary(tool: string, input: Record<string, unknown>): string {
  if (tool === "lab_create") return [scalar(input.device_id), scalar(input.profile),
    ...(input.seed == null ? [] : [labT("parameter", "seed", scalar(input.seed), "")])].join(" · ");
  if (tool === "lab_stop") return scalar(input.run_id);
  return requestSummary(input);
}
