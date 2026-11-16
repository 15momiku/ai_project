"""JD（岗位描述）结构化解析（对应文档 §3.4.2）。

两级策略与简历解析一致（API_CONTRACT §2.2 / §3）：
1. 首选 LLM：`jd_prompt()` + `JDProfile` 规范化，`parse_source="llm"`；
2. 失败 -> 规则引擎 `_rule_parse()`，`parse_source="rule"`，绝不 500。
"""

from __future__ import annotations

import re

from ..prompts.templates import jd_prompt
from ..schemas import JDProfile
from . import field_quality
from .field_quality import CONF_FALLBACK, CONF_LABELED, CONF_NONE, CONF_SECTION
from .llm import get_llm
from .resume_parser import (
    _clean_lines,
    _coerce_str_list,
    _cut,
    _flatten_to_text,
    _strip_bullet,
    extract_industries,
    extract_skills,
    extract_soft_skills,
)

# JD 板块关键词（文档 §3.4.2）
JD_SECTION_KEYS: list[tuple[str, list[str]]] = [
    (
        "responsibilities",
        ["岗位职责", "工作职责", "主要职责", "职责描述", "职位职责", "工作内容", "岗位描述",
         "你需要做", "你将负责", "你将", "职责"],
    ),
    (
        "requirements",
        ["任职要求", "任职资格", "岗位要求", "职位要求", "任职条件", "能力要求",
         "我们希望你", "要求"],
    ),
    ("bonus", ["加分项", "优先条件", "加分", "满足以下条件优先"]),
]

# 职责条目的常见动词开头
_VERB_HEADS = [
    "负责", "参与", "完成", "推动", "搭建", "输出", "设计", "分析", "跟踪", "协助", "主导",
    "制定", "优化", "维护", "对接", "撰写", "推进", "支持", "配合", "根据", "落地", "跟进",
]

_DEGREE_REQ_RE = re.compile(r"(博士|硕士|研究生|本科|大专|专科)(及以上|以上)?")
_EXP_RE = re.compile(r"(\d+)\s*[-~—]?\s*(\d+)?\s*年(?:以上|及以上)?(?:相关)?(?:工作)??经验")
_LOCATION_RE = re.compile(r"(?:工作地点|办公地点|地点|城市|base地|base)\s*[:：]?\s*([^\n，,；;]{1,30})")
_DEPARTMENT_RE = re.compile(r"(?:所属部门|部门|所属团队|团队)\s*[:：]?\s*([^\n，,；;]{2,30})")


# ============================================================
# 工具
# ============================================================


def _detect_jd_section(line: str) -> tuple[str, str] | None:
    """判断一行是否为 JD 板块标题，返回 (板块key, 标题后剩余内容)。"""
    cleaned = re.sub(r"[\s:：]+", "", line)
    if not cleaned:
        return None
    # 「我们希望你...」常带一长句补充说明，需先于长度判定，归入任职要求
    for prefix in ("我们希望你", "我们期待你", "我们期望你", "你需要具备", "我们希望"):
        if cleaned.startswith(prefix):
            return "requirements", cleaned[len(prefix):].strip(" :：、,，-—|[]【】()（）")
    if len(cleaned) > 18:
        return None
    for key, keywords in JD_SECTION_KEYS:
        for kw in sorted(keywords, key=len, reverse=True):
            if cleaned.startswith(kw):
                remainder = cleaned[len(kw):].strip(" :：、,，-—|[]【】()（）")
                return key, remainder
    return None


def _split_jd_sections(lines: list[str]) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = "header"
    for line in lines:
        hit = _detect_jd_section(line)
        if hit:
            current, remainder = hit
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(remainder)
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _clean_title(text: str) -> str:
    """清洗岗位名称：去掉「岗位名称：」前缀与「- 2026 校园招聘」等后缀。"""
    t = (text or "").strip()
    t = re.sub(r"^(?:岗位|职位|岗位名称|职位名称|招聘岗位)\s*[:：]?\s*", "", t)
    t = re.sub(r"[-—|｜/]\s*20\d{2}\s*.*$", "", t)
    t = re.sub(r"\s*20\d{2}\s*届?.*$", "", t)
    t = re.sub(r"\s*(?:校园|社会|春季|秋季)?招聘.*$", "", t)
    t = re.sub(r"\s*[（(]\s*[)）]\s*$", "", t)
    return t.strip(" -—|｜·•")


