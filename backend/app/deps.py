"""公共依赖与工具。"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import User
from .services import auth as auth_service

# 匿名访客（未登录、localStorage 无设备ID）共用的固定账号。
# 用固定 id 而不是随机 id，保证同一批匿名数据能归到同一用户下，也不会无限增长 User 表。
_GUEST_USER_ID = "guest"


def load_json(text: str | None, default: Any = None) -> Any:
    if not text:
        return default if default is not None else {}
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


def dump_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def new_id() -> str:
    return uuid.uuid4().hex


def ensure_user(db: Session, user_id: str = "", nickname: str = "同学", school: str = "") -> User:
    """获取或创建演示用户（MVP 不做登录体系，前端用 localStorage 里的设备ID）。

    `user_id` 为空时返回**内存里的游客身份**，不落库 —— 原因见下方注释。
    """
    if user_id:
        user = db.get(User, user_id)
        if user:
            return user
        user = User(user_id=user_id, nickname=nickname or "同学", school=school or "")
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    # 游客身份：**只构造对象，不写库**。
    #
    # 历史教训：这里原来会 `db.add` 一行 `user_id="guest"` 的账号。因为
    # `/api/auth/me` 属于「必须走落盘库」的认证接口，而未登录访客每次打开页面都会调它，
    # 于是**每个访客都会在真实库里留下一行 username 为空的 guest 账号** ——
    # 「数据库里只保留用户注册的账号」这条规则永远达不到，启动清理也追不上页面访问。
    #
    # 现在游客的所有数据写入路径都要求登录（`deps.require_login`），
    # 所以这个对象只承担两件事：携带 `user_id="guest"` 供 `is_guest()` 判定、
    # 让 `/api/auth/me` 能如实回答「你是游客」。它退化成纯内存对象，
    # 既不落盘、也不会成为任何资源的外键父行。
    guest = db.get(User, _GUEST_USER_ID)
    if guest is not None:
        return guest  # 老库里可能还有这一行，读到就沿用（启动清理会回收）
    return User(user_id=_GUEST_USER_ID, nickname=nickname or "同学", school=school or "")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round1(value: float) -> float:
    return round(float(value) + 1e-9, 1)


# ============================================================
# 时间显示
# ============================================================
# 库里存的是 naive UTC（`now_utc()`），而接口里给用户看的日期/时间必须用本地时区，
# 否则中国用户在 UTC+8 的 00:00~08:00 之间完成的面试会被显示成「前一天」，
# 报告的「评分时间」也会比实际早 8 小时。
_LOCAL_TZ = datetime.now().astimezone().tzinfo


def as_local(dt: datetime | None) -> datetime | None:
    """把 UTC 时间转成运行环境本地时间；naive 值按 UTC 解释。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_LOCAL_TZ)


def local_iso(dt: datetime | None = None) -> str:
    """本地时区的 ISO 字符串（用于展示类字段）。"""
    return (as_local(dt) or datetime.now(_LOCAL_TZ)).isoformat(timespec="seconds")


def local_date_str(dt: datetime | None) -> str:
    """本地时区的日期（YYYY-MM-DD），用于历史曲线等按天聚合的展示。"""
    d = as_local(dt)
    return d.strftime("%Y-%m-%d") if d else ""


def tokenize(text: str) -> set[str]:
    """极简中文/英文分词：用于技能命中率等规则计算。"""
    import re

    if not text:
        return set()
    lowered = text.lower()
    tokens = set(re.findall(r"[a-zA-Z][a-zA-Z0-9+#.\-]{1,20}", lowered))
    # 中文按 2-4 字滑窗 + 常见技能词表补充
    chinese = re.findall(r"[\u4e00-\u9fa5]{2,}", lowered)
    for seg in chinese:
        for n in (2, 3, 4):
            for i in range(len(seg) - n + 1):
                tokens.add(seg[i : i + n])
    return tokens


# ============================================================
# 认证 / 数据归属
# ============================================================


def get_guest_user(db: Session) -> User:
    """获取或创建公共游客账号（未登录时的数据归属方）。"""
    return ensure_user(db, "")


def bearer_token(authorization: str | None) -> str:
    """从 `Authorization: Bearer xxx` 头里取出令牌；不合法一律返回空串。"""
    raw = (authorization or "").strip()
    if not raw:
        return ""
    parts = raw.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    # 容忍直接塞裸令牌的调用方（不鼓励，但不要因此 500）
    return parts[0].strip() if len(parts) == 1 else ""


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """当前用户：令牌有效则返回该用户，否则落到公共游客账号。

    这是**所有业务接口取用户身份的唯一入口**——一律以令牌为准，
    绝不再相信请求体/查询串里的 `user_id`（那是客户端可任意伪造的字符串）。
    `settings.allow_guest=false` 时未登录直接 401。
    """
    token = bearer_token(authorization)
    if token:
        user = auth_service.resolve_token(db, token)
        if user is not None:
            return user
        # 带了令牌却无效：明确 401，否则前端会以为自己是登录态、
        # 却把数据写进游客账号，用户看到的是「我的记录不见了」。
        raise HTTPException(
            status_code=401,
            detail="登录状态已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.allow_guest:
        raise HTTPException(
            status_code=401,
            detail="请先登录后再使用",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return get_guest_user(db)


def is_guest(user: User | None) -> bool:
    return user is None or (user.user_id or "") == _GUEST_USER_ID


def require_login(user: User = Depends(current_user)) -> User:
    """需要**真正登录**的接口用这个依赖：游客身份一律 401。

    与 `current_user` 的分工：
      · `current_user` 在 `ALLOW_GUEST=true` 时会把未登录请求放行成公共游客身份，
        适合「公开可读」的接口（公司库、榜单、元信息）；
      · `require_login` 在游客身份上直接 401，用于**会产生个人数据或消耗模型额度**的接口。

    为什么需要它：产品要求「模拟面试 / 我的面试 / 简历优化」仅登录用户可用。
    只在页面上挡是纸糊的门——接口仍能被直接调用。这里把门槛放进依赖，
    前端拦一层、后端拒一层，两边一致。

    返回 401（而不是 403）并带 `WWW-Authenticate`，与前端既有约定一致：
    客户端收到 401 会清令牌并引导登录。
    """
    if is_guest(user):
        raise HTTPException(
            status_code=401,
            detail="请先登录后再使用（游客模式不保存任何数据）",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def assert_owner(row: Any, user: User, what: str = "资源") -> Any:
    """所有权断言：不属于当前用户时返回 **404**（而不是 403）。

    用 404 是刻意的：403 等于告诉攻击者「这个 id 真实存在，只是你没权限」，
    会让人能靠遍历 id 探测出别人有多少份简历/多少场面试。404 不泄露存在性。
    """
    if row is None:
        raise HTTPException(status_code=404, detail=f"{what}不存在")
    owner = getattr(row, "user_id", None)
    if (owner or "") != (user.user_id or ""):
        raise HTTPException(status_code=404, detail=f"{what}不存在")
    return row


def auth_user_payload(user: User) -> dict[str, Any]:
    """把 User 行转成可下发的用户信息（绝不包含 password_hash）。"""
    return {
        "user_id": user.user_id or "",
        "username": user.username or "",
        "nickname": user.nickname or "同学",
        "school": user.school or "",
        "is_member": int(user.is_member or 0),
        "is_guest": is_guest(user),
    }
