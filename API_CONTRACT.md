# 校招全流程助手 · 接口契约（唯一事实来源）

> 所有模块必须严格遵守本文件。数据契约以 `backend/app/schemas.py` 为准，业务常量以 `backend/app/constants.py` 为准。
> 任何模块都不得修改 `schemas.py` / `constants.py` / `config.py` / `db.py` / `models.py` / `deps.py` / `prompts/templates.py` / `services/llm.py` 的既有签名。

Base URL：`http://127.0.0.1:8000`，所有业务接口前缀 `/api`。前端开发服务器 `http://127.0.0.1:5173` 通过 Vite proxy 把 `/api` 转发到 8000。

---

## 0. 模块归属（谁写哪个文件）

| 模块 | 文件 | 负责人 |
| --- | --- | --- |
| 认证与数据私有 | `app/services/auth.py`<br>`app/routers/auth.py` | 主控 |
| 模块一 解析与匹配 | `app/services/pdf_parser.py`<br>`app/services/resume_parser.py`<br>`app/services/jd_parser.py`<br>`app/services/company_service.py`<br>`app/services/matching.py`<br>`app/services/sample_data.py`<br>`app/routers/profile.py` | Agent A |
| 模块二 面试引擎 | `app/services/interview_engine.py`<br>`app/services/session_housekeeping.py`<br>`app/routers/interview.py` | Agent B |
| 模块三 评分与报告 | `app/services/scoring.py`<br>`app/services/plan.py`<br>`app/services/report_service.py`<br>`app/services/practice_store.py`<br>`app/services/analytics.py`<br>`app/routers/report.py`<br>`app/routers/practice.py` | Agent C |
| 体验增强增量（均为纯计算，无新依赖） | `app/services/speech_metrics.py`（语速/填充词阈值）<br>`app/services/benchmark.py`（同岗位 P25/P50/P75）<br>`app/services/field_quality.py`（置信度分级与覆盖率）<br>`app/services/analytics.py::build_growth`（成长面板聚合） | 主控 |
| 媒体（TTS/ASR） | `app/services/media.py`<br>`app/routers/media.py` | 主控 |
| 岗位信息汇总（共享岗位库 + 各人独立投递进度 + 统计） | `app/services/job_store.py`<br>`app/routers/jobs.py`<br>`app/data/jobs_seed.json` | 主控 |
| 前端 H5 | `frontend/**` | Agent D |

---

## 1. 跨模块固定函数签名

### 1.0 `app/services/auth.py` + `app/deps.py`（主控提供，**所有写权限的模块都要用**）

身份只从 `Authorization: Bearer <token>` 解析，**请求体/查询串里的 `user_id` 一律忽略**
（它是客户端可任意伪造的字符串，不再是身份来源，仅为兼容旧调用方而保留在 schema 中）。

```python
# app/services/auth.py（纯标准库，不引入第三方加密依赖）
class AuthError(Exception):            # .message / .status_code
def hash_password(password: str) -> str                 # pbkdf2_sha256$迭代$salt$hash
def verify_password(password: str, stored: str) -> bool # 恒定时间比较
def register(db, username, password, nickname="", school="") -> User   # 账号重复抛 AuthError(409)
def authenticate(db, username, password) -> User        # 失败统一 AuthError(401)
def issue_token(db, user) -> tuple[str, datetime]       # 返回 (明文令牌, 到期时间)
def resolve_token(db, token) -> User | None             # 无效/过期/已吊销 -> None
def revoke_token(db, token) -> None                     # 退出登录，即时生效
def purge_expired_tokens(db) -> int                     # 启动自愈

# app/deps.py
def current_user(authorization: str | None = Header(None), db=Depends(get_db)) -> User
    # 令牌有效 -> 该用户；令牌无效 -> 401；无令牌且 allow_guest -> 公共游客账号；否则 401
def is_guest(user) -> bool
def assert_owner(row, user, what="资源") -> Any          # 非本人 -> 404（不是 403）
def auth_user_payload(user) -> dict                      # 不含任何口令学字段
```

**数据私有三条铁律**

1. 记不清用户是谁时，用 `Depends(current_user)`，**不要**再从 `payload.user_id` / `Query(user_id)` 取。
2. 凡是按 id 取个人资源的接口，必须 `assert_owner(row, user)`；**返回 404 而不是 403**——
   403 等于告诉攻击者「这个 id 真实存在，只是你没权限」，能被用来遍历出别人有多少份简历。
3. 新增个人资源表时必须带 `user_id` 列，否则无法被 `assert_owner` 保护。

游客模式（`ALLOW_GUEST=true`，默认）下未登录请求归公共 `guest` 账号，
**该账号的数据对所有未登录访客可见，不具私有性**；把 `ALLOW_GUEST` 设为 `false`
即可让未登录请求一律 401，数据完全私有。

### 1.1 `app/services/company_service.py`（Agent A 提供，主控 `main.py` 调用）
```python
def load_companies() -> list[dict]                 # 读取 app/data/companies.json
def hot_companies(limit: int = 50) -> list[CompanyBrief]
def find_company(name: str) -> dict | None         # 精确/模糊匹配内置公司库，返回原始 dict
def search_companies(q: str, limit: int = 20) -> list[dict]
async def resolve_company_profile(db: Session, name: str) -> tuple[dict, str]   # (CompanyProfile dict, "auto"|"manual")
```

### 1.2 `app/services/matching.py`（Agent A 提供，Agent B 会 import）
```python
def compute_match(resume: dict, jd: dict, company: dict | None = None) -> dict
# 返回 schemas.MatchResponse 的 dict 形态，必须包含键：
# overall(int 0-100), level(str), matched(list), weak(list), strategy(focus/deep_dive/pressure_test), breakdown(list), radar_target(dict)
```

### 1.3 `app/services/interview_engine.py`（Agent B 提供）
```python
class InterviewEngine:
    def __init__(self, profile: dict, total_questions: int = 9) -> None: ...
    def plan(self) -> dict                      # 面试计划（各环节题量）
    async def opening(self) -> dict             # 开场白+第1题（自我介绍），返回 Turn dict
    async def respond(self, dialogue: list[dict], answer_text: str,
                      answer_seconds: int = 0, skipped: bool = False) -> dict | None
        # 返回「下一个面试官 Turn」，面试结束时返回 None
    async def closing(self) -> str              # 结束语
```
`Turn` dict 必须含 `schemas.Turn` 的全部字段：`turn_id, role, content, focus, section, index, total, is_followup, reference_points, speak_text, created_at`。
- `role` 固定 `"interviewer"`。
- `speak_text` = `content` 去掉括号标注（如「考察：xxx」）后的纯口语文本，供 TTS 使用。
- `index` 为题目序号（1..total）；开场白与第一题合并为 `index=1, section="自我介绍"`。

