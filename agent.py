"""
智能体管理器
支持自动发现智能体、加载提示词、管理配置
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from config import ConfigPathManager

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """智能体配置类"""

    agent_id: str
    name: str = ""
    description: str = ""
    prompt_file: str = ""

    @classmethod
    def from_dict(cls, agent_id: str, config: Dict[str, Any]) -> "AgentConfig":
        """从字典创建智能体配置"""
        return cls(
            agent_id=agent_id,
            name=config.get("name", agent_id),
            description=config.get("description", ""),
            prompt_file=config.get("prompt_file", f"{agent_id}.md"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        data: Dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "prompt_file": self.prompt_file,
        }
        return data


class AgentManager:
    """智能体管理器"""

    def __init__(
        self,
        agents_dir: Optional[Path] = None,
        config_file: Optional[Path] = None,
    ):
        """
        初始化智能体管理器

        Args:
            agents_dir: 智能体目录，默认为 ./agents 或 ~/.ask-agent/agents
            config_file: 配置文件路径，默认为 ~/.ask-agent/agents.json
        """
        base_dir = Path.cwd()

        # 使用 ConfigPathManager 查找配置文件 - 优先使用当前目录，否则使用用户目录
        if config_file is None:
            config_manager = ConfigPathManager("agents.json")
            found_config = config_manager.find_config()
            if found_config:
                self.config_file = found_config
            else:
                self.config_file = config_manager.user_dir_path
                self.config_file.parent.mkdir(parents=True, exist_ok=True)
        else:
            self.config_file = Path(config_file)

        # 查找 agents 目录：当前目录优先，否则使用用户目录
        if agents_dir is None:
            current_agents_dir = base_dir / "agents"
            user_agents_dir = Path.home() / ".ask-agent" / "agents"

            if current_agents_dir.exists() and any(current_agents_dir.glob("*.md")):
                self.agents_dir = current_agents_dir
                logger.debug(f"使用当前目录的 agents: {self.agents_dir}")
            elif user_agents_dir.exists() and any(user_agents_dir.glob("*.md")):
                self.agents_dir = user_agents_dir
                logger.debug(f"使用用户目录的 agents: {self.agents_dir}")
            else:
                self.agents_dir = current_agents_dir
                logger.debug(f"使用默认的 agents 目录: {self.agents_dir}")
        else:
            self.agents_dir = Path(agents_dir)

        self.agents: Dict[str, AgentConfig] = {}
        self.default_agent: Optional[str] = None
        self.current_agent: Optional[AgentConfig] = None

        # 加载配置和自动发现智能体
        self.load_config()
        self._discover_agents()

    def load_config(self) -> bool:
        """加载智能体配置文件"""
        config_exists = self.config_file.exists()
        if not config_exists:
            logger.info(f"智能体配置文件不存在: {self.config_file}")
            return False

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.default_agent = data.get("default_agent")

            for agent_id, agent_data in data.get("agents", {}).items():
                self.agents[agent_id] = AgentConfig.from_dict(agent_id, agent_data)

            logger.info(f"成功加载 {len(self.agents)} 个智能体配置")

            return True

        except Exception as e:
            logger.error(f"加载智能体配置失败: {e}")
            return False

    def _discover_agents(self):
        """自动发现 agents 目录下的所有 .md 文件并生成配置"""
        discovered_count = 0

        for md_file in self.agents_dir.glob("*.md"):
            agent_id = md_file.stem

            # 如果智能体已存在，跳过
            if agent_id in self.agents:
                continue

            # 创建默认配置
            self.agents[agent_id] = AgentConfig(
                agent_id=agent_id,
                name=agent_id,
                description="",
                prompt_file=f"{agent_id}.md",
            )
            discovered_count += 1

        if discovered_count > 0:
            logger.info(f"自动发现 {discovered_count} 个智能体")
            # 自动保存配置
            self.save_config()

    def save_config(self) -> bool:
        """保存配置文件"""
        try:
            data: Dict[str, Any] = {
                "default_agent": self.default_agent,
                "agents": {},
            }

            for agent_id, agent in self.agents.items():
                data["agents"][agent_id] = agent.to_dict()

            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.info("智能体配置已保存")
            return True

        except Exception as e:
            logger.error(f"保存配置失败: {e}")
            return False

    def get_agent(self, agent_id: str) -> Optional[AgentConfig]:
        """获取智能体配置"""
        return self.agents.get(agent_id)

    def list_agents(self) -> List[AgentConfig]:
        """列出所有智能体"""
        return list(self.agents.values())

    def set_current_agent(self, agent_id: str) -> bool:
        """设置当前智能体"""
        agent = self.get_agent(agent_id)
        if not agent:
            logger.warning(f"智能体不存在: {agent_id}")
            return False

        self.current_agent = agent
        logger.info(f"已切换到智能体: {agent.name}")
        return True

    def get_agent_prompt(self, agent_id: str) -> Optional[str]:
        """获取智能体的系统提示词"""
        agent = self.get_agent(agent_id)
        if not agent:
            return None

        prompt_path = self.agents_dir / agent.prompt_file
        if not prompt_path.exists():
            logger.warning(f"智能体提示词文件不存在: {prompt_path}")
            return None

        try:
            return prompt_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"读取智能体提示词失败: {e}")
            return None

    def set_default_agent(self, agent_id: str) -> bool:
        """设置默认智能体（builtin 是特殊值，表示使用内置提示词）"""
        if agent_id != "builtin" and agent_id not in self.agents:
            logger.warning(f"智能体不存在: {agent_id}")
            return False

        self.default_agent = agent_id
        self.save_config()
        return True

    def reload(self):
        """重新加载配置和智能体"""
        self.agents.clear()
        self.current_agent = None
        self.load_config()
        self._discover_agents()
        logger.info("智能体管理器已重新加载")
