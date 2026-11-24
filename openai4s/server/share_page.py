"""A session as one self-contained, read-only HTML file (EXP-3).

"Share (read-only link)" needs a relay somebody has to host. This is the
zero-configuration alternative: a single ``.html`` a researcher can attach to
an e-mail, which opens from ``file://`` and makes no network request.

It renders the share *view* document and nothing else: the redacted
projection the hosted share page already shows (`ShareProjectionBuilder.
serialize_view`), so there is one decision about what a shared session may
show and this file cannot disagree with it.

Three properties are the point, and each has a test:

* **Nothing executes and nothing loads.** No script, no external stylesheet,
  font or image, and a Content-Security-Policy ``<meta>`` that would refuse
  them anyway. Collapsing is ``<details>``.
* **Everything is text.** Every value from the session goes through
  ``html.escape``; the small Markdown subset below builds its few tags around
  escaped text and never passes a value through as markup.
* **Bounded.** Text and inlined images each have a budget; what a budget cuts
  is said at the top of the page instead of silently missing.
"""

from __future__ import annotations

import base64
import html
import re
from typing import Any, Iterable, Mapping

#: What the page may load: nothing but the images inlined in it.
CONTENT_SECURITY_POLICY = (
    "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    "base-uri 'none'; form-action 'none'"
)
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
}
#: The name `gateway._seed_example_project` gives the example project.
_SEEDED_EXAMPLE_PROJECT_NAME = "Example project"
#: One inlined image, all of them together, one text field, all text together.
MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_IMAGES_BYTES = 24 * 1024 * 1024
MAX_FIELD_CHARS = 200_000
MAX_TEXT_CHARS = 8 * 1024 * 1024

_COPY: dict[str, dict[str, str]] = {
    "en": {
        "untitled": "OpenAI4S session",
        "badge": "Read-only export",
        "about": "Exported from OpenAI4S. This file is a static copy: nothing in it "
        "runs, and it does not connect to the network.",
        "model": "Model",
        "project": "Project",
        "example_project": "Example project",
        "user": "You",
        "assistant": "Assistant",
        "cell": "Cell {0}",
        "failed": "failed",
        "output": "Output",
        "stderr": "Warnings and errors (stderr)",
        "error": "Error",
        "wrote": "Files written",
        "cut": "(cut here to keep this file small)",
        "artifacts": "Artifacts",
        "name": "Name",
        "type": "Type",
        "size": "Size",
        "no_artifacts": "This session has no artifacts.",
        "note_text": "Long text was cut in {0} place(s) to keep this file small.",
        "note_images": "{0} figure(s) are listed by name only: inlining them would "
        "pass this file's image budget.",
        "note_hidden": "{0} internal cell(s) are not shown.",
        "note_excluded": "{0} artifact(s) could not be included in a shared copy "
        "and are not listed.",
    },
    "zh": {
        "untitled": "OpenAI4S 会话",
        "badge": "只读导出",
        "about": "由 OpenAI4S 导出。这是一份静态副本：其中没有任何内容会运行，"
        "也不会连接网络。",
        "model": "模型",
        "project": "项目",
        "example_project": "示例项目",
        "user": "你",
        "assistant": "助手",
        "cell": "单元 {0}",
        "failed": "失败",
        "output": "输出",
        "stderr": "警告与错误（stderr）",
        "error": "错误",
        "wrote": "写出的文件",
        "cut": "（为控制文件大小，此处已截断）",
        "artifacts": "产物",
        "name": "名称",
        "type": "类型",
        "size": "大小",
        "no_artifacts": "这个会话没有产物。",
        "note_text": "为控制文件大小，有 {0} 处长文本被截断。",
        "note_images": "有 {0} 张图只列出了文件名：内嵌它们会超出本文件的图片预算。",
        "note_hidden": "有 {0} 个内部单元没有显示。",
        "note_excluded": "有 {0} 个产物无法放进共享副本，没有列出。",
    },
}

