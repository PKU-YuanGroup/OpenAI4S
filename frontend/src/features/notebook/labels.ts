/**
 * Runtime-segment display helpers. app.js:10063-10075.
 * Notebook groups by raw kernel_id; the chip label is "Python" / "Python — struct".
 */

export function kernelLabel(k: string | null | undefined): string {
  k = k || "python";
  return k.replace(/^python\b/i, "Python");
}

/** Stored kernel_id from a kernel-status env object. app.js:10067-10075. */
export function kernelIdFromEnv(env: { kernel_id?: string; name?: string } | null | undefined): string {
  if (env && typeof env.kernel_id === "string" && env.kernel_id) return env.kernel_id;
  const n = ((env && env.name) || "").trim();
  if (!n || n === "python" || n === "base") return "python";
  return "python — " + n;
}
