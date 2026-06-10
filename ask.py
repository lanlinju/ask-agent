#!/usr/bin/env python3

import sys
import os
import re
import requests
from requests.exceptions import RequestException
import json
from typing import List, Dict, Any
import argparse
import subprocess
import logging
import time
import random
import atexit
import shutil
import asyncio
import base64
from dotenv import load_dotenv
from pathlib import Path
import platform
import threading
from core.mcp import MCPManager
from core.provider import ProviderConfig
from core.session import SessionManager
from core.role import RoleManager
from core.agent import AgentManager
from core.command import CommandManager
from core.memory import MemoryManager, MEMORY_GUIDANCE
from typing import Optional
from core.config import ConfigPathManager, get_config_path, load_app_config, AppConfig, ChannelConfig
from core.proactive import ProactiveScheduler
from util import YELLOW, GREEN, RESET, BLUE, format_range_info, format_diff, read_file, write_file
from util.background import BackgroundManager, drain_background_notifications
from util.hooks import HookManager, HookEvent, HookInput
from util.agent_team import TeammateManager
from core.telegram_group import TelegramGroupManager, is_bot_mentioned, has_other_mentions
from util.message_buffer import MessageBufferManager, buffer_manager
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.history import InMemoryHistory

_ENV_PATH_MANAGER = ConfigPathManager(".env")
_env_path = _ENV_PATH_MANAGER.find_config()
load_dotenv(dotenv_path=_env_path, override=True)

# 指数退避配置
BACKOFF_BASE_DELAY = 1.0  # 基础延迟（秒）
BACKOFF_MAX_DELAY = 128   # 最大延迟（秒）
MAX_RETRIES = 3           # 最大重试次数

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if sys.platform != "win32":
    import readline

_INPUT_HISTORY = InMemoryHistory()
_PROMPT_SESSION = PromptSession(history=_INPUT_HISTORY)


# Ask Agent

# 配置API参数
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QQ_APP_ID = os.getenv("QQ_APP_ID")
QQ_APP_SECRET = os.getenv("QQ_APP_SECRET")
WORKDIR = Path.cwd()

# 系统问答工具助手提示词
SYSTEM_PROMPT_ASK = """你是一个终端问答工具助手。你的特点是：
1. 回答简洁、直接、高效
2. 对命令行、脚本、系统等技术问题有专长
3. 提供的代码和命令可以直接在终端中使用
4. 避免冗长的解释，用户更在乎可用的答案
5. 如果是多行的输出或代码，用清晰的格式展示"""
# 系统翻译提示词
SYSTEM_PROMPT_TRANSLATE = """你是一个专业的终端英语翻译工具。严格遵守以下规则：
1. 将英语句子翻译成中文
2. 将中文句子翻译成英语
3. 英语单词，翻译时给出音标，给出常见释义
4. 缩写词翻译时给出全称
5. 你只执行翻译相关任务，不回答非翻译问题，不提供额外解释、建议或对话。

[英语单词翻译输出格式]
单词 [英] [美]
词性. 释义

[单词示例]
computer [kəmˈpjuːtə(r)] [kəmˈpjuːtər]
n. 计算机，电脑
"""
# Agent Type Registry - The core of subagent mechanism
AGENT_TYPES = {
    "explore": {
        "description": "Read-only agent for exploring code, finding files, searching",
        "tools": ["bash", "read_file", "glob", "grep"],  # No write access
        "prompt": "You are an exploration agent. Search and analyze, but never modify files. Return a concise summary.",
    },
    "code": {
        "description": "Full agent for implementing features and fixing bugs",
        "tools": "*",  # All tools
        "prompt": "You are a coding agent. Implement the requested changes efficiently.",
    },
    "plan": {
        "description": "Planning agent for designing implementation strategies",
        "tools": ["bash", "read_file", "glob", "grep"],  # Read-only
        "prompt": "You are a planning agent. Analyze the codebase and output a numbered implementation plan. Do NOT make changes.",
    },
}


class SkillLoader:
    """
    Loads and manages skills from SKILL.md files.

    A skill is a FOLDER containing:
    - SKILL.md (required): YAML frontmatter + markdown instructions
    - scripts/ (optional): Helper scripts the model can run
    - references/ (optional): Additional documentation
    - assets/ (optional): Templates, files for output
    """

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills = {}
        self.load_skills()

    def parse_skill_md(self, path: Path) -> dict:
        """
        Parse a SKILL.md file into metadata and body.

        Returns dict with: name, description, body, path, dir
        Returns None if file doesn't match format.
        """
        content = read_file(path)

        # Match YAML frontmatter between --- markers
        match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
        if not match:
            return None

        frontmatter, body = match.groups()

        # Parse YAML-like frontmatter (simple key: value)
        metadata = {}
        for line in frontmatter.strip().split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip().strip("\"'")

        # Require name and description
        if "name" not in metadata or "description" not in metadata:
            return None

        return {
            "name": metadata["name"],
            "description": metadata["description"],
            "body": body.strip(),
            "path": path,
            "dir": path.parent,
        }

    def load_skills(self):
        """Scan skills directory and load all valid SKILL.md files."""
        if not self.skills_dir.exists():
            return

        for skill_dir in self.skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue

            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            skill = self.parse_skill_md(skill_md)
            if skill:
                self.skills[skill["name"]] = skill

    def get_descriptions(self) -> str:
        """Generate skill descriptions for system prompt."""
        if not self.skills:
            return "(no skills available)"

        return "\n".join(
            f"- {name}: {skill['description']}" for name, skill in self.skills.items()
        )

    def get_skill_content(self, name: str) -> str:
        """Get full skill content for injection."""
        if name not in self.skills:
            return None

        skill = self.skills[name]
        content = f"# Skill: {skill['name']}\n\n{skill['body']}"

        # List available resources (Layer 3 hints)
        resources = []
        for folder, label in [
            ("scripts", "Scripts"),
            ("references", "References"),
            ("assets", "Assets"),
        ]:
            folder_path = skill["dir"] / folder
            if folder_path.exists():
                files = list(folder_path.glob("*"))
                if files:
                    resources.append(
                        f"{label}: {', '.join(str(f.relative_to(skill['dir'])) for f in files)}"
                    )

        if resources:
            content += f"\n\n**Available resources in {skill['dir']}:**\n"
            content += "\n".join(f"- {r}" for r in resources)

        return content

    def list_skills(self) -> list:
        """Return list of available skill names."""
        return list(self.skills.keys())


# Global skill loader instance
SKILLS_DIR = WORKDIR / "skills"
SKILLS = SkillLoader(SKILLS_DIR)


def list_skills():
    """列出所有可用的 Skills"""
    skills = SKILLS.list_skills()
    if not skills:
        print("\n📭 暂无可用 Skills")
        print("💡 提示: 在 skills/ 目录下创建包含 SKILL.md 的文件夹即可添加 Skill\n")
        return

    print("\n📋 可用 Skills:\n")
    for name, skill in SKILLS.skills.items():
        desc = skill.get("description", "")
        print(f"  • {name}")
        if desc:
            print(f"    {desc}")
    print()


# Global config data (from config.json, centralized)
_CONFIG_DATA: Optional[Dict[str, Any]] = None
_CONFIG_PATH = ConfigPathManager("config.json").find_config()
if _CONFIG_PATH:
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            _CONFIG_DATA = json.load(f)
    except Exception:
        pass

# Global MCP manager instance
if _CONFIG_DATA and "mcp" in _CONFIG_DATA:
    MCP_MANAGER = MCPManager(data=_CONFIG_DATA["mcp"].get("servers"))
else:
    MCP_MANAGER = MCPManager()
MEMORY_MANAGER = MemoryManager()

# Global hook manager instance
HOOK_MANAGER = HookManager(workdir=WORKDIR)

# Config path managers
_COMMAND_PATH_MANAGER = ConfigPathManager("command.json")

# Global provider config instance
if _CONFIG_DATA and "provider" in _CONFIG_DATA:
    PROVIDER_CONFIG = ProviderConfig(data=_CONFIG_DATA["provider"])
else:
    _PROVIDERS_PATH_MANAGER = ConfigPathManager("providers.json")
    PROVIDERS_PATH = _PROVIDERS_PATH_MANAGER.find_config()
    PROVIDER_CONFIG = ProviderConfig(PROVIDERS_PATH if PROVIDERS_PATH else "providers.json")

# Global config file path - always use user directory
_CONFIG_PATH_MANAGER = ConfigPathManager("config.json")
CONFIG_FILE = _CONFIG_PATH_MANAGER.user_dir_path
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Global app config instance
APP_CONFIG: Optional[AppConfig] = None

# Global Telegram group manager instance (lazy init)
_TELEGRAM_GROUP_MANAGER: Optional[TelegramGroupManager] = None

# Global cache directory - always use user directory
CACHE_DIR = Path.home() / ".ask-agent" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Global background manager instance
RUNTIME_DIR = CACHE_DIR / "runtime-tasks"
BG_MANAGER = BackgroundManager(RUNTIME_DIR, workdir=WORKDIR)

# Global agent team manager instance (lazy init)
TEAM_MANAGER: Optional[TeammateManager] = None
AGENT_TEAM_ENABLED = False


def _cleanup_team_dir():
    """Remove .team directory on program exit."""
    if not AGENT_TEAM_ENABLED or not TEAM_MANAGER:
        return
    team_dir = WORKDIR / ".team"
    if team_dir.exists():
        try:
            shutil.rmtree(team_dir)
        except Exception:
            pass


atexit.register(_cleanup_team_dir)

# Global session manager instance - will be initialized with the current mode
SESSION_MANAGER: Optional[SessionManager] = None

# Global role manager instance
ROLE_MANAGER: Optional[RoleManager] = None

# Global agent manager instance
AGENT_MANAGER: Optional[AgentManager] = None

# Command manager globals
COMMAND_DIR = WORKDIR / "command"
COMMAND_CONFIG = _COMMAND_PATH_MANAGER.find_config() or (WORKDIR / "command.json")
COMMAND_MANAGER: Optional[CommandManager] = None


def init_role_manager() -> RoleManager:
    """初始化角色管理器"""
    global ROLE_MANAGER
    if not ROLE_MANAGER:
        config_data = _CONFIG_DATA.get("role") if _CONFIG_DATA and "role" in _CONFIG_DATA else None
        ROLE_MANAGER = RoleManager(cache_dir=CACHE_DIR, config_data=config_data)
    return ROLE_MANAGER


def init_agent_manager() -> AgentManager:
    """初始化智能体管理器"""
    global AGENT_MANAGER
    if not AGENT_MANAGER:
        config_data = _CONFIG_DATA.get("agent") if _CONFIG_DATA and "agent" in _CONFIG_DATA else None
        AGENT_MANAGER = AgentManager(config_data=config_data)
    return AGENT_MANAGER


def get_current_role_id() -> Optional[str]:
    """获取当前角色 ID"""
    if not ROLE_MANAGER:
        return None
    return ROLE_MANAGER.current_role.role_id if ROLE_MANAGER.current_role else None


def get_current_agent_id() -> Optional[str]:
    """获取当前智能体 ID"""
    if not AGENT_MANAGER:
        return None
    return AGENT_MANAGER.current_agent.agent_id if AGENT_MANAGER.current_agent else None


def get_role_name(role_id: str) -> str:
    """获取角色名称"""
    if not ROLE_MANAGER:
        return role_id

    role = ROLE_MANAGER.get_role(role_id)
    name = role.name if role else role_id
    result = name if name else role_id
    # 仅对英文名称首字母大写
    if result and result[0].isalpha() and result[0].isascii():
        result = result.capitalize()
    return f"{GREEN}{result}{RESET}"


def get_agent_name(agent_id: str) -> str:
    """获取智能体名称"""
    if not AGENT_MANAGER:
        return agent_id

    agent = AGENT_MANAGER.get_agent(agent_id)
    name = agent.name if agent else agent_id
    result = name if name else agent_id
    # 仅对英文名称首字母大写
    if result and result[0].isalpha() and result[0].isascii():
        result = result.capitalize()
    return f"{GREEN}{result}{RESET}"


def list_roles() -> List[Dict]:
    """列出所有可用角色"""
    init_role_manager()
    assert ROLE_MANAGER is not None

    roles_list = []
    for role in ROLE_MANAGER.list_roles():
        roles_list.append(
            {
                "id": role.role_id,
                "name": role.name,
                "description": role.description,
            }
        )
    return roles_list


def _display_roles(roles: List[Dict], show_current_marker: bool = False):
    """显示角色列表

    Args:
        roles: 角色列表
        show_current_marker: 是否显示当前角色标记
    """
    current_role_id_val = get_current_role_id()
    print("\n📋 可用角色:\n")
    for i, role in enumerate(roles, 1):
        if show_current_marker:
            marker = "→ " if role["id"] == current_role_id_val else "  "
            print(f"{marker}[{i}] {role['name']} ({role['id']})")
            print(f"     {role['description']}\n")
        else:
            print(f"  [{i}] {role['name']} ({role['id']})")
            print(f"      {role['description']}\n")


def _select_role_interactive(roles: List[Dict], save_session: bool = True) -> bool:
    """交互式选择角色

    Args:
        roles: 角色列表
        save_session: 是否保存当前会话

    Returns:
        是否成功选择了角色
    """
    try:
        choice = input("请选择角色 (编号 或 0/Enter 取消): ").strip()
        if choice == "0" or choice == "":
            print("已取消\n")
            return False

        index = int(choice) - 1
        if 0 <= index < len(roles):
            role_id = roles[index]["id"]
            if save_session:
                save_current_session()
            ROLE_MANAGER.set_current_role(role_id)
            current_mode = ROLE
            ROLE_MANAGER.set_default_role(role_id)
            init_system_prompt(current_mode, role_id)
            _save_section_to_config()
            print(f"✅ 已切换到角色: {get_role_name(role_id)}\n")
            return True
        else:
            print("❌ 无效的编号\n")
    except ValueError:
        print("❌ 请输入有效的数字\n")
    except KeyboardInterrupt:
        print("\n已取消\n")
    return False


def _apply_role(role_id: str):
    """应用角色设置（内部函数）"""
    global current_mode
    current_mode = ROLE
    init_system_prompt(current_mode, role_id)
    print(f"✅ 已进入角色扮演模式: {get_role_name(role_id)}\n")


def _save_section_to_config():
    """将 ROLE_MANAGER/AGENT_MANAGER 状态同步回 _CONFIG_DATA 并保存 config.json
    （仅当配置来源是 config.json 时有效）
    """
    global _CONFIG_DATA, _CONFIG_PATH, ROLE_MANAGER, AGENT_MANAGER
    if not _CONFIG_DATA or not _CONFIG_PATH:
        return

    if "role" in _CONFIG_DATA and ROLE_MANAGER:
        _CONFIG_DATA["role"]["default_role"] = ROLE_MANAGER.default_role
        _CONFIG_DATA["role"]["roles"] = {
            rid: role.to_dict() for rid, role in ROLE_MANAGER.roles.items()
        }

    if "agent" in _CONFIG_DATA and AGENT_MANAGER:
        _CONFIG_DATA["agent"]["default_agent"] = AGENT_MANAGER.default_agent

    if "provider" in _CONFIG_DATA:
        _CONFIG_DATA["provider"]["model"] = PROVIDER_CONFIG.default_model

    try:
        with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(_CONFIG_DATA, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"保存 config.json 失败: {e}")


def switch_role(role_id: str, save_session: bool = True) -> bool:
    """切换到指定角色"""
    init_role_manager()
    assert ROLE_MANAGER is not None

    if save_session:
        save_current_session()

    if not ROLE_MANAGER.switch_to(role_id):
        print(f"❌ 未找到角色: {role_id}\n")
        return False

    _apply_role(role_id)
    _save_section_to_config()
    return True


def enter_role_mode():
    """进入角色模式，使用默认角色"""
    save_current_session()
    init_role_manager()
    assert ROLE_MANAGER is not None

    default_role = ROLE_MANAGER.get_current_or_default()

    # 默认角色存在，直接进入
    if default_role and ROLE_MANAGER.get_role(default_role):
        ROLE_MANAGER.set_current_role(default_role)
        _apply_role(default_role)
        return

    # 默认角色不存在，让用户选择
    roles = list_roles()
    if not roles:
        print("📭 暂无可用角色\n")
        print(
            "💡 提示: 在 roles/ 目录或 ~/.ask-agent/roles/ 下创建 .md 文件即可添加角色\n"
        )
        return
    _display_roles(roles, show_current_marker=False)
    _select_role_interactive(roles, save_session=False)


def list_roles_interactive():
    """交互式列出并选择角色"""
    if current_mode != ROLE:
        print("❌ 请先进入角色扮演模式: /role\n")
        return

    roles = list_roles()
    if not roles:
        print("📭 暂无可用角色\n")
        return
    _display_roles(roles, show_current_marker=True)
    _select_role_interactive(roles, save_session=True)


def list_agents() -> List[Dict]:
    """列出所有可用智能体（包含builtin）"""
    init_agent_manager()
    assert AGENT_MANAGER is not None

    # 先添加 builtin 选项
    agents_list = [
        {
            "id": "builtin",
            "name": "builtin",
            "description": "内置智能体，使用系统默认提示词",
        }
    ]

    # 添加其他智能体
    for agent in AGENT_MANAGER.list_agents():
        agents_list.append(
            {
                "id": agent.agent_id,
                "name": agent.name,
                "description": agent.description,
            }
        )
    return agents_list


def _display_agents(agents: List[Dict], show_current_marker: bool = False):
    """显示智能体列表

    Args:
        agents: 智能体列表
        show_current_marker: 是否显示当前智能体标记
    """
    current_agent_id_val = get_current_agent_id()
    if not current_agent_id_val:
        # 如果没有当前智能体，检查默认智能体
        init_agent_manager()
        if AGENT_MANAGER and AGENT_MANAGER.default_agent:
            current_agent_id_val = AGENT_MANAGER.default_agent
        else:
            current_agent_id_val = "builtin"

    print("\n📋 可用智能体:\n")
    for i, agent in enumerate(agents, 1):
        if show_current_marker:
            marker = "→ " if agent["id"] == current_agent_id_val else "  "
            print(f"{marker}[{i}] {agent['name']} ({agent['id']})")
            print(f"     {agent['description']}\n")
        else:
            print(f"  [{i}] {agent['name']} ({agent['id']})")
            print(f"      {agent['description']}\n")


def _select_agent_interactive(agents: List[Dict], save_session: bool = True) -> bool:
    """交互式选择智能体

    Args:
        agents: 智能体列表
        save_session: 是否保存当前会话

    Returns:
        是否成功选择了智能体
    """
    init_agent_manager()
    assert AGENT_MANAGER is not None

    try:
        choice = input("请选择智能体 (编号 或 0/Enter 取消): ").strip()
        if choice == "0" or choice == "":
            print("已取消\n")
            return False

        index = int(choice) - 1
        if 0 <= index < len(agents):
            agent_id = agents[index]["id"]
            if save_session:
                save_current_session()

            # builtin 是特殊值，使用内置提示词
            if agent_id == "builtin":
                AGENT_MANAGER.current_agent = None
                AGENT_MANAGER.set_default_agent("builtin")
                init_system_prompt(AGENT)
                print("✅ 已切换到内置智能体\n")
            else:
                AGENT_MANAGER.set_current_agent(agent_id)
                AGENT_MANAGER.set_default_agent(agent_id)
                init_system_prompt(AGENT, agent_id)
                print(f"✅ 已切换到智能体: {get_agent_name(agent_id)}\n")
            _save_section_to_config()
            return True
        else:
            print("❌ 无效的编号\n")
    except ValueError:
        print("❌ 请输入有效的数字\n")
    except KeyboardInterrupt:
        print("\n已取消\n")
    return False


