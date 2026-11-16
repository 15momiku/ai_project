"""语音服务：TTS / ASR（文档 §4.2.2、§4.2.3、§4.4、§4.7）。

三级降级策略
------------
TTS：edge-tts（本地免费、可拿字级时间戳） → 阿里云 TTS（需 Key，预留接入点） → 浏览器 speechSynthesis
ASR：阿里云实时识别（需 Key，预留接入点） → 浏览器 Web Speech API

任何异常都不会抛出到接口层，只会通过 `provider` 字段告知前端该走哪条链路。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
import uuid
from pathlib import Path

from ..config import settings

# 阿里云 NLS Token 缓存（有效期一般 24h，提前 10 分钟刷新）
_A_TOKEN: dict[str, object] = {"value": None, "expire": 0.0}

# 阿里云标准音色 -> (voice_name) 见 config: aliyun_tts_voice_female/male


def _has_edge_tts() -> bool:
    try:
        import edge_tts  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


# 同一原因只打一次日志：凭据错/SDK 缺失这类问题每个请求都会复现，
# 每次都 print 会把日志刷爆，而完全不打印又让人完全无法排查（这正是「语音加载失败」难查的原因）。
_LOGGED_ONCE: set[str] = set()


def _log_once(key: str, message: str) -> None:
    if key in _LOGGED_ONCE:
        return
    _LOGGED_ONCE.add(key)
    print(message)


def _has_aliyun_sdk(*modules: str) -> bool:
    """阿里云 NLS Python SDK 是否可用（TTS 与 ASR 共用同一个包：`nls`）。

    `_has_aliyun_tts()` / `asr_status()` 只检查凭据是**不够**的：SDK 没装时
    SDK 版合成会在 import 处失败并静默 return None，于是 /api/health 与前端音色提示
    都还显示 aliyun，而每一次合成都降级成 `provider=browser`（前端没有音频可播 →
    表现为「语音加载失败」），排查成本极高。

    默认校验两个子模块；也可只校验调用方真正需要的那一个。本函数只做判断、不打印，
    由调用方决定要不要提示安装（TTS 还能走 HTTP，不需要装 SDK）。
    """
    import importlib

    for name in modules or ("speech_synthesizer", "speech_recognizer"):
        try:
            importlib.import_module(f"nls.{name}")
        except Exception:  # noqa: BLE001
            return False
    return True


def _aliyun_sdk_hint() -> None:
    """提示安装 NLS SDK（只打一次）。只有 ASR 需要它，TTS 走 RESTful HTTP 不需要。"""
    _log_once(
        "aliyun_sdk",
        "[voice] 未安装阿里云 NLS SDK（pip install alibabacloud-nls-python-sdk）："
        "阿里云实时语音识别（ASR）不可用，将回落浏览器识别；"
        "阿里云 TTS 不受影响（走 RESTful HTTP）",
    )


def _has_http_client() -> bool:
    """httpx 是否可用（阿里云 TTS 的 RESTful 链路只需它，无需 NLS SDK）。"""
    try:
        import httpx  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _has_aliyun_tts() -> bool:
    """阿里云 TTS 是否可用：AppKey + 凭据 + （SDK **或** httpx）。

    RESTful 接口（`/stream/v1/tts`）是纯 HTTP，只要 httpx 能跑就不需要装 NLS SDK——
    这正是「配了 aliyun 就只能听 edge 音色」的解药。
    """
    return (
        bool(getattr(settings, "aliyun_tts_appkey", ""))
        and _has_aliyun_credential()
        and (_has_aliyun_sdk("speech_synthesizer") or _has_http_client())
    )


def _has_aliyun_credential() -> bool:
    if getattr(settings, "aliyun_tts_token", ""):
        return True
    if getattr(settings, "aliyun_access_key_id", "") and getattr(
        settings, "aliyun_access_key_secret", ""
    ):
        return True
    return False


def aliyun_voice(voice: str) -> str:
    """返回阿里云 NLS 音色名（与 edge 的 zh-CN-* 命名不同）。"""
    return settings.aliyun_tts_voice_male if voice == "male" else settings.aliyun_tts_voice_female


# 阿里云 NLS 的 HTTP 端点（全部无需 NLS SDK）
_ALIYUN_TOKEN_URL = "https://nls-meta.cn-shanghai.aliyuncs.com/"
_ALIYUN_TTS_REST_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/tts"
# RESTful 语音合成单次请求最多 300 字符，超出会被**静默截断**，所以按句切分后再拼接
_ALIYUN_TTS_TEXT_LIMIT = 300


def _pop_percent_encode(value: str) -> str:
    """阿里云 POP 签名的 percentEncode（RFC3986）：空格 -> %20，'*' -> %2A，'~' 不编码。"""
    from urllib.parse import quote

    encoded = quote(str(value), safe="")
    return encoded.replace("+", "%20").replace("*", "%2A").replace("%7E", "~")


def _pop_signature(params: dict[str, str], ak_secret: str, method: str = "POST") -> str:
    """按阿里云 POP RPC 规则计算 Signature（HMAC-SHA1 + Base64）。"""
    import base64
    import hashlib
    import hmac

    canonical = "&".join(
        f"{_pop_percent_encode(k)}={_pop_percent_encode(v)}" for k, v in sorted(params.items())
    )
    string_to_sign = f"{method}&{_pop_percent_encode('/')}&{_pop_percent_encode(canonical)}"
    digest = hmac.new(
        f"{ak_secret}&".encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _token_via_sdk(ak_id: str, ak_secret: str) -> tuple[str | None, float]:
    """用 NLS SDK 换取 token（装了 SDK 时优先走这条，行为与历史版本一致）。"""
    if not _has_aliyun_sdk("token"):
        return None, 0.0
    try:
        from nls.token import getToken

        # getToken 不返回过期时间，按 NLS 惯例取 24h 保守值
        return getToken(ak_id, ak_secret), time.time() + 24 * 3600
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] 阿里云 token 获取失败（SDK）：{exc}")
        return None, 0.0


def _token_via_http(ak_id: str, ak_secret: str) -> tuple[str | None, float]:
    """用 POP OpenAPI（CreateToken）换取 token，**无需 NLS SDK**。

    文档：https://help.aliyun.com/document_detail/72153.html
    """
    import uuid
    from datetime import datetime, timezone

    try:
        import httpx
    except Exception:  # noqa: BLE001
        return None, 0.0

    params = {
        "AccessKeyId": ak_id,
        "Action": "CreateToken",
        "Format": "JSON",
        "RegionId": "cn-shanghai",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": str(uuid.uuid4()),
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2019-02-28",
    }
    params["Signature"] = _pop_signature(params, ak_secret)
    try:
        resp = httpx.post(
            _ALIYUN_TOKEN_URL, data=params, timeout=httpx.Timeout(10.0, connect=5.0)
        )
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] 阿里云 token 获取失败（HTTP）：{exc}")
        return None, 0.0

    token_obj = payload.get("Token") or {}
    value = token_obj.get("Id")
    if value:
        try:
            expire = float(token_obj.get("ExpireTime") or 0)
        except (TypeError, ValueError):
            expire = 0.0
        return str(value), expire or (time.time() + 24 * 3600)
    print(
        "[tts] 阿里云 token 接口返回错误："
        f"{payload.get('Code') or payload.get('ErrMsg') or payload}",
    )
    return None, 0.0


def _get_aliyun_token() -> str | None:
    """获取有效 NLS Token：显式配置 > SDK 换取 > HTTP 换取，模块级缓存。

    离过期不足 10 分钟即重新获取；HTTP 链路用服务端返回的 ExpireTime，比固定 24h 更准。
    """
    explicit = getattr(settings, "aliyun_tts_token", "")
    if explicit:
        return explicit
    ak_id = getattr(settings, "aliyun_access_key_id", "")
    ak_secret = getattr(settings, "aliyun_access_key_secret", "")
    if not (ak_id and ak_secret):
        return None

    now = time.time()
    cached = _A_TOKEN.get("value")
    if cached and float(_A_TOKEN.get("expire") or 0) - now > 600:
        return str(cached)

    token, expire = _token_via_sdk(ak_id, ak_secret)
    if not token:
        token, expire = _token_via_http(ak_id, ak_secret)
    if not token:
        _log_once(
            "aliyun_token",
            "[tts] 阿里云 token 获取失败：请检查 ALIYUN_ACCESS_KEY_ID/SECRET，"
            "以及 RAM 是否授予 AliyunNLSFullAccess",
        )
        _A_TOKEN["value"] = None
        return None
    _A_TOKEN["value"] = token
    _A_TOKEN["expire"] = expire
    return token


def tts_provider_order() -> list[str]:
    """按优先级返回**当前确实可用**的后端 TTS 提供方（不含 browser）。

    - 显式配置的提供方排第一；
    - 其余按 edge → aliyun 顺序兜底。这样阿里云没装 SDK / AppKey 无权限 / 欠费时
      仍然能用免费的 edge-tts 出声，而不是像旧实现那样直接静默降级到 browser
      （前端没有 audio_url 可播 = 用户听到的「面试官语音加载失败」）。
    """
    want = (settings.tts_provider or "auto").lower()
    if want == "browser":
        return []
    available = [
        p
        for p in ("edge", "aliyun")
        if (p == "edge" and _has_edge_tts()) or (p == "aliyun" and _has_aliyun_tts())
    ]
    if want in available:
        return [want] + [p for p in available if p != want]
    return available


def tts_status() -> str:
    """返回当前生效的 TTS 提供方：edge | aliyun | browser。

    语义是「**实际**会被优先使用的那一个」：凭据齐了但依赖/SDK 不可用时绝不能报 aliyun，
    否则 /api/health、启动日志和前端音色标签全在说谎，而每一次合成都已静默降级。
    """
    order = tts_provider_order()
    return order[0] if order else "browser"


def tts_diagnosis() -> str:
    """TTS 配置自检提示（不包含任何密钥内容）；一切正常时返回空串。

    对齐 `llm_diagnosis()` 的做法：把「为什么没用上你配的提供方」直接打在启动日志里。
    """
    want = (settings.tts_provider or "auto").lower()
    if want != "aliyun" or _has_aliyun_tts():
        return ""
    reasons = []
    if not getattr(settings, "aliyun_tts_appkey", ""):
        reasons.append("缺 ALIYUN_TTS_APPKEY")
    if not _has_aliyun_credential():
        reasons.append("缺 ALIYUN_ACCESS_KEY_ID/SECRET（或 ALIYUN_TTS_TOKEN）")
    if not (_has_aliyun_sdk("speech_synthesizer") or _has_http_client()):
        reasons.append("既没有 NLS SDK 也没有 httpx，无法调用阿里云接口")
    return (
        f"TTS_PROVIDER=aliyun 但不可用（{'；'.join(reasons)}）"
        f"，已自动改用 {tts_status()}"
    )


def asr_status() -> str:
    """返回当前生效的 ASR 提供方：aliyun | browser。

    同样必须校验 SDK：否则前端会按「云端识别」准备音频并上传，
    结果后端永远识别失败，用户看到的是「语音识别失败」而不是自动走 Web Speech API。
    """
    want = (settings.asr_provider or "auto").lower()
    if (
        want == "aliyun"
        and bool(getattr(settings, "aliyun_asr_appkey", ""))
        and _has_aliyun_credential()
    ):
        if _has_aliyun_sdk("speech_recognizer"):
            return "aliyun"
        _aliyun_sdk_hint()
    return "browser"


def voice_name(voice: str) -> str:
    return settings.tts_voice_male if voice == "male" else settings.tts_voice_female


# ------------------------------------------------------------
# 分句（文档 §4.4：按句分割 + 时间戳）
# ------------------------------------------------------------
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_LONG_CLAUSE = re.compile(r"(?<=[，,、])")


def split_sentences(text: str, max_len: int = 42) -> list[str]:
    """把整段话切成字幕句：优先按句末标点，过长再按逗号切。"""
    text = (text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    for raw in _SENT_SPLIT.split(text):
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) <= max_len:
            chunks.append(raw)
            continue
        buf = ""
        for piece in _LONG_CLAUSE.split(raw):
            if not piece:
                continue
            if len(buf) + len(piece) <= max_len:
                buf += piece
            else:
                if buf:
                    chunks.append(buf)
                buf = piece
        if buf:
            chunks.append(buf)
    return chunks or [text]


def strip_annotation(text: str) -> str:
    """去掉面试官提问里括号内的考察点标注，得到纯口语文本（供 TTS 使用）。"""
    if not text:
        return ""
    clean = re.sub(r"[（(]\s*(?:考察|考察点|注)\s*[:：][^）)]*[）)]", "", text)
    clean = re.sub(r"[（(][^）)]{0,30}考察[^）)]{0,30}[）)]", "", clean)
    return re.sub(r"\s{2,}", " ", clean).strip()


# ------------------------------------------------------------
# TTS 合成
# ------------------------------------------------------------


def _cache_path(text: str, voice: str, rate: str) -> Path:
    key = hashlib.md5(f"{text}|{voice}|{rate}".encode("utf-8")).hexdigest()[:20]
    return settings.tts_cache_path / f"{key}.mp3"


# ---------------------------- 音频有效性校验 ----------------------------
# 阿里云/edge 合成偶尔会返回被截断的结果（实测出现过：43 字文本只产出一个
# 576 字节 / 0.19 秒的 mp3）。旧实现只校验「文件存在且非空」，于是这个坏文件
# 被当成缓存命中永久复用：前端拿到一段几乎无声的音频，而后端给的时间轴是按
# 字数估算的 —— 字幕与语音彻底错位，表现为「字幕跟不上 / 不同步」。
# 因此这里真正解析 MP3 帧，按实测时长判断音频是否合理。

_MP3_BITRATES_V1_L3 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_MP3_BITRATES_V2_L3 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)
_MP3_RATES_V1 = (44100, 48000, 32000, 0)
_MP3_RATES_V2 = (22050, 24000, 16000, 0)

# 中文 TTS 语速上限。正常朗读约 4~7 字/秒；即便把语速调到最快也远达不到 12 字/秒。
# 留 1.6 倍余量后仍能可靠识别「音频明显短于文本应有时长」的截断产物。
_MAX_TTS_CHARS_PER_SECOND = 12.0


def _mp3_duration(raw: bytes) -> float:
    """解析 MP3 帧头累加真实时长（秒）。不依赖任何第三方库；非 MP3 返回 0.0。"""
    data = raw
    # 跳过 ID3v2 标签
    if data[:3] == b"ID3" and len(data) > 10:
        size = (
            ((data[6] & 0x7F) << 21)
            | ((data[7] & 0x7F) << 14)
            | ((data[8] & 0x7F) << 7)
            | (data[9] & 0x7F)
        )
        data = data[10 + size :]

    total = 0.0
    frames = 0
    i = 0
    n = len(data)
    while i + 4 <= n and frames < 20000:  # 帧数上限，避免异常数据长时间循环
        if data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
            i += 1
            continue
        ver_bits = (data[i + 1] >> 3) & 0x03
        layer_bits = (data[i + 1] >> 1) & 0x03
        br_idx = (data[i + 2] >> 4) & 0x0F
        sr_idx = (data[i + 2] >> 2) & 0x03
        pad = (data[i + 2] >> 1) & 0x01
        # 保留位 / 非法码率索引 / 非法采样率索引 → 不是有效帧头，前移一字节继续找
        if ver_bits == 1 or layer_bits == 0 or br_idx in (0, 15) or sr_idx == 3:
            i += 1
            continue
        v1 = ver_bits == 3
        sr = (_MP3_RATES_V1 if v1 else _MP3_RATES_V2)[sr_idx]
        br = (_MP3_BITRATES_V1_L3 if v1 else _MP3_BITRATES_V2_L3)[br_idx] * 1000
        if layer_bits == 3:  # Layer I
            flen = (12 * br // sr + pad) * 4
            samples = 384
        elif v1:  # MPEG-1 Layer II/III
            flen = 144 * br // sr + pad
            samples = 1152
        else:  # MPEG-2/2.5 Layer III
            flen = 72 * br // sr + pad
            samples = 576
        if flen <= 4:
            i += 1
            continue
        total += samples / sr
        frames += 1
        i += flen

    # 小于 2 帧不足以认定为音频
    return total if frames >= 2 else 0.0


def _audio_is_valid(raw: bytes, text: str) -> bool:
    """音频是否与文本匹配（用于拦截截断/损坏的合成结果与缓存）。

    判据：实测时长不能短于「文本按最快语速朗读」所需的时间。
    例：43 字文本至少应有 43 / 12 ≈ 3.6 秒；实测 0.19 秒 → 判为损坏。
    """
    duration = _mp3_duration(raw)
    if duration <= 0:
        return False
    expected_min = max(0.3, len(text or "") / _MAX_TTS_CHARS_PER_SECOND)
    return duration >= expected_min


def _cache_hit(cache: Path, expect_text: str = "") -> bool:
    """缓存命中判定：必须存在、非空，且**音频内容有效**。

    - 后台补写任务与在线请求并发时，会短暂出现「文件已创建但内容还没写完」；
      只看 `exists()` 会把这种半成品当命中，返回 0 字节的 mp3 并永久缓存下去。
    - 更隐蔽的是「写完了但是截断的」：文件非空（576 字节），时长却只有 0.19 秒。
      这种坏缓存一旦写入就再也不会被刷新，前端永远拿到错位的音频与字幕。

    因此这里按实测时长校验；无效则当作未命中，交给上层重新合成并覆盖。
    `expect_text` 为对应文本，用于判断时长是否合理。
    """
    try:
        if not cache.is_file() or cache.stat().st_size <= 0:
            return False
        raw = cache.read_bytes()
    except OSError:
        return False
    # 无文本上下文时只做「能被解析出帧」的最低校验
    return _audio_is_valid(raw, expect_text)


def _write_cache_atomic(cache: Path, payload: bytes) -> None:
    """原子写缓存：先写临时文件再 os.replace，避免读到写了一半的文件。"""
    if not payload:
        return
    tmp = cache.with_name(f"{cache.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(payload)
        os.replace(tmp, cache)
    except OSError as exc:  # 缓存写失败不影响本次返回
        print(f"[tts] 缓存写入失败（忽略）：{exc}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _estimate_sentences(sentences: list[str]) -> list[dict]:
    """无字级时间戳时，按字符数比例估算每句时间（中文约 4.5 字/秒）。"""
    total_chars = sum(len(s) for s in sentences) or 1
    duration = max(1.0, total_chars / 4.5)
    out, cursor = [], 0.0
    for s in sentences:
        seg = duration * (len(s) / total_chars)
        out.append({"text": s, "start": round(cursor, 2), "end": round(cursor + seg, 2)})
        cursor += seg
    return out


async def _edge_within_budget(
    text: str, voice: str, rate: str, budget: float | None = None
) -> dict | None:
    """在时间预算内等待 edge-tts；超时则立刻放弃（改用下一个提供方）。

    超时的合成任务不会被取消，而是转入后台继续跑并写入缓存，
    因此用户第一次听到别的音源，后续同文本即可命中高质量音频。
    """
    import asyncio

    budget = max(0.5, float(budget if budget is not None else settings.tts_edge_timeout_seconds))
    task = asyncio.ensure_future(_synthesize_edge_inner(text, voice, rate))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=budget)
    except asyncio.TimeoutError:
        print(f"[tts] edge-tts 超过 {budget}s 预算，改用其它音源，后台继续合成并缓存")

        async def _finish() -> None:
            try:
                res = await task
                if res:
                    print(f"[tts] 后台合成完成，已缓存：{text[:18]}…")
            except Exception as exc:  # noqa: BLE001
                print(f"[tts] 后台合成失败：{exc}")

        asyncio.ensure_future(_finish())
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] edge-tts 异常，改用其它音源：{exc}")
        return None


async def _synthesize_edge_inner(text: str, voice: str, rate: str) -> dict | None:
    try:
        import edge_tts
    except Exception:  # noqa: BLE001
        return None

    try:
        sentences = split_sentences(text)
        cache = _cache_path(text, voice, rate)
        boundaries: list[tuple[int, int, int]] = []  # (offset_ticks, duration_ticks, char_index)

        if not _cache_hit(cache, text):
            communicate = edge_tts.Communicate(text, voice_name(voice), rate=rate)
            audio = bytearray()
            scanned = 0
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio.extend(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word = chunk.get("text", "")
                    pos = text.find(word, scanned)
                    if pos >= 0:
                        scanned = pos + len(word)
                    else:
                        pos = scanned
                    boundaries.append((chunk.get("offset", 0), chunk.get("duration", 0), pos))
            if not audio:
                return None
            # 截断的合成结果不能写缓存：一旦写入就会永久复用，导致字幕与语音错位
            if not _audio_is_valid(bytes(audio), text):
                print(
                    f"[tts] edge 合成结果异常（{len(audio)} 字节，"
                    f"{_mp3_duration(bytes(audio)):.2f}s，文本 {len(text)} 字），不写缓存"
                )
                return None
            _write_cache_atomic(cache, bytes(audio))

        # 字节级缓存命中时仍用估算时间戳（真实项目可把 boundaries 一并缓存）
        if boundaries:
            ticks_total = max((b[0] + b[1] for b in boundaries), default=0) or 1
            units = 10_000_000  # edge-tts offset 单位为 100ns
            total_seconds = ticks_total / units
            out = []
            for s in sentences:
                start_char = text.find(s)
                end_char = start_char + len(s)
                inside = [b for b in boundaries if start_char <= b[2] < end_char]
                if inside:
                    st = inside[0][0] / units
                    en = (inside[-1][0] + inside[-1][1]) / units
                else:
                    st = en = start_char / max(len(text), 1) * total_seconds
                out.append({"text": s, "start": round(st, 2), "end": round(en, 2)})
            sentences_out = out
            duration = total_seconds
        else:
            sentences_out = _estimate_sentences(sentences)
            duration = sentences_out[-1]["end"] if sentences_out else 0.0

        return {
            "provider": "edge",
            "audio_url": f"/static/tts_cache/{cache.name}",
            "mime": "audio/mpeg",
            "sentences": sentences_out,
            "duration_estimate": round(duration, 2),
            "fallback_text": text,
        }
    except Exception as exc:  # noqa: BLE001 网络失败等 → 降级浏览器
        print(f"[tts] edge-tts 合成失败，降级浏览器语音: {exc}")
        return None


def _parse_rate_to_aliyun(rate: str) -> int:
    """把 "+10%" / "-5%" 这种前端语速映射为阿里云 speech_rate（-500~500）。"""
    try:
        num = int(re.sub(r"[^0-9-]", "", rate or "0")) if rate else 0
    except ValueError:
        num = 0
    return max(-500, min(500, num * 10))


def _rescale_sentences(sentences: list[dict], real_duration: float) -> list[dict]:
    """把按字数估算的时间轴整体缩放到音频真实时长。

    阿里云 RESTful 链路没有字级时间戳，`_estimate_sentences` 按 4.5 字/秒 估算，
    与实测音频常有 10%~30% 偏差。这里按比例校正后，每条分句的 start/end 都落在
    真实音频时间上 —— 前端可以少做一层补偿，字幕与语音也更贴合。
    """
    if not sentences or real_duration <= 0.5:
        return sentences
    est_total = (sentences[-1].get("end") or 0.0)
    if est_total <= 0.5:
        return sentences
    k = real_duration / est_total
    # 差异极小就不动，避免引入无意义的浮点抖动
    if abs(k - 1.0) < 0.02:
        return sentences
    return [
        {
            "text": s.get("text", ""),
            "start": round((s.get("start") or 0.0) * k, 2),
            "end": round((s.get("end") or 0.0) * k, 2),
        }
        for s in sentences
    ]


def _aliyun_result(text: str, cache: Path) -> dict:
    """阿里云合成结果（音频已落缓存）。

    时间轴优先按音频**实测时长**缩放（比纯按字数估算准），
    这样前端的字幕与语音同步误差更小。
    """
    sentences = split_sentences(text)
    estimated = _estimate_sentences(sentences)

    duration = 0.0
    try:
        duration = _mp3_duration(cache.read_bytes())
    except OSError:
        duration = 0.0
    if duration > 0.5:
        estimated = _rescale_sentences(estimated, duration)

    return {
        "provider": "aliyun",
        "audio_url": f"/static/tts_cache/{cache.name}",
        "mime": "audio/mpeg",
        "sentences": estimated,
        "duration_estimate": round(
            estimated[-1]["end"] if estimated else 0.0, 2
        ),
        "fallback_text": text,
    }


def _aliyun_audio_via_sdk(text: str, voice: str, rate: str, token: str) -> bytes | None:
    """SDK（WebSocket）链路：长文本原生支持，装了 SDK 时优先用。"""
    try:
        from nls.speech_synthesizer import NlsSpeechSynthesizer
    except Exception:  # noqa: BLE001
        return None

    audio = bytearray()
    errors: list[str] = []

    def _on_data(data, *_):
        audio.extend(data)

    def _on_error(msg, *_):
        # 不能吞掉云端错误：AppKey 无权限、欠费等全靠它才能定位
        errors.append(str(msg))

    try:
        syn = NlsSpeechSynthesizer(
            url="wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1",
            token=token,
            appkey=settings.aliyun_tts_appkey,
            on_data=_on_data,
            on_completed=lambda *_a: None,
            on_error=_on_error,
            on_close=lambda *_a: None,
            callback_args=[],
        )
        syn.start(
            text=text,
            voice=aliyun_voice(voice),
            aformat="mp3",
            sample_rate=24000,
            volume=50,
            speech_rate=_parse_rate_to_aliyun(rate),
            pitch_rate=0,
            wait_complete=True,
            start_timeout=10,
            completed_timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] 阿里云 SDK 合成失败，改用其它链路: {exc}")
        return None

    if audio:
        return bytes(audio)
    if errors:
        _log_once(
            "aliyun_synth_error",
            f"[tts] 阿里云合成返回错误（AppKey/TTS 权限或音色不匹配）：{errors[0][:300]}",
        )
    return None


def _split_for_restful(text: str) -> list[str]:
    """按句切分到 RESTful 接口的 300 字符上限内（超出会被静默截断）。"""
    if len(text) <= _ALIYUN_TTS_TEXT_LIMIT:
        return [text]
    chunks: list[str] = []
    buf = ""
    for sentence in split_sentences(text) or [text]:
        while len(sentence) > _ALIYUN_TTS_TEXT_LIMIT:
            # 单句就超限：硬切（正常情况下不会走到，中文长句很少超过 300 字）
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(sentence[: _ALIYUN_TTS_TEXT_LIMIT])
            sentence = sentence[_ALIYUN_TTS_TEXT_LIMIT:]
        if len(buf) + len(sentence) > _ALIYUN_TTS_TEXT_LIMIT:
            chunks.append(buf)
            buf = sentence
        else:
            buf += sentence
    if buf:
        chunks.append(buf)
    return chunks or [text]


def _strip_id3v2(data: bytes) -> bytes:
    """去掉 MP3 开头的 ID3v2 标签：多段音频直接拼接时，中途的 ID3 会让播放器提前停止。"""
    if len(data) < 10 or data[:3] != b"ID3":
        return data
    size = (
        ((data[6] & 0x7F) << 21)
        | ((data[7] & 0x7F) << 14)
        | ((data[8] & 0x7F) << 7)
        | (data[9] & 0x7F)
    )
    total = 10 + size + (10 if data[5] & 0x10 else 0)  # 标记了 footer 时再多 10 字节
    return data[total:] if 0 < total < len(data) else data


def _aliyun_audio_via_http(text: str, voice: str, rate: str, token: str) -> bytes | None:
    """RESTful HTTP 链路（`/stream/v1/tts`）：**不需要 NLS SDK**。

    文档：https://help.aliyun.com/document_detail/84435.html（单次上限 300 字符，超出截断）
    成功时 Content-Type 为 audio/mpeg，body 是二进制音频；失败时是 application/json 错误体。
    """
    try:
        import httpx
    except Exception:  # noqa: BLE001
        return None

    payload_base = {
        "appkey": settings.aliyun_tts_appkey,
        "format": "mp3",
        "sample_rate": 16000,  # 8000/16000 各音色都支持；24000 只有部分音色支持
        "voice": aliyun_voice(voice),
        "volume": 50,
        "speech_rate": _parse_rate_to_aliyun(rate),
        "pitch_rate": 0,
    }
    audio = bytearray()
    for chunk in _split_for_restful(text):
        body = dict(payload_base, text=chunk)
        try:
            resp = httpx.post(
                _ALIYUN_TTS_REST_URL,
                json=body,
                # token 放请求头，不落进 URL / 日志
                headers={"X-NLS-Token": token, "Content-Type": "application/json"},
                timeout=httpx.Timeout(20.0, connect=5.0),
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[tts] 阿里云 RESTful 请求异常，改用其它链路: {exc}")
            return None

        content_type = (resp.headers.get("Content-Type") or "").lower()
        if resp.status_code != 200 or "audio" not in content_type:
            detail = resp.text[:300]
            _log_once(
                "aliyun_rest_error",
                f"[tts] 阿里云 RESTful 合成失败（HTTP {resp.status_code} / {content_type or '无 Content-Type'}）：{detail}",
            )
            return None

        piece = resp.content
        if audio:
            piece = _strip_id3v2(piece)  # 只保留第一段的 ID3
        audio.extend(piece)

    return bytes(audio) if audio else None


def _synthesize_aliyun_inner(text: str, voice: str, rate: str) -> dict | None:
    """阿里云 TTS 合成（同步阻塞，需在 to_thread 中调用）。

    链路：SDK（WebSocket，装了才走）→ RESTful HTTP（无需 SDK）。
    """
    cache = _cache_path(text, "aliyun|" + voice, rate)
    if _cache_hit(cache, text):
        return _aliyun_result(text, cache)

    token = _get_aliyun_token()
    if not token:
        return None

    audio = _aliyun_audio_via_sdk(text, voice, rate, token)
    if not audio:
        audio = _aliyun_audio_via_http(text, voice, rate, token)
    if not audio:
        # 两条链路都失败：具体原因已由 token / RESTful 的日志给出，这里不再叠加提示
        print("[tts] 阿里云未返回音频，改用其它音源")
        return None

    # 截断的合成结果不能写缓存（见 _audio_is_valid 的说明）：
    # 一旦写入就会永久复用，前端只会拿到错位的音频与字幕。
    if not _audio_is_valid(audio, text):
        print(
            f"[tts] 阿里云合成结果异常（{len(audio)} 字节，"
            f"{_mp3_duration(audio):.2f}s，文本 {len(text)} 字），改用其它音源"
        )
        return None

    _write_cache_atomic(cache, audio)
    return _aliyun_result(text, cache)


async def _aliyun_within_budget(
    text: str, voice: str, rate: str, budget: float | None = None
) -> dict | None:
    """在时间预算内等待阿里云合成；超时改用下一个提供方。"""
    budget = max(1.0, float(budget if budget is not None else settings.tts_edge_timeout_seconds * 2))
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_synthesize_aliyun_inner, text, voice, rate),
            timeout=budget,
        )
    except asyncio.TimeoutError:
        print(f"[tts] 阿里云 TTS 超过 {budget}s，改用其它音源")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"[tts] 阿里云 TTS 异常，改用其它音源: {exc}")
        return None


async def synthesize(text: str, voice: str = "female", rate: str = "+0%") -> dict:
    """合成语音。返回 schemas.TTSResponse 的 dict 形态。

    逐级尝试**可用**的后端提供方（显式配置的优先，其余兜底），
    全部失败才回落到 `provider="browser"`（前端静音 + 字幕）。
    旧实现只试配置的那一个，失败就直接报 browser：配了 aliyun 但环境不满足时，
    表现就是「面试官语音加载失败」这种没有任何声音、也没有任何日志的静默故障。
    """
    plain = strip_annotation(text)
    if not plain:
        # 没有可朗读文本：必须回报 browser，不能回报 edge/aliyun——
        # 前端是按 provider 决定「播 audio_url」还是「不用后端音频」的，
        # 报 edge 但 audio_url 为空会让前端去播一个空地址。
        return {
            "provider": "browser",
            "audio_url": "",
            "mime": "audio/mpeg",
            "sentences": [],
            "duration_estimate": 0.0,
            "fallback_text": "",
        }

    providers = tts_provider_order()
    for idx, provider in enumerate(providers):
        if provider == "edge":
            result = await _edge_within_budget(plain, voice, rate)
        else:
            # 接入点：阿里云语音合成（NlsSpeechSynthesizer，需 AppKey + AccessKey/Token + SDK）
            result = await _aliyun_within_budget(plain, voice, rate)
        if result:
            return result
        if idx + 1 < len(providers):
            print(f"[tts] {provider} 合成失败，改用 {providers[idx + 1]} 兜底")

    # 兜底：只回 browser，前端不再使用浏览器 speechSynthesis（见 README「TTS 降级说明」）
    sentences = split_sentences(plain)
    estimated = _estimate_sentences(sentences)
    return {
        "provider": "browser",
        "audio_url": "",
        "mime": "audio/mpeg",
        "sentences": estimated,
        "duration_estimate": estimated[-1]["end"] if estimated else 0.0,
        "fallback_text": plain,
    }


# ------------------------------------------------------------
# ASR 识别
# ------------------------------------------------------------


def _recognize_aliyun_pcm(pcm: bytes, rate: int = 16000) -> dict | None:
    """用阿里云实时识别（NlsSpeechRecognizer）识别音频。

    pcm 需为 rate 采样率单声道 16bit 裸 PCM（可带 WAV 文件头，会自动剥掉）。
    成功返回 {provider, text, confidence, is_final}，失败返回 None（调用方降级为 browser）。

    注意：阿里云实时识别必须「分块流式 + 帧间小延时」发送，一次性整包 send 服务端
    会收不到 VAD 结果、只回 RecognitionStarted 就挂起。识别结果通过 on_result_changed
    逐步返回且是**累积**文本，需取最后一条，而不要逐条拼接。
    """
    try:
        from nls.speech_recognizer import NlsSpeechRecognizer
    except Exception:  # noqa: BLE001
        _has_aliyun_sdk("speech_recognizer")  # 记录「SDK 未安装」的原因（只打一次）
        return None

    token = _get_aliyun_token()
    if not token:
        return None

    # 剥掉 WAV 头，得到裸 PCM
    if pcm[:4] == b"RIFF" and len(pcm) > 44:
        pcm = pcm[44:]

    # 尾部补 0.6s 静音，模拟自然句末停顿，触发 VAD 结束
    pcm = pcm + b"\x00\x00" * int(rate * 0.6)

    text_holder: list[str] = [""]

    def _on_result(msg, *_):
        # 实时识别中间结果：文本在 payload.result，属累积文本
        try:
            import json

            payload = json.loads(msg)
            text = (payload.get("payload") or {}).get("result") or payload.get("result") or ""
            if text:
                text_holder[0] = text
        except Exception:  # noqa: BLE001
            pass

    def _on_completed(_msg=None, *_):
        pass

    chunk = int(rate * 0.1) * 2  # 100ms 一帧（16bit x 声道数）
    rec = None
    try:
        rec = NlsSpeechRecognizer(
            url="wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1",
            token=token,
            appkey=settings.aliyun_asr_appkey,
            on_result_changed=_on_result,
            on_completed=_on_completed,
            on_error=lambda *_a: None,
            on_close=lambda *_a: None,
            callback_args=[],
        )
        rec.start(
            aformat="pcm",
            sample_rate=rate,
            ch=1,
            enable_intermediate_result=True,
            enable_punctuation_prediction=True,
            timeout=15,
        )
        for i in range(0, len(pcm), chunk):
            rec.send_audio(pcm[i : i + chunk])
            time.sleep(0.02)
        rec.stop(timeout=20)
    except Exception as exc:  # noqa: BLE001
        print(f"[asr] 阿里云识别异常，降级浏览器识别: {exc}")
        # 已有部分中间结果时仍然继续用（下面会取 text_holder 的最后结果）
        if not text_holder[0]:
            return None
    finally:
        if rec is not None:
            try:
                rec.shutdown()
            except Exception:  # noqa: BLE001
                pass

    text = text_holder[0].strip()
    if text:
        return {"provider": "aliyun", "text": text, "confidence": 0.0, "is_final": True}
    return None


async def recognize(audio_bytes: bytes) -> dict:
    """识别语音，返回 schemas.ASRResponse 的 dict 形态。

    链路：aliyun（一句话识别）→ browser（前端 Web Speech API）。
    前端应上传 16kHz 单声道 16bit 的裸 PCM（或带 WAV 头）；容器格式
    （webm/ogg）无法直接转码，识别会走 browser 兜底而非本地转码。
    """
    provider = asr_status()

    if provider == "aliyun":
        result = await asyncio.to_thread(_recognize_aliyun_pcm, audio_bytes)
        if result:
            return result
        print("[asr] 阿里云识别未返回文本，降级浏览器识别")

    return {
        "provider": "browser",
        "text": "",
        "confidence": 0.0,
        "is_final": True,
    }
