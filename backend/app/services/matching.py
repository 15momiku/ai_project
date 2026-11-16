"""简历 × JD × 公司 匹配度引擎（对应文档 §3.5 / §3.5.1 / §3.5.2）。

对外唯一入口：`compute_match(resume, jd, company)`（API_CONTRACT §1.2，模块二会 import）。

评分模型（§3.5.1，权重合计 1.0，保证 overall 可落在 0-100）：
1. 硬技能匹配   0.35 —— JD hard_skills 与简历技能/经历文本的大小写不敏感包含匹配，命中率×10
2. 经验匹配     0.30 —— JD responsibilities 与简历实习/项目文本的词元重叠（Jaccard，取最高分后加权）
3. 学历/专业匹配 0.15 —— 满足门槛给 9-10，偏低给 5，未提要求给 8
4. 软素质匹配   0.10 —— JD soft_skills 在简历文本中的命中率
5. 公司文化适配 0.10 —— 公司 culture_tags 在简历中的体现度（无公司信息时给中性 8）

同时产出：
- `weak`：JD 要求但简历未体现的硬技能 / 软素质 / 经历方向（§3.5.1 第4点）
- `matched`：简历中超出 JD 要求的加分项（额外技能 / 获奖 / 量化成果，§3.5.1 第5点）
- `strategy`：重点考察 / 深挖经历 / 压力测试（§3.5.2）
- `radar_target`：constants.TARGET_RADAR 按 JD 硬技能数量 ±0.5 微调

所有空字段都会走默认分支，绝不抛异常。
"""

from __future__ import annotations

import re

from ..constants import TARGET_RADAR
from ..deps import clamp, tokenize
from ..schemas import MatchResponse
from .resume_parser import SKILL_ALIASES, _has_skill

# 权重（合计 1.0）
WEIGHTS = {
    "hard_skill": 0.35,
    "experience": 0.30,
    "education": 0.15,
    "soft_skill": 0.10,
    "culture_fit": 0.10,
}

# 学历档位（用于比较门槛）
_DEGREE_RANK = {"大专": 1, "专科": 1, "本科": 2, "学士": 2, "硕士": 3, "研究生": 3, "博士": 4}

# 经历方向词表：用于「经历方向薄弱项」识别与深挖标签
_DIRECTION_WORDS = [
    "数据产品", "数据分析", "产品", "运营", "增长", "算法", "研发", "商业化",
    "用户研究", "项目管理", "竞品分析", "B端", "C端", "SaaS", "供应链", "风控",
]

# 量化成果识别
_QUANT_RE = re.compile(
    r"[^。；;\n]{0,24}(?:\d+(?:\.\d+)?\s*[%％倍万+]|提升|增长|降低|下降|覆盖|排名)[^。；;\n]{0,24}"
)


# ============================================================
# 小工具
# ============================================================


def _jaccard(a: str, b: str) -> float:
    ta, tb = tokenize(a or ""), tokenize(b or "")
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _resume_skill_blob(resume: dict) -> str:
    """简历「技能相关文本」：技能标签 + 实习/项目描述 + 自我评价。"""
    parts: list[str] = list(resume.get("skills") or [])
    for it in resume.get("internships") or []:
        parts.extend([it.get("company", ""), it.get("title", "")])
        parts.extend(it.get("duties") or [])
        parts.extend(it.get("achievements") or [])
    for pj in resume.get("projects") or []:
        parts.extend([pj.get("name", ""), pj.get("role", ""), pj.get("description", "")])
        parts.extend(pj.get("tech_stack") or [])
    parts.append(resume.get("self_evaluation") or "")
    for edu in resume.get("education") or []:
        parts.extend(edu.get("highlights") or [])
    return " ".join(p for p in parts if p)


def _jd_hard_skills(jd: dict) -> list[str]:
    return list(((jd.get("requirements") or {}).get("hard_skills")) or [])


def _jd_soft_skills(jd: dict) -> list[str]:
    return list(((jd.get("requirements") or {}).get("soft_skills")) or [])


def _degree_rank(text: str) -> int:
    for key, rank in _DEGREE_RANK.items():
        if key in (text or ""):
            return rank
    return 0


# ============================================================
# 各维度打分
# ============================================================


