"""How the built-in example session ends.

The example is seeded by running cells through the notebook REPL, not by a
turn, so nothing closed it the way a turn is closed: its summary went to the
store only (a page that was watching never received it) and the session was
recorded ``done`` whatever had happened in it.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Iterable, Mapping

from openai4s.server.urls import completion_artifact_url

#: Which file each material of the example is, by the cell that writes it.
#: The figure's name carries its cell number, so it is matched by suffix.
_MATERIAL_FILES: dict[str, Callable[[str], bool]] = {
    "figure": lambda name: name.endswith(".png"),
    "table": lambda name: name == "family_biochemistry.csv",
    "structure": lambda name: name == "nif3_structure.pdb",
    "report": lambda name: name == "nif3_report.md",
}

#: The example's own words, in the two UI languages (CHAT-7: the prompt and
#: the summary were English only).
_COPY: dict[str, dict[str, str]] = {
    "en": {
        "prompt": "Analyse the NIF3/DUF34 protein family using real data only: pull "
        "family records and sequences from the UniProt REST API, compute "
        "per-protein biochemistry (MW, pI, GRAVY) and pairwise sequence "
        "identity, plot a Kyte-Doolittle hydropathy profile, fetch a "
        "representative 3D structure from the RCSB PDB API, and write a "
        "short reproducible report.",
        "task_summary": "Pull NIF3/DUF34 family records + sequences from the UniProt "
        "REST API, compute real per-protein biochemistry and pairwise "
        "identity with a Kyte-Doolittle hydropathy "
        "profile, fetch a representative structure from the RCSB PDB "
        "API, and write a reproducible report — every value real.",
        "done": "Done — every value in this session is computed from real data "
        "(no simulated or placeholder values).",
        "cells_failed": "{failed} of {total} example cells did not complete on this "
        "install:",
        "modules": "\nMissing Python module(s): {names}. The example uses the science "
        "extra (numpy, pandas, matplotlib). From a source checkout run `uv sync "
        "--extra science` (or `./setup.sh`); for a pip install run `pip install "
        '"openai4s[science]"`. Restart OpenAI4S afterwards so new kernels pick '
        "them up.\n",
        "partial": "\nEvery value that **was** produced is computed from real data "
        "(no simulated or placeholder values), and the materials below "
        "list only what was actually produced.",
        "steps_one": "1 step failed along the way, so what depended on it is "
        "missing below:",
        "steps_many": "{count} steps failed along the way, so what depended on them "
        "is missing below:",
        "inputs": "**Real inputs**\n"
        "- **UniProt REST API** — NIF3/DUF34 family records + sequences\n"
        "- **RCSB PDB API** — full-text search + coordinate download of a "
        "representative structure\n"
        "- **MCP connector `example`** — `calc` / `now` tools over the "
        "Connectors bridge",
        "materials": "**Materials — click any artifact to view**",
        "figure": "hydropathy figure (PNG): Kyte-Doolittle profile of the "
        "reference sequence",
        "table": "per-protein length / MW / pI / GRAVY / % identity",
        "structure": "real RCSB structure (opens in the 3Dmol viewer)",
        "report": "reproducible summary with data provenance",
        "figure_label": "hydropathy figure",
        "table_label": "biochemistry table",
        "report_label": "summary report",
        "missing": "- _{label}_ — not produced this run (Cell {cell} did not "
        "complete; see its error in the Notebook)",
        "structure_missing": "- _3D structure_ — skipped this run: the RCSB download "
        "was unreachable (no placeholder is ever substituted; see nif3_report.md)",
        # SCI-02: plain words, not bold that reads as a link. The Notebook is
        # the panel above the message box, not a tab.
        "closing": "The executed cells are in the Notebook above the message box; "
        "every material is also in the Files panel.",
    },
    "zh": {
        "prompt": "只用真实数据分析 NIF3/DUF34 蛋白家族：从 UniProt REST API 获取家族"
        "记录和序列，计算每个蛋白的理化性质（分子量、等电点、GRAVY）和两两序列"
        "一致性，绘制 Kyte-Doolittle 疏水性曲线，从 RCSB PDB API 获取一个代表性"
        "三维结构，并写一份简短、可复现的报告。",
        "task_summary": "从 UniProt REST API 获取 NIF3/DUF34 家族记录和序列，计算真实"
        "的蛋白理化性质和两两一致性，绘制 Kyte-Doolittle 疏水性曲线，从 RCSB PDB "
        "API 获取代表性结构，并写出可复现的报告：每个数值都来自真实数据。",
        "done": "已完成：本会话里的每个数值都由真实数据计算得到（没有模拟值或占位值）。",
        "cells_failed": "{total} 个示例单元中有 {failed} 个在这台机器上没有完成：",
        "modules": "\n缺少 Python 模块：{names}。示例需要 science 附加依赖（numpy、"
        "pandas、matplotlib）。源码安装请运行 `uv sync --extra science`（或 "
        '`./setup.sh`）；pip 安装请运行 `pip install "openai4s[science]"`。之后重启 '
        "OpenAI4S，新的内核才会用上它们。\n",
        "partial": "\n**已经**得到的每个数值都由真实数据计算（没有模拟值或占位值），"
        "下面只列出实际生成的产物。",
        "steps_one": "过程中有 1 个步骤失败，依赖它的内容在下面缺失：",
        "steps_many": "过程中有 {count} 个步骤失败，依赖它们的内容在下面缺失：",
        "inputs": "**真实输入**\n"
        "- **UniProt REST API**：NIF3/DUF34 家族记录和序列\n"
        "- **RCSB PDB API**：全文检索，并下载一个代表性结构的坐标\n"
        "- **MCP 连接器 `example`**：通过连接器调用 `calc` / `now` 工具",
        "materials": "**产物（点击即可查看）**",
        "figure": "疏水性图（PNG）：参考序列的 Kyte-Doolittle 曲线",
        "table": "每个蛋白的长度 / 分子量 / 等电点 / GRAVY / 一致性百分比",
        "structure": "来自 RCSB 的真实结构（在 3D 查看器中打开）",
        "report": "带数据来源说明的可复现总结",
        "figure_label": "疏水性图",
        "table_label": "理化性质表",
        "report_label": "总结报告",
        "missing": "- _{label}_：本次没有生成（单元 {cell} 没有完成，报错见 "
        "Notebook）",
        "structure_missing": "- _三维结构_：本次跳过，RCSB 下载不可达（不会用占位结构"
        "代替，详见 nif3_report.md）",
        "closing": "已执行的单元在输入框上方的 Notebook 里；每个产物也都在文件面板中。",
    },
}


def copy(language: str, key: str) -> str:
    """One of the example's strings in ``language`` (``zh``, else English)."""

    return _COPY["zh" if language == "zh" else "en"][key]


