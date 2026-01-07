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
from mcp import MCPManager

load_dotenv()

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Ask Agent

# 配置API参数
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
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
        "tools": ["bash", "read_file"],  # No write access
        "prompt": "You are an exploration agent. Search and analyze, but never modify files. Return a concise summary.",
    },
    "code": {
        "description": "Full agent for implementing features and fixing bugs",
        "tools": "*",  # All tools
        "prompt": "You are a coding agent. Implement the requested changes efficiently.",
    },
    "plan": {
        "description": "Planning agent for designing implementation strategies",
        "tools": ["bash", "read_file"],  # Read-only
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
            f"- {name}: {skill['description']}"
            for name, skill in self.skills.items()
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
            ("assets", "Assets")
        ]:
            folder_path = skill["dir"] / folder
            if folder_path.exists():
                files = list(folder_path.glob("*"))
                if files:
                    resources.append(
                        f"{label}: {', '.join(f.name for f in files)}")

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

# Global MCP manager instance
MCP_MANAGER = MCPManager()


def get_agent_descriptions() -> str:
    """Generate agent type descriptions for the Task tool."""
    return "\n".join(
        f"- {name}: {cfg['description']}"
        for name, cfg in AGENT_TYPES.items()
    )


# 系统智能体提示词
SYSTEM_PROMPT_AGENT = f"""You are a coding agent at {WORKDIR}.

Loop: plan -> act with tools -> report.

**Skills available** (invoke with Skill tool when task matches):
{SKILLS.get_descriptions()}

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
"""

ASK = 0         # 问答模式
TRANSLATE = 1   # 翻译模式
AGENT = 2       # 智能体模式
current_mode: int = ASK
# 对话历史缓冲
messages: List[Dict[str, str | List]] = []
# 问答模式是否记忆上下文
memory = True


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

            validated.append({
                "content": content,
                "status": status
            })

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


def init_system_prompt(mode: int = ASK):
    """初始化系统提示词"""
    messages.clear()
    if mode == TRANSLATE:
        system_prompt = SYSTEM_PROMPT_TRANSLATE
    elif mode == AGENT:
        system_prompt = SYSTEM_PROMPT_AGENT
    else:
        system_prompt = SYSTEM_PROMPT_ASK
    messages.append({"role": "system", "content": system_prompt})


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command.\nLinux/macOS: use bash/zsh, forward slashes.\nWindows: use PowerShell, backslashes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute (bash/zsh/PowerShell)"
                    }
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
                        "description": "Relative path to the file"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max lines to read (default: all)"
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
                        "description": "Relative path for the file"
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write"
                    },
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
                        "description": "Relative path to the file"
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (must match precisely)"
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Replacement text"
                    },
                },
                "required": ["path", "old_text", "new_text"],
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
                                    "description": "Task description"
                                },
                                "status": {
                                    "type": "string",
                                    "enum": ["pending", "in_progress", "completed"],
                                    "description": "Task status"
                                }
                            },
                            "required": ["content", "status"],
                        }
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
                        "description": "Short task name (3-5 words) for progress display"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Detailed instructions for the subagent"
                    },
                    "agent_type": {
                        "type": "string",
                        "enum": list(AGENT_TYPES.keys()),
                        "description": "Type of agent to spawn"
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
                        "description": "Name of the skill to load"
                    }
                },
                "required": ["skill"],
            },
        },
    },
]


def run_bash(command: str) -> str:
    """执行 bash 命令并返回 stdout/stderr"""
    if any(d in command for d in ["rm -rf /", "sudo", "shutdown"]):
        return "Error: Dangerous command blocked"
    print(f"  \033[34m$ {command}\033[0m")
    return execute_cmd(command)


def safe_path(p: str) -> Path:
    """Ensure path stays within workspace."""
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def run_read(path: str, limit: int = None) -> str:
    """Read file contents."""
    try:
        lines = safe_path(path).read_text().splitlines()
        if limit:
            lines = lines[:limit]
        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    """Write content to file."""
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """Replace exact text in file."""
    try:
        fp = safe_path(path)
        text = fp.read_text()
        if old_text not in text:
            return f"Error: Text not found in {path}"
        fp.write_text(text.replace(old_text, new_text, 1))
        return f"Edited {path}"
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