def _score_hard_skill(resume: dict, jd: dict) -> tuple[float, list[str], list[str]]:
    """硬技能匹配：返回 (分数, 命中列表, 未命中列表)。"""
    required = _jd_hard_skills(jd)
    if not required:
        return 8.0, [], []
    blob = _resume_skill_blob(resume)
    hit: list[str] = []
    miss: list[str] = []
    for skill in required:
        if _has_skill(blob, skill):
            hit.append(skill)
            continue
        if any(_has_skill(blob, alias) for alias in SKILL_ALIASES.get(skill, [])):
            hit.append(skill)
            continue
        miss.append(skill)
    return clamp(len(hit) / len(required) * 10, 0, 10), hit, miss


def _score_experience(resume: dict, jd: dict) -> tuple[float, str]:
    """经验匹配：JD 职责逐条与简历经历文本做 Jaccard，取最高分，再按「覆盖率 + 强度」映射到 0-10。"""
    responsibilities = [r for r in (jd.get("responsibilities") or []) if r]
    resume_texts: list[str] = []
    for it in resume.get("internships") or []:
        text = " ".join((it.get("duties") or []) + (it.get("achievements") or []))
        if text:
            resume_texts.append(text)
    for pj in resume.get("projects") or []:
        text = f"{pj.get('name', '')} {pj.get('description', '')}"
        if text.strip():
            resume_texts.append(text)

    if not responsibilities:
        return 8.0, "JD 未给出明确职责，经验匹配按中性分计算"
    if not resume_texts:
        return 4.0, "简历未提供实习/项目经历文本，经验匹配给低分"

    best_scores = [max(_jaccard(r, t) for t in resume_texts) for r in responsibilities]
    # 覆盖率：存在可识别重叠（>=0.01）的职责占比；强度：平均 Jaccard
    coverage = sum(1 for s in best_scores if s >= 0.01) / len(best_scores)
    avg_j = sum(best_scores) / len(best_scores)
    score = clamp(2.0 + coverage * 4.0 + min(avg_j, 0.10) * 45.0, 0, 10)
    detail = (
        f"职责覆盖 {coverage * 100:.0f}%，最高词元重叠 {max(best_scores):.3f}"
        f"（平均 {avg_j:.3f}）"
    )
    return score, detail


def _score_education(resume: dict, jd: dict) -> tuple[float, str]:
    """学历/专业匹配：满足门槛 9-10，偏低 5，未提要求 8。"""
    jd_degree = ((jd.get("requirements") or {}).get("degree")) or ""
    resume_degree = ((resume.get("basic") or {}).get("degree")) or ""
    if not resume_degree:
        for edu in resume.get("education") or []:
            if edu.get("degree"):
                resume_degree = edu["degree"]
                break

    need = _degree_rank(jd_degree)
    have = _degree_rank(resume_degree)
    if need == 0:
        score = 8.0 if have else 7.0
        detail = f"JD 未明确学历门槛，简历学历「{resume_degree or '未提供'}」"
    elif have == 0:
        score = 6.0
        detail = f"JD 要求「{jd_degree}」，简历未识别到学历"
    elif have >= need:
        score = 9.5 if have == need else 10.0
        detail = f"学历满足要求：{resume_degree} ≥ {jd_degree}"
    else:
        score = 5.0
        detail = f"学历低于门槛：{resume_degree} < {jd_degree}"

    # 专业相关度小幅加成（不超过 10）
    major = ((resume.get("basic") or {}).get("major")) or ""
    if major and score < 10 and need and have >= need:
        if any(k in jd_degree for k in ("相关专业",)) or any(k in major for k in ("数据", "计算", "统计", "数学", "软件", "信息")):
            score = clamp(score + 0.5, 0, 10)
            detail += f"；专业「{major}」与岗位方向相关"
    return score, detail


def _score_soft_skill(resume: dict, jd: dict) -> tuple[float, list[str], list[str]]:
    """软素质匹配：JD soft_skills 在简历全文中的命中率。"""
    required = _jd_soft_skills(jd)
    if not required:
        return 8.0, [], []
    blob = _resume_skill_blob(resume)
    hit = [s for s in required if s in blob]
    miss = [s for s in required if s not in blob]
    return clamp(len(hit) / len(required) * 10, 0, 10), hit, miss


