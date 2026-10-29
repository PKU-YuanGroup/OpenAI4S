import { afterEach, describe, expect, it } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import {
  ApiError,
  ArtifactReadError,
  isApiStatus,
  isArtifactReadAborted,
  PREVIEW_READ_MAX_BYTES,
  PREVIEW_READ_TIMEOUT_MS,
  readArtifactPrefix,
  readArtifactBytes,
  fetchArtifactText,
  setArtifactsFetch,
  THUMB_READ_CONCURRENCY,
  THUMB_READ_MAX_BYTES,
} from "./api";

function encoded(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function streamed(
  chunks: Uint8Array[],
  hooks: {
    onCancel?: () => void;
    onPull?: () => void;
    neverClose?: boolean;
  } = {},
): ReadableStream<Uint8Array> {
  let index = 0;
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      hooks.onPull?.();
      if (hooks.neverClose) return;
      if (index < chunks.length) controller.enqueue(chunks[index++]!);
      else controller.close();
    },
    cancel() {
      hooks.onCancel?.();
    },
  });
}

function responseOf(
  chunks: Uint8Array[],
  init: ResponseInit = {},
  hooks: { onCancel?: () => void; onPull?: () => void; neverClose?: boolean } = {},
): Response {
  return new Response(streamed(chunks, hooks), init);
}

function bodyless(headers: Record<string, string>, text = ""): Response {
  return {
    ok: true,
    status: 200,
    body: null,
    headers: new Headers(headers),
    text: async () => text,
  } as unknown as Response;
}

afterEach(() => {
  setArtifactsFetch(null);
  resetStoreFields();
});

describe("frozen preview read numbers (F1)", () => {
  it("pins the four constants by name and value", () => {
    expect(PREVIEW_READ_MAX_BYTES).toBe(8_388_608);
    expect(THUMB_READ_MAX_BYTES).toBe(262_144);
    expect(THUMB_READ_CONCURRENCY).toBe(4);
    expect(PREVIEW_READ_TIMEOUT_MS).toBe(15_000);
  });
});

describe("fetchArtifactText: strict mode (F2)", () => {
  it("returns the complete body when it is exactly at the limit", async () => {
    const chunk = new Uint8Array(6).fill(65);
    setArtifactsFetch(async () => responseOf([chunk, chunk]));
    await expect(fetchArtifactText("/api/v1/artifacts/a", { maxBytes: 12 })).resolves.toBe(
      "AAAAAA".repeat(2),
    );
  });

  it("stops at the first byte over the budget with no length header and cancels the reader", async () => {
    let cancelled = false;
    const chunk = new Uint8Array(8).fill(66);
    setArtifactsFetch(
      async () =>
        new Response(
          streamed([chunk, chunk, chunk, chunk], {
            onCancel: () => {
              cancelled = true;
            },
          }),
        ),
    );
    const error = await fetchArtifactText("/api/v1/artifacts/a", { maxBytes: 12 }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(ArtifactReadError);
    expect((error as ArtifactReadError).kind).toBe("too_large");
    expect((error as ArtifactReadError).maxBytes).toBe(12);
    expect((error as ArtifactReadError).bytes).toBe(12);
    expect(cancelled).toBe(true);
  });

  it("rejects a body that forges a small Content-Length", async () => {
    const chunk = new Uint8Array(8).fill(67);
    setArtifactsFetch(
      async () =>
        new Response(streamed([chunk, chunk, chunk]), {
          headers: { "content-length": "8" },
        }),
    );
    const error = await fetchArtifactText("/api/v1/artifacts/a", { maxBytes: 12 }).catch(
      (e: unknown) => e,
    );
    expect((error as ArtifactReadError).kind).toBe("too_large");
  });

  it("rejects from an oversized Content-Length before reading the body", async () => {
    let readers = 0;
    let cancelled = false;
    setArtifactsFetch(
      async () =>
        ({
          ok: true,
          status: 200,
          headers: new Headers({ "content-length": "9000000" }),
          body: {
            cancel: async () => {
              cancelled = true;
            },
            getReader: () => {
              readers += 1;
              throw new Error("the body must not be read");
            },
          },
        }) as unknown as Response,
    );
    const error = await fetchArtifactText("/api/v1/artifacts/big").catch((e: unknown) => e);
    expect((error as ArtifactReadError).kind).toBe("too_large");
    expect((error as ArtifactReadError).maxBytes).toBe(PREVIEW_READ_MAX_BYTES);
    expect(readers).toBe(0);
    expect(cancelled).toBe(true);
  });

  it("throws ApiError with the HTTP status for a non-2xx response", async () => {
    setArtifactsFetch(async () => new Response("server error", { status: 500 }));
    const error = await fetchArtifactText("/api/v1/artifacts/e").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(500);
    expect(isApiStatus(error, 500)).toBe(true);
  });

  it("times out a stalled body and reports timeout", async () => {
    setArtifactsFetch(
      async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            pull() {
              /* never delivers, never closes */
            },
          }),
        ),
    );
    const error = await fetchArtifactText("/api/v1/artifacts/t", { timeoutMs: 25 }).catch(
      (e: unknown) => e,
    );
    expect((error as ArtifactReadError).kind).toBe("timeout");
    expect(isArtifactReadAborted(error)).toBe(false);
  });

  it("reports an external abort as aborted, not as a failed read", async () => {
    setArtifactsFetch(async (_url, init) => {
      const signal = init?.signal;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            signal?.addEventListener("abort", () => {
              controller.error(new DOMException("aborted", "AbortError"));
            });
          },
          pull() {
            /* waits for the abort */
          },
        }),
      );
    });
    const controller = new AbortController();
    const read = fetchArtifactText("/api/v1/artifacts/c", { signal: controller.signal });
    controller.abort();
    const error = await read.catch((e: unknown) => e);
    expect((error as ArtifactReadError).kind).toBe("aborted");
    expect(isArtifactReadAborted(error)).toBe(true);
  });
});

