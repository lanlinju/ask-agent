from typing import Optional, Callable, Awaitable

from .auth import QQAuth
from .gateway import QQGateway, QQMessage, QQ_API_BASE
from .messages import QQMessages


class QQBot:
    """QQ Bot 主控制器"""

    def __init__(self, app_id: str, app_secret: str,
                 api_base: str = QQ_API_BASE,
                 reply_chunk_size: int = 1500,
                 enable_markdown: bool = True):
        self.auth = QQAuth(app_id, app_secret)
        self.messages = QQMessages(self.auth, api_base, reply_chunk_size, enable_markdown)
        self.gateway = QQGateway(self.auth, api_base)
        self.on_message_callback: Optional[Callable[[QQMessage], Awaitable[None]]] = None

    async def start(self, on_message: Callable[[QQMessage], Awaitable[None]]) -> None:
        """启动Bot"""
        self.on_message_callback = on_message
        await self.gateway.start(self._handle_message)

    async def stop(self) -> None:
        """停止Bot"""
        await self.gateway.stop()

    async def _handle_message(self, message: QQMessage) -> None:
        """处理消息"""
        if self.on_message_callback:
            await self.on_message_callback(message)

    async def send_text(self, openid: str, text: str, chat_type: str = "private",
                        msg_id: Optional[str] = None) -> bool:
        """发送文本消息"""
        return await self.messages.send_text(openid, text, chat_type, msg_id)

    async def send_markdown(self, openid: str, markdown: str, chat_type: str = "private",
                           msg_id: Optional[str] = None) -> bool:
        """发送Markdown消息"""
        return await self.messages.send_markdown(openid, markdown, chat_type, msg_id)

    async def send_image(self, openid: str, file_path: str, chat_type: str = "private",
                        msg_id: Optional[str] = None) -> bool:
        """发送图片消息"""
        return await self.messages.send_image(openid, file_path, chat_type, msg_id)

    async def send_voice(self, openid: str, voice: bytes, chat_type: str = "private",
                         msg_id: Optional[str] = None) -> bool:
        """发送语音消息"""
        return await self.messages.send_voice(openid, voice, chat_type, msg_id)


_qq_bot_instance: Optional[QQBot] = None


async def start_qq_bot(app_id: str, app_secret: str,
                       on_message: Callable[[QQMessage], Awaitable[None]],
                       api_base: str = QQ_API_BASE,
                       reply_chunk_size: int = 1500,
                       enable_markdown: bool = True) -> QQBot:
    """启动QQ Bot"""
    global _qq_bot_instance
    _qq_bot_instance = QQBot(app_id, app_secret, api_base, reply_chunk_size, enable_markdown)
    await _qq_bot_instance.start(on_message)
    return _qq_bot_instance


async def stop_qq_bot() -> None:
    """停止QQ Bot"""
    global _qq_bot_instance
    if _qq_bot_instance:
        await _qq_bot_instance.stop()
        _qq_bot_instance = None


def get_qq_bot() -> Optional[QQBot]:
    """获取QQ Bot实例"""
    return _qq_bot_instance
