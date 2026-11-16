"""数字人面试官 · AI 语音对话主循环引擎（文档 §6.2.1）。

职责
----
1. `plan()`      ：生成面试计划（自我介绍→项目深挖→专业技能→场景题→行为面试→反问环节）。
2. `opening()`   ：开场白 + 第 1 题（自我介绍），返回第 1 个面试官 Turn。
3. `respond()`   ：核心推进逻辑 —— 追问判断（文档 §6.2.1 第5条）+ 下一题生成；
                   **进度只从 dialogue 推导**，不依赖实例内部可变状态，
                   因此路由可在每次请求重建引擎而不丢进度（API_CONTRACT §2.3）。
4. `closing()`   ：结束语。
5. `build_profile()`：把 简历/JD/公司/匹配结果 压成 flat profile dict。

降级原则（API_CONTRACT §3 / 文档 §4.7）
--------------------------------------
LLM 不可用、超时或返回格式错误时，一律走内置中文校招题库；任何路径都不抛异常。
"""

from __future__ import annotations

import re
from typing import Any

from .. import constants
from ..deps import local_iso, new_id
from ..prompts import templates as prompts
from . import media
from .llm import get_llm

# ============================================================
# 追问阈值（体验增强 §14 压力面试模式会放宽这两个值）
# ============================================================
# 常规：回答短于该字符数视为空泛 → 触发追问
_FOLLOWUP_MIN_CHARS = 25
# 常规：上报时长短于该秒数视为准备不足 → 触发追问（0 表示未计时，不判定）
_FOLLOWUP_MIN_SECONDS = 15

# ============================================================
# 通用小工具
# ============================================================


def _now_iso() -> str:
    """统一时间戳格式（ISO 字符串，本地时区，供前端展示）。"""
    return local_iso()


def _normalize_total(value: Any, minimum: int = 6) -> int:
    """题量归一化：默认 9 题，最少 `minimum`、最多 20 题。

    整场面试的下限是 6（保证 6 个环节每环节 ≥1）；体验增强 §9 的「单题特训」
    只练一个环节，允许低到 3 题，因此下限可调。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = 9
    if n <= 0:
        n = 9
    return max(int(minimum), min(20, n))


def _clean_focus(focus: str, fallback: str) -> str:
    """清掉模型把提示词示例照抄进来的前缀。

    模型有时直接返回「考察点，如：自我介绍能力」这种带说明字样的值，
    展示在报告里很突兀；这里剥掉前缀，只保留考察点名称本身。
    """
    text = (focus or "").strip()
    for prefix in ("考察点，如：", "考察点，如:", "考察点：", "考察点:", "考察：", "考察:", "考察点", "考察"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip(" 　：:，,、")
            break
    if not text or len(text) > 30:
        return fallback
    return text


def _as_list(value: Any) -> list:
    """把 str / list / None 统一成 list（字符串按常见分隔符拆开）。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v is not None and str(v).strip() != ""]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = re.split(r"[、,，;；/\n]+", text)
        return [p.strip() for p in parts if p.strip()]
    return [value]


def _finalize(text: str) -> str:
    """清理模板残留占位符与重复空格。"""
    text = re.sub(r"\{[a-zA-Z_]+\}", "", text or "")
    return re.sub(r"\s{2,}", " ", text).strip()


def _item_name(item: Any) -> str:
    """从简历条目（dict 或 str）里取项目名。"""
    if isinstance(item, dict):
        name = (item.get("name") or item.get("title") or "").strip()
        if name:
            return name
        desc = (item.get("description") or "").strip()
        return desc[:20] if desc else ""
    return str(item).strip()


def _internship_name(item: Any) -> str:
    """从实习条目里取「公司（岗位）」形式的名字。"""
    if isinstance(item, dict):
        company = (item.get("company") or "").strip()
        title = (item.get("title") or "").strip()
        if company and title:
            return f"{company}（{title}）"
        return company or title
    return str(item).strip()


def _project_titles_from(value: Any) -> list[str]:
    out: list[str] = []
    for it in _as_list(value):
        name = _item_name(it)
        if name and name not in out:
            out.append(name)
    return out


def _internship_titles_from(value: Any) -> list[str]:
    out: list[str] = []
    for it in _as_list(value):
        name = _internship_name(it)
        if name and name not in out:
            out.append(name)
    return out


def _project_techs_from(value: Any) -> list[str]:
    out: list[str] = []
    for it in _as_list(value):
        if isinstance(it, dict):
            out.append("、".join(str(x) for x in _as_list(it.get("tech_stack"))))
        else:
            out.append("")
    return out


def _text_of_projects(value: Any) -> str:
    lines: list[str] = []
    for it in _as_list(value):
        if isinstance(it, dict):
            seg = (it.get("name") or "").strip()
            role = (it.get("role") or "").strip()
            tech = "、".join(str(x) for x in _as_list(it.get("tech_stack")))
            desc = (it.get("description") or "").strip()
            if role:
                seg = f"{seg}（{role}）" if seg else role
            if tech:
                seg = f"{seg} 技术栈：{tech}" if seg else f"技术栈：{tech}"
            if desc:
                seg = f"{seg}：{desc[:60]}" if seg else desc[:60]
            if seg:
                lines.append(seg)
        else:
            s = str(it).strip()
            if s:
                lines.append(s)
    return "；".join(lines) or "无"


def _text_of_campus(value: Any) -> str:
    """校园经历转成一行文本（供 LLM 出题/评分提示词使用）。"""
    lines: list[str] = []
    for it in _as_list(value):
        if isinstance(it, dict):
            name = (it.get("name") or "").strip()
            role = (it.get("role") or "").strip()
            desc = (it.get("description") or "").strip()
            highlights = "、".join(str(x) for x in _as_list(it.get("highlights"))[:2])
            seg = f"{name}（{role}）" if (name and role) else (name or role)
            body = f"{desc[:60]}；{highlights}" if (desc and highlights) else (desc[:60] or highlights)
            if body:
                seg = f"{seg}：{body}" if seg else body
            if seg:
                lines.append(seg)
        else:
            s = str(it).strip()
            if s:
                lines.append(s)
    return "；".join(lines) or "无"


def _text_of_internships(value: Any) -> str:
    lines: list[str] = []
    for it in _as_list(value):
        if isinstance(it, dict):
            company = (it.get("company") or "").strip()
            title = (it.get("title") or "").strip()
            start, end = (it.get("start") or "").strip(), (it.get("end") or "").strip()
            period = f"{start}-{end}" if (start or end) else ""
            head = " ".join(x for x in [company, title] if x)
            duties = "、".join(str(x) for x in _as_list(it.get("duties"))[:2])
            seg = head
            if period:
                seg = f"{seg}（{period}）" if seg else period
            if duties:
                seg = f"{seg}：{duties[:50]}" if seg else duties[:50]
            if seg:
                lines.append(seg)
        else:
            s = str(it).strip()
            if s:
                lines.append(s)
    return "；".join(lines) or "无"


