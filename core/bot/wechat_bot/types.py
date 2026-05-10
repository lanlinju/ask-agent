from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Any, Dict, List, Literal, Optional, TypedDict, Union


class MessageType(IntEnum):
    """消息类型"""
    USER = 1
    BOT = 2


class MessageState(IntEnum):
    """消息状态"""
    NEW = 0
    GENERATING = 1
    FINISH = 2


class MessageItemType(IntEnum):
    """消息内容类型"""
    TEXT = 1
    IMAGE = 2
    VOICE = 3
    FILE = 4
    VIDEO = 5


class BaseInfo(TypedDict):
    """基础信息"""
    channel_version: str


class CDNMedia(TypedDict):
    """CDN媒体信息"""
    encrypt_query_param: str
    aes_key: str
    encrypt_type: Optional[int]


class TextItem(TypedDict):
    """文本内容"""
    text: str


class ImageItem(TypedDict):
    """图片内容"""
    media: CDNMedia
    aeskey: Optional[str]
    url: Optional[str]
    mid_size: Optional[Union[str, int]]
    thumb_size: Optional[Union[str, int]]
    thumb_height: Optional[int]
    thumb_width: Optional[int]
    hd_size: Optional[Union[str, int]]


class VoiceItem(TypedDict):
    """语音内容"""
    media: CDNMedia
    encode_type: Optional[int]
    text: Optional[str]
    playtime: Optional[int]


class FileItem(TypedDict):
    """文件内容"""
    media: CDNMedia
    file_name: Optional[str]
    md5: Optional[str]
    len: Optional[str]


class VideoItem(TypedDict):
    """视频内容"""
    media: CDNMedia
    video_size: Optional[Union[str, int]]
    play_length: Optional[int]
    thumb_media: Optional[CDNMedia]


class RefMessage(TypedDict):
    """引用消息"""
    title: Optional[str]
    message_item: Optional[MessageItem]


class MessageItem(TypedDict):
    """消息内容项"""
    type: MessageItemType
    text_item: Optional[TextItem]
    image_item: Optional[ImageItem]
    voice_item: Optional[VoiceItem]
    file_item: Optional[FileItem]
    video_item: Optional[VideoItem]
    ref_msg: Optional[RefMessage]


class WeixinMessage(TypedDict):
    """微信消息"""
    message_id: int
    from_user_id: str
    to_user_id: str
    client_id: str
    create_time_ms: int
    message_type: MessageType
    message_state: MessageState
    context_token: str
    item_list: List[MessageItem]


class GetUpdatesRequest(TypedDict):
    """获取更新请求"""
    get_updates_buf: str
    base_info: BaseInfo


class GetUpdatesResponse(TypedDict):
    """获取更新响应"""
    ret: int
    msgs: List[WeixinMessage]
    get_updates_buf: str
    longpolling_timeout_ms: Optional[int]
    errcode: Optional[int]
    errmsg: Optional[str]


class SendMessageMessage(TypedDict):
    """发送消息的消息体"""
    from_user_id: str
    to_user_id: str
    client_id: str
    message_type: MessageType
    message_state: MessageState
    context_token: str
    item_list: List[MessageItem]


class SendMessageRequest(TypedDict):
    """发送消息请求"""
    msg: SendMessageMessage
    base_info: BaseInfo


class SendTypingRequest(TypedDict):
    """发送正在输入状态请求"""
    ilink_user_id: str
    typing_ticket: str
    status: Literal[1, 2]
    base_info: BaseInfo


class GetConfigResponse(TypedDict):
    """获取配置响应"""
    typing_ticket: Optional[str]
    ret: Optional[int]
    errcode: Optional[int]
    errmsg: Optional[str]


class QrCodeResponse(TypedDict):
    """QR码响应"""
    qrcode: str
    qrcode_img_content: str


