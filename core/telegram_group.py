"""
Telegram 群组管理器
支持群组消息过滤、独立会话管理和工具权限控制
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any

from core.config import ChannelConfig, GroupConfig

logger = logging.getLogger(__name__)


class TelegramGroupManager:
    """Telegram 群组管理器"""

    def __init__(self, channel_config: ChannelConfig, cache_dir: Path):
        """初始化群组管理器

        Args:
            channel_config: Telegram 频道配置
            cache_dir: 缓存目录路径
        """
        self.config = channel_config
        self.cache_dir = cache_dir / "telegram" / "groups"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # 群组会话缓存: {group_id: {"messages": [], "system_prompt": str}}
        self.group_sessions: Dict[str, Dict[str, Any]] = {}

        logger.info(f"Telegram 群组管理器初始化，缓存目录: {self.cache_dir}")

    def should_respond(
        self,
        user_id: str,
        group_id: str,
        is_mentioned: bool = False,
        has_other_mentions: bool = False,
    ) -> bool:
        """判断是否应该响应消息

        Args:
            user_id: 用户 ID
            group_id: 群组 ID
            is_mentioned: bot 是否被 @提及
            has_other_mentions: 消息中是否提到了其他人（但不是 bot）

        Returns:
            是否应该响应
        """
        # 如果群组策略为 disabled，不响应
        if self.config.group_policy == "disabled":
            return False

        # 检查用户权限（白名单/黑名单）
        if not self.config.is_user_allowed(user_id, group_id):
            logger.debug(f"用户 {user_id} 在群组 {group_id} 中无权限")
            return False

        # 获取群组配置
        group_config = self.config.get_group_config(group_id)

        # 检查用户是否在白名单中（白名单用户无需 @提及）
        is_whitelisted = self._is_user_whitelisted(user_id, group_id)

        # 白名单用户无需 @提及，直接响应
        if is_whitelisted:
            return True

        # 非白名单用户需要检查 @提及
        if group_config.require_mention and not is_mentioned:
            # 如果需要 @提及但没有提及，检查是否应该忽略
            if group_config.ignore_other_mentions and has_other_mentions:
                # 提到了其他人但没有提到 bot，忽略
                return False
            # 需要 @提及但没有提及，不响应
            return False

        return True

    def _is_user_whitelisted(self, user_id: str, group_id: str) -> bool:
        """检查用户是否在白名单中

        Args:
            user_id: 用户 ID
            group_id: 群组 ID

        Returns:
            是否在白名单中
        """
        group_config = self.config.get_group_config(group_id)

        # 检查群组级别白名单
        if group_config.allow_from and user_id in group_config.allow_from:
            return True

        # 检查全局白名单
        if self.config.group_policy == "allowlist" and user_id in self.config.group_allow_from:
            return True

        return False

    def should_save_to_history(
        self,
        user_id: str,
        group_id: str,
    ) -> bool:
        """判断是否应该保存消息到历史记录

        Args:
            user_id: 用户 ID
            group_id: 群组 ID

        Returns:
            是否应该保存
        """
        # 检查用户是否被允许
        if not self.config.is_user_allowed(user_id, group_id):
            return False

        # 获取群组配置
        group_config = self.config.get_group_config(group_id)

        # 如果 tool_policy 为 none，不保存
        if group_config.tool_policy == "none":
            return False

        return True

    def get_group_messages(self, group_id: str) -> List[Dict]:
        """获取群组消息历史

        Args:
            group_id: 群组 ID

        Returns:
            消息列表
        """
        if group_id not in self.group_sessions:
            self._load_group_session(group_id)

        return self.group_sessions[group_id].get("messages", [])

    def get_group_system_prompt(self, group_id: str) -> str:
        """获取群组系统提示词

        Args:
            group_id: 群组 ID

        Returns:
            系统提示词
        """
        group_config = self.config.get_group_config(group_id)
        return group_config.prompt

    def add_message(self, group_id: str, message: Dict):
        """添加消息到群组历史

        Args:
            group_id: 群组 ID
            message: 消息数据
        """
        if group_id not in self.group_sessions:
            self._load_group_session(group_id)

        self.group_sessions[group_id]["messages"].append(message)

        # 限制历史消息数量
        group_config = self.config.get_group_config(group_id)
        limit = group_config.history_limit

        messages = self.group_sessions[group_id]["messages"]
        if len(messages) > limit:
            # 保留系统提示词（如果有）和最近的消息
            system_msgs = [m for m in messages if m.get("role") == "system"]
            other_msgs = [m for m in messages if m.get("role") != "system"]

            # 保留最近的消息
            other_msgs = other_msgs[-(limit - len(system_msgs)):]
            self.group_sessions[group_id]["messages"] = system_msgs + other_msgs

    def clear_group_messages(self, group_id: str):
        """清空群组消息历史

        Args:
            group_id: 群组 ID
        """
        if group_id in self.group_sessions:
            # 保留系统提示词
            system_msgs = [
                m for m in self.group_sessions[group_id]["messages"]
                if m.get("role") == "system"
            ]
            self.group_sessions[group_id]["messages"] = system_msgs
            logger.info(f"已清空群组 {group_id} 的消息历史")

    def save_group_session(self, group_id: str):
        """保存群组会话到文件

        Args:
            group_id: 群组 ID
        """
        if group_id not in self.group_sessions:
            return

        session_data = self.group_sessions[group_id]
        messages = session_data.get("messages", [])

        # 不保存只包含系统提示词的会话
        if len(messages) <= 1:
            return

        file_path = self.cache_dir / f"{group_id}.json"
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "group_id": group_id,
                        "message_count": len(messages),
                        "messages": messages,
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.debug(f"群组会话已保存: {group_id}")
        except Exception as e:
            logger.error(f"保存群组会话失败: {e}")

    def save_all_sessions(self):
        """保存所有群组会话"""
        for group_id in self.group_sessions:
            self.save_group_session(group_id)

    def _load_group_session(self, group_id: str):
        """从文件加载群组会话

        Args:
            group_id: 群组 ID
        """
        file_path = self.cache_dir / f"{group_id}.json"

        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.group_sessions[group_id] = {
                    "messages": data.get("messages", []),
                }
                logger.debug(f"已加载群组会话: {group_id}")
                return
            except Exception as e:
                logger.warning(f"加载群组会话失败: {e}")

        # 初始化新的群组会话
        self.group_sessions[group_id] = {"messages": []}

    def get_allowed_tools(self, group_id: str) -> List[str]:
        """获取群组允许使用的工具列表

        Args:
            group_id: 群组 ID

        Returns:
            允许的工具名称列表，None 表示使用默认工具集
        """
        group_config = self.config.get_group_config(group_id)

        if group_config.tool_policy == "none":
            return []

        if group_config.tool_policy == "restricted":
            # 只允许安全工具
            return [
                "webfetch",
                "TodoWrite",
                "Skill",
                "read_file",
                "glob",
                "grep",
            ]

        # tool_policy == "full"，返回 None 表示使用默认工具集
        return None

    def get_tools_deny_list(self, group_id: str) -> List[str]:
        """获取群组禁用的工具列表

        Args:
            group_id: 群组 ID

        Returns:
            禁用的工具名称列表
        """
        group_config = self.config.get_group_config(group_id)
        return group_config.tools_deny


def is_bot_mentioned(message_text: str, entities: list, bot_username: str) -> bool:
    """检查 bot 是否被 @提及

    Args:
        message_text: 消息文本
        entities: 消息实体列表
        bot_username: bot 用户名

    Returns:
        是否被提及
    """
    if not message_text or not entities:
        return False

    for entity in entities:
        if entity.type == "mention":
            # 提取提及的用户名
            mention_text = message_text[entity.offset : entity.offset + entity.length]
            if f"@{bot_username}" in mention_text:
                return True

    return False


def has_other_mentions(message_text: str, entities: list, bot_username: str) -> bool:
    """检查消息中是否提到了其他人（但不是 bot）

    Args:
        message_text: 消息文本
        entities: 消息实体列表
        bot_username: bot 用户名

    Returns:
        是否提到了其他人
    """
    if not message_text or not entities:
        return False

    for entity in entities:
        if entity.type == "mention":
            mention_text = message_text[entity.offset : entity.offset + entity.length]
            # 如果提到了其他人（不是 bot）
            if f"@{bot_username}" not in mention_text:
                return True

    return False
