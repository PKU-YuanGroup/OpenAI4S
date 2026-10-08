import { useState } from "preact/hooks";
import { LANG } from "../../i18n/runtime";
import { labT } from "./copy";
import { lab, labConnected, type LabState } from "./state";
import { permissionSummary, requestSummary } from "./summary";
import type { Capability, ChannelSpec, Command, Descriptor, Device, Observation } from "./types";

type ChannelView = { spec: ChannelSpec; quality: string; value: unknown };
function matchesShape(value: unknown, shape: number[]): boolean {
  if (!shape.length) return typeof value === "number" && Number.isFinite(value);
  return Array.isArray(value) && value.length === shape[0] && value.every((v) => matchesShape(v, shape.slice(1)));
}
export function publicChannels(descriptor: Descriptor, observation: Observation | null): ChannelView[] {
  return descriptor.observation_channels.filter((s) => ["simulated_sensor", "physical_sensor"].includes(s.source)).map((spec) => {
    const row = observation?.channels.find((c) => c.name === spec.name && c.kind === spec.kind &&
      c.unit === spec.unit && c.source === spec.source && JSON.stringify(c.shape) === JSON.stringify(spec.shape));
    const valid = spec.kind === "category" ? typeof row?.value === "string" : matchesShape(row?.value, spec.shape);
    const quality = !spec.available || row?.quality === "unavailable" ? "unavailable" : row?.quality === "ok" && valid ? "ok" : "unknown";
    return { spec, quality, value: quality === "ok" ? row!.value : null };
  });
}
const missing = (quality: string): string => labT(quality === "unavailable" ? "unavailable" : "unknown");
const date = (value: number | undefined): string => typeof value === "number" && Number.isFinite(value)
  ? new Date(value).toLocaleString(LANG === "zh" ? "zh-CN" : "en-US") : labT("unknown");
const backend = (name: string): string => name === "toy" ? "Toy" : name === "chemgymrl" ? "ChemGymRL" : name;

export function DeviceSetup({ devices, disabled }: { devices: Device[]; disabled: boolean }) {
  const [chosen, setChosen] = useState("");
  const [chosenProfile, setProfile] = useState("");
  const [seed, setSeed] = useState("");
  const device = devices.find((d) => d.device_id === chosen) || devices[0];
  const profile = device?.profiles.includes(chosenProfile) ? chosenProfile : device?.profiles[0] || "";
  const seedValue = seed.trim() === "" ? undefined : Number(seed);
  const validSeed = seedValue === undefined || Number.isInteger(seedValue) && seedValue >= 0 && seedValue <= 4294967295;
  return <section class="lab-card lab-setup">
    <h3>{labT("create")}</h3>
    {!device ? <p>{labT("noDevices")}</p> : <>
      <span class="lab-badge">{labT("simulation", backend(device.backend))}</span>
      <label>{labT("device")}<select value={device.device_id} disabled={disabled} onChange={(e) => { setChosen(e.currentTarget.value); setProfile(""); }}>
        {devices.map((d) => <option value={d.device_id} key={d.device_id}>{d.title} · {d.device_id}</option>)}
      </select></label>
      <label>{labT("profile")}<select value={profile} disabled={disabled} onChange={(e) => setProfile(e.currentTarget.value)}>
        {device.profiles.map((p) => <option value={p} key={p}>{p}</option>)}
      </select></label>
      <label>{labT("seed")}<input type="number" min="0" max="4294967295" step="1" value={seed} disabled={disabled} onInput={(e) => setSeed(e.currentTarget.value)} /></label>
      {!validSeed && <p role="alert">{labT("invalidSeed")}</p>}
      {device.available !== true && <p class="lab-notice">{device.availability_detail || labT(device.available === false ? "unavailable" : "availabilityUnknown")}</p>}
      <button type="button" disabled={disabled || device.available !== true || !profile || !validSeed} onClick={() => void lab.create(device, profile, seedValue)}>{labT("create")}</button>
    </>}
  </section>;
}

