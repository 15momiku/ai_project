"""简历 PDF 排版（Jinja2 模板 + wkhtmltopdf）。

职责：把结构化简历数据填进 Jinja2 模板（backend/app/templates/resume/），
再用 wkhtmltopdf（pdfkit）转成 PDF，并自动挑选「能塞进一整页的最大字号」。

三条关键约束：
- 模板里的尺寸全部相对 body 的 em，因此只调节基准字号即可整篇缩放；
- wkhtmltopdf 需系统安装（或设 WKHTMLTOPDF_PATH），找不到时抛带安装说明的异常；
- 细线宽度一律写 px，避免字号压到最小时被 Qt 取整丢掉（见模板内注释）。

与主应用的关系：本模块是**纯同步阻塞**的（pdfkit 调外部进程），
调用方（routers/resume_optimizer.py）必须用 asyncio.to_thread 包裹，避免阻塞事件循环。
"""

from __future__ import annotations

import io
import logging
import os
import shutil
from pathlib import Path
from typing import NamedTuple

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

from ..config import BACKEND_DIR

logger = logging.getLogger(__name__)

TEMPLATE_DIR = BACKEND_DIR / "app" / "templates" / "resume"

# 允许渲染的模板白名单（接口会校验，避免任意文件名）
AVAILABLE_TEMPLATES = ("template1.html", "template2.html")

# 前端展示用的模板元信息（id 与 AVAILABLE_TEMPLATES 严格一致）
TEMPLATE_CATALOG = (
    {
        "id": "template1.html",
        "name": "简约商务",
        "desc": "深蓝 + 浅金双色分割条，3×3 基本信息表，理工科技术岗取向",
    },
    {
        "id": "template2.html",
        "name": "简约现代",
        "desc": "顶部横向信息栏，留白更宽松，通用岗位取向",
    },
)

# ========== 模板文案（中/英文简历） ==========
# 模板里不能写死中文标题，否则英文简历里会混进「教育背景」「背景：」这类中文。
TEMPLATE_LABELS = {
    "zh": {
        "doc_title": "简历",
        "html_lang": "zh-CN",
        "resume_title": "个人简历",
        "resume_title_en": "Personal resume",
        "basic_info": "基本信息",
        "education": "教育背景",
        "work": "工作经历",
        "project": "项目经历",
        "campus": "校园经历",
        "honors": "荣誉奖项",
        "skills": "技能特长",
        "label_sep": "：",
        "name": "姓名",
        "job_intention": "求职意向",
        "age": "年龄",
        "gender": "性别",
        "ethnicity": "民族",
        "political_status": "政治面貌",
        "phone": "电话",
        "email": "邮箱",
        "wechat": "微信",
    },
    "en": {
        "doc_title": "Resume",
        "html_lang": "en",
        "resume_title": "Resume",
        "resume_title_en": "Curriculum Vitae",
        "basic_info": "Basic Information",
        "education": "Education",
        "work": "Work Experience",
        "project": "Projects",
        "campus": "Campus Experience",
        "honors": "Honors & Awards",
        "skills": "Skills",
        "label_sep": ": ",
        "name": "Name",
        "job_intention": "Job Objective",
        "age": "Age",
        "gender": "Gender",
        "ethnicity": "Ethnicity",
        "political_status": "Political Status",
        "phone": "Phone",
        "email": "Email",
        "wechat": "WeChat",
    },
}

# 模板2 顶部横向排列的基础信息（顺序即展示顺序）
META_FIELDS = ("age", "gender", "ethnicity", "political_status", "phone", "email", "wechat")

# 模板1 基本信息 3×3 表格的字段与顺序（按行填充）
BASIC_INFO_FIELDS = (
    "name", "job_intention", "age",
    "gender", "ethnicity", "political_status",
    "phone", "email", "wechat",
)

# ========== 自动适配一整页的字号范围 ==========
DESIGN_BASE_FONT = 20.0  # 设计字号（不缩放时的基准字号）
MIN_BASE_FONT = 10.5  # 下限，再小就影响可读性了，宁可溢出到第二页
FONT_TOLERANCE = 0.5  # 二分搜索精度（px）

# 页面设置：留出页边距，正文不再用 body padding
# 注意：不要加 disable-smart-shrinking —— 那会让 CSS px 按 72dpi 映射，正文凭空放大 1/3。
PDF_OPTIONS = {
    "encoding": "UTF-8",
    "quiet": None,
    "enable-local-file-access": None,
    "page-size": "A4",
    "margin-top": "12mm",
    "margin-bottom": "12mm",
    "margin-left": "14mm",
    "margin-right": "14mm",
}

env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)

# wkhtmltopdf 查找顺序：环境变量 → 常见安装路径 → PATH
_CANDIDATE_PATHS = (
    os.getenv("WKHTMLTOPDF_PATH", ""),
    r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
    r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
    "/usr/local/bin/wkhtmltopdf",
    "/usr/bin/wkhtmltopdf",
    "/opt/homebrew/bin/wkhtmltopdf",
)

_config = None


def normalize_language(language: str | None) -> str:
    """把任意输入收敛到受支持的 'zh' / 'en'。"""
    return "en" if str(language or "").strip().lower().startswith("en") else "zh"


def resolve_labels(template_name: str, language: str) -> dict:
    """按语言取文案。两个模板共用一套叫法，保留 template_name 参数便于将来单模板改词。"""
    return dict(TEMPLATE_LABELS[normalize_language(language)])


