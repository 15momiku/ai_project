"""简历结构化解析（对应文档 §3.4.1）。

两级策略（API_CONTRACT §2.2 / §3 降级总原则）：
1. 首选 LLM：`llm.chat_json(resume_prompt(text))`，成功且含 `basic` 键 -> `ResumeProfile` 规范化，`parse_source="llm"`；
2. 失败 / mock 模式返回 None / 结构不合法 -> 规则引擎 `_rule_parse()`，`parse_source="rule"`。

规则引擎真实解析文本（正则 + 板块切分 + 技能词表命中），
产出一定可以通过 `schemas.ResumeProfile` 校验，保证接口永不 500。
"""

from __future__ import annotations

import re
from typing import Any

from ..prompts.templates import resume_prompt
from ..schemas import ResumeProfile
from . import field_quality
from .field_quality import (
    CONF_DERIVED,
    CONF_FALLBACK,
    CONF_LABELED,
    CONF_NONE,
    CONF_SECTION,
    CONF_SECTION_THIN,
)
from .llm import get_llm

# ============================================================
# 技能词表（约 63 项，文档 §3.4.1 规则抽取用）
# ============================================================
SKILL_KEYWORDS: list[str] = [
    # 编程语言
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "PHP", "R", "MATLAB",
    # 前端 / 后端框架
    "HTML", "CSS", "React", "Vue", "Angular", "Node.js", "Django", "Flask", "FastAPI", "Spring",
    # 数据库 / 大数据
    "SQL", "MySQL", "PostgreSQL", "Oracle", "MongoDB", "Redis", "Hive", "Spark", "Hadoop",
    # 数据 / 办公工具
    "Excel", "PPT", "Word", "Tableau", "Power BI", "Pandas", "NumPy", "SPSS", "SAS",
    # 产品 / 设计工具
    "Axure", "Figma", "Sketch", "Photoshop", "Illustrator",
    # 工程化
    "Linux", "Git", "Docker", "Kubernetes",
    # 软性专业能力（中文）
    "数据分析", "数据挖掘", "数据可视化", "机器学习", "深度学习",
    "用户研究", "需求分析", "竞品分析", "产品设计", "产品运营", "项目管理",
    # 语言证书
    "英语CET-6", "英语CET-4", "雅思", "托福",
]

# 别名词典：把简历里的其它写法归一化为标准技能名
SKILL_ALIASES: dict[str, list[str]] = {
    "JavaScript": ["JS"],
    "TypeScript": ["TS"],
    "Node.js": ["Node", "NodeJS"],
    "Power BI": ["PowerBI", "Power-BI"],
    "Photoshop": ["PS", "Ps"],
    "机器学习": ["ML"],
    "深度学习": ["DL"],
    "英语CET-6": ["CET-6", "CET6", "英语六级", "六级", "CET 6"],
    "英语CET-4": ["CET-4", "CET4", "英语四级", "四级", "CET 4"],
    "雅思": ["IELTS"],
    "托福": ["TOEFL"],
    "SQL": ["Mysql", "MySQL"],  # MySQL 已在词表，此处仅兜底大小写
}

# 软素质词表（JD 规则解析与薄弱项识别共用）
SOFT_SKILL_KEYWORDS: list[str] = [
    "沟通", "协作", "团队合作", "抗压", "学习能力", "责任心", "主动性", "逻辑",
    "表达", "执行力", "自驱", "好奇心", "同理心",
]

# 行业词表（JD keywords 抽取用）
INDUSTRY_KEYWORDS: list[str] = [
    "互联网", "电商", "金融", "教育", "医疗", "制造", "新能源", "游戏", "快消", "AI",
    "人工智能", "大数据", "云计算", "SaaS", "企业服务", "文娱", "内容", "社交", "物流",
    "汽车", "房地产", "咨询", "广告", "消费", "半导体",
]

# ---------------- 正则 ----------------
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_TIME_RE = re.compile(r"(\d{4})\s*[.\-/年]\s*(\d{1,2})?")
_SCHOOL_RE = re.compile(r"([\u4e00-\u9fa5]{2,12}(?:大学|学院|学校))")
_MAJOR_RE = re.compile(r"专业[:：]?\s*([\u4e00-\u9fa5A-Za-z]{2,14})")
# 「专业排名前 10%」这类句式会误命中，用黑名单词过滤
_MAJOR_BLACKLIST = ("排名", "优先", "要求", "相关", "不限", "课程", "以上", "学历",
                    "方向", "经验", "知识", "能力", "技能", "背景", "实习")
_BULLET_RE = re.compile(
    r"^\s*(?:[\uf000-\uf8ff]|[\-–—\*·•●○▪◦‣⁃∙※◆■□▍]|\d{1,2}\s*[.、)）])\s*"
)
_QUANT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*\+?\s*(?:%|％|倍|万|千|百|位|个|条|人|次|分|小时|分钟|天|周|月|年"
    r"|份|场|处|组|项|台|套|页|名|款|种|轮|版|篇|张|家|字)"
    r"|提升|增长|降低|下降|覆盖|达成|排名|获奖|第一|前三"
)

# 板块标题 -> 关键词（文档 §3.4.1 板块切分）
SECTION_KEYS: list[tuple[str, list[str]]] = [
    ("education", ["教育背景", "教育经历", "教育信息", "学历背景", "EDUCATION"]),
    ("internships", ["实习经历", "实习经验", "工作经历", "工作经验", "实践经历", "实习工作经历",
                     "INTERNSHIP", "INTERNSHIPS", "WORKEXPERIENCE", "EXPERIENCE"]),
    # 校园经历紧跟在实习/工作之后：简历里它常写做「社会实践」「学生工作」，
    # 放在后面可以避免抢走「实践经历」这类更明确的实习板块标题。
    ("campus_experience", ["校园经历", "校园活动", "校园实践", "社团经历", "社团活动", "学生工作",
                           "学生会经历", "社会实践", "志愿服务", "志愿活动", "课外活动", "组织经历",
                           "CAMPUSACTIVITIES", "EXTRACURRICULAR"]),
    ("projects", ["项目经历", "项目经验", "项目实践", "科研经历", "项目介绍", "PROJECTS", "PROJECT"]),
    ("skills", ["专业技能", "技能证书", "技能特长", "掌握技能", "IT技能", "技能",
                "TECHNICALSKILLS", "SKILLS"]),
    ("awards", ["获奖情况", "获奖经历", "荣誉奖项", "奖励", "奖项", "获奖", "荣誉", "AWARDS", "HONORS"]),
    ("self_evaluation", ["自我评价", "个人评价", "自我描述", "个人总结", "自我介绍", "SUMMARY", "ABOUTME"]),
]

