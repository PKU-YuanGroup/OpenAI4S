---
name: volcengine-datapro
description: Discover and call the configured Volcengine DataPro dataPro_search(query:string) MCP tool for professional-dataset queries.
origin: openai4s
capabilities:
  network:
    mode: host_only
    # Deliberately empty. The destination is the connector's configuration,
    # not this Skill's to declare, and the guidance below never names the
    # endpoint or its auth headers so the agent cannot reach around the
    # managed connector.
    domains: []

---

# Volcengine DataPro

Use only the `volcengine-datapro` connector and its `dataPro_search` tool.

## Input

Accept one non-empty string named `query`.

## Discover and search

Run this workflow in a Python cell:

```python
if type(query) is not str or not query.strip():
    raise ValueError("query must be a non-empty string")

discovery = host.mcp.tools("volcengine-datapro")
tools = discovery.get("tools") if isinstance(discovery, dict) else None
if not isinstance(tools, list) or not any(
    isinstance(tool, dict) and tool.get("name") == "dataPro_search"
    for tool in tools
):
    raise RuntimeError("dataPro_search is not available on volcengine-datapro")

result = host.mcp.call(
    "volcengine-datapro",
    "dataPro_search",
    {"query": query},
)
raw = result.get("raw") if isinstance(result, dict) else None
structured = raw.get("structuredContent") if isinstance(raw, dict) else None
code = structured.get("code") if isinstance(structured, dict) else None

if type(code) is int and code == 0:
    index = result.get("index") if isinstance(result, dict) else None
    complete = (
        isinstance(index, dict)
        and index.get("complete") is True
        and type(index.get("source_leaf_count")) is int
        and type(index.get("indexed_leaf_count")) is int
        and index.get("source_leaf_count") == index.get("indexed_leaf_count")
        and isinstance(index.get("source_digest"), str)
        and index.get("source_digest") == index.get("indexed_digest")
    )
    if not complete:
        raise RuntimeError("专业数据集本次返回内容未完整索引")
    print("专业数据集可用")
elif type(code) is int and code == 4011:
    raise RuntimeError("Key 无效、额度不足，或者专业数据集 Harness 未开启。")
else:
    raise RuntimeError(f"专业数据集不可用（code={code!r}）")

result
```

Discovery confirms only that the named tool is advertised. Never treat
discovery as authentication or report `专业数据集可用` from it. Report that
status only after the real search call returns an integer
`raw.structuredContent.code` equal to `0`; a boolean or string zero does not
qualify. A successful call is reported as available only after its index
receipt proves that every JSON leaf in this response was indexed. This receipt
describes the current response; it is not a claim that the remote corpus was
fully mirrored.
