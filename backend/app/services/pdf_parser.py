"""PDF 文本提取（对应文档 §3.4.1 简历解析链路：PDF -> 纯文本）。

设计要点：
- 首选 pdfplumber 逐页提取文本，多页用换行拼接；
- 任何异常（缺依赖 / 文件损坏 / 加密）都捕获后返回 ("", [错误说明])，绝不向上抛；
- 页面无文本时给出「疑似扫描件」warning，由上层把 parse_status 置为 failed（API_CONTRACT §2.2 校验要求）；
- PDF 解析是同步阻塞 IO，调用方（routers/profile.py）用 asyncio.to_thread 包裹，避免阻塞事件循环。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from ..config import settings

# 上传大小上限（MB），供上层提示文案与校验共用
MAX_UPLOAD_MB: int = settings.max_upload_mb

# 单页可提取文本低于该长度时视为「疑似扫描件」页
_MIN_PAGE_CHARS = 8


def validate_pdf(filename: str, size: int) -> None:
    """校验上传文件是否为合格 PDF（API_CONTRACT §2.2 校验要求）。

    - 非 .pdf 后缀 -> 400
    - 超过 settings.max_upload_mb -> 400（message 说明上限）

    非法时 raise fastapi.HTTPException(400)，合法时静默返回。
    """
    name = (filename or "").strip()
    if not name.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail=f"仅支持 PDF 文件，当前文件「{name or '未命名'}」不是 .pdf 格式。",
        )
    if size is None or size <= 0:
        raise HTTPException(status_code=400, detail="文件为空，请重新上传。")
    limit_bytes = MAX_UPLOAD_MB * 1024 * 1024
    if size > limit_bytes:
        raise HTTPException(
            status_code=400,
            detail=f"文件大小 {size / 1024 / 1024:.1f}MB 超过上限 {MAX_UPLOAD_MB}MB，请压缩后重试。",
        )


def extract_text_from_pdf(path: str | Path) -> tuple[str, list[str]]:
    """提取 PDF 全文（对应文档 §3.4.1 第一步）。

    返回 (text, warnings)：
    - text：所有页面文本用 \"\\n\" 拼接后的结果；
    - warnings：扫描件提示 / 单页失败提示 / 整体异常说明。

    约定：本函数绝不抛异常，失败时返回 ("", [错误说明])。
    """
    warnings: list[str] = []
    file_path = Path(path)

    if not file_path.exists():
        return "", [f"PDF 文件不存在：{file_path.name}"]

    try:
        import pdfplumber  # 延迟导入：缺依赖时进入降级分支而不是启动即崩
    except Exception as exc:  # noqa: BLE001
        return "", [f"PDF 解析组件不可用（pdfplumber 未安装）：{exc}"]

    try:
        pages_text: list[str] = []
        empty_pages: list[int] = []
        with pdfplumber.open(str(file_path)) as pdf:
            if not pdf.pages:
                return "", ["PDF 内容为空，无法提取文本。"]
            for idx, page in enumerate(pdf.pages):
                try:
                    page_text = page.extract_text() or ""
                except Exception as exc:  # noqa: BLE001 单页失败不影响其它页
                    page_text = ""
                    warnings.append(f"第 {idx + 1} 页解析失败：{exc}")
                if len(page_text.strip()) < _MIN_PAGE_CHARS:
                    empty_pages.append(idx + 1)
                pages_text.append(page_text.strip())
        full_text = "\n".join(t for t in pages_text if t).strip()

        # 全篇几乎无文本 -> 判定疑似扫描件（API_CONTRACT §2.2）
        if len(full_text) < _MIN_PAGE_CHARS:
            return "", ["疑似扫描件（PDF 无可提取文本），请改用「粘贴文本」方式上传简历/岗位描述。"]

        # 部分页面无文本也提示（可能是扫描件与文本页混排）
        if empty_pages and len(empty_pages) >= max(1, len(pages_text) // 2):
            warnings.append(
                f"第 {'、'.join(str(p) for p in empty_pages[:5])} 页疑似扫描图片，未能提取文本。"
            )
        return full_text, warnings
    except Exception as exc:  # noqa: BLE001 加密/损坏/权限等统一降级
        return "", [f"PDF 读取失败（可能已加密或文件损坏）：{exc}"]
