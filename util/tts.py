"""
TTS (Text-to-Speech) 工具模块
支持 MiMo-V2.5-TTS 系列模型进行语音合成
"""

import base64
import io
import logging
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 支持的音频格式
SUPPORTED_FORMATS = ["wav", "mp3", "pcm", "pcm16"]

# 临时音频文件目录
TEMP_AUDIO_DIR = Path(tempfile.gettempdir()) / "ask-agent-audio"


def get_tts_api_config(provider_config=None, model: str = None) -> Optional[dict]:
    """获取 TTS API 配置

    Args:
        provider_config: ProviderConfig 实例，用于从 providers.json 读取配置
        model: TTS 模型名称，用于查找对应的 provider

    Returns:
        API 配置字典，未找到返回 None
    """
    if provider_config and model:
        # 根据 model 查找对应的 provider
        provider = provider_config.get_provider_for_model(model)
        if provider and provider.enabled:
            return {
                "base_url": provider.options.base_url,
                "api_key": provider.options.resolve_api_key(),
            }

    # 回退到环境变量
    api_key = os.getenv("TTS_API_KEY", "")
    if api_key:
        return {
            "base_url": os.getenv("TTS_API_URL", ""),
            "api_key": api_key,
            "model": os.getenv("TTS_API_MODEL", ""),
        }

    return None


def load_audio_sample(sample_path: str) -> Optional[str]:
    """加载音频样本并转换为 Base64

    Args:
        sample_path: 音频文件路径

    Returns:
        Base64 编码的音频数据，失败返回 None
    """
    try:
        path = Path(sample_path).expanduser().resolve()
        if not path.exists():
            logger.error(f"音频样本不存在: {sample_path}")
            return None

        with open(path, "rb") as f:
            audio_bytes = f.read()

        # 检查文件大小 (最大 10MB)
        if len(audio_bytes) > 10 * 1024 * 1024:
            logger.error("音频样本过大 (最大 10MB)")
            return None

        return base64.b64encode(audio_bytes).decode("utf-8")
    except Exception as e:
        logger.error(f"加载音频样本失败: {e}")
        return None


def get_audio_mime_type(file_path: str) -> str:
    """获取音频文件的 MIME 类型"""
    ext = Path(file_path).suffix.lower()
    mime_map = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".m4a": "audio/mp4",
    }
    return mime_map.get(ext, "audio/mpeg")


