"""同岗位 / 全站基准分布（体验增强 §6 可信度）。

用途：报告页把「本场得分」放到历史分布上做刻度（P25 / P50 / P75），
让用户知道自己是「普遍水平」还是「明显偏差」，而不是只看到一个孤立数字。

诚实原则（沿用项目既有风格）：
- 样本不足（< `_MIN_SAMPLE`）时**不编造基准**，返回 `available=False` + `note`；
- `note` 明确写出「演示基准」或「样本不足」，前端据此展示标注，
  绝不让用户误以为这是权威统计。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import load_json, round1
from ..models import InterviewSession

# 低于该样本量不给基准：两三条数据算出来的 P75 会误导用户
_MIN_SAMPLE = 8
# 扫描上限：报告生成时才调用一次，但也不能让历史越多越慢
_SCAN_LIMIT = 120

# 「没有真实报告」的统一判定：与 `analytics._EMPTY_REPORTS` /
# `routers.report` 的口径保持一致，避免同一份数据在不同模块得出相反结论。
_EMPTY_REPORTS = ("", "{}", "null", "[]", "None")


def _percentile(values: list[float], q: float) -> float:
    """线性插值分位数（q ∈ [0,1]）。values 必须非空。"""
    data = sorted(values)
    if len(data) == 1:
        return data[0]
    pos = (len(data) - 1) * q
    low = int(pos)
    high = min(low + 1, len(data) - 1)
    frac = pos - low
    return data[low] + (data[high] - data[low]) * frac


def _distribution(values: list[float]) -> dict[str, float]:
    return {
        "p25": round1(_percentile(values, 0.25)),
        "p50": round1(_percentile(values, 0.50)),
        "p75": round1(_percentile(values, 0.75)),
    }


def _unavailable(note: str, sample_size: int = 0, scope: str = "all") -> dict:
    return {
        "available": False,
        "note": note,
        "sample_size": sample_size,
        "p25": 0.0,
        "p50": 0.0,
        "p75": 0.0,
        "scope": scope,
        "dims": {},
    }


def benchmarks(db: Session, job_title: str = "") -> dict:
    """同岗位（样本不够则退化为全站）的分数基准。

    返回：
        {
          available, note, sample_size, p25, p50, p75, scope,
          dims: { 维度名: {p25, p50, p75} }
        }

    只读 `finished` 会话：`aborted` 的报告可能由不完整对话生成，
    把它们的低分算进基准会系统性低估「正常水平」。
    """
    # 1) 同岗位优先
    rows = _scan(db, job_title) if job_title else []
    scope = "job"
    if len(rows) < _MIN_SAMPLE:
        rows_all = _scan(db, "")
        # 同岗位样本不够时退化到全站，但把 scope 如实告诉前端
        if len(rows_all) > len(rows):
            rows, scope = rows_all, "all"

    if len(rows) < _MIN_SAMPLE:
        return _unavailable(
            f"同岗位历史样本不足（当前 {len(rows)} 份，需 ≥{_MIN_SAMPLE} 份），暂不展示基准刻度",
            sample_size=len(rows),
            scope=scope,
        )

    overall = [r["score"] for r in rows if r["score"] > 0]
    dist = _distribution(overall) if overall else {"p25": 0.0, "p50": 0.0, "p75": 0.0}

    dims: dict[str, dict[str, float]] = {}
    for name in {n for r in rows for n in r["dims"]}:
        vals = [r["dims"][name] for r in rows if name in r["dims"] and r["dims"][name] > 0]
        if len(vals) >= _MIN_SAMPLE:
            dims[name] = _distribution(vals)

    where = "同岗位" if scope == "job" else "全站"
    return {
        "available": True,
        "note": f"基准来自{where} {len(rows)} 份已完成面试的实测分布（P25 / P50 / P75）",
        "sample_size": len(rows),
        "scope": scope,
        **dist,
        "dims": dims,
    }


def _scan(db: Session, job_title: str) -> list[dict]:
    """读取已结束会话的报告分数（含六维），最多 `_SCAN_LIMIT` 份。

    只 select 需要的两列，避免把整份 report_json 无谓地全部载入到内存里做无关解析。
    """
    stmt = (
        select(InterviewSession.report_json, InterviewSession.score)
        .where(InterviewSession.status == "finished")
        .order_by(InterviewSession.created_at.desc())
        .limit(_SCAN_LIMIT)
    )
    if job_title:
        stmt = stmt.where(InterviewSession.job_title == job_title)

    out: list[dict] = []
    try:
        for report_json, score in db.execute(stmt).all():
            raw = report_json or ""
            if raw in _EMPTY_REPORTS:
                continue
            report = load_json(raw, {})
            if not isinstance(report, dict):
                continue
            dims: dict[str, float] = {}
            for d in report.get("dimensions") or []:
                if not isinstance(d, dict):
                    continue
                name = str(d.get("name") or "")
                try:
                    value = float(d.get("score") or 0.0)
                except (TypeError, ValueError):
                    continue
                if name:
                    dims[name] = value
            try:
                total = float(score or 0.0)
            except (TypeError, ValueError):
                total = 0.0
            if total <= 0:
                total = float((report.get("overview") or {}).get("score") or 0.0)
            out.append({"score": total, "dims": dims})
    except Exception as exc:  # noqa: BLE001 基准是增强信息，读失败不能影响报告
        print(f"[benchmark] 读取历史分布失败（已忽略）: {exc}")
        return []
    return out
