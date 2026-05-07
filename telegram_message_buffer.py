"""
Telegram 消息缓冲区管理
支持连续消息批量回复，让 bot 更像真人聊天
"""

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Dict, List, Optional

logger = logging.getLogger(__name__)


class MessageBuffer:
    """消息缓冲区，支持延迟回复"""

    def __init__(self, timeout: float = 3.0):
        """初始化消息缓冲区

        Args:
            timeout: 超时时间（秒），收到消息后等待多久再处理
        """
        self.timeout = timeout
        # 用户消息队列: {user_key: [{"text": str, "timestamp": float, "update": Any, "context": Any}]}
        self._buffers: Dict[str, List[Dict[str, Any]]] = {}
        # 用户计时器: {user_key: asyncio.Task}
        self._timers: Dict[str, asyncio.Task] = {}
        # 处理回调函数
        self._callback: Optional[Callable[..., Coroutine]] = None

    def set_callback(self, callback: Callable[..., Coroutine]) -> None:
        """设置消息处理回调函数

        Args:
            callback: 异步回调函数，接收 (user_key, messages, update, context) 参数
        """
        self._callback = callback

    def _get_user_key(self, chat_id: str, user_id: str) -> str:
        """生成用户唯一键

        Args:
            chat_id: 聊天 ID
            user_id: 用户 ID

        Returns:
            用户唯一键
        """
        return f"{chat_id}:{user_id}"

    async def add_message(
        self,
        chat_id: str,
        user_id: str,
        text: str,
        update: Any,
        context: Any,
    ) -> None:
        """添加消息到缓冲区

        Args:
            chat_id: 聊天 ID
            user_id: 用户 ID
            text: 消息文本
            update: Telegram Update 对象
            context: Telegram Context 对象
        """
        user_key = self._get_user_key(chat_id, user_id)

        # 初始化缓冲区
        if user_key not in self._buffers:
            self._buffers[user_key] = []

        # 添加消息
        self._buffers[user_key].append({
            "text": text,
            "timestamp": time.time(),
            "update": update,
            "context": context,
        })

        logger.debug(f"消息已添加到缓冲区: {user_key}, 当前 {len(self._buffers[user_key])} 条消息")

        # 取消之前的计时器
        if user_key in self._timers:
            self._timers[user_key].cancel()
            logger.debug(f"已取消旧计时器: {user_key}")

        # 启动新的计时器
        self._timers[user_key] = asyncio.create_task(
            self._wait_and_process(user_key)
        )
        logger.debug(f"已启动新计时器: {user_key}, 超时: {self.timeout}秒")

    async def _wait_and_process(self, user_key: str) -> None:
        """等待超时后处理消息

        Args:
            user_key: 用户唯一键
        """
        try:
            await asyncio.sleep(self.timeout)

            # 获取所有累积的消息
            messages = self._buffers.pop(user_key, [])
            self._timers.pop(user_key, None)

            if not messages:
                return

            logger.info(f"缓冲区超时，处理 {len(messages)} 条消息: {user_key}")

            # 调用回调函数处理消息
            if self._callback:
                # 使用最后一条消息的 update 和 context
                last_msg = messages[-1]
                await self._callback(
                    user_key,
                    messages,
                    last_msg["update"],
                    last_msg["context"],
                )

        except asyncio.CancelledError:
            # 计时器被取消（收到新消息）
            logger.debug(f"计时器已取消: {user_key}")
        except Exception as e:
            logger.error(f"处理缓冲消息失败: {e}")
            # 清理
            self._buffers.pop(user_key, None)
            self._timers.pop(user_key, None)

    def get_buffered_messages(self, chat_id: str, user_id: str) -> List[Dict[str, Any]]:
        """获取缓冲区中的消息（不触发处理）

        Args:
            chat_id: 聊天 ID
            user_id: 用户 ID

        Returns:
            消息列表
        """
        user_key = self._get_user_key(chat_id, user_id)
        return self._buffers.get(user_key, [])

    def clear_buffer(self, chat_id: str, user_id: str) -> None:
        """清空缓冲区

        Args:
            chat_id: 聊天 ID
            user_id: 用户 ID
        """
        user_key = self._get_user_key(chat_id, user_id)
        self._buffers.pop(user_key, None)
        if user_key in self._timers:
            self._timers[user_key].cancel()
            self._timers.pop(user_key, None)
        logger.debug(f"已清空缓冲区: {user_key}")

    def has_pending_messages(self, chat_id: str, user_id: str) -> bool:
        """检查是否有待处理的消息

        Args:
            chat_id: 聊天 ID
            user_id: 用户 ID

        Returns:
            是否有待处理的消息
        """
        user_key = self._get_user_key(chat_id, user_id)
        return user_key in self._buffers and len(self._buffers[user_key]) > 0

    @property
    def pending_count(self) -> int:
        """获取所有待处理消息的总数"""
        return sum(len(msgs) for msgs in self._buffers.values())


class TelegramMessageBufferManager:
    """Telegram 消息缓冲区管理器"""

    def __init__(self):
        self._buffers: Dict[str, MessageBuffer] = {}

    def get_or_create_buffer(
        self,
        chat_id: str,
        timeout: float = 3.0,
    ) -> MessageBuffer:
        """获取或创建缓冲区

        Args:
            chat_id: 聊天 ID
            timeout: 超时时间

        Returns:
            消息缓冲区实例
        """
        if chat_id not in self._buffers:
            self._buffers[chat_id] = MessageBuffer(timeout=timeout)
        return self._buffers[chat_id]

    def remove_buffer(self, chat_id: str) -> None:
        """移除缓冲区

        Args:
            chat_id: 聊天 ID
        """
        self._buffers.pop(chat_id, None)

    @property
    def total_pending(self) -> int:
        """获取所有缓冲区的待处理消息总数"""
        return sum(buf.pending_count for buf in self._buffers.values())


# 全局缓冲区管理器实例
buffer_manager = TelegramMessageBufferManager()