### 1.4 `app/services/report_service.py`（Agent C 提供）
```python
async def build_report(db: Session, session: InterviewSession) -> dict
# 返回 schemas.InterviewReport 的 dict 形态（7 个板块齐全）
```
`build_report` 自行从 DB 读取 resume / jd / company / 历史会话，不依赖其它 Agent 的模块。

### 1.5 `app/services/practice_store.py`（Agent C 提供）
```python
def save_wrong_questions(db: Session, session: InterviewSession, report: dict) -> int
def list_wrong_questions(db: Session, user_id: str, category: str = "") -> dict
def build_leaderboard(db: Session, board_type: str, user_id: str = "") -> dict
```

### 1.6 `app/services/media.py`（主控提供）
```python
def tts_status() -> str
def asr_status() -> str
async def synthesize(text: str, voice: str = "female", rate: str = "+0%") -> dict   # schemas.TTSResponse dict
async def recognize(audio_bytes: bytes) -> dict       # schemas.ASRResponse dict
# 追加（纯新增，未改动上面四个的签名）：
def tts_provider_order() -> list[str]   # 当前确实可用的后端音源（不含 browser），显式配置的排第一
def tts_diagnosis() -> str              # 启动自检提示；配置正常时返回 ""
```
约定：
- `tts_status()` / `asr_status()` 返回**实际生效**的提供方，必须校验依赖是否可用
  （阿里云 TTS 需要 httpx 或 NLS SDK；阿里云 ASR 需要 NLS SDK），不得只看凭据。
- `synthesize()` 按 `tts_provider_order()` 逐级尝试，全部失败才返回
  `provider="browser"` + `audio_url=""`（前端据此走浏览器 `speechSynthesis`，见 §2.6）。
- 阿里云 TTS 有两条链路：NLS SDK（WebSocket，装了才走）→ RESTful HTTP（阿里云合成接口，只需 httpx）。
  RESTful 单次上限 300 字符，超出按句切分拼接；token 走 POP `CreateToken`，经 `X-NLS-Token` 头传递。

### 1.7 `app/services/session_housekeeping.py`（Agent B 提供，主控 `main.py` 调用）
```python
DEFAULT_IDLE_TIMEOUT_HOURS: float = 6.0
def idle_timeout_hours() -> float                    # 当前生效阈值（小时）
def last_activity_at(session: InterviewSession) -> datetime          # 末次对话活动（UTC aware）
def is_stale(session: InterviewSession, timeout_hours: float | None = None) -> bool
def close_session(db: Session, session: InterviewSession,
                  timeout_hours: float | None = None) -> bool        # 未超时返回 False（幂等）
def close_stale_sessions(db: Session, timeout_hours: float | None = None,
                         user_id: str = "") -> list[str]
    # 批量收敛过期 ongoing 会话；user_id 为空时扫全库（启动自愈），非空时只扫该用户
    # 返回被关闭的 session_id 列表
```
阈值来源优先级：显式入参 > `config.settings.session_idle_timeout_hours`（.env / 环境变量）
> 环境变量 `SESSION_IDLE_TIMEOUT_HOURS` > 默认 6.0；**`<=0` 表示关闭自动收敛**（此时全部函数为 no-op）。

约定：只把 `status=="ongoing"` 且空闲超时的会话改成 `aborted`（补 `finished_at`），
**不删除任何对话内容**；`finished` / 已 `aborted` 的会话是幂等 no-op。计时基准取
`max(session.created_at, dialogue 中最后一条 turn 的 created_at)`——turn 时间戳是本地时区字符串，
`created_at` 是 naive UTC，比较前统一折算成 UTC aware（否则东八区会多算 8 小时空闲、
把还在进行的面试误判为超时）。

---

## 2. 接口清单

### 2.0 认证（主控 → `app/routers/auth.py`）
| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| POST | `/api/auth/register` | `RegisterRequest` | `AuthResponse`（注册即登录，直接下发 token） |
| POST | `/api/auth/login` | `LoginRequest` | `AuthResponse` |
| POST | `/api/auth/logout` | -（读 `Authorization` 头） | `OkResponse`（幂等，删服务端令牌行） |
| GET | `/api/auth/me` | -（读 `Authorization` 头） | `AuthUser`（未登录返回游客身份；令牌无效 **401**）；含引导所需布尔位 `has_resume` / `has_jd` / `finished_sessions`；**不含任何额度 / 次数字段** |
| GET | `/api/auth/status` | - | `{allow_guest, token_ttl_hours}` |

**校验与安全要求**
- 账号 3-32 位，`[A-Za-z0-9]` 开头，只含字母/数字/`_`/`-`；长度大小写不敏感（统一小写入库）。不满足 → 400。
- 密码 6-128 位 → 否则 400；**只存 PBKDF2-HMAC-SHA256 派生值，任何响应都不得回显口令学字段**。
- 账号已存在 → 409；**账号不存在与密码错误一律 401 且提示文案相同**（防账号枚举）。
- 同一账号+IP 连续失败 8 次 → 429，15 分钟窗口（进程内限流；多实例部署需换 Redis）。
- 令牌 32 字节 URL-safe 随机串，**数据库只存其 SHA-256**；TTL 默认 720 小时（`AUTH_TOKEN_TTL_HOURS`）。
- 退出登录删除该令牌行；**其它设备的令牌不受影响**。

### 2.1 元信息（主控，已实现）
| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + 当前 LLM/TTS/ASR 模式；`app_id`（纯 ASCII：`ai_project`）供启动脚本校验「这个端口上跑的确实是本项目」——同级目录的另一个项目默认也监听 8000 |
| GET | `/api/health/llm` | 大模型配置自检：当前厂商 / 模型 / 未生效原因 |
| GET | `/api/meta/options` | 六维模型、等级表、环节、热门公司、面试官音色；**不含价格 / 次数 / 购买相关字段** |
| GET | `/api/demo/seed` | **Agent A**：一键载入示例简历+JD+公司，返回 ids（归属当前用户） |

