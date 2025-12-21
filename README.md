# Ask Agent (ag)

 一个基于 DeepSeek API 的高效终端问答工具

## 特性

- 🚀 **流式输出** - 实时显示 API 响应，无需等待
- 💬 **对话上下文** - 保持完整的对话历史，支持多轮对话
- ⚡ **管道友好** - 支持管道输入，可与其他命令组合
- 📝 **交互式模式** - 直观的聊天界面
- 🔧 **多种使用方式** - 交互、一问一答、管道模式

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
5. 设置环境变量

```bash
export DEEPSEEK_API_KEY="sk-your-api-key-here"

# 为了永久设置，添加到 ~/.bashrc 或 ~/.zshrc
echo 'export DEEPSEEK_API_KEY="sk-your-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

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

### 2. 一问一答模式

```bash
ag "你的问题" -q
```

回答后直接退出，不进入交互模式。

### 3. 管道模式

从管道读取输入：

```bash
echo "解释一下这个命令" | ag

# 或配合管道处理
cat file.py | ag "这段代码有什么问题？"
```

### 4. 管道后进入对话模式

```bash
cat script.sh | ag "优化这个脚本" -a
```

回答后进入交互模式，继续追问。

## 命令行选项

```
usage: ag [-q] [-a] [query]

Ask Agent - DeepSeek 聊天客户端

positional arguments:
  query             要提问的内容（如果未提供，将从标准输入读取）

optional arguments:
  -q, --quit        一问一答模式，回答后直接退出
  -a, --after       管道模式中，回答后进入连续对话模式
```

## 使用示例

### 查询系统信息
```bash
ag "如何查看系统内存使用情况?"
```

### 分析代码
```bash
cat app.js | ag "这段代码有什么性能问题？" -a
```

### 调试脚本
```bash
./script.sh 2>&1 | ag "为什么会出现这个错误？"
```

### 学习和讨论
```bash
ag
# 进入交互模式后可以持续追问
```

## 环境变量

- `DEEPSEEK_API_KEY` - **必需** DeepSeek API 密钥

## 架构

- **流式处理** - 使用 HTTP 流式传输，实时处理 API 响应
- **对话状态管理** - 在内存中维护对话历史，支持完整的上下文
- **错误处理** - 详细的错误提示，方便故障排查
- **模式切换** - 灵活支持多种交互模式

## 系统提示词

ag 使用的系统提示词优化了 AI 的回答方式：

- 简洁、直接、高效
- 专注于命令行和技术问题
- 提供可直接使用的代码和命令
- 避免冗长解释

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
