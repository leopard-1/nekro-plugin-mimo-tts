"""兼容旧版按模块安装的入口；实现与 __init__.py 保持一致。"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import tempfile
import time
import wave
from contextvars import ContextVar
from pathlib import Path
from typing import Annotated, Any, List

import httpx
from nekro_agent.api.plugin import (
    Arg,
    CmdCtl,
    CommandExecutionContext,
    CommandPermission,
    CommandResponse,
    ConfigBase,
    ExtraField,
    NekroPlugin,
    SandboxMethodType,
)
from nekro_agent.api.schemas import AgentCtx
from nekro_agent.models.db_chat_channel import DBChatChannel
from nekro_agent.schemas.chat_message import ChatMessage, ChatType
from nekro_agent.tools.message_id import normalize_ref_msg_id
from pydantic import Field, field_validator

plugin = NekroPlugin(
    name="MiMo 文本转语音",
    module_name="mimo_tts",
    description="基于小米 MiMo-V2.5-TTS 系列模型，将文本合成为语音消息并发送到群聊或私聊。",
    version="0.3.0",
    author="Sakuralis",
    url="https://github.com/leopard-1/nekro-plugin-mimo-tts",
    support_adapter=[
        "onebot_v11",
        "wechat_openilink",
        "wechat_ilink_multi",
        "wechatpad",
        "wxwork",
        "wxwork_corp_app",
        "telegram",
        "discord",
        "qqbot_openclaw",
        "web",
        "sse",
    ],
    allow_sleep=True,
    sleep_brief="当用户希望 AI 用语音回复、朗读文本、配音或把文字转换成语音消息时激活。",
)

MIMO_TTS_MODELS = {
    "mimo-v2.5-tts",
    "mimo-v2.5-tts-voicedesign",
}
MIMO_BUILTIN_VOICES = {
    "mimo_default",
    "冰糖",
    "茉莉",
    "苏打",
    "白桦",
    "Mia",
    "Chloe",
    "Milo",
    "Dean",
}
STREAM_SAMPLE_RATE = 24000
STREAM_SAMPLE_WIDTH = 2
STREAM_CHANNELS = 1
_CHANNEL_SPLIT_RE = re.compile(r"[,，\s;/|]+")


def _split_supported_channel_config(raw: str) -> List[str]:
    return [item.strip() for item in _CHANNEL_SPLIT_RE.split(raw) if item.strip()]


@plugin.mount_config()
class MiMoTTSConfig(ConfigBase):
    """MiMo TTS 配置。"""

    MIMO_API_KEY: str = Field(
        default="",
        title="MiMo API Key",
        description="小米 MiMo API Key。可从 MiMo API 开放平台获取。",
        json_schema_extra=ExtraField(is_secret=True, placeholder="sk-...").model_dump(),
    )
    ENABLE_TTS: bool = Field(
        default=False,
        title="启用文本转语音",
        description="总开关。开启后会向 Agent 注入语音回复工具，命令也会允许发送语音。",
    )
    SUPPORTED_CHANNELS: List[str] = Field(
        default_factory=list,
        title="支持的频道",
        description=(
            "精确频道白名单。按 OneBot 风格填写 onebot_group_123456789 或 "
            "onebot_private_123456789；也兼容 Nekro 原始 chat_key：onebot_v11-group_123456789。"
        ),
        json_schema_extra=ExtraField(
            sub_item_name="频道",
            placeholder="onebot_group_123456789",
        ).model_dump(),
    )
    CUSTOM_PERSONA: str = Field(
        default="",
        title="可定制的人设内容",
        description="用于 MiMo voice design 的音色/角色/语气描述。非空时优先于官方预设音色，并自动使用 voice design 模型。",
        json_schema_extra=ExtraField(
            is_textarea=True,
            placeholder="例如：年轻女性，声音清澈偏甜，语速略快，语气轻快自然。",
        ).model_dump(),
    )
    PRESET_VOICE: str = Field(
        default="mimo_default",
        title="官方预设音色",
        description="当“可定制的人设内容”为空时生效。支持 mimo_default、冰糖、茉莉、苏打、白桦、Mia、Chloe、Milo、Dean。",
        json_schema_extra=ExtraField(placeholder="mimo_default").model_dump(),
    )
    STREAM_OUTPUT: bool = Field(
        default=False,
        title="选择流式输出",
        description="开启后使用 MiMo SSE 流式接口并拼接 PCM16 音频；关闭后使用非流式 WAV 输出。",
    )
    SYNTHESIS_TIMEOUT: float = Field(
        default=60.0,
        title="语音合成超时时间",
        description="单段语音合成超时时间，单位秒。",
        ge=5.0,
        le=300.0,
    )
    MODEL: str = Field(
        default="mimo-v2.5-tts",
        title="选择模型",
        description="支持 mimo-v2.5-tts 和 mimo-v2.5-tts-voicedesign。填写自定义人设时会优先使用 voice design。",
        json_schema_extra=ExtraField(placeholder="mimo-v2.5-tts").model_dump(),
    )
    MAX_VOICE_DURATION: int = Field(
        default=60,
        title="最长语音消息时长",
        description="单条语音最长时长，单位秒。文本较长时会按估算拆成多条语音。",
        ge=3,
        le=300,
    )
    API_BASE_URL: str = Field(
        default="https://api.xiaomimimo.com/v1",
        title="MiMo API Base URL",
        description="默认使用官方 OpenAI 兼容接口地址。",
    )
    FALLBACK_TEXT_ON_FAILURE: bool = Field(
        default=True,
        title="失败时回退文本",
        description="合成或发送语音失败时，是否回退发送原始文本。",
    )
    VOICE_FILE_FALLBACK: bool = Field(
        default=True,
        title="语音发送失败时改发音频文件",
        description=(
            "当 NapCat 缺少 ntsilk/ffmpeg 支持，语音消息转换失败时，自动改为发送 WAV 音频文件，"
            "避免直接丢弃语音或只回退为文本。"
        ),
    )
    VOICE_RETRY_INTERVAL: int = Field(
        default=1800,
        title="语音能力重试间隔",
        description="语音转换失败后，暂停尝试语音消息的秒数；到期后会重新尝试发送语音消息。",
        ge=60,
        le=86400,
    )
    SYNC_TEXT_WITH_VOICE: bool = Field(
        default=False,
        title="文字与语音同步发送",
        description=(
            "开启后，自动回复会同时发送文字和语音，避免只发语音导致用户看不到文字。"
            "关闭时默认只发语音；用户说“打字/用文字”等提示时会临时改为同步发送。"
        ),
    )
    TEXT_SYNC_TURNS: int = Field(
        default=5,
        title="临时同步发送轮数",
        description=(
            "用户说“打字/用文字”等提示后，临时开启文字+语音同步的用户回合数。"
            "用户明确说“接下来N次”时以用户说明为准。"
        ),
        ge=1,
        le=50,
    )
    MAX_SEGMENTS_PER_MESSAGE: int = Field(
        default=4,
        title="单次最多语音条数",
        description="长文本拆分后的最多语音条数，避免一次发送过多语音。",
        ge=1,
        le=10,
    )

    @field_validator("SUPPORTED_CHANNELS", mode="before")
    @classmethod
    def validate_supported_channels(cls, value: Any) -> List[str]:
        """兼容旧版逗号/换行字符串配置。"""
        if isinstance(value, list):
            channels: List[str] = []
            for item in value:
                if isinstance(item, str):
                    channels.extend(_split_supported_channel_config(item))
            return channels
        if isinstance(value, str):
            return _split_supported_channel_config(value)
        return []


config = plugin.get_config(MiMoTTSConfig)
_AUTO_TTS_BYPASS: ContextVar[bool] = ContextVar("mimo_tts_bypass", default=False)
_ORIGINAL_SEND_TEXT: Any = None
_VOICE_FAILURE_UNTIL: float = 0.0
_VOICE_FAILURE_REASON: str = ""
_VOICE_FAILURE_LOGGED_AT: float = 0.0

TEXT_MODE_SYNC = "sync"
TEXT_MODE_VOICE = "voice"
TEXT_MODE_TEXT = "text"
TEXT_MODE_AUTO = "auto"
TEXT_MODE_LABELS = {
    TEXT_MODE_SYNC: "文字+语音同步",
    TEXT_MODE_VOICE: "仅语音",
    TEXT_MODE_TEXT: "仅文字",
}
_TEXT_MODE_OVERRIDES: dict[str, dict[str, Any]] = {}
_TEXT_MODE_PENDING: set[str] = set()

_COUNT_RE = re.compile(r"(\d+)\s*(?:次|条|轮|回合|句)")
_PERSIST_RE = re.compile(r"以后|今后|往后|每次|一直|始终|永久")
_RESET_RE = re.compile(r"恢复默认|自动模式|正常模式|默认模式")
_TEXT_ONLY_RE = re.compile(
    r"只要文字|仅文字|只用文字|只发文字|只打字|不要语音|别发语音|不用语音|别用语音|不要发语音|"
    r"文字就行|文字即可|打字就行|不想听语音|不想用语音|别念",
)
_VOICE_ONLY_RE = re.compile(
    r"只要语音|仅语音|只用语音|只发语音|不要文字|别打字|不要打字|别打文字|不用打字|不用文字|别发文字|不用发文字|"
    r"不要发文字|语音就行|语音即可|不想看文字|取消同步|关闭同步|停止同步|不用同步|不要同步|别同步|不同步",
)
_SYNC_WORD_RE = re.compile(r"打字|打文字|文字|文本|同步|文字和语音|语音和文字|一起发")
_SYNC_INTENT_RE = re.compile(
    r"以后|今后|往后|每次|一直|始终|请|麻烦|帮我|给我|我要|能不能|可以|能否|"
    r"改成|切换|使用|用|发|回复|接下来|同步",
)
_SYNC_EXACT = {
    "打字",
    "打文字",
    "文字",
    "文本",
    "用文字",
    "发文字",
    "文字回复",
    "文本回复",
    "同步",
    "同步发送",
    "同步输出",
    "文字和语音",
    "语音和文字",
    "语音同步",
    "一起发",
    "文字语音",
    "语音文字",
}
_SYNC_PREFIXES = (
    "打字",
    "打文字",
    "文字",
    "文本",
    "同步",
    "用文字",
    "发文字",
    "文字和语音",
    "语音和文字",
    "语音同步",
    "一起发",
)


def _normalize_model() -> str:
    model = (config.MODEL or "mimo-v2.5-tts").strip()
    if model not in MIMO_TTS_MODELS:
        plugin.logger.warning(f"未知 MiMo TTS 模型 {model!r}，已回退到 mimo-v2.5-tts")
        model = "mimo-v2.5-tts"

    if config.CUSTOM_PERSONA.strip():
        return "mimo-v2.5-tts-voicedesign"

    if model == "mimo-v2.5-tts-voicedesign":
        plugin.logger.warning("voice design 模型需要填写 CUSTOM_PERSONA，已回退到内置音色模型")
        return "mimo-v2.5-tts"

    return model


def _normalize_voice() -> str:
    voice = (config.PRESET_VOICE or "mimo_default").strip() or "mimo_default"
    if voice.lower() in {"mimo", "mimo-default"} or voice in {"MiMo", "MiMo-默认", "默认"}:
        return "mimo_default"
    if voice not in MIMO_BUILTIN_VOICES:
        plugin.logger.warning(f"未知 MiMo 官方预设音色 {voice!r}，仍将按原值提交给 MiMo API")
    return voice


def _configured_supported_channels() -> List[str]:
    raw_channels = config.SUPPORTED_CHANNELS or []
    if isinstance(raw_channels, str):
        return _split_supported_channel_config(raw_channels)

    channels: List[str] = []
    for item in raw_channels:
        if isinstance(item, str):
            channels.extend(_split_supported_channel_config(item))
    return channels


def _normalize_supported_channel_key(raw: str) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None

    short_match = re.fullmatch(r"onebot_(group|private)_(.+)", value, flags=re.IGNORECASE)
    if short_match:
        chat_type = short_match.group(1).lower()
        chat_id = short_match.group(2).strip()
        return f"onebot_v11-{chat_type}_{chat_id}"

    compat_match = re.fullmatch(r"onebot_v11_(group|private)_(.+)", value, flags=re.IGNORECASE)
    if compat_match:
        chat_type = compat_match.group(1).lower()
        chat_id = compat_match.group(2).strip()
        return f"onebot_v11-{chat_type}_{chat_id}"

    broad_values = {"group", "groups", "private", "privates", "all", "群聊", "群", "私聊", "私信", "私", "全部"}
    if value.lower() in broad_values:
        return None

    return value


def _supported_chat_keys() -> set[str]:
    return {
        chat_key
        for chat_key in (_normalize_supported_channel_key(item) for item in _configured_supported_channels())
        if chat_key
    }


def _display_channel_key(chat_key: str) -> str:
    match = re.fullmatch(r"onebot_v11-(group|private)_(.+)", chat_key, flags=re.IGNORECASE)
    if match:
        return f"onebot_{match.group(1).lower()}_{match.group(2)}"
    return chat_key


def _format_supported_channels() -> str:
    chat_keys = sorted(_supported_chat_keys())
    if not chat_keys:
        return "未配置"

    display_keys = [_display_channel_key(chat_key) for chat_key in chat_keys]
    shown = display_keys[:5]
    suffix = f" 等 {len(display_keys)} 个" if len(display_keys) > len(shown) else ""
    return "、".join(shown) + suffix


async def _is_channel_allowed(chat_key: str) -> bool:
    if not config.ENABLE_TTS:
        return False

    normalized_chat_key = _normalize_supported_channel_key(chat_key) or chat_key
    return normalized_chat_key in _supported_chat_keys()


def _text_mode_state_key(chat_key: str) -> str:
    return _normalize_supported_channel_key(chat_key) or chat_key


def _set_text_mode_override(chat_key: str, mode: str, remaining: int) -> None:
    key = _text_mode_state_key(chat_key)
    _TEXT_MODE_OVERRIDES[key] = {
        "mode": mode,
        "remaining": remaining,
    }
    if remaining < 0:
        _TEXT_MODE_PENDING.discard(key)


def _clear_text_mode_override(chat_key: str) -> None:
    key = _text_mode_state_key(chat_key)
    _TEXT_MODE_OVERRIDES.pop(key, None)
    _TEXT_MODE_PENDING.discard(key)


def _get_text_mode_override(chat_key: str) -> dict[str, Any] | None:
    return _TEXT_MODE_OVERRIDES.get(_text_mode_state_key(chat_key))


def _effective_text_mode(chat_key: str) -> str:
    """用户临时设置优先于配置开关。"""
    override = _get_text_mode_override(chat_key)
    if override:
        return str(override.get("mode") or TEXT_MODE_AUTO)
    return TEXT_MODE_SYNC if config.SYNC_TEXT_WITH_VOICE else TEXT_MODE_VOICE


def _consume_text_mode(chat_key: str) -> None:
    """每个用户回合最多消耗一次临时设置；持续设置（-1）不消耗。"""
    key = _text_mode_state_key(chat_key)
    override = _TEXT_MODE_OVERRIDES.get(key)
    if not override:
        _TEXT_MODE_PENDING.discard(key)
        return

    remaining = int(override.get("remaining", 0))
    if remaining < 0:
        return

    # 同一个用户回合里可能发送多条消息（进度、语音、同步文字），只计一次对话。
    if key not in _TEXT_MODE_PENDING:
        return
    _TEXT_MODE_PENDING.discard(key)

    remaining -= 1
    if remaining <= 0:
        _TEXT_MODE_OVERRIDES.pop(key, None)
        plugin.logger.info(f"文字发送临时偏好已到期，恢复配置默认: {key}")
    else:
        override["remaining"] = remaining


def _detect_text_mode_instruction(text: str) -> tuple[str, int] | None:
    """从用户消息中识别发送方式偏好，返回 (模式, 剩余次数)。

    次数为 -1 表示持续生效。识别失败返回 None。
    """
    raw = (text or "").strip()
    if not raw or len(raw) > 160:
        return None
    if raw.startswith(("/", "!", "！")) or "```" in raw:
        return None

    compact = re.sub(r"\s+", "", raw)
    if not compact:
        return None

    if _RESET_RE.search(compact):
        return (TEXT_MODE_AUTO, 0)

    if _TEXT_ONLY_RE.search(compact):
        mode = TEXT_MODE_TEXT
    elif _VOICE_ONLY_RE.search(compact):
        mode = TEXT_MODE_VOICE
    elif (
        compact in _SYNC_EXACT
        or compact.startswith(_SYNC_PREFIXES)
        or (_SYNC_WORD_RE.search(compact) and _SYNC_INTENT_RE.search(compact))
    ):
        mode = TEXT_MODE_SYNC
    else:
        return None

    turns = max(1, min(50, int(config.TEXT_SYNC_TURNS or 5)))
    count_match = _COUNT_RE.search(compact)
    if count_match:
        turns = max(1, min(50, int(count_match.group(1))))
    if _PERSIST_RE.search(compact):
        turns = -1

    return (mode, turns)


def _text_mode_status(chat_key: str) -> str:
    mode = _effective_text_mode(chat_key)
    label = TEXT_MODE_LABELS.get(mode, mode)
    override = _get_text_mode_override(chat_key)
    if not override:
        return f"{label}（跟随配置）"
    remaining = int(override.get("remaining", 0))
    if remaining < 0:
        return f"{label}（用户设置，持续生效）"
    return f"{label}（用户临时设置，剩余 {remaining} 轮对话）"


def _text_mode_prompt(chat_key: str) -> str:
    mode = _effective_text_mode(chat_key)
    if mode == TEXT_MODE_SYNC:
        text = (
            "当前为文字+语音同步模式：调用一次 `send_mimo_tts_message` 会同时发送文字和语音，"
            "不要再额外调用 `send_text`，避免重复。"
        )
    elif mode == TEXT_MODE_TEXT:
        text = "当前用户要求仅发送文字：不要调用 `send_mimo_tts_message`，直接使用 `send_text` 发送文字。"
    else:
        text = (
            "当前为仅语音模式：调用 `send_mimo_tts_message` 发送语音。"
            "若用户明确要求文字，请依赖插件根据用户最新说明切换的模式；"
            "代码、表格等不适合朗读的内容可直接调用 `send_text`。"
        )

    override = _get_text_mode_override(chat_key)
    if override and int(override.get("remaining", 0)) > 0:
        text += f"（用户临时设置的偏好还剩 {override['remaining']} 轮对话）"
    return text


def _validate_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        raise ValueError("语音文本不能为空")
    return cleaned


def _is_voice_conversion_error(exc: BaseException) -> bool:
    """识别 NapCat 缺少 ntsilk 支持导致的语音转换失败。"""
    text = str(exc).lower()
    if "ntsilk" in text:
        return True
    if "requested output format" in text and "silk" in text:
        return True
    if "silk" in text and ("ffmpeg" in text or "转换" in text or "语音" in text):
        return True
    return False


def _voice_attempt_allowed() -> bool:
    """语音转换失败后进入冷却期，避免每条消息都触发一次失败。"""
    return time.monotonic() >= _VOICE_FAILURE_UNTIL


def _mark_voice_failure(exc: BaseException) -> None:
    """记录失败并进入冷却期；同一冷却期内只输出一次修复提示。"""
    global _VOICE_FAILURE_UNTIL, _VOICE_FAILURE_REASON, _VOICE_FAILURE_LOGGED_AT

    interval = max(60, int(config.VOICE_RETRY_INTERVAL or 1800))
    _VOICE_FAILURE_UNTIL = time.monotonic() + interval
    _VOICE_FAILURE_REASON = str(exc).strip()[:500]

    now = time.monotonic()
    if now - _VOICE_FAILURE_LOGGED_AT < 60:
        return
    _VOICE_FAILURE_LOGGED_AT = now
    fallback_hint = "已临时改为发送音频文件" if config.VOICE_FILE_FALLBACK else "将按配置回退文本"
    plugin.logger.warning(
        f"NapCat 语音转换不可用（缺少 ntsilk/ffmpeg 支持），{fallback_hint}；"
        f"{interval} 秒后重试语音。修复方法见 README 的“语音发送失败”章节。"
        f"原始错误: {_VOICE_FAILURE_REASON}"
    )


def _voice_status_text() -> str:
    if _voice_attempt_allowed():
        return "语音（正常）"
    remaining = max(0, int(_VOICE_FAILURE_UNTIL - time.monotonic()))
    return f"语音（转换失败，{remaining} 秒后重试；当前降级为音频文件）"


def _estimate_chars_per_segment() -> int:
    # Chinese speech is often around 4-6 chars/s. Use a conservative value.
    return max(20, int(config.MAX_VOICE_DURATION) * 5)


def _split_text(text: str) -> list[str]:
    limit = _estimate_chars_per_segment()
    max_segments = max(1, int(config.MAX_SEGMENTS_PER_MESSAGE or 1))
    sentences = re.split(r"(?<=[。！？!?；;\n])", text)
    chunks: list[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > limit:
            flush_current()
            for start in range(0, len(sentence), limit):
                chunks.append(sentence[start : start + limit].strip())
            continue
        if current and len(current) + len(sentence) > limit:
            flush_current()
        current += sentence

    flush_current()
    return chunks[:max_segments]


def _build_payload(text: str, *, stream: bool) -> dict[str, Any]:
    model = _normalize_model()
    messages: list[dict[str, str]] = []
    audio: dict[str, Any] = {"format": "pcm16" if stream else "wav"}

    if model == "mimo-v2.5-tts-voicedesign":
        messages.append({"role": "user", "content": config.CUSTOM_PERSONA.strip()})
        audio["optimize_text_preview"] = False
    else:
        if config.CUSTOM_PERSONA.strip():
            messages.append({"role": "user", "content": config.CUSTOM_PERSONA.strip()})
        audio["voice"] = _normalize_voice()

    messages.append({"role": "assistant", "content": text})

    return {
        "model": model,
        "messages": messages,
        "audio": audio,
        "stream": stream,
    }


def _headers() -> dict[str, str]:
    api_key = config.MIMO_API_KEY.strip()
    if not api_key:
        raise ValueError("请先在插件配置中填写 MIMO_API_KEY")
    return {
        "api-key": api_key,
        "Content-Type": "application/json",
    }


def _endpoint() -> str:
    return f"{config.API_BASE_URL.rstrip('/')}/chat/completions"


def _write_wav_from_pcm16(pcm_bytes: bytes, output_path: Path) -> None:
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(STREAM_CHANNELS)
        wav_file.setsampwidth(STREAM_SAMPLE_WIDTH)
        wav_file.setframerate(STREAM_SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wav_file:
            frames = wav_file.getnframes()
            rate = wav_file.getframerate()
            if rate <= 0:
                return 0.0
            return frames / float(rate)
    except wave.Error:
        return 0.0


async def _synthesize_non_streaming(text: str, output_path: Path) -> Path:
    payload = _build_payload(text, stream=False)
    timeout = httpx.Timeout(float(config.SYNTHESIS_TIMEOUT), connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        response = await client.post(_endpoint(), headers=_headers(), json=payload)
        response.raise_for_status()
        data = response.json()

    try:
        audio_data = data["choices"][0]["message"]["audio"]["data"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"MiMo TTS 响应中未找到音频数据: {data}") from exc

    output_path.write_bytes(base64.b64decode(audio_data))
    return output_path


async def _synthesize_streaming(text: str, output_path: Path) -> Path:
    payload = _build_payload(text, stream=True)
    timeout = httpx.Timeout(float(config.SYNTHESIS_TIMEOUT), connect=15.0)
    chunks: list[bytes] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("POST", _endpoint(), headers=_headers(), json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line.removeprefix("data:").strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    event = json.loads(raw)
                    audio = event.get("choices", [{}])[0].get("delta", {}).get("audio")
                    if audio and audio.get("data"):
                        chunks.append(base64.b64decode(audio["data"]))
                except Exception as exc:
                    plugin.logger.warning(f"解析 MiMo 流式 TTS chunk 失败: {exc}")

    if not chunks:
        raise RuntimeError("MiMo 流式 TTS 未返回音频数据")

    _write_wav_from_pcm16(b"".join(chunks), output_path)
    return output_path


async def _synthesize_to_file(text: str) -> Path:
    suffix = ".wav"
    fd, raw_path = tempfile.mkstemp(prefix=f"mimo_tts_{int(time.time() * 1000)}_", suffix=suffix)
    import os

    os.close(fd)
    output_path = Path(raw_path)

    if config.STREAM_OUTPUT:
        return await _synthesize_streaming(text, output_path)
    return await _synthesize_non_streaming(text, output_path)


async def _send_onebot_record(ctx: AgentCtx, chat_key: str, audio_path: Path, ref_msg_id: str | None = None) -> None:
    del ctx
    from nekro_agent.adapters.onebot_v11.core.bot import get_bot
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    bot = get_bot()
    db_chat_channel = await DBChatChannel.get_channel(chat_key=chat_key)
    chat_id = int(db_chat_channel.channel_id.split("_")[1])
    message = Message()
    normalized_ref = normalize_ref_msg_id(ref_msg_id)
    if normalized_ref:
        message.append(MessageSegment.reply(id_=int(normalized_ref)))
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    message.append(MessageSegment.record(file=f"base64://{audio_b64}"))

    if db_chat_channel.chat_type == ChatType.GROUP:
        await bot.call_api("send_group_msg", group_id=chat_id, message=message)
    elif db_chat_channel.chat_type == ChatType.PRIVATE:
        await bot.call_api("send_private_msg", user_id=chat_id, message=message)
    else:
        raise ValueError(f"不支持的聊天类型: {db_chat_channel.chat_type}")


async def _send_onebot_file(ctx: AgentCtx, chat_key: str, audio_path: Path, ref_msg_id: str | None = None) -> None:
    """在 NapCat 无法完成语音转换时，改为把音频作为文件发送。

    优先走 Nekro 的文件通道；若协议端与 Agent 不在同一挂载环境，则回退到
    OneBot 的 base64 文件段，避免依赖 SANDBOX_ONEBOT_SERVER_MOUNT_DIR。
    """
    try:
        sandbox_path = await ctx.fs.mixed_forward_file(audio_path, file_name=audio_path.name)
        await ctx.ms.send_file(chat_key, sandbox_path, ctx, record=True, ref_msg_id=ref_msg_id)
        return
    except Exception as exc:
        plugin.logger.warning(f"通过 Nekro 文件通道发送音频失败，尝试 OneBot base64 直传: {exc}")

    from nekro_agent.adapters.onebot_v11.core.bot import get_bot
    from nonebot.adapters.onebot.v11 import Message, MessageSegment

    bot = get_bot()
    db_chat_channel = await DBChatChannel.get_channel(chat_key=chat_key)
    chat_id = int(db_chat_channel.channel_id.split("_")[1])
    message = Message()
    normalized_ref = normalize_ref_msg_id(ref_msg_id)
    if normalized_ref:
        message.append(MessageSegment.reply(id_=int(normalized_ref)))
    audio_b64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
    message.append(
        MessageSegment(
            type="file",
            data={"file": f"base64://{audio_b64}", "name": audio_path.name},
        ),
    )

    if db_chat_channel.chat_type == ChatType.GROUP:
        await bot.call_api("send_group_msg", group_id=chat_id, message=message)
    elif db_chat_channel.chat_type == ChatType.PRIVATE:
        await bot.call_api("send_private_msg", user_id=chat_id, message=message)
    else:
        raise ValueError(f"不支持的聊天类型: {db_chat_channel.chat_type}")


async def _send_audio_file(ctx: AgentCtx, chat_key: str, audio_path: Path, ref_msg_id: str | None = None) -> str:
    """发送音频，返回实际使用的发送方式（voice / file）。"""
    db_chat_channel = await DBChatChannel.get_channel(chat_key=chat_key)
    if db_chat_channel.adapter_key == "onebot_v11":
        if _voice_attempt_allowed():
            try:
                await _send_onebot_record(ctx, chat_key, audio_path, ref_msg_id)
                return "voice"
            except Exception as exc:
                if not _is_voice_conversion_error(exc):
                    raise
                _mark_voice_failure(exc)

        if not config.VOICE_FILE_FALLBACK:
            raise RuntimeError(
                "NapCat 语音转换失败，且配置中已关闭“语音发送失败时改发音频文件”",
            )

        await _send_onebot_file(ctx, chat_key, audio_path, ref_msg_id)
        return "file"

    sandbox_path = await ctx.fs.mixed_forward_file(audio_path, file_name=audio_path.name)
    await ctx.ms.send_file(chat_key, sandbox_path, ctx, record=True, ref_msg_id=ref_msg_id)
    return "file"


async def _send_text_direct(
    ctx: AgentCtx,
    chat_key: str,
    text: str,
    ref_msg_id: str | None = None,
    *,
    record: bool = True,
) -> None:
    """绕过自动 TTS 钩子直接发送文字。"""
    token = _AUTO_TTS_BYPASS.set(True)
    try:
        if _ORIGINAL_SEND_TEXT is not None:
            await _ORIGINAL_SEND_TEXT(chat_key, text, ctx, record=record, ref_msg_id=ref_msg_id)
        else:
            await ctx.ms.send_text(chat_key, text, ctx, record=record, ref_msg_id=ref_msg_id)
    finally:
        _AUTO_TTS_BYPASS.reset(token)


async def _send_text_fallback(ctx: AgentCtx, chat_key: str, text: str, ref_msg_id: str | None = None) -> None:
    if config.FALLBACK_TEXT_ON_FAILURE:
        await _send_text_direct(ctx, chat_key, text, ref_msg_id=ref_msg_id)


def _should_auto_convert_text(message: str) -> bool:
    stripped = (message or "").strip()
    if not stripped:
        return False
    if stripped.startswith(("/", "!", "！")):
        return False
    if "```" in stripped:
        return False
    if re.search(r"^\s*\|.+\|\s*$", stripped, re.MULTILINE):
        return False
    return True


async def _auto_tts_send_text(
    chat_key: str,
    message: str,
    ctx: AgentCtx,
    *,
    record: bool = True,
    ref_msg_id: str | None = None,
) -> None:
    if _ORIGINAL_SEND_TEXT is None:
        raise RuntimeError("MiMo TTS 自动转换钩子未初始化")

    if _AUTO_TTS_BYPASS.get() or not record or not config.ENABLE_TTS or not _should_auto_convert_text(message):
        await _ORIGINAL_SEND_TEXT(chat_key, message, ctx, record=record, ref_msg_id=ref_msg_id)
        return

    try:
        if await _is_channel_allowed(chat_key):
            await _synthesize_and_send(
                ctx,
                chat_key,
                message,
                ref_msg_id=ref_msg_id,
                record=record,
            )
            return
    except Exception as exc:
        plugin.logger.exception(f"自动发送语音/文字失败，准备按配置回退文本: {exc}")
        if not config.FALLBACK_TEXT_ON_FAILURE:
            raise

    await _send_text_direct(ctx, chat_key, message, ref_msg_id=ref_msg_id, record=record)
    _consume_text_mode(chat_key)


async def _synthesize_and_send(
    ctx: AgentCtx,
    chat_key: str,
    text: str,
    ref_msg_id: str | None = None,
    *,
    record: bool = True,
    allow_text_sync: bool = True,
    consume_mode: bool = True,
) -> str:
    if not await _is_channel_allowed(chat_key):
        raise PermissionError("当前频道未启用 MiMo 文本转语音")

    text_mode = _effective_text_mode(chat_key) if allow_text_sync else TEXT_MODE_VOICE
    cleaned_text = _validate_text(text)
    text_content = (text or "").strip() or cleaned_text
    if text_mode == TEXT_MODE_TEXT:
        await _send_text_direct(ctx, chat_key, text_content, ref_msg_id=ref_msg_id, record=record)
        if consume_mode:
            _consume_text_mode(chat_key)
        return "已按用户当前偏好改为文字发送"

    sync_text = text_mode == TEXT_MODE_SYNC
    chunks = _split_text(cleaned_text)
    if not chunks:
        raise ValueError("语音文本为空")

    sent_modes: list[str] = []
    durations: list[float] = []
    for index, chunk in enumerate(chunks, start=1):
        audio_path: Path | None = None
        try:
            audio_path = await asyncio.wait_for(
                _synthesize_to_file(chunk),
                timeout=float(config.SYNTHESIS_TIMEOUT) + 5.0,
            )
            duration = _wav_duration(audio_path)
            if duration and duration > float(config.MAX_VOICE_DURATION) + 0.8:
                raise ValueError(
                    f"第 {index} 段语音实际时长 {duration:.1f}s 超过配置上限 {config.MAX_VOICE_DURATION}s",
                )
            mode = await _send_audio_file(ctx, chat_key, audio_path, ref_msg_id if index == 1 else None)
            sent_modes.append(mode)
            durations.append(duration)
        finally:
            if audio_path:
                audio_path.unlink(missing_ok=True)

    if len(chunks) == max(1, int(config.MAX_SEGMENTS_PER_MESSAGE or 1)) and len("".join(chunks)) < len(cleaned_text):
        plugin.logger.warning("文本过长，已按 MAX_SEGMENTS_PER_MESSAGE 截断后合成")

    voice_count = sent_modes.count("voice")
    file_count = sent_modes.count("file")
    summary_parts: list[str] = []
    if voice_count:
        summary_parts.append(f"{voice_count} 条语音")
    if file_count:
        summary_parts.append(f"{file_count} 个音频文件")

    if sync_text:
        try:
            await _send_text_direct(
                ctx,
                chat_key,
                text_content,
                ref_msg_id=ref_msg_id,
                record=record,
            )
            summary_parts.append("1 条文字")
        except Exception as exc:
            plugin.logger.warning(f"同步文字发送失败: {exc}")

    summary = "、".join(summary_parts) or "0 条音频"

    if consume_mode:
        _consume_text_mode(chat_key)

    duration_text = ", ".join(f"{d:.1f}s" for d in durations if d)
    suffix = f"（{duration_text}）" if duration_text else ""
    if file_count:
        suffix += "（NapCat 语音转换不可用，已改为音频文件；修复方法见 README）"
    return f"已发送 MiMo 音频：{summary}{suffix}"


@plugin.mount_collect_methods()
async def collect_available_methods(ctx: AgentCtx):
    if await _is_channel_allowed(ctx.chat_key):
        return [send_mimo_tts_message, synthesize_mimo_tts_file]
    return []


@plugin.mount_prompt_inject_method("mimo_tts_prompt")
async def mimo_tts_prompt(ctx: AgentCtx) -> str:
    if not await _is_channel_allowed(ctx.chat_key):
        return ""

    style = "自定义人设音色" if config.CUSTOM_PERSONA.strip() else f"官方预设音色 {config.PRESET_VOICE or 'mimo_default'}"
    mode = "流式" if config.STREAM_OUTPUT else "非流式"
    return (
        "MiMo 文本转语音插件已在当前频道启用。"
        f"{_text_mode_prompt(ctx.chat_key)}"
        f"当前使用{style}，{mode}合成。"
        "语音文本应是适合朗读的自然语句，不要包含文件路径、调试信息、Markdown 表格或长代码块。"
        "用户可以用“打字/用文字/不要语音/不要文字”等说法临时改变发送方式，以用户最新说明为准。"
    )


@plugin.mount_sandbox_method(
    SandboxMethodType.TOOL,
    name="发送 MiMo TTS 语音消息",
    description=(
        "将指定文本通过小米 MiMo-V2.5-TTS 合成为语音并发送到群聊或私聊。"
        "当前为文字+语音同步模式时会同时发送文字。"
    ),
)
async def send_mimo_tts_message(
    ctx: AgentCtx,
    chat_key: str,
    text: str,
    ref_msg_id: str | None = None,
) -> str:
    """发送 MiMo TTS 语音消息。

    Args:
        chat_key (str): 目标聊天频道标识，通常使用当前聊天的 `_ck`。
        text (str): 要合成为语音并发送的自然语言文本。必须是适合朗读的最终回复；
            当前为文字+语音同步模式时，也会作为文字消息发送。
        ref_msg_id (str | None): 可选引用消息 ID，部分适配器可用。

    Returns:
        str: 发送结果。
    """
    return await _synthesize_and_send(ctx, chat_key, text, ref_msg_id)


@plugin.mount_sandbox_method(
    SandboxMethodType.TOOL,
    name="合成 MiMo TTS 音频文件",
    description="将文本合成为音频文件并返回沙盒路径，适合需要先生成文件再处理的场景。",
)
async def synthesize_mimo_tts_file(ctx: AgentCtx, text: str) -> str:
    """合成 MiMo TTS 音频文件。

    Args:
        text (str): 要合成为语音的自然语言文本。

    Returns:
        str: 生成音频文件的沙盒路径。
    """
    cleaned_text = _validate_text(text)
    first_chunk = _split_text(cleaned_text)[0]
    audio_path = await _synthesize_to_file(first_chunk)
    try:
        duration = _wav_duration(audio_path)
        if duration and duration > float(config.MAX_VOICE_DURATION) + 0.8:
            raise ValueError(f"语音实际时长 {duration:.1f}s 超过配置上限 {config.MAX_VOICE_DURATION}s")
        return await ctx.fs.mixed_forward_file(audio_path, file_name=audio_path.name)
    finally:
        audio_path.unlink(missing_ok=True)


@plugin.mount_command(
    name="mimo_tts",
    description="使用 MiMo TTS 合成并发送语音。",
    aliases=["tts", "语音合成", "mimo语音"],
    usage="/mimo_tts <要朗读的文本>",
    permission=CommandPermission.PUBLIC,
    category="语音",
    tags=["tts", "voice", "mimo"],
)
async def mimo_tts_cmd(
    context: CommandExecutionContext,
    text: Annotated[str, Arg("要合成为语音的文本", positional=True, greedy=True)] = "",
) -> CommandResponse:
    if not text.strip():
        return CmdCtl.failed("请输入要合成为语音的文本，例如：/mimo_tts 你好，我是 MiMo。")

    ctx = await AgentCtx.create_by_chat_key(context.chat_key)
    try:
        result = await _synthesize_and_send(
            ctx,
            context.chat_key,
            text,
            allow_text_sync=False,
            consume_mode=False,
        )
        return CmdCtl.success(result)
    except Exception as exc:
        plugin.logger.exception(f"MiMo TTS 命令执行失败: {exc}")
        if config.FALLBACK_TEXT_ON_FAILURE:
            try:
                await _send_text_fallback(ctx, context.chat_key, text)
            except Exception as fallback_exc:
                plugin.logger.warning(f"MiMo TTS 文本回退发送失败: {fallback_exc}")
        return CmdCtl.failed(f"MiMo TTS 失败：{exc}")


@plugin.mount_command(
    name="mimo_tts_text",
    description="设置 MiMo TTS 的文字/语音发送模式。",
    aliases=["mimo_tts_mode", "tts_text", "文字模式", "语音模式"],
    usage="/mimo_tts_text <on|off|text|auto>",
    permission=CommandPermission.PUBLIC,
    category="语音",
    tags=["tts", "voice", "text"],
)
async def mimo_tts_text_cmd(
    context: CommandExecutionContext,
    mode: Annotated[str, Arg("on=文字+语音，off=仅语音，text=仅文字，auto=跟随配置", positional=True)] = "",
) -> CommandResponse:
    if not await _is_channel_allowed(context.chat_key):
        return CmdCtl.failed("当前频道未启用 MiMo 文本转语音，请先开启总开关并配置频道")

    value = mode.strip().lower()
    if not value:
        return CmdCtl.success(
            f"当前文字发送模式：{_text_mode_status(context.chat_key)}\n"
            "用法：/mimo_tts_text on（文字+语音）| off（仅语音）| text（仅文字）| auto（跟随配置）",
        )

    if value in {"auto", "reset", "default", "默认", "自动"}:
        _clear_text_mode_override(context.chat_key)
        return CmdCtl.success(f"已恢复跟随配置：{_text_mode_status(context.chat_key)}")

    if value in {"on", "sync", "both", "同步", "文字+语音", "文字和语音"}:
        target = TEXT_MODE_SYNC
    elif value in {"off", "voice", "语音", "仅语音"}:
        target = TEXT_MODE_VOICE
    elif value in {"text", "only_text", "仅文字", "文字"}:
        target = TEXT_MODE_TEXT
    else:
        return CmdCtl.failed("未知模式，请使用 on（文字+语音）、off（仅语音）、text（仅文字）或 auto（跟随配置）")

    _set_text_mode_override(context.chat_key, target, -1)
    return CmdCtl.success(f"已切换为{TEXT_MODE_LABELS[target]}（持续生效，直到修改或重启插件）")


@plugin.mount_command(
    name="mimo_tts_status",
    description="查看 MiMo TTS 插件状态。",
    aliases=["mimo_tts_info"],
    permission=CommandPermission.PUBLIC,
    category="语音",
)
async def mimo_tts_status_cmd(context: CommandExecutionContext) -> CommandResponse:
    ctx = await AgentCtx.create_by_chat_key(context.chat_key)
    allowed = await _is_channel_allowed(context.chat_key)
    model = _normalize_model()
    estimated_limit = _estimate_chars_per_segment()
    db_chat_channel = await DBChatChannel.get_channel(chat_key=context.chat_key)
    lines = [
        "MiMo TTS 状态",
        f"总开关：{'开启' if config.ENABLE_TTS else '关闭'}",
        f"当前频道：{_display_channel_key(context.chat_key)}（{db_chat_channel.chat_type.value}）",
        f"当前频道可用：{'是' if allowed else '否'}",
        f"支持频道：{_format_supported_channels()}",
        f"调用模式：{'流式' if config.STREAM_OUTPUT else '非流式'}",
        f"模型：{model}",
        f"音色：{'自定义人设' if config.CUSTOM_PERSONA.strip() else _normalize_voice()}",
        f"单条最长：{config.MAX_VOICE_DURATION}s（约 {estimated_limit} 字/段）",
        f"语音发送：{_voice_status_text()}",
        f"文字发送：{_text_mode_status(context.chat_key)}",
        f"API Key：{'已填写' if config.MIMO_API_KEY.strip() else '未填写'}",
        f"适配器：{ctx.adapter_key}",
    ]
    if not _voice_attempt_allowed() and _VOICE_FAILURE_REASON:
        lines.append(f"最近错误：{_VOICE_FAILURE_REASON[:160]}")
    return CmdCtl.success("\n".join(lines))


@plugin.mount_on_user_message()
async def capture_text_mode_hint(ctx: AgentCtx, message: ChatMessage) -> None:
    """识别用户“打字/不要语音”等发送方式偏好，并临时/持续生效。"""
    del ctx
    if not await _is_channel_allowed(message.chat_key):
        return None

    state_key = _text_mode_state_key(message.chat_key)
    if state_key in _TEXT_MODE_OVERRIDES:
        # 新用户回合开始，允许本次回复消耗一次临时次数。
        _TEXT_MODE_PENDING.add(state_key)

    detected = _detect_text_mode_instruction(message.content_text)
    if not detected:
        return None

    mode, turns = detected
    if mode == TEXT_MODE_AUTO:
        _clear_text_mode_override(message.chat_key)
        _TEXT_MODE_PENDING.discard(state_key)
        plugin.logger.info(f"用户要求恢复默认文字发送模式: {message.chat_key}")
        return None

    _set_text_mode_override(message.chat_key, mode, turns)
    if turns > 0:
        _TEXT_MODE_PENDING.add(state_key)
    duration = "持续生效" if turns < 0 else f"{turns} 轮对话"
    plugin.logger.info(
        f"检测到用户文字发送偏好: {message.chat_key} -> {TEXT_MODE_LABELS[mode]}（{duration}）",
    )
    return None


@plugin.on_enabled()
async def install_auto_tts_hook() -> None:
    global _ORIGINAL_SEND_TEXT, _VOICE_FAILURE_UNTIL

    _VOICE_FAILURE_UNTIL = 0.0
    _TEXT_MODE_OVERRIDES.clear()
    _TEXT_MODE_PENDING.clear()

    from nekro_agent.api import message as message_api

    if getattr(message_api.send_text, "_mimo_tts_wrapped", False):
        return

    _ORIGINAL_SEND_TEXT = message_api.send_text
    _auto_tts_send_text._mimo_tts_wrapped = True  # type: ignore[attr-defined]
    message_api.send_text = _auto_tts_send_text
    plugin.logger.info("MiMo TTS 自动文本转语音钩子已安装")


@plugin.on_disabled()
async def uninstall_auto_tts_hook() -> None:
    global _ORIGINAL_SEND_TEXT

    from nekro_agent.api import message as message_api

    if _ORIGINAL_SEND_TEXT is not None and getattr(message_api.send_text, "_mimo_tts_wrapped", False):
        message_api.send_text = _ORIGINAL_SEND_TEXT
        _ORIGINAL_SEND_TEXT = None
        plugin.logger.info("MiMo TTS 自动文本转语音钩子已卸载")


@plugin.mount_cleanup_method()
async def cleanup():
    """清理插件资源。"""
    await uninstall_auto_tts_hook()