def _score_culture(resume: dict, company: dict | None) -> tuple[float, str]:
    """公司文化适配：culture_tags 在简历中的体现度；无公司信息给中性 8。"""
    company = company or {}
    tags = [t for t in (company.get("culture_tags") or []) if t]
    if not tags:
        return 8.0, "未提供公司文化信息，按中性分计算"
    blob = _resume_skill_blob(resume) + " " + ((resume.get("basic") or {}).get("name") or "")
    hit: list[str] = []
    for tag in tags:
        grams = [tag[i : i + 2] for i in range(max(1, len(tag) - 1))]
        if any(g in blob for g in grams if len(g) == 2):
            hit.append(tag)
    ratio = len(hit) / len(tags)
    score = clamp(6.0 + ratio * 4.0, 0, 10)
    detail = f"公司文化标签体现 {len(hit)}/{len(tags)}：{'、'.join(hit) or '暂未体现'}"
    return score, detail


# ============================================================
# 体验增强 §8：维度下钻的关键词与建议
# ============================================================


def _weight_hint(key: str) -> str:
    """把"这一维为什么值这么多分"讲清楚，避免用户觉得权重是黑箱。

    按**实际排名**措辞（而不是阈值）：0.35 与 0.30 都 ≥30%，用阈值会让两维
    都自称"权重最高"，反而暴露了模板感。
    """
    weight = float(WEIGHTS.get(key, 0.0))
    pct = int(round(weight * 100))
    ordered = sorted(WEIGHTS.values(), reverse=True)
    if not ordered:
        return f"占总分 {pct}%"
    if weight >= ordered[0]:
        rank = "权重最高的一维，直接决定总分"
    elif weight >= ordered[1]:
        rank = "权重次高，是拉开差距的一维"
    elif weight >= ordered[2]:
        rank = "中等权重，补强它能稳住总分"
    else:
        rank = "权重较小，属于锦上添花"
    return f"占总分 {pct}%，{rank}"


def _experience_keywords(resume: dict, jd: dict) -> tuple[list[str], list[str]]:
    """经历方向词在「JD 职责」与「简历经历」中的覆盖情况。"""
    jd_text = " ".join((jd.get("responsibilities") or []) + [((jd.get("basic") or {}).get("title")) or ""])
    needs = [w for w in _DIRECTION_WORDS if w in jd_text]
    if not needs:
        return [], []
    blob = _resume_skill_blob(resume)
    hit = [w for w in needs if w in blob]
    miss = [w for w in needs if w not in blob]
    return hit, miss


def _education_keywords(resume: dict, jd: dict) -> tuple[list[str], list[str]]:
    """学历/专业命中的词（简历实际写了什么 vs 岗位要求什么）。"""
    req = jd.get("requirements") or {}
    resume_degree = ((resume.get("basic") or {}).get("degree")) or ""
    if not resume_degree:
        for edu in resume.get("education") or []:
            if edu.get("degree"):
                resume_degree = edu["degree"]
                break
    major = ((resume.get("basic") or {}).get("major")) or ""
    hit = [x for x in (resume_degree, major) if x]
    miss: list[str] = []
    jd_degree = str(req.get("degree") or "")
    if jd_degree and _degree_rank(resume_degree) < _degree_rank(jd_degree):
        miss.append(jd_degree)
    return hit, miss


def _culture_keywords(resume: dict, company: dict | None) -> tuple[list[str], list[str]]:
    """公司文化标签在简历中的体现情况（与 `_score_culture` 同一套二字滑窗口径）。"""
    tags = [t for t in ((company or {}).get("culture_tags") or []) if t]
    if not tags:
        return [], []
    blob = _resume_skill_blob(resume) + " " + ((resume.get("basic") or {}).get("name") or "")
    hit: list[str] = []
    miss: list[str] = []
    for tag in tags:
        grams = [tag[i : i + 2] for i in range(max(1, len(tag) - 1))]
        if any(g in blob for g in grams if len(g) == 2):
            hit.append(tag)
        else:
            miss.append(tag)
    return hit, miss


def _skill_suggestion(miss_hard: list[str]) -> str:
    """缺技能时给一条**能立刻动手**的建议，而不是"建议提升技能"这种废话。"""
    if not miss_hard:
        return "技能要求已全部覆盖，这一维可以不用再动。"
    top = miss_hard[0]
    return (
        f"在项目经历里为「{top}」补一句具体用法（做什么、用了什么方法、拿到什么结果），"
        f"技能维度按命中率计分，补一条就能直接提分。"
    )


# ============================================================
# 薄弱项 / 优势项 / 策略
# ============================================================


