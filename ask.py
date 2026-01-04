#!/usr/bin/env python3

import sys
import os
import requests
import json
from typing import List, Dict
import argparse
import subprocess
import logging
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

logging.basicConfig(format="[%(levelname)s] %(message)s")
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
# 系统智能体提示词
SYSTEM_PROMPT_AGENT = f"""You are a coding agent at {WORKDIR}.

Loop: plan -> act with tools -> update todos -> report.

Rules:
- Use TodoWrite to track multi-step tasks
- Mark tasks in_progress before starting, completed when done
- Prefer tools over prose. Act, don't just explain.
- After finishing, summarize what changed."""

# Shown at the start of conversation
INITIAL_REMINDER = "<reminder>Use TodoWrite for multi-step tasks.</reminder>"

# Shown if model hasn't updated todos in a while
NAG_REMINDER = "<reminder>10+ turns without todo update. Please update todos.</reminder>"


ASK = 0         # 问答模式
TRANSLATE = 1   # 翻译模式
AGENT = 2       # 智能体模式
current_mode: int = ASK
# 对话历史缓冲
messages: List[Dict[str, str | List]] = []
# 问答模式是否记忆上下文
memory = True


class TodoManager:
    """
    Manages a structured task list with enforced constraints.

    Key Design Decisions:
    --------------------
    1. Max 20 items: Prevents the model from creating endless lists
    2. One in_progress: Forces focus - can only work on ONE thing at a time
    3. Required fields: Each item needs content, status, and activeForm

    The activeForm field deserves explanation:
    - It's the PRESENT TENSE form of what's happening
    - Shown when status is "in_progress"
    - Example: content="Add tests", activeForm="Adding unit tests..."

    This gives real-time visibility into what the agent is doing.
    """

    def __init__(self):
        self.items = []

    def update(self, items: list) -> str:
        """
        Validate and update the todo list.

        The model sends a complete new list each time. We validate it,
        store it, and return a rendered view that the model will see.

        Validation Rules:
        - Each item must have: content, status, activeForm
        - Status must be: pending | in_progress | completed
        - Only ONE item can be in_progress at a time
        - Maximum 20 items allowed

        Returns:
            Rendered text view of the todo list
        """
        validated = []
        in_progress_count = 0

        for i, item in enumerate(items):
            # Extract and validate fields
            content = str(item.get("content", "")).strip()
            status = str(item.get("status", "pending")).lower()
            active_form = str(item.get("activeForm", "")).strip()

            # Validation checks
            if not content:
                raise ValueError(f"Item {i}: content required")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"Item {i}: invalid status '{status}'")
            if not active_form:
                raise ValueError(f"Item {i}: activeForm required")

            if status == "in_progress":
                in_progress_count += 1

            validated.append({
                "content": content,
                "status": status,
                "activeForm": active_form
            })

        # Enforce constraints
        if len(validated) > 20:
            raise ValueError("Max 20 todos allowed")
        if in_progress_count > 1:
            raise ValueError("Only one task can be in_progress at a time")

        self.items = validated
        return self.render()

    def render(self) -> str:
        """
        Render the todo list as human-readable text.

        Format:
            [x] Completed task
            [>] In progress task <- Doing something...
            [ ] Pending task

            (2/3 completed)

        This rendered text is what the model sees as the tool result.
        It can then update the list based on its current state.
        """
        if not self.items:
            return "No todos."

        lines = []
        for item in self.items:
            if item["status"] == "completed":
                lines.append(f"[x] {item['content']}")
            elif item["status"] == "in_progress":
                lines.append(f"[>] {item['content']} <- {item['activeForm']}")
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
    # Tool 1: Bash - The gateway to everything
    # Can run any command: git, npm, python, curl, etc.
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Run a shell command. Use for: ls, find, grep, git, npm, python, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute"
                    }
                },
                "required": ["command"],
            },
        },
    },

    # Tool 2: Read File - For understanding existing code
    # Returns file content with optional line limit for large files
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents. Returns UTF-8 text.",
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

    # Tool 3: Write File - For creating new files or complete rewrites
    # Creates parent directories automatically
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file. Creates parent directories if needed.",
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

    # Tool 4: Edit File - For surgical changes to existing code
    # Uses exact string matching for precise edits
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. Use for surgical edits.",
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

    # Tool 5: TodoWrite - For task tracking and planning
    # This is the key addition that enables structured planning
    {
        "type": "function",
        "function": {
            "name": "TodoWrite",
            "description": "Update the task list. Use to plan and track progress.",
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
                                },
                                "activeForm": {
                                    "type": "string",
                                    "description": "Present tense action, e.g. 'Reading files'"
                                }
                            },
                            "required": ["content", "status", "activeForm"],
                        }
                    }
                },
                "required": ["todos"],
            },
        },
    },
]


