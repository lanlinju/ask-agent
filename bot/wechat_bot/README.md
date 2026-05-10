# 微信 Bot 模块

Ask Agent 的微信 Bot 集成模块，通过腾讯 iLink Bot API 实现微信个人账号的消息收发。

## 架构

```
ask.py (命令入口 /wechat)
  └── bot/wechat_bot/
      ├── __init__.py      # 包导出
      ├── auth.py          # WeChatAuth - QR码登录和token管理
      ├── gateway.py       # WeChatGateway - 长轮询消息接收
      ├── messages.py      # WeChatMessages - 消息发送（文本/typing状态）
      ├── bot.py           # WeChatBot - 主控制器，组合上述模块
      ├── types.py         # 数据类型定义
      ├── handlers.py      # 处理器
      └── application.py   # Application类
```

### 模块职责

| 模块 | 类 | 职责 |
|------|-----|------|
| `auth.py` | `WeChatAuth` | QR码登录、凭证保存/加载、token管理 |
| `gateway.py` | `WeChatGateway` | 长轮询消息接收、会话管理、自动重连 |
| `messages.py` | `WeChatMessages` | 发送文本消息、typing状态管理 |
| `bot.py` | `WeChatBot` | 主控制器，组合 auth/gateway/messages |
| `types.py` | `WeChatMessage` | 消息数据结构 |
| `handlers.py` | `BaseHandler` | 消息处理器基类 |
| `application.py` | `Application` | 应用类，管理处理器和生命周期 |

### 数据流

```
微信用户
  │
  │ iLink Bot API (长轮询)
  ▼
WeChatGateway ──解析──▶ WeChatMessage ──回调──▶ WeChatBot._handle_message
                                                      │
                                                      ▼
                                               ask.handle_wechat_message
                                                      │
                                                      ▼
                                                 agent() 处理
                                                      │
                                                      ▼
WeChatMessages.send_text / send_typing ◀──────────────┘
  │
  │ HTTPS (iLink Bot API)
  ▼
微信用户
```

## 模块详细说明

### auth.py - 认证

通过QR码登录获取 `bot_token`，自动保存凭证到 `~/.ask-agent/wechat/credentials.json`，支持强制重新登录。

### gateway.py - 长轮询Gateway

- 使用 `getupdates` 接口进行长轮询（服务器hold 35秒）
- 自动处理会话过期（`errcode: -14`）并重新登录
- 消息解析和分发
- 支持文本、图片、语音、文件、视频消息类型

### messages.py - 消息发送

- **文本消息**：支持长文本自动分块（2000字符限制）
- **Typing状态**：显示"对方正在输入中"，需要先获取 `typing_ticket`

### bot.py - 主控制器

组合 `WeChatAuth`、`WeChatGateway`、`WeChatMessages`，提供统一的 `start/stop/send` 接口。

## iLink Bot API 协议

### 基础信息

- **Base URL**: `https://ilinkai.weixin.qq.com`
- **CDN URL**: `https://novac2c.cdn.weixin.qq.com/c2c`
- **协议**: HTTP/JSON
- **认证**: Bearer Token (bot_token)

### 通用请求头

所有POST请求需要携带以下Header：

```
Content-Type: application/json
AuthorizationType: ilink_bot_token
Authorization: Bearer <bot_token>
X-WECHAT-UIN: <base64(String(randomUint32))>
```

所有POST请求体包含：`base_info: { channel_version: "<version>" }`

### 认证流程

#### 1. 获取QR码

```
GET /ilink/bot/get_bot_qrcode?bot_type=3

响应:
{
  "qrcode": "<token>",
  "qrcode_img_content": "<url_or_base64>"
}
```

#### 2. 轮询扫码状态

```
GET /ilink/bot/get_qrcode_status?qrcode=<token>
Headers: { "iLink-App-ClientVersion": "1" }

响应:
{
  "status": "wait" | "scaned" | "confirmed" | "expired",
  "bot_token": "<token>",
  "ilink_bot_id": "<bot_id>",
  "ilink_user_id": "<user_id>",
  "baseurl": "<api_base_url>"
}
```

### 消息收取：长轮询

```
POST /ilink/bot/getupdates
Body: {
  "get_updates_buf": "<cursor_or_empty>",
  "base_info": { "channel_version": "1.0.0" }
}
Timeout: 35s (服务器hold连接)

响应:
{
  "ret": 0,
  "msgs": WeixinMessage[],
  "get_updates_buf": "<new_cursor>",
  "longpolling_timeout_ms": 35000
}
```

**错误处理**: `ret != 0` 时，`errcode: -14` 表示会话过期，需要重新登录。

### 消息发送

```
POST /ilink/bot/sendmessage
Body: {
  "msg": {
    "from_user_id": "",
    "to_user_id": "<user_id>",
    "client_id": "<unique_id>",
    "message_type": 2,        // BOT
    "message_state": 2,       // FINISH
    "context_token": "<from_inbound_msg>",  // 必须原样传回
    "item_list": [{ "type": 1, "text_item": { "text": "..." } }]
  },
  "base_info": { "channel_version": "1.0.0" }
}
```

