"""简历优化（润色 / ATS 检测 / 旧简历解析 / PDF 导出）。

对应能力：
1. 简历润色   —— 口语化经历 -> 专业 STAR 三段式（支持中/英文）
2. ATS 兼容性检测 —— 总分 + 五个模块的详细诊断
3. 旧简历 PDF 解析 —— 上传 PDF 自动抽取并结构化，回填表单
4. PDF 导出   —— 见 services/resume_doc.py

工程约定（与本项目其它模块一致）：
- 不自带厂商 SDK 与独立 Key，一律走本项目统一的 LLMClient
  （`services/llm.py`：DeepSeek / 通义 / 智谱 / Kimi / OpenAI 任一可用即可，
   自动按已配置的 Key 挑厂商；没有任何 Key 时明确报错而不是静默降级）；
- 不自己抽 PDF 文本，复用 `services/pdf_parser.extract_text_from_pdf`
  （本项目的统一 PDF 解析入口）；
- 所有大模型调用都是 async，且**由调用方保证不阻塞事件循环**。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from .llm import get_llm
from .jd_parser import extract_industries, extract_skills, extract_soft_skills
from .resume_parser import SKILL_KEYWORDS

logger = logging.getLogger(__name__)

# ==========================================================================
# 数据模型（字段名与模板/Jinja2 一一对应，不要随意改名）
# ==========================================================================


class ExperienceItem(BaseModel):
    title: str = ""
    company: str = ""
    start_date: str = ""
    end_date: str = ""
    description: str = ""


class HonorItem(BaseModel):
    name: str = ""
    date: str = ""
    level: str = ""  # 奖励等级，允许留空（如「国家级」「校级一等奖」）


class ResumeData(BaseModel):
    name: str = ""
    phone: str = ""
    email: str = ""
    # 以下为模板2（简约现代）用到的补充信息，全部可选
    job_intention: str = ""  # 求职意向
    age: str = ""
    gender: str = ""
    ethnicity: str = ""
    wechat: str = ""
    political_status: str = ""  # 政治面貌（模板1 基本信息表用）
    slogan: str = ""  # 顶部标语（模板1 标题区用）
    photo: str = ""  # 证件照：data:image/...;base64 或 http(s) 链接
    education: list[ExperienceItem] = []
    work_experience: list[ExperienceItem] = []
    project_experience: list[ExperienceItem] = []
    campus_experience: list[ExperienceItem] = []
    honors: list[HonorItem] = []
    skills: list[str] = []


class PolishRequest(BaseModel):
    resume: ResumeData
    jd: str = ""
    language: str = "zh"  # "zh" 或 "en"


class ATSRequest(BaseModel):
    resume: ResumeData
    jd: str = ""


class KeywordCoverageRequest(BaseModel):
    """JD 关键词覆盖率请求（体验增强 §11）。与 ATS 同样是纯计算、不落库。"""

    resume: ResumeData
    jd: str = ""


class RenderPDFRequest(BaseModel):
    resume: ResumeData
    template_name: str = "template1.html"
    language: str = "zh"  # 决定 PDF 里的区块标题与标签分隔符


# ==========================================================================
# 结构化工具（与原始实现保持同口径）
# ==========================================================================
_ITEM_FIELDS = ("title", "company", "start_date", "end_date", "description")
_LIST_FIELDS = ("education", "work_experience", "project_experience", "campus_experience")
# 荣誉奖项字段顺序：时间在左、名称居中、等级在右
_HONOR_FIELDS = ("date", "name", "level")
_TEXT_FIELDS = (
    "name", "phone", "email", "job_intention", "age", "gender", "ethnicity",
    "wechat", "political_status", "slogan",
)
# 证件照不经过大模型：base64 图片塞进 prompt 会白白烧掉大量 token
_PASSTHROUGH_FIELDS = ("photo",)

# STAR 段前标签（中英文都兼容）
_STAR_LABELS = {
    "背景", "行动", "结果",
    "S", "T", "A", "R",
    "Situation", "Task", "Action", "Result", "Background",
}
_STAR_LABELS_LOWER = {x.lower() for x in _STAR_LABELS}

_bullet_re = re.compile(r"^\s*(?:[•·▪◦●○*\-–—]|\d+[\.、\)]|[（(]\d+[）)])\s*")


def split_star(desc: str) -> list[dict[str, str]]:
    """把 description 按行拆成 [{label, text}, ...]。

    - 行首是已知标签（背景/行动/结果 / Background/Action/Result 等）时抽到 label；
    - 否则 label 为空字符串，text 为整行。
    """
    if not isinstance(desc, str):
        return []
    result: list[dict[str, str]] = []
    for raw in desc.splitlines():
        line = _bullet_re.sub("", raw).strip()
        if not line:
            continue
        matched = False
        for sep in ("：", ":"):
            if sep in line:
                label, _, text = line.partition(sep)
                label = label.strip()
                if label.lower() in _STAR_LABELS_LOWER:
                    result.append({"label": label, "text": text.strip()})
                    matched = True
                    break
        if not matched:
            result.append({"label": "", "text": line})
    return result


def _normalize_description(desc: Any) -> str:
    """把字面量 "\\n" / "<br>" 转成真实换行，去掉空行与 AI 多带的项目符号。"""
    if not isinstance(desc, str):
        return ""
    desc = (
        desc.replace("\\n", "\n")
        .replace("<br>", "\n")
        .replace("<br/>", "\n")
        .replace("<br />", "\n")
    )
    return "\n".join(c for c in (ln.strip() for ln in desc.splitlines()) if c)


def normalize_descriptions(data: dict[str, Any]) -> dict[str, Any]:
    """为每条经历生成 description_lines，供模板渲染（STAR 分段）。"""
    for key in _LIST_FIELDS:
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            item["description"] = _normalize_description(item.get("description", ""))
            item["description_lines"] = split_star(item["description"])
    return data


def _str_or(value: Any, fallback_value: Any) -> str:
    """优先取非空字符串，否则回退到备选值。"""
    if isinstance(value, str) and value.strip():
        return value
    return fallback_value if isinstance(fallback_value, str) else ""


def _sanitize_dict_list(raw_items: Any, source_items: Any, fields) -> list[dict[str, str]]:
    """把大模型返回的 [{...}] 规整成 fields 指定的形状，逐项按序回退到原始表单数据。"""
    sources = source_items if isinstance(source_items, list) else []
    cleaned: list[dict[str, str]] = []
    if isinstance(raw_items, list):
        for idx, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            src = sources[idx] if idx < len(sources) and isinstance(sources[idx], dict) else {}
            cleaned.append({field: _str_or(item.get(field), src.get(field)) for field in fields})
    if not cleaned:
        cleaned = [
            {field: _str_or(None, src.get(field)) for field in fields}
            for src in sources
            if isinstance(src, dict)
        ]
    return cleaned


def sanitize_resume(data: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    """把大模型返回的 JSON 规整成 ResumeData 的形状。

    字段缺失/类型不对时回退到原始表单数据，避免前端拿到结构损坏的对象后
    在导出 PDF 时触发 422 校验错误。
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="大模型返回的 JSON 不是对象")

    result: dict[str, Any] = {}
    for key in _TEXT_FIELDS:
        result[key] = _str_or(data.get(key), fallback.get(key))

    for key in _LIST_FIELDS:
        result[key] = _sanitize_dict_list(data.get(key), fallback.get(key), _ITEM_FIELDS)

    result["honors"] = _sanitize_dict_list(data.get("honors"), fallback.get("honors"), _HONOR_FIELDS)

    # 证件照原样透传，永远不采用大模型给的值
    for key in _PASSTHROUGH_FIELDS:
        result[key] = str(fallback.get(key) or "")

    skills = data.get("skills")
    if not isinstance(skills, list):
        skills = fallback.get("skills") or []
    result["skills"] = [str(s).strip() for s in skills if str(s).strip()]

    return result