def _link(artifact: Mapping[str, Any], *, trusted_delivery: bool) -> str:
    """The same Markdown link a normal completion gives an artifact."""

    name = str(artifact.get("filename") or "artifact")
    label = name.replace("[", "\\[").replace("]", "\\]")
    target = completion_artifact_url(
        artifact_id=artifact.get("artifact_id") or artifact.get("id"),
        filename=name,
        version_id=artifact.get("version_id") or artifact.get("latest_version_id"),
        trusted_delivery=trusted_delivery,
    )
    return f"[{label}]({target})" if target else f"**{label}**"


def summary_text(
    *,
    language: str,
    materials: Iterable[str | None],
    artifacts: Iterable[Mapping[str, Any]],
    cell_failures: list[str],
    step_failures: list[str],
    trusted_delivery: bool = False,
) -> str:
    """The example's closing message, reconciled against what really ran.

    ``materials`` is the plan in cell order: for each cell the material it
    writes, or None. A produced material is a real link (the summary said
    "click any artifact to view" over bold text); a missing one says which
    cell did not write it.
    """

    plan = list(materials)
    rows = [row for row in artifacts if isinstance(row, Mapping)]
    lines: list[str] = []
    for cell, material in enumerate(plan, start=1):
        if material is None:
            continue
        made = next(
            (
                row
                for row in rows
                if _MATERIAL_FILES[material](str(row.get("filename") or ""))
            ),
            None,
        )
        if made is not None:
            link = _link(made, trusted_delivery=trusted_delivery)
            lines.append(f"- {link} — {copy(language, material)}")
        elif material == "structure":
            lines.append(copy(language, "structure_missing"))
        else:
            label = copy(language, material + "_label")
            lines.append(copy(language, "missing").format(label=label, cell=cell))

    if cell_failures:
        modules = sorted(
            {
                match.group(1).split(".")[0]
                for failure in cell_failures
                for match in [re.search(r"No module named '([^']+)'", failure)]
                if match
            }
        )
        header = (
            copy(language, "cells_failed").format(
                failed=len(cell_failures), total=len(plan)
            )
            + "\n"
            + "".join(f"- {failure}\n" for failure in cell_failures)
            + (
                copy(language, "modules").format(
                    names=", ".join(f"`{name}`" for name in modules)
                )
                if modules
                else ""
            )
            + copy(language, "partial")
        )
    else:
        header = copy(language, "done")
    parts = [header]
    if step_failures:
        note = (
            copy(language, "steps_one")
            if len(step_failures) == 1
            else copy(language, "steps_many").format(count=len(step_failures))
        )
        parts.append(note + "\n" + "\n".join(f"- {line}" for line in step_failures))
    parts.append(copy(language, "inputs"))
    parts.append(copy(language, "materials") + "\n" + "\n".join(lines))
    parts.append(copy(language, "closing"))
    return "\n\n".join(parts)


def failed_steps(store: Any, frame_id: str) -> list[str]:
    """One line per step that failed, including one its cell recovered from.

    A cell that catches a failed fetch and carries on finishes without an
    error, so the cell results alone cannot say that a data source was
    unreachable. The step record can.
    """

    lines: list[str] = []
    for step in store.list_steps(frame_id):
        if step.get("status") != "error":
            continue
        title = " ".join(str(step.get("title") or "step").split())
        reason = " ".join(str(step.get("summary") or "").split())
        reason = reason.removeprefix("failed:").removeprefix("failed").strip()
        lines.append(f"{title}: {reason[:200]}" if reason else title)
    return lines


def finish(
    *,
    store: Any,
    emit: Callable[[dict], None],
    frame_id: str,
    summary: str,
    failed_cells: int,
) -> str:
    """Publish the summary and close the session the way a turn does.

    The same two events a turn ends with -- the final text as a
    ``text_chunk``, then the terminal ``frame_update`` -- so an open page
    shows the summary without a reload. Returns the terminal status.
    """

    status = "failed" if failed_cells else "completed"
    emit(
        {
            "type": "text_chunk",
            "frame_id": frame_id,
            "block_type": "text",
            "chunk": summary + "\n",
        }
    )
    store.add_message(
        root_frame_id=frame_id, role="assistant", frame_id=frame_id, content=summary
    )
    store.update_frame(frame_id, status="failed" if failed_cells else "done")
    emit({"type": "frame_update", "frame_id": frame_id, "status": status})
    return status


__all__ = ["copy", "failed_steps", "finish", "summary_text"]
