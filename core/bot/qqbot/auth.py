import time
import logging
import requests

logger = logging.getLogger(__name__)

QQ_TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


class QQAuth:
    """QQ Bot 认证管理"""

    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.token = ""
        self.expires_at = 0

    async def get_access_token(self) -> str:
        """获取访问令牌"""
        now = time.time() * 1000
        if self.token and now < self.expires_at - 60000:
            return self.token

        try:
            response = requests.post(
                QQ_TOKEN_URL,
                json={
                    "appId": self.app_id,
                    "clientSecret": self.app_secret
                },
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if not response.ok or not data.get("access_token"):
                raise Exception(f"QQ token request failed: {response.status_code} {data}")

            self.token = data["access_token"]
            self.expires_at = now + (int(data.get("expires_in", 7200)) * 1000)
            return self.token

        except Exception as e:
            logger.error(f"获取QQ访问令牌失败: {e}")
            raise
