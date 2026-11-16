"""大模型客户端（文档 §6.1：DeepSeek / 通义千问 API）。

设计要点：
- 统一走 OpenAI 兼容协议，DeepSeek 与 通义千问(DashScope compatible-mode) 同一套代码。
- 没有任何 API Key 时自动进入 mock 模式：`chat_json()` 返回 None，
  调用方（解析 / 面试 / 评分）随即走各自的规则引擎兜底 —— 保证零密钥也能完整演示。
- 所有真实调用都带重试与超时；失败同样降级为 None，绝不让接口 500。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx

from ..config import settings

_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# 各厂商统一走 OpenAI 兼容协议，这里只登记「密钥字段 / base_url 字段 / 模型字段」。
# 元组格式：(provider, api_key 字段, base_url 字段, model 字段, 是否参与 auto 自动探测)
# - 顺序即 auto 模式的优先级；
# - ollama 为本机部署、不需要密钥，因此不参与 auto 探测，只在显式指定时才启用，
#   否则会在用户根本没装 Ollama 的情况下被自动选中，每次调用都超时降级。
_PROVIDER_SPECS: list[tuple[str, str, str, str, bool]] = [
    ("deepseek", "deepseek_api_key", "deepseek_base_url", "deepseek_model", True),
    ("dashscope", "dashscope_api_key", "dashscope_base_url", "dashscope_model", True),
    ("zhipu", "zhipu_api_key", "zhipu_base_url", "zhipu_model", True),
    ("moonshot", "moonshot_api_key", "moonshot_base_url", "moonshot_model", True),
    ("openai", "openai_api_key", "openai_base_url", "openai_model", True),
    ("ollama", "", "ollama_base_url", "ollama_model", False),
]


def _find_spec(provider: str) -> tuple[str, str, str, str, bool] | None:
    for spec in _PROVIDER_SPECS:
        if spec[0] == provider:
            return spec
    return None


def _spec_ready(spec: tuple[str, str, str, str, bool]) -> bool:
    """厂商是否可用：需要密钥的看密钥是否非空，不需要密钥的（本地 ollama）视为就绪。"""
    key_field = spec[1]
    if not key_field:
        return True
    return bool((getattr(settings, key_field, "") or "").strip())


def available_providers() -> list[dict[str, object]]:
    """列出各厂商的配置状态（供健康检查/前端提示，不返回密钥本身）。"""
    out: list[dict[str, object]] = []
    for spec in _PROVIDER_SPECS:
        name, key_field, _base, model_field, auto = spec
        out.append({
            "provider": name,
            "configured": _spec_ready(spec),
            "requires_key": bool(key_field),
            "auto": auto,
            "model": getattr(settings, model_field, ""),
        })
    return out


def extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里稳健地抠出 JSON（容忍 ```json 围栏、前后废话、单引号）。"""
    if not text:
        return None
    candidates: list[str] = []
    m = _JSON_FENCE.search(text)
    if m:
        candidates.append(m.group(1))
    candidates.append(text.strip())
    # 退而求其次：截取第一个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for c in candidates:
        if not c:
            continue
        try:
            data = json.loads(c)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            try:
                data = json.loads(c.replace("'", '"'))
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    return None