def _apply_agent(agent_id: str):
    """应用智能体设置（内部函数）"""
    global current_mode
    current_mode = AGENT
    if AGENT_MANAGER.is_builtin(agent_id):
        init_system_prompt(current_mode)
        print("✅ 已进入智能体模式 (builtin)\n")
    else:
        init_system_prompt(current_mode, agent_id)
        print(f"✅ 已进入智能体模式: {get_agent_name(agent_id)}\n")


def switch_agent(agent_id: str, save_session: bool = True) -> bool:
    """切换到指定智能体"""
    init_agent_manager()
    assert AGENT_MANAGER is not None

    if save_session:
        save_current_session()

    if not AGENT_MANAGER.switch_to(agent_id):
        print(f"❌ 未找到智能体: {agent_id}\n")
        return False

    _apply_agent(agent_id)
    _save_section_to_config()
    return True


def enter_agent_mode():
    """进入智能体模式，使用默认智能体"""
    save_current_session()
    init_agent_manager()
    assert AGENT_MANAGER is not None

    default_agent = AGENT_MANAGER.get_current_or_default()

    # 默认智能体不存在，让用户选择
    if not AGENT_MANAGER.is_builtin(default_agent) and not AGENT_MANAGER.get_agent(
        default_agent
    ):
        agents = list_agents()
        _display_agents(agents, show_current_marker=False)
        _select_agent_interactive(agents, save_session=False)
        return

    _apply_agent(default_agent)


def list_agents_interactive():
    """交互式列出并选择智能体"""
    if current_mode != AGENT:
        print("❌ 请先进入智能体模式: /agent\n")
        return

    agents = list_agents()
    _display_agents(agents, show_current_marker=True)
    _select_agent_interactive(agents, save_session=True)


def _print_prompt():
    """打印当前提示符"""
    current_role_id_val = get_current_role_id()
    if current_mode == ROLE and current_role_id_val:
        role_name = get_role_name(current_role_id_val)
        print(f"{role_name} ({model_prompt}): ", flush=True)
    else:
        print(f"\n🤖 Assistant ({model_prompt}): ", flush=True)


def _print_newline():
    """打印响应后的分隔符"""
    if current_mode == ROLE:
        print()
    else:
        print("\n")


def init_app_config() -> AppConfig:
    """初始化应用配置"""
    global APP_CONFIG
    if APP_CONFIG is None:
        APP_CONFIG = load_app_config()
        logger.info(f"应用配置已加载，频道: {list(APP_CONFIG.channels.keys())}")
    return APP_CONFIG


def init_proactive_scheduler() -> Optional[ProactiveScheduler]:
    """初始化主动消息调度器"""
    global _PROACTIVE_SCHEDULER
    if _PROACTIVE_SCHEDULER is not None:
        return _PROACTIVE_SCHEDULER

    config = init_app_config()
    proactive_config = config.proactive_message
    if not proactive_config.enabled:
        logger.info("主动消息未启用，跳过初始化")
        return None

    _PROACTIVE_SCHEDULER = ProactiveScheduler(
        config=proactive_config,
        llm_caller=get_streaming_response,
        messages_getter=lambda: messages,
    )

    logger.info("主动消息调度器已初始化")
    return _PROACTIVE_SCHEDULER


def get_telegram_group_manager() -> TelegramGroupManager:
    """获取 Telegram 群组管理器（懒加载）"""
    global _TELEGRAM_GROUP_MANAGER
    if _TELEGRAM_GROUP_MANAGER is None:
        config = init_app_config()
        telegram_config = config.get_channel_config("telegram")
        _TELEGRAM_GROUP_MANAGER = TelegramGroupManager(telegram_config, CACHE_DIR)
    return _TELEGRAM_GROUP_MANAGER


def init_providers() -> bool:
    """初始化 Provider 配置"""
    if not PROVIDER_CONFIG.load():
        logger.warning("Provider 配置加载失败，使用默认配置")
        # 确保使用默认的 DEEPSEEK_MODEL 作为 fallback
        update_model_prompt()
        return False

    # 设置默认模型
    global DEEPSEEK_MODEL, DEEPSEEK_API_URL, DEEPSEEK_API_KEY

    if PROVIDER_CONFIG.default_model:
        api_config = PROVIDER_CONFIG.get_api_config(PROVIDER_CONFIG.default_model)
        if api_config:
            DEEPSEEK_API_URL = api_config["base_url"].rstrip("/v1")
            DEEPSEEK_API_KEY = api_config["api_key"]
            DEEPSEEK_MODEL = api_config["model"]
            logger.info(f"使用默认模型: {PROVIDER_CONFIG.default_model}")

    update_model_prompt()
    return True


def init_command_manager() -> CommandManager:
    """初始化命令管理器"""
    global COMMAND_MANAGER
    if not COMMAND_MANAGER:
        COMMAND_MANAGER = CommandManager(COMMAND_DIR, COMMAND_CONFIG)
    return COMMAND_MANAGER


def init_team_manager() -> TeammateManager:
    """初始化 Agent Team 管理器"""
    global TEAM_MANAGER
    if not TEAM_MANAGER:
        team_dir = WORKDIR / ".team"
        TEAM_MANAGER = TeammateManager(
            team_dir=team_dir,
            llm_caller=lambda msgs, tools, silent: get_streaming_response(msgs, tools, silent),
            tool_executor=execute_tool,
        )
    return TEAM_MANAGER


def list_custom_commands():
    """列出所有自定义命令"""
    init_command_manager()

    commands = COMMAND_MANAGER.list_commands()
    if not commands:
        print("\n📭 暂无自定义命令")
        print("💡 提示: 在 command/ 目录创建 .md 文件或编辑 command.json 添加命令\n")
        return

    print("\n📋 自定义命令:\n")
    for cmd in sorted(commands, key=lambda x: x.name):
        print(f"  /{cmd.name}")
        if cmd.description:
            print(f"     {cmd.description}")
        print()


def handle_custom_command(cmd_name: str, full_command: str):
    """处理自定义命令"""
    cmd = COMMAND_MANAGER.get_command(cmd_name)
    if not cmd:
        return

    print(f"🚀 执行命令: /{cmd_name}")
    if cmd.description:
        print(f"   {cmd.description}")

    _print_prompt()
    agent(cmd.template)
    _print_newline()


def switch_model(model_id: str):
    """切换模型"""
    api_config = PROVIDER_CONFIG.get_api_config(model_id)
    if not api_config:
        print(f"❌ 未找到模型: {model_id}\n")
        return

    global DEEPSEEK_MODEL, DEEPSEEK_API_URL, DEEPSEEK_API_KEY

    DEEPSEEK_API_URL = api_config["base_url"].rstrip("/v1")
    DEEPSEEK_API_KEY = api_config["api_key"]
    DEEPSEEK_MODEL = api_config["model"]

    model_info = PROVIDER_CONFIG.get_model_info(model_id)
    print(f"✅ 已切换到模型: {model_info.name} ({model_info.provider_id})\n")

    PROVIDER_CONFIG.default_model = model_id

    # 保存到独立的 providers.json（仅当来源不是 config.json 时）
    if not (_CONFIG_DATA and "provider" in _CONFIG_DATA):
        PROVIDER_CONFIG.save()

    # 同步回 config.json（如果来源是 config.json）
    _save_section_to_config()

    update_model_prompt()


def list_models() -> list:
    """列出可用模型并返回模型ID列表（不打印）"""
    model_list = []
    for provider in PROVIDER_CONFIG.list_enabled_providers():
        for model in provider.list_models():
            full_id = f"{provider.id}/{model.id}"
            model_list.append(full_id)
    return model_list


def display_models():
    """显示可用模型列表"""
    print("\n📋 可用模型:\n")

    index = 1
    current_index = PROVIDER_CONFIG.get_current_model_index()

    for provider in PROVIDER_CONFIG.list_enabled_providers():
        print(f"  {provider.name}:")
        for model in provider.list_models():
            full_id = f"{provider.id}/{model.id}"
            marker = "→" if index == current_index else " "
            print(f"  {marker} [{index}] {full_id}: {model.name}")
            index += 1
        print()


def switch_model_by_index(index: int) -> bool:
    """根据编号切换模型

    Args:
        index: 模型编号（从1开始）

    Returns:
        是否成功切换
    """
    model_id = PROVIDER_CONFIG.get_model_by_index(index)
    if not model_id:
        print(f"❌ 无效的模型编号: {index}\n")
        return False

    switch_model(model_id)
    return True


def interactive_select_model():
    """交互式选择模型"""
    display_models()

    model_list = list_models()
    if not model_list:
        print("❌ 没有可用的模型\n")
        return

    try:
        choice = input("请输入模型编号 (0 or 直接Enter 取消): ").strip()
        if choice == "0" or choice == "":
            print("已取消\n")
            return

        switch_model_by_index(int(choice))
    except ValueError:
        print("❌ 请输入有效的数字\n")
    except KeyboardInterrupt:
        print("\n已取消\n")


def get_agent_descriptions() -> str:
    """Generate agent type descriptions for the Task tool."""
    return "\n".join(
        f"- {name}: {cfg['description']}" for name, cfg in AGENT_TYPES.items()
    )


def get_environment_info() -> str:
    """获取运行环境信息，用于系统提示词的动态段落。

    Returns:
        环境信息字符串
    """
    import datetime

    os_name = platform.system()
    os_release = platform.release()
    arch = platform.machine()
    if os_name == "Windows":
        shell = "PowerShell"
    else:
        shell = "Bash"
    today = datetime.date.today().isoformat()
    python_ver = platform.python_version()
    user = os.getenv("USER") or os.getenv("USERNAME") or "unknown"

    lines = [
        f"Current date: {today}",
        f"OS: {os_name} {os_release} | {arch}",
        f"Shell: {shell}",
        f"Python: {python_ver}",
        f"User: {user}",
        f"Working Directory: {WORKDIR}",
        f"Model: {DEEPSEEK_MODEL}",
    ]
    return "Environment:\n" + "\n".join(f"- {l}" for l in lines)

ASK = 0  # 问答模式
TRANSLATE = 1  # 翻译模式
AGENT = 2  # 智能体模式
ROLE = 3  # 角色扮演模式

# 系统智能体核心提示词（不含动态内容，由 SystemPromptBuilder 组装）
SYSTEM_PROMPT_AGENT_CORE = """You are a coding agent.

Loop: plan -> act with tools -> report.

Rules:
- Use Skill tool IMMEDIATELY when a task matches a skill description
- Use Task tool for subtasks needing focused exploration or implementation
- Use TodoWrite to track multi-step work
- Prefer tools over prose. Act, don't just explain.
- After finishing, summarize what changed.
"""

# 动态边界标记：上面更稳定，下面更容易变
DYNAMIC_BOUNDARY = "=== DYNAMIC_BOUNDARY ==="


class SystemPromptBuilder:
    """按流水线组装系统提示词，每一段只负责一种来源。

    Pipeline:
      1. core       - 核心身份与行为说明
      2. tools      - 工具说明（skills/MCP/subagents 元信息）
      3. memory     - 跨会话记忆
      4. guidance   - 工具使用指导（如 MemorySave）
      5. DYNAMIC_BOUNDARY
      6. dynamic    - 动态环境信息（日期、目录、模型等）
    """

    def __init__(self, mode: int = ASK, role_id: str | None = None, agent_id: str | None = None):
        self.mode = mode
        self.role_id = role_id
        self.agent_id = agent_id

    # -- Section 1: Core instructions --
    def _build_core(self) -> str:
        if self.mode == TRANSLATE:
            return SYSTEM_PROMPT_TRANSLATE
        elif self.mode == AGENT:
            actual_agent_id = self.agent_id
            if not actual_agent_id:
                init_agent_manager()
                assert AGENT_MANAGER is not None
                actual_agent_id = AGENT_MANAGER.default_agent

            if not actual_agent_id or actual_agent_id == "builtin":
                return SYSTEM_PROMPT_AGENT_CORE
            else:
                init_agent_manager()
                assert AGENT_MANAGER is not None
                agent_prompt = AGENT_MANAGER.get_agent_prompt(actual_agent_id)
                if agent_prompt:
                    return agent_prompt
                else:
                    print(f"❌ 未找到智能体: {actual_agent_id}")
                    return SYSTEM_PROMPT_AGENT_CORE
        elif self.mode == ROLE and self.role_id:
            init_role_manager()
            assert ROLE_MANAGER is not None
            system_prompt = ROLE_MANAGER.get_role_prompt(self.role_id)
            if not system_prompt:
                print(f"❌ 未找到角色: {self.role_id}")
                return SYSTEM_PROMPT_ASK
            return system_prompt
        else:
            return SYSTEM_PROMPT_ASK

    # -- Section 2: Tool metadata (skills/MCP/subagents) --
    def _build_tools_metadata(self) -> str:
        """注入工具元信息（skills/MCP/subagents 描述）。

        - ASK/TRANSLATE 模式：无工具，不注入
        - AGENT/ROLE 模式：注入，帮助模型了解可用能力
        """
        if self.mode not in (AGENT, ROLE):
            return ""

        parts = []
        skills_desc = SKILLS.get_descriptions()
        if skills_desc and skills_desc != "(no skills available)":
            parts.append(f"**Skills available** (invoke with Skill tool when task matches):\n{skills_desc}")

        mcp_desc = MCP_MANAGER.get_descriptions()
        if mcp_desc and mcp_desc != "(no MCP servers configured)":
            parts.append(f"**MCP servers available** (invoke with MCP tool to connect):\n{mcp_desc}")

        agent_desc = get_agent_descriptions()
        if agent_desc:
            parts.append(f"**Subagents available** (invoke with Task tool for focused subtasks):\n{agent_desc}")

        return "\n\n".join(parts)

    # -- Section 3: Memory content --
    def _build_memory(self) -> str:
        memory_section = MEMORY_MANAGER.get_memory_prompt()
        return memory_section if memory_section else ""

    # -- Section 4: Tool usage guidance --
    def _build_guidance(self) -> str:
        if self.mode in (AGENT, ROLE):
            return MEMORY_GUIDANCE
        return ""

    # -- Section 6: Dynamic context --
    def _build_dynamic(self) -> str:
        """每轮可能变化的环境信息，放在 DYNAMIC_BOUNDARY 下方。"""
        return get_environment_info()

    # -- Assemble all sections --
    def build(self) -> str:
        """按流水线组装完整系统提示词。"""
        sections = []

        core = self._build_core()
        if core:
            sections.append(core)

        tools = self._build_tools_metadata()
        if tools:
            sections.append(tools)

        memory = self._build_memory()
        if memory:
            sections.append(memory)

        guidance = self._build_guidance()
        if guidance:
            sections.append(guidance)

        # 稳定/动态边界
        sections.append(DYNAMIC_BOUNDARY)

        dynamic = self._build_dynamic()
        if dynamic:
            sections.append(dynamic)

        return "\n\n".join(sections)

    def list_sections(self) -> List[str]:
        """列出当前配置下各段的标题，用于调试。"""
        sections = ["core"]
        if self._build_tools_metadata():
            sections.append("tools_metadata")
        if self._build_memory():
            sections.append("memory")
        if self._build_guidance():
            sections.append("guidance")
        sections.append("DYNAMIC_BOUNDARY")
        sections.append("dynamic")
        return sections


current_mode: int = ASK
# 对话历史缓冲
messages: List[Dict[str, str | List]] = []
# 问答模式是否记忆上下文
memory = True
# 当前模型提示符
model_prompt = DEEPSEEK_MODEL
# 标题是否已生成
title_generated = False
# Telegram Bot 上下文（用于发送图片等操作）
_telegram_update: Optional[Update] = None
_telegram_context: Optional[ContextTypes.DEFAULT_TYPE] = None
_telegram_pending_tasks: List = []
# QQ Bot 上下文
_qq_bot: Optional[Any] = None
_qq_current_openid: Optional[str] = None
# 主动消息调度器
_PROACTIVE_SCHEDULER: Optional[ProactiveScheduler] = None


class TodoManager:
    """Manages a structured task list with enforced constraints."""

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        validated = []
        in_progress_count = 0

        for i, item in enumerate(items):
            # Extract and validate fields
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()

            # Validation checks
            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")

            if status == "in_progress":
                in_progress_count += 1

            validated.append({"content": content, "status": status})

        # Enforce constraints
        if len(validated) > 20:
            raise ValueError("Max 20 todos allowed")
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")

        self.items = validated
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "No todos."

        lines = []
        for item in self.items:
            if item["status"] == "completed":
                lines.append(f"[✓] {item['content']}")
            elif item["status"] == "in_progress":
                lines.append(f"[>] {item['content']}")
            else:
                lines.append(f"[ ] {item['content']}")

        completed = sum(1 for t in self.items if t["status"] == "completed")
        lines.append(f"\n({completed}/{len(self.items)} completed)")

        return "\n".join(lines)


# Global todo manager instance
TODO = TodoManager()


def init_session_manager(mode: int = ASK, role_id: Optional[str] = None):
    """初始化会话管理器，根据模式设置对应的子目录"""
    global SESSION_MANAGER

    if mode == TRANSLATE:
        session_type = "translate"
        cache_dir = CACHE_DIR
        use_subdir = True
    elif mode == AGENT:
        session_type = "agent"
        cache_dir = CACHE_DIR
        use_subdir = True
    elif mode == ROLE and role_id:
        init_role_manager()
        assert ROLE_MANAGER is not None
        cache_dir = ROLE_MANAGER.get_history_dir(role_id)
        session_type = "role"
        use_subdir = False
    else:
        session_type = "ask"
        cache_dir = CACHE_DIR
        use_subdir = True

    SESSION_MANAGER = SessionManager(
        cache_dir=cache_dir, session_type=session_type, use_subdir=use_subdir
    )


def init_system_prompt(
    mode: int = ASK, role_id: Optional[str] = None, agent_id: Optional[str] = None
):
    """初始化系统提示词（通过 SystemPromptBuilder 组装流水线）"""
    global title_generated
    messages.clear()
    title_generated = False

    builder = SystemPromptBuilder(mode=mode, role_id=role_id, agent_id=agent_id)
    system_prompt = builder.build()

    # 角色未找到时回退到 ASK 模式
    if mode == ROLE and role_id:
        init_role_manager()
        assert ROLE_MANAGER is not None
        if not ROLE_MANAGER.get_role_prompt(role_id):
            global current_mode
            current_mode = ASK

    messages.append({"role": "system", "content": system_prompt})

    init_session_manager(mode, role_id)

    if mode == ROLE and role_id and SESSION_MANAGER:
        load_role_session(role_id)


