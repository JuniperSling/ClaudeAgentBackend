# Claude Agent Backend

基于 Claude Agent SDK 的 QQ 聊天机器人后端服务。通过智谱 AI（ZhiPu）的 Anthropic 兼容端点接入大模型，以 NapCatQQ（OneBot v11）作为 QQ 消息通道，支持私聊/群聊对话、文件处理、定时任务等功能。

## 核心能力

- **AI 对话**：接入 Claude Agent SDK，通过智谱 API 兼容端点使用 GLM 系列模型，支持多轮对话、工具调用
- **QQ 消息**：通过 NapCatQQ (OneBot v11) 实现 QQ 私聊和群聊消息收发，支持文本、图片、文件、引用回复、@提及
- **文件处理**：接收 QQ 文件（docx/xlsx/pdf/图片等），自动下载到用户工作区，支持内容提取和 Agent 处理
- **定时任务**：用户通过自然语言让 Agent 创建 cron 定时任务，Agent 自动生成 Python 脚本并注册执行
- **网络搜索**：集成 Google 搜索（Serper API），Agent 可实时搜索天气、新闻等信息
- **工作区隔离**：每个用户/群聊拥有独立工作区，文件和 Agent 操作互相隔离

## 架构

```
                    ┌──────────────┐
                    │   QQ 用户    │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │  QQ 服务器    │
                    └──────┬───────┘
                           │ NTQQ 协议
                    ┌──────▼───────┐
                    │   NapCatQQ   │ ← Docker 容器
                    │  (OneBot v11)│
                    └──────┬───────┘
                           │ WebSocket + HTTP
┌──────────────────────────▼──────────────────────────┐
│                  Claude Agent Backend                │
│                                                      │
│  ┌─────────┐  ┌────────────┐  ┌──────────────────┐  │
│  │ QQBot   │→ │ MessageRouter │→ │  AgentRunner    │  │
│  │ Adapter │  │ + Session Mgr │  │ (Claude SDK +   │  │
│  └─────────┘  └──────────────┘  │  MCP Tools)     │  │
│       ↑                          └────────┬─────────┘  │
│       │                                   │            │
│  ┌────┴─────┐  ┌──────────┐  ┌───────────▼──────┐   │
│  │ Internal │  │ TaskSched│  │  Custom MCP Tools │   │
│  │ HTTP API │← │ (APSched)│  │ web_search, files │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ SQLite   │  │ User Mgr │  │ File Handler     │   │
│  │ Database │  │          │  │ (download/extract)│   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
└──────────────────────────────────────────────────────┘
```

## 项目结构