def _detect_title(lines: list[str], job_title: str) -> str:
    """岗位名称：优先入参，其次首行 / 含「岗位/职位/招聘」的短行。"""
    if job_title and job_title.strip():
        return job_title.strip()
    for line in lines[:3]:
        cleaned = re.sub(r"[\s:：]+", "", line)
        if 1 < len(cleaned) <= 40:
            cand = _clean_title(line)
            if cand and len(cand) <= 24:
                return cand
    for line in lines[:12]:
        if any(k in line for k in ("岗位", "职位", "招聘")) and len(line) <= 40:
            cand = _clean_title(line)
            if cand:
                return cand
    return ""


def _split_items(text: str) -> list[str]:
    """把「1. xxx 2. yyy」「· xxx；· yyy」这类流水文本拆成条目。"""
    if not text:
        return []
    parts = re.split(r"(?:^|\s)\d{1,2}\s*[.、)）]|[\n；;·•●]|\s{3,}", text)
    return [p.strip(" ：:，,、-—") for p in parts if p and p.strip(" ：:，,、-—")]


# 岗位名词后缀：用于从「招聘产品经理，要求…」这类单行 JD 里抠出岗位名
_ROLE_SUFFIX = "经理|工程师|专员|实习生|分析师|设计师|架构师|助理|主管|顾问|研究员|策划|运营|开发|测试|讲师"
_ROLE_RE = re.compile(
    rf"([\u4e00-\u9fa5A-Za-z]{{1,10}}(?:{_ROLE_SUFFIX})(?:[（(][^）)\n]{{1,14}}[）)])?)"
)
_RECRUIT_RE = re.compile(r"(?:招聘|招募|诚聘|急招|岗位为|职位为|岗位是|职位是)\s*[:：]?\s*([^\n，,。；;、]{2,24})")
_DUTY_VERB_RE = re.compile(
    r"(?:^|[，,。；;\n])\s*((?:负责|参与|推动|协助|主导|承担|完成|落地|搭建|跟进)[^\n，,。；;]{4,80})"
)


def _extract_title_from_sentence(text: str) -> str:
    """兜底：从整段文本里找岗位名（文档 §3.4.2 基本信息）。"""
    m = _RECRUIT_RE.search(text or "")
    if m:
        cand = _clean_title(m.group(1))
        if cand and 2 <= len(cand) <= 24:
            return cand
    m = _ROLE_RE.search(text or "")
    if m:
        cand = _clean_title(m.group(1))
        if cand and 2 <= len(cand) <= 24:
            return cand
    return ""


def _extract_inline_duties(text: str) -> list[str]:
    """兜底：JD 没有分条职责时，抽「负责/参与/推动…」这类动作短语。"""
    out: list[str] = []
    for m in _DUTY_VERB_RE.finditer(text or ""):
        item = _cut(m.group(1).strip(" ，,。；;、"), 120)
        if item and item not in out:
            out.append(item)
    return out[:6]


# ============================================================
# 规则引擎
# ============================================================