def clear_history():
    """清除对话历史，重新装载系统提示词"""
    global title_generated

    # 清除全部消息
    messages.clear()
    title_generated = False

    # 重新构建系统提示词
    role_id = get_current_role_id()
    agent_id = get_current_agent_id()
    builder = SystemPromptBuilder(mode=current_mode, role_id=role_id, agent_id=agent_id)
    system_prompt = builder.build()
    messages.append({"role": "system", "content": system_prompt})


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": f"Run a shell command (bash/zsh on Linux/macOS, PowerShell on Windows). Current OS: {platform.system()}.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (bash/zsh/PowerShell)",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "timeout in seconds (default: 10)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Start reading from line number (1-indexed, default: 1)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default: all)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path for the file",
                    },
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (must match precisely)",
                    },
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "Find files matching a glob pattern (e.g. '*.rs', '**/*.py', 'src/**/*.ts').",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern to match files",
                    },
                    "path": {
                        "type": "string",
                        "description": "Base directory to search from (default: workspace root)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Search file contents using a regex pattern. Returns file:line_number matches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regex pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory to search in (default: workspace root)",
                    },
                    "include": {
                        "type": "string",
                        "description": "File extension filter, e.g. '*.py', '*.js'",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "webfetch",
            "description": "Fetch content from a URL. Returns text, markdown, or html.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["text", "markdown", "html"],
                        "description": "Output format (default: markdown)",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "TodoWrite",
            "description": "Update the task list.",
            "parameters": {
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "description": "List of todo items",
                        "items": {
                            "type": "object",
                            "properties": {
                                "content": {
                                    "type": "string",
                                    "description": "Task description",
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Task status",
                                },
                            },
                            "required": ["content", "status"],
                        },
                    }
                },
                "required": ["todos"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Task",
            "description": f"Spawn a subagent for a focused subtask.\n\nAgent types:\n{get_agent_descriptions()}",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Short task name (3-5 words) for progress display",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Detailed instructions for the subagent",
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": list(AGENT_TYPES.keys()),
                        "description": "Type of agent to spawn",
                    },
                },
                "required": ["description", "prompt", "agent_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Skill",
            "description": f"""Load a skill to gain specialized knowledge for a task.\n\nAvailable skills:\n{SKILLS.get_descriptions()}""",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "description": "Name of the skill to load",
                    }
                },
                "required": ["skill"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "MCP",
            "description": f"""Connect to an MCP server to use its tools.\n\nAvailable servers:\n{MCP_MANAGER.get_descriptions()}""",
            "parameters": {
                "type": "object",
                "properties": {
                    "server": {
                        "type": "string",
                        "description": "Name of the MCP server to connect",
                    }
                },
                "required": ["server"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "MemorySave",
            "description": "Save a persistent memory that survives across sessions. Use for user preferences, corrections, non-obvious project facts, or external resource pointers. Do NOT use for code structure, task state, or secrets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short identifier (e.g. prefer_tabs, db_schema)",
                    },
                    "description": {
                        "type": "string",
                        "description": "One-line summary of what this memory captures",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["user", "feedback", "project", "reference"],
                        "description": "user=preferences, feedback=corrections, project=non-obvious project conventions or decision reasons, reference=external resource pointers",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full memory content (multi-line OK)",
                    },
                },
                "required": ["name", "description", "type", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "background_run",
            "description": "Run a shell command in a background thread. Returns task_id immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run in background",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default: 300)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_background",
            "description": "Check background task status. Provide task_id for one task, or omit to list all.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task ID to check (omit to list all)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recognize_image",
            "description": "Recognize and analyze image content. Use this when user asks to describe, analyze, or understand one or multiple images. Supports network URLs and local file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of image URLs or local file paths (e.g. ['https://example.com/1.jpg', './local.png'])",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Question or instruction about the image(s) (default: 'Describe this image in detail')",
                    },
                },
                "required": ["image_urls"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recognize_audio",
            "description": "Recognize and analyze audio content. Use this when user asks to describe, transcribe, or understand audio. Supports network URLs and local file paths.",
            "parameters": {
                "type": "object",
                "properties": {
                    "audio_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of audio URLs or local file paths (e.g. ['https://example.com/audio.mp3', './local.wav'])",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Question or instruction about the audio(s) (default: 'Describe the content of this audio')",
                    },
                },
                "required": ["audio_urls"],
            },
        },
    },
]

# Bot mode tools (ROLE mode only - send media to user)
BOT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_image",
            "description": "Send an image file to the user. Supports JPG, PNG, GIF, BMP, WebP formats.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the image file (relative or absolute)",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption for the image",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_voice",
            "description": "Send a voice message to the user using TTS (text-to-speech). Converts text to speech audio and sends it as a voice message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to convert to speech and send as voice message",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Voice ID for TTS. Optional, uses default if not specified.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_file",
            "description": "Send a file to the user. Supports any file type (PDF, ZIP, documents, etc.). Max 50MB.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file (relative or absolute)",
                    },
                    "caption": {
                        "type": "string",
                        "description": "Optional caption for the file",
                    },
                },
                "required": ["path"],
            },
        },
    },
]

ONLY_BASH_TOOL = os.getenv("ONLY_BASH_TOOL", "disabled").lower()
if ONLY_BASH_TOOL == "enabled":
    TOOLS = TOOLS[:1]  # 仅保留 bash 工具

# Agent Team tools (available in AGENT mode when --agent-team is set)
TEAM_TOOLS = TeammateManager.lead_tools()


def run_bash(command: str, timeout: Optional[int] = None) -> str:
    """执行 bash 命令并返回 stdout/stderr"""
    if any(d in command for d in ["rm -rf /", "shutdown"]):
        return "Error: Dangerous command blocked"
    print(f"  \033[34m$ {command}\033[0m")
    return execute_cmd(command, timeout)


def safe_path(p: str, allow_escape: bool = True) -> Path:
    """Ensure path stays within workspace.

    Args:
        p: 文件路径
        allow_escape: 是否允许路径超出工作区范围
    """
    if allow_escape:
        return Path(p).resolve()
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, offset: int = 0, limit: int = None) -> str:
    """Read file contents."""
    try:
        range_args = {}
        if offset:
            range_args["offset"] = offset
        if limit:
            range_args["limit"] = limit
        print(f"\033[34m→ Read {path}{format_range_info(range_args)}\033[0m")
        lines = read_file(safe_path(path)).splitlines()
        if offset:
            lines = lines[offset - 1 :]
        if limit:
            lines = lines[:limit]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    """Write content to file."""
    try:
        print(f"\033[34m→ Wrote {path}\033[0m")
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        write_file(fp, content)
        # 打印部分内容（最大2000字符）
        preview = content[:2000]
        suffix = "..." if len(content) > 2000 else ""
        print(f"{preview}{suffix}")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in file."""
    try:
        print(f"\033[34m→ Edited {path}\033[0m")
        fp = safe_path(path)
        text = read_file(fp)
        if old_text not in text:
            return f"Error: Text not found in {path}"
        new_file_text = text.replace(old_text, new_text, 1)
        write_file(fp, new_file_text)
        print(format_diff(old_text, new_text, colored=True))
        return (
            f"Edited {path}: replaced {len(old_text)} chars with {len(new_text)} chars"
        )
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str, path: str | None = None) -> str:
    """Find files matching a glob pattern."""
    try:
        # 检查是否被中断
        _interrupt_ctrl.check()

        base = safe_path(path) if path else WORKDIR
        print(f"\033[34m✱ Glob {pattern} in {base.relative_to(WORKDIR) or '.'}\033[0m")
        matches = []
        for p in base.glob(pattern):
            # 每处理一个文件检查一次中断
            _interrupt_ctrl.check()
            if p.is_file():
                matches.append(str(p.relative_to(WORKDIR)))
        matches.sort()
        if not matches:
            return "(no matches)"
        logger.debug("Glob matches: %s", matches)
        return f"{len(matches)} matches\n" + "\n".join(matches)
    except InterruptedError:
        return "Glob interrupted by user (ESC)"
    except Exception as e:
        return f"Error: {e}"


def run_grep(pattern: str, path: str | None = None, include: str | None = None) -> str:
    """Search file contents using a regex pattern."""
    try:
        # 检查是否被中断
        _interrupt_ctrl.check()

        base = safe_path(path) if path else WORKDIR
        print(
            f'\033[34m✱ Grep "{pattern}" in {base.relative_to(WORKDIR) or "."}\033[0m'
        )
        regex = re.compile(pattern)
        results = []

        if base.is_file():
            files = [base]
        elif include:
            files = sorted(base.rglob(include))
        else:
            files = sorted(p for p in base.rglob("*") if p.is_file())

        for fp in files:
            # 每处理一个文件检查一次中断
            _interrupt_ctrl.check()

            if not fp.is_file():
                continue
            try:
                for i, line in enumerate(
                    read_file(fp, errors='replace').splitlines(), 1
                ):
                    if regex.search(line):
                        rel = fp.relative_to(WORKDIR)
                        results.append(f"{rel}:{i}: {line.rstrip()}")
            except (UnicodeDecodeError, PermissionError):
                continue

        if not results:
            return "(no matches)"
        logger.debug("Grep matches: %s", results)
        return f"{len(results)} matches\n" + "\n".join(results)
    except InterruptedError:
        return "Grep interrupted by user (ESC)"
    except Exception as e:
        return f"Error: {e}"


def run_webfetch(url: str, format: str = "markdown") -> str:
    """Fetch content from a URL."""
    try:
        print(f"\033[34m% WebFetch {url} ({format})\033[0m")
        headers = {"User-Agent": "Mozilla/5.0 ask-agent"}
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        text = resp.text

        if format == "text":
            # Strip HTML tags for plain text
            text = re.sub(r"<[^>]+>", "", text)
            text = re.sub(r"\s+", " ", text).strip()
        elif format == "html":
            pass  # raw HTML
        else:  # markdown
            if "html" in content_type:
                # Basic HTML to markdown conversion
                text = re.sub(r"<br\s*/?>", "\n", text)
                text = re.sub(r"</?(p|div|section|article)[^>]*>", "\n", text)
                text = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>", r"\n**\2**\n", text)
                text = re.sub(
                    r"<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"[\2](\1)", text
                )
                text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text)
                text = re.sub(
                    r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", text, flags=re.DOTALL
                )
                text = re.sub(r"<[^>]+>", "", text)
                text = re.sub(r"\n{3,}", "\n\n", text).strip()

        if len(text) > 50000:
            text = text[:50000] + "\n\n... (truncated at 50000 chars)"
        logger.debug("WebFetch result:\n%s", text)
        return text
    except Exception as e:
        return f"Error: {e}"


def run_todo(items: list) -> str:
    """Update the todo list."""
    try:
        return TODO.update(items)
    except Exception as e:
        return f"Error: {e}"


def run_send_image(path: str, caption: str = "") -> str:
    """Send an image file to the user.

    Args:
        path: Path to the image file (relative or absolute)
        caption: Optional caption for the image

    Returns:
        Success or error message
    """
    global _telegram_update, _telegram_pending_tasks, _qq_bot, _qq_current_openid

    if _wechat_bot:
        return "❌ 微信 Bot 暂不支持发送图片"

    try:
        # Resolve the path
        image_path = safe_path(path)
        print(f"\033[34m→ Send Image {path}\033[0m")

        # Check if file exists
        if not image_path.exists():
            return f"Error: Image file not found: {path}"

        # Check if it's a file
        if not image_path.is_file():
            return f"Error: Path is not a file: {path}"

        # Check if it's an image file (by extension)
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}
        if image_path.suffix.lower() not in image_extensions:
            return f"Error: File is not a supported image format: {image_path.suffix}"

        # QQ Bot mode
        if _qq_bot and _qq_current_openid:
            import asyncio

            async def send_qq_image():
                await _qq_bot.send_image(_qq_current_openid, str(image_path))

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(send_qq_image())
            else:
                loop.run_until_complete(send_qq_image())
            return f"Image sent: {path}"

        # Telegram Bot mode
        if _telegram_update and _telegram_update.message:
            import asyncio

            async def send_photo():
                with open(image_path, 'rb') as photo:
                    await _telegram_update.message.reply_photo(
                        photo=photo,
                        caption=caption if caption else None
                    )

            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(send_photo())
                _telegram_pending_tasks.append(task)
            else:
                loop.run_until_complete(send_photo())

            return f"Image sent: {path}"

        # CLI mode
        return f"Image path: {image_path} (not in Bot mode, cannot send)"

    except Exception as e:
        return f"Error sending image: {e}"


def run_send_voice(text: str, voice: str = "") -> str:
    """Send a voice message to the user via TTS.

    Args:
        text: Text to convert to speech
        voice: Voice ID for TTS (optional)

    Returns:
        Success or error message
    """
    global _telegram_update, _telegram_pending_tasks, _qq_bot, _qq_current_openid

    if _wechat_bot:
        return "❌ 微信 Bot 暂不支持发送语音"

    try:
        from util.tts import text_to_speech, get_tts_api_config, convert_audio_format, strip_parentheses

        print(f"\033[34m→ Send Voice\033[0m")

        # Strip parentheses content
        tts_text = strip_parentheses(text)
        if not tts_text:
            return "Error: Text is empty after removing parentheses content"

        # Get TTS config - try current role config first (ignore enabled flag), then env vars
        voice_config = get_current_voice_config_raw()
        if not voice_config:
            voice_config = {"model": os.getenv("TTS_API_MODEL", "")}
        if voice:
            voice_config["voice_id"] = voice

        api_config = get_tts_api_config(PROVIDER_CONFIG, voice_config.get("model"))
        if not api_config:
            return "Error: TTS API not configured. Set TTS_API_KEY and TTS_API_URL in .env, or configure voice in your role settings."

        # Generate speech
        audio_bytes = text_to_speech(tts_text, voice_config, api_config)
        if not audio_bytes:
            return "Error: Failed to generate speech"

        # QQ Bot mode
        if _qq_bot and _qq_current_openid:
            import asyncio

            async def send_qq_voice():
                await _qq_bot.send_voice(_qq_current_openid, audio_bytes)

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(send_qq_voice())
            else:
                loop.run_until_complete(send_qq_voice())
            return "Voice sent"

        # Telegram Bot mode
        if _telegram_update and _telegram_update.message:
            import asyncio

            # Telegram requires ogg/opus for voice messages
            ogg_bytes = convert_audio_format(audio_bytes, "mp3", "ogg")

            async def send_voice_msg():
                await _telegram_update.message.reply_voice(ogg_bytes or audio_bytes)

            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(send_voice_msg())
                _telegram_pending_tasks.append(task)
            else:
                loop.run_until_complete(send_voice_msg())

            return "Voice sent"

        # CLI mode
        return "Voice generated (not in Bot mode, cannot send)"

    except Exception as e:
        return f"Error sending voice: {e}"


def run_send_file(path: str, caption: str = "") -> str:
    """Send a file to the user.

    Args:
        path: Path to the file (relative or absolute)
        caption: Optional caption for the file

    Returns:
        Success or error message
    """
    global _telegram_update, _telegram_pending_tasks, _qq_bot, _qq_current_openid

    if _wechat_bot:
        return "❌ 微信 Bot 暂不支持发送文件"

    try:
        # Resolve the path
        file_path = safe_path(path)
        print(f"\033[34m→ Send File {path}\033[0m")

        # Check if file exists
        if not file_path.exists():
            return f"Error: File not found: {path}"

        # Check if it's a file
        if not file_path.is_file():
            return f"Error: Path is not a file: {path}"

        # Check file size (50MB limit)
        file_size = file_path.stat().st_size
        if file_size > 50 * 1024 * 1024:
            return f"Error: File too large ({file_size / 1024 / 1024:.1f}MB). Max 50MB."

        # QQ Bot mode
        if _qq_bot and _qq_current_openid:
            import asyncio

            async def send_qq_file():
                await _qq_bot.send_file(_qq_current_openid, str(file_path))

            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(send_qq_file())
            else:
                loop.run_until_complete(send_qq_file())
            return f"File sent: {path}"

        # Telegram Bot mode
        if _telegram_update and _telegram_update.message:
            import asyncio

            async def send_document():
                with open(file_path, 'rb') as doc:
                    await _telegram_update.message.reply_document(
                        document=doc,
                        caption=caption if caption else None,
                        filename=file_path.name
                    )

            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = asyncio.ensure_future(send_document())
                _telegram_pending_tasks.append(task)
            else:
                loop.run_until_complete(send_document())

            return f"File sent: {path}"

        # CLI mode
        return f"File path: {file_path} (not in Bot mode, cannot send)"

    except Exception as e:
        return f"Error sending file: {e}"


def run_recognize_image(image_urls: List[str], prompt: str = "Describe this image in detail") -> str:
    """Recognize and analyze image content (supports multiple images).

    Args:
        image_urls: List of image URLs or local file paths
        prompt: Question or instruction about the image(s)

    Returns:
        Model response about the image(s)
    """
    try:
        count = len(image_urls)
        print(f"\033[34m→ Recognize Image\033[0m")

        # Check if current model supports image input
        if not current_model_supports_image_input():
            error_msg = "Error: Current model does not support image input"
            messages.append({"role": "user", "content": f"[Image Error] {error_msg}"})
            return error_msg

        if not image_urls:
            error_msg = "Error: No image URLs provided"
            messages.append({"role": "user", "content": f"[Image Error] {error_msg}"})
            return error_msg

        from util.image import image_to_base64, get_image_mime_type, is_supported_image, validate_image_size

        multimodal_content = []
        success = 0
        errors = []

        for url in image_urls:
            # Auto-detect source type
            if url.startswith(("http://", "https://")):
                multimodal_content.append({"type": "image_url", "image_url": {"url": url}})
                success += 1
            else:
                try:
                    image_path = safe_path(url)

                    if not image_path.exists():
                        errors.append(f"{url} (not found)")
                        continue

                    if not image_path.is_file():
                        errors.append(f"{url} (not a file)")
                        continue

                    if not is_supported_image(str(image_path)):
                        errors.append(f"{url} (unsupported format)")
                        continue

                    if not validate_image_size(str(image_path), max_size_mb=50.0):
                        errors.append(f"{url} (too large)")
                        continue

                    base64_data = image_to_base64(str(image_path))
                    if not base64_data:
                        errors.append(f"{url} (read failed)")
                        continue

                    mime_type = get_image_mime_type(str(image_path))
                    multimodal_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{base64_data}"}
                    })
                    success += 1

                except Exception as e:
                    errors.append(f"{url} ({e})")

        # Add text prompt
        multimodal_content.append({"type": "text", "text": prompt})

        # Inject into messages
        messages.append({"role": "user", "content": multimodal_content})

        if errors:
            print(f"\033[31m  Errors: {'; '.join(errors)}\033[0m")

        return f"Recognized {count} image(s)"

    except Exception as e:
        error_msg = f"Error recognizing image: {e}"
        messages.append({"role": "user", "content": f"[Image Error] {error_msg}"})
        return error_msg


def run_recognize_audio(audio_urls: List[str], prompt: str = "Describe the content of this audio") -> str:
    """Recognize and analyze audio content (supports multiple audios).

    Args:
        audio_urls: List of audio URLs or local file paths
        prompt: Question or instruction about the audio(s)

    Returns:
        Model response about the audio(s)
    """
    try:
        count = len(audio_urls)
        print(f"\033[34m→ Recognize Audio\033[0m")

        # Check if current model supports audio input
        if not current_model_supports_audio_input():
            error_msg = "Error: Current model does not support audio input"
            messages.append({"role": "user", "content": f"[Audio Error] {error_msg}"})
            return error_msg

        if not audio_urls:
            error_msg = "Error: No audio URLs provided"
            messages.append({"role": "user", "content": f"[Audio Error] {error_msg}"})
            return error_msg

        # Supported audio extensions
        supported_audio_extensions = {'.mp3', '.wav', '.flac', '.m4a', '.ogg'}

        def is_supported_audio(file_path: str) -> bool:
            ext = Path(file_path).suffix.lower()
            return ext in supported_audio_extensions

        def get_audio_mime_type(file_path: str) -> str:
            ext = Path(file_path).suffix.lower()
            mime_map = {
                '.mp3': 'audio/mpeg',
                '.wav': 'audio/wav',
                '.flac': 'audio/flac',
                '.m4a': 'audio/mp4',
                '.ogg': 'audio/ogg',
            }
            return mime_map.get(ext, 'audio/mpeg')

        def audio_to_base64(file_path: str) -> str:
            """将音频文件转换为 Base64 编码"""
            with open(file_path, 'rb') as f:
                audio_bytes = f.read()
            return base64.b64encode(audio_bytes).decode('utf-8')

        multimodal_content = []
        success = 0
        errors = []

        for url in audio_urls:
            # Auto-detect source type
            if url.startswith(("http://", "https://")):
                multimodal_content.append({
                    "type": "input_audio",
                    "input_audio": {"data": url}
                })
                success += 1
            else:
                try:
                    audio_path = safe_path(url)

                    if not audio_path.exists():
                        errors.append(f"{url} (not found)")
                        continue

                    if not audio_path.is_file():
                        errors.append(f"{url} (not a file)")
                        continue

                    if not is_supported_audio(str(audio_path)):
                        errors.append(f"{url} (unsupported format)")
                        continue

                    # Check file size (max 50MB for base64)
                    file_size_mb = audio_path.stat().st_size / (1024 * 1024)
                    if file_size_mb > 50:
                        errors.append(f"{url} (too large: {file_size_mb:.1f}MB, max 50MB)")
                        continue

                    base64_data = audio_to_base64(str(audio_path))
                    if not base64_data:
                        errors.append(f"{url} (read failed)")
                        continue

                    mime_type = get_audio_mime_type(str(audio_path))
                    multimodal_content.append({
                        "type": "input_audio",
                        "input_audio": {"data": f"data:{mime_type};base64,{base64_data}"}
                    })
                    success += 1

                except Exception as e:
                    errors.append(f"{url} ({e})")

        # Add text prompt
        multimodal_content.append({"type": "text", "text": prompt})

        # Inject into messages
        messages.append({"role": "user", "content": multimodal_content})

        if errors:
            print(f"\033[31m  Errors: {'; '.join(errors)}\033[0m")

        return f"Recognized {success} audio(s)"

    except Exception as e:
        error_msg = f"Error recognizing audio: {e}"
        messages.append({"role": "user", "content": f"[Audio Error] {error_msg}"})
        return error_msg


def run_skill(skill_name: str) -> str:
    """Load a skill and inject it into the conversation."""
    content = SKILLS.get_skill_content(skill_name)

    if content is None:
        available = ", ".join(SKILLS.list_skills()) or "none"
        return f"Error: Unknown skill '{skill_name}'. Available: {available}"

    # Wrap in tags so model knows it's skill content
    return f"""<skill-loaded name="{skill_name}">
{content}
</skill-loaded>

