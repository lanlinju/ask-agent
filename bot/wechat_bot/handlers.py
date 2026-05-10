from __future__ import annotations
from typing import Callable, Awaitable, Optional, Any
from .types import Update, Context

HandlerCallback = Callable[[Update, Context], Awaitable[None]]


class BaseHandler:
    """处理器基类"""

    def check_update(self, update: Update) -> bool:
        """检查是否应该处理此更新"""
        return False

    async def handle_update(self, update: Update, context: Context) -> None:
        """处理更新"""
        raise NotImplementedError


class CommandHandler(BaseHandler):
    """命令处理器，匹配 /command 开头的消息"""

    def __init__(self, command: str, callback: HandlerCallback):
        self.command = command if command.startswith("/") else f"/{command}"
        self.callback = callback

    def check_update(self, update: Update) -> bool:
        text = update.message.text.strip()
        cmd = text.split()[0]
        return cmd == self.command

    async def handle_update(self, update: Update, context: Context) -> None:
        await self.callback(update, context)


class MessageHandler(BaseHandler):
    """消息处理器，通过 filter 匹配"""

    def __init__(self, filter_fn: Callable[[Update], bool], callback: HandlerCallback):
        self.filter_fn = filter_fn
        self.callback = callback

    def check_update(self, update: Update) -> bool:
        return self.filter_fn(update)

    async def handle_update(self, update: Update, context: Context) -> None:
        await self.callback(update, context)


class filters:
    """消息过滤器，模仿 python-telegram-bot 的 filters"""

    @staticmethod
    def command(update: Update) -> bool:
        """匹配以 / 开头的命令消息"""
        return update.message.text.startswith("/")

    @staticmethod
    def photo(update: Update) -> bool:
        """匹配包含图片的消息"""
        return len(update.message.photo) > 0

    @staticmethod
    def voice(update: Update) -> bool:
        """匹配包含语音的消息"""
        return update.message.voice is not None

    @staticmethod
    def text(update: Update) -> bool:
        """匹配纯文本消息（无图片、无语音）"""
        return bool(update.message.text) and not update.message.photo and not update.message.voice

    @staticmethod
    def text_and_not_command(update: Update) -> bool:
        """匹配非命令的文本消息"""
        return filters.text(update) and not filters.command(update)

    class _And:
        """组合过滤器：AND"""

        def __init__(self, *fns: Callable[[Update], bool]):
            self.fns = fns

        def __call__(self, update: Update) -> bool:
            return all(fn(update) for fn in self.fns)

        def __and__(self, other: Callable[[Update], bool]) -> "filters._And":
            return filters._And(*self.fns, other)

    class _Not:
        """取反过滤器"""

        def __init__(self, fn: Callable[[Update], bool]):
            self.fn = fn

        def __call__(self, update: Update) -> bool:
            return not self.fn(update)

    @staticmethod
    def _create(fn: Callable[[Update], bool]):
        """创建可组合的过滤器"""
        return fn

    class _FilterFactory:
        """过滤器工厂，支持 & 和 ~ 运算符"""

        def __init__(self, fn: Callable[[Update], bool]):
            self.fn = fn

        def __call__(self, update: Update) -> bool:
            return self.fn(update)

        def __and__(self, other) -> "filters._And":
            if isinstance(other, filters._FilterFactory):
                return filters._And(self.fn, other.fn)
            return filters._And(self.fn, other)

        def __rand__(self, other) -> "filters._And":
            if isinstance(other, filters._FilterFactory):
                return filters._And(other.fn, self.fn)
            return filters._And(other, self.fn)

        def __invert__(self) -> "filters._Not":
            return filters._Not(self.fn)


# 使 filters 支持组合运算
filters.command = filters._FilterFactory(filters.command)  # type: ignore
filters.photo = filters._FilterFactory(filters.photo)  # type: ignore
filters.voice = filters._FilterFactory(filters.voice)  # type: ignore
filters.text = filters._FilterFactory(filters.text)  # type: ignore
filters.text_and_not_command = filters._FilterFactory(filters.text_and_not_command)  # type: ignore


__all__ = [
    "BaseHandler",
    "CommandHandler",
    "MessageHandler",
    "filters",
]