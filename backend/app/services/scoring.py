"""六维评分引擎（文档 §5.1 六维评分模型 / §5.3 评分 Prompt / §5.4 二次校验与防作弊）。

设计原则（文档 §11 第 4 条：评分准确性是生命线）
------------------------------------------------
1. 优先调用 LLM（`prompts.templates.scoring_prompt`）。
2. LLM 不可用（mock 模式 / 调用失败 / 返回异常）时，**必须**走规则评分引擎 `_rule_score`。
   规则引擎会真实分析文本：术语密度、答案长度、量化数据、方法论、结构化连接词、
   STAR 要素、口头禅密度、跑题检测、JD 技能命中率、追问下的表现、公司文化匹配度……
   绝不返回固定分。
3. 关键维度（专业能力 / 岗位匹配度）LLM 可用时再做一次独立打分（`second_pass_prompt`）取平均。
4. 任何异常都降级，保证"永远能出报告"。
"""

from __future__ import annotations

import re
from typing import Any

from ..constants import DIMENSIONS, FILLER_WORDS, GRADES, STAR_WORDS, STRUCTURE_WORDS
from ..deps import clamp, round1
from ..prompts.templates import scoring_prompt, second_pass_prompt
from .llm import get_llm

# ============================================================
# 正则与词表
# ============================================================

# 量化数据：百分比 / 倍数 / 提升降低 / 万 / ROI / 转化率等（文档 §5.1 专业能力评分依据）
_QUANT_RE = re.compile(
    r"\d+(?:\.\d+)?\s*%|\d+(?:\.\d+)?\s*倍|提升\s*\d+|降低\s*\d+|下降\s*\d+|"
    r"\d+\s*万|\d+\s*亿|ROI|转化率|留存率|复购率|点击率|GMV|DAU|MAU|UV|PV|"
    r"\d+\s*个(?:月|周|天|人|次|题)",
    re.IGNORECASE,
)

# 方法论 / 抽象能力词（专业能力加分）
_METHOD_WORDS = ["方法论", "框架", "模型", "闭环", "拆解", "优先级", "复盘", "迭代", "原理", "机制", "策略"]

# 因果连接词（逻辑思维加分）
_CAUSAL_WORDS = ["因为", "所以", "因此", "由于", "导致", "从而", "才使得"]

# 结论先行标志（逻辑思维）
_CONCLUSION_WORDS = ["我认为", "核心是", "主要是", "最重要的是", "结论", "关键是", "简单来说", "总的来说"]

# 承认不足并补充（应变能力加分）
_HUMILITY_WORDS = ["我确实", "不足", "补充", "我的理解是", "这块我可能", "当时没考虑到", "换个角度", "不太清楚", "没做过"]

# 慌乱词（应变能力减分）
_PANIC_WORDS = ["怎么说", "反正", "然后呢", "呃", "额", "不知道"]

# 求职动机表达（岗位匹配度）
_MOTIVATION_WORDS = ["贵公司", "贵司", "岗位", "业务", "方向", "团队", "公司", "发展", "契合", "匹配"]

# 团队协作 / 价值观表达（文化适配）
_VALUE_WORDS = ["团队", "协作", "用户", "价值观", "长期", "成长", "主动", "学习", "共创", "共赢", "责任心"]

# 明显复制题目 / 模板痕迹（防作弊）
_COPY_MARKERS = ["如下所示", "如下", "略", "同上", "参考上面", "复制", "粘贴", "答案见"]

_ALLOWED_PRIORITY = ("high", "medium", "low")

# 每个维度的改进建议模板（actions 恰好 3 条，resources ≥1 条；严禁"多练习"式空话）
_DIM_IMPROVEMENTS: dict[str, dict[str, Any]] = {
    "professional": {
        "problem": "专业表达停留在业务描述层面，缺少术语、方法论与量化结果的支撑",
        "actions": [
            "把 JD 里的 hard skills 逐条抄成一页《技能对标表》，每条写「我做过的一个例子 + 一个待补缺口」，产出 1 页表格",
            "针对缺口最大的 2 项技能（如 SQL / 数据分析），在牛客网完成对应专项 20 道高频题，把错题整理成一页错题清单并写明错因",
            "把简历里 3 段核心经历改写成「背景-任务-动作-量化结果」四句式，确保每条至少含 1 个数字指标",
        ],
        "resources": [
            "牛客网 SQL / 数据分析专项题库（免费）",
            "《人人都是产品经理》第 4 章：需求分析与优先级",
            "B 站「SQL 从入门到实战」系列公开课",
        ],
    },
    "logic": {
        "problem": "回答缺少结构化框架，结论与论据混在一起，逻辑链条不清晰",
        "actions": [
            "用 STAR 结构把 3 段核心经历各写成 150 字口述稿，开头先给结论、再补 2-3 条论据，产出一份口述稿",
            "每道题作答前用「第一/第二/第三」列提纲，回答时刻意使用这些连接词，录音后回听核对是否成链",
            "挑 5 道行为面试题，各写 3 版答案（纯感受版 / 有框架版 / 带结果版）并对比，删掉纯感受表述",
        ],
        "resources": [
            "牛客网行为面试（STAR）题库",
            "《金字塔原理》第 2 章：结论先行",
            "B 站「结构化表达 MECE」讲解视频",
        ],
    },
    "expression": {
        "problem": "口头禅偏多、回答时长控制不稳，信息密度不足，影响表达流畅度",
        "actions": [
            "录音回答 3 道自我介绍类问题，统计「然后/就是/嗯」出现次数，第二遍把口头禅压到每百字 1 次以内",
            "每题作答先给一句话结论，整体控制在 60-90 秒（约 200-300 字），用手机计时并记录 3 次时长",
            "把跑题的回答挑出来重写，只保留与 JD 关键词相关的 2 个要点，产出一页《精简版回答》",
        ],
        "resources": [
            "B 站「面试表达与控场」系列公开课",
            "微信读书《非暴力沟通》免费章节",
        ],
    },
    "matching": {
        "problem": "回答与岗位 JD 的对应关系不明确，求职动机表述空泛",
        "actions": [
            "把 JD 的 hard_skills 与 keywords 各做一张清单，从简历里为每一条找一条证据，缺失的标注补齐计划",
            "重写「为什么选择这个岗位/公司」回答，写成「岗位要求 → 我的匹配点 → 我能带来的价值」三段式，每段 1 句",
            "在 3 次模拟面试中强制使用 JD 原文关键词（如 SQL、用户增长），复盘时统计每题命中数量",
        ],
        "resources": [
            "目标公司招聘官网 JD 原文（免费）",
            "牛客网对应岗位面经专区（免费）",
        ],
    },
    "adaptability": {
        "problem": "面对追问容易慌乱，缺少「承认边界 + 给出思路」的应对套路",
        "actions": [
            "准备 5 个「我不太确定」场景的应对模板：先承认边界 → 说明已知部分 → 给出求解思路，产出 5 条话术",
            "找同学做 3 轮压力追问练习，每个回答至少被追问 2 次，练到不出现「嗯…这个…」的长停顿",
            "复盘被追问的那道题，写「当时为什么答不上来 + 补一个正确回答」各 100 字",
        ],
        "resources": [
            "牛客网压力面经验帖",
            "B 站「面试被追问怎么接」讲解视频",
        ],
    },
    "culture_fit": {
        "problem": "回答中缺乏与目标公司文化和价值观呼应的具体事例",
        "actions": [
            "整理目标公司官网/公众号的 3 个价值观关键词，为每个关键词准备一个自己的真实故事",
            "在回答中加入团队协作/用户视角的具体例子，每个例子说明「我做了什么 + 团队结果」",
            "模拟面试结尾主动反问 1 个关于团队文化的问题，并记录面试官反馈用于下次调整",
        ],
        "resources": [
            "目标公司官网「关于我们 / 价值观」页面",
            "B 站目标公司校招宣讲会回放",
        ],
    },
}