export function VesselView({ descriptor, observation, action }: { descriptor: Descriptor; observation: Observation | null; action?: Capability }) {
  const layer = publicChannels(descriptor, observation).find((c) => c.spec.name === "layers");
  const labels = layer?.spec.axes?.[0]?.labels;
  const rows = layer?.quality === "ok" && layer.spec.shape.length === 2 && labels?.length === layer.spec.shape[0]
    ? layer.value as number[][] : null;
  return <section class="lab-card">
    <h3>{labT("vessels")}</h3>
    <p class="lab-muted">{labT("layers")}</p>
    <div class="lab-vessels">
      {descriptor.resources.map((resource) => {
        const row = rows && labels ? rows[labels.indexOf(resource.resource_id)] : null;
        const highlighted = resource.resource_id === action?.source || resource.resource_id === action?.target;
        return <figure key={resource.resource_id} class={highlighted ? "lab-vessel lab-highlight" : "lab-vessel"}>
          {row ? <svg viewBox="0 0 100 150" role="img" aria-label={resource.label}>
            {row.map((intensity, i) => <rect key={i} x="16" y={12 + 124 * i / row.length} width="68" height={124 / row.length + 0.1}
              fill={`rgba(41, 122, 171, ${0.12 + 0.88 * Math.max(0, Math.min(1, intensity))})`} />)}
            <path d="M12 6 V132 Q12 140 20 140 H80 Q88 140 88 132 V6" fill="none" stroke="currentColor" stroke-width="2" />
          </svg> : <div class="lab-empty-vessel">{missing(layer?.quality || "unknown")}</div>}
          <figcaption><strong>{resource.label}</strong><small>{resource.resource_id}</small><small>{labT("composition")}</small></figcaption>
        </figure>;
      })}
    </div>
  </section>;
}

function sensorText(value: unknown): string {
  if (typeof value === "string" || typeof value === "number") return String(value);
  if (Array.isArray(value)) return "[" + value.map(sensorText).join(", ") + "]";
  return labT("unknown");
}
export function ObservationView({ descriptor, observation }: { descriptor: Descriptor; observation: Observation | null }) {
  return <section class="lab-card"><h3>{labT("observations")}</h3>
    {observation && <p>{labT("observationSequence", observation.sequence)}</p>}
    {publicChannels(descriptor, observation).map(({ spec, quality, value }) => <div class="lab-channel" key={spec.name}>
      <strong>{spec.name}</strong>
      <small>{labT("source")}: {spec.source} · {labT("unit")}: {spec.unit} · {labT("quality")}: {quality}</small>
      {quality !== "ok" ? <p>{missing(quality)}</p> : spec.name === "layers" ?
        <p>{spec.shape.join(" × ")} · {labT("vessels")}</p> : <pre>{sensorText(value)}</pre>}
    </div>)}
  </section>;
}

export function ActionForm({ descriptor, disabled, onSelect }: { descriptor: Descriptor; disabled: boolean; onSelect: (c: Capability | undefined) => void }) {
  const caps = descriptor.capabilities.filter((c) => !c.terminal);
  const [chosen, setChosen] = useState("");
  const [values, setValues] = useState<Record<string, number>>({});
  const cap = caps.find((c) => c.capability_id === chosen) || caps[0];
  const selected = cap ? Object.fromEntries(Object.entries(cap.parameters).map(([name, spec]) => [name,
    spec.allowed.includes(values[name]!) ? values[name]! : spec.allowed[0]!])) : {};
  const valid = !!cap && Object.entries(cap.parameters).every(([name, spec]) => spec.allowed.includes(selected[name]!));
  return <section class="lab-card">
    <h3>{labT("selectedAction")}</h3>
    <label>{labT("operation")}<select disabled={disabled || !cap} value={cap?.capability_id || ""} onChange={(e) => {
      setChosen(e.currentTarget.value); setValues({}); onSelect(caps.find((c) => c.capability_id === e.currentTarget.value));
    }}>{caps.map((c) => <option key={c.capability_id} value={c.capability_id}>{c.operation} · {c.source || "—"} → {c.target || "—"}</option>)}</select></label>
    {cap && Object.entries(cap.parameters).map(([name, spec]) => <label key={cap.capability_id + name}>{name} ({spec.unit})
      <select disabled={disabled} value={String(selected[name])} onChange={(e) => setValues({ ...values, [name]: Number(e.currentTarget.value) })}>
        {spec.allowed.map((value) => <option key={value} value={String(value)}>{value} {spec.unit}</option>)}
      </select></label>)}
    <button type="button" disabled={disabled || !valid} onClick={() => cap && void lab.execute(cap, selected)}>{labT("execute")}</button>
  </section>;
}