def text_to_speech(
    text: str,
    voice_config: dict,
    api_config: Optional[dict] = None,
) -> Optional[bytes]:
    """将文本转换为语音

    Args:
        text: 要转换的文本
        voice_config: 音色配置
            - type: 音色类型 "preset"（预置）、"design"（设计）、"clone"（克隆）
            - voice_id: 预置音色 ID（preset 模式）
            - sample: 音频样本路径（clone 模式）
            - style: 音色描述（design 模式）或风格指令（可选）
            - model: TTS 模型名称（可选，优先于 api_config）
        api_config: API 配置（可选）
            - base_url: API 地址
            - api_key: API 密钥
            - model: 默认 TTS 模型名称

    Returns:
        音频字节数据（MP3 格式），失败返回 None
    """
    if api_config is None:
        api_config = get_tts_api_config()

    if not api_config.get("api_key"):
        logger.error("TTS API Key 未配置")
        return None

    if not api_config.get("base_url"):
        logger.error("TTS API URL 未配置")
        return None

    try:
        # 获取模型
        model = voice_config.get("model") or api_config.get("model")
        if not model:
            logger.error("TTS 模型未配置")
            return None

        # 构建 voice 参数
        voice_type = voice_config.get("type", "preset")

        if voice_type == "clone":
            # 音色克隆模式
            sample_path = voice_config.get("sample")
            if not sample_path:
                logger.error("音色克隆模式需要 sample 路径")
                return None

            sample_base64 = load_audio_sample(sample_path)
            if not sample_base64:
                return None

            mime_type = get_audio_mime_type(sample_path)
            voice = f"data:{mime_type};base64,{sample_base64}"
        elif voice_type == "design":
            # 音色设计模式 - voice 参数为空，通过 style 指定音色描述
            voice = None
        else:
            # 预置音色模式
            voice = voice_config.get("voice_id", "mimo_default")

        # 构建消息
        messages = []

        # 添加风格指令 (如果有)
        style = voice_config.get("style")
        if style:
            messages.append({"role": "user", "content": style})

        # 添加要合成的文本
        messages.append({"role": "assistant", "content": text})

        # 调用 API
        headers = {
            "api-key": api_config["api_key"],
            "Content-Type": "application/json",
        }

        audio_config = {"format": "mp3"}
        if voice:
            audio_config["voice"] = voice

        payload = {
            "model": model,
            "messages": messages,
            "audio": audio_config,
        }

        response = requests.post(
            f"{api_config['base_url']}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )

        if response.status_code != 200:
            logger.error(f"TTS API 错误: {response.status_code} {response.text}")
            return None

        result = response.json()
        audio_data = result.get("choices", [{}])[0].get("message", {}).get("audio", {}).get("data")

        if not audio_data:
            logger.error("TTS API 未返回音频数据")
            return None

        mp3_bytes = base64.b64decode(audio_data)

        # 转换为 ogg 格式 (Telegram 语音消息需要)
        ogg_bytes = convert_audio_format(mp3_bytes, "mp3", "ogg")
        if ogg_bytes:
            return ogg_bytes

        # 如果转换失败，返回原始 mp3
        return mp3_bytes

    except Exception as e:
        logger.error(f"语音合成失败: {e}")
        return None


def convert_audio_format(
    audio_bytes: bytes,
    input_format: str = "mp3",
    output_format: str = "ogg",
) -> Optional[bytes]:
    """转换音频格式

    Args:
        audio_bytes: 输入音频字节
        input_format: 输入格式
        output_format: 输出格式

    Returns:
        转换后的音频字节，失败返回 None
    """
    try:
        # 使用 ffmpeg 转换
        with tempfile.NamedTemporaryFile(suffix=f".{input_format}", delete=False) as input_file:
            input_file.write(audio_bytes)
            input_path = input_file.name

        output_path = input_path.replace(f".{input_format}", f".{output_format}")

        # ffmpeg 转换命令
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-c:a", "libopus" if output_format == "ogg" else "copy",
            "-b:a", "64k",
            output_path,
        ]

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

        if result.returncode == 0 and os.path.exists(output_path):
            with open(output_path, "rb") as f:
                output_bytes = f.read()

            # 清理临时文件
            os.unlink(input_path)
            os.unlink(output_path)

            return output_bytes
        else:
            logger.warning(f"音频格式转换失败: {result.stderr.decode()}")
            os.unlink(input_path)
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None

    except FileNotFoundError:
        logger.warning("ffmpeg 未安装，跳过格式转换")
        return None
    except Exception as e:
        logger.error(f"音频格式转换错误: {e}")
        return None


def text_to_speech_stream(
    text: str,
    voice_config: dict,
    api_config: Optional[dict] = None,
) -> Optional[bytes]:
    """流式将文本转换为语音 (当前降级为非流式)

    Args:
        text: 要转换的文本
        voice_config: 音色配置
        api_config: API 配置

    Returns:
        音频字节数据，失败返回 None
    """
    # MiMo TTS 流式接口当前降级为兼容模式
    # 直接使用非流式接口
    return text_to_speech(text, voice_config, api_config)


def save_audio_to_file(audio_bytes: bytes, filename: str = "tts_output.mp3") -> Optional[Path]:
    """保存音频到临时文件

    Args:
        audio_bytes: 音频字节数据
        filename: 文件名

    Returns:
        文件路径，失败返回 None
    """
    try:
        TEMP_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
        file_path = TEMP_AUDIO_DIR / filename

        with open(file_path, "wb") as f:
            f.write(audio_bytes)

        return file_path
    except Exception as e:
        logger.error(f"保存音频文件失败: {e}")
        return None