def merge_arguments(tool_calls_collected: List) -> List:
    if not tool_calls_collected:
        return []

    tool_calls_by_index = {}

    for tool_call in tool_calls_collected:
        index = tool_call.get("index", 0)

        if index not in tool_calls_by_index:
            tool_calls_by_index[index] = {
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""}
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

    result = [tool_calls_by_index[i]
              for i in sorted(tool_calls_by_index.keys())]
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
        {"role": "user", "content": prompt}
    ]

    # Progress tracking
    print(f"  [{agent_type}] {description}", end='', flush=True)
    start = time.time()
    tool_count = 0

    # Run the same agent loop (silently - don't print to main chat)
    while True:
        content, tool_calls = get_streaming_response(
            sub_messages, sub_tools, True)

        # Add assistant response to subagent history
        sub_assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            sub_assistant_msg["tool_calls"] = tool_calls
        sub_messages.append(sub_assistant_msg)

        # If no tools to execute, break
        if not tool_calls:
            break

        # Execute tools
        for tool_call in tool_calls:
            tool_count += 1
            name = tool_call['function']['name']
            args = json.loads(tool_call['function']['arguments'])
            output = execute_tool(name, args)

            tool_result = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": output
            }

            # Update progress line (in-place)
            elapsed = time.time() - start
            sys.stdout.write(
                f"\r  [{agent_type}] {description} ... {tool_count} tools, {elapsed:.1f}s")
            sys.stdout.flush()

            sub_messages.append(tool_result)

    # Final progress update
    elapsed = time.time() - start
    sys.stdout.write(
        f"\r  [{agent_type}] {description} - done ({tool_count} tools, {elapsed:.1f}s)\n")

    # Return the final text content
    return content


def execute_tool(name: str, args: dict) -> str:
    if name == "bash":
        return run_bash(args["command"])
    if name == "read_file":
        return run_read(args["path"], args.get("limit") or 0)
    if name == "write_file":
        return run_write(args["path"], args["content"])
    if name == "edit_file":
        return run_edit(args["path"], args["old_text"], args["new_text"])
    if name == "TodoWrite":
        return run_todo(args["todos"])
    if name == "Task":
        return run_task(args["description"], args["prompt"], args["agent_type"])
    if name == "Skill":
        return run_skill(args["skill"])
    if name.startswith("mcp_"):
        import re
        match = re.match(r"^mcp_(.+)_(.+)$", name)
        if match:
            server_name = match.group(1)
            tool_name = match.group(2)
            return MCP_MANAGER.call_mcp_tool(server_name, tool_name, args)
        return f"Error: Invalid MCP tool name: {name}"
    return f"Unknown tool: {name}"


