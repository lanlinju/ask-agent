# Ask Agent (ag)

 一个基于 DeepSeek API 的高效终端问答工具，支持智能体模式

## 特性

- 🚀 **流式输出** - 实时显示 API 响应，无需等待
- 💬 **对话上下文** - 保持完整的对话历史，支持多轮对话
- ⚡ **管道友好** - 支持管道输入，可与其他命令组合
- 📝 **交互式模式** - 直观的聊天界面
- 🔧 **多种使用方式** - 交互、一问一答、管道模式
- 🤖 **智能体模式** - 支持代码编辑、文件操作等高级功能
- 🧩 **可扩展技能** - 通过 Skills 加载领域知识
- 🔌 **MCP 支持** - 集成 Model Context Protocol，支持外部工具
- 🎯 **多 Provider 支持** - 支持多个 AI Provider（OpenAI、DeepSeek 等）
- 🔄 **模型切换** - 交互式选择和切换不同的 AI 模型

## 安装

### 前置要求

- Python 3.7+
- 至少一个有效的 AI Provider API 密钥（DeepSeek、OpenAI 等）

### 步骤

1. 克隆项目
```bash
git clone https://github.com/lanlinju/ask-agent
cd ask-agent
```

2. 安装依赖
```bash
# 方式一：从 requirements.txt 安装（推荐）
pip install -r requirements.txt

# 方式二：单独安装依赖
pip install python-dotenv requests
```

3. 创建软链接（可选，简化使用）
```bash
# 创建 ~/.local/bin 目录（如果不存在）
mkdir -p ~/.local/bin

# 创建软链接
ln -s "$(pwd)/ag" ~/.local/bin/ag

# 确保 ~/.local/bin 在 PATH 中（如果不在，添加到 ~/.bashrc 或 ~/.zshrc）
export PATH="$HOME/.local/bin:$PATH"
```

之后就可以直接使用 `ag` 命令了！

4. 配置 AI Providers

ag 支持多个 AI Provider，通过 `providers.json` 配置文件管理。

创建 `providers.json` 文件（示例）：

```json
{
  "model": "deepseek/deepseek-chat",
  "providers": {
    "deepseek": {
      "name": "DeepSeek",
      "enabled": true,
      "options": {
        "baseURL": "https://api.deepseek.com/v1",
        "apiKey": "env:DEEPSEEK_API_KEY",
      },
      "models": {
        "deepseek-chat": {
          "name": "DeepSeek Chat"
        },
        "deepseek-reasoner": {
          "name": "DeepSeek Reasoner"
        }
      }
    },
    "openai": {
      "name": "OpenAI",
      "enabled": true,
      "options": {
        "baseURL": "https://api.openai.com/v1",
        "apiKey": "env:OPENAI_API_KEY",
      },
      "models": {
        "gpt-4o": {
          "name": "GPT-4o"
        },
        "gpt-4-turbo": {
        }
      }
    }
  }
}
```

**API Key 设置：**

支持环境变量引用（推荐），格式为 `env:ENV_VAR_NAME`。

**方式一：环境变量（推荐用于长期使用）**
```bash
# Linux
export DEEPSEEK_API_KEY="sk-your-api-key-here"
export OPENAI_API_KEY="sk-openai-api-key-here"

# Windows
$env:DEEPSEEK_API_KEY="sk-your-api-key-here"
$env:OPENAI_API_KEY="sk-openai-api-key-here"

# 为了永久设置，添加到 ~/.bashrc 或 ~/.zshrc
echo 'export DEEPSEEK_API_KEY="sk-your-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

**方式二：直接在配置文件中设置**
```json
"options": {
  "baseURL": "https://api.deepseek.com/v1",
  "apiKey": "sk-your-api-key-here"
}
```

**方式三：命令行参数（不推荐，仅用于单次测试）**
```bash
ag --api-key "sk-your-api-key-here" "你的问题"
```

**方式四：.env 文件**

在项目根目录创建 `.env` 文件：

```bash
# DeepSeek API 配置
DEEPSEEK_API_KEY=sk-your-api-key-here

# OpenAI API 配置
OPENAI_API_KEY=sk-openai-api-key-here

# 日志配置
LOG_LEVEL=ERROR
```

.env 文件会自动被加载，无需手动设置环境变量。

5. 获取 API 密钥

访问以下官网获取 API 密钥：

**DeepSeek：**
访问 [DeepSeek 官网](https://www.deepseek.com) 获取 API 密钥：
1. 前往 https://platform.deepseek.com/
2. 注册或登录账户
3. 进入 API Keys 页面
4. 创建新的 API 密钥
5. 在 providers.json 中设置环境变量引用或直接设置

## 使用

### 1. 交互模式 - 持续对话

```bash
ag