def _rule_parse(raw_text: str, job_title: str = "") -> dict:
    """JD 规则引擎（文档 §3.4.2）。"""
    text = raw_text or ""
    lines = _clean_lines(text)
    sections = _split_jd_sections(lines)

    # ---- 岗位名称 ----
    title = _detect_title(lines, job_title)
    if not title:
        title = _clean_title(lines[0]) if lines else ""
    if not title:
        # 单行 JD（如「招聘产品经理，要求熟悉SQL…」）没有独立标题行时的兜底
        title = _extract_title_from_sentence(text)

    # ---- 地点 / 部门 ----
    location = ""
    m = _LOCATION_RE.search(text)
    if m:
        location = m.group(1).strip()
    department = ""
    m = _DEPARTMENT_RE.search(text)
    if m:
        department = m.group(1).strip()

    # ---- 岗位职责 ----
    responsibilities: list[str] = []
    raw_resp = sections.get("responsibilities", [])
    for line in raw_resp:
        for item in _split_items(_strip_bullet(line)):
            if not item:
                continue
            if item not in responsibilities:
                responsibilities.append(_cut(item, 120))
    # 动词开头的条目优先排前
    responsibilities.sort(key=lambda s: 0 if any(s.startswith(v) for v in _VERB_HEADS) else 1)
    responsibilities = responsibilities[:6]
    if not responsibilities:
        # 兜底：JD 未分条列出职责时，从整段文本里抽「负责/参与/推动…」动作短语
        responsibilities = _extract_inline_duties(text)

    # ---- 要求：学历 / 经验 ----
    req_text = " ".join(sections.get("requirements", [])) or text
    degree = ""
    m = _DEGREE_REQ_RE.search(req_text)
    if m:
        degree = m.group(1) + (m.group(2) or "")
    experience = ""
    m = _EXP_RE.search(req_text)
    if m:
        experience = f"{m.group(1)}" + (f"-{m.group(2)}" if m.group(2) else "") + "年经验"
    elif "经验不限" in req_text or "不限经验" in req_text:
        experience = "经验不限"
    elif re.search(r"20\d{2}\s*届|应届", text):
        experience = "应届生"

    # ---- 硬技能 / 软技能 ----
    hard_skills = extract_skills(text)[:12]
    soft_skills = extract_soft_skills(text)[:8]

    # ---- 加分项 ----
    bonus: list[str] = []
    for line in sections.get("bonus", []):
        for item in _split_items(_strip_bullet(line)):
            item = _cut(item, 120)
            # 只保留真正的加分/优先描述，避免把混入板块的其它说明带进来
            if item and item not in bonus and ("优先" in item or "加分" in item):
                bonus.append(item)
    if not bonus:
        for line in lines:
            if ("优先" in line or "加分" in line) and len(line) <= 100:
                item = _cut(_strip_bullet(line), 120)
                if item and item not in bonus and ("优先" in item or "加分" in item):
                    bonus.append(item)
    bonus = bonus[:6]

    # ---- keywords：行业词表 ----
    keywords = extract_industries(text)[:8]
    if not keywords and title:
        keywords = [title]

    # ---- 体验增强 §7：字段级置信度 ----
    # 只描述"是怎么被找到的"：有显式板块标题或用精确正则命中的算高置信，
    # 从全文兜底抽到的算低置信（这类正是用户最该先核对的地方）。
    conf: dict[str, float] = {
        "basic.title": CONF_SECTION if title else CONF_NONE,
        "basic.department": CONF_LABELED if department else CONF_NONE,
        "basic.location": CONF_LABELED if location else CONF_NONE,
    }
    has_resp_section = bool(sections.get("responsibilities"))
    if not responsibilities:
        conf["responsibilities"] = CONF_NONE
    elif has_resp_section:
        conf["responsibilities"] = CONF_SECTION
    else:
        conf["responsibilities"] = CONF_FALLBACK

    req_section = bool(sections.get("requirements"))
    conf["requirements.degree"] = (CONF_SECTION if req_section else CONF_FALLBACK) if degree else CONF_NONE
    conf["requirements.experience"] = (
        (CONF_SECTION if req_section else CONF_FALLBACK) if experience else CONF_NONE
    )
    # 技能走的是全文词表命中，没有"技能板块"概念，因此按命中数量分档：
    # 命中越多越可能是真的要求，命中 1-2 个更可能是碰巧出现的词。
    conf["requirements.hard_skills"] = _skill_confidence(len(hard_skills))
    conf["requirements.soft_skills"] = _skill_confidence(len(soft_skills))
    conf["requirements.bonus"] = (CONF_SECTION if sections.get("bonus") else CONF_FALLBACK) if bonus else CONF_NONE
    conf["keywords"] = _skill_confidence(len(keywords))

    data = {
        "basic": {"title": title, "department": department, "location": location},
        "responsibilities": responsibilities,
        "requirements": {
            "degree": degree,
            "experience": experience,
            "hard_skills": hard_skills,
            "soft_skills": soft_skills,
            "bonus": bonus,
        },
        "keywords": keywords,
        "field_confidence": conf,
    }
    try:
        return JDProfile(**data).model_dump()
    except Exception:  # noqa: BLE001
        return JDProfile().model_dump()