# 中文字符（含中日韩统一表意文字扩展 A 区常见范围）
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def find_cjk_fields(data: Any, prefix: str = "") -> list[str]:
    """递归找出含中文字符的字段路径，用于英文简历的残留检查。"""
    found: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            found += find_cjk_fields(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            found += find_cjk_fields(value, f"{prefix}[{idx}]")
    elif isinstance(data, str) and _CJK_RE.search(data):
        found.append(prefix or "(root)")
    return found


# ==========================================================================
# ATS 报告规整
# ==========================================================================
ATS_MODULES = (
    ("keyword_match", "关键词匹配度", "是否覆盖 JD 中的核心技能、术语与职责关键词"),
    ("structure", "结构规范度", "是否使用标准区块标题，便于 ATS 分块解析"),
    ("date_format", "日期格式规范性", "起止时间是否统一为 YYYY-MM、是否完整可解析"),
    ("description_quality", "描述文案专业性", "是否使用行动动词、量化成果与相关技术栈"),
    ("contact_info", "基本信息完整性", "姓名、电话、邮箱、技能等关键字段是否齐全"),
)

# 大模型偶尔用「无显著问题」这类占位文字填空，渲染出来会很奇怪，直接丢掉
_PLACEHOLDER_ITEMS = {
    "无", "无。", "无显著问题", "无明显问题", "无问题", "暂无", "没有",
    "none", "n/a", "na", "无建议", "无需修改",
}


def score_level(score: int) -> str:
    """把分数映射成中文档位。"""
    if score >= 90:
        return "优秀"
    if score >= 80:
        return "良好"
    if score >= 70:
        return "一般"
    if score >= 60:
        return "待改进"
    return "较差"


def score_grade(score: int) -> str:
    """把分数映射成等级字母。"""
    for threshold, grade in ((90, "A"), (85, "A-"), (80, "B+"), (75, "B"),
                             (70, "B-"), (60, "C"), (0, "D")):
        if score >= threshold:
            return grade
    return "D"


def _as_score(value: Any) -> Optional[int]:
    """把任意输入转成 0~100 的整数分，无法解析时返回 None。"""
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, number))


