# 自定义命令配置

Ask Agent 支持自定义命令功能，可以将常用的提示词模板保存为命令，快速执行。

## 目录

- [概述](#概述)
- [配置方式](#配置方式)
- [Markdown 方式](#markdown-方式)
- [JSON 方式](#json-方式)
- [命令属性](#命令属性)
- [使用示例](#使用示例)
- [注意事项](#注意事项)

## 概述

自定义命令允许你将常用的提示词模板保存为简短的命令，通过 `/命令名` 快速调用。例如，将「代码审查」提示词保存为 `/review` 命令。

## 配置方式

支持两种配置方式：

1. **Markdown 文件** - 在 `command/` 目录下创建 `.md` 文件
2. **JSON 配置** - 在 `command.json` 文件中配置

> JSON 配置优先级高于 Markdown 配置，同名命令会被覆盖。

## Markdown 方式

在 `command/` 目录下创建 Markdown 文件，文件名即为命令名（不含扩展名）。

### 文件格式

```markdown
---
description: 命令的简短描述
---
这里是提示词模板内容...
```

### 示例

创建文件 `command/review.md`:

```markdown
---
description: Code review assistant
---
请作为代码审查助手分析以下代码，重点关注：
1. 代码规范和最佳实践
2. 潜在的性能问题
3. 安全性风险
4. 可维护性建议

请提供具体的改进建议和修改后的代码示例。
```

执行命令：`/review`

### 文件结构

```
ask-agent/
├── command/
│   ├── review.md      # /review 命令
│   ├── test.md        # /test 命令
│   └── docs.md        # /docs 命令
└── command.json
```

## JSON 方式

在项目根目录创建或编辑 `command.json` 文件：

```json
{
  "command": {
    "命令名": {
      "template": "提示词模板内容",
      "description": "命令描述"
    }
  }
}
```

### 示例

```json
{
  "command": {
    "review": {
      "template": "请作为代码审查助手分析以下代码...",
      "description": "Code review assistant"
    },
    "test": {
      "template": "请为以下代码编写单元测试...",
      "description": "Generate unit tests"
    }
  }
}
```

## 命令属性

| 属性 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 命令名称（JSON 方式为键名，Markdown 方式为文件名） |
| `template` | string | 是 | 提示词模板内容 |
| `description` | string | 否 | 命令描述，用于帮助信息显示 |
| `agent` | string | 否 | 使用的 Agent 类型（experimental） |
| `model` | string | 否 | 使用的模型 ID（experimental） |

### template 模板

模板中可以包含任意文本，会被直接发送给 AI 模型。你可以在模板中使用：

- 固定的任务描述
- 特定的格式要求
- 上下文信息占位符

## 使用示例

### 1. 创建简单的问答命令

`command/faq.md`:
```markdown
---
description: 常见问题解答助手
---
请回答以下关于项目的常见问题。如果信息不足，请说明需要补充哪些内容。
```

使用：`/faq`

### 2. 创建技术文档生成命令

`command/docs.md`:
```markdown
---
description: Generate technical documentation
---
请为提供的代码生成技术文档，包括：
1. 功能概述
2. 接口说明
3. 使用示例
4. 注意事项

使用 Markdown 格式输出。
```

使用：`/docs`

### 3. 创建代码转换命令

`command/py2js.md`:
```markdown
---
description: Convert Python to JavaScript
---
请将以下 Python 代码转换为等价的 JavaScript 代码，保持相同的逻辑和功能。
保留原始注释，适当添加 JS 特有的最佳实践。
```

使用：`/py2js`

### 4. 使用 JSON 配置多个命令

`command.json`:
```json
{
  "command": {
    "refactor": {
      "template": "请重构以下代码，提高可读性和性能...",
      "description": "Refactor code"
    },
    "explain": {
      "template": "请详细解释以下代码的功能和工作原理...",
      "description": "Explain code"
    },
    "optimize": {
      "template": "请优化以下代码的性能瓶颈...",
      "description": "Optimize performance"
    }
  }
}
```

## 查看自定义命令

在 Ask Agent 中输入：

```
/commands
```

或查看帮助信息：

```
/help
```

## 注意事项

1. **命令名规范**：
   - 只支持字母、数字、下划线
   - 不区分大小写
   - 建议使用简洁的英文名称

2. **模板内容**：
   - 支持多行文本
   - 可以包含 Markdown 格式
   - 建议保持简洁，避免过长

3. **优先级**：
   - JSON 配置会覆盖同名的 Markdown 配置
   - 建议统一使用一种方式管理命令

4. **加载时机**：
   - 命令在程序启动时加载
   - 修改后需要重启程序

## 最佳实践

1. **命令分类**：
   - 按功能创建不同的命令文件
   - 使用有意义的命令名

2. **模板优化**：
   - 定期优化常用模板
   - 根据实际使用效果调整

3. **版本管理**：
   - 将命令配置纳入版本控制
   - 方便团队共享和同步