# 或者使用完整路径
python ag
./ag
```

输入问题后，ag 会保持对话历史，支持多轮交互。

**支持的命令：**
- `exit` - 退出程序
- `/ask` - 进入问答模式（清空对话历史）
- `/agent` - 进入智能体模式（清空对话历史）
- `/e` - 进入翻译模式（清空对话历史）
- `/new` - 创建新会话（清空对话历史）
- `/models` - 交互式选择和切换模型
- `/help` - 显示帮助信息
- `/mcp` - 交互式选择并连接 MCP 服务器
- `/mcp -l` - 列出所有可用的 MCP 服务器
- `!command` - 执行shell命令（如 `!ls`, `!pwd`, `!cat file.txt`），命令和输出会自动添加到消息历史

### 模型切换

ag 支持在多个 AI Provider 之间切换模型：

```bash
# 交互式选择模型
ag /models

# 示例输出：
# 📋 可用模型:
#
#   DeepSeek:
#   → [1] deepseek/deepseek-chat: DeepSeek Chat
#     [2] deepseek/deepseek-reasoner: DeepSeek Reasoner
#
#   OpenAI:
#     [3] openai/gpt-4o: GPT-4o
#     [4] openai/gpt-4-turbo: GPT-4 Turbo
#
# 请输入模型编号 (0 or 直接Enter 取消): 3
# ✅ 已切换到模型: GPT-4o (openai)
```

选择的模型会自动保存为默认模型，下次启动时自动使用。

### 2. 一问一答模式

```bash
ag "你的问题" -q
```

回答后直接退出，不进入交互模式。

### 3. 翻译模式

```bash
# 使用 -e 选项进入翻译模式
ag -e "computer"

# 或在交互模式中输入 /e 进入翻译模式
ag -e
```

### 4. Shell命令模式

在交互模式中，可以执行shell命令。命令和输出都会添加到消息历史，便于AI助手基于命令结果进行后续分析：

```bash
ag

# 进入后输入以下命令
/ls                    # 列出当前目录
/pwd                   # 显示当前路径
/cat filename          # 查看文件内容
```
### 5. 管道模式

从管道读取输入：

```bash
echo "解释一下这个命令" | ag

# 或配合管道处理
cat file.py | ag "这段代码有什么问题？"
```

### 5. 无上下文模式

不记忆对话历史，每次问答后清空历史：

```bash
ag "第一个问题" -n

# 或在交互模式中使用
ag -n
```

这对需要独立问答的场景很有用。

## 命令行选项

```
usage: ag [-q] [-a] [-e] [-n] [--agent] [--api-key API_KEY] [--log-level LOG_LEVEL] [query]

Ask Agent - DeepSeek 聊天客户端

positional arguments:
  query                要提问的内容（如果未提供，将从标准输入读取）

optional arguments:
  -q, --quit           一问一答模式，回答后直接退出
  -a, --after          管道模式中，回答后进入连续对话模式
  -e, --translate      进入翻译模式
  --agent              进入智能体模式
  -n, --no-memory      不记忆上下文，每次问答后只保留系统提示词
  --api-key API_KEY    API 密钥（临时覆盖配置文件中的设置，不推荐长期使用）
  --log-level LOG_LEVEL  设置日志级别（DEBUG, INFO, WARNING, ERROR, CRITICAL）
```

## 使用示例

### 查询系统信息
```bash
ag "如何查看系统内存使用情况?"
```

### 分析代码
```bash
cat app.js | ag "这段代码有什么性能问题？"
```

### 调试脚本
```bash
./script.sh 2>&1 | ag "为什么会出现这个错误？"
```

### 翻译英语单词
```bash
ag -e "computer"
```

### 无上下文问答
```bash
ag -n "这是独立的问题，不需要记忆历史"
```

### 使用命令行指定 API 密钥
```bash
ag --api-key "sk-your-api-key" "你的问题"
```
建议使用 `providers.json` 配置文件管理 API 密钥。

### 学习和讨论
```bash
ag
# 进入交互模式后可以持续追问
# 输入 /e 进入翻译模式
# 输入 /ask 返回问答模式
# 输入 /ls 执行 ls 命令，结果会添加到对话历史
```

### 智能体模式
```bash
# 使用 --agent 选项进入智能体模式
ag --agent "帮我写一个 Python 脚本来读取文件"

# 或在交互模式中输入 /agent 进入智能体模式
ag
/agent
```

智能体模式支持以下功能：
- 文件读写操作
- Shell 命令执行
- 任务管理
- 子智能体调用
- 技能加载
- MCP 服务器工具调用

### 使用 MCP 工具
```bash
# 1. 进入智能体模式
ag --agent

