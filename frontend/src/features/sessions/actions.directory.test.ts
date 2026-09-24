/**
 * Session-menu actions keep the sidebar directory truthful: a folder made by
 * "New folder and move" is listed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String, API: "/api/v1", ApiError: Error }));
vi.mock("./chrome", () => ({ hint: vi.fn(), openMenu: vi.fn(), ensureActivateKeys: vi.fn() }));
vi.mock("./conversation", () => ({ openConversation: vi.fn(), resumeWatch: vi.fn() }));

import { t } from "../../i18n";
import { _foldersFor, _sessionScope, folders, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { moveToFolderAt } from "./actions";
import { api } from "./api";
import { openMenu, type MenuItem } from "./chrome";

beforeEach(() => {
  resetStoreFields();
  vi.mocked(api).mockReset();
  vi.mocked(openMenu).mockReset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("New folder and move", () => {
  it("lists the new folder instead of answering the refresh from the cached folders", async () => {
    // The sidebar has already read project P: its sessions and its folders.
    project.value = "P";
    _sessionScope.value = "P";
    sessions.value = [{ id: "f", project_id: "P" }];
    folders.value = [{ folder_id: "old", name: "Old" }];
    _foldersFor.value = "P";
    let serverFolders = [{ folder_id: "old", name: "Old" }];
    vi.mocked(api).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/projects/P/folders" && init?.method === "POST") {
        serverFolders = [...serverFolders, { folder_id: "new", name: "Lab" }];
        return { folder_id: "new" };
      }
      if (path === "/projects/P/folders") return { folders: serverFolders };
      if (path === "/frames/f/folder") return { ok: true };
      if (path.startsWith("/frames?")) return { frames: [{ id: "f", project_id: "P", folder_id: "new" }], has_more: false };
      throw new Error(`unexpected request: ${path}`);
    });
    vi.stubGlobal("prompt", () => "Lab");

    moveToFolderAt({} as Element, "f");
    const items = vi.mocked(openMenu).mock.calls[0]![1] as MenuItem[];
    const create = items.find((item) => item.label === t("moveFolder.newFolderAndMove"));
    await create!.onClick!();

    expect((folders.value as Array<{ folder_id: string }>).map((row) => row.folder_id)).toEqual(["old", "new"]);
  });
});
