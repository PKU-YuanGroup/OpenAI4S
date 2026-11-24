import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setLang } from "../../i18n";
import { _titleName, currentId, project, projects } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { running } from "../../stores/stream";
import { approvalPendingFor } from "../messages/approval";
import { bootDocumentTitle, documentTitle } from "./doc-title";

describe("SYNC-18 the browser tab names the open session", () => {
  let stop: (() => void) | null = null;
  const doc = { title: "OpenAI4S" };

  beforeEach(async () => {
    resetStoreFields();
    approvalPendingFor.value = null;
    doc.title = "OpenAI4S";
    await setLang("en");
    vi.stubGlobal("document", doc);
  });

  afterEach(() => {
    if (stop) stop();
    stop = null;
    vi.unstubAllGlobals();
  });

  it("is the session and its project, follows a rename and a running turn, and is plain at home", () => {
    projects.value = [{ project_id: "p-1", name: "Growth assay" }];
    project.value = "p-1";
    stop = bootDocumentTitle();
    expect(doc.title).toBe("OpenAI4S");
    currentId.value = "f-1";
    _titleName.value = "Growth Assay Data Analysis";
    expect(doc.title).toBe("Growth Assay Data Analysis · Growth assay — OpenAI4S");
    running.value = true;
    expect(doc.title).toBe("⏳ Growth Assay Data Analysis · Growth assay — OpenAI4S");
    _titleName.value = "Renamed";
    expect(doc.title).toBe("⏳ Renamed · Growth assay — OpenAI4S");
    running.value = false;
    currentId.value = null;
    expect(doc.title).toBe("OpenAI4S");
  });

  it("an approval wait shows its own prefix instead of the hourglass", () => {
    expect(
      documentTitle({ open: true, name: "Fit", project: "", running: true, awaiting: true }),
    ).toBe("(Approval needed) Fit — OpenAI4S");
    currentId.value = "f-2";
    _titleName.value = "Fit";
    running.value = true;
    stop = bootDocumentTitle();
    approvalPendingFor.value = "f-2";
    expect(doc.title).toBe("(Approval needed) Fit — OpenAI4S");
  });

  it("cuts a first message used as the name before the automatic title arrives (VIEW-4)", () => {
    const first =
      "Please analyse the RNA-seq counts in data/counts.csv: run QC, normalise, and test differential expression";
    expect(documentTitle({ open: true, name: first, project: "", running: false, awaiting: false })).toBe(
      "Please analyse the RNA-seq counts in dat… — OpenAI4S",
    );
    const zh = "请分析 data/counts.csv 中的 RNA-seq 计数矩阵：先做质控，再做标准化和差异表达分析，最后画火山图";
    expect(documentTitle({ open: true, name: zh, project: "", running: false, awaiting: false })).toBe(
      Array.from(zh).slice(0, 40).join("") + "… — OpenAI4S",
    );
  });
});

describe("the session title field is as wide as its words (VIEW-4)", () => {
  it("counts a Chinese character as two Latin columns", async () => {
    const { displayWidth, setTitle } = await import("./dom");
    expect(displayWidth("RNA-seq")).toBe(7);
    expect(displayWidth("询问身份与能力")).toBe(14);
    expect(displayWidth("RNA-seq质控与差异分析")).toBe(7 + 14);
    const input = { value: "", size: 0, removeAttribute: () => {} };
    vi.stubGlobal("document", { querySelector: (sel: string) => (sel === "#conv-title" ? input : null) });
    try {
      setTitle("询问身份与能力");
      expect(input.size).toBe(15);
      setTitle("RNA-seq质控与差异分析");
      expect(input.size).toBe(22);
      setTitle("x".repeat(200));
      expect(input.size).toBe(64);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