# ============================================================
# 等级映射（文档 §5.1.1）
# ============================================================


def grade_of(score: float) -> dict:
    """按 `constants.GRADES` 返回 {grade, desc, offer}：按 min 降序匹配第一个 score >= min 的档。"""
    s = float(score or 0.0)
    for g in sorted(GRADES, key=lambda x: x["min"], reverse=True):
        if s >= g["min"]:
            return {"grade": g["grade"], "desc": g["desc"], "offer": g["offer"]}
    last = GRADES[-1]
    return {"grade": last["grade"], "desc": last["desc"], "offer": last["offer"]}


# ============================================================
# 逐题记录抽取（文档 §5.2.3 逐题分析）
# ============================================================

_SKIP_TOKENS = {"", "（跳过本题）", "(跳过本题)", "（跳过）"}


def _to_int(value: Any, default: int = 0) -> int:
    """安全转 int：脏数据（None / 非数字字符串 / 浮点字符串）一律回落默认值。

    历史会话里出现过 `index="3"`、`answer_seconds=""` 这类脏值，
    直接用 int() 会抛异常并让**整份报告**降级为兜底分数，代价过大。
    """
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _estimate_seconds(text: str) -> int:
    """无真实作答时长时按中文约 4 字/秒估算。"""
    if not text:
        return 0
    return max(3, int(round(len(text) / 4.0)))


def build_question_records(dialogue: list[dict]) -> list[dict]:
    """从 dialogue 里抽取「面试官题 + 候选人回答」配对。

    - `role="interviewer"` 且 `index>0` → 一道题；若 index 与上一题相同，视为追问，合并到同一题，
      追问文本追加到 question 里并置 `is_followup=True`。
    - 紧随其后的 `role="candidate"` 作为该题 answer（多条拼接）。
    - `role="system"` → 该题标记跳过，answer 记为「（跳过本题）」。
    - 稳健处理没有回答的题（answer 为空）。
    """
    records: list[dict] = []
    current: dict | None = None

    for turn in dialogue or []:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        content = str(turn.get("content") or "").strip()
        if role == "interviewer":
            idx = _to_int(turn.get("index"), 0)
            if idx <= 0:
                # 开场白 / 结束语（index=0）不参与逐题分析
                continue
            is_followup = bool(turn.get("is_followup"))
            if current is not None and idx == current["index"]:
                # 同题追问：合并
                current["question"] = (current["question"] + "\n追问：" + content).strip()
                current["is_followup"] = True
                continue
            current = {
                "index": idx,
                "section": str(turn.get("section") or ""),
                "focus": str(turn.get("focus") or ""),
                "question": content,
                "answer": "",
                "answer_seconds": 0,
                "is_followup": is_followup,
                "retake": False,
                "hint_used": False,
            }
            records.append(current)

        elif role == "candidate":
            if current is None:
                continue
            text = content
            secs = turn.get("answer_seconds")
            if current["answer"]:
                current["answer"] = (current["answer"] + "\n" + text).strip()
            else:
                current["answer"] = text
            if isinstance(secs, (int, float)) and secs > 0:
                current["answer_seconds"] = max(current["answer_seconds"], int(secs))
            # 体验增强 §4：重录 / 依赖提示的标记跟随候选人回合，
            # 报告需要把它们归到**具体某一题**（只给一个总数无法指导用户改进）。
            if turn.get("retake"):
                current["retake"] = True
            if turn.get("hint_used"):
                current["hint_used"] = True

        elif role == "system":
            if current is None:
                continue
            if not current["answer"]:
                current["answer"] = "（跳过本题）"
                current["answer_seconds"] = 0

    # 收尾：补时长估算
    for rec in records:
        if rec["answer"] and rec["answer"] not in _SKIP_TOKENS and rec["answer_seconds"] <= 0:
            rec["answer_seconds"] = _estimate_seconds(rec["answer"])
    return records


# ============================================================
# 规则评分引擎内部工具
# ============================================================


def _hits(text: str, words: list[str] | tuple[str, ...]) -> int:
    if not text:
        return 0
    return sum(text.count(w) for w in words if w)


def _quote(text: str, limit: int = 40) -> str:
    """截取回答原文片段，每条 ≤40 字。"""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "…"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？!?；;\n]", text or "")
    return [p.strip() for p in parts if p.strip()]


def _answers_of(records: list[dict]) -> list[str]:
    out: list[str] = []
    for r in records:
        a = (r.get("answer") or "").strip()
        if a and a not in _SKIP_TOKENS:
            out.append(a)
    return out


def build_ctx(resume: dict, jd: dict, company: dict | None) -> dict:
    """预抽取评分所需的岗位/简历/公司关键词。"""
    req = (jd or {}).get("requirements") or {}
    hard_skills = [str(s) for s in (req.get("hard_skills") or []) if s]
    keywords = [str(s) for s in ((jd or {}).get("keywords") or []) if s]
    resume_skills = [str(s) for s in ((resume or {}).get("skills") or []) if s]
    culture_tags = [str(s) for s in ((company or {}).get("culture_tags") or []) if s]
    return {
        "hard_skills": hard_skills,
        "keywords": keywords,
        "resume_skills": resume_skills,
        "culture_tags": culture_tags,
        "professional_kw": DIMENSIONS[0]["keywords"],
    }


def _answer_quality(text: str, rec: dict, ctx: dict) -> float:
    """单题规则质量分（1-9.6）：用于逐题得分与应变能力对比。"""
    t = (text or "").strip()
    if not t or t in _SKIP_TOKENS:
        return 2.5

    score = 5.4
    ln = len(t)

    # 长度
    if ln < 15:
        score -= 1.6
    elif ln < 40:
        score -= 0.9
    elif ln <= 300:
        score += 0.6
    elif ln > 400:
        score -= 0.4

    # 专业术语 + JD 硬技能命中
    term = _hits(t, ctx["professional_kw"]) + _hits(t, ctx["hard_skills"])
    score += min(term * 0.35, 1.5)

    # 结构化连接词
    struct = _hits(t, STRUCTURE_WORDS)
    score += min(struct * 0.25, 1.0)

    # 量化数据
    if _QUANT_RE.search(t):
        score += 0.5

    # STAR 要素
    if _hits(t, STAR_WORDS) >= 1:
        score += 0.3

    # 口头禅密度
    filler = _hits(t, FILLER_WORDS)
    density = filler / max(ln / 100.0, 1.0)
    score -= min(density * 0.3, 1.2)

    # 作答时长过短
    secs = int(rec.get("answer_seconds") or 0)
    if 0 < secs < 10:
        score -= 0.8

    return clamp(score, 1.0, 9.6)


