"""数据库连接与会话管理（文档 §3.6 数据存储设计）。

默认使用 SQLite 开箱即跑；把 DATABASE_URL 换成
mysql+pymysql://user:pwd@host/db 或 postgresql+psycopg://... 即可无缝切到 MySQL / PostgreSQL。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_connect_args = {}
if settings.resolved_database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.resolved_database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    """建表（幂等）。

    注意：`create_all` 只建**缺失的表**，不会给已有表加列。体验增强新增了
    `interview_session.retake_count` / `hint_count`，老库若缺列会在查询时报
    `no such column`，所以建表后再做一次幂等补列（`_ensure_extra_columns`）。
    """
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(bind=engine)
    _ensure_extra_columns()
    _ensure_guest_engine()


# ============================================================
# 游客专用：进程内内存库（数据不落盘、重启即消失）
# ============================================================
#
# 产品要求：**游客模式下所有数据不可见、不保存，面试记录不保存**。
#
# 实现方式是把「有没有令牌」这一个信号变成两条完全独立的存储链路：
#   · 带令牌（登录）→ 正常落盘库；
#   · 不带令牌（游客）→ 进程内 `sqlite://`（StaticPool 保证同一进程共享同一个内存库）。
#
# 这样做而不是「游客干脆不写库」，是因为面试引擎本身要靠一条会话记录在
# /start → /answer → /report 之间传递对话；彻底不写会让整条链路失效。
# 内存库让游客**照样能完整跑一场**，同时满足：
#   1) 不落盘：进程退出/重启后游客数据彻底消失，磁盘上找不到任何游客痕迹；
#   2) 不可见：游客读的是内存库，**物理上读不到登录用户的数据**（不是靠权限过滤，
#      而是根本不在同一个库里），公共 guest 账号历史数据也不会再被任何游客读到；
#   3) 录音与文档：游客上传的 PDF 只落临时文件、解析完即删（见 profile.py）。
#
# 代价（如实记录）：同一进程内的多个游客共享这个内存库，猜到 32 位 hex 的会话 id
# 仍可互看；因此前端把「我的面试 / 简历优化」对游客完全隐藏，后端也不向游客
# 提供任何列表接口。多 worker 部署时每个进程各有一份内存库，游客刷新可能换进程
# → 表现为「记录消失了」，这与「游客不保存」的产品语义一致。
_guest_engine = None
_GuestSessionLocal = None


def _ensure_guest_engine():
    """惰性创建游客内存库（含建表 + 预置公共公司库）。返回 guest 的 sessionmaker。"""
    global _guest_engine, _GuestSessionLocal
    if _GuestSessionLocal is not None:
        return _GuestSessionLocal

    from sqlalchemy.pool import StaticPool

    from . import models  # noqa: F401  确保模型已注册

    _guest_engine = create_engine(
        "sqlite://",
        echo=False,
        future=True,
        # 内存库必须共用同一个连接，否则每次连接都是一个空库
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=_guest_engine)
    _GuestSessionLocal = sessionmaker(
        bind=_guest_engine, autoflush=False, autocommit=False, future=True
    )
    _seed_guest_companies()
    return _GuestSessionLocal


def _seed_guest_companies() -> int:
    """把内置公司库预置进游客内存库。

    `company` 是**公共参考数据**（表里没有 `user_id`，对所有访客开放），游客的匹配度与
    出题同样要用。游客内存库初始是空的，不预置的话：
      · `GET /api/company/{id}` 只能靠内置库按 id 反查，manual 覆盖会丢；
      · `/api/interview/start` 按 company_id 读公司会拿到 None，出题丢掉公司定制
        （面试风格 / 业务背景），游客的第一场体验明显变差。
    50 条以内、每个进程只做一次，成本可忽略。失败只记日志，不阻断。
    """
    from .models import Company
    from .services import company_service

    try:
        with _GuestSessionLocal() as db:
            have = {c.company_id for c in db.query(Company).all()}
            added = 0
            for raw in company_service.load_companies():
                profile = company_service._to_profile(raw)
                name = profile.get("name") or ""
                cid = company_service.company_id_for(name)
                if not cid or cid in have:
                    continue
                db.add(
                    Company(
                        company_id=cid,
                        name=name,
                        profile_json=json.dumps(profile, ensure_ascii=False),
                        source="auto",
                    )
                )
                added += 1
            if added:
                db.commit()
        return added
    except Exception as exc:  # noqa: BLE001
        print(f"[db] 游客库预置公司画像失败（不影响流程）: {exc}")
        return 0


def guest_db_session() -> Session:
    """开一个游客内存库会话（供非 FastAPI 依赖场景复用）。"""
    return _ensure_guest_engine()()


# 老库需要补的列：表名 -> {列名: DDL 片段}
# 只放「新增的可选列」，且必须带默认值，否则已有行会因 NOT NULL 无默认而报错。
_EXTRA_COLUMNS: dict[str, dict[str, str]] = {
    "interview_session": {
        "retake_count": "INTEGER NOT NULL DEFAULT 0",
        "hint_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "wrong_question": {
        # 体验增强 §9：微面试要按原题所在环节出题，老库需要补这一列
        "section": "VARCHAR(32) NOT NULL DEFAULT ''",
    },
    "job_posting": {
        # 汇总列表表头新增「行业」列：老库（岗位信息汇总上线后建的）没有这一列，
        # 不补的话查询会报 `no such column: job_posting.industry`。
        # 默认空串是正确语义：历史岗位确实没填过行业，列表如实显示「—」。
        "industry": "VARCHAR(60) NOT NULL DEFAULT ''",
    },
}


def _ensure_extra_columns() -> None:
    """幂等地为已有表补列（SQLite / MySQL / PostgreSQL 的 ADD COLUMN 语法一致）。

    失败只打印日志、不抛异常：补列是兼容老库的便利手段，
    不应该因为某个库不支持而阻断整个服务启动。
    """
    from sqlalchemy import inspect, text

    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
    except Exception as exc:  # noqa: BLE001
        print(f"[db] 检查表结构失败，跳过补列: {exc}")
        return

    for table, columns in _EXTRA_COLUMNS.items():
        if table not in tables:
            continue
        try:
            have = {c["name"] for c in inspector.get_columns(table)}
        except Exception as exc:  # noqa: BLE001
            print(f"[db] 读取 {table} 列信息失败，跳过补列: {exc}")
            continue
        missing = [name for name in columns if name not in have]
        if not missing:
            continue
        for name in missing:
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {columns[name]}"))
                print(f"[db] 已为老库补列：{table}.{name}")
            except Exception as exc:  # noqa: BLE001
                print(f"[db] 补列 {table}.{name} 失败（该库可能已有等价字段）: {exc}")



def get_db(request: Request = None) -> Iterator[Session]:
    """FastAPI 依赖：请求级数据库会话。

    **游客走内存库**（见上文「游客专用」一节）：带 `Authorization` 头的请求用落盘库，
    没带的用进程内内存库。这里只看「有没有带令牌头」，不验证令牌是否有效——
    无效令牌由 `deps.current_user` 统一返回 401，避免同一件事有两个判定入口。
    """
    auth_header = ""
    if request is not None:
        auth_header = request.headers.get("authorization") or ""
    path = request.url.path if request is not None else ""
    # 认证接口必须走落盘库：注册/登录取的是**全局**用户与令牌表，
    # 写进游客内存库的话，下一次带令牌的请求会在落盘库里找不到这个令牌 → 刚注册就 401。
    if path.startswith("/api/auth"):
        factory = SessionLocal
    else:
        factory = SessionLocal if auth_header.strip() else _ensure_guest_engine()
    db = factory()
    try:
        yield db
    finally:
        db.close()
