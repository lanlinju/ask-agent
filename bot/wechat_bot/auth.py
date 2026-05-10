from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests
import qrcode

logger = logging.getLogger(__name__)

ILINK_API_BASE = "https://ilinkai.weixin.qq.com"
DEFAULT_TOKEN_DIR = Path.home() / ".ask-agent" / "wechat"
DEFAULT_TOKEN_PATH = DEFAULT_TOKEN_DIR / "credentials.json"
QR_POLL_INTERVAL_MS = 2_000


@dataclass
class Credentials:
    """微信Bot凭证"""
    token: str
    base_url: str
    account_id: str
    user_id: str


class WeChatAuth:
    """微信Bot认证管理"""

    def __init__(self, base_url: str = ILINK_API_BASE, token_path: Optional[Path] = None):
        self.base_url = base_url
        self.token_path = token_path or DEFAULT_TOKEN_PATH
        self.credentials: Optional[Credentials] = None

    def _build_headers(self, token: Optional[str] = None) -> dict[str, str]:
        """构建请求头"""
        uin = str(int.from_bytes(os.urandom(4), "big"))
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": base64.b64encode(uin.encode("utf-8")).decode("ascii"),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _save_credentials(self, credentials: Credentials) -> None:
        """保存凭证到文件"""
        self.token_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        payload = {
            "token": credentials.token,
            "baseUrl": credentials.base_url,
            "accountId": credentials.account_id,
            "userId": credentials.user_id,
        }
        self.token_path.write_text(
            f"{json.dumps(payload, indent=2)}\n", encoding="utf-8"
        )
        self.token_path.chmod(0o600)

    def _load_credentials(self) -> Optional[Credentials]:
        """从文件加载凭证"""
        try:
            parsed = json.loads(self.token_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

        token = parsed.get("token")
        base_url = parsed.get("baseUrl") or parsed.get("base_url")
        account_id = parsed.get("accountId") or parsed.get("account_id")
        user_id = parsed.get("userId") or parsed.get("user_id")

        if not all(isinstance(x, str) for x in [token, base_url, account_id, user_id]):
            return None

        return Credentials(
            token=token,
            base_url=base_url,
            account_id=account_id,
            user_id=user_id,
        )

    def _clear_credentials(self) -> None:
        """清除凭证文件"""
        self.token_path.unlink(missing_ok=True)

    async def login(self, force: bool = False) -> Credentials:
        """登录微信Bot

        Args:
            force: 强制重新登录，忽略已保存的凭证

        Returns:
            Credentials对象
        """
        if not force:
            existing = self._load_credentials()
            if existing is not None:
                self.credentials = existing
                self.base_url = existing.base_url
                logger.info(f"使用已保存的凭证登录: {existing.user_id}")
                return existing

        logger.info("开始QR码登录流程...")
        while True:
            # 1. 获取QR码
            qr = await self._fetch_qr_code()
            self._display_qr_code(qr["qrcode_img_content"])

            # 2. 轮询扫码状态
            last_status: Optional[str] = None
            while True:
                status = await self._poll_qr_status(qr["qrcode"])

                if status["status"] != last_status:
                    if status["status"] == "scaned":
                        logger.info("QR码已扫描，请在微信中确认登录")
                    elif status["status"] == "confirmed":
                        logger.info("登录已确认")
                    elif status["status"] == "expired":
                        logger.info("QR码已过期，正在获取新的QR码...")
                    last_status = status["status"]

                if status["status"] == "confirmed":
                    token = status.get("bot_token")
                    account_id = status.get("ilink_bot_id")
                    user_id = status.get("ilink_user_id")
                    if not all(isinstance(x, str) for x in [token, account_id, user_id]):
                        raise RuntimeError("QR登录确认成功，但API未返回Bot凭证")

                    credentials = Credentials(
                        token=token,
                        base_url=status.get("baseurl") or self.base_url,
                        account_id=account_id,
                        user_id=user_id,
                    )
                    self._save_credentials(credentials)
                    self.credentials = credentials
                    self.base_url = credentials.base_url
                    logger.info(f"登录成功: {credentials.user_id}")
                    return credentials

                if status["status"] == "expired":
                    break

                await asyncio.sleep(QR_POLL_INTERVAL_MS / 1000)

    async def get_token(self) -> str:
        """获取访问令牌

        Returns:
            bot_token字符串
        """
        if self.credentials is None:
            await self.login()
        return self.credentials.token

    def _display_qr_code(self, qrcode_img_content: str) -> None:
        """显示二维码

        Args:
            qrcode_img_content: 二维码内容（URL、base64或SVG）
        """
        if not qrcode_img_content:
            logger.warning("二维码内容为空")
            return

        content = str(qrcode_img_content)

        if content.startswith("data:image/"):
            # base64编码的图片，保存为文件并尝试在终端显示
            header, b64 = content.split(",", 1)
            m = re.search(r"data:image/(\w+)", header)
            ext = m.group(1) if m else "png"
            filename = f"qrcode.{ext}"
            with open(filename, "wb") as f:
                f.write(base64.b64decode(b64))
            logger.info(f"二维码已保存到 {filename}")
            print(f"\n请扫描二维码登录: {filename}\n")

        elif content.startswith("http"):
            # URL链接，在终端生成ASCII二维码
            print("\n请使用微信扫描以下二维码登录:")
            print(f"\n链接: {content}\n")
            # 设置stdout编码为utf-8以支持Unicode字符
            sys.stdout.reconfigure(encoding='utf-8')
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=1,
                border=1,
            )
            qr.add_data(content)
            qr.make(fit=True)
            qr.print_ascii(invert=True)
            print()

        elif content.startswith("<svg"):
            # SVG格式
            filename = "qrcode.svg"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(content)
            logger.info(f"二维码已保存到 {filename}")
            print(f"\n请扫描二维码登录: {filename}\n")

        else:
            # 其他格式（可能是base64数据）
            filename = "qrcode.png"
            with open(filename, "wb") as f:
                f.write(base64.b64decode(content))
            logger.info(f"二维码已保存到 {filename}")
            print(f"\n请扫描二维码登录: {filename}\n")

    async def _fetch_qr_code(self) -> dict[str, str]:
        """获取登录QR码"""
        url = f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type=3"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        if "qrcode" not in data:
            raise Exception(f"获取QR码失败: {data}")

        return data

    async def _poll_qr_status(self, qrcode: str) -> dict[str, Any]:
        """轮询QR码状态"""
        url = f"{self.base_url}/ilink/bot/get_qrcode_status?qrcode={qrcode}"
        headers = {"iLink-App-ClientVersion": "1"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()


# 全局实例
_wechat_auth_instance: Optional[WeChatAuth] = None


async def get_wechat_auth(base_url: str = ILINK_API_BASE) -> WeChatAuth:
    """获取全局WeChatAuth实例"""
    global _wechat_auth_instance
    if _wechat_auth_instance is None:
        _wechat_auth_instance = WeChatAuth(base_url)
    return _wechat_auth_instance


async def login_wechat(force: bool = False) -> Credentials:
    """登录微信Bot"""
    auth = await get_wechat_auth()
    return await auth.login(force=force)


async def logout_wechat() -> None:
    """登出微信Bot"""
    global _wechat_auth_instance
    if _wechat_auth_instance:
        _wechat_auth_instance._clear_credentials()
        _wechat_auth_instance = None


__all__ = [
    "Credentials",
    "WeChatAuth",
    "get_wechat_auth",
    "login_wechat",
    "logout_wechat",
    "ILINK_API_BASE",
]