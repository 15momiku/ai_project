"""Prompt 模板库（对齐文档 §5.3、§6.2.1 与附录 A 的核心 Prompt 清单）。

清单：
1. 简历结构化提取  PDF文本 -> 结构化JSON
2. JD结构化提取    JD文本 -> 岗位画像JSON
3. 公司画像补全    公司名 -> 公司画像JSON
4. 面试官对话      三份档案+对话历史 -> 下一个问题
5. 六维评分        完整面试记录 -> 评分报告JSON
6. 改进建议生成    薄弱项 -> 7天提升计划
"""

from __future__ import annotations

import json
from typing import Any

# ============================================================
# 1. 简历结构化提取
# ============================================================
RESUME_SYSTEM = "你是一名资深HR，擅长把非结构化简历文本抽取为严格 JSON。只输出 JSON，不要解释。"

RESUME_USER = """请从下面的简历文本中抽取结构化信息，输出 JSON，字段必须完整（缺失填空字符串或空数组）：

{{
  "basic": {{"name":"","school":"","major":"","degree":"","grade":"","phone":"","email":""}},
  "education": [{{"school":"","major":"","degree":"","start":"","end":"","highlights":[]}}],
  "internships": [{{"company":"","title":"","start":"","end":"","duties":[],"achievements":[]}}],
  "projects": [{{"name":"","role":"","start":"","end":"","description":"","tech_stack":[]}}],
  "campus_experience": [{{"name":"","role":"","start":"","end":"","description":"","highlights":[]}}],
  "skills": [],
  "awards": [],
  "self_evaluation": ""
}}

要求：
- 时间统一为 YYYY.MM 或 YYYY 格式
- 实习/项目描述保留原文中的量化成果（如"提升30%"）
- campus_experience 是**校园经历**：学生会、社团、志愿服务、社会实践、竞赛组织等，
  name 填组织或活动名称，role 填担任职务（如"副主席""社长""志愿者"），
  含量化结果的条目放进 highlights，其余描述合并进 description；
  简历中若没有校园经历，填空数组 []
- 不要把实习或课程项目写进 campus_experience
- skills 抽成短标签（如"Python"、"Figma"、"SQL"），不要整句
- 不要编造简历中不存在的信息

简历文本：
---
{resume_text}
---"""


def resume_prompt(resume_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": RESUME_SYSTEM},
        {"role": "user", "content": RESUME_USER.format(resume_text=resume_text[:12000])},
    ]


# ============================================================
# 2. JD 结构化提取
# ============================================================
JD_SYSTEM = "你是一名资深招聘专家，擅长把岗位JD抽取为严格 JSON。只输出 JSON，不要解释。"

JD_USER = """请从下面的岗位 JD 中抽取结构化信息，输出 JSON：

{{
  "basic": {{"title":"","department":"","location":""}},
  "responsibilities": ["职责1","职责2"],
  "requirements": {{"degree":"","experience":"","hard_skills":[],"soft_skills":[],"bonus":[]}},
  "keywords": []
}}

要求：
- responsibilities 提取 3-6 条，动词开头
- hard_skills 是可通过技能验证的硬技能（如 SQL、Axure、Python、Excel）
- soft_skills 是软素质（如沟通表达、抗压、学习能力）
- bonus 是加分项
- keywords 是行业/业务方向/产品类型标签

JD 文本：
---
{jd_text}
---"""


def jd_prompt(jd_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": JD_SYSTEM},
        {"role": "user", "content": JD_USER.format(jd_text=jd_text[:8000])},
    ]


# ============================================================
# 3. 公司画像
# ============================================================
COMPANY_SYSTEM = "你是一名熟悉中国校招市场的职业规划师，输出严格 JSON。"

COMPANY_USER = """请给出公司「{name}」的校招画像，输出 JSON：

{{
  "name":"{name}",
  "industry":"","scale":"","business":"",
  "culture_tags":[],
  "interview_style":"压力面|结构化面|温和面|追问式",
  "style_desc":"",
  "common_questions":[],
  "headquarters":""
}}

要求：
- interview_style 选一个最贴近的
- common_questions 给出 3-5 个该公司常考的面试题
- 不确定的信息写"待确认"，不要编造具体数据"""


def company_prompt(name: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": COMPANY_SYSTEM},
        {"role": "user", "content": COMPANY_USER.format(name=name)},
    ]


