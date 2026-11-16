"""全局配置。

所有配置均可通过 .env 覆盖。默认配置刻意做成「零密钥可运行」：
不配置任何 API Key 时 LLM 自动进入 mock 演示模式，语音走浏览器原生能力。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ---- 应用元信息 ----
    # 展示名（会出现在启动日志、/docs 标题与前端品牌位）。改它只影响展示，
    # **不要**动 `health.app_id` 那个 ASCII 标识：启动脚本靠它确认
    # 「这个端口上跑的确实是本项目」，中文名在 PowerShell 里会被按 Latin-1 解码而比对上失败。
    app_name: str = "校招全流程助手"
    app_version: str = "1.0.0"

    # ---- 大模型 ----
    # auto：按下方优先级自动挑选第一个「已配置密钥」的厂商
    # 可选值：auto | deepseek | dashscope | zhipu | moonshot | openai | ollama | mock
    llm_provider: str = "auto"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_model: str = "qwen-plus"
    # 智谱 GLM：控制台 https://open.bigmodel.cn 的 API Key，走 OpenAI 兼容端点
    zhipu_api_key: str = ""
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_model: str = "glm-4-flash"
    # 月之暗面 Kimi
    moonshot_api_key: str = ""
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    moonshot_model: str = "moonshot-v1-8k"
    # OpenAI（如在国内直连不通，需要自备代理地址）
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    # 本地 Ollama：不需要密钥，仅在 llm_provider=ollama 时启用
    ollama_base_url: str = "http://localhost:11434/v1"
    ollama_model: str = "qwen2.5:7b"
    # 单次大模型请求的超时（秒）。默认 120s 是刻意放宽的：
    # 免费/轻量模型（如 glm-4-flash）处理「整份简历结构化」这类长输出要 70-90 秒，
    # 若按普通接口的 30s 设计会导致每次都超时降级，看起来像「密钥没生效」。
    # 换用更快的模型后可自行调小。
    llm_timeout_seconds: float = 120.0
    # 仅对网络抖动/限流类错误重试；超时不重试（超时重试会让等待时间成倍放大）
    llm_max_retries: int = 2

    # ---- TTS / ASR（文档 §4.2）----
    tts_provider: str = "auto"  # auto | edge | browser | aliyun
    tts_voice_female: str = "zh-CN-XiaoxiaoNeural"
    tts_voice_male: str = "zh-CN-YunxiNeural"
    # 阿里云 TTS（NLS）专属音色（与 edge 的 zh-CN-* 命名不同）
    aliyun_tts_voice_female: str = "xiaoyun"
    aliyun_tts_voice_male: str = "xiaogang"
    # 语音合成可用性预算（秒）：超过该时长立即降级浏览器语音，
    # 同时让 edge-tts 在后台继续合成并写入缓存，下一轮同文本即可命中优质音频。
    # 这样既满足文档附录B「AI 响应 ≤3 秒」，又能在网络好时自动升级音质。
    tts_edge_timeout_seconds: float = 3.5
    asr_provider: str = "auto"  # auto | browser | aliyun
    # 阿里云语音（TTS/ASR）接入凭据。留空时 `tts_status()` / `asr_status()`
    # 会明确降级为 browser，而不是静默地把 provider 报成 aliyun。
    aliyun_access_key_id: str = ""
    aliyun_access_key_secret: str = ""
    aliyun_tts_appkey: str = ""
    aliyun_tts_token: str = ""
    aliyun_asr_appkey: str = ""

    # ---- 数据存储（文档 §3.6）----
    database_url: str = "sqlite:///./data/app.db"
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 5
    tts_cache_dir: str = "./data/tts_cache"

    # ---- 跨域 ----
    cors_origins: str = "*"

    # ---- 登录 / 会话令牌 ----
    # 登录令牌有效期（小时），默认 30 天；退出登录会立即吊销，不依赖过期时间。
    auth_token_ttl_hours: float = 720.0
    # 是否允许「游客试用」：未带令牌的请求落到公共 guest 账号（身份仍是同一个）。
    # 设为 false 时除 /api/auth/* 与元信息外，业务接口一律 401，数据完全私有。
    #
    # 游客的**数据**走进程内内存库（见 db.py「游客专用」一节），因此：
    #   · 不落盘：进程退出/重启后彻底消失，磁盘上找不到任何游客痕迹；
    #   · 不可见：游客读的是内存库，物理上读不到登录用户的数据，也读不到历史 guest 数据；
    #   · 前端把「我的面试 / 简历优化」对游客隐藏，报告列表与录音回放都不提供。
    allow_guest: bool = True

    # ---- 业务参数 ----
    default_question_count: int = 9  # 文档附录B：完整走完 8-10 题
    max_answer_seconds: int = 300  # 单题最长回答时长
    # 进行中的面试空闲超过该小时数后自动收敛为 aborted（见 services/session_housekeeping.py）；
    # 设为 0 或负数表示关闭自动收敛。用户中途关页面不会再调 /finish，
    # 没有这个阈值时会话会永久停在 ongoing。
    session_idle_timeout_hours: float = 6.0

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 派生路径 ----
    @property
    def upload_path(self) -> Path:
        p = Path(self.upload_dir)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def tts_cache_path(self) -> Path:
        p = Path(self.tts_cache_dir)
        if not p.is_absolute():
            p = BACKEND_DIR / p
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def resolved_database_url(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///./"):
            rel = url.replace("sqlite:///./", "")
            abs_path = BACKEND_DIR / rel
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{abs_path.as_posix()}"
        return url

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