def _build_weak(
    resume: dict,
    jd: dict,
    miss_hard: list[str],
    miss_soft: list[str],
) -> tuple[list[dict], list[str]]:
    """生成薄弱项与「重点考察」文案。"""
    weak: list[dict] = []
    focus: list[str] = []
    resume_text = _resume_skill_blob(resume)
    jd_text = " ".join((jd.get("responsibilities") or []) + [((jd.get("basic") or {}).get("title")) or ""])

    for skill in miss_hard[:4]:
        weak.append({"dimension": "硬技能", "detail": f"岗位要求「{skill}」，简历中未体现相关技能或经历"})
        focus.append(f"{skill} 能力（口头考察）")

    for soft in miss_soft[:3]:
        weak.append({"dimension": "软素质", "detail": f"岗位要求「{soft}」，简历与经历中缺少佐证"})
        focus.append(f"{soft}（行为面考察）")

    for direction in _DIRECTION_WORDS:
        if direction in jd_text and direction not in resume_text:
            weak.append({"dimension": "经历方向", "detail": f"岗位涉及「{direction}」方向，简历中缺少对应经历"})
            focus.append(f"{direction} 经历（经历追问）")
        if len(weak) >= 6:
            break

    if not resume.get("internships"):
        weak.append({"dimension": "实践经历", "detail": "简历中未识别到实习经历，建议补充与岗位相关的实践"})
    if not weak:
        weak.append({"dimension": "综合", "detail": "暂未发现明显短板，建议进一步深挖经历细节"})
    return weak[:6], focus[:3] if focus else ["简历与岗位整体契合，重点考察经历真实性"]


def _build_matched(resume: dict, hit_hard: list[str], jd: dict) -> list[dict]:
    """优势项：技能命中 + 额外技能 + 获奖 + 量化成果。"""
    matched: list[dict] = []
    if hit_hard:
        matched.append(
            {"dimension": "技能匹配", "detail": f"命中岗位核心技能：{'、'.join(hit_hard)}"}
        )
    jd_required = set(_jd_hard_skills(jd))
    extra = [s for s in (resume.get("skills") or []) if s not in jd_required and s not in hit_hard]
    if extra:
        matched.append({"dimension": "额外优势", "detail": f"简历具备岗位未明确要求的加分技能：{'、'.join(extra[:6])}"})

    awards = list(resume.get("awards") or [])
    if awards:
        matched.append({"dimension": "获奖荣誉", "detail": "获奖情况：" + "；".join(awards[:3])})

    achievements: list[str] = []
    for it in resume.get("internships") or []:
        achievements.extend(it.get("achievements") or [])
    for pj in resume.get("projects") or []:
        achievements.extend(_QUANT_RE.findall(pj.get("description") or ""))
    if achievements:
        matched.append({"dimension": "成果量化", "detail": "量化成果：" + "；".join(a.strip() for a in achievements[:3])})

    if not matched:
        matched.append({"dimension": "基础信息", "detail": "简历信息完整，可围绕教育背景与基础技能展开考察"})
    return matched[:6]


def _build_deep_dive(resume: dict, jd: dict) -> list[str]:
    """深挖经历：从项目/实习中挑与 JD 方向重叠度最高的 2 个。"""
    jd_text = " ".join(
        (jd.get("responsibilities") or [])
        + _jd_hard_skills(jd)
        + list(jd.get("keywords") or [])
        + [((jd.get("basic") or {}).get("title")) or ""]
    )
    directions = [d for d in _DIRECTION_WORDS if d in jd_text] or list(jd.get("keywords") or [])

    candidates: list[tuple[str, str]] = []  # (展示名, 文本)
    for pj in resume.get("projects") or []:
        name = pj.get("name") or "项目经历"
        candidates.append((name, f"{name} {pj.get('description', '')}"))
    for it in resume.get("internships") or []:
        name = f"{it.get('company', '')}{(' ' + it['title']) if it.get('title') else ''}".strip() or "实习经历"
        candidates.append((name, " ".join([name] + (it.get("duties") or []) + (it.get("achievements") or []))))

    if not candidates:
        return ["简历中的核心经历（与JD 岗位要求 方向相关）"]

    scored: list[tuple[float, str, str]] = []
    for name, text in candidates:
        label = ""
        for d in directions:
            if d in text:
                label = d
                break
        sim = max((_jaccard(text, d) for d in directions), default=0.0) if directions else 0.0
        if not label and directions:
            label = max(directions, key=lambda d: _jaccard(text, d))
        label = label or "岗位要求"
        scored.append((sim + (1.0 if label in text else 0.0), name, label))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = [f"{name}（与JD {label} 方向相关）" for _, name, label in scored[:2]]
    return out


