import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defaultModel, defaultModelName, models } from "../../stores/customize";
import { resetStoreFields } from "../../stores/signal-field";
import { loadModels } from "../../features/customize/models";
import { ModelSelect } from "./ModelSelect";

type Node = { type?: unknown; props?: Record<string, unknown> & { children?: unknown } };
const fetchMock = vi.fn();
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function options(tree: Node): Node[] {
  const kids = tree.props?.children;
  return (Array.isArray(kids) ? kids : [kids]).filter(
    (node): node is Node => !!node && typeof node === "object" && (node as Node).type === "option",
  );
}

beforeEach(() => {
  resetStoreFields();
  fetchMock.mockReset().mockImplementation((url: string, init?: RequestInit) => {
    if (url === "/api/v1/models") {
      return Promise.resolve(
        response({
          models: {
            default: [
              { id: "doubao-seed-2.0-pro", name: "doubao-seed-2.0-pro" },
              { id: "mp-claude", name: "Claude", model: "claude-sonnet-4-5" },
            ],
          },
          default_model_id: "doubao-seed-2.0-pro",
        }),
      );
    }
    if (url === "/api/v1/models/default" && init?.method === "PUT") {
      return Promise.resolve(response({ default_model_id: "mp-claude" }));
    }
    return Promise.resolve(response({}, 404));
  });
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  resetStoreFields();
});

describe("#model-select", () => {
  it("renders one option per configured model, the default selected", async () => {
    await loadModels();
    const tree = ModelSelect() as Node;
    expect(tree.type).toBe("select");
    expect(tree.props?.id).toBe("model-select");
    expect(options(tree).map((node) => node.props?.value)).toEqual(["doubao-seed-2.0-pro", "mp-claude"]);
    expect(tree.props?.value).toBe("doubao-seed-2.0-pro");
  });

  it("renders a single empty option before anything is configured", () => {
    const tree = ModelSelect() as Node;
    expect(options(tree)).toHaveLength(1);
    expect(options(tree)[0]?.props?.value).toBe("");
  });

  it("choosing an entry sets it as the server default and names it by model", async () => {
    await loadModels();
    const tree = ModelSelect() as Node;
    const onChange = tree.props?.onChange as (event: unknown) => void;
    onChange({ currentTarget: { value: "mp-claude" } });
    await vi.waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/models/default",
        expect.objectContaining({ method: "PUT", body: JSON.stringify({ model_id: "mp-claude" }) }),
      ),
    );
    expect(defaultModel.value).toBe("mp-claude");
    expect(defaultModelName.value).toBe("claude-sonnet-4-5");
    expect((ModelSelect() as Node).props?.value).toBe("mp-claude");
  });

  it("a refused choice puts the previous selection back", async () => {
    await loadModels();
    fetchMock.mockImplementation(() => Promise.resolve(response({ error: "no" }, 409)));
    const onChange = (ModelSelect() as Node).props?.onChange as (event: unknown) => void;
    onChange({ currentTarget: { value: "mp-claude" } });
    await vi.waitFor(() => expect(defaultModel.value).toBe("doubao-seed-2.0-pro"));
    expect(defaultModelName.value).toBe("doubao-seed-2.0-pro");
    expect((models.value as unknown[]).length).toBe(2);
  });
});
