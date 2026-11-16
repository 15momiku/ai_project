"""报告生成进度（报告生成可视化进度）。

为什么要它：一份报告要跑两次大模型评分（首轮 + 二次校验），实测数十秒；前端此前只有静态
骨架屏 + 「请稍候」，用户无法判断是在正常处理还是已经卡死，于是刷新页面或反复点「重新生成」。

设计取舍
--------
- **进程内记录，不落库**：进度是瞬时状态，刷新页面或重启进程就失效，没必要写表。
  单进程部署（`start.ps1` 的 `uvicorn --reload`，以及单进程托管前端的生产模式）完全够用。
  多 worker 时请求可能落在别的进程、查不到进度，此时 `snapshot()` 明确返回 `available=False`，
  前端退化为「不确定进度条」——**不假装有进度**。
  这与 `services/auth.py` 的进程内登录限流同一取舍（多实例部署需换共享存储）。
- **进度不撒谎**：`percent` 只按**已真正完成**的阶段数计算，进行中的阶段算半格的是 CSS 上的
  动效，而不是数字。子步骤（「第 1 轮评分 / 二次校验」）只体现在文字上，不虚增百分比。
- **协程安全**：只在协程里做「读改写一个 key」，中间没有 await；事件循环单线程即可保证一致。
- **容量自愈**：最多保留 `_MAX_ENTRIES` 条，超出淘汰最早的；`_TTL_SECONDS` 过期自动清理，
  反复生成报告不会让长跑进程涨内存。

对外只暴露 begin / stage / set_mode / finish / fail / reset / snapshot，调用方不必关心存储形态。
"""

from __future__ import annotations

import time

# 阶段清单：顺序即进度顺序，label 直接展示给用户
STAGES: list[tuple[str, str]] = [
    ("load", "读取面试记录"),
    ("score", "六维评分"),
    ("enrich", "维度归因与基准"),
    ("plan", "改进建议与 7 天计划"),
    ("questions", "逐题分析与证据"),
    ("persist", "保存报告与错题"),
]

_STAGE_KEYS = [key for key, _ in STAGES]
_STAGE_LABELS = dict(STAGES)

# 超过这个时间仍在 running：可能真的很慢（大模型排队），也可能是进程重启把记录留在了这里。
# 如实标记 stale 交给前端提示，而不是假装一切正常。
_STALE_SECONDS = 300.0
_TTL_SECONDS = 1800.0
_MAX_ENTRIES = 64

# session_id -> 进度记录
_registry: dict[str, dict] = {}


def _now() -> float:
    return time.monotonic()


def _prune() -> None:
    """清理过期记录；容量超限时淘汰最早开始的。"""
    now = _now()
    for sid in [s for s, r in _registry.items() if now - r["started_at"] > _TTL_SECONDS]:
        _registry.pop(sid, None)
    if len(_registry) > _MAX_ENTRIES:
        ordered = sorted(_registry.items(), key=lambda kv: kv[1]["started_at"])
        for sid, _ in ordered[: len(_registry) - _MAX_ENTRIES]:
            _registry.pop(sid, None)


def _blank_stages() -> list[dict]:
    return [
        {"key": key, "label": label, "status": "pending", "detail": "", "seconds": None}
        for key, label in STAGES
    ]


def _set_detail(record: dict, key: str, detail: str) -> None:
    """子步骤说明同时写到记录与对应阶段项上。

    阶段项才是接口对外暴露的形态（前端按 `stages[i].detail` 渲染当前子步骤），
    只更新 record["detail"] 的话前端永远看不到「第 1 轮 / 二次校验」这类信息。
    """
    record["detail"] = detail or record.get("detail", "")
    for item in record["stages"]:
        if item["key"] == key:
            item["detail"] = record["detail"]
            return


def begin(session_id: str, user_id: str = "") -> None:
    """开始一次生成。重复调用（并发点两次「重新生成」）按最新一次重置。"""
    if not session_id:
        return
    _prune()
    now = _now()
    _registry[session_id] = {
        "session_id": session_id,
        "user_id": user_id or "",
        "status": "running",
        "started_at": now,
        "updated_at": now,
        "stage": _STAGE_KEYS[0],
        "detail": "",
        "stage_started": {_STAGE_KEYS[0]: now},  # 各阶段首次进入的时间，用于算耗时
        "scoring_mode": "",
        "message": "",
        "stages": _blank_stages(),
    }