_STYLE = (
    "body{font:15px/1.6 system-ui,-apple-system,'Segoe UI',sans-serif;color:#1f1e1b;"
    "background:#faf9f5;margin:0}main{max-width:860px;margin:0 auto;padding:32px 20px}"
    "h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:32px 0 8px}"
    "h4{font-size:15px;margin:12px 0 4px}.meta,.about{color:#6c6a64;font-size:13px}"
    ".badge{display:inline-block;border:1px solid #c9c4b6;border-radius:999px;"
    "padding:0 8px;font-size:12px;margin-left:8px;vertical-align:middle}"
    ".note{background:#f1ecdf;border-radius:8px;padding:8px 14px;margin:16px 0;"
    "font-size:13px}.msg{margin:16px 0;padding:10px 14px;border-radius:10px}"
    ".user{background:#efe9de}.assistant{background:#fff;border:1px solid #e8e0d2}"
    ".who{font-size:12px;color:#6c6a64;margin-bottom:2px}"
    "details{margin:12px 0;border:1px solid #e8e0d2;border-radius:10px;background:#fff}"
    "summary{cursor:pointer;padding:8px 14px;font-size:13px;color:#3d3c38}"
    ".bad{color:#b3261e}.body{padding:0 14px 10px}.label{font-size:12px;color:#6c6a64;"
    "margin:8px 0 2px}pre{background:#f5f0e8;border-radius:8px;padding:10px 12px;"
    "overflow:auto;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;"
    "white-space:pre-wrap;word-break:break-word;margin:4px 0}"
    "code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:#f5f0e8;"
    "border-radius:4px;padding:0 3px}pre code{background:none;padding:0}"
    "figure{margin:10px 0}img{max-width:100%;height:auto;border-radius:6px}"
    "figcaption{font-size:12px;color:#6c6a64}table{border-collapse:collapse;width:100%;"
    "font-size:13px}td,th{text-align:left;padding:4px 10px 4px 0;"
    "border-bottom:1px solid #e8e0d2}ul{margin:4px 0;padding-left:22px}"
    "p{margin:6px 0}"
)

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*]\s+(.*)$")
_INLINE = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*|\[([^\]\n]+)\]\(([^)\s]+)\)")


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _inline(text: str) -> str:
    """Code spans, bold and links; every piece of ``text`` is escaped."""

    out: list[str] = []
    at = 0
    for match in _INLINE.finditer(text):
        out.append(_esc(text[at : match.start()]))
        code, bold, label, target = match.groups()
        if code is not None:
            out.append(f"<code>{_esc(code)}</code>")
        elif bold is not None:
            out.append(f"<strong>{_esc(bold)}</strong>")
        elif re.match(r"https?://", target):
            out.append(
                f'<a href="{_esc(target)}" rel="noopener noreferrer">{_esc(label)}</a>'
            )
        else:
            # A link into the daemon (an artifact, a view) leads nowhere from
            # a file; its text stays.
            out.append(_esc(label))
        at = match.end()
    out.append(_esc(text[at:]))
    return "".join(out)


def _prose(text: str) -> str:
    """The Markdown a chat message uses, as a few safe tags."""

    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    fence: list[str] | None = None

    def flush() -> None:
        if paragraph:
            blocks.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph.clear()
        if items:
            blocks.append(
                "<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>"
            )
            items.clear()

    for line in text.splitlines():
        if fence is not None:
            if _FENCE.match(line):
                blocks.append("<pre><code>" + _esc("\n".join(fence)) + "</code></pre>")
                fence = None
            else:
                fence.append(line)
            continue
        if _FENCE.match(line):
            flush()
            fence = []
            continue
        heading = _HEADING.match(line)
        bullet = _BULLET.match(line)
        if heading:
            flush()
            blocks.append(f"<h4>{_inline(heading.group(1))}</h4>")
        elif bullet:
            if paragraph:
                flush()
            items.append(_inline(bullet.group(1)))
        elif not line.strip():
            flush()
        else:
            if items:
                flush()
            paragraph.append(_inline(line))
    if fence is not None:  # an unclosed fence is still code
        blocks.append("<pre><code>" + _esc("\n".join(fence)) + "</code></pre>")
    flush()
    return "".join(blocks)


