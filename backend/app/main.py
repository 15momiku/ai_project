"""校招全流程助手 · 后端入口（FastAPI）。

启动：
    cd backend
    uvicorn app.main:app --reload --port 8000
接口文档：http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import BACKEND_DIR, settings
from .constants import (
    DIMENSIONS,
    GRADES,
    INTERVIEWER_OPTIONS,
    INTERVIEW_SECTIONS,
)
from .db import SessionLocal, init_db
from .schemas import (
    DimensionMeta,
    HealthResponse,
    MetaOptionsResponse,
)
from .services.company_service import hot_companies
from .services.llm import llm_diagnosis, llm_status
from .services.media import asr_status, tts_diagnosis, tts_status
from .services import auth as auth_service
from .services import resume_doc, session_housekeeping

# 路由模块
from .routers import auth as auth_router
from .routers import interview as interview_router
from .routers import jobs as jobs_router
from .routers import media as media_router
from .routers import practice as practice_router
from .routers import profile as profile_router
from .routers import report as report_router
from .routers import resume_optimizer as resume_optimizer_router


def _sweep_stale_sessions() -> None:
    """启动自愈：把上次进程遗留下来的 ongoing 僵尸会话收敛为 aborted（契约 §1.7）。

    用户中途关掉页面就不会再调用 `/finish`，这些会话会永久挂在 ongoing；
    每次启动清一次，配合 `GET /api/interview/{id}` 的请求内收敛，保证状态最终一致。
    """
    hours = session_housekeeping.idle_timeout_hours()
    if hours <= 0:
        print("[boot] 会话自动收敛已关闭（SESSION_IDLE_TIMEOUT_HOURS<=0）")
        return
    try:
        with SessionLocal() as db:
            closed = session_housekeeping.close_stale_sessions(db)
    except Exception as exc:  # noqa: BLE001 自愈失败不能阻断启动
        print(f"[boot] 超时会话收敛跳过: {exc}")
        return
    if closed:
        print(f"[boot] 已收敛 {len(closed)} 场空闲超过 {hours:g} 小时的面试 -> aborted")
    else:
        print(f"[boot] 无超时会话（空闲阈值 {hours:g} 小时）")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print(f"[boot] {settings.app_name} v{settings.app_version} 启动完成")
    _sweep_stale_sessions()
    # 清理过期登录令牌，避免 auth_token 表只增不减
    try:
        with SessionLocal() as db:
            purged = auth_service.purge_expired_tokens(db)
        if purged:
            print(f"[boot] 已清理 {purged} 个过期登录令牌")
    except Exception as exc:  # noqa: BLE001 清理失败不阻断启动
        print(f"[boot] 过期令牌清理跳过: {exc}")
    # 只保留「用户注册出来的账号」：老库里可能残留 username 为空的公共 guest 行，
    # 那是早期版本为未登录请求即席创建的（现在游客走内存库，已不再产生）。
    try:
        with SessionLocal() as db:
            stale = auth_service.purge_non_registered_users(db)
        if stale:
            print(f"[boot] 已清理 {stale} 个非注册账号（含其全部数据）")
    except Exception as exc:  # noqa: BLE001
        print(f"[boot] 非注册账号清理跳过: {exc}")
    if not settings.allow_guest:
        print("[boot] 游客模式已关闭（ALLOW_GUEST=false）：未登录请求将返回 401，数据完全私有")
    else:
        # 说清游客数据的去向，排查"游客的记录去哪了"时不用翻代码
        print("[boot] 游客模式开启：未登录请求走进程内内存库（不落盘、重启即消失），"
              "「我的面试 / 简历优化」对游客隐藏")
    print(f"[boot] LLM: {llm_status()}  TTS: {tts_status()}  ASR: {asr_status()}")
    # 简历优化的 PDF 导出依赖系统安装 wkhtmltopdf；缺了就明确说清楚，
    # 否则用户点「下载 PDF」只会得到一个 503，排查不到原因。
    print(
        "[boot] 简历优化 PDF 导出："
        + ("可用" if resume_doc.pdf_available() else "不可用（未检测到 wkhtmltopdf，请安装或设 WKHTMLTOPDF_PATH）")
    )
    # 配了 aliyun 但环境不满足（缺 SDK / 缺凭据）时，把原因和实际生效的提供方打在日志里，
    # 否则表现是「面试官没有声音」，而日志一片安静、前端只提示一句语音加载失败
    voice_hint = tts_diagnosis()
    if voice_hint:
        print(f"[boot] {voice_hint}")
    # 密钥没生效时把原因直接打在启动日志里，不用去翻文档排查
    diag = llm_diagnosis()
    if diag["mode"] != "live":
        print(f"[boot] LLM 未启用：{diag['hint']}")
    else:
        print(f"[boot] LLM 已启用：{diag['provider']} / {diag['model']}")
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "面向大学生的 AI 求职全流程助手：找岗投递（共享岗位库 + 投递统计）· 改简历（STAR 润色 / ATS 检测 / "
        "PDF 导出）· 练面试（简历×JD×公司三维匹配 + 数字人语音面试）· 复盘改进（六维结构化评分报告 / 错题本）"
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request, exc):  # noqa: ANN001
    """兜底：任何未捕获异常都返回结构化错误，前端不会白屏。"""
    print(f"[error] {request.method} {request.url.path} -> {exc}")
    return JSONResponse(
        status_code=500,
        content={"ok": False, "message": f"服务异常，已降级处理：{exc}", "detail": str(exc)},
    )


# ---------------- 元信息 ----------------
@app.get("/api/health", response_model=HealthResponse, tags=["meta"])
async def health() -> HealthResponse:
    llm = llm_status()
    return HealthResponse(
        app=settings.app_name,
        version=settings.app_version,
        llm_mode=llm["mode"],
        llm_provider=llm["provider"],
        tts_provider=tts_status(),
        asr_provider=asr_status(),
    )


@app.get("/api/health/llm", tags=["meta"])
async def health_llm() -> dict:
    """大模型配置自检：说明当前用的是哪个厂商，没生效时给出原因。

    不返回任何密钥内容，只报告「是否已配置」。
    """
    return llm_diagnosis()


@app.get("/api/meta/options", response_model=MetaOptionsResponse, tags=["meta"])
async def meta_options() -> MetaOptionsResponse:
    llm = llm_status()
    return MetaOptionsResponse(
        version=settings.app_version,
        llm_mode=llm["mode"],
        llm_provider=llm["provider"],
        tts_provider=tts_status(),
        asr_provider=asr_status(),
        dimensions=[DimensionMeta(**d) for d in DIMENSIONS],
        grades=GRADES,
        interview_sections=INTERVIEW_SECTIONS,
        hot_companies=hot_companies(),
        interviewer_options=INTERVIEWER_OPTIONS,
    )


# ---------------- 业务路由 ----------------
app.include_router(auth_router.router, prefix="/api", tags=["auth"])
app.include_router(profile_router.router, prefix="/api", tags=["profile"])
app.include_router(interview_router.router, prefix="/api", tags=["interview"])
app.include_router(report_router.router, prefix="/api", tags=["report"])
app.include_router(media_router.router, prefix="/api", tags=["media"])
app.include_router(practice_router.router, prefix="/api", tags=["practice"])
# 简历优化（润色 / ATS 检测 / PDF 解析 / PDF 导出）
app.include_router(resume_optimizer_router.router, prefix="/api", tags=["resume"])
# 岗位信息汇总（共享岗位库 + 各人独立的投递进度 + 投递统计；
# 岗位库由登录用户共同维护）
app.include_router(jobs_router.router, prefix="/api", tags=["jobs"])

# TTS 音频缓存等静态资源：/static/tts_cache/xxx.mp3
# 只暴露 tts_cache 目录：data_root 下还有 uploads/（用户简历原件）与 app.db（全量业务数据），
# 直接挂载整个 data 目录会让 /static/app.db、/static/uploads/*.pdf 可被任意下载。
settings.tts_cache_path.mkdir(parents=True, exist_ok=True)
app.mount("/static/tts_cache", StaticFiles(directory=str(settings.tts_cache_path)), name="tts_static")


# ---------------- 单进程部署模式 ----------------
# 若前端已构建（frontend/dist 存在），由后端直接托管 SPA，一条命令即可对外提供完整服务。
# 注意：catch-all 必须最后注册，且必须比根路由更早生效——否则 `GET /` 会被 root() 抢走。
_DIST_DIR = (BACKEND_DIR.parent / "frontend" / "dist").resolve()
_HAS_DIST = (_DIST_DIR / "index.html").is_file()


if not _HAS_DIST:

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "app": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "frontend": "cd frontend && npm run dev  ->  http://127.0.0.1:5173",
        }


if _HAS_DIST:

    # SPA 入口必须显式禁用缓存。
    # dist/index.html 只带 ETag/Last-Modified、没有 Cache-Control 时，浏览器会套用
    # 「启发式新鲜度 ≈ 10% ×（当前时间 − Last-Modified）」，把首页缓存数小时到数天。
    # 这个地址上以前跑过另一个独立应用，
    # 于是换了应用之后，用户按原地址打开看到的会是缓存里那个早已停掉的旧页面
    # （旧页面是单文件、样式脚本全内联，离线也能完整渲染，所以症状特别像「服务没换」）。
    _INDEX_HEADERS = {"Cache-Control": "no-cache, must-revalidate"}
    # Vite 产物文件名带内容哈希（index-B28e51i2.css），可以放心长期强缓存
    _HASHED_ASSET_RE = re.compile(r"-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2?|ttf|png|jpe?g|svg|webp)$")

    def _resolve_static(full_path: str):
        """把 URL 路径安全地映射到 dist 内的文件；越界（路径穿越）返回 None。"""
        if not full_path:
            return None
        try:
            candidate = (_DIST_DIR / full_path).resolve()
        except (OSError, ValueError, RuntimeError):
            return None
        if candidate == _DIST_DIR or _DIST_DIR not in candidate.parents:
            return None
        return candidate if candidate.is_file() else None

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        # 接口与静态资源命名空间下不存在的东西必须 404，
        # 不能被 SPA 首页吃掉（否则前端拿到 HTML 200 静默失败，也掩盖了真实 404）。
        if full_path == "api" or full_path.startswith(("api/", "static/")):
            return JSONResponse(
                status_code=404,
                content={"ok": False, "message": f"资源不存在：/{full_path}"},
            )
        asset = _resolve_static(full_path)
        if asset is not None:
            if _HASHED_ASSET_RE.search(asset.name):
                return FileResponse(
                    asset, headers={"Cache-Control": "public, max-age=31536000, immutable"}
                )
            return FileResponse(asset, headers=_INDEX_HEADERS)
        return FileResponse(_DIST_DIR / "index.html", headers=_INDEX_HEADERS)

    # 这行说的是「后端自己也在托管前端」，不是对外访问地址：对外一律经 Caddy 的域名 + HTTPS。
    # 端口曾经写死成 8000（本地默认值），于是用 --port 8010 跑起来时日志会报出一个错的地址 ——
    # 排查时最怕被日志指错方向，所以这里只说清事实，不报端口。
    print("[boot] 已挂载前端产物：单进程同域提供（对外走 Caddy 的 HTTPS；容器内 8000）")
    print(f"       产物目录：{_DIST_DIR}")
