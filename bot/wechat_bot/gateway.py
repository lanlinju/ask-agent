from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any, Callable, Awaitable, Optional

import requests

from .auth import WeChatAuth, ILINK_API_BASE
from .types import (
    WeChatMessage,
    WeixinMessage,
    MessageType,
    MessageItemType,
    MessageKind,
    GetUpdatesResponse,
)

logger = logging.getLogger(__name__)

CHANNEL_VERSION = "1.0.0"


class WeChatGateway:
    """微信长轮询Gateway"""

    def __init__(self, auth: WeChatAuth, base_url: str = ILINK_API_BASE):
        self.auth = auth
        self.base_url = base_url
        self.cursor: str = ""
        self.on_message_callback: Optional[Callable[[WeChatMessage], Awaitable[None]]] = None
        self._stopped = False
        self._current_poll_task: Optional[asyncio.Task] = None

    def _build_headers(self, token: str) -> dict[str, str]:
        """构建请求头"""
        uin = str(int.from_bytes(os.urandom(4), "big"))
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": base64.b64encode(uin.encode("utf-8")).decode("ascii"),
        }

    def _build_base_info(self) -> dict[str, str]:
        """构建base_info"""
        return {"channel_version": CHANNEL_VERSION}

    async def start(self, on_message: Callable[[WeChatMessage], Awaitable[None]]) -> None:
        """启动Gateway"""
        self.on_message_callback = on_message
        self._stopped = False
        logger.info("启动微信长轮询Gateway...")
        await self._run_loop()

    async def stop(self) -> None:
        """停止Gateway"""
        logger.info("正在停止微信Gateway...")
        self._stopped = True
        if self._current_poll_task and not self._current_poll_task.done():
            self._current_poll_task.cancel()
        logger.info("微信Gateway已停止")

    async def _run_loop(self) -> None:
        """主轮询循环"""
        retry_delay_seconds = 1.0

        while not self._stopped:
            try:
                token = await self.auth.get_token()
                self._current_poll_task = asyncio.create_task(
                    self._get_updates(token)
                )
                updates = await self._current_poll_task
                self._current_poll_task = None
                self.cursor = updates.get("get_updates_buf") or self.cursor
                retry_delay_seconds = 1.0

                for raw in updates.get("msgs", []):
                    message = self._to_wechat_message(raw)
                    if message and self.on_message_callback:
                        logger.debug(f"收到消息: {message.text[:50]}")
                        await self.on_message_callback(message)

            except asyncio.CancelledError:
                self._current_poll_task = None
                if self._stopped:
                    break
                raise
            except Exception as e:
                self._current_poll_task = None
                if self._stopped:
                    break

                if self._is_session_expired(e):
                    logger.warning("会话已过期，需要重新登录")
                    self.auth.credentials = None
                    self.cursor = ""
                    try:
                        await self.auth.login(force=True)
                        retry_delay_seconds = 1.0
                        continue
                    except Exception as login_error:
                        logger.error(f"重新登录失败: {login_error}")

                logger.error(f"轮询错误: {e}")
                await asyncio.sleep(retry_delay_seconds)
                retry_delay_seconds = min(retry_delay_seconds * 2, 10.0)

    async def _get_updates(self, token: str) -> GetUpdatesResponse:
        """获取更新"""
        url = f"{self.base_url}/ilink/bot/getupdates"
        body = {
            "get_updates_buf": self.cursor,
            "base_info": self._build_base_info(),
        }
        headers = self._build_headers(token)

        response = requests.post(url, json=body, headers=headers, timeout=40)
        response.raise_for_status()
        data = response.json()

        if data.get("ret", 0) != 0:
            errcode = data.get("errcode")
            if errcode == -14:
                raise Exception("Session expired")
            raise Exception(f"getupdates failed: {data}")

        return data

    def _to_wechat_message(self, raw: WeixinMessage) -> Optional[WeChatMessage]:
        """将原始消息转换为WeChatMessage"""
        if raw.get("message_type") != MessageType.USER:
            return None

        user_id = raw.get("from_user_id", "")
        context_token = raw.get("context_token", "")
        item_list = raw.get("item_list", [])

        text = self._extract_text(item_list)
        msg_type = self._detect_type(item_list)
        create_time_ms = raw.get("create_time_ms", 0)

        from datetime import datetime, timezone
        timestamp = datetime.fromtimestamp(create_time_ms / 1000, tz=timezone.utc).astimezone()

        return WeChatMessage(
            id=raw.get("message_id"),
            user_id=user_id,
            text=text,
            type=msg_type,
            context_token=context_token,
            timestamp=timestamp,
            raw=raw,
        )

    def _extract_text(self, items: list) -> str:
        """提取消息文本"""
        parts = []
        for item in items:
            item_type = item.get("type")
            if item_type == MessageItemType.TEXT:
                text = item.get("text_item", {}).get("text", "")
            elif item_type == MessageItemType.IMAGE:
                text = item.get("image_item", {}).get("url", "[image]")
            elif item_type == MessageItemType.VOICE:
                text = item.get("voice_item", {}).get("text", "[voice]")
            elif item_type == MessageItemType.FILE:
                text = item.get("file_item", {}).get("file_name", "[file]")
            elif item_type == MessageItemType.VIDEO:
                text = "[video]"
            else:
                text = ""

            if text:
                parts.append(text)

        return "\n".join(parts)

    def _detect_type(self, items: list) -> MessageKind:
        """检测消息类型"""
        first = items[0] if items else None
        item_type = first.get("type") if first else None

        if item_type == MessageItemType.IMAGE:
            return "image"
        if item_type == MessageItemType.VOICE:
            return "voice"
        if item_type == MessageItemType.FILE:
            return "file"
        if item_type == MessageItemType.VIDEO:
            return "video"
        return "text"

    def _is_session_expired(self, error: Exception) -> bool:
        """检查是否是会话过期错误"""
        return "Session expired" in str(error) or "-14" in str(error)


__all__ = ["WeChatGateway"]