def _size(value: Any) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


class _Budget:
    """What is left of the page's text and image allowance, and what was cut."""

    def __init__(self) -> None:
        self.text = MAX_TEXT_CHARS
        self.images = MAX_IMAGES_BYTES
        self.text_cuts = 0
        self.image_cuts = 0

    def take(self, value: Any) -> tuple[str, bool]:
        text = str(value or "")
        allowed = min(len(text), MAX_FIELD_CHARS, self.text)
        self.text -= allowed
        if allowed < len(text):
            self.text_cuts += 1
            return text[:allowed], True
        return text, False

    def image(self, data: bytes | None) -> bool:
        if not data or len(data) > MAX_IMAGE_BYTES or len(data) > self.images:
            self.image_cuts += 1
            return False
        self.images -= len(data)
        return True


def render_page(
    view: Mapping[str, Any],
    images: Mapping[str, bytes],
    *,
    language: str = "en",
) -> bytes:
    """One HTML document for a share view; ``images`` maps a view artifact's
    ``artifact-data/<sha256>`` to its bytes."""

    words = _COPY["zh" if language == "zh" else "en"]
    budget = _Budget()

    def block(label: str, value: Any, css: str = "") -> str:
        text, cut = budget.take(value)
        if not text and not cut:
            return ""
        note = f'<div class="label">{_esc(words["cut"])}</div>' if cut else ""
        return (
            f'<div class="label">{_esc(label)}</div>'
            f"<pre{css}>{_esc(text)}</pre>{note}"
        )

    def message(row: Mapping[str, Any]) -> str:
        role = "user" if row.get("role") == "user" else "assistant"
        text, cut = budget.take(row.get("content"))
        note = f'<div class="label">{_esc(words["cut"])}</div>' if cut else ""
        return (
            f'<div class="msg {role}"><div class="who">{_esc(words[role])}</div>'
            f"{_prose(text)}{note}</div>"
        )

    def figure(ref: Mapping[str, Any]) -> str:
        name = str(ref.get("filename") or "")
        dot = name.rfind(".")
        mime = _IMAGE_TYPES.get(name[dot:].lower() if dot >= 0 else "")
        data = images.get(f"artifact-data/{ref.get('sha256')}")
        if mime is None or not budget.image(data):
            return f'<div class="label">{_esc(name)}</div>'
        encoded = base64.b64encode(data or b"").decode("ascii")
        return (
            f'<figure><img alt="{_esc(name)}" src="data:{mime};base64,{encoded}">'
            f"<figcaption>{_esc(name)}</figcaption></figure>"
        )

    def cell(row: Mapping[str, Any]) -> str:
        failed = bool(str(row.get("error") or "").strip()) or str(
            row.get("status") or ""
        ).lower() in {"error", "failed", "interrupted", "cancelled", "timeout"}
        title = words["cell"].format(_esc(row.get("cell_index") or "?"))
        title += " · " + _esc(row.get("language") or "python")
        if failed:
            title += f' · <span class="bad">{_esc(words["failed"])}</span>'
        written = ", ".join(str(item) for item in row.get("files_written") or ())
        body = (
            block("", row.get("source"))
            + block(words["output"], row.get("stdout"))
            + block(words["stderr"], row.get("stderr"))
            + block(words["error"], row.get("error"), ' class="bad"')
            + "".join(
                figure(ref)
                for ref in row.get("figure_refs") or ()
                if isinstance(ref, Mapping)
            )
            + (block(words["wrote"], written) if written else "")
        )
        return f'<details><summary>{title}</summary><div class="body">{body}</div></details>'

    def moment(row: Mapping[str, Any]) -> int:
        try:
            return int(row.get("created_at") or 0)
        except (TypeError, ValueError):
            return 0

    timeline: list[tuple[int, int, int, str, Mapping[str, Any]]] = []
    for index, row in enumerate(_rows(view.get("messages"))):
        timeline.append((moment(row), 0, index, "message", row))
    for index, row in enumerate(_rows(view.get("cells"))):
        timeline.append((moment(row), 1, index, "cell", row))
    timeline.sort(key=lambda item: item[:3])
    body = "".join(
        message(row) if kind == "message" else cell(row)
        for _at, _order, _index, kind, row in timeline
    )

    artifacts = list(_rows(view.get("artifacts")))
    if artifacts:
        listing = (
            f"<table><tr><th>{_esc(words['name'])}</th><th>{_esc(words['type'])}</th>"
            f"<th>{_esc(words['size'])}</th></tr>"
            + "".join(
                f"<tr><td>{_esc(row.get('filename'))}</td>"
                f"<td>{_esc(row.get('content_type') or '')}</td>"
                f"<td>{_esc(_size(row.get('size_bytes')))}</td></tr>"
                for row in artifacts
            )
            + "</table>"
        )
    else:
        listing = f'<p class="meta">{_esc(words["no_artifacts"])}</p>'

    session = view.get("session") if isinstance(view.get("session"), Mapping) else {}
    title = str(session.get("name") or session.get("task_summary") or "").strip()
    title = title or words["untitled"]
    # I18N-2: the project the daemon seeds on first boot, in the page's
    # language while it keeps the name it was seeded with (the workbench's
    # `displayProjectName`). The view carries the name only, not the id.
    project_name = session.get("project_name")
    if project_name == _SEEDED_EXAMPLE_PROJECT_NAME:
        project_name = words["example_project"]
    meta = " · ".join(
        f"{_esc(words[key])}: {_esc(value)}"
        for key, value in (
            ("project", project_name),
            ("model", session.get("model")),
        )
        if value
    )
    excluded = view.get("excluded") if isinstance(view.get("excluded"), Mapping) else {}
    left_out = sum(
        int(group.get("count") or 0)
        for group in excluded.get("artifacts_excluded") or ()
        if isinstance(group, Mapping)
    )
    notes = [
        words[key].format(count)
        for key, count in (
            ("note_text", budget.text_cuts),
            ("note_images", budget.image_cuts),
            ("note_hidden", int(view.get("hidden_cell_count") or 0)),
            ("note_excluded", left_out),
        )
        if count
    ]
    document = (
        "<!doctype html>\n"
        f'<html lang="{"zh" if language == "zh" else "en"}"><head>'
        '<meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" '
        f'content="{_esc(CONTENT_SECURITY_POLICY)}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="referrer" content="no-referrer">'
        f"<title>{_esc(title)}</title><style>{_STYLE}</style></head><body><main>"
        f'<h1>{_esc(title)}<span class="badge">{_esc(words["badge"])}</span></h1>'
        + (f'<div class="meta">{meta}</div>' if meta else "")
        + f'<div class="about">{_esc(words["about"])}</div>'
        + "".join(f'<div class="note">{_esc(note)}</div>' for note in notes)
        + body
        + f"<h2>{_esc(words['artifacts'])}</h2>{listing}"
        + "</main></body></html>\n"
    )
    return document.encode("utf-8")


def _rows(value: Any) -> Iterable[Mapping[str, Any]]:
    return [row for row in value or () if isinstance(row, Mapping)]


__all__ = [
    "CONTENT_SECURITY_POLICY",
    "MAX_FIELD_CHARS",
    "MAX_IMAGES_BYTES",
    "MAX_IMAGE_BYTES",
    "MAX_TEXT_CHARS",
    "render_page",
]
