"""
会话管理器
支持创建、保存、加载和列出会话
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import hashlib

logger = logging.getLogger(__name__)


class SessionManager:
    """会话管理器"""

    def __init__(self, cache_dir: Optional[Path] = None, session_type: str = "default"):
        """
        初始化会话管理器

        Args:
            cache_dir: 缓存目录路径，默认为 ./cache
            session_type: 会话类型（ask/agent/translate等），用于创建子目录
        """
        base_dir = cache_dir or Path("cache")
        self.cache_dir = base_dir / session_type
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.current_session_id: Optional[str] = None
        self.current_session_name: Optional[str] = None
        self.session_type = session_type

        logger.info(f"会话管理器初始化，缓存目录: {self.cache_dir}")

    def generate_session_id(self) -> str:
        """
        生成会话 ID

        Returns:
            会话 ID（时间戳 + 短哈希）
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_hash = hashlib.md5(str(datetime.now().timestamp()).encode()).hexdigest()[
            :6
        ]
        return f"{timestamp}_{random_hash}"

    def generate_session_name(self, messages: List[Dict]) -> str:
        """
        从消息生成会话名称

        Args:
            messages: 消息列表

        Returns:
            会话名称
        """
        # 查找第一个用户消息
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if content:
                    # 取前 30 个字符作为名称
                    name = content[:30].strip()
                    # 替换换行符
                    name = name.replace("\n", " ")
                    return name if name else "新会话"

        return "新会话"

    def save_session(
        self,
        messages: List[Dict],
        session_id: Optional[str] = None,
        session_name: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[Tuple[str, Path]]:
        """
        保存会话到文件

        Args:
            messages: 消息列表
            session_id: 会话 ID（如果为 None 则自动生成）
            session_name: 会话名称（如果为 None 则自动生成）
            metadata: 额外的元数据

        Returns:
            (session_id, file_path)，如果消息只有 system prompt 则返回 None
        """
        if len(messages) <= 1:
            logger.debug("会话只包含 system prompt，不保存")
            return None

        # 生成会话 ID
        if not session_id:
            session_id = self.generate_session_id()

        # 生成会话名称
        if not session_name:
            session_name = self.generate_session_name(messages)

        # 构建会话数据
        session_data = {
            "id": session_id,
            "name": session_name,
            "created_at": datetime.now().isoformat(),
            "message_count": len(messages),
            "metadata": metadata or {},
            "messages": messages,
        }

        # 保存到文件
        file_path = self.cache_dir / f"{session_id}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)

            logger.info(f"会话已保存: {session_id} -> {file_path}")
            return (session_id, file_path)

        except Exception as e:
            logger.error(f"保存会话失败: {e}")
            raise

    def load_session(self, session_id: str) -> Optional[Dict]:
        """
        加载会话

        Args:
            session_id: 会话 ID

        Returns:
            会话数据，如果不存在则返回 None
        """
        file_path = self.cache_dir / f"{session_id}.json"

        if not file_path.exists():
            logger.warning(f"会话文件不存在: {file_path}")
            return None

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                session_data = json.load(f)

            logger.info(f"会话已加载: {session_id}")
            return session_data

        except Exception as e:
            logger.error(f"加载会话失败: {e}")
            return None

    def list_sessions(self, limit: int = 20) -> List[Dict]:
        """
        列出最近的会话

        Args:
            limit: 返回的最大会话数

        Returns:
            会话列表（按时间倒序）
        """
        sessions = []

        # 查找所有会话文件
        session_files = sorted(
            self.cache_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )

        # 读取会话元数据
        for file_path in session_files[:limit]:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    session_data = json.load(f)

                # 只保留元数据，不包含完整消息
                session_info = {
                    "id": session_data.get("id"),
                    "name": session_data.get("name"),
                    "created_at": session_data.get("created_at"),
                    "message_count": session_data.get("message_count"),
                    "file_path": str(file_path),
                }

                sessions.append(session_info)

            except Exception as e:
                logger.warning(f"读取会话文件失败: {file_path}, {e}")
                continue

        return sessions

    def delete_session(self, session_id: str) -> bool:
        """
        删除会话

        Args:
            session_id: 会话 ID

        Returns:
            是否删除成功
        """
        file_path = self.cache_dir / f"{session_id}.json"

        if not file_path.exists():
            logger.warning(f"会话不存在: {session_id}")
            return False

        try:
            file_path.unlink()
            logger.info(f"会话已删除: {session_id}")
            return True
        except Exception as e:
            logger.error(f"删除会话失败: {e}")
            return False

    def new_session(self, current_messages: List[Dict]) -> str:
        """
        创建新会话（保存当前会话并重置）

        Args:
            current_messages: 当前消息列表

        Returns:
            新会话 ID
        """
        result = self.save_session(
            current_messages,
            session_id=self.current_session_id,
            session_name=self.current_session_name,
        )
        if result:
            session_id, file_path = result
            logger.info(f"旧会话已保存: {session_id}")

        # 生成新会话 ID
        new_session_id = self.generate_session_id()
        self.current_session_id = new_session_id
        self.current_session_name = None

        logger.info(f"新会话已创建: {new_session_id}")
        return new_session_id

    def switch_session(self, session_id: str) -> Optional[List[Dict]]:
        """
        切换到指定会话

        Args:
            session_id: 会话 ID

        Returns:
            会话消息列表，如果失败则返回 None
        """
        session_data = self.load_session(session_id)

        if not session_data:
            return None

        self.current_session_id = session_id
        self.current_session_name = session_data.get("name")

        logger.info(f"已切换到会话: {session_id}")
        return session_data.get("messages", [])

    def get_session_info(self) -> Dict[str, str]:
        """
        获取当前会话信息

        Returns:
            会话信息字典
        """
        return {
            "session_id": self.current_session_id or "未保存",
            "session_name": self.current_session_name or "新会话",
        }