def _as_str_list(value: Any) -> list[str]:
    raw: list[str] = []
    if isinstance(value, list):
        raw = [str(item).strip() for item in value]
    elif isinstance(value, str) and value.strip():
        raw = [value.strip()]
    return [item for item in raw if item and item.lower() not in _PLACEHOLDER_ITEMS]


def sanitize_ats_report(data: Any) -> dict[str, Any]:
    """把大模型返回的 ATS 报告规整成前端可直接渲染的完整结构。

    五个模块一个都不能少，缺字段就补默认值，脏数据不会让前端渲染崩掉。
    """
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="ATS 检测返回格式异常")

    raw_modules = data.get("modules")
    raw_modules = raw_modules if isinstance(raw_modules, list) else []
    details_in = data.get("details") if isinstance(data.get("details"), dict) else {}

    dict_modules = [m for m in raw_modules if isinstance(m, dict)]

    modules: list[dict[str, Any]] = []
    for index, (key, name, desc) in enumerate(ATS_MODULES):
        # 先按 key / 中文名匹配，模型没给 key 时退化为按顺序匹配
        raw: dict[str, Any] = {}
        for candidate in dict_modules:
            if str(candidate.get("key", "")).strip() == key or str(candidate.get("name", "")).strip() == name:
                raw = candidate
                break
        else:
            if index < len(dict_modules):
                raw = dict_modules[index]

        score = _as_score(raw.get("score"))
        if score is None:
            score = _as_score(details_in.get(key))
        if score is None:
            score = 0
        modules.append({
            "key": key,
            "name": name,
            "description": desc,
            "score": score,
            # 档位一律由分数推导，避免大模型一会「及格」一会「中」导致徽章口径不一致
            "level": score_level(score),
            "summary": str(raw.get("summary") or "").strip(),
            "issues": _as_str_list(raw.get("issues")),
            "suggestions": _as_str_list(raw.get("suggestions")),
            "highlights": _as_str_list(raw.get("highlights")),
        })

    total = _as_score(data.get("score"))
    if total is None:
        total = int(round(sum(m["score"] for m in modules) / len(modules))) if modules else 0

    suggestions = _as_str_list(data.get("suggestions"))
    if not suggestions:
        # 兜底：从各模块建议里各取一条组成总体建议
        for module in modules:
            suggestions.extend(module["suggestions"][:1])

    return {
        "score": total,
        "grade": str(data.get("grade") or "").strip() or score_grade(total),
        "level": str(data.get("level") or "").strip() or score_level(total),
        "summary": str(data.get("summary") or "").strip(),
        "modules": modules,
        "details": {m["key"]: m["score"] for m in modules},  # 兼容旧字段
        "suggestions": suggestions,
    }