def _quote_with_number(answer: str) -> str:
    m = _QUANT_RE.search(answer or "")
    if not m:
        return ""
    start = max(0, m.start() - 12)
    return _quote((answer[start:]).strip())


# ============================================================
# 六维规则打分（核心）
# ============================================================


def _score_professional(answers: list[str], ctx: dict) -> tuple[float, str, list[str]]:
    """专业能力：术语密度 + 答案长度 + 量化数据 + 方法论（文档 §5.1）。"""
    n = max(len(answers), 1)
    text = "\n".join(answers)
    avg_len = sum(len(a) for a in answers) / n if answers else 0

    term_hits = sum(_hits(a, ctx["professional_kw"]) for a in answers)
    hard_hits = sum(_hits(a, ctx["hard_skills"]) for a in answers)
    density = (term_hits + hard_hits) / n
    density_delta = min(density * 0.22, 1.3)

    if not answers:
        len_delta = -1.5
    elif avg_len < 40:
        len_delta = -1.1
    elif avg_len < 80:
        len_delta = -0.4
    elif avg_len <= 320:
        len_delta = 0.3
    else:
        len_delta = -0.5  # 过长 → 简洁度扣分

    quant_count = len(_QUANT_RE.findall(text))
    quant_delta = 0.75 if quant_count >= 3 else (0.4 if quant_count >= 1 else -0.35)

    method_hits = _hits(text, _METHOD_WORDS)
    method_delta = 0.5 if method_hits >= 2 else (0.3 if method_hits >= 1 else -0.45)

    score = clamp(6.0 + density_delta + len_delta + quant_delta + method_delta, 1.0, 9.6)

    comment = (
        f"面试中命中专业术语/岗位技能 {term_hits + hard_hits} 次，量化数据 {quant_count} 处，"
        f"平均单题回答 {int(avg_len)} 字。"
        + ("能主动给出数字结果与方法论，专业表达较扎实。" if quant_delta > 0 and method_delta > 0
           else "回答偏业务描述，缺少可验证的数据和方法论支撑，深度有待加强。")
    )
    evidence = _pick_evidence(answers, ctx["professional_kw"] + ctx["hard_skills"], prefer_quant=True)
    return round1(score), comment, evidence


def _score_logic(answers: list[str], text: str) -> tuple[float, str, list[str]]:
    """逻辑思维：结构化连接词 + STAR + 结论先行 + 因果链 + 句子长度。"""
    struct_hits = _hits(text, STRUCTURE_WORDS)
    star_hits = _hits(text, STAR_WORDS)
    causal_hits = _hits(text, _CAUSAL_WORDS)

    struct_delta = min(struct_hits * 0.22, 1.2)
    star_delta = min(star_hits * 0.25, 0.8)
    causal_delta = 0.5 if causal_hits >= 2 else (0.25 if causal_hits >= 1 else 0.0)

    # 结论先行：首句较短或含结论标志词
    conclusion_cnt = 0
    long_sentence_cnt = 0
    total_sent = 0
    for a in answers:
        sents = _sentences(a)
        if not sents:
            continue
        total_sent += len(sents)
        first = sents[0]
        if len(first) <= 22 or any(w in first for w in _CONCLUSION_WORDS):
            conclusion_cnt += 1
        long_sentence_cnt += sum(1 for s in sents if len(s) > 65)

    n = max(len(answers), 1)
    conclusion_ratio = conclusion_cnt / n
    if conclusion_ratio >= 0.5:
        concl_delta = 0.5
    elif conclusion_ratio >= 0.25:
        concl_delta = 0.2
    else:
        concl_delta = -0.35

    if struct_hits == 0:
        struct_delta -= 0.45
    long_ratio = long_sentence_cnt / max(total_sent, 1)
    len_delta = -0.6 if long_ratio > 0.4 else (0.2 if long_ratio < 0.15 else 0.0)

    score = clamp(6.0 + struct_delta + star_delta + causal_delta + concl_delta + len_delta, 1.0, 9.6)

    comment = (
        f"结构化连接词命中 {struct_hits} 次，STAR 要素 {star_hits} 处，因果链 {causal_hits} 处，"
        f"{int(conclusion_ratio * 100)}% 的回答做到了结论先行。"
        + ("回答层次清晰、有框架感。" if struct_hits >= 2 and conclusion_ratio >= 0.4
           else "回答有内容但框架感不足，建议先给结论再用「第一/第二」展开论据。")
    )
    evidence = _pick_evidence(answers, STRUCTURE_WORDS + STAR_WORDS)
    return round1(score), comment, evidence


def _score_expression(answers: list[str], records: list[dict], ctx: dict) -> tuple[float, str, list[str]]:
    """表达沟通：口头禅密度 + 过短 + 跑题 + 作答时长（文档 §5.1 表达维度）。"""
    text = "\n".join(answers)
    chars = max(len(text), 1)
    filler_hits = _hits(text, FILLER_WORDS)
    filler_density = filler_hits / (chars / 100.0)
    filler_delta = -min(filler_density * 0.25, 1.8)

    n = max(len(records), 1)
    short_ratio = sum(1 for r in records if r.get("answer") and len(r["answer"]) < 30) / n
    # 跑题：答案既不命中 JD 关键词也不命中简历关键词
    offtopic = 0
    answered = 0
    for r in records:
        a = (r.get("answer") or "").strip()
        if not a or a in _SKIP_TOKENS:
            continue
        answered += 1
        if _hits(a, ctx["hard_skills"] + ctx["keywords"] + ctx["resume_skills"] + ctx["professional_kw"]) == 0:
            offtopic += 1
    offtopic_ratio = offtopic / max(answered, 1)
    # 时长不足
    short_time = sum(1 for r in records if r.get("answer") and 0 < int(r.get("answer_seconds") or 0) < 15)
    short_time_ratio = short_time / n
    long_ratio = sum(1 for r in records if r.get("answer") and len(r["answer"]) > 400) / n

    pos = 0.0
    if not answers:
        pos = -1.2
    else:
        avg_len = chars / max(answered, 1)
        if 60 <= avg_len <= 320:
            pos += 0.5
        if filler_hits == 0:
            pos += 0.5
        if _hits(text, STRUCTURE_WORDS) > 0:
            pos += 0.3
        if any(w in text for w in ("总结", "简单来说", "核心是", "举个例子")):
            pos += 0.3

    score = clamp(
        6.0 + pos + filler_delta - short_ratio * 1.0 - offtopic_ratio * 1.0
        - short_time_ratio * 0.8 - long_ratio * 0.4,
        1.0,
        9.6,
    )

    comment = (
        f"口头禅出现 {filler_hits} 次（每百字 {round(filler_density, 2)} 次），过短回答 {int(short_ratio * 100)}%，"
        f"疑似跑题 {int(offtopic_ratio * 100)}%，准备不足（<15 秒）{int(short_time_ratio * 100)}%。"
        + ("表达流畅、信息密度合适。" if filler_delta > -0.5 and offtopic_ratio < 0.2
           else "口头禅与跑题影响了表达质量，建议先给结论、控制到 60-90 秒并紧扣岗位关键词。")
    )
    evidence = _pick_evidence(answers, ctx["hard_skills"] + ctx["keywords"] + ctx["professional_kw"])
    return round1(score), comment, evidence


