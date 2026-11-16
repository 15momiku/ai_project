"""会话收敛：把长期没有进展的 `ongoing` 面试标记为 `aborted`（API_CONTRACT §1.7）。

为什么需要它
------------
用户中途关掉页面就不会再调用 `/finish`，这些会话会永久停在 `ongoing`：
- 数据库里「进行中的面试」无限累积，无从区分「真在进行」和「早就放弃了」；
- 用户重新打开旧链接时，会看到一个永远答不完的会话，而且 answer/skip 仍然接受写入，
  容易在早已放弃的会话上继续追加对话。

处理策略
--------
空闲超过 `SESSION_IDLE_TIMEOUT_HOURS`（默认 6 小时，<=0 关闭）的 `ongoing` 会话 → 置 `aborted`
并补齐 `finished_at`；**对话内容一律保留**，因此仍然可以基于已有对话生成报告。

计时基准取 `max(session.created_at, dialogue 中最后一条 turn 的 created_at)`。
注意两类时间戳的时区不同：turn 的 `created_at` 是本地时区 ISO 字符串（`deps.local_iso()`），
而 `session.created_at` 是 naive UTC（`models.now_utc()`），比较前必须统一折算成 UTC aware，
否则东八区会凭空多出 8 小时空闲、把还在进行的面试误判为超时。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import deps
from ..config import settings
from ..models import InterviewSession, now_utc

DEFAULT_IDLE_TIMEOUT_HOURS: float = 6.0


def _resolve_timeout(timeout_hours: float | None = None) -> float:
    """解析生效的空闲阈值（小时）。显式入参 > Settings 字段 > 环境变量 > 默认值。"""
    if timeout_hours is not None:
        try:
            return float(timeout_hours)
        except (TypeError, ValueError):
            return DEFAULT_IDLE_TIMEOUT_HOURS
    raw = getattr(settings, "session_idle_timeout_hours", None)
    if raw is None:
        # config.py 未声明该字段时仍然可用（例如按老版本配置启动）
        raw = os.getenv("SESSION_IDLE_TIMEOUT_HOURS")
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_IDLE_TIMEOUT_HOURS


def idle_timeout_hours() -> float:
    """当前生效的空闲阈值（小时），供启动日志展示。"""
    return _resolve_timeout()


def _as_utc(value: object) -> datetime | None:
    """把 turn 的本地时区 ISO 字符串 / 库里的 naive UTC datetime 统一成 UTC aware。"""
    if value is None or value == "":
        return None
    dt: datetime
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def last_activity_at(session: InterviewSession) -> datetime:
    """该会话最后一次活动时间（UTC aware）。没有任何可用时间戳时退回「现在」。"""
    stamps: list[datetime | None] = [_as_utc(session.created_at)]
    dialogue = deps.load_json(session.dialogue_json, [])
    if isinstance(dialogue, list):
        for item in dialogue:
            if isinstance(item, dict):
                stamps.append(_as_utc(item.get("created_at")))
    known = [s for s in stamps if s is not None]
    if not known:
        return now_utc()
    return max(known)


def is_stale(session: InterviewSession, timeout_hours: float | None = None) -> bool:
    """是否「进行中且已空闲超时」。非 ongoing / 已结束 / 关闭阈值时恒为 False（幂等）。"""
    hours = _resolve_timeout(timeout_hours)
    if hours <= 0:
        return False
    if (session.status or "ongoing") != "ongoing":
        return False
    return now_utc() - last_activity_at(session) > timedelta(hours=hours)


def _mark_aborted(session: InterviewSession) -> None:
    """只改内存字段，由调用方统一 commit（批量收敛时避免逐行提交）。"""
    session.status = "aborted"
    if session.finished_at is None:
        session.finished_at = now_utc()


def close_session(
    db: Session, session: InterviewSession, timeout_hours: float | None = None
) -> bool:
    """单场收敛：仅当该会话已空闲超时才置为 `aborted`，返回是否发生了变更。

    幂等：已 `finished` / 已 `aborted` 的会话不会被改动，也不会覆盖 `finished_at`。
    """
    if not is_stale(session, timeout_hours):
        return False
    _mark_aborted(session)
    try:
        db.commit()
        db.refresh(session)
    except Exception as exc:  # noqa: BLE001 收敛失败不能影响接口主流程
        print(f"[housekeeping] 收敛会话失败 {session.session_id}: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return False
    return True


def close_stale_sessions(
    db: Session, timeout_hours: float | None = None, user_id: str = ""
) -> list[str]:
    """批量收敛过期 `ongoing` 会话，返回被关闭的 session_id 列表。

    `user_id` 非空时只处理该用户（用于请求内收敛）；为空时处理全库（用于启动自愈）。
    """
    hours = _resolve_timeout(timeout_hours)
    if hours <= 0:
        return []
    try:
        stmt = select(InterviewSession).where(InterviewSession.status == "ongoing")
        if user_id:
            stmt = stmt.where(InterviewSession.user_id == user_id)
        rows = list(db.execute(stmt).scalars().all())
    except Exception as exc:  # noqa: BLE001 查询失败只记日志
        print(f"[housekeeping] 查询进行中会话失败: {exc}")
        return []

    closed: list[str] = []
    for session in rows:
        if is_stale(session, hours):
            _mark_aborted(session)
            closed.append(session.session_id)

    if not closed:
        return []
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[housekeeping] 批量收敛失败（{len(closed)} 场）: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []
    return closed
