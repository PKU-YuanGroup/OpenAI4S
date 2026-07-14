import DefaultTheme from "vitepress/theme";
import { h, nextTick, onMounted, watch } from "vue";
import { useData, useRoute } from "vitepress";
import type { Theme } from "vitepress";
import "./style.css";

const repository = "https://github.com/PKU-YuanGroup/OpenAI4S";
let configured = false;

async function renderMermaid() {
  if (typeof document === "undefined") return;
  await nextTick();
  const nodes = Array.from(
    document.querySelectorAll<HTMLElement>(".mermaid:not([data-processed])")
  );
  if (!nodes.length) return;
  const { default: mermaid } = await import("mermaid");
  if (!configured) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "base",
      themeVariables: {
        primaryColor: "#191713",
        primaryTextColor: "#f3ede3",
        primaryBorderColor: "#d9a24b",
        lineColor: "#7f8fa6",
        secondaryColor: "#101820",
        tertiaryColor: "#24201a",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
      }
    });
    configured = true;
  }
  await mermaid.run({ nodes });
}

const DocumentationLayout = {
  setup() {
    const { frontmatter, lang } = useData();
    return () =>
      h(DefaultTheme.Layout, null, {
        "doc-before": () => {
          const rawDate = frontmatter.value.last_verified;
          if (!rawDate) return null;
          const date = String(rawDate).slice(0, 10);
          const revision = String(frontmatter.value.verified_commit || "");
          const chinese = lang.value.toLowerCase().startsWith("zh");
          const prefix = chinese ? "代码核验" : "Code verified";
          const dateLabel = chinese ? "核验日期" : "verified";
          const children: Array<string | ReturnType<typeof h>> = [
            `${prefix} · ${dateLabel} ${date}`
          ];
          if (revision) {
            children.push(" · ");
            children.push(
              h(
                "a",
                {
                  href: `${repository}/commit/${revision}`,
                  rel: "noreferrer",
                  target: "_blank"
                },
                revision
              )
            );
          }
          return h("div", { class: "verification-meta" }, children);
        }
      });
  }
};

export default {
  extends: DefaultTheme,
  Layout: DocumentationLayout,
  setup() {
    const route = useRoute();
    onMounted(() => void renderMermaid());
    watch(
      () => route.path,
      () => void renderMermaid()
    );
  }
} satisfies Theme;