def _text_of_education(value: Any) -> str:
    lines: list[str] = []
    for it in _as_list(value):
        if isinstance(it, dict):
            segs = [
                str(it.get("school") or "").strip(),
                str(it.get("major") or "").strip(),
                str(it.get("degree") or "").strip(),
            ]
            period = "-".join(x for x in [str(it.get("start") or "").strip(), str(it.get("end") or "").strip()] if x)
            line = " ".join(s for s in segs if s)
            if period:
                line = f"{line}（{period}）" if line else period
            if line:
                lines.append(line)
        else:
            s = str(it).strip()
            if s:
                lines.append(s)
    return "；".join(lines) or "未提供"


# ============================================================
# 环节元信息（考察点 / 评分参考要点 / 说明）
# ============================================================

_SECTION_DESC: dict[str, str] = {
    "自我介绍": "开场与个人定位，判断表达概括与岗位匹配",
    "项目深挖": "围绕简历中的真实项目/实习逐层深挖",
    "专业技能": "针对 JD 硬技能（优先薄弱项）考察真实使用场景",
    "场景题": "结合公司业务构造实战场景推演",
    "行为面试": "STAR 结构的行为与协作考察",
    "反问环节": "候选人的提问与求职动机",
}

_SECTION_FOCUS: dict[str, list[str]] = {
    "自我介绍": ["自我认知与岗位匹配", "表达概括能力", "经历亮点提炼"],
    "项目深挖": [
        "项目角色与个人贡献",
        "需求拆解能力",
        "技术方案权衡",
        "量化结果意识",
        "复盘与迭代能力",
        "跨团队协作",
    ],
    "专业技能": ["专业技能掌握深度", "工具与方法的实际应用", "数据与指标意识", "方案优化能力"],
    "场景题": ["业务理解与拆解", "优先级与资源分配", "问题排查与决策"],
    "行为面试": ["团队协作与沟通", "抗压与应变", "主动性与复盘"],
    "反问环节": ["求职动机与关注点", "岗位理解"],
    "结束语": ["面试收尾"],
}

_SECTION_REFERENCE: dict[str, list[str]] = {
    "自我介绍": [
        "是否围绕岗位讲清个人定位",
        "是否有 1-2 个与岗位强相关的亮点",
        "表达是否结构清晰、控制在 2 分钟内",
    ],
    "项目深挖": [
        "是否讲清个人在项目中的具体职责而非团队泛述",
        "是否有明确的技术/业务决策与权衡过程",
        "是否给出可量化的结果或影响",
        "是否能复盘不足并给出改进",
    ],
    "专业技能": [
        "是否说明技能的真实使用场景而非罗列名词",
        "是否体现方法/原理层面的理解",
        "是否考虑数据规模与边界情况",
        "是否有优化与验证意识",
    ],
    "场景题": [
        "是否先厘清目标与约束条件",
        "是否有结构化分析框架（如分层/分优先级）",
        "方案是否可落地并有验证方式",
        "是否兼顾成本与体验",
    ],
    "行为面试": [
        "是否用 STAR 结构呈现（背景-任务-行动-结果）",
        "是否突出个人动作而非团队泛述",
        "是否有明确结果与个人反思",
    ],
    "反问环节": [
        "是否提出与岗位/成长相关的高质量问题",
        "是否体现对公司业务的了解",
        "是否展现长期意愿与主动思考",
    ],
    "结束语": ["表达是否礼貌得体"],
}

_REFERENCE_PAD = ["回答的结构化程度与条理", "内容的具体性与真实度", "对岗位要求的贴合程度"]


# ============================================================
# 规则题库（LLM 不可用时的降级，必须真实可用）
# 占位符：{project} {tech} {company} {job_title} {industry} {business} {skill}
# 注意：这里**没有**「专业技能」键——该环节由 _skill_question 按技能类型
# 从 _SKILL_BANK / _GAP_BANK 出题，本表只覆盖其余环节。
# ============================================================

_RULE_BANK: dict[str, list[str]] = {
    # ---------------- 自我介绍（8）----------------
    "自我介绍": [
        "先简单做个自我介绍吧，重点说说你和这个岗位最相关的经历，控制在两分钟以内。",
        "我们用两分钟互相认识一下，请你介绍一下自己，以及为什么想投{job_title}这个方向。",
        "请你做一个简短的自我介绍，重点讲一件最能体现你能力的事。",
        "从你最有代表性的一段经历讲起，做个自我介绍吧。",
        "请用一两分钟介绍自己，特别说说你为{job_title}这个岗位做了哪些准备。",
        "先介绍一下你自己吧，我更想听你和这个岗位相关的部分。",
        "请做个自我介绍，并说说你觉得自己最大的优势是什么。",
        "我们用两分钟认识一下，介绍自己的时候可以说说你最有成就感的一段经历。",
    ],
    # ---------------- 项目深挖（12）----------------
    "项目深挖": [
        "你在『{project}』这个项目里具体负责哪一部分？当时最难的技术或业务决策是什么？",
        "『{project}』最终的效果怎么样？有没有可量化的结果？你个人的贡献占多大比例？",
        "『{project}』里你和团队是怎么分工的？如果出现过分歧，你们最后怎么达成一致？",
        "介绍一下『{project}』的整体方案，为什么选择{tech}这条路线？有没有考虑过别的方案？",
        "『{project}』推进过程中你遇到的最大阻力是什么？你具体采取了哪些动作？",
        "如果现在让你重做『{project}』，你会保留什么、改掉什么？为什么？",
        "『{project}』里的关键数据或指标是怎么定义和采集的？你怎么判断它做得好不好？",
        "在『{project}』里你踩过最大的一个坑是什么？后来是怎么定位和解决的？",
        "你在『{company}』实习期间跟进的核心工作是什么？其中哪些环节是你独立负责的？",
        "在『{company}』实习时，你交付的成果给业务带来了什么变化？有数据可以佐证吗？",
        "『{project}』里你觉得最有技术含量的一部分是什么？展开讲讲你的实现思路。",
        "『{project}』做完之后你复盘过哪些问题？下一次你会怎么优化方案或协作方式？",
    ],
    # ---------------- 场景题（10）----------------
    "场景题": [
        "我们公司现在主要做{business}。假设业务要在一个新渠道快速起量，你会怎么设计推进方案？",
        "结合{business}这个业务，如果用户增长突然停滞，你会从哪些角度找原因、又怎么验证？",
        "如果让你负责{business}方向上的一个新功能，从需求到上线你会怎么排优先级？",
        "在{business}场景下，成本和体验往往会冲突。如果必须二选一，你会怎么权衡并说服团队？",
        "假设我们所在的{industry}行业有一个核心指标突然下滑 20%，你会怎么排查？",
        "如果业务方给的需求很模糊，只说了要提升{business}的效果，你会怎么把它拆成可执行的任务？",
        "结合{job_title}的岗位职责，如果资源只给一半，你会砍掉哪些事、保住哪些事？为什么？",
        "我们在{business}这块业务上做了一个新尝试，上线后数据不及预期，你会怎么复盘和调整？",
        "假设你要在两周内交付一个{business}相关的小方案，你会怎么安排节奏和验证方式？",
        "在{business}业务里，你会用哪几个指标来判断这件事做得好不好？为什么选这几个？",
    ],
    # ---------------- 行为面试（10，STAR 类）----------------
    "行为面试": [
        "请用 STAR 讲一次你主动推动、最终拿到结果的经历：背景、你的任务、你做了什么、结果如何。",
        "讲一次你和队友意见不合的经历，你是怎么沟通并最终达成一致的？",
        "讲一次你在很短时间内完成一件难事的经历，你是怎么安排优先级的？",
        "讲一次你失败或被否定的经历，你从中复盘出了什么、后来改了什么？",
        "有没有一次你承担了超出职责范围的事情？当时为什么接、结果怎么样？",
        "讲一次你被别人指出问题、并做出改变的经历，你是怎么调整的？",
        "讲一次你需要说服别人接受你方案的经历，你用了什么策略？",
        "讲一次你在信息不完整的情况下做决策的经历，你后来怎么验证对错？",
        "讲一次你主动帮团队里其他人补位的经历，你当时是怎么考虑的？",
        "讲一次你从零开始学习一项新技能并用到实践中的经历，学习路径是什么？",
    ],
    # ---------------- 反问环节（8，均包含固定问法）----------------
    "反问环节": [
        "我的问题问完了，你有什么想问我的吗？",
        "面试到这里差不多了，你有什么想问我的吗？",
        "接下来把时间交给你，你有什么想问我的吗？",
        "我这边的问题基本结束了，你有什么想问我的吗？",
        "最后留点时间给你，你有什么想问我的吗？",
        "关于岗位或者团队，你有什么想问我的吗？",
        "我们进入反问环节，你有什么想问我的吗？",
        "我也想听听你的问题，你有什么想问我的吗？",
    ],
}

