"""模块三 · 评分报告路由（API_CONTRACT §2.4）。

注意：`GET /api/report/history` 必须先于 `/{session_id}` 系列路由注册，
否则会被 `/{session_id}` 吃掉。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import assert_owner, dump_json, load_json, local_date_str, local_iso, new_id, require_login
from ..models import InterviewSession, ReportFeedback, User
from ..schemas import (
    InterviewReport,
    OkResponse,
    ReportFeedbackRequest,
    ReportProgressResponse,
)
from ..services import report_progress, report_service

router = APIRouter()


# ============================================================
# 内部助手
# ============================================================


def _get_session(db: Session, session_id: str, user: User) -> InterviewSession:
    """取会话并**校验归属**：别人的报告一律 404（报告含逐题原文，最敏感）。"""
    try:
        row = db.get(InterviewSession, session_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"会话查询失败：{exc}") from exc
    return assert_owner(row, user, "面试会话")


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _has_report(session: InterviewSession) -> bool:
    """是否已有一份**真实可用**的报告。

    不能只判断 `report_json` 非空：`POST /feedback` 在报告尚未生成时会写入
    `{"session_id":..., "meta":{...}}`，只判非空会让这种占位记录被当成「已有报告」，
    历史曲线里也会多出一个 0 分的点。
    """
    text = (session.report_json or "").strip()
    if text in ("", "{}", "null", "[]", "None"):
        return False
    data = load_json(text, {})
    return isinstance(data, dict) and bool(data.get("overview"))


async def _ensure_report(db: Session, session: InterviewSession) -> dict:
    if _has_report(session):
        data = load_json(session.report_json, {})
        if isinstance(data, dict) and data.get("overview"):
            return data
    return await _generate(db, session)


async def _generate(db: Session, session: InterviewSession) -> dict:
    """生成报告，并把进度状态兜住。

    `build_report` 自己已经兜住了所有业务异常（永远返回一份报告），这里再包一层是为了
    保证「进度不会永远停在 running」：任何逃出来的异常都先把进度标记为 failed，
    否则前端会一直显示一个正在评分的假进度，直到 TTL 过期。
    """
    try:
        return await report_service.build_report(db, session)
    except Exception as exc:  # noqa: BLE001
        report_progress.fail(session.session_id or "", str(exc))
        raise


def _as_report(session_id: str, data: dict) -> InterviewReport:
    """契约校验失败也要收尾进度（失败原因如实写进进度里，便于前端提示）。"""
    try:
        return InterviewReport(**data)
    except Exception as exc:  # noqa: BLE001
        report_progress.fail(session_id, f"报告数据校验失败：{exc}")
        raise


# ============================================================
# GET /api/report/history —— 必须先注册（避免被 /{session_id} 吃掉）
# ============================================================


@router.get("/report/history", summary="历史进步追踪（最近 10 次）")
async def report_history(
    db: Session = Depends(get_db), user: User = Depends(require_login)
) -> dict:
    """返回 `{items:[ProgressPoint]}`。

    只返回**当前登录用户**的历史——入参里的 `user_id` 已被刻意移除，
    否则任何人都能靠拼一个 user_id 拉走别人的分数曲线。
    """
    items: list[dict] = []
    try:
        rows = (
            db.execute(
                select(InterviewSession)
                .where(
                    InterviewSession.user_id == user.user_id,
                    InterviewSession.status == "finished",
                )
                .order_by(InterviewSession.created_at.asc())
            )
            .scalars()
            .all()
        )
        valid = [r for r in rows if _has_report(r)]
        for r in valid[-10:]:
            dt = r.finished_at or r.created_at
            date = local_date_str(dt)
            score = r.score
            if not score:
                score = (load_json(r.report_json, {}).get("overview") or {}).get("score") or 0.0
            items.append(
                {
                    "session_id": r.session_id,
                    "date": date,
                    "score": round(float(score or 0.0), 1),
                    "job_title": r.job_title or "",
                }
            )
    except Exception as exc:  # noqa: BLE001 历史查询失败返回空列表，不 500
        print(f"[report] history 查询失败: {exc}")
        items = []
    return {"items": items}


# ============================================================
# POST /api/report/{session_id}/generate
# ============================================================


@router.post(
    "/report/{session_id}/generate",
    response_model=InterviewReport,
    summary="生成/重新生成面试报告",
)
async def generate_report(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> InterviewReport:
    """session 不存在 → 404；未完成（ongoing）也允许生成（用已有对话）。

    生成期间进度可由 `GET /api/report/{session_id}/progress` 读取（阶段清单 / 真实耗时）。
    """
    session = _get_session(db, session_id, user)
    data = await _generate(db, session)
    return _as_report(session_id, data)


# ============================================================
# GET /api/report/{session_id}/progress —— 生成过程可视化
# ============================================================


@router.get(
    "/report/{session_id}/progress",
    response_model=ReportProgressResponse,
    summary="读取报告生成进度（阶段 / 耗时 / 评分方式）",
)
async def report_progress_endpoint(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> ReportProgressResponse:
    """返回生成进度快照。**鉴权与其它报告接口一致**：别人的会话一律 404。

    进度是进程内的瞬时状态，因此是**只读、无副作用**的：
    - 从未生成过 / 生成早已结束且记录已过期 → `available=false`、`status=idle`；
    - 多进程部署时请求可能落在别的进程 → 同样是 `available=false`，
      前端据此退化为「不确定进度条」，而不是显示一个假的百分比。
    """
    session = _get_session(db, session_id, user)
    return ReportProgressResponse(**report_progress.snapshot(session.session_id or ""))


# ============================================================
# GET /api/report/{session_id}
# ============================================================


@router.get("/report/{session_id}", response_model=InterviewReport, summary="获取面试报告（未生成则自动生成）")
async def get_report(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> InterviewReport:
    session = _get_session(db, session_id, user)
    data = await _ensure_report(db, session)
    return _as_report(session_id, data)


# ============================================================
# GET /api/report/{session_id}/export
# ============================================================


@router.get("/report/{session_id}/export", summary="导出报告（markdown | json）")
async def export_report(
    session_id: str,
    format: str = Query(default="markdown", pattern="^(markdown|json)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> dict:
    session = _get_session(db, session_id, user)
    report = await _ensure_report(db, session)

    job_title = session.job_title or (report.get("overview") or {}).get("job_title") or "目标岗位"
    date = local_iso()[:10].replace("-", "")

    if format == "json":
        return {
            "filename": f"面试报告_{job_title}_{date}.json",
            "content": dump_json(report),
        }

    content = report_service.report_to_markdown(report)
    return {
        "filename": f"面试报告_{job_title}_{date}.md",
        "content": content,
    }


# ============================================================
# POST /api/report/{session_id}/feedback
# ============================================================


@router.post("/report/{session_id}/feedback", response_model=OkResponse, summary="报告 / 单题评分反馈")
async def report_feedback(
    session_id: str,
    req: ReportFeedbackRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> OkResponse:
    """体验增强 §6：既支持整份报告「有用/没用」，也支持逐题「准 / 偏高 / 偏低」。

    逐题反馈是低成本的 Prompt 调优闭环：写进 `report_feedback` 表后可按
    session / question 统计，再把偏差样本回灌到评分 Prompt。
    """
    session = _get_session(db, session_id, user)

    # 1) 逐题反馈 / 整体反馈统一落表（便于后续统计，不必反序列化 report_json）
    question_index = -1 if req.question_index is None else int(req.question_index)
    verdict = str(req.verdict or "")
    if question_index >= 0 and verdict:
        # 题号必须在报告范围内，否则前端传错会让反馈挂到不存在的题上
        total = _to_int((load_json(session.plan_json, {}) or {}).get("total"), 0)
        if total and question_index > total:
            raise HTTPException(status_code=400, detail="题号超出本场范围")

    if question_index >= 0 or verdict or req.helpful is not None or req.comment:
        try:
            db.add(
                ReportFeedback(
                    user_id=user.user_id or "",
                    session_id=session.session_id,
                    question_index=question_index,
                    verdict=verdict,
                    helpful=-1 if req.helpful is None else (1 if req.helpful else 0),
                    comment=str(req.comment or ""),
                )
            )
            db.commit()
        except Exception as exc:  # noqa: BLE001 反馈写失败不该 500，但要如实返回
            print(f"[report] 反馈落库失败（已忽略）: {exc}")
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass

    # 2) 写穿到已生成的报告：GET 直接返回存量报告（不重新评分），
    #    所以反馈必须**同时**落进 report_json，否则用户刷新后看不到自己的反馈。
    try:
        report = load_json(session.report_json, {})
        if isinstance(report, dict) and report.get("overview"):
            if verdict and question_index >= 0:
                for q in report.get("questions") or []:
                    if isinstance(q, dict) and _to_int(q.get("index"), -1) == question_index:
                        q["feedback_verdict"] = verdict
                        break
            meta = report.get("meta")
            if not isinstance(meta, dict):
                meta = {}
            if req.helpful is not None:
                meta["feedback_helpful"] = bool(req.helpful)
            if req.comment:
                meta["feedback_comment"] = req.comment
            report["meta"] = meta
            session.report_json = dump_json(report)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 反馈保存失败: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    if verdict:
        return OkResponse(ok=True, message="已记录，感谢反馈，我们会持续校准评分")
    if req.helpful is None:
        return OkResponse(ok=True, message="已记录你的反馈")
    return OkResponse(ok=True, message="感谢反馈" if req.helpful else "已记录你的反馈，我们会持续优化")


# ============================================================
# GET /api/report/{session_id}/share
# ============================================================


@router.get("/report/{session_id}/share", summary="生成分享数据（海报由前端绘制）")
async def share_report(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> dict:
    session = _get_session(db, session_id, user)
    report = await _ensure_report(db, session)

    ov = report.get("overview") or {}
    dims = [
        {"name": d.get("name", ""), "score": d.get("score", 0)}
        for d in (report.get("dimensions") or [])
    ]
    share_id = new_id()[:12]
    scored_at = (report.get("meta") or {}).get("scored_at") or ""
    date = scored_at[:10] if scored_at else local_iso()[:10]

    poster_data = {
        "score": ov.get("score", 0),
        "percent": ov.get("percent", 0),
        "grade": ov.get("grade", ""),
        "grade_desc": ov.get("grade_desc", ""),
        "job_title": session.job_title or "",
        "company_name": session.company_name or "",
        "dimensions": dims,
        "date": date,
        "comment": ov.get("one_line_comment", ""),
    }
    return {
        "share_id": share_id,
        "url": f"/share/report/{share_id}",
        "poster_data": poster_data,
    }
