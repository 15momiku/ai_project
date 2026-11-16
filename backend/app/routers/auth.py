"""认证路由：注册 / 登录 / 退出 / 当前用户（API_CONTRACT §2.0）。

安全约定：
- 密码只以 PBKDF2 派生值入库，接口任何响应都不回显口令学字段；
- 登录失败统一提示「账号或密码不正确」，不区分账号不存在与密码错误（防账号枚举）；
- 同一账号+IP 连续失败会被限流，避免离线字典暴力破解；
- 退出登录删除服务端令牌行，立即失效。
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import auth_user_payload, bearer_token, current_user
from ..models import InterviewSession, JobDescription, Resume, User
from ..schemas import (
    AuthResponse,
    AuthUser,
    LoginRequest,
    OkResponse,
    RegisterRequest,
)
from ..services import auth as auth_service

router = APIRouter()


# ============================================================
# 登录限流（进程内，够用于单实例部署；多实例请换成 Redis）
# ============================================================

_MAX_FAILURES = 8
_WINDOW_SECONDS = 15 * 60
_failures: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "") or "unknown"


def _throttle_key(request: Request, username: str) -> str:
    return f"{_client_ip(request)}|{(username or '').strip().lower()}"


def _check_throttle(key: str) -> None:
    """失败次数超限时直接拒绝（密码正确也不放行，必须等窗口过期）。"""
    stamps = _failures.get(key) or []
    now = time.time()
    fresh = [t for t in stamps if now - t < _WINDOW_SECONDS]
    if len(fresh) >= _MAX_FAILURES:
        wait_min = max(1, int((_WINDOW_SECONDS - (now - fresh[0])) / 60) + 1)
        raise HTTPException(
            status_code=429,
            detail=f"登录失败次数过多，请 {wait_min} 分钟后再试",
        )
    _failures[key] = fresh


def _record_failure(key: str) -> None:
    _failures.setdefault(key, []).append(time.time())


def _clear_failures(key: str) -> None:
    _failures.pop(key, None)


# ============================================================
# 接口
# ============================================================


@router.post("/auth/register", response_model=AuthResponse, summary="注册新账号并自动登录")
async def register(req: RegisterRequest, db: Session = Depends(get_db)) -> AuthResponse:
    try:
        user = auth_service.register(
            db,
            username=req.username,
            password=req.password,
            nickname=req.nickname,
            school=req.school,
        )
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001 注册失败绝不 500 白屏
        print(f"[auth] 注册失败: {exc}")
        raise HTTPException(status_code=400, detail="注册失败，请稍后重试") from exc

    token, expires = auth_service.issue_token(db, user)
    return AuthResponse(
        token=token,
        expires_at=expires.isoformat(timespec="seconds"),
        message="注册成功，全部功能已对登录用户免费开放",
        # 与 /auth/me 同一种形状（含准备度计数），前端登录后不必再补一次请求
        user=AuthUser(**_full_user_payload(db, user)),
    )


@router.post("/auth/login", response_model=AuthResponse, summary="账号密码登录")
async def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)) -> AuthResponse:
    key = _throttle_key(request, req.username)
    _check_throttle(key)
    try:
        user = auth_service.authenticate(db, req.username, req.password)
    except auth_service.AuthError as exc:
        _record_failure(key)
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] 登录失败: {exc}")
        raise HTTPException(status_code=400, detail="登录失败，请稍后重试") from exc

    _clear_failures(key)
    token, expires = auth_service.issue_token(db, user)
    return AuthResponse(
        token=token,
        expires_at=expires.isoformat(timespec="seconds"),
        # 同样用完整载荷：登录后立刻能看到准备度清单，不必再补一次 /auth/me
        user=AuthUser(**_full_user_payload(db, user)),
    )


@router.post("/auth/logout", response_model=OkResponse, summary="退出登录（吊销当前令牌）")
async def logout(request: Request, db: Session = Depends(get_db)) -> OkResponse:
    token = bearer_token(request.headers.get("authorization"))
    auth_service.revoke_token(db, token)
    # 即使令牌本来就无效也返回成功：退出登录必须幂等，不能因为状态不一致就报错
    return OkResponse(ok=True, message="已退出登录")


@router.get("/auth/me", response_model=AuthUser, summary="当前登录用户（未登录返回游客身份）")
async def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> AuthUser:
    """返回当前身份，含三个「有没有」的计数。

    一律用 `_full_user_payload()` 组装：**注册 / 登录 / me 三个接口必须返回同一种形状**。
    曾经只有 `/auth/me` 填 has_resume / finished_sessions，而登录与注册响应里是
    默认值（False / 0）——前端登录后直接拿登录响应里的 user，于是
    「刚登录看不到准备度、要刷新一次才出现」。同一个字段时有时无，是这类 bug 的根源。
    """
    return AuthUser(**_full_user_payload(db, user))


def _full_user_payload(db: Session, user: User) -> dict:
    """身份载荷的唯一组装入口：基础信息 + 准备度计数。

    每一项都用 try/except 兜住：身份接口是前端启动的必经之路，
    任何一项查库失败都不该让它 500（失败时该项按「没有」处理，页面照常可用）。
    """
    payload = auth_user_payload(user)
    payload["has_resume"] = _has_any(db, Resume, user)
    payload["has_jd"] = _has_any(db, JobDescription, user)
    payload["finished_sessions"] = _count_finished(db, user)
    return payload


def _has_any(db: Session, model, user: User) -> bool:
    """该用户是否至少有一条该类记录（只查 1 条，不做全表统计）。"""
    try:
        return (
            db.query(model.user_id)
            .filter(model.user_id == (user.user_id or ""))
            .first()
            is not None
        )
    except Exception as exc:  # noqa: BLE001 身份接口不能因为统计失败而 500
        print(f"[auth] 准备度统计失败（按未创建处理）: {exc}")
        return False


def _count_finished(db: Session, user: User) -> int:
    try:
        return int(
            db.query(InterviewSession)
            .filter(
                InterviewSession.user_id == (user.user_id or ""),
                InterviewSession.status == "finished",
            )
            .count()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] 已完成会话统计失败（按 0 处理）: {exc}")
        return 0


@router.get("/auth/status", summary="认证配置：是否允许游客、令牌有效期")
async def auth_status() -> dict:
    """前端据此决定是否显示「以游客身份继续」入口。不含任何敏感信息。"""
    return {
        "allow_guest": bool(settings.allow_guest),
        "token_ttl_hours": float(settings.auth_token_ttl_hours or 0.0),
    }