```
ClaudeAgentBackend/
├── src/
│   ├── main.py                  # 入口，启动所有服务，消息路由，斜杠命令
│   ├── config.py                # 配置加载（YAML + 环境变量 + pydantic）
│   ├── agent/
│   │   ├── runner.py            # AgentRunner：封装 Claude SDK Client，流式处理
│   │   └── tools.py             # 自定义 MCP 工具（搜索、任务管理、文件发送）
│   ├── channels/
│   │   ├── base.py              # BaseChannel 抽象 + IncomingMessage 数据模型
│   │   ├── qq/
│   │   │   └── bot.py           # QQBot：NapCat WebSocket 连接、消息解析、文件处理
│   │   └── web/                 # WebUI 渠道（预留）
│   ├── session/
│   │   └── manager.py           # SessionManager：对话历史管理，TTL 过期清理
│   ├── scheduler/
│   │   ├── manager.py           # TaskScheduler：APScheduler + SQLite 持久化
│   │   ├── base_task.py         # BaseTask 抽象类（预留）
│   │   └── tasks/               # 具体任务模板（预留）
│   ├── users/
│   │   └── manager.py           # UserManager：用户创建、认证、白名单
│   └── services/
│       ├── database.py          # SQLite 数据库初始化和连接管理
│       ├── file_handler.py      # 文件下载（共享卷/API/URL）和内容提取
│       ├── file_manager.py      # 文件路径管理（预留）
│       └── internal_api.py      # 内部 HTTP API（MCP 工具跨进程通信）
├── config/
│   ├── config.yaml              # 运行时配置
│   └── config.example.yaml      # 配置模板
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

## 快速部署

### 前置条件

- Docker + Docker Compose
- 一个 QQ 号（用于机器人登录）
- 智谱 AI API Key（https://open.bigmodel.cn）
- Serper API Key（https://serper.dev，用于 Google 搜索）

### 1. 克隆项目

```bash
git clone https://github.com/JuniperSling/ClaudeAgentBackend.git
cd ClaudeAgentBackend
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 填入：
# ANTHROPIC_BASE_URL=https://open.bigmodel.cn/api/anthropic
# ANTHROPIC_API_KEY=你的智谱API Key
# SERPER_API_KEY=你的Serper API Key
# ADMIN_QQ_ID=管理员QQ号
# ADMIN_PASSWORD=管理员密码（用于未来 Web 端登录）
```

### 3. 配置文件

```bash
cp config/config.example.yaml config/config.yaml
# 按需修改模型名称、session TTL 等参数
```

### 4. 启动服务

```bash
docker compose up -d
```

### 5. 扫码登录 QQ

打开 NapCat WebUI 扫码登录：

```
http://你的IP:6099/webui/
```

Token 在 NapCat 日志中可以找到：`docker logs napcat 2>&1 | grep token`

登录成功后机器人即可使用。

### 更新代码

src 目录通过 volume 挂载，更新代码后重启即可，无需重新 build：

```bash
git pull
docker compose restart claude-agent
```

## 斜杠命令

| 命令 | 说明 | 群聊是否需要@ |
|------|------|:---:|
| /help | 显示帮助 | 否 |
| /clear | 清空当前会话历史 | 否 |
| /new | 新会话（清历史 + 清工作区文件） | 否 |
| /files | 查看当前工作区文件 | 否 |
| /tasks | 查看我的定时任务 | 否 |
| /model | 查看当前模型配置 | 否 |
| /adduser | （管理员）添加用户 | 否 |
| /users | （管理员）查看所有用户 | 否 |

## 消息处理规则

- **私聊**：所有消息直接处理
- **群聊文本**：必须 @机器人 才回复
- **群聊文件**：自动下载到群工作区，不需要@，回复"已收到"
- **群聊表情包**：自动忽略
- **斜杠命令**：群聊中不需要@即可触发

## 模型配置

通过 `MODEL_PRESETS` 注册多个模型，可在运行时通过 `/model <name>` 切换：

| 模型 | 提供商 | 用途 |
|------|--------|------|
| glm-4.7 | 智谱 GLM-4.7 | 高智能旗舰 |
| glm-5.1 | 智谱 GLM-5.1 | 默认 |
| deepseek-v4-flash | DeepSeek V4 Flash | 快速/低成本 |
| deepseek-v4-pro | DeepSeek V4 Pro | 强力 |

每个模型映射到独立的 `base_url` 和 API Key 环境变量。运行时通过 `/model deepseek-v4-flash` 等命令切换。

## Agent 权限模式

当前使用 `bypassPermissions` 模式，Agent 可自由使用所有内置工具（Bash、Read、Write、Edit 等）。容器以非 root 用户 `agent` 运行。

## 自定义 MCP 工具

Agent 通过 in-process MCP Server 注册自定义工具，工具在 Claude Code CLI 子进程中通过 Internal HTTP API（端口 9199）回调主进程：

| 工具名 | 功能 |
|--------|------|
| web_search | Google 搜索（Serper API） |
| web_fetch | 抓取网页内容 |
| send_file_to_chat | 发送文件到当前 QQ 对话 |
| get_current_user_info | 获取当前用户信息 |

## SDK 内置工具拦截（Cron）

通过 PreToolUse hook 拦截 Claude Code CLI 内置的 `CronCreate`/`CronList`/`CronDelete` 工具，
将它们转发到我们的 `TaskScheduler`：

- **CronCreate**：用户说"X 时间提醒我 Y"时 Agent 会调用此工具，hook 把任务存到我们的数据库
- **任务触发**：APScheduler 到点时启动新的 ClaudeSDKClient，把 cron 的 `prompt` 作为 user_message
- **一次性任务**：`recurring=false` 时执行后自动删除
- **session 路由**：任务的目标会话从创建时的 `session_key` 推导（私聊回私聊，群聊回群聊）
| get_current_user_info | 获取当前用户信息 |
| send_file_to_chat | 发送文件到 QQ 对话 |

## 许可

私有项目。