def _score_matching(answers: list[str], ctx: dict) -> tuple[float, str, list[str]]:
    """岗位匹配度：JD 硬技能命中率 + 关键词命中率 + 求职动机（文档 §5.1 匹配维度）。"""
    text = "\n".join(answers)
    hard = ctx["hard_skills"]
    kw = ctx["keywords"]
    hard_hit = [s for s in hard if s and s in text]
    kw_hit = [s for s in kw if s and s in text]
    hard_rate = len(hard_hit) / max(len(hard), 1)
    kw_rate = len(kw_hit) / max(len(kw), 1)
    motiv_hits = _hits(text, _MOTIVATION_WORDS)
    resume_hit = [s for s in ctx["resume_skills"] if s and s in text]

    delta = 0.0
    if hard:
        delta += hard_rate * 1.6
        if hard_rate < 0.2:
            delta -= 0.8
    else:
        delta += 0.4
    if kw:
        delta += kw_rate * 0.9
    delta += 0.6 if motiv_hits >= 2 else (0.3 if motiv_hits >= 1 else -0.4)
    if resume_hit:
        delta += 0.4

    score = clamp(6.0 + delta, 1.0, 9.6)
    comment = (
        f"JD 硬技能命中 {len(hard_hit)}/{len(hard) or 0}（{int(hard_rate * 100)}%），"
        f"关键词命中 {len(kw_hit)}/{len(kw) or 0}，求职动机表达 {motiv_hits} 处。"
        + ("经历与岗位要求对应清晰，动机合理。" if hard_rate >= 0.5 and motiv_hits >= 1
           else "回答与 JD 的对应关系不够直接，建议主动把经历改写成岗位要求的语言，并明确求职动机。")
    )
    evidence = _pick_evidence(answers, hard + kw)
    return round1(score), comment, evidence


def _score_adaptability(records: list[dict], ctx: dict) -> tuple[float, str, list[str]]:
    """应变能力：追问下的表现 / 承认不足 / 慌乱词（文档 §5.1 应变维度）。"""
    followups = [r for r in records if r.get("is_followup")]
    if not followups:
        return (
            6.5,
            "本场面试未触发追问，应变能力按中性 6.5 分计。建议主动准备「被追问数据/细节」的场景，"
            "练习先承认边界再给出求解思路的应对模板。",
            _pick_evidence(_answers_of(records), _HUMILITY_WORDS) or _first_evidence(records),
        )

    fq_scores = [_answer_quality(r.get("answer") or "", r, ctx) for r in followups]
    others = [r for r in records if not r.get("is_followup") and (r.get("answer") or "").strip() not in _SKIP_TOKENS]
    other_scores = [_answer_quality(r.get("answer") or "", r, ctx) for r in others]
    fq_avg = sum(fq_scores) / max(len(fq_scores), 1)
    other_avg = sum(other_scores) / max(len(other_scores), 1) if other_scores else 6.0

    text = "\n".join((r.get("answer") or "") for r in followups)
    humility = _hits(text, _HUMILITY_WORDS)
    panic = _hits(text, _PANIC_WORDS) + _hits(text, FILLER_WORDS)

    score = 6.5 + (fq_avg - 6.0) * 0.7 + (fq_avg - other_avg) * 0.5
    if humility >= 1:
        score += 0.5
    score -= min(panic * 0.15, 0.8)

    score = clamp(score, 1.0, 9.6)
    comment = (
        f"本场触发 {len(followups)} 次追问，追问题平均质量 {round1(fq_avg)} 分，"
        f"较非追问题 {round1(other_avg)} 分{'更高' if fq_avg >= other_avg else '偏低'}；"
        f"承认不足/补充表述 {humility} 处，慌乱词 {panic} 处。"
        + ("压力下表现稳定、应对有章法。" if fq_avg >= other_avg and humility >= 1
           else "追问下容易出现信息量下降或慌乱，建议准备「承认边界—给出思路」的应对套路。")
    )
    evidence = _pick_evidence([r.get("answer") or "" for r in followups], _HUMILITY_WORDS) or _first_evidence(followups)
    return round1(score), comment, evidence


def _score_culture(answers: list[str], ctx: dict) -> tuple[float, str, list[str]]:
    """文化适配：公司文化标签匹配 + 团队协作/价值观表达（文档 §5.1 文化维度）。"""
    text = "\n".join(answers)
    tags = ctx["culture_tags"]
    tag_hits = _hits(text, tags)
    value_hits = _hits(text, _VALUE_WORDS)

    if not tags:
        base, note = 6.5, "公司文化标签未提供，按中性基准评估。"
    else:
        base = 6.0
        note = (
            f"直接呼应公司文化标签 {tag_hits} 次。"
            if tag_hits
            else "回答未直接复述公司文化标签词。"
        )
        if tag_hits + value_hits == 0:
            base -= 0.6

    score = clamp(base + min(tag_hits * 0.5, 1.2) + min(value_hits * 0.12, 1.2), 1.0, 9.6)
    comment = (
        note + f"团队协作/价值观类表达 {value_hits} 处。"
        + ("回答能自然呼应公司的工作风格与价值观。" if tag_hits + value_hits >= 2
           else "较少体现与目标公司文化的呼应，建议用真实经历说明自己与团队风格的契合点。")
    )
    evidence = _pick_evidence(answers, tags + _VALUE_WORDS)
    return round1(score), comment, evidence


def _first_evidence(records: list[dict]) -> list[str]:
    for r in records:
        a = (r.get("answer") or "").strip()
        if a and a not in _SKIP_TOKENS:
            return [_quote(a)]
    return []


def _pick_evidence(answers: list[str], words: list[str], prefer_quant: bool = False) -> list[str]:
    """挑选引用原文片段：优先命中关键词/量化数据的句子，每条 ≤40 字，至少 1 条。"""
    out: list[str] = []
    # 1) 命中关键词的句子
    for a in answers:
        for s in re.split(r"[。！？!?；;\n]", a):
            s = s.strip()
            if not s:
                continue
            if any(w and w in s for w in words):
                out.append(_quote(s))
                if len(out) >= 2:
                    break
        if len(out) >= 2:
            break
    # 2) 补量化句子
    if prefer_quant or not out:
        for a in answers:
            q = _quote_with_number(a)
            if q and q not in out:
                out.append(q)
                break
    # 3) 兜底：最相关的一句（最长的那句信息量最大）
    if not out and answers:
        best = max(answers, key=lambda x: len(x or ""))
        if best:
            out.append(_quote(best))
    return out[:3]


# ============================================================
# 逐题 good / improve / reference
# ============================================================


def _question_good(text: str, ctx: dict) -> list[str]:
    good: list[str] = []
    if _QUANT_RE.search(text):
        q = _quote_with_number(text)
        good.append(f"给出了量化结果：\u201c{q}\u201d")
    struct = [w for w in STRUCTURE_WORDS if w in text]
    if struct:
        good.append(f"用「{'/'.join(struct[:2])}」分层陈述，条理清晰")
    star = [w for w in STAR_WORDS if w in text]
    if star:
        good.append("描述包含背景/动作/结果，具备 STAR 要素")
    terms = [w for w in (ctx["professional_kw"] + ctx["hard_skills"]) if w in text]
    if terms:
        good.append(f"提到「{'、'.join(terms[:2])}」等专业/岗位术语，体现岗位知识")
    if len(text) >= 80 and not good:
        good.append("回答充实，信息量足够，能展开说明细节")
    if any(w in text for w in _HUMILITY_WORDS):
        good.append("面对追问坦诚承认边界并补充说明，态度可靠")
    if not good:
        good.append("正面回应了问题，没有跑题或回避")
    return good[:3]