Follow the instructions in the skill above to complete the user's task."""


def run_mcp(server_name: str) -> str:
    """Connect to an MCP server and inject its tools."""
    print(f"\033[34m→ Connecting to {server_name}\033[0m")

    if not MCP_MANAGER.loaded:
        MCP_MANAGER.load_config()

    server = MCP_MANAGER.get_server_info(server_name)
    if not server:
        available = ", ".join(MCP_MANAGER.config.list_enabled_servers()) or "none"
        return f"Error: Unknown MCP server '{server_name}'. Available: {available}"

    if not server.enabled:
        return f"Error: MCP server '{server_name}' is disabled"

    if MCP_MANAGER.is_server_connected(server_name):
        return f"MCP server '{server_name}' already connected"

    if not MCP_MANAGER.connect_server(server_name):
        return f"Error: Failed to connect to MCP server '{server_name}'"

    _, tools = MCP_MANAGER.active_clients[server_name]
    TOOLS.extend(tools)

    return f"Connected to MCP server: {server_name} ({len(tools)} tools)"


def merge_arguments(tool_calls_collected: List) -> List:
    if not tool_calls_collected:
        return []

    tool_calls_by_index = {}

    for tool_call in tool_calls_collected:
        index = tool_call.get("index", 0)

        if index not in tool_calls_by_index:
            tool_calls_by_index[index] = {
                "id": "",
                "index": index,
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }

        current = tool_calls_by_index[index]

        if tool_call.get("id"):
            current["id"] = tool_call["id"]
        # 兼容Gemini的thought_signature (嵌套在extra_content.google.thought_signature)
        # Gemini API要求回传时保持extra_content结构
        if "extra_content" in tool_call:
            current["extra_content"] = tool_call["extra_content"]
        if "function" in tool_call:
            func = tool_call["function"]
            if func.get("name"):
                current["function"]["name"] = func["name"]
            if func.get("arguments"):
                current["function"]["arguments"] += func["arguments"]

    result = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]
    logger.debug("merge arguments result:\n%s", json.dumps(result, indent=2))

    return result


def get_tools_for_agent(type: str) -> list:
    """
    Filter tools based on agent type.

    Each agent type has a whitelist of allowed tools.
    '*' means all tools (but subagents don't get Task to prevent infinite recursion).
    """
    allowed = AGENT_TYPES.get(type, {}).get("tools", "*")

    if allowed == "*":
        # All tools except Task (no recursion)
        return [t for t in TOOLS if t["function"]["name"] != "Task"]

    return [t for t in TOOLS if t["function"]["name"] in allowed]


def run_task(description: str, prompt: str, agent_type: str) -> str:
    """Execute a subagent task with isolated context."""
    if agent_type not in AGENT_TYPES:
        return f"Error: Unknown agent type '{agent_type}'"

    config = AGENT_TYPES[agent_type]

    # Agent-specific system prompt
    sub_system = f"""You are a {agent_type} subagent at {WORKDIR}.

{config["prompt"]}

Complete the task and return a clear, concise summary."""

    # Filtered tools for this agent type
    sub_tools = get_tools_for_agent(agent_type)

    # ISOLATED message history - this is the key!
    # The subagent starts fresh, doesn't see parent's conversation
    sub_messages = [
        {"role": "system", "content": sub_system},
        {"role": "user", "content": prompt},
    ]

    # Progress tracking
    print(f"  [{agent_type}] {description}", end="", flush=True)
    start = time.time()
    tool_count = 0

    sub_turn = 1
    reasoning_start_index = len(messages)
    # Run the same agent loop (silently - don't print to main chat)
    while True:
        content, reasoning_content, tool_calls = get_streaming_response(
            sub_messages, sub_tools, True
        )

        # Add assistant response to subagent history
        sub_assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            sub_assistant_msg["tool_calls"] = tool_calls
            # 思考模式下，有工具调用时回传 reasoning_content
            sub_assistant_msg["reasoning_content"] = reasoning_content
        if sub_turn > 1 and not tool_calls: # 工具调用结束时，此时tool_calls=[]，需要额外判定追加 reasoning_content
            sub_assistant_msg["reasoning_content"] = reasoning_content     
        sub_messages.append(sub_assistant_msg)

        # If no tools to execute, break
        if not tool_calls:
            # if reasoning_content:
            #     cleanup_reasoning_content(messages, reasoning_start_index, sub_turn)
            break

        # Execute tools
        for tool_call in tool_calls:
            tool_count += 1
            name = tool_call["function"]["name"]
            args = json.loads(tool_call["function"]["arguments"])
            output = execute_tool(name, args)

            tool_result = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": output,
            }

            # Update progress line (in-place)
            elapsed = time.time() - start
            sys.stdout.write(
                f"\r  [{agent_type}] {description} ... {tool_count} tools, {elapsed:.1f}s"
            )
            sys.stdout.flush()

            sub_messages.append(tool_result)

        sub_turn += 1

    # Final progress update
    elapsed = time.time() - start
    sys.stdout.write(
        f"\r  [{agent_type}] {description} - done ({tool_count} tools, {elapsed:.1f}s)\n"
    )

    # Return the final text content
    return content


def truncate_output(output: str, max_lines: int = 24, max_chars: int = 10000) -> str:
    """按行数和字符数截断输出内容。

    Args:
        output: 要截断的输出内容
        max_lines: 最大行数，默认24行
        max_chars: 最大字符数，默认10000字符

    Returns:
        截断后的内容，超出部分用 "..." 省略
    """
    # 先按字符数截断
    if len(output) > max_chars:
        output = output[:max_chars] + "\n... (truncated at {} chars)".format(max_chars)

    # 再按行数截断
    lines = output.split('\n')
    if len(lines) > max_lines:
        return '\n'.join(lines[:max_lines]) + "\n... (truncated)"
    return output


def execute_tool(name: str, args: dict) -> str:
    if name == "bash":
        return run_bash(args["command"], timeout=args.get("timeout"))
    if name == "read_file":
        return run_read(
            args["path"],
            offset=args.get("offset", 0),
            limit=args.get("limit"),
        )
    if name == "write_file":
        return run_write(args["path"], args["content"])
    if name == "edit_file":
        return run_edit(args["path"], args["old_text"], args["new_text"])
    if name == "glob":
        return run_glob(args["pattern"], path=args.get("path"))
    if name == "grep":
        return run_grep(
            args["pattern"], path=args.get("path"), include=args.get("include")
        )
    if name == "webfetch":
        return run_webfetch(args["url"], format=args.get("format", "markdown"))
    if name == "TodoWrite":
        return run_todo(args["todos"])
    if name == "Task":
        return run_task(args["description"], args["prompt"], args["agent_type"])
    if name == "Skill":
        return run_skill(args["skill"])
    if name == "MCP":
        return run_mcp(args["server"])
    if name == "MemorySave":
        return MEMORY_MANAGER.save_memory(
            args["name"], args["description"], args["type"], args["content"]
        )
    if name == "background_run":
        return BG_MANAGER.run(args["command"], timeout=args.get("timeout", 300))
    if name == "check_background":
        return BG_MANAGER.check(args.get("task_id"))
    if name == "send_image":
        return run_send_image(args["path"], caption=args.get("caption", ""))
    if name == "send_voice":
        return run_send_voice(args["text"], voice=args.get("voice", ""))
    if name == "send_file":
        return run_send_file(args["path"], caption=args.get("caption", ""))
    if name == "recognize_image":
        return run_recognize_image(
            args["image_urls"],
            prompt=args.get("prompt", "Describe this image in detail")
        )
    if name == "recognize_audio":
        return run_recognize_audio(
            args["audio_urls"],
            prompt=args.get("prompt", "Describe the content of this audio")
        )
    # Agent Team tools
    if name in ("spawn_teammate", "list_teammates", "send_message", "read_inbox", "broadcast", "shutdown_teammate"):
        team = init_team_manager()
        return team.execute_lead_tool(name, args)
    if name.startswith("mcp_"):
        import re

        match = re.match(r"^mcp_(.+?)_(.+)$", name)
        if match:
            server_name = match.group(1)
            tool_name = match.group(2)
            return MCP_MANAGER.call_mcp_tool(server_name, tool_name, args)
        return f"Error: Invalid MCP tool name: {name}"
    return f"Unknown tool: {name}"


def llm_generate_title(messages: List[Dict]) -> str:
    """使用LLM生成会话标题"""
    conversation_text = ""
    for msg in messages:
        if msg.get("role") in ("user", "assistant"):
            content = msg.get("content", "")
            if content:
                conversation_text += f"{msg['role']}: {content}\n"

    prompt = f"请为以下对话生成一个简洁的标题（不超过15字），概括主要话题：\n{conversation_text}"

    try:
        title, _, _ = get_streaming_response(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            silent=True,
            useTools=False,
        )
        return title.strip()[:15]  # 限制在15字以内
    except Exception as e:
        logger.warning(f"生成标题失败: {e}")
        return "新会话"


def llm_compress_messages(messages: List[Dict]) -> str:
    """使用LLM压缩消息历史"""
    if len(messages) <= 2:
        return None

    conversation_text = ""
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role in ("user", "assistant") and content:
            conversation_text += f"{role}: {content}\n"

    prompt = f"""请将以下对话压缩成简洁的摘要，保留关键信息：
- 保留对话的核心主题和关键信息
- 突出重要的决定、解决方案或结论
- 保持对话的逻辑连贯性
- 用第三人称概括对话内容

摘要应简洁明了，便于后续对话继续：\n{conversation_text}"""

    try:
        summary, _, _ = get_streaming_response(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            silent=True,
            useTools=False,
        )
        return summary.strip()
    except Exception as e:
        logger.warning(f"压缩消息失败: {e}")
        return None


def stat_token(data: Dict):
    usage = data.get("usage", None)
    if usage:
        usage = data["usage"]
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", 0)
        logger.info(
            "Token 使用: prompt=%d, completion=%d, total=%d",
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )


class InterruptController:
    """统一的中断控制器

    提供统一的中断机制，支持：
    - ESC 键中断
    - HTTP 请求中断
    - 子进程中断
    - 工具执行中断
    """

    def __init__(self):
        self._event = threading.Event()
        self._processes: List[subprocess.Popen] = []
        self._streaming_response = None
        self._lock = threading.Lock()

    @property
    def is_interrupted(self) -> bool:
        """是否被中断"""
        return self._event.is_set()

    def interrupt(self):
        """触发中断"""
        self._event.set()
        self._close_streaming()
        self._kill_processes()

    def reset(self):
        """重置中断状态"""
        self._event.clear()
        with self._lock:
            self._processes.clear()
            self._streaming_response = None

    def check(self):
        """检查是否中断，如果中断则抛出异常"""
        if self._event.is_set():
            raise InterruptedError("Operation interrupted by user")

    def register_process(self, proc: subprocess.Popen):
        """注册子进程，以便中断时 kill"""
        with self._lock:
            self._processes.append(proc)

    def unregister_process(self, proc: subprocess.Popen):
        """取消注册子进程"""
        with self._lock:
            if proc in self._processes:
                self._processes.remove(proc)

    def register_streaming(self, response):
        """注册流式响应，以便中断时关闭"""
        self._streaming_response = response

    def unregister_streaming(self):
        """取消注册流式响应"""
        self._streaming_response = None

    def _close_streaming(self):
        """关闭流式响应"""
        if self._streaming_response is not None:
            try:
                self._streaming_response.close()
            except Exception:
                pass
            self._streaming_response = None

    def _kill_processes(self):
        """终止所有注册的子进程"""
        with self._lock:
            for proc in self._processes[:]:
                try:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                except Exception:
                    pass
            self._processes.clear()


# 全局中断控制器
_interrupt_ctrl = InterruptController()


def _start_esc_listener(stop_event: threading.Event):
    """监听 ESC 键。用 cbreak 模式：逐字符读取，保留输出处理(换行正常)。"""
    if sys.platform == "win32":
        import msvcrt

        while not stop_event.is_set():
            if msvcrt.kbhit():
                if msvcrt.getwch() == "\x1b":
                    _interrupt_ctrl.interrupt()
                    break
            time.sleep(0.05)
    else:
        import tty
        import termios
        import select

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        new = termios.tcgetattr(fd)
        # cbreak: 禁用 ICANON(逐字符读取) + ECHO(不回显)
        # 保留 ISIG(Ctrl+C 生效) + 所有输出处理(ONLCR 换行正常)
        new[3] &= ~(termios.ICANON | termios.ECHO)
        try:
            termios.tcsetattr(fd, termios.TCSANOW, new)
            while not stop_event.is_set():
                r, _, _ = select.select([fd], [], [], 0.1)
                if r:
                    ch = os.read(fd, 1)
                    if ch == b"\x1b":
                        _interrupt_ctrl.interrupt()
                        break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: base * 2^attempt + random(0, 1)."""
    delay = min(BACKOFF_BASE_DELAY * (2 ** attempt), BACKOFF_MAX_DELAY)
    jitter = random.uniform(0, 1)
    return delay + jitter


def get_streaming_response(
    messages: List,
    tools: List,
    silent: bool = False,
    useTools: bool = True,
) -> tuple[str, str, List]:
    """获取流式响应，带指数退避重试"""
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _get_streaming_response(messages, tools, silent, useTools)
        except (requests.exceptions.ChunkedEncodingError, 
                requests.exceptions.StreamConsumedError,
                requests.exceptions.ConnectionError,
                AttributeError):
            # 流被中断关闭时可能抛出这些异常
            # 返回空内容，由调用方检查中断状态
            print("Stream interrupted")
            return ("", "", [])
        except RequestException as e:
            if attempt < MAX_RETRIES:
                delay = backoff_delay(attempt)
                if not silent:
                    print(f"[Recovery] Connection error: {e}. "
                        f"Retrying in {delay:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(delay)
                continue
            else:
                # If we exhaust all retries, raise the last exception
                raise RequestException(f"Failed after {MAX_RETRIES} retries: {e}")

def _get_streaming_response(
    messages: List,
    tools: List,
    silent: bool = False,
    useTools: bool = True,
) -> tuple[str, str, List]:
    """获取真实的API流式响应，包含完整的对话上下文和系统提示词"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "tools": tools,
        "tool_choice": "auto" if useTools else "none",
        "stream": True,
        "thinking": {"type": get_thinking_mode()},
    }

    # thinking 启用时设置 reasoning_effort
    if get_thinking_mode() == "enabled":
        data["reasoning_effort"] = PROVIDER_CONFIG.reasoning_effort

    # Gemini 不支持 thinking 字段
    if re.search(r"googleapis\.com", DEEPSEEK_API_URL):
        data.pop("thinking", None)
        data.pop("reasoning_effort", None)

    if current_mode in (ASK, TRANSLATE):
        data.pop("tools", None) 
        data.pop("tool_choice", None)    

    collected_content = ""
    tool_calls_collected = []
    reasoning_content = ""
    reasoning_in_progress = False
    in_think_tag = False  # 跟踪是否在 <think> 标签内
    should_display = not silent and current_mode != ROLE

    def start_thinking():
        nonlocal reasoning_in_progress
        if not reasoning_in_progress:
            reasoning_in_progress = True
            if should_display:
                print("\033[34mThinking: \033[0m", end="", flush=True)

    def stop_thinking():
        nonlocal reasoning_in_progress
        if reasoning_in_progress:
            reasoning_in_progress = False
            if should_display:
                print("\n")

    with requests.post(
        f"{DEEPSEEK_API_URL}/v1/chat/completions",
        headers=headers,
        json=data,
        stream=True,
        timeout=(10, 30),
    ) as response:
        # 注册流式响应到中断控制器
        _interrupt_ctrl.register_streaming(response)

        if response.status_code != 200:
            print(f"❌ API错误: {response.status_code} {response.text}")
            return ("", "", [])
        response.encoding = 'utf-8'
        for chunk in response.iter_lines(decode_unicode=True):
            if not chunk:
                continue
            # 检查是否被中断
            if _interrupt_ctrl.is_interrupted:
                break
            if not chunk.startswith("data:"):
                continue
            if chunk == "data: [DONE]":
                logger.debug("data: [DONE]")
                break
            try:
                data = json.loads(chunk[6:])  # 去掉 "data: " 前缀
                # logger.debug("data: %s", data)
                if len(data["choices"]) == 0:
                    logger.debug("\nchoices length is 0")
                    continue
                if data["choices"][0].get("finish_reason") is not None:
                    finish_reason = data["choices"][0]["finish_reason"]
                    logger.debug("\nfinish_reason: %s", finish_reason)
                    # 打印 token 使用情况
                    stat_token(data)
                    # 在角色模式下，如果因长度限制而停止，自动压缩上下文
                    if finish_reason == "length" and current_mode == ROLE:
                        print("\n📝 上下文即将达到限制，自动压缩对话历史...\n")
                        summarizer()
                    # continue
                if "choices" in data and data["choices"][0]["delta"]:
                    delta = data["choices"][0]["delta"]
                    # 推理内容
                    if delta.get("reasoning_content"):
                        start_thinking()
                        reasoning_content += delta["reasoning_content"]
                        if should_display:
                            print(
                                f"\033[90m{delta['reasoning_content']}\033[0m",
                                end="",
                                flush=True,
                            )
                    # 文本内容
                    elif delta.get("content"):
                        stop_thinking()
                        content = delta["content"]
                        if "<think>" in content:  # 推理开始
                            in_think_tag = True
                            collected_content += content
                            if should_display:
                                print("\033[34mThinking: \033[0m", end="", flush=True)
                                print(
                                    f"\033[90m{content.replace('<think>', '')}\033[0m",
                                    end="",
                                    flush=True,
                                )
                            continue
                        if "</think>" in content:  # 推理结束
                            in_think_tag = False
                            collected_content += content
                            content = content.split("</think>")
                            if should_display:
                                print(  
                                    f"\033[90m{content[0]}\033[0m", end="", flush=True
                                )
                            print(content[1].replace("\n", ""), end="", flush=True)
                            continue
                        if in_think_tag:
                            # 在 think 标签内，作为推理内容
                            collected_content += content
                            if should_display:
                                print(f"\033[90m{content}\033[0m", end="", flush=True)
                            continue
                        collected_content += content
                        if not silent:
                            print(content, end="", flush=True)
                    # 工具调用
                    elif delta.get("tool_calls"):
                        tool_calls = delta["tool_calls"]
                        logger.debug("tool delta: %s", tool_calls)
                        for tool_call in tool_calls:
                            tool_calls_collected.append(tool_call)
            except json.JSONDecodeError:
                continue

    # 取消注册流式响应
    _interrupt_ctrl.unregister_streaming()
    _streaming_response = None
    return (collected_content, reasoning_content, merge_arguments(tool_calls_collected))


