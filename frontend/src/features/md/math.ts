/**
 * Lazy TeX typesetting for the `.md-math` elements `renderMd(…, { math: true })`
 * leaves behind (ART-2).
 *
 * KaTeX, its stylesheet and its fonts are one separate chunk, fetched the first
 * time a document actually contains math, so no other surface pays for them.
 * The element holds the escaped TeX source until then, which is also what stays
 * on screen if the chunk cannot load.
 *
 * `trust: false` keeps `\href`, `\url`, `\includegraphics` and the like inert,
 * `maxExpand`/`maxSize` bound macro bombs, and `throwOnError: false` renders a
 * malformed formula as its red source instead of failing the whole document.
 */

export type KatexApi = {
  render: (tex: string, element: HTMLElement, options: Record<string, unknown>) => void;
};

export const KATEX_OPTIONS: Readonly<Record<string, unknown>> = Object.freeze({
  throwOnError: false,
  trust: false,
  strict: "ignore",
  output: "htmlAndMathml",
  maxSize: 50,
  maxExpand: 1000,
});

let loading: Promise<KatexApi> | null = null;

/** The KaTeX chunk plus its stylesheet, loaded once; a failed load is retried next time. */
export function loadKatex(): Promise<KatexApi> {
  if (!loading) {
    loading = Promise.all([import("katex"), import("katex/dist/katex.min.css")])
      .then(([mod]) => {
        const api = ((mod as { default?: unknown }).default ?? mod) as KatexApi;
        if (!api || typeof api.render !== "function") throw new Error("KaTeX did not load");
        return api;
      })
      .catch((error: unknown) => {
        loading = null;
        throw error;
      });
  }
  return loading;
}

/**
 * Typeset every not-yet-rendered `.md-math` under `root`. Resolves to how many
 * formulas were rendered; never rejects. A formula KaTeX refuses keeps its
 * source, marked `md-math-raw`.
 */
export async function typesetMath(
  root: ParentNode,
  load: () => Promise<KatexApi> = loadKatex,
): Promise<number> {
  if (!root || typeof root.querySelectorAll !== "function") return 0;
  const nodes = Array.from(root.querySelectorAll<HTMLElement>(".md-math:not(.md-math-done)"));
  if (!nodes.length) return 0;
  let katex: KatexApi;
  try {
    katex = await load();
  } catch {
    nodes.forEach((node) => node.classList.add("md-math-raw"));
    return 0;
  }
  let rendered = 0;
  for (const node of nodes) {
    // The view may have been replaced while the chunk was loading.
    if (node.isConnected === false) continue;
    const tex = node.textContent || "";
    try {
      katex.render(tex, node, {
        ...KATEX_OPTIONS,
        displayMode: node.getAttribute("data-display") === "1",
      });
      node.classList.add("md-math-done");
      rendered++;
    } catch {
      node.textContent = tex;
      node.classList.add("md-math-raw");
    }
  }
  return rendered;
}
