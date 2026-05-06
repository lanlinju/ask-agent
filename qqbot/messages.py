import base64
import logging
from typing import Optional

import requests

from .auth import QQAuth

logger = logging.getLogger(__name__)

QQ_API_BASE = "https://api.sgroup.qq.com"


class QQMessages:
    """QQ 消息发送管理"""

    def __init__(self, auth: QQAuth, api_base: str = QQ_API_BASE,
                 reply_chunk_size: int = 1500, enable_markdown: bool = True):
        self.auth = auth
        self.api_base = api_base
        self.reply_chunk_size = reply_chunk_size
        self.enable_markdown = enable_markdown
        self.seq = 1

    async def send_text(self, openid: str, text: str, msg_id: Optional[str] = None,
                        event_id: Optional[str] = None) -> bool:
        """发送文本消息"""
        chunks = self._split_text(text)
        for chunk in chunks:
            await self._send_text_chunk(openid, chunk, msg_id, event_id)
        return True

    async def send_markdown(self, openid: str, markdown: str, msg_id: Optional[str] = None,
                           event_id: Optional[str] = None) -> bool:
        """发送Markdown消息"""
        if not self.enable_markdown:
            return await self.send_text(openid, markdown, msg_id, event_id)

        chunks = self._split_text(markdown)
        for chunk in chunks:
            try:
                await self._send_markdown_chunk(openid, chunk, msg_id, event_id)
            except Exception as e:
                logger.warning(f"QQ markdown发送失败，回退到文本: {e}")
                await self._send_text_chunk(openid, chunk, msg_id, event_id)
        return True

    async def send_image(self, openid: str, file_path: str, msg_id: Optional[str] = None,
                        event_id: Optional[str] = None) -> bool:
        """发送图片消息"""
        try:
            file_info = await self._upload_image(openid, file_path)
            await self._send_media(openid, file_info, msg_id, event_id)
            return True
        except Exception as e:
            logger.error(f"发送图片失败: {e}")
            return False

    async def _upload_image(self, openid: str, file_path: str) -> str:
        """上传图片"""
        token = await self.auth.get_access_token()
        url = f"{self.api_base}/v2/users/{openid}/files"

        with open(file_path, "rb") as f:
            file_data = base64.b64encode(f.read()).decode("utf-8")

        response = requests.post(
            url,
            json={
                "file_type": 1,
                "srv_send_msg": False,
                "file_data": file_data
            },
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        response.raise_for_status()
        data = response.json()

        if not response.ok or not data.get("file_info"):
            raise Exception(f"QQ上传图片失败: {response.status_code} {data}")

        return data["file_info"]

    async def send_voice(self, openid: str, voice: bytes, msg_id: Optional[str] = None,
                         event_id: Optional[str] = None) -> bool:
        """发送语音消息

        Args:
            openid: 用户 openid
            voice: 音频字节数据（支持 silk/ogg/mp3 等格式）
        """
        try:
            token = await self.auth.get_access_token()
            upload_url = f"{self.api_base}/v2/users/{openid}/files"

            file_data = base64.b64encode(voice).decode("utf-8")

            response = requests.post(
                upload_url,
                json={
                    "file_type": 3,
                    "srv_send_msg": False,
                    "file_data": file_data
                },
                headers={
                    "Authorization": f"QQBot {token}",
                    "Content-Type": "application/json"
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()

            if not response.ok or not data.get("file_info"):
                raise Exception(f"QQ上传语音失败: {response.status_code} {data}")

            await self._send_media(openid, data["file_info"], msg_id, event_id)
            return True
        except Exception as e:
            logger.error(f"发送语音失败: {e}")
            return False

    async def _send_media(self, openid: str, file_info: str, msg_id: Optional[str] = None,
                         event_id: Optional[str] = None) -> None:
        """发送媒体消息"""
        token = await self.auth.get_access_token()
        url = f"{self.api_base}/v2/users/{openid}/messages"

        body = {
            "msg_type": 7,
            "media": {"file_info": file_info},
            "msg_seq": self.seq
        }
        self.seq += 1

        if msg_id:
            body["msg_id"] = msg_id
        if event_id:
            body["event_id"] = event_id

        response = requests.post(
            url,
            json=body,
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        response.raise_for_status()

        if not response.ok:
            raise Exception(f"QQ发送媒体失败: {response.status_code} {response.text}")

    async def _send_text_chunk(self, openid: str, content: str, msg_id: Optional[str] = None,
                              event_id: Optional[str] = None) -> None:
        """发送文本消息块"""
        token = await self.auth.get_access_token()
        url = f"{self.api_base}/v2/users/{openid}/messages"

        body = {
            "msg_type": 0,
            "content": content,
            "msg_seq": self.seq
        }
        self.seq += 1

        if msg_id:
            body["msg_id"] = msg_id
        if event_id:
            body["event_id"] = event_id

        response = requests.post(
            url,
            json=body,
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        response.raise_for_status()

        if not response.ok:
            raise Exception(f"QQ发送消息失败: {response.status_code} {response.text}")

    async def _send_markdown_chunk(self, openid: str, content: str, msg_id: Optional[str] = None,
                                  event_id: Optional[str] = None) -> None:
        """发送Markdown消息块"""
        token = await self.auth.get_access_token()
        url = f"{self.api_base}/v2/users/{openid}/messages"

        body = {
            "msg_type": 2,
            "markdown": {"content": content},
            "msg_seq": self.seq
        }
        self.seq += 1

        if msg_id:
            body["msg_id"] = msg_id
        if event_id:
            body["event_id"] = event_id

        response = requests.post(
            url,
            json=body,
            headers={
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json"
            },
            timeout=30
        )
        response.raise_for_status()

        if not response.ok:
            raise Exception(f"QQ发送markdown失败: {response.status_code} {response.text}")

    def _split_text(self, text: str) -> list:
        """分割长文本"""
        text = text.strip() or "(empty)"
        chunks = []
        remaining = text

        while len(remaining) > self.reply_chunk_size:
            cut = remaining.rfind("\n", 0, self.reply_chunk_size)
            if cut < self.reply_chunk_size * 0.5:
                cut = self.reply_chunk_size
            chunks.append(remaining[:cut])
            remaining = remaining[cut:].lstrip()

        chunks.append(remaining)
        return chunks
