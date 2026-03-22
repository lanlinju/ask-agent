#!/usr/bin/env python3

import sys
import os
import re
import requests
import json
from typing import List, Dict
import argparse
import subprocess
import logging
import time
from dotenv import load_dotenv
from pathlib import Path
import platform
import threading
from mcp import MCPManager
from provider import ProviderConfig
from session import SessionManager
from role import RoleManager
from agent import AgentManager
from command import CommandManager
from typing import Optional
from config import ConfigPathManager, get_config_path
from util import YELLOW, GREEN, RESET
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

load_dotenv(override=True)

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if sys.platform != "win32":
    import readline

_interrupted = False


def _start_esc_listener(stop_event: threading.Event):
    """监听 ESC 键。用 cbreak 模式：逐字符读取，保留输出处理(换行正常)。"""
    global _interrupted
    if sys.platform == "win32":
        import msvcrt

        while not stop_event.is_set():
            if msvcrt.kbhit():
                if msvcrt.getwch() == "\x1b":
                    _interrupted = True
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
                        _interrupted = True
                        break
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


_INPUT_HISTORY = InMemoryHistory()
_PROMPT_SESSION = PromptSession(history=_INPUT_HISTORY)


# Ask Agent

# 配置API参数
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
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
        content = path.read_text()

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


# Global MCP manager instance
MCP_MANAGER = MCPManager()

# Config path managers
_PROVIDERS_PATH_MANAGER = ConfigPathManager("providers.json")
_COMMAND_PATH_MANAGER = ConfigPathManager("command.json")

# Global provider config instance
PROVIDERS_PATH = _PROVIDERS_PATH_MANAGER.find_config()
PROVIDER_CONFIG = ProviderConfig(PROVIDERS_PATH if PROVIDERS_PATH else "providers.json")

# Global config file path - always use user directory
_CONFIG_PATH_MANAGER = ConfigPathManager("config.json")
CONFIG_FILE = _CONFIG_PATH_MANAGER.user_dir_path
CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)

# Global cache directory - always use user directory
CACHE_DIR = Path.home() / ".ask-agent" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

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
        ROLE_MANAGER = RoleManager(cache_dir=CACHE_DIR)
    return ROLE_MANAGER


def init_agent_manager() -> AgentManager:
    """初始化智能体管理器"""
    global AGENT_MANAGER
    if not AGENT_MANAGER:
        AGENT_MANAGER = AgentManager()
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
    PROVIDER_CONFIG.save()

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


# 系统智能体提示词
SYSTEM_PROMPT_AGENT = f"""You are a coding agent at {WORKDIR}.

Loop: plan -> act with tools -> report.

**Skills available** (invoke with Skill tool when task matches):
{SKILLS.get_descriptions()}

**MCP servers available** (invoke with MCP tool to connect):
{MCP_MANAGER.get_descriptions()}

**Subagents available** (invoke with Task tool for focused subtasks):
{get_agent_descriptions()}

Rules:
- Use Skill tool IMMEDIATELY when a task matches a skill description
- Use Task tool for subtasks needing focused exploration or implementation
- Use TodoWrite to track multi-step work
- Prefer tools over prose. Act, don't just explain.
- After finishing, summarize what changed.

Environment:
- OS platform: {platform.system()}
    - Windows: use PowerShell command.
    - Linux/macOS: use Bash command.
"""

ASK = 0  # 问答模式
TRANSLATE = 1  # 翻译模式
AGENT = 2  # 智能体模式
ROLE = 3  # 角色扮演模式
current_mode: int = ASK
# 对话历史缓冲
messages: List[Dict[str, str | List]] = []
# 问答模式是否记忆上下文
memory = True
# 当前模型提示符
model_prompt = DEEPSEEK_MODEL
# 标题是否已生成
title_generated = False


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
                lines.append(f"[x] {item['content']}")
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
    """初始化系统提示词"""
    global title_generated
    messages.clear()
    title_generated = False
    if mode == TRANSLATE:
        system_prompt = SYSTEM_PROMPT_TRANSLATE
    elif mode == AGENT:
        # 获取要使用的智能体ID
        actual_agent_id = agent_id
        if not actual_agent_id:
            # 使用默认智能体
            init_agent_manager()
            assert AGENT_MANAGER is not None
            actual_agent_id = AGENT_MANAGER.default_agent

        # builtin 或 None 使用内置提示词
        if not actual_agent_id or actual_agent_id == "builtin":
            system_prompt = SYSTEM_PROMPT_AGENT
        else:
            # 使用智能体的markdown文件内容
            init_agent_manager()
            assert AGENT_MANAGER is not None
            agent_prompt = AGENT_MANAGER.get_agent_prompt(actual_agent_id)
            if agent_prompt:
                system_prompt = agent_prompt
            else:
                print(f"❌ 未找到智能体: {actual_agent_id}")
                system_prompt = SYSTEM_PROMPT_AGENT
    elif mode == ROLE and role_id:
        init_role_manager()
        assert ROLE_MANAGER is not None
        system_prompt = ROLE_MANAGER.get_role_prompt(role_id)
        if not system_prompt:
            print(f"❌ 未找到角色: {role_id}")
            current_mode = ASK
            system_prompt = SYSTEM_PROMPT_ASK
            role_id = None
    else:
        system_prompt = SYSTEM_PROMPT_ASK
    messages.append({"role": "system", "content": system_prompt})

    init_session_manager(mode, role_id)

    if mode == ROLE and role_id and SESSION_MANAGER:
        load_role_session(role_id)


