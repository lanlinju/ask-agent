import asyncio
import json
import logging
import re
from typing import Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field

import requests
import websockets

from .auth import QQAuth

logger = logging.getLogger(__name__)

QQ_API_BASE = "https://api.sgroup.qq.com"

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

INTENT_GROUP_AND_C2C = 1 << 25
MAX_RECONNECT = 5


@dataclass
class QQMessage:
    """QQ消息数据结构"""
    id: Optional[str] = None
    event_id: Optional[str] = None
    openid: str = ""
    group_openid: str = ""
    chat_type: str = "private"  # "private" or "group"
    content: str = ""
    attachments: list = field(default_factory=list)
    raw: Any = None


class QQGateway:
    """QQ WebSocket Gateway"""

    def __init__(self, auth: QQAuth, api_base: str = QQ_API_BASE):
        self.auth = auth
        self.api_base = api_base
        self.socket = None
        self.heartbeat_timer: Optional[asyncio.Task] = None
        self.reconnect_timer: Optional[asyncio.Task] = None
        self.reconnect_count: int = 0
        self.session_id: Optional[str] = None
        self.seq: Optional[int] = None
        self.on_message_callback: Optional[Callable[[QQMessage], Awaitable[None]]] = None

    async def start(self, on_message: Callable[[QQMessage], Awaitable[None]]) -> None:
        """启动Gateway"""
        self.on_message_callback = on_message
        await self._connect()

    async def stop(self) -> None:
        """停止Gateway"""
        logger.info("正在停止 Gateway...")
        if self.socket:
            await self.socket.close()
        self._cleanup_heartbeat()
        if self.reconnect_timer:
            self.reconnect_timer.cancel()
            self.reconnect_timer = None
        print("✅ QQ Gateway 已停止")

    async def _connect(self) -> None:
        """连接到Gateway"""
        self.reconnect_timer = None
        try:
            token = await self.auth.get_access_token()
            logger.info("正在获取 Gateway URL...")
            response = requests.get(
                f"{self.api_base}/gateway/bot",
                headers={"Authorization": f"QQBot {token}"},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if not response.ok or not data.get("url"):
                raise Exception(f"QQ gateway请求失败: {response.status_code} {data}")

            logger.info(f"正在连接 Gateway...")
            self.socket = await websockets.connect(data["url"])
            self.reconnect_count = 0
            print("✅ QQ Gateway 已连接")

            asyncio.create_task(self._message_handler())

        except Exception as e:
            logger.error(f"连接QQ gateway失败: {e}")
            self._schedule_reconnect()

    async def _message_handler(self) -> None:
        """处理WebSocket消息"""
        try:
            if not self.socket:
                return
            async for message in self.socket:
                try:
                    if isinstance(message, bytes):
                        message = message.decode("utf-8")
                    await self._handle_packet(message)
                except Exception as e:
                    logger.error(f"处理QQ gateway消息错误: {e}")
        except websockets.exceptions.ConnectionClosed:
            print("⚠️ QQ Gateway 连接已断开，正在重连...")
            self._cleanup_heartbeat()
            self._schedule_reconnect()
        except Exception as e:
            logger.error(f"QQ gateway消息处理错误: {e}")
            self._cleanup_heartbeat()
            self._schedule_reconnect()

    async def _handle_packet(self, data: str) -> None:
        """处理数据包"""
        payload = json.loads(data)

        if isinstance(payload.get("s"), int):
            self.seq = payload["s"]

        op = payload.get("op")

        if op == OP_DISPATCH:
            await self._handle_dispatch(payload)
        elif op == OP_HELLO:
            interval = payload.get("d", {}).get("heartbeat_interval", 45000)
            logger.info(f"收到 HELLO, 心跳间隔: {interval}ms")
            self._start_heartbeat(payload.get("d", {}))
            await self._identify_or_resume()
        elif op == OP_RECONNECT:
            logger.warning("收到 RECONNECT 指令，正在重连...")
            await self._reconnect_now()
        elif op == OP_INVALID_SESSION:
            logger.warning("收到 INVALID_SESSION，正在重新认证...")
            self.session_id = None
            await self._reconnect_now()
        elif op == OP_HEARTBEAT_ACK:
            logger.debug("心跳确认")

    async def _handle_dispatch(self, payload: dict) -> None:
        """处理事件分发"""
        event_type = payload.get("t")

        if event_type == "READY":
            ready = payload.get("d", {})
            if ready.get("session_id"):
                self.session_id = ready["session_id"]
            print("✅ QQ Bot 认证成功，已就绪")
            return

        if event_type == "C2C_MESSAGE_CREATE":
            message = self._extract_private_message(payload.get("d", {}))
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            message = self._extract_group_message(payload.get("d", {}))
        else:
            logger.debug(f"忽略事件: {event_type}")
            return

        if message and self.on_message_callback:
            logger.debug(f"收到事件: {event_type}")
            if message.attachments:
                types = [a.get("content_type", "unknown") for a in message.attachments]
                logger.info(f"消息类型: {', '.join(types)}")
            await self.on_message_callback(message)

    def _extract_private_message(self, data: dict) -> Optional[QQMessage]:
        """提取消息"""
        author = data.get("author", {})
        openid = (
            data.get("openid") or
            data.get("user_openid") or
            author.get("user_openid") or
            author.get("openid")
        )

        if not openid:
            return None

        content = re.sub(r"<@!?\d+>", "", data.get("content", "")).strip()
        attachments = self._extract_attachments(data)

        return QQMessage(
            id=data.get("id") or data.get("msg_id"),
            event_id=data.get("event_id"),
            openid=openid,
            chat_type="private",
            content=content,
            attachments=attachments,
            raw=data
        )

    def _extract_group_message(self, data: dict) -> Optional[QQMessage]:
        """提取群聊消息"""
        author = data.get("author", {})
        openid = (
            author.get("member_openid") or
            author.get("user_openid") or
            author.get("openid")
        )
        group_openid = data.get("group_openid", "")

        if not openid:
            return None

        # 去掉 @机器人 的标记
        content = re.sub(r"<@!?\d+>", "", data.get("content", "")).strip()
        attachments = self._extract_attachments(data)

        return QQMessage(
            id=data.get("id") or data.get("msg_id"),
            event_id=data.get("event_id"),
            openid=openid,
            group_openid=group_openid,
            chat_type="group",
            content=content,
            attachments=attachments,
            raw=data
        )

    def _extract_attachments(self, data: dict) -> list:
        """提取附件"""
        attachments = data.get("attachments", [])
        if not isinstance(attachments, list):
            return []

        result = []
        for item in attachments:
            if isinstance(item, dict):
                result.append({
                    "content_type": item.get("content_type", ""),
                    "filename": item.get("filename", "attachment"),
                    "size": item.get("size"),
                    "url": item.get("url"),
                    "width": item.get("width"),
                    "height": item.get("height")
                })
        return result

    def _start_heartbeat(self, data: dict) -> None:
        """启动心跳"""
        interval = data.get("heartbeat_interval", 45000) / 1000
        self._cleanup_heartbeat()

        async def heartbeat():
            while True:
                await asyncio.sleep(interval)
                if not self.socket:
                    break
                try:
                    await self.socket.send(json.dumps({
                        "op": OP_HEARTBEAT,
                        "d": self.seq
                    }))
                except Exception as e:
                    print(f"⚠️ QQ 心跳发送失败: {e}，触发重连")
                    self._schedule_reconnect()
                    break

        self.heartbeat_timer = asyncio.create_task(heartbeat())

    async def _identify_or_resume(self) -> None:
        """认证或恢复会话"""
        token = await self.auth.get_access_token()

        if not self.socket:
            return

        if self.session_id and isinstance(self.seq, int):
            logger.info(f"恢复会话: session_id={self.session_id}, seq={self.seq}")
            await self.socket.send(json.dumps({
                "op": OP_RESUME,
                "d": {
                    "token": f"QQBot {token}",
                    "session_id": self.session_id,
                    "seq": self.seq
                }
            }))
        else:
            logger.info("发送 IDENTIFY 认证...")
            await self.socket.send(json.dumps({
                "op": OP_IDENTIFY,
                "d": {
                    "token": f"QQBot {token}",
                    "intents": INTENT_GROUP_AND_C2C,
                    "shard": [0, 1],
                    "properties": {}
                }
            }))

    async def _reconnect_now(self) -> None:
        """立即重连"""
        print("🔄 QQ Gateway 正在立即重连...")
        if self.socket:
            try:
                await self.socket.close()
            except Exception:
                pass
        self.socket = None
        self._cleanup_heartbeat()
        self.reconnect_timer = None
        await self._connect()

    def _schedule_reconnect(self) -> None:
        """计划重连"""
        if self.reconnect_timer:
            return

        self.reconnect_count += 1
        if self.reconnect_count > MAX_RECONNECT:
            print(f"❌ QQ Gateway 已重连 {MAX_RECONNECT} 次均失败，停止重连")
            return

        delay = min(self.reconnect_count * 2, 30)
        print(f"🔄 QQ Gateway {delay}秒后重连 ({self.reconnect_count}/{MAX_RECONNECT})...")

        async def reconnect():
            try:
                await asyncio.sleep(delay)
                print(f"🔄 QQ Gateway 正在重连 ({self.reconnect_count}/{MAX_RECONNECT})...")
                # 清理旧连接状态
                if self.socket:
                    try:
                        await self.socket.close()
                    except Exception:
                        pass
                    self.socket = None
                await self._connect()
                print("✅ QQ Gateway 重连成功")
            except Exception as e:
                print(f"❌ QQ Gateway 重连失败: {e}")
                self.reconnect_timer = None
                self._schedule_reconnect()

        self.reconnect_timer = asyncio.create_task(reconnect())

    def _cleanup_heartbeat(self) -> None:
        """清理心跳定时器"""
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
            self.heartbeat_timer = None