# ==========================================================================
# 大模型调用
# ==========================================================================
_NO_LLM_HINT = (
    "简历优化需要大模型支持，但后端当前没有可用的 API Key"
    "（正在使用规则引擎演示模式）。请在 backend/.env 中填写 "
    "DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / ZHIPU_API_KEY 之一后重启后端。"
)


async def _chat_json(
    system_prompt: str,
    user_prompt: str,
    *,
    temperature: float = 0.5,
    max_tokens: int = 4000,
) -> dict[str, Any]:
    """统一的大模型 JSON 调用入口（把失败翻译成对用户有意义的 HTTP 错误）。"""
    llm = get_llm()
    if llm.mode == "mock":
        raise HTTPException(status_code=503, detail=_NO_LLM_HINT)

    data = await llm.chat_json(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if data is None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"大模型（{llm.provider}/{llm.model_name}）调用失败或返回内容无法解析为 JSON，"
                "请稍后重试；若持续失败可在 backend/.env 调大 LLM_TIMEOUT_SECONDS 或换用更快的模型。"
            ),
        )
    return data


# ==========================================================================
# 接口1：简历润色（支持中/英文，STAR 三段式）
# ==========================================================================
_ZH_SYSTEM = "你是一个专业的简历优化助手，请严格按照JSON格式输出结果，不要包含任何其他文字。"
_EN_SYSTEM = (
    "You are a professional resume editor. Always respond in English and "
    "strictly in JSON format. Do not include any text outside the JSON. "
    "Every field value must be in English (only numbers, dates and email "
    "addresses may keep their original form); never output Chinese characters."
)


def _polish_prompt_en(resume_json: str, jd: str) -> str:
    return f"""
You are a professional resume editor. Polish the following resume content and output strictly in JSON format.

Original resume (JSON):
{resume_json}

Target job description (JD):
{jd}

Requirements:
1. Keep the original data structure (field names unchanged), including "campus_experience" and "honors".
2. The ENTIRE output must be in English — every field, not just the descriptions:
   "name", "title", "company", "description", "skills", "job_intention", "gender", "ethnicity",
   and the "name"/"level" of each honor.
   Only numbers, dates and email addresses keep their original form.
   Do NOT output a single Chinese character anywhere in the JSON.
3. Translate Chinese proper nouns into their official English names where they exist
   (e.g. 北京大学 -> Peking University, 腾讯 -> Tencent, 国家奖学金 -> National Scholarship,
   国家级 -> National Level), and romanize personal names in pinyin (e.g. 张三 -> Zhang San).
4. Polish every "description" field to be more professional and attractive, and align it with keywords from the JD.
5. For each entry in "work_experience", "project_experience" and "campus_experience", the "description"
   MUST be split into THREE parts following the STAR method, separated by newline
   characters "\\n", as a single string:
   - Part 1 MUST start with "Background:" — the project/business context and the problem to be solved;
   - Part 2 MUST start with "Action:" — what you specifically did, and with which methods/technologies/tools;
   - Part 3 MUST start with "Result:" — quantified outcomes and impact (e.g. "improved by 30%", "served 200+ users").
6. Except for the labels "Background:", "Action:", "Result:", do NOT use any bullet
   points (•, ·, -, *), numbering, or other subtitles.
7. The "description" of "education" should remain a single paragraph, without labels.
8. Return ONLY JSON, no other text.
9. Keep each paragraph to around 80 words.
10. For "honors", translate "name" and "level" into English, keep "date" unchanged, and do not
    reorder the entries.
"""


