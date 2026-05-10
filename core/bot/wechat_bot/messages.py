from __future__ import annotations

import logging
import os
import base64
from typing import Any, Optional
from uuid import uuid4

import aiohttp

from .auth import WeChatAuth, ILINK_API_BASE
from .types import (
    MessageType,
    MessageState,
    MessageItemType,
    BaseInfo,
    SendMessageMessage,
)

logger = logging.getLogger(__name__)

_CHANNEL_VERSION = "1.0.0"


class WeChatMessages:
    """微信消息发送管理"""

    def __init__(self, auth: WeChatAuth, session: aiohttp.ClientSession, base_url: str = ILINK_API_BASE):
        self.auth = auth
        self.session = session
        self.base_url = base_url

    def _build_headers(self, token: str) -> dict[str, str]:
        """构建请求头"""
        uin = str(int.from_bytes(os.urandom(4), "big"))
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {token}",
            "X-WECHAT-UIN": base64.b64encode(uin.encode("utf-8")).decode("ascii"),
        }

    def _build_base_info(self) -> BaseInfo:
        """构建base_info"""
        return {"channel_version": _CHANNEL_VERSION}

    def _build_text_message(self, user_id: str, context_token: str, text: str) -> SendMessageMessage:
        """构建文本消息"""
        return {
            "from_user_id": "",
            "to_user_id": user_id,
            "client_id": str(uuid4()),
            "message_type": MessageType.BOT,
            "message_state": MessageState.FINISH,
            "context_token": context_token,
            "item_list": [
                {
                    "type": MessageItemType.TEXT,
                    "text_item": {"text": text},
                }
            ],
        }

    async def send_text(self, user_id: str, text: str, context_token: str) -> bool:
        """发送文本消息

        Args:
            user_id: 用户ID
            text: 消息文本
            context_token: 上下文令牌（从收到的消息中获取）

        Returns:
            是否发送成功
        """
        if not text:
            raise ValueError("消息文本不能为空")

        try:
            token = await self.auth.get_token()
            chunks = self._chunk_text(text, 2000)

            for chunk in chunks:
                msg = self._build_text_message(user_id, context_token, chunk)
                await self._send_message(token, msg)

            return True
        except Exception as e:
            logger.error(f"发送文本消息失败: {e}")
            return False

    async def send_typing(self, user_id: str, context_token: str, status: int = 1) -> bool:
        """发送正在输入状态

        Args:
            user_id: 用户ID
            context_token: 上下文令牌
            status: 状态 (1=开始输入, 2=取消输入)

        Returns:
            是否发送成功
        """
        try:
            token = await self.auth.get_token()

            # 获取typing_ticket
            config = await self._get_config(token, user_id, context_token)
            typing_ticket = config.get("typing_ticket")
            if not typing_ticket:
                logger.warning("无法获取typing_ticket")
                return False

            # 发送typing状态
            url = f"{self.base_url}/ilink/bot/sendtyping"
            body = {
                "ilink_user_id": user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": self._build_base_info(),
            }
            headers = self._build_headers(token)

            async with self.session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            if data.get("ret", 0) != 0:
                logger.warning(f"发送typing状态失败: {data}")
                return False

            return True
        except Exception as e:
            logger.error(f"发送typing状态失败: {e}")
            return False

    async def _get_config(self, token: str, user_id: str, context_token: str) -> dict:
        """获取配置（包括typing_ticket）"""
        url = f"{self.base_url}/ilink/bot/getconfig"
        body = {
            "ilink_user_id": user_id,
            "context_token": context_token,
            "base_info": self._build_base_info(),
        }
        headers = self._build_headers(token)

        async with self.session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            return await resp.json(content_type=None)

    async def _send_message(self, token: str, msg: SendMessageMessage) -> dict:
        """发送消息"""
        url = f"{self.base_url}/ilink/bot/sendmessage"
        body = {
            "msg": msg,
            "base_info": self._build_base_info(),
        }
        headers = self._build_headers(token)

        async with self.session.post(url, json=body, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)

        if data.get("ret", 0) != 0:
            raise Exception(f"发送消息失败: {data}")

        return data

    def _chunk_text(self, text: str, limit: int) -> list[str]:
        """分割长文本"""
        chunks = [text[i:i + limit] for i in range(0, len(text), limit)]
        return chunks or [""]


__all__ = ["WeChatMessages"]
