# 代码架构详解

本文档面向 AI 辅助开发，详细描述项目的代码架构、数据流、关键设计决策和扩展方向。

## 1. 整体数据流

```
QQ用户发消息
    ↓
NapCatQQ (Docker容器) 通过 NTQQ 协议收到消息
    ↓ WebSocket 推送 OneBot v11 事件
QQBot._listen() 收到 JSON 事件
    ↓
QQBot._handle_event() 解析消息
    ├─ 提取 text_parts (用户文字)
    ├─ 提取 file_segments (文件/图片)
    ├─ 过滤表情包 (summary 含"表情")
    ├─ 群聊：检查是否 @机器人 或 /命令 或 文件
    ↓
    ├─ 纯文件无文字 → download_file() → 回复"已收到" → return
    ├─ /命令 → _handle_command() → return
    └─ 有文字 → 构造 IncomingMessage → on_message 回调
            ↓
Application._process_message()
    ├─ UserManager.get_by_qq_id() → 无则自动注册
    ├─ SessionManager.get_or_create() → 获取/创建 session
    ├─ SessionManager.get_history() → 加载对话历史
    ├─ 发送"正在思考中..." (引用回复)
    ↓
AgentRunner.run()
    ├─ set_context(user, session_key) → 设置 MCP 工具上下文
    ├─ _list_workspace_files() → 扫描工作区文件列表
    ├─ _build_prompt() → 拼接 system prompt + 文件列表 + 历史 + 用户消息
    ├─ ClaudeSDKClient(options) → 启动 Claude Code CLI 子进程
    │   ├─ model, max_turns, permission_mode, cwd, mcp_servers
    │   └─ MCP Server 注册自定义工具
    ├─ client.query(prompt) → 发送给模型
    ├─ client.receive_response() → 流式接收
    │   ├─ ThinkingBlock → on_progress("thinking", 摘要)
    │   ├─ ToolUseBlock → on_progress("tool", 工具名+参数)
    │   ├─ TextBlock → 累积 result_text
    │   └─ ResultMessage → 完成
    ├─ _heartbeat task → 每60秒检查，超时未收到事件则发心跳
    ↓
    返回 result_text → _strip_markdown() 去 Markdown 格式
    ↓
SessionManager.append_message() → 保存到数据库
QQBot.send_text() → 发送回复 (引用原消息)
```

## 2. 核心模块详解

### 2.1 QQBot 适配器 (`channels/qq/bot.py`)

职责：与 NapCat 的 WebSocket/HTTP 通信，消息解析和发送。

关键设计：
- **WebSocket 重连循环**：`_run_loop()` 断线后指数退避重连（2s→60s）
- **消息解析不依赖 CQ 码**：直接使用 `event["message"]` JSON 数组解析，避免 CQ 码中 URL 特殊字符导致的解析错误
- **文件下载**：调用 `file_handler.download_file()`，传入 `group_id` 用于区分群文件/私聊文件 API
- **发送消息支持 [at:QQ号] 标签**：`_parse_at_tags()` 将文本中的 `[at:123]` 转为 OneBot at 消息段
- **超长消息自动分段**：`_split_message()` 按 3000 字符分割，优先在换行处断开
- **发送文件用同步 httpx**：`_send_file_sync()` 避免异步事件循环冲突（MCP 工具通过 Internal API 调用时）

### 2.2 AgentRunner (`agent/runner.py`)

职责：封装 Claude Agent SDK，管理 Agent 生命周期和流式输出。

关键设计：
- **ClaudeSDKClient 而非 query()**：使用 Client 模式以支持 MCP 自定义工具
- **permission_mode="bypassPermissions"**：容器以非 root 用户运行，Agent 可自由使用所有工具
- **cwd 按用户/群隔离**：每次调用根据 workspace_id 设置不同的工作目录
- **心跳机制**：后台 asyncio Task 每 60 秒检查，如果模型长时间无事件输出，发送进度消息给用户
- **Markdown 清理**：`_strip_markdown()` 将模型输出的 Markdown 转为 QQ 友好的纯文本
- **工作区文件注入 prompt**：每次调用前扫描 workspace 目录，将文件列表附到 prompt 中

### 2.3 MCP 工具 (`agent/tools.py`)

职责：定义 Agent 可调用的自定义工具。

关键设计：
- **工具在 Claude Code CLI 子进程中执行**：无法直接访问主进程内存，通过 Internal HTTP API（127.0.0.1:9199）回调
- **上下文传递**：`set_context(user, session_key)` 在每次 Agent 调用前设置，工具通过全局变量读取。因为 MCP 工具是进程内执行的（SDK MCP Server），全局变量在子进程 fork 时会被复制
- **web_search**：调用 Serper API（Google 搜索），返回 answerBox + knowledgeGraph + organic results
- **create_scheduled_task**：根据 session_key 自动判断目标（群聊→群，私聊→用户），生成 Python 脚本保存到 tasks 目录
- **send_file_to_chat**：通过 Internal API → QQBot._send_file_sync() → NapCat upload_private_file/upload_group_file

### 2.4 Internal HTTP API (`services/internal_api.py`)

职责：MCP 工具跨进程调用主进程的桥梁。

运行方式：在主进程中启动一个 threading.HTTPServer（端口 9199），MCP 工具通过 urllib 同步调用。