describe("fetchArtifactText: no-body fallback rule (F2)", () => {
  it("falls back to response.text() only for identity encoding with a small known length", async () => {
    setArtifactsFetch(async () => bodyless({ "content-length": "5" }, "hello"));
    await expect(fetchArtifactText("/api/v1/artifacts/n")).resolves.toBe("hello");
  });

  it("refuses a compressed body it cannot size while streaming", async () => {
    setArtifactsFetch(async () =>
      bodyless({ "content-length": "5", "content-encoding": "gzip" }, "hello"),
    );
    const error = await fetchArtifactText("/api/v1/artifacts/n").catch((e: unknown) => e);
    expect((error as ArtifactReadError).kind).toBe("too_large");
  });

  it("refuses a body whose size is not knowable at all", async () => {
    setArtifactsFetch(async () => bodyless({}, "hello"));
    const error = await fetchArtifactText("/api/v1/artifacts/n").catch((e: unknown) => e);
    expect((error as ArtifactReadError).kind).toBe("too_large");
  });
});

describe("readArtifactPrefix: prefix mode (F2)", () => {
  it("returns a complete prefix with truncated false below the budget", async () => {
    setArtifactsFetch(async () => responseOf([encoded("complete file")]));
    await expect(
      readArtifactPrefix("/api/v1/artifacts/p", { maxBytes: 100 }),
    ).resolves.toEqual({ text: "complete file", truncated: false });
  });

  it("stops exactly at the budget, cancels the reader and marks truncated", async () => {
    let cancelled = false;
    const chunk = encoded("0123456789");
    setArtifactsFetch(
      async () =>
        new Response(
          streamed([chunk, chunk], {
            onCancel: () => {
              cancelled = true;
            },
          }),
        ),
    );
    const read = await readArtifactPrefix("/api/v1/artifacts/p", { maxBytes: 10 });
    expect(read).toEqual({ text: "0123456789", truncated: true });
    expect(cancelled).toBe(true);
  });

  it("drops an incomplete trailing UTF-8 sequence instead of emitting a replacement character", async () => {
    const text = "AAAAAAAAA" + "é" + "Z";
    setArtifactsFetch(async () => responseOf([encoded(text)]));
    const read = await readArtifactPrefix("/api/v1/artifacts/p", { maxBytes: 10 });
    expect(read.truncated).toBe(true);
    expect(read.text).toBe("AAAAAAAAA");
    expect(read.text).not.toContain("\uFFFD");
  });

  it("returns every byte of a binary body and rejects a body past the budget", async () => {
    const raw = new Uint8Array([0xff, 0xfe, 0x00, 0x41]);
    setArtifactsFetch(async () => responseOf([raw]));
    await expect(readArtifactBytes("/api/v1/artifacts/b", { maxBytes: 4 })).resolves.toEqual(raw);
    setArtifactsFetch(async () => responseOf([raw]));
    const error = await readArtifactBytes("/api/v1/artifacts/b", { maxBytes: 3 }).catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(ArtifactReadError);
    expect((error as ArtifactReadError).kind).toBe("too_large");
  });

  it("decodes multi-byte characters that fit wholly inside the prefix", async () => {
    setArtifactsFetch(async () => responseOf([encoded("中文报告")]));
    // "中文报告" is 12 UTF-8 bytes; reading exactly to the budget keeps every
    // whole character and marks the read truncated.
    await expect(readArtifactPrefix("/api/v1/artifacts/p", { maxBytes: 12 })).resolves.toEqual({
      text: "中文报告",
      truncated: true,
    });
  });
});
