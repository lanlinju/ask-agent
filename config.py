"""
配置文件路径管理器
支持当前目录和用户目录 (~/.ask-agent) 两种配置文件位置
"""

import logging
from pathlib import Path
from typing import List, Optional, Union

logger = logging.getLogger(__name__)


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
