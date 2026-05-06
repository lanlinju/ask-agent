from __future__ import annotations
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .bot import QQBot
    from .gateway import QQMessage


class User:
    """QQ 用户"""

    def __init__(self, openid: str, username: str = ""):
        self.id = openid
        self.openid = openid
        self.username = username or openid

    def __repr__(self) -> str:
        return f"User(openid={self.openid!r})"


class Chat:
    """QQ 会话"""

    def __init__(self, openid: str, chat_type: str = "private"):
        self.id = openid
        self.openid = openid
        self.type = chat_type

    def __repr__(self) -> str:
        return f"Chat(openid={self.openid!r}, type={self.type!r})"


class Message:
    """消息对象，对 QQMessage 的包装"""

    def __init__(self, raw: QQMessage):
        self._raw = raw
        self.text: str = raw.content
        self.caption: str = raw.content
        self.photo: list[dict] = [
            a for a in raw.attachments if a.get("content_type", "").startswith("image/")
        ]
        self.voice: Optional[dict] = next(
            (a for a in raw.attachments if a.get("content_type", "").startswith("audio/")),
            None,
        )
        self.attachments: list[dict] = raw.attachments

    async def reply_text(self, text: str) -> None:
        raise NotImplementedError("Use Context.bot.send_text instead")

    async def reply_photo(self, photo: Any = None, caption: str = "") -> None:
        raise NotImplementedError("Use Context.bot.send_image instead")

    async def reply_voice(self, voice: bytes) -> None:
        raise NotImplementedError("Use Context.bot.send_voice instead")

    async def reply_markdown(self, markdown: str) -> None:
        raise NotImplementedError("Use Context.bot.send_markdown instead")


class Update:
    """更新对象，模仿 python-telegram-bot 的 Update"""

    def __init__(self, raw_message: QQMessage):
        self._raw = raw_message
        self.update_id: Optional[str] = raw_message.id
        self.message: Message = Message(raw_message)
        self.effective_user: User = User(raw_message.openid)
        self.effective_chat: Chat = Chat(raw_message.openid)

    def __repr__(self) -> str:
        return f"Update(update_id={self.update_id!r})"


class Context:
    """上下文对象，模仿 python-telegram-bot 的 ContextTypes.DEFAULT_TYPE"""

    def __init__(self, bot: QQBot, update: Update):
        self.bot: QQBot = bot
        self.update: Update = update
        self.user_data: dict[str, Any] = {}
        self.chat_data: dict[str, Any] = {}

    async def send_text(self, text: str) -> None:
        """发送文本消息到当前用户"""
        await self.bot.send_text(self.update.effective_chat.openid, text)

    async def send_markdown(self, markdown: str) -> None:
        """发送 Markdown 消息到当前用户"""
        await self.bot.send_markdown(self.update.effective_chat.openid, markdown)

    async def send_image(self, file_path: str) -> None:
        """发送图片到当前用户"""
        await self.bot.send_image(self.update.effective_chat.openid, file_path)

    async def send_voice(self, voice: bytes) -> None:
        """发送语音到当前用户"""
        await self.bot.send_voice(self.update.effective_chat.openid, voice)
