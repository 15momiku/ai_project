"""单题口播指标计算（体验增强 §5 口播实时反馈）。

设计原则：**只给事实，不给分**。

- 指标：语速（字/分）、填充词命中次数与原文、单题时长；
- 阈值集中在 `constants.py`，前后端共用同一口径，避免"前端说偏快、后端说正常"；
- 填充词复用 `constants.FILLER_WORDS`（评分引擎的口头禅词表），不新增数据源，
  因此实时反馈与最终评分对"口头禅"的判断永远一致；
- 本模块不被评分算法调用：报告里的口播指标只是并排展示的事实地铁，
  不参与六维分数，避免出现"边答边掉分"。
"""

from __future__ import annotations

from typing import Any

from ..constants import (
    FILLER_WORDS,
    SPEECH_FOCUS_SECONDS,
    SPEECH_RATE_IDEAL_MAX,
    SPEECH_RATE_IDEAL_MIN,
    SPEECH_RATE_LOW,
)


def filler_hits(text: str) -> tuple[list[str], int]:
    """返回 (命中的口头禅去重列表, 总命中次数)。

    总次数按出现次数累加（"然后"出现 3 次就算 3 次），
    与评分引擎 `_hits()` 的口径一致。
    """
    src = text or ""
    if not src:
        return [], 0
    words: list[str] = []
    total = 0
    for word in FILLER_WORDS:
        count = src.count(word)
        if count:
            words.append(word)
            total += count
    return words, total


def chars_per_minute(text: str, seconds: int) -> float:
    """语速（字/分）。时长不足 1 秒时返回 0，表示"无法计算"而不是"极快"。"""
    if not text or seconds is None or int(seconds) <= 0:
        return 0.0
    return round(len(text) / (int(seconds) / 60.0), 1)


def level_of(cpm: float) -> str:
    """语速档位：slow | good | fast。无法计算时归入 good（不提示即不打扰）。"""
    if cpm <= 0:
        return "good"
    if cpm < SPEECH_RATE_IDEAL_MIN:
        return "slow"
    if cpm > SPEECH_RATE_IDEAL_MAX:
        return "fast"
    return "good"


def note_of(cpm: float, filler_count: int, seconds: int) -> str:
    """给用户的一句人话提示；没有值得说的就返回空串（界面据此不显示这一条）。"""
    parts: list[str] = []
    if cpm > 0:
        if cpm < SPEECH_RATE_LOW:
            parts.append("内容偏少，可以再展开一些细节")
        elif cpm < SPEECH_RATE_IDEAL_MIN:
            parts.append(f"语速偏慢（{cpm:.0f} 字/分）")
        elif cpm > SPEECH_RATE_IDEAL_MAX:
            parts.append(f"语速偏快（{cpm:.0f} 字/分）")
    if filler_count > 0:
        parts.append(f"填充词 {filler_count} 次")
    if int(seconds or 0) > SPEECH_FOCUS_SECONDS:
        parts.append("单题偏长，下次可以更聚焦")
    return "；".join(parts)


def evaluate(text: str, seconds: int) -> dict[str, Any]:
    """一次性算出该题的全部口播指标（前端上报值与服务端判定都走这里）。"""
    src = text or ""
    secs = int(seconds or 0)
    words, count = filler_hits(src)
    cpm = chars_per_minute(src, secs)
    return {
        "chars": len(src),
        "duration_seconds": secs,
        "chars_per_minute": cpm,
        "filler_count": count,
        "filler_words": words,
        "level": level_of(cpm),
        "note": note_of(cpm, count, secs),
    }