### 2.2 模块一（Agent A → `app/routers/profile.py`）
> 下列接口全部经 `Depends(current_user)`：简历/JD 的归属只由令牌决定，
> 按 id 读写他人资源一律 **404**；`user_id` 表单/JSON 字段已不再参与身份判定。

| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| POST | `/api/resume/upload` | multipart：`file`(PDF, ≤5MB)、`user_id`(可选) | `ResumeUploadResponse`（含 `coverage` / `confidence_levels`） |
| POST | `/api/resume/text` | `{text, user_id?}`（粘贴文本，无 PDF 时的入口） | `ResumeUploadResponse` |
| GET | `/api/resume/{resume_id}` | - | `ResumeUploadResponse` |
| PUT | `/api/resume/{resume_id}` | `ResumeUpdateRequest` | `ResumeUploadResponse` |
| POST | `/api/jd/parse` | `JDParseRequest`(JSON) | `JDParseResponse`（含 `coverage` / `confidence_levels`） |
| POST | `/api/jd/upload` | multipart：`file`(PDF)、`user_id` | `JDParseResponse` |
| GET | `/api/jd/list` | - | `JDListResponse`（**必须先注册，避免被 `/jd/{jd_id}` 吃掉**） |
| GET | `/api/jd/{jd_id}` | - | `JDParseResponse` |
| PUT | `/api/jd/{jd_id}` | `JDUpdateRequest` | `JDParseResponse` |
| GET | `/api/company/search` | query：`q`、`limit` | `CompanySearchResponse` |
| GET | `/api/company/{company_id}` | - | `CompanyResponse` |
| POST | `/api/company/manual` | `CompanyManualRequest` | `CompanyResponse` |
| POST | `/api/match` | `MatchRequest` | `MatchResponse` |
| POST | `/api/demo/seed` | - | `{user_id, resume_id, jd_id, company_id, resume, jd, company, match}` |
| GET | `/api/demo/seed` | - | 同上（GET 形态，便于前端一键载入） |

**解析置信度与覆盖率（体验增强 §7）**
- `ResumeProfile.field_confidence` / `JDProfile.field_confidence`：`{字段名: 0~1}`，由 `services/field_quality.py` 计算。
  分级常量：命中标签 `0.95` / 命中板块 `0.88` / 板块存在但条目过少 `0.72` / 派生字段 `0.6` / 规则兜底 `0.5` / 未识别 `0`。
  低于 `LOW_CONFIDENCE=0.7` 的字段由前端用琥珀色高亮并前置排序（`low_confidence_fields()` 提供同一判定，前后端不分叉）。
- `coverage`：`{identified, total, missing[], ratio}`，用于「已识别 12/16 项」卡片；缺失项可直接补录。
- 两个字段都是**可选增量**，老客户端忽略即可；解析走规则引擎时置信度整体下移但接口不报错。

**校验要求**
- PDF 大小超过 `settings.max_upload_mb` → 400，message 说明上限。
- 非 PDF 后缀 → 400。
- PDF 无可提取文本（扫描件）→ 仍返回 200，`parse_status="failed"`，`warnings` 提示「疑似扫描件，请粘贴文本」。
- 所有解析接口**永不 500**：LLM 失败自动走规则引擎（`parse_source="rule"`）。
- `/api/company/*` 与 `/api/match` 中的公司画像是**公共参考数据**（`company` 表无 `user_id`），
  搜索/查看对所有访客开放；`/api/match` 仍会校验简历与 JD 的归属。

**`ResumeProfile` 字段（`schemas.py` 为准）**
`basic` / `education` / `internships` / `projects` / `campus_experience` / `skills` / `awards` / `self_evaluation`。
其中 `campus_experience` 为**校园经历**（学生会、社团、志愿服务、社会实践、竞赛组织等），
每条结构 `{name, role, start, end, description, highlights}`：
`name` 是组织或活动名、`role` 是担任职务，含量化结果的条目放 `highlights`。
规则引擎按板块标题「校园经历 / 校园活动 / 校园实践 / 社团经历 / 学生工作 / 社会实践 / 志愿服务 / 课外活动」切分，
识别不到该板块时返回空数组（不是错误）。

### 2.3 模块二（Agent B → `app/routers/interview.py`）
| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| POST | `/api/interview/start` | `InterviewStartRequest`（新增可选 `retry_mode` / `focus_section` / `focus_dimension` / `pressure_mode`） | `InterviewStartResponse`（**不含额度字段；不会因用量返回 402**） |
| POST | `/api/interview/{session_id}/answer` | `InterviewAnswerRequest`（新增可选 `retake` / `hint_used`） | `InterviewAnswerResponse`（含 `progress_info`） |
| POST | `/api/interview/{session_id}/skip` | `InterviewSkipRequest` | `InterviewAnswerResponse` |
| POST | `/api/interview/{session_id}/finish` | - | `InterviewSessionState` |
| POST | `/api/interview/{session_id}/speech-metrics` | `SpeechMetricsRequest` | `SpeechMetricsResponse` |
| GET | `/api/interview/{session_id}` | - | `InterviewSessionState`（含 `progress` / `retake_count` / `hint_count`） |
| GET | `/api/interview/{session_id}/transcript` | - | `{session_id, items:[TranscriptItem]}`（每项含 `turn_id` / `answer_seconds` / `retake` / `hint_used`） |

**体验增强增量（文档 §2 / §4 / §9 / §13）**
- `progress`：`{index, total, stage, elapsed_seconds, estimated_remaining:[min,max]}`，供顶部进度条渲染；
  `stage` 用「开场/中段/收尾/反问」四段映射，`estimated_remaining` 按本场已答时长的中位数外推（无样本时用 `SPEECH_EST_SECONDS_PER_QUESTION`）。
- `retake`（重录过本题）/ `hint_used`（用过思路提示）由客户端如实上报，服务端只在报告里**如实标注**，
  **不参与任何评分计算**，仅影响 `independent_answer_rate`（独立作答率）的分母/分子。
- `/speech-metrics`：只落库不评分（单题语速字符/分、填充词命中次数与明细、时长）。
  阈值判断在前端即时反馈，后端保留原始值以便后续做基准，`speech_metric` 表带 `user_id`。