# 标题开头的序号（「二、项目经历」「1.项目经历」）与装饰符号（「【项目经历】」「（1）…」）
_HEADING_ORDINAL_RE = re.compile(r"^(?:[一二三四五六七八九十]{1,3}|\d{1,2})\s*[、.．,)）]\s*")
_HEADING_DECOR_RE = re.compile(r"^[【\[（(《<〉》)\]】>·•▲■★◆●▶▎|｜\s]+")
# 不改变板块归属的标题修饰前缀（「主要项目经历」「相关实习经历」）
_HEADING_PREFIX_WORDS = ("主要的", "代表性", "主要", "相关的", "相关", "核心", "精选", "重点", "个人", "其他", "我的")
# 组合标题（「实习/项目经历」）里的连接符
_EXP_TITLE_SEP_RE = re.compile(r"[／/、,，&\+]")
# 经历类标题词：判断组合标题时用
_INTERN_TITLE_WORDS = ("实习", "工作", "实践", "internship", "work")
_PROJECT_TITLE_WORDS = ("项目", "科研", "project")

# 部门/职位关键词：用于从「公司 | 职位 | 时间」行里区分公司与职位
_TITLE_WORDS = ["实习生", "工程师", "经理", "助理", "专员", "主管", "总监", "顾问", "分析师",
                "研究员", "设计师", "运营", "开发", "架构师", "负责人", "组长", "产品", "数据"]

# 公司/机构后缀：单段行按它切分「公司 | 职位」，比按职务词切更准
_ORG_SUFFIX_RE = re.compile(r"有限公司|有限责任公司|股份有限公司|集团|公司|研究院|研究所|事务所|工作室|银行|医院")
# 项目条目末尾的角色词（「…设计与实现 团队组长」）
_PROJECT_ROLE_TAIL_RE = re.compile(r"[\s　]+((?:团队|项目)?(?:组长|队长|负责人|主要成员|核心成员|成员|主导人|参与者))$")
# 上一行没有句末标点时，下一行视为 PDF 折行续写而不是新条目
_SENTENCE_END_CHARS = ("。", "！", "？", "；", ";", ".", "!", "?", "：", ":")

# 校园组织的常见职务：`_TITLE_WORDS` 里没有「主席/部长/社长」这类词，
# 单独维护一份，避免把「校学生会 副主席」整串当成组织名。
_CAMPUS_ROLE_WORDS = ["副主席", "副部长", "副会长", "秘书长", "学习委员", "生活委员", "宣传委员",
                      "组织委员", "体育委员", "文艺委员", "副班长", "主席", "部长", "社长", "副社长",
                      "会长", "团支书", "班长", "队长", "委员", "干事", "负责人", "志愿者", "成员"]


# ============================================================
# 基础工具
# ============================================================


def _clean_lines(raw_text: str) -> list[str]:
    """按行切分并做统一化：去掉全角空格与不可见字符。"""
    text = (raw_text or "").replace("\u3000", " ").replace("\xa0", " ")
    lines: list[str] = []
    for line in text.splitlines():
        s = line.strip().strip("\ufeff")
        if s:
            lines.append(s)
    return lines


def _strip_bullet(line: str) -> str:
    return _BULLET_RE.sub("", line).strip()


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "\u4e00" <= ch <= "\u9fff"


def _append_body(bullets: list[str], line: str) -> None:
    """把一行正文并入条目的描述行列表。

    PDF 提取常把一条 bullet 折成多行：上一行没有以句末标点结尾时，
    说明这句还没写完，应该拼接而不是当成新的一条。
    这样既避免把「…转化为散斑对」和「比度差异，实现…」拆成两条，
    也顺带清掉了处理 bullet 标记（含 Wingdings 私有区字形）。
    """
    body = _strip_bullet(line).strip()
    if not body:
        return
    if bullets and not bullets[-1].endswith(_SENTENCE_END_CHARS):
        prev = bullets[-1]
        joiner = "" if (_is_cjk(prev[-1:]) and _is_cjk(body[:1])) else " "
        bullets[-1] = f"{prev}{joiner}{body}"
    else:
        bullets.append(body)


