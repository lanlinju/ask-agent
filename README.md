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

## 安装

### 前置要求

- Python 3.7+
- 有效的 DeepSeek API 密钥

### 步骤

1. 克隆项目
```bash
git clone https://github.com/lanlinju/ask-agent
cd ask-agent
```

2. 安装依赖
```bash
pip install requests
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

4. 获取并设置 API 密钥

访问 [DeepSeek 官网](https://www.deepseek.com) 获取 API 密钥：
1. 前往 https://platform.deepseek.com/
2. 注册或登录账户
3. 进入 API Keys 页面
4. 创建新的 API 密钥
5. 设置环境变量或通过命令行参数使用

**方式一：环境变量（推荐用于长期使用）**
```bash
# Linux
export DEEPSEEK_API_KEY="sk-your-api-key-here"

# Windows
$env:DEEPSEEK_API_KEY="sk-your-api-key-here"

# 为了永久设置，添加到 ~/.bashrc 或 ~/.zshrc
echo 'export DEEPSEEK_API_KEY="sk-your-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

**方式二：命令行参数（推荐用于单次使用）**
```bash
ag --api-key "sk-your-api-key-here" "你的问题"
```

**方式三：.env 文件**

在项目根目录创建 `.env` 文件：

```bash
# .env 文件示例
DEEPSEEK_API_KEY=sk-your-api-key-here
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_API_URL=https://api.deepseek.com
LOG_LEVEL=ERROR
```

.env 文件会自动被加载，无需手动设置环境变量。

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
- `/help` - 显示帮助信息
- `!command` - 执行shell命令（如 `!ls`, `!pwd`, `!cat file.txt`），命令和输出会自动添加到消息历史

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
  --api-key API_KEY    DeepSeek API 密钥（如果不提供，将使用 DEEPSEEK_API_KEY 环境变量）
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

## 运行示例
```bash
➜  ask-agent git:(main) ✗ ./ag                                
💬^ :
UEFI是什么缩写？

🤖 Assistant: 
UEFI = Unified Extensible Firmware Interface（统一可扩展固件接口）

💬^ :
/ls
ag
README.md
💬^ :

```

## 环境变量

以下环境变量可以通过系统环境变量设置，也可以在 `.env` 文件中配置：

- `DEEPSEEK_API_KEY` - **必需** DeepSeek API 密钥
- `DEEPSEEK_MODEL` - 可选，模型名称（默认：deepseek-chat）
- `DEEPSEEK_API_URL` - 可选，API 端点（默认：https://api.deepseek.com）
- `LOG_LEVEL` - 可选，日志级别（默认：ERROR，可选：DEBUG, INFO, WARNING, ERROR, CRITICAL）

**配置优先级**：命令行参数 > 系统环境变量 > .env 文件

## .env 文件配置

在项目根目录创建 `.env` 文件可以方便地管理配置：

### .env 文件示例

```bash
# DeepSeek API 配置
DEEPSEEK_API_KEY=sk-your-api-key-here
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_API_URL=https://api.deepseek.com

# 日志配置
LOG_LEVEL=ERROR
```

### 使用说明

1. 在项目根目录创建 `.env` 文件
2. 添加所需的配置项
3. `.env` 文件会在程序启动时自动加载

### .gitignore 配置

```bash
# .gitignore
.env
```

## 特性详解

### 对话上下文管理

- **记忆模式（默认）** - 保持完整的对话历史，支持多轮追问
- **无上下文模式** - 使用 `-n` 选项，每次问答后清空历史，适合独立问题

### 多模式支持

1. **问答模式** - 通用问题回答
2. **翻译模式** - 英汉互译，包含音标和释义
3. **智能体模式** - 支持文件操作、Shell命令执行等高级功能
4. **Shell命令集成** - 执行命令结果自动添加到对话历史

## 架构

- **流式处理** - 使用 HTTP 流式传输，实时处理 API 响应
- **对话状态管理** - 在内存中维护对话历史，支持完整的上下文
- **错误处理** - 详细的错误提示，方便故障排查
- **模式切换** - 灵活支持多种交互模式
- **智能体工具系统** - 支持文件读写、命令执行、任务管理等工具
- **技能系统** - 可扩展的技能加载机制，支持领域知识注入
- **子智能体** - 支持派生子智能体处理特定任务

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

### API 错误 (401)

检查 API 密钥是否正确：

```bash
echo $DEEPSEEK_API_KEY
```

## 参考

[learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)