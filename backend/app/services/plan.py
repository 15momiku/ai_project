"""7 天个性化提升计划（文档 §5.2.6 / Prompt 清单第 6 条）。

优先级：LLM（`prompts.templates.plan_prompt`）→ 规则生成 `_rule_plan`。
硬性要求：
- 恰好 7 天，每天 2-3 个任务；
- 任务 type ∈ {知识补充, 模拟练习, 复盘总结}；
- 任务必须具体可执行（含产出物），严禁「多练习」「加强学习」这类空话；
- 7 天覆盖薄弱项、先急后缓，最后一天是「全真模拟 + 复盘」。
"""

from __future__ import annotations

from typing import Any

from ..deps import clamp
from ..prompts.templates import plan_prompt
from .llm import get_llm

_ALLOWED_TYPES = ("知识补充", "模拟练习", "复盘总结")
_ALLOWED_SET = set(_ALLOWED_TYPES)

# ============================================================
# 薄弱项分类（文档 §5.2.6：按薄弱项关键词分派模板）
# ============================================================

# 分类顺序即「先急后缓」的默认优先级（越靠前越急）
_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("sql", ["sql", "数据库", "查询", "join", "group by"]),
    ("coding", ["编程", "代码", "算法", "python", "java", "开发", "工程", "coding", "指针"]),
    ("product", ["产品", "需求", "原型", "axure", "prd", "竞品", "功能设计", "需求拆解"]),
    ("data", ["数据", "指标", "分析", "excel", "看板", "留存", "转化", "增长"]),
    ("adaptability", ["应变", "压力", "追问", "临场", "紧张", "被问倒"]),
    ("expression", ["表达", "沟通", "口头禅", "流畅", "跑题", "控场", "语速"]),
    ("industry", ["行业", "公司", "业务", "文化", "价值观", "认知", "赛道"]),
]

_CATEGORY_LABEL = {
    "sql": "SQL 与数据处理",
    "coding": "编程与算法",
    "product": "产品思维与需求分析",
    "data": "数据分析与指标",
    "adaptability": "应变与压力面试",
    "expression": "表达与沟通",
    "industry": "行业与公司认知",
    "general": "岗位核心能力",
}