- `retry_mode=true` + `focus_section` / `focus_dimension`：单题特训的 3 题微面试，题目全部来自该薄弱环节，
  开场题也跟随 `focus_section`（普通会话仍从自我介绍开始，行为逐字节不变）。
- `pressure_mode=true`：压力面试模式，追问门槛从 `25 字 / 15 秒` 提到 `40 字 / 40 秒`，非追问不改；
  该标记落在 `plan_json` 并在恢复会话时还原（刷新页面不会退回普通模式）。

**流程要求**
- `start`：读取 resume/jd/company → `compute_match`（import 自 `services.matching`，必须 try/except 兜底）→ 建 `InterviewSession` → 生成 opening Turn 落库 → 返回。
  `resume_id` / `jd_id` 必须是**当前用户自己的**（否则 404）；`session.user_id` 取令牌用户，`req.user_id` 忽略。
  **只校验登录、不校验次数**：服务端没有计数器，响应里也没有额度字段。
- `answer`：把候选人回答追加进 `dialogue_json`；调用 `engine.respond()` 得到下一个 Turn；`None` 表示面试结束，此时 `status="finished"`、`next_turn=null`、写入 `closing` 结束语 turn（role=`interviewer`, index=0）。累加 `duration_seconds`。
- 界面上「反问环节」的处理：面试官提问「你有什么想问我的吗？」后，候选人回答即为最后一题；其后再 `respond` 返回 `None`。
- `skip`：把该题标记为跳过（dialogue 追加 role=`system` 内容「（候选人选择跳过本题）」），直接进入下一题，不追问。
- 撤销/异常：session 不存在 → 404；**session 不属于当前用户 → 同样 404**（不泄露存在性）；session 已 finished 还要 answer → 409。

- 超时收敛（见 §1.7）：`ongoing` 会话空闲超过 `SESSION_IDLE_TIMEOUT_HOURS`（默认 6 小时）后
  会被置为 `aborted`，`finished_at` 补齐，之后 answer / skip 一律 409。触发时机是**后端启动**与
  **每次 `GET /api/interview/{id}`**。`aborted` 不属于 `finished`，因此不会进入历史进步曲线与排行榜
  （两者只统计 `status=="finished"`），但仍允许生成报告（报告只依赖 `dialogue_json`）。

### 2.4 模块三（Agent C → `app/routers/report.py`）
| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| GET | `/api/report/history` | -（**只返回当前登录用户**，`user_id` 入参已移除） | `{items:[ProgressPoint]}`（**必须先注册，避免被 `/{session_id}` 吃掉**） |
| POST | `/api/report/{session_id}/generate` | - | `InterviewReport` |
| GET | `/api/report/{session_id}` | - | `InterviewReport`（未生成则自动生成） |
| GET | `/api/report/{session_id}/progress` | - | `ReportProgressResponse`（生成进度：阶段清单 / 完成百分比 / 真实耗时 / 评分方式；无进度时 `available=false`） |
| GET | `/api/report/{session_id}/export` | query：`format=markdown\|json` | `{filename, content}` |
| POST | `/api/report/{session_id}/feedback` | `ReportFeedbackRequest`（可带 `question_index` + `verdict=accurate\|high\|low`） | `OkResponse`（逐题反馈会**回写到已落库的 `report_json`**，刷新页面后仍在） |
| GET | `/api/report/{session_id}/share` | - | `{share_id, url, poster_data}`（海报数据由前端渲染） |

> 全部接口都要求该 session **属于当前用户**（他人 session → 404）。报告含逐题回答原文，是最敏感的资源，
> 不允许出现「拿到 session_id 就能读」的路径。

**报告生成进度（生成过程可视化）**

一份报告要跑两次大模型评分（首轮 + 二次校验），实测数十秒；`GET .../progress` 让前端能显示
「跑到哪一步、这一步多久、用的是什么评分方式」，而不是一个转圈的骨架屏。

- 阶段固定 6 步：读取面试记录 → 六维评分 → 维度归因与基准 → 改进建议与 7 天计划 →
  逐题分析与证据 → 保存报告与错题。`scoring.score_session` 通过可选回调 `on_stage`
  上报子步骤（规则基线 / 第 1 轮大模型评分 / 二次校验），只影响文案，**不虚增百分比**。
- `percent` 只按**已真正完成**的阶段数计算；进行中的阶段由前端用动效 + 已耗时表达「在动」。
- 进度是**进程内瞬时状态**（`services/report_progress.py`），不落库：多 worker 部署时可能读不到，
  此时 `available=false` + `note`，前端退化为不确定进度条 —— **不假装有进度**。
  与 `services/auth.py` 的进程内登录限流同一取舍（多实例部署需换共享存储）。
- `stale=true` 表示超出预期时长仍在跑（大模型排队，或进程重启留下的记录），前端据此提示可刷新。
- 只读、无副作用；鉴权与其它报告接口一致（他人会话 404）。


**评分要求（文档 §5.1~§5.4）**
- 六维：专业能力25% / 逻辑思维20% / 表达沟通15% / 岗位匹配度20% / 应变能力10% / 文化适配10%，每维 1-10 分。
- 总分 = Σ(维度分×权重)，换算百分制；等级按 `constants.GRADES`。
- 优先调用 LLM（`prompts.templates.scoring_prompt`）；LLM 不可用或返回异常时，必须用**规则评分引擎**兜底，且规则引擎要真的分析文本（长度、结构化连接词、JD 技能命中、口头禅密度、量化数据、STAR 要素、追问下的表现），不得返回固定分。
- 二次校验（§5.4）：对 professional / matching 两维可再调一次 `second_pass_prompt`，取平均。
- 防作弊（§5.4）：单题回答 `<10秒` 或答案 `<15字` → 该题 `too_short=True`，`meta.cheating_flags` 加「第 N 题回答过短，评分仅供参考」。
- 待改进项最多 3 条，按 high/medium/low 排序，每条必须含 `actions`(恰好3条) 与 `resources`(≥1条)。
- 7 天计划：每天 2-3 个任务，任务类型限「知识补充 / 模拟练习 / 复盘总结」，严禁「多练习」类空话。
- 生成报告后自动调用 `practice_store.save_wrong_questions` 收集单题 `<6` 分的题。
- 报告落库结果必须如实上报：写入数据库失败（或走了极端降级分支、报告未落库）时，
  仍按契约 §3 返回完整报告并置 `meta.persist_failed=true`，前端据此提示
  「本报告未保存，重新打开会重新评分」。**不得静默吞掉落库失败**。