def get_streaming_response(messages: List, tools: List, silent: bool = False) -> tuple[str, List]:
    """获取真实的API流式响应，包含完整的对话上下文和系统提示词"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }

    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "tools": tools if current_mode == AGENT else [],
        "tool_choice": "auto" if current_mode == AGENT else "none",
        "stream": True
    }

    collected_content = ""
    tool_calls_collected = []

    with requests.post(f"{DEEPSEEK_API_URL}/v1/chat/completions", headers=headers, json=data, stream=True) as response:
        if response.status_code != 200:
            print(f"❌ API错误: {response.status_code} {response.text}")
            return ("", [])
        for chunk in response.iter_lines():
            if chunk:
                decoded = chunk.decode('utf-8')
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
                    if data['choices'][0]['finish_reason'] != None:
                        logger.info("\nfinish_reason: %s", data['choices'][0]['finish_reason'])
                        break
                    if "choices" in data and data["choices"][0]["delta"]:
                        delta = data["choices"][0]["delta"]
                        # 文本内容
                        if delta.get("content"):
                            content = delta["content"]
                            collected_content += content
                            if not silent:
                                print(content, end='', flush=True)
                        # 工具调用
                        if delta.get("tool_calls"):
                            tool_calls = delta["tool_calls"]
                            logger.debug("tool delta: %s", tool_calls)
                            for tool_call in tool_calls:
                                tool_calls_collected.append(tool_call)

                except json.JSONDecodeError:
                    continue

    # logger.info("完整回答: %s", tool_calls_collected)

    return (collected_content, merge_arguments(tool_calls_collected))


def command(command: str):
    """处理命令"""
    global current_mode

    if command == 'exit':
        sys.exit(0)

    # 进入翻译模式
    if command == '/e':
        current_mode = TRANSLATE
        init_system_prompt(current_mode)
        print("✅ 已进入翻译模式\n")
        return

    # 进入问答模式
    if command == '/ask':
        current_mode = ASK
        init_system_prompt(current_mode)
        print("✅ 已进入问答模式\n")
        return

    # 进入智能体模式
    if command == '/agent':
        current_mode = AGENT
        init_system_prompt(current_mode)
        print("✅ 已进入智能体模式\n")
        return

    # 创建新会话
    if command == '/new':
        init_system_prompt(current_mode)
        print("✅ 已创建新会话\n")
        return

    # 显示帮助
    if command == '/help':
        show_help()
        return

    # 列出所有可用的 MCP 服务器
    if command == '/mcp':
        list_mcp_servers()
        return

    # 使用指定的 MCP 服务器（不指定名称则加载所有）
    if command.startswith('/use'):
        server_name = command[5:].strip()
        if not server_name:
            use_all_mcp_servers()
        else:
            use_mcp_server(server_name)
        return

    if command == '/mcp-status':
        show_mcp_status()
        return

    if command.startswith('/disconnect '):
        server_name = command[12:].strip()
        disconnect_mcp_server(server_name)
        return

    # 处理shell命令 (!开头)
    if command.startswith('!'):
        shell(command[1:])  # 提取命令，去掉前面的 !
        return


def use_all_mcp_servers():
    """连接并使用所有可用的 MCP 服务器"""
    servers = MCP_MANAGER.list_servers()

    if not servers:
        print("❌ 未找到 MCP 服务器配置")
        print("   请在当前目录创建 mcp.json 配置文件")
        return

    for name in servers:
        use_mcp_server(name)

    print(f"\n🔌 已连接所有 MCP 服务器 ({len(servers)} 个)...")
    print()


def use_mcp_server(name: str):
    """连接并使用指定的 MCP 服务器"""
    print(f"\n🔌 连接 MCP 服务器: {name}...")

    global TOOLS
    if MCP_MANAGER.connect_server(name):
        client, tools = MCP_MANAGER.active_clients[name]
        TOOLS.extend(tools)
        print(f"✅ 成功连接，加载了 {len(tools)} 个工具")

        # 显示工具列表
        if tools:
            print(f"\n可用工具:")
            for tool in tools:
                tool_name = tool['function']['name']
                desc = tool['function'].get('description', 'N/A')
                # 移除 MCP 前缀以显示原始名称
                display_name = tool_name.replace(f"mcp_{name}_", "")
                print(f"  - {display_name}: {desc}")
        print()
    else:
        print(f"❌ 连接失败\n")


def list_mcp_servers():
    """列出所有可用的 MCP 服务器"""
    servers = MCP_MANAGER.list_servers()

    if not servers:
        print("❌ 未找到 MCP 服务器配置")
        print("   请在当前目录创建 mcp.json 配置文件")
        return

    print(f"\n📋 可用的 MCP 服务器 ({len(servers)} 个):\n")

    for name in servers:
        server = MCP_MANAGER.get_server_info(name)
        if not server:
            continue

        # 检查是否已连接
        status = "✓ 已连接" if name in MCP_MANAGER.active_clients else "○ 未连接"
        print(f"  [{status}] {name}")
        print(f"    类型: {server.type}")
        if server.description:
            print(f"    描述: {server.description}")
        if server.is_stdio():
            cmd = server.get_full_command()
            if cmd:
                print(f"    命令: {' '.join(cmd)}")
        else:
            print(f"    URL: {server.url}")
        if not server.enabled:
            print(f"    状态: 已禁用")
        print()


def show_mcp_status():
    """显示 MCP 连接状态"""
    active = list(MCP_MANAGER.active_clients.keys())

    if not active:
        print("\n📊 当前没有活动的 MCP 连接\n")
        return

    print(f"\n📊 活动的 MCP 服务器 ({len(active)} 个):\n")

    for name in active:
        client, tools = MCP_MANAGER.active_clients[name]
        print(f"  ✓ {name}")
        print(f"    工具数量: {len(tools)}")
        print(f"    类型: {type(client).__name__}")
        print()


def disconnect_mcp_server(name: str):
    """断开指定的 MCP 服务器"""
    print(f"\n🔌 断开 MCP 服务器: {name}...")

    if MCP_MANAGER.disconnect_server(name):
        print(f"✅ 已断开连接\n")
    else:
        print(f"❌ 断开失败\n")


def show_help():
    """显示帮助信息"""
    help_text = """
 📖 Ask Agent 命令帮助
  🔹 交互模式命令：
    /ask          - 进入问答模式
    /agent        - 进入智能体模式
    /e            - 进入翻译模式
    /new          - 创建新会话
    /help         - 显示此帮助信息
    !command      - 执行shell命令（如 !ls, !pwd, !cat file.txt）
    exit          - 退出程序

  🔹 MCP 服务器管理：
    /mcp          - 列出所有可用的 MCP 服务器
    /use [name]   - 连接并使用 MCP 服务器（不指定名称则连接所有）
    /disconnect <name> - 断开指定的 MCP 服务器
    /mcp-status   - 显示当前 MCP 连接状态

  🔹 智能体模式功能：
    - 自动使用 Skills 工具加载领域知识（PDF处理、MCP开发等）
    - 支持通过 Task 工具启动子智能体
    - 支持通过 TodoWrite 工具管理任务列表
    - 支持连接和使用 MCP 服务器提供的工具
 """
    print(help_text)


def execute_cmd(cmd: str) -> str:
    """执行shell命令并返回输出"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=WORKDIR,
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stdout + result.stderr
        return (output.strip() if output else "(no output)")[:50000]
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


