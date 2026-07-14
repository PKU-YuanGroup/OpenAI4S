import { defineConfig } from "vitepress";

const github = "https://github.com/PKU-YuanGroup/OpenAI4S";
const editRef = process.env.OPENAI4S_DOCS_EDIT_REF || "main";

const englishSidebar = [
  {
    text: "Architecture",
    items: [
      { text: "Architecture at a glance", link: "/architecture" },
      { text: "System context", link: "/architecture/system-context" },
      { text: "Action routing & completion", link: "/architecture/action-routing" },
      { text: "Kernels & Host RPC", link: "/architecture/kernels-and-host-rpc" },
      { text: "Web runtime & concurrency", link: "/architecture/web-runtime" },
      { text: "Projections & persistence", link: "/architecture/projections-and-persistence" },
      { text: "Artifacts & provenance", link: "/architecture/artifacts-and-provenance" },
      { text: "Checkpoints & recovery", link: "/architecture/checkpoints-and-recovery" },
      { text: "Failure boundaries", link: "/architecture/failure-boundaries" }
    ]
  },
  {
    text: "Contributing",
    items: [
      { text: "Codebase map", link: "/contributing/codebase-map" },
      { text: "Extension model", link: "/backend-extension-guide" },
      { text: "Testing & validation", link: "/contributing/testing" },
      { text: "Release validation", link: "/release-validation" }
    ]
  },
  {
    text: "Operations",
    items: [
      { text: "Operations overview", link: "/operations/" },
      { text: "Deployment", link: "/operations/deployment" },
      { text: "Configuration", link: "/configuration" },
      { text: "Data, backup & restore", link: "/operations/data-management" },
      { text: "Security architecture", link: "/security" },
      { text: "Security hardening", link: "/operations/security-hardening" },
      { text: "Remote compute", link: "/compute" }
    ]
  },
  {
    text: "Reference",
    items: [
      { text: "Terminology", link: "/reference/terminology" },
      { text: "Implementation status", link: "/reference/implementation-status" },
      { text: "Documentation policy", link: "/reference/documentation-policy" },
      { text: "Web workbench", link: "/webapp" },
      { text: "Web API", link: "/webapp-api" },
      { text: "Skills", link: "/skills" },
      { text: "Jupyter adapter", link: "/jupyter" }
    ]
  }
];

const chineseSidebar = [
  {
    text: "架构",
    items: [
      { text: "架构总览", link: "/zh/architecture" },
      { text: "系统上下文", link: "/zh/architecture/system-context" },
      { text: "动作路由与完成语义", link: "/zh/architecture/action-routing" },
      { text: "内核与 Host RPC", link: "/zh/architecture/kernels-and-host-rpc" },
      { text: "Web 运行时与并发", link: "/zh/architecture/web-runtime" },
      { text: "投影与持久化", link: "/zh/architecture/projections-and-persistence" },
      { text: "产物与来源追踪", link: "/zh/architecture/artifacts-and-provenance" },
      { text: "检查点与恢复", link: "/zh/architecture/checkpoints-and-recovery" },
      { text: "故障边界", link: "/zh/architecture/failure-boundaries" }
    ]
  },
  {
    text: "贡献开发",
    items: [
      { text: "代码库地图", link: "/zh/contributing/codebase-map" },
      { text: "扩展模型", link: "/zh/backend-extension-guide" },
      { text: "测试与验证", link: "/zh/contributing/testing" },
      { text: "发布验证", link: "/zh/release-validation" }
    ]
  },
  {
    text: "部署与运维",
    items: [
      { text: "运维总览", link: "/zh/operations/" },
      { text: "部署", link: "/zh/operations/deployment" },
      { text: "配置", link: "/zh/configuration" },
      { text: "数据、备份与恢复", link: "/zh/operations/data-management" },
      { text: "安全架构", link: "/zh/security" },
      { text: "安全加固", link: "/zh/operations/security-hardening" },
      { text: "远程计算", link: "/zh/compute" }
    ]
  },
  {
    text: "参考",
    items: [
      { text: "术语表", link: "/zh/reference/terminology" },
      { text: "实现状态", link: "/zh/reference/implementation-status" },
      { text: "文档治理", link: "/zh/reference/documentation-policy" },
      { text: "Web 工作台", link: "/zh/webapp" },
      { text: "Web API", link: "/zh/webapp-api" },
      { text: "Skills", link: "/zh/skills" },
      { text: "Jupyter 适配器", link: "/zh/jupyter" }
    ]
  }
];

