"""API 数据契约（前后端 + 各服务模块共同遵守的唯一事实来源）。

命名规范：字段名统一中文语义的英文短名；前端展示文案由前端负责映射。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# ============================================================
# 通用
# ============================================================


class OkResponse(BaseModel):
    ok: bool = True
    message: str = ""


class HealthResponse(BaseModel):
    status: str = "ok"
    # 机器可读的项目标识（纯 ASCII）。启动脚本用它确认「这个端口上跑的确实是本项目」：
    # 同级目录的另一个项目默认也监听 8000，只看「HTTP 有响应」会把它的页面当成我们的。
    # 用 ASCII 而不是 app 字段：FastAPI 的 JSON 响应头不带 charset，Windows PowerShell 5.1
    # 会按 Latin-1 解码，中文 app 名在脚本里变成乱码，字符串比对必然失败。
    app_id: str = "ai_project"
    app: str
    version: str
    llm_mode: Literal["live", "mock"]
    llm_provider: str
    tts_provider: str
    asr_provider: str


# ============================================================
# 报告生成进度（生成过程可视化）
# ============================================================


class ReportProgressStage(BaseModel):
    """单个阶段的展示状态。"""

    key: str
    label: str
    status: Literal["pending", "active", "done"] = "pending"
    # 子步骤说明（例如「第 1 轮六维评分」「二次校验」），只影响文案，不影响百分比
    detail: str = ""
    # 该阶段耗时（秒）：已完成的是终值，进行中的是「已跑多久」
    seconds: float | None = None


class ReportProgressResponse(BaseModel):
    session_id: str
    # 是否拿到了真实进度。多进程部署 / 尚未开始生成时为 False，前端据此退化为不确定进度条
    available: bool = False
    status: Literal["idle", "running", "done", "failed"] = "idle"
    stage: str = ""
    stage_label: str = ""
    stage_index: int = 0
    total_stages: int = 0
    # 只按**已真正完成**的阶段计算，不虚增
    percent: int = 0
    elapsed_seconds: float = 0.0
    # 评分方式：llm / rule / hybrid。已知后前端可给出「通常需要多久」的如实说明
    scoring_mode: str = ""
    # 超过预期时长仍在 running（可能是大模型排队，也可能是进程重启留下的记录）
    stale: bool = False
    note: str = ""
    message: str = ""
    stages: list[ReportProgressStage] = Field(default_factory=list)


# ============================================================
# 认证（登录 / 注册）
# ============================================================


class RegisterRequest(BaseModel):
    username: str
    password: str
    nickname: str = ""
    school: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthUser(BaseModel):
    """下发给前端的当前用户信息，**不含任何口令学字段**。"""

    user_id: str
    username: str = ""
    nickname: str = "同学"
    school: str = ""
    # 历史遗留字段（0 免费 / 1 月度 / 2 年度）。全部功能已对登录用户开放，
    # 会员体系不再参与任何判定，这里保留只是为了不破坏既有前端与数据库列。
    is_member: int = 0
    # 是否游客（未登录）。前端据此显示「登录后数据私有」提示与登录入口。
    is_guest: bool = False
    # 体验增强 §1：首页「准备度清单」需要一个「是不是全新用户」的判断依据。
    # 只回答「有没有」，不返回任何简历/JD 内容，避免把敏感数据塞进身份接口。
    # 仅 `GET /api/auth/me` 会填充（登录/注册响应里保持默认 False）。
    has_resume: bool = False
    has_jd: bool = False
    finished_sessions: int = 0


class AuthResponse(BaseModel):
    token: str
    expires_at: str = ""
    user: AuthUser
    # 注册/登录后给用户的一句话提示（可为空），前端据此弹一句欢迎语。
    message: str = ""


# ============================================================
# 模块一：档案（简历 / JD / 公司）
# ============================================================


class ResumeBasic(BaseModel):
    name: str = ""
    school: str = ""
    major: str = ""
    degree: str = ""
    grade: str = ""
    phone: str = ""
    email: str = ""


class EducationItem(BaseModel):
    school: str = ""
    major: str = ""
    degree: str = ""
    start: str = ""
    end: str = ""
    highlights: list[str] = Field(default_factory=list)


class InternshipItem(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str = ""
    duties: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: str = ""
    role: str = ""
    start: str = ""
    end: str = ""
    description: str = ""
    tech_stack: list[str] = Field(default_factory=list)


class CampusExperienceItem(BaseModel):
    """校园经历（文档 §3.4.1）：学生会 / 社团 / 志愿服务 / 社会实践 / 竞赛组织等。

    结构对齐 `ProjectItem`：`name` 是组织或活动名，`role` 是担任职务；
    含量化结果的条目单独收进 `highlights`，其余合并进 `description`。
    """

    name: str = ""
    role: str = ""
    start: str = ""
    end: str = ""
    description: str = ""
    highlights: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    """文档 §3.4.1 输出字段。"""

    basic: ResumeBasic = Field(default_factory=ResumeBasic)
    education: list[EducationItem] = Field(default_factory=list)
    internships: list[InternshipItem] = Field(default_factory=list)
    projects: list[ProjectItem] = Field(default_factory=list)
    campus_experience: list[CampusExperienceItem] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    awards: list[str] = Field(default_factory=list)
    self_evaluation: str = ""
    # 体验增强 §7：每个字段的解析置信度（0~1），只由规则引擎给出。
    # 低置信度字段前端用琥珀色高亮并前置排序，把「从头看一遍」变成「只改 3 个地方」。
    # LLM 结构化结果不臆造置信度（模型自评的置信度不可靠），此时为空 dict，前端退化为无标记。
    field_confidence: dict[str, float] = Field(default_factory=dict)


class FieldConfidence(BaseModel):
    """单字段的解析置信度（体验增强 §7）。

    `level` 由后端按统一阈值给出，前端不必自己定档，避免两端口径漂移：
    `high` 可信 / `mid` 建议核对 / `low` 需修正（会被高亮并前置排序）。
    """

    key: str
    label: str = ""
    confidence: float = 0.0
    level: Literal["high", "mid", "low"] = "low"


class FieldCoverage(BaseModel):
    """字段覆盖率（体验增强 §7）：`已识别 12/16 项` + 缺失项可直接补录。"""

    recognized: int = 0
    total: int = 0
    missing: list[str] = Field(default_factory=list)
    note: str = ""


class ResumeUploadResponse(BaseModel):
    resume_id: str
    file_name: str
    parse_status: str
    parse_source: Literal["llm", "rule"]
    parsed: ResumeProfile
    warnings: list[str] = Field(default_factory=list)
    summary_line: str = ""  # 前端「解析结果摘要」一行文案
    # 体验增强 §7：解析完成后的字段覆盖率卡片 + 逐字段置信度（按最该先改的排前）
    coverage: FieldCoverage = Field(default_factory=FieldCoverage)
    confidence_levels: list[FieldConfidence] = Field(default_factory=list)


class ResumeUpdateRequest(BaseModel):
    parsed: ResumeProfile


class ResumeTextRequest(BaseModel):
    """粘贴简历文本（没有 PDF 时的兜底入口，文档 §10 风险应对：多解析方案兜底）。"""

    text: str
    user_id: str = ""


# ---------------- JD ----------------


class JDBasic(BaseModel):
    title: str = ""
    department: str = ""
    location: str = ""


class JDRequirements(BaseModel):
    degree: str = ""
    experience: str = ""
    hard_skills: list[str] = Field(default_factory=list)
    soft_skills: list[str] = Field(default_factory=list)
    bonus: list[str] = Field(default_factory=list)


class JDProfile(BaseModel):
    """文档 §3.4.2 输出字段。"""

    basic: JDBasic = Field(default_factory=JDBasic)
    responsibilities: list[str] = Field(default_factory=list)
    requirements: JDRequirements = Field(default_factory=JDRequirements)
    keywords: list[str] = Field(default_factory=list)
    # 体验增强 §7：字段级解析置信度（0~1），语义同 ResumeProfile.field_confidence
    field_confidence: dict[str, float] = Field(default_factory=dict)


class JDParseRequest(BaseModel):
    text: str
    job_title: str = ""


class JDParseResponse(BaseModel):
    jd_id: str
    parse_status: str
    parse_source: Literal["llm", "rule"]
    parsed: JDProfile
    warnings: list[str] = Field(default_factory=list)
    # 体验增强 §7：字段覆盖率 + 逐字段置信度
    coverage: FieldCoverage = Field(default_factory=FieldCoverage)
    confidence_levels: list[FieldConfidence] = Field(default_factory=list)


class JDListItem(BaseModel):
    """岗位列表项（体验增强 §8：多 JD 对比只需要摘要，不下发职责全文）。"""

    jd_id: str
    title: str = ""
    department: str = ""
    location: str = ""
    degree: str = ""
    experience: str = ""
    hard_skill_count: int = 0
    parse_status: str = "pending"
    created_at: str = ""


class JDListResponse(BaseModel):
    items: list[JDListItem] = Field(default_factory=list)
    total: int = 0


class JDUpdateRequest(BaseModel):
    parsed: JDProfile


# ---------------- 公司 ----------------


class CompanyProfile(BaseModel):
    """文档 §3.4.3 公司画像。"""

    name: str = ""
    industry: str = ""
    scale: str = ""
    business: str = ""
    culture_tags: list[str] = Field(default_factory=list)
    interview_style: str = ""  # 压力面 / 结构化面 / 温和面 / 追问式
    style_desc: str = ""
    common_questions: list[str] = Field(default_factory=list)
    headquarters: str = ""
    note: str = "信息仅供参考"


class CompanyBrief(BaseModel):
    company_id: str = ""
    name: str
    industry: str = ""
    scale: str = ""
    interview_style: str = ""


class CompanySearchResponse(BaseModel):
    items: list[CompanyBrief] = Field(default_factory=list)
    total: int = 0
    auto_matched: bool = False
    profile: CompanyProfile | None = None


class CompanyManualRequest(BaseModel):
    name: str
    industry: str = ""
    scale: str = ""
    business: str = ""
    culture_tags: list[str] = Field(default_factory=list)
    interview_style: str = "结构化面"
    style_desc: str = ""


class CompanyResponse(BaseModel):
    company_id: str
    source: Literal["auto", "manual"]
    profile: CompanyProfile


# ---------------- 匹配度（文档 §3.5）----------------


class MatchItem(BaseModel):
    dimension: str
    detail: str


class MatchStrategy(BaseModel):
    focus: list[str] = Field(default_factory=list)  # 重点考察
    deep_dive: list[str] = Field(default_factory=list)  # 深挖经历
    pressure_test: list[str] = Field(default_factory=list)  # 压力测试


class MatchBreakdown(BaseModel):
    key: str
    dimension: str
    score: float  # 0-10
    weight: float
    detail: str
    # 体验增强 §8：每一维可下钻——命中的关键词（绿）与缺失的关键词（琥珀），
    # 以及"补一句就能提分"的具体建议；weight_hint 解释这一维为什么值这么多权重。
    hit_keywords: list[str] = Field(default_factory=list)
    miss_keywords: list[str] = Field(default_factory=list)
    suggestion: str = ""
    weight_hint: str = ""


class MatchRequest(BaseModel):
    resume_id: str
    jd_id: str
    company_id: str = ""


class MatchResponse(BaseModel):
    """文档 §3.5.2 输出示例。"""

    overall: int  # 0-100
    level: str
    matched: list[MatchItem] = Field(default_factory=list)
    weak: list[MatchItem] = Field(default_factory=list)
    strategy: MatchStrategy = Field(default_factory=MatchStrategy)
    breakdown: list[MatchBreakdown] = Field(default_factory=list)
    radar_target: dict[str, float] = Field(default_factory=dict)  # 目标岗位平均分（雷达图虚线）


# ============================================================
# 模块二：面试会话
# ============================================================


class InterviewStartRequest(BaseModel):
    resume_id: str
    jd_id: str
    company_id: str = ""
    mode: Literal["voice", "text"] = "voice"
    interviewer: Literal["female", "male"] = "female"
    question_count: int = 9  # 文档附录B：8-10 题
    user_id: str = ""
    # 体验增强 §9「单题特训」：只练一个薄弱环节的 3 题微面试。
    # retry_mode=True 时 question_count 会被压到 3，且题目全部集中在 focus_section。
    retry_mode: bool = False
    focus_section: str = ""
    # 触发本次特训的薄弱维度（会作为 weak_point 注入出题上下文，让题目更针对）
    focus_dimension: str = ""
    # 体验增强 §14：压力面试模式（追问更密、语气更直接、偏压力测试题）。
    # 这是**练习模式**，不是让 AI 真的刁难用户——报告里会明确标注，避免与其他场次直接比分数。
    pressure_mode: bool = False


class Turn(BaseModel):
    turn_id: str
    role: Literal["interviewer", "candidate", "system"]
    content: str
    focus: str = ""  # 考察点，如「需求拆解能力」
    section: str = ""  # 自我介绍 / 项目深挖 / 专业技能 / 场景题 / 行为面试 / 反问环节
    index: int = 0  # 第几题（从 1 开始，0 表示非题目发言）
    total: int = 0
    is_followup: bool = False
    reference_points: list[str] = Field(default_factory=list)  # 仅供评分/报告使用，不下发面试中
    speak_text: str = ""  # 去掉括号标注后的纯口语文本，供 TTS 使用
    created_at: str = ""


class ProgressInfo(BaseModel):
    """面试进度（体验增强 §2）。

    给顶部粘性条渲染用：进度用**点阵**而非百分比，剩余时间给**区间**而非精确值
    （给精确值反而会因估算误差引发焦虑）。`source` 说明区间是怎么来的，
    前端据此决定是否标注「按历史估算」。
    """

    index: int = 0  # 当前进行到第几题（从 1 开始；0 表示尚未开始）
    total: int = 0
    answered: int = 0  # 已作答题数（跳过也算「已处理」，另由 skipped 区分）
    stage: str = ""  # 当前环节：自我介绍 / 项目深挖 / ...
    elapsed_seconds: int = 0
    # [下限, 上限] 秒。优先用「本场已答题的中位耗时」，其次用该用户口播历史的中位数，
    # 都没有时按 45 秒/题估算——始终给区间而非精确值。
    estimated_remaining_seconds: list[int] = Field(default_factory=list)
    source: Literal["session", "history", "estimate"] = "estimate"
    # 与既有的「会话空闲超时自动收敛」机制衔接：界面需要据此说明
    # 「会话将保留，可稍后继续，报告仍可生成」，避免用户误以为数据丢失。
    idle_timeout_hours: float = 0.0
    can_resume: bool = True


class InterviewStartResponse(BaseModel):

    session_id: str
    status: str
    mode: str
    interviewer: str
    job_title: str
    company_name: str
    total_questions: int
    opening: Turn
    plan: dict[str, Any] = Field(default_factory=dict)
    match: MatchResponse | None = None


class InterviewAnswerRequest(BaseModel):
    question_id: str = ""  # 对应 Turn.turn_id
    answer_text: str
    answer_seconds: int = 0
    input_mode: Literal["voice", "text"] = "voice"
    # 体验增强 §4：重录覆盖当前题答案，不计惩罚，但报告中必须标注「本题重录 N 次」；
    # hint_used 表示本题用了「给我一点思路」，报告中计入「独立作答率」。
    # 二者都不参与评分算法，只做诚实披露。
    retake: bool = False
    hint_used: bool = False


class InterviewAnswerResponse(BaseModel):
    session_id: str
    status: str  # ongoing | finished
    next_turn: Turn | None = None
    question_index: int = 0
    total_questions: int = 0
    is_finished: bool = False
    progress: float = 0.0  # 兼容旧客户端：0~1 的完成比例
    progress_info: ProgressInfo | None = None  # 体验增强 §2：结构化进度


class InterviewSkipRequest(BaseModel):
    question_id: str = ""


class TranscriptItem(BaseModel):
    role: str
    content: str
    focus: str = ""
    section: str = ""
    index: int = 0
    created_at: str = ""
    # 体验增强 §4「中断恢复」需要：刷新页面后前端必须能拿到**当前题的 turn_id**，
    # 否则提交 /answer 时只能省略 question_id，失去「重复提交/过期提交」的并发保护。
    turn_id: str = ""
    answer_seconds: int = 0
    retake: bool = False
    hint_used: bool = False


class InterviewSessionState(BaseModel):
    session_id: str
    status: str
    job_title: str
    company_name: str
    mode: str
    interviewer: str
    question_index: int
    total_questions: int
    score: float = 0.0
    duration_seconds: int = 0
    dialogue: list[TranscriptItem] = Field(default_factory=list)
    has_report: bool = False
    # 体验增强：刷新/重进页面时恢复进度条与「重录·提示」计数
    progress: ProgressInfo | None = None
    retake_count: int = 0
    hint_count: int = 0


class SpeechMetricsRequest(BaseModel):
    """单题口播指标上报（体验增强 §5）。

    前端在回答结束时把本地统计结果上报，服务端**只落库、不评分**。
    所有字段都可缺省：前端算不出来（例如文字模式）时不报即可。
    """

    question_index: int = 0
    answer_text: str = ""
    duration_seconds: int = 0
    chars: int = 0
    chars_per_minute: float = 0.0
    filler_count: int = 0
    filler_words: list[str] = Field(default_factory=list)
    level: Literal["slow", "good", "fast"] = "good"


class SpeechMetricsResponse(BaseModel):
    ok: bool = True
    saved: bool = True
    # 回传服务端判定的结果，便于前端在报告中复用同一套阈值口径
    chars_per_minute: float = 0.0
    filler_count: int = 0
    filler_words: list[str] = Field(default_factory=list)
    level: Literal["slow", "good", "fast"] = "good"
    note: str = ""


# ============================================================
# 模块三：评分报告（文档 §5.2，7 个板块）
# ============================================================


class ReportOverview(BaseModel):
    """板块1：总览卡片。"""

    score: float  # 0-10，一位小数
    percent: int  # 百分制
    grade: str  # S/A/B/C/D/E 级
    grade_desc: str
    offer_probability: str
    one_line_comment: str
    duration_minutes: int = 0
    question_count: int = 0
    answered_count: int = 0


class BenchmarkInfo(BaseModel):
    """同岗位历史基准（体验增强 §6）。

    数据不足时必须 `available=False` 并给出 `note`，沿用项目既有的
    「演示数据一律标注」的诚实风格——绝不把凭空构造的基准伪装成真实分布。
    """

    available: bool = False
    note: str = ""
    sample_size: int = 0
    p25: float = 0.0
    p50: float = 0.0
    p75: float = 0.0
    scope: str = "all"  # all | job —— 基准是按全站还是按同岗位统计


class DimensionAnchor(BaseModel):
    """维度评价锚点：让抽象分数具体化（「什么样算 8 分」）。"""

    score: float
    level: str
    example: str


class DimensionScore(BaseModel):
    """板块2：六维评分。"""

    key: str
    name: str
    score: float
    weight: float
    comment: str
    evidence: list[str] = Field(default_factory=list)
    target: float = 0.0  # 目标岗位平均分
    improved: float = 0.0  # 相比上一次的变化
    # 体验增强 §6：评分归因（依据了什么）+ 评价锚点 + 同岗位基准
    basis: list[str] = Field(default_factory=list)
    anchors: list[DimensionAnchor] = Field(default_factory=list)
    benchmark: BenchmarkInfo = Field(default_factory=BenchmarkInfo)


class QuestionAnalysis(BaseModel):
    """板块3：逐题详细分析。"""

    index: int
    section: str = ""
    focus: str = ""
    question: str
    answer: str = ""
    score: float
    good: list[str] = Field(default_factory=list)
    improve: list[str] = Field(default_factory=list)
    reference: str = ""
    answer_seconds: int = 0
    too_short: bool = False
    # 体验增强 §4：跳过必须与低分区分开——混为一谈会直接损伤评分公信力。
    # 状态优先级：skipped（未作答） > retake/hint（外部辅助） > 低分。
    skipped: bool = False
    retake_count: int = 0
    hint_used: bool = False
    # 命中评分依据的回答原文片段，供前端高亮「结论的出处」
    evidence_spans: list[str] = Field(default_factory=list)
    # 用户在报告页对该题评分的反馈：accurate|high|low|""
    feedback_verdict: str = ""
    # 该题的口播指标（语速/填充词/时长），来自 speech_metric 表
    speech: SpeechMetricsResponse | None = None


class StrengthItem(BaseModel):
    """板块4：核心优势（3条）。"""

    title: str
    detail: str
    evidence: str = ""


class ImprovementItem(BaseModel):
    """板块5：待改进项（按优先级）。"""

    priority: Literal["high", "medium", "low"]
    dimension: str
    problem: str
    evidence: str = ""  # 引用回答原文
    actions: list[str] = Field(default_factory=list)  # 3 条可执行步骤
    resources: list[str] = Field(default_factory=list)


class PlanTask(BaseModel):
    type: str  # 知识补充 | 模拟练习 | 复盘总结
    title: str
    detail: str
    duration_min: int = 30


class PlanDay(BaseModel):
    """板块6：个性化 7 天提升计划。"""

    day: int
    theme: str
    tasks: list[PlanTask] = Field(default_factory=list)


class ProgressPoint(BaseModel):
    """板块7：历史进步追踪。"""

    session_id: str
    date: str
    score: float
    job_title: str = ""


class ProgressTrack(BaseModel):
    history: list[ProgressPoint] = Field(default_factory=list)
    fastest_dimension: str = ""
    fastest_delta: float = 0.0
    still_weak_dimension: str = ""
    summary: str = ""


class ReportMeta(BaseModel):
    scored_at: str = ""
    model: str = "mock-engine"
    scoring_mode: Literal["llm", "rule", "hybrid"] = "rule"
    cheating_flags: list[str] = Field(default_factory=list)
    feedback_helpful: bool | None = None
    # 这份报告是否**没能**写进数据库（落库失败 / 走了极端降级分支）。
    # True 时接口仍返回完整报告（契约 §3：不允许抛 500），但下一次读取会重新生成，
    # 分数可能不同，因此前端必须提示用户，否则用户无从判断"刷新后分数变了"是否正常。
    persist_failed: bool = False
    # 体验增强 §6：AI 生成标识与基准说明，前端必须在首屏显著位置展示，
    # 不得让 AI 评分看起来像真人 HR 的判断。
    ai_generated: bool = True
    benchmark_note: str = ""


class InterviewReport(BaseModel):
    session_id: str
    overview: ReportOverview
    dimensions: list[DimensionScore] = Field(default_factory=list)
    radar_target: dict[str, float] = Field(default_factory=dict)
    questions: list[QuestionAnalysis] = Field(default_factory=list)
    strengths: list[StrengthItem] = Field(default_factory=list)
    improvements: list[ImprovementItem] = Field(default_factory=list)
    plan7days: list[PlanDay] = Field(default_factory=list)
    progress: ProgressTrack = Field(default_factory=ProgressTrack)
    meta: ReportMeta = Field(default_factory=ReportMeta)
    # 体验增强 §6：可信度标识与全站/同岗位基准
    ai_disclaimer: str = (
        "本报告由 AI 生成，仅供参考，不构成录用判断。评分依据已在每个维度下方展开。"
    )
    benchmark: BenchmarkInfo = Field(default_factory=BenchmarkInfo)
    # 本题「重录 / 依赖提示」的场次汇总，供报告顶部与「独立作答率」展示
    retake_count: int = 0
    hint_count: int = 0
    independent_answer_rate: float = 1.0
    # 体验增强 §9：若本场是「单题特训」的微面试，标出它练的是哪个环节/维度，
    # 报告页据此展示「上次 X → 本次 Y」的前后对比。
    retry_section: str = ""
    retry_dimension: str = ""
    # 体验增强 §14：本场是否为压力面试模式 / 用了哪个行业的专项题库
    pressure_mode: bool = False
    industry: str = ""
    industry_bank: bool = False


class ReportFeedbackRequest(BaseModel):
    """报告反馈（体验增强 §6）。

    `helpful` 保持可选以兼容既有前端；逐题反馈用 `question_index` + `verdict`。
    """

    helpful: bool | None = None
    comment: str = ""
    # -1 表示整份报告；>=0 表示第 N 题（与 report.questions[].index 对齐）
    question_index: int = -1
    verdict: Literal["accurate", "high", "low", ""] = ""


# ============================================================
# 语音（TTS / ASR）
# ============================================================


class TTSSentence(BaseModel):
    text: str
    start: float = 0.0
    end: float = 0.0


class TTSRequest(BaseModel):
    text: str
    voice: Literal["female", "male"] = "female"
    rate: str = "+0%"


class TTSResponse(BaseModel):
    provider: str  # edge | aliyun | browser
    audio_url: str = ""  # 为空则前端使用浏览器 speechSynthesis
    audio_base64: str = ""
    mime: str = "audio/mpeg"
    sentences: list[TTSSentence] = Field(default_factory=list)
    duration_estimate: float = 0.0
    fallback_text: str = ""


class ASRResponse(BaseModel):
    provider: str  # browser | aliyun | whisper
    text: str = ""
    confidence: float = 0.0
    is_final: bool = True


# ============================================================
# 错题本 / 排行榜（V1.5）
# ============================================================


class TrendPoint(BaseModel):
    """趋势点（体验增强 §9）：同维度在某一日的得分。"""

    date: str = ""
    score: float = 0.0


class GrowthDay(BaseModel):
    date: str = ""
    weekday: str = ""
    sessions: int = 0
    done: bool = False
    is_today: bool = False


class GrowthWeek(BaseModel):
    label: str = ""
    sessions: int = 0
    avg_score: float = 0.0


class GrowthDimension(BaseModel):
    key: str
    name: str = ""
    first: float = 0.0
    last: float = 0.0
    delta: float = 0.0


class GrowthSummary(BaseModel):
    """成长面板（体验增强 §12：进步排行 · 连续打卡）。

    只陈述事实（连续几天、这周练了几场、各维度从多少到多少），不做惩罚式设计。
    """

    streak_days: int = 0
    best_streak: int = 0
    total_sessions: int = 0
    total_minutes: int = 0
    active_days: int = 0
    last_7_days: list[GrowthDay] = Field(default_factory=list)
    weeks: list[GrowthWeek] = Field(default_factory=list)
    dimension_delta: list[GrowthDimension] = Field(default_factory=list)
    next_goal: str = ""
    note: str = ""


class WrongQuestionItem(BaseModel):
    id: str
    session_id: str
    job_title: str = ""
    category: str = ""
    section: str = ""  # 原题环节（体验增强 §9：微面试按同一环节出题）
    dimension_key: str = ""  # 对应的六维 key（同维度前后对比）
    focus: str = ""
    question: str = ""
    answer: str = ""
    score: float = 0.0
    issue: str = ""
    correct_thinking: str = ""
    reference: str = ""
    retry_count: int = 0
    created_at: str = ""
    # 体验增强 §9：同维度历次得分（时间升序）+ 最近一次分数（前后对比的基准）
    trend: list[TrendPoint] = Field(default_factory=list)
    before_score: float = 0.0


class WrongQuestionListResponse(BaseModel):
    total: int = 0
    by_category: dict[str, int] = Field(default_factory=dict)
    items: list[WrongQuestionItem] = Field(default_factory=list)


class RetryWrongQuestionRequest(BaseModel):
    wrong_id: str
    user_id: str = ""
    # 体验增强 §9：是否同时开一场「3 题微面试」。默认开启，
    # 老客户端不传时行为不变（只返回参考思路与重做次数）。
    start_micro: bool = False


class MicroSessionPlan(BaseModel):
    """单题特训的微面试计划（体验增强 §9）。"""

    session_id: str
    total: int = 3
    focus_section: str = ""
    focus_dimension: str = ""
    dimension_name: str = ""
    # 前后对比的基准：上次该维度的得分（来自该用户已完成面试的实测分布）
    before_score: float = 0.0
    before_trend: list[TrendPoint] = Field(default_factory=list)
    opening: Turn | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    match: MatchResponse | None = None


class RetryWrongQuestionResponse(BaseModel):
    wrong_id: str
    question: str = ""
    reference: str = ""
    tips: list[str] = Field(default_factory=list)
    retry_count: int = 0
    micro: MicroSessionPlan | None = None


class LeaderboardItem(BaseModel):
    rank: int
    nickname: str
    score: float
    school: str = ""
    job_title: str = ""
    badge: str = ""


class LeaderboardResponse(BaseModel):
    type: str
    title: str
    items: list[LeaderboardItem] = Field(default_factory=list)
    my_rank: int = 0
    note: str = ""


# ============================================================
# 元信息
# ============================================================


class DimensionMeta(BaseModel):
    key: str
    name: str
    weight: float
    desc: str


class MetaOptionsResponse(BaseModel):
    version: str
    llm_mode: str
    llm_provider: str
    tts_provider: str
    asr_provider: str
    dimensions: list[DimensionMeta] = Field(default_factory=list)
    grades: list[dict[str, Any]] = Field(default_factory=list)
    interview_sections: list[str] = Field(default_factory=list)
    hot_companies: list[CompanyBrief] = Field(default_factory=list)
    interviewer_options: list[dict[str, str]] = Field(default_factory=list)


# ============================================================
# 岗位信息汇总（共享岗位库 + 各人独立投递进度）
# ============================================================


class JobRecord(BaseModel):
    """一条「岗位机会 + （我看到的）投递进度」。

    这是前端列表与统计页唯一的数据形状：岗位信息是共享的，
    `progress` / `progress_at` 两项来自当前用户自己的进度表。
    """

    job_id: str
    company: str = ""
    nature: str = ""
    industry: str = ""
    audience: str = ""
    locations: list[str] = Field(default_factory=list)
    majors: list[str] = Field(default_factory=list)
    positions: list[str] = Field(default_factory=list)
    notice_url: str = ""
    apply_url: str = ""
    note: str = ""
    # 「更新时间」= 岗位信息进库时间（只读，不随编辑变化）
    posted_at: str = ""
    # 我的投递进度（默认「未投递」）与「进度更新时间」（进度一变就刷新）
    progress: str = "未投递"
    progress_at: str = ""


class JobListResponse(BaseModel):
    items: list[JobRecord] = Field(default_factory=list)
    total: int = 0
    # **昨天**新入库的岗位条数：列表上方「昨日更新 N 家」醒目标注的数据源。
    # 由服务端按**本地时区**统计（见 job_store.count_posted_yesterday），
    # 不受前端筛选 / 搜索影响——它是「昨天岗位库新增了多少」，不是「筛选结果里有多少」。
    # 统计上一日而不是今天：今天的数据还在导入、数字会跳，昨天是一段已经走完的完整区间。
    posted_yesterday: int = 0
    # 状态字典由后端下发，前端不再各写一份（含阶段点数与分组顺序）
    statuses: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    note: str = ""


class JobUpsertRequest(BaseModel):
    """新增 / 修改一条岗位信息。

    字段与 `JobRecord` 对齐，但**不含** progress / progress_at / posted_at：
    进度只能由本人通过 `/jobs/{id}/progress` 改，岗位录入时间由系统记录。

    三个多值字段刻意接受「数组或字符串」两种形状：界面导入时前端已经拆成数组，
    但直接调接口的人（或脚本）更可能写 `"北京、上海"`。服务端的 `split_tags`
    本来就能处理这两种输入，模型层如果只收数组，就会把它变成一个 422 —— 明明
    下一步就能正确解析，却在门口把请求挡回去，属于自找的摩擦。
    """

    company: str = ""
    nature: str = ""
    industry: str = ""
    audience: str = ""
    locations: list[str] | str = Field(default_factory=list)
    majors: list[str] | str = Field(default_factory=list)
    positions: list[str] | str = Field(default_factory=list)
    notice_url: str = ""
    apply_url: str = ""
    note: str = ""


class JobBulkRequest(BaseModel):
    """批量导入（仅管理员）。

    `dedupe=False`（**默认**）不去重：源表里有多少行就导入多少行，
    同一家企业在不同批次 / 岗位方向下重复出现是校招汇总表的常态，
    默认去重会把后面的行悄悄吃掉。需要按企业名合并时显式传 `dedupe=True`。

    导入**只读取 8 个岗位信息字段**（不含公告链接与备注），
    源表里的「投递进度 / 更新时间」即使同名也会被忽略——这是刻意的：
    从别处拿到的汇总表不该污染自己的投递记录。
    """

    jobs: list[JobUpsertRequest] = Field(default_factory=list)
    dedupe: bool = False


class JobBulkResponse(BaseModel):
    ok: bool = True
    added: int = 0
    skipped: int = 0
    total: int = 0
    message: str = ""


class JobProgressRequest(BaseModel):
    """设置我的投递进度；`status` 取值以 `constants.JOB_STATUS_META` 为准。"""

    status: str


class JobProgressResponse(BaseModel):
    ok: bool = True
    job_id: str = ""
    status: str = "未投递"
    progress_at: str = ""
    message: str = ""


class JobFunnelStep(BaseModel):
    name: str
    value: int = 0
    # 与上一级相比的转化率（%，投递一级固定 100）
    rate: int = 0
    # 相对投递总数的长度百分比（用于进度条宽度）
    percent: int = 0


class JobBarItem(BaseModel):
    name: str
    value: int = 0
    percent: int = 0
    type: str = ""


class JobStatsResponse(BaseModel):
    """投递统计（**只统计「投递进度不为未投递」的岗位**）。

    聚合在后端算，是为了让「漏斗怎么递进推算」「已挂怎么归因」只有一个实现；
    明细表的筛选与排序仍在前端本地做（数据量小、交互要即时）。
    """

    total: int = 0
    applied: int = 0
    applied_rate: int = 0
    active: int = 0
    fail: int = 0
    # 终态通过的条数。字段名用 passed 而不是 pass —— 后者是 Python 关键字，
    # 不能作为字段名（写成 pass 会直接 SyntaxError）。
    passed: int = 0
    reject_total: int = 0
    rejection_rate: int = 0
    funnel: list[JobFunnelStep] = Field(default_factory=list)
    rejected: list[JobBarItem] = Field(default_factory=list)
    status_dist: list[JobBarItem] = Field(default_factory=list)
    nature_dist: list[JobBarItem] = Field(default_factory=list)
    nature_kinds: int = 0
    note: str = ""