def _build_pressure_test(weak: list[dict]) -> list[str]:
    """压力测试问题：基于薄弱项，措辞专业、只做事实性追问。"""
    questions: list[str] = []
    for item in weak:
        dim = item.get("dimension")
        detail = item.get("detail", "")
        m = re.search(r"「([^」]+)」", detail)
        key = m.group(1) if m else ""
        if dim == "硬技能" and key:
            questions.append(f"简历中「{key}」相关经历较少，如果入职后需要用到它，你会怎么快速上手？")
        elif dim == "软素质" and key:
            questions.append(f"岗位比较看重「{key}」，能举一个你在团队协作中体现这一点的具体例子吗？")
        elif dim == "经历方向" and key:
            questions.append(f"你目前的经历更多集中在其它方向，为什么选择「{key}」方向的岗位？为此做了哪些准备？")
        elif dim == "实践经历":
            questions.append("你的实践经历相对集中在校园场景，能讲一个你投入最多、收获最大的项目吗？")
        if len(questions) >= 3:
            break
    if not questions:
        questions.append("你自认为目前最需要提升的能力是什么？如果拿到这个岗位，你打算怎么补上？")
    return questions[:3]


def _level_text(overall: int) -> str:
    """匹配等级文案（§3.5.2，4 档）。"""
    if overall >= 85:
        return "高度匹配，简历与岗位高度契合，建议直接进入面试冲刺"
    if overall >= 70:
        return "基本匹配，有短板需补强，建议针对性准备后再投递"
    if overall >= 55:
        return "匹配度一般，需重点补强相关技能与经历"
    return "匹配度偏低，建议重新评估岗位或补充相关经历"


def _radar_target(jd: dict) -> dict:
    """雷达图目标分：TARGET_RADAR 按 JD 硬技能数量 ±0.5 微调。"""
    radar = {k: float(v) for k, v in TARGET_RADAR.items()}
    n = len(_jd_hard_skills(jd))
    delta = 0.5 if n >= 6 else (-0.5 if n <= 2 else 0.0)
    if delta:
        radar["专业能力"] = round(radar.get("专业能力", 7.0) + delta, 1)
        radar["岗位匹配度"] = round(radar.get("岗位匹配度", 6.8) + delta, 1)
    return radar


# ============================================================
# 对外入口
# ============================================================


