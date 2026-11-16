"""简历优化路由（润色 / ATS 检测 / PDF 解析 / PDF 导出；沿用本项目的鉴权与 LLM 约定）。
路径前缀由 main.py 统一加 `/api`，因此这里的完整路径是：
    POST /api/resume-optimizer/polish        简历润色（中/英文，STAR 三段式）
    POST /api/resume-optimizer/ats           ATS 兼容性检测（总分 + 五模块诊断）
    POST /api/resume-optimizer/upload-pdf    上传旧简历 PDF，自动解析并结构化
    POST /api/resume-optimizer/render-pdf    按模板导出 PDF
    GET  /api/resume-optimizer/meta          模板清单 / PDF 可用性 / 大模型状态

权限口径：登录即可用，**没有任何次数限制**，接口不会拒绝请求。

设计要点：
- 全部是**无状态**计算接口：不落库、不产生用户数据，因此「刷新即重来」；
- 耗时的同步阻塞调用（pdfplumber / wkhtmltopdf）一律 asyncio.to_thread，不阻塞事件循环；
- 大模型不可用时返回 503 + 明确指引（见 services/resume_optimizer._NO_LLM_HINT），
  而不是静默返回假数据 —— 简历润色结果会被用户直接拿去投递，编不得。
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..deps import require_login
from ..models import User
from ..services import pdf_parser, resume_doc, resume_optimizer
from ..services.llm import llm_status

logger = logging.getLogger(__name__)

router = APIRouter()

# 最长边界的简历文本：留出足够空间给「结构化整份简历」这类长输出
_PARSE_TIMEOUT_HINT = "PDF 解析超时，请换一份更小的 PDF 或改用粘贴文本"


@router.get("/resume-optimizer/meta", tags=["resume"])
async def resume_optimizer_meta() -> dict:
    """前端首屏需要的元信息：可用模板、PDF 导出是否可用、大模型状态、空表单骨架。"""
    llm = llm_status()
    return {
        "templates": list(resume_doc.TEMPLATE_CATALOG),
        "pdf_available": resume_doc.pdf_available(),
        "llm_mode": llm["mode"],
        "llm_provider": llm["provider"],
        "empty_resume": resume_optimizer.empty_resume(),
        "max_upload_mb": pdf_parser.MAX_UPLOAD_MB,
    }


@router.post("/resume-optimizer/polish", tags=["resume"])
async def polish(
    payload: resume_optimizer.PolishRequest,
    user: User = Depends(require_login),
) -> dict:
    """口语化简历 -> 专业 STAR 三段式（支持中/英文）。"""
    return await resume_optimizer.polish_resume(payload)


@router.post("/resume-optimizer/ats", tags=["resume"])
async def ats(
    payload: resume_optimizer.ATSRequest,
    user: User = Depends(require_login),
) -> dict:
    """ATS 兼容性检测：总分 + 五个模块的问题/建议/亮点。"""
    return await resume_optimizer.check_ats(payload)


@router.post("/resume-optimizer/keyword-coverage", tags=["resume"])
async def keyword_coverage_route(
    payload: resume_optimizer.KeywordCoverageRequest,
    user: User = Depends(require_login),
) -> dict:
    """JD 关键词覆盖率（体验增强 §11）。

    **不需要大模型**：关键词是否出现是确定性事实，规则算出来的结果更可解释、更快，
    因此零密钥环境下这块看板照常可用。
    """
    return resume_optimizer.keyword_coverage(payload.resume, payload.jd)


@router.post("/resume-optimizer/upload-pdf", tags=["resume"])
async def upload_pdf(
    file: UploadFile = File(...),
    user: User = Depends(require_login),
) -> dict:
    """上传旧简历 PDF，抽取文本并结构化回填表单。

    PDF 抽取是同步阻塞 IO（pdfplumber），放到线程池里跑；
    抽取失败（扫描件/加密/损坏）返回 400 + 具体原因，前端引导用户改用粘贴文本。
    """
    raw = await file.read()
    pdf_parser.validate_pdf(file.filename or "", len(raw))

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(raw)
            tmp_path = tmp.name

        try:
            text, warnings = await asyncio.wait_for(
                asyncio.to_thread(pdf_parser.extract_text_from_pdf, tmp_path),
                timeout=60,
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail=_PARSE_TIMEOUT_HINT) from None

        if not text.strip():
            raise HTTPException(
                status_code=400,
                detail="；".join(warnings) or "PDF 中未提取到文本内容，请改用粘贴文本方式录入简历。",
            )

        data = await resume_optimizer.structure_resume_text(text)
        return {"resume": data, "warnings": warnings}
    finally:
        # 无论成功失败都清理临时文件：用户简历原件不能留在磁盘上
        if tmp_path and Path(tmp_path).exists():
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass


@router.post("/resume-optimizer/render-pdf", tags=["resume"])
async def render_pdf(
    payload: resume_optimizer.RenderPDFRequest,
    user: User = Depends(require_login),
) -> Response:
    """按模板导出 PDF（自动适配一整页字号）。

    wkhtmltopdf 是外部进程调用（同步阻塞），必须丢到线程池，
    否则一次导出会把整个事件循环卡住数秒。
    """
    if payload.template_name not in resume_doc.AVAILABLE_TEMPLATES:
        raise HTTPException(
            status_code=400,
            detail=f"未知模板: {payload.template_name}，可选: {', '.join(resume_doc.AVAILABLE_TEMPLATES)}",
        )
    if not resume_doc.pdf_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "PDF 导出不可用：未检测到 wkhtmltopdf。"
                "请安装后重启后端（https://wkhtmltopdf.org/downloads.html），"
                "或设置环境变量 WKHTMLTOPDF_PATH 指向可执行文件。"
            ),
        )

    data_dict = resume_optimizer.normalize_descriptions(payload.resume.model_dump())

    def _render() -> resume_doc.PdfFitResult:
        return resume_doc.render_resume_to_pdf_fitted(
            data_dict, payload.template_name, language=payload.language
        )

    try:
        fit = await asyncio.to_thread(_render)
    except Exception as exc:  # noqa: BLE001 PDF 引擎异常统一翻译成 500
        logger.exception("简历 PDF 生成失败")
        raise HTTPException(status_code=500, detail=f"PDF 生成失败：{exc}") from exc

    return Response(
        content=fit.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=resume_{payload.template_name}",
            # 供前端判断是否需要提示用户精简内容
            "X-Resume-Pages": str(fit.pages),
            "X-Resume-Base-Font": f"{fit.base_font:g}",
            "Access-Control-Expose-Headers": "X-Resume-Pages, X-Resume-Base-Font",
        },
    )
