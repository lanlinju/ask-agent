# 配置文件说明

本文档介绍 Ask Agent 的配置文件格式和用法。

## roles.json - 角色配置

角色配置文件用于管理角色扮演模式中的角色。

### 文件位置

- 项目目录：`./roles.json`（优先）
- 用户目录：`~/.ask-agent/roles.json`（备选）

### 配置格式

```json
{
  "default_role": "角色ID",
  "roles": {
    "角色ID": {
      "name": "显示名称",
      "description": "角色描述",
      "prompt_file": "提示词文件.md",
      "voice": {
        "enabled": true,
        "model": "TTS模型ID",
        "type": "preset|design|clone",
        "voice_id": "预置音色ID",
        "sample": "音频样本路径",
        "style": "音色描述"
      }
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `default_role` | string | 否 | 默认角色 ID |
| `roles` | object | 是 | 角色配置对象 |

#### 角色配置

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 角色显示名称 |
| `description` | string | 否 | 角色描述 |
| `prompt_file` | string | 否 | 提示词文件名（默认：`{role_id}.md`） |
| `voice` | object | 否 | 语音配置 |

#### 语音配置

| 字段 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用语音 |
| `model` | string | TTS 模型 ID |
| `type` | string | 音色类型：`preset`（预置）、`design`（设计）、`clone`（克隆） |
| `voice_id` | string | 预置音色 ID（preset 模式） |
| `sample` | string | 音频样本路径（clone 模式） |
| `style` | string | 音色描述（design 模式） |

### 配置示例

```json
{
  "default_role": "frieren",
  "roles": {
    "frieren": {
      "name": "frieren",
      "description": "",
      "prompt_file": "frieren.md"
    },
    "纳西妲": {
      "name": "纳西妲",
      "description": "",
      "voice": {
        "enabled": true,
        "model": "mimo-v2.5-tts-voiceclone",
        "type": "clone",
        "sample": "./voices/nahida.mp3"
      },
      "prompt_file": "纳西妲.md"
    },
    "translate-agent": {
      "name": "translate-agent",
      "description": "",
      "prompt_file": "translate-agent.md"
    }
  }
}
```

---

## agents.json - 智能体配置

智能体配置文件用于管理智能体模式中的智能体。

### 文件位置

- 项目目录：`./agents.json`（优先）
- 用户目录：`~/.ask-agent/agents.json`（备选）

### 配置格式

```json
{
  "default_agent": "智能体ID",
  "agents": {
    "智能体ID": {
      "name": "显示名称",
      "description": "智能体描述",
      "prompt_file": "提示词文件.md"
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `default_agent` | string | 否 | 默认智能体 ID（`"builtin"` 表示内置） |
| `agents` | object | 是 | 智能体配置对象 |

#### 智能体配置

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 智能体显示名称 |
| `description` | string | 否 | 智能体描述 |
| `prompt_file` | string | 否 | 提示词文件名（默认：`{agent_id}.md`） |

### 配置示例

```json
{
  "default_agent": "coding-agent-prodigy",
  "agents": {
    "coding-agent-prodigy": {
      "name": "coding-agent-prodigy",
      "description": "",
      "prompt_file": "coding-agent-prodigy.md"
    },
    "coding-agent": {
      "name": "coding-agent",
      "description": "",
      "prompt_file": "coding-agent.md"
    },
    "command-line-agent": {
      "name": "command-line-agent",
      "description": "",
      "prompt_file": "command-line-agent.md"
    },
    "translate-agent": {
      "name": "translate-agent",
      "description": "",
      "prompt_file": "translate-agent.md"
    }
  }
}
```

---

## 提示词文件

角色和智能体的提示词文件存放在对应的目录中：

```
~/.ask-agent/
├── roles/          # 角色提示词
│   ├── frieren.md
│   └── 纳西妲.md
└── agents/         # 智能体提示词
    ├── coding-agent.md
    └── command-line-agent.md
```

### 提示词文件格式

提示词文件是纯文本 Markdown 文件，内容即为系统提示词：

```markdown
# 角色名称

你是一个...（角色描述）

## 特点
- 特点1
- 特点2

## 说话风格
- 风格描述
```

---

## 其他配置文件

| 配置文件 | 说明 | 位置 |
|----------|------|------|
| `providers.json` | AI Provider 配置 | `~/.ask-agent/providers.json` |
| `mcp.json` | MCP 服务器配置 | 项目目录或 `~/.ask-agent/mcp.json` |