def _skill_confidence(hits: int) -> float:
    """按词表命中数量给置信度：命中越多越可信，0 表示没找到。"""
    if hits <= 0:
        return CONF_NONE
    if hits >= 4:
        return CONF_LABELED
    if hits >= 2:
        return CONF_SECTION
    return CONF_FALLBACK


def jd_coverage(parsed: dict) -> dict:
    """JD 的字段覆盖率卡片数据（体验增强 §7）。"""
    conf = (parsed or {}).get("field_confidence") or {}
    if not isinstance(conf, dict):
        conf = {}
    return field_quality.coverage(conf, field_quality.JD_FIELD_LABELS)


# ============================================================
# 对外接口
# ============================================================


# 需求条目的字段别名（模型常写中文键，或把列表项给成对象）
_REQ_LIST_ALIASES: dict[str, tuple[str, ...]] = {
    "hard_skills": ("hard_skills", "硬技能", "技能要求", "技能"),
    "soft_skills": ("soft_skills", "软技能", "软性要求"),
    "bonus": ("bonus", "加分项", "优先条件", "nice_to_have"),
}


def _coerce_jd(data: dict) -> dict:
    """校验前把 LLM 输出抹平成 `JDProfile` 能接受的形状。

    与简历解析同理：只要有一个字段形状不符，`JDProfile(**data)` 就会整体失败、
    退回规则引擎，用户看到的是「配了 key 但结果还是规则引擎的」。
    """
    basic: dict[str, str] = {}
    raw_basic = data.get("basic")
    if isinstance(raw_basic, dict):
        for key, value in raw_basic.items():
            if isinstance(key, str):
                basic[key] = _flatten_to_text(value)

    raw_req = data.get("requirements")
    req: dict[str, object] = {}
    if isinstance(raw_req, dict):
        req["degree"] = _flatten_to_text(raw_req.get("degree") or raw_req.get("学历"))
        req["experience"] = _flatten_to_text(raw_req.get("experience") or raw_req.get("经验"))
        for field, aliases in _REQ_LIST_ALIASES.items():
            value: object = None
            for alias in aliases:
                if raw_req.get(alias) not in (None, "", [], {}):
                    value = raw_req[alias]
                    break
            req[field] = _coerce_str_list(value)

    return {
        "basic": basic,
        "responsibilities": _coerce_str_list(
            data.get("responsibilities") or data.get("职责") or data.get("岗位职责")
        ),
        "requirements": req,
        "keywords": _coerce_str_list(data.get("keywords") or data.get("关键词")),
    }


async def parse_jd(raw_text: str, job_title: str = "") -> tuple[dict, str, list[str]]:
    """解析 JD（文档 §3.4.2）。返回 (JDProfile dict, parse_source, warnings)。"""
    warnings: list[str] = []
    text = (raw_text or "").strip()
    if not text:
        return JDProfile().model_dump(), "rule", ["JD 文本为空，未提取到任何内容。"]

    # ---- 1. 首选 LLM ----
    try:
        data = await get_llm().chat_json(jd_prompt(text))
    except Exception as exc:  # noqa: BLE001
        data = None
        warnings.append(f"JD LLM 解析异常，已回退规则引擎：{exc}")

    if isinstance(data, dict) and "basic" in data:
        try:
            # 先抹平模型输出的形状差异（键名中英混用、列表项给成对象等）
            parsed = JDProfile(**_coerce_jd(data)).model_dump()
            # 规则引擎补全 LLM 漏掉的岗位名称
            if not parsed["basic"].get("title") and job_title:
                parsed["basic"]["title"] = job_title
            return parsed, "llm", warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"LLM 返回结构不规范，已回退规则引擎：{exc}")

    # ---- 2. 规则引擎兜底 ----
    parsed = _rule_parse(text, job_title)
    if not parsed["basic"].get("title"):
        warnings.append("未能识别岗位名称，请手动确认。")
    if not parsed.get("responsibilities"):
        warnings.append("未识别到岗位职责，建议核对 JD 结构或手动补充。")
    if not parsed["requirements"].get("hard_skills"):
        warnings.append("未识别到硬技能要求，匹配度中的技能维度将按默认分计算。")
    return parsed, "rule", warnings