def _cut(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _extract_times(text: str) -> list[str]:
    """抽取形态统一为 YYYY.MM 或 YYYY 的时间点（文档 §3.4.1 时间格式要求）。"""
    out: list[str] = []
    for m in _TIME_RE.finditer(text or ""):
        year, month = m.group(1), m.group(2)
        if month:
            try:
                mo = int(month)
            except ValueError:
                mo = 0
            out.append(f"{year}.{mo:02d}" if 1 <= mo <= 12 else year)
        else:
            out.append(year)
    return out


_DEGREE_WORDS = ("博士研究生", "硕士研究生", "博士后", "博士", "硕士", "学士", "本科", "大专", "专科", "高职")


def _strip_degree_word(seg: str) -> str:
    """剥掉片段里的学历词与括号补充说明。

    「应用物理学（光电）学士」→「应用物理学」，便于把带学历后缀的片段当作专业名。
    """
    out = re.sub(r"[（(][^）)]*[）)]", "", seg or "")
    for word in _DEGREE_WORDS:
        out = out.replace(word, "")
    return out.strip()


def _extract_degree(text: str) -> str:
    """学历识别：取最高学历。"""
    t = text or ""
    if "博士" in t:
        return "博士"
    if "硕士" in t or "研究生" in t or "碩士" in t:
        return "硕士"
    if "本科" in t or "学士" in t:
        return "本科"
    if "大专" in t or "专科" in t or "高职" in t:
        return "大专"
    return ""


def _extract_grade(text: str) -> str:
    """年级识别：大一..大四 / 研一..研三 / 应届。"""
    t = text or ""
    m = re.search(r"(大[一二三四五六]|研[一二三]|应届)", t)
    if m:
        return m.group(1)
    if "在读" in t:
        return "在读"
    return ""


def _has_skill(text: str, skill: str) -> bool:
    """大小写不敏感的技能命中判定（英文用词边界，中文直接包含）。"""
    if not text:
        return False
    if re.search(r"[\u4e00-\u9fa5]", skill):
        return skill in text
    pattern = r"(?<![A-Za-z0-9+#])" + re.escape(skill) + r"(?![A-Za-z0-9+#])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def extract_skills(text: str) -> list[str]:
    """按技能词表 + 别名从文本中抽取技能（保持词表顺序，去重）。"""
    if not text:
        return []
    found: list[str] = []
    for skill in SKILL_KEYWORDS:
        if _has_skill(text, skill):
            if skill not in found:
                found.append(skill)
            continue
        for alias in SKILL_ALIASES.get(skill, []):
            if _has_skill(text, alias):
                if skill not in found:
                    found.append(skill)
                break
    return found


def extract_soft_skills(text: str) -> list[str]:
    """抽取软素质关键词（JD 规则解析用）。"""
    if not text:
        return []
    return [s for s in SOFT_SKILL_KEYWORDS if s in text]


def extract_industries(text: str) -> list[str]:
    """抽取行业/业务方向关键词（JD keywords）。"""
    if not text:
        return []
    return [k for k in INDUSTRY_KEYWORDS if k in text]


# ============================================================
# 板块切分
# ============================================================


def _normalize_heading(line: str) -> str:
    """标题归一化：去掉空白/冒号、开头的序号与装饰符号、以及「主要/相关」这类修饰前缀。

    这样「二、项目经历」「1.项目经历」「【项目经历】」「主要项目经历」
    都能归一到「项目经历」，避免只认「项目经历」这一种写法。
    """
    cleaned = re.sub(r"[\s:：]+", "", line or "")
    if not cleaned:
        return ""
    for _ in range(3):
        before = cleaned
        cleaned = _HEADING_ORDINAL_RE.sub("", cleaned)
        cleaned = _HEADING_DECOR_RE.sub("", cleaned)
        cleaned = cleaned.strip("【】[]()（）《》<>·•▲■★◆●●")
        if cleaned == before:
            break
    for _ in range(2):
        for word in _HEADING_PREFIX_WORDS:
            # 只剥前缀，且剥完必须还剩足够长的标题，避免把「个人总结」剥成空
            if cleaned.startswith(word) and len(cleaned) - len(word) >= 3:
                cleaned = cleaned[len(word):]
                break
        else:
            break
    return cleaned


def _is_combined_experience_title(cleaned: str) -> bool:
    """识别「实习/项目经历」「实习与项目经历」这类把两类经历写在同一个标题里的板块。

    两个硬约束避免把正文句子误判成标题：
    1) 必须以经历类词开头（「负责项目经历整理工作」这类句首是动词，直接排除）；
    2) 必须同时出现实习类与项目类词，且以「经历/经验」收尾或带明确连接符。
    """
    lower = cleaned.lower()
    starts_experience = (
        cleaned.startswith(_INTERN_TITLE_WORDS + _PROJECT_TITLE_WORDS)
        or lower.startswith(("internship", "work", "project"))
    )
    if not starts_experience:
        return False
    has_intern = any(w in cleaned for w in _INTERN_TITLE_WORDS) or ("internship" in lower) or ("work" in lower)
    has_project = any(w in cleaned for w in _PROJECT_TITLE_WORDS) or ("project" in lower)
    if not (has_intern and has_project):
        return False
    if cleaned.endswith(("经历", "经验")) or lower.endswith("experience"):
        return True
    # 没有「经历/经验」后缀时要求带明确连接符，避免误判「实习项目经理」这类短语
    return bool(_EXP_TITLE_SEP_RE.search(cleaned))


def _detect_section(line: str) -> tuple[str, str] | None:
    """判断一行是否为板块标题，返回 (板块key, 标题后的剩余内容)。"""
    cleaned = _normalize_heading(line)
    if not cleaned or len(cleaned) > 18:
        return None
    # 组合标题单列成 experience 段，交给 `_parse_combined_experience` 按条目拆分
    if _is_combined_experience_title(cleaned):
        return "experience", ""
    lower = cleaned.lower()
    # 长关键词优先，避免「技能」抢走「专业技能」；英文标题按小写比较
    for key, keywords in SECTION_KEYS:
        for kw in sorted(keywords, key=len, reverse=True):
            if lower.startswith(kw.lower()):
                remainder = cleaned[len(kw):].strip(" :：、,，-—|[]【】()（）")
                return key, remainder
    return None


def _split_sections(lines: list[str]) -> dict[str, list[str]]:
    """把简历文本切分成各板块的行列表。"""
    sections: dict[str, list[str]] = {}
    current = "header"
    for line in lines:
        hit = _detect_section(line)
        if hit:
            current, remainder = hit
            sections.setdefault(current, [])
            if remainder:
                sections[current].append(remainder)
            continue
        sections.setdefault(current, []).append(line)
    return sections


# ============================================================
# 基本信息
# ============================================================


def _pick_major(text: str) -> str:
    """从「专业：xxx」句式里取专业名，并用黑名单过滤「专业排名前 10%」这类误命中。"""
    if not text:
        return ""
    for m in _MAJOR_RE.finditer(text):
        cand = re.sub(r"[\s:：]+", "", m.group(1))
        if not cand or len(cand) < 2:
            continue
        if any(bad in cand for bad in _MAJOR_BLACKLIST):
            continue
        return cand
    return ""


def _extract_name(lines: list[str], raw_text: str) -> str:
    """姓名：优先「姓名：xxx」，其次前 5 行中 2-4 个中文字符且不含「简历/个人/求职」的行。"""
    m = re.search(r"姓名[:：]?\s*([\u4e00-\u9fa5·]{2,6})", raw_text or "")
    if m:
        return m.group(1)
    for line in lines[:5]:
        candidate = re.sub(r"[\s:：|｜]+", "", line)
        if 2 <= len(candidate) <= 4 and re.fullmatch(r"[\u4e00-\u9fa5·]{2,4}", candidate):
            if not any(bad in candidate for bad in ("简历", "个人", "求职")):
                return candidate
    return ""


def _extract_basic(lines: list[str], raw_text: str) -> tuple[dict, dict]:
    """抽取 basic 字段。

    同时返回每个字段的**来源**（`origin`），供体验增强 §7 计算置信度：
    用户需要知道哪些字段是"有明确标签的"、哪些是"从上下文猜出来的"。
    """
    text = raw_text or ""
    phone_m = _PHONE_RE.search(text)
    email_m = _EMAIL_RE.search(text)
    # 学校：优先前 8 行里带「大学/学院/学校」的行
    school = ""
    school_origin = ""
    for line in lines[:8]:
        m = _SCHOOL_RE.search(line)
        if m:
            school = m.group(1)
            school_origin = "head"
            break
    if not school:
        m = _SCHOOL_RE.search(text)
        school = m.group(1) if m else ""
        school_origin = "text" if school else ""
    # 专业：带「专业」的行
    major_line = "\n".join(lines[:10])
    major = _pick_major(major_line)
    # 学历 / 年级：从较靠前的文本里判断（避免被自我评价里的「研究生」干扰）
    head = "\n".join(lines[:12]) or text
    name = _extract_name(lines, text)
    basic = {
        "name": name,
        "school": school,
        "major": major,
        "degree": _extract_degree(head),
        "grade": _extract_grade(head),
        "phone": phone_m.group(0) if phone_m else "",
        "email": email_m.group(0) if email_m else "",
    }
    # 专业若来自「专业：xxx」这类显式标签，置信度应高于纯启发式切分
    major_origin = "label" if major and re.search(r"专\s*业\s*[:：]", major_line) else ("text" if major else "")
    origin = {
        "name": "head" if name else "",
        # 正则命中手机/邮箱是精确匹配，来源记为 label（最高档）
        "phone": "label" if basic["phone"] else "",
        "email": "label" if basic["email"] else "",
        "school": school_origin,
        "major": major_origin,
        "degree": "text" if basic["degree"] else "",
        "grade": "text" if basic["grade"] else "",
    }
    return basic, origin


# ============================================================
# 教育 / 实习 / 项目 / 获奖
# ============================================================


def _parse_one_education(line: str, ctx: str) -> dict:
    """解析一条教育经历。"""
    times = _extract_times(line)
    school_m = _SCHOOL_RE.search(line)
    school = school_m.group(1) if school_m else ""

    # 专业：先看本行的「专业：xxx」，再看所属板块上下文
    major = _pick_major(line) or _pick_major(ctx)
    if not major:
        cleaned = line
        if school:
            cleaned = cleaned.replace(school, " ")
        cleaned = _TIME_RE.sub(" ", cleaned)
        cleaned = re.sub(r"[\-\~至到]{1,2}", " ", cleaned)
        for seg in re.split(r"[\s|｜,，、/]+", cleaned):
            seg = re.sub(r"[\s:：]+", "", seg)
            if not seg:
                continue
            # 「应用物理学（光电）学士」这类把学历词缀在专业后面的写法：
            # 先剥掉学历词再判断，否则整段会因为含「学士」被跳过，
            # 专业名就落到「绩点排名3.4」这种后面的片段上
            peeled = _strip_degree_word(seg)
            if peeled:
                seg = peeled
            if len(seg) < 2 or len(seg) > 14:
                continue
            if re.fullmatch(r"[\d.\-]+", seg):
                continue
            if any(w in seg for w in _TITLE_WORDS):
                continue
            # 绩点/排名/课程/成绩 这类学业信息不是专业名
            if any(bad in seg for bad in _MAJOR_BLACKLIST) or "课程" in seg or "GPA" in seg.upper():
                continue
            major = seg
            break

    return {
        "school": school,
        "major": major,
        "degree": _extract_degree(line),
        "start": times[0] if times else "",
        "end": times[1] if len(times) > 1 else "",
        "highlights": [],
    }


def _parse_education(section_lines: list[str], raw_text: str, head_lines: list[str]) -> list[dict]:
    """解析教育板块；若板块缺失则退化为从文本里找教育行。"""
    items: list[dict] = []
    # 1) 板块内按「时间 + 学校」聚合
    buffer = ""
    for line in section_lines:
        if _SCHOOL_RE.search(line):
            if buffer:
                items.append(_parse_one_education(buffer, buffer))
            buffer = line
        elif buffer:
            buffer += " " + line
        else:
            buffer = line
    if buffer:
        items.append(_parse_one_education(buffer, buffer))

    # 2) 兜底：从前 10 行里找带「大学/学院/学校」的行
    if not items:
        for line in head_lines[:10]:
            if _SCHOOL_RE.search(line):
                items.append(_parse_one_education(line, line))
                break
    # 3) 仍然没有：给一条只填学校的空记录（保证前端有展示）
    if not items:
        school = ""
        m = _SCHOOL_RE.search(raw_text or "")
        if m:
            school = m.group(1)
        items.append({"school": school, "major": "", "degree": "", "start": "", "end": "", "highlights": []})
    return items


def _split_org_line(line: str) -> tuple[str, str]:
    """把「字节跳动 | 数据分析实习生 | 2024.07-2024.10」拆成 (公司, 职位)。"""
    text = _TIME_RE.sub(" ", line)
    # 只清掉「2025.07 - 2025.09」这类独立的时间区间连接符，
    # 避免残留的 "-" 被当成一个片段，也避免误伤「AI-驱动的推荐系统」这种带连字符的名称
    text = re.sub(r"\s[\-—~至到]{1,2}\s", " ", text)
    segs = [s.strip(" |｜,，、/·•\t") for s in re.split(r"[|｜\t]|\s{2,}", text)]
    segs = [s for s in segs if s]
    if len(segs) >= 2:
        # 职位优先选含职位关键词的片段
        title = ""
        for s in segs[1:]:
            if any(w in s for w in _TITLE_WORDS):
                title = s
                break
        if not title:
            title = segs[1]
        company = segs[0]
        return company, title
    if segs:
        seg = segs[0]
        # 「成都闻为科技有限公司 光电测试助理实习」：有公司后缀时按后缀切，
        # 比按职务词切更准（按职务词会把「光电测试」留在公司名里）
        m = _ORG_SUFFIX_RE.search(seg)
        if m and m.end() < len(seg.rstrip()):
            return seg[: m.end()].strip(), seg[m.end():].strip()
        for w in sorted(_TITLE_WORDS, key=len, reverse=True):
            idx = seg.find(w)
            if idx > 0:
                return seg[:idx].strip(), seg[idx:].strip()
        return seg.strip(), ""
    return "", ""


def _split_project_line(line: str) -> tuple[str, str]:
    """把项目条目标题行拆成 (项目名, 角色)。

    兼容「名称 | 角色 | 时间」「时间 名称 角色」等写法；
    末尾带「团队组长 / 负责人」这类角色词时会从项目名里剥离出来。
    """
    text = _TIME_RE.sub(" ", line)
    text = re.sub(r"\s[\-—~至到]{1,2}\s", " ", text)
    segs = [s.strip(" |｜,，、/·•\t") for s in re.split(r"[|｜\t]|\s{2,}", text)]
    segs = [s for s in segs if s]
    if not segs:
        return "", ""
    name = segs[0]
    role = segs[1] if len(segs) >= 2 else ""
    if not role:
        m = _PROJECT_ROLE_TAIL_RE.search(name)
        if m:
            role = m.group(1)
            name = name[: m.start()].strip()
    return name.strip(), role.strip()


def _is_item_header(line: str) -> bool:
    """判断板块内一行是否为「实习/项目」条目标题行。"""
    if not line:
        return False
    if _extract_times(line):
        return True
    if any(sep in line for sep in ("|", "｜", "\t")):
        return True
    return False


def _parse_internships(section_lines: list[str]) -> list[dict]:
    """解析实习/工作经历板块。"""
    items: list[dict] = []
    cur: dict | None = None
    bullets: list[str] = []

    def flush() -> None:
        nonlocal cur, bullets
        if cur is None:
            return
        quantified = [b for b in bullets if _QUANT_RE.search(b)]
        qualitative = [b for b in bullets if b not in quantified]
        if not qualitative and quantified:
            qualitative = quantified[:2]
        cur["duties"] = [_cut(b, 160) for b in qualitative][:6]
        cur["achievements"] = [_cut(b, 160) for b in quantified][:6]
        items.append(cur)
        cur, bullets = None, []

    for line in section_lines:
        if _is_item_header(line) and not line.lstrip().startswith(("·", "•", "-", "*")):
            flush()
            company, title = _split_org_line(line)
            times = _extract_times(line)
            cur = {
                "company": company,
                "title": title,
                "start": times[0] if times else "",
                "end": times[1] if len(times) > 1 else "",
                "duties": [],
                "achievements": [],
            }
        else:
            if cur is None:
                body = _strip_bullet(line).strip()
                if not body:
                    continue
                cur = {"company": "", "title": "", "start": "", "end": "",
                       "duties": [], "achievements": []}
                bullets.append(body)
            else:
                _append_body(bullets, line)
    flush()
    return [it for it in items if it["company"] or it["title"] or it["duties"]]


def _parse_projects(section_lines: list[str]) -> list[dict]:
    """解析项目经历板块。"""
    items: list[dict] = []
    cur: dict | None = None
    bullets: list[str] = []

    def flush() -> None:
        nonlocal cur, bullets
        if cur is None:
            return
        desc = "；".join(bullets)
        cur["description"] = _cut(desc, 320)
        cur["tech_stack"] = extract_skills(desc)[:8]
        items.append(cur)
        cur, bullets = None, []

    for line in section_lines:
        if _is_item_header(line) and not line.lstrip().startswith(("·", "•", "-", "*")):
            flush()
            name, role = _split_project_line(line)
            times = _extract_times(line)
            cur = {"name": name, "role": role, "start": times[0] if times else "",
                   "end": times[1] if len(times) > 1 else "", "description": "", "tech_stack": []}
        else:
            body = _strip_bullet(line)
            if not body:
                continue
            if cur is None:
                cur = {"name": body[:20], "role": "", "start": "", "end": "",
                       "description": "", "tech_stack": []}
            else:
                _append_body(bullets, line)
    flush()
    return [it for it in items if it["name"] or it["description"]]


def _split_campus_line(line: str) -> tuple[str, str]:
    """把「校学生会 | 副主席 | 2023.09-2024.06」拆成 (组织, 职务)。

    先按校园职务词表匹配，匹配不到再退回通用的 `_split_org_line`，
    这样「学生会 副主席」这类没有分隔符的写法也能正确拆开。
    """
    text = _TIME_RE.sub(" ", line)
    text = re.sub(r"[\-\~至到]{1,2}", " ", text)
    segs = [s.strip(" |｜,，、/·•\t") for s in re.split(r"[|｜\t]|\s{2,}", text)]
    segs = [s for s in segs if s]
    if len(segs) >= 2:
        for s in segs[1:]:
            if any(w in s for w in _CAMPUS_ROLE_WORDS):
                return segs[0], s
        return segs[0], segs[1]
    if segs:
        seg = segs[0]
        # 「班级 学习委员」「AI 研习社 社长」：按最后一个空格切，右侧是职务就直接采用
        if " " in seg.strip():
            left, _, right = seg.rpartition(" ")
            if left.strip() and any(w in right for w in _CAMPUS_ROLE_WORDS):
                return left.strip(), right.strip()
        for w in sorted(_CAMPUS_ROLE_WORDS, key=len, reverse=True):
            idx = seg.find(w)
            if idx > 0:
                return seg[:idx].strip(), seg[idx:].strip()
        return _split_org_line(line)
    return "", ""


def _parse_campus_experience(section_lines: list[str]) -> list[dict]:
    """解析校园经历板块（学生会 / 社团 / 志愿服务 / 社会实践 / 竞赛组织）。

    每条：`name`（组织或活动名）+ `role`（职务）+ 时间 + `description` + `highlights`。
    含量化结果的 bullet 单独收进 `highlights`，与实习经历里 duties/achievements 的拆分口径一致。
    """
    items: list[dict] = []
    cur: dict | None = None
    bullets: list[str] = []

    def flush() -> None:
        nonlocal cur, bullets
        if cur is None:
            return
        quantified = [b for b in bullets if _QUANT_RE.search(b)]
        qualitative = [b for b in bullets if b not in quantified]
        # 全是量化成果时至少留一条进 description，避免详情整段空白
        if not qualitative and quantified:
            qualitative = quantified[:2]
        cur["description"] = _cut("；".join(qualitative), 320)
        cur["highlights"] = [_cut(b, 160) for b in quantified][:6]
        items.append(cur)
        cur, bullets = None, []

    for line in section_lines:
        if _is_item_header(line) and not line.lstrip().startswith(("·", "•", "-", "*")):
            flush()
            name, role = _split_campus_line(line)
            times = _extract_times(line)
            cur = {
                "name": name,
                "role": role,
                "start": times[0] if times else "",
                "end": times[1] if len(times) > 1 else "",
                "description": "",
                "highlights": [],
            }
        else:
            body = _strip_bullet(line)
            if not body:
                continue
            if cur is None:
                cur = {"name": body[:24], "role": "", "start": "", "end": "",
                       "description": "", "highlights": []}
            else:
                _append_body(bullets, line)
    flush()
    return [it for it in items if it["name"] or it["description"]]


# ============================================================
# 合并板块「实习/项目经历」
# ============================================================

# 判定单条经历归属哪一类时用的特征
_COMPANY_HINT_RE = re.compile(r"公司|集团|有限公司|股份|科技|研究院|研究所|事务所|银行|医院|工作室|inc|ltd", re.I)
_PROJECT_NAME_HINT_RE = re.compile(r"系统|平台|设计|实现|基于|算法|模型|研究|装置|仿真|开发|项目|方案|框架|网站|小程序|app", re.I)
_INTERN_BODY_HINT_RE = re.compile(r"实习背景|实习成果|实习内容|实习职责|工作任务|岗位职责|工作内容|工作成果")
_PROJECT_BODY_HINT_RE = re.compile(r"项目概述|系统概述|项目简介|项目背景|项目成果|系统成果|项目职责|项目实现|技术栈|仿真")
_INTERN_ROLE_HINT_RE = re.compile(r"助理|实习生|专员|实习岗")
_PROJECT_ROLE_HINT_RE = re.compile(r"组长|队长|负责人|团队成员|项目成员")


def _classify_experience_item(block: list[str]) -> str:
    """判定合并板块里的一条经历属于实习还是项目。

    用打分制：公司名 / 职务带「实习·助理」/ 实习类小标题 偏实习；
    项目名特征词（系统、基于、设计与实现…）/「项目」字样 / 项目类小标题 偏项目。
    同分时归为实习，与「实习/项目经历」的书写顺序一致。
    """
    head = (block[0] if block else "").strip()
    body = " ".join(block[1:])
    intern_score = 0
    project_score = 0
    if _COMPANY_HINT_RE.search(head):
        intern_score += 3
    if "实习" in head:
        intern_score += 3
    if _INTERN_ROLE_HINT_RE.search(head):
        intern_score += 1
    if _INTERN_BODY_HINT_RE.search(body):
        intern_score += 3
    if _PROJECT_NAME_HINT_RE.search(head):
        project_score += 3
    if "项目" in head:
        project_score += 3
    if _PROJECT_ROLE_HINT_RE.search(head):
        project_score += 1
    if _PROJECT_BODY_HINT_RE.search(body):
        project_score += 3
    return "internship" if intern_score >= project_score else "project"


def _parse_combined_experience(section_lines: list[str]) -> tuple[list[dict], list[dict]]:
    """解析「实习/项目经历」这类合并板块，返回 (实习列表, 项目列表)。

    先按条目切块（一行条目头 + 其后的描述行），逐块判定归属，
    再分别交给 `_parse_internships` / `_parse_projects`，
    这样字段口径与独立板块完全一致，不会出现两套解析逻辑。
    """
    blocks: list[list[str]] = []
    for line in section_lines:
        is_head = bool(_is_item_header(line)) and not line.lstrip().startswith(("·", "•", "-", "*"))
        if is_head or not blocks:
            blocks.append([line])
        else:
            blocks[-1].append(line)

    intern_lines: list[str] = []
    project_lines: list[str] = []
    for block in blocks:
        if not any(x.strip() for x in block):
            continue
        target = intern_lines if _classify_experience_item(block) == "internship" else project_lines
        target.extend(block)
    return _parse_internships(intern_lines), _parse_projects(project_lines)


def _parse_awards(section_lines: list[str], raw_text: str) -> list[str]:
    """解析获奖/荣誉板块；板块缺失时全文本兜底。"""
    awards = [_strip_bullet(l) for l in section_lines if _strip_bullet(l)]
    if awards:
        return [_cut(a, 120) for a in awards][:8]
    fallback: list[str] = []
    for line in (raw_text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if any(k in s for k in ("奖学金", "获奖", "荣誉", "奖项", "称号", "竞赛", "大赛")) and len(s) <= 80:
            fallback.append(_cut(_strip_bullet(s), 120))
    return fallback[:8]


def _parse_self_evaluation(section_lines: list[str]) -> str:
    text = " ".join(_strip_bullet(l) for l in section_lines if l.strip())
    return _cut(text, 300)


# ============================================================
# 规则引擎主入口
# ============================================================


def _rule_parse(raw_text: str) -> dict:
    """规则引擎：真实解析简历文本，产出可通过 ResumeProfile 校验的 dict（文档 §3.4.1）。"""
    text = raw_text or ""
    lines = _clean_lines(text)
    sections = _split_sections(lines)
    head_lines = sections.get("header", lines)

    basic, basic_origin = _extract_basic(lines, text)
    education = _parse_education(sections.get("education", []), text, head_lines)
    internships = _parse_internships(sections.get("internships", []))
    projects = _parse_projects(sections.get("projects", []))
    campus_experience = _parse_campus_experience(sections.get("campus_experience", []))
    # 「实习/项目经历」这类合并标题：按条目拆分后并入各自结果（与独立板块共存）
    combined = sections.get("experience") or []
    if combined:
        combined_internships, combined_projects = _parse_combined_experience(combined)
        internships += combined_internships
        projects += combined_projects

    # 技能：以「技能」板块 + 项目技术栈为主，全文兜底补充
    skill_text = " ".join(sections.get("skills", [])) or text
    skills = extract_skills(skill_text)
    for extra in extract_skills(text):
        if extra not in skills:
            skills.append(extra)
    skills = skills[:20]

    awards = _parse_awards(sections.get("awards", []), text)
    self_evaluation = _parse_self_evaluation(sections.get("self_evaluation", []))

    # ---- 基本信息补齐（用教育板块回填）----
    first_edu = education[0] if education else {}
    for field, source_key in (("school", "school"), ("major", "major"), ("degree", "degree")):
        if not basic.get(field) and first_edu.get(source_key):
            basic[field] = first_edu.get(source_key, "")
            # 回填来的值置信度要低于直接抽到的：它来自另一个板块，可能不是本人的
            basic_origin[field] = "derived"

    lists_for_conf = {
        "education": education,
        "internships": internships,
        "projects": projects,
        "campus_experience": campus_experience,
        "skills": skills,
        "awards": awards,
        "self_evaluation": self_evaluation,
    }
    data = {
        "basic": basic,
        "education": education,
        "internships": internships,
        "projects": projects,
        "campus_experience": campus_experience,
        "skills": skills,
        "awards": awards,
        "self_evaluation": self_evaluation,
        # 体验增强 §7：字段级置信度（只由规则引擎给出，供前端低置信度高亮与前置排序）
        "field_confidence": _confidence_map(sections, lists_for_conf, basic, basic_origin),
    }
    try:
        return ResumeProfile(**data).model_dump()
    except Exception:  # noqa: BLE001 兜底：极端脏数据也返回空壳而不是崩溃
        return ResumeProfile().model_dump()


# 经历类字段的"关键子字段"：用来判断条目是完整解析出来的，还是只切到了标题
_LIST_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "education": ("school", "major", "degree"),
    "internships": ("company", "title"),
    "projects": ("name", "description"),
    "campus_experience": ("name", "role"),
    "awards": (),
}


def _confidence_map(
    sections: dict[str, list[str]],
    lists: dict[str, list],
    basic: dict,
    origin: dict,
) -> dict[str, float]:
    """按"这份信息是怎么被找到的"给出逐字段置信度（体验增强 §7）。

    刻意不做"模型自评"式的打分：这里每一个数字都能对应到一个可解释的来源
    （显式板块标题 / 全文兜底 / 其他板块回填 / 未找到），
    因此前端把它展示成"建议先核对这几项"是站得住的。
    """
    conf: dict[str, float] = {}

    # ---- basic：按来源定档 ----
    for field in ("name", "school", "major", "degree", "grade", "phone", "email"):
        value = str(basic.get(field) or "").strip()
        src = str(origin.get(field) or "")
        if not value:
            conf[f"basic.{field}"] = CONF_NONE
        elif src == "label":
            conf[f"basic.{field}"] = CONF_LABELED
        elif src == "head":
            conf[f"basic.{field}"] = CONF_SECTION
        elif src == "derived":
            conf[f"basic.{field}"] = CONF_DERIVED
        else:
            conf[f"basic.{field}"] = CONF_FALLBACK

    # ---- 列表类：看是否有显式标题 + 条目关键字段的完整度 ----
    for key, keys in _LIST_KEY_FIELDS.items():
        items = lists.get(key) or []
        if not items:
            conf[key] = CONF_NONE
            continue
        if keys:
            filled = sum(
                1
                for it in items
                if isinstance(it, dict) and any(str(it.get(k) or "").strip() for k in keys)
            )
            ratio = filled / max(1, len(items))
        else:
            ratio = 1.0 if any(str(it or "").strip() for it in items) else 0.0
        base = CONF_SECTION if ratio >= 0.8 else CONF_SECTION_THIN
        # 没有显式板块标题（靠合并标题或全文兜底拿到）时降档
        if not sections.get(key):
            base = min(base, CONF_FALLBACK)
        conf[key] = base

    # ---- 技能：以技能板块命中为高置信，全文兜底为低置信 ----
    if not lists.get("skills"):
        conf["skills"] = CONF_NONE
    elif sections.get("skills"):
        conf["skills"] = CONF_SECTION
    else:
        conf["skills"] = CONF_FALLBACK

    # ---- 自我评价：整段文本，有标题即可信 ----
    if not str(lists.get("self_evaluation") or "").strip():
        conf["self_evaluation"] = CONF_NONE
    elif sections.get("self_evaluation"):
        conf["self_evaluation"] = CONF_SECTION
    else:
        conf["self_evaluation"] = CONF_FALLBACK

    return conf


def resume_coverage(parsed: dict) -> dict:
    """简历的字段覆盖率卡片数据（体验增强 §7）。"""
    conf = (parsed or {}).get("field_confidence") or {}
    if not isinstance(conf, dict):
        conf = {}
    return field_quality.coverage(conf, field_quality.RESUME_FIELD_LABELS)


# ============================================================
# LLM 输出归一化（校验前抹平形状差异）
# ============================================================
# 说明：模型对「奖项」这类字段常返回对象数组（{"date": ..., "award": ...}），
# 而 schema 要求字符串数组。过去只要有一个字段形状不符，ResumeProfile 校验就整体失败，
# 于是整份 LLM 解析结果被丢弃、退回规则引擎 —— 表现为「明明配了 key 却还是 rule」。
# 这里在校验前把常见偏差抹平，尽量保住模型已经抽好的信息。

# 各板块条目的字段别名（不同模型/温度下键名会漂移，含中文键）
_ITEM_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "education": {
        "school": ("school", "学校", "院校"),
        "major": ("major", "专业"),
        "degree": ("degree", "学历", "学位"),
        "start": ("start", "开始", "入学时间"),
        "end": ("end", "结束", "毕业时间"),
        "highlights": ("highlights", "亮点", "主要经历", "描述"),
    },
    "internships": {
        "company": ("company", "公司", "单位", "机构"),
        "title": ("title", "职位", "岗位"),
        "start": ("start", "开始"),
        "end": ("end", "结束"),
        "duties": ("duties", "工作内容", "职责", "主要工作"),
        "achievements": ("achievements", "成果", "业绩", "业绩亮点"),
    },
    "projects": {
        "name": ("name", "项目名称"),
        "role": ("role", "角色", "担任角色"),
        "start": ("start", "开始"),
        "end": ("end", "结束"),
        "description": ("description", "描述", "项目描述", "项目简介"),
        "tech_stack": ("tech_stack", "技术栈", "使用技术"),
    },
    "campus_experience": {
        "name": ("name", "组织", "活动名称", "组织名称"),
        "role": ("role", "职务", "担任职务"),
        "start": ("start", "开始"),
        "end": ("end", "结束"),
        "description": ("description", "描述", "主要工作"),
        "highlights": ("highlights", "亮点", "成果", "主要成果"),
    },
}

