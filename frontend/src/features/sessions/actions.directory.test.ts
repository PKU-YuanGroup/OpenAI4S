/**
 * Session-menu actions keep the sidebar directory truthful: a folder made by
 * "New folder and move" is listed, and deleting the open session never
 * reopens it, even when the list refresh after the delete fails.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./api", () => ({ api: vi.fn(), apiErrorText: String, API: "/api/v1", ApiError: Error }));
vi.mock("./chrome", () => ({ hint: vi.fn(), openMenu: vi.fn(), ensureActivateKeys: vi.fn() }));
vi.mock("./conversation", () => ({ openConversation: vi.fn(), resumeWatch: vi.fn() }));

import { t } from "../../i18n";
import { _foldersFor, _sessionScope, currentId, folders, project, sessions } from "../../stores/session";
import { resetStoreFields } from "../../stores/signal-field";
import { deleteSession, moveToFolderAt } from "./actions";
import { api } from "./api";
import { openMenu, type MenuItem } from "./chrome";
import { openConversation } from "./conversation";

beforeEach(() => {
  resetStoreFields();
  vi.mocked(api).mockReset();
  vi.mocked(openMenu).mockReset();
  vi.mocked(openConversation).mockReset();
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

describe("deleting the open session while the list refresh fails", () => {
  function deleteWithFailedRefresh(rows: Array<{ id: string; project_id: string }>) {
    project.value = "P";
    _sessionScope.value = "P";
    currentId.value = "f";
    sessions.value = rows;
    vi.mocked(api).mockImplementation(async (path: string, init?: RequestInit) => {
      if (path === "/frames/f" && init?.method === "DELETE") return { ok: true };
      throw new Error("daemon restarting");
    });
    return deleteSession("f");
  }

  it("opens another session, never the one it just deleted", async () => {
    await deleteWithFailedRefresh([{ id: "f", project_id: "P" }, { id: "g", project_id: "P" }]);
    expect(openConversation).toHaveBeenCalledExactlyOnceWith("g", "P");
    expect((sessions.value as Array<{ id: string }>).map((row) => row.id)).toEqual(["g"]);
  });

  it("clears the conversation when the deleted session was the only one", async () => {
    await deleteWithFailedRefresh([{ id: "f", project_id: "P" }]);
    expect(openConversation).not.toHaveBeenCalled();
    expect(currentId.value).toBeNull();
  });
});
