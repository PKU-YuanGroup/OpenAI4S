"""Versioned data-disclosure copy for the experimental judgment layer."""

from __future__ import annotations

from typing import Mapping

DISCLOSURE_VERSION = "2026-09-20"

CAPABILITIES: tuple[str, ...] = (
    "skill_suggest",
    "literature_check",
    "text_features",
    "safety_shadow",
    "task_mode_shadow",
)

# What each capability sends to api.typesafe.ai. Keys are capability ids.
DISCLOSURE_TEXT: dict[str, dict[str, str]] = {
    "skill_suggest": {
        "en": (
            "The current user request text, plus names, descriptions, and "
            "SKILL.md opening fragments of candidate Skills in the current scope."
        ),
        "zh": (
            "用户当前请求文本，以及当前 scope 内候选 Skill 的名称、描述和 "
            "SKILL.md 开头片段。"
        ),
    },
    "literature_check": {
        "en": "The research question, paper passages, and claims to be checked.",
        "zh": "研究问题、论文片段，以及待核验的结论。",
    },
    "text_features": {
        "en": "The user-selected data-row text used to build features.",
        "zh": "用户选中的、用于构造特征的数据行文本。",
    },
    "safety_shadow": {
        "en": (
            "The pending code cell, fragments of tool results, and a session "
            "trajectory summary. This is the highest-risk outbound payload."
        ),
        "zh": (
            "待执行的代码 cell、工具返回的内容片段，以及会话轨迹摘要。"
            "这是风险最高的外发内容，界面上会单独强调。"
        ),
    },
    "task_mode_shadow": {
        "en": "The current user request text.",
        "zh": "用户当前请求文本。",
    },
}

FACTS_EN = (
    "The TypeSafe Jev service is hosted in the United States. "
    "Its privacy policy states that inputs are not used to train models, "
    "but it does not specify a retention period. "
    "Zero Data Retention (ZDR) is available only to enterprise accounts. "
    "Do not enable this feature for sensitive data."
)

FACTS_ZH = (
    "TypeSafe Jev 服务托管在美国。"
    "隐私政策承诺不用输入训练模型，但没有写明保留期限。"
    "零数据保留（ZDR）只对企业账户开放。"
    "敏感数据不要开启。"
)


def is_acknowledged(ack_record: object, capability: str) -> bool:
    """True when ``ack_record`` matches this version and lists ``capability``."""

    if not isinstance(ack_record, Mapping):
        return False
    if str(ack_record.get("version") or "") != DISCLOSURE_VERSION:
        return False
    listed = ack_record.get("capabilities")
    if isinstance(listed, str):
        names: tuple[object, ...] = (listed,)
    elif isinstance(listed, (list, tuple, set, frozenset)):
        names = tuple(listed)
    else:
        return False
    return capability in names