export function CommandHistory({ commands, observation, querying, sequences = {} }: { commands: Command[]; observation: Observation | null; querying: string | null; sequences?: Record<string, number> }) {
  return <section class="lab-card"><h3>{labT("actions")}</h3><p class="lab-muted">{labT("recentActions", commands.length)}</p>
    {commands.map((command) => <article key={command.command_id} class={command.state === "outcome_unknown" ? "lab-command lab-uncertain" : "lab-command"}>
      <strong>#{command.seq} · {command.state}</strong>
      <small>{command.command_id}</small>
      <p>{labT("request")}: {requestSummary(command.request)}</p>
      {command.error && <p class="lab-notice">{command.error_code}: {command.error}</p>}
      <p>{labT("receipt")}: {command.receipt ? `${command.receipt.status} · ${labT("applied", command.receipt.applied)}` : labT("noReceipt")}</p>
      <p>{labT("observation")}: {command.observation_id || labT("noObservation")}
        {command.observation_id && observation?.observation_id === command.observation_id ? ` · ${labT("observationSequence", observation.sequence)}` :
          command.observation_id && sequences[command.observation_id] != null ? ` · ${labT("observationSequence", sequences[command.observation_id])}` : ""}</p>
      {command.state === "outcome_unknown" && <div role="status"><p>{labT("unknownOutcome")}</p>
        <button type="button" disabled={querying !== null} onClick={() => void lab.reconcile(command.command_id)}>{labT("reconcile")}</button></div>}
    </article>)}
  </section>;
}

export function LabPane() {
  const state: LabState = lab.state.value;
  const { detail, pending } = state;
  const [action, setAction] = useState<{ runId: string; capability: Capability } | undefined>(undefined);
  const selectedAction = detail?.descriptor.capabilities.find((c) => detail.run.run_id === action?.runId && c.capability_id === action.capability.capability_id) || detail?.descriptor.capabilities.find((c) => !c.terminal);
  return <div class="lab-pane">
    <header class="lab-heading"><h2>{labT("title")}</h2><button type="button" disabled={!state.rootId || state.loading} onClick={() => void lab.refresh()}>{labT("refresh")}</button></header>
    <p class="lab-connection" role="status">{labT(labConnected.value ? "connected" : "disconnected")}</p>
    {!state.rootId ? <p>{labT("noSession")}</p> : <>
      {state.loading && <p role="status">{labT("loading")}</p>}
      {state.error && <p class="lab-notice" role="alert">{labT("error", state.error)}</p>}
      {pending && <section class="lab-card lab-uncertain"><p>{labT(pending.sending ? "pending" : "retryNotice")}</p>
        <p>{permissionSummary(pending.intent.kind === "create" ? "lab_create" : "lab_execute", { ...pending.intent.body, ...(pending.intent.kind === "execute" ? { run_id: pending.intent.runId } : {}) })}</p>
        <code>{pending.intent.body.idempotency_key}</code>
        {!pending.sending && <button type="button" onClick={() => void lab.retry()}>{labT("retry")}</button>}
      </section>}
      <DeviceSetup devices={state.devices} disabled={!!pending || state.stopping} />
      {state.runs.length ? <label>{labT("run")}<select value={state.selectedRunId || ""} disabled={!!pending?.sending || state.stopping || !!state.querying} onChange={(e) => void lab.select(e.currentTarget.value)}>
        {state.runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.profile} · {r.run_id} · {r.status}</option>)}
      </select></label> : <p>{labT("noRuns")}</p>}
      {detail && <>
        <section class="lab-card lab-overview">
          <span class="lab-badge">{labT("simulation", backend(detail.run.backend))}</span>
          <h3>{detail.run.profile}</h3><small>{detail.run.device_id}</small>
          <div class="lab-metrics"><span>{labT("steps", detail.run.step_count, detail.run.budgets.max_steps)}</span><span>{labT("revision", detail.run.revision)}</span></div>
          <p>{labT("state", detail.run.status)}</p>
          <p>{labT("modelTime", detail.observation?.sim_time ?? labT("unknown"))}</p>
          <p>{labT("latest", date(detail.observation?.wall_time_ms))}</p>
          {detail.run.end_reason && <p>{labT("endedReason", detail.run.end_reason)}</p>}
        </section>
        <VesselView descriptor={detail.descriptor} observation={detail.observation} action={selectedAction} />
        <ObservationView descriptor={detail.descriptor} observation={detail.observation} />
        <ActionForm key={detail.run.run_id} descriptor={detail.descriptor} disabled={detail.run.status !== "ready" || !!pending || state.stopping || !!state.querying} onSelect={(capability) => setAction(capability ? { runId: detail.run.run_id, capability } : undefined)} />
        <button class="lab-stop" type="button" disabled={state.stopping || ["ended", "failed"].includes(detail.run.status)} onClick={() => void lab.stop()}>{labT("end")}</button>
        <CommandHistory commands={detail.commands} observation={detail.observation} querying={state.querying} sequences={state.sequences} />
        <section class="lab-card"><h3>{labT("results")}</h3><p class="lab-muted">{labT("exportLater")}</p></section>
      </>}
    </>}
  </div>;
}
