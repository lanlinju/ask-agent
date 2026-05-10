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
        self.username = username

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
            (a for a in raw.attachments if a.get("content_type", "").startswith("voice")),
            None,
        )
        self.attachments: list[dict] = raw.attachments

    async def reply_text(self, text: str) -> None:
        raise NotImplementedError("Use Context.bot.send_text instead")

    async def reply_photo(self, photo: Any = None, caption: str = "") -> None:
        raise NotImplementedError("Use Context.bot.send_image instead")

    async def reply_voice(self, voice: bytes) -> None:
        raise NotImplementedError("Use Context.bot.send_voice instead")

    async def reply_document(self, document: Any = None, caption: str = "", filename: str = "") -> None:
        raise NotImplementedError("Use Context.bot.send_file instead")

    async def reply_markdown(self, markdown: str) -> None:
        raise NotImplementedError("Use Context.bot.send_markdown instead")


class Update:
    """更新对象，模仿 python-telegram-bot 的 Update"""

    def __init__(self, raw_message: QQMessage):
        self._raw = raw_message
        self.update_id: Optional[str] = raw_message.id
        self.message: Message = Message(raw_message)
        self.effective_user: User = User(raw_message.openid)

        # 群聊用 group_openid，私聊用 openid
        chat_id = raw_message.group_openid or raw_message.openid
        self.effective_chat: Chat = Chat(chat_id, raw_message.chat_type)

    def __repr__(self) -> str:
        return f"Update(update_id={self.update_id!r})"


class Context:
    """上下文对象，模仿 python-telegram-bot 的 ContextTypes.DEFAULT_TYPE"""

    def __init__(self, bot: QQBot, update: Update, application: Any = None):
        self.bot: QQBot = bot
        self.update: Update = update
        self.application: Any = application
        self.user_data: dict[str, Any] = {}
        self.chat_data: dict[str, Any] = {}

    async def send_text(self, text: str) -> None:
        """发送文本消息到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_text(chat.openid, text, chat.type)

    async def send_markdown(self, markdown: str) -> None:
        """发送 Markdown 消息到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_markdown(chat.openid, markdown, chat.type)

    async def send_image(self, file_path: str) -> None:
        """发送图片到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_image(chat.openid, file_path, chat.type)

    async def send_voice(self, voice: bytes) -> None:
        """发送语音到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_voice(chat.openid, voice, chat.type)

    async def send_file(self, file_path: str) -> None:
        """发送文件到当前聊天"""
        chat = self.update.effective_chat
        await self.bot.send_file(chat.openid, file_path, chat.type)


class SendProxy:
    """轻量级发送代理，接口与 Context 一致，用于主动消息等无 update 场景"""

    def __init__(self, bot: QQBot, openid: str, chat_type: str = "private"):
        self.bot: QQBot = bot
        self.openid = openid
        self.chat_type = chat_type

    async def send_text(self, text: str) -> None:
        await self.bot.send_text(self.openid, text, self.chat_type)

    async def send_markdown(self, markdown: str) -> None:
        await self.bot.send_markdown(self.openid, markdown, self.chat_type)

    async def send_voice(self, voice: bytes) -> None:
        await self.bot.send_voice(self.openid, voice, self.chat_type)

    async def send_file(self, file_path: str) -> None:
        await self.bot.send_file(self.openid, file_path, self.chat_type)
