"""面试路由（API_CONTRACT §2.3）：开始 / 作答 / 跳过 / 结束 / 状态 / 逐字稿。

设计要点
--------
- 引擎**无状态**：每次请求都用 `plan_json` + 三份档案重新构造 `InterviewEngine`，
  `total_questions` 取自 `plan_json["total"]`，进度完全由 `dialogue_json` 推导。
- 永不 500：`compute_match` / LLM / 结束语 等外部依赖全部 try/except 降级。
- 所有会话级写操作都会 `db.commit()` 持久化 `dialogue_json`。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import deps
from ..config import settings
from ..constants import SPEECH_EST_SECONDS_PER_QUESTION
from ..db import get_db
from ..deps import assert_owner, local_iso, new_id, require_login
from ..models import (
    Company,
    InterviewSession,
    JobDescription,
    Resume,
    SpeechMetric,
    User,
    now_utc,
)
from ..schemas import (
    InterviewAnswerRequest,
    InterviewAnswerResponse,
    InterviewSessionState,
    InterviewSkipRequest,
    InterviewStartRequest,
    InterviewStartResponse,
    MatchResponse,
    ProgressInfo,
    SpeechMetricsRequest,
    SpeechMetricsResponse,
    TranscriptItem,
    Turn,
)
from ..services.interview_engine import InterviewEngine, build_profile
from ..services import session_housekeeping, speech_metrics

# Agent A 的 matching 模块可能尚未就绪：导入失败时降级为最小匹配结果
try:
    from ..services.matching import compute_match  # type: ignore
except Exception:  # noqa: BLE001
    compute_match = None  # type: ignore

router = APIRouter()


# ============================================================
# 内部工具
# ============================================================


def _now_iso() -> str:
    """Turn 的时间戳（展示用）。用本地时区，避免前端显示比实际早 8 小时。"""
    return local_iso()


def _to_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _default_total() -> int:
    return _to_int(getattr(settings, "default_question_count", 9), 9) or 9


# 体验增强 §9：单题特训的题量（一个环节练 3 题，约 4 分钟）
_RETRY_QUESTION_COUNT = 3


def _get_session(db: Session, session_id: str, user: User) -> InterviewSession:
    """取会话并**校验归属**：别人的会话一律 404（连同其对话与报告一起不可见）。"""
    return assert_owner(db.get(InterviewSession, session_id), user, "面试会话")


def _converge_stale(db: Session, session: InterviewSession) -> None:
    """空闲超时的会话先收敛为 aborted，再按「已结束」语义返回 409。

    等价于「一场面试长时间没人管就等于结束了」：这样旧链接不会既答不完、
    又能被继续追加对话（写坏数据）。未超时不产生任何写操作，直接返回 None。
    """
    if session_housekeeping.close_session(db, session):
        raise HTTPException(
            status_code=409,
            detail=f"面试已因超过 {session_housekeeping.idle_timeout_hours():g} 小时无操作自动结束，请重新开始一场面试",
        )


def _dialogue(session: InterviewSession) -> list[dict]:
    data = deps.load_json(session.dialogue_json, [])
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def _plan(session: InterviewSession) -> dict:
    data = deps.load_json(session.plan_json, {})
    return data if isinstance(data, dict) else {}


def _total_of(session: InterviewSession) -> int:
    return _to_int(_plan(session).get("total"), 0) or _default_total()


def _has_report(session: InterviewSession) -> bool:
    """是否已生成真实报告。

    只判 `report_json` 非空是不够的：`POST /report/{id}/feedback` 在报告还没生成时
    会写入 `{"session_id":..., "meta":{...}}` 这样的占位内容，
    只判非空会让前端误以为报告已就绪。
    """
    raw = deps.load_json(session.report_json, {})
    return isinstance(raw, dict) and bool(raw.get("overview"))


def _has_closing(dialogue: list[dict]) -> bool:
    return any(str(d.get("section") or "") == "结束语" for d in dialogue)


def _current_index(dialogue: list[dict]) -> int:
    """dialogue 中最大的面试官题号（0 表示还没有题目）。"""
    best = 0
    for d in dialogue:
        if d.get("role") != "interviewer":
            continue
        idx = _to_int(d.get("index"), 0)
        if idx > best:
            best = idx
    return best


def _current_question(dialogue: list[dict]) -> tuple[int, str, str]:
    """返回当前题目的 (index, section, focus)。"""
    best_idx, best_item = 0, None
    for d in dialogue:
        if d.get("role") != "interviewer":
            continue
        idx = _to_int(d.get("index"), 0)
        if idx > 0 and idx >= best_idx:
            best_idx, best_item = idx, d
    if best_item is None:
        return 0, "", ""
    return best_idx, str(best_item.get("section") or ""), str(best_item.get("focus") or "")


def _answered_questions(dialogue: list[dict]) -> int:
    """已答题目数 = 面试官题目 index 去重后的个数（追问复用同 index，不重复计数）。"""
    indices = set()
    for d in dialogue:
        if d.get("role") != "interviewer":
            continue
        idx = _to_int(d.get("index"), 0)
        if idx > 0:
            indices.add(idx)
    return len(indices)


def _answered_count(dialogue: list[dict]) -> int:
    """已**处理**的题目数：有候选人回答或跳过记录的题号去重计数。

    与 `_answered_questions`（问过几题）区分开：
    进度条的「已答/共 N 题」用的是这个，跳过也算「已处理」（另由 skipped 标记区分）。
    """
    done = set()
    for d in dialogue:
        idx = _to_int(d.get("index"), 0)
        if idx > 0 and d.get("role") in ("candidate", "system"):
            done.add(idx)
    return len(done)


def _median(values: list[int]) -> int:
    data = sorted(v for v in values if v > 0)
    if not data:
        return 0
    mid = len(data) // 2
    if len(data) % 2:
        return data[mid]
    return int(round((data[mid - 1] + data[mid]) / 2))


def _per_question_seconds(db: Session, session: InterviewSession, dialogue: list[dict]) -> tuple[int, str]:
    """每题耗时基准与来源：本场中位 → 该用户口播历史中位 → 45 秒估算。

    只查 `speech_metric`（带 user_id 索引的单表查询）而不去解析历史会话的
    dialogue_json：后者每次答题都要 JSON 反序列化多份大字段，得不偿失。
    """
    this_session = [
        _to_int(d.get("answer_seconds"), 0)
        for d in dialogue
        if d.get("role") == "candidate" and _to_int(d.get("answer_seconds"), 0) > 0
    ]
    if len(this_session) >= 2:
        return _median(this_session), "session"

    try:
        rows = (
            db.query(SpeechMetric.duration_seconds)
            .filter(SpeechMetric.user_id == (session.user_id or ""))
            .order_by(SpeechMetric.created_at.desc())
            .limit(20)
            .all()
        )
        history = [_to_int(r[0], 0) for r in rows]
    except Exception as exc:  # noqa: BLE001 历史读取失败不影响进度展示
        print(f"[interview] 读取口播历史失败，改用估算: {exc}")
        history = []
    med = _median(history)
    if med:
        return med, "history"

    return SPEECH_EST_SECONDS_PER_QUESTION, "estimate"


def _progress_info(db: Session, session: InterviewSession, dialogue: list[dict]) -> ProgressInfo:
    """组装进度条渲染所需的全部信息（体验增强 §2）。"""
    total = _total_of(session)
    index, stage, _focus = _current_question(dialogue)
    answered = _answered_count(dialogue)
    remaining = max(0, total - answered)

    per_q, source = _per_question_seconds(db, session, dialogue)
    # 给区间而不是精确值：下限按较快节奏，上限留足超时与追问的余量
    low = int(round(remaining * per_q * 0.75 / 10.0)) * 10
    high = int(round(remaining * per_q * 1.4 / 10.0)) * 10
    if remaining == 0:
        low = high = 0

    return ProgressInfo(
        index=index,
        total=total,
        answered=answered,
        stage=stage,
        elapsed_seconds=_to_int(session.duration_seconds, 0),
        estimated_remaining_seconds=[low, high],
        source=source,  # type: ignore[arg-type]
        idle_timeout_hours=session_housekeeping.idle_timeout_hours(),
        can_resume=(session.status or "ongoing") == "ongoing",
    )


def _closing_turn(text: str, total: int) -> dict:
    """结束语 Turn（role=interviewer, index=0, section=结束语）。"""
    content = (text or "").strip() or "今天的面试就到这里，感谢你的参与，系统稍后会为你生成六维评分报告。"
    from ..services import media

    return {
        "turn_id": new_id(),
        "role": "interviewer",
        "content": content,
        "focus": "",
        "section": "结束语",
        "index": 0,
        "total": int(total or 0),
        "is_followup": False,
        "reference_points": [],
        "speak_text": media.strip_annotation(content) or content,
        "created_at": _now_iso(),
    }


def _minimal_match() -> dict:
    """compute_match 不可用时的最小可用匹配结果（API_CONTRACT §3 降级）。

    `level` 与正常路径 `matching._level_text()` 保持同一语义（整句文案），
    否则前端同一个字段要兼容「C级」和整句话两种渲染。
    """
    return {
        "overall": 60,
        "level": "关键信息不足，匹配度仅供参考（简历或岗位信息缺失）",
        "matched": [],
        "weak": [],
        "strategy": {"focus": [], "deep_dive": [], "pressure_test": []},
        "breakdown": [],
        "radar_target": {
            "专业能力": 7.0,
            "逻辑思维": 6.8,
            "表达沟通": 7.2,
            "岗位匹配度": 6.8,
            "应变能力": 6.5,
            "文化适配": 6.8,
        },
    }


# ============================================================
# 会话级并发保护
# ============================================================
# answer/skip 都是「读 dialogue → 追加 → 写回」的读改写序列，同一 session 并发提交会互相覆盖。
# SQLite 单进程下用会话级互斥锁串行化；配合同一请求的 question_id 校验，
# 重复提交/过期提交会被拒绝而不是把对话写乱。
_session_locks: dict[str, asyncio.Lock] = {}


def _lock_of(session_id: str) -> asyncio.Lock:
    lock = _session_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_locks[session_id] = lock
        # 会话锁只服务于进行中的面试：字典过大时清掉一批已释放的锁，避免长期运行内存增长
        if len(_session_locks) > 2048:
            for key in [k for k, v in _session_locks.items() if not v.locked() and k != session_id][:1024]:
                _session_locks.pop(key, None)
    return lock


def _max_answer_seconds() -> int:
    return _to_int(getattr(settings, "max_answer_seconds", 300), 300) or 300


def _clamp_seconds(value) -> int:
    """作答时长取值：负数归零，超过 max_answer_seconds 截断（防止客户端上报超大值污染报告时长）。"""
    secs = _to_int(value, 0)
    if secs < 0:
        return 0
    return min(secs, _max_answer_seconds())


def _current_question_id(dialogue: list[dict]) -> str:
    """当前待作答题目的 turn_id（dialogue 中最后一个 index>0 的面试官发言）。"""
    turn_id = ""
    for d in dialogue:
        if d.get("role") != "interviewer":
            continue
        if _to_int(d.get("index"), 0) > 0:
            turn_id = str(d.get("turn_id") or "")
    return turn_id


def _check_question_id(dialogue: list[dict], question_id: str) -> None:
    """客户端指定的 question_id 必须与当前题目一致。

    不传（老前端兼容）时放行；传了但不匹配说明是重复提交或过期提交，
    必须拒绝——否则会对已经推进的对话再追加一轮回答，把题目答错位。
    """
    if not question_id:
        return
    current = _current_question_id(dialogue)
    if current and question_id != current:
        raise HTTPException(status_code=409, detail="当前题目已更新，请刷新页面后重新作答")


def _build_profile_for_session(db: Session, session: InterviewSession) -> dict:
    """从 DB 重新读取三份档案 + 匹配结果，rebuild flat profile（引擎无状态的前提）。"""
    resume = db.get(Resume, session.resume_id) if session.resume_id else None
    jd = db.get(JobDescription, session.jd_id) if session.jd_id else None
    company = db.get(Company, session.company_id) if session.company_id else None

    resume_parsed = deps.load_json(resume.parsed_json, {}) if resume else {}
    jd_parsed = deps.load_json(jd.parsed_json, {}) if jd else {}
    company_profile = deps.load_json(company.profile_json, {}) if company else {}
    if not isinstance(company_profile, dict) or not company_profile:
        company_profile = None

    match = deps.load_json(session.match_json, {})
    if not isinstance(match, dict):
        match = {}

    profile = build_profile(resume_parsed, jd_parsed, company_profile, match)
    # 档案可能已被删除：用会话上冗余保存的岗位/公司名兜底，保证出题上下文不丢
    if session.job_title and profile.get("job_title") in ("", "目标岗位"):
        profile["job_title"] = session.job_title
    if session.company_name and profile.get("company_name") in ("", "目标公司"):
        profile["company_name"] = session.company_name

    # 体验增强 §14：压力面试模式必须从**会话计划**里恢复。
    # 引擎每次请求都是无状态重建的，如果只在 start 时改了内存里的 profile，
    # 那么除第 1 题之外的题目都会退回常规阈值——压力面会"开场像压力面、后面全不像"。
    plan = deps.load_json(session.plan_json, {})
    if isinstance(plan, dict) and plan.get("pressure_mode"):
        profile["pressure_mode"] = True
        profile["interview_style"] = "压力面"
    return profile


def _make_engine(db: Session, session: InterviewSession) -> tuple[InterviewEngine, dict]:
    plan = _plan(session)
    total = _to_int(plan.get("total"), 0) or _default_total()
    engine = InterviewEngine(_build_profile_for_session(db, session), total_questions=total)
    return engine, plan


def _state(session: InterviewSession, db: Session | None = None) -> InterviewSessionState:
    """组装 InterviewSessionState。"""
    dialogue = _dialogue(session)
    total = _total_of(session)
    items: list[TranscriptItem] = []
    for d in dialogue:
        items.append(
            TranscriptItem(
                role=str(d.get("role") or ""),
                content=str(d.get("content") or ""),
                focus=str(d.get("focus") or ""),
                section=str(d.get("section") or ""),
                index=_to_int(d.get("index"), 0),
                created_at=str(d.get("created_at") or ""),
                # 中断恢复：前端据此在刷新后仍能带上正确的 question_id
                turn_id=str(d.get("turn_id") or ""),
                answer_seconds=_to_int(d.get("answer_seconds"), 0),
                retake=bool(d.get("retake")),
                hint_used=bool(d.get("hint_used")),
            )
        )
    # 刷新/重进页面时恢复进度条：db 缺失（理论上的内部调用）时退化为不返回 progress
    progress = _progress_info(db, session, dialogue) if db is not None else None
    return InterviewSessionState(
        session_id=session.session_id,
        status=session.status or "ongoing",
        job_title=session.job_title or "",
        company_name=session.company_name or "",
        mode=session.mode or "voice",
        interviewer=session.interviewer or "female",
        question_index=_current_index(dialogue),
        total_questions=total,
        score=float(session.score or 0.0),
        duration_seconds=_to_int(session.duration_seconds, 0),
        dialogue=items,
        has_report=_has_report(session),
        progress=progress,
        retake_count=_to_int(getattr(session, "retake_count", 0), 0),
        hint_count=_to_int(getattr(session, "hint_count", 0), 0),
    )


async def _advance(
    db: Session,
    session: InterviewSession,
    engine: InterviewEngine,
    dialogue: list[dict],
    total: int,
    answer_text: str,
    answer_seconds: int,
    skipped: bool,
) -> InterviewAnswerResponse:
    """调用引擎出下一题，并按结果落库（answer / skip 共用）。"""
    answered = _answered_questions(dialogue)
    seconds = _clamp_seconds(answer_seconds)
    next_turn = await engine.respond(
        dialogue, answer_text, seconds, skipped=skipped
    )

    if seconds:
        session.duration_seconds = _to_int(session.duration_seconds, 0) + seconds

    if next_turn is None:
        # 面试结束：写状态 + 结束语（§2.3）
        if not _has_closing(dialogue):
            try:
                closing_text = await engine.closing()
            except Exception as exc:  # noqa: BLE001
                print(f"[interview] 结束语生成失败，使用兜底文案: {exc}")
                closing_text = ""
            dialogue.append(_closing_turn(closing_text, total))
        session.status = "finished"
        if session.finished_at is None:
            session.finished_at = now_utc()
        question_index = total
        is_finished = True
        progress = 1.0
    else:
        dialogue.append(next_turn)
        question_index = _to_int(next_turn.get("index"), 0)
        is_finished = False
        progress = min(1.0, (answered / total) if total else 0.0)

    session.dialogue_json = deps.dump_json(dialogue)
    db.commit()
    db.refresh(session)

    return InterviewAnswerResponse(
        session_id=session.session_id,
        status=session.status,
        next_turn=Turn(**next_turn) if next_turn else None,
        question_index=question_index,
        total_questions=total,
        is_finished=is_finished,
        progress=round(progress, 4),
        progress_info=_progress_info(db, session, dialogue),
    )


# ============================================================
# 接口实现（API_CONTRACT §2.3）
# ============================================================


@router.post("/interview/start", response_model=InterviewStartResponse, summary="开始一场模拟面试")
async def interview_start(
    req: InterviewStartRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> InterviewStartResponse:
    """读取简历/JD/公司 → 计算匹配度 → 建会话 → 返回开场白+第 1 题。

    简历/JD 必须是**当前用户自己的**（别人的 id 会被当作不存在），
    会话归属只由登录令牌决定，请求体里的 user_id 一律忽略。
    """
    resume = assert_owner(db.get(Resume, req.resume_id), user, "简历") if req.resume_id else None
    jd = assert_owner(db.get(JobDescription, req.jd_id), user, "JD") if req.jd_id else None
    if resume is None or jd is None:
        raise HTTPException(status_code=404, detail="简历或JD不存在，请先上传/粘贴")

    company = db.get(Company, req.company_id) if req.company_id else None
    resume_parsed = deps.load_json(resume.parsed_json, {})
    jd_parsed = deps.load_json(jd.parsed_json, {})
    company_profile = deps.load_json(company.profile_json, {}) if company else None
    if not isinstance(company_profile, dict) or not company_profile:
        company_profile = None

    # ---- 匹配度：优先 Agent A 的 compute_match，失败/缺失走最小兜底 ----
    match: dict
    if compute_match is not None:
        try:
            result = compute_match(resume_parsed, jd_parsed, company_profile)
            match = result if isinstance(result, dict) and result else _minimal_match()
        except Exception as exc:  # noqa: BLE001
            print(f"[interview] compute_match 失败，使用最小匹配结果: {exc}")
            match = _minimal_match()
    else:
        print("[interview] matching 模块不可用，使用最小匹配结果")
        match = _minimal_match()

    profile = build_profile(resume_parsed, jd_parsed, company_profile, match)

    # ---- 体验增强 §14：压力面试模式（练习模式，报告里会标注）----
    if req.pressure_mode:
        profile["pressure_mode"] = True
        # 面试官风格改成压力面：开场白与 LLM 出题语气都会跟着变
        profile["interview_style"] = "压力面"
        # 提问策略里提高压力测试的占比（复用匹配度给出的压力测试点，没有就用硬技能兜底）
        strategy = profile.get("match_strategy")
        if isinstance(strategy, dict):
            pressure = [str(x) for x in (strategy.get("pressure_test") or [])]
            focus = [str(x) for x in (strategy.get("focus") or [])]
            merged = pressure + [x for x in focus if x not in pressure]
            strategy["pressure_test"] = merged[:6] or pressure
            profile["match_strategy"] = strategy

    # ---- 体验增强 §9：单题特训（3 题微面试，全部集中在同一个薄弱环节）----
    retry_total = _RETRY_QUESTION_COUNT if req.retry_mode else 0
    focus_section = req.focus_section if req.retry_mode else ""
    if req.retry_mode and req.focus_dimension:
        # 把薄弱维度放到 weak_points 最前面：规则题库与 LLM 出题都会优先针对它
        weak = [str(x) for x in (profile.get("weak_points") or [])]
        if req.focus_dimension not in weak:
            weak.insert(0, req.focus_dimension)
        profile["weak_points"] = weak[:6]

    engine = InterviewEngine(
        profile,
        total_questions=retry_total or req.question_count or _default_total(),
        focus_section=focus_section,
    )
    plan = engine.plan()
    # 记录本场用到的行业与题库命中情况：报告页据此说明"题目为什么是这些"
    plan["industry"] = str(profile.get("industry") or "")
    plan["industry_bank"] = bool(engine.industry_bank())
    if req.retry_mode:
        # 把特训目标记进计划：报告页据此展示「单题特训」的前后对比横幅
        plan["focus_dimension"] = req.focus_dimension or ""
    if req.pressure_mode:
        # 记进计划：报告页据此标注「本场为压力面试模式」，并提示不要与其他场次直接比分数
        plan["pressure_mode"] = True
    total = _to_int(plan.get("total"), 9)
    opening_turn = await engine.opening()

    jd_title = (
        str(getattr(jd, "job_title", "") or "").strip()
        or str((jd_parsed.get("basic") or {}).get("title") or "").strip()
        or str(profile.get("job_title") or "")
    )
    company_name = str(getattr(company, "name", "") or "").strip() or str(profile.get("company_name") or "")

    session = InterviewSession(
        session_id=new_id(),
        user_id=user.user_id,
        resume_id=req.resume_id,
        jd_id=req.jd_id,
        company_id=req.company_id or "",
        job_title=jd_title,
        company_name=company_name,
        mode=req.mode,
        interviewer=req.interviewer,
        match_json=deps.dump_json(match),
        plan_json=deps.dump_json(plan),
        dialogue_json=deps.dump_json([opening_turn]),
        report_json="{}",
        status="ongoing",
        score=0.0,
        duration_seconds=0,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    match_model: MatchResponse | None = None
    try:
        match_model = MatchResponse(**match)
    except Exception:  # noqa: BLE001 匹配结果结构异常时不下发，不影响面试
        match_model = None

    return InterviewStartResponse(
        session_id=session.session_id,
        status=session.status,
        mode=session.mode,
        interviewer=session.interviewer,
        job_title=session.job_title,
        company_name=session.company_name,
        total_questions=total,
        opening=Turn(**opening_turn),
        plan=plan,
        match=match_model,
    )


@router.post(
    "/interview/{session_id}/answer",
    response_model=InterviewAnswerResponse,
    summary="提交一道回答并获取下一题",
)
async def interview_answer(
    session_id: str,
    req: InterviewAnswerRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> InterviewAnswerResponse:
    session = _get_session(db, session_id, user)
    _converge_stale(db, session)
    if (session.status or "ongoing") != "ongoing":
        raise HTTPException(status_code=409, detail="面试已结束，无法继续作答")

    async with _lock_of(session_id):
        # 锁内重新读取：并发请求可能在等待期间已经推进了对话
        db.refresh(session)
        # 等锁期间可能已经越过空闲阈值：同样先收敛，再走统一的「已结束」分支
        session_housekeeping.close_session(db, session)
        if (session.status or "ongoing") != "ongoing":
            raise HTTPException(status_code=409, detail="面试已结束，无法继续作答")

        dialogue = _dialogue(session)
        _check_question_id(dialogue, req.question_id)

        engine, plan = _make_engine(db, session)
        total = _to_int(plan.get("total"), 0) or engine.total
        cur_index, cur_section, cur_focus = _current_question(dialogue)

        # 候选人回答落库（focus/section 从当前题继承）
        candidate_turn = Turn(
            turn_id=new_id(),
            role="candidate",
            content=req.answer_text or "",
            focus=cur_focus,
            section=cur_section,
            index=cur_index,
            total=total,
            created_at=_now_iso(),
        )
        dialogue.append(candidate_turn.model_dump())
        # 额外记录作答时长：评分引擎用它判断「回答过短/准备不足」（文档 §5.1 表达沟通、§5.4 防作弊）
        dialogue[-1]["answer_seconds"] = _clamp_seconds(req.answer_seconds)
        # 体验增强 §4：重录与思路提示只做**诚实披露**，不参与评分算法。
        # 标记写在候选人回合上（dialogue 是 JSON 列，不需要迁移），
        # 报告据此把「重录 N 次」「依赖提示」归到**具体某一题**，而不是只给一个总数。
        if req.retake:
            dialogue[-1]["retake"] = True
            session.retake_count = _to_int(getattr(session, "retake_count", 0), 0) + 1
        if req.hint_used:
            dialogue[-1]["hint_used"] = True
            session.hint_count = _to_int(getattr(session, "hint_count", 0), 0) + 1

        return await _advance(
            db, session, engine, dialogue, total, req.answer_text or "", req.answer_seconds, False
        )


@router.post(
    "/interview/{session_id}/skip",
    response_model=InterviewAnswerResponse,
    summary="跳过当前题目，直接进入下一题（不追问）",
)
async def interview_skip(
    session_id: str,
    req: InterviewSkipRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> InterviewAnswerResponse:
    session = _get_session(db, session_id, user)
    _converge_stale(db, session)
    if (session.status or "ongoing") != "ongoing":
        raise HTTPException(status_code=409, detail="面试已结束，无法继续操作")

    async with _lock_of(session_id):
        db.refresh(session)
        session_housekeeping.close_session(db, session)
        if (session.status or "ongoing") != "ongoing":
            raise HTTPException(status_code=409, detail="面试已结束，无法继续操作")

        dialogue = _dialogue(session)
        _check_question_id(dialogue, req.question_id)

        engine, plan = _make_engine(db, session)
        total = _to_int(plan.get("total"), 0) or engine.total
        cur_index, cur_section, cur_focus = _current_question(dialogue)

        skip_turn = Turn(
            turn_id=new_id(),
            role="system",
            content="（候选人选择跳过本题）",
            focus=cur_focus,
            section=cur_section,
            index=cur_index,
            total=total,
            created_at=_now_iso(),
        )
        dialogue.append(skip_turn.model_dump())

        # skipped=True：跳过不触发追问
        return await _advance(db, session, engine, dialogue, total, "", 0, True)


@router.post(
    "/interview/{session_id}/finish",
    response_model=InterviewSessionState,
    summary="提前结束面试（幂等）",
)
async def interview_finish(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> InterviewSessionState:
    session = _get_session(db, session_id, user)
    dialogue = _dialogue(session)

    if not _has_closing(dialogue):
        engine, plan = _make_engine(db, session)
        total = _to_int(plan.get("total"), 0) or engine.total
        try:
            closing_text = await engine.closing()
        except Exception as exc:  # noqa: BLE001
            print(f"[interview] 结束语生成失败，使用兜底文案: {exc}")
            closing_text = ""
        dialogue.append(_closing_turn(closing_text, total))
        session.dialogue_json = deps.dump_json(dialogue)

    # aborted（超时自动收敛）也允许用户主动「结束」并拿报告：显式操作优先于超时推断
    if (session.status or "ongoing") != "finished":
        session.status = "finished"
    if session.finished_at is None:
        session.finished_at = now_utc()
    db.commit()
    db.refresh(session)
    return _state(session, db)


@router.post(
    "/interview/{session_id}/speech-metrics",
    response_model=SpeechMetricsResponse,
    summary="上报单题口播指标（语速 / 填充词 / 时长，只落库不评分）",
)
async def interview_speech_metrics(
    session_id: str,
    req: SpeechMetricsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> SpeechMetricsResponse:
    """体验增强 §5：实时反馈的**事实**入库，供报告并排展示与后续阈值调优。

    刻意不调用评分引擎：实时只给事实，评分仍走六维模型，避免「边答边掉分」的焦虑。
    落库失败不影响答题主流程（返回 saved=false），因为这里的数据是观测性的。
    """
    session = _get_session(db, session_id, user)

    # 服务端用同一套阈值口径重算一遍，避免前端算法漂移导致两边结论不一致
    metrics = speech_metrics.evaluate(req.answer_text or "", req.duration_seconds)
    if req.question_index:
        metrics["question_index"] = int(req.question_index)
    else:
        metrics["question_index"] = _current_index(_dialogue(session))

    saved = True
    try:
        db.add(
            SpeechMetric(
                user_id=user.user_id or "",
                session_id=session.session_id,
                question_index=int(metrics["question_index"]),
                chars=int(metrics["chars"]),
                duration_seconds=int(metrics["duration_seconds"]),
                chars_per_minute=float(metrics["chars_per_minute"]),
                filler_count=int(metrics["filler_count"]),
                filler_words_json=deps.dump_json(metrics["filler_words"]),
                level=str(metrics["level"]),
            )
        )
        db.commit()
    except Exception as exc:  # noqa: BLE001 口播指标是观测数据，写失败不能影响答题
        saved = False
        print(f"[interview] 口播指标落库失败（已忽略）: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass

    return SpeechMetricsResponse(
        ok=True,
        saved=saved,
        chars_per_minute=float(metrics["chars_per_minute"]),
        filler_count=int(metrics["filler_count"]),
        filler_words=list(metrics["filler_words"]),
        level=str(metrics["level"]),  # type: ignore[arg-type]
        note=str(metrics["note"]),
    )


@router.get("/interview/{session_id}", response_model=InterviewSessionState, summary="获取面试会话状态")
async def interview_state(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> InterviewSessionState:
    session = _get_session(db, session_id, user)
    # 请求内收敛：用户重新打开一场早就放弃的面试时，前端拿到 aborted 就能把 TA 引导到报告页，
    # 而不是让 TA 对着一道永远答不完的题（同时 answer/skip 也不会再接受写入）。
    session_housekeeping.close_session(db, session)
    return _state(session, db)


@router.get("/interview/{session_id}/transcript", summary="获取面试逐字稿")
async def interview_transcript(
    session_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> dict:
    session = _get_session(db, session_id, user)
    state = _state(session)
    return {"session_id": session.session_id, "items": [item.model_dump() for item in state.dialogue]}
