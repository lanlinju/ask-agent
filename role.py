"""
角色扮演管理器
支持自动发现角色、加载提示词、管理历史记录
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any
from dataclasses import dataclass, field

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
    _is_custom: bool = field(default=False, repr=False)

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
            _is_custom=True,
        )


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
        self.role_cache_dir = self.cache_dir
        self.role_cache_dir.mkdir(parents=True, exist_ok=True)

        self.roles: Dict[str, RoleConfig] = {}
        self.default_role: Optional[str] = None
        self.current_role: Optional[RoleConfig] = None

        # 确保目录存在
        self.roles_dir.mkdir(parents=True, exist_ok=True)

        # 加载配置和自动发现角色
        self.load_config()
        self._discover_roles()

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
        if not self.config_file.exists():
            logger.info(f"角色配置文件不存在: {self.config_file}")
            self._create_default_config()
            return False

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.default_role = data.get("default_role")

            for role_id, role_data in data.get("roles", {}).items():
                self.roles[role_id] = RoleConfig.from_dict(role_id, role_data)

            logger.info(f"成功加载 {len(self.roles)} 个角色配置")
            return True

        except Exception as e:
            logger.error(f"加载角色配置失败: {e}")
            return False

    def _discover_roles(self):
        """自动发现 roles 目录下的所有 .md 文件"""
        discovered_count = 0

        for md_file in self.roles_dir.glob("*.md"):
            role_id = md_file.stem

            # 如果角色已存在且是自定义配置，跳过
            if role_id in self.roles and self.roles[role_id]._is_custom:
                continue

            # 创建默认配置
            self.roles[role_id] = RoleConfig(
                role_id=role_id,
                name=role_id,
                description="",
                prompt_file=f"{role_id}.md",
                history_path=None,
                _is_custom=False,
            )
            discovered_count += 1

        if discovered_count > 0:
            logger.info(f"自动发现 {discovered_count} 个角色")

    def _create_default_config(self):
        """创建默认配置文件和示例角色"""
        default_config = {
            "default_role": "frieren",
            "roles": {
                "frieren": {
                    "name": "芙莉莲",
                    "description": "千年魔法使，情感淡漠但温柔",
                    "prompt_file": "frieren.md",
                    "model": "",
                    "temperature": 0.8,
                },
                "nahida": {
                    "name": "纳西妲",
                    "description": "智慧之神，充满好奇与童真",
                    "prompt_file": "nahida.md",
                },
            },
        }

        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(default_config, f, ensure_ascii=False, indent=2)
            logger.info(f"已创建默认配置文件: {self.config_file}")

            # 创建示例角色文件
            self._create_example_roles()

        except Exception as e:
            logger.error(f"创建默认配置失败: {e}")

    def _create_example_roles(self):
        """创建示例角色提示词文件"""
        frieren_prompt = """你是芙莉莲（Frieren），一位活了超过一千年的精灵魔法使。

## 角色设定
- 外表是银发紫眸的少女精灵，实际年龄超过一千年
- 曾是勇者欣梅尔队伍的成员，完成了讨伐魔王的十年旅程
- 性格淡漠、不善表达情感，但内心温柔且重视同伴
- 对魔法有着极度的热情和执着，喜欢收集各种魔法
- 由于寿命悠长，对时间的感知与人类不同

## 对话风格
- 语气平淡、简洁，少有情绪波动
- 偶尔会说出让人惊讶的冷漠言论，但并非恶意
- 谈到魔法时会变得更有活力
- 会回忆起过去的旅程和同伴
- 对人类的情感和时间观念感到困惑，但在努力理解

## 注意事项
- 保持角色一致性，不要跳戏
- 回答要符合角色的世界观和价值观
- 可以适当引用原作中的经典场景或台词"""

        nahida_prompt = """你是纳西妲（Nahida），提瓦特大陆须弥的草神，智慧之神。

## 角色设定
- 外表是白发绿眸的小女孩，实际是掌管智慧的神明
- 被困在净善宫五百年，对外面的世界充满好奇
- 拥有强大的知识和智慧，但缺乏实际生活经验
- 性格善良、温柔、充满童真，喜欢学习新事物
- 能够进入梦境，连接「世界树」获取知识

## 对话风格
- 语气温柔、友善，带有孩童般的好奇心
- 喜欢用简单的比喻解释复杂的事物
- 会提出很多问题，想要了解一切
- 说话时偶尔会用"诶嘿"等可爱的语气词
- 面对困难时会展现出神明的智慧和成熟

## 注意事项
- 保持童真与智慧的平衡
- 对世界充满好奇，但不失神明的威严
- 善于倾听，给予温柔的建议
- 可以适当提及须弥、世界树等相关设定"""

        try:
            frieren_file = self.roles_dir / "frieren.md"
            if not frieren_file.exists():
                frieren_file.write_text(frieren_prompt, encoding="utf-8")
                logger.info(f"已创建示例角色文件: {frieren_file}")

            nahida_file = self.roles_dir / "nahida.md"
            if not nahida_file.exists():
                nahida_file.write_text(nahida_prompt, encoding="utf-8")
                logger.info(f"已创建示例角色文件: {nahida_file}")

        except Exception as e:
            logger.error(f"创建示例角色文件失败: {e}")

    def save_config(self) -> bool:
        """保存配置文件（只保存自定义配置）"""
        try:
            data = {
                "default_role": self.default_role,
                "roles": {},
            }

            for role_id, role in self.roles.items():
                if role._is_custom:
                    role_data: Dict[str, Any] = {
                        "name": role.name,
                        "description": role.description,
                        "prompt_file": role.prompt_file,
                    }
                    if role.history_path:
                        role_data["history_path"] = role.history_path
                    if role.model:
                        role_data["model"] = role.model
                    if role.temperature is not None:
                        role_data["temperature"] = role.temperature

                    data["roles"][role_id] = role_data

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
