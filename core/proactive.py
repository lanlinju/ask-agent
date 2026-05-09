"""主动消息调度器

基于用户不活跃时间触发：用户停止聊天后开始计时，
超过随机间隔且概率命中时发送主动消息。
用户重新聊天则重置计时器。

支持 QQ 和 Telegram，发送失败（如月度上限）直接打印错误日志。
"""

import asyncio
import logging
import random
import re
import time
from datetime import datetime
from typing import Any, Callable, Awaitable, Dict, List, Optional

from .config import ProactiveMessageConfig

logger = logging.getLogger(__name__)


class ProactiveUserState:
    """单个用户的主动消息状态"""

    __slots__ = ("openid", "last_active", "next_interval_hours")

    def __init__(self, openid: str):
        self.openid = openid
        self.last_active: float = time.time()
        self.next_interval_hours: float = 0.0

    def generate_interval(self, config: ProactiveMessageConfig):
        """生成新的随机间隔"""
        self.next_interval_hours = random.uniform(
            config.min_interval_hours, config.max_interval_hours
        )

    def idle_seconds(self) -> float:
        """返回用户已空闲的秒数"""
        return time.time() - self.last_active

    def is_interval_reached(self) -> bool:
        """检查空闲时间是否已达到间隔"""
        if self.next_interval_hours <= 0:
            return False
        return self.idle_seconds() >= self.next_interval_hours * 3600


class ProactiveScheduler:
    """主动消息调度器（基于用户不活跃时间）"""

    def __init__(
        self,
        config: ProactiveMessageConfig,
        llm_caller: Callable[[List[Dict], List, bool, bool], tuple[str, str, List]],
        messages_getter: Callable[[], List[Dict[str, Any]]],
    ):
        self.config = config
        self.llm_caller = llm_caller
        self.messages_getter = messages_getter
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 发送回调: channel -> async (openid, message, chat_type) -> None
        self._send_callbacks: Dict[str, Callable[[str, str, str], Awaitable[None]]] = {}
        # 用户状态: channel -> {user_key: ProactiveUserState}
        self._users: Dict[str, Dict[str, ProactiveUserState]] = {}

    def register_send_callback(
        self, channel: str, callback: Callable[[str, str, str], Awaitable[None]]
    ):
        """注册消息发送回调"""
        self._send_callbacks[channel] = callback

    def register_user(self, channel: str, user_key: str, openid: str):
        """注册用户或更新活跃时间（在收到用户消息时调用）

        每次调用都会重置该用户的空闲计时器并重新生成随机间隔。
        """
        if channel not in self._users:
            self._users[channel] = {}

        states = self._users[channel]
        if user_key in states:
            state = states[user_key]
            state.last_active = time.time()
            state.openid = openid
            state.generate_interval(self.config)
            logger.debug(
                f"主动消息: 用户 {channel}/{user_key} 活跃，"
                f"重置计时器，新间隔 {state.next_interval_hours:.2f}h"
            )
        else:
            state = ProactiveUserState(openid)
            state.generate_interval(self.config)
            states[user_key] = state
            logger.info(
                f"主动消息: 注册用户 {channel}/{user_key}, "
                f"间隔 {state.next_interval_hours:.2f}h"
            )

    async def start(self):
        """启动调度器"""
        if not self.config.enabled:
            logger.info("主动消息未启用")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        check_sec = self.config.check_interval_seconds
        logger.info(
            f"主动消息调度器已启动: "
            f"不活跃间隔 {self.config.min_interval_hours}-{self.config.max_interval_hours}h, "
            f"轮询间隔 {check_sec}s, "
            f"概率 {self.config.send_probability}, "
            f"活跃时间 {self.config.active_start}:00-{self.config.active_end}:00"
        )

    async def stop(self):
        """停止调度器"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("主动消息调度器已停止")

    def _is_active_hours(self) -> bool:
        """检查是否在活跃时间窗口内"""
        current_hour = datetime.now().hour
        return self.config.active_start <= current_hour < self.config.active_end

    async def _scheduler_loop(self):
        """主调度循环：按 check_interval_seconds 轮询"""
        check_sec = self.config.check_interval_seconds
        while self._running:
            try:
                await asyncio.sleep(check_sec)

                if not self._running:
                    break

                if not self._is_active_hours():
                    continue

                await self._check_and_send()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"主动消息调度器异常: {e}", exc_info=True)
                await asyncio.sleep(60)

    async def _check_and_send(self):
        """检查所有用户，对空闲超时且概率命中的用户发送主动消息"""
        for channel, users in self._users.items():
            send_cb = self._send_callbacks.get(channel)
            if not send_cb:
                continue

            for user_key, state in list(users.items()):
                if not self._running:
                    break

                if not state.is_interval_reached():
                    continue

                # 概率判断
                if random.random() > self.config.send_probability:
                    logger.debug(
                        f"主动消息: 用户 {channel}/{user_key} "
                        f"空闲 {state.idle_seconds() / 3600:.1f}h, 概率未命中, 重新生成间隔"
                    )
                    state.last_active = time.time()
                    state.generate_interval(self.config)
                    continue

                logger.info(
                    f"主动消息: 用户 {channel}/{user_key} "
                    f"空闲 {state.idle_seconds() / 3600:.1f}h >= "
                    f"间隔 {state.next_interval_hours:.2f}h, 尝试发送"
                )

                try:
                    response = await self._generate_proactive_message()
                    if not response:
                        logger.warning(f"主动消息: 生成消息失败，跳过用户 {user_key}")
                        state.last_active = time.time()
                        state.generate_interval(self.config)
                        continue

                    await send_cb(state.openid, response, "private")
                    logger.info(f"主动消息: 已发送给 {channel}/{user_key}")

                    # 发送成功，重置计时器和间隔
                    state.last_active = time.time()
                    state.generate_interval(self.config)

                except Exception as e:
                    # 发送失败（如 QQ 月度上限），直接打印错误，重新计时
                    logger.error(
                        f"主动消息发送失败 ({channel}/{user_key}): {e}"
                    )
                    state.last_active = time.time()
                    state.generate_interval(self.config)

    async def _generate_proactive_message(self) -> str:
        """生成主动消息（共享上下文）

        在共享 messages 中注入提示词，获取 AI 回复后剔除注入的内容。
        """
        messages = self.messages_getter()
        if not messages:
            logger.warning("主动消息: 消息列表为空")
            return ""

        # 注入主动消息提示
        proactive_prompt = f"[系统提示 - 主动消息] {self.config.prompt}"
        injected_user_msg = {"role": "user", "content": proactive_prompt}
        messages.append(injected_user_msg)

        try:
            content, _, _ = self.llm_caller(messages, [], True, False)
            if not content:
                logger.warning("主动消息: AI 返回空内容")
                return ""

            # 清理 think 标签
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
            print(f"主动消息: {content}")
            return content

        except Exception as e:
            logger.error(f"主动消息: AI 调用失败: {e}", exc_info=True)
            return ""

        finally:
            # 剔除注入的提示词和 AI 回复
            try:
                if injected_user_msg in messages:
                    messages.remove(injected_user_msg)
                if messages and messages[-1].get("role") == "assistant":
                    messages.pop()
            except Exception as e:
                logger.warning(f"主动消息: 清理消息失败: {e}")