# 2. 连接 MCP 服务器
/mcp
# 选择需要的服务器编号

# 3. 智能体将自动使用 MCP 提供的工具
帮我读取 /tmp 目录下的文件列表
```

## MCP 服务器管理

Ask Agent 支持 Model Context Protocol (MCP)，可以连接外部工具服务器扩展功能。

### 配置 MCP 服务器

在项目根目录创建 `mcp.json` 配置文件(示例配置)：

```json
{
  "servers": {
    "filesystem": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
      "description": "文件系统访问服务器",
      "enabled": true
    },
    "http_server": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "description": "HTTP 服务器",
      "timeout": 30,
      "enabled": true
    },
    "stdio_server": {
      "type": "stdio",
      "command": "python",
      "args": ["./server/stdio_server.py"],
      "description": "本地 MCP 服务器",
      "enabled": true
    }
  }
}
```

### 服务器类型

1. **stdio 类型** - 本地服务器，通过标准输入/输出通信
   - `command`: 启动命令
   - `args`: 命令参数
   - `env`: 环境变量（可选）
   - `cwd`: 工作目录（可选）

2. **http 类型** - 远程服务器，通过 HTTP 通信
   - `url`: 服务器地址
   - `headers`: 请求头（可选）
   - `timeout`: 超时时间（可选）

### 使用 MCP 命令

在交互模式中使用 MCP 相关命令：

```bash
ag
# 进入交互模式后
/mcp          # 交互式选择并连接服务器
/mcp -l       # 列出所有可用服务器
```

交互式选择时，已连接的服务器会显示绿色的 `(active)` 标记。

### MCP 配置说明

- `enabled`: 是否启用该服务器（默认：true）
- `description`: 服务器描述，用于交互式选择时显示
- `type`: 服务器类型，支持 "stdio" 或 "http"（支持别名："local"、"remote"、"streamablehttp"）

**详细配置说明：** 查看 [MCP 配置文档](docs/mcp-config.md) 了解完整的配置选项和示例。

## 运行示例
```bash
➜  ask-agent git:(main) ✗ ./ag
💬^ :
UEFI是什么缩写？

🤖 Assistant:
UEFI = Unified Extensible Firmware Interface（统一可扩展固件接口）

💬^ :
/mcp

可用的 MCP 服务器 (2 个):
------------------------------------------------------------
  1. http_server          用于测试的MCP服务器
  2. stdio_server         用于测试的本地MCP服务器      (active)
------------------------------------------------------------

请输入要连接的服务器编号 (支持多个，用空格分隔，按 Enter 退出): 1

✅ 成功连接 1 个服务器

💬^ :
```

## 环境变量

以下环境变量可以通过系统环境变量设置，也可以在 `.env` 文件中配置：

- `DEEPSEEK_API_KEY` - DeepSeek API 密钥（如果在 providers.json 中使用 `env:DEEPSEEK_API_KEY`）
- `OPENAI_API_KEY` - OpenAI API 密钥（如果在 providers.json 中使用 `env:OPENAI_API_KEY`）
- `LOG_LEVEL` - 可选，日志级别（默认：ERROR，可选：DEBUG, INFO, WARNING, ERROR, CRITICAL）

**配置优先级**：
1. providers.json 配置文件
2. 系统环境变量
3. .env 文件
4. 命令行参数 `--api-key`（仅用于临时覆盖）

## .env 文件配置

在项目根目录创建 `.env` 文件可以方便地管理 API 密钥：

### .env 文件示例

```bash
# DeepSeek API 配置
DEEPSEEK_API_KEY=sk-your-deepseek-api-key-here

# OpenAI API 配置
OPENAI_API_KEY=sk-openai-api-key-here