def _polish_prompt_zh(resume_json: str, jd: str) -> str:
    return f"""
请将以下口语化的简历内容润色为专业的简历描述，并严格按照 JSON 格式输出。

原始简历信息（JSON格式）：
{resume_json}

岗位 JD：
{jd}

要求：
1. 保持原有数据结构（字段名不变），包括 campus_experience（校园经历）和 honors（荣誉奖项）。
2. 对 description 字段进行润色，使其更专业、有吸引力，并适当匹配 JD 中的关键词。
3. 所有 description 字段必须输出为中文。
4. 【重点】work_experience、project_experience 和 campus_experience 中每一条经历的 description，
   必须按 STAR 法则拆成三段，段与段之间用换行符 "\\n" 分隔，整体仍是一个字符串：
   第一段必须以「背景：」开头，交代项目/业务背景与要解决的问题；
   第二段必须以「行动：」开头，说明你具体做了什么，用了什么方法/技术/工具；
   第三段必须以「结果：」开头，用可量化数据说明成果与影响（如"提升 30%""服务 200+ 用户"）。
5. 除「背景：」「行动：」「结果：」这三个段前标签外，正文中不要出现任何
   项目符号（•、·、-、*）、序号或其它小标题。
6. 教育背景的 description 保持普通单段，不需要分段、不加标签。
7. 只返回 JSON，不要有其他文字。
8. 控制每段字数在100字左右。
9. honors（荣誉奖项）必须原样返回：每一项保留 date（获奖时间）、name（荣誉名称）和
   level（奖励等级），不要翻译、改写或调整顺序。
"""


async def ensure_english(resume: dict[str, Any]) -> dict[str, Any]:
    """英文简历兜底：大模型偶尔仍会漏几个中文字段（尤其是公司名/职位/荣誉等级）。

    检测到残留中文时再补一次「只做翻译」的调用，而不是把中文混进英文简历。
    二次调用失败时退回第一次的结果，保证接口仍然可用。
    """
    leftovers = find_cjk_fields(resume)
    if not leftovers:
        return resume

    logger.warning("英文简历仍残留中文（%d 处）：%s", len(leftovers), ", ".join(leftovers[:8]))
    prompt = f"""
以下 JSON 本应是全英文简历，但仍有部分字段是中文。请把残留的中文翻译成自然的英文表达。

要求：
1. 保持 JSON 结构和字段名完全不变；
2. 只翻译中文内容，已经是英文的部分原样保留；
3. 日期、数字、邮箱保持原样；
4. 不要输出任何中文字符；
5. 只返回 JSON，不要有其他文字。

{json.dumps(resume, ensure_ascii=False, indent=2)}
"""
    system_prompt = (
        "You are a professional translator. Always respond in English and strictly "
        "in JSON format. Never output Chinese characters."
    )
    try:
        translated = sanitize_resume(
            await _chat_json(system_prompt, prompt, temperature=0.3), resume
        )
    except Exception as exc:  # noqa: BLE001 翻译失败不影响主流程
        logger.warning("英文补翻译失败，返回首次结果：%s", exc)
        return resume

    remaining = find_cjk_fields(translated)
    if remaining:
        logger.warning("补翻译后仍有中文残留：%s", ", ".join(remaining[:8]))
    else:
        logger.info("英文补翻译完成，已无中文残留")
    return translated


async def polish_resume(req: PolishRequest) -> dict[str, Any]:
    """简历润色主流程：结构化 -> 大模型润色 -> 规整 -> （英文时）补翻译。"""
    original = req.resume.model_dump()

    # 证件照是 base64 大字符串，绝不能塞进 prompt（白白烧 token），这里先摘掉
    prompt_resume = req.resume.model_dump()
    prompt_resume.pop("photo", None)
    resume_json = json.dumps(prompt_resume, ensure_ascii=False)

    if req.language == "en":
        system_prompt = _EN_SYSTEM
        prompt = _polish_prompt_en(resume_json, req.jd)
    else:
        system_prompt = _ZH_SYSTEM
        prompt = _polish_prompt_zh(resume_json, req.jd)

    polished = await _chat_json(system_prompt, prompt, temperature=0.7, max_tokens=4000)
    polished_data = sanitize_resume(polished, original)
    if req.language == "en":
        # 兜底：确保英文简历里不混入中文
        polished_data = await ensure_english(polished_data)
    return normalize_descriptions(polished_data)