**报告新增字段（体验增强 §6 / §9 / §13）**
- `ai_disclaimer`：固定文案「本报告由 AI 生成，仅供参考，不构成录用判断」，首屏可见（不冒充真人面试官）。
- 每维 `DimensionScore.basis`（依据了什么：命中关键词/量化数据/结构化表达）+ `anchors`（「什么样算 8 分」示例句）+ `benchmark`；
  每题 `QuestionAnalysis.evidence_spans`（本题原文里支撑结论的片段）、`feedback_verdict`（用户反馈）、
  `skipped` / `retake_count` / `hint_used` / `speech`（语速与填充词）。
- `benchmark`：`services/benchmark.py` 对**同岗位已结束会话**取 P25/P50/P75 分位；
  样本 `<8` 时返回 `available=false` + `note`（延续「演示基准」的诚实标注，绝不假装有数据）。
- `retake_count` / `hint_count` / `independent_answer_rate`：诚实披露重录与提示，不扣分。
- `retry_section` / `retry_dimension`：报告直接给出「下一步练哪一项」，点进去即 3 题微面试。
- `pressure_mode` / `industry` / `industry_bank`：压力模式与行业专项题库标记（行业由岗位/JD 关键词匹配，含别名表）。

### 2.5 错题本 / 排行榜 / 成长面板（Agent C → `app/routers/practice.py`）
| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| GET | `/api/practice/wrong-questions` | query：`category`(可选)（**只看当前用户**，`user_id` 入参已移除） | `WrongQuestionListResponse`（每项含 `section` / `dimension_key` / `trend` / `before_score`） |
| POST | `/api/practice/retry` | `RetryWrongQuestionRequest`（`start_micro=true` 时创建 3 题微面试） | `{wrong_id, question, reference, tips:[str], retry_count}` 或 `RetryWrongQuestionResponse`（含 `micro_session` 的会话与首题） |
| DELETE | `/api/practice/wrong-questions/{id}` | - | `OkResponse` |
| GET | `/api/leaderboard` | query：`type=job\|school\|progress` | `LeaderboardResponse` |
| GET | `/api/growth/summary` | -（只看当前用户） | `GrowthSummary`（`streak` / `best_streak` / `total_sessions` / `active_days` / `last_7_days[]` / `weeks[]` / `dimension_delta` / `next_goal` / `note`） |

排行榜在**没有真实用户数据**时，返回一批标注为示例的匿名数据（`note` 字段说明为演示数据），保证页面有内容。
榜单本身是公开的，但「我的排名」只按当前登录用户的身份计算；错题相关接口对他人记录一律 404。

- 单题特训（§9）：`POST /api/practice/retry` 带 `start_micro=true` 时，内部复用 `interview_start`
  创建一场 3 题会话（`_RETRY_QUESTION_COUNT=3`，全部指向该薄弱环节），完成后给出**同维度前后对比**
  （`before_score` → 本次），这才是「练够 100 次」的实际单位。
- 成长面板（§12）：`streak` / `active_days` 用**本地日期**（`local_iso()[:10]`）统计；
  用 UTC 会让东八区用户当天的练习被算到昨天，7 天格子与连续天数会凭空少一天。
- `note` 字段在样本不足时说明「数据还少，先把最近几场跑完」，不编造趋势。

### 2.8 简历优化（→ `app/routers/resume_optimizer.py`）
| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| GET | `/api/resume-optimizer/meta` | - | `{templates:[{id,name,desc}], pdf_available, llm_mode, llm_provider, empty_resume, max_upload_mb}` |
| POST | `/api/resume-optimizer/polish` | `PolishRequest{resume, jd, language}` | 规整后的简历 JSON（含每条的 `description_lines`） |
| POST | `/api/resume-optimizer/ats` | `ATSRequest{resume, jd}` | `{score, grade, level, summary, modules[5], details, suggestions}` |
| POST | `/api/resume-optimizer/keyword-coverage` | `KeywordCoverageRequest{resume, jd}` | `{coverage:{covered, total, ratio, covered_keywords[], missing_keywords[]}, skill_missing[]}` |
| POST | `/api/resume-optimizer/upload-pdf` | multipart：`file`(.pdf) | `{resume, warnings}` |
| POST | `/api/resume-optimizer/render-pdf` | `RenderPDFRequest{resume, template_name, language}` | PDF 二进制 + 头 `X-Resume-Pages` / `X-Resume-Base-Font` |

- **全部无状态**：不落库、不产生用户数据；`render-pdf` 只在内存里渲染，上传的 PDF 解析完即删临时文件。
- `resume` 的结构 = `ResumeData`（`app/services/resume_optimizer.py`），字段名与 PDF 模板一一对应：
  `name/phone/email/job_intention/age/gender/ethnicity/wechat/political_status/slogan/photo`
  + `education/work_experience/project_experience/campus_experience`（`{title,company,start_date,end_date,description}`）
  + `honors`（`{date,name,level}`）+ `skills`。
- 润色/ATS **必须走本项目统一的 `services/llm.py`**（不再自带 ZHIPU key）；
  大模型不可用（mock 模式）时返回 **503 + 明确指引**，不返回假数据——简历内容会被直接投递，编不得。
- `keyword-coverage`（§11 的 JD 关键词覆盖率看板）是**纯确定性计算，不调用大模型**：
  从 JD 里抽取目标关键词（上限 `_MAX_TARGET_KEYWORDS=20`），与简历全文做包含匹配，
  因此 **mock 模式与未配 Key 时同样可用**，不会因为 503 而让看板消失。
- PDF 导出依赖系统安装 `wkhtmltopdf`（缺省走 `WKHTMLTOPDF_PATH` 或常见安装路径）；
  缺失时启动日志明确提示，接口返回 503，其余功能不受影响。

### 2.14 岗位信息汇总（→ `app/routers/jobs.py` + `app/services/job_store.py`）

产品口径：**岗位信息全员共享，投递进度各人独立**。

