import { afterEach, describe, expect, it } from "vitest";
import { resetStoreFields } from "../../stores/signal-field";
import {
  ApiError,
  ArtifactReadError,
  PREVIEW_MAX_BYTES,
  THUMB_MAX_BYTES,
  THUMB_MAX_CONCURRENCY,
  THUMB_READ_TIMEOUT_MS,
  fetchArtifactText,
  readArtifactText,
  setArtifactsFetch,
} from "./api";

function encoded(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

function streamed(
  chunks: Uint8Array[],
  hooks: { onCancel?: () => void; onPull?: () => void; neverClose?: boolean } = {},
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

function responseOf(chunks: Uint8Array[], init: ResponseInit = {}): Response {
  return new Response(streamed(chunks), init);
}

afterEach(() => {
  setArtifactsFetch(null);
  resetStoreFields();
});

describe("frozen preview read budgets (PLAN §2.4)", () => {
  it("keeps the frozen numbers", () => {
    expect(PREVIEW_MAX_BYTES).toBe(8 * 1024 * 1024);
    expect(THUMB_MAX_BYTES).toBe(256 * 1024);
    expect(THUMB_MAX_CONCURRENCY).toBe(4);
    expect(THUMB_READ_TIMEOUT_MS).toBe(15_000);
  });
});

describe("readArtifactText budget (R1)", () => {
  it("reads a complete body under the budget and decodes multibyte text across chunks", async () => {
    const whole = "中文🙂ok — naïve\r\nsecond line";
    const bytes = encoded(whole);
    // Split inside a 4-byte emoji and a 3-byte Chinese character.
    const chunks = [bytes.subarray(0, 2), bytes.subarray(2, 7), bytes.subarray(7)];
    setArtifactsFetch(async () => responseOf(chunks));
    const read = await readArtifactText("/api/v1/artifacts/versions/v1", { maxBytes: 1024 });
    expect(read.text).toBe(whole);
    expect(read.bytes).toBe(bytes.byteLength);
    expect(read.truncated).toBe(false);
  });

  it("stops at the byte budget across chunks, cancels the reader, and returns no body", async () => {
    let cancelled = false;
    const chunk = new Uint8Array(8).fill(65);
    setArtifactsFetch(
      async () =>
        new Response(streamed([chunk, chunk, chunk, chunk, chunk], { onCancel: () => { cancelled = true; } })),
    );
    const error = await readArtifactText("/api/v1/artifacts/a", { maxBytes: 12 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ArtifactReadError);
    expect((error as ArtifactReadError).code).toBe("too-large");
    expect((error as ArtifactReadError).bytes).toBe(12);
    expect(cancelled).toBe(true);
    // Nothing was handed to a parser: the caller only ever sees the rejection
    // or an explicitly allowed prefix, never a silently short document.
    await expect(readArtifactText("/api/v1/artifacts/a", { maxBytes: 12 })).rejects.toBeInstanceOf(
      ArtifactReadError,
    );
  });

  it("cannot be bypassed by a missing or forged content length", async () => {
    const body = { onCancel: undefined } as { onCancel?: () => void };
    const chunk = new Uint8Array(8).fill(66);
    setArtifactsFetch(
      async () =>
        new Response(streamed([chunk, chunk, chunk, chunk], body), {
          // A compressed or lying response understates the decoded body.
          headers: { "content-length": "8", "content-encoding": "gzip" },
        }),
    );
    const error = await readArtifactText("/api/v1/artifacts/b", { maxBytes: 20 }).catch((e: unknown) => e);
    expect((error as ArtifactReadError).code).toBe("too-large");
  });

  it("rejects early from trusted metadata before any byte is read", async () => {
    let reads = 0;
    setArtifactsFetch(async () => {
      reads += 1;
      return new Response("never read");
    });
    const error = await readArtifactText("/api/v1/artifacts/big", {
      maxBytes: 1024,
      declaredBytes: 2048,
    }).catch((e: unknown) => e);
    expect((error as ArtifactReadError).code).toBe("too-large");
    expect(reads).toBe(0);
  });

  it("checks the HTTP status before reading the body", async () => {
    let readers = 0;
    setArtifactsFetch(
      async () =>
        ({
          ok: false,
          status: 500,
          body: {
            getReader: () => {
              readers += 1;
              throw new Error("body must not be read for an error status");
            },
          },
        }) as unknown as Response,
    );
    const error = await readArtifactText("/api/v1/artifacts/e", { maxBytes: 1024 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(500);
    expect(readers).toBe(0);
  });

  it("refuses automatic preview when the size cannot be proven bounded", async () => {
    setArtifactsFetch(async () => ({ ok: true, body: null }) as unknown as Response);
    const error = await readArtifactText("/api/v1/artifacts/u").catch((e: unknown) => e);
    expect((error as ArtifactReadError).code).toBe("unbounded");
  });

  it("classifies a read cancelled by its owner", async () => {
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
    const read = readArtifactText("/api/v1/artifacts/c", { signal: controller.signal });
    controller.abort();
    const error = await read.catch((e: unknown) => e);
    expect((error as ArtifactReadError).code).toBe("cancelled");
  });

  it("classifies a read that outlives its deadline", async () => {
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
    const error = await readArtifactText("/api/v1/artifacts/t", { timeoutMs: 25 }).catch(
      (e: unknown) => e,
    );
    expect((error as ArtifactReadError).code).toBe("timeout");
  });

  it("classifies a transport failure", async () => {
    setArtifactsFetch(async () => {
      throw new TypeError("network down");
    });
    const error = await readArtifactText("/api/v1/artifacts/f").catch((e: unknown) => e);
    expect((error as ArtifactReadError).code).toBe("read-failed");
  });

  it("keeps an over-budget prefix only when the caller allows a partial preview", async () => {
    const chunk = encoded("x".repeat(20));
    setArtifactsFetch(async () => responseOf([chunk]));
    const read = await readArtifactText("/api/v1/artifacts/p", {
      maxBytes: 10,
      allowPartial: true,
    });
    expect(read.truncated).toBe(true);
    expect(read.text).toBe("x".repeat(10));
    const strict = await readArtifactText("/api/v1/artifacts/p", { maxBytes: 10 }).catch(
      (e: unknown) => e,
    );
    expect((strict as ArtifactReadError).code).toBe("too-large");
  });

  it("keeps fetchArtifactText complete-only under the 8 MiB main preview budget", async () => {
    const text = "small complete file\n";
    setArtifactsFetch(async () => responseOf([encoded(text)]));
    await expect(fetchArtifactText("/api/v1/artifacts/s")).resolves.toBe(text);
  });
});