**重要**: `context_token` 必须使用收到消息中的值，不可复用旧消息的token。

### 发送Typing状态

#### 1. 获取typing_ticket

```
POST /ilink/bot/getconfig
Body: {
  "ilink_user_id": "<user_id>",
  "context_token": "<context_token>",
  "base_info": { "channel_version": "1.0.0" }
}

响应:
{
  "typing_ticket": "<base64_ticket>"
}
```

#### 2. 发送typing状态

```
POST /ilink/bot/sendtyping
Body: {
  "ilink_user_id": "<user_id>",
  "typing_ticket": "<ticket>",
  "status": 1,  // 1=开始输入, 2=取消输入
  "base_info": { "channel_version": "1.0.0" }
}
```

### 消息结构

```typescript
WeixinMessage {
  message_id: number,
  from_user_id: string,      // 用户ID格式: xxx@im.wechat
  to_user_id: string,        // BotID格式: xxx@im.bot
  client_id: string,
  create_time_ms: number,
  message_type: 1(USER) | 2(BOT),
  message_state: 0(NEW) | 1(GENERATING) | 2(FINISH),
  context_token: string,     // 必须原样传回
  item_list: MessageItem[]
}

MessageItem {
  type: 1(TEXT) | 2(IMAGE) | 3(VOICE) | 4(FILE) | 5(VIDEO)
  text_item?: { text: string }
  image_item?: { media: CDNMedia, aeskey?: string, url?: string, ... }
  voice_item?: { media: CDNMedia, text?: string, playtime?: number }
  file_item?: { media: CDNMedia, file_name?: string, md5?: string }
  video_item?: { media: CDNMedia, video_size?, play_length? }
}

CDNMedia {
  encrypt_query_param: string,
  aes_key: string,
  encrypt_type?: 0|1
}
```

### 媒体文件加密

- **算法**: AES-128-ECB with PKCS7 padding
- **aes_key编码**: base64(raw 16 bytes) for images, base64(hex 32 chars) for file/voice/video
- **上传流程**: 生成随机key → 加密文件 → POST到CDN → 获取download param
- **下载流程**: 从CDN获取 → 用key解密

### API端点列表

| Endpoint | Method | 功能 |
|---|---|---|
| `/ilink/bot/get_bot_qrcode` | GET | 获取登录二维码 |
| `/ilink/bot/get_qrcode_status` | GET | 轮询扫码状态 |
| `/ilink/bot/getupdates` | POST | 长轮询收消息 |
| `/ilink/bot/sendmessage` | POST | 发送消息 |
| `/ilink/bot/getconfig` | POST | 获取配置（typing_ticket） |
| `/ilink/bot/sendtyping` | POST | 发送typing状态 |
| `/ilink/bot/getuploadurl` | POST | 获取CDN上传地址 |

## 使用

### 二维码登录

登录时会根据二维码内容自动处理：
- **URL链接**：在终端直接显示ASCII二维码，并打印链接
- **Base64图片**：保存为 `qrcode.png` 或对应格式文件
- **SVG格式**：保存为 `qrcode.svg` 文件

终端二维码显示依赖 `qrcode` 库：
```bash
pip install qrcode>=7.4
```

### 基本用法

```python
from bot.wechat_bot import WeChatBot

bot = WeChatBot()
await bot.login()

@bot.on_message
async def handle(message):
    await bot.send_typing(message.user_id, message.context_token)
    await bot.reply(message, f"Echo: {message.text}")

await bot.start(handle)
```

### 使用Application

```python
from bot.wechat_bot import Application, MessageHandler, CommandHandler, filters

app = Application.builder().build()
app.add_handler(CommandHandler("start", handle_start))
app.add_handler(MessageHandler(filters.text, handle_text))
app.run_polling()
```

### 环境变量

| 变量 | 说明 | 必需 |
|------|------|------|
| `WECHAT_BASE_URL` | iLink API基础URL | 否（默认: `https://ilinkai.weixin.qq.com`） |

## 注意事项

1. **每次扫码登录Bot ID会变化**，这是iLink平台的设计，属于正常现象。
2. **仅限合规使用**，需遵守《微信ClawBot功能使用条款》。
3. **context_token必须原样传回**，否则消息无法投递。
4. **会话有效期24小时**，过期后需要重新扫码登录。
5. **消息历史**：没有拉取历史消息的API，只有`get_updates_buf`游标机制。
6. **速率限制**：官方未公开，需要实测。

## 参考

- [weixin-bot SDK](https://github.com/epiral/weixin-bot)
- [weixin-ClawBot-API](https://github.com/codeenxi/weixin-ClawBot-API)
- [OpenClaw文档](https://docs.openclaw.ai)
- [微信ClawBot功能使用条款](https://docs.openclaw.ai/terms)