# 技能差异化题库（要求：不同技能类型不能问同一句话）
_SKILL_BANK: dict[str, list[str]] = {
    "SQL类": [
        "JD 里要求熟悉 {skill}，能讲讲你在实际项目里是怎么写的吗？如果数据量再大 10 倍你会怎么优化？",
        "{skill} 场景里一条查询明显变慢了，你会怎么一步步定位和优化？",
        "用 {skill} 做过最复杂的一次取数需求是什么？说说你的表设计和查询思路。",
        "{skill} 里索引、执行计划这些概念你怎么理解？项目里你怎么判断该不该加索引？",
        "如果要你用 {skill} 算日活和次日留存，你会怎么写？口径上要注意什么？",
        "{skill} 做多表关联时出现数据不一致，你会怎么排查和验证？",
    ],
    "数据分析类": [
        "JD 里要求熟悉 {skill}，能讲讲你用它在实际项目里做过哪些分析吗？",
        "用 {skill} 分析业务问题时，你一般怎么定义指标口径？举个例子说明。",
        "如果 {skill} 得出的结论和业务方直觉相反，你会怎么验证并沟通？",
        "你在 {skill} 上做过最复杂的一次分析是什么？数据量和维度大概到什么量级？",
        "用 {skill} 做分析时，你怎么判断数据质量是否可靠？",
        "如果让你用 {skill} 搭一套日常监控报表，你会放哪几个指标？为什么？",
    ],
    "产品设计类": [
        "JD 里要求熟悉 {skill}，能结合你做过的一个功能说说你是怎么用它推进的吗？",
        "用 {skill} 做原型之前，你一般会先想清楚哪些问题？",
        "如果需求方和你在 {skill} 方案上意见不一致，你会怎么推进？",
        "你在 {skill} 相关的工作里做过最完整的一次闭环是什么？从需求到上线。",
        "用 {skill} 时你怎么做取舍？比如交互复杂度和开发成本的冲突。",
        "如果让你用 {skill} 重构一个体验很差的老功能，你会从哪一步开始？",
    ],
    "编程类": [
        "JD 里要求熟悉 {skill}，能讲讲你在项目里的实际使用场景吗？大概写过什么量级的代码？",
        "用 {skill} 时你遇到过最难定位的一个问题是什么？最后怎么排查出来的？",
        "在 {skill} 里你怎么做性能优化？有没有具体的前后数据对比？",
        "如果让你用 {skill} 从零搭一个小系统，你会怎么划分模块和接口？",
        "{skill} 的代码你平时怎么做测试和评审？怎么保证交付质量？",
        "关于 {skill}，你了解它的哪些底层机制？这些机制对实际开发有什么影响？",
    ],
    "通用类": [
        "JD 里要求熟悉 {skill}，能讲讲你在实际项目里怎么用的吗？如果数据量再大 10 倍你会怎么调整？",
        "关于 {skill}，你觉得自己目前处在什么水平？请用一个具体例子说明。",
        "你在项目里用 {skill} 解决过的最难的一个问题是什么？当时是怎么定位的？",
        "如果让你给完全不懂 {skill} 的同学讲清楚它，你会从哪几点入手？",
        "{skill} 你用下来觉得它最大的局限在哪里？遇到这种情况你会怎么办？",
        "除了 {skill}，你还用过哪些同类工具或方法？为什么最后选择了它？",
        "如果要在团队内推广 {skill}，你会怎么推进？需要特别注意什么？",
        "讲讲你在 {skill} 上踩过的坑，以及你后来总结出的经验。",
        "如果现在让你从零学 {skill}，你会怎么安排前两周的学习节奏？",
        "针对 {skill}，你平时怎么检验自己用得对不对？有没有可量化的标准？",
    ],
}

# 薄弱项专问题库（weak_points 命中时使用，聚焦「怎么补短板」）
_GAP_BANK: list[str] = [
    "JD 里特别看重 {skill}，你目前的积累到什么程度了？可以结合一个具体例子讲讲。",
    "我注意到你在 {skill} 方面的经历相对少一些，能说说你是怎么补这块短板的吗？",
    "如果让你现在把 {skill} 练到能独立交付的水平，你会怎么安排学习路径？",
    "关于 {skill}，你觉得自己的优势和不足分别在哪里？",
    "JD 要求熟悉 {skill}，你在这方面做过最接近的一次实践是什么？",
    "{skill} 这块如果现场让你上手，你会先从哪一步开始？",
]