# ============================================================
# 4. 面试官系统 Prompt（文档 §6.2.1 原文结构）
# ============================================================
INTERVIEWER_SYSTEM = """# 角色
你是一位有10年经验的{company_name}{job_title}面试官，正在校招面试一名大学生。

# 公司背景
- 行业：{industry}
- 文化：{culture_tags}
- 面试风格：{interview_style}
- 业务重点：{business}

# 岗位要求（来自JD）
- 岗位职责：{responsibilities}
- 核心技能要求：{hard_skills}
- 软技能要求：{soft_skills}
- 加分项：{bonus}

# 候选人简历摘要
- 教育：{education}
- 实习经历：{internships}
- 项目经历：{projects}
- 技能栈：{skills}
- 薄弱项（需重点考察）：{weak_points}

# 面试规则
1. 先简短开场（结合公司风格），然后逐题提问，每次只问一个问题
2. 提问策略：60%围绕简历经历深挖（特别是与JD相关的），30%围绕JD核心技能考察（特别是薄弱项），10%结合公司文化和业务场景
3. 对薄弱项适当追问，观察应变和学习能力
4. 每次提问后在括号内标注考察点（如「考察：需求拆解能力」）
5. 根据回答质量决定是否追问细节，回答模糊就追问"具体怎么做的""数据是多少"
6. 面试结构：自我介绍→项目深挖（2-3题）→专业技能（2-3题）→场景题（1-2题）→行为面试（1题）→反问环节
7. 语气根据公司风格调整（如字节偏压力面、国企偏温和）
8. 用户说"结束"或答完最后一题时说结束语

# 禁止事项
- 不要问简历上已经明确写了答案的问题
- 不要问与岗位无关的问题
- 不要一次问多个问题

# 输出格式（严格 JSON，不要任何多余文字）
{{"question":"面试官要说的完整口语内容，含括号内的考察点标注","focus":"考察点名称（只填名称本身，例如「需求拆解能力」；不要带「考察点」「如」这类前缀字样）","section":"{sections}","is_followup":false,"reference_points":["评分参考要点1","要点2"]}}"""


def interviewer_system(profile: dict[str, Any]) -> str:
    return INTERVIEWER_SYSTEM.format(
        company_name=profile.get("company_name") or "某公司",
        job_title=profile.get("job_title") or "岗位",
        industry=profile.get("industry") or "互联网",
        culture_tags="、".join(profile.get("culture_tags") or []) or "未提供",
        interview_style=profile.get("interview_style") or "结构化面",
        business=profile.get("business") or "未提供",
        responsibilities="；".join(profile.get("responsibilities") or []) or "未提供",
        hard_skills="、".join(profile.get("hard_skills") or []) or "未提供",
        soft_skills="、".join(profile.get("soft_skills") or []) or "未提供",
        bonus="、".join(profile.get("bonus") or []) or "无",
        education=profile.get("education") or "未提供",
        internships=profile.get("internships") or "无",
        projects=profile.get("projects") or "无",
        skills="、".join(profile.get("skills") or []) or "未提供",
        weak_points="、".join(profile.get("weak_points") or []) or "无",
        sections="|".join(["自我介绍", "项目深挖", "专业技能", "场景题", "行为面试", "反问环节"]),
    )


def interviewer_next_question(
    profile: dict[str, Any],
    dialogue: list[dict[str, str]],
    section: str,
    index: int,
    total: int,
    is_followup: bool,
) -> list[dict[str, str]]:
    convo = "\n".join(
        f"{'面试官' if d.get('role') == 'interviewer' else '候选人'}：{d.get('content', '')}"
        for d in dialogue[-12:]
    )
    hint = (
        f"现在是第 {index}/{total} 题，属于「{section}」环节。"
        + ("请针对候选人上一个回答继续追问细节。" if is_followup else "请提出该环节的下一道问题。")
    )
    return [
        {"role": "system", "content": interviewer_system(profile)},
        {"role": "user", "content": f"# 当前对话历史\n{convo}\n\n# 任务\n{hint}"},
    ]


def interviewer_followup_decision(dialogue: list[dict[str, str]]) -> list[dict[str, str]]:
    last = dialogue[-1]["content"] if dialogue else ""
    return [
        {
            "role": "system",
            "content": "你是一名面试官助手。判断候选人的回答是否需要追问。只输出 JSON。",
        },
        {
            "role": "user",
            "content": (
                f"候选人回答：{last}\n\n"
                '输出 {{"need_followup": true/false, "reason":"", "probe":"追问问题"}}\n'
                "需要追问的情形：回答空泛、只有结论没有过程、没有数据、明显回避问题。"
            ),
        },
    ]


def interviewer_closing(profile: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": interviewer_system(profile)},
        {
            "role": "user",
            "content": "面试已结束。请用 2-3 句话给出专业、温和的结束语，告知候选人报告即将生成。只输出结束语本身。",
        },
    ]


# ============================================================
# 5. 六维评分（文档 §5.3 原文结构）
# ============================================================
SCORING_SYSTEM = "你是一位资深面试评委，只输出严格 JSON。"

