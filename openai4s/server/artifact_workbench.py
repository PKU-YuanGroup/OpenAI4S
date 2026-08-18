"""Stage 9 Artifact workbench: tables, diffs, locators, and Ketcher.

The official workbench is opt-in through ``stage9_artifact_workbench``. Flag-off
behaviour is unchanged: Ketcher stays the historical placeholder, tables stay
client-capped, and annotations stay image pins.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
from collections.abc import Mapping, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

KETCHER_VERSION = "3.7.0"
KETCHER_VENDOR = Path(__file__).resolve().parent / "webui" / "vendor" / "ketcher"
ANNOTATION_KINDS = frozenset({"image", "pdf", "html"})
_SMILES_ATOM = re.compile(r"Br|Cl|[A-Z][a-z]?")


class WorkbenchError(Exception):
    def __init__(self, status: int, message: str, code: str = "workbench_error"):
        super().__init__(message)
        self.status = status
        self.message = message
        self.code = code


def official_workbench_enabled(config: Any) -> bool:
    flags = getattr(config, "roadmap_features", None)
    return bool(
        flags is not None and getattr(flags, "stage9_artifact_workbench", False)
    )


def require_workbench(config: Any) -> None:
    if not official_workbench_enabled(config):
        raise WorkbenchError(
            403,
            "artifact workbench is disabled",
            "workbench_disabled",
        )


def parse_delimited(text: str, filename: str = "") -> list[list[str]]:
    sample = text[:4096]
    name = str(filename or "").lower()
    dialect = csv.excel_tab if name.endswith(".tsv") else csv.excel
    if "\t" in sample and sample.count("\t") > sample.count(","):
        dialect = csv.excel_tab
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    return [list(row) for row in reader]


def infer_column_type(values: Sequence[str]) -> str:
    nonempty = [item.strip() for item in values if str(item).strip() != ""]
    if not nonempty:
        return "text"
    if all(re.fullmatch(r"[+-]?\d+", item) for item in nonempty):
        return "integer"
    if all(
        re.fullmatch(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", item)
        for item in nonempty
    ):
        return "number"
    return "text"


def _coerce(value: str, kind: str) -> Any:
    text = str(value)
    if kind == "integer":
        try:
            return int(text)
        except ValueError:
            return text
    if kind == "number":
        try:
            return float(text)
        except ValueError:
            return text
    return text.lower()


def query_table(
    rows: Sequence[Sequence[str]],
    *,
    sort: str = "",
    descending: bool = False,
    filters: Mapping[str, str] | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    if not rows:
        return {
            "columns": [],
            "column_types": [],
            "rows": [],
            "total_rows": 0,
            "offset": 0,
            "limit": limit,
            "sorted_by": None,
            "filters": {},
        }
    header = [str(name or f"col_{index}") for index, name in enumerate(rows[0])]
    body = [list(row) + [""] * max(0, len(header) - len(row)) for row in rows[1:]]
    types = [
        infer_column_type([row[index] if index < len(row) else "" for row in body])
        for index in range(len(header))
    ]
    filtered = body
    applied: dict[str, str] = {}
    for name, needle in dict(filters or {}).items():
        if name not in header or needle == "":
            continue
        index = header.index(name)
        applied[name] = str(needle)
        hay = str(needle).lower()
        kind = types[index]
        if kind in {"integer", "number"}:
            filtered = [
                row
                for row in filtered
                if str(row[index] if index < len(row) else "").strip()
                == str(needle).strip()
            ]
        else:
            filtered = [
                row
                for row in filtered
                if hay in str(row[index] if index < len(row) else "").lower()
            ]
    sort_name = sort if sort in header else None
    if sort_name:
        index = header.index(sort_name)
        kind = types[index]
        filtered = sorted(
            filtered,
            key=lambda row: _coerce(row[index] if index < len(row) else "", kind),
            reverse=bool(descending),
        )
    start = max(0, int(offset))
    size = max(1, min(int(limit), 500))
    page = filtered[start : start + size]
    return {
        "columns": header,
        "column_types": types,
        "rows": page,
        "total_rows": len(filtered),
        "offset": start,
        "limit": size,
        "sorted_by": sort_name,
        "descending": bool(descending) if sort_name else False,
        "filters": applied,
    }


def read_parquet_rows(path: Path) -> list[list[str]]:
    try:
        import pyarrow.parquet as parquet  # type: ignore[import-not-found]
    except ImportError as error:
        raise WorkbenchError(
            415,
            "parquet requires pyarrow from the science extra",
            "parquet_unavailable",
        ) from error
    table = parquet.read_table(path)
    header = [str(name) for name in table.column_names]
    rows = [header]
    for index in range(table.num_rows):
        rows.append(
            [
                "" if value is None else str(value)
                for value in table.slice(index, 1).to_pylist()[0].values()
            ]
        )
    return rows


def unified_diff(old: str, new: str, *, from_label: str, to_label: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
    )


def parse_molfile(text: str) -> dict[str, Any] | None:
    first = str(text or "").split("$$$$", 1)[0]
    lines = first.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) < 4 or "V3000" in (lines[3] or "").upper():
        return None
    counts = lines[3]
    try:
        atom_count = int(counts[0:3])
        bond_count = int(counts[3:6])
    except ValueError:
        pieces = counts.split()
        if len(pieces) < 2:
            return None
        atom_count, bond_count = int(pieces[0]), int(pieces[1])
    if atom_count < 1 or atom_count > 2000:
        return None
    atoms: list[dict[str, Any]] = []
    for index in range(atom_count):
        line = lines[4 + index] if 4 + index < len(lines) else ""
        pieces = line.split()
        element = (line[31:34].strip() if len(line) >= 34 else "") or (
            pieces[3] if len(pieces) > 3 else "C"
        )
        element = re.sub(r"[^A-Za-z]", "", element)[:3] or "C"
        atoms.append({"element": element})
    bonds: list[dict[str, Any]] = []
    for index in range(bond_count):
        line = (
            lines[4 + atom_count + index] if 4 + atom_count + index < len(lines) else ""
        )
        pieces = line.split()
        try:
            left = int(line[0:3] if len(line) >= 3 else pieces[0]) - 1
            right = int(line[3:6] if len(line) >= 6 else pieces[1]) - 1
            order = int(line[6:9] if len(line) >= 9 else pieces[2])
        except (ValueError, IndexError):
            continue
        if 0 <= left < len(atoms) and 0 <= right < len(atoms):
            bonds.append({"a": left, "b": right, "order": order})
    return {"atoms": atoms, "bonds": bonds, "title": (lines[0] or "Molecule").strip()}


def smiles_carbon_count(text: str) -> int:
    return sum(1 for token in _SMILES_ATOM.findall(text) if token == "C")


def structure_summary(content: str, filename: str = "") -> dict[str, Any]:
    name = str(filename or "").lower()
    if name.endswith((".smi", ".smiles")) or (
        "\n" not in content and re.search(r"[cC]\d*", content)
    ):
        line = content.strip().splitlines()[0] if content.strip() else ""
        smiles = line.split()[0] if line else ""
        carbons = smiles_carbon_count(smiles)
        return {
            "format": "smiles",
            "smiles": smiles,
            "carbon_count": carbons,
            "bond_count": None,
            "atoms": [],
        }
    parsed = parse_molfile(content)
    if parsed is None:
        raise WorkbenchError(400, "unrecognized structure file", "invalid_structure")
    carbons = sum(1 for atom in parsed["atoms"] if atom["element"].upper() == "C")
    return {
        "format": "mol",
        "carbon_count": carbons,
        "bond_count": len(parsed["bonds"]),
        "atoms": parsed["atoms"],
        "bonds": parsed["bonds"],
        "title": parsed.get("title") or "",
    }


def is_benzene(summary: Mapping[str, Any]) -> bool:
    if summary.get("format") == "smiles":
        compact = re.sub(r"\s+", "", str(summary.get("smiles") or ""))
        return compact in {"c1ccccc1", "C1=CC=CC=C1", "c1ccccc1"}
    if int(summary.get("carbon_count") or 0) != 6:
        return False
    bonds = list(summary.get("bonds") or [])
    if len(bonds) < 6:
        return False
    graph: dict[int, list[int]] = {}
    for bond in bonds:
        graph.setdefault(int(bond["a"]), []).append(int(bond["b"]))
        graph.setdefault(int(bond["b"]), []).append(int(bond["a"]))
    carbons = [
        index
        for index, atom in enumerate(summary.get("atoms") or [])
        if str(atom.get("element") or "").upper() == "C"
    ]
    return len(carbons) == 6 and all(
        len(graph.get(index, [])) >= 2 for index in carbons
    )


def extract_pdf_text(data: bytes) -> list[dict[str, Any]]:
    if not data.startswith(b"%PDF"):
        raise WorkbenchError(415, "not a PDF", "not_pdf")
    decoded = data.decode("latin-1", "replace")
    chunks: list[str] = []
    for match in re.finditer(r"\((?:\\.|[^\\)])*\)\s*Tj", decoded):
        raw = match.group(0)[1 : match.group(0).rfind(")")]
        raw = (
            raw.replace("\\(", "(")
            .replace("\\)", ")")
            .replace("\\n", "\n")
            .replace("\\\\", "\\")
        )
        if raw.strip():
            chunks.append(raw)
    for match in re.finditer(r"\[((?:\s*\((?:\\.|[^\\)])*\)\s*)+)\]\s*TJ", decoded):
        parts = re.findall(r"\((?:\\.|[^\\)])*\)", match.group(1))
        text = "".join(
            part[1:-1].replace("\\(", "(").replace("\\)", ")") for part in parts
        )
        if text.strip():
            chunks.append(text)
    joined = " ".join(chunks)
    return [{"page": 1, "index": 0, "text": joined[:20_000]}]


class _OutlineParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.outline: list[dict[str, str]] = []
        self._stack: list[str] = []
        self._skip = 0
        self._buf = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
            return
        mapping = {name: value or "" for name, value in attrs}
        ident = mapping.get("id")
        selector = f"#{ident}" if ident else tag
        if ident:
            path = selector
        else:
            path = ">".join([*self._stack, tag]) if self._stack else tag
        self._stack.append(tag)
        self._buf = ""
        self.outline.append(
            {
                "tag": tag,
                "selector": path[:240],
                "id": ident or "",
                "text": "",
            }
        )

    def handle_data(self, data: str) -> None:
        if self._skip or not self.outline:
            return
        text = " ".join(data.split())
        if text and not self.outline[-1]["text"]:
            self.outline[-1]["text"] = text[:240]

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1
            return
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()


def html_outline(text: str) -> list[dict[str, str]]:
    parser = _OutlineParser()
    parser.feed(text)
    parser.close()
    return [
        item
        for item in parser.outline
        if item["tag"] not in {"html", "head", "meta", "link"}
    ][:200]


def normalize_locator(kind: str, locator: Any) -> dict[str, Any]:
    if kind not in ANNOTATION_KINDS:
        raise WorkbenchError(400, "kind must be image, pdf, or html", "invalid_kind")
    data = dict(locator or {}) if isinstance(locator, Mapping) else {}
    if kind == "image":
        return {
            "rel_x": float(data.get("rel_x") or data.get("x") or 0),
            "rel_y": float(data.get("rel_y") or data.get("y") or 0),
        }
    if kind == "pdf":
        quote = str(data.get("quote") or data.get("text") or "").strip()
        if not quote:
            raise WorkbenchError(400, "pdf locator requires quote", "invalid_locator")
        return {
            "page": int(data.get("page") or 1),
            "start": int(data.get("start") or 0),
            "end": int(data.get("end") or 0),
            "quote": quote[:2000],
        }
    selector = str(data.get("selector") or "").strip()
    quote = str(data.get("quote") or data.get("text") or "").strip()
    if not selector and not quote:
        raise WorkbenchError(
            400, "html locator requires selector or quote", "invalid_locator"
        )
    return {"selector": selector[:500], "quote": quote[:2000]}


def format_located_annotations(annos: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "【Workbench 标注反馈】用户在 Artifact 的精确位置写下了意见。",
        "请按 version 与 locator 修改对应文件，不要改到别的版本。",
    ]
    for item in annos:
        kind = str(item.get("kind") or "image")
        locator = item.get("locator") or {}
        if isinstance(locator, str):
            try:
                locator = json.loads(locator)
            except json.JSONDecodeError:
                locator = {}
        name = item.get("artifact_name") or item.get("artifact_id") or "artifact"
        version = item.get("version_id") or ""
        if kind == "pdf":
            where = f"PDF p.{locator.get('page', 1)} " f"«{locator.get('quote', '')}»"
        elif kind == "html":
            where = (
                f"HTML {locator.get('selector') or ''} " f"«{locator.get('quote', '')}»"
            )
        else:
            where = f"image x={locator.get('rel_x', item.get('rel_x'))} y={locator.get('rel_y', item.get('rel_y'))}"
        lines.append(
            f"• {name}#{version} [{item.get('number')}] {where}: "
            f"{str(item.get('body') or '').strip()}"
        )
    return "\n".join(lines)


def ketcher_assets_present() -> bool:
    return (KETCHER_VENDOR / "static" / "js" / "main.8617f334.js").is_file()


def ketcher_document(config: Any, query: Mapping[str, Any] | None = None) -> bytes:
    if not official_workbench_enabled(config) or not ketcher_assets_present():
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Ketcher</title></head><body style='font:14px system-ui;"
            "padding:2rem;color:#444'><p>Chemical structure editor placeholder. "
            "Bundle Ketcher assets here to enable in-browser structure drawing."
            "</p></body></html>"
        ).encode("utf-8")
    params = query or {}

    def _one(name: str) -> str:
        value = params.get(name)
        if isinstance(value, list):
            value = value[0] if value else ""
        text = str(value or "")
        return re.sub(r"[^A-Za-z0-9._:-]", "", text)[:128]

    artifact_id = _one("artifact_id") or _one("artifact")
    return _KETCHER_WRAPPER.replace(
        "__ARTIFACT__", html.escape(artifact_id, quote=True)
    ).encode("utf-8")


_KETCHER_WRAPPER = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>Ketcher v3.7.0</title>
  <style>
    html,body{height:100%;margin:0;font:13px system-ui,sans-serif}
    #openai4s-artifact{display:flex;gap:8px;align-items:center;padding:8px 12px;border-bottom:1px solid #ddd}
    iframe{border:0;width:100%;height:calc(100% - 46px)}
    button{font:inherit}
  </style>
</head>
<body>
  <div id="openai4s-artifact" data-ketcher-core="ketcher-core" data-ketcher-js="ketcher.js">
    <strong>Ketcher v3.7.0</strong>
    <button type="button" id="ketcher-save">Save artifact version</button>
    <span id="ketcher-status">loading real editor assets</span>
  </div>
  <iframe id="ketcher-frame" title="Ketcher" src="/static/vendor/ketcher/index.html"></iframe>
  <script>
    const artifactId = "__ARTIFACT__";
    const status = document.getElementById("ketcher-status");
    const frame = document.getElementById("ketcher-frame");
    function ketcher() {
      try { return frame.contentWindow && frame.contentWindow.ketcher; } catch (e) { return null; }
    }
    async function loadArtifact() {
      if (!artifactId) { status.textContent = "ready"; return; }
      const response = await fetch("/api/v1/artifacts/" + encodeURIComponent(artifactId));
      if (!response.ok) { status.textContent = "artifact load failed"; return; }
      const text = await response.text();
      const editor = ketcher();
      if (editor && editor.setMolecule) await editor.setMolecule(text);
      status.textContent = "loaded " + artifactId;
    }
    window.addEventListener("message", (event) => {
      if (event.data && event.data.eventType === "init") loadArtifact();
    });
    frame.addEventListener("load", () => setTimeout(loadArtifact, 400));
    document.getElementById("ketcher-save").onclick = async () => {
      const editor = ketcher();
      if (!editor || !artifactId) { status.textContent = "nothing to save"; return; }
      const mol = editor.getMolfile ? await editor.getMolfile() : "";
      const response = await fetch("/api/v1/artifacts/" + encodeURIComponent(artifactId) + "/structure", {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify({content: mol, format: "mol"})
      });
      const payload = await response.json().catch(() => ({}));
      status.textContent = response.ok
        ? ("saved " + (payload.version_id || "") + (payload.unchanged ? " (unchanged)" : ""))
        : ("save failed: " + (payload.error || response.status));
    };
  </script>
</body>
</html>
"""