def compute_match(resume: dict, jd: dict, company: dict | None = None) -> dict:
    """计算匹配度（文档 §3.5.1），返回 `schemas.MatchResponse` 的 dict 形态。"""
    resume = resume or {}
    jd = jd or {}
    company = company or {}

    # ---- 各维度打分 ----
    hard_score, hit_hard, miss_hard = _score_hard_skill(resume, jd)
    exp_score, exp_detail = _score_experience(resume, jd)
    edu_score, edu_detail = _score_education(resume, jd)
    soft_score, hit_soft, miss_soft = _score_soft_skill(resume, jd)
    culture_score, culture_detail = _score_culture(resume, company)

    # ---- 体验增强 §8：每一维下钻用的命中/缺失关键词与"补一句就能提分"的建议 ----
    jd_hard = _jd_hard_skills(jd)
    jd_soft = _jd_soft_skills(jd)
    exp_hit, exp_miss = _experience_keywords(resume, jd)
    edu_hit, edu_miss = _education_keywords(resume, jd)
    culture_hit, culture_miss = _culture_keywords(resume, company)

    breakdown = [
        {
            "key": "hard_skill",
            "dimension": "硬技能匹配",
            "score": round(hard_score, 1),
            "weight": WEIGHTS["hard_skill"],
            "detail": (
                f"命中 {len(hit_hard)}/{len(jd_hard)} 项核心技能"
                + (f"；缺口：{'、'.join(miss_hard)}" if miss_hard else "；无技能缺口")
                if jd_hard
                else "JD 未列出硬技能要求，按中性分计算"
            ),
            "hit_keywords": hit_hard[:12],
            "miss_keywords": miss_hard[:12],
            "suggestion": _skill_suggestion(miss_hard),
            "weight_hint": _weight_hint("hard_skill"),
        },
        {
            "key": "experience",
            "dimension": "经验匹配",
            "score": round(exp_score, 1),
            "weight": WEIGHTS["experience"],
            "detail": exp_detail,
            "hit_keywords": exp_hit[:12],
            "miss_keywords": exp_miss[:12],
            "suggestion": (
                f"把「{'、'.join(exp_miss[:2])}」相关的职责，改写成你做过的一段经历"
                "（背景 → 你的动作 → 量化结果），这是提升幅度最大的一处。"
                if exp_miss
                else "职责覆盖已经不错，接下来把每段经历都补上一个数字结果即可。"
            ),
            "weight_hint": _weight_hint("experience"),
        },
        {
            "key": "education",
            "dimension": "学历/专业匹配",
            "score": round(edu_score, 1),
            "weight": WEIGHTS["education"],
            "detail": edu_detail,
            "hit_keywords": edu_hit,
            "miss_keywords": edu_miss,
            "suggestion": (
                "学历是硬门槛时无法靠简历弥补，但可以在「自我评价」里补一句与岗位方向相关的"
                "课程/研究/证书，让筛选者看到可迁移性。"
                if edu_miss
                else "学历与专业都满足要求，这一维不需要额外动作。"
            ),
            "weight_hint": _weight_hint("education"),
        },
        {
            "key": "soft_skill",
            "dimension": "软素质匹配",
            "score": round(soft_score, 1),
            "weight": WEIGHTS["soft_skill"],
            "detail": (
                f"命中 {len(hit_soft)}/{len(jd_soft)} 项软素质要求"
                + (f"；缺口：{'、'.join(miss_soft)}" if miss_soft else "")
                if jd_soft
                else "JD 未列出软素质要求，按中性分计算"
            ),
            "hit_keywords": hit_soft[:12],
            "miss_keywords": miss_soft[:12],
            "suggestion": (
                f"「{'、'.join(miss_soft[:2])}」这类软素质无法直接写，要靠经历体现："
                "在项目描述里补一句你和谁协作、推动过什么分歧、最后怎么落地的。"
                if miss_soft
                else "软素质已有佐证，保持即可。"
            ),
            "weight_hint": _weight_hint("soft_skill"),
        },
        {
            "key": "culture_fit",
            "dimension": "公司文化适配",
            "score": round(culture_score, 1),
            "weight": WEIGHTS["culture_fit"],
            "detail": culture_detail,
            "hit_keywords": culture_hit[:12],
            "miss_keywords": culture_miss[:12],
            "suggestion": (
                f"在自我评价里用一句真实经历呼应该公司的「{'、'.join(culture_miss[:2])}」，"
                "比空喊口号有效。"
                if culture_miss
                else "文化标签已有呼应，可在面试里准备一个对应的小故事。"
            ),
            "weight_hint": _weight_hint("culture_fit"),
        },
    ]

    # ---- 总分（§3.5.1：Σ(维度分 × 权重) × 10）----
    total = sum(b["score"] * b["weight"] for b in breakdown)
    overall = int(clamp(round(total * 10), 0, 100))

    # ---- 信息缺失保护：简历或 JD 关键信息缺失时，不允许给出虚高结论 ----
    resume_missing = not any(
        [
            (resume.get("basic") or {}).get("name"),
            (resume.get("basic") or {}).get("school"),
            resume.get("education"),
            resume.get("internships"),
            resume.get("projects"),
            resume.get("skills"),
        ]
    )
    jd_missing = not any(
        [
            jd.get("responsibilities"),
            _jd_hard_skills(jd),
            _jd_soft_skills(jd),
            ((jd.get("requirements") or {}).get("degree")) or "",
            ((jd.get("basic") or {}).get("title")) or "",
        ]
    )
    if resume_missing and jd_missing:
        overall = 0
        level = "简历与岗位信息均为空，暂无法计算匹配度"
    elif resume_missing or jd_missing:
        overall = min(overall, 50)
        level = "关键信息不足，匹配度仅供参考（简历或岗位信息缺失）"
    else:
        level = _level_text(overall)

    weak, focus = _build_weak(resume, jd, miss_hard, miss_soft)
    matched = _build_matched(resume, hit_hard, jd)
    deep_dive = _build_deep_dive(resume, jd)
    pressure_test = _build_pressure_test(weak)

    data = {
        "overall": overall,
        "level": level,
        "matched": matched,
        "weak": weak,
        "strategy": {"focus": focus, "deep_dive": deep_dive, "pressure_test": pressure_test},
        "breakdown": breakdown,
        "radar_target": _radar_target(jd),
    }
    try:
        return MatchResponse(**data).model_dump()
    except Exception:  # noqa: BLE001 极端脏数据也返回可用结果
        return MatchResponse(
            overall=overall,
            level=level,
            radar_target=_radar_target(jd),
        ).model_dump()