def build_meta_items(resume_data: dict, labels: dict) -> list[str]:
    """模板2：把基础信息拼成一行可渲染的文本，空值自动跳过。"""
    items = []
    for key in META_FIELDS:
        value = str(resume_data.get(key) or "").strip()
        if value:
            items.append(f"{labels[key]}{labels['label_sep']}{value}")
    return items


def build_basic_info(resume_data: dict, labels: dict) -> list[dict[str, str]]:
    """模板1：基本信息 3×3 表格的单元格（按 BASIC_INFO_FIELDS 顺序，空值跳过）。"""
    cells = []
    for key in BASIC_INFO_FIELDS:
        value = str(resume_data.get(key) or "").strip()
        if value:
            cells.append({"key": key, "label": labels[key], "value": value})
    return cells


def find_wkhtmltopdf() -> str:
    """定位 wkhtmltopdf 可执行文件，找不到时抛出带说明的异常。"""
    for candidate in _CANDIDATE_PATHS:
        if candidate and Path(candidate).is_file():
            return candidate
    found = shutil.which("wkhtmltopdf")
    if found:
        return found
    raise RuntimeError(
        "未找到 wkhtmltopdf，请先安装（https://wkhtmltopdf.org/downloads.html），"
        "或设置环境变量 WKHTMLTOPDF_PATH 指向其可执行文件路径"
    )


def pdf_available() -> bool:
    """PDF 导出是否可用：wkhtmltopdf 可执行 + pdfkit 已安装。"""
    try:
        import pdfkit  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    try:
        find_wkhtmltopdf()
    except RuntimeError:
        return False
    return True


def get_pdfkit_config():
    """惰性构建 pdfkit 配置，避免 import 阶段就因缺少 wkhtmltopdf 而失败。"""
    global _config
    if _config is None:
        import pdfkit

        _config = pdfkit.configuration(wkhtmltopdf=find_wkhtmltopdf())
    return _config


def _validate_template(template_name: str) -> None:
    if template_name not in AVAILABLE_TEMPLATES:
        raise ValueError(
            f"未知模板: {template_name}，可选: {', '.join(AVAILABLE_TEMPLATES)}"
        )


def render_resume_to_html(
    resume_data: dict,
    template_name: str = "template1.html",
    base_font: float = DESIGN_BASE_FONT,
    language: str = "zh",
) -> str:
    """把简历数据填充进模板。base_font 决定全篇字号，其余尺寸都是它的 em 倍数。"""
    _validate_template(template_name)
    try:
        template = env.get_template(template_name)
    except TemplateNotFound as exc:
        raise ValueError(f"模板文件缺失: {template_name}") from exc
    labels = resolve_labels(template_name, language)
    return template.render(
        base_font=round(float(base_font), 2),
        labels=labels,
        meta_items=build_meta_items(resume_data, labels),
        basic_info=build_basic_info(resume_data, labels),
        **resume_data,
    )


def generate_pdf_from_html(html_content: str) -> bytes:
    """HTML 转 PDF，直接返回字节数据（不落盘）。"""
    import pdfkit

    return pdfkit.from_string(
        html_content, False, configuration=get_pdfkit_config(), options=PDF_OPTIONS
    )


def count_pdf_pages(pdf_bytes: bytes) -> int:
    """统计 PDF 页数。"""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return len(pdf.pages)


class PdfFitResult(NamedTuple):
    """自适应排版结果。"""

    pdf: bytes
    base_font: float
    pages: int


def render_resume_to_pdf_fitted(
    resume_data: dict,
    template_name: str = "template1.html",
    max_pages: int = 1,
    language: str = "zh",
) -> PdfFitResult:
    """渲染简历 PDF，并自动挑选「能塞进 max_pages 页的最大字号」。

    先按设计字号试一次（多数简历一次命中，没有额外开销）；
    若溢出，则在 [MIN_BASE_FONT, DESIGN_BASE_FONT] 上二分搜索最大可行字号。
    字号越小排版越紧，但换行也会更少，所以压到下限仍放不下时按最小字号输出。
    """
    _validate_template(template_name)
    language = normalize_language(language)

    cache: dict = {}

    def build(base_font: float) -> bytes:
        key = round(float(base_font), 2)
        if key not in cache:
            html = render_resume_to_html(resume_data, template_name, key, language)
            cache[key] = generate_pdf_from_html(html)
        return cache[key]

    pdf_bytes = build(DESIGN_BASE_FONT)
    pages = count_pdf_pages(pdf_bytes)
    if pages <= max_pages:
        logger.info("内容放得下设计字号 %.1fpx，无需缩放", DESIGN_BASE_FONT)
        return PdfFitResult(pdf_bytes, DESIGN_BASE_FONT, pages)

    lo, hi = MIN_BASE_FONT, DESIGN_BASE_FONT  # 已知 hi 放不下
    best: bytes | None = None
    best_font = None
    while hi - lo > FONT_TOLERANCE:
        mid = round((lo + hi) / 2, 2)
        candidate = build(mid)
        if count_pdf_pages(candidate) <= max_pages:
            best, best_font = candidate, mid
            lo = mid
        else:
            hi = mid

    if best is None:
        best = build(MIN_BASE_FONT)
        best_font = MIN_BASE_FONT
        pages = count_pdf_pages(best)
        if pages > max_pages:
            logger.warning(
                "内容过多：基准字号压到 %.1fpx 仍有 %d 页，建议精简经历条目",
                MIN_BASE_FONT, pages,
            )
    else:
        pages = count_pdf_pages(best)
        logger.info("已自适应：基准字号 %.2fpx 时内容正好放下 %d 页", best_font, pages)

    return PdfFitResult(best, best_font, pages)