def play_audio(audio_bytes: bytes, format: str = "mp3") -> bool:
    """播放音频（终端模式）

    Args:
        audio_bytes: 音频字节数据
        format: 音频格式

    Returns:
        是否播放成功
    """
    file_path = None
    try:
        # 保存到临时文件
        file_path = save_audio_to_file(audio_bytes, f"tts_output.{format}")
        if not file_path:
            return False

        # 根据系统选择播放器
        system = platform.system()

        if system == "Windows":
            return _play_audio_windows(str(file_path))
        elif system == "Darwin":
            return _play_audio_macos(str(file_path))
        else:
            return _play_audio_linux(str(file_path))

    except Exception as e:
        logger.error(f"播放音频失败: {e}")
        return False
    finally:
        # 清理临时文件
        if file_path and file_path.exists():
            try:
                file_path.unlink()
            except Exception:
                pass

# 参考：https://github.com/rany2/edge-tts/blob/master/src/edge_playback/win32_playback.py
def _play_audio_windows(file_path: str) -> bool:
    """Windows 下使用 MCI API 播放音频

    Args:
        file_path: 音频文件路径

    Returns:
        是否播放成功
    """
    try:
        from ctypes import create_unicode_buffer, windll, wintypes

        _get_short_path_name_w = windll.kernel32.GetShortPathNameW
        _get_short_path_name_w.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        _get_short_path_name_w.restype = wintypes.DWORD

        def get_short_path_name(long_name: str) -> str:
            """获取 DOS 短路径名"""
            output_buf_size = 0
            while True:
                output_buf = create_unicode_buffer(output_buf_size)
                needed = _get_short_path_name_w(long_name, output_buf, output_buf_size)
                if output_buf_size >= needed:
                    return output_buf.value
                output_buf_size = needed

        mci_send_string_w = windll.winmm.mciSendStringW

        def mci_send(msg: str) -> None:
            """发送 MCI 命令"""
            result = mci_send_string_w(msg, 0, 0, 0)
            if result != 0:
                logger.error(f"MCI 错误 {result}: {msg}")
                raise Exception(f"MCI error {result}")

        mp3_shortname = get_short_path_name(file_path)

        mci_send("Close All")
        mci_send(f'Open "{mp3_shortname}" Type MPEGVideo Alias theMP3')
        mci_send("Play theMP3 Wait")
        mci_send("Close theMP3")

        return True

    except Exception as e:
        logger.error(f"Windows 音频播放失败: {e}")
        return False


def _play_audio_macos(file_path: str) -> bool:
    """macOS 下播放音频

    Args:
        file_path: 音频文件路径

    Returns:
        是否播放成功
    """
    try:
        subprocess.run(["afplay", file_path], check=True)
        return True
    except Exception as e:
        logger.error(f"macOS 音频播放失败: {e}")
        return False


def _play_audio_linux(file_path: str) -> bool:
    """Linux 下播放音频

    Args:
        file_path: 音频文件路径

    Returns:
        是否播放成功
    """
    players = ["mpv", "ffplay", "aplay", "paplay"]
    for player in players:
        try:
            if player == "ffplay":
                subprocess.run(
                    [player, "-nodisp", "-autoexit", file_path],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.run([player, file_path], check=True)
            return True
        except FileNotFoundError:
            continue

    logger.warning("未找到可用的音频播放器")
    return False


def text_to_speech_and_play(
    text: str,
    voice_config: dict,
    api_config: Optional[dict] = None,
) -> bool:
    """将文本转换为语音并播放（终端模式）

    Args:
        text: 要转换的文本
        voice_config: 音色配置
        api_config: API 配置

    Returns:
        是否成功播放
    """
    audio_bytes = text_to_speech(text, voice_config, api_config)
    if not audio_bytes:
        return False

    return play_audio(audio_bytes)