def run_bash(command: str) -> str:
    """执行 bash 命令并返回 stdout/stderr"""
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"

    print(f"\033[33m$ {command}\033[0m")
    return exec(command)


def safe_path(p: str) -> Path:
    # 1. 拼接：WORKDIR / "relative/path"
    # 2. 解析：处理所有 ../ 和符号链接，得到绝对路径
    path = (WORKDIR / p).resolve()

    # 3. 验证：绝对路径是否还在工作空间内
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")

    return path


def run_read(path: str, limit: int = 0) -> str:
    """
    Read file contents with optional line limit.

    For large files, use limit to read just the first N lines.
    Output truncated to 50KB to prevent context overflow.
    """
    try:
        text = safe_path(path).read_text()
        lines = text.splitlines()

        if limit and limit < len(lines):
            lines = lines[:limit]
            lines.append(f"... ({len(text.splitlines()) - limit} more lines)")

        return "\n".join(lines)[:50000]

    except Exception as e:
        return f"Error: {e}"


def run_write(path: str, content: str) -> str:
    """
    Write content to file, creating parent directories if needed.

    This is for complete file creation/overwrite.
    For partial edits, use edit_file instead.
    """
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content)
        return f"Wrote {len(content)} bytes to {path}"

    except Exception as e:
        return f"Error: {e}"


def run_edit(path: str, old_text: str, new_text: str) -> str:
    """
    Replace exact text in a file (surgical edit).

    Uses exact string matching - the old_text must appear verbatim.
    Only replaces the first occurrence to prevent accidental mass changes.
    """
    try:
        fp = safe_path(path)
        content = fp.read_text()

        if old_text not in content:
            return f"Error: Text not found in {path}"

        # Replace only first occurrence for safety
        new_content = content.replace(old_text, new_text, 1)
        fp.write_text(new_content)
        return f"Edited {path}"

    except Exception as e:
        return f"Error: {e}"


def run_todo(todos: List[Dict]) -> str:
    """
    Update the todo list.

    The model sends a complete new list (not a diff).
    We validate it and return the rendered view.
    """
    try:
        return TODO.update(todos)
    except Exception as e:
        return f"Error: {e}"


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
    return f"Unknown tool: {name}"


def get_streaming_response(messages: List) -> tuple[str, List]:
    """获取真实的API流式响应，包含完整的对话上下文和系统提示词"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream"
    }

    data = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "tools": TOOLS,
        "tool_choice": "auto",
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
                try:
                    data = json.loads(decoded[6:])  # 去掉 "data: " 前缀
                    if "choices" in data and data["choices"][0]["delta"]:
                        delta = data["choices"][0]["delta"]
                        # 文本内容
                        if delta.get("content"):
                            content = delta["content"]
                            collected_content += content
                            print(content, end='', flush=True)
                        # 工具调用
                        if delta.get("tool_calls"):
                            tool_calls = delta["tool_calls"]
                            logger.debug("tool delta: %s", tool_calls)
                            for tool_call in tool_calls:
                                tool_calls_collected.append(tool_call)

                except json.JSONDecodeError:
                    continue
        print('\n')  # 换行

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

    # 清空对话历史
    if command == '/reset':
        init_system_prompt(current_mode)
        print("✅ 已清空对话历史\n")
        return

    # 显示帮助
    if command == '/help':
        show_help()
        return

    # 处理shell命令
    shell(command[1:])  # 提取命令，去掉前面的 /


def show_help():
    """显示帮助信息"""
    help_text = """
 📖 Ask Agent 命令帮助
 🔹 交互模式命令：
   /ask          - 进入问答模式
   /agent        - 进入智能体模式
   /e            - 进入翻译模式
   /reset        - 清空当前对话历史
   /help         - 显示此帮助信息
   /shell args   - 执行shell命令（如 /ls, /pwd, /cat file.txt）
   exit          - 退出程序
