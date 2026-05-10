from __future__ import annotations
import logging
import asyncio
from typing import Optional

from .auth import WeChatAuth, ILINK_API_BASE
from .gateway import WeChatGateway
from .messages import WeChatMessages
from .bot import WeChatBot
from .types import WeChatMessage, Update, Context
from .handlers import BaseHandler

logger = logging.getLogger(__name__)


class Application:
    """微信Bot应用，模仿 python-telegram-bot 的 Application

    用法:
        app = Application.builder().build()
        app.add_handler(MessageHandler(filters.text, handle_text))
        app.add_handler(CommandHandler("start", handle_start))
        app.run_polling()
    """

    def __init__(self, bot: WeChatBot):
        self.bot: WeChatBot = bot
        self.handlers: list[BaseHandler] = []
        self._running = False
        self._stop_event: Optional[asyncio.Event] = None

    class Builder:
        """Application 构建器"""

        def __init__(self):
            self._base_url: str = ILINK_API_BASE

        def base_url(self, url: str) -> "Application.Builder":
            """设置API基础URL"""
            self._base_url = url
            return self

        def build(self) -> "Application":
            """构建Application"""
            bot = WeChatBot(self._base_url)
            return Application(bot)

    @staticmethod
    def builder() -> "Application.Builder":
        """获取构建器"""
        return Application.Builder()

    def add_handler(self, handler: BaseHandler) -> None:
        """注册处理器"""
        self.handlers.append(handler)

    async def _dispatch(self, message: WeChatMessage) -> None:
        """分发消息到匹配的处理器"""
        update = Update(message)
        context = Context(self.bot, update, application=self)

        for handler in self.handlers:
            if handler.check_update(update):
                try:
                    await handler.handle_update(update, context)
                except Exception as e:
                    logger.error(f"Handler error: {e}")
                return

        logger.debug(f"No handler matched for message: {message.text[:50]}")

    async def start(self) -> None:
        """启动Bot（非阻塞）"""
        await self.bot.login()
        await self.bot.start(self._dispatch)
        self._running = True

    async def stop(self) -> None:
        """停止Bot"""
        self._running = False
        await self.bot.stop()
        if self._stop_event:
            self._stop_event.set()

    def run_polling(self, scheduler=None) -> None:
        """阻塞式运行，Ctrl+C 停止

        Args:
            scheduler: 可选的 ProactiveScheduler，将与 Bot 一起启动
        """
        stop_event = asyncio.Event()
        self._stop_event = stop_event

        async def _run():
            await self.start()
            if scheduler:
                await scheduler.start()
            print("微信Bot已启动！按 Ctrl+C 停止")
            try:
                await stop_event.wait()
            finally:
                if scheduler:
                    await scheduler.stop()

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            pass


__all__ = ["Application"]