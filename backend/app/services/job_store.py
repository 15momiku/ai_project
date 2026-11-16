"""岗位信息汇总（共享岗位库 + 各人独立投递进度 + 投递统计）。

本模块的产品语义（逐条对应页面上的口径）：

1. **岗位信息全员共享，投递进度各人独立。**
   岗位库（`JobPosting`）只有管理员能改，所有登录用户都能看；
   进度（`JobProgress`）每人一份，**管理员也看不到别人的**。
   唯一的例外是管理员专属的「账号投递概览」，它只统计**条数**、不展示具体进度，
   这就是「账号投递概览」的粒度：只显示条数，不显示进度。

2. **导入只负责岗位信息，不负责投递状态。**
   导入时「投递进度」一律记为「未投递」、「更新时间」记为导入当天；
   源表里即使有同名列也一律忽略（否则从别处拿到的汇总表会污染自己的投递记录）。

3. **两个「更新时间」不是一回事。**
   `JobPosting.posted_at` = 岗位信息进库时间（只读）；
   `JobProgress.progress_at` = 我的进度最后变动时间（进度一变就刷新）。

4. **删除岗位 / 清空岗位库会连带清除所有人的相关进度**，不留孤儿数据。

数据清洗全部在服务端做（`_clean_*`），因为「导入」是唯一的数据入口，
前端多传的字段一律丢弃：接口只认白名单里的字段，避免以后前端改一版就多出一列脏数据。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..constants import (
    JOB_FIELD_LIMITS,
    JOB_FUNNEL_STEPS,
    JOB_STAGES,
    JOB_STATUS_DEFAULT,
    JOB_STATUS_LIST,
    JOB_STATUS_META,
    JOB_TAGS_MAX,
    JOB_TAG_LIMIT,
    job_status_of,
)
from ..deps import dump_json, load_json, local_date_str, new_id
from ..deps import _LOCAL_TZ  # 本地时区：count_posted_today 判定「今天」的起止时刻
from ..models import JobPosting, JobProgress, now_utc

# 多值字段（工作地点 / 专业要求 / 招聘岗位）的分隔符：
# 中文顿号、逗号、分号、斜杠、竖线与换行都算分隔，与前端 splitTokens 完全一致
_SPLIT_RE = re.compile(r"[、,，;；/|｜\n\r\t]+")

# 示例岗位库（20 条演示数据，随代码走，不进数据库迁移）
_SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "jobs_seed.json"


# ============================================================
# 清洗
# ============================================================


def _clean_str(value: Any, limit: int) -> str:
    """去首尾空白并截断；None 一律当空串（绝不写 None 进库）。"""
    return str("" if value is None else value).strip()[:limit]


def split_tags(value: Any, max_items: int = JOB_TAGS_MAX) -> list[str]:
    """把「北京、上海 / 深圳」这类文本拆成去重后的标签数组。

    单值字段（列表也行）与字符串都接受：导入时单元格是字符串，
    手工录入时前端已经拆好数组，两条路径共用这一个函数。
    """
    if isinstance(value, (list, tuple, set)):
        raw: Iterable[Any] = value
    else:
        raw = _SPLIT_RE.split(str("" if value is None else value))
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _clean_str(item, JOB_TAG_LIMIT)
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= max_items:
            break
    return out


def clean_posting(payload: dict | None) -> dict:
    """按白名单清洗一条岗位信息；未列出的字段一律丢弃。"""
    src = payload or {}
    return {
        "company": _clean_str(src.get("company"), JOB_FIELD_LIMITS["company"]),
        "nature": _clean_str(src.get("nature"), JOB_FIELD_LIMITS["nature"]),
        "industry": _clean_str(src.get("industry"), JOB_FIELD_LIMITS["industry"]),
        "audience": _clean_str(src.get("audience"), JOB_FIELD_LIMITS["audience"]),
        "locations": split_tags(src.get("locations")),
        "majors": split_tags(src.get("majors")),
        "positions": split_tags(src.get("positions")),
        "notice_url": _clean_str(src.get("notice_url"), JOB_FIELD_LIMITS["notice_url"]),
        "apply_url": _clean_str(src.get("apply_url"), JOB_FIELD_LIMITS["apply_url"]),
        "note": _clean_str(src.get("note"), JOB_FIELD_LIMITS["note"]),
    }


def is_valid_status(status: str) -> bool:
    return status in JOB_STATUS_META


# ============================================================
# 读取
# ============================================================


def _posting_row(db: Session, job_id: str) -> JobPosting | None:
    try:
        return db.get(JobPosting, job_id)
    except Exception as exc:  # noqa: BLE001 会话异常时按「不存在」处理，避免 500
        print(f"[jobs] 读取岗位失败: {exc}")
        return None


def list_postings(db: Session) -> list[JobPosting]:
    """全部岗位，最新的在前。

    排序固定为「录入时间倒序」：这是列表页的默认顺序，前端再按用户点击的列重排。
    放在 SQL 里而不是 Python 里，是为了让接口返回的顺序与数据库无关地稳定。
    """
    rows = db.execute(select(JobPosting).order_by(JobPosting.posted_at.desc(), JobPosting.company)).scalars()
    return list(rows)


def progress_map(db: Session, user_id: str) -> dict[str, JobProgress]:
    """当前用户的进度：job_id -> JobProgress（只查自己，天然不可能读到别人的）。"""
    rows = db.execute(select(JobProgress).where(JobProgress.user_id == user_id)).scalars()
    return {row.job_id: row for row in rows}


def build_record(posting: JobPosting, progress: JobProgress | None) -> dict:
    """岗位 + 我的进度 → 一条渲染用记录（时间统一转成**本地时区**的展示字符串）。"""
    status = progress.status if progress is not None and is_valid_status(progress.status) else JOB_STATUS_DEFAULT
    return {
        "job_id": posting.job_id,
        "company": posting.company or "",
        "nature": posting.nature or "",
        "industry": posting.industry or "",
        "audience": posting.audience or "",
        "locations": load_json(posting.locations_json, []) or [],
        "majors": load_json(posting.majors_json, []) or [],
        "positions": load_json(posting.positions_json, []) or [],
        "notice_url": posting.notice_url or "",
        "apply_url": posting.apply_url or "",
        "note": posting.note or "",
        # 「更新时间」= 岗位信息进库时间（只读，不随编辑变化）
        "posted_at": local_date_str(posting.posted_at),
        # 我的投递进度与「进度更新时间」
        "progress": status,
        "progress_at": local_date_str(progress.progress_at) if progress is not None else "",
    }


def list_records(db: Session, user_id: str) -> list[dict]:
    """共享岗位库 + 我的进度，合并成一份记录列表。"""
    progress = progress_map(db, user_id)
    return [build_record(row, progress.get(row.job_id)) for row in list_postings(db)]


def count_posted_yesterday(db: Session) -> int:
    """**昨天**新入库的岗位条数（列表上方「昨日更新 N 家」标注的数据源）。

    口径写死在这里，避免前端各算各的：

    - **统计上一日，不是今天**。今天的数据还在陆续导入、数字会一直跳，
      而「昨天」是一段已经走完、不会再变的完整区间 —— 对「这个岗位库
      最近有没有在维护」这个问题，它比一个还在涨的当日数字更可信。
    - **按 `posted_at`（进库时间）算**，它就是列表里的「更新时间」。
      不用 `created_at`：两者在导入路径上写入的是同一个时间戳，
      但 `posted_at` 才是页面上展示、且示例数据会刻意往前推的那个字段，
      用别的字段会出现「列表显示昨天更新、标注却不认」的自相矛盾。
    - **按本地时区的「昨天」算**。`posted_at` 存的是 UTC，直接拿
      `date(posted_at)` 比会在 UTC+8 的凌晨把跨日的数据算错。因此先算出
      本地昨天的 00:00 与次日 00:00，再转回 UTC 做左闭右开区间比较。
    - **按岗位条数算，不去重企业**：同一家企业昨天导入 3 条岗位就是 3 条，
      与列表「共 N 条岗位」的口径一致；去重只在导入时按企业名做（见 bulk_create）。

    这个数字**不受筛选 / 搜索影响**（全库口径）：它是「昨天岗位库新增了多少」，
    跟着筛选条件变会让人误以为「昨天只更新了这么点」。
    """
    yesterday = datetime.now(_LOCAL_TZ).date() - timedelta(days=1)
    start_local = datetime.combine(yesterday, time.min, tzinfo=_LOCAL_TZ)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = (start_local + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)
    return int(
        db.query(func.count(JobPosting.job_id))
        .filter(JobPosting.posted_at >= start_utc, JobPosting.posted_at < end_utc)
        .scalar()
        or 0
    )


# ============================================================
# 写入（全部登录用户可用；登录判定在路由层用 require_login）
# ============================================================


def create_posting(db: Session, payload: dict, created_by: str = "") -> dict:
    """新增一条岗位（手工录入路径；批量导入见 bulk_create）。"""
    data = clean_posting(payload)
    now = now_utc()
    row = JobPosting(
        job_id=new_id(),
        company=data["company"],
        nature=data["nature"],
        industry=data["industry"],
        audience=data["audience"],
        locations_json=dump_json(data["locations"]),
        majors_json=dump_json(data["majors"]),
        positions_json=dump_json(data["positions"]),
        notice_url=data["notice_url"],
        apply_url=data["apply_url"],
        note=data["note"],
        created_by=created_by,
        posted_at=now,
        created_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return build_record(row, None)


def update_posting(db: Session, job_id: str, payload: dict) -> dict | None:
    """修改一条岗位信息。

    **刻意不动 `posted_at`**：它是「岗位信息进库时间」，改了就不再是它了
    （编辑接口同样不改 posted_at）。
    """
    row = _posting_row(db, job_id)
    if row is None:
        return None
    data = clean_posting(payload)
    row.company = data["company"]
    row.nature = data["nature"]
    row.industry = data["industry"]
    row.audience = data["audience"]
    row.locations_json = dump_json(data["locations"])
    row.majors_json = dump_json(data["majors"])
    row.positions_json = dump_json(data["positions"])
    row.notice_url = data["notice_url"]
    row.apply_url = data["apply_url"]
    row.note = data["note"]
    db.commit()
    db.refresh(row)
    return build_record(row, None)


def delete_posting(db: Session, job_id: str) -> bool:
    """删除一条岗位，**并清除所有用户在该岗位上的进度**（不留孤儿数据）。"""
    row = _posting_row(db, job_id)
    if row is None:
        return False
    db.query(JobProgress).filter(JobProgress.job_id == job_id).delete(synchronize_session=False)
    db.delete(row)
    db.commit()
    return True


def clear_postings(db: Session) -> int:
    """清空岗位库，同样连带清除所有人的相关进度。返回被清掉的岗位条数。"""
    removed = int(db.query(func.count(JobPosting.job_id)).scalar() or 0)
    db.query(JobProgress).delete(synchronize_session=False)
    db.query(JobPosting).delete(synchronize_session=False)
    db.commit()
    return removed


def bulk_create(db: Session, items: Any, dedupe: bool = False, created_by: str = "") -> dict:
    """批量导入（Excel / CSV / 粘贴都走这里）。

    `dedupe=False`（**默认**）：**不去重**，源表里有多少行就导入多少行，
    同一家企业出现多次也会各自入库。这是导入岗位数据的默认口径 ——
    一份校招汇总表里，同一家企业在不同批次 / 不同岗位方向下重复出现是**常态**
    （比如「华为」同时有研发岗和销售岗两行），默认去重会把后面那些行悄悄吃掉，
    而用户从结果里只看到「跳过 N 条」，很难判断丢的是不是自己要的数据。
    需要按企业合并时，显式传 `dedupe=True`。

    `dedupe=True` 时**按企业名称去重**（同一批内部也去重），与已有岗位库比对。

    两种模式下**「企业名称为空」的行都会被跳过**：空企业名是无效行，
    不是重复行，与去重无关。
    返回 added / skipped / total，让导入结果页能如实说明「导入了几条、跳过几条」。
    """
    rows = items if isinstance(items, list) else []
    stamp = now_utc()
    existing: set[str] = set()
    if dedupe:
        existing = {
            (row.company or "").strip().lower()
            for row in db.execute(select(JobPosting)).scalars()
        }

    added = 0
    skipped = 0
    for raw in rows:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        data = clean_posting(raw)
        if not data["company"]:
            skipped += 1
            continue
        key = data["company"].lower()
        if dedupe and key in existing:
            skipped += 1
            continue
        existing.add(key)
        db.add(
            JobPosting(
                job_id=new_id(),
                company=data["company"],
                nature=data["nature"],
                industry=data["industry"],
                audience=data["audience"],
                locations_json=dump_json(data["locations"]),
                majors_json=dump_json(data["majors"]),
                positions_json=dump_json(data["positions"]),
                notice_url=data["notice_url"],
                apply_url=data["apply_url"],
                note=data["note"],
                created_by=created_by,
                posted_at=stamp,
                created_at=stamp,
            )
        )
        added += 1

    db.commit()
    total = int(db.query(func.count(JobPosting.job_id)).scalar() or 0)
    return {"added": added, "skipped": skipped, "total": total}


# ============================================================
# 投递进度（每个登录用户只能改自己的）
# ============================================================


def set_progress(db: Session, user_id: str, job_id: str, status: str) -> dict | None:
    """设置我的投递进度。

    - 岗位不存在 → 返回 None（路由层转 404，不泄露岗位是否存在之外的任何信息）；
    - `status` 为「未投递」→ **删掉这一行**（「没投过」不该占存储，统计也就不用过滤）；
    - 其它状态 → upsert，并把 `progress_at` 刷新为当天。
    """
    if _posting_row(db, job_id) is None:
        return None

    row = (
        db.execute(
            select(JobProgress).where(JobProgress.user_id == user_id, JobProgress.job_id == job_id)
        ).scalars().first()
    )

    if status == JOB_STATUS_DEFAULT:
        if row is not None:
            db.delete(row)
            db.commit()
        return {"job_id": job_id, "status": JOB_STATUS_DEFAULT, "progress_at": ""}

    now = now_utc()
    if row is None:
        row = JobProgress(
            id=new_id(), user_id=user_id, job_id=job_id, status=status, progress_at=now, updated_at=now
        )
        db.add(row)
    else:
        row.status = status
        row.progress_at = now
        row.updated_at = now
    db.commit()
    db.refresh(row)
    return {"job_id": job_id, "status": row.status, "progress_at": local_date_str(row.progress_at)}


# ============================================================
# 统计（只统计「投递进度不为未投递」的岗位）
# ============================================================


def compute_stats(records: list[dict]) -> dict:
    """投递统计的**唯一口径**：只统计已投递的记录。

    四个区块的计算规则（前后端共用这一份，避免「换了前端就换了算法」）：

    - **漏斗按阶段递进推算**：一条记录处于「已初面」，视为已通过初筛、测评、AI 面。
      若跳过了某个环节（如免笔试），漏斗会把它算作已通过——这是刻意的简化，
      明细表才是精确事实，页面上也照原样标注了「递进推算」。
    - **已挂按环节归因**：`stage` 决定它挂在哪个环节（初筛挂 → 简历初筛）。
    - **转化率**：与上一级相比（投递一级固定 100%）。
    """
    applied = [r for r in records if r.get("progress") and r["progress"] != JOB_STATUS_DEFAULT]
    total = len(records)

    reached = [len(applied), 0, 0, 0, 0, 0, 0]
    rejected = {name: 0 for name in JOB_STAGES}
    status_counts: dict[str, int] = {}
    nature_counts: dict[str, int] = {}
    active = fail = passed = 0

    for rec in applied:
        meta = job_status_of(rec["progress"])
        stage = int(meta["stage"])
        kind = meta["type"]
        # 第 k 级（过初筛…过终面）的通过数：stage >= k + 1 即视为已通过该环节
        for k in range(1, 7):
            if stage >= k + 1:
                reached[k] += 1
        if kind == "fail":
            name = JOB_STAGES[stage - 1] if 1 <= stage <= len(JOB_STAGES) else ""
            if name:
                rejected[name] += 1
            fail += 1
        elif kind == "pass":
            passed += 1
        else:
            active += 1
        status_counts[rec["progress"]] = status_counts.get(rec["progress"], 0) + 1
        nature = rec.get("nature") or "未填写"
        nature_counts[nature] = nature_counts.get(nature, 0) + 1

    funnel_max = reached[0] or 1
    funnel = []
    for i, name in enumerate(JOB_FUNNEL_STEPS):
        value = reached[i]
        prev = value if i == 0 else reached[i - 1]
        funnel.append(
            {
                "name": name,
                "value": value,
                "rate": 100 if i == 0 else (round(value / prev * 100) if prev else 0),
                "percent": round(value / funnel_max * 100),
            }
        )

    reject_max = max([*rejected.values(), 1])
    rejected_rows = [
        {"name": f"{name}挂", "value": rejected[name], "percent": round(rejected[name] / reject_max * 100)}
        for name in JOB_STAGES
    ]

    status_max = max([*status_counts.values(), 1])
    status_rows = sorted(
        (
            {
                "name": name,
                "value": status_counts[name],
                "type": job_status_of(name)["type"],
                "percent": round(status_counts[name] / status_max * 100),
            }
            for name in JOB_STATUS_LIST
            if status_counts.get(name)
        ),
        key=lambda item: -item["value"],
    )

    nature_rows_raw = sorted(nature_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    nature_max = max([*(count for _, count in nature_rows_raw), 1])
    nature_rows = [
        {"name": name, "value": count, "percent": round(count / nature_max * 100)}
        for name, count in nature_rows_raw
    ]

    return {
        "total": total,
        "applied": len(applied),
        "applied_rate": round(len(applied) / total * 100) if total else 0,
        "active": active,
        "fail": fail,
        # 键名用 passed 而不是 pass：pass 是 Python 关键字，不能作为字段名
        "passed": passed,
        "reject_total": fail,
        "funnel": funnel,
        "rejected": rejected_rows,
        "status_dist": status_rows,
        "nature_dist": nature_rows,
        "nature_kinds": len(nature_rows),
    }


def load_seed_jobs() -> list[dict]:
    """读取内置示例岗位（20 条）。

    示例里的时间是**相对今天**的（文件里存 `days_ago`），每次载入都会重新算成
    当天日期，这样示例数据永远不会「看起来是一年前的」。
    """
    try:
        raw = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
        print(f"[jobs] 示例岗位读取失败: {exc}")
        return []

    items = raw.get("jobs") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []

    today = datetime.now(timezone.utc).date()
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        days_ago = item.get("days_ago")
        try:
            delta = int(days_ago) if days_ago is not None else 0
        except (TypeError, ValueError):
            delta = 0
        out.append(
            {
                # 示例数据的「更新时间」= 今天往前推 N 天（与 days_ago 对应）
                "_posted_at": datetime.combine(today - timedelta(days=max(0, delta)), datetime.min.time(), tzinfo=timezone.utc),
                **{k: v for k, v in item.items() if k != "days_ago"},
            }
        )
    return out


def seed_postings(db: Session, created_by: str = "") -> dict:
    """载入示例岗位（按企业名称去重，重复点也只补缺的那几条）。"""
    rows = load_seed_jobs()
    if not rows:
        return {"added": 0, "skipped": 0, "total": int(db.query(func.count(JobPosting.job_id)).scalar() or 0)}

    existing = {
        (row.company or "").strip().lower()
        for row in db.execute(select(JobPosting)).scalars()
    }
    added = 0
    skipped = 0
    for raw in rows:
        data = clean_posting(raw)
        if not data["company"]:
            skipped += 1
            continue
        key = data["company"].lower()
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        posted_at = raw.get("_posted_at") or now_utc()
        db.add(
            JobPosting(
                job_id=new_id(),
                company=data["company"],
                nature=data["nature"],
                industry=data["industry"],
                audience=data["audience"],
                locations_json=dump_json(data["locations"]),
                majors_json=dump_json(data["majors"]),
                positions_json=dump_json(data["positions"]),
                notice_url=data["notice_url"],
                apply_url=data["apply_url"],
                note=data["note"],
                created_by=created_by,
                posted_at=posted_at,
                created_at=now_utc(),
            )
        )
        added += 1
    db.commit()
    total = int(db.query(func.count(JobPosting.job_id)).scalar() or 0)
    return {"added": added, "skipped": skipped, "total": total}