class LLMClient:
    """OpenAI 兼容协议的轻量客户端。"""

    def __init__(self) -> None:
        self.provider = self._resolve_provider()
        self.mode: str = "mock" if self.provider == "mock" else "live"

    # ---------------- 内部 ----------------

    @staticmethod
    def _resolve_provider() -> str:
        """决定使用哪个厂商。

        - `mock`：强制规则引擎；
        - 具体厂商名：该厂商配好了就用，没配好则降级 mock（不静默换厂商，便于发现问题）；
        - `auto` 或写了未知厂商名：按 `_PROVIDER_SPECS` 优先级挑第一个已配置密钥的厂商。
        """
        want = (settings.llm_provider or "auto").lower()
        if want == "mock":
            return "mock"
        spec = _find_spec(want)
        if spec is not None:
            return want if _spec_ready(spec) else "mock"
        for candidate in _PROVIDER_SPECS:
            if candidate[4] and _spec_ready(candidate):
                return candidate[0]
        return "mock"

    @property
    def model_name(self) -> str:
        spec = _find_spec(self.provider)
        if not spec:
            return "mock-rule-engine"
        return getattr(settings, spec[3], "") or "unknown"

    def _endpoint(self) -> tuple[str, str]:
        """返回 (chat/completions 地址, 密钥)。密钥可以为空（本地 ollama）。"""
        spec = _find_spec(self.provider)
        if not spec:
            return "", ""
        base = (getattr(settings, spec[2], "") or "").rstrip("/")
        key = (getattr(settings, spec[1], "") or "") if spec[1] else ""
        if not base:
            return "", ""
        return f"{base}/chat/completions", key

    async def _post(self, payload: dict[str, Any]) -> str | None:
        url, key = self._endpoint()
        if not url:
            return None
        headers = {"Content-Type": "application/json"}
        # 本地 ollama 不需要密钥；带一个空的 Bearer 头有些部署会直接 401
        if key:
            headers["Authorization"] = f"Bearer {key}"
        last_err: Exception | None = None
        for attempt in range(settings.llm_max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
            except httpx.TimeoutException:
                # 超时说明「这个模型处理这条提示词就是慢」，重试只会把用户等待时间成倍放大
                # （重试 2 次 = 等 3 倍超时），所以直接放弃、交给调用方走规则引擎兜底。
                print(
                    f"[llm] {self.provider}/{self.model_name} 超时"
                    f"（>{settings.llm_timeout_seconds:.0f}s），降级规则引擎。"
                    f"若该模型的正常耗时确实偏长，可在 .env 调大 LLM_TIMEOUT_SECONDS "
                    f"或换用更快的模型（如 glm-4-air / glm-4-plus / deepseek-chat）"
                )
                return None
            except Exception as exc:  # noqa: BLE001 网络/限流/格式异常统一降级
                last_err = exc
                if attempt < settings.llm_max_retries:
                    await asyncio.sleep(0.8 * (attempt + 1))
        # 带上异常类型：httpx 的 ReadTimeout 等信息为空，只打 {exc} 会输出空白，无法排查
        detail = f"{type(last_err).__name__}: {last_err}" if last_err else "未知错误"
        print(f"[llm] {self.provider}/{self.model_name} 调用失败，降级到规则引擎: {detail}")
        return None

    # ---------------- 对外 ----------------

    async def chat_text(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1600,
    ) -> str | None:
        if self.mode == "mock":
            return None
        return await self._post(
            {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            }
        )

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.4,
        max_tokens: int = 3000,
    ) -> dict[str, Any] | None:
        """要求模型输出严格 JSON；失败/ mock 模式返回 None。"""
        if self.mode == "mock":
            return None
        text = await self._post(
            {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                "response_format": {"type": "json_object"},
            }
        )
        if text is None:
            return None
        data = extract_json(text)
        if data is None:
            # 部分模型不支持 response_format，退化为普通对话再试一次
            text = await self.chat_text(messages, temperature=temperature, max_tokens=max_tokens)
            data = extract_json(text or "")
        return data


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client


def llm_status() -> dict[str, str]:
    c = get_llm()
    return {"mode": c.mode, "provider": c.provider, "model": c.model_name}


def llm_diagnosis() -> dict[str, object]:
    """配置自检：说明当前为什么用/没用大模型，并列出各厂商配置状态。

    用来避免「填了 key 却仍走规则引擎」这类问题只能靠翻文档排查。
    """
    c = get_llm()
    configured = [p["provider"] for p in available_providers() if p["configured"]]
    if c.mode == "live":
        hint = f"已启用 {c.provider}（{c.model_name}）"
    elif (settings.llm_provider or "auto").lower() == "mock":
        hint = "LLM_PROVIDER 被显式设为 mock，强制使用规则引擎"
    elif not configured:
        hint = (
            "未检测到任何已配置的 API Key，当前走规则引擎。"
            "请在 backend/.env 里填写 DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / ZHIPU_API_KEY 之一"
            "（注意是 .env，不是 .env.example），保存后重启后端。"
        )
    else:
        want = (settings.llm_provider or "auto").lower()
        hint = f"LLM_PROVIDER={want} 未配置好密钥，已降级规则引擎；当前已配好的厂商：{configured}"
    return {
        "mode": c.mode,
        "provider": c.provider,
        "model": c.model_name,
        "llm_provider_setting": settings.llm_provider,
        "configured_providers": configured,
        "providers": available_providers(),
        "hint": hint,
    }