def _question_improve(rec: dict, text: str, ctx: dict) -> list[str]:
    improve: list[str] = []
    if not text or text in _SKIP_TOKENS:
        improve.append("未作答/跳过本题，无法评估该题表现")
        return improve[:3]
    if len(text) < 40:
        improve.append("回答偏短，缺少细节与数据支撑，建议展开到 60-90 秒")
    if not _QUANT_RE.search(text):
        improve.append("全程没有量化结果，说服力不足，建议补充一个具体的数字指标")
    fillers = [w for w in FILLER_WORDS if w in text]
    if fillers:
        cnt = _hits(text, fillers)
        improve.append(f"出现「{'、'.join(fillers[:2])}」等口头禅 {cnt} 次，影响流畅度")
    if _hits(text, STRUCTURE_WORDS) == 0:
        improve.append("缺少「第一/第二」等结构化连接词，逻辑链条不明显")
    if _hits(text, STAR_WORDS) == 0:
        improve.append("未说明具体动作与结果，建议补 STAR 结构")
    if _hits(text, ctx["hard_skills"] + ctx["keywords"] + ctx["resume_skills"]) == 0:
        improve.append("回答未紧扣岗位要求，有跑题倾向，建议回到 JD 关键词")
    if len(text) > 400:
        improve.append("回答过长，建议先给结论再展开，控制在 90 秒内")
    secs = int(rec.get("answer_seconds") or 0)
    if 0 < secs < 15:
        improve.append(f"作答仅约 {secs} 秒，准备不足，建议留足思考与展开时间")
    if not improve:
        improve.append("整体不错，可进一步补充量化结果与复盘反思以提升说服力")
    return improve[:3]


def _question_reference(rec: dict, ctx: dict) -> str:
    focus = rec.get("focus") or "该题考察点"
    section = rec.get("section") or ""
    hard = "、".join(ctx["hard_skills"][:2]) or "岗位核心技能"
    lines = [
        f"参考思路：先给一句话结论，说明你在「{focus}」上的做法或观点。",
        "用「背景→我的动作→量化结果」三段展开，至少给出 1 个数字指标（如提升 30%、覆盖 500 名用户）。",
        f"如果与 {hard} 相关，主动把经历改写成岗位要求的语言，体现可迁移能力。",
        "最后用一句话复盘得失（哪里做得好、下次怎么改进），完成闭环。",
    ]
    if section == "自我介绍":
        lines[1] = "按「学校专业 → 与岗位相关的 2 段经历 → 一句求职动机」组织，控制在 90 秒内。"
    elif section == "反问环节":
        lines = [
            "参考思路：提问要体现你已做过功课，而非泛泛而问。",
            "优先问「业务方向 / 团队分工 / 新人成长路径」三类问题，各准备 1 条。",
            "避免一上来就问薪资与加班，先展示对岗位的思考再谈待遇。",
        ]
    elif section == "行为面试":
        lines[0] = f"参考思路：用 STAR 讲一个真实案例，先说「{focus}」相关的结论。"
    return " ".join(lines)


# ============================================================
# 评分归因（体验增强 §6）
# ============================================================
# 报告页要让用户「点开结论就能看到出处」。这里把各维度**实际看过的可数事实**
# 摆出来，用的就是 `_score_*` 里同一批常量词表，因此归因与得分口径必然一致；
# 本函数只做「把数字列出来」，不重新计算任何分数。


def basis_of(key: str, records: list[dict], ctx: dict) -> list[str]:
    """某一维度的评分依据（人话条目）。数据缺失时返回空列表，前端不展示该区块。"""
    answers = [
        (r.get("answer") or "").strip()
        for r in records
        if (r.get("answer") or "").strip() and (r.get("answer") or "").strip() not in _SKIP_TOKENS
    ]
    text = "\n".join(answers)
    if not text:
        return []
    out: list[str] = []

    if key == "professional":
        terms = _hits(text, ctx.get("professional_kw", []) + ctx.get("hard_skills", []))
        out.append(f"专业/岗位术语命中 {terms} 次")
        out.append(f"量化结果 {len(_QUANT_RE.findall(text))} 处")
    elif key == "logic":
        out.append(f"结构化连接词（第一/其次/最后）{_hits(text, STRUCTURE_WORDS)} 次")
        out.append(f"STAR 要素（背景/动作/结果）{_hits(text, STAR_WORDS)} 处")
    elif key == "expression":
        secs = [int(r.get("answer_seconds") or 0) for r in records if int(r.get("answer_seconds") or 0) > 0]
        out.append(f"口头禅（然后/就是/嗯）{_hits(text, FILLER_WORDS)} 次")
        if secs:
            out.append(f"单题平均作答 {int(sum(secs) / len(secs))} 秒")
    elif key == "matching":
        hard = ctx.get("hard_skills") or []
        hit = [s for s in hard if s and s in text]
        if hard:
            out.append(f"JD 硬技能命中 {len(hit)}/{len(hard)} 项")
        out.append(f"JD 关键词命中 {_hits(text, ctx.get('keywords') or [])} 次")
        out.append(f"简历技能被回扣 {len([s for s in (ctx.get('resume_skills') or []) if s and s in text])} 项")
    elif key == "adaptability":
        followups = len([r for r in records if r.get("is_followup")])
        out.append(f"本场追问 {followups} 次")
        out.append(f"承认边界/补充说明的表述 {_hits(text, DIMENSIONS[4]['keywords'])} 处")
    elif key == "culture_fit":
        tags = ctx.get("culture_tags") or []
        hit = [t for t in tags if t and t in text]
        if tags:
            out.append(f"公司文化标签命中 {len(hit)}/{len(tags)} 个")
        out.append(f"团队/协作/价值观相关表述 {_hits(text, DIMENSIONS[5]['keywords'])} 处")
    return out


def span_of(answer: str, ctx: dict) -> list[str]:
    """该题回答里**支撑评分结论**的原文片段（供前端高亮「出处」）。

    只做「找出被计分依据引用的那几处」，不重新判断质量：
    命中术语 + 含量化数据的句子，最多 4 条，去重保序。
    """
    text = (answer or "").strip()
    if not text or text in _SKIP_TOKENS:
        return []
    out: list[str] = []

    # 1) 含量化数据的句子（评分里权重最高的正面信号）
    for sentence in re.split(r"[。；;\n]", text):
        s = sentence.strip()
        if s and _QUANT_RE.search(s):
            out.append(s[:80])

    # 2) 命中的岗位/专业术语（点明「是因为这些词加了分」）
    words = (ctx.get("professional_kw") or []) + (ctx.get("hard_skills") or []) + (
        ctx.get("keywords") or []
    )
    hits = [w for w in dict.fromkeys(words) if w and w in text]
    if hits:
        out.append("命中术语：" + "、".join(hits[:8]))

    seen: set[str] = set()
    uniq: list[str] = []
    for item in out:
        if item and item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq[:4]