def save_current_session():
    """保存当前会话"""
    if not SESSION_MANAGER:
        return

    try:
        session_id = SESSION_MANAGER.current_session_id
        session_name = SESSION_MANAGER.current_session_name

        current_role_id_val = get_current_role_id()
        if current_mode == ROLE and current_role_id_val:
            session_id = current_role_id_val
            session_name = current_role_id_val

        result = SESSION_MANAGER.save_session(
            messages,
            session_id=session_id,
            session_name=session_name,
        )
        if result:
            logger.info(
                f"💾 会话已保存到 ~/.ask-agent/cache/{SESSION_MANAGER.session_type}/, id = {session_id}"
            )
    except Exception as e:
        print(f"❌ 保存会话失败: {e}")


def load_role_session(role_id: str):
    """加载角色的历史会话"""
    if not SESSION_MANAGER:
        return

    session_data = SESSION_MANAGER.load_session(role_id)
    if session_data:
        global messages, title_generated
        messages = session_data.get("messages", [])
        title_generated = True
        SESSION_MANAGER.current_session_id = role_id
        SESSION_MANAGER.current_session_name = role_id
        message_count = len(messages)
        logger.info(f"  📖 已加载历史会话 ({message_count} 条消息)\n")
        logger.info(f"已加载角色历史会话: {role_id}")


def summarizer():
    """压缩对话历史"""
    global messages

    if len(messages) <= 4:
        print("❌ 消息数量过少，无需压缩")
        return

    # 需要压缩的消数量（前3/4）
    compress_count = len(messages) * 3 // 4

    # 保留后1/4的原始消息
    recent_msgs = messages[compress_count:]

    # 需要压缩的消息（前3/4）
    to_compress = messages[:compress_count]

    print(f"🔄 正在压缩前 {len(to_compress)} 条消息...")
    summary = llm_compress_messages(to_compress)

    if not summary:
        print("❌ 压缩失败")
        return

    system_msg = messages[0] if messages[0].get("role") == "system" else None
    compressed_messages = [system_msg] if system_msg else []
    compressed_messages.append(
        {"role": "system", "content": f"[历史对话摘要]\n{summary}"}
    )
    compressed_messages.extend(recent_msgs)

    messages = compressed_messages
    print(f"✅ 压缩完成，当前消息数: {len(messages)}")


def load_session(session_id: str):
    """加载指定会话"""
    if not SESSION_MANAGER:
        print("❌ 会话管理器未初始化")
        return

    session_data = SESSION_MANAGER.load_session(session_id)
    if not session_data:
        print(f"❌ 未找到会话: {session_id}")
        return

    global messages
    messages = session_data.get("messages", [])
    SESSION_MANAGER.current_session_id = session_id
    SESSION_MANAGER.current_session_name = session_data.get("name")

    print(f"✅ 已加载会话: {SESSION_MANAGER.current_session_name}")


def list_sessions():
    """列出当前模式的所有会话"""
    if not SESSION_MANAGER:
        print("❌ 会话管理器未初始化")
        return

    sessions = SESSION_MANAGER.list_sessions(limit=5)
    if not sessions:
        print(f"📭 ~/.ask-agent/cache/{SESSION_MANAGER.session_type}/ 目录下暂无会话")
        return

    print(f"\n📋 最近会话 ({SESSION_MANAGER.session_type}):\n")
    for i, session in enumerate(sessions, 1):
        marker = "→ " if session["id"] == SESSION_MANAGER.current_session_id else "  "
        print(f"{marker}[{i}] {session['name']}")
        print(f"     ID: {session['id']}")
        print(
            f"     消息数: {session['message_count']}, 创建时间: {session['created_at'][:19]}\n"
        )


def command(command: str):
    """处理命令"""
    global current_mode

    if command == "exit" or command == "/exit":
        save_current_session()
        save_config(current_mode)
        sys.exit(0)

    # 进入翻译模式
    if command == "/e":
        save_current_session()
        current_mode = TRANSLATE
        init_system_prompt(current_mode)
        print("✅ 已进入翻译模式\n")
        return

    # 进入问答模式
    if command == "/ask":
        save_current_session()
        current_mode = ASK
        init_system_prompt(current_mode)
        print("✅ 已进入问答模式\n")
        return

    # 智能体命令: /agent, /agent -l, /agent name
    if command == "/agent" or command.startswith("/agent "):
        # 解析参数
        parts = command.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        if arg == "-l":
            # /agent -l: 列出可用智能体
            agents = list_agents()
            _display_agents(agents, show_current_marker=True)
        elif arg:
            # /agent name: 进入特定智能体
            switch_agent(arg)
        else:
            # /agent: 进入智能体模式，使用默认智能体
            enter_agent_mode()
        return

    # 交互式选择智能体
    if command == "/agents":
        list_agents_interactive()
        return

    # 角色命令: /role, /role -l, /role name
    if command == "/role" or command.startswith("/role "):
        # 解析参数
        parts = command.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        if arg == "-l":
            # /role -l: 列出可用角色
            roles = list_roles()
            _display_roles(roles, show_current_marker=True)
        elif arg:
            # /role name: 进入指定角色
            switch_role(arg)
        else:
            # /role: 进入角色模式，使用默认角色
            enter_role_mode()
        return

    # 交互式选择角色
    if command == "/roles":
        list_roles_interactive()
        return

    # 语音控制命令: /voice enable, /voice disable
    if command == "/voice" or command.startswith("/voice "):
        parts = command.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""

        if arg == "enable":
            toggle_voice(True)
        elif arg == "disable":
            toggle_voice(False)
        else:
            show_voice_status()
        return

    # 创建新会话
    if command == "/new":
        save_current_session()
        init_system_prompt(current_mode)
        print("✅ 已创建新会话\n")
        return

    # 清除对话历史
    if command == "/clear":
        clear_history()
        print("✅ 已清除对话历史\n")
        return

    # 启动 Telegram Bot
    if command == "/bot":
        if not BOT_TOKEN:
            print("❌ 错误: TELEGRAM_BOT_TOKEN 环境变量未设置")
            print("💡 提示: 请设置 TELEGRAM_BOT_TOKEN 环境变量")
            print("   例如: export TELEGRAM_BOT_TOKEN='your_bot_token_here' \n")
            return
        print("🤖 启动 Telegram Bot...")
        run_bot()
        return

    # 启动 QQ Bot
    if command == "/qqbot":
        if not QQ_APP_ID or not QQ_APP_SECRET:
            print("❌ 错误: QQ_APP_ID 或 QQ_APP_SECRET 环境变量未设置")
            print("💡 提示: 请设置以下环境变量:")
            print("   export QQ_APP_ID='your_app_id'")
            print("   export QQ_APP_SECRET='your_app_secret' \n")
            return
        print("🤖 启动 QQ Bot...")
        run_qq_bot()
        return

    # 启动微信 Bot
    if command == "/webot":
        print("🤖 启动微信 Bot...")
        run_wechat_bot()
        return

    # 显示帮助
    if command == "/help":
        show_help()
        return

    # 列出会话
    if command == "/session":
        list_sessions()
        return

    # 压缩对话
    if command == "/compact":
        summarizer()
        return

    # 加载会话
    if command.startswith("/load "):
        session_id = command[6:].strip()
        if session_id:
            load_session(session_id)
        else:
            print("❌ 请提供会话 ID，例如: /load 20250109_120000_abc123")
        return

    # 列出所有可用的 MCP 服务器
    if command.startswith("/mcp"):
        handle_mcp_command(command)
        return

    # Agent Team 管理
    if command == "/team":
        if not AGENT_TEAM_ENABLED:
            print("❌ Agent Team 未启用，请使用 --agent-team 参数\n")
            return
        team = init_team_manager()
        print(team.list_all())
        return

    # 读取 lead 的 team inbox
    if command == "/inbox":
        if not AGENT_TEAM_ENABLED:
            print("❌ Agent Team 未启用，请使用 --agent-team 参数\n")
            return
        team = init_team_manager()
        inbox = team.bus.read_inbox("lead")
        if inbox:
            print(json.dumps(inbox, indent=2, ensure_ascii=False))
        else:
            print("📭 Inbox is empty")
        return

    # 模型命令: /model, /model -l, /model number
    if command == "/model" or command.startswith("/model "):
        # 解析参数
        parts = command.split(maxsplit=1)
        arg = parts[1].strip() if len(parts) > 1 else ""

        if arg == "-l":
            # /model -l: 列出可用模型
            display_models()
        elif arg:
            # /model id: 切换到指定ID的模型
            switch_model(arg)
        else:
            # /model: 交互式选择模型
            interactive_select_model()
        return

    # 列出自定义命令
    if command == "/commands":
        list_custom_commands()
        return

    # 列出可用的 Skills
    if command == "/skills":
        list_skills()
        return

    # 列出/管理记忆
    if command == "/memories" or command.startswith("/memories "):
        parts = command.split(maxsplit=2)
        if len(parts) > 1 and parts[1] == "-d" and len(parts) > 2:
            # /memories -d name: 删除指定记忆
            result = MEMORY_MANAGER.delete_memory(parts[2])
            print(f"  {result}\n")
        else:
            # /memories: 列出所有记忆
            listing = MEMORY_MANAGER.list_memories()
            print(f"  Memories ({MEMORY_MANAGER.memory_dir}):\n{listing}\n")
        return

    # 新增: 处理自定义命令
    if command.startswith("/"):
        cmd_name = command[1:].split()[0]
        init_command_manager()
        if COMMAND_MANAGER.has_command(cmd_name):
            handle_custom_command(cmd_name, command)
            return

    # 如果不是已知命令，显示错误
    if command.startswith("/"):
        print(f"❌ 未知命令: {command}")
        print("💡 使用 /help 查看可用命令\n")
        return

    # 处理shell命令 (!开头)
    if command.startswith("!"):
        shell(command[1:])  # 提取命令，去掉前面的 !
        return


def handle_mcp_command(command: str):
    parts = command.split()
    if len(parts) > 1 and "-l" in parts:
        MCP_MANAGER.list_mcp_servers()
    else:
        server_names = MCP_MANAGER.interactive_select_server()
        for name in server_names:
            client, tools = MCP_MANAGER.active_clients[name]
            TOOLS.extend(tools)


def show_help():
    """显示帮助信息"""
    command_descriptions = (
        COMMAND_MANAGER.get_command_descriptions()
        if COMMAND_MANAGER
        else "  (暂无自定义命令)"
    )
    help_text = f"""
 📖 Ask Agent 命令帮助
  🔹 交互模式命令：
    /ask          - 进入问答模式
    /agent        - 进入智能体模式
    /agent <name> - 进入指定智能体
    /agent -l     - 列出所有可用智能体
    /agents       - 交互式选择智能体
    /e            - 进入翻译模式
    /role         - 进入角色扮演模式
    /role <name>  - 进入指定角色
    /role -l      - 列出所有可用角色
    /roles        - 交互式选择角色
    /voice        - 显示当前角色语音状态
    /voice enable - 启用角色语音
    /voice disable- 禁用角色语音
    /model        - 交互式选择模型
    /model <id>   - 切换到指定ID的模型
    /model -l     - 列出所有可用模型
    /new          - 创建新会话
    /clear        - 清除当前对话历史
    /compact      - 压缩对话历史，将前3/4的消息压缩为摘要
    /session      - 列出当前模式的所有会话
    /load <id>    - 加载指定会话（使用 /session 查看 ID）
    /commands     - 列出所有自定义命令
    /skills       - 列出所有可用的 Skills
    /memories     - 列出所有跨会话记忆
    /memories -d <name> - 删除指定记忆
    /team         - 列出所有团队成员及状态
    /inbox        - 读取并清空团队收件箱
    /bot          - 启动 Telegram Bot（需设置 TELEGRAM_BOT_TOKEN 环境变量）
    /qqbot        - 启动 QQ Bot（需设置 QQ_APP_ID 和 QQ_APP_SECRET 环境变量）
    /webot        - 启动微信 Bot（扫码登录）
    /help         - 显示此帮助信息
    /exit         - 退出程序（自动保存会话）
    !command      - 执行shell命令（如 !ls, !pwd, !cat file.txt）
    exit          - 退出程序（自动保存会话）

  🔹 自定义命令：
{command_descriptions}

  🔹 MCP 服务器管理：
    /mcp          - 交互式选择并连接 MCP 服务器
    /mcp -l       - 列出所有可用的 MCP 服务器

  🔹 智能体模式功能：
    - 自动使用 Skills 工具加载领域知识（PDF处理、MCP开发等）
    - 支持通过 Task 工具启动子智能体
    - 支持通过 TodoWrite 工具管理任务列表
    - 支持连接和使用 MCP 服务器提供的工具
    - 智能体配置存放在 agents.json，提示词存放在 agents/ 目录
    - 支持 Agent Team: spawn_teammate, send_message, broadcast 等团队协作工具

  🔹 角色扮演模式功能：
    - 使用角色扮演系统提示词与角色对话
    - 每个角色拥有独立的对话历史
    - 角色配置存放在 roles.json，提示词存放在 roles/ 目录

   🔹 会话管理：
     - 会话按模式自动分类保存到 ~/.ask-agent/cache/ 下各模式子目录
     - 切换模式或退出时自动保存当前会话

   🔹 IDE 集成 (ACP):
     ag --acp            - 以 ACP Agent 模式运行，支持 Zed/JetBrains 等 IDE
     支持流式响应、工具调用、模型切换、Plan/Build 模式
 """
    print(help_text)
    return help_text