# 条目本身是纯字符串时，放进该板块的主字段
_LIST_PRIMARY_FIELD = {
    "education": "school",
    "internships": "company",
    "projects": "name",
    "campus_experience": "name",
}
# 条目内的列表字段 / 文本字段
_ITEM_LIST_FIELDS = ("highlights", "duties", "achievements", "tech_stack")
_ITEM_TEXT_FIELDS = ("school", "major", "degree", "start", "end",
                     "company", "title", "name", "role", "description")


def _flatten_to_text(value: Any) -> str:
    """把任意值压成一行文本。

    `{"date": "25.11", "award": "国家级一等奖"}` → `"25.11 国家级一等奖"`。
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        parts = [_flatten_to_text(v) for v in value.values()]
        return " ".join(p for p in parts if p).strip()
    if isinstance(value, (list, tuple)):
        parts = [_flatten_to_text(v) for v in value]
        return " ".join(p for p in parts if p).strip()
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _coerce_str_list(value: Any) -> list[str]:
    """归一化成字符串列表（skills / awards 这类 schema 只接受字符串的字段）。"""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[str] = []
    for item in items:
        text = _flatten_to_text(item)
        if text and text not in out:
            out.append(text)
    return out


def _coerce_items(section: str, value: Any) -> list[dict]:
    """归一化对象列表（education / internships / projects / campus_experience）。"""
    primary = _LIST_PRIMARY_FIELD.get(section, "name")
    aliases = _ITEM_ALIASES.get(section, {})
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: list[dict] = []
    for item in items:
        if isinstance(item, str):
            if item.strip():
                out.append({primary: item.strip()})
            continue
        if not isinstance(item, dict):
            text = _flatten_to_text(item)
            if text:
                out.append({primary: text})
            continue
        row: dict[str, Any] = {}
        for field in (*_ITEM_TEXT_FIELDS, *_ITEM_LIST_FIELDS):
            raw = item.get(field)
            if raw in (None, "", [], {}):
                # 同名字段没有时，再按别名找一遍（兼容中文键）
                for alias in aliases.get(field, ()):
                    if item.get(alias) not in (None, "", [], {}):
                        raw = item[alias]
                        break
            if raw in (None, "", [], {}):
                continue
            row[field] = _coerce_str_list(raw) if field in _ITEM_LIST_FIELDS else _flatten_to_text(raw)
        if row:
            out.append(row)
    return out


def _coerce_profile(data: dict) -> dict:
    """校验前把 LLM 输出抹平成 `ResumeProfile` 能接受的形状。"""
    basic: dict[str, str] = {}
    raw_basic = data.get("basic")
    if isinstance(raw_basic, dict):
        for key, value in raw_basic.items():
            if isinstance(key, str):
                basic[key] = _flatten_to_text(value)

    out: dict[str, Any] = {
        "basic": basic,
        "skills": _coerce_str_list(data.get("skills")),
        "awards": _coerce_str_list(data.get("awards")),
        "self_evaluation": _flatten_to_text(data.get("self_evaluation")),
    }
    for section in ("education", "internships", "projects", "campus_experience"):
        out[section] = _coerce_items(section, data.get(section))

    # 学历统一到契约约定的词表（本科/硕士/博士/大专）：
    # 模型常写成「学士」「研究生」，而前端按契约词表展示；
    # 认不出来的写法（如 MBA）保留原值，不做丢弃
    raw_degree = _flatten_to_text(basic.get("degree"))
    normalized_degree = _extract_degree(raw_degree)
    if not normalized_degree:
        normalized_degree = _extract_degree(" ".join(str(e.get("degree") or "") for e in out["education"]))
    basic["degree"] = normalized_degree or raw_degree
    return out


async def parse_resume(raw_text: str) -> tuple[dict, str, list[str]]:
    """解析简历（文档 §3.4.1）。返回 (ResumeProfile dict, parse_source, warnings)。"""
    warnings: list[str] = []
    text = (raw_text or "").strip()
    if not text:
        return ResumeProfile().model_dump(), "rule", ["简历文本为空，未提取到任何内容。"]

    # ---- 1. 首选 LLM ----
    try:
        data = await get_llm().chat_json(resume_prompt(text))
    except Exception as exc:  # noqa: BLE001 外部依赖失败不抛 500
        data = None
        warnings.append(f"简历 LLM 解析异常，已回退规则引擎：{exc}")

    if isinstance(data, dict) and "basic" in data:
        try:
            # 先抹平模型输出的形状差异（奖项给成对象数组、键名用中文等），
            # 否则一个字段不合规就会让整份 LLM 结果被丢弃
            return ResumeProfile(**_coerce_profile(data)).model_dump(), "llm", warnings
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"LLM 返回结构不规范，已回退规则引擎：{exc}")

    # ---- 2. 规则引擎兜底 ----
    parsed = _rule_parse(text)
    basic = parsed.get("basic", {})
    if not basic.get("name"):
        warnings.append("未能识别姓名，请在解析结果中手动补充。")
    if not parsed.get("internships") and not parsed.get("projects"):
        warnings.append("未识别到实习/项目经历，建议核对简历板块标题或手动补充。")
    return parsed, "rule", warnings


def summary_line(profile: dict) -> str:
    """前端「解析结果摘要」一行文案：
    姓名｜学校·专业·学历｜实习 N 段｜项目 N 个｜校园 N 段｜技能 M 项。"""
    profile = profile or {}
    basic = profile.get("basic") or {}
    education = profile.get("education") or []
    first = education[0] if education else {}
    school = basic.get("school") or first.get("school") or "未知院校"
    major = basic.get("major") or first.get("major") or "未知专业"
    degree = basic.get("degree") or first.get("degree") or ""
    name = basic.get("name") or "未识别姓名"
    edu_part = "·".join([b for b in (school, major, degree) if b])
    return (
        f"{name}｜{edu_part}｜实习 {len(profile.get('internships') or [])} 段"
        f"｜项目 {len(profile.get('projects') or [])} 个"
        f"｜校园 {len(profile.get('campus_experience') or [])} 段"
        f"｜技能 {len(profile.get('skills') or [])} 项"
    )