| 方法 | 路径 | 权限 | 入参 | 出参 |
| --- | --- | --- | --- | --- |
| GET | `/api/jobs` | 登录 | - | `JobListResponse{items:[JobRecord], total, posted_yesterday, statuses[], stages[], note}` |
| GET | `/api/jobs/stats` | 登录 | - | `JobStatsResponse{total, applied, applied_rate, active, fail, passed, reject_total, rejection_rate, funnel[], rejected[], status_dist[], nature_dist[], nature_kinds, note}` |
| PUT | `/api/jobs/{job_id}/progress` | 登录 | `JobProgressRequest{status}` | `JobProgressResponse{ok, job_id, status, progress_at, message}` |
| POST | `/api/jobs` | 登录 | `JobUpsertRequest` | `JobRecord` |
| PUT | `/api/jobs/{job_id}` | 登录 | `JobUpsertRequest` | `JobRecord` |
| DELETE | `/api/jobs/{job_id}` | 登录 | - | `OkResponse`（连带清除所有人的相关进度） |
| POST | `/api/jobs/bulk` | 登录 | `JobBulkRequest{jobs[], dedupe=false}` | `JobBulkResponse{added, skipped, total, message}` |
| POST | `/api/jobs/seed` | 登录 | - | `JobBulkResponse`（内置 20 条示例，按企业名去重） |
| DELETE | `/api/jobs` | 登录 | - | `OkResponse`（清空岗位库 + 清除所有人的相关进度） |

> **权限只有一档：登录**。游客一律 401，登录用户全部接口可用（包括维护岗位库）——
> 没有角色字段、没有白名单、也没有「二期再收紧」的预留位。响应里也不再有 `is_admin`
> 这类开关字段：**前端不需要判断「我能不能维护」，能登录就能维护**。
> 谁改的记在 `job_posting.created_by`，追溯靠它，不靠权限分层。
> 单条「账号投递概览」（每个账号已投递多少条）**已删除**：它是全站唯一一处跨账号聚合，
> 与「数据私有零例外」直接冲突，而它的用途不足以抵掉这个代价。本模块现在不提供任何
> 跨账号的读接口，`/jobs` 下只剩「岗位库 + 我自己的进度 + 我自己的统计」。

`JobRecord` = `{job_id, company, nature, industry, audience, locations[], majors[], positions[], notice_url, apply_url, note, posted_at, progress, progress_at}`。

> `nature`（**雇主类型**：国企/民企/外企/事业单位。字段名仍是 `nature`，只有展示名改了）
> 与 `industry`（行业：银行/互联网/制造业…）是**两个独立字段**，列表里是相邻但不同的两列，
> 不要互相覆盖。`audience` 的展示名是**毕业届别**（同样只改展示名，键名不变）。
> `posted_yesterday` = **昨天新入库的岗位条数**（按本地时区、按岗位条数而非去重企业），
> 供列表上方「昨日更新 N 家」标注使用；它**不受前端筛选/搜索影响**（全库口径）。
> 统计上一日而非今天：今天的数据还在陆续导入、数字会一直跳，昨天是一段已经走完的完整区间。

八条不可动摇的口径：

1. **`JobPosting` 是全站唯一一张「共享、不带 `user_id`」的业务表**（与公共公司库同类）。
   只留 `created_by` 记录是谁导入的，用于追溯，**不参与权限判断**。
2. **维护权限只有「登录」一档**：任何登录用户都能新增 / 修改 / 删除 / 批量导入 / 载入示例 / 清空。
   不引入第二套角色，也不再有「隐藏按钮代替权限」这种半吊子做法——
   服务端没有可绕过的入口，前端也就没有需要隐藏的按钮。
3. **进度归属由令牌决定**：请求体里没有、也不接受 `user_id`，因此只能改自己的；
   读到别人的进度按 404 处理（本模块不提供任何「看别人进度」的接口）。
4. **`posted_at` 与 `progress_at` 是两个不同的东西**：前者是岗位信息进库时间
   （导入当天，编辑岗位也不改），后者是**我的进度**最后变动时间。字段名刻意不叫
   `updated_at`——一个永不随写入刷新的 `updated_at` 会误导后来者。
5. **导入只读取 8 个岗位信息字段**（企业名称 / 雇主类型 / 行业 / 毕业届别 / 工作地点 /
   专业要求 / 招聘岗位 / 投递链接）：源表里的「投递进度 / 更新时间」即使同名也一律忽略，
   **「公告链接」与「备注」也已从导入字段中移除**（接口仍接受这两个字段，
   库里也仍然保留、历史数据不动，只是导入向导不再读取它们）。
   进度统一记「未投递」、录入时间由服务端记当天（`JobUpsertRequest` 里根本没有这两个字段）。
   多值字段（地点 / 专业 / 岗位）接受数组**或**字符串，服务端 `split_tags` 统一按
   `、,，;；/|｜` 与换行拆分。
   **`dedupe` 默认 `false`（不去重）**：源表有多少行就导入多少行，同一家企业因不同
   批次 / 岗位方向重复出现时都会各自入库；需要按企业名合并时显式传 `true`。
   两种模式下「企业名称为空」的行都会被跳过（那是无效行，不是重复行）。
   前端 `bulkImportJobs()` 的默认参数与服务端默认值保持一致；「载入示例」走的是
   另一条路径（`job_store.seed_postings`），它**始终按企业名去重**、可以反复点。
6. **状态在服务端逐条校验**：不在 `constants.JOB_STATUS_META` 里的值直接 **400**；
   `status == 未投递` 表示**删除该行**（「没投过」不该占存储，统计也就不必再过滤）。
7. **统计只统计已投递**，且聚合口径只有一份实现（`job_store.compute_stats`）：
   漏斗按**阶段递进推算**（「已初面」视为已过初筛/测评/AI 面），已挂按 `stage` 归因到环节。
   明细表的筛选 / 排序仍在前端本地做（数据量小、交互要即时）。
8. **删除岗位 / 清空岗位库连带清除所有人在这些岗位上的进度**，不留孤儿数据。

> **没有有效期**：这一节此前用 `jobs_expires_at` 做过一道「按天开放、到期 403」的门，
> 现在整块删掉了——岗位库对所有登录用户**永久开放、不计次数**，服务端不再有任何岗位库的
> 有效期字段。理由是它不产生模型成本（纯数据操作），又是用户天天要回来改的台账，
> 按次/按天限制只会逼着他省着用；而它本身是共享台账，
> 用个人购买窗口去挡它，挡住的其实是愿意维护岗位库的人。

