/**
 * Minimal fetch Response stand-in so cache/index tests do not need jsdom.
 *
 * It carries a real `body` stream as well as `text()`/`json()`: artifact byte
 * reads enforce their budget while streaming, so a stub without a body would
 * be indistinguishable from a transport that cannot prove any size bound
 * (which the preview refuses on purpose).
 */
export function jsonResponse(body: unknown, status = 200): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  const encoded = new TextEncoder().encode(text);
  return {
    ok: status >= 200 && status < 300,
    status,
    body: new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoded);
        controller.close();
      },
    }),
    text: async () => text,
    json: async () => (typeof body === "string" ? JSON.parse(body) : body),
    headers: { get: () => "application/json" },
  } as unknown as Response;
}
