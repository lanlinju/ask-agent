# MCP 配置说明

Ask Agent 支持通过 Model Context Protocol (MCP) 连接外部工具服务器。

## 配置文件位置

在项目根目录创建 `mcp.json` 文件：

```bash
# 项目目录结构
ask-agent/
├── ask.py
├── mcp.json          # MCP 配置文件
├── requirements.txt
└── README.md
```

## 服务器类型

### Stdio 类型（本地服务器）

通过标准输入/输出与本地服务器进程通信。

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
    "local_server": {
      "type": "stdio",
      "command": "python",
      "args": ["./server/server.py"],
      "env": {
        "API_KEY": "secret-key"
      },
      "cwd": "/path/to/server",
      "description": "自定义本地服务器",
      "enabled": true
    }
  }
}
```

**Stdio 配置字段：**
- `command`（必需）: 可执行文件路径或命令
- `args`（可选）: 命令行参数数组
- `env`（可选）: 环境变量字典
- `cwd`（可选）: 工作目录

### HTTP 类型（远程服务器）

通过 HTTP 与远程服务器通信。

```json
{
  "servers": {
    "http_server": {
      "type": "http",
      "url": "http://localhost:8000/mcp",
      "headers": {
        "Authorization": "Bearer your-token",
        "X-Custom-Header": "value"
      },
      "timeout": 30,
      "description": "远程 HTTP 服务器",
      "enabled": true
    },
    "cloud_mcp": {
      "type": "http",
      "url": "https://api.example.com/mcp",
      "timeout": 60,
      "description": "云端 MCP 服务",
      "enabled": true
    }
  }
}
```

**HTTP 配置字段：**
- `url`（必需）: 服务器端点 URL
- `headers`（可选）: HTTP 请求头
- `timeout`（可选）: 请求超时时间（秒）

## 通用配置字段

所有服务器类型都支持以下字段：

- `type`（必需）: 服务器类型，支持值：
  - `"stdio"` 或 `"local"` - 本地进程服务器
  - `"http"` 或 `"streamablehttp"` 或 `"remote"` - HTTP 服务器
- `description`（可选）: 服务器描述，在交互式选择时显示
- `enabled`（可选）: 是否启用该服务器（默认: `true`）

## 完整示例

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
    "database": {
      "type": "local",
      "command": "python",
      "args": ["db_server.py"],
      "env": {
        "DB_HOST": "localhost",
        "DB_PORT": "5432"
      },
      "description": "数据库访问服务器",
      "enabled": false
    }
  }
}
```

## 使用配置

### 交互式选择服务器

```bash
# 启动 Ask Agent
ag

# 在交互模式中
/mcp          # 显示服务器列表并选择
/mcp -l       # 列出所有服务器及详细信息
```

已连接的服务器会显示绿色的 `(active)` 标记。

### 在智能体模式中使用

连接 MCP 服务器后，智能体可以自动使用服务器提供的工具：

```bash
# 启动智能体模式
ag --agent

# 连接 MCP 服务器
/mcp
# 选择需要的服务器

# 使用 MCP 工具
列出 /home/user 目录下的文件
```

## 常见问题

### Q: 如何测试 MCP 服务器是否正常工作？

A: 使用 `/mcp -l` 查看服务器状态，然后选择服务器尝试连接。连接成功后会显示可用的工具列表。

### Q: 一个服务器可以连接多次吗？

A: 不需要。已连接的服务器会显示 `(active)` 标记，重复连接不会重复加载工具。

### Q: 如何临时禁用某个服务器？

A: 在 `mcp.json` 中将 `"enabled"` 设置为 `false`，或在交互式选择时不选择该服务器。

### Q: Stdio 服务器的命令找不到怎么办？

A: 确保：
1. 命令在系统 PATH 中，或
2. 使用绝对路径，或
3. 在 `cwd` 字段指定工作目录并使用相对路径

### Q: HTTP 服务器连接超时怎么办？

A: 调整 `timeout` 字段的值（单位：秒）：

```json
{
  "type": "http",
  "url": "http://slow-server.com/mcp",
  "timeout": 120
}
```