class QrStatusResponse(TypedDict):
    """QR码状态响应"""
    status: Literal["wait", "scaned", "confirmed", "expired"]
    bot_token: Optional[str]
    ilink_bot_id: Optional[str]
    ilink_user_id: Optional[str]
    baseurl: Optional[str]


MessageKind = Literal["text", "image", "voice", "file", "video"]


@dataclass
class WeChatMessage:
    """微信消息数据结构"""
    id: Optional[int] = None
    user_id: str = ""
    text: str = ""
    type: MessageKind = "text"
    context_token: str = ""
    timestamp: Optional[datetime] = None
    raw: Optional[WeixinMessage] = None


@dataclass
class User:
    """微信用户"""

    def __init__(self, user_id: str, username: str = ""):
        self.id = user_id
        self.user_id = user_id
        self.username = username

    def __repr__(self) -> str:
        return f"User(user_id={self.user_id!r})"


@dataclass
class Chat:
    """微信会话"""

    def __init__(self, user_id: str, chat_type: str = "private"):
        self.id = user_id
        self.user_id = user_id
        self.type = chat_type

    def __repr__(self) -> str:
        return f"Chat(user_id={self.user_id!r}, type={self.type!r})"


class Message:
    """消息对象，对WeChatMessage的包装"""

    def __init__(self, raw: WeChatMessage):
        self._raw = raw
        self.text: str = raw.text
        self.caption: str = raw.text
        self.photo: List[Dict] = []
        self.voice: Optional[Dict] = None
        self.attachments: List[Dict] = []

    async def reply_text(self, text: str) -> None:
        raise NotImplementedError("Use Context.bot.send_text instead")

    async def reply_photo(self, photo: Any = None, caption: str = "") -> None:
        raise NotImplementedError("Use Context.bot.send_image instead")

    async def reply_voice(self, voice: bytes) -> None:
        raise NotImplementedError("Use Context.bot.send_voice instead")

    async def reply_document(self, document: Any = None, caption: str = "", filename: str = "") -> None:
        raise NotImplementedError("Use Context.bot.send_file instead")


class Update:
    """更新对象，模仿python-telegram-bot的Update"""

    def __init__(self, raw_message: WeChatMessage):
        self._raw = raw_message
        self.update_id: Optional[int] = raw_message.id
        self.message: Message = Message(raw_message)
        self.effective_user: User = User(raw_message.user_id)
        self.effective_chat: Chat = Chat(raw_message.user_id, "private")

    def __repr__(self) -> str:
        return f"Update(update_id={self.update_id!r})"


class Context:
    """上下文对象"""

    def __init__(self, bot: Any, update: Update, application: Any = None):
        self.bot: Any = bot
        self.update: Update = update
        self.application: Any = application
        self.user_data: Dict[str, Any] = {}
        self.chat_data: Dict[str, Any] = {}

    async def send_text(self, text: str) -> None:
        """发送文本消息到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_text(chat.user_id, text)

    async def send_image(self, file_path: str) -> None:
        """发送图片到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_image(chat.user_id, file_path)

    async def send_voice(self, voice: bytes) -> None:
        """发送语音到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_voice(chat.user_id, voice)

    async def send_file(self, file_path: str) -> None:
        """发送文件到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_file(chat.user_id, file_path)


__all__ = [
    "MessageType",
    "MessageState",
    "MessageItemType",
    "BaseInfo",
    "CDNMedia",
    "TextItem",
    "ImageItem",
    "VoiceItem",
    "FileItem",
    "VideoItem",
    "RefMessage",
    "MessageItem",
    "WeixinMessage",
    "GetUpdatesRequest",
    "GetUpdatesResponse",
    "SendMessageMessage",
    "SendMessageRequest",
    "SendTypingRequest",
    "GetConfigResponse",
    "QrCodeResponse",
    "QrStatusResponse",
    "MessageKind",
    "WeChatMessage",
    "User",
    "Chat",
    "Message",
    "Update",
    "Context",
]