"""
配置文件路径管理器
支持当前目录和用户目录 (~/.ask-agent) 两种配置文件位置
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ============ 频道配置数据类 ============


@dataclass
class MessageBufferConfig:
    """消息缓冲区配置"""

    enabled: bool = False  # 是否启用消息缓冲
    timeout: float = 3.0  # 超时时间（秒）
    group_only: bool = True  # 是否只在群组中启用

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MessageBufferConfig":
        """从字典创建配置"""
        return cls(
            enabled=data.get("enabled", False),
            timeout=data.get("timeout", 3.0),
            group_only=data.get("groupOnly", True),
        )


@dataclass
class GroupConfig:
    """群组配置"""

    name: str = ""  # 群组友好名称（日志用）
    require_mention: bool = True  # 是否需要 @机器人 才响应
    ignore_other_mentions: bool = False  # 忽略提及他人但未提及机器人的消息
    history_limit: int = 50  # 保留的历史消息数量
    tool_policy: str = "full"  # 工具策略: full | restricted | none
    prompt: str = ""  # 群组专属提示词
    allow_from: List[str] = field(default_factory=list)  # 群组级别用户白名单
    block_from: List[str] = field(default_factory=list)  # 群组级别用户黑名单
    tools_deny: List[str] = field(default_factory=list)  # 禁用的工具列表

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GroupConfig":
        """从字典创建配置"""
        return cls(
            name=data.get("name", ""),
            require_mention=data.get("requireMention", True),
            ignore_other_mentions=data.get("ignoreOtherMentions", False),
            history_limit=data.get("historyLimit", 50),
            tool_policy=data.get("toolPolicy", "full"),
            prompt=data.get("prompt", ""),
            allow_from=data.get("allowFrom", []),
            block_from=data.get("blockFrom", []),
            tools_deny=data.get("toolsDeny", []),
        )


@dataclass
class ChannelConfig:
    """频道配置（Telegram/QQ Bot 等）"""

    enabled: bool = False
    group_policy: str = "allowlist"  # allowlist | blocklist | disabled
    group_allow_from: List[str] = field(default_factory=list)  # 全局用户白名单/黑名单
    groups: Dict[str, GroupConfig] = field(default_factory=dict)  # 群组配置
    message_buffer: MessageBufferConfig = field(default_factory=MessageBufferConfig)  # 消息缓冲配置

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelConfig":
        """从字典创建配置"""
        groups = {}
        for group_id, group_data in data.get("groups", {}).items():
            groups[group_id] = GroupConfig.from_dict(group_data)

        return cls(
            enabled=data.get("enabled", False),
            group_policy=data.get("groupPolicy", "allowlist"),
            group_allow_from=data.get("groupAllowFrom", []),
            groups=groups,
            message_buffer=MessageBufferConfig.from_dict(data.get("messageBuffer", {})),
        )

    def get_group_config(self, group_id: str) -> GroupConfig:
        """获取群组配置，支持 * 通配符

        Args:
            group_id: 群组 ID

        Returns:
            群组配置，如果不存在则返回默认配置
        """
        if group_id in self.groups:
            return self.groups[group_id]
        return self.groups.get("*", GroupConfig())

    def is_user_allowed(self, user_id: str, group_id: str) -> bool:
        """检查用户是否被允许在指定群组中触发机器人

        Args:
            user_id: 用户 ID
            group_id: 群组 ID

        Returns:
            是否允许
        """
        group_config = self.get_group_config(group_id)

        # 1. 检查群组级别黑名单
        if group_config.block_from and user_id in group_config.block_from:
            return False

        # 2. 检查群组级别白名单（优先级最高）
        if group_config.allow_from:
            return user_id in group_config.allow_from

        # 3. 检查全局策略
        if self.group_policy == "disabled":
            return False

        if self.group_policy == "allowlist":
            return user_id in self.group_allow_from

        if self.group_policy == "blocklist":
            return user_id not in self.group_allow_from

        return True


@dataclass
class AppConfig:
    """应用配置"""

    mode: int = 0  # 当前模式
    channels: Dict[str, ChannelConfig] = field(default_factory=dict)  # 频道配置

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """从字典创建配置"""
        channels = {}
        for channel_id, channel_data in data.get("channels", {}).items():
            channels[channel_id] = ChannelConfig.from_dict(channel_data)

        return cls(
            mode=data.get("mode", 0),
            channels=channels,
        )

    def get_channel_config(self, channel_id: str) -> ChannelConfig:
        """获取频道配置

        Args:
            channel_id: 频道 ID (telegram/qqbot)

        Returns:
            频道配置，如果不存在则返回默认配置
        """
        return self.channels.get(channel_id, ChannelConfig())


class ConfigPathNotFoundError(Exception):
    """配置文件路径未找到异常"""

    pass


class ConfigPathManager:
    """配置文件路径管理器"""

    USER_DIR = ".ask-agent"

    def __init__(self, config_name: str):
        """
        初始化配置路径管理器

        Args:
            config_name: 配置文件名称（如 "mcp.json", "providers.json"）
        """
        self.config_name = config_name
        self._current_dir = Path.cwd()
        self._user_dir = Path.home() / self.USER_DIR

    @property
    def current_dir_path(self) -> Path:
        """获取当前目录下的配置文件路径"""
        return self._current_dir / self.config_name

    @property
    def user_dir_path(self) -> Path:
        """获取用户目录下的配置文件路径"""
        return self._user_dir / self.config_name

    def find_config(self) -> Optional[Path]:
        """
        查找配置文件
        优先顺序: 当前目录 -> 用户目录

        Returns:
            配置文件路径，如果不存在则返回 None
        """
        if self.current_dir_path.exists():
            logger.debug(f"在当前目录找到配置文件: {self.current_dir_path}")
            return self.current_dir_path

        if self.user_dir_path.exists():
            logger.debug(f"在用户目录找到配置文件: {self.user_dir_path}")
            return self.user_dir_path

        logger.debug(f"未找到配置文件: {self.config_name}")
        return None

    def find_config_or_raise(self) -> Path:
        """
        查找配置文件，如果不存在则抛出异常

        Returns:
            配置文件路径

        Raises:
            ConfigPathNotFoundError: 配置文件不存在
        """
        config_path = self.find_config()
        if config_path is None:
            raise ConfigPathNotFoundError(
                f"配置文件 '{self.config_name}' 不存在，"
                f"尝试的路径:\n"
                f"  - {self.current_dir_path}\n"
                f"  - {self.user_dir_path}"
            )
        return config_path

    def list_search_paths(self) -> List[Path]:
        """
        列出所有搜索路径（按优先级排序）

        Returns:
            配置文件搜索路径列表
        """
        return [self.current_dir_path, self.user_dir_path]

    def ensure_user_dir(self) -> Path:
        """
        确保用户目录存在，如果不存在则创建

        Returns:
            用户目录路径
        """
        self._user_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"确保用户目录存在: {self._user_dir}")
        return self._user_dir

    def get_user_dir(self) -> Path:
        """
        获取用户目录路径

        Returns:
            用户目录路径
        """
        return self._user_dir

    def create_in_user_dir(self, content: str) -> Path:
        """
        在用户目录中创建配置文件

        Args:
            content: 配置文件内容

        Returns:
            创建的配置文件路径
        """
        self.ensure_user_dir()
        config_path = self.user_dir_path

        with open(config_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"在用户目录创建配置文件: {config_path}")
        return config_path

    def exists_in_current_dir(self) -> bool:
        """
        检查当前目录是否存在配置文件

        Returns:
            是否存在
        """
        return self.current_dir_path.exists()

    def exists_in_user_dir(self) -> bool:
        """
        检查用户目录是否存在配置文件

        Returns:
            是否存在
        """
        return self.user_dir_path.exists()

    def exists_anywhere(self) -> bool:
        """
        检查任意位置是否存在配置文件

        Returns:
            是否存在
        """
        return self.exists_in_current_dir() or self.exists_in_user_dir()


def get_config_path(config_name: str, required: bool = False) -> Optional[Path]:
    """
    便捷函数：获取配置文件路径

    Args:
        config_name: 配置文件名称
        required: 如果为 True，当配置文件不存在时抛出异常

    Returns:
        配置文件路径，如果不存在且 required 为 False 则返回 None

    Raises:
        ConfigPathNotFoundError: required 为 True 且配置文件不存在
    """
    manager = ConfigPathManager(config_name)

    if required:
        return manager.find_config_or_raise()
    else:
        return manager.find_config()


def load_app_config(config_path: Optional[Path] = None) -> AppConfig:
    """加载应用配置

    Args:
        config_path: 配置文件路径，如果为 None 则自动查找

    Returns:
        应用配置对象
    """
    if config_path is None:
        config_path = ConfigPathManager("config.json").find_config()

    if config_path is None or not config_path.exists():
        logger.debug("配置文件不存在，使用默认配置")
        return AppConfig()

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig.from_dict(data)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"加载配置文件失败: {e}，使用默认配置")
        return AppConfig()


def save_app_config(config: AppConfig, config_path: Optional[Path] = None) -> bool:
    """保存应用配置

    Args:
        config: 应用配置对象
        config_path: 配置文件路径，如果为 None 则使用用户目录

    Returns:
        是否保存成功
    """
    if config_path is None:
        manager = ConfigPathManager("config.json")
        manager.ensure_user_dir()
        config_path = manager.user_dir_path

    try:
        # 读取现有配置（如果存在）
        existing_data = {}
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                existing_data = json.load(f)

        # 更新配置
        existing_data["mode"] = config.mode
        if config.channels:
            existing_data["channels"] = {}
            for channel_id, channel_config in config.channels.items():
                channel_data = {
                    "enabled": channel_config.enabled,
                    "groupPolicy": channel_config.group_policy,
                    "groupAllowFrom": channel_config.group_allow_from,
                    "groups": {},
                }
                for group_id, group_config in channel_config.groups.items():
                    group_data = {
                        "name": group_config.name,
                        "requireMention": group_config.require_mention,
                        "ignoreOtherMentions": group_config.ignore_other_mentions,
                        "historyLimit": group_config.history_limit,
                        "toolPolicy": group_config.tool_policy,
                        "prompt": group_config.prompt,
                    }
                    if group_config.allow_from:
                        group_data["allowFrom"] = group_config.allow_from
                    if group_config.block_from:
                        group_data["blockFrom"] = group_config.block_from
                    if group_config.tools_deny:
                        group_data["toolsDeny"] = group_config.tools_deny
                    channel_data["groups"][group_id] = group_data
                existing_data["channels"][channel_id] = channel_data

        # 写入文件
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=2, ensure_ascii=False)

        logger.info(f"配置已保存到: {config_path}")
        return True
    except Exception as e:
        logger.error(f"保存配置失败: {e}")
        return False
