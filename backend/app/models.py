"""数据模型（文档 §3.6 数据存储设计）。

5 张核心表：user / resume / job_description / company / interview_session
外加 V1.5 的错题本表 wrong_question。
结构化结果统一存 TEXT(JSON)，便于换库与迭代。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """用户表：user_id、账号、昵称、手机号、学校、创建时间。"""

    __tablename__ = "user"

    user_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # 登录账号。不设 unique 约束：游客/演示账号的 username 都是空串，
    # 而 SQLite 的 UNIQUE 允许多个 NULL 却不允许多个 ""。唯一性在 services/auth.py 里显式校验。
    username: Mapped[str] = mapped_column(String(64), default="", index=True)
    # PBKDF2-HMAC-SHA256 派生值（格式 pbkdf2_sha256$迭代数$salt$hash），绝不存明文。
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    nickname: Mapped[str] = mapped_column(String(64), default="同学")
    phone: Mapped[str] = mapped_column(String(32), default="")
    school: Mapped[str] = mapped_column(String(128), default="")
    is_member: Mapped[int] = mapped_column(Integer, default=0)  # 0 免费 / 1 月度 / 2 年度
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class AuthToken(Base):
    """登录令牌表（服务端会话）。

    只存 token 的 SHA-256，不存原文：即使 app.db 泄露，也无法直接拿去冒充登录。
    退出登录即删除该行，因此令牌可被即时吊销（这点优于自包含的 JWT）。
    """

    __tablename__ = "auth_token"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("user.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    expires_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Resume(Base):
    """简历表：resume_id、user_id、原始PDF文件URL、解析后结构化JSON、解析状态、上传时间。"""

    __tablename__ = "resume"

    resume_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("user.user_id"), index=True)
    file_name: Mapped[str] = mapped_column(String(255), default="")
    file_url: Mapped[str] = mapped_column(String(512), default="")  # 原始 PDF 存储路径
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parsed_json: Mapped[str] = mapped_column(Text, default="{}")
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|parsed|failed
    parse_source: Mapped[str] = mapped_column(String(16), default="rule")  # llm|rule
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class JobDescription(Base):
    """JD表：jd_id、user_id、原始文本/文件、解析后结构化JSON、岗位名称、上传时间。"""

    __tablename__ = "job_description"

    jd_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("user.user_id"), index=True)
    source_type: Mapped[str] = mapped_column(String(16), default="text")  # text|file
    raw_text: Mapped[str] = mapped_column(Text, default="")
    parsed_json: Mapped[str] = mapped_column(Text, default="{}")
    job_title: Mapped[str] = mapped_column(String(128), default="")
    parse_status: Mapped[str] = mapped_column(String(16), default="pending")
    parse_source: Mapped[str] = mapped_column(String(16), default="rule")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class Company(Base):
    """公司表：company_id、公司名称、公司画像JSON、来源（自动/手动）、更新时间。"""

    __tablename__ = "company"

    company_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), index=True)
    profile_json: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(16), default="auto")  # auto|manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)


class InterviewSession(Base):
    """面试会话表：session_id、user_id、三个档案外键、匹配度报告、面试状态、对话记录、面试报告。"""

    __tablename__ = "interview_session"

    session_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), ForeignKey("user.user_id"), index=True)
    resume_id: Mapped[str] = mapped_column(String(32), default="")
    jd_id: Mapped[str] = mapped_column(String(32), default="")
    company_id: Mapped[str] = mapped_column(String(32), default="")

    job_title: Mapped[str] = mapped_column(String(128), default="")
    company_name: Mapped[str] = mapped_column(String(128), default="")
    mode: Mapped[str] = mapped_column(String(16), default="voice")  # voice|text
    interviewer: Mapped[str] = mapped_column(String(16), default="female")  # female|male

    match_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    dialogue_json: Mapped[str] = mapped_column(Text, default="[]")
    report_json: Mapped[str] = mapped_column(Text, default="{}")

    status: Mapped[str] = mapped_column(String(16), default="ongoing")  # ongoing|finished|aborted
    score: Mapped[float] = mapped_column(Float, default=0.0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # 体验增强 §4：报告中必须诚实区分「重录」与「依赖提示」，
    # 否则用户无法知道自己的分数里有没有外挂。两者只做计数，不参与评分算法。
    retake_count: Mapped[int] = mapped_column(Integer, default=0)
    hint_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class WrongQuestion(Base):
    """错题本表（V1.5）：自动收集单题得分低于 6 分的题目。"""

    __tablename__ = "wrong_question"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    job_title: Mapped[str] = mapped_column(String(128), default="")
    category: Mapped[str] = mapped_column(String(32), default="专业类")  # 专业类|逻辑类|表达类|应变类|匹配类
    # 原题所属环节（自我介绍/项目深挖/…）。体验增强 §9 的「3 题微面试」要按同一环节出题，
    # 因此必须留档；老数据为空时由 category 兜底映射。
    section: Mapped[str] = mapped_column(String(32), default="")
    focus: Mapped[str] = mapped_column(String(128), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    answer: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    issue: Mapped[str] = mapped_column(Text, default="")
    correct_thinking: Mapped[str] = mapped_column(Text, default="")
    reference: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class SpeechMetric(Base):
    """单题口播指标表（体验增强 §5 口播实时反馈）。

    设计意图：**只落库、不评分**。实时只给事实（语速 / 填充词 / 时长），
    评分仍走六维模型，避免用户「边答边掉分」的焦虑；同时这些数据是后续
    调优「表达沟通」维度阈值的唯一真实依据。

    带 `user_id` 以满足 `assert_owner` 铁律（越权一律 404）。
    """

    __tablename__ = "speech_metric"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    question_index: Mapped[int] = mapped_column(Integer, default=0)
    chars: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    chars_per_minute: Mapped[float] = mapped_column(Float, default=0.0)
    filler_count: Mapped[int] = mapped_column(Integer, default=0)
    # 命中的口头禅原文（如 ["那个", "就是"]），便于回溯是哪几个词刷出来的
    filler_words_json: Mapped[str] = mapped_column(Text, default="[]")
    level: Mapped[str] = mapped_column(String(16), default="good")  # slow|good|fast
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class ReportFeedback(Base):
    """报告反馈表（体验增强 §6）。

    承载两类反馈，写入后可按 session / question 统计，直接反哺 Prompt 调优：
    - 整份报告：`helpful` 有用/没用（沿用原有语义）；
    - 单题：`question_index` + `verdict` ∈ accurate|high|low（准 / 偏高 / 偏低）。

    带 `user_id` 以满足 `assert_owner` 铁律。
    """

    __tablename__ = "report_feedback"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    # -1 表示整份报告的反馈；>=0 表示第 N 题的反馈（与 report.questions[].index 对齐）
    question_index: Mapped[int] = mapped_column(Integer, default=-1)
    verdict: Mapped[str] = mapped_column(String(16), default="")  # accurate|high|low|""
    helpful: Mapped[int] = mapped_column(Integer, default=-1)  # 1 有用 / 0 没用 / -1 未表态
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class JobPosting(Base):
    """岗位信息表（全员共享的岗位库）。

    **这是全站唯一一张「所有人共享、不带 user_id」的业务表**，与 `company`（公共
    参考公司库）同类：岗位信息是大家共同维护的一份公开资料，投递进度才是个人数据。

    三条刻意的设计：

    1. **没有 user_id**。共享数据如果带上归属，就必然要回答「别人能不能看」，
       而这里的答案永远是「能看」；带一行没用的 user_id 只会让 `assert_owner`
       铁律在这里变成一个必须解释的例外。仅保留 `created_by` 记录**是谁导入的**，
       用于追溯，不参与任何权限判断（岗位库由全部登录用户共同维护）。
    2. **`posted_at` 而不是 `updated_at`**。它表示「这条岗位信息进入岗位库的时间」：
       导入时记为导入当天，此后**编辑岗位也不会改变它**。叫 `updated_at` 会误导
       后来者以为它随写入刷新，而真正随进度刷新的是 `JobProgress.progress_at`
       （页面上分别显示为「更新时间」与「进度更新时间」两个不同的东西）。
    3. **没有「编辑岗位」之外的产生途径**：岗位信息的唯一来源是管理员导入
       （Excel / CSV / 粘贴 / 载入示例）。界面上只保留删除，避免「界面改一处、
       表格改一处」两边对不上——这是刻意的产品决策：岗位库是台账，不是编辑器。
    """

    __tablename__ = "job_posting"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    company: Mapped[str] = mapped_column(String(120), index=True)
    nature: Mapped[str] = mapped_column(String(60), default="")  # 企业性质
    # 行业（互联网 / 金融 / 制造业…）。与「企业性质」分开：性质说的是「国企/民企/外企」，
    # 行业说的是「做什么生意」，两者在列表里是相邻但不同的两列，不要合并。
    industry: Mapped[str] = mapped_column(String(60), default="")
    audience: Mapped[str] = mapped_column(String(120), default="")  # 招聘对象
    # 多值字段统一存 JSON 数组文本：工作地点 / 专业要求 / 招聘岗位
    locations_json: Mapped[str] = mapped_column(Text, default="[]")
    majors_json: Mapped[str] = mapped_column(Text, default="[]")
    positions_json: Mapped[str] = mapped_column(Text, default="[]")
    notice_url: Mapped[str] = mapped_column(String(512), default="")  # 公告链接
    apply_url: Mapped[str] = mapped_column(String(512), default="")  # 投递链接
    note: Mapped[str] = mapped_column(Text, default="")  # 内推码 / 笔试形式 / 避坑点
    # 导入这张表的管理员 user_id（追溯用，不参与权限判断）
    created_by: Mapped[str] = mapped_column(String(32), default="")
    # 岗位信息的录入时间（= 页面上的「更新时间」），只读、不随编辑变化
    posted_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc)


class JobProgress(Base):
    """投递进度表：**每个用户只有自己的一份**。

    一个 (user_id, job_id) 组合只该有一行，因此这里不额外造主键约束，
    而是在服务层用「先查后写」的 upsert 保证；`status == 未投递` 时**直接删行**
    而不是写一个「未投递」状态——「没投过」本来就不该占一行存储，
    统计口径也就不用再过滤一次。

    与 `JobPosting.posted_at` 的分工（这两个时间不能混，混了一定被误读）：
      · `posted_at`   = 岗位信息什么时候进库的（系统记录，只读）；
      · `progress_at` = **我的进度**最后一次变动的时间，进度一变就刷新为当天。
    两个时间在页面上分别叫「更新时间」与「进度更新时间」，不是一回事。

    带 `user_id`，同样满足「个人资源表必须带 user_id」铁律：读到别人的进度
    一律按 404 处理（本项目不提供任何「看别人进度」的接口）。
    """

    __tablename__ = "job_progress"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    # 取值以 constants.JOB_STATUS_META 的键为准（服务端逐条校验，非法值 400）
    status: Mapped[str] = mapped_column(String(16), default="简历初筛")
    # 进度最后变动时间（= 页面上的「进度更新时间」）
    progress_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_utc, onupdate=now_utc)