def clear_history():
    """清除对话历史，保留系统提示词"""
    global title_generated
    messages[:] = messages[:1]
    title_generated = False


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command (bash/zsh on Linux/macOS, PowerShell on Windows).",
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
                        "description": "Start reading from line number (0-indexed, default: 0)",
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
            "description": "Find files matching a glob pattern (e.g. '**/*.py', 'src/**/*.ts').",
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
]

# Please ignore the following code, it's for personal preference and has no effect on the main logic. I recently like to see how the LLM uses the Linux command line.
ONLY_BASH_TOOL = os.getenv("ONLY_BASH_TOOL", "disabled").lower()
if ONLY_BASH_TOOL == "enabled":
    TOOLS = TOOLS[:1]  # 仅保留 bash 工具


def run_bash(command: str, timeout: Optional[int] = None) -> str:
    """执行 bash 命令并返回 stdout/stderr"""
    if any(d in command for d in ["rm -rf /", "shutdown"]):
        return "Error: Dangerous command blocked"
    print(f"  \033[34m$ {command}\033[0m")
    return execute_cmd(command, timeout)


def safe_path(p: str) -> Path:
    """Ensure path stays within workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, offset: int = 0, limit: int = None) -> str:
    """Read file contents."""
    try:
        range_info = f"[offset={offset}, limit={limit}]" if offset or limit else ""
        print(f"\033[34m→ Read {path} {range_info}\033[0m")
        lines = safe_path(path).read_text().splitlines()
        if offset:
            lines = lines[offset:]
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
        fp.write_text(content)
        # 打印部分内容（最大800字符）
        preview = content[:800]
        suffix = "..." if len(content) > 800 else ""
        print(f"{preview}{suffix}")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in file."""
    try:
        print(f"\033[34m→ Edited {path}\033[0m")
        fp = safe_path(path)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: Text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1))
        return (
            f"Edited {path}: replaced {len(old_text)} chars with {len(new_text)} chars"
        )
    except Exception as e:
        return f"Error: {e}"


def run_glob(pattern: str, path: str | None = None) -> str:
    """Find files matching a glob pattern."""
    try:
        base = safe_path(path) if path else WORKDIR
        print(f"\033[34m✱ Glob {pattern} in {base.relative_to(WORKDIR) or '.'}\033[0m")
        matches = sorted(
            str(p.relative_to(WORKDIR)) for p in base.glob(pattern) if p.is_file()
        )
        if not matches:
            return "(no matches)"
        logger.debug("Glob matches: %s", matches)
        return f"{len(matches)} matches\n" + "\n".join(matches)
    except Exception as e:
        return f"Error: {e}"


