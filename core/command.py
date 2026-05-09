#!/usr/bin/env python3

"""
自定义命令管理器

支持两种配置方式:
1. Markdown 文件: command/test.md
   ---
   description: Run tests with coverage
   agent: build
   model: deepseek/deepseek-chat
   ---
   Run the full test suite with coverage report...

2. JSON 配置: command.json
   {
     "command": {
       "test": {
         "template": "Run the full test suite...",
         "description": "Run tests with coverage",
         "agent": "build",
         "model": "deepseek/deepseek-chat"
       }
     }
   }
"""

import json
import re
from pathlib import Path
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class Command:
    """表示一个自定义命令"""

    def __init__(
        self,
        name: str,
        template: str,
        description: str = "",
        agent: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.name = name
        self.template = template
        self.description = description
        self.agent = agent
        self.model = model

    def __repr__(self):
        return f"Command(name={self.name}, agent={self.agent}, model={self.model})"


class CommandManager:
    """
    管理自定义命令

    支持两种配置方式:
    1. Markdown 文件: command/test.md
    2. JSON 配置: command.json
    """

    def __init__(self, command_dir: Path, config_file: Path):
        self.command_dir = command_dir
        self.config_file = config_file
        self.commands: Dict[str, Command] = {}
        self.load_commands()

    def parse_command_md(self, path: Path) -> Optional[Command]:
        """
        解析命令的 Markdown 文件

        格式:
        ---
        description: Run tests with coverage
        agent: build
        model: anthropic/claude-3-5-sonnet-20241022
        ---
        Run the full test suite...
        """
        try:
            content = path.read_text(encoding="utf-8")

            # 匹配 YAML frontmatter
            match = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", content, re.DOTALL)
            if not match:
                logger.warning(f"命令文件格式错误: {path}")
                return None

            frontmatter, template = match.groups()

            # 解析 frontmatter
            metadata = {}
            for line in frontmatter.strip().split("\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    metadata[key.strip()] = value.strip().strip("\"'")

            # 命令名称从文件名获取（去掉 .md 扩展名）
            name = path.stem

            return Command(
                name=name,
                template=template.strip(),
                description=metadata.get("description", ""),
                agent=metadata.get("agent"),
                model=metadata.get("model"),
            )
        except Exception as e:
            logger.error(f"解析命令文件失败 {path}: {e}")
            return None

    def load_from_markdown(self):
        """从 command/ 目录加载所有 markdown 命令"""
        if not self.command_dir.exists():
            return

        for md_file in self.command_dir.glob("*.md"):
            command = self.parse_command_md(md_file)
            if command:
                self.commands[command.name] = command
                logger.info(f"加载命令: /{command.name} (来自 {md_file.name})")

    def load_from_json(self):
        """从 command.json 加载命令配置"""
        if not self.config_file.exists():
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                config = json.load(f)

            commands_config = config.get("command", {})
            for name, cmd_data in commands_config.items():
                # JSON 配置优先级高于 markdown，如果已存在则覆盖
                command = Command(
                    name=name,
                    template=cmd_data.get("template", ""),
                    description=cmd_data.get("description", ""),
                    agent=cmd_data.get("agent"),
                    model=cmd_data.get("model"),
                )
                self.commands[name] = command
                logger.info(f"加载命令: /{name} (来自 command.json)")

        except json.JSONDecodeError as e:
            logger.error(f"解析 command.json 失败: {e}")
        except Exception as e:
            logger.error(f"加载命令配置失败: {e}")

    def load_commands(self):
        """加载所有命令（先 markdown，后 JSON）"""
        self.commands.clear()
        self.load_from_markdown()
        self.load_from_json()

    def get_command(self, name: str) -> Optional[Command]:
        """获取指定名称的命令"""
        return self.commands.get(name)

    def list_commands(self) -> List[Command]:
        """列出所有可用命令"""
        return list(self.commands.values())

    def has_command(self, name: str) -> bool:
        """检查命令是否存在"""
        return name in self.commands

    def get_command_descriptions(self) -> str:
        """生成命令描述列表（用于帮助信息）"""
        if not self.commands:
            return "  (暂无自定义命令)"

        lines = []
        for cmd in sorted(self.commands.values(), key=lambda x: x.name):
            desc = cmd.description or "无描述"
            agent_info = f" [agent: {cmd.agent}]" if cmd.agent else ""
            model_info = f" [model: {cmd.model}]" if cmd.model else ""
            lines.append(f"    /{cmd.name:<15} - {desc}{agent_info}{model_info}")

        return "\n".join(lines)