端点：
- POST /task/add → TaskScheduler.add_task()
- POST /task/list → TaskScheduler.list_tasks()
- POST /task/delete → TaskScheduler.remove_task()
- POST /user/info → UserManager.get_by_qq_id()
- POST /file/send → QQBot._send_file_sync()
- POST /msg/send → 发送文本消息

注意事项：
- 使用 `asyncio.new_event_loop()` 在同步线程中运行异步代码
- 文件发送必须用同步方法（`_send_file_sync`），避免事件循环冲突

### 2.5 文件处理 (`services/file_handler.py`)

职责：从 NapCat 下载文件，提取文本内容。

下载优先级：
1. **共享 Docker 卷**：NapCat 下载文件到 `/app/.config/QQ/`，claude-agent 通过共享卷 `/napcat_files/` 读取，直接 copy（毫秒级）
2. **base64**：get_file API 返回 base64 编码数据
3. **get_group_file_url / get_private_file_url**：获取直链后流式下载
4. **direct_url**：消息段中自带的 URL（图片等）

文本提取支持：docx (python-docx)、xlsx (openpyxl)、pdf (PyPDF2)、纯文本文件

### 2.6 定时任务 (`scheduler/manager.py`)

职责：管理 cron 定时任务的 CRUD 和执行。

关键设计：
- **APScheduler AsyncIOScheduler**：异步调度器，支持 cron 表达式
- **SQLite 持久化**：任务信息存数据库，启动时 `_load_tasks_from_db()` 恢复所有 active 任务
- **subprocess 执行脚本**：`_run_script()` 在子进程中运行 Python 脚本，stdout 作为消息发送
- **任务清理**：删除任务时同步删除脚本文件
- **[at:QQ号] 支持**：脚本 stdout 中的 `[at:123]` 会被解析为 QQ @消息

### 2.7 数据库 (`services/database.py`)

三张表：
- **users**：qq_id (唯一)、password_hash (bcrypt)、role (admin/user)、max_tasks
- **sessions**：channel + channel_session_id (唯一索引)、history (JSON)、last_active
- **tasks**：owner_id (外键)、cron_expr、script_path、target_channel、target_id、status

QQ 渠道用户自动注册（首次发消息创建），密码随机生成用于未来 Web 端登录。

## 3. Docker 架构

```yaml
services:
  napcat:        # QQ 协议端，NTQQ + OneBot v11
    volumes:
      - napcat_config  # OneBot 配置 + 登录信息
      - napcat_data    # QQ 数据 + 下载的文件缓存

  claude-agent:  # 主服务
    volumes:
      - ./src:/app/src:ro        # 代码热更新（不用 rebuild）
      - ./config:/app/config:ro  # 配置文件
      - agent_data:/app/data     # SQLite + workspace + 任务脚本
      - napcat_data:/napcat_files:ro  # 共享 NapCat 文件（只读）
```

napcat_data 卷被两个容器共享，实现零网络开销的文件传递。

## 4. Workspace 隔离

```
/app/data/workspace/
├── {user_qq_id}/          # 私聊 workspace
│   ├── 文件A.docx         # 用户上传的文件
│   ├── output.txt         # Agent 生成的文件
│   └── ...
├── group_{group_id}/      # 群聊 workspace
│   ├── 群文件.pdf
│   └── ...
```

Agent 的 cwd 设置为对应的 workspace 目录，`ls`、`read`、`write` 等操作自然隔离。

## 5. 扩展方向

### 5.1 WebUI 渠道 (`channels/web/`)

已预留目录。计划：
- FastAPI + WebSocket 实现
- JWT 鉴权（QQ号 + 密码登录）
- 流式输出（SSE 或 WebSocket 推送 Agent 中间结果）
- 文件上传下载界面
- 与 QQ 渠道共享 SessionManager 和 UserManager

### 5.2 更多消息渠道

继承 `BaseChannel` 即可：
- Telegram Bot
- 微信（需要另一个协议层）
- Discord
- Slack

### 5.3 模型切换

当前通过 `ANTHROPIC_BASE_URL` + `config.yaml` 切换。可扩展为：
- 运行时动态切换（`/model switch glm-4.5-flash`）
- 多模型并存（不同任务用不同模型）
- 接入 OpenAI 兼容端点（需要单独的 OpenAI Agent 实现）

### 5.4 任务系统增强

- 任务执行日志持久化
- 任务失败重试和告警
- 更复杂的触发条件（webhook 触发、条件触发）
- 任务模板市场（预置常用任务：天气播报、新闻摘要、网站监控等）

### 5.5 文件处理增强

- 图片 OCR（接入 OCR API）
- 音视频处理（转写、摘要）
- 生成 docx/pdf 文件并发送（目前只能发纯文本文件）

### 5.6 安全加固

- Agent sandbox：Docker-in-Docker 或 gVisor 限制 Agent Bash 工具
- 用户配额：限制每日 API 调用次数和 token 消耗
- 审计日志：记录所有 Agent 工具调用
- 敏感操作确认：特定工具需用户二次确认

### 5.7 可观测性

- Prometheus metrics 导出
- 结构化日志（JSON 格式）
- Agent 执行链路追踪（每轮 tool call 和结果）
- 成本统计面板（按用户统计 token 消耗）

### 5.8 多实例部署

当前单实例 SQLite。扩展为：
- PostgreSQL 替代 SQLite
- Redis 缓存 session
- 多 worker 负载均衡
- NapCat 多账号管理