# 每类薄弱项对应的「一天模板」：theme + 2-3 个任务（type/title/detail/duration_min）
_CATEGORY_DAYS: dict[str, dict[str, Any]] = {
    "sql": {
        "theme": "SQL 基础补强与手写练习",
        "tasks": [
            {
                "type": "知识补充",
                "title": "过一遍 SQL 核心语法（SELECT/JOIN/GROUP BY/窗口函数）",
                "detail": "在牛客网「SQL 篇」按章节各做 5 题，把不会的语法抄进一页速查卡",
                "duration_min": 40,
            },
            {
                "type": "模拟练习",
                "title": "刷完牛客 SQL 高频 20 题",
                "detail": "限时 40 分钟完成 20 题，把错题整理成一页错题清单并写明错因",
                "duration_min": 45,
            },
            {
                "type": "复盘总结",
                "title": "用口头话讲清一道 SQL 题的解题思路",
                "detail": "挑 1 道错题录音 90 秒说清「需求→思路→写法→边界」，回听并记录卡壳点",
                "duration_min": 20,
            },
        ],
    },
    "coding": {
        "theme": "编程基础与手写代码训练",
        "tasks": [
            {
                "type": "知识补充",
                "title": "整理目标岗位常考的 3 个编程知识点",
                "detail": "结合 JD 关键词，把数组/字符串/复杂度等高频考点写成 1 页知识卡片",
                "duration_min": 40,
            },
            {
                "type": "模拟练习",
                "title": "手写完成 5 道高频算法题",
                "detail": "在 LeetCode/牛客各写 5 题，不借助补全，每题计时 20 分钟并记录通过率",
                "duration_min": 50,
            },
            {
                "type": "复盘总结",
                "title": "口述 1 道题的完整解题过程",
                "detail": "录音 2 分钟讲清「思路→边界→复杂度」，对照题解补齐遗漏点",
                "duration_min": 25,
            },
        ],
    },
    "product": {
        "theme": "产品思维与需求拆解补强",
        "tasks": [
            {
                "type": "知识补充",
                "title": "读《人人都是产品经理》需求分析章节并做笔记",
                "detail": "读完第 4 章，用「用户-场景-痛点-方案」四要素写 1 个自己的需求分析笔记",
                "duration_min": 40,
            },
            {
                "type": "模拟练习",
                "title": "拆解 2 道产品面试真题并画出需求框架",
                "detail": "任选 2 道「如何设计 XX 功能」真题，各画一张需求优先级图并写出取舍理由",
                "duration_min": 45,
            },
            {
                "type": "复盘总结",
                "title": "把自己的项目改写成产品语言",
                "detail": "用「背景-目标-方案-数据结果」重写 1 段项目经历，控制在 150 字",
                "duration_min": 25,
            },
        ],
    },
    "data": {
        "theme": "数据分析与指标意识训练",
        "tasks": [
            {
                "type": "知识补充",
                "title": "梳理岗位常用指标与口径",
                "detail": "整理 DAU/留存率/转化率等 5 个指标的算法与适用场景，产出一页指标卡",
                "duration_min": 35,
            },
            {
                "type": "模拟练习",
                "title": "给 2 段项目经历补上量化结果",
                "detail": "用 Excel 把项目数据算成百分比/倍数，每段经历至少写出 1 个可讲的结果数字",
                "duration_min": 40,
            },
            {
                "type": "复盘总结",
                "title": "练习用数据讲故事",
                "detail": "录音 90 秒，用「问题-数据-结论-行动」讲清一个项目的数据成果",
                "duration_min": 20,
            },
        ],
    },
    "adaptability": {
        "theme": "应变能力与压力面试专项",
        "tasks": [
            {
                "type": "知识补充",
                "title": "整理 5 条「不确定问题」的应对话术",
                "detail": "按「承认边界→说明已知→给出求解思路」写出 5 条模板，抄成一页话术卡",
                "duration_min": 30,
            },
            {
                "type": "模拟练习",
                "title": "找同学做 3 轮压力追问练习",
                "detail": "每个回答至少被追问 2 次，练到不出现「嗯…这个…」的长停顿，记录 3 次表现",
                "duration_min": 45,
            },
            {
                "type": "复盘总结",
                "title": "复盘最卡的那道追问",
                "detail": "写「当时为什么答不上来 + 补一个正确回答」各 100 字，并朗读一遍",
                "duration_min": 25,
            },
        ],
    },
    "expression": {
        "theme": "表达沟通与控场训练",
        "tasks": [
            {
                "type": "知识补充",
                "title": "学习「结论先行 + 三点式」表达框架",
                "detail": "看 1 个结构化表达讲解视频，整理出「一句话结论 + 第一/第二/第三」模板",
                "duration_min": 30,
            },
            {
                "type": "模拟练习",
                "title": "录音作答 3 道自我介绍类问题",
                "detail": "每题 60-90 秒并计时，统计「然后/就是/嗯」次数，第二遍压到每百字 1 次以内",
                "duration_min": 40,
            },
            {
                "type": "复盘总结",
                "title": "重写 2 个跑题回答",
                "detail": "把跑题的回答挑出来重写，只保留与 JD 关键词相关的 2 个要点，产出精简版回答",
                "duration_min": 25,
            },
        ],
    },
    "industry": {
        "theme": "行业与目标公司认知补强",
        "tasks": [
            {
                "type": "知识补充",
                "title": "整理目标公司 3 个业务方向与价值观关键词",
                "detail": "浏览官网/公众号，把业务线、价值观关键词各抄成一张卡片，用于回答呼应",
                "duration_min": 35,
            },
            {
                "type": "模拟练习",
                "title": "写并演练「为什么选择这个岗位/公司」",
                "detail": "按「岗位要求→我的匹配点→我能带来的价值」三段式写稿，录音 90 秒演练 2 遍",
                "duration_min": 40,
            },
            {
                "type": "复盘总结",
                "title": "准备 3 个反问题目",
                "detail": "围绕业务方向/团队分工/新人成长各写 1 个反问，避免只问薪资加班",
                "duration_min": 20,
            },
        ],
    },
    "general": {
        "theme": "岗位核心能力巩固",
        "tasks": [
            {
                "type": "知识补充",
                "title": "对照 JD 梳理「要求—我的证据」对照表",
                "detail": "把 JD 要求逐条抄下，每条填「我做过的一个例子 + 一个待补缺口」，产出 1 页表格",
                "duration_min": 40,
            },
            {
                "type": "模拟练习",
                "title": "针对最薄弱的一道题重答 3 遍",
                "detail": "用 STAR 结构重写答案，每遍录音并计时，保留最好的一版作为口述稿",
                "duration_min": 40,
            },
            {
                "type": "复盘总结",
                "title": "整理一页今日错题复盘",
                "detail": "把今天所有错题与卡壳点抄成一页清单，标注明天要优先解决的 2 项",
                "duration_min": 20,
            },
        ],
    },
}


