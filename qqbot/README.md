# QQ Bot 模块

Ask Agent 的 QQ Bot 集成模块，通过 QQ 官方 OpenAPI 和 WebSocket Gateway 实现单聊消息收发。

## 架构

```
ask.py (命令入口 /qqbot)
  └── qqbot/
      ├── __init__.py      # 包导出
      ├── auth.py          # QQAuth - access_token 认证管理
      ├── gateway.py       # QQGateway - WebSocket Gateway 连接与事件分发
      ├── messages.py      # QQMessages - 消息发送（文本/Markdown/图片）
      └── bot.py           # QQBot - 主控制器，组合上述模块
```

### 模块职责

| 模块 | 类 | 职责 |
|------|-----|------|
| `auth.py` | `QQAuth` | 管理 access_token 的获取和自动续期 |
| `gateway.py` | `QQGateway` | WebSocket 连接、心跳维护、事件分发、自动重连 |
| `gateway.py` | `QQMessage` | 消息数据结构 |
| `messages.py` | `QQMessages` | 发送文本、Markdown、图片消息，长文本自动分块 |
| `bot.py` | `QQBot` | 主控制器，组合 auth/gateway/messages |

### 数据流

```
QQ 服务器
  │
  │ WebSocket (Gateway)
  ▼
QQGateway ──解析──▶ QQMessage ──回调──▶ QQBot._handle_message
                                              │
                                              ▼
                                       ask.handle_qq_message
                                              │
                                              ▼
                                         agent() 处理
                                              │
                                              ▼
QQMessages.send_markdown / send_text ◀────────┘
  │
  │ HTTPS (OpenAPI)
  ▼
QQ 服务器
```

## 模块详细说明

### auth.py - 认证

通过 `appId` + `clientSecret` 获取 access_token，自动缓存并在过期前 60 秒刷新。

### gateway.py - WebSocket Gateway

- 连接 QQ Gateway 获取 WebSocket URL
- 处理 `HELLO` → `IDENTIFY` → `READY` 握手流程
- 心跳保活（默认 45 秒间隔）
- 会话恢复（`RESUME`）和断线重连
- 事件分发：仅处理 `C2C_MESSAGE_CREATE`（单聊消息）

### messages.py - 消息发送

- **文本消息**：`msg_type: 0`，支持长文本自动分块
- **Markdown 消息**：`msg_type: 2`，失败自动回退到文本
- **图片消息**：先上传获取 `file_info`，再以 `msg_type: 7` 发送富媒体

### bot.py - 主控制器

组合 `QQAuth`、`QQGateway`、`QQMessages`，提供统一的 `start/stop/send` 接口。

## QQ OpenAPI 协议

### 认证

#### 获取 access_token

```
POST https://bots.qq.com/app/getAppAccessToken
Content-Type: application/json

请求:
{
  "appId": "1903967151",
  "clientSecret": "c9UbVSEm6IG4ez52"
}

响应:
{
  "access_token": "ACCESS_TOKEN_STRING",
  "expires_in": 7200
}
```

### 消息发送

所有消息发送接口需要 `Authorization: QQBot {access_token}` 请求头。

#### 发送文本消息

```
POST https://api.sgroup.qq.com/v2/users/{openid}/messages
Authorization: QQBot {access_token}
Content-Type: application/json

{
  "msg_type": 0,
  "content": "你好，世界！",
  "msg_id": "消息ID（被动回复时必填）",
  "msg_seq": 1
}
```

#### 发送 Markdown 消息

```
POST https://api.sgroup.qq.com/v2/users/{openid}/messages
Authorization: QQBot {access_token}
Content-Type: application/json

{
  "msg_type": 2,
  "markdown": {
    "content": "**加粗文本**\n普通文本"
  },
  "msg_id": "消息ID",
  "msg_seq": 2
}
```

#### 上传图片

```
POST https://api.sgroup.qq.com/v2/users/{openid}/files
Authorization: QQBot {access_token}
Content-Type: application/json

{
  "file_type": 1,
  "srv_send_msg": false,
  "file_data": "base64编码的图片数据"
}

响应:
{
  "file_info": "file_info字符串"
}
```

#### 发送图片消息

```
POST https://api.sgroup.qq.com/v2/users/{openid}/messages
Authorization: QQBot {access_token}
Content-Type: application/json

{
  "msg_type": 7,
  "media": {
    "file_info": "上传接口返回的file_info"
  },
  "msg_id": "消息ID",
  "msg_seq": 3
}
```

