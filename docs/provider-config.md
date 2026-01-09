# Provider 配置说明

Ask Agent 支持通过 `providers.json` 配置文件管理多个 AI Provider，实现灵活的模型切换和多源调用。

## 概述

Provider 配置系统允许：
- 同时配置多个 AI Provider（OpenAI、DeepSeek 等）
- 支持环境变量引用，保护 API 密钥安全
- 启用/禁用特定 Provider
- 为每个 Provider 配置多个模型
- 指定默认模型

## 配置文件结构

`providers.json` 配置文件的基本结构：

```json
{
  "model": "provider_id/model_id",
  "providers": {
    "provider_id": {
      "name": "Provider 显示名称",
      "enabled": true,
      "options": {
        "baseURL": "https://api.example.com/v1",
        "apiKey": "env:API_KEY",
        "timeout": 60,
        "maxRetries": 3,
        "headers": {}
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

## 字段说明

### 顶层字段

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model` | string | 否 | 默认模型 ID，格式：`provider_id/model_id` |
| `providers` | object | 是 | Provider 配置对象 |

### Provider 配置

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `name` | string | 否 | Provider 显示名称，用于界面展示 |
| `enabled` | boolean | 否 | 是否启用该 Provider（默认：true） |
| `options` | object | 是 | Provider 连接选项 |
| `models` | object | 是 | 模型配置对象 |

### Options 选项

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `baseURL` | string | 是 | API 基础 URL |
| `apiKey` | string | 是 | API 密钥，支持直接值或环境变量引用 |
| `timeout` | number | 否 | 请求超时时间（秒） |
| `maxRetries` | number | 否 | 最大重试次数 |
| `headers` | object | 否 | 额外的 HTTP 请求头 |

### 模型配置

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `model_id` | string | 是 | 模型 ID（Provider 的模型标识符，对象的键） |
| `name` | string | 是 | 模型显示名称 |

## 环境变量引用

支持通过 `env:` 前缀引用环境变量，这是推荐的方式：

```json
{
  "options": {
    "apiKey": "env:DEEPSEEK_API_KEY"
  }
}
```

程序启动时会自动从环境变量中读取对应的值。

### 优势

- **安全性**：API 密钥不会出现在配置文件中
- **灵活性**：不同环境可以使用不同的 API 密钥
- **可维护性**：统一在 `.env` 文件或系统环境中管理

### 设置环境变量

**Linux/macOS**：
```bash
export DEEPSEEK_API_KEY="sk-your-api-key-here"
```

**Windows PowerShell**：
```powershell
$env:DEEPSEEK_API_KEY="sk-your-api-key-here"
```

**永久设置**（添加到 `~/.bashrc` 或 `~/.zshrc`）：
```bash
echo 'export DEEPSEEK_API_KEY="sk-your-api-key-here"' >> ~/.bashrc
source ~/.bashrc
```

## 支持的 Provider

Ask Agent 支持任何兼容 OpenAI API 格式的 Provider。

### 官方测试示例

#### DeepSeek

```json
{
  "deepseek": {
    "name": "DeepSeek",
    "enabled": true,
    "options": {
      "baseURL": "https://api.deepseek.com/v1",
      "apiKey": "env:DEEPSEEK_API_KEY",
      "timeout": 60
    },
    "models": {
      "deepseek-chat": {
        "name": "DeepSeek Chat"
      },
      "deepseek-reasoner": {
        "name": "DeepSeek Reasoner"
      }
    }
  }
}
```

**获取 API 密钥**：https://platform.deepseek.com/

### 其他兼容 Provider

以下 Provider 也兼容 OpenAI API 格式：

- **Groq** - https://console.groq.com/
- **Together AI** - https://api.together.xyz/
- **LocalAI** - 本地部署的 OpenAI 兼容服务
- **Ollama** - 需要配置为 OpenAI 兼容模式

只要 Provider 兼容 OpenAI API 格式，都可以在 `providers.json` 中配置。

## 完整配置示例

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
        "timeout": 60,
        "maxRetries": 3
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
      "enabled": false,
      "options": {
        "baseURL": "https://api.openai.com/v1",
        "apiKey": "env:OPENAI_API_KEY",
        "timeout": 60
      },
      "models": {
        "gpt-4o": {
          "name": "GPT-4o"
        },
        "gpt-4-turbo": {
          "name": "GPT-4 Turbo"
        }
      }
    }
  }
}
```

## 使用模型

### 交互式切换模型

```bash
ag
/models
```

会列出所有可用模型，输入编号即可切换。

## 验证配置

```bash
# 列出所有可用模型
/models
```

## 常见问题

### 1. 环境变量未生效

**问题**：使用 `env:ENV_VAR_NAME` 但提示 API Key 未设置

**解决**：
```bash
# 检查环境变量是否设置
echo $DEEPSEEK_API_KEY

# 检查环境变量名称拼写是否正确
# 注意区分大小写
```

### 2. 模型不存在

**问题**：提示"未找到模型: xxx"

**解决**：
```bash
# 列出所有可用模型
/models

# 检查 providers.json 配置
cat providers.json

# 确认模型 ID 是否正确
# 格式应该是 provider_id/model_id 或 model_id
```

### 3. Provider 被禁用

**问题**：某个 Provider 无法使用

**解决**：检查 `providers.json` 中该 Provider 的 `enabled` 字段是否为 `true`

```json
{
  "deepseek": {
    "enabled": true  // 确保为 true
  }
}
```
## 配置优先级

配置读取的优先级（从高到低）：

1. `providers.json` 配置文件
2. 系统环境变量
3. `.env` 文件
4. 命令行参数 `--api-key`（仅用于临时覆盖）

## 最佳实践

1. **使用环境变量**：不要在配置文件中直接写入 API 密钥
2. **启用必要的 Provider**：只启用需要的 Provider，减少初始化时间
3. **设置合理的超时**：根据网络状况调整 timeout 值
4. **配置重试机制**：设置 `maxRetries` 提高请求可靠性
5. **定期更新模型**：关注 Provider 的模型更新，及时添加新模型

## 相关文档

- [主 README](../README.md)
- [MCP 配置](./mcp-config.md)