def _detect_category(text: str) -> str:
    low = (text or "").lower()
    for cat, kws in _CATEGORY_RULES:
        if any(k in low for k in kws):
            return cat
    return "general"


def _collect_categories(weak_points: list[str], improvements: list[dict]) -> list[str]:
    """把薄弱项 + 改进项分类，去重并保持「先急后缓」（high 优先）。"""
    cats: list[str] = []
    # 先按 improvements 的优先级收集
    for imp in improvements or []:
        if not isinstance(imp, dict):
            continue
        text = f"{imp.get('dimension', '')} {imp.get('problem', '')} {' '.join(imp.get('actions') or [])}"
        cat = _detect_category(text)
        if cat not in cats:
            cats.append(cat)
    for wp in weak_points or []:
        cat = _detect_category(str(wp))
        if cat not in cats:
            cats.append(cat)
    if not cats:
        cats.append("general")
    return cats


def _make_day(day: int, cat: str) -> dict:
    tpl = _CATEGORY_DAYS.get(cat) or _CATEGORY_DAYS["general"]
    tasks = [dict(t) for t in tpl["tasks"]]
    return {"day": day, "theme": tpl["theme"], "tasks": tasks}


def _final_day(job_title: str, weak_labels: list[str]) -> dict:
    """最后一天固定为「全真模拟 + 复盘」（文档 §5.2.6）。"""
    focus = "、".join(weak_labels) if weak_labels else "六维薄弱项"
    return {
        "day": 7,
        "theme": f"{job_title or '目标岗位'}全真模拟与复盘",
        "tasks": [
            {
                "type": "模拟练习",
                "title": f"完整走一遍 9 题全真模拟（重点：{focus}）",
                "detail": "在本产品开一次完整面试（含 1 轮压力追问），全程录音并生成报告，记录总分与六维分数",
                "duration_min": 45,
            },
            {
                "type": "复盘总结",
                "title": "对比首日与今日报告，写一页进步清单",
                "detail": "把本次六维分数与第一天对比，列出提升最多的维度与仍不达标的维度各 2 条",
                "duration_min": 30,
            },
            {
                "type": "知识补充",
                "title": "补齐复盘暴露的最后 1 个缺口",
                "detail": "针对复盘中分数最低的维度，精读 1 篇资料或做完 10 道对应练习题",
                "duration_min": 30,
            },
        ],
    }


def _advance_day(day: dict) -> dict:
    """同一薄弱项出现多轮时，产出「进阶巩固」版，避免 7 天内容简单重复。"""
    tasks = []
    for t in day["tasks"]:
        nt = dict(t)
        nt["title"] = f"{t['title']}（进阶）"
        nt["detail"] = f"先重读上一轮的产出，再{t['detail']}"
        tasks.append(nt)
    return {"day": day["day"], "theme": f"{day['theme']}（进阶巩固）", "tasks": tasks}


