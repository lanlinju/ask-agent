from __future__ import annotations

import logging
from typing import Optional, Callable, Awaitable, Any

from .auth import WeChatAuth, Credentials, ILINK_API_BASE
from .gateway import WeChatGateway
from .messages import WeChatMessages
from .types import WeChatMessage

logger = logging.getLogger(__name__)


class WeChatBot:
    """微信Bot主控制器"""

    def __init__(self, base_url: str = ILINK_API_BASE):
        self.auth = WeChatAuth(base_url)
        self.messages = WeChatMessages(self.auth, base_url)
        self.gateway = WeChatGateway(self.auth, base_url)
        self.on_message_callback: Optional[Callable[[WeChatMessage], Awaitable[None]]] = None

    async def login(self, force: bool = False) -> Credentials:
        """登录微信Bot

        Args:
            force: 强制重新登录

        Returns:
            Credentials对象
        """
        return await self.auth.login(force=force)

    async def start(self, on_message: Callable[[WeChatMessage], Awaitable[None]]) -> None:
        """启动Bot

        Args:
            on_message: 消息处理回调函数
        """
        self.on_message_callback = on_message
        logger.info("启动微信Bot...")
        await self.gateway.start(self._handle_message)

    async def stop(self) -> None:
        """停止Bot"""
        logger.info("停止微信Bot...")
        await self.gateway.stop()

    async def _handle_message(self, message: WeChatMessage) -> None:
        """处理消息"""
        if self.on_message_callback:
            await self.on_message_callback(message)

    async def send_text(self, user_id: str, text: str, context_token: str) -> bool:
        """发送文本消息

        Args:
            user_id: 用户ID
            text: 消息文本
            context_token: 上下文令牌

        Returns:
            是否发送成功
        """
        return await self.messages.send_text(user_id, text, context_token)

    async def send_typing(self, user_id: str, context_token: str, status: int = 1) -> bool:
        """发送正在输入状态

        Args:
            user_id: 用户ID
            context_token: 上下文令牌
            status: 状态 (1=开始输入, 2=取消输入)

        Returns:
            是否发送成功
        """
        return await self.messages.send_typing(user_id, context_token, status)

    async def reply(self, message: WeChatMessage, text: str) -> bool:
        """回复消息

        Args:
            message: 原始消息
            text: 回复文本

        Returns:
            是否发送成功
        """
        return await self.send_text(message.user_id, text, message.context_token)


_wechat_bot_instance: Optional[WeChatBot] = None


async def start_wechat_bot(
    base_url: str = ILINK_API_BASE,
    on_message: Optional[Callable[[WeChatMessage], Awaitable[None]]] = None,
) -> WeChatBot:
    """启动微信Bot

    Args:
        base_url: API基础URL
        on_message: 消息处理回调函数

    Returns:
        WeChatBot实例
    """
    global _wechat_bot_instance
    _wechat_bot_instance = WeChatBot(base_url)

    if on_message:
        await _wechat_bot_instance.start(on_message)

    return _wechat_bot_instance


async def stop_wechat_bot() -> None:
    """停止微信Bot"""
    global _wechat_bot_instance
    if _wechat_bot_instance:
        await _wechat_bot_instance.stop()
        _wechat_bot_instance = None


def get_wechat_bot() -> Optional[WeChatBot]:
    """获取微信Bot实例"""
    return _wechat_bot_instance


__all__ = [
    "WeChatBot",
    "start_wechat_bot",
    "stop_wechat_bot",
    "get_wechat_bot",
]