前端对应物：`pages/JobHub.jsx`（容器 + 两个标签）、`components/jobs/*`（列表 / 统计 /
导入向导 / 进度选择器 / 状态徽章 / 通用弹层 / **分页 `JobPager`**）、`lib/xlsx.js`（零依赖 .xlsx 读写）、
`constants/jobStatus.js`（与 `constants.JOB_STATUS_META` **逐字段镜像**，改字典必须两边同时改）。

- **没有整库导出接口**：导出全部岗位与「岗位库全员共享、进度各人独立」的定位相冲突，
  已下线；导出只保留「投递统计 → 导出统计 Excel」（当前筛选 / 排序下的**全部**已投递记录，
  不受分页影响），在前端用 `lib/xlsx.js` 生成，因此服务端不提供任何导出接口。
- **两个列表都每页 30 条**（汇总列表与已投递明细，页大小常量 `JOB_PAGE_SIZE` 在 `JobPager.jsx`），
  分页在**前端**完成：接口一次性返回当前用户的全部记录（岗位库量级在千条以内），
  这样筛选 / 搜索 / 排序 / 翻页都是即时的，不需要为每次交互再跑一趟服务端。
  若将来岗位库涨到万级，再改成服务端分页（届时接口需要新增 `page` / `page_size` 参数）。

自检：`cd backend && python test_jobs_flow.py`（权限 / 进度隔离 / 导入语义 / 统计口径 / 连带清理；
断言条数以脚本输出为准）。

### 2.7 降级与鉴权的关系

401 与降级是两回事，不要混：**鉴权失败必须如实返回 401**（前端据此清令牌并引导登录），
不能为了「永不报错」把它降级成 200 + 空数据——那会让用户以为自己的数据丢了。
`allow_guest=true` 时未登录不会触发 401，业务接口照常以游客身份工作。

**现在 403 只剩一种语义：权限不足**（此前的「岗位库已到期」那一路随有效期一起删除，
全站再没有任何接口会因为「你的账号不够格」而拒绝一个正常登录的用户）。
因此前端**不应该**再为 403 维护一个「已到期」的独立状态：403 一律按「这个请求不被允许」处理，
并且**绝不要因为 403 去清令牌**——清令牌是 401 的语义，混用会把人莫名带去登录页。

### 2.9 游客模式：只读公开接口，其余一律 401（`db.get_db` + `deps.require_login`）

产品要求「模拟面试 / 简历优化 / 我的面试 / 岗位信息汇总 仅登录用户可用」，因此接口按用途分成两类：

| 类别 | 依赖 | 接口 |
| --- | --- | --- |
| **需要登录** | `Depends(require_login)`：游客身份 → **401**（带 `WWW-Authenticate`） | `/interview/*`、`/resume/*`、`/jd/*`（`/jd/list` 等个人数据）、`/match`、`/demo/seed`、`/report/*`、`/practice/wrong-questions|retry`、`/growth/summary`、`/resume-optimizer/polish|ats|keyword-coverage|upload-pdf|render-pdf`、**岗位信息汇总全部接口**（`GET /jobs`、`GET /jobs/stats`、`PUT /jobs/{id}/progress`、`POST/PUT/DELETE /jobs*`） |
| **公开只读** | `Depends(current_user)` 或不依赖用户：游客可用 | `/health`、`/meta/options`、`/company/search`、`/company/{id}`、`/leaderboard`、`/resume-optimizer/meta` |

> **岗位库的维护不再有第二个权限维度**：新增 / 修改 / 删除 / 批量导入 / 载入示例 / 清空
> 对所有登录用户开放，`deps` 里也不再提供任何「是不是特权账号」的帮助函数。
> 想追溯是哪次写入改坏了数据，看 `job_posting.created_by`，而不是靠权限分层。

两条存储链路依旧成立（`db.get_db` 按「有没有 `Authorization` 头」分流）：

1. **认证接口例外**：`/api/auth/*` 一律走落盘库（注册/登录取的是全局用户与令牌表）。
2. **游客走进程内内存库**（`sqlite://` + StaticPool）：现在它是**防御纵深** ——
   上述 401 已经挡住了写入，但万一将来某个接口漏了 `require_login`，
   游客写进去的数据也只会落在内存里、不落盘、重启即消失。
   内存库创建时预置内置公司库（`_seed_guest_companies`），保证公开的公司查询两库一致。
3. 游客上传的 PDF 走系统临时文件、解析完即删（`profile._write_upload`）；
   TTS 合成音频仍缓存到 `data/tts_cache/`（公开的合成语音，不含用户数据）。
4. **401 先于 404**：游客拿着别人的资源 id 访问会先撞上登录门槛（401），
   而不是归属校验（404）——两者都不泄露存在性，且 401 更直白地告诉对方"你该登录"。

`test_auth_flow.py` 的 [8] 段对此有专项断言（个人接口全 401、公开接口全 200、
被拒调用后落盘库行数一个不涨、游客库与落盘库物理隔离），条数以脚本输出为准。

> **小节编号说明**：编号 2.10 / 2.11 / 2.12 / 2.13 已停用（原先归在这几个编号下的模块整体下线）。
> 其余编号保持不变，因此代码注释与其它文档里的 `§2.x` 引用不需要跟着改。

### 2.6 媒体（主控 → `app/routers/media.py`）
| 方法 | 路径 | 入参 | 出参 |
| --- | --- | --- | --- |
| POST | `/api/tts/synthesize` | `TTSRequest` | `TTSResponse` |
| POST | `/api/asr/recognize` | multipart：`audio` | `ASRResponse` |
| GET | `/api/media/tts/voices` | - | `{voices:[{key,label,provider,style}]}` |

TTS 策略（三级降级）：按 `tts_provider_order()` 逐级尝试阿里云 RESTful/SDK、`edge-tts`，成功则生成 mp3 存
`data/tts_cache/`，返回 `provider`(aliyun|edge) + `audio_url=/static/tts_cache/xxx.mp3` + 分句时间戳；
全部失败 → `provider="browser"`、`audio_url=""`，前端改用浏览器 `speechSynthesis`（**不再静音**）。
ASR 策略：无云厂商 Key 或缺少 NLS SDK → `provider="browser"`，前端用 Web Speech API；后端只保留可选接口。

---