def _rule_plan(weak_points: list[str], job_title: str, improvements: list[dict]) -> list[dict]:
    """规则生成 7 天计划：覆盖薄弱项、先急后缓，最后一天全真模拟 + 复盘。"""
    cats = _collect_categories(weak_points, improvements)
    weak_labels = [_CATEGORY_LABEL.get(c, c) for c in cats][:6]

    days: list[dict] = []
    seen: dict[str, int] = {}
    # Day1..Day6 由薄弱项分类填充（不足则循环补足，重复轮次升级为「进阶巩固」）
    for i in range(6):
        cat = cats[i] if i < len(cats) else cats[i % len(cats)]
        seen[cat] = seen.get(cat, 0) + 1
        day = _make_day(i + 1, cat)
        if seen[cat] > 1:
            day = _advance_day(day)
        days.append(day)

    days.append(_final_day(job_title, weak_labels))
    return days


# ============================================================
# LLM 结果规范化
# ============================================================


def _norm_task(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    ttype = str(raw.get("type") or "").strip()
    if ttype not in _ALLOWED_SET:
        # 归一化常见写法
        if "练习" in ttype or "模拟" in ttype:
            ttype = "模拟练习"
        elif "复盘" in ttype or "总结" in ttype:
            ttype = "复盘总结"
        else:
            ttype = "知识补充"
    title = str(raw.get("title") or "").strip()
    detail = str(raw.get("detail") or raw.get("desc") or "").strip()
    if not title:
        return None
    try:
        dur = int(raw.get("duration_min") or 30)
    except (TypeError, ValueError):
        dur = 30
    dur = int(clamp(dur, 15, 120))
    return {"type": ttype, "title": title, "detail": detail, "duration_min": dur}


def _norm_llm_plan(data: Any, weak_points: list[str], job_title: str) -> list[dict] | None:
    """校验 LLM 生成的计划：必须 7 天、每天 2-3 个任务、type 合法，否则返回 None 走规则版。"""
    if not isinstance(data, dict):
        return None
    raw_days = data.get("days") or data.get("plan7days") or []
    if not isinstance(raw_days, list) or len(raw_days) != 7:
        return None

    days: list[dict] = []
    for i, rd in enumerate(raw_days):
        if not isinstance(rd, dict):
            return None
        theme = str(rd.get("theme") or "").strip()
        tasks_raw = rd.get("tasks") or []
        tasks = [t for t in (_norm_task(x) for x in tasks_raw) if t]
        # 每天补齐/截断到 2-3 个
        if len(tasks) < 2:
            return None
        tasks = tasks[:3]
        days.append({"day": int(rd.get("day") or (i + 1)), "theme": theme or f"Day{i + 1} 提升训练", "tasks": tasks})

    # 强制第 7 天为全真模拟 + 复盘
    cats = _collect_categories(weak_points, [])
    days[6] = _final_day(job_title, [_CATEGORY_LABEL.get(c, c) for c in cats][:6])
    return days


# ============================================================
# 对外主入口
# ============================================================


async def build_plan(weak_points: list[str], job_title: str, improvements: list[dict]) -> list[dict]:
    """生成 7 天提升计划，返回 `PlanDay` 形态的 list（恰好 7 天）。"""
    # 1) LLM 优先
    try:
        llm = get_llm()
        if getattr(llm, "mode", "mock") != "mock":
            data = await llm.chat_json(plan_prompt(weak_points or ["岗位核心能力"], job_title))
            days = _norm_llm_plan(data, weak_points or [], job_title)
            if days:
                return days
    except Exception as exc:  # noqa: BLE001 LLM 失败 → 规则兜底
        print(f"[plan] LLM 计划生成失败，走规则版: {exc}")

    # 2) 规则版兜底（永远能出）
    try:
        return _rule_plan(weak_points or ["岗位核心能力"], job_title, improvements or [])
    except Exception as exc:  # noqa: BLE001 极端兜底
        print(f"[plan] 规则版异常，使用极简计划: {exc}")
        return [_final_day(job_title, []) if i == 6 else _make_day(i + 1, "general") for i in range(7)]