def run_grep(pattern: str, path: str | None = None, include: str | None = None) -> str:
    """Search file contents using a regex pattern."""
    try:
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
            if not fp.is_file():
                continue
            try:
                for i, line in enumerate(
                    fp.read_text(errors="replace").splitlines(), 1
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

        if "id" in tool_call:
            current["id"] = tool_call["id"]
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
            if reasoning_content:  # tool_calls 需添加推理内容
                sub_assistant_msg["reasoning_content"] = reasoning_content
        sub_messages.append(sub_assistant_msg)

        # If no tools to execute, break
        if not tool_calls:
            if reasoning_content:
                cleanup_reasoning_content(messages, reasoning_start_index, sub_turn)
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


def get_streaming_response(
    messages: List, tools: List, silent: bool = False, useTools: bool = True
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
        "stream": True,
    }

    if useTools and current_mode in (AGENT, ROLE):
        data["tools"] = tools
        data["tool_choice"] = "auto"

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
    ) as response:
        if response.status_code != 200:
            print(f"❌ API错误: {response.status_code} {response.text}")
            return ("", "", [])
        for chunk in response.iter_lines():
            if not chunk:
                continue
            # ESC interrupt check
            if _interrupted:
                break
            decoded = chunk.decode("utf-8")
            if not decoded.startswith("data:"):
                continue
            if decoded == "data: [DONE]":
                logger.debug("data: [DONE]")
                break
            try:
                data = json.loads(decoded[6:])  # 去掉 "data: " 前缀
                # logger.debug("data: %s", data)
                if len(data["choices"]) == 0:
                    logger.debug("\nchoices length is 0")
                    continue
                if data["choices"][0]["finish_reason"] != None:
                    finish_reason = data["choices"][0]["finish_reason"]
                    logger.info("\nfinish_reason: %s", finish_reason)
                    # 打印 token 使用情况
                    stat_token(data)
                    # 在角色模式下，如果因长度限制而停止，自动压缩上下文
                    if finish_reason == "length" and current_mode == ROLE:
                        print("\n📝 上下文即将达到限制，自动压缩对话历史...\n")
                        summarizer()
                    break
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
                        if "<think>" in content:
                            in_think_tag = True
                            if should_display:
                                print("\033[34mThinking: \033[0m", end="", flush=True)
                            content = content.replace("<think>", "")
                        if "</think>" in content:
                            in_think_tag = False
                            continue
                        if in_think_tag:
                            # 在 think 标签内，作为推理内容
                            reasoning_content += content
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

    # logger.info("完整回答: %s", tool_calls_collected)
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
    if command == "/mcp":
        handle_mcp_command(command)
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
    /bot          - 启动 Telegram Bot（需设置 TELEGRAM_BOT_TOKEN 环境变量）
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

  🔹 角色扮演模式功能：
    - 使用角色扮演系统提示词与角色对话
    - 每个角色拥有独立的对话历史
    - 角色配置存放在 roles.json，提示词存放在 roles/ 目录

   🔹 会话管理：
     - 会话按模式自动分类保存到 ~/.ask-agent/cache/ask/、~/.ask-agent/cache/agent/、~/.ask-agent/cache/translate/、~/.ask-agent/cache/role/、~/.ask-agent/cache/role_<角色id>/
     - 切换模式或退出时自动保存当前会话
 """
    print(help_text)
    return help_text


def execute_cmd(cmd: str, timeout: Optional[int] = None) -> str:
    """执行shell命令并返回输出"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=timeout or 10,
        )
        output = result.stdout + result.stderr
        return output.strip()
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

    # 根据命令名分发处理
    if cmd == "/start":
        await update.message.reply_text(
            "欢迎！使用/exit命令退出Telegram Bot,/save命令保存聊天会话"
        )
    if cmd == "/exit" or cmd == "/save":
        save_current_session()
        save_config(current_mode)
        await update.message.reply_text("会话已保存")
        if cmd == "/exit":
            sys.exit(0)
    elif cmd == "/help":
        await update.message.reply_text(f"{show_help()}")
        await update.message.reply_text("注意不要执行交互式命令!")
    else:
        command(cmd)
        await update.message.reply_text(f"执行命令: {cmd}")


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理接收到的文本消息"""
    user = update.effective_user
    message_text = update.message.text

    # 打印到控制台
    print(
        f"收到消息 | 用户: {user.username or user.first_name} (ID: {user.id}) | 内容: {message_text}"
    )

    # 回复用户确认收到
    await update.message.reply_text(f"{agent(message_text)}")
    print()


def run_bot():
    # 创建应用
    application = Application.builder().token(BOT_TOKEN).build()

    # 添加处理器 - 捕获所有以 / 开头的命令
    application.add_handler(MessageHandler(filters.COMMAND, bot_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    print("机器人已启动！按 Ctrl+C 停止")

    # 运行机器人
    application.run_polling(allowed_updates=Update.ALL_TYPES)


def agent(prompt: str) -> str:
    """处理问题，添加到历史并获取回答"""

    # 将用户新消息添加到消息列表
    messages.append({"role": "user", "content": prompt})

    # 记录当前问题的工具调用轮次
    sub_turn = 1
    # 记录本轮推理开始时的消息索引
    reasoning_start_index = len(messages)
    while True:
        global _interrupted
        _interrupted = False
        _esc_stop = threading.Event()
        t = None

        if sys.stdin.isatty():
            t = threading.Thread(
                target=_start_esc_listener, args=(_esc_stop,), daemon=True
            )
            t.start()

        try:
            content, reasoning_content, tool_calls = get_streaming_response(
                messages, TOOLS
            )
        finally:
            _esc_stop.set()
            if t is not None:
                t.join(timeout=0.5)

        # 构建助手消息并添加到历史
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
            if reasoning_content:  # tool_calls 需添加推理内容
                assistant_msg["reasoning_content"] = reasoning_content
        messages.append(assistant_msg)
        logger.debug("添加助手回复: %s", assistant_msg)

        # 如果没有工具调用或者esc中断，结束循环
        if not tool_calls or _interrupted:
            _interrupted = False
            cleanup_reasoning_content(messages, reasoning_start_index, sub_turn)
            return content

        for tool_call in tool_calls:
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
                preview = output[:400] + "..." if len(output) > 400 else output
                print(f"{preview}")

            tool_result = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": output,
            }

            logger.debug("Add tool output: %s", tool_result)
            # 将工具执行结果添加到消息列表
            messages.append(tool_result)

        sub_turn += 1


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
    """翻译模式或不记忆模式时清理对话历史"""
    # 检查是否需要清理（智能体模式保留记忆）
    if current_mode == TRANSLATE or not memory:
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
        current_role_id_val = get_current_role_id()
        if mode == ROLE and current_role_id_val:
            config = {"mode": mode, "role_id": current_role_id_val}
        else:
            config = {"mode": mode}
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
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
            agent(user_input)
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

    args = parser.parse_args()

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

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

    # 初始化 Provider 配置
    init_providers()

    # 确保 model_prompt 已设置
    if not model_prompt:
        update_model_prompt()

    # 初始化命令管理器
    init_command_manager()

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