### WebSocket Gateway

#### 获取 Gateway URL

```
GET https://api.sgroup.qq.com/gateway/bot
Authorization: QQBot {access_token}

响应:
{
  "url": "wss://api.sgroup.qq.com/websocket"
}
```

#### WebSocket 操作码 (Opcode)

| Opcode | 名称 | 方向 | 说明 |
|--------|------|------|------|
| 0 | DISPATCH | 服务端→客户端 | 事件分发 |
| 1 | HEARTBEAT | 双向 | 心跳 |
| 2 | IDENTIFY | 客户端→服务端 | 鉴权认证 |
| 6 | RESUME | 客户端→服务端 | 恢复会话 |
| 7 | RECONNECT | 服务端→客户端 | 要求重连 |
| 9 | INVALID_SESSION | 服务端→客户端 | 会话无效 |
| 10 | HELLO | 服务端→客户端 | 连接建立 |
| 11 | HEARTBEAT_ACK | 服务端→客户端 | 心跳确认 |

#### IDENTIFY（鉴权）

```json
{
  "op": 2,
  "d": {
    "token": "QQBot {access_token}",
    "intents": 33554432,
    "shard": [0, 1],
    "properties": {}
  }
}
```

`intents: 33554432` 即 `1 << 25`（`INTENT_GROUP_AND_C2C`），接收单聊消息事件。

#### HELLO（连接建立）

```json
{
  "op": 10,
  "d": {
    "heartbeat_interval": 45000
  }
}
```

#### HEARTBEAT（心跳）

```json
{
  "op": 1,
  "d": null
}
```

客户端按 `heartbeat_interval` 毫秒间隔发送，`d` 为最近收到的序列号。

#### DISPATCH（事件分发）

```json
{
  "op": 0,
  "s": 42,
  "t": "C2C_MESSAGE_CREATE",
  "d": {
    "id": "消息ID",
    "msg_id": "消息ID",
    "event_id": "事件ID",
    "content": "<@!123456> 你好",
    "author": {
      "user_openid": "用户openid"
    },
    "openid": "用户openid",
    "attachments": [
      {
        "content_type": "image/jpeg",
        "filename": "photo.jpg",
        "size": 123456,
        "url": "https://...",
        "width": 800,
        "height": 600
      }
    ]
  }
}
```

#### attachment content_type 说明

| content_type | 类型 | 附加字段 |
|-------------|------|----------|
| `image/jpeg` | JPEG 图片 | `width`, `height` |
| `image/png` | PNG 图片 | `width`, `height` |
| `image/gif` | GIF 图片 | `width`, `height` |
| `voice` | 语音 | `voice_wav_url`（WAV 链接）、`asr_refer_text`（ASR 参考结果） |
| `video/mp4` | 视频 | — |
| `file` | 文件 | — |

**语音附件特殊字段**：
- `voice_wav_url`：WAV 格式语音链接（优先使用，模型兼容性最好）
- `asr_refer_text`：QQ 服务端语音识别结果（可直接使用，无需模型转写）
- `url`：原始语音链接（SILK V3 格式，需 `silk-python` 解码后才能被模型识别）

**SILK 语音格式**：QQ 语音底层使用 SILK V3 编码（文件头 `#!SILK_V3`），不是通用音频格式。
模型 API 仅支持 mp3/flac/m4a/wav/ogg，需要先用 `silk-python` 解码为 PCM 再转 WAV。

#### RESUME（恢复会话）

```json
{
  "op": 6,
  "d": {
    "token": "QQBot {access_token}",
    "session_id": "会话ID",
    "seq": 42
  }
}
```

#### RECONNECT（要求重连）

```json
{
  "op": 7
}
```

#### INVALID_SESSION（会话无效）

```json
{
  "op": 9,
  "d": false
}
```

## 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `QQ_APP_ID` | QQ 机器人 AppID | 是 |
| `QQ_APP_SECRET` | QQ 机器人 AppSecret | 是 |

## 使用

```bash
# 设置环境变量
export QQ_APP_ID="your_app_id"
export QQ_APP_SECRET="your_app_secret"

# 启动 ask-agent
python ask.py

# 在交互模式中启动 QQ Bot
/qqbot
```

## 参考

- [QQ 开放平台文档](https://bot.q.qq.com/wiki/develop/api/)
