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
});
