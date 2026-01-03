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

logging.basicConfig(format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Ask Agent

# 配置API参数
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_API_URL = "https://api.deepseek.com"

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
SYSTEM_PROMPT_AGENT = f"""You are a CLI agent at {os.getcwd()}. Solve problems using bash commands.

Rules:
- Prefer tools over prose. Act first, explain briefly after.
- Read files: cat, grep, find, rg, ls, head, tail
- Write files: echo '...' > file, sed -i, or cat << 'EOF' > fil"""

ASK = 0         # 问答模式
TRANSLATE = 1   # 翻译模式
AGENT = 2       # 智能体模式
current_mode: int = ASK
# 对话历史缓冲
messages: List[Dict[str, str]] = []
# 问答模式是否记忆上下文
memory = True


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

# ========== 工具 ==========


def bash_tool(command: str) -> str:
    """执行 bash 命令并返回 stdout/stderr"""
    pass


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute bash shell command on the local machine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The bash command to execute."}
                },
                "required": ["command"]
            }
        }
    }
]


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
    
    result = [tool_calls_by_index[i] for i in sorted(tool_calls_by_index.keys())]
    logger.info("最终工具调用:\n%s", json.dumps(result, indent=2))
    
    return result


def get_streaming_response(prompt: str) -> str:
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
            return ""
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
                            tool_call = delta["tool_calls"]
                            logger.info("工具调用: %s", tool_call)
                            tool_calls_collected.append(tool_call[0])

                except json.JSONDecodeError:
                    continue
        print('\n')  # 换行

    # logger.info("完整回答: %s", tool_calls_collected)
    # 处理工具调用（如果有的话）
    merge_arguments(tool_calls_collected)

    return collected_content


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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # 将stderr重定向到stdout
            text=True,
            timeout=10
        )

        output = result.stdout

        return output.strip() if output else "(无输出)"
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


def ask(question: str) -> str:
    """处理普通问题，添加到历史并获取回答"""
    # 将用户新消息添加到消息列表
    messages.append({"role": "user", "content": question})
    # 调用API获取流式响应
    answer = get_streaming_response(question)
    # 将助手回复添加到消息列表
    messages.append({"role": "assistant", "content": answer})
    return answer


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

    while True:
        user_input = input("💬^ :\n").strip()
        if not user_input:
            continue

        # 处理特殊命令
        if is_command(user_input):
            command(user_input.lower())
            continue

        print("\n🤖 Assistant: ", flush=True)

        # 获取回答并打印
        ask(user_input)

        sanitize_memory()


def restore_tty():
    """重新打开 stdin 用于交互"""
    if sys.stdin.isatty():
        return
    if sys.platform != 'win32':
        sys.stdin = open('/dev/tty')
    else:
        sys.stdin = open('CON', 'r')


def pipe_mode(prompt: str = None, quit: bool = False, continue_conversation: bool = False):
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

    ask(combined_prompt)

    # 如果启用连续对话，则进入交互模式
    if not continue_conversation and not (prompt and not quit):
        return

    restore_tty()
    chat_loop()


def main():
    load_dotenv()

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
        default=os.getenv("LOG_LEVEL", "ERROR"),
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
    current_mode = args.translate

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
    logger.info("Starting Ask Agent...")
    main()