# ============================================================
# 优势 / 改进项
# ============================================================


def _build_strengths(dim_scores: dict[str, float], answers: list[str], ctx: dict) -> list[dict]:
    ranked = sorted(dim_scores.items(), key=lambda kv: kv[1], reverse=True)
    name_of = {d["key"]: d["name"] for d in DIMENSIONS}
    out: list[dict] = []

    for key, sc in ranked[:2]:
        ev = _pick_evidence(answers, ctx["professional_kw"] if key == "professional" else [])
        if not ev:
            ev = _pick_evidence(answers, ctx["hard_skills"] + ctx["keywords"]) or [_quote(answers[0]) if answers else ""]
        out.append(
            {
                "title": f"{name_of[key]}表现突出（{round1(sc)} 分）",
                "detail": f"该维度得分 {round1(sc)} 分，高于其余维度，是本次面试的核心竞争力，建议在后续面试中继续放大。",
                "evidence": ev[0] if ev else "",
            }
        )

    # 第三条：量化数据意识
    quant_answer = next((a for a in answers if _QUANT_RE.search(a)), "")
    if quant_answer:
        out.append(
            {
                "title": "有量化数据意识",
                "detail": "回答中主动给出数字结果，能把经历转化为可衡量的成果，明显优于只会讲过程的候选人。",
                "evidence": _quote_with_number(quant_answer) or _quote(quant_answer),
            }
        )
    else:
        out.append(
            {
                "title": "经历与岗位存在可迁移的能力线索",
                "detail": "简历中的项目/实习与岗位要求存在交集，建议把这些交集显性化成「岗位要求 → 我的证据」的对照表述。",
                "evidence": _quote(answers[0]) if answers else "",
            }
        )

    # 不变式守卫：报告固定给 3 条核心优势（dim_scores 目前恒为 6 个维度，
    # 因此上面 2+1 条刚好凑满；维度数量变化时这里兜底）。
    while len(out) < 3:
        out.append(
            {
                "title": "面试完成度良好",
                "detail": "完成了全部题目的作答，配合度与稳定性为后续提升打下基础。",
                "evidence": "",
            }
        )
    return out[:3]


def _priority_of(score: float) -> str:
    if score < 6.0:
        return "high"
    if score < 7.0:
        return "medium"
    return "low"


def _build_improvements(dim_scores: dict[str, float], records: list[dict], ctx: dict) -> list[dict]:
    name_of = {d["key"]: d["name"] for d in DIMENSIONS}
    ordered = sorted(dim_scores.items(), key=lambda kv: kv[1])
    candidates = [(k, s) for k, s in ordered if s < 8.0][:3]
    if not candidates:
        candidates = ordered[:1]

    out: list[dict] = []
    for key, sc in candidates:
        tpl = _DIM_IMPROVEMENTS.get(key, _DIM_IMPROVEMENTS["professional"])
        # problem 必须引用回答原文
        weakest = min(
            (r for r in records if (r.get("answer") or "").strip() not in _SKIP_TOKENS),
            key=lambda r: _answer_quality(r.get("answer") or "", r, ctx),
            default=None,
        )
        quote = _quote((weakest.get("answer") or "")) if weakest else ""
        problem = f"「{name_of[key]}」仅得 {round1(sc)} 分：{tpl['problem']}。"
        if quote:
            problem += f"典型回答：「{quote}」"
        actions = list(tpl["actions"])[:3]
        # 不变式守卫：README 附录 B 承诺「每条改进项含 3 个可执行步骤」，
        # 模板若被改成不足 3 条，这里补齐而不是让报告少一条。
        while len(actions) < 3:
            actions.append("把上述两项产出整理进一页复盘清单，并在下一次模拟面试前重读一遍")
        out.append(
            {
                "priority": _priority_of(sc),
                "dimension": name_of[key],
                "problem": problem,
                "evidence": quote,
                "actions": actions,
                "resources": list(tpl["resources"]),
            }
        )
    # 按优先级 high>medium>low 排序
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda x: order.get(x.get("priority", "low"), 3))
    return out[:3]


# ============================================================
# 防作弊（文档 §5.4）
# ============================================================