# 技能归类规则（文档要求：数据分析类 / SQL类 / 产品设计类 / 编程类 / 通用类）
_SKILL_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("SQL类", ["sql", "mysql", "postgres", "oracle", "数据库", "查询", "hive", "clickhouse", "建表", "索引"]),
    (
        "数据分析类",
        [
            "数据分析",
            "数据挖掘",
            "数据建模",
            "数据仓库",
            "bi",
            "tableau",
            "power bi",
            "excel",
            "spss",
            "pandas",
            "numpy",
            "统计",
            "指标",
            "a/b",
            "abtest",
            "漏斗",
            "报表",
            "埋点",
            "增长",
        ],
    ),
    (
        "产品设计类",
        [
            "axure",
            "figma",
            "sketch",
            "原型",
            "prd",
            "需求",
            "产品设计",
            "交互",
            "用户体验",
            "ux",
            "ui",
            "竞品",
            "用户研究",
            "敏捷",
            "scrum",
            "运营",
            "内容",
        ],
    ),
    (
        "编程类",
        [
            "python",
            "java",
            "c++",
            "c#",
            "golang",
            "javascript",
            "typescript",
            "前端",
            "后端",
            "全栈",
            "算法",
            "数据结构",
            "linux",
            "git",
            "django",
            "flask",
            "spring",
            "react",
            "vue",
            "node",
            "docker",
            "kubernetes",
            "k8s",
            "机器学习",
            "深度学习",
            "pytorch",
            "tensorflow",
            "爬虫",
            "运维",
        ],
    ),
]

# 不像「技能」的薄弱项描述词（这类用于考察点，不能当作技能名塞进技能题）
_NOT_SKILL_MARKERS = (
    "不足",
    "缺乏",
    "缺少",
    "较弱",
    "较少",
    "不够",
    "偏弱",
    "欠缺",
    "待提升",
    "需要加强",
    "经验",
    "意识",
    "能力",
)


def _skill_category(skill: str) -> str:
    """把技能名归类，用于挑选差异化的问题模板。"""
    s = (skill or "").lower()
    for category, keys in _SKILL_CATEGORY_RULES:
        if any(k in s for k in keys):
            return category
    return "通用类"


def _looks_like_skill(text: str) -> bool:
    """判断一条薄弱项描述是否可以直接当作「技能名」使用。"""
    t = (text or "").strip()
    if not t or len(t) > 14:
        return False
    if any(m in t for m in _NOT_SKILL_MARKERS):
        return False
    if re.search(r"[，。；,;]", t):
        return False
    return True


# ============================================================
# 题量分配（文档 §6.2.1 第6条）
# ============================================================

_FLEX_SECTIONS = ["项目深挖", "专业技能", "场景题", "行为面试"]


def _adjust_quotas(total: int) -> dict[str, int]:
    """按 total 等比例调整各环节题量。

    - 自我介绍 / 反问环节恒为 1 题；
    - 其余 4 个环节至少各 1 题；
    - total == 9 时严格等于 constants.SECTION_QUOTA（1/3/2/1/1/1）。
    """
    total = _normalize_total(total)
    if total == 9:
        return {name: int(constants.SECTION_QUOTA.get(name, 1)) for name in constants.INTERVIEW_SECTIONS}

    quotas: dict[str, int] = {"自我介绍": 1, "反问环节": 1}
    rem = total - 2  # 留给 4 个弹性环节的题量
    if rem <= len(_FLEX_SECTIONS):
        for name in _FLEX_SECTIONS:
            quotas[name] = 1
        return quotas

    base = {name: int(constants.SECTION_QUOTA.get(name, 1)) for name in _FLEX_SECTIONS}
    extra = rem - len(_FLEX_SECTIONS)
    weight_sum = sum(base.values()) or 1
    ideal = {name: extra * base[name] / weight_sum for name in _FLEX_SECTIONS}
    floor = {name: int(ideal[name]) for name in _FLEX_SECTIONS}
    left = extra - sum(floor.values())
    # 最大余数法补足，保证确定性
    order = sorted(_FLEX_SECTIONS, key=lambda n: (-(ideal[n] - floor[n]), -base[n], _FLEX_SECTIONS.index(n)))
    i = 0
    while left > 0 and order:
        floor[order[i % len(order)]] += 1
        left -= 1
        i += 1
    for name in _FLEX_SECTIONS:
        quotas[name] = 1 + floor[name]
    return quotas


# ============================================================
# 核心引擎
# ============================================================


