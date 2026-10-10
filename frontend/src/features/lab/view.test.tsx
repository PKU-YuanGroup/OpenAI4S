import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
const hooks = vi.hoisted(() => ({ values: [] as unknown[] }));
vi.mock("preact/hooks", () => ({ useState: (value: unknown) => [hooks.values.length ? hooks.values.shift() : value, () => undefined] }));
vi.mock("../artifacts/ui", () => ({ openViewer: vi.fn() }));
import { i18nReady, setLang } from "../../i18n/runtime";
import { openViewer } from "../artifacts/ui";
import { permActionLine } from "../send/permission";
import { labT } from "./copy";
import { lab } from "./state";
import { ActionForm, CommandHistory, DeviceSetup, LabPane, ObservationView, ResultsView, TerminalControls, VesselView } from "./view";
import { command, descriptor, detail, device, exported, observation, run } from "./fixtures";

type Node = { type: unknown; props: Record<string, unknown> };
function nodes(raw: unknown): Node[] {
  if (Array.isArray(raw)) return raw.flatMap(nodes);
  if (!raw || typeof raw !== "object") return [];
  const n = raw as Node;
  if (typeof n.type === "function") return nodes(n.type(n.props));
  return [n, ...nodes(n.props?.children)];
}
function text(raw: unknown): string {
  if (Array.isArray(raw)) return raw.map(text).join("");
  if (raw == null || typeof raw === "boolean") return "";
  if (typeof raw !== "object") return String(raw);
  const n = raw as Node;
  return typeof n.type === "function" ? text(n.type(n.props)) : text(n.props?.children);
}
const elements = (tree: unknown, type: string) => nodes(tree).filter((n) => n.type === type);
beforeAll(async () => { await i18nReady(); });
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => { hooks.values = []; vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe("Lab bench public view", () => {
  it("generates only manifest levels with explicit units and disables unavailable creation", () => {
    const form = ActionForm({ descriptor, disabled: false, onSelect: () => {} });
    const selects = elements(form, "select");
    expect(elements(selects[1], "option").map((n) => n.props.value)).toEqual(["200", "400", "600", "800", "1000"]);
    expect(text(selects[1])).toContain("200 mL");
    expect(text(form)).not.toContain("end_experiment");
    const setup = DeviceSetup({ devices: [{ ...device, available: false, availability_detail: "Run openai4s lab setup chemgymrl." }], disabled: false });
    expect(text(setup)).toContain("Run openai4s lab setup chemgymrl.");
    expect(elements(setup, "button")[0]!.props.disabled).toBe(true);
  });
  it("does not draw unavailable or unknown channels as zero, even if a payload carries a number", () => {
    const obs = observation(); obs.channels[0]!.quality = "unknown"; obs.channels[1]!.value = 91827;
    const observed = ObservationView({ descriptor, observation: obs });
    expect(text(observed)).toContain(labT("unavailable")); expect(text(observed)).not.toContain("91827");
    const vessels = VesselView({ descriptor, observation: obs });
    expect(elements(vessels, "rect")).toHaveLength(0);
    expect(text(vessels)).toContain(labT("unknown"));
    expect(text(vessels)).toContain(labT("composition"));
  });
  it("displays declared category strings and integers without relabeling them unknown", () => {
    for (const value of ["Target A", 2]) {
      const obs = observation(); obs.channels[2]!.value = value;
      const tree = ObservationView({ descriptor, observation: obs });
      const target = nodes(tree).find((n) => n.props.class === "lab-channel" && text(n).startsWith("targets"));
      expect(text(target)).toContain(String(value));
      expect(text(target)).not.toContain(labT("unknown"));
    }
  });
  it("uses resource axes, highlights the selected endpoints, and ignores undeclared channels", () => {
    const obs = observation();
    const tree = VesselView({ descriptor, observation: obs, action: descriptor.capabilities[0] });
    const figures = elements(tree, "figure");
    expect(elements(figures[0], "rect")[0]!.props.fill).toContain(String(.12 + .88 * .2));
    expect(figures[0]!.props.class).toContain("lab-highlight");
    obs.channels.push({ ...obs.channels[1]!, name: "undeclared", quality: "ok", value: 91827 });
    expect(text(ObservationView({ descriptor, observation: obs }))).not.toContain("91827");
  });
  it("draws vessel rows only when the first layers axis indexes resources", () => {
    const renamed = structuredClone(descriptor);
    renamed.observation_channels[0]!.axes = [{ name: "sample", labels: ["extraction_vessel", "beaker_1"] }, { name: "layer_px" }];
    const tree = VesselView({ descriptor: renamed, observation: observation() });
    expect(elements(tree, "rect")).toHaveLength(0);
    expect(elements(VesselView({ descriptor, observation: observation() }), "rect")).toHaveLength(8);
  });
  it("shows loading rather than an empty bench before the scope's first read", () => {
    lab.scope("unread-root", 1, true);
    const unread = text(LabPane());
    expect(unread).toContain(labT("loading"));
    expect(unread).not.toContain(labT("noDevices")); expect(unread).not.toContain(labT("noRuns"));
    lab.state.value = { ...lab.state.value, loaded: true };
    const loaded = text(LabPane());
    expect(loaded).toContain(labT("noDevices")); expect(loaded).not.toContain(labT("loading"));
  });
  it("summarizes a capability without a source or target as none, not unknown", () => {
    for (const request of [{ ...command().request, operation: "end_experiment", source: null, target: null, parameters: {} },
      { run_id: "labrun-one", operation: "mix_model", source: "extraction_vessel", expected_revision: 3 }]) {
      const line = permActionLine({ tool: "lab_execute", input: request }).text;
      expect(line).not.toContain(labT("unknown"));
      expect(line).toContain(request.source ? "extraction_vessel → —" : "— → —");
    }
  });
  it("shows rejection reasons and gives outcome_unknown an explicit command query button", () => {
    const query = vi.spyOn(lab, "reconcile").mockResolvedValue();
    const tree = CommandHistory({ commands: [command(), command({ command_id: "rejected", state: "rejected", error: "Allowed volume: 200, 400 mL" })], observation: null, querying: null });
    expect(text(tree)).toContain("Allowed volume: 200, 400 mL");
    expect(text(tree)).toContain(labT("unknownOutcome"));
    const button = elements(tree, "button")[0]!;
    expect(text(button)).toBe(labT("reconcile"));
    (button.props.onClick as () => void)(); expect(query).toHaveBeenCalledWith("labcmd-one");
  });
  it("uses confirmed observation sequences rather than receipt step counts", () => {
    const row = command({ state: "succeeded", observation_id: "older-observation", receipt: { applied: true, status: "succeeded", error: null, raw: { terminated: false, truncated: false }, end_reason: null, sim_time: 4, step_index: 9 } });
    const tree = CommandHistory({ commands: [row], observation: null, querying: null, sequences: { "older-observation": 4 } });
    expect(text(tree)).toContain(labT("observationSequence", 4));
    expect(text(tree)).not.toContain(labT("observationSequence", 9));
  });
  it("renders all three permission summaries without raw actions or truth fields", () => {
    const input = { ...command().request, seed: 0, device_id: device.device_id, profile: device.profiles[0], provider_action: "PRIVATE_ACTION", evaluation: "PRIVATE_EVALUATION", parameters: { volume: { value: 200, unit: "mL" }, reward: { value: 91827, unit: "dimensionless" } } };
    const execute = permActionLine({ tool: "lab_execute", input }).text;
    expect(execute).toContain("labrun-one"); expect(execute).toContain("beaker_1 → extraction_vessel");
    expect(execute).toContain("volume 200 mL"); expect(execute).toContain("expected_revision 0");
    expect(execute).not.toMatch(/PRIVATE_|91827/);
    expect(permActionLine({ tool: "lab_create", input }).text).toContain("seed 0");
    expect(permActionLine({ tool: "lab_stop", input }).text).toBe("labrun-one");
  });
  it("disables stepping in busy, quarantined and ended states while retaining an independent stop", () => {
    lab.scope("root", 1, true);
    for (const status of ["busy", "quarantined", "ended"] as const) {
      lab.state.value = { ...lab.state.value, loaded: true, detail: detail({ run: run({ status }) }) };
      const buttons = elements(LabPane(), "button");
      expect(buttons.find((b) => text(b) === labT("execute"))!.props.disabled).toBe(true);
      expect(buttons.find((b) => text(b) === labT("end"))!.props.disabled).toBe(true);
      expect(buttons.find((b) => text(b) === labT("stop"))!.props.disabled).toBe(status === "ended");
    }
  });
  it("repaints feature copy in both languages without changing confirmed data", async () => {
    lab.scope("root", 1, true); lab.state.value = { ...lab.state.value, loaded: true, detail: detail() };
    const confirmed = lab.state.value.detail;
    await setLang("zh"); expect(text(LabPane())).toContain("模型时间，非秒");
    await setLang("en"); expect(text(LabPane())).toContain("Model time, not seconds");
    expect(lab.state.value.detail).toBe(confirmed);
  });
});

describe("Lab results and safe playback", () => {
  const ready = () => {
    lab.scope("root", 1, true);
    lab.state.value = { ...lab.state.value, selectedRunId: "labrun-one", detail: detail(), loaded: true };
    return lab.state.value;
  };
  const click = (node: Node) => (node.props.onClick as () => void)();

  it("requires explicit ground-truth opt-in and displays the export pending state", () => {
    const state = ready(), exporting = vi.spyOn(lab, "exportRun").mockResolvedValue();
    let tree = ResultsView({ state });
    expect(elements(tree, "input")[0]?.props).toMatchObject({ type: "checkbox", checked: false, disabled: false });
    expect(text(tree)).toContain(labT("evaluationWarning"));
    click(elements(tree, "button")[0]!); expect(exporting).toHaveBeenLastCalledWith(false);
    hooks.values = [true]; tree = ResultsView({ state });
    click(elements(tree, "button")[0]!); expect(exporting).toHaveBeenLastCalledWith(true);
    tree = ResultsView({ state: { ...state, exported: { runId: "labrun-one", loading: true, error: "", result: null } } });
    expect(elements(tree, "button")[0]?.props.disabled).toBe(true);
    expect(elements(tree, "input")[0]?.props.disabled).toBe(true);
    expect(text(tree)).toContain(labT("exporting"));
  });

  it("pins every exported version in links and viewer calls and notes a truth download", () => {
    const state = ready();
    const result = exported({ include_evaluation: true });
    result.artifacts.push({ kind: "report", artifact_id: "report-artifact", version_id: "report-version", filename: "report.md", checksum: "report-checksum" });
    const tree = ResultsView({ state: { ...state, exported: { runId: "labrun-one", loading: false, error: "", result, truthDownloaded: true } } });
    expect(text(tree)).toContain(labT("exportedCounts", 2, 2)); expect(text(tree)).toContain(labT("groundTruthDownloaded"));
    const plain = ResultsView({ state: { ...state, exported: { runId: "labrun-one", loading: false, error: "", result: exported() } } });
    expect(text(plain)).not.toContain(labT("groundTruthDownloaded"));
    const links = elements(tree, "a"); expect(links).toHaveLength(2);
    for (const [index, link] of links.entries()) {
      const artifact = result.artifacts[index]!;
      expect(link.props.href).toBe(`?artifact=${artifact.artifact_id}&version_id=${artifact.version_id}`);
      const preventDefault = vi.fn();
      (link.props.onClick as (event: unknown) => void)({ button: 0, preventDefault });
      expect(preventDefault).toHaveBeenCalledOnce();
      expect(openViewer).toHaveBeenLastCalledWith({ id: artifact.artifact_id, version_id: artifact.version_id, filename: artifact.filename, root_frame_id: "root" });
    }
    expect(elements(ResultsView({ state: { ...state, selectedRunId: "other-run", exported: { runId: "labrun-one", loading: false, error: "", result } } }), "a")).toHaveLength(0);
  });

  it("renders recorded unknown commands without query controls and wires the slider to read-only selection", () => {
    const state = ready(), select = vi.spyOn(lab, "selectReplay").mockImplementation(() => {});
    const execute = vi.spyOn(lab, "execute").mockResolvedValue(), reconcile = vi.spyOn(lab, "reconcile").mockResolvedValue();
    const replay = { runId: "labrun-one", loading: false, error: "", entries: [{ command: null, observation: observation() }, { command: command(), observation: null }], index: 1 };
    const tree = ResultsView({ state: { ...state, replay } });
    expect(text(tree)).toContain(labT("replayReadOnly")); expect(text(tree)).toContain(labT("noObservation"));
    expect(elements(tree, "button").some((button) => text(button) === labT("reconcile"))).toBe(false);
    const slider = elements(tree, "input").find((input) => input.props.type === "range")!;
    expect(slider.props).toMatchObject({ min: "0", max: 1, value: 1, step: "1" });
    (slider.props.onInput as (event: unknown) => void)({ currentTarget: { value: "0" } });
    expect(select).toHaveBeenCalledExactlyOnceWith(0);
    expect(execute).not.toHaveBeenCalled(); expect(reconcile).not.toHaveBeenCalled();
  });

  it("requires different confirmations for terminal completion and safety stop, including cancellation", () => {
    const state = ready(), end = vi.spyOn(lab, "end").mockResolvedValue(), stop = vi.spyOn(lab, "stop").mockResolvedValue();
    const confirm = vi.fn().mockReturnValue(false); vi.stubGlobal("confirm", confirm);
    const buttons = elements(TerminalControls({ state }), "button");
    click(buttons[0]!); click(buttons[1]!);
    expect(confirm.mock.calls).toEqual([[labT("endConfirm")], [labT("stopConfirm")]]);
    expect(end).not.toHaveBeenCalled(); expect(stop).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);
    click(buttons[0]!); expect(end).toHaveBeenCalledOnce(); expect(stop).not.toHaveBeenCalled();
    click(buttons[1]!); expect(stop).toHaveBeenCalledOnce();
  });

  it("disables end during uncertainty or a pending request and disables both after termination or during stop", () => {
    const state = ready();
    const pending = { intent: { kind: "execute" as const, runId: "labrun-one", body: command().request }, sending: true, error: "" };
    for (const [patch, disabled] of [
      [{ pending }, [true, false]], [{ querying: "labcmd-one" }, [true, false]],
      [{ detail: detail({ commands: [command()] }) }, [true, false]],
      [{ stopping: true }, [true, true]],
      [{ detail: detail({ run: run({ status: "ended" }) }) }, [true, true]],
      [{ detail: detail({ run: run({ status: "failed" }) }) }, [true, true]],
      [{}, [false, false]],
    ] as const) {
      expect(elements(TerminalControls({ state: { ...state, ...patch } }), "button").map((button) => button.props.disabled)).toEqual(disabled);
    }
  });
});