def _build_cheating_flags(records: list[dict]) -> list[str]:
    flags: list[str] = []
    for r in records:
        idx = r.get("index") or 0
        ans = (r.get("answer") or "").strip()
        q = (r.get("question") or "").strip()
        # 跳过题是用户主动行为，不能算「回答过短/疑似作弊」——
        # 否则一份跳过 3 题的答卷会带着 3 条防作弊标记，误导用户。
        if ans in _SKIP_TOKENS:
            continue
        too_short = _to_int(r.get("answer_seconds"), 0) < 10 or len(ans) < 15
        if too_short:
            flags.append(f"第 {idx} 题回答过短，评分仅供参考")
        # 高度雷同 / 复制痕迹
        if ans and q:
            overlap = _char_overlap(ans, q)
            if overlap > 0.7 or any(m in ans for m in _COPY_MARKERS):
                flags.append(f"第 {idx} 题回答疑似照搬题目或模板，评分仅供参考")
    # 去重保序
    seen: set[str] = set()
    dedup: list[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            dedup.append(f)
    return dedup


def _char_overlap(a: str, b: str) -> float:
    sa, sb = set(a), set(b)
    if not sa:
        return 0.0
    return len(sa & sb) / len(sa)


# ============================================================
# 汇总
# ============================================================


def _build_overview(dim_scores: dict[str, float], comment: str) -> dict:
    total = round1(sum(dim_scores.get(d["key"], 0.0) * d["weight"] for d in DIMENSIONS))
    g = grade_of(total)
    return {
        "score": total,
        "percent": int(round(total * 10)),
        "grade": g["grade"],
        "grade_desc": g["desc"],
        "offer_probability": g["offer"],
        "one_line_comment": comment,
    }


def _one_line_comment(dim_scores: dict[str, float]) -> str:
    name_of = {d["key"]: d["name"] for d in DIMENSIONS}
    strong_phrase = {
        "professional": "专业表达有干货",
        "logic": "逻辑框架清晰",
        "expression": "表达流畅有重点",
        "matching": "与岗位要求契合度高",
        "adaptability": "压力下应变稳",
        "culture_fit": "与团队文化适配好",
    }
    weak_phrase = {
        "professional": "专业深度与术语储备",
        "logic": "结构化表达与论证",
        "expression": "口语表达与控场",
        "matching": "经历与 JD 的对标",
        "adaptability": "追问下的应变",
        "culture_fit": "与公司文化的呼应",
    }
    ordered = sorted(dim_scores.items(), key=lambda kv: kv[1], reverse=True)
    top_key, top_score = ordered[0]
    bottom_key, bottom_score = ordered[-1]
    return (
        f"{strong_phrase.get(top_key, name_of[top_key])}（{name_of[top_key]} {round1(top_score)} 分）是本次亮点，"
        f"但{weak_phrase.get(bottom_key, name_of[bottom_key])}（{name_of[bottom_key]} {round1(bottom_score)} 分）"
        f"是明显短板，按提升计划补齐后整体竞争力会显著提升。"
    )


def _empty_dim_scores() -> dict[str, float]:
    return {d["key"]: 6.0 for d in DIMENSIONS}


# ============================================================
# 规则评分引擎主入口
# ============================================================


def _rule_score(
    resume: dict,
    jd: dict,
    company: dict | None,
    questions: list[dict],
) -> dict:
    """规则评分引擎：真实分析文本，返回报告片段 dict（永不抛异常）。"""
    ctx = build_ctx(resume, jd, company)
    answers = _answers_of(questions)
    all_text = "\n".join(answers)

    pro_s, pro_c, pro_e = _score_professional(answers, ctx)
    log_s, log_c, log_e = _score_logic(answers, all_text)
    exp_s, exp_c, exp_e = _score_expression(answers, questions, ctx)
    mat_s, mat_c, mat_e = _score_matching(answers, ctx)
    ada_s, ada_c, ada_e = _score_adaptability(questions, ctx)
    cul_s, cul_c, cul_e = _score_culture(answers, ctx)

    dim_scores = {
        "professional": pro_s,
        "logic": log_s,
        "expression": exp_s,
        "matching": mat_s,
        "adaptability": ada_s,
        "culture_fit": cul_s,
    }
    comments = {
        "professional": (pro_c, pro_e),
        "logic": (log_c, log_e),
        "expression": (exp_c, exp_e),
        "matching": (mat_c, mat_e),
        "adaptability": (ada_c, ada_e),
        "culture_fit": (cul_c, cul_e),
    }

    dimensions = [
        {
            "key": d["key"],
            "score": dim_scores[d["key"]],
            "comment": comments[d["key"]][0],
            "evidence": comments[d["key"]][1] or [_quote(answers[0]) if answers else ""],
        }
        for d in DIMENSIONS
    ]
    for dim in dimensions:
        if not dim["evidence"]:
            dim["evidence"] = ["（本场未采集到有效回答原文）"]

    # 逐题分析
    q_out: list[dict] = []
    for r in questions:
        if int(r.get("index") or 0) <= 0:
            continue
        text = (r.get("answer") or "").strip()
        q_score = round1(_answer_quality(text, r, ctx)) if text and text not in _SKIP_TOKENS else 4.0
        q_out.append(
            {
                "index": int(r.get("index") or 0),
                "score": q_score,
                "good": _question_good(text, ctx) if text and text not in _SKIP_TOKENS else [],
                "improve": _question_improve(r, text, ctx),
                "reference": _question_reference(r, ctx),
            }
        )

    overview = _build_overview(dim_scores, _one_line_comment(dim_scores))
    return {
        "overview": overview,
        "dimensions": dimensions,
        "questions": q_out,
        "strengths": _build_strengths(dim_scores, answers, ctx),
        "improvements": _build_improvements(dim_scores, questions, ctx),
        "cheating_flags": _build_cheating_flags(questions),
        "dim_scores": dim_scores,
    }


# ============================================================
# LLM 结果规范化
# ============================================================

_KEY_ALIAS = {d["key"]: d["key"] for d in DIMENSIONS}
_KEY_ALIAS.update({d["name"]: d["key"] for d in DIMENSIONS})


def _norm_key(raw: Any) -> str:
    s = str(raw or "").strip()
    if s in _KEY_ALIAS:
        return _KEY_ALIAS[s]
    en = s.lower().replace(" ", "_")
    return en if en in _KEY_ALIAS else ""


def _norm_score(raw: Any, fallback: float) -> float:
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return fallback
    if v <= 0:
        return fallback
    return clamp(v, 1.0, 10.0)


def _dialogue_text(dialogue: list[dict]) -> str:
    lines: list[str] = []
    for t in dialogue or []:
        if not isinstance(t, dict):
            continue
        role = t.get("role")
        who = "面试官" if role == "interviewer" else ("候选人" if role == "candidate" else "系统")
        lines.append(f"{who}：{str(t.get('content') or '').strip()}")
    return "\n".join(lines)


def _resume_brief(resume: dict) -> str:
    if not resume:
        return "未提供"
    basic = resume.get("basic") or {}
    edu = "、".join(
        f"{e.get('school', '')}{e.get('major', '')}{e.get('degree', '')}"
        for e in (resume.get("education") or [])
        if isinstance(e, dict)
    )
    intern = "；".join(
        f"{i.get('company', '')} {i.get('title', '')}"
        for i in (resume.get("internships") or [])
        if isinstance(i, dict)
    )
    proj = "；".join(
        f"{p.get('name', '')} {p.get('role', '')}"
        for p in (resume.get("projects") or [])
        if isinstance(p, dict)
    )
    return f"姓名：{basic.get('name', '')}；教育：{edu or '未提供'}；实习：{intern or '无'}；项目：{proj or '无'}；技能：{'、'.join(resume.get('skills') or [])}"


def _jd_brief(jd: dict) -> str:
    if not jd:
        return "未提供"
    req = jd.get("requirements") or {}
    return (
        f"职责：{'；'.join((jd.get('responsibilities') or [])[:4])}；"
        f"硬技能：{'、'.join(req.get('hard_skills') or [])}；"
        f"软技能：{'、'.join(req.get('soft_skills') or [])}；"
        f"关键词：{'、'.join(jd.get('keywords') or [])}"
    )


def _merge_llm(base: dict, llm: dict) -> tuple[dict, str]:
    """把 LLM 返回的片段规范化后覆盖到规则结果上，返回 (fragment, scoring_mode)。"""
    used = 0
    total_dims = len(DIMENSIONS)

    # dimensions
    llm_dims = {}
    for item in llm.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        key = _norm_key(item.get("key") or item.get("name"))
        if not key:
            continue
        llm_dims[key] = item

    merged_dims: list[dict] = []
    for d in base["dimensions"]:
        key = d["key"]
        item = llm_dims.get(key)
        if item:
            sc = _norm_score(item.get("score"), d["score"])
            ev = [str(x) for x in (item.get("evidence") or []) if str(x).strip()]
            merged_dims.append(
                {
                    "key": key,
                    "score": round1(sc),
                    "comment": str(item.get("comment") or "").strip() or d["comment"],
                    "evidence": ev[:3] or d["evidence"],
                }
            )
            used += 1
        else:
            merged_dims.append(dict(d))
    base["dimensions"] = merged_dims

    # questions（LLM 只给分数，其余保留规则版本）
    llm_q = {}
    for item in llm.get("questions") or []:
        if not isinstance(item, dict):
            continue
        try:
            llm_q[int(item.get("index"))] = item
        except (TypeError, ValueError):
            continue
    merged_q = []
    for q in base["questions"]:
        item = llm_q.get(int(q["index"]))
        if item:
            q = dict(q)
            q["score"] = round1(_norm_score(item.get("score"), q["score"]))
            g = [str(x) for x in (item.get("good") or []) if str(x).strip()]
            im = [str(x) for x in (item.get("improve") or []) if str(x).strip()]
            if g:
                q["good"] = g[:3]
            if im:
                q["improve"] = im[:3]
            ref = str(item.get("reference") or "").strip()
            if ref:
                q["reference"] = ref
        merged_q.append(q)
    base["questions"] = merged_q

    # strengths（LLM 版本需恰好 3 条）
    llm_strengths = []
    for item in llm.get("strengths") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        detail = str(item.get("detail") or "").strip()
        if title and detail:
            llm_strengths.append(
                {"title": title, "detail": detail, "evidence": str(item.get("evidence") or "").strip()}
            )
    if len(llm_strengths) >= 3:
        base["strengths"] = llm_strengths[:3]

    # improvements（LLM 版本需 actions 恰好 3 条且 resources 非空，否则保留规则版本）
    llm_improvements = []
    for item in llm.get("improvements") or []:
        if not isinstance(item, dict):
            continue
        acts = [str(x) for x in (item.get("actions") or []) if str(x).strip()]
        res = [str(x) for x in (item.get("resources") or []) if str(x).strip()]
        if len(acts) < 3 or not res:
            continue
        pr = str(item.get("priority") or "medium").strip()
        if pr not in _ALLOWED_PRIORITY:
            pr = "medium"
        problem = str(item.get("problem") or "").strip()
        dim = str(item.get("dimension") or "").strip()
        if not problem or not dim:
            continue
        llm_improvements.append(
            {
                "priority": pr,
                "dimension": dim,
                "problem": problem,
                "evidence": str(item.get("evidence") or "").strip(),
                "actions": acts[:3],
                "resources": res,
            }
        )
    if llm_improvements:
        order = {"high": 0, "medium": 1, "low": 2}
        llm_improvements.sort(key=lambda x: order.get(x["priority"], 3))
        base["improvements"] = llm_improvements[:3]

    # 重算 overview（基于合并后的维度分）
    dim_scores = {d["key"]: d["score"] for d in base["dimensions"]}
    base["dim_scores"] = dim_scores
    base["overview"] = _build_overview(dim_scores, _one_line_comment(dim_scores))

    if used >= total_dims:
        mode = "llm"
    elif used > 0:
        mode = "hybrid"
    else:
        mode = "rule"
    return base, mode


# ============================================================
# 对外主入口
# ============================================================


async def score_session(
    job_title: str,
    company_name: str,
    resume: dict,
    jd: dict,
    company: dict | None,
    dialogue: list[dict],
    questions: list[dict],
    on_stage=None,
) -> tuple[dict, str]:
    """六维评分主入口，返回 `(report 片段 dict, scoring_mode)`，scoring_mode ∈ {"llm","rule","hybrid"}。

    文档 §5.3（评分 Prompt）→ §5.4（二次校验 + 防作弊）。
    LLM 不可用时强制走规则引擎 `_rule_score`，规则引擎永远能出报告。

    `on_stage(key)` 是可选的进度回调（报告生成可视化进度）：key ∈
    {"rule","llm_first","llm_second"}。评分本身要跑两次大模型，是整份报告最慢的一段，
    这里把子步骤报出去，前端的进度条才不会长时间停在「六维评分」上不知所云。
    回调异常一律吞掉 —— 埋点不能影响评分。
    """
    _notify(on_stage, "rule")
    # 1) 规则引擎始终先跑一遍，作为兜底基线
    try:
        base = _rule_score(resume, jd, company, questions)
    except Exception as exc:  # noqa: BLE001 规则引擎异常也要兜底
        print(f"[scoring] 规则引擎异常，使用保守基线: {exc}")
        base = _baseline_fragment(questions)

    # 2) LLM 优先
    try:
        llm = get_llm()
    except Exception as exc:  # noqa: BLE001
        print(f"[scoring] LLM 客户端初始化失败，走规则引擎: {exc}")
        return base, "rule"

    if getattr(llm, "mode", "mock") == "mock":
        return base, "rule"

    llm_data: dict | None = None
    _notify(on_stage, "llm_first")
    try:
        llm_data = await llm.chat_json(
            scoring_prompt(
                job_title or "目标岗位",
                company_name or "某公司",
                _resume_brief(resume),
                _jd_brief(jd),
                _dialogue_text(dialogue),
            )
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[scoring] LLM 评分失败，走规则引擎: {exc}")
        llm_data = None

    if not isinstance(llm_data, dict) or not llm_data:
        return base, "rule"

    try:
        merged, mode = _merge_llm(base, llm_data)
    except Exception as exc:  # noqa: BLE001
        print(f"[scoring] LLM 结果规范化失败，走规则引擎: {exc}")
        return base, "rule"

    # 3) 二次校验（文档 §5.4）：professional / matching 独立再评一次取平均
    _notify(on_stage, "llm_second")
    try:
        second = await llm.chat_json(
            second_pass_prompt(job_title or "目标岗位", _jd_brief(jd), _dialogue_text(dialogue))
        )
        if isinstance(second, dict):
            dim_map = {d["key"]: d for d in merged["dimensions"]}
            changed = False
            for key in ("professional", "matching"):
                raw = second.get(key) or second.get(_name_of(key))
                if raw is None:
                    continue
                try:
                    s2 = float(raw)
                except (TypeError, ValueError):
                    continue
                if s2 <= 0:
                    continue
                first = dim_map[key]["score"]
                dim_map[key]["score"] = round1(clamp((first + clamp(s2, 1.0, 10.0)) / 2.0, 1.0, 10.0))
                changed = True
            if changed:
                dim_scores = {d["key"]: d["score"] for d in merged["dimensions"]}
                merged["dim_scores"] = dim_scores
                merged["overview"] = _build_overview(dim_scores, _one_line_comment(dim_scores))
    except Exception as exc:  # noqa: BLE001 二次校验失败不影响主结果
        print(f"[scoring] 二次校验跳过: {exc}")

    return merged, mode


def _notify(on_stage, key: str) -> None:
    """安全地报告评分子步骤（进度埋点）。回调异常绝不影响评分本身。"""
    if on_stage is None:
        return
    try:
        on_stage(key)
    except Exception as exc:  # noqa: BLE001
        print(f"[scoring] 进度回调异常（已忽略）: {exc}")


def _name_of(key: str) -> str:
    for d in DIMENSIONS:
        if d["key"] == key:
            return d["name"]
    return key


def _baseline_fragment(questions: list[dict]) -> dict:
    """极端兜底：规则引擎也异常时用的保守基线（仍然逐题给出分数）。"""
    dim_scores = _empty_dim_scores()
    dims = []
    for d in DIMENSIONS:
        q = [r for r in questions if (r.get("answer") or "").strip()]
        dims.append(
            {
                "key": d["key"],
                "score": dim_scores[d["key"]],
                "comment": f"「{d['name']}」按中性基准 6.0 分计（评分引擎降级模式）。",
                "evidence": [_quote((q[0].get("answer") if q else "") or "") or "（无有效回答原文）"],
            }
        )
    q_out = [
        {
            "index": int(r.get("index") or 0),
            "score": 6.0,
            "good": [],
            "improve": ["评分引擎降级，建议重新生成报告"],
            "reference": _question_reference(r, build_ctx({}, {}, None)),
        }
        for r in questions
        if int(r.get("index") or 0) > 0
    ]
    return {
        "overview": _build_overview(dim_scores, "本次报告由降级模式生成，建议稍后重新生成以获得准确评分。"),
        "dimensions": dims,
        "questions": q_out,
        "strengths": _build_strengths(dim_scores, [], build_ctx({}, {}, None)),
        "improvements": _build_improvements(dim_scores, questions, build_ctx({}, {}, None)),
        "cheating_flags": _build_cheating_flags(questions),
        "dim_scores": dim_scores,
    }