class InterviewEngine:
    """面试官对话引擎。

    注意：除 `profile` 与 `total` 外**不持有任何可变进度状态**，
    进度完全由调用方传入的 `dialogue` 推导（API_CONTRACT §2.3 要求）。
    """

    def __init__(
        self,
        profile: dict,
        total_questions: int = 9,
        focus_section: str = "",
    ) -> None:
        self.profile: dict = profile or {}
        # 体验增强 §9「单题特训」：把整场压到同一个环节上，
        # 否则 3 题会被配额摊到 3 个环节，练不到同一个薄弱点。
        self.focus_section: str = (
            focus_section if focus_section in constants.INTERVIEW_SECTIONS else ""
        )
        self.total: int = _normalize_total(total_questions, 3 if self.focus_section else 6)
        self._plan_cache: dict | None = None

    # ---------------- 计划 ----------------

    def plan(self) -> dict:
        """返回面试计划：各环节题量与序号区间 + 匹配策略。"""
        if self._plan_cache is None:
            self._plan_cache = self._build_plan()
        return self._plan_cache

    def _build_plan(self) -> dict:
        # 单题特训：全部题量集中在 focus_section，其余环节配额为 0
        if self.focus_section:
            sections = []
            cursor = 1
            for name in constants.INTERVIEW_SECTIONS:
                quota = self.total if name == self.focus_section else 0
                sections.append(
                    {
                        "name": name,
                        "quota": quota,
                        "index_start": cursor if quota else 0,
                        "index_end": cursor + quota - 1 if quota else 0,
                        "focus": "、".join(_SECTION_FOCUS.get(name, [])[:3]),
                        "description": _SECTION_DESC.get(name, ""),
                    }
                )
                cursor += quota
            return {
                "total": self.total,
                "sections": sections,
                "strategy": self._strategy(),
                "source": "rule",
                "mode": "retry",
                "focus_section": self.focus_section,
            }
        quotas = _adjust_quotas(self.total)
        sections: list[dict] = []
        cursor = 1
        for name in constants.INTERVIEW_SECTIONS:
            quota = int(quotas.get(name, 1))
            sections.append(
                {
                    "name": name,
                    "quota": quota,
                    "index_start": cursor,
                    "index_end": cursor + quota - 1,
                    "focus": "、".join(_SECTION_FOCUS.get(name, [])[:3]),
                    "description": _SECTION_DESC.get(name, ""),
                }
            )
            cursor += quota
        return {"total": self.total, "sections": sections, "strategy": self._strategy(), "source": "rule"}

    def _section_start(self, name: str) -> int:
        for sec in self.plan()["sections"]:
            if sec["name"] == name:
                return int(sec["index_start"])
        return 1

    def _section_of(self, index: int) -> str:
        for sec in self.plan()["sections"]:
            if int(sec["index_start"]) <= index <= int(sec["index_end"]):
                return sec["name"]
        return constants.INTERVIEW_SECTIONS[-1]

    def _strategy(self) -> dict:
        raw = self.profile.get("match_strategy") or {}
        if not isinstance(raw, dict):
            raw = {}
        focus = _as_list(raw.get("focus"))
        deep_dive = _as_list(raw.get("deep_dive"))
        pressure = _as_list(raw.get("pressure_test"))
        if not focus:
            focus = self._weak_points()[:3] or self._hard_skills()[:3]
        if not deep_dive:
            deep_dive = self._project_titles()[:3]
        if not pressure:
            pressure = self._hard_skills()[:2] or self._project_titles()[:2]
        return {
            "focus": [str(x) for x in focus[:6]],
            "deep_dive": [str(x) for x in deep_dive[:6]],
            "pressure_test": [str(x) for x in pressure[:6]],
        }

    # ---------------- profile 读取助手 ----------------

    def _hard_skills(self) -> list[str]:
        return [str(x) for x in _as_list(self.profile.get("hard_skills"))]

    def _weak_points(self) -> list[str]:
        return [str(x) for x in _as_list(self.profile.get("weak_points"))]

    def _project_titles(self) -> list[str]:
        return _project_titles_from(self.profile.get("projects"))

    def _project_techs(self) -> list[str]:
        return _project_techs_from(self.profile.get("projects"))

    def _internship_titles(self) -> list[str]:
        return _internship_titles_from(self.profile.get("internships"))

    def _business_short(self) -> str:
        business = (self.profile.get("business") or "").strip()
        if not business:
            return "我们的核心业务"
        # 先按句末标点取第一句，再按顿号/逗号取第一个完整分句，避免截断成一个残句
        first = re.split(r"[。；;\n]", business)[0].strip()
        first = re.split(r"[，,、]", first)[0].strip()
        if not first:
            first = business
        # 场景题里要嵌进句子，控制在 12 字内，超出则按标点/空格优雅截断
        if len(first) > 12:
            head = re.split(r"[\s（(]", first)[0].strip()
            first = head if 2 <= len(head) <= 12 else first[:12]
        return first

    def _prompt_profile(self) -> dict:
        """供 prompts.templates 使用的 profile（education/internships/projects 转成文本）。"""
        p: dict = dict(self.profile)
        p["education"] = _text_of_education(self.profile.get("education"))
        p["internships"] = _text_of_internships(self.profile.get("internships"))
        p["projects"] = _text_of_projects(self.profile.get("projects"))
        p["campus_experience"] = _text_of_campus(self.profile.get("campus_experience"))
        p["culture_tags"] = _as_list(self.profile.get("culture_tags"))
        p["responsibilities"] = _as_list(self.profile.get("responsibilities"))
        p["hard_skills"] = self._hard_skills()
        p["soft_skills"] = _as_list(self.profile.get("soft_skills"))
        p["bonus"] = _as_list(self.profile.get("bonus"))
        p["skills"] = _as_list(self.profile.get("skills"))
        p["weak_points"] = self._weak_points()
        return p

    # ---------------- 考察点 / 参考要点 ----------------

    def _focus_for(self, section: str, index: int) -> str:
        pool = _SECTION_FOCUS.get(section) or ["综合能力"]
        try:
            i = max(1, int(index))
        except (TypeError, ValueError):
            i = 1
        return pool[(i - 1) % len(pool)]

    def _reference_for(self, section: str) -> list[str]:
        refs = list(_SECTION_REFERENCE.get(section) or [])
        return refs or ["回答的真实性与具体程度"]

    # ---------------- 开场 ----------------

    def _greeting(self) -> str:
        """结合公司风格生成 1-2 句开场白（文档 §6.2.1 第7条）。"""
        company = self.profile.get("company_name") or "我们公司"
        style = (self.profile.get("interview_style") or "结构化面").strip()
        title = self.profile.get("job_title") or "这个岗位"
        if "压力" in style:
            return f"你好，我是{company}{title}方向的面试官。今天我们的节奏会快一些，问题也会比较直接，请如实回答就好。"
        if "温和" in style:
            return f"你好，欢迎参加{company}的面试，我是今天的面试官。我们就像平时聊天一样，你放松说就好。"
        if "追问" in style:
            return f"你好，我是{company}负责{title}的面试官。今天我们聊得会比较细，你不用怕被问住，想到什么就说什么。"
        return f"你好，欢迎参加{company}的面试，我是今天的面试官。我们是结构化面谈，会围绕你的经历和岗位要求聊几个问题，不用紧张。"

    async def opening(self) -> dict:
        """开场白 + 第 1 题，返回 Turn dict。

        整场面试的第 1 题固定是「自我介绍」；但体验增强 §9 的单题特训要把整场
        压在一个薄弱环节上，此时第 1 题必须直接落在该环节——否则 3 题里会白白
        浪费一题在自我介绍上，练不到真正想练的东西。
        """
        first_section = self.focus_section or "自我介绍"
        greeting = self._greeting()
        data = await self._llm_question([], first_section, 1, False)
        if data:
            content = data["content"]
            company = self.profile.get("company_name") or ""
            if company and company not in content:
                content = f"{greeting}{content}"
            question = {
                "content": content,
                "focus": data.get("focus") or self._focus_for(first_section, 1),
                "section": first_section,
                "reference_points": data.get("reference_points") or self._reference_for(first_section),
            }
        else:
            rule = self._rule_question(first_section, 1)
            question = {
                "content": f"{greeting}{rule['content']}",
                "focus": rule["focus"],
                "section": first_section,
                "reference_points": rule["reference_points"],
            }
        turn = self._make_turn(1, first_section, question, is_followup=False)
        turn["content"] = _finalize(turn["content"])
        turn["speak_text"] = media.strip_annotation(turn["content"]) or turn["content"]
        return turn

    # ---------------- 核心推进 ----------------

    async def respond(
        self,
        dialogue: list[dict],
        answer_text: str,
        answer_seconds: int = 0,
        skipped: bool = False,
    ) -> dict | None:
        """给定完整对话与候选人最新回答，返回下一个面试官 Turn；面试结束返回 None。"""
        dialogue = list(dialogue or [])
        try:
            text = answer_text or ""
            try:
                seconds = int(answer_seconds or 0)
            except (TypeError, ValueError):
                seconds = 0
            return await self._respond(dialogue, text, seconds, bool(skipped))
        except Exception as exc:  # noqa: BLE001 任何异常都不能向上抛，规则兜底
            print(f"[interview] respond 异常，走规则兜底: {exc}")
            try:
                return self._safe_next(dialogue)
            except Exception as inner:  # noqa: BLE001
                print(f"[interview] 规则兜底仍失败: {inner}")
                return None

    async def _respond(self, dialogue: list[dict], answer_text: str, answer_seconds: int, skipped: bool) -> dict | None:
        questions = self._interviewer_questions(dialogue)
        if not questions:
            # 对话异常（没有面试官提问）→ 直接给第 1 题，避免流程卡死
            return self._make_turn(1, "自我介绍", self._rule_question("自我介绍", 1), is_followup=False)

        cur_index = max(int(q.get("index") or 0) for q in questions)
        last = questions[-1]
        cur_section = str(last.get("section") or "") or self._section_of(cur_index)

        # 已答完最后一题（反问环节完成）→ 面试结束（文档 §2.3 流程要求）
        if cur_index >= self.total:
            return None

        # ---- 追问判断（文档 §6.2.1 第5条）----
        if not skipped and self._need_followup(questions, cur_index, cur_section, answer_text, answer_seconds):
            question = await self._followup_question(cur_index, cur_section, dialogue)
            return self._make_turn(cur_index, cur_section, question, is_followup=True)

        # ---- 进入下一题：按 plan 配额推进到下一环节 ----
        next_index = cur_index + 1
        section = self._section_of(next_index)
        question = await self._next_question(next_index, section, dialogue)
        return self._make_turn(next_index, section, question, is_followup=False)

    @staticmethod
    def _interviewer_questions(dialogue: list[dict]) -> list[dict]:
        """dialogue 中所有「面试官提问」（role=interviewer 且 index>0）。"""
        out: list[dict] = []
        for item in dialogue:
            if not isinstance(item, dict):
                continue
            if item.get("role") != "interviewer":
                continue
            try:
                idx = int(item.get("index") or 0)
            except (TypeError, ValueError):
                idx = 0
            if idx > 0:
                out.append(item)
        return out

    def _need_followup(
        self,
        questions: list[dict],
        cur_index: int,
        cur_section: str,
        answer_text: str,
        answer_seconds: int,
    ) -> bool:
        """追问触发条件（文档 §6.2.1 第5条）。

        触发需同时满足：
        1. 上一题仍未追问过（最后一个面试官 Turn 不是追问）；
        2. 同一题尚未追问过（dialogue 中没有同 index 的追问）；
        3. 尚未到最后一题（反问环节是收尾，不追问）；
        4. 回答空泛：长度 < 25 字 / 命中 VAGUE_MARKERS / 已上报的作答时长 < 15 秒
           （未上报时长按「不知道时长」处理，不触发追问）。
        """
        if cur_section == "反问环节":
            return False
        if cur_index >= self.total:
            return False
        if questions and questions[-1].get("is_followup"):
            return False
        for q in questions:
            try:
                same = int(q.get("index") or 0) == cur_index
            except (TypeError, ValueError):
                same = False
            if same and q.get("is_followup"):
                return False

        text = (answer_text or "").strip()
        # 压力面试模式（体验增强 §14）：追问更密是本模式的核心特征，
        # 因此把两个阈值都放宽——常规要"明显空泛"才追问，压力面则是"不够具体就追问"。
        pressure = bool(self.profile.get("pressure_mode"))
        min_chars = (
            constants.PRESSURE_FOLLOWUP_MIN_CHARS if pressure else _FOLLOWUP_MIN_CHARS
        )
        min_seconds = (
            constants.PRESSURE_FOLLOWUP_MIN_SECONDS if pressure else _FOLLOWUP_MIN_SECONDS
        )
        if len(text) < min_chars:
            return True
        if any(marker in text for marker in constants.VAGUE_MARKERS):
            return True
        # 只有在客户端**确实上报了**作答时长时才用「时间太短」判定。
        # answer_seconds=0 表示没有计时（纯文本作答 / 前端未计时），
        # 若把它当作「作答不足 15 秒」，每道题都会追问一次，实际轮数翻倍，
        # 与「一场 8-10 题」的题量约定不符。
        try:
            secs = int(answer_seconds or 0)
        except (TypeError, ValueError):
            secs = 0
        if 0 < secs < min_seconds:
            return True
        return False

    # ---------------- 出题：LLM 优先、规则兜底 ----------------

    async def _llm_question(
        self,
        dialogue: list[dict],
        section: str,
        index: int,
        is_followup: bool,
    ) -> dict | None:
        """调用 LLM 出题；任何异常/格式错误返回 None，交给规则题库。"""
        try:
            llm = get_llm()
            messages = prompts.interviewer_next_question(
                self._prompt_profile(), dialogue, section, index, self.total, is_followup
            )
            data = await llm.chat_json(messages, temperature=0.7, max_tokens=700)
        except Exception as exc:  # noqa: BLE001
            print(f"[interview] LLM 出题失败，降级规则题库: {exc}")
            return None
        if not isinstance(data, dict):
            return None
        content = str(data.get("question") or data.get("content") or "").strip()
        if not content:
            return None
        section_out = str(data.get("section") or section).strip()
        if section_out not in constants.INTERVIEW_SECTIONS:
            section_out = section
        refs = [str(x) for x in _as_list(data.get("reference_points"))][:4]
        focus = str(data.get("focus") or "").strip() or self._focus_for(section, index)
        focus = _clean_focus(focus, self._focus_for(section, index))
        return {
            "content": content,
            "focus": focus,
            "section": section_out,
            "reference_points": refs or self._reference_for(section),
        }

    async def _next_question(self, index: int, section: str, dialogue: list[dict]) -> dict:
        data = await self._llm_question(dialogue, section, index, False)
        if data:
            return data
        return self._rule_question(section, index)

    async def _followup_question(self, cur_index: int, cur_section: str, dialogue: list[dict]) -> dict:
        """追问内容：LLM 决策优先，失败则从 FOLLOWUP_PROBES 结合上下文挑。"""
        focus = self._focus_for(cur_section, cur_index)
        try:
            llm = get_llm()
            decision = await llm.chat_json(
                prompts.interviewer_followup_decision(dialogue), temperature=0.5, max_tokens=300
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[interview] LLM 追问决策失败，降级规则: {exc}")
            decision = None
        if isinstance(decision, dict) and decision.get("need_followup"):
            probe = str(decision.get("probe") or "").strip()
            if probe:
                return {
                    "content": self._annotate(probe, focus),
                    "focus": focus,
                    "section": cur_section,
                    "reference_points": self._reference_for(cur_section),
                }
        probes = constants.FOLLOWUP_PROBES or ["能再具体展开讲讲吗？"]
        probe = probes[(max(1, cur_index) - 1) % len(probes)]
        content = f"{self._context_prefix(cur_section, cur_index)}{probe}"
        return {
            "content": self._annotate(content, focus),
            "focus": focus,
            "section": cur_section,
            "reference_points": self._reference_for(cur_section),
        }

    def _context_prefix(self, cur_section: str, cur_index: int) -> str:
        """规则追问的上下文前缀：点名简历真实项目 / 具体技能，避免追问变成万能句。"""
        if cur_section == "项目深挖":
            projects = self._project_titles()
            if projects:
                offset = max(0, int(cur_index) - self._section_start("项目深挖"))
                return f"关于你刚才讲的『{projects[offset % len(projects)]}』这段经历，"
        if cur_section == "专业技能":
            candidates, _ = self._skill_candidates()
            if candidates:
                offset = max(0, int(cur_index) - self._section_start("专业技能"))
                return f"关于你刚提到的 {candidates[offset % len(candidates)]}，"
        return {
            "自我介绍": "关于你刚才的自我介绍，",
            "场景题": "关于你刚才给的方案，",
            "行为面试": "关于你刚才讲的这件事，",
        }.get(cur_section, "")

    # ---------------- 规则题库（降级路径，必须真实可用）----------------

    def _rule_question(self, section: str, index: int) -> dict:
        if section not in _RULE_BANK:
            section = "专业技能"
        if section == "项目深挖":
            raw = self._project_question(index)
        elif section == "专业技能":
            raw = self._skill_question(index)
        elif section == "场景题":
            raw = self._scenario_question(index)
        else:
            bank = _RULE_BANK[section]
            raw = bank[(max(1, int(index)) - 1) % len(bank)]
        content = self._inject(raw)
        focus = self._focus_for(section, index)
        return {
            "content": self._annotate(content, focus),
            "focus": focus,
            "section": section,
            "reference_points": self._reference_for(section),
        }

    def _inject(self, text: str) -> str:
        """注入岗位/公司/行业/业务信息（replace 而非 format，避免占位符残留报错）。"""
        job = self.profile.get("job_title") or "目标岗位"
        company = self.profile.get("company_name") or "我们公司"
        industry = self.profile.get("industry") or "互联网"
        business = self._business_short()
        out = (
            (text or "")
            .replace("{job_title}", str(job))
            .replace("{company}", str(company))
            .replace("{industry}", str(industry))
            .replace("{business}", str(business))
        )
        return _finalize(out)

    def _annotate(self, content: str, focus: str) -> str:
        """在题目后标注考察点（如「（考察：需求拆解能力）」），由 media.strip_annotation 去噪供 TTS。"""
        text = (content or "").strip()
        if not text:
            return text
        if "考察" in text:
            return text
        return _finalize(f"{text}（考察：{focus}）")

    def _project_question(self, index: int) -> str:
        """项目深挖题：必须点名简历里的真实项目名 / 实习公司。

        简历没有实习和项目时（例如低年级同学），退回用校园经历提问，
        比抛出「你简历里最有代表性的一段经历」这种空泛说法更贴近真实面试。
        """
        projects = self._project_titles()
        techs = self._project_techs()
        internships = self._internship_titles()
        if not projects and internships:
            projects = list(internships)
        if not projects:
            projects = _project_titles_from(self.profile.get("campus_experience"))
        if not projects:
            projects = ["你简历里最有代表性的一段经历"]
        offset = max(0, int(index) - self._section_start("项目深挖"))
        project = projects[offset % len(projects)]
        tech = ""
        if techs:
            tech = techs[offset % len(techs)] or ""
        if not tech:
            hard = self._hard_skills()
            tech = hard[0] if hard else "这套技术方案"
        company = internships[offset % len(internships)] if internships else project
        bank = _RULE_BANK["项目深挖"]
        # 模板下标与项目下标共用同一个 0 基偏移：
        # 用 1 基 index 取模板会让本节第一题跳过头一条模板，模板与项目也对不上。
        template = bank[offset % len(bank)]
        return template.replace("{project}", project).replace("{tech}", tech).replace("{company}", company)

    def _skill_candidates(self) -> tuple[list[str], set[str]]:
        """技能候选池：薄弱项（可作技能名的）优先，其次 JD 硬技能，最后简历技能。

        返回 (候选列表, 薄弱项集合)。
        """
        hard = self._hard_skills()
        resume_skills = [str(x) for x in _as_list(self.profile.get("skills"))]
        weak_skills = [w for w in self._weak_points() if _looks_like_skill(w)]
        pool: list[str] = []
        for s in weak_skills + hard + resume_skills:
            if s and s not in pool:
                pool.append(s)
        return pool, set(weak_skills)

    def _skill_question(self, index: int) -> str:
        """专业技能题：点名 JD 硬技能，优先挑薄弱项，并按技能类型切换模板。"""
        candidates, gap_set = self._skill_candidates()
        if not candidates:
            skill = "岗位要求的核心技能"
            is_gap = False
            offset = max(0, int(index) - 1)
        else:
            offset = max(0, int(index) - self._section_start("专业技能"))
            skill = candidates[offset % len(candidates)]
            is_gap = skill in gap_set
        if is_gap:
            bank = _GAP_BANK
        else:
            bank = _SKILL_BANK.get(_skill_category(skill)) or _SKILL_BANK["通用类"]
        template = bank[offset % len(bank)]
        return template.replace("{skill}", skill)

    def industry_bank(self) -> list[str]:
        """当前公司行业对应的专项场景题库；没有匹配时返回空列表。

        行业名在画像里写法不统一（「电商」「短视频」都算互联网），
        因此先经 `INDUSTRY_ALIASES` 归一，再查题库。
        """
        raw = str(self.profile.get("industry") or "").strip()
        if not raw:
            return []
        key = constants.INDUSTRY_ALIASES.get(raw)
        if not key:
            # 画像里可能是「互联网/电商」这种组合写法，做一次包含匹配
            for alias, mapped in constants.INDUSTRY_ALIASES.items():
                if alias and alias in raw:
                    key = mapped
                    break
        if not key:
            return []
        return list(constants.INDUSTRY_QUESTION_BANKS.get(key) or [])

    def _scenario_question(self, index: int) -> str:
        """场景题：命中行业题库时优先用行业专项题，否则回落通用模板。

        行业题是"只有这个行业才会问"的场景（如金融的风控与合规、制造业的良率排查），
        因此它比通用模板更接近真实面试；回落逻辑保证零配置/未知行业也能出题。
        """
        bank = self.industry_bank() or _RULE_BANK["场景题"]
        return bank[(max(1, int(index)) - 1) % len(bank)]

    # ---------------- 组装 Turn ----------------

    def _make_turn(self, index: int, section: str, question: dict, is_followup: bool) -> dict:
        content = str(question.get("content") or "").strip() or "请继续展开讲讲你的这段经历。"
        focus = str(question.get("focus") or "").strip() or self._focus_for(section, index)
        refs = [str(x) for x in _as_list(question.get("reference_points"))][:4]
        if len(refs) < 2:
            for pad in _REFERENCE_PAD:
                if len(refs) >= 2:
                    break
                if pad not in refs:
                    refs.append(pad)
        return {
            "turn_id": new_id(),
            "role": "interviewer",
            "content": content,
            "focus": focus,
            "section": section,
            "index": int(index),
            "total": self.total,
            "is_followup": bool(is_followup),
            "reference_points": refs,
            "speak_text": media.strip_annotation(content) or content,
            "created_at": _now_iso(),
        }

    def _safe_next(self, dialogue: list[dict]) -> dict | None:
        """最后一道保险：用规则题库强行给下一题，仍失败则结束。"""
        questions = self._interviewer_questions(dialogue)
        if not questions:
            return self._make_turn(1, "自我介绍", self._rule_question("自我介绍", 1), is_followup=False)
        try:
            cur_index = max(int(q.get("index") or 0) for q in questions)
        except (TypeError, ValueError):
            cur_index = len(questions)
        if cur_index >= self.total:
            return None
        next_index = cur_index + 1
        section = self._section_of(next_index)
        return self._make_turn(next_index, section, self._rule_question(section, next_index), is_followup=False)

    # ---------------- 结束语 ----------------

    async def closing(self) -> str:
        """2-3 句专业温和的结束语，提及将生成六维评分报告。"""
        try:
            llm = get_llm()
            text = await llm.chat_text(
                prompts.interviewer_closing(self._prompt_profile()), temperature=0.7, max_tokens=200
            )
            if text and text.strip():
                return media.strip_annotation(text.strip())
        except Exception as exc:  # noqa: BLE001
            print(f"[interview] LLM 结束语失败，降级规则: {exc}")
        return self._rule_closing()

    def _rule_closing(self) -> str:
        company = self.profile.get("company_name") or "我们"
        return (
            f"今天的交流就到这里，非常感谢你抽出时间参加{company}的这次面试。"
            "你的回答我们都已经完整记录下来了，接下来系统会为你生成一份六维评分报告，"
            "包括专业能力、逻辑思维、表达沟通、岗位匹配度、应变和文化适配六个维度的分析与提升建议。"
            "希望这份反馈能帮你在后续的面试里表现更好，也祝你求职顺利。"
        )


# ============================================================
# profile 组装（供路由调用）
# ============================================================


def build_profile(
    resume: dict,
    jd: dict,
    company: dict | None,
    match: dict | None,
) -> dict:
    """把 简历 / JD / 公司 / 匹配结果 压成引擎所需的 flat profile dict。

    - resume / jd 为 schemas 中 ResumeProfile / JDProfile 的 dict 形态；
    - company 为 CompanyProfile 的 dict 形态（可为 None）；
    - match 为 MatchResponse 的 dict 形态（可为 None）。
    """
    resume = resume if isinstance(resume, dict) else {}
    jd = jd if isinstance(jd, dict) else {}
    company = company if isinstance(company, dict) else {}
    match = match if isinstance(match, dict) else {}

    rbasic = resume.get("basic") if isinstance(resume.get("basic"), dict) else {}
    jbasic = jd.get("basic") if isinstance(jd.get("basic"), dict) else {}
    req = jd.get("requirements") if isinstance(jd.get("requirements"), dict) else {}

    hard_skills = [str(x) for x in _as_list(req.get("hard_skills"))]
    soft_skills = [str(x) for x in _as_list(req.get("soft_skills"))]
    bonus = [str(x) for x in _as_list(req.get("bonus"))]
    resume_skills = [str(x) for x in _as_list(resume.get("skills"))]
    projects = _as_list(resume.get("projects"))
    internships = _as_list(resume.get("internships"))
    campus_experience = _as_list(resume.get("campus_experience"))
    education = _as_list(resume.get("education"))

    job_title = str(jbasic.get("title") or "").strip() or "目标岗位"
    company_name = str(company.get("name") or "").strip() or "目标公司"

    # ---- 薄弱项：匹配报告中 weak 项 + 简历未体现的 JD 硬技能 ----
    weak_points: list[str] = []
    for item in _as_list(match.get("weak")):
        if isinstance(item, dict):
            text = str(item.get("detail") or item.get("dimension") or "").strip()
        else:
            text = str(item).strip()
        if text and text not in weak_points:
            weak_points.append(text)
    resume_skill_text = " ".join(resume_skills).lower()
    for skill in hard_skills:
        s = str(skill).strip()
        if s and s.lower() not in resume_skill_text and s not in weak_points:
            weak_points.append(s)
    weak_points = weak_points[:6]

    # ---- 匹配策略 ----
    raw_strategy = match.get("strategy") if isinstance(match.get("strategy"), dict) else {}
    strategy = {
        "focus": [str(x) for x in _as_list(raw_strategy.get("focus"))],
        "deep_dive": [str(x) for x in _as_list(raw_strategy.get("deep_dive"))],
        "pressure_test": [str(x) for x in _as_list(raw_strategy.get("pressure_test"))],
    }
    if not strategy["focus"]:
        strategy["focus"] = list(weak_points[:3]) or list(hard_skills[:3])
    if not strategy["deep_dive"]:
        strategy["deep_dive"] = (
            _project_titles_from(projects)[:3]
            or _internship_titles_from(internships)[:3]
            # 没有实习/项目时（例如大一新生），用校园经历顶上，避免深挖环节无题可出
            or _project_titles_from(campus_experience)[:3]
        )
    if not strategy["pressure_test"]:
        strategy["pressure_test"] = list(hard_skills[:2]) or _project_titles_from(projects)[:2]

    return {
        # 公司
        "company_name": company_name,
        "industry": str(company.get("industry") or "互联网").strip() or "互联网",
        "culture_tags": [str(x) for x in _as_list(company.get("culture_tags"))],
        "interview_style": str(company.get("interview_style") or "结构化面").strip() or "结构化面",
        "style_desc": str(company.get("style_desc") or "").strip(),
        "business": str(company.get("business") or "").strip(),
        "company_common_questions": [str(x) for x in _as_list(company.get("common_questions"))],
        # 岗位
        "job_title": job_title,
        "job_department": str(jbasic.get("department") or "").strip(),
        "job_location": str(jbasic.get("location") or "").strip(),
        "responsibilities": [str(x) for x in _as_list(jd.get("responsibilities"))],
        "hard_skills": hard_skills,
        "soft_skills": soft_skills,
        "bonus": bonus,
        "jd_keywords": [str(x) for x in _as_list(jd.get("keywords"))],
        "experience": str(req.get("experience") or "").strip(),
        "degree": str(req.get("degree") or "").strip(),
        # 候选人
        "basic": rbasic,
        "education": education,
        "internships": internships,
        "projects": projects,
        "campus_experience": campus_experience,
        "skills": resume_skills,
        "awards": [str(x) for x in _as_list(resume.get("awards"))],
        "self_evaluation": str(resume.get("self_evaluation") or "").strip(),
        # 分析结果
        "weak_points": weak_points,
        "match_strategy": strategy,
        "match_overall": match.get("overall", 0),
        "match_level": str(match.get("level") or ""),
    }
