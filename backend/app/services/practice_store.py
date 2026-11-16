"""错题本 / 排行榜（文档 §5.5.2 错题本 / V1.5 排行榜）。

- `save_wrong_questions`：报告生成后自动收集单题 <6 分的题目（按 session_id + question 去重）。
- `list_wrong_questions`：错题列表（按分类聚合 + 按创建时间倒序）。
- `build_leaderboard`：岗位榜 / 校园榜 / 进步榜，必须基于真实数据；不足 10 条时补充标注为演示的匿名条目。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import DIMENSIONS, WRONG_CATEGORIES, WRONG_CATEGORY_NAMES
from ..deps import load_json, local_date_str, local_iso, new_id, round1
from ..models import InterviewSession, User, WrongQuestion, now_utc

# ============================================================
# 演示数据（真实数据不足 10 条时补齐；note 里明确说明）
# ============================================================

_DEMO_ENTRIES: list[tuple[str, float, str, str]] = [
    ("陈同学", 8.8, "上海交通大学", "产品经理"),
    ("林同学", 8.6, "浙江大学", "产品经理"),
    ("王同学", 8.4, "武汉大学", "数据分析师"),
    ("李同学", 8.2, "中山大学", "产品经理"),
    ("赵同学", 8.0, "南京大学", "数据分析师"),
    ("周同学", 7.8, "华中科技大学", "后端开发工程师"),
    ("吴同学", 7.7, "同济大学", "产品经理"),
    ("郑同学", 7.5, "北京邮电大学", "前端开发工程师"),
    ("孙同学", 7.4, "西安交通大学", "数据分析师"),
    ("黄同学", 7.2, "四川大学", "产品经理"),
    ("徐同学", 7.0, "东南大学", "后端开发工程师"),
    ("马同学", 6.9, "华南理工大学", "产品经理"),
]

_DEMO_NOTE = "部分为演示数据，接入真实用户后自动替换"

_BADGES = {1: "🥇本周第一", 2: "🥈本周第二", 3: "🥉本周第三"}


# ============================================================
# 错题本
# ============================================================


def _infer_category(q: dict, report: dict) -> str:
    """由该题主要失分维度映射到 `constants.WRONG_CATEGORIES` 的中文分类。"""
    text = f"{q.get('question', '')} {q.get('focus', '')} {' '.join(str(x) for x in (q.get('improve') or []))}"
    best_key, best_score = "", -1
    for d in DIMENSIONS:
        hits = sum(text.count(w) for w in d["keywords"]) + (2 if d["name"] in text else 0)
        if hits > best_score:
            best_score, best_key = hits, d["key"]
    if best_score <= 0:
        # 回退到报告里分数最低的维度
        dims = [d for d in (report.get("dimensions") or []) if isinstance(d, dict)]
        if dims:
            try:
                best_key = str(min(dims, key=lambda x: float(x.get("score") or 0)).get("key") or "")
            except (TypeError, ValueError):
                best_key = ""
        if not best_key:
            best_key = "professional"
    return WRONG_CATEGORIES.get(best_key, "专业类")


def _to_item(row: WrongQuestion, trend_map: dict[str, list[dict]] | None = None) -> dict:
    # created_at 在库里是 naive UTC，前端直接 slice(0,10) 展示「收录于」，
    # 会给 UTC+8 用户显示成前一天，因此这里统一转成本地时区再下发。
    created = local_iso(row.created_at) if row.created_at else ""
    dim_key = dimension_key_of(row)
    trend = list((trend_map or {}).get(dim_key) or [])
    return {
        "id": row.id,
        "session_id": row.session_id,
        "job_title": row.job_title or "",
        "category": row.category or "",
        "section": getattr(row, "section", "") or "",
        "dimension_key": dim_key,
        "focus": row.focus or "",
        "question": row.question or "",
        "answer": row.answer or "",
        "score": round1(row.score or 0.0),
        "issue": row.issue or "",
        "correct_thinking": row.correct_thinking or "",
        "reference": row.reference or "",
        "retry_count": int(row.retry_count or 0),
        "created_at": created,
        # 体验增强 §9：同维度历次得分（时间升序），用于练完后的前后对比
        "trend": trend,
        "before_score": trend[-1]["score"] if trend else 0.0,
    }


def save_wrong_questions(db: Session, session: InterviewSession, report: dict) -> int:
    """把 `report["questions"]` 里 score<6 的题写入错题本（去重：同 session_id + question 跳过）。返回新增条数。"""
    if session is None or not isinstance(report, dict):
        return 0
    added = 0
    try:
        for q in report.get("questions") or []:
            if not isinstance(q, dict):
                continue
            try:
                score = float(q.get("score") or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            if score >= 6.0:
                continue
            question = str(q.get("question") or "").strip()
            if not question:
                continue
            exists = (
                db.execute(
                    select(WrongQuestion).where(
                        WrongQuestion.session_id == session.session_id,
                        WrongQuestion.question == question,
                    )
                )
                .scalars()
                .first()
            )
            if exists:
                continue
            improves = [str(x) for x in (q.get("improve") or []) if str(x).strip()]
            reference = str(q.get("reference") or "")
            row = WrongQuestion(
                id=new_id(),
                user_id=session.user_id or "",
                session_id=session.session_id,
                job_title=session.job_title or "",
                category=_infer_category(q, report),
                section=str(q.get("section") or ""),
                focus=str(q.get("focus") or ""),
                question=question,
                answer=str(q.get("answer") or ""),
                score=round1(score),
                issue=improves[0] if improves else "",
                correct_thinking=reference,
                reference=reference,
                retry_count=0,
                created_at=now_utc(),
            )
            db.add(row)
            added += 1
        if added:
            db.commit()
    except Exception as exc:  # noqa: BLE001 错题本失败不影响报告主流程
        print(f"[practice] 错题本写入失败（已忽略）: {exc}")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return 0
    return added


def list_wrong_questions(db: Session, user_id: str, category: str = "") -> dict:
    """返回 `WrongQuestionListResponse` 的 dict 形态；user_id 为空时返回空列表。

    注意 `total` 是**该用户的错题总数（不随 category 变化）**，
    `items` 才是按 category 过滤后的结果，`by_category` 同样是全量统计。
    前端「全部」标签读 `total`、分类标签读 `by_category[cat]`，
    如果 total 取过滤后的条数，切到某个分类后「全部」的数字会跟着变小。
    """
    empty = {"total": 0, "by_category": {name: 0 for name in WRONG_CATEGORY_NAMES}, "items": []}
    if not user_id:
        return empty
    try:
        rows = (
            db.execute(
                select(WrongQuestion)
                .where(WrongQuestion.user_id == user_id)
                .order_by(WrongQuestion.created_at.desc())
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[practice] 查询错题失败: {exc}")
        return empty

    by_category = {name: 0 for name in WRONG_CATEGORY_NAMES}
    for r in rows:
        cat = r.category or ""
        if cat in by_category:
            by_category[cat] += 1
        elif cat:
            by_category[cat] = by_category.get(cat, 0) + 1

    filtered = [r for r in rows if not category or (r.category or "") == category]
    # 体验增强 §9：每道错题带上「同维度历次得分」，用于练完后的前后对比。
    # 一次扫描建好 {维度key: [(日期, 分数)]}，避免逐题查库。
    trend_map = _dimension_trend(db, user_id)
    items = [_to_item(r, trend_map) for r in filtered]
    return {"total": len(rows), "by_category": by_category, "items": items}


# 错题分类（中文）↔ 六维 key 的双向映射：分类来自 WRONG_CATEGORIES，反向查即可
_CATEGORY_TO_DIM: dict[str, str] = {v: k for k, v in WRONG_CATEGORIES.items()}

# 老数据没有 section 时的兜底：按分类挑一个最相关环节出微面试题
_CATEGORY_FALLBACK_SECTION: dict[str, str] = {
    "专业类": "专业技能",
    "逻辑类": "场景题",
    "表达类": "行为面试",
    "匹配类": "项目深挖",
    "应变类": "场景题",
    "文化类": "行为面试",
}

# 趋势扫描的会话上限：错题本不是高频页面，但也不该让历史越多越慢
_TREND_SCAN_LIMIT = 20


def _dimension_trend(db: Session, user_id: str) -> dict[str, list[dict]]:
    """该用户最近若干场已结束面试里，各维度的得分序列（按时间升序）。

    用**维度分**而不是"错题分"：练完微面试后对比的正是同一维度的分数，
    拿单题分数去比会得出"上次 4.5 → 本次 7.0"这种口径不一致的结论。
    """
    out: dict[str, list[dict]] = {}
    if not user_id:
        return out
    try:
        rows = (
            db.execute(
                select(InterviewSession)
                .where(
                    InterviewSession.user_id == user_id,
                    InterviewSession.status == "finished",
                )
                .order_by(InterviewSession.created_at.desc())
                .limit(_TREND_SCAN_LIMIT)
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 趋势是增强信息，失败不影响错题本
        print(f"[practice] 读取维度趋势失败（已忽略）: {exc}")
        return out

    # 查询是倒序的，反转后即为时间升序
    for row in reversed(rows):
        report = load_json(row.report_json, {})
        if not isinstance(report, dict):
            continue
        date = local_date_str(row.finished_at or row.created_at)
        for d in report.get("dimensions") or []:
            if not isinstance(d, dict):
                continue
            key = str(d.get("key") or "")
            if not key:
                continue
            try:
                score = round1(float(d.get("score") or 0.0))
            except (TypeError, ValueError):
                continue
            out.setdefault(key, []).append({"date": date, "score": score})
    for key in out:
        out[key] = out[key][-_TREND_SCAN_LIMIT:]
    return out


def dimension_trend_for(db: Session, user_id: str, dim_key: str) -> list[dict]:
    """单个维度的历次得分（时间升序）；供单题特训的前后对比使用。"""
    if not user_id or not dim_key:
        return []
    return list(_dimension_trend(db, user_id).get(dim_key) or [])


def focus_section_of(row: WrongQuestion) -> str:
    """微面试该练哪个环节：优先用原题环节，老数据按分类兜底。"""
    section = str(getattr(row, "section", "") or "").strip()
    if section:
        return section
    return _CATEGORY_FALLBACK_SECTION.get(str(row.category or ""), "项目深挖")


def dimension_key_of(row: WrongQuestion) -> str:
    """错题分类对应的六维 key（用于同维度前后对比）。"""
    return _CATEGORY_TO_DIM.get(str(row.category or ""), "professional")


def build_retry_tips(row: WrongQuestion) -> list[str]:
    """3 条解题思路：聚焦考察点、结构化重答、复盘上次的失分点。"""
    focus = (row.focus or "").strip() or "该题考察点"
    ref = (row.reference or "").strip()
    issue = (row.issue or "").strip()
    tips = [
        f"先复述考察点：这题考的是「{focus}」，作答前用 10 秒列出一句话结论。",
        "用「结论先行 + 第一/第二 两条论据」组织一次 60-90 秒回答，并至少给出 1 个量化结果。",
    ]
    if issue:
        tips.append(f"针对上次失分点复盘：{issue}——重答时逐条对照，确保这次不再踩坑。")
    elif ref:
        tips.append(f"对照参考要点自检：{ref[:60]}，补齐自己漏掉的那一点。")
    else:
        tips.append("录音回听自己的回答，标出跑题和口头禅，重录一版更精简的答案。")
    return tips[:3]


# ============================================================
# 排行榜
# ============================================================


def _mask_name(name: str) -> str:
    """昵称脱敏：只留姓氏 + 「同学」，如「林宇航」→「林同学」。

    同时把「演示同学 / 测试同学 / 同学」这类无意义昵称统一显示为「匿名同学」，
    避免榜单上出现「演同学」「同同学」这种读起来别扭的名字。
    """
    n = (name or "").strip()
    for prefix in ("演示", "示例", "测试", "体验", "demo", "Demo", "DEMO"):
        if n.startswith(prefix):
            n = n[len(prefix) :]
            break
    n = n.replace("同学", "").replace("用户", "").strip()
    if not n:
        return "匿名同学"
    return n[0] + "同学"


def _score_of(s: InterviewSession) -> float:
    try:
        return float(s.score or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _real_user_rows(db: Session) -> tuple[list[dict], dict[str, str]]:
    """返回 (真实用户成绩行, user_id -> school)。每个用户取最高分那次会话。"""
    try:
        sessions = (
            db.execute(
                select(InterviewSession)
                .where(InterviewSession.status == "finished", InterviewSession.score > 0)
                .order_by(InterviewSession.created_at.asc())
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[practice] 查询排行榜会话失败: {exc}")
        return [], {}

    # user_id -> school / nickname
    school_map: dict[str, str] = {}
    name_map: dict[str, str] = {}
    try:
        users = db.execute(select(User)).scalars().all()
        for u in users:
            school_map[u.user_id] = u.school or ""
            name_map[u.user_id] = u.nickname or ""
    except Exception as exc:  # noqa: BLE001
        print(f"[practice] 查询用户失败: {exc}")

    best: dict[str, InterviewSession] = {}
    per_user: dict[str, list[InterviewSession]] = {}
    for s in sessions:
        uid = s.user_id or ""
        if not uid:
            continue
        per_user.setdefault(uid, []).append(s)
        if uid not in best or _score_of(s) >= _score_of(best[uid]):
            best[uid] = s

    rows: list[dict] = []
    for uid, s in best.items():
        rows.append(
            {
                "user_id": uid,
                "nickname": _mask_name(name_map.get(uid, "")),
                "score": round1(_score_of(s)),
                "school": school_map.get(uid, ""),
                "job_title": s.job_title or "",
                "_sessions": per_user.get(uid, []),
            }
        )
    return rows, school_map


def _most_common(values: list[str]) -> str:
    counter: dict[str, int] = {}
    for v in values:
        if v:
            counter[v] = counter.get(v, 0) + 1
    if not counter:
        return ""
    return max(counter.items(), key=lambda kv: kv[1])[0]


def _target_job(rows: list[dict], user_id: str) -> str:
    if user_id:
        for r in rows:
            if r["user_id"] == user_id and r["job_title"]:
                return r["job_title"]
    return _most_common([r["job_title"] for r in rows])


def _target_school(rows: list[dict], school_map: dict[str, str], user_id: str) -> str:
    if user_id and school_map.get(user_id):
        return school_map[user_id]
    return _most_common([r["school"] for r in rows])


def _dedupe_rows(rows: list[dict], user_id: str = "") -> list[dict]:
    """按「脱敏昵称 + 学校」去重，保留分数最高的一条。

    反复点击「一键体验示例简历」会建出多个同名演示用户，不去重会在榜单上重复霸榜。
    当前用户所在分组始终保留本人，保证「我的排名」能正确命中。
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        groups.setdefault((r["nickname"], r["school"]), []).append(r)

    out: list[dict] = []
    for group in groups.values():
        pick = max(group, key=lambda r: r["score"])
        if user_id:
            for r in group:
                if r["user_id"] == user_id:
                    pick = r
                    break
        out.append(pick)
    return out