"""
    print(help_text)


def exec(cmd: str) -> str:
    """执行shell命令并返回输出"""
    try:
        # 执行shell命令并捕获输出
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=WORKDIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 将stderr重定向到stdout
            text=True,
            timeout=10
        )

        output = result.stdout

        return output[:50000].strip() if output else "(无输出)"
    except subprocess.TimeoutExpired:
        return "❌ 命令执行超时"
    except Exception as e:
        return f"❌ 命令执行错误: {str(e)}"


def shell(cmd: str):
    """处理shell命令，添加到历史并执行"""
    # 将命令添加到消息历史
    messages.append({"role": "user", "content": f"执行shell命令: {cmd}"})

    # 执行命令
    output = exec(cmd)

    # 输出到终端
    print(output)

    # 将输出添加到消息历史
    messages.append({"role": "user", "content": f"Shell命令执行结果:\n{output}"})


# Track how many rounds since last todo update
rounds_without_todo = 0


def agent(prompt: str):
    """处理问题，添加到历史并获取回答"""
    global rounds_without_todo

    # 将用户新消息添加到消息列表
    messages.append({"role": "user", "content": prompt})

    while True:
        # 调用API获取流式响应
        content, tool_calls = get_streaming_response(messages)

        # 构建助手消息并添加到历史
        assistant_msg = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)
        logger.debug("添加助手回复: %s", assistant_msg)

        # 如果没有工具调用，结束循环
        if not tool_calls:
            break

        used_todo = False
        for tool_call in tool_calls:
            name = tool_call['function']['name']
            args = json.loads(tool_call['function']['arguments'])
            logger.info("执行工具: %s, 参数: %s", name, args)

            output = execute_tool(name, args)
            preview = output[:200] + "..." if len(output) > 200 else output
            print(f"  {preview}")

            # 如果是 TodoWrite 调用，重置计数器
            if name == "TodoWrite":
                used_todo = True

            tool_result = {
                "role": "tool",
                "tool_call_id": tool_call["id"],
                "content": output
            }

            logger.debug("Add tool output: %s", tool_result)
            # 将工具执行结果添加到消息列表
            messages.append(tool_result)

        # Update counter: reset if used todo, increment otherwise
        if used_todo:
            rounds_without_todo = 0
        else:
            rounds_without_todo += 1

        # 检查是否需要注入提醒
        if rounds_without_todo > 10:
            messages.append({"role": "user", "content": NAG_REMINDER})
            rounds_without_todo = 0

        logger.info("[%s] 工具执行完毕，继续获取新的回答...", name)


def sanitize_memory():
    """翻译模式或不记忆模式时清理对话历史"""
    # 检查是否需要清理（智能体模式保留记忆）
    if current_mode == TRANSLATE or not memory:
        init_system_prompt(current_mode)  # 重新初始化系统提示词


def is_command(command: str) -> bool:
    """检查输入是否为命令"""
    return command.startswith('/') or command.lower() == 'exit'


def chat_loop():
    """主聊天循环，支持完整的对话上下文和对话命令"""

    first_message = True

    while True:
        user_input = input("💬^ :\n").strip()
        if not user_input:
            continue

        # 处理特殊命令
        if is_command(user_input):
            command(user_input.lower())
            continue

        if first_message and current_mode == AGENT:
            # Gentle reminder at start
            messages.append({"role": "user", "content": INITIAL_REMINDER})
            first_message = False

        print("\n🤖 Assistant: ", flush=True)
        agent(user_input)

        sanitize_memory()


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
