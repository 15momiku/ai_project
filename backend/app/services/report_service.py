"""报告编排服务（文档 §5.2 报告 7 个板块 + §1.4 固定签名）。

`build_report` 自行从 DB 读取 resume / jd / company / 历史会话，编排：
逐题记录 → 六维评分 → 7 天计划 → 历史追踪 → 组装 `InterviewReport` → 落库 + 收集错题。
任何异常都降级，保证"永远能出报告"（文档 §4.7 降级总原则）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import DIMENSION_ANCHORS, DIMENSIONS, INTERVIEW_SECTIONS, TARGET_RADAR
from ..deps import dump_json, load_json, local_iso, round1
from ..models import (
    Company,
    InterviewSession,
    JobDescription,
    ReportFeedback,
    Resume,
    SpeechMetric,
)
from ..schemas import InterviewReport
from . import analytics, benchmark, practice_store, report_progress, speech_metrics
from .llm import llm_status
from .plan import build_plan
from .scoring import basis_of, build_ctx, build_question_records, grade_of, score_session, span_of

_SKIP_ANSWERS = {"", "（跳过本题）", "(跳过本题)", "（跳过）"}

# 六维评分内部的子步骤文案（scoring.score_session 通过 on_stage 回调上报）。
# 两次大模型调用是整份报告最慢的一段，把「第几轮」说清楚，用户才不会以为卡死了。
_SCORE_SUBSTAGE = {
    "rule": "规则基线（本地计算）",
    "llm_first": "第 1 轮大模型评分",
    "llm_second": "二次校验（专业能力 / 岗位匹配度）",
}


def _to_int(value: Any, default: int = 0) -> int:
    """安全转 int：脏数据一律回落默认值，避免单条记录把整份报告打回兜底版本。"""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ============================================================
# DB 读取助手
# ============================================================


def _load_resume(db: Session, resume_id: str) -> dict:
    if not resume_id:
        return {}
    try:
        row = db.get(Resume, resume_id)
        return load_json(row.parsed_json, {}) if row else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 读取简历失败: {exc}")
        return {}


def _load_jd(db: Session, jd_id: str) -> dict:
    if not jd_id:
        return {}
    try:
        row = db.get(JobDescription, jd_id)
        return load_json(row.parsed_json, {}) if row else {}
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 读取 JD 失败: {exc}")
        return {}


def _load_company(db: Session, company_id: str) -> dict | None:
    if not company_id:
        return None
    try:
        row = db.get(Company, company_id)
        if not row:
            return None
        profile = load_json(row.profile_json, {})
        if isinstance(profile, dict) and not profile.get("name"):
            profile["name"] = row.name
        return profile
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 读取公司失败: {exc}")
        return None


def _dims_map(report: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in (report or {}).get("dimensions") or []:
        if not isinstance(d, dict):
            continue
        try:
            out[str(d.get("key"))] = float(d.get("score") or 0.0)
        except (TypeError, ValueError):
            continue
    return out


def _previous_dims(db: Session, session: InterviewSession) -> dict[str, float]:
    """取该用户上一次已生成报告会话的六维分数（用于计算 improved）。"""
    try:
        rows = (
            db.execute(
                select(InterviewSession)
                .where(
                    InterviewSession.user_id == session.user_id,
                    InterviewSession.session_id != session.session_id,
                )
                .order_by(InterviewSession.created_at.desc())
            )
            .scalars()
            .all()
        )
        for r in rows:
            dims = _dims_map(load_json(r.report_json, {}))
            if dims:
                return dims
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 读取历史维度失败: {exc}")
    return {}


# ============================================================
# 组装
# ============================================================


def _enrich_dimensions(
    fragment_dims: list[dict],
    prev_dims: dict[str, float],
    records: list[dict] | None = None,
    ctx: dict | None = None,
    bench: dict | None = None,
) -> list[dict]:
    """补上 name / weight / target / improved，以及体验增强 §6 的归因与基准。"""
    bench = bench or {}
    bench_dims = bench.get("dims") or {}
    bench_common = {
        "available": bool(bench.get("available")),
        "note": str(bench.get("note") or ""),
        "sample_size": int(bench.get("sample_size") or 0),
        "scope": str(bench.get("scope") or "all"),
    }
    out: list[dict] = []
    for d in fragment_dims or []:
        key = str(d.get("key") or "")
        meta = next((x for x in DIMENSIONS if x["key"] == key), None)
        name = meta["name"] if meta else (key or "未知维度")
        weight = float(meta["weight"]) if meta else 0.0
        try:
            score = round1(float(d.get("score") or 0.0))
        except (TypeError, ValueError):
            score = 0.0
        improved = 0.0
        if key in prev_dims:
            improved = round1(score - float(prev_dims[key]))
        evidence = [str(x) for x in (d.get("evidence") or []) if str(x).strip()]

        # 评分依据：列出该维度实际看过的可数事实（与得分口径同源）
        basis = basis_of(key, records or [], ctx or {}) if records is not None else []

        # 评价锚点：按本场得分就近取一档，避免把三档全列出来制造噪音
        anchors = [
            {"score": float(a["score"]), "level": a["level"], "example": a["example"]}
            for a in (DIMENSION_ANCHORS.get(key) or [])
        ]
        anchor = None
        if anchors:
            anchor = min(anchors, key=lambda a: abs(a["score"] - score))

        dim_bench = dict(bench_common)
        if dim_bench["available"] and name in bench_dims:
            dim_bench.update(bench_dims[name])
        elif dim_bench["available"]:
            # 该维度样本不足：整份基准可用但这一维不够，如实降级而不是拿总分的刻度冒充
            dim_bench["available"] = False
            dim_bench["note"] = "该维度历史样本不足，暂不展示基准刻度"

        out.append(
            {
                "key": key,
                "name": name,
                "score": score,
                "weight": weight,
                "comment": str(d.get("comment") or ""),
                "evidence": evidence or ["（本场未采集到有效回答原文）"],
                "target": float(TARGET_RADAR.get(name, 0.0)),
                "improved": improved,
                "basis": basis,
                "anchors": [anchor] if anchor else [],
                "benchmark": dim_bench,
            }
        )
    return out


def _speech_map(db: Session, session: InterviewSession) -> dict[int, dict]:
    """该会话的口播指标，按题号索引（同题多次上报取最后一条）。"""
    out: dict[int, dict] = {}
    try:
        rows = (
            db.query(SpeechMetric)
            .filter(
                SpeechMetric.session_id == session.session_id,
                # 必须显式加括号：`== x or ""` 会被解析成 `(== x) or ""`，
                # 传给 filter 的会变成空字符串并直接抛 SQL 表达式错误。
                SpeechMetric.user_id == (session.user_id or ""),
            )
            .order_by(SpeechMetric.created_at.asc())
            .all()
        )
    except Exception as exc:  # noqa: BLE001 口播指标是增强信息，读失败不影响报告
        print(f"[report] 读取口播指标失败（已忽略）: {exc}")
        return out

    for row in rows:
        cpm = float(row.chars_per_minute or 0.0)
        count = int(row.filler_count or 0)
        secs = int(row.duration_seconds or 0)
        words = load_json(row.filler_words_json, [])
        out[int(row.question_index or 0)] = {
            "ok": True,
            "saved": True,
            "chars_per_minute": cpm,
            "filler_count": count,
            "filler_words": [str(x) for x in (words if isinstance(words, list) else [])],
            "level": str(row.level or "good"),
            "note": speech_metrics.note_of(cpm, count, secs),
        }
    return out


def _feedback_map(db: Session, session: InterviewSession) -> dict[int, str]:
    """逐题评分反馈：题号 -> verdict（同题多次反馈取最后一条）。

    仅用于**生成**报告时回填；报告已存在时由反馈接口直接写进 `report_json`
    （见 `routers/report.py::report_feedback`），避免每次 GET 都查一次表。
    """
    out: dict[int, str] = {}
    try:
        rows = (
            db.query(ReportFeedback)
            .filter(
                ReportFeedback.session_id == session.session_id,
                ReportFeedback.user_id == (session.user_id or ""),
                ReportFeedback.question_index >= 0,
            )
            .order_by(ReportFeedback.created_at.asc())
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 读取逐题反馈失败（已忽略）: {exc}")
        return out
    for row in rows:
        verdict = str(row.verdict or "")
        if verdict:
            out[int(row.question_index)] = verdict
    return out


def _build_questions(
    records: list[dict],
    frag_questions: list[dict],
    speech_map: dict[int, dict] | None = None,
    feedback_map: dict[int, str] | None = None,
    ctx: dict | None = None,
) -> list[dict]:
    q_map: dict[int, dict] = {}
    for q in frag_questions or []:
        if not isinstance(q, dict):
            continue
        try:
            q_map[int(q.get("index"))] = q
        except (TypeError, ValueError):
            continue

    speech_map = speech_map or {}
    feedback_map = feedback_map or {}
    ctx = ctx or {}

    out: list[dict] = []
    for r in records:
        idx = _to_int(r.get("index"), 0)
        if idx <= 0:
            continue
        answer = (r.get("answer") or "").strip()
        secs = _to_int(r.get("answer_seconds"), 0)
        # 跳过题/未作答是用户的主动选择，不能算「回答过短」——
        # 否则报告里每道跳过的题都挂着 ⚠️，还会进防作弊标记。
        skipped = answer in _SKIP_ANSWERS
        too_short = (not skipped) and (secs < 10 or len(answer) < 15)
        qd = q_map.get(idx, {})
        try:
            q_score = round1(float(qd.get("score"))) if qd.get("score") is not None else 5.0
        except (TypeError, ValueError):
            q_score = 5.0
        out.append(
            {
                "index": idx,
                "section": r.get("section") or "",
                "focus": r.get("focus") or "",
                "question": r.get("question") or "",
                "answer": answer,
                "score": q_score,
                "good": [str(x) for x in (qd.get("good") or [])],
                "improve": [str(x) for x in (qd.get("improve") or [])],
                "reference": str(qd.get("reference") or ""),
                "answer_seconds": secs,
                "too_short": too_short,
                # 体验增强 §4：四种状态必须可区分（未作答 / 重录 / 依赖提示 / 单纯低分）
                "skipped": skipped,
                "retake_count": 1 if r.get("retake") else 0,
                "hint_used": bool(r.get("hint_used")),
                "evidence_spans": [] if skipped else span_of(answer, ctx),
                "feedback_verdict": str(feedback_map.get(idx) or ""),
                "speech": speech_map.get(idx),
            }
        )
    return out


def _derive_weak_points(dimensions: list[dict], improvements: list[dict]) -> list[str]:
    """薄弱项 = 高优先级改进项（维度名 + 问题）+ 低分维度名，用于生成 7 天计划。"""
    pts: list[str] = []
    for imp in improvements or []:
        if not isinstance(imp, dict):
            continue
        if imp.get("priority") == "high":
            dim = str(imp.get("dimension") or "").strip()
            prob = str(imp.get("problem") or "").strip()
            if dim:
                pts.append(dim)
            if prob:
                pts.append(prob[:60])
    for d in dimensions or []:
        try:
            if float(d.get("score") or 0.0) < 7.0:
                pts.append(str(d.get("name") or ""))
        except (TypeError, ValueError):
            continue
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for p in pts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq[:6]


def _assemble(session: InterviewSession, data: dict) -> dict:
    """用 InterviewReport 校验兜底；失败则返回最小可用报告。"""
    try:
        report = InterviewReport(**data).model_dump()
        return report
    except Exception as exc:  # noqa: BLE001
        print(f"[report] 报告校验失败，使用兜底版本: {exc}")
        return _emergency_report(session)


def _emergency_report(session: InterviewSession) -> dict:
    dims = []
    for d in DIMENSIONS:
        dims.append(
            {
                "key": d["key"],
                "name": d["name"],
                "score": 6.0,
                "weight": float(d["weight"]),
                "comment": f"「{d['name']}」按中性基准 6.0 分计（报告降级模式）。",
                "evidence": ["（报告生成异常，未采集到有效回答原文）"],
                "target": float(TARGET_RADAR.get(d["name"], 0.0)),
                "improved": 0.0,
            }
        )
    g = grade_of(6.0)
    duration = max(1, int((session.duration_seconds or 0) // 60)) if session else 1
    return {
        "session_id": getattr(session, "session_id", ""),
        "overview": {
            "score": 6.0,
            "percent": 60,
            "grade": g["grade"],
            "grade_desc": g["desc"],
            "offer_probability": g["offer"],
            "one_line_comment": "报告生成过程中出现异常，当前为降级版本，建议稍后重新生成以获得准确评分。",
            "duration_minutes": duration,
            "question_count": 0,
            "answered_count": 0,
        },
        "dimensions": dims,
        "radar_target": dict(TARGET_RADAR),
        "questions": [],
        "strengths": [
            {"title": "完成了一场完整模拟", "detail": "能够走完全流程，是持续提升的基础。", "evidence": ""},
            {"title": "有明确的岗位目标", "detail": "针对目标岗位进行专项练习，方向清晰。", "evidence": ""},
            {"title": "具备复盘意识", "detail": "主动查看报告并复盘，是拉开差距的关键习惯。", "evidence": ""},
        ],
        "improvements": [],
        "plan7days": [],
        "progress": {"history": [], "fastest_dimension": "", "fastest_delta": 0.0, "still_weak_dimension": "", "summary": "报告降级生成，历史追踪暂不可用。"},
        "meta": {
            "scored_at": local_iso(),
            "model": "mock-rule-engine",
            "scoring_mode": "rule",
            "cheating_flags": [],
            "feedback_helpful": None,
            # 默认 False：这条兜底报告会被正常落库；只有真的没写进 DB 时
            # 才由 build_report 置为 True（见下方落库与异常分支）。
            "persist_failed": False,
        },
    }


# ============================================================
# 对外主入口（§1.4）
# ============================================================


async def build_report(db: Session, session: InterviewSession) -> dict:
    """完整编排，返回 `InterviewReport` 的 dict 形态（7 个板块齐全）。

    同时向 `report_progress` 上报阶段进度（报告生成可视化进度）：
    整份报告最慢的是两次大模型评分，前端据此显示阶段清单、当前子步骤与真实耗时，
    而不是一个转圈的骨架屏。埋点只写进程内进度表，**不改变本函数的返回值**。
    """
    sid = session.session_id or ""
    report_progress.begin(sid, session.user_id or "")
    try:
        report_progress.stage(sid, "load")
        resume = _load_resume(db, session.resume_id)
        jd = _load_jd(db, session.jd_id)
        company = _load_company(db, session.company_id)

        dialogue = load_json(session.dialogue_json, [])
        if not isinstance(dialogue, list):
            dialogue = []
        records = build_question_records(dialogue)

        job_title = session.job_title or (jd.get("basic") or {}).get("title") or "目标岗位"
        company_name = session.company_name or (company or {}).get("name") or ""
        # 单题特训的微面试会在 plan_json 里标记 focus_section/focus_dimension
        plan_of_session = load_json(session.plan_json, {})
        if not isinstance(plan_of_session, dict):
            plan_of_session = {}

        report_progress.stage(sid, "score", "规则基线 + 大模型评分")
        fragment, mode = await score_session(
            job_title,
            company_name,
            resume,
            jd,
            company,
            dialogue,
            records,
            on_stage=lambda key: report_progress.stage(sid, "score", _SCORE_SUBSTAGE.get(key, "")),
        )
        report_progress.set_mode(sid, mode)

        report_progress.stage(sid, "enrich")
        prev_dims = _previous_dims(db, session)
        # 体验增强 §6：评分归因需要与评分引擎同一批词表；基准取自已完成会话的实测分布
        ctx = build_ctx(resume, jd, company)
        bench = benchmark.benchmarks(db, job_title)
        dimensions = _enrich_dimensions(
            fragment.get("dimensions") or [], prev_dims, records, ctx, bench
        )

        improvements = [x for x in (fragment.get("improvements") or []) if isinstance(x, dict)]
        weak_points = _derive_weak_points(dimensions, improvements)
        report_progress.stage(sid, "plan", "生成改进建议与 7 天计划")
        plan7days = await build_plan(weak_points, job_title, improvements)

        progress = analytics.build_progress(db, session.user_id or "", session.session_id, dimensions)

        report_progress.stage(sid, "questions")
        questions = _build_questions(
            records,
            fragment.get("questions") or [],
            _speech_map(db, session),
            _feedback_map(db, session),
            ctx,
        )
        answered = sum(1 for r in records if (r.get("answer") or "").strip() not in _SKIP_ANSWERS)
        question_count = sum(1 for r in records if int(r.get("index") or 0) > 0)

        # 体验增强 §4/§6：重录与依赖提示要如实汇总，并给出「独立作答率」——
        # 用了提示的题不算独立完成，否则用户会高估自己的真实水平。
        retake_count = _to_int(getattr(session, "retake_count", 0), 0)
        hint_count = _to_int(getattr(session, "hint_count", 0), 0)
        real_answers = [q for q in questions if not q.get("skipped")]
        independent = (
            round1(len([q for q in real_answers if not q.get("hint_used")]) / len(real_answers))
            if real_answers
            else 1.0
        )

        overview = dict(fragment.get("overview") or {})
        overview["score"] = round1(overview.get("score") or 0.0)
        overview["percent"] = int(overview.get("percent") or round((overview["score"]) * 10))
        overview["duration_minutes"] = max(1, int((session.duration_seconds or 0) // 60))
        overview["question_count"] = question_count
        overview["answered_count"] = answered

        # 保留历史反馈
        old_report = load_json(session.report_json, {})
        old_meta = old_report.get("meta") if isinstance(old_report, dict) else {}
        feedback = old_meta.get("feedback_helpful") if isinstance(old_meta, dict) else None

        status = llm_status()
        meta = {
            "scored_at": local_iso(),
            "model": str(status.get("model") or "mock-rule-engine"),
            "scoring_mode": mode if mode in ("llm", "rule", "hybrid") else "rule",
            "cheating_flags": [str(x) for x in (fragment.get("cheating_flags") or [])],
            "feedback_helpful": feedback,
            "ai_generated": True,
            "benchmark_note": str(bench.get("note") or ""),
        }

        data = {
            "session_id": session.session_id,
            "overview": overview,
            "dimensions": dimensions,
            "radar_target": dict(TARGET_RADAR),
            "questions": questions,
            "strengths": [x for x in (fragment.get("strengths") or []) if isinstance(x, dict)],
            "improvements": improvements,
            "plan7days": plan7days,
            "progress": progress,
            "meta": meta,
            "benchmark": {
                k: v for k, v in bench.items() if k != "dims"
            },
            "retake_count": retake_count,
            "hint_count": hint_count,
            "independent_answer_rate": independent,
            # 体验增强 §9：单题特训的场次要在报告里说明"这次只练了哪一个环节"
            "retry_section": str(plan_of_session.get("focus_section") or ""),
            "retry_dimension": str(plan_of_session.get("focus_dimension") or ""),
            # 体验增强 §14：压力面试模式与行业题库命中情况
            "pressure_mode": bool(plan_of_session.get("pressure_mode")),
            "industry": str(plan_of_session.get("industry") or ""),
            "industry_bank": bool(plan_of_session.get("industry_bank")),
        }

        report = _assemble(session, data)

        report_progress.stage(sid, "persist")
        # 落库。失败时不能静默：报告虽然照常返回，但没进数据库，
        # 用户下次打开会重新评分、分数可能不同，必须通过 meta.persist_failed 告知前端。
        try:
            session.report_json = dump_json(report)
            session.score = float((report.get("overview") or {}).get("score") or 0.0)
            db.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"[report] 报告落库失败，已回滚并标记 persist_failed: {exc}")
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            meta["persist_failed"] = True
            report["meta"] = meta

        # 收集错题（单题 <6 分）
        practice_store.save_wrong_questions(db, session, report)
        # 走到这里报告一定能返回（哪怕是降级报告）：进度按 done 收尾，
        # 是否降级由报告自身的 meta.scoring_mode / meta.persist_failed 如实说明。
        report_progress.finish(sid)
        return report

    except Exception as exc:  # noqa: BLE001 极端兜底：报告必须能出
        print(f"[report] build_report 异常，返回降级报告: {exc}")
        # 异常可能来自事务中途（例如 commit 抛错），先回滚避免会话停留在坏事务里
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        emergency = _emergency_report(session)
        # 这条降级报告没有落库：下次读取会重新生成，必须告知前端，不能静默装作成功
        # （注意 `_assemble` 校验失败时也会用 `_emergency_report`，但那条路径会正常落库，
        #   所以标记只放在这里，而不是放进 `_emergency_report` 内部）
        try:
            emergency["meta"]["persist_failed"] = True
        except Exception:  # noqa: BLE001
            pass
        report_progress.finish(sid)
        return emergency


# ============================================================
# Markdown 导出
# ============================================================


def _fmt_num(v: Any) -> str:
    try:
        return str(round1(float(v)))
    except (TypeError, ValueError):
        return "0.0"


def report_to_markdown(report: dict) -> str:
    """把报告渲染成结构清晰的 Markdown（用于 `/export?format=markdown`）。"""
    report = report or {}
    ov = report.get("overview") or {}
    lines: list[str] = []

    lines.append(f"# 模拟面试评分报告 · {ov.get('grade', '')}")
    lines.append("")
    lines.append(f"- **综合得分**：{_fmt_num(ov.get('score'))} / 10（百分制 {ov.get('percent', 0)} 分）")
    lines.append(f"- **等级**：{ov.get('grade', '')} —— {ov.get('grade_desc', '')}")
    lines.append(f"- **Offer 概率**：{ov.get('offer_probability', '')}")
    lines.append(f"- **面试时长**：{ov.get('duration_minutes', 0)} 分钟 ｜ **题目数**：{ov.get('question_count', 0)} ｜ **已作答**：{ov.get('answered_count', 0)}")
    lines.append(f"- **一句话评价**：{ov.get('one_line_comment', '')}")
    lines.append("")

    # 六维
    lines.append("## 一、六维评分")
    lines.append("")
    lines.append("| 维度 | 得分 | 权重 | 目标分 | 较上次 | 评语 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for d in report.get("dimensions") or []:
        improved = _fmt_num(d.get("improved"))
        improved_txt = f"+{improved}" if float(d.get("improved") or 0) > 0 else improved
        lines.append(
            f"| {d.get('name', '')} | {_fmt_num(d.get('score'))} | "
            f"{int(float(d.get('weight') or 0) * 100)}% | {_fmt_num(d.get('target'))} | "
            f"{improved_txt} | {d.get('comment', '')} |"
        )
    lines.append("")

    # 逐题
    lines.append("## 二、逐题详细分析")
    lines.append("")
    for q in report.get("questions") or []:
        flag = " ⚠️回答过短" if q.get("too_short") else ""
        lines.append(f"### 第 {q.get('index', '')} 题（{q.get('section', '')}）｜得分 {_fmt_num(q.get('score'))}{flag}")
        lines.append(f"- **考察点**：{q.get('focus', '')}")
        lines.append(f"- **问题**：{q.get('question', '')}")
        lines.append(f"- **我的回答**：{q.get('answer', '') or '（未作答）'}")
        for g in q.get("good") or []:
            lines.append(f"- ✅ 做得好：{g}")
        for im in q.get("improve") or []:
            lines.append(f"- 🔧 待改进：{im}")
        lines.append(f"- 📖 参考答案要点：{q.get('reference', '')}")
        lines.append("")

    # 优势
    lines.append("## 三、核心优势")
    lines.append("")
    for s in report.get("strengths") or []:
        lines.append(f"### {s.get('title', '')}")
        lines.append(s.get("detail", ""))
        if s.get("evidence"):
            lines.append(f"> 证据：{s.get('evidence')}")
        lines.append("")

    # 改进项
    lines.append("## 四、待改进项（按优先级）")
    lines.append("")
    for im in report.get("improvements") or []:
        prio = {"high": "高", "medium": "中", "low": "低"}.get(im.get("priority", "low"), "低")
        lines.append(f"### [{prio}] {im.get('dimension', '')}")
        lines.append(f"- **问题表现**：{im.get('problem', '')}")
        if im.get("evidence"):
            lines.append(f"- **原文证据**：{im.get('evidence')}")
        for i, a in enumerate(im.get("actions") or [], start=1):
            lines.append(f"- 行动 {i}：{a}")
        if im.get("resources"):
            lines.append(f"- 推荐资源：{'；'.join(im.get('resources') or [])}")
        lines.append("")

    # 7 天计划
    lines.append("## 五、7 天提升计划")
    lines.append("")
    lines.append("| 天数 | 主题 | 任务类型 | 任务 | 具体做法 | 时长 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for day in report.get("plan7days") or []:
        tasks = day.get("tasks") or []
        if not tasks:
            lines.append(f"| Day{day.get('day', '')} | {day.get('theme', '')} | - | - | - | - |")
            continue
        for i, t in enumerate(tasks):
            day_txt = f"Day{day.get('day', '')}" if i == 0 else ""
            theme_txt = day.get("theme", "") if i == 0 else ""
            dur = t.get("duration_min", 30)
            lines.append(
                f"| {day_txt} | {theme_txt} | {t.get('type', '')} | {t.get('title', '')} | "
                f"{t.get('detail', '')} | {dur} 分钟 |"
            )
    lines.append("")

    # 历史追踪
    prog = report.get("progress") or {}
    lines.append("## 六、历史进步追踪")
    lines.append("")
    lines.append(f"- **提升最快维度**：{prog.get('fastest_dimension', '') or '暂无'}（+{_fmt_num(prog.get('fastest_delta'))}）")
    lines.append(f"- **仍薄弱维度**：{prog.get('still_weak_dimension', '') or '暂无'}")
    lines.append(f"- **总结**：{prog.get('summary', '')}")
    history = prog.get("history") or []
    if history:
        lines.append("")
        lines.append("| 日期 | 岗位 | 综合分 |")
        lines.append("| --- | --- | --- |")
        for h in history:
            lines.append(f"| {h.get('date', '')} | {h.get('job_title', '')} | {_fmt_num(h.get('score'))} |")
    lines.append("")

    # meta
    meta = report.get("meta") or {}
    lines.append("---")
    lines.append("")
    lines.append(
        f"> 评分时间：{meta.get('scored_at', '')} ｜ 评分模型：{meta.get('model', '')} ｜ "
        f"评分模式：{meta.get('scoring_mode', '')}"
    )
    flags = meta.get("cheating_flags") or []
    if flags:
        lines.append(">")
        lines.append("> ⚠️ 防作弊提示：")
        for f in flags:
            lines.append(f"> - {f}")
    if meta.get("persist_failed"):
        # 导出的文件要如实说明：这份报告没有存进系统，下次打开会重新评分
        lines.append(">")
        lines.append("> ⚠️ 本报告未能保存到系统，重新打开报告页会重新评分，分数可能有差异。")
    lines.append("")

    # 面试环节参考（保证导出内容结构完整）
    lines.append("")
    lines.append("<!-- 面试结构：" + " → ".join(INTERVIEW_SECTIONS) + " -->")
    return "\n".join(lines)