# ==========================================================================
# 接口2：ATS 兼容性检测（总分 + 五个模块）
# ==========================================================================
def _ats_prompt(resume_text: str, jd: str) -> str:
    return f"""
你是一个资深的简历 ATS（申请人跟踪系统）检测专家。请对以下简历做**分模块的详细诊断**。

简历内容（JSON格式）：
{resume_text}

目标岗位 JD：
{jd}

请逐项评分（每个模块满分 100）并给出详细诊断，共 5 个模块：
1. keyword_match 关键词匹配度：是否覆盖 JD 中的核心技能、术语、职责关键词
2. structure 结构规范度：是否使用标准区块标题（教育背景/工作经历/项目经历/校园经历/荣誉奖项/技能特长），区块是否齐全
3. date_format 日期格式规范性：起止时间是否统一为 YYYY-MM、是否完整、ATS 能否解析
4. description_quality 描述文案专业性：是否使用行动动词、量化成果、与 JD 相关的技术栈
5. contact_info 基本信息完整性：姓名、电话、邮箱、技能等关键字段是否齐全

诊断要求：
- issues 必须引用简历或 JD 中的**具体内容**（例如"JD 要求「高并发」，但简历描述中未出现该词"），禁止写空泛套话；
- suggestions 必须是可直接落地的修改动作，最好给出建议改写后的字句；
- highlights 写该模块做得好的地方，没有就返回空数组；
- 每个模块给 1~3 条 issues、1~3 条 suggestions；
- 每个模块的 summary 用一句话概括结论（不要重复 issues 的内容）。

请严格按以下 JSON 格式输出，不要有其他文字：
{{
    "score": 82,
    "grade": "B+",
    "summary": "一句话总评，指出最关键的短板",
    "modules": [
        {{
            "key": "keyword_match",
            "name": "关键词匹配度",
            "score": 78,
            "level": "良好",
            "summary": "该模块的一句话结论",
            "issues": ["JD 要求「高并发」，但简历中未出现该关键词"],
            "suggestions": ["在工作经历的结果段补充「支撑日均千万级请求」"],
            "highlights": ["已覆盖 Python、FastAPI 等核心技能"]
        }}
    ],
    "suggestions": ["最该优先修改的 3 条总体建议"]
}}
"""


async def check_ats(req: ATSRequest) -> dict[str, Any]:
    """ATS 检测主流程。"""
    payload = req.resume.model_dump()
    payload.pop("photo", None)  # 图片 base64 对 ATS 诊断没有任何价值
    resume_text = json.dumps(payload, ensure_ascii=False, indent=2)
    result = await _chat_json(_ZH_SYSTEM, _ats_prompt(resume_text, req.jd), temperature=0.4)
    return sanitize_ats_report(result)


# ==========================================================================
# 接口2.5：JD 关键词覆盖率（体验增强 §11）
# ==========================================================================
# 刻意**不走大模型**：一是"改完有没有变好"这个判断不该依赖 Key，
# 二是关键词是否出现是确定性事实，用规则算出来的结果比模型复述更可解释、更快。
# 因而零密钥环境下这块看板照常可用，符合项目的核心承诺。

# JD 里最多取多少个目标关键词：太多会变成噪音清单，用户反而不动手
_MAX_TARGET_KEYWORDS = 20


def _resume_text_blob(resume: Any) -> str:
    """把整份简历压成一段文本，用于判断关键词是否已覆盖。"""
    data = resume.model_dump() if isinstance(resume, BaseModel) else (resume or {})
    parts: list[str] = []
    basic = data.get("basic") or {}
    parts.extend(str(v) for v in basic.values() if isinstance(v, (str, int, float)))
    parts.append(str(data.get("job_intention") or ""))
    for key in ("work_experience", "project_experience", "campus_experience"):
        for item in data.get(key) or []:
            if not isinstance(item, dict):
                continue
            parts.extend(str(v) for v in item.values() if isinstance(v, str))
    parts.extend(str(s) for s in (data.get("skills") or []))
    for item in data.get("honors") or []:
        if isinstance(item, dict):
            parts.extend(str(v) for v in item.values() if isinstance(v, str))
    return " ".join(parts)


