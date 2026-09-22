"""Byte-stable snapshot of default-off surfaces the judgment layer must not change.

Initially captured before product code for the experimental judgment layer.
The catalog and tool-schema fixtures also include the independent main-branch
additions documented in the operator guide. A mismatch means the default-off
path drifted from that recorded integration baseline.

Run ``uv run python -c "import tests.test_judgment_default_off as t; t.capture()"``
to rewrite ``tests/fixtures/judgment_default_off/*.json``.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

import pytest

# conftest already pins this for pytest; capture() must pin it before config
# import because dataclass defaults freeze env at construction time.
os.environ.setdefault("OPENAI4S_SKIP_DOTENV", "1")

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "judgment_default_off"
_SKILLS_DIR = _REPO / "skills"

_SEARCH_QUERIES_EN = (
    "single-cell rna analysis clustering annotation",
    "protein structure prediction and design pipeline",
    "literature review citation screening",
    "differential expression volcano plot",
    "molecular docking ligand binding",
)
_SEARCH_QUERIES_ZH = (
    "帮我对这批单细胞数据做细胞类型注释",
    "蛋白质结构如何预测与设计",
    "写一篇文献综述并核验引用",
    "分子对接怎么做",
    "差异表达分析的完整流程",
)

# 5 fast-path + 5 static-hit + 5 risk-token-but-allowed = 15.
_CLASSIFY_SNIPPETS = (
    # fast-path: no risk tokens
    "import numpy as np\nprint(np.mean([1, 2, 3]))",
    "df = pd.read_csv('data.csv'); df.describe()",
    "x = [1, 2, 3]\nprint(sum(x) / len(x))",
    "import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])",
    'print("hello")',
    # static hit
    'import os\nos.environ["LD_PRELOAD"] = "/tmp/x/evil.so"',
    'open("/home/u/.ssh/id_rsa").read()',
    'open("/etc/shadow").read()',
    "import base64\nexec(base64.b64decode(payload))",
    '__import__("os").system("id")',
    # risk token present, heuristic allows
    "import subprocess\nsubprocess.run(['ls', '-la'])",
    "import socket\ns = socket.socket()",
    'import ctypes; ctypes.CDLL("libfoo.so")',
    'dyn.load("libfoo.so")',
    "import pickle\ndata = pickle.loads(buf)",
)

_SNAPSHOT_FILES = (
    "system_context.json",
    "search.json",
    "registry.json",
    "classify_code.json",
)

_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_ISO_TIME_RE = re.compile(
    r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?\b"
)
_MAC_TMP_RE = re.compile(r"/var/folders/[^/\s\"']+/[^/\s\"']+/[^/\s\"']+")
_WIN_USER_RE = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+(?:\\[^\\\s\"']+)*")
_ENV_LIST_KEYS = frozenset({"envs", "conda_envs", "environments", "packages", "conda"})


def _pin_offline_env(data_dir: Path) -> None:
    os.environ["OPENAI4S_SKIP_DOTENV"] = "1"
    os.environ["OPENAI4S_DATA_DIR"] = str(data_dir)
    os.environ["OPENAI4S_LLM_PROVIDER"] = "deepseek"
    os.environ["OPENAI4S_DEEPSEEK_API_KEY"] = "test-key"
    os.environ["OPENAI4S_LLM_API_KEY"] = "test-key"
    os.environ["OPENAI4S_UNATTENDED_APPROVAL"] = "deny"
    os.environ["OPENAI4S_SECRET_STORE"] = "plaintext"
    os.environ["OPENAI4S_NOTEBOOK_REPL"] = "0"
    os.environ.pop("OPENAI4S_SKILLS_DIR", None)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_jsonable(item) for item in value]
        try:
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        except TypeError:
            return sorted(items, key=repr)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _replacement_pairs(data_dir: Path | None = None) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    home = str(Path.home())
    if home:
        pairs.append((home, "<HOME>"))
    env_home = os.environ.get("HOME") or ""
    if env_home and env_home != home:
        pairs.append((env_home, "<HOME>"))
    tmp = os.path.realpath(tempfile.gettempdir())
    if tmp and tmp not in {"/tmp", "/private/tmp"}:
        pairs.append((tmp, "<TMP>"))
    pairs.append((str(_REPO), "<REPO>"))
    if data_dir is not None:
        pairs.append((str(data_dir.resolve()), "<DATA_DIR>"))
        pairs.append((str(data_dir), "<DATA_DIR>"))
    env_data = os.environ.get("OPENAI4S_DATA_DIR")
    if env_data:
        pairs.append((env_data, "<DATA_DIR>"))
    pairs.sort(key=lambda item: len(item[0]), reverse=True)
    return pairs


def _normalize_string(text: str, pairs: list[tuple[str, str]]) -> str:
    for src, dst in pairs:
        if not src:
            continue
        text = text.replace(src, dst)
        text = text.replace(src.replace("\\", "/"), dst)
    text = _MAC_TMP_RE.sub("<TMP>", text)
    text = _WIN_USER_RE.sub("<HOME>", text)
    text = _UUID_RE.sub("<UUID>", text)
    text = _ISO_TIME_RE.sub("<TIMESTAMP>", text)
    return text


def normalize(value: Any, *, data_dir: Path | None = None) -> Any:
    """Drop machine-local paths, ids, timestamps, and accidental ordering."""

    pairs = _replacement_pairs(data_dir)
    payload = _jsonable(value)

    def walk(node: Any, key: str | None = None) -> Any:
        if isinstance(node, dict):
            out = {str(k): walk(v, str(k)) for k, v in node.items()}
            return dict(sorted(out.items()))
        if isinstance(node, list):
            walked = [walk(item, key) for item in node]
            if key in _ENV_LIST_KEYS and all(isinstance(item, str) for item in walked):
                return sorted(walked)
            return walked
        if isinstance(node, str):
            return _normalize_string(node, pairs)
        return node

    return walk(payload)


def dump_snapshot(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _fail_urlopen(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("urlopen must not be called on the judgment default-off path")


class _UrlopenGuard:
    def __enter__(self) -> None:
        self._original = urllib.request.urlopen
        urllib.request.urlopen = _fail_urlopen  # type: ignore[method-assign]
        return None

    def __exit__(self, *_exc: object) -> None:
        urllib.request.urlopen = self._original  # type: ignore[method-assign]


def _make_cfg(data_dir: Path):
    from openai4s.config import Config

    return Config(data_dir=data_dir, skills_dir=_SKILLS_DIR)


def collect_snapshots(*, data_dir: Path) -> dict[str, Any]:
    """Return the four default-off surfaces, already normalized."""

    from openai4s.host.skills import SkillService
    from openai4s.security.classifier import classify_code
    from openai4s.tools.registry import REGISTRY

    cfg = _make_cfg(data_dir)
    service = SkillService(cfg)
    with _UrlopenGuard():
        system_context = service.system_context()
        search: dict[str, Any] = {}
        for query in _SEARCH_QUERIES_EN + _SEARCH_QUERIES_ZH:
            search[query] = service.search({"query": query, "limit": 5})
        registry = [
            {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            }
            for tool in REGISTRY
        ]
        classify = []
        for code in _CLASSIFY_SNIPPETS:
            verdict = classify_code(code, cfg, mode="heuristic")
            classify.append(
                {
                    "code": code,
                    "decision": verdict.decision,
                    "categories": list(verdict.categories),
                    "reason": verdict.reason,
                    "source": verdict.source,
                }
            )
    return {
        "system_context.json": normalize(system_context, data_dir=data_dir),
        "search.json": normalize(search, data_dir=data_dir),
        "registry.json": normalize(registry, data_dir=data_dir),
        "classify_code.json": normalize(classify, data_dir=data_dir),
    }


def capture(target: Path | None = None) -> Path:
    """Write the four snapshot files. Used by the merger to re-check the baseline."""

    fixture_dir = target or _FIXTURE_DIR
    fixture_dir.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix="openai4s-judgment-off-"))
    _pin_offline_env(data_dir)
    snapshots = collect_snapshots(data_dir=data_dir)
    for name, payload in snapshots.items():
        (fixture_dir / name).write_text(dump_snapshot(payload), encoding="utf-8")
    return fixture_dir


def _load_fixture(name: str) -> bytes:
    path = _FIXTURE_DIR / name
    return path.read_bytes()


@pytest.fixture(autouse=True)
def _block_urlopen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _fail_urlopen)


def test_normalize_strips_machine_local_values(tmp_path: Path) -> None:
    home_hit = str(Path.home() / "secret-file")
    tmp_hit = str(Path(tempfile.gettempdir()) / "openai4s-xyz")
    repo_hit = str(_REPO / "openai4s" / "config.py")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    data_hit = str(data_dir / "openai4s.db")
    payload = {
        "home": home_hit,
        "tmp": tmp_hit,
        "repo": repo_hit,
        "data": data_hit,
        "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "when": "2026-09-20T12:34:56Z",
        "envs": ["z-env", "a-env"],
        "shuffled": {"b": 1, "a": 2},
    }
    out = normalize(payload, data_dir=data_dir)
    text = dump_snapshot(out)
    assert str(Path.home()) not in text
    assert os.path.realpath(
        tempfile.gettempdir()
    ) not in text or tempfile.gettempdir() in {
        "/tmp",
        "/private/tmp",
    }
    if os.path.realpath(tempfile.gettempdir()) not in {"/tmp", "/private/tmp"}:
        assert os.path.realpath(tempfile.gettempdir()) not in text
    assert str(_REPO) not in text
    assert str(data_dir) not in text
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" not in text
    assert "2026-09-20T12:34:56Z" not in text
    assert out["envs"] == ["a-env", "z-env"]
    assert list(out["shuffled"]) == ["a", "b"]
    assert out["home"].startswith("<HOME>")
    assert out["repo"].startswith("<REPO>")
    assert out["data"].startswith("<DATA_DIR>")
    assert out["uuid"] == "<UUID>"
    assert out["when"] == "<TIMESTAMP>"


def test_snapshot_fixtures_exist() -> None:
    missing = [name for name in _SNAPSHOT_FILES if not (_FIXTURE_DIR / name).is_file()]
    assert missing == [], f"run capture() to write {missing}"


def test_default_off_snapshots_match_byte_for_byte(tmp_path: Path) -> None:
    data_dir = tmp_path / "openai4s-data"
    data_dir.mkdir()
    snapshots = collect_snapshots(data_dir=data_dir)
    for name in _SNAPSHOT_FILES:
        actual = dump_snapshot(snapshots[name]).encode("utf-8")
        expected = _load_fixture(name)
        assert actual == expected, f"{name} drifted from the default-off snapshot"


def test_chinese_skill_search_is_empty(tmp_path: Path) -> None:
    data_dir = tmp_path / "openai4s-data"
    data_dir.mkdir()
    snapshots = collect_snapshots(data_dir=data_dir)
    search = snapshots["search.json"]
    for query in _SEARCH_QUERIES_ZH:
        assert search[query] == [], query


def test_classify_snapshot_covers_three_heuristic_paths() -> None:
    payload = json.loads(_load_fixture("classify_code.json").decode("utf-8"))
    assert len(payload) == 15
    sources = {row["source"] for row in payload}
    assert "fast-path" in sources
    assert "static" in sources
    decisions = {(row["source"], row["decision"]) for row in payload}
    assert ("fast-path", "SAFE") in decisions
    assert ("static", "UNSAFE") in decisions
    assert ("static", "SAFE") in decisions


def test_urlopen_guard_fires_if_anything_calls_it() -> None:
    with pytest.raises(AssertionError, match="urlopen must not"):
        urllib.request.urlopen("https://example.invalid")  # type: ignore[arg-type]


def test_default_config_judgment_flags_are_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openai4s.config import Config
    from openai4s.judgment.disclosure import CAPABILITIES
    from openai4s.judgment.flags import resolve

    for name in (
        "OPENAI4S_EXPERIMENTAL_JUDGMENT",
        "OPENAI4S_JUDGMENT_SKILL_SUGGEST",
        "OPENAI4S_JUDGMENT_LITERATURE",
        "OPENAI4S_JUDGMENT_TEXT_FEATURES",
        "OPENAI4S_JUDGMENT_SAFETY_SHADOW",
        "OPENAI4S_JUDGMENT_TASK_MODE_SHADOW",
        "OPENAI4S_JUDGMENT_PROVIDER",
        "OPENAI4S_JUDGMENT_MODEL",
        "OPENAI4S_JUDGMENT_TIMEOUT_S",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config(data_dir=tmp_path / "openai4s-data", skills_dir=_SKILLS_DIR)
    flags = cfg.experimental_judgment
    assert flags.master is None
    assert flags.skill_suggest is None
    assert flags.literature_check is None
    assert flags.text_features is None
    assert flags.safety_shadow is None
    assert flags.task_mode_shadow is None
    assert flags.provider == "typesafe"
    assert flags.model == "jev-1.13.0"
    assert flags.timeout_s == 3.0
    effective = resolve(cfg, None)
    assert effective.master.enabled is False
    for name in CAPABILITIES:
        assert getattr(effective, name).enabled is False


if __name__ == "__main__":
    capture()
    print(f"wrote snapshots under {_FIXTURE_DIR}")