def _kill_proc(proc: subprocess.Popen):
    """终止进程，先terminate后kill"""
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def execute_cmd(cmd: str, timeout: Optional[int] = None) -> str:
    """执行shell命令并返回输出"""
    try:
        # 检查是否已中断
        _interrupt_ctrl.check()

        # Windows 上使用 PowerShell，其他系统使用默认 shell
        if platform.system() == "Windows":
            proc = subprocess.Popen(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", cmd],
                cwd=WORKDIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
        else:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=WORKDIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        # 注册进程到中断控制器
        _interrupt_ctrl.register_process(proc)

        timeout_sec = timeout or 10

        try:
            stdout, stderr = proc.communicate(timeout=timeout_sec)
            return (stdout + stderr).strip()
        except subprocess.TimeoutExpired:
            _kill_proc(proc)
            return f"Warning: Command timed out after {timeout_sec}s"
        finally:
            # 取消注册进程
            _interrupt_ctrl.unregister_process(proc)
    except InterruptedError:
        return "Command interrupted by user (ESC)"
    except KeyboardInterrupt:
        raise
    except Exception as e:
        return f"Error: {e}"


def shell(cmd: str):
    """处理shell命令，添加到历史并执行"""
    # 将命令添加到消息历史
    messages.append({"role": "user", "content": f"执行shell命令: {cmd}"})

    # 执行命令
    output = execute_cmd(cmd)

    # 输出到终端
    print(output)

    # 将输出添加到消息历史
    messages.append({"role": "user", "content": f"Shell命令执行结果:\n{output}"})


async def bot_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理所有命令"""
    command_text = update.message.text  # 例如: "/start arg1 arg2"
    command_parts = command_text.split()
    cmd = command_parts[0]  # "/start"
    args = command_parts[1:]  # ["arg1", "arg2"]
    chat = update.effective_chat
    is_group = chat.type in ["group", "supergroup"]

    # 根据命令名分发处理
    if cmd == "/start":
        await update.message.reply_text(
            "欢迎！使用/exit命令退出Telegram Bot,/save命令保存聊天会话"
        )
    elif cmd == "/exit" or cmd == "/save":
        save_current_session()
        save_config(current_mode)
        await update.message.reply_text("会话已保存")
        if cmd == "/exit":
            context.application.stop_running()
    elif cmd == "/clear":
        if is_group:
            # 群组中清空群组消息历史
            group_manager = get_telegram_group_manager()
            group_id = str(chat.id)
            group_manager.clear_group_messages(group_id)
            await update.message.reply_text("✅ 已清除群组对话历史")
        else:
            # 私聊中清空全局消息历史
            clear_history()
            await update.message.reply_text("✅ 已清除对话历史")
    elif cmd == "/new":
        if is_group:
            # 群组中清空群组消息历史（重新初始化）
            group_manager = get_telegram_group_manager()
            group_id = str(chat.id)
            group_manager.clear_group_messages(group_id)
            await update.message.reply_text("✅ 已创建新会话")
        else:
            save_current_session()
            init_system_prompt(current_mode)
            await update.message.reply_text("✅ 已创建新会话")
    elif cmd == "/help":
        await update.message.reply_text(f"{show_help()}")
        await update.message.reply_text("注意不要执行交互式命令!")
    else:
        command(command_text)
        await update.message.reply_text(f"执行命令: {command_text}")


def get_image_mime_type(file_path: str) -> str:
    """根据文件扩展名获取图片 MIME 类型（已迁移到 util/image.py）"""
    from util.image import get_image_mime_type as _get_image_mime_type
    return _get_image_mime_type(file_path)


def current_model_supports_image_input() -> bool:
    """检查当前模型是否支持图片输入

    Returns:
        当前模型是否支持图片输入
    """
    model_info = PROVIDER_CONFIG.get_model_info(DEEPSEEK_MODEL)
    if model_info and model_info.modalities:
        return model_info.modalities.supports_image_input()
    return False


def current_model_supports_audio_input() -> bool:
    """检查当前模型是否支持音频输入

    Returns:
        当前模型是否支持音频输入
    """
    model_info = PROVIDER_CONFIG.get_model_info(DEEPSEEK_MODEL)
    if model_info and model_info.modalities:
        return model_info.modalities.supports_audio_input()
    return False


async def download_telegram_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str | None:
    """下载 Telegram 图片并返回 Base64 编码

    Args:
        update: Telegram 更新对象
        context: Telegram 上下文

    Returns:
        Base64 编码的图片数据，失败返回 None
    """
    from util.image import image_bytes_to_base64

    try:
        # 获取最大尺寸的图片
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)

        # 下载图片到内存
        photo_bytes = await file.download_as_bytearray()

        # 转换为 Base64
        base64_data = image_bytes_to_base64(photo_bytes)

        return base64_data
    except Exception as e:
        logger.error(f"下载图片失败: {e}")
        return None


async def send_response(update: Update, context: ContextTypes.DEFAULT_TYPE, response: str, is_mentioned: bool = True):
    """发送响应到 Telegram（文本 + 可选语音）

    Args:
        update: Telegram 更新对象
        context: Telegram 上下文
        response: 模型回复文本
        is_mentioned: 是否被 @提及（默认 True）
    """
    global _telegram_pending_tasks

    # 移除 <think>...</think> 标签及其内容
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)

    # 检查是否需要语音回复
    voice_config = get_current_voice_config()

    if voice_config:
        # 文本 + 语音回复
        await reply_with_voice(update, response, voice_config)
    else:
        # 超过 1024 字符直接发送
        if len(response) > 1024:
            if response.strip():
                if is_mentioned:
                    await update.message.reply_text(response)
                else:
                    chat_id = update.effective_chat.id
                    await context.bot.send_message(chat_id=chat_id, text=response)
        else:
            # 按 \n\n 分割消息
            paragraphs = [p.strip() for p in response.split("\n\n") if p.strip()]

            if not paragraphs:
                return

            chat_id = update.effective_chat.id

            # 被 @提及：第一条用 reply_text（引用），剩余用 send_message
            # 未被 @提及：全部用 send_message
            if is_mentioned:
                await update.message.reply_text(paragraphs[0])
                for para in paragraphs[1:]:
                    await context.bot.send_message(chat_id=chat_id, text=para)
            else:
                for para in paragraphs:
                    await context.bot.send_message(chat_id=chat_id, text=para)

    # 等待所有待处理的异步任务（如图片发送）
    if _telegram_pending_tasks:
        await asyncio.gather(*_telegram_pending_tasks)
        _telegram_pending_tasks = []


def setup_telegram_context(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """设置 Telegram 全局上下文

    Args:
        update: Telegram 更新对象
        context: Telegram 上下文
    """
    global _telegram_update, _telegram_context, _telegram_pending_tasks
    _telegram_update = update
    _telegram_context = context
    _telegram_pending_tasks = []


async def reply_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理接收到的图片消息"""
    user = update.effective_user
    chat = update.effective_chat

    # 检查当前模型是否支持图片输入
    if not current_model_supports_image_input():
        model_info = PROVIDER_CONFIG.get_model_info(DEEPSEEK_MODEL)
        model_name = model_info.name if model_info else DEEPSEEK_MODEL
        await update.message.reply_text(
            f"❌ 当前模型 {model_name} 不支持图片理解\n"
            f"请切换到支持图片输入的模型"
        )
        return

    # 设置全局 Telegram 上下文
    setup_telegram_context(update, context)

    # 群组消息处理
    is_group = chat.type in ["group", "supergroup"]
    if is_group:
        group_manager = get_telegram_group_manager()
        group_id = str(chat.id)
        user_id = str(user.id)

        # 检查用户权限
        if not group_manager.config.is_user_allowed(user_id, group_id):
            return

    # 获取图片说明（如果有）
    caption = update.message.caption or "请描述这张图片的内容"

    # 打印到控制台
    if is_group:
        prefix = f"[群:{group_manager.config.get_group_config(group_id).name or group_id}] "
    else:
        prefix = ""
    print(f"收到图片 | {prefix}用户: {user.username or user.first_name} (ID: {user.id}) | 说明: {caption}")
    print(f"{BLUE}Telegram Bot (图片理解):{RESET}")

    # 下载图片并转换为 Base64
    image_base64 = await download_telegram_photo(update, context)

    if not image_base64:
        await update.message.reply_text("❌ 图片下载失败，请重试")
        return

    # 获取图片 MIME 类型
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    file_path = file.file_path or ""
    mime_type = get_image_mime_type(file_path)

    # 使用独立上下文进行图片理解（不污染对话历史）
    temp_messages = [
        {"role": "system", "content": "你是一个图片识别助手。请详细描述图片的内容，然后回答用户的问题。"},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_base64}"}},
                {"type": "text", "text": "请描述这张图片的内容"}
            ]
        }
    ]

    # 调用模型进行图片理解
    content, image_description, _ = get_streaming_response(temp_messages, [], silent=True, useTools=False)

    if not content:
        await update.message.reply_text("❌ 图片理解失败")
        return

    # 打印识别结果
    print(f"  图片描述: {content}")
    
    # 构建助手消息并添加到历史
    if is_group:
        # 群组消息：添加到群组历史（包含用户信息）
        user_name = user.username or user.first_name or str(user_id)
        group_manager.add_message(group_id, {
            "role": "user",
            "content": f"{user_name}: [图片]\n以下是识别图片的结果，直接根据以下内容回答:\n<image-description>\n{content}\n</image-description>"
        })

        # 获取群组消息历史
        group_messages = group_manager.get_group_messages(group_id)

        # 初始化群组消息历史（如果为空）
        if not group_messages:
            # 使用当前模式的系统提示词
            role_id = get_current_role_id()
            agent_id = get_current_agent_id()
            builder = SystemPromptBuilder(mode=current_mode, role_id=role_id, agent_id=agent_id)
            system_prompt = builder.build()

            # 如果有群组专属提示词，追加到系统提示词后面
            group_prompt = group_manager.get_group_system_prompt(group_id)
            if group_prompt:
                system_prompt = f"{system_prompt}\n\n{group_prompt}"

            group_messages.append({"role": "system", "content": system_prompt})

        # 获取回复（使用群组消息历史）
        response = agent(caption, group_messages)

        # 添加助手回复到群组历史
        group_manager.add_message(group_id, {"role": "assistant", "content": response})
    else:
        # 私聊消息处理（原有逻辑）
        messages.append({"role": "user", "content": f"以下是识别图片的结果，直接根据以下内容回答:\n<image-description>\n{content}\n</image-description>"})
        response = agent(caption)

    # 发送响应（图片消息通常是被 @提及后发送的）
    await send_response(update, context, response, True)
    print()


async def download_telegram_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bytes | None:
    """下载 Telegram 语音并返回音频数据

    Args:
        update: Telegram 更新对象
        context: Telegram 上下文

    Returns:
        audio_bytes，失败返回 None
    """
    try:
        voice = update.message.voice
        if not voice:
            return None

        file = await context.bot.get_file(voice.file_id)

        # 下载语音到内存
        voice_bytes = await file.download_as_bytearray()

        # Telegram 语音默认是 OGG 格式
        return bytes(voice_bytes)
    except Exception as e:
        logger.error(f"下载语音失败: {e}")
        return None


async def reply_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理接收到的语音消息"""
    user = update.effective_user
    chat = update.effective_chat

    # 检查当前模型是否支持音频输入
    if not current_model_supports_audio_input():
        model_info = PROVIDER_CONFIG.get_model_info(DEEPSEEK_MODEL)
        model_name = model_info.name if model_info else DEEPSEEK_MODEL
        await update.message.reply_text(
            f"❌ 当前模型 {model_name} 不支持音频理解\n"
            f"请切换到支持音频输入的模型"
        )
        return

    # 设置全局 Telegram 上下文
    setup_telegram_context(update, context)

    # 获取语音说明（如果有）
    caption = "直接给出语音的内容" #update.message.caption or "请描述这个语音的内容"

    # 打印到控制台
    if chat.type in ["group", "supergroup"]:
        group_manager = get_telegram_group_manager()
        group_id = str(chat.id)
        prefix = f"[群:{group_manager.config.get_group_config(group_id).name or group_id}] "
    else:
        prefix = ""
    print(f"收到语音 | {prefix}用户: {user.username or user.first_name} (ID: {user.id})")
    print(f"{BLUE}Telegram Bot (语音理解):{RESET}")

    # 下载语音并转换为 Base64
    voice_bytes = await download_telegram_voice(update, context)

    if not voice_bytes:
        await update.message.reply_text("❌ 语音下载失败，请重试")
        return

    audio_base64 = base64.b64encode(voice_bytes).decode('utf-8')

    mime_type = 'audio/ogg'

    # 调试信息
    print(f"  音频大小: {len(voice_bytes)} bytes, MIME: {mime_type}")

    # 使用独立上下文进行语音识别（不污染对话历史）
    temp_messages = [
        {"role": "system", "content": "你是一个语音识别助手。请将用户发送的语音内容转换为文字，只输出识别到的文字内容，不要添加任何解释。"},
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{audio_base64}"}},
                {"type": "text", "text": caption}
            ]
        }
    ]

    # 调用模型进行语音识别
    content, reasoning, _ = get_streaming_response(temp_messages, [], silent=True, useTools=False)
    
    recognized_text =  content if content else reasoning
    # 打印识别结果
    print(f"  识别结果: {recognized_text}")
        
    if not recognized_text:
        await update.message.reply_text("❌ 语音识别失败")
        return

    # 群组消息处理
    is_group = chat.type in ["group", "supergroup"]
    mentioned = True  # 语音消息通常是被 @提及后发送的
    if is_group:
        group_manager = get_telegram_group_manager()
        group_id = str(chat.id)
        user_id = str(user.id)

        # 检查用户权限
        if not group_manager.config.is_user_allowed(user_id, group_id):
            return

        # 添加到群组历史（包含用户信息）
        user_name = user.username or user.first_name or str(user_id)
        user_message = f"{user_name}: [语音]\n{recognized_text}"
        group_manager.add_message(group_id, {"role": "user", "content": user_message})

        # 获取群组消息历史
        group_messages = group_manager.get_group_messages(group_id)

        # 初始化群组消息历史（如果为空）
        if not group_messages:
            # 使用当前模式的系统提示词
            role_id = get_current_role_id()
            agent_id = get_current_agent_id()
            builder = SystemPromptBuilder(mode=current_mode, role_id=role_id, agent_id=agent_id)
            system_prompt = builder.build()

            # 如果有群组专属提示词，追加到系统提示词后面
            group_prompt = group_manager.get_group_system_prompt(group_id)
            if group_prompt:
                system_prompt = f"{system_prompt}\n\n{group_prompt}"

            group_messages.append({"role": "system", "content": system_prompt})

        # 获取回复（使用群组消息历史）
        response = agent(recognized_text, group_messages)

        # 添加助手回复到群组历史
        group_manager.add_message(group_id, {"role": "assistant", "content": response})
    else:
        # 私聊消息处理（原有逻辑）
        response = agent(recognized_text)

    # 发送响应
    await send_response(update, context, response, mentioned)
    print()


async def reply_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理接收到的文本消息"""
    user = update.effective_user
    chat = update.effective_chat
    message_text = update.message.text

    # 设置全局 Telegram 上下文
    setup_telegram_context(update, context)

    # 获取缓冲配置
    telegram_config = init_app_config().get_channel_config("telegram")
    buffer_config = telegram_config.message_buffer

    # 群组消息处理
    is_group = chat.type in ["group", "supergroup"]
    if is_group:
        group_manager = get_telegram_group_manager()
        group_id = str(chat.id)
        user_id = str(user.id)
        
        # Info 日志
        user_display = user.username or user.first_name or str(user.id)
        logger.info(f"[群:{group_manager.config.get_group_config(group_id).name or group_id}] {user_display}: {message_text}")

        # 检查是否被 @提及
        bot_info = await context.bot.get_me()
        mentioned = is_bot_mentioned(message_text, update.message.entities, bot_info.username)
        other_mentions = has_other_mentions(message_text, update.message.entities, bot_info.username)

        # 判断是否应该响应
        if not group_manager.should_respond(user_id, group_id, mentioned, other_mentions):
            # 即使不响应，也保存消息到历史记录（用于上下文记忆）
            if group_manager.should_save_to_history(user_id, group_id):
                group_manager.add_message(group_id, {"role": "user", "content": message_text})
            return

        # 判断是否启用缓冲（群组中未被 @提及时启用）
        should_buffer = buffer_config.enabled and not mentioned
    else:
        # 私聊消息处理
        # 判断是否启用缓冲（groupOnly=false 时私聊也启用）
        should_buffer = buffer_config.enabled and not buffer_config.group_only
        mentioned = True  # 私聊默认被提及

        # 注册私聊用户到主动消息调度器
        if _PROACTIVE_SCHEDULER:
            _PROACTIVE_SCHEDULER.register_user("telegram", str(user.id), str(chat.id))

    # 启用缓冲，添加到缓冲区
    if should_buffer:
        user_display = user.username or user.first_name or str(user.id)
        print(f"[缓冲] 消息已缓存: {user_display}: {message_text}")
        
        async def process_buffered_messages(user_key, messages, upd, ctx):
            """处理缓冲的消息"""
            combined_text = "\n".join([m["text"] for m in messages])
            print(f"[缓冲] 处理 {len(messages)} 条消息: {combined_text}")
            if is_group:
                await _process_group_message(upd, ctx, group_manager, group_id, user_id, combined_text, mentioned)
            else:
                response = agent(combined_text)
                await send_response(upd, ctx, response, True)
                print()
        
        chat_buf = buffer_manager.get_or_create_buffer(str(chat.id), buffer_config.timeout)
        chat_buf.set_callback(process_buffered_messages)
        await chat_buf.add_message(str(chat.id), str(user.id), message_text, update, context)
        return

    # 不启用缓冲，直接处理
    if is_group:
        # 打印到控制台
        prefix = f"[群:{group_manager.config.get_group_config(group_id).name or group_id}] "
        print(f"收到消息 | {prefix}用户: {user.username or user.first_name} (ID: {user.id}) | 内容: {message_text}")
        print(f"{BLUE}Telegram Bot:{RESET}")

        # 处理群组消息
        await _process_group_message(update, context, group_manager, group_id, user_id, message_text, mentioned)
    else:
        # 私聊消息处理
        print(f"收到消息 | 用户: {user.username or user.first_name} (ID: {user.id}) | 内容: {message_text}")
        print(f"{BLUE}Telegram Bot:{RESET}")

        # 获取回复
        response = agent(message_text)

        # 发送响应
        await send_response(update, context, response, True)
        print()


async def _process_group_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    group_manager: TelegramGroupManager,
    group_id: str,
    user_id: str,
    message_text: str,
    mentioned: bool,
) -> None:
    """处理群组消息（内部函数）

    Args:
        update: Telegram Update 对象
        context: Telegram Context 对象
        group_manager: 群组管理器
        group_id: 群组 ID
        user_id: 用户 ID
        message_text: 消息文本
        mentioned: 是否被 @提及
    """
    user = update.effective_user

    # 获取群组消息历史
    group_messages = group_manager.get_group_messages(group_id)

    # 初始化群组消息历史（如果为空）
    if not group_messages:
        # 使用当前模式的系统提示词
        role_id = get_current_role_id()
        agent_id = get_current_agent_id()
        builder = SystemPromptBuilder(mode=current_mode, role_id=role_id, agent_id=agent_id)
        system_prompt = builder.build()

        # 如果有群组专属提示词，追加到系统提示词后面
        group_prompt = group_manager.get_group_system_prompt(group_id)
        if group_prompt:
            system_prompt = f"{system_prompt}\n\n{group_prompt}"

        group_messages.append({"role": "system", "content": system_prompt})

    # 添加用户消息（包含用户信息，让 AI 区分谁发的消息）
    user_name = user.username or user.first_name or str(user_id)
    user_message = f"{user_name}: {message_text}"
    group_manager.add_message(group_id, {"role": "user", "content": user_message})

    # 获取回复（使用群组消息历史）
    response = agent(message_text, group_messages)

    # 添加助手回复到群组历史
    group_manager.add_message(group_id, {"role": "assistant", "content": response})

    # 发送响应
    await send_response(update, context, response, mentioned)
    print()


def get_current_voice_config() -> Optional[dict]:
    """获取当前角色的语音配置"""
    global ROLE_MANAGER
    if not ROLE_MANAGER or not ROLE_MANAGER.current_role:
        return None
    return ROLE_MANAGER.current_role.get_voice_config()


def get_current_voice_config_raw() -> Optional[dict]:
    """获取当前角色的语音配置（忽略 enabled 状态，用于 send_voice 工具）"""
    global ROLE_MANAGER
    if not ROLE_MANAGER or not ROLE_MANAGER.current_role:
        return None
    return ROLE_MANAGER.current_role.get_voice_config_raw()


def toggle_voice(enabled: bool):
    """启用或禁用当前角色的语音

    Args:
        enabled: 是否启用语音
    """
    global ROLE_MANAGER

    if current_mode != ROLE:
        print("❌ 请先进入角色扮演模式: /role\n")
        return

    if not ROLE_MANAGER:
        print("❌ 当前没有选择角色\n")
        return

    result = ROLE_MANAGER.toggle_voice(enabled)
    _save_section_to_config()
    print(f"{result}\n")


def show_voice_status():
    """显示当前角色的语音状态"""
    global ROLE_MANAGER

    if current_mode != ROLE:
        print("❌ 请先进入角色扮演模式: /role\n")
        return

    if not ROLE_MANAGER:
        print("❌ 当前没有选择角色\n")
        return

    result = ROLE_MANAGER.get_voice_status()
    print(f"{result}\n")


