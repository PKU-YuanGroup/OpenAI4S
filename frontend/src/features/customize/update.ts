/**
 * U2 copy table (PLAN §10 item 3): feature-local, because `i18n/en.ts` /
 * `i18n/zh.ts` are generated from the frozen legacy `app.js` and must not be
 * hand-edited (F18). The update card is the only reader.
 */
import { copyLookup, type CopyTable } from "../../i18n/copy";

const COPY: CopyTable = {
  en: {
    "update.title": "Version and updates",
    "update.desc":
      "This version only provides checking and guidance. It never downloads, installs, restarts or upgrades anything in place, and it never checks by itself.",
    "update.only": "This version only provides checking and guidance.",
    "update.current": "Current version",
    "update.channel": "Install channel",
    "update.latest": "Latest known version",
    "update.checkedAt": "Last checked",
    "update.cache": "Cache",
    "update.cachedAgo": "from the local cache ({0} ago)",
    "update.noCache": "not from a cache",
    "update.notChecked": "Not checked yet",
    "update.check": "Check for updates",
    "update.force": "Force a fresh check",
    "update.checking": "Checking…",
    "update.available": "A newer version is available: {0}",
    "update.upToDate": "This is the latest version",
    "update.unknown": "Could not confirm whether a newer version exists",
    "update.reason": "Reason: {0}",
    "update.previous": "Last known {0} (checked at {1})",
    "update.trust.two_witnesses":
      "The two independent sources agree on the digest",
    "update.trust.single_witness": "Only one source could be read",
    "update.trust.none": "Integrity could not be established",
    "update.authorship": "Authorship is not verified",
    "update.adminOnly":
      "Only an administrator can check for updates on this instance",
    "update.rateLimited": "Too many checks; try again in {0}",
    "update.seconds": "{0}s",
    "update.timedOut":
      "The check timed out, so whether a newer version exists is unknown",
    "update.checkFailed": "The check failed: {0}",
    "update.readFailed": "Could not read the local update state: {0}",
    "update.malformed": "The daemon did not answer with the expected update shape",
    "update.guidance": "Installation instructions",
    "update.guidanceBody":
      "Installation and upgrades are performed by this installation's maintenance channel. This card only reports what the configured release index says; it does not download, install or restart anything.",
    "update.manualStep": "Manual step for this installation",
    "update.copy": "Copy",
    "update.copied": "Copied",
    "update.refusal":
      "This installation is not updated from the browser; follow the manual step below.",
    "update.unknownCode": "reason code: {0}",
    "update.reason.ok": "the release index answered",
    "update.reason.offline_pin": "the daemon is pinned offline by configuration",
    "update.reason.network_disabled": "network access is disabled for this daemon",
    "update.reason.egress_blocked": "the daemon's egress policy blocked the request",
    "update.reason.ssrf_blocked": "the request was blocked by the SSRF guard",
    "update.reason.unreachable": "the release index could not be reached",
    "update.reason.timeout": "the release index did not answer in time",
    "update.reason.http_error": "the release index returned an error",
    "update.reason.too_large": "the release index answered with too much data",
    "update.reason.malformed": "the release index answer could not be read",
    "update.reason.yanked": "the published release was withdrawn",
    "update.reason.witness_disagreement": "the two release witnesses disagreed",
    "update.reason.witness_missing": "only one release witness could be read",
    "update.reason.bad_index": "the configured release index is invalid",
    "update.reason.unparsable_running": "the running version could not be parsed",
    "update.reason.unknown": "the daemon could not determine an answer",
    "update.channel.venv": "Python environment (venv)",
    "update.channel.linux-bundle": "Linux bundle",
    "update.channel.wsl-bundle": "WSL bundle",
    "update.channel.source": "Source checkout",
    "update.channel.macos-app": "macOS app",
    "update.channel.container": "Container image",
    "update.channel.unknown": "Unknown channel",
    "update.channelGuide.venv":
      "Upgrade through the Python environment that owns this install (for example pip/uv in that venv), then restart the daemon yourself.",
    "update.channelGuide.linux-bundle":
      "Follow the Linux bundle's documented maintenance process; this card never replaces the installed tree.",
    "update.channelGuide.wsl-bundle":
      "Follow the WSL bundle's documented maintenance process; this card never replaces the installed tree.",
    "update.channelGuide.source":
      "Update the source checkout and run ./setup.sh as usual.",
    "update.channelGuide.macos-app":
      "Install a new signed app build; the running app is never replaced from here.",
    "update.channelGuide.container":
      "Pull and deploy a new image; the running container is discarded on restart.",
    "update.channelGuide.unknown":
      "Follow the maintenance procedure for this installation; the daemon could not identify its channel.",
  },
  zh: {
    "update.title": "版本与更新",
    "update.desc":
      "本版本只提供检查与指引。它不会下载、安装、重启或在原位升级任何东西，也不会自行检查。",
    "update.only": "本版本只提供检查与指引。",
    "update.current": "当前版本",
    "update.channel": "安装渠道",
    "update.latest": "最近已知版本",
    "update.checkedAt": "检查时间",
    "update.cache": "缓存",
    "update.cachedAgo": "来自本地缓存（{0}前）",
    "update.noCache": "非缓存",
    "update.notChecked": "尚未检查",
    "update.check": "检查更新",
    "update.force": "强制重新检查",
    "update.checking": "正在检查…",
    "update.available": "有新版本可用：{0}",
    "update.upToDate": "已是最新版本",
    "update.unknown": "无法确认是否有新版本",
    "update.reason": "原因：{0}",
    "update.previous": "上次已知 {0}（检查于 {1}）",
    "update.trust.two_witnesses": "两个独立来源的摘要一致",
    "update.trust.single_witness": "仅一个来源可读",
    "update.trust.none": "无法建立完整性",
    "update.authorship": "作者身份未验证",
    "update.adminOnly": "本实例仅管理员可检查更新",
    "update.rateLimited": "检查过于频繁；请在 {0} 后重试",
    "update.seconds": "{0} 秒",
    "update.timedOut": "检查超时，因此无法确认是否有新版本",
    "update.checkFailed": "检查失败：{0}",
    "update.readFailed": "无法读取本地更新状态：{0}",
    "update.malformed": "daemon 未返回预期的更新形状",
    "update.guidance": "安装说明",
    "update.guidanceBody":
      "安装与升级由本安装的维护渠道执行。本卡片只报告所配置发布索引的说法；不下载、不安装、不重启任何东西。",
    "update.manualStep": "本安装的人工步骤",
    "update.copy": "复制",
    "update.copied": "已复制",
    "update.refusal": "本安装不从浏览器更新；请按下方人工步骤操作。",
    "update.unknownCode": "原因代码：{0}",
    "update.reason.ok": "发布索引已答复",
    "update.reason.offline_pin": "该 daemon 被配置为离线固定",
    "update.reason.network_disabled": "该 daemon 禁用了网络",
    "update.reason.egress_blocked": "该 daemon 的外联策略阻止了请求",
    "update.reason.ssrf_blocked": "请求被 SSRF 防护阻止",
    "update.reason.unreachable": "无法联系发布索引",
    "update.reason.timeout": "发布索引未及时答复",
    "update.reason.http_error": "发布索引返回错误",
    "update.reason.too_large": "发布索引答复超出大小限制",
    "update.reason.malformed": "发布索引答复无法解析",
    "update.reason.yanked": "已发布的版本被撤回",
    "update.reason.witness_disagreement": "两个发布见证不一致",
    "update.reason.witness_missing": "只能读取到一个发布见证",
    "update.reason.bad_index": "配置的发布索引无效",
    "update.reason.unparsable_running": "无法解析当前运行的版本",
    "update.reason.unknown": "daemon 无法给出答案",
    "update.channel.venv": "Python 环境（venv）",
    "update.channel.linux-bundle": "Linux 安装包",
    "update.channel.wsl-bundle": "WSL 安装包",
    "update.channel.source": "源码检出",
    "update.channel.macos-app": "macOS 应用",
    "update.channel.container": "容器镜像",
    "update.channel.unknown": "未知渠道",
    "update.channelGuide.venv":
      "请通过拥有此安装的 Python 环境升级（例如该 venv 中的 pip/uv），然后自行重启 daemon。",
    "update.channelGuide.linux-bundle":
      "按 Linux 安装包文档中的维护流程操作；本卡片不会替换已安装目录。",
    "update.channelGuide.wsl-bundle":
      "按 WSL 安装包文档中的维护流程操作；本卡片不会替换已安装目录。",
    "update.channelGuide.source": "更新源码检出，并照常运行 ./setup.sh。",
    "update.channelGuide.macos-app":
      "请安装新的已签名应用包；此处绝不会替换正在运行的应用。",
    "update.channelGuide.container":
      "拉取并部署新镜像；运行中的容器会在重启时丢弃。",
    "update.channelGuide.unknown":
      "请按本安装的维护流程操作；daemon 无法识别自己的渠道。",
  },
};

export const updateT = copyLookup(COPY);