# 日志配置
LOG_LEVEL=ERROR
```

### 使用说明

1. 在项目根目录创建 `.env` 文件
2. 添加所需的 API 密钥
3. 在 `providers.json` 中使用 `env:ENV_VAR_NAME` 引用这些环境变量
4. `.env` 文件会在程序启动时自动加载


## 特性详解

### 对话上下文管理

- **记忆模式（默认）** - 保持完整的对话历史，支持多轮追问
- **无上下文模式** - 使用 `-n` 选项，每次问答后清空历史，适合独立问题

### 多模式支持

1. **问答模式** - 通用问题回答
2. **翻译模式** - 英汉互译，包含音标和释义
3. **智能体模式** - 支持文件操作、Shell命令执行等高级功能
4. **Shell命令集成** - 执行命令结果自动添加到对话历史

## Provider 配置系统

ag 支持多个 AI Provider，通过 `providers.json` 配置文件统一管理。

### 配置文件结构

```json
{
  "model": "deepseek/deepseek-chat",
  "providers": {
    "provider_id": {
      "name": "Provider 显示名称",
      "enabled": true,
      "options": {
        "baseURL": "https://api.example.com/v1",
        "apiKey": "env:API_KEY",
        "timeout": 60,
        "maxRetries": 3
      },
      "models": {
        "model_id": {
          "name": "模型显示名称"
        }
      }
    }
  }
}
```

### 配置说明

**顶层字段：**
- `model`: 默认模型 ID（格式：`provider_id/model_id`），可选
- `providers`: Provider 配置对象

**Provider 配置：**
- `name`: Provider 显示名称
- `enabled`: 是否启用该 Provider（布尔值）
- `options.baseURL`: API 基础 URL
- `options.apiKey`: API 密钥，支持直接值或环境变量引用（格式：`env:ENV_VAR_NAME`）
- `options.timeout`: 请求超时时间（秒），可选
- `options.maxRetries`: 最大重试次数，可选
- `models`: 模型配置对象

**模型配置：**
- `model_id`: 模型 ID（Provider 的模型标识符）
- `name`: 模型显示名称

### 环境变量引用

支持通过 `env:` 前缀引用环境变量：

```json
{
  "options": {
    "apiKey": "env:DEEPSEEK_API_KEY"
  }
}
```

程序会自动从环境变量中读取对应的值。

### 支持的 Provider

ag 支持任何兼容 OpenAI API 格式的 Provider：

**示例：**
- DeepSeek
- OpenAI

只要 Provider 兼容 OpenAI API 格式，都可以在 `providers.json` 中配置。

## 架构

- **流式处理** - 使用 HTTP 流式传输，实时处理 API 响应
- **对话状态管理** - 在内存中维护对话历史，支持完整的上下文
- **错误处理** - 详细的错误提示，方便故障排查
- **模式切换** - 灵活支持多种交互模式
- **智能体工具系统** - 支持文件读写、命令执行、任务管理等工具
- **技能系统** - 可扩展的技能加载机制，支持领域知识注入
- **子智能体** - 支持派生子智能体处理特定任务
- **MCP 集成** - 支持 Model Context Protocol，可连接外部工具服务器

## 系统提示词

ag 根据不同模式使用不同的系统提示词：

### 问答模式
- 简洁、直接、高效
- 专注于命令行和技术问题
- 提供可直接使用的代码和命令
- 避免冗长解释

### 翻译模式
- 英汉互译
- 英语单词提供音标和释义
- 缩写词提供全称

### 智能体模式
- 计划-行动-报告循环
- 自动使用 Skills 工具加载领域知识
- 支持子智能体调用
- 任务列表管理
- 工具优先于解释
- 支持调用已连接的 MCP 服务器工具

## 技能系统（Skills）

ag 支持通过 `skills/` 目录加载可扩展的技能模块。每个技能是一个文件夹，包含：

- **SKILL.md**（必需）- 技能描述和指令
- **scripts/**（可选）- 可执行脚本
- **references/**（可选）- 参考文档
- **assets/**（可选）- 模板和输出文件

智能体会自动识别并加载这些技能，在任务匹配时使用相应的领域知识。

## 子智能体（Subagents）

智能体模式支持派生子智能体处理特定任务：

- **explore** - 只读探索智能体，用于探索代码、查找文件、搜索
- **code** - 完整功能智能体，用于实现功能和修复错误
- **plan** - 规划智能体，用于设计实现策略

## 故障排查

### 错误：未设置 DEEPSEEK_API_KEY

```bash
export DEEPSEEK_API_KEY="your-api-key"
```

### MCP 配置错误

如果遇到 MCP 服务器连接问题：

1. 检查 `mcp.json` 文件格式是否正确
2. 确保 stdio 类型的 `command` 和 `args` 配置正确
3. 确保 http 类型的 `url` 可以访问
4. 查看日志输出（使用 `--log-level DEBUG`）获取详细信息

```bash
# 启用调试日志
ag --log-level DEBUG
```

### 查看已连接的 MCP 服务器

```bash
ag
/mcp -l
```

### API 错误 (401)

检查 API 密钥是否正确：

```bash
# 检查 DeepSeek API 密钥
echo $DEEPSEEK_API_KEY

# 检查 OpenAI API 密钥
echo $OPENAI_API_KEY

# 检查其他 Provider 的 API 密钥
```

## 参考

- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)
- [deepseek-guides-thinking-mode-tool-calls](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode#%E5%B7%A5%E5%85%B7%E8%B0%83%E7%94%A8)