def play_voice_in_terminal(text: str, voice_config: dict):
    """在终端模式下播放语音

    Args:
        text: 要转换为语音的文本
        voice_config: 语音配置
    """
    from util.tts import text_to_speech_and_play, get_tts_api_config

    try:
        print(f"\033[34m🎤 生成语音...\033[0m")

        # 获取 TTS 模型名称
        model = voice_config.get("model")

        # 获取 TTS API 配置
        api_config = get_tts_api_config(PROVIDER_CONFIG, model)

        if not api_config:
            print(f"\033[31m✗ TTS API 未配置，跳过语音\033[0m")
            return

        success = text_to_speech_and_play(text, voice_config, api_config)

        if success:
            print(f"\033[32m✓ 语音播放完成\033[0m")
        else:
            print(f"\033[31m✗ 语音播放失败\033[0m")

    except Exception as e:
        logger.error(f"语音播放失败: {e}")
        print(f"\033[31m✗ 语音播放失败: {e}\033[0m")


def try_play_voice(response: str):
    """尝试播放语音（终端模式，仅角色扮演模式）

    Args:
        response: 模型回复文本
    """
    # 仅在角色扮演模式下播放语音
    if current_mode != ROLE:
        return

    voice_config = get_current_voice_config()
    if not voice_config:
        return

    # 清理响应文本
    clean_response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    if clean_response.strip():
        play_voice_in_terminal(clean_response.strip(), voice_config)


async def reply_with_voice(update: Update, text: str, voice_config: dict):
    """发送文本和语音回复

    Args:
        update: Telegram 更新对象
        text: 回复文本
        voice_config: 语音配置
    """
    from util.tts import text_to_speech, get_tts_api_config, strip_parentheses

    # 先发送文本
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if para.strip():
            await update.message.reply_text(para)

    # 发送语音
    try:
        print(f"\033[34m🎤 生成语音...\033[0m")

        # 获取 TTS 模型名称
        model = voice_config.get("model")

        # 获取 TTS API 配置
        api_config = get_tts_api_config(PROVIDER_CONFIG, model)

        if not api_config:
            print(f"\033[31m✗ TTS API 未配置，跳过语音\033[0m")
            return

        # 去掉括号内容后再生成语音
        tts_text = strip_parentheses(text)
        if not tts_text:
            return
        audio_bytes = text_to_speech(tts_text, voice_config, api_config)

        if audio_bytes:
            from util.tts import convert_audio_format
            ogg_bytes = convert_audio_format(audio_bytes, "mp3", "ogg")
            # Telegram 语音消息需要 ogg/opus 格式
            await update.message.reply_voice(ogg_bytes or audio_bytes)
            print(f"\033[32m✓ 语音已发送\033[0m")
        else:
            print(f"\033[31m✗ 语音生成失败\033[0m")

    except Exception as e:
        logger.error(f"发送语音失败: {e}")
        print(f"\033[31m✗ 语音发送失败: {e}\033[0m")


def run_bot():
    from telegram.error import TimedOut, NetworkError, Conflict

    # 初始化应用配置和群组管理器
    config = init_app_config()
    telegram_config = config.get_channel_config("telegram")
    group_manager = get_telegram_group_manager()

    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()

    # 定义支持的聊天类型
    chat_types = filters.ChatType.PRIVATE
    if telegram_config.group_policy != "disabled":
        chat_types = chat_types | filters.ChatType.GROUPS

    # 添加处理器 - 捕获所有以 / 开头的命令
    application.add_handler(MessageHandler(filters.COMMAND & chat_types, bot_command))
    application.add_handler(MessageHandler(filters.PHOTO & chat_types, reply_photo))
    application.add_handler(MessageHandler(filters.VOICE & chat_types, reply_voice))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & chat_types, reply_text))

    # 注册关闭时保存群组会话的回调
    async def on_shutdown(app):
        group_manager.save_all_sessions()
        logger.info("所有群组会话已保存")

    application.post_shutdown = on_shutdown

    # 初始化主动消息调度器
    scheduler = init_proactive_scheduler()
    if scheduler:
        async def _telegram_send_callback(chat_id: str, message: str, chat_type: str = "private"):
            if _telegram_update and _telegram_context:
                await send_response(_telegram_update, _telegram_context, message, True)

        scheduler.register_send_callback("telegram", _telegram_send_callback)

        async def _on_bot_ready(app):
            """Bot 就绪后启动主动消息调度器"""
            await scheduler.start()

        async def _on_bot_shutdown_with_scheduler(app):
            """Bot 关闭时停止主动消息调度器"""
            await scheduler.stop()
            group_manager.save_all_sessions()
            logger.info("所有群组会话已保存")

        application.post_init = _on_bot_ready
        application.post_shutdown = _on_bot_shutdown_with_scheduler

    print("机器人已启动！按 Ctrl+C 停止")
    if telegram_config.group_policy != "disabled":
        print(f"群组策略: {telegram_config.group_policy}")

    # 运行机器人
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Conflict as e:
        print("❌ Telegram Bot 冲突：已有另一个 bot 实例在运行")
        print("   请先停止之前的 bot 实例，然后再启动")
        logger.error(f"Telegram Bot Conflict: {e}")
    except TimedOut:
        print("❌ Telegram Bot 连接超时，请检查网络连接或代理设置")
    except NetworkError as e:
        print(f"❌ Telegram Bot 网络错误: {e}")
    except Exception as e:
        print(f"❌ Telegram Bot 发生错误: {e}")


_MARKDOWN_RE = re.compile(
    r"(?:"
    r"\*\*.*?\*\*"          # **bold**
    r"|`[^`]+`"             # `code`
    r"|```[\s\S]*?```"      # code block
    r"|^#{1,6}\s"           # heading (at line start)
    r"|^\s*[-*+]\s"         # unordered list
    r"|^\s*\d+\.\s"         # ordered list
    r"|^\s*>\s"             # blockquote
    r"|\[.+?\]\(.+?\)"      # [link](url)
    r")",
    re.MULTILINE,
)


def _is_markdown(text: str) -> bool:
    """检测文本是否包含 Markdown 语法"""
    return bool(_MARKDOWN_RE.search(text))



async def _send_qq_response(context, response: str):
    """发送响应到 QQ（markdown 或文本 + 语音）"""
    global _qq_bot

    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)

    try:
        if _is_markdown(response):
            await context.send_markdown(response)
        else:
            for para in response.split("\n\n"):
                para = para.strip()
                if para:
                    await context.send_text(para)
    except Exception as e:
        logger.error(f"发送QQ消息失败: {e}")
        await context.send_text(response)

    # 语音回复
    voice_config = get_current_voice_config()
    if voice_config:
        try:
            from util.tts import text_to_speech, get_tts_api_config, strip_parentheses
            model = voice_config.get("model")
            api_config = get_tts_api_config(PROVIDER_CONFIG, model)
            if api_config:
                print(f"\033[34m🎤 生成语音...\033[0m")
                tts_text = strip_parentheses(response)
                if tts_text:
                    audio_bytes = text_to_speech(tts_text, voice_config, api_config)
                    if audio_bytes:
                        await context.send_voice(audio_bytes)
                        print(f"\033[32m✓ 语音已发送\033[0m")
        except Exception as e:
            logger.error(f"QQ语音发送失败: {e}")


async def _qq_bot_command(update, context):
    """QQ 命令分发（所有 / 开头的消息）"""
    global _qq_current_openid
    _qq_current_openid = update.effective_chat.openid

    user = update.effective_user
    chat = update.effective_chat
    prefix = f"[群:{chat.openid}] " if chat.type == "group" else ""
    print(f"收到命令 | {prefix}用户: {user.id} | 内容: {update.message.text}")
    print(f"{BLUE}QQ Bot:{RESET}")

    cmd = update.message.text.split()[0]

    if cmd == "/start":
        await context.send_text("欢迎！使用/exit命令退出QQ Bot，/save命令保存聊天会话")
    elif cmd == "/exit":
        save_current_session()
        save_config(current_mode)
        await context.send_text("会话已保存")
        if context.application:
            await context.application.stop()
    elif cmd == "/save":
        save_current_session()
        save_config(current_mode)
        await context.send_text("会话已保存")
    elif cmd == "/help":
        await context.send_markdown(show_help())
    elif cmd == "/new":
        save_current_session()
        init_system_prompt(current_mode)
        await context.send_text("✅ 已创建新会话")
    elif cmd == "/clear":
        clear_history()
        await context.send_text("✅ 已清除对话历史")
    else:
        # 其他命令交给 command() 统一处理（/ask, /agent, /role 等）
        try:
            command(update.message.text)
            await context.send_text(f"执行命令: {update.message.text}")
        except Exception as e:
            await context.send_text(f"命令执行失败: {e}")


async def _qq_handle_photo(update, context):
    """QQ 图片消息处理"""
    global _qq_current_openid
    _qq_current_openid = update.effective_chat.openid

    user = update.effective_user
    chat = update.effective_chat
    caption = update.message.text or ""
    prefix = f"[群:{chat.openid}] " if chat.type == "group" else ""
    print(f"收到图片 | {prefix}用户: {user.id} | 说明: {caption}")
    print(f"{BLUE}QQ Bot (图片理解):{RESET}")

    image_attachments = update.message.photo
    if not image_attachments:
        return

    if not current_model_supports_image_input():
        model_info = PROVIDER_CONFIG.get_model_info(DEEPSEEK_MODEL)
        model_name = model_info.name if model_info else DEEPSEEK_MODEL
        await context.send_text(f"❌ 当前模型 {model_name} 不支持图片理解，请切换到支持图片输入的模型")
        return

    print(f"\033[34m→ QQ 图片识别 ({len(image_attachments)} 张)\033[0m")

    multimodal_content = []
    for att in image_attachments:
        url = att.get("url")
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            b64 = base64.b64encode(resp.content).decode("utf-8")
            ct = att.get("content_type", "image/jpeg")
            multimodal_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{ct};base64,{b64}"}
            })
        except Exception as e:
            logger.error(f"下载QQ图片失败: {e}")
            await context.send_text(f"❌ 图片下载失败: {e}")
            return

    caption = update.message.text or "请描述这张图片的内容"
    multimodal_content.append({"type": "text", "text": caption})

    temp_messages = [
        {"role": "system", "content": "你是一个图片识别助手。请详细描述图片的内容，然后回答用户的问题。"},
        {"role": "user", "content": multimodal_content}
    ]
    content, _, _ = get_streaming_response(temp_messages, [], silent=True, useTools=False)

    if not content:
        await context.send_text("❌ 图片理解失败")
        return

    print(f"  图片描述: {content}")

    if chat.type == "group":
        # 群组消息：添加用户信息（QQ Bot 只有 openid，没有用户名）
        user_message = f"用户{user.id}: [图片]\n以下是识别图片的结果，直接根据以下内容回答:\n<image-description>\n{content}\n</image-description>"
        messages.append({"role": "user", "content": user_message})
        response = agent(caption, messages)
    else:
        messages.append({"role": "user", "content": f"以下是识别图片的结果，直接根据以下内容回答:\n<image-description>\n{content}\n</image-description>"})
        response = agent(caption)

    await _send_qq_response(context, response)
    print()


def _convert_to_wav(audio_bytes: bytes) -> bytes | None:
    """尝试将音频转换为 WAV 格式（silk → pcm → wav）

    Returns:
        WAV 字节数据，失败返回 None（表示使用原始音频）
    """
    # 检测 silk 头: Tencent silk 以 \x02 开头，标准 silk 以 #!SILK 开头
    is_silk = (
        audio_bytes[:1] == b'\x02' and audio_bytes[1:9] == b'#!SILK_V'
    ) or (
        audio_bytes[:8] == b'#!SILK_V'
    )
    if not is_silk:
        return None

    try:
        import pysilk
        import io
        import subprocess

        # silk → pcm
        silk_io = io.BytesIO(audio_bytes)
        pcm_io = io.BytesIO()
        pysilk.decode(silk_io, pcm_io, sample_rate=24000)
        pcm_bytes = pcm_io.getvalue()

        # pcm → wav (via ffmpeg)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "s16le", "-ar", "24000", "-ac", "1",
             "-i", "pipe:0", "-f", "wav", "pipe:1"],
            input=pcm_bytes,
            capture_output=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout

        logger.warning(f"ffmpeg pcm→wav 失败: {proc.stderr.decode(errors='replace')[:200]}")
        return pcm_bytes  # 返回 pcm 作为兜底

    except ImportError:
        logger.warning("pysilk 未安装，无法解码 silk 语音。pip install silk-python")
        return None
    except Exception as e:
        logger.error(f"silk 转换失败: {e}")
        return None


async def _qq_handle_voice(update, context):
    """QQ 语音消息处理"""
    global _qq_current_openid
    _qq_current_openid = update.effective_chat.openid

    user = update.effective_user
    chat = update.effective_chat
    prefix = f"[群:{chat.openid}] " if chat.type == "group" else ""
    print(f"收到语音 | {prefix}用户: {user.id}")
    print(f"{BLUE}QQ Bot (语音理解):{RESET}")

    voice_att = update.message.voice
    if not voice_att:
        return

    print(f"\033[34m→ QQ 语音识别\033[0m")

    # 方案1: 优先使用 QQ 服务端 ASR 结果（无需下载音频）
    asr_text = voice_att.get("asr_refer_text")
    if asr_text:
        print(f"  QQ ASR 结果: {asr_text}")
        if chat.type == "group":
            # 群组消息：添加用户信息（QQ Bot 只有 openid，没有用户名）
            user_message = f"用户{user.id}: [语音]\n{asr_text}"
            response = agent(user_message)
        else:
            response = agent(asr_text)
        await _send_qq_response(context, response)
        print()
        return

    # 方案2: 下载音频，silk 解码后用模型识别
    if not current_model_supports_audio_input():
        model_info = PROVIDER_CONFIG.get_model_info(DEEPSEEK_MODEL)
        model_name = model_info.name if model_info else DEEPSEEK_MODEL
        await context.send_text(f"❌ 当前模型 {model_name} 不支持音频理解，请切换到支持音频输入的模型")
        return

    url = voice_att.get("voice_wav_url") or voice_att.get("url")
    if not url:
        await context.send_text("❌ 语音下载失败：无URL")
        return

    logger.info(f"  下载语音: {url[:60]}...")

    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        audio_bytes = resp.content
    except Exception as e:
        logger.error(f"下载QQ语音失败: {e}")
        await context.send_text(f"❌ 语音下载失败: {e}")
        return

    # silk 格式检测和转换
    wav_bytes = _convert_to_wav(audio_bytes)
    if wav_bytes:
        audio_bytes = wav_bytes
        mime_type = "audio/wav"
        logger.info(f"  silk → WAV 转换成功, 大小: {len(audio_bytes)} bytes")
    else:
        mime_type = "audio/ogg"
        logger.info(f"  直接使用原始音频, 大小: {len(audio_bytes)} bytes")

    audio_base64 = base64.b64encode(audio_bytes).decode("utf-8")

    temp_messages = [
        {"role": "system", "content": "你是一个语音识别助手。请将用户发送的语音内容转换为文字，只输出识别到的文字内容，不要添加任何解释。"},
        {
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": f"data:{mime_type};base64,{audio_base64}"}},
                {"type": "text", "text": "直接给出语音的内容"}
            ]
        }
    ]
    content, reasoning, _ = get_streaming_response(temp_messages, [], silent=True, useTools=False)
    recognized_text = content if content else reasoning

    print(f"  识别结果: {recognized_text}")

    if not recognized_text:
        await context.send_text("❌ 语音识别失败")
        return

    if chat.type == "group":
        # 群组消息：添加用户信息（QQ Bot 只有 openid，没有用户名）
        user_message = f"用户{user.id}: [语音]\n{recognized_text}"
        response = agent(user_message)
    else:
        response = agent(recognized_text)

    await _send_qq_response(context, response)
    print()


async def _qq_handle_text(update, context):
    """QQ 文本消息处理"""
    global _qq_current_openid
    _qq_current_openid = update.effective_chat.openid

    user = update.effective_user
    chat = update.effective_chat
    is_group = chat.type == "group"
    prefix = f"[群:{chat.openid}] " if is_group else ""
    print(f"收到消息 | {prefix}用户: {user.id} | 内容: {update.message.text}")
    print(f"{BLUE}QQ Bot:{RESET}")

    # 注册私聊用户到主动消息调度器
    if not is_group and _PROACTIVE_SCHEDULER:
        _PROACTIVE_SCHEDULER.register_user("qqbot", chat.openid, chat.openid)

    # 获取缓冲配置
    qqbot_config = init_app_config().get_channel_config("qqbot")
    buffer_config = qqbot_config.message_buffer

    # 判断是否启用缓冲
    should_buffer = buffer_config.enabled and (is_group or not buffer_config.group_only)

    if should_buffer:
        # 启用缓冲，添加到缓冲区
        print(f"[缓冲] 消息已缓存: 用户{user.id}: {update.message.text}")

        async def process_buffered_messages(user_key, messages, upd, ctx):
            """处理缓冲的消息"""
            combined_text = "\n".join([m["text"] for m in messages])
            print(f"[缓冲] 处理 {len(messages)} 条消息: {combined_text}")
            # 群组消息添加用户信息，私聊直接使用
            user_message = f"用户{user.id}: {combined_text}" if is_group else combined_text
            response = agent(user_message)
            await _send_qq_response(ctx, response)
            print()

        chat_buf = buffer_manager.get_or_create_buffer(str(chat.id), buffer_config.timeout)
        chat_buf.set_callback(process_buffered_messages)
        await chat_buf.add_message(str(chat.id), str(user.id), update.message.text, update, context)
        return

    # 不启用缓冲，直接处理
    user_message = f"用户{user.id}: {update.message.text}" if is_group else update.message.text
    response = agent(user_message)

    await _send_qq_response(context, response)
    print()


def run_qq_bot():
    """运行QQ Bot"""
    from core.bot.qqbot import Application, MessageHandler, filters

    assert QQ_APP_ID is not None
    assert QQ_APP_SECRET is not None

    app = Application.builder().token(QQ_APP_ID, QQ_APP_SECRET).build()

    app.add_handler(MessageHandler(filters.command, _qq_bot_command))
    app.add_handler(MessageHandler(filters.photo, _qq_handle_photo))
    app.add_handler(MessageHandler(filters.voice, _qq_handle_voice))
    app.add_handler(MessageHandler(filters.text, _qq_handle_text))

    global _qq_bot
    _qq_bot = app.bot

    # 初始化主动消息调度器
    scheduler = init_proactive_scheduler()

    if scheduler:
        # 注册 QQ 发送回调（复用 _send_qq_response）
        async def _qq_send_callback(openid: str, message: str, chat_type: str = "private"):
            if not _qq_bot:
                return
            from core.bot.qqbot.types import SendProxy
            proxy = SendProxy(_qq_bot, openid, chat_type)
            await _send_qq_response(proxy, message)

        scheduler.register_send_callback("qqbot", _qq_send_callback)

    app.run_polling(scheduler=scheduler)


# 微信 Bot 相关全局变量
_wechat_bot: Optional[Any] = None
_wechat_current_user_id: Optional[str] = None
_wechat_current_context_token: Optional[str] = None