def build_leaderboard(db: Session, board_type: str, user_id: str = "") -> dict:
    """返回 `LeaderboardResponse` 的 dict 形态。board_type ∈ job | school | progress。"""
    btype = board_type if board_type in ("job", "school", "progress") else "job"

    rows, school_map = _real_user_rows(db)
    rows = _dedupe_rows(rows, user_id)
    target_job = _target_job(rows, user_id)
    target_school = _target_school(rows, school_map, user_id)

    # 真实条目（item, user_id）
    real_items: list[tuple[dict, str]] = []
    if btype == "job":
        title = f"{target_job} 岗位排行榜" if target_job else "综合排行榜"
        for r in rows:
            if target_job and r["job_title"] and r["job_title"] != target_job:
                continue
            real_items.append(
                (
                    {
                        "rank": 0,
                        "nickname": r["nickname"],
                        "score": r["score"],
                        "school": r["school"],
                        "job_title": r["job_title"],
                        "badge": "",
                    },
                    r["user_id"],
                )
            )
    elif btype == "school":
        title = f"{target_school} 校园排行榜" if target_school else "校园排行榜"
        for r in rows:
            if target_school and r["school"] != target_school:
                continue
            real_items.append(
                (
                    {
                        "rank": 0,
                        "nickname": r["nickname"],
                        "score": r["score"],
                        "school": r["school"],
                        "job_title": r["job_title"],
                        "badge": "",
                    },
                    r["user_id"],
                )
            )
    else:  # progress：按最近两次分数差排序
        title = "进步榜 TOP10"
        for r in rows:
            sessions = r.get("_sessions") or []
            if len(sessions) < 2:
                continue
            latest = _score_of(sessions[-1])
            prev = _score_of(sessions[-2])
            delta = round1(latest - prev)
            # 进步榜只收「确实进步」的人：delta<=0 会显示成「-0.1 分」，
            # 放在进步榜里语义自相矛盾，也会让榜首看起来像在退步。
            if delta <= 0:
                continue
            real_items.append(
                (
                    {
                        "rank": 0,
                        "nickname": r["nickname"],
                        "score": delta,
                        "school": r["school"],
                        "job_title": r["job_title"],
                        "badge": "",
                    },
                    r["user_id"],
                )
            )

    # 演示条目补齐
    demo_items: list[tuple[dict, str]] = []
    if len(real_items) < 10:
        need = 10 - len(real_items)
        for i, (name, score, school, job) in enumerate(_DEMO_ENTRIES[: max(need, 0)]):
            if btype == "job":
                row_job = target_job or job
                row_school = school
                row_score = score
            elif btype == "school":
                row_job = job
                row_school = target_school or school
                row_score = score
            else:
                row_job = job
                row_school = school
                row_score = round1(1.8 - i * 0.12)  # 进步榜演示：正提升值
            demo_items.append(
                (
                    {
                        "rank": 0,
                        "nickname": name,
                        "score": row_score,
                        "school": row_school,
                        "job_title": row_job,
                        "badge": "",
                    },
                    "",
                )
            )

    merged = real_items + demo_items
    merged.sort(key=lambda t: t[0]["score"], reverse=True)

    items: list[dict] = []
    my_rank = 0
    for idx, (item, uid) in enumerate(merged, start=1):
        item["rank"] = idx
        item["badge"] = _BADGES.get(idx, "")
        items.append(item)
        if user_id and uid == user_id and my_rank == 0:
            my_rank = idx

    note = _DEMO_NOTE if demo_items else "榜单基于已完成面试的真实数据统计"
    if btype == "progress":
        note = "进步榜按最近两次综合分差值排序（只统计分数有提升的同学）；" + note

    return {
        "type": btype,
        "title": title,
        "items": items,
        "my_rank": my_rank,
        "note": note,
    }