SCORING_USER = """你是一位资深面试评委，请根据以下面试记录对候选人进行专业评分。

【岗位信息】岗位：{job_title} 公司：{company_name}
【候选人背景】{resume_brief}
【岗位核心要求】{jd_brief}
【完整面试记录】{dialogue}

【评分要求】从6个维度评分（1-10分，允许小数）：
1. 专业能力（25%） 2. 逻辑思维（20%） 3. 表达沟通（15%）
4. 岗位匹配度（20%） 5. 应变能力（10%） 6. 文化适配（10%）

【输出格式】严格JSON：
{{
  "overview":{{"score":7.2,"grade":"B级","grade_desc":"基本合格，有竞争力","offer_probability":"50-70%","one_line_comment":"一句话评价"}},
  "dimensions":[
    {{"key":"professional","score":7.5,"comment":"评语","evidence":["引用候选人回答原文"]}},
    {{"key":"logic","score":7.0,"comment":"","evidence":[]}},
    {{"key":"expression","score":7.0,"comment":"","evidence":[]}},
    {{"key":"matching","score":7.0,"comment":"","evidence":[]}},
    {{"key":"adaptability","score":6.5,"comment":"","evidence":[]}},
    {{"key":"culture_fit","score":7.0,"comment":"","evidence":[]}}
  ],
  "questions":[
    {{"index":1,"score":7.0,"good":["做得好1"],"improve":["待改进1"],"reference":"参考答案要点"}}
  ],
  "strengths":[{{"title":"","detail":"","evidence":""}}],
  "improvements":[
    {{"priority":"high","dimension":"专业能力","problem":"问题表现","evidence":"引用回答原文",
      "actions":["步骤1","步骤2","步骤3"],"resources":["推荐资源1"]}}
  ]
}}

【评分原则】
- 严格按应届生校招标准评分，不要放水
- 6分=及格线，8分以上=优秀，不要普遍给高分
- 评语要具体，引用候选人回答原文作为证据
- 改进方法要可操作，不要说"多练习"这种空话
- 待改进项最多3条，按优先级排序
- strengths 固定 3 条"""


def scoring_prompt(
    job_title: str,
    company_name: str,
    resume_brief: str,
    jd_brief: str,
    dialogue_text: str,
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SCORING_SYSTEM},
        {
            "role": "user",
            "content": SCORING_USER.format(
                job_title=job_title,
                company_name=company_name,
                resume_brief=resume_brief[:2500],
                jd_brief=jd_brief[:2000],
                dialogue=dialogue_text[:12000],
            ),
        },
    ]


# 二次校验 Prompt（文档 §5.4：关键维度用第二个 Prompt 独立评分）
SECOND_PASS_SYSTEM = "你是一名严格的面试评审，只输出 JSON。为避免打分偏差，请独立重新评估。"
SECOND_PASS_USER = """请独立评估该候选人「专业能力」与「岗位匹配度」两项，只输出这两项分数：

岗位：{job_title}（{jd_brief}）
面试记录：
{dialogue}

输出：{{"professional": 7.0, "matching": 6.5, "reason": ""}}
注意：独立判断，不要参考任何已有分数。"""


def second_pass_prompt(job_title: str, jd_brief: str, dialogue_text: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SECOND_PASS_SYSTEM},
        {
            "role": "user",
            "content": SECOND_PASS_USER.format(
                job_title=job_title, jd_brief=jd_brief[:1200], dialogue=dialogue_text[:10000]
            ),
        },
    ]


# ============================================================
# 6. 改进建议 / 7天提升计划
# ============================================================
PLAN_SYSTEM = "你是一名大学生求职教练，擅长把面试薄弱项拆成可执行的每日训练任务。只输出 JSON。"

PLAN_USER = """根据以下薄弱项，生成 7 天个性化提升计划，输出 JSON：

薄弱项：{weak_points}
目标岗位：{job_title}
可用资源偏好：{prefer}

{{
  "days":[
    {{"day":1,"theme":"主题","tasks":[
      {{"type":"知识补充","title":"任务标题","detail":"具体怎么做","duration_min":30}},
      {{"type":"模拟练习","title":"","detail":"","duration_min":20}}
    ]}}
  ]
}}

要求：
- 每天 2-3 个任务，类型从「知识补充 / 模拟练习 / 复盘总结」中选
- 任务必须具体到可立刻执行（含产出物）
- 7 天必须覆盖所有薄弱项，先急后缓
- 严禁出现"多练习""加强学习"这类空话"""


def plan_prompt(weak_points: list[str], job_title: str, prefer: str = "免费公开资源") -> list[dict[str, str]]:
    return [
        {"role": "system", "content": PLAN_SYSTEM},
        {
            "role": "user",
            "content": PLAN_USER.format(
                weak_points=json.dumps(weak_points, ensure_ascii=False),
                job_title=job_title,
                prefer=prefer,
            ),
        },
    ]