export default defineConfig({
  title: "OpenAI4S Documentation",
  description: "Architecture, contributor, and operations documentation for OpenAI4S.",
  base: "/docs/",
  srcExclude: [
    "backend-refactor-architecture.md",
    "package-architecture.md",
    "plan-corecoder-refactor.md",
    "refactor-plan.md"
  ],
  cleanUrls: true,
  // Git timestamps are misleading for an uncommitted or cherry-picked docs
  // release. The theme renders frontmatter.last_verified instead.
  lastUpdated: false,
  sitemap: {
    hostname: "https://openai4s.org/docs/"
  },
  head: [
    ["meta", { name: "theme-color", content: "#090909" }],
    ["meta", { property: "og:site_name", content: "OpenAI4S Documentation" }]
  ],
  locales: {
    root: {
      label: "English",
      lang: "en",
      title: "OpenAI4S Documentation",
      description: "Architecture, contributor, and operations documentation."
    },
    zh: {
      label: "简体中文",
      lang: "zh-CN",
      link: "/zh/",
      title: "OpenAI4S 文档",
      description: "OpenAI4S 架构、贡献开发与部署运维文档。",
      themeConfig: {
        siteTitle: "OpenAI4S 文档",
        search: {
          provider: "local",
          options: {
            translations: {
              button: {
                buttonText: "搜索",
                buttonAriaLabel: "搜索文档"
              },
              modal: {
                displayDetails: "显示详情",
                resetButtonTitle: "清除查询",
                backButtonTitle: "关闭搜索",
                noResultsText: "未找到相关结果",
                footer: {
                  selectText: "选择",
                  selectKeyAriaLabel: "回车",
                  navigateText: "切换",
                  navigateUpKeyAriaLabel: "向上",
                  navigateDownKeyAriaLabel: "向下",
                  closeText: "关闭",
                  closeKeyAriaLabel: "退出"
                }
              }
            }
          }
        },
        socialLinks: [{ icon: "github", link: github }],
        nav: [
          { text: "架构", link: "/zh/architecture" },
          { text: "贡献开发", link: "/zh/contributing/codebase-map" },
          { text: "部署运维", link: "/zh/operations/" },
          { text: "实现状态", link: "/zh/reference/implementation-status" }
        ],
        sidebar: chineseSidebar,
        outline: { label: "本页目录", level: [2, 3] },
        docFooter: { prev: "上一页", next: "下一页" },
        lastUpdated: { text: "最后更新" },
        editLink: {
          pattern: `${github}/edit/${editRef}/docs/:path`,
          text: "在 GitHub 上编辑此页"
        },
        darkModeSwitchLabel: "外观",
        lightModeSwitchTitle: "切换到浅色主题",
        darkModeSwitchTitle: "切换到深色主题",
        sidebarMenuLabel: "目录",
        returnToTopLabel: "返回顶部",
        langMenuLabel: "切换语言",
        skipToContentLabel: "跳转到正文",
        footer: {
          message: "文档以代码和测试为依据；状态标签用于区分契约与 best-effort 行为。",
          copyright: "OpenAI4S · MIT"
        }
      }
    }
  },
  markdown: {
    lineNumbers: true,
    config(md) {
      const fallback = md.renderer.rules.fence!;
      md.renderer.rules.fence = (tokens, index, options, env, self) => {
        const token = tokens[index];
        if (token.info.trim() === "mermaid") {
          const source = md.utils.escapeHtml(token.content);
          return `<div class="mermaid" role="img">${source}</div>`;
        }
        return fallback(tokens, index, options, env, self);
      };
    }
  },
  themeConfig: {
    siteTitle: "OpenAI4S Docs",
    search: {
      provider: "local"
    },
    socialLinks: [{ icon: "github", link: github }],
    nav: [
      { text: "Architecture", link: "/architecture" },
      { text: "Contributing", link: "/contributing/codebase-map" },
      { text: "Operations", link: "/operations/" },
      { text: "Status", link: "/reference/implementation-status" }
    ],
    sidebar: englishSidebar,
    outline: { label: "On this page", level: [2, 3] },
    docFooter: { prev: "Previous", next: "Next" },
    lastUpdated: { text: "Last updated" },
    editLink: {
      pattern: `${github}/edit/${editRef}/docs/:path`,
      text: "Edit this page on GitHub"
    },
    footer: {
      message: "Documentation is verified against code and tests; status labels distinguish contracts from best-effort behavior.",
      copyright: "OpenAI4S · MIT"
    }
  }
});