def checksum_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ArtifactWorkbenchService:
    """Session-facing table/diff/structure/locator operations."""

    def __init__(self, *, store: Any, artifacts: Any, broadcast: Any = None) -> None:
        self.store = store
        self.artifacts = artifacts
        self.broadcast = broadcast

    def _artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = self.store.get_artifact(artifact_id)
        if not artifact:
            raise WorkbenchError(404, "artifact not found", "artifact_not_found")
        return artifact

    def _bytes(
        self, artifact: Mapping[str, Any], version_id: str | None = None
    ) -> bytes:
        version = version_id or artifact.get("latest_version_id")
        if version:
            meta = self.store.version_meta(str(version)) or {}
            path = meta.get("snapshot_path") or meta.get("path")
            if path and Path(path).is_file():
                return Path(path).read_bytes()
        live = self.artifacts.live_path(artifact)
        if live.is_file():
            return live.read_bytes()
        raise WorkbenchError(404, "artifact bytes not found", "artifact_missing")

    def table(
        self,
        artifact_id: str,
        *,
        sort: str = "",
        descending: bool = False,
        filters: Mapping[str, str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        name = str(artifact.get("filename") or "")
        if name.lower().endswith(".parquet"):
            live = self.artifacts.live_path(artifact)
            rows = read_parquet_rows(live)
        else:
            raw = self._bytes(artifact)
            rows = parse_delimited(raw.decode("utf-8", "replace"), name)
        result = query_table(
            rows,
            sort=sort,
            descending=descending,
            filters=filters,
            offset=offset,
            limit=limit,
        )
        result["artifact_id"] = artifact_id
        result["version_id"] = artifact.get("latest_version_id")
        result["filename"] = name
        return result

    def diff(
        self,
        artifact_id: str,
        *,
        from_version: str | None = None,
        to_version: str | None = None,
    ) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        versions = self.store.list_versions(artifact_id) or []
        if not versions:
            raise WorkbenchError(404, "no versions", "no_versions")
        oldest = versions[0]["version_id"]
        newest = versions[-1]["version_id"]
        left_id = from_version or oldest
        right_id = to_version or newest
        left = self._bytes(artifact, left_id).decode("utf-8", "replace")
        right = self._bytes(artifact, right_id).decode("utf-8", "replace")
        return {
            "artifact_id": artifact_id,
            "from_version_id": left_id,
            "to_version_id": right_id,
            "changed": left != right,
            "diff": unified_diff(
                left, right, from_label=str(left_id), to_label=str(right_id)
            ),
        }

    def save_structure(
        self, artifact_id: str, *, content: str, fmt: str = "mol"
    ) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        text = str(content or "")
        summary = structure_summary(text, artifact.get("filename") or f"struct.{fmt}")
        current_id = artifact.get("latest_version_id")
        current = self.store.version_meta(current_id) if current_id else None
        digest = checksum_text(text)
        if current and current.get("checksum") == digest:
            return {
                "ok": True,
                "artifact_id": artifact_id,
                "version_id": current_id,
                "unchanged": True,
                "structure": summary,
            }
        live = self.artifacts.live_path(artifact)
        live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text(text, encoding="utf-8")
        record = self.store.save_artifact(
            path=str(live),
            filename=artifact["filename"],
            content_type=artifact.get("content_type") or "chemical/x-mdl-molfile",
            size_bytes=len(text.encode("utf-8")),
            checksum=digest,
            frame_id=artifact.get("root_frame_id"),
            project_id=artifact.get("project_id"),
            artifact_id=artifact_id,
        )
        if hasattr(self.artifacts, "write_version_snapshot"):
            self.artifacts.write_version_snapshot(
                record["version_id"], artifact["filename"], data=text.encode("utf-8")
            )
        if self.broadcast and artifact.get("root_frame_id"):
            self.broadcast(
                artifact["root_frame_id"],
                {
                    "type": "artifact_created",
                    "artifact": {
                        "id": artifact_id,
                        "filename": artifact["filename"],
                        "version_id": record["version_id"],
                        "root_frame_id": artifact.get("root_frame_id"),
                    },
                },
            )
        return {
            "ok": True,
            "artifact_id": artifact_id,
            "version_id": record["version_id"],
            "unchanged": False,
            "structure": summary,
        }

    def pdf_text(self, artifact_id: str) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        pages = extract_pdf_text(self._bytes(artifact))
        return {
            "artifact_id": artifact_id,
            "version_id": artifact.get("latest_version_id"),
            "pages": pages,
        }

    def html_outline(self, artifact_id: str) -> dict[str, Any]:
        artifact = self._artifact(artifact_id)
        outline = html_outline(self._bytes(artifact).decode("utf-8", "replace"))
        return {
            "artifact_id": artifact_id,
            "version_id": artifact.get("latest_version_id"),
            "elements": outline,
        }
