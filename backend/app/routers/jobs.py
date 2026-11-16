"""岗位信息汇总路由（共享岗位库 + 各人独立投递进度 + 统计 · API_CONTRACT §2.14）。

权限模型（与全站既有约定一致，不做第二套）：

| 能力 | 已登录用户 | 游客 |
|---|:---:|:---:|
| 查看共享岗位库 | ✅ | ❌ 401 |
| 导入 / 载入示例 / 清空 / 删除 / 修改岗位 | ✅ | ❌ 401 |
| 修改**自己**的投递进度 | ✅ | ❌ 401 |
| 查看投递统计（基于自己进度） | ✅ | ❌ 401 |

四条刻意的取舍：

1. **整个模块要求登录**（`require_login`），连「看岗位库」也不例外。
   本项目的游客走**进程内内存库**，岗位库在那个库里是空的，
   放行只会得到一个「岗位库是空的」的假象，
   还会与「游客模式下不展示任何数据」的产品口径自相矛盾。
   因此导航里它和「模拟面试 / 我的面试」一样带锁标记，游客看到的是说明卡。

2. **岗位库由登录用户共同维护**：没有管理员角色，也没有白名单。
   岗位信息是全员共享的一份台账，任何登录用户都能导入、修正、删除；
   每一次写入都记下 `created_by`，出问题时能追溯到是谁改的。

3. **投递进度各人一份**（`require_login` + 令牌判定归属）：
   别人的进度在任何接口里都读不到，请求体里也没有、不接受 `user_id`。

4. **删除岗位 / 清空岗位库会连带清除所有人的相关进度**（`job_store` 里保证），
   避免留下指向不存在岗位的孤儿进度行。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..constants import JOB_STAGES, JOB_STATUS_DEFAULT, JOB_STATUS_LIST
from ..db import get_db
from ..deps import require_login
from ..models import User
from ..schemas import (
    JobBulkRequest,
    JobBulkResponse,
    JobListResponse,
    JobProgressRequest,
    JobProgressResponse,
    JobRecord,
    JobStatsResponse,
    JobUpsertRequest,
    OkResponse,
)
from ..services import job_store

router = APIRouter()

# 列表接口的固定说明（前端直接展示，避免同一句话在前端写死两份）
_LIST_NOTE = "岗位信息全员共享、登录即可维护；投递进度各自独立保存，别人看不到你的进度。"


def _list_payload(db: Session, user: User) -> JobListResponse:
    records = job_store.list_records(db, user.user_id or "")
    return JobListResponse(
        items=[JobRecord(**rec) for rec in records],
        total=len(records),
        posted_yesterday=job_store.count_posted_yesterday(db),
        statuses=JOB_STATUS_LIST,
        stages=JOB_STAGES,
        note=_LIST_NOTE,
    )


# ============================================================
# GET /api/jobs —— 共享岗位库 + 我的投递进度
# ============================================================


@router.get("/jobs", response_model=JobListResponse, summary="岗位库列表（含我的投递进度）")
async def list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobListResponse:
    """岗位信息全员共享，进度只有自己那一份。

    维护入口（导入 / 载入示例 / 清空 / 删除 / 修改）对**所有登录用户**开放，
    因此响应里不再需要「我是不是管理员」这种开关。
    """
    return _list_payload(db, user)


# ============================================================
# GET /api/jobs/stats —— 投递统计（只统计已投递）
# ============================================================


@router.get("/jobs/stats", response_model=JobStatsResponse, summary="投递统计（基于自己的进度）")
async def job_stats(
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobStatsResponse:
    """聚合口径见 `job_store.compute_stats`：只统计「投递进度不为未投递」的岗位。"""
    records = job_store.list_records(db, user.user_id or "")
    data = job_store.compute_stats(records)
    return JobStatsResponse(
        **data,
        rejection_rate=round(data["fail"] / data["applied"] * 100) if data["applied"] else 0,
        note="漏斗按阶段递进推算：未记录中间环节的岗位按「已通过」计；明细表才是精确事实。",
    )


# ============================================================
# PUT /api/jobs/{job_id}/progress —— 修改我的投递进度
# ============================================================


@router.put(
    "/jobs/{job_id}/progress",
    response_model=JobProgressResponse,
    summary="设置我的投递进度（未投递 = 删除记录）",
)
async def set_job_progress(
    job_id: str,
    req: JobProgressRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobProgressResponse:
    """状态在服务端逐条校验：不在字典里的值直接 400，不靠前端隐藏下拉。

    归属由令牌决定（请求体里没有、也不接受 user_id），因此**只能改自己的**。
    """
    status = (req.status or "").strip()
    if not job_store.is_valid_status(status):
        raise HTTPException(status_code=400, detail=f"无效的投递进度：{status or '（空）'}")

    result = job_store.set_progress(db, user.user_id or "", job_id, status)
    if result is None:
        raise HTTPException(status_code=404, detail="岗位不存在")

    message = "已清空该岗位的投递记录" if status == JOB_STATUS_DEFAULT else f"投递进度已更新为「{status}」"
    return JobProgressResponse(**result, message=message)


# ============================================================
# 岗位库维护（全部登录用户可用）
# ============================================================


@router.post("/jobs", response_model=JobRecord, summary="新增岗位")
async def create_job(
    req: JobUpsertRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobRecord:
    if not (req.company or "").strip():
        raise HTTPException(status_code=400, detail="企业名称不能为空")
    return JobRecord(**job_store.create_posting(db, req.model_dump(), user.user_id or ""))


@router.post("/jobs/bulk", response_model=JobBulkResponse, summary="批量导入岗位")
async def bulk_jobs(
    req: JobBulkRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobBulkResponse:
    """Excel / CSV / 粘贴 / 示例数据都走这一个入口（解析在前端完成）。

    只读取 9 个岗位信息字段：源表里的「投递进度」「更新时间」即使同名也一律忽略，
    进度统一为「未投递」、录入时间统一为导入当天。
    """
    if not req.jobs:
        raise HTTPException(status_code=400, detail="没有可导入的数据")
    result = job_store.bulk_create(
        db, [item.model_dump() for item in req.jobs], dedupe=req.dedupe, created_by=user.user_id or ""
    )
    return JobBulkResponse(
        **result,
        message=f"导入完成：新增 {result['added']} 条，跳过 {result['skipped']} 条",
    )


@router.post("/jobs/seed", response_model=JobBulkResponse, summary="载入示例岗位")
async def seed_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobBulkResponse:
    """载入内置的 20 条示例岗位，按企业名称去重（重复点只补缺的那几条）。"""
    result = job_store.seed_postings(db, created_by=user.user_id or "")
    return JobBulkResponse(
        **result,
        message=f"示例数据：新增 {result['added']} 条，跳过 {result['skipped']} 条",
    )


@router.delete("/jobs", response_model=OkResponse, summary="清空岗位库")
async def clear_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> OkResponse:
    """清空岗位库，**并清除所有人的相关进度**（不留孤儿数据）。"""
    removed = job_store.clear_postings(db)
    return OkResponse(ok=True, message=f"已清空 {removed} 条岗位，并清除所有人在这些岗位上的投递进度")


@router.put("/jobs/{job_id}", response_model=JobRecord, summary="修改岗位")
async def update_job(
    job_id: str,
    req: JobUpsertRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JobRecord:
    if not (req.company or "").strip():
        raise HTTPException(status_code=400, detail="企业名称不能为空")
    row = job_store.update_posting(db, job_id, req.model_dump())
    if row is None:
        raise HTTPException(status_code=404, detail="岗位不存在")
    # 返回的是「岗位信息」本身；进度由前端在保存后重新拉列表（这里不带调用者的进度，
    # 避免让一个用于维护岗位库的接口顺带回答「我投了没有」）。
    return JobRecord(**row)


@router.delete("/jobs/{job_id}", response_model=OkResponse, summary="删除岗位")
async def delete_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> OkResponse:
    """删除单条岗位，连带清除所有用户在该岗位上的进度。"""
    if not job_store.delete_posting(db, job_id):
        raise HTTPException(status_code=404, detail="岗位不存在")
    return OkResponse(ok=True, message="已删除该岗位，并清除所有人在它上面的投递进度")
