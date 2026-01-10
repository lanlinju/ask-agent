"""
角色扮演管理器
支持自动发现角色、加载提示词、管理历史记录
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RoleConfig:
    """角色配置类"""

    role_id: str
    name: str = ""
    description: str = ""
    prompt_file: str = ""
    history_path: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None

    @classmethod
    def from_dict(cls, role_id: str, config: Dict[str, Any]) -> "RoleConfig":
        """从字典创建角色配置"""
        return cls(
            role_id=role_id,
            name=config.get("name", role_id),
            description=config.get("description", ""),
            prompt_file=config.get("prompt_file", f"{role_id}.md"),
            history_path=config.get("history_path"),
            model=config.get("model"),
            temperature=config.get("temperature"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "prompt_file": self.prompt_file,
        }
        if self.history_path:
            data["history_path"] = self.history_path
        if self.model:
            data["model"] = self.model
        if self.temperature is not None:
            data["temperature"] = self.temperature
        return data


class RoleManager:
    """角色扮演管理器"""

    def __init__(
        self,
        roles_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        config_file: Optional[Path] = None,
    ):
        """
        初始化角色管理器

        Args:
            roles_dir: 角色目录，默认为 ./roles
            cache_dir: 缓存目录，默认为 ./cache
            config_file: 配置文件路径，默认为根目录的 roles.json
        """
        base_dir = Path.cwd()
        self.roles_dir = roles_dir or (base_dir / "roles")
        self.config_file = config_file or (base_dir / "roles.json")
        self.cache_dir = cache_dir or (base_dir / "cache")
        self.role_cache_dir = self.cache_dir / "role"
        self.role_cache_dir.mkdir(parents=True, exist_ok=True)

        self.roles: Dict[str, RoleConfig] = {}
        self.default_role: Optional[str] = None
        self.current_role: Optional[RoleConfig] = None

        # 确保目录存在
        self.roles_dir.mkdir(parents=True, exist_ok=True)

        # 加载配置和自动发现角色
        self.load_config()
        self._discover_roles()

        # 如果默认角色为空且有角色存在，设置为第一个角色
        if not self.default_role and self.roles:
            first_role_id = list(self.roles.keys())[0]
            self.default_role = first_role_id
            logger.info(f"默认角色设置为: {first_role_id}")
            # 自动保存配置
            self.save_config()

    def generate_session_type(self, role_id: str) -> str:
        """
        生成角色会话类型（用于 SessionManager）

        Args:
            role_id: 角色 ID

        Returns:
            会话类型字符串，如 "role_frieren"
        """
        return f"role_{role_id}"

    def get_history_dir(self, role_id: str) -> Path:
        """
        获取角色的历史记录目录

        Args:
            role_id: 角色 ID

        Returns:
            历史记录目录路径
        """
        role = self.get_role(role_id)
        if role and role.history_path:
            # 使用自定义路径
            history_dir = Path(role.history_path)
            if not history_dir.is_absolute():
                history_dir = self.cache_dir / history_dir
        else:
            # 使用默认路径 cache/role/<role_id>
            history_dir = self.role_cache_dir / role_id

        history_dir.mkdir(parents=True, exist_ok=True)
        return history_dir

    def load_config(self) -> bool:
        """加载角色配置文件"""
        config_exists = self.config_file.exists()
        if not config_exists:
            logger.info(f"角色配置文件不存在: {self.config_file}")
            return False

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.default_role = data.get("default_role")

            for role_id, role_data in data.get("roles", {}).items():
                self.roles[role_id] = RoleConfig.from_dict(role_id, role_data)

            logger.info(f"成功加载 {len(self.roles)} 个角色配置")

            # 如果默认角色为空且有角色存在，设置为第一个角色
            if not self.default_role and self.roles:
                first_role_id = list(self.roles.keys())[0]
                self.default_role = first_role_id
                logger.info(f"默认角色设置为: {first_role_id}")
                # 自动保存配置
                self.save_config()

            return True

        except Exception as e:
            logger.error(f"加载角色配置失败: {e}")
            return False

    def _discover_roles(self):
        """自动发现 roles 目录下的所有 .md 文件并生成配置"""
        discovered_count = 0

        for md_file in self.roles_dir.glob("*.md"):
            role_id = md_file.stem

            # 如果角色已存在，跳过
            if role_id in self.roles:
                continue

            # 创建默认配置
            self.roles[role_id] = RoleConfig(
                role_id=role_id,
                name=role_id,
                description="",
                prompt_file=f"{role_id}.md",
                history_path=None,
            )
            discovered_count += 1

        if discovered_count > 0:
            logger.info(f"自动发现 {discovered_count} 个角色")
            # 自动保存配置
            self.save_config()

    def save_config(self) -> bool:
        """保存配置文件"""
        try:
            data: Dict[str, Any] = {
                "default_role": self.default_role,
                "roles": {},
            }

            for role_id, role in self.roles.items():
                data["roles"][role_id] = role.to_dict()

            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("角色配置已保存")
            return True

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    def get_role(self, role_id: str) -> Optional[RoleConfig]:
        """获取角色配置"""
        return self.roles.get(role_id)

    def list_roles(self) -> List[RoleConfig]:
        """列出所有角色"""
        return list(self.roles.values())

    def set_current_role(self, role_id: str) -> bool:
        """设置当前角色"""
        role = self.get_role(role_id)
        if not role:
            logger.warning(f"角色不存在: {role_id}")
            return False

        self.current_role = role
        logger.info(f"已切换到角色: {role.name}")
        return True

    def get_role_prompt(self, role_id: str) -> Optional[str]:
        """获取角色的系统提示词"""
        role = self.get_role(role_id)
        if not role:
            return None

        prompt_path = self.roles_dir / role.prompt_file
        if not prompt_path.exists():
            logger.warning(f"角色提示词文件不存在: {prompt_path}")
            return None

        try:
            return prompt_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取角色提示词失败: {e}")
            return None

    def set_default_role(self, role_id: str) -> bool:
        """设置默认角色"""
        if role_id not in self.roles:
            logger.warning(f"角色不存在: {role_id}")
            return False

        self.default_role = role_id
        self.save_config()
        return True

    def reload(self):
        """重新加载配置和角色"""
        self.roles.clear()
        self.current_role = None
        self.load_config()
        self._discover_roles()
        logger.info("角色管理器已重新加载")
