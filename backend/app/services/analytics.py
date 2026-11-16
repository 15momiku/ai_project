"""历史进步追踪（文档 §5.2.7）。

从该用户最近 10 次已完成会话的 `report_json` 里读六维分数，生成：
- 历史进步曲线 `ProgressPoint` 列表；
- 提升最快的维度 / 仍薄弱的维度 / 一句中文总结。
"""

from __future__ import annotations

from datetime import date as _date
from datetime import timedelta as _timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import DIMENSION_NAME
from ..deps import load_json, local_date_str, local_iso, round1
from ..models import InterviewSession

_MAX_POINTS = 10
# 连续打卡的天级步长（放模块顶部，供 build_growth 与其子逻辑共用）
_ONE_DAY = _timedelta(days=1)

# 「没有真实报告」的统一判定：与 `routers/report.py::_has_report` 保持一致，
# 避免同一份数据在不同模块得出相反结论（例如只写入了反馈的空壳报告）。
_EMPTY_REPORTS = ("", "{}", "null", "[]", "None")


def _date_of(session: InterviewSession) -> str:
    # 用本地时区格式化：库里是 naive UTC，直接 strftime 会让 UTC+8 用户在凌晨
    # 完成的练习落到「前一天」，历史曲线的日期与实际不符。
    return local_date_str(session.finished_at or session.created_at)


def _dims_map(report: dict) -> dict[str, float]:
    # 历史脏数据里 report_json 可能是 `[]` / `null`，非 dict 时直接返回空，
    # 否则 `.get` 会抛 AttributeError 把整份报告打回兜底版本。
    if not isinstance(report, dict):
        return {}
    out: dict[str, float] = {}
    for d in report.get("dimensions") or []:
        if not isinstance(d, dict):
            continue
        key = str(d.get("key") or "")
        try:
            out[key] = float(d.get("score") or 0.0)
        except (TypeError, ValueError):
            continue
    return out