def keyword_coverage(resume: Any, jd_text: str) -> dict[str, Any]:
    """JD 目标关键词在简历中的覆盖率。

    目标词来自 JD 文本的技能/软素质/行业词表命中（与解析、匹配度共用同一批词表），
    因此"看板说缺什么"和"匹配度说缺什么"不会是两套结论。
    """
    jd = jd_text or ""
    if not (jd.strip() and _resume_text_blob(resume).strip()):
        return {
            "total": 0,
            "covered_count": 0,
            "covered": [],
            "missing": [],
            "skill_missing": [],
            "note": "填入简历内容与岗位 JD 后，这里会显示关键词覆盖率。" if not jd.strip() else "请先填写简历内容。",
        }

    blob = _resume_text_blob(resume)
    # 目标词：硬技能优先，其次软素质，再补行业词；去重保序
    targets: list[str] = []
    for word in (
        extract_skills(jd)
        + extract_soft_skills(jd)
        + extract_industries(jd)
    ):
        w = str(word or "").strip()
        if w and w not in targets:
            targets.append(w)
    targets = targets[:_MAX_TARGET_KEYWORDS]

    if not targets:
        return {
            "total": 0,
            "covered_count": 0,
            "covered": [],
            "missing": [],
            "skill_missing": [],
            "note": "没从 JD 里识别出明确的关键词，可以粘贴更完整的岗位职责与任职要求。",
        }

    covered = [w for w in targets if w in blob]
    missing = [w for w in targets if w not in blob]
    # 只有"技能类"的缺失词才适合一键加进技能栏；行业/软素质类硬加进去反而像凑数
    skill_missing = [w for w in missing if w in SKILL_KEYWORDS]

    return {
        "total": len(targets),
        "covered_count": len(covered),
        "covered": covered,
        "missing": missing,
        "skill_missing": skill_missing,
        "note": (
            f"已覆盖 {len(covered)}/{len(targets)} 个 JD 关键词。"
            "带「加入技能特长」的缺失词可以直接补进技能栏；其余建议在项目/经历描述里用一句话体现，"
            "只堆关键词会被面试官追问。"
        )
        if missing
        else f"JD 关键词已全部覆盖（{len(targets)} 项），这一块不用再改。",
    }


# ==========================================================================
# 接口3：从简历 PDF 文本中结构化抽取（供上传解析使用）
# ==========================================================================
def _parse_prompt(text: str) -> str:
    return f"""
请从以下简历文本中提取关键信息，并严格按照 JSON 格式输出。

简历文本：
{text[:3000]}

输出格式：
{{
    "name": "姓名",
    "phone": "电话",
    "email": "邮箱",
    "job_intention": "求职意向",
    "education": [
        {{"title": "专业", "company": "学校", "start_date": "开始时间", "end_date": "结束时间", "description": "描述"}}
    ],
    "work_experience": [
        {{"title": "职位", "company": "公司", "start_date": "开始时间", "end_date": "结束时间", "description": "描述"}}
    ],
    "project_experience": [
        {{"title": "项目名称", "company": "组织/客户", "start_date": "开始时间", "end_date": "结束时间", "description": "描述"}}
    ],
    "campus_experience": [
        {{"title": "角色/职务", "company": "组织/社团", "start_date": "开始时间", "end_date": "结束时间", "description": "描述"}}
    ],
    "honors": [
        {{"date": "获奖时间", "name": "荣誉名称", "level": "奖励等级"}}
    ],
    "skills": ["技能1", "技能2"]
}}

如果某字段信息缺失，请填空字符串或空数组。
只返回 JSON，不要有其他文字。
"""


async def structure_resume_text(text: str) -> dict[str, Any]:
    """把简历纯文本交给大模型，抽成本项目的结构化简历格式。"""
    parsed = await _chat_json(_ZH_SYSTEM, _parse_prompt(text), temperature=0.2)
    return normalize_descriptions(sanitize_resume(parsed, {}))


def empty_resume() -> dict[str, Any]:
    """空白简历骨架（供前端「清空表单」与后端兜底使用，字段与 ResumeData 一致）。"""
    return {
        "name": "", "phone": "", "email": "", "job_intention": "", "age": "",
        "gender": "", "ethnicity": "", "wechat": "", "political_status": "",
        "slogan": "", "photo": "",
        "education": [], "work_experience": [], "project_experience": [],
        "campus_experience": [], "honors": [], "skills": [],
    }
