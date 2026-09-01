/** Minimal fetch Response stand-in so cache/index tests do not need jsdom. */
export function jsonResponse(body: unknown, status = 200): Response {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    json: async () => (typeof body === "string" ? JSON.parse(body) : body),
    headers: { get: () => "application/json" },
  } as unknown as Response;
}