## 3. 降级总原则（文档 §4.7）
1. 任何外部依赖（LLM / TTS / ASR / PDF）失败都必须降级，不允许抛 500 给前端。
2. 降级时必须通过响应字段（`parse_source` / `provider` / `meta.scoring_mode` / `warnings`）明确告知前端。
3. 前端根据 `provider` 字段决定用后端音频还是浏览器原生语音。
4. **零 Key 全流程可跑通**：未配任何厂商 Key（`LLM_PROVIDER=mock`）时，
   简历/JD 解析、出题、评分、7 天计划、单题特训、成长面板、基准分位、关键词覆盖率
   全部走规则/确定性实现，功能不缺项；唯一例外是简历优化的「AI 润色 / ATS 检测」，
   它们返回 **503 + 配置指引**（结果会被用户直接拿去投递，不能编），前端按钮灰显并弹层说明。
5. 体验增强新增的服务**一律不引入新的硬依赖**：
   `speech_metrics`（阈值常量比较）、`benchmark`（本地分位统计）、`field_quality`（分级常量）、
   `analytics.build_growth`（本地聚合）、`keyword_coverage`（字符串匹配）全部是纯计算，
   无 Key、无网络也能给出结果；样本不足时返回 `available=false` / `note`，而不是编造数据。

---

## 4. 开发自检

在**仓库根目录**执行（不要写死本机绝对路径）：

```powershell
cd backend
python -c "from app.main import app; print('import ok', len(app.routes))"
```

> 新版 FastAPI 把 `include_router` 的结果以 `_IncludedRouter` 惰性持有，所以 `len(app.routes)`
> 打印的是「内置路由 + 各路由对象 + 1 个静态挂载 + 1 个 SPA 兜底」，不等于接口总数。
> **接口共 60 个**（按 HTTP 方法计，`/api/*`，54 条路径，以 `/docs` 为准）——这个数字会随功能增删变，
> 想核对就重建一次 OpenAPI：`python -X utf8 -c "from app.main import app; s=app.openapi(); print(len(s['paths']), sum(len([m for m in v if m in ('get','post','put','patch','delete')]) for v in s['paths'].values()))"`。
> 只要不抛异常即为通过。

登录与数据隔离的端到端自检（进程内 TestClient，不需要先起服务）：

```powershell
cd backend
python test_auth_flow.py
```

覆盖游客身份、注册/登录/退出、账号枚举与弱口令拒绝，以及**跨用户越权读简历/JD/会话/报告/错题一律 404**、
请求体伪造 `user_id` 不生效；其中 [8] 专门验证**游客模式数据隔离**：
游客（无令牌）可完整跑通 demo seed → 面试 → 报告，但会话/简历/JD 只落在进程内存库
（`guest_db_session()` 查得到、`SessionLocal()` 查不到），登录用户与游客双向不可见。
全部通过时退出码为 0（实测 60 项，以脚本输出的汇总行为准）。

体验增强 14 项功能的端到端自检（同上，进程内 TestClient，强制 `LLM_PROVIDER=mock`）：

```powershell
cd backend
python test_experience_flow.py
```

按设计文档 §1–§14 分为 14 组（含追加的 [15] 报告生成进度）：设备自检读数、进度与预计剩余、
重录/提示的诚实标注、语速与填充词、评分归因与基准、逐题反馈回写、解析置信度与覆盖率、匹配度下钻、
JD 切换对比、单题特训（3 题微面试 + 前后对比）、7 天计划闭环、简历优化对照与关键词覆盖、成长面板、
深色模式令牌、压力模式与行业题库、报告生成进度（状态机 + 端到端 + 越权 404）。全部通过时退出码为 0
（实测 165 项）。

岗位信息汇总的权限 / 进度隔离 / 导入语义 / 统计口径 / 连带清理由第三套脚本覆盖：

```powershell
cd backend
python test_jobs_flow.py
```

> 三套自检的实测断言条数：`test_auth_flow.py` 60 项、`test_experience_flow.py` 165 项、
> `test_jobs_flow.py` 75 项。数字随功能调整会变，以各自输出的最后一次汇总行为准。

> 改动前后的**行为等价性**另有快照工具：`python .regress/run.py` 会对 services 与 interview
> 两条链路做 A/B 哈希比对（强制 mock，剔除 `created_at`/`turn_id`/`session_id` 等易变字段），
> `.regress/deep_diff.py` 给出逐路径差异。**普通改动不允许出现 REMOVED/CHANGED。**

---

## 5. 数据模型增量（附录 A 落地结果）

`create_all` **不会给已存在的表加列**，因此增量列统一由 `app/db.py` 的
`_EXTRA_COLUMNS` + `_ensure_extra_columns()` 在启动时做幂等 `ALTER TABLE ADD COLUMN`（失败只记日志，不阻断启动）。
老库（含真实用户数据）升级后无需重建。

| 归属 | 变更 | 说明 |
| --- | --- | --- |
| `interview_session` | `+ retake_count` / `+ hint_count` | 如实记录重录与提示次数，不参与评分 |
| `wrong_question` | `+ section` | 薄弱环节归属，支撑「哪一环弱 → 练哪一环」 |
| `speech_metric` | **新表** | 单题语速/填充词/时长，带 `user_id`，只落库不评分 |
| `report_feedback` | **新表** | 逐题 `accurate/high/low` 反馈，带 `user_id`，用于 Prompt 调优回流 |
| `job_posting` | **新表**（岗位信息汇总） | 全员共享的岗位库。**唯一不带 `user_id` 的业务表**（与公共公司库同类），**任何登录用户都可写**（没有写权限分层，谁改的记在 `created_by`）；`nature`（展示为「雇主类型」）与 `industry`（行业）是两个独立列；`posted_at` = 录入时间（只读） |
| `job_progress` | **新表**（岗位信息汇总） | 每人一份的投递进度，`(user_id, job_id)` 逻辑唯一，带 `user_id` 以满足「个人资源表必须带 user_id」铁律；`progress_at` = 进度最后变动时间 |

> 上表只列出早期那一批增量；后续新增的表（`job_posting` / `job_progress` /
> `speech_metric` / `report_feedback` 等）以 `app/models.py` 为准——
> 这里手工维护的清单**一定会滞后**，改动数据结构时请同步补一行，但不要把它当成唯一事实来源。

**兼容性铁律**：所有新增字段可缺省；`ALLOW_GUEST=true` 时游客路径同样可跑通；
未配 Key 的 mock 模式下除「AI 润色 / ATS 检测」外全部功能照常可用。