def stage(session_id: str, key: str, detail: str = "") -> None:
    """推进到某个阶段；同阶段再次调用只更新 detail（用于「第 1 轮 / 二次校验」这类子步骤）。

    未知 key 直接忽略：埋点不该因为写错一个字符串就把生成流程带崩。
    """
    record = _registry.get(session_id)
    if not record or key not in _STAGE_LABELS:
        return
    now = _now()
    if key == record["stage"]:
        _set_detail(record, key, detail)
        record["updated_at"] = now
        return

    started = record.setdefault("stage_started", {})
    target = _STAGE_KEYS.index(key)
    for i, item in enumerate(record["stages"]):
        if i < target and item["status"] != "done":
            item["status"] = "done"
            item["seconds"] = round(max(0.0, now - started.get(item["key"], now)), 1)
        elif i == target:
            item["status"] = "active"
        elif i > target:
            item["status"] = "pending"
            item["seconds"] = None
    started.setdefault(key, now)
    record["stage"] = key
    _set_detail(record, key, detail)
    record["updated_at"] = now


def set_mode(session_id: str, mode: str) -> None:
    """记录评分方式（llm / rule / hybrid）。前端据此给出「通常需要多久」的如实说明。"""
    record = _registry.get(session_id)
    if record and mode:
        record["scoring_mode"] = str(mode)
        record["updated_at"] = _now()


def finish(session_id: str) -> None:
    record = _registry.get(session_id)
    if not record:
        return
    now = _now()
    started = record.get("stage_started") or {}
    for item in record["stages"]:
        if item["status"] != "done":
            item["status"] = "done"
        if item["seconds"] is None:
            item["seconds"] = round(max(0.0, now - started.get(item["key"], now)), 1)
    record["status"] = "done"
    record["stage"] = _STAGE_KEYS[-1]
    record["detail"] = ""
    record["updated_at"] = now


def fail(session_id: str, message: str = "") -> None:
    """生成失败。仍然返回阶段明细，便于定位卡在哪一步。"""
    record = _registry.get(session_id)
    if not record:
        return
    record["status"] = "failed"
    record["message"] = str(message or "")
    record["updated_at"] = _now()


def reset(session_id: str) -> None:
    _registry.pop(session_id, None)


def snapshot(session_id: str) -> dict:
    """返回给接口的进度快照。没有记录时 available=False（前端退化为不确定进度）。"""
    _prune()
    record = _registry.get(session_id)
    if not record:
        return {
            "session_id": session_id,
            "available": False,
            "status": "idle",
            "stage": "",
            "stage_label": "",
            "stage_index": 0,
            "total_stages": len(STAGES),
            "percent": 0,
            "elapsed_seconds": 0.0,
            "scoring_mode": "",
            "stale": False,
            "note": "当前部署方式（多进程）可能拿不到生成进度，已按不确定进度展示",
            "message": "",
            "stages": _blank_stages(),
        }

    now = _now()
    elapsed = max(0.0, now - record["started_at"])
    status = record["status"]
    current = record["stage"]
    active_key = current if current in _STAGE_KEYS else ""
    done_count = sum(1 for item in record["stages"] if item["status"] == "done")
    percent = 100 if status == "done" else int(round(done_count / len(STAGES) * 100))

    stages = [dict(item) for item in record["stages"]]
    # 进行中的阶段：补上「已经跑了多久」，UI 可显示「六维评分 · 42s」
    for item in stages:
        if item["status"] == "active":
            item["seconds"] = round(max(0.0, now - record["stage_started"].get(item["key"], now)), 1)

    return {
        "session_id": record["session_id"],
        "available": True,
        "status": status,
        "stage": current,
        "stage_label": _STAGE_LABELS.get(current, ""),
        "stage_index": _STAGE_KEYS.index(current) + 1 if active_key else 0,
        "total_stages": len(STAGES),
        "percent": max(0, min(100, percent)),
        "elapsed_seconds": round(elapsed, 1),
        "scoring_mode": record.get("scoring_mode", ""),
        "stale": bool(status == "running" and elapsed > _STALE_SECONDS),
        "note": "",
        "message": record.get("message", ""),
        "stages": stages,
    }
