"""模块一路由：简历 / JD / 公司 / 匹配度（API_CONTRACT §2.2）。

- 路径不含 `/api` 前缀（由 `main.py` 统一 `include_router(prefix="/api")`）；
- 所有响应都通过 `schemas` 的 Response 模型构造，字段名严格一致；
- PDF 解析失败（扫描件）仍返回 200，只是 `parse_status="failed"` + `warnings` 提示；
- 永不 500：LLM / PDF 失败自动降级（parse_source="rule"）。
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..deps import assert_owner, dump_json, is_guest, load_json, local_iso, new_id, require_login
from ..models import Company, JobDescription, Resume, User
from ..schemas import (
    CompanyBrief,
    CompanyManualRequest,
    CompanyProfile,
    CompanyResponse,
    CompanySearchResponse,
    JDListItem,
    JDListResponse,
    JDParseRequest,
    JDParseResponse,
    JDUpdateRequest,
    MatchRequest,
    MatchResponse,
    ResumeTextRequest,
    ResumeUpdateRequest,
    ResumeUploadResponse,
)
from ..services import (
    company_service,
    field_quality,
    jd_parser,
    matching,
    pdf_parser,
    resume_parser,
    sample_data,
)

router = APIRouter()


# ============================================================
# 工具：行 -> 响应模型
# ============================================================


def _resume_response(row: Resume) -> ResumeUploadResponse:
    parsed = load_json(row.parsed_json, {}) or {}
    try:
        profile = parsed if parsed else {}
        response = ResumeUploadResponse(
            resume_id=row.resume_id,
            file_name=row.file_name or "",
            parse_status=row.parse_status or "pending",
            parse_source=row.parse_source if row.parse_source in ("llm", "rule") else "rule",
            parsed=profile,
            warnings=load_json(row.warnings_json, []) or [],
            summary_line=resume_parser.summary_line(profile),
            # 体验增强 §7：覆盖率与逐字段置信度（放在这里，上传/粘贴/读取/更新四条路径一次覆盖）
            coverage=resume_parser.resume_coverage(profile),
            confidence_levels=_confidence_levels(
                profile, field_quality.RESUME_FIELD_LABELS
            ),
        )
    except Exception as exc:  # noqa: BLE001 脏数据兜底，绝不 500
        response = ResumeUploadResponse(
            resume_id=row.resume_id,
            file_name=row.file_name or "",
            parse_status="failed",
            parse_source="rule",
            parsed={},
            warnings=[f"历史解析结果不完整，已重置：{exc}"],
            summary_line=resume_parser.summary_line({}),
        )
    return response


def _confidence_levels(parsed: dict, labels: dict[str, str]) -> list[dict]:
    """把 `parsed.field_confidence` 转成带 level 的清单（阈值只在后端定义一次）。"""
    conf = (parsed or {}).get("field_confidence") or {}
    if not isinstance(conf, dict) or not conf:
        # LLM 结构化结果不臆造置信度：返回空清单，前端退化为"无标记"
        return []
    return field_quality.low_confidence_fields(conf, labels)


def _jd_response(row: JobDescription) -> JDParseResponse:
    parsed = load_json(row.parsed_json, {}) or {}
    try:
        return JDParseResponse(
            jd_id=row.jd_id,
            parse_status=row.parse_status or "pending",
            parse_source=row.parse_source if row.parse_source in ("llm", "rule") else "rule",
            parsed=parsed,
            warnings=[],
            coverage=jd_parser.jd_coverage(parsed),
            confidence_levels=_confidence_levels(parsed, field_quality.JD_FIELD_LABELS),
        )
    except Exception as exc:  # noqa: BLE001
        return JDParseResponse(
            jd_id=row.jd_id,
            parse_status="failed",
            parse_source="rule",
            parsed={},
            warnings=[f"历史解析结果不完整，已重置：{exc}"],
        )


def _get_resume_or_404(db: Session, resume_id: str, user: User) -> Resume:
    """取简历并**校验归属**：别人的简历一律 404（不泄露存在性）。"""
    return assert_owner(db.get(Resume, resume_id), user, "简历记录")


def _get_jd_or_404(db: Session, jd_id: str, user: User) -> JobDescription:
    """取 JD 并**校验归属**：别人的 JD 一律 404。"""
    return assert_owner(db.get(JobDescription, jd_id), user, "JD 记录")


def _company_response(company_id: str, source: str, profile: dict) -> CompanyResponse:
    return CompanyResponse(
        company_id=company_id,
        source="manual" if source == "manual" else "auto",
        profile=CompanyProfile(**profile),
    )


def _find_company_by_id(company_id: str) -> tuple[str, dict] | None:
    """按 id 在「内置库」中反查公司，返回 (company_id, 原始 dict)。"""
    if not company_id:
        return None
    for raw in company_service.load_companies():
        if company_service.company_id_for(raw.get("name", "")) == company_id:
            return company_id, raw
    return None


def _resolve_company_id(db: Session, name: str) -> str:
    """取公司 id：优先 DB（resolve 会写库），否则用内置库的稳定 id。"""
    row = db.query(Company).filter(Company.name == name).first()
    if row:
        return row.company_id
    return company_service.company_id_for(name)


def _write_upload(content: bytes, stored_name: str, user: User) -> tuple[Path, bool]:
    """把上传的 PDF 写到磁盘，返回 `(路径, 是否临时文件)`。

    **游客不保存任何数据**：改落系统临时文件、解析完立刻删除，磁盘上不留用户文档；
    登录用户仍按原逻辑存到 `uploads/`（`file_url` 指向它）。
    落盘失败不阻断解析（与既有行为一致：解析走内存内容）。
    """
    if is_guest(user):
        try:
            fd, tmp = tempfile.mkstemp(prefix="aic_guest_", suffix=".pdf")
            with os.fdopen(fd, "wb") as fp:
                fp.write(content)
            return Path(tmp), True
        except Exception as exc:  # noqa: BLE001
            print(f"[upload] 游客临时文件创建失败（继续解析）: {exc}")
            return settings.upload_path / stored_name, False

    path = settings.upload_path / stored_name
    try:
        path.write_bytes(content)
    except Exception as exc:  # noqa: BLE001 落盘失败不阻断：仍用内存内容继续解析
        print(f"[upload] PDF 落盘失败（继续解析）：{exc}")
    return path, False


def _drop_upload(path: Path, is_temp: bool) -> None:
    """删除游客的临时上传文件（失败只记日志，不影响响应）。"""
    if not is_temp:
        return
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[upload] 临时文件清理失败（可忽略）: {exc}")


# ============================================================
# 简历
# ============================================================


@router.post("/resume/upload", response_model=ResumeUploadResponse, summary="上传 PDF 简历并解析")
async def upload_resume(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> ResumeUploadResponse:
    """上传 PDF 简历（≤ settings.max_upload_mb）。

    先落库（parse_status=pending）再解析，解析完成后更新记录；
    扫描件返回 200 + parse_status="failed"，绝不抛 500。
    简历归属**只由登录令牌决定**（请求体里多余的 user_id 一律忽略）。
    """
    filename = file.filename or ""
    # 有 size 属性时先快速拒绝超大文件，避免整体读入内存
    size = getattr(file, "size", None)
    if size:
        pdf_parser.validate_pdf(filename, size)
    content = await file.read()
    pdf_parser.validate_pdf(filename, len(content))

    resume_id = new_id()
    stored_name = f"{resume_id}.pdf"
    path, is_temp = _write_upload(content, stored_name, user)

    row = Resume(
        resume_id=resume_id,
        user_id=user.user_id,
        file_name=filename or stored_name,
        file_url="" if is_temp else f"/static/uploads/{stored_name}",
        parse_status="pending",
        parse_source="rule",
        warnings_json="[]",
    )
    db.add(row)
    db.commit()

    # PDF 解析是阻塞 IO，放线程池里跑（文件 ≤5MB）
    try:
        text, warnings = await asyncio.to_thread(pdf_parser.extract_text_from_pdf, str(path))
    finally:
        # 游客的临时文件解析完即删：磁盘上不留用户文档
        _drop_upload(path, is_temp)
    row.raw_text = text or ""

    if not (text or "").strip():
        row.parse_status = "failed"
        row.parse_source = "rule"
        row.parsed_json = dump_json({})
        row.warnings_json = dump_json(warnings or ["PDF 无可提取文本，请改用「粘贴文本」方式上传。"])
    else:
        parsed, source, parse_warnings = await resume_parser.parse_resume(text)
        row.parse_status = "parsed"
        row.parse_source = source
        row.parsed_json = dump_json(parsed)
        row.warnings_json = dump_json((warnings or []) + (parse_warnings or []))
    db.commit()
    db.refresh(row)
    return _resume_response(row)


@router.post("/resume/text", response_model=ResumeUploadResponse, summary="粘贴简历文本并解析")
async def parse_resume_text(
    payload: ResumeTextRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> ResumeUploadResponse:
    """没有 PDF 时直接粘贴简历文本（文档 §10：多解析方案兜底）。

    与上传 PDF 走同一套结构化解析，解析能力完全一致。
    """
    text = (payload.text or "").strip()
    if len(text) < 20:
        raise HTTPException(status_code=400, detail="简历文本太短，请粘贴完整简历内容后再试。")

    row = Resume(
        resume_id=new_id(),
        user_id=user.user_id,
        file_name="粘贴的简历文本",
        file_url="",
        raw_text=text,
        parse_status="pending",
        parse_source="rule",
        warnings_json="[]",
    )
    db.add(row)
    db.commit()

    parsed, source, warnings = await resume_parser.parse_resume(text)
    row.parse_status = "parsed"
    row.parse_source = source
    row.parsed_json = dump_json(parsed)
    row.warnings_json = dump_json(warnings or [])
    db.commit()
    db.refresh(row)
    return _resume_response(row)


@router.get("/resume/{resume_id}", response_model=ResumeUploadResponse, summary="获取简历解析结果")
async def get_resume(
    resume_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> ResumeUploadResponse:
    return _resume_response(_get_resume_or_404(db, resume_id, user))


@router.put("/resume/{resume_id}", response_model=ResumeUploadResponse, summary="手动修正简历解析结果")
async def update_resume(
    resume_id: str,
    payload: ResumeUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> ResumeUploadResponse:
    row = _get_resume_or_404(db, resume_id, user)
    parsed = payload.parsed.model_dump()
    row.parsed_json = dump_json(parsed)
    row.parse_status = "parsed"
    if row.parse_source not in ("llm", "rule"):
        row.parse_source = "rule"
    row.warnings_json = dump_json(["已按用户手动修正的内容保存。"])
    db.commit()
    db.refresh(row)
    return _resume_response(row)


# ============================================================
# JD
# ============================================================


@router.post("/jd/parse", response_model=JDParseResponse, summary="粘贴文本解析 JD")
async def parse_jd_text(
    payload: JDParseRequest, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> JDParseResponse:
    text = (payload.text or "").strip()
    parsed, source, warnings = await jd_parser.parse_jd(text, payload.job_title)
    row = JobDescription(
        jd_id=new_id(),
        user_id=user.user_id,
        source_type="text",
        raw_text=text,
        parsed_json=dump_json(parsed),
        job_title=(parsed.get("basic") or {}).get("title", "") or payload.job_title,
        parse_status="parsed" if text else "failed",
        parse_source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    content = _jd_response(row)
    content.warnings = warnings or []
    return content


@router.post("/jd/upload", response_model=JDParseResponse, summary="上传 PDF 版 JD 并解析")
async def upload_jd(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JDParseResponse:
    filename = file.filename or ""
    size = getattr(file, "size", None)
    if size:
        pdf_parser.validate_pdf(filename, size)
    content = await file.read()
    pdf_parser.validate_pdf(filename, len(content))

    jd_id = new_id()
    stored_name = f"jd_{jd_id}.pdf"
    path, is_temp = _write_upload(content, stored_name, user)

    try:
        text, warnings = await asyncio.to_thread(pdf_parser.extract_text_from_pdf, str(path))
    finally:
        # 游客的临时文件解析完即删
        _drop_upload(path, is_temp)
    row = JobDescription(
        jd_id=jd_id,
        user_id=user.user_id,
        source_type="file",
        raw_text=text or "",
        parsed_json=dump_json({}),
        parse_status="pending",
        parse_source="rule",
    )
    db.add(row)
    db.commit()

    if not (text or "").strip():
        row.parse_status = "failed"
        row.parsed_json = dump_json({})
        db.commit()
        db.refresh(row)
        content_out = _jd_response(row)
        content_out.warnings = warnings or ["PDF 无可提取文本，请改用「粘贴文本」方式上传 JD。"]
        return content_out

    parsed, source, parse_warnings = await jd_parser.parse_jd(text)
    row.parsed_json = dump_json(parsed)
    row.job_title = (parsed.get("basic") or {}).get("title", "")
    row.parse_status = "parsed"
    row.parse_source = source
    db.commit()
    db.refresh(row)
    content_out = _jd_response(row)
    content_out.warnings = (warnings or []) + (parse_warnings or [])
    return content_out


# ============================================================
# 岗位列表（体验增强 §8：同一份简历挂 2-3 个 JD 做对比）
# ⚠️ 必须声明在 `/jd/{jd_id}` **之前**：否则 `GET /jd/list` 会被路径参数路由
#    吃掉（jd_id="list"）并返回 404 —— 与 `/report/history`、`/company/search`
#    是同一类顺序陷阱，所以这里紧贴在上方。
# ============================================================


@router.get("/jd/list", response_model=JDListResponse, summary="我解析过的岗位列表（用于多 JD 对比）")
async def list_jds(
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JDListResponse:
    """只返回**当前用户自己的** JD（别人的一律看不到，符合数据私有铁律）。

    只下发对比所需要的摘要字段（岗位名/部门/学历门槛/技能数/时间），
    不返回职责全文——多 JD 对比用不上，少下发也就少一分泄露面。
    """
    rows = (
        db.query(JobDescription)
        .filter(JobDescription.user_id == (user.user_id or ""))
        .order_by(JobDescription.created_at.desc())
        .limit(limit)
        .all()
    )
    items: list[JDListItem] = []
    for row in rows:
        parsed = load_json(row.parsed_json, {}) or {}
        if not isinstance(parsed, dict):
            parsed = {}
        basic = parsed.get("basic") or {}
        req = parsed.get("requirements") or {}
        items.append(
            JDListItem(
                jd_id=row.jd_id,
                title=str(basic.get("title") or row.job_title or ""),
                department=str(basic.get("department") or ""),
                location=str(basic.get("location") or ""),
                degree=str(req.get("degree") or ""),
                experience=str(req.get("experience") or ""),
                hard_skill_count=len([s for s in (req.get("hard_skills") or []) if s]),
                parse_status=row.parse_status or "pending",
                created_at=local_iso(row.created_at) if row.created_at else "",
            )
        )
    return JDListResponse(items=items, total=len(items))


@router.get("/jd/{jd_id}", response_model=JDParseResponse, summary="获取 JD 解析结果")
async def get_jd(
    jd_id: str, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> JDParseResponse:
    return _jd_response(_get_jd_or_404(db, jd_id, user))


@router.put("/jd/{jd_id}", response_model=JDParseResponse, summary="手动修正 JD 解析结果")
async def update_jd(
    jd_id: str,
    payload: JDUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
) -> JDParseResponse:
    row = _get_jd_or_404(db, jd_id, user)
    parsed = payload.parsed.model_dump()
    row.parsed_json = dump_json(parsed)
    row.job_title = (parsed.get("basic") or {}).get("title", "")
    row.parse_status = "parsed"
    if row.parse_source not in ("llm", "rule"):
        row.parse_source = "rule"
    db.commit()
    db.refresh(row)
    content = _jd_response(row)
    content.warnings = ["已按用户手动修正的内容保存。"]
    return content


# ============================================================
# 公司（注意：/company/search 必须声明在 /company/{company_id} 之前）
# ============================================================


@router.get("/company/search", response_model=CompanySearchResponse, summary="搜索内置公司库")
async def search_company(
    q: str = Query("", description="公司名关键词，可为空"),
    limit: int = Query(20, ge=1, le=50),
) -> CompanySearchResponse:
    items = company_service.search_companies(q, limit)
    briefs = [
        CompanyBrief(
            company_id=company_service.company_id_for(c.get("name", "")),
            name=c.get("name", ""),
            industry=c.get("industry", ""),
            scale=c.get("scale", ""),
            interview_style=c.get("interview_style", ""),
        )
        for c in items
    ]
    matched = company_service.find_company(q) if (q or "").strip() else None
    return CompanySearchResponse(
        items=briefs,
        total=len(briefs),
        auto_matched=matched is not None,
        profile=CompanyProfile(**company_service._to_profile(matched)) if matched else None,
    )


@router.get("/company/{company_id}", response_model=CompanyResponse, summary="获取公司画像")
async def get_company(company_id: str, db: Session = Depends(get_db)) -> CompanyResponse:
    # 1) DB 记录（manual 或已缓存）
    row = db.get(Company, company_id)
    if row:
        profile = load_json(row.profile_json, {}) or {}
        if profile.get("name"):
            profile.setdefault("note", "信息仅供参考")
            profile.setdefault("interview_style", "结构化面")
            return _company_response(row.company_id, row.source or "auto", profile)
    # 2) 内置库按 id 反查
    hit = _find_company_by_id(company_id)
    if hit:
        cid, raw = hit
        return _company_response(cid, "auto", company_service._to_profile(raw))
    raise HTTPException(status_code=404, detail=f"未找到公司画像：{company_id}")


@router.post("/company/manual", response_model=CompanyResponse, summary="手动录入公司画像")
async def manual_company(payload: CompanyManualRequest, db: Session = Depends(get_db)) -> CompanyResponse:
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="公司名称不能为空。")
    profile = CompanyProfile(
        name=name,
        industry=payload.industry,
        scale=payload.scale,
        business=payload.business,
        culture_tags=payload.culture_tags,
        interview_style=payload.interview_style or "结构化面",
        style_desc=payload.style_desc,
        common_questions=[],
        headquarters="",
        note="用户手动录入，信息以用户填写为准",
    ).model_dump()

    row = db.query(Company).filter(Company.name == name).first()
    if row:
        row.profile_json = dump_json(profile)
        row.source = "manual"
    else:
        row = Company(name=name, profile_json=dump_json(profile), source="manual")
        db.add(row)
    db.commit()
    db.refresh(row)
    return _company_response(row.company_id, "manual", profile)


# ============================================================
# 匹配度
# ============================================================


@router.post("/match", response_model=MatchResponse, summary="计算简历与 JD 的匹配度")
async def match(
    payload: MatchRequest, db: Session = Depends(get_db), user: User = Depends(require_login)
) -> MatchResponse:
    resume_row = _get_resume_or_404(db, payload.resume_id, user)
    jd_row = _get_jd_or_404(db, payload.jd_id, user)
    resume = load_json(resume_row.parsed_json, {}) or {}
    jd = load_json(jd_row.parsed_json, {}) or {}

    company: dict | None = None
    if payload.company_id:
        company_row = db.get(Company, payload.company_id)
        if company_row:
            company = load_json(company_row.profile_json, {}) or None
        if company is None:
            hit = _find_company_by_id(payload.company_id)
            if hit:
                company = company_service._to_profile(hit[1])

    # 与 interview 路由保持一致：匹配失败也必须降级返回，不能把 500 抛给前端（契约 §3）
    try:
        data = matching.compute_match(resume, jd, company)
        return MatchResponse(**data)
    except Exception as exc:  # noqa: BLE001
        print(f"[profile] compute_match 失败，返回最小匹配结果: {exc}")
        return MatchResponse(
            overall=0,
            level="匹配度计算失败，请检查简历与 JD 内容后重试",
            matched=[],
            weak=[],
            strategy={},
            breakdown=[],
            radar_target={},
        )


# ============================================================
# 一键演示数据
# ============================================================


async def _seed_demo(db: Session, user: User) -> dict:
    """载入示例简历 + JD + 公司并计算匹配度（API_CONTRACT §2.2）。

    示例数据归属**当前登录用户**（未登录时归公共游客账号）：
    这样每个人点「一键体验」后拿到的 resume_id/jd_id 都只能被自己读到，
    既不会互相污染，也不会把别人的示例数据串进自己的历史与排行榜。
    """
    # 1) 简历
    resume_parsed, resume_source, resume_warnings = await resume_parser.parse_resume(
        sample_data.DEMO_RESUME_TEXT
    )
    resume_row = Resume(
        resume_id=new_id(),
        user_id=user.user_id,
        file_name="示例简历-林宇航.pdf",
        file_url="",
        raw_text=sample_data.DEMO_RESUME_TEXT,
        parsed_json=dump_json(resume_parsed),
        parse_status="parsed",
        parse_source=resume_source,
        warnings_json=dump_json(resume_warnings),
    )
    db.add(resume_row)

    # 2) JD
    jd_parsed, jd_source, _jd_warnings = await jd_parser.parse_jd(sample_data.DEMO_JD_TEXT)
    jd_row = JobDescription(
        jd_id=new_id(),
        user_id=user.user_id,
        source_type="text",
        raw_text=sample_data.DEMO_JD_TEXT,
        parsed_json=dump_json(jd_parsed),
        job_title=(jd_parsed.get("basic") or {}).get("title", ""),
        parse_status="parsed",
        parse_source=jd_source,
    )
    db.add(jd_row)
    db.commit()
    db.refresh(resume_row)
    db.refresh(jd_row)

    # 3) 公司（内置库命中 + 写回 DB）
    company_profile, company_source = await company_service.resolve_company_profile(
        db, sample_data.DEMO_COMPANY_NAME
    )
    company_id = _resolve_company_id(db, company_profile.get("name") or sample_data.DEMO_COMPANY_NAME)

    # 4) 匹配度
    match_data = matching.compute_match(resume_parsed, jd_parsed, company_profile)

    return {
        "user_id": user.user_id,
        "resume_id": resume_row.resume_id,
        "jd_id": jd_row.jd_id,
        "company_id": company_id,
        "resume": _resume_response(resume_row).model_dump(),
        "jd": _jd_response(jd_row).model_dump(),
        "company": _company_response(company_id, company_source, company_profile).model_dump(),
        "match": match_data,
    }


@router.post("/demo/seed", summary="一键载入示例简历+JD+公司")
async def demo_seed(
    db: Session = Depends(get_db), user: User = Depends(require_login)
) -> dict:
    return await _seed_demo(db, user)


@router.get("/demo/seed", summary="一键载入示例简历+JD+公司（GET 便捷入口）")
async def demo_seed_get(
    db: Session = Depends(get_db), user: User = Depends(require_login)
) -> dict:
    return await _seed_demo(db, user)
