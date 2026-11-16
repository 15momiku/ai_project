"""模块三 · 错题本 / 排行榜路由（API_CONTRACT §2.5）。

接口：GET wrong-questions / POST retry / DELETE wrong-questions/{id} / GET leaderboard。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..constants import DIMENSIONS
from ..db import get_db
from ..deps import assert_owner, current_user, require_login
from ..models import InterviewSession, User, WrongQuestion
from ..schemas import (
    GrowthSummary,
    InterviewStartRequest,
    LeaderboardResponse,
    MicroSessionPlan,
    OkResponse,
    RetryWrongQuestionRequest,
    RetryWrongQuestionResponse,
    WrongQuestionListResponse,
)
from ..services import analytics, practice_store

router = APIRouter()


# ============================================================
# GET /api/practice/wrong-questions
# ============================================================


@router.get(
    "/practice/wrong-questions",
    response_model=WrongQuestionListResponse,
    summary="错题本列表（可按分类过滤）",
)
async def wrong_questions(
    category: str = Query(default=""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> WrongQuestionListResponse:
    """错题本只属于当前登录用户（契约里的 `user_id` 入参已移除，由令牌决定）。"""
    data = practice_store.list_wrong_questions(db, user.user_id, category)
    return WrongQuestionListResponse(**data)


# ============================================================
# POST /api/practice/retry —— 重做错题 / 开单题特训
# ============================================================


@router.post(
    "/practice/retry",
    response_model=RetryWrongQuestionResponse,
    summary="重做错题：返回参考思路，并可选开一场 3 题微面试",
)
async def retry_wrong_question(
    req: RetryWrongQuestionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> RetryWrongQuestionResponse:
    """体验增强 §9：把「重做一道错题」从"再跑整场 9 题"变成"练 3 题"。

    `start_micro=True` 时会用原题的**同一环节**建一场 3 题微面试，
    并带上该维度的历史得分作为前后对比基准；建会话失败不影响参考思路返回
    （错题本的核心价值是"能立刻看到怎么答"，不能因为会话创建失败就整体失败）。
    """
    try:
        row = db.get(WrongQuestion, req.wrong_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"错题查询失败：{exc}") from exc
    # 别人的错题一律 404：错题含原题与你的原始回答，不能靠 id 猜读
    row = assert_owner(row, user, "错题")

    tips = practice_store.build_retry_tips(row)
    row.retry_count = int(row.retry_count or 0) + 1
    try:
        db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[practice] 重做次数保存失败: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    micro = await _start_micro_session(db, user, row) if req.start_micro else None

    return RetryWrongQuestionResponse(
        wrong_id=row.id,
        question=row.question or "",
        reference=row.reference or "",
        tips=tips,
        retry_count=int(row.retry_count or 0),
        micro=micro,
    )


async def _start_micro_session(db: Session, user: User, row: WrongQuestion) -> MicroSessionPlan | None:
    """按原题环节建一场 3 题微面试；任何失败都返回 None 而不是让接口 500。"""
    try:
        src = db.get(InterviewSession, row.session_id)
        if src is None or not src.resume_id or not src.jd_id:
            return None
        section = practice_store.focus_section_of(row)
        dim_key = practice_store.dimension_key_of(row)
        dim_name = next((d["name"] for d in DIMENSIONS if d["key"] == dim_key), dim_key)

        # 直接复用面试开始接口：匹配度、计划、开场白、会话落库全部同一套逻辑，
        # 避免在错题本里再抄一遍建会话的代码。
        from .interview import interview_start

        started = await interview_start(
            InterviewStartRequest(
                resume_id=src.resume_id,
                jd_id=src.jd_id,
                company_id=src.company_id or "",
                mode=src.mode or "text",
                interviewer=src.interviewer or "female",
                question_count=3,
                retry_mode=True,
                focus_section=section,
                focus_dimension=dim_name,
            ),
            db=db,
            user=user,
        )
        trend = practice_store.dimension_trend_for(db, user.user_id or "", dim_key)
        return MicroSessionPlan(
            session_id=started.session_id,
            total=int(started.total_questions or 3),
            focus_section=section,
            focus_dimension=dim_key,
            dimension_name=dim_name,
            before_score=trend[-1]["score"] if trend else 0.0,
            before_trend=trend,
            opening=started.opening,
            plan=started.plan,
            match=started.match,
        )
    except Exception as exc:  # noqa: BLE001 微面试是增强能力，失败要能降级
        print(f"[practice] 微面试创建失败（已降级为仅参考思路）: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None


# ============================================================
# DELETE /api/practice/wrong-questions/{id}
# ============================================================


@router.delete("/practice/wrong-questions/{wrong_id}", response_model=OkResponse, summary="删除错题")
async def delete_wrong_question(
    wrong_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> OkResponse:
    try:
        row = db.get(WrongQuestion, wrong_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"错题查询失败：{exc}") from exc
    # 只能删自己的错题（别人的返回 404，不暴露其存在）
    row = assert_owner(row, user, "错题")
    try:
        db.delete(row)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[practice] 删除错题失败: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return OkResponse(ok=False, message="删除失败，请稍后重试")
    return OkResponse(ok=True, message="已从错题本移除")


# ============================================================
# GET /api/leaderboard
# ============================================================


@router.get("/leaderboard", response_model=LeaderboardResponse, summary="排行榜（岗位 / 校园 / 进步）")
async def leaderboard(
    type: str = Query(default="job"),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
) -> LeaderboardResponse:
    """排行榜是**公开榜单**（契约 §2.5：榜单本身对所有访客开放），因此这里用
    `current_user` 而不是 `require_login`：游客也能看榜单，「我的排名」只按当前身份算。

    与「模拟面试 / 我的面试 / 简历优化」的区别在于：它不产生也不暴露任何**个人**数据，
    是产品对外的展示面（数据不足时带 `note` 标注为演示数据）。
    """
    data = practice_store.build_leaderboard(db, type, user.user_id)
    return LeaderboardResponse(**data)


# ============================================================
# GET /api/growth/summary —— 成长面板（体验增强 §12）
# ============================================================


@router.get("/growth/summary", response_model=GrowthSummary, summary="我的成长面板（连续打卡 / 趋势 / 下一步目标）")
async def growth_summary(
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> GrowthSummary:
    """只统计**当前用户自己**的已完成面试；按本地时区归日。

    与排行榜放在同一个路由模块：两者共同回答"我跟别人比怎么样 / 我自己在进步吗"。
    """
    data = analytics.build_growth(db, user.user_id or "")
    return GrowthSummary(**data)
