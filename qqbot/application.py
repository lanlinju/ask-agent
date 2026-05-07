from __future__ import annotations
import logging
import asyncio
from typing import Optional

from .auth import QQAuth
from .gateway import QQGateway, QQMessage
from .messages import QQMessages
from .bot import QQBot, QQ_API_BASE
from .types import Update, Context
from .handlers import BaseHandler

logger = logging.getLogger(__name__)


class Application:
    """QQ Bot 应用，模仿 python-telegram-bot 的 Application

    用法:
        app = Application.builder().token(app_id, app_secret).build()
        app.add_handler(MessageHandler(filters.text, handle_text))
        app.add_handler(CommandHandler("start", handle_start))
        app.run_polling()
    """

    def __init__(self, bot: QQBot):
        self.bot: QQBot = bot
        self.handlers: list[BaseHandler] = []
        self._running = False
        self._stop_event: Optional[asyncio.Event] = None

    class Builder:
        """Application 构建器"""

        def __init__(self):
            self._app_id: str = ""
            self._app_secret: str = ""
            self._api_base: str = QQ_API_BASE
            self._reply_chunk_size: int = 1500
            self._enable_markdown: bool = True

        def token(self, app_id: str, app_secret: str) -> "Application.Builder":
            """设置 QQ Bot 凭证"""
            self._app_id = app_id
            self._app_secret = app_secret
            return self

        def api_base(self, url: str) -> "Application.Builder":
            self._api_base = url
            return self

        def reply_chunk_size(self, size: int) -> "Application.Builder":
            self._reply_chunk_size = size
            return self

        def enable_markdown(self, enabled: bool) -> "Application.Builder":
            self._enable_markdown = enabled
            return self

        def build(self) -> "Application":
            bot = QQBot(
                self._app_id,
                self._app_secret,
                self._api_base,
                self._reply_chunk_size,
                self._enable_markdown,
            )
            return Application(bot)

    @staticmethod
    def builder() -> "Application.Builder":
        return Application.Builder()

    def add_handler(self, handler: BaseHandler) -> None:
        """注册处理器"""
        self.handlers.append(handler)

    async def _dispatch(self, message: QQMessage) -> None:
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

        logger.debug(f"No handler matched for message: {message.content[:50]}")

    async def start(self) -> None:
        """启动 Bot（非阻塞）"""
        await self.bot.start(self._dispatch)
        self._running = True

    async def stop(self) -> None:
        """停止 Bot"""
        self._running = False
        await self.bot.stop()
        if self._stop_event:
            self._stop_event.set()

    def run_polling(self) -> None:
        """阻塞式运行，Ctrl+C 停止"""
        stop_event = asyncio.Event()
        self._stop_event = stop_event

        async def _run():
            await self.start()
            print("QQ Bot已启动！按 Ctrl+C 停止")
            await stop_event.wait()

        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            pass