async def _wechat_handle_message(message):
    """微信消息分发"""
    global _wechat_current_user_id, _wechat_current_context_token
    _wechat_current_user_id = message.user_id
    _wechat_current_context_token = message.context_token

    if message.type == "text":
        await _wechat_handle_text(message)
    elif message.type == "image":
        if _wechat_bot:
            await _wechat_bot.reply(message, "❌ 暂不支持图片消息")
    elif message.type == "voice":
        if _wechat_bot:
            await _wechat_bot.reply(message, "❌ 暂不支持语音消息")
    elif message.type in ("file", "video"):
        if _wechat_bot:
            await _wechat_bot.reply(message, "❌ 暂不支持文件/视频消息")
    else:
        print(f"忽略微信消息类型: {message.type}")


async def _send_wechat_response(bot, message, response: str):
    """发送响应到微信（分段发送，支持换行）"""
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)

    for para in response.split("\n\n"):
        para = para.strip()
        if para:
            await bot.reply(message, para)


async def _wechat_handle_text(message):
    """微信文本消息处理"""
    user_id = message.user_id
    text = message.text.strip()

    print(f"收到微信消息 | 用户: {user_id} | 内容: {text}")
    print(f"{BLUE}微信 Bot:{RESET}")

    # 注册用户到主动消息调度器
    if _PROACTIVE_SCHEDULER:
        _PROACTIVE_SCHEDULER.register_user("wechat", user_id, user_id)

    # 命令处理
    if text.startswith("/"):
        await _wechat_handle_command(message)
        return

    # 发送typing状态
    if _wechat_bot:
        await _wechat_bot.send_typing(user_id, message.context_token, 1)

    response = agent(text)

    if _wechat_bot:
        await _send_wechat_response(_wechat_bot, message, response)
        await _wechat_bot.send_typing(user_id, message.context_token, 2)

    print()


async def _wechat_handle_command(message):
    """微信命令处理"""
    text = message.text.strip()
    cmd = text.split()[0]

    if cmd == "/start":
        await _wechat_bot.reply(message, "欢迎！使用/exit命令退出微信 Bot，/save命令保存聊天会话")
    elif cmd == "/exit":
        save_current_session()
        save_config(current_mode)
        await _wechat_bot.reply(message, "会话已保存")
        if _wechat_bot:
            await _wechat_bot.stop()
    elif cmd == "/save":
        save_current_session()
        save_config(current_mode)
        await _wechat_bot.reply(message, "会话已保存")
    elif cmd == "/help":
        await _wechat_bot.reply(message, show_help())
    elif cmd == "/new":
        save_current_session()
        init_system_prompt(current_mode)
        await _wechat_bot.reply(message, "已创建新会话")
    elif cmd == "/clear":
        clear_history()
        await _wechat_bot.reply(message, "已清除对话历史")
    else:
        try:
            command(text)
            await _wechat_bot.reply(message, f"执行命令: {text}")
        except Exception as e:
            await _wechat_bot.reply(message, f"命令执行失败: {e}")


def run_wechat_bot():
    """运行微信Bot"""
    from core.bot.wechat_bot import WeChatBot
    import asyncio

    async def _run():
        global _wechat_bot

        bot = WeChatBot()
        _wechat_bot = bot

        # 登录
        print("正在登录微信Bot...")
        credentials = await bot.login()
        print(f"✅ 登录成功: {credentials.user_id}")

        # 初始化主动消息调度器
        scheduler = init_proactive_scheduler()

        if scheduler:
            async def _wechat_send_callback(user_id: str, message: str, chat_type: str = "private"):
                if not _wechat_bot:
                    return
                await _wechat_bot.send_text(user_id, message, "")

            scheduler.register_send_callback("wechat", _wechat_send_callback)

        # 启动消息处理
        print("微信Bot已启动！按 Ctrl+C 停止")
        stop_event = asyncio.Event()

        try:
            await bot.start(_wechat_handle_message)
            if scheduler:
                await scheduler.start()
            await stop_event.wait()
        finally:
            if scheduler:
                await scheduler.stop()
            await bot.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n微信Bot已停止")
    except Exception as e:
        print(f"❌ 微信Bot发生错误: {e}")


def _drain_team_inbox(messages: list) -> None:
    """Drain the lead's team inbox and inject messages into conversation."""
    if not TEAM_MANAGER:
        return
    inbox = TEAM_MANAGER.bus.read_inbox("lead")
    if not inbox:
        return
    notif_text = "\n".join(
        f"[team:{msg.get('from', '?')}] {msg.get('content', '')}"
        for msg in inbox
    )
    messages.append({
        "role": "user",
        "content": f"<team-inbox>\n{notif_text}\n</team-inbox>",
    })


def agent(prompt: str, agent_messages: Optional[List[Dict]] = None) -> str:
    """处理问题，添加到历史并获取回答

    Args:
        prompt: 用户输入的问题
        agent_messages: 消息列表，如果为 None 则使用全局 messages

    Returns:
        AI 回复内容
    """
    # 使用传入的 messages 或全局 messages
    messages = agent_messages if agent_messages is not None else globals()["messages"]

    # 重置中断控制器
    _interrupt_ctrl.reset()

    # 启动ESC监听线程，持续到整个agent()调用结束
    _esc_stop = threading.Event()
    t = None
    if sys.stdin.isatty():
        t = threading.Thread(target=_start_esc_listener, args=(_esc_stop,), daemon=True)
        t.start()

    # 记录当前问题的工具调用轮次
    sub_turn = 1
    # 记录本轮推理开始时的消息索引
    reasoning_start_index = len(messages)

    try:
        # 将用户新消息添加到消息列表
        messages.append({"role": "user", "content": prompt})

        # Build active tools list
        active_tools = TOOLS[:]
        
        # ROLE mode: add media sending tools (send_image, send_voice, send_file)
        if current_mode == ROLE:
            active_tools = active_tools + BOT_TOOLS
        
        # AGENT mode with team: add team tools
        if current_mode == AGENT and AGENT_TEAM_ENABLED:
            active_tools = active_tools + TEAM_TOOLS

        while True:
            # 检查是否被中断
            if _interrupt_ctrl.is_interrupted:
                break

            # 每轮模型调用前，排空后台任务通知
            drain_background_notifications(messages, BG_MANAGER)

            # 每轮模型调用前，排空 team inbox 并注入 messages
            if TEAM_MANAGER and AGENT_TEAM_ENABLED:
                _drain_team_inbox(messages)

            content, reasoning_content, tool_calls = get_streaming_response(
                messages, active_tools
            )

            # 如果被中断且没有内容，直接返回
            if _interrupt_ctrl.is_interrupted and not content and not tool_calls:
                return ""

            # 构建助手消息并添加到历史
            assistant_msg = {"role": "assistant", "content": content}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
                # 思考模式下，有工具调用时回传 reasoning_content
                assistant_msg["reasoning_content"] = reasoning_content
            if sub_turn > 1 and not tool_calls: # 工具调用结束时，此时tool_calls=[]，需要额外判定追加 reasoning_content
                assistant_msg["reasoning_content"] = reasoning_content        
            messages.append(assistant_msg)
            logger.debug("添加助手回复: %s", assistant_msg)

            # 如果没有工具调用或者被中断，结束循环
            if not tool_calls or _interrupt_ctrl.is_interrupted:
                return content

            for tool_call in tool_calls:
                # 检查是否被中断
                if _interrupt_ctrl.is_interrupted:
                    # 添加中断标记的工具结果
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": "[Interrupted by user]",
                    })
                    break

                name = tool_call["function"]["name"]
                args = json.loads(tool_call["function"]["arguments"])
                logger.info("执行工具: %s, 参数: %s", name, args)

                # Task and Skill tools have special display handling
                if name == "Task":
                    print(f"\n> Task: {args.get('description', 'subtask')}")
                elif name == "Skill":
                    print(f"\n> Loading skill: {args.get('skill', '?')}")
                else:
                    print(f"\n> {name}")

                output = execute_tool(name, args)

                if name == "Skill":
                    print(f"  Skill loaded ({len(output)} chars)")
                elif name != "Task":
                    print(truncate_output(output))

                tool_result = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": output,
                }

                logger.debug("Add tool output: %s", tool_result)
                messages.append(tool_result)

            sub_turn += 1
    except KeyboardInterrupt:
        print("\n⚠️ Task execution interrupted by user")
        # 保留已有的内容返回
        if messages and messages[-1].get("role") == "assistant":
            return messages[-1].get("content", "")
        return ""
    finally:
        # 停止 ESC 监听线程
        _esc_stop.set()
        if t is not None:
            t.join(timeout=0.5)


def cleanup_reasoning_content(messages: list, start_index: int, tool_call_round: int):
    """
    清理推理内容

    Args:
        messages: 消息列表
        start_index: 本轮问题开始的索引
        tool_call_round: 工具调用轮次
    """
    if tool_call_round == 1:
        logger.debug("本轮问题没有工具调用，不需要清理")
        messages[start_index].pop(
            "reasoning_content", None
        )  # 清理可能被esc键中断的工具调用的推理内容
        return

    logger.info("清理推理内容，共清理%d轮", tool_call_round)

    # 遍历从start_index之后的所有助手消息
    for i in range(start_index, len(messages)):
        if messages[i].get("role") == "assistant":
            messages[i].pop("reasoning_content", None)

    logger.info("推理内容清理完成")


def sanitize_memory():
    """不记忆模式时清理对话历史"""
    if not memory:
        init_system_prompt(current_mode)  # 重新初始化系统提示词


def is_command(command: str) -> bool:
    """检查输入是否为命令"""
    return (
        command.startswith("/") or command.startswith("!") or command.lower() == "exit"
    )


def update_model_prompt():
    """更新当前模型和provider的提示符"""
    global model_prompt
    try:
        model_id = (
            PROVIDER_CONFIG.default_model
            if PROVIDER_CONFIG.default_model
            else DEEPSEEK_MODEL
        )
        model_info = PROVIDER_CONFIG.get_model_info(model_id)
        if model_info:
            model_name = model_info.name if model_info.name else DEEPSEEK_MODEL
            provider = PROVIDER_CONFIG.get_provider(model_info.provider_id)
            provider_name = provider.name if provider else model_info.provider_id
            model_prompt = f"{model_name} \033[90m{provider_name}\033[0m"
        else:
            model_prompt = DEEPSEEK_MODEL
    except Exception as e:
        logger.warning(f"更新模型提示符失败: {e}, 使用默认值")
        model_prompt = DEEPSEEK_MODEL


def get_thinking_mode() -> str:
    """获取当前模型的 thinking 模式配置

    优先级: provider 级别 > 全局级别 > 默认(enabled)
    
    Returns:
        "enabled" 或 "disabled"
    """
    try:
        model_id = PROVIDER_CONFIG.default_model if PROVIDER_CONFIG.default_model else DEEPSEEK_MODEL
        api_config = PROVIDER_CONFIG.get_api_config(model_id)
        if api_config:
            return api_config.get("thinking", PROVIDER_CONFIG.thinking)
        return PROVIDER_CONFIG.thinking
    except Exception:
        return "enabled"


def load_config() -> dict:
    """加载配置文件"""
    if not CONFIG_FILE.exists():
        return {"mode": ASK}

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            return config
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"加载配置文件失败: {e}")
        return {"mode": ASK}


def save_config(mode: int):
    """保存配置文件"""
    try:
        # 读取现有配置以保留 channels 等其他配置
        existing_config = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    existing_config = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # 更新 mode 和 role_id
        current_role_id_val = get_current_role_id()
        existing_config["mode"] = mode
        if mode == ROLE and current_role_id_val:
            existing_config["role_id"] = current_role_id_val
        elif "role_id" in existing_config:
            # 如果不是角色模式，移除 role_id
            del existing_config["role_id"]

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_config, f, indent=2, ensure_ascii=False)
        logger.info(f"配置已保存: mode={mode}")
    except IOError as e:
        logger.warning(f"保存配置文件失败: {e}")


def get_mode_prompt() -> str:
    """获取当前模式的提示符"""
    prefix = "💬^"
    if current_mode == TRANSLATE:
        prompt = "(Translate)"
    elif current_mode == AGENT:
        prompt = "(Agent)"
    elif current_mode == ROLE:
        return f"{YELLOW}You:{RESET}\n"
    else:
        prompt = "(Ask)"
    return f"{prefix} {prompt}:\n"


def generate_title():
    """异步生成会话标题，不阻塞主对话"""
    if current_mode == ROLE:
        return

    def _generate():
        try:
            global title_generated
            if not title_generated and SESSION_MANAGER:
                title = llm_generate_title(messages)
                SESSION_MANAGER.current_session_name = title
                title_generated = True
                logger.info(f"生成会话标题: {title}")
        except Exception as e:
            logger.warning(f"异步生成标题失败: {e}")

    thread = threading.Thread(target=_generate, daemon=True)
    thread.start()


def chat_loop():
    """主聊天循环，支持完整的对话上下文和对话命令"""

    while True:
        user_input = _PROMPT_SESSION.prompt(ANSI(f"{get_mode_prompt()}")).strip()
        if not user_input:
            continue

        # 处理特殊命令
        if is_command(user_input):
            command(user_input)
            continue

        _print_prompt()
        try:
            response = agent(user_input)
            try_play_voice(response)
            sanitize_memory()
            generate_title()
            _print_newline()
        except Exception as e:
            logger.error(f"Agent error: {e}")
            print(f"\n❌ 发生错误: {e}\n")


def restore_tty():
    """重新打开 stdin 用于交互"""
    if sys.stdin.isatty():
        return
    if sys.platform != "win32":
        sys.stdin = open("/dev/tty")
    else:
        sys.stdin = open("CON", "r")


def pipe_mode(
    prompt: str | None = None, quit: bool = False, continue_conversation: bool = False
):
    """管道模式：支持管道输入 + 额外问题组合"""
    stdin_input = None

    # 检查是否有来自管道的输入
    if not sys.stdin.isatty():
        stdin_input = sys.stdin.read().strip()

    # 组合管道输入和命令行参数
    if stdin_input and prompt:
        # 如果既有管道输入又有参数，组合它们
        combined_prompt = f"{stdin_input}\n\n---\n\n{prompt}"
    elif stdin_input:
        # 只有管道输入
        combined_prompt = stdin_input
    elif prompt:
        # 只有命令行参数
        combined_prompt = prompt
    else:
        # 都没有
        print("❌ 错误: 需要提供输入内容", file=sys.stderr)
        sys.exit(1)

    agent(combined_prompt)
    print()

    # 如果启用连续对话，则进入交互模式
    if not continue_conversation and not (prompt and not quit):
        return
    restore_tty()
    chat_loop()


def main():
    parser = argparse.ArgumentParser(
        description="Ask Agent - DeepSeek 问答客户端", prog="ag"
    )
    parser.add_argument(
        "query",
        nargs="*",  # 接收多个参数
        help="要提问的内容（如果未提供，将从标准输入读取）",
    )
    parser.add_argument(
        "-q",
        "--quit",
        action="store_true",
        help="一问一答模式，回答后直接退出（默认为连续对话）",
    )
    parser.add_argument(
        "-a", "--after", action="store_true", help="管道模式中，回答后进入连续对话模式"
    )
    parser.add_argument("--ask", action="store_true", help="进入问答模式")
    parser.add_argument("-e", "--translate", action="store_true", help="进入翻译模式")
    parser.add_argument("--agent", action="store_true", help="进入智能体模式")
    parser.add_argument("--agent-team", action="store_true", help="启用智能体团队模式（需配合 --agent 使用）")
    parser.add_argument("--role", type=str, help="进入角色扮演模式，指定角色ID")
    parser.add_argument(
        "-n",
        "--no-memory",
        action="store_true",
        help="不记忆上下文，每次问答后只保留系统提示词",
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="DeepSeek API 密钥（如果不提供，将使用 DEEPSEEK_API_KEY 环境变量）",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=os.getenv("LOG_LEVEL", "ERROR"),  # 默认日志级别
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="设置日志级别（默认: ERROR，可通过 .env 文件的 LOG_LEVEL 配置）",
    )
    parser.add_argument(
        "--acp",
        action="store_true",
        help="Run as ACP agent over stdio (for IDE integration)",
    )

    args = parser.parse_args()

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    # ACP mode: run as agent over stdio, skip normal initialization
    if args.acp:
        import asyncio
        from core.acp_agent import run_acp_agent

        asyncio.run(run_acp_agent(args.log_level))
        return

    # 设置 API 密钥
    global DEEPSEEK_API_KEY
    if args.api_key:
        DEEPSEEK_API_KEY = args.api_key

    # if not DEEPSEEK_API_KEY:
    #     print("❌ 错误: 未设置 API 密钥。请使用 --api-key 参数或设置 DEEPSEEK_API_KEY 环境变量",
    #           file=sys.stderr)
    #     sys.exit(1)

    # 设置记忆模式
    global memory
    memory = not args.no_memory

    # 设置 Agent Team 开关
    global AGENT_TEAM_ENABLED
    AGENT_TEAM_ENABLED = args.agent_team

    # 加载跨会话记忆（必须在 init_system_prompt 之前，否则记忆不会注入系统提示词）
    mem_count = MEMORY_MANAGER.load_all()
    if mem_count > 0:
        logger.info(f"[Memory: {mem_count} memories loaded from {MEMORY_MANAGER.memory_dir}]")

    # 加载上次保存的模式
    saved_config = load_config()
    saved_mode = saved_config.get("mode", ASK)
    saved_role_id = saved_config.get("role_id")

    # 更新当前模式
    global current_mode
    if args.role:
        init_role_manager()
        assert ROLE_MANAGER is not None

        if not ROLE_MANAGER.get_role(args.role):
            print(f"❌ 未找到角色: {args.role}", file=sys.stderr)
            sys.exit(1)

        ROLE_MANAGER.set_current_role(args.role)
        current_mode = ROLE
        init_system_prompt(current_mode, args.role)
    elif args.ask:
        current_mode = ASK
        init_system_prompt(current_mode)
    elif args.agent:
        current_mode = AGENT
        init_system_prompt(current_mode)
    elif args.translate:
        current_mode = TRANSLATE
        init_system_prompt(current_mode)
    elif saved_mode == ROLE and saved_role_id:
        init_role_manager()
        assert ROLE_MANAGER is not None
        ROLE_MANAGER.set_current_role(saved_role_id)
        current_mode = ROLE
        init_system_prompt(current_mode, saved_role_id)
    else:
        current_mode = saved_mode
        init_system_prompt(current_mode)

    # 初始化应用配置（包含频道配置）
    init_app_config()

    # 初始化 Provider 配置
    init_providers()

    # 确保 model_prompt 已设置
    if not model_prompt:
        update_model_prompt()

    # 初始化命令管理器
    init_command_manager()

    # Fire SessionStart hooks
    HOOK_MANAGER.run_hooks(
        HookEvent.SESSION_START,
        HookInput(event=HookEvent.SESSION_START),
    )

    # 将多个参数连接成一个字符串
    query = " ".join(args.query) if args.query else None

    try:
        # 如果提供了查询或输入来自管道，使用管道模式
        if query or (not sys.stdin.isatty()):
            pipe_mode(query, args.quit, args.after)
        else:
            # 否则进入交互模式
            chat_loop()
    except (KeyboardInterrupt, EOFError):
        save_current_session()
        save_config(current_mode)
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        print(f"\n❌ 发生错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
