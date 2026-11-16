"""解析字段置信度与覆盖率（体验增强 §7）。

设计原则：置信度描述的是**这份信息是怎么被找到的**，不是"模型自认为有多准"。
因此：

- 只有**规则引擎**会给置信度。它知道自己是从带标题的板块里抽到的，
  还是从全文里兜底猜的，这个区别对用户有意义；
- **LLM 结构化结果不臆造置信度**（让模型给自己打分不可靠），此时 `field_confidence`
  为空 dict，前端退化为"无标记"，而不是显示一个假的把握度；
- 置信度只用于**排序与提示**：低置信度字段排到前面、加琥珀色标记，
  把"从头看一遍"变成"只改 3 个地方"。它从不参与任何评分或匹配计算。
"""

from __future__ import annotations

# 置信度档位：命名而不是散落的魔法数字，便于前后端与文案对照
CONF_LABELED = 0.95  # 有明确标签（「姓名：」「电话：」）或带标题的板块，且内容完整
CONF_SECTION = 0.88  # 有明确板块标题，内容可用
CONF_SECTION_THIN = 0.72  # 有板块标题，但条目字段偏少（可能切分不干净）
CONF_DERIVED = 0.6  # 从别的板块回填（如学校由教育经历补出）
CONF_FALLBACK = 0.5  # 没有板块标题，靠全文兜底命中
CONF_NONE = 0.0  # 没有找到

# 低于该阈值视为"低置信度"，前端会高亮并前置排序
LOW_CONFIDENCE = 0.7
# 低于该阈值视为"根本没找到"，计入缺失项
FOUND_MIN = 0.01

# 展示用的中文标签（前端只负责渲染，文案口径在这里统一）
RESUME_FIELD_LABELS: dict[str, str] = {
    "basic.name": "姓名",
    "basic.school": "学校",
    "basic.major": "专业",
    "basic.degree": "学历",
    "basic.phone": "手机",
    "basic.email": "邮箱",
    "basic.grade": "年级",
    "education": "教育背景",
    "internships": "实习经历",
    "projects": "项目经历",
    "campus_experience": "校园经历",
    "skills": "专业技能",
    "awards": "获奖情况",
    "self_evaluation": "自我评价",
}

JD_FIELD_LABELS: dict[str, str] = {
    "basic.title": "岗位名称",
    "basic.department": "所属部门",
    "basic.location": "工作地点",
    "responsibilities": "岗位职责",
    "requirements.hard_skills": "硬技能要求",
    "requirements.soft_skills": "软技能要求",
    "requirements.degree": "学历要求",
    "requirements.experience": "经验要求",
    "requirements.bonus": "加分项",
    "keywords": "关键词",
}


def coverage(conf: dict[str, float], labels: dict[str, str]) -> dict:
    """把 `{字段: 置信度}` 汇总成覆盖率卡片所需的结构。

    `total` 取标签表的字段数（也就是"一份完整的简历/JD 应该有哪些块"），
    而不是"这次解析返回了几个键"——否则缺失项永远不会出现在分母里，
    覆盖率的数字就没有意义了。
    """
    if not isinstance(labels, dict) or not labels:
        return {"recognized": 0, "total": 0, "missing": [], "note": ""}

    recognized = 0
    missing: list[str] = []
    for key, label in labels.items():
        value = 0.0
        try:
            value = float((conf or {}).get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value >= FOUND_MIN:
            recognized += 1
        else:
            missing.append(label)

    total = len(labels)
    note = ""
    if recognized < total:
        note = (
            f"已识别 {recognized}/{total} 项。"
            "缺失项通常是简历里没有写、或板块标题不明显；可以直接补录，补上后匹配与出题都会更准。"
        )
    if not conf:
        note = (
            f"已识别 {recognized}/{total} 项。"
            "本次结果由大模型结构化产出，未提供逐字段置信度；如需置信度标记可改用规则引擎解析。"
        )
    return {
        "recognized": recognized,
        "total": total,
        "missing": missing,
        "note": note,
    }


def low_confidence_fields(conf: dict[str, float], labels: dict[str, str]) -> list[dict]:
    """低置信度字段清单（按置信度升序 = 最该先改的排最前）。

    返回 `[{key, label, confidence, level}]`；`level` 让前端不必自己定阈值：
    `low`(需修正) / `mid`(建议核对) / `high`(可信)。
    """
    out: list[dict] = []
    for key, label in labels.items():
        try:
            value = float((conf or {}).get(key) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value >= CONF_SECTION:
            level = "high"
        elif value >= LOW_CONFIDENCE:
            level = "mid"
        else:
            level = "low"
        out.append({"key": key, "label": label, "confidence": round(value, 2), "level": level})
    out.sort(key=lambda x: (x["confidence"], x["key"]))
    return out