def agent(prompt: str):
    """处理问题，添加到历史并获取回答"""

    # 将用户新消息添加到消息列表
    messages.append({"role": "user", "content": prompt})

    while True:
        # 调用API获取流式响应
        content, tool_calls = get_streaming_response(messages, TOOLS)

        # 构建助手消息并添加到历史
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        logger.debug("添加助手回复: %s", assistant_msg)

        # 如果没有工具调用，结束循环
        if not tool_calls:
            break

        for tool_call in tool_calls:
            name = tool_call['function']['name']
            args = json.loads(tool_call['function']['arguments'])
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
                "content": output
            }

            logger.debug("Add tool output: %s", tool_result)
            # 将工具执行结果添加到消息列表
            messages.append(tool_result)


def sanitize_memory():
    """翻译模式或不记忆模式时清理对话历史"""
    # 检查是否需要清理（智能体模式保留记忆）
    if current_mode == TRANSLATE or not memory:
        init_system_prompt(current_mode)  # 重新初始化系统提示词


def is_command(command: str) -> bool:
    """检查输入是否为命令"""
    return command.startswith('/') or command.startswith('!') or command.lower() == 'exit'


def get_mode_prompt() -> str:
    """获取当前模式的提示符"""
    if current_mode == TRANSLATE:
        return "Translate"
    elif current_mode == AGENT:
        return "Agent"
    else:
        return "Ask"


def chat_loop():
    """主聊天循环，支持完整的对话上下文和对话命令"""

    while True:
        user_input = input(f"💬^ ({get_mode_prompt()}):\n").strip()
        if not user_input:
            continue

        # 处理特殊命令
        if is_command(user_input):
            command(user_input.lower())
            continue

        print("\n🤖 Assistant: ", flush=True)
        agent(user_input)

        sanitize_memory()

        print('\n')  # 换行


def restore_tty():
    """重新打开 stdin 用于交互"""
    if sys.stdin.isatty():
        return
    if sys.platform != 'win32':
        sys.stdin = open('/dev/tty')
    else:
        sys.stdin = open('CON', 'r')


def pipe_mode(prompt: str | None = None, quit: bool = False, continue_conversation: bool = False):
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

    # 如果启用连续对话，则进入交互模式
    if not continue_conversation and not (prompt and not quit):
        return

    restore_tty()
    chat_loop()


def main():
    parser = argparse.ArgumentParser(
        description="Ask Agent - DeepSeek 问答客户端",
        prog="ag"
    )
    parser.add_argument(
        "query",
        nargs="*",       # 接收多个参数
        help="要提问的内容（如果未提供，将从标准输入读取）"
    )
    parser.add_argument(
        "-q", "--quit",
        action="store_true",
        help="一问一答模式，回答后直接退出（默认为连续对话）"
    )
    parser.add_argument(
        "-a", "--after",
        action="store_true",
        help="管道模式中，回答后进入连续对话模式"
    )
    parser.add_argument(
        "-e", "--translate",
        action="store_true",
        help="进入翻译模式"
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="进入智能体模式"
    )
    parser.add_argument(
        "-n", "--no-memory",
        action="store_true",
        help="不记忆上下文，每次问答后只保留系统提示词"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="DeepSeek API 密钥（如果不提供，将使用 DEEPSEEK_API_KEY 环境变量）"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=os.getenv("LOG_LEVEL", "ERROR"),    # 默认日志级别
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="设置日志级别（默认: ERROR，可通过 .env 文件的 LOG_LEVEL 配置）"
    )

    args = parser.parse_args()

    # 设置日志级别
    logging.getLogger().setLevel(getattr(logging, args.log_level.upper()))

    # 设置 API 密钥
    global DEEPSEEK_API_KEY
    if args.api_key:
        DEEPSEEK_API_KEY = args.api_key

    if not DEEPSEEK_API_KEY:
        print("❌ 错误: 未设置 API 密钥。请使用 --api-key 参数或设置 DEEPSEEK_API_KEY 环境变量",
              file=sys.stderr)
        sys.exit(1)

    # 设置记忆模式
    global memory
    memory = not args.no_memory

    # 更新当前模式
    global current_mode
    if args.agent:
        current_mode = AGENT
    elif args.translate:
        current_mode = TRANSLATE
    else:
        current_mode = ASK

    # 初始化系统提示词
    init_system_prompt(current_mode)

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
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")


if __name__ == "__main__":
    main()