def _recent_finished(db: Session, user_id: str, limit: int = _MAX_POINTS) -> list[InterviewSession]:
    """该用户最近 limit 次 status=finished 且 report_json 非空的会话（时间升序）。"""
    if not user_id:
        return []
    try:
        rows = (
            db.execute(
                select(InterviewSession)
                .where(
                    InterviewSession.user_id == user_id,
                    InterviewSession.status == "finished",
                )
                .order_by(InterviewSession.created_at.asc())
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 查询失败 → 空历史，不抛异常
        print(f"[analytics] 查询历史会话失败: {exc}")
        return []

    valid = [r for r in rows if (r.report_json or "").strip() not in _EMPTY_REPORTS]
    return valid[-limit:]


def build_progress(
    db: Session,
    user_id: str,
    current_session_id: str,
    current_dims: list[dict],
) -> dict:
    """组装 `ProgressTrack` dict。

    - history：最近 10 次会话的 ProgressPoint（升序）。
    - fastest_dimension / fastest_delta：与「上一次会话」比提升最多的维度。
    - still_weak_dimension：当前分最低的维度。
    - summary：一句中文总结（只有 1 次记录时给鼓励文案）。
    """
    from ..constants import DIMENSION_NAME

    sessions = _recent_finished(db, user_id)

    history: list[dict] = []
    dim_history: list[dict[str, float]] = []
    for s in sessions:
        report = load_json(s.report_json, {})
        if not isinstance(report, dict):
            report = {}
        overview = report.get("overview")
        overview = overview if isinstance(overview, dict) else {}
        history.append(
            {
                "session_id": s.session_id,
                "date": _date_of(s),
                "score": round1(s.score or overview.get("score") or 0.0),
                "job_title": s.job_title or "",
            }
        )
        dim_history.append(_dims_map(report))

    cur_map: dict[str, float] = {}
    for d in current_dims or []:
        if not isinstance(d, dict):
            continue
        key = str(d.get("key") or "")
        try:
            cur_map[key] = float(d.get("score") or 0.0)
        except (TypeError, ValueError):
            continue

    # 当前会话若已在 history 中（例如重复生成），以传入的 current_dims 覆盖其维度分
    for i, s in enumerate(sessions):
        if s.session_id == current_session_id and cur_map:
            dim_history[i] = cur_map

    # 「上一次会话」= 排除当前会话后的最后一次
    prev_map: dict[str, float] = {}
    for i in range(len(sessions) - 1, -1, -1):
        if sessions[i].session_id == current_session_id:
            continue
        if dim_history[i]:
            prev_map = dim_history[i]
            break

    # 提升最快的维度
    fastest_key = ""
    fastest_delta = 0.0
    if cur_map and prev_map:
        for key, score in cur_map.items():
            if key not in prev_map:
                continue
            delta = round1(score - prev_map[key])
            if delta > fastest_delta:
                fastest_delta = delta
                fastest_key = key

    # 仍薄弱的维度 = 当前最低分
    weak_key = ""
    if cur_map:
        weak_key = min(cur_map.items(), key=lambda kv: kv[1])[0]

    fastest_name = DIMENSION_NAME.get(fastest_key, "")
    weak_name = DIMENSION_NAME.get(weak_key, "")

    # 总结
    summary = ""
    if len(history) <= 1:
        base = cur_map.get("professional")
        head = "这是你的第 1 次练习，已生成完整六维报告。" if base is None else (
            f"这是你的第 1 次练习（{DIMENSION_NAME.get('professional', '专业能力')} {round1(base)} 分）。"
        )
        summary = head + "建议先按提升计划完成 Day1 训练；再完成 1 次即可看到进步曲线，加油！"
    else:
        first = history[0]
        last = history[-1]
        parts = [
            f"{len(history)} 次练习综合分从 {round1(first['score'])} "
            f"{'提升到' if last['score'] >= first['score'] else '变为'} {round1(last['score'])}"
        ]
        if fastest_name:
            parts.append(f"{fastest_name}进步最快(+{round1(fastest_delta)})")
        if weak_name:
            parts.append(f"{weak_name}仍需加强")
        summary = "，".join(parts) + "。"

    return {
        "history": history,
        "fastest_dimension": fastest_name,
        "fastest_delta": round1(fastest_delta),
        "still_weak_dimension": weak_name,
        "summary": summary,
    }


# ============================================================
# 成长面板（体验增强 §12：进步排行 · 连续打卡）
# ============================================================
# 全部基于该用户**已完成**的会话，按本地时区聚合。
# 故意不做"每天必须打卡"的压力式设计：只陈述事实（连续几天、这周练了几场），
# 断签也不惩罚，符合"练够 100 次"的长期主义。

_GROWTH_SCAN_LIMIT = 200
_WEEK_LABELS = ["一", "二", "三", "四", "五", "六", "日"]


def build_growth(db: Session, user_id: str) -> dict:
    """成长面板数据：连续打卡、近 7 天格子、近 4 周汇总、维度首末对比、下一步目标。"""
    empty = {
        "streak_days": 0,
        "best_streak": 0,
        "total_sessions": 0,
        "total_minutes": 0,
        "active_days": 0,
        "last_7_days": [],
        "weeks": [],
        "dimension_delta": [],
        "next_goal": "先完成第 1 场模拟面试，成长曲线才有起点。",
        "note": "",
    }
    if not user_id:
        return empty

    try:
        rows = (
            db.execute(
                select(InterviewSession)
                .where(
                    InterviewSession.user_id == user_id,
                    InterviewSession.status == "finished",
                )
                .order_by(InterviewSession.created_at.asc())
                .limit(_GROWTH_SCAN_LIMIT)
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 成长面板失败不该影响页面
        print(f"[analytics] 成长面板查询失败: {exc}")
        return empty

    valid = [r for r in rows if (r.report_json or "").strip() not in _EMPTY_REPORTS]
    if not valid:
        return empty

    # ---- 打卡：按**本地日期**去重，断签不惩罚，只如实统计 ----
    day_counts: dict[str, int] = {}
    for row in valid:
        day = _date_of(row)
        if day:
            day_counts[day] = day_counts.get(day, 0) + 1
    days_sorted = sorted(day_counts.keys())
    # 注意：不能写 local_date_str(None)——它对 None 返回空串（as_local(None) 为 None），
    # 于是"今天"会变成空字符串，格子和连续天数会静默算成 0。用 local_iso() 取当前本地日期。
    today = local_iso()[:10]

    streak = 0
    if days_sorted:
        # 连续段从"今天或昨天"往前数：今天还没练不该显示断签（否则一早打开就看到 0）
        cursor = _to_date(today)
        if cursor is not None:
            if today not in day_counts and _to_date(days_sorted[-1]) != cursor - _ONE_DAY:
                # 最近一次练习既不是今天也不是昨天 → 连续已断
                cursor = None
            while cursor is not None:
                key = cursor.isoformat()
                if key in day_counts:
                    streak += 1
                    cursor -= _ONE_DAY
                else:
                    break

    best = 0
    run = 0
    prev = None
    for day in days_sorted:
        d = _to_date(day)
        if d is None:
            continue
        run = run + 1 if prev is not None and d - prev == _ONE_DAY else 1
        best = max(best, run)
        prev = d

    # ---- 近 7 天格子（含今天，最早在左）----
    last_7: list[dict] = []
    start = _to_date(today)
    if start is not None:
        for offset in range(6, -1, -1):
            d = start - _ONE_DAY * offset
            key = d.isoformat()
            last_7.append(
                {
                    "date": key[5:],
                    "weekday": _WEEK_LABELS[d.weekday()],
                    "sessions": day_counts.get(key, 0),
                    "done": key in day_counts,
                    "is_today": offset == 0,
                }
            )

    # ---- 近 4 周汇总 ----
    weeks: list[dict] = []
    if start is not None:
        for w in range(3, -1, -1):
            end = start - _ONE_DAY * (7 * w)
            begin = end - _ONE_DAY * 6
            bucket = [
                r
                for r in valid
                if (d := _to_date(_date_of(r))) is not None and begin <= d <= end
            ]
            scores = [_score_of_session(r) for r in bucket]
            scores = [s for s in scores if s > 0]
            weeks.append(
                {
                    "label": f"{begin.isoformat()[5:]}~{end.isoformat()[5:]}",
                    "sessions": len(bucket),
                    "avg_score": round1(sum(scores) / len(scores)) if scores else 0.0,
                }
            )

    # ---- 维度首末对比（只看首场与最近一场，避免被中间波动误导）----
    first_dims = _dims_map(load_json(valid[0].report_json, {}))
    last_dims = _dims_map(load_json(valid[-1].report_json, {}))
    dimension_delta = [
        {
            "key": key,
            "name": DIMENSION_NAME.get(key, key),
            "first": round1(first_dims[key]),
            "last": round1(last_dims[key]),
            "delta": round1(last_dims[key] - first_dims[key]),
        }
        for key in last_dims
        if key in first_dims
    ]
    dimension_delta.sort(key=lambda x: x["delta"], reverse=True)

    # ---- 下一步目标：给一句**能立刻执行**的话，而不是口号 ----
    total = len(valid)
    if streak == 0:
        next_goal = "连续练习还差第一步：今天完成 1 场（或 1 次 3 题特训）就能开始计数。"
    elif total < 3:
        next_goal = f"已连续 {streak} 天。再完成 {3 - total} 场就能看到有意义的进步曲线。"
    else:
        weakest = min(dimension_delta, key=lambda x: x["last"]) if dimension_delta else None
        next_goal = (
            f"已连续 {streak} 天、累计 {total} 场。当前最该补的是「{weakest['name']}」"
            f"（{weakest['last']} 分），建议去错题本做一次 3 题特训。"
            if weakest
            else f"已连续 {streak} 天、累计 {total} 场，继续保持。"
        )

    return {
        "streak_days": streak,
        "best_streak": best,
        "total_sessions": total,
        "total_minutes": int(sum(int(r.duration_seconds or 0) for r in valid) // 60),
        "active_days": len(days_sorted),
        "last_7_days": last_7,
        "weeks": weeks,
        "dimension_delta": dimension_delta,
        "next_goal": next_goal,
        "note": "统计口径：仅计入已完成的面试（超时收敛为 aborted 的会话不计），按本地时区归日。",
    }


def _to_date(value: str):
    """'YYYY-MM-DD' → date；非法值返回 None（脏数据不该让面板整块失败）。"""
    try:
        return _date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _score_of_session(row: InterviewSession) -> float:
    try:
        if row.score and float(row.score) > 0:
            return float(row.score)
    except (TypeError, ValueError):
        pass
    report = load_json(row.report_json, {})
    try:
        return float((report.get("overview") or {}).get("score") or 0.0)
    except (TypeError, ValueError):
        return